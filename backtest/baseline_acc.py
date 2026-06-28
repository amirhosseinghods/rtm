#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
baseline_acc.py -- BASELINE accuracy the improvements must beat.

Two parts:
  (1) Realised directional accuracy from web/learning/predictions.jsonl
      (overall, by tf, by confluence/combo, and live-only subset).
  (2) An HONEST backtest of the CURRENT projection backbone (rsi_tools.project's
      directional score) on the local JSON OHLCV dataset (site/data/ohlcv_*.json).

HONEST BACKTEST RULES enforced here:
  - A call at bar i uses ONLY close[:i+1] (features computed on past data only).
  - Scored vs close[i+H]: correct iff sign(close[i+H]-close[i])==call AND
    |move| > THR (THR=0.0005); flat counts WRONG.
  - HTF bias is derived by resampling the same series into higher TFs and running
    struct_engine, then as-of aligning the higher-TF trend value valid at each bar's
    close time (merge_asof backward -> no look-ahead).
  - No fitting on the scored bars (the backbone has no fitted params).

Run:  PYTHONIOENCODING=utf-8 python backtest/baseline_acc.py
"""
import os, sys, json
import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backtest"))
import rsi_tools as RT
import rtm_bt as B

PRED = os.path.join(ROOT, "web", "learning", "predictions.jsonl")
DATA = os.path.join(ROOT, "site", "data")

SYMBOLS = ["BTCUSDT","ETHUSDT","XRPUSDT","SOLUSDT","BNBUSDT","ADAUSDT","DOGEUSDT",
           "AVAXUSDT","LINKUSDT","LTCUSDT","DOTUSDT","PAXGUSDT","XAUUSD"]
TFS = ["M5","M15","H1"]
THR = 0.0005
TFW = {"M5": 0.6, "M15": 0.8, "H1": 1.0}     # tf_weight grows with TF (project semantics)
# horizon per TF (bars). M5 we test {12,24,48} and pick; M15/H1 sensible single H.
HORIZONS = {"M5": [12, 24, 48], "M15": [12, 24], "H1": [12, 24]}
# higher TFs to resample for HTF bias (multiplier in bars of the base TF)
HTF_MULT = {"M5": [3, 12], "M15": [4, 16], "H1": [4, 24]}   # M5->M15,H1 ; M15->H1,H4 ; H1->H4,D1
L = 5


# ---------------------------------------------------------------------------
# PART 1 : realised accuracy from the prediction log
# ---------------------------------------------------------------------------
def part1():
    rows = []
    for line in open(PRED, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        r = json.loads(line)
        if r.get("scored") and r.get("correct") is not None:
            rows.append(r)

    def acc(sub):
        n = len(sub)
        if n == 0:
            return (None, 0)
        return (sum(1 for r in sub if r["correct"]) / n, n)

    out = {}
    out["overall"] = acc(rows)
    by_tf = {}
    for tf in sorted(set(r["tf"] for r in rows)):
        by_tf[tf] = acc([r for r in rows if r["tf"] == tf])
    out["by_tf"] = by_tf
    by_combo = {}
    cc = [r for r in rows if "combo" in r]
    for k in sorted(set(r["combo"] for r in cc)):
        by_combo[k] = acc([r for r in cc if r["combo"] == k])
    out["by_combo"] = by_combo
    # live-only (src != train ; live rows have no 'src' key)
    live = [r for r in rows if r.get("src") != "train"]
    out["live_overall"] = acc(live)
    live_tf = {}
    for tf in sorted(set(r["tf"] for r in live)):
        live_tf[tf] = acc([r for r in live if r["tf"] == tf])
    out["live_by_tf"] = live_tf
    out["n_train"] = sum(1 for r in rows if r.get("src") == "train")
    out["n_live"] = len(live)
    return out


# ---------------------------------------------------------------------------
# PART 2 : honest backtest of the backbone on the JSON dataset
# ---------------------------------------------------------------------------
def load_json(sym, tf):
    p = os.path.join(DATA, f"ohlcv_{sym}_{tf}.json")
    if not os.path.exists(p):
        return None
    d = json.load(open(p))
    a = np.array(d["bars"], float)
    if a.ndim != 2 or a.shape[0] < 200:
        return None
    return a   # cols: t,o,h,l,c


def htf_bias(t, o, h, l, c, mult):
    """Resample base series into a higher TF (mult bars per HTF bar), run struct_engine
    to get the trend, and as-of align the HTF trend (valid from HTF bar CLOSE time) back
    onto the base bars. Returns an int array aligned to base bars (no look-ahead)."""
    idx = pd.to_datetime(t.astype("int64"), unit="s")
    df = pd.DataFrame({"Open": o, "High": h, "Low": l, "Close": c}, index=idx)
    # build HTF bars by grouping every `mult` base bars (right-closed -> close known at last bar)
    n = len(c)
    grp = np.arange(n) // mult
    agg = pd.DataFrame({
        "Open": df["Open"].groupby(grp).first(),
        "High": df["High"].groupby(grp).max(),
        "Low":  df["Low"].groupby(grp).min(),
        "Close": df["Close"].groupby(grp).last(),
        "ct":   pd.Series(idx).groupby(grp).last().values,   # close time of the HTF bar
    })
    oo = agg["Open"].values; hh = agg["High"].values
    ll = agg["Low"].values; ccl = agg["Close"].values
    if len(ccl) < (2 * L + 2):
        return np.zeros(n, int)
    tr, *_ = B.struct_engine(oo, hh, ll, ccl, L)
    src = pd.DataFrame({"tr": tr, "_ct": pd.DatetimeIndex(agg["ct"].values)}).sort_values("_ct")
    tgt = pd.DataFrame({"_t": pd.DatetimeIndex(idx)})
    m = pd.merge_asof(tgt, src, left_on="_t", right_on="_ct", direction="backward")
    return np.nan_to_num(m["tr"].values).astype(int)


def build_score(t, o, h, l, c, tf):
    """Vectorised per-bar directional score mirroring rsi_tools.project's backbone.
    Every term at bar i uses only data[:i+1]."""
    n = len(c)
    atr = B.atr_rma(h, l, c, 14)
    rsi = RT.rsi(c, 14)
    # slope over 20 bars (sign), as in project()/build_calls
    slope = np.zeros(n)
    slope[20:] = np.sign(c[20:] - c[:-20])
    # RSI pull
    rsi_pull = np.where(rsi < 30, 1.0, np.where(rsi > 70, -1.0, 0.0))
    rsi_pull = np.nan_to_num(rsi_pull)
    # divergence pull, carried forward (project uses recent_div within 14 bars of 'now';
    # build_calls carries 12 bars). Use causal carry-forward of 12 bars.
    div = np.zeros(n)
    try:
        for d in RT.divergences(h, l, c, rsi, L=5, recent_bars=n):
            b = d["bar"]
            start = b + 5   # = b + L; pivot only confirmable here (no 5-bar look-ahead)
            div[start:min(n, start + 12)] = 1.0 if d["type"] == "bull" else -1.0
    except Exception:
        pass
    # HTF bias: sign(1*b1 + 2*b2 + 2*b3) -- here we have 2 HTFs from this series
    m1, m2 = HTF_MULT[tf]
    b1 = htf_bias(t, o, h, l, c, m1)
    b2 = htf_bias(t, o, h, l, c, m2)
    bias = np.sign(1.0 * b1 + 2.0 * b2)
    w = TFW[tf]
    # project backbone (zones/setup/dominance unavailable offline -> omitted, as in build_calls)
    score = 1.0 * bias + 0.4 * slope + 0.9 * w * rsi_pull + 0.7 * w * div
    return score, atr, rsi, slope, div, bias


def regime_keys(atr, c, rsi, slope, i):
    """Classify bar i into a trend-strength regime (|slope_20|/atr proxy) and RSI regime.
    slope sign is +-1, so trend strength uses the raw 20-bar move magnitude / atr."""
    # raw 20-bar move magnitude relative to ATR
    a = atr[i]
    if not np.isfinite(a) or a <= 0:
        ts = "na"
    else:
        mv = abs(c[i] - c[i - 20]) / a if i >= 20 else 0.0
        if mv < 1.0:
            ts = "chop(<1atr)"
        elif mv < 3.0:
            ts = "trend(1-3atr)"
        else:
            ts = "strong(>3atr)"
    rv = rsi[i]
    if not np.isfinite(rv):
        rk = "na"
    elif rv < 30:
        rk = "oversold(<30)"
    elif rv > 70:
        rk = "overbought(>70)"
    elif rv < 45:
        rk = "weak(30-45)"
    elif rv > 55:
        rk = "strong(55-70)"
    else:
        rk = "mid(45-55)"
    return ts, rk


def part2():
    res = {"by_tf": {}, "by_regime_ts": {}, "by_regime_rsi": {}, "by_symbol": {},
           "m5_horizon": {}, "n_total": 0}
    # accumulators: key -> [hits, n]
    agg_tf = {}
    agg_ts = {}
    agg_rsi = {}
    agg_sym = {}
    agg_m5h = {}     # horizon -> [hits,n]
    total_h = 0

    for tf in TFS:
        Hlist = HORIZONS[tf]
        Hmain = Hlist[-1] if tf != "M5" else 24   # main reported H per TF (M5 -> 24)
        for sym in SYMBOLS:
            a = load_json(sym, tf)
            if a is None:
                continue
            t, o, h, l, c = a[:, 0], a[:, 1], a[:, 2], a[:, 3], a[:, 4]
            n = len(c)
            score, atr, rsi, slope, div, bias = build_score(t, o, h, l, c, tf)
            call = np.sign(score)
            start = 60   # warmup for rsi/atr/htf
            # M5: evaluate all horizons for the horizon-sensitivity table
            for H in Hlist:
                last = n - H - 1
                for i in range(start, last):
                    d = call[i]
                    if d == 0:
                        continue
                    fut = c[i + H]
                    move = (fut - c[i]) / c[i]
                    correct = (abs(move) > THR) and ((move > 0) == (d > 0))
                    if tf == "M5":
                        pc = agg_m5h.setdefault(H, [0, 0]); pc[1] += 1; pc[0] += int(correct)
                    if H == Hmain:
                        # main accumulators
                        kt = agg_tf.setdefault(tf, [0, 0]); kt[1] += 1; kt[0] += int(correct)
                        ks = agg_sym.setdefault((tf, sym), [0, 0]); ks[1] += 1; ks[0] += int(correct)
                        ts, rk = regime_keys(atr, c, rsi, slope, i)
                        kts = agg_ts.setdefault((tf, ts), [0, 0]); kts[1] += 1; kts[0] += int(correct)
                        krs = agg_rsi.setdefault((tf, rk), [0, 0]); krs[1] += 1; krs[0] += int(correct)
                        total_h += 1

    def fin(d):
        out = {}
        for k, (hh, nn) in d.items():
            out[str(k)] = {"acc": round(hh / nn, 4) if nn else None, "n": nn}
        return out

    res["by_tf"] = fin(agg_tf)
    res["by_regime_ts"] = fin(agg_ts)
    res["by_regime_rsi"] = fin(agg_rsi)
    res["by_symbol"] = fin(agg_sym)
    res["m5_horizon"] = fin(agg_m5h)
    res["n_total"] = total_h
    # overall across main accumulator
    th = sum(v[0] for v in agg_tf.values()); tn = sum(v[1] for v in agg_tf.values())
    res["overall"] = {"acc": round(th / tn, 4) if tn else None, "n": tn}
    return res


def fmt(label, acc, n):
    a = "  n/a " if acc is None else f"{100*acc:5.1f}%"
    return f"  {label:26} acc={a}  n={n}"


def main():
    print("=" * 70)
    print("PART 1 -- realised accuracy from predictions.jsonl")
    print("=" * 70)
    p1 = part1()
    a, n = p1["overall"]
    print(fmt("OVERALL (all scored)", a, n))
    print(f"    (train-backfill rows={p1['n_train']}, live rows={p1['n_live']})")
    print("  by tf:")
    for tf, (aa, nn) in p1["by_tf"].items():
        print(fmt("   " + tf, aa, nn))
    print("  by confluence (combo = #styles agreeing):")
    for k, (aa, nn) in p1["by_combo"].items():
        print(fmt(f"   combo={k}", aa, nn))
    la, ln = p1["live_overall"]
    print(fmt("LIVE-ONLY overall", la, ln))
    for tf, (aa, nn) in p1["live_by_tf"].items():
        print(fmt("   live " + tf, aa, nn))

    print()
    print("=" * 70)
    print("PART 2 -- honest backtest of the CURRENT backbone (JSON dataset)")
    print("=" * 70)
    p2 = part2()
    o = p2["overall"]
    print(fmt("OVERALL (main horizons)", o["acc"], o["n"]))
    print("  by tf (main H: M5=24,M15=24,H1=24):")
    for tf, v in p2["by_tf"].items():
        print(fmt("   " + tf, v["acc"], v["n"]))
    print("  M5 horizon sensitivity (12/24/48 bars):")
    for H, v in sorted(p2["m5_horizon"].items(), key=lambda kv: int(kv[0])):
        print(fmt(f"   H={H}", v["acc"], v["n"]))
    print("  by trend-strength regime (|move_20|/atr):")
    for k, v in sorted(p2["by_regime_ts"].items()):
        print(fmt("   " + k, v["acc"], v["n"]))
    print("  by RSI regime:")
    for k, v in sorted(p2["by_regime_rsi"].items()):
        print(fmt("   " + k, v["acc"], v["n"]))
    print("  worst symbols (n>=200, lowest acc):")
    syms = [(k, v) for k, v in p2["by_symbol"].items() if v["n"] >= 200]
    for k, v in sorted(syms, key=lambda kv: kv[1]["acc"])[:8]:
        print(fmt("   " + k, v["acc"], v["n"]))
    print("  best symbols (n>=200, highest acc):")
    for k, v in sorted(syms, key=lambda kv: -kv[1]["acc"])[:5]:
        print(fmt("   " + k, v["acc"], v["n"]))

    # dump machine-readable JSON for the orchestrator
    blob = {"part1": p1, "part2": p2}
    outp = os.path.join(os.path.dirname(__file__), "baseline_acc_result.json")
    with open(outp, "w", encoding="utf-8") as f:
        json.dump(blob, f, default=str, ensure_ascii=False, indent=2)
    print(f"\nwrote {outp}")


if __name__ == "__main__":
    main()
