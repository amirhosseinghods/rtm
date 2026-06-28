#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
method_eval.py — Score EACH directional method/style separately, measure which wins in
different time windows, and build a WALK-FORWARD ENSEMBLE that weights each style by its
own recent track record (so the winning style dominates, and the blend adapts over time).

Honest design:
  • Each method emits a per-bar vote (+1 up / -1 down / 0 abstain) from the SAME engine the
    live app uses (rtm_bt.prep_symbol + rsi_tools).
  • A vote at bar i is scored against the REAL forward return over horizon H (no look-ahead).
  • Per-period hit-rates show how the winner shifts across time.
  • The ensemble at bar i weights each method by its hit-rate over a TRAILING window whose
    outcomes were already resolved before i — so weights never peek at the future.

Run:  cd ~/Desktop/trade/backtest && python3 method_eval.py [SYM ...] [--tf M5] [--h 24]
Defaults to the validated watchlist core, M5, horizon 24 bars (~2h on M5).
"""
import os, sys
import numpy as np
import rtm_bt as B
import rsi_tools as RT

CORE = ["BTCUSDT", "XRPUSDT", "ETHUSD", "SOLUSD", "XAUUSD",
        "BNBUSD", "ADAUSD", "AVAXUSD", "LINKUSD", "LTCUSD", "DOTUSD", "ATOMUSD", "DOGEUSD"]

THR = 0.0005   # flat band: |fwd| < THR counts as "no move" (a directional call there fails)


def method_votes(D):
    """Return {name: vote_array(+1/-1/0)} for each directional style, length n."""
    o, h, l, c, atr = D["o"], D["h"], D["l"], D["c"], D["atr"]
    n = len(c)
    b1, b2, b3 = D["b1"], D["b2"], D["b3"]
    votes = {}

    # 1) HTF structure bias (1h+4h+D)
    votes["bias_htf"] = np.sign(1.0 * b1 + 2.0 * b2 + 2.0 * b3).astype(float)

    # 2) RSI overbought/oversold (mean-reversion: <30 -> up, >70 -> down)
    rsi = RT.rsi(c, 14)
    rv = np.zeros(n)
    rv[rsi < 30] = 1.0
    rv[rsi > 70] = -1.0
    votes["rsi_30_70"] = rv

    # 3) discount / premium of the dealing range (discount -> up, premium -> down)
    pH, pL = D["pH"], D["pL"]
    dp = np.zeros(n)
    with np.errstate(invalid="ignore"):
        mid = pL + 0.5 * (pH - pL)
        valid = np.isfinite(mid) & (pH > pL)
        dp[valid & (c < mid)] = 1.0
        dp[valid & (c > mid)] = -1.0
    votes["disc_prem"] = dp

    # 4) at an active 1h order block (demand band -> up, supply band -> down)
    def zone_vote(demB, demT, supB, supT):
        v = np.zeros(n)
        with np.errstate(invalid="ignore"):
            din = np.isfinite(demT) & (c >= demB) & (c <= demT)
            sin = np.isfinite(supT) & (c >= supB) & (c <= supT)
        v[din] = 1.0
        v[sin] = -1.0
        return v
    votes["zone_1h"] = zone_vote(D["c_demB"], D["c_demT"], D["c_supB"], D["c_supT"])
    votes["zone_15m"] = zone_vote(D["a_demB"], D["a_demT"], D["a_supB"], D["a_supT"])

    # 5) RSI regular divergence, carried K bars forward from where it forms
    dv = np.zeros(n)
    try:
        for d in RT.divergences(h, l, c, rsi, L=5, recent_bars=n):
            b = d["bar"]
            s = 1.0 if d["type"] == "bull" else -1.0
            dv[b:min(n, b + 12)] = s
    except Exception:
        pass
    votes["rsi_div"] = dv

    return votes, c, atr


def score_method(vote, c, H):
    """For bars with a non-zero vote and a resolvable horizon, return (hits, n, mean_move%)."""
    n = len(c)
    idx = np.where((vote != 0) & (np.arange(n) + H < n))[0]
    if len(idx) == 0:
        return 0, 0, 0.0, idx, None
    fwd = (c[idx + H] - c[idx]) / c[idx]
    v = vote[idx]
    hit = np.where(np.abs(fwd) < THR, False, (np.sign(fwd) == v))
    signed = v * fwd                                  # >0 means the call paid
    return int(hit.sum()), len(idx), float(signed.mean() * 100), idx, (v, fwd, hit)


def ensemble_votes(votes, c, H, W=3000):
    """Walk-forward blend: at bar i, weight each method by its hit-rate over the trailing
    window [i-W, i-H] (outcomes already known by i), abstaining methods <=50%. No look-ahead."""
    n = len(c)
    names = list(votes.keys())
    V = np.vstack([votes[k] for k in names])          # (m, n)
    # precompute, for each method, per-bar resolved outcome (only where it voted & resolvable)
    fwd_ok = np.full((len(names), n), np.nan)
    for mi, k in enumerate(names):
        v = votes[k]
        res = np.arange(n) + H < n
        m = (v != 0) & res
        ii = np.where(m)[0]
        if len(ii):
            f = (c[ii + H] - c[ii]) / c[ii]
            ok = np.where(np.abs(f) < THR, 0.0, (np.sign(f) == v[ii]).astype(float))
            fwd_ok[mi, ii] = ok
    out = np.zeros(n)
    for i in range(W + H, n):
        lo = i - W
        wsum = 0.0; acc = 0.0
        for mi in range(len(names)):
            seg = fwd_ok[mi, lo:i - H]                 # resolved-before-i outcomes
            seg = seg[~np.isnan(seg)]
            if len(seg) < 20:
                continue
            rate = seg.mean()
            w = max(0.0, (rate - 0.5) * 2.0)           # 50%->0, 70%->0.4, 100%->1.0
            if w <= 0 or V[mi, i] == 0:
                continue
            acc += w * V[mi, i]; wsum += w
        out[i] = np.sign(acc) if wsum > 0 else 0.0
    return out


def run(symbols, tf="M5", H=24, periods=5):
    pooled = {}                  # method -> [hits, n, sum_signed%]
    pooled_per = {}              # method -> per-period [ [hits,n], ... ]
    ens_pooled = [0, 0]
    ens_per = [[0, 0] for _ in range(periods)]
    per_symbol = {}

    for sym in symbols:
        try:
            D = B.prep_symbol(sym, tf)
        except Exception as e:
            print(f"  skip {sym}: {str(e)[:60]}")
            continue
        votes, c, atr = method_votes(D)
        n = len(c)
        if n < 5000:
            print(f"  skip {sym}: only {n} bars")
            continue
        bounds = np.linspace(0, n, periods + 1).astype(int)
        per_symbol[sym] = {}
        for k, v in votes.items():
            hits, cnt, mv, idx, detail = score_method(v, c, H)
            pooled.setdefault(k, [0, 0, 0.0])
            pooled[k][0] += hits; pooled[k][1] += cnt; pooled[k][2] += mv * cnt
            per_symbol[sym][k] = (hits, cnt, mv)
            # per-period
            pp = pooled_per.setdefault(k, [[0, 0] for _ in range(periods)])
            if detail is not None:
                vv, fwd, hh = detail
                for p in range(periods):
                    sel = (idx >= bounds[p]) & (idx < bounds[p + 1])
                    pp[p][0] += int(hh[sel].sum()); pp[p][1] += int(sel.sum())
        # ensemble
        ev = ensemble_votes(votes, c, H)
        ehits, ecnt, emv, eidx, edet = score_method(ev, c, H)
        ens_pooled[0] += ehits; ens_pooled[1] += ecnt
        if edet is not None:
            _, _, ehh = edet
            for p in range(periods):
                sel = (eidx >= bounds[p]) & (eidx < bounds[p + 1])
                ens_per[p][0] += int(ehh[sel].sum()); ens_per[p][1] += int(sel.sum())
        per_symbol[sym]["ENSEMBLE"] = (ehits, ecnt, emv)
        print(f"  {sym:9} n={n:6}  done")

    return pooled, pooled_per, ens_pooled, ens_per, per_symbol, periods


def pct(h, n):
    return f"{100*h/n:5.1f}%" if n else "  -  "


def confluence_eval(symbols, tf="M5"):
    """The metric that actually matters for THIS system: does method-agreement raise the R
    expectancy of REAL zone entries? Uses the proven config (HTF/1h zones, grade2, with-trend)
    via bt_structure.collect, then scores each entry by how many *independent* styles agree."""
    os.environ.setdefault("FD_SRC", "1h")            # proven config: 1h-anchored zones only
    os.environ.setdefault("FD_CONF", "none")
    import importlib, bt_structure as BS
    importlib.reload(BS)
    EXTRA = ["rsi_30_70", "disc_prem", "rsi_div"]     # bias/zone are already implied by the gated entry
    rows = []
    for sym in symbols:
        try:
            D = B.prep_symbol(sym, tf)
        except Exception as e:
            print(f"  skip {sym}: {str(e)[:50]}"); continue
        sess = "london" if sym == "XAUUSD" else "ny"
        recs = BS.collect(D, sym, sess)
        votes, c, atr = method_votes(D)
        for r in recs:
            R = r.get("2R")
            if R is None:
                continue
            bar, dr = r["i"], r["dir"]
            agree = [m for m in EXTRA if votes[m][bar] == dr]
            rows.append({"R": float(R), "confl": len(agree),
                         **{m: int(votes[m][bar] == dr) for m in EXTRA}})
        print(f"  {sym:9} {len([1 for r in recs if r.get('2R') is not None]):4} entries")
    if not rows:
        print("no entries"); return
    import numpy as _np
    Rall = _np.array([x["R"] for x in rows])
    base_exp = Rall.mean(); base_wr = (Rall > 0).mean() * 100
    print(f"\n=== CONFLUENCE on real 1h+grade2 entries (2R rule) — does combining help? ===")
    print(f"baseline (all {len(rows)} entries): expR {base_exp:+.3f}  WR {base_wr:.1f}%")
    print("\nby number of agreeing styles (RSI30/70, disc/prem, divergence):")
    for k in range(0, len(EXTRA) + 1):
        sel = _np.array([x["R"] for x in rows if x["confl"] == k])
        if len(sel) >= 15:
            print(f"   {k} agree: n={len(sel):4}  expR {sel.mean():+.3f}  WR {(sel>0).mean()*100:5.1f}%")
    print("\nper-style (entries where that style agrees vs not):")
    for m in EXTRA:
        a = _np.array([x["R"] for x in rows if x[m]])
        b = _np.array([x["R"] for x in rows if not x[m]])
        if len(a) >= 15 and len(b) >= 15:
            print(f"   {m:11} agree: n={len(a):4} expR {a.mean():+.3f} | disagree: n={len(b):4} expR {b.mean():+.3f}"
                  f"   -> delta {a.mean()-b.mean():+.3f}")
    # simple combined rule: require >=2 agreeing styles
    hi = _np.array([x["R"] for x in rows if x["confl"] >= 2])
    if len(hi) >= 15:
        print(f"\ncombined filter (>=2 styles agree): n={len(hi)}  expR {hi.mean():+.3f}  WR {(hi>0).mean()*100:.1f}%"
              f"   (vs baseline {base_exp:+.3f})")


def main():
    if "--confluence" in sys.argv or "--conf" in sys.argv:
        args = [a for a in sys.argv[1:] if not a.startswith("--")]
        tf = sys.argv[sys.argv.index("--tf") + 1] if "--tf" in sys.argv else "M5"
        confluence_eval(args or CORE, tf)
        return
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    tf = "M5"; H = 24
    if "--tf" in sys.argv:
        tf = sys.argv[sys.argv.index("--tf") + 1]
    if "--h" in sys.argv:
        H = int(sys.argv[sys.argv.index("--h") + 1])
    symbols = args or CORE
    print(f"Evaluating methods on {len(symbols)} symbols, tf={tf}, horizon={H} bars\n")
    pooled, pooled_per, ens_pooled, ens_per, per_symbol, P = run(symbols, tf, H)

    print("\n=== POOLED: each method's directional accuracy (vote vs real forward move) ===")
    rows = []
    for k, (h, n, smv) in pooled.items():
        rows.append((k, h, n, 100 * h / n if n else 0, smv / n if n else 0))
    rows.sort(key=lambda r: -r[3])
    print(f"{'method':12} {'hit-rate':>9} {'n':>7} {'mean move%':>11}")
    for k, h, n, hr, mv in rows:
        print(f"{k:12} {hr:8.1f}% {n:7} {mv:+10.3f}%")
    eh, en = ens_pooled
    print(f"{'ENSEMBLE':12} {pct(eh,en):>9} {en:7}   (walk-forward weighted blend)")

    print(f"\n=== ACCURACY BY TIME WINDOW (history split into {P} equal periods, oldest→newest) ===")
    hdr = "method".ljust(12) + "".join(f"  P{p+1:>5}" for p in range(P))
    print(hdr)
    winners = [None] * P
    best = [(-1, None) for _ in range(P)]
    for k, pp in pooled_per.items():
        line = k.ljust(12)
        for p in range(P):
            h, n = pp[p]
            r = 100 * h / n if n else -1
            line += f"  {pct(h,n)}"
            if n >= 30 and r > best[p][0]:
                best[p] = (r, k)
        print(line)
    eline = "ENSEMBLE".ljust(12)
    for p in range(P):
        h, n = ens_per[p]
        eline += f"  {pct(h,n)}"
    print(eline)
    print("\nwinning style per period (n>=30): " +
          " | ".join(f"P{p+1}:{best[p][1] or '-'}({best[p][0]:.0f}%)" for p in range(P)))


if __name__ == "__main__":
    main()
