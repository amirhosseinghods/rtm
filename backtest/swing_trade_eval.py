#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
swing_trade_eval.py -- LEAK-FREE trade evaluator: turn the live projection (direction from the
shipped per-TF logistic + TP/SL sized from the swing-reach model) into simulated trades, and
measure WIN-RATE / expectancy. This is the shared, honest harness the winrate-search agents
parameterise (they pick the config; the harness enforces causality + the fill rules), so the
backtest can't silently leak.

HONEST RULES:
  - Entry signal at bar i uses ONLY causal features (exp_idea-2.feature_matrix) and the FIXED,
    already-walk-forward-validated projection_model weights from web/tuned.json. No per-bar refit.
  - TP/SL are placed from ATR[i] and the per-TF median reach (web/tuned.json swing_model) — both
    known at bar i. The fill is simulated forward on intrabar High/Low over [i+1, i+H]; if TP and
    SL are both touched in the same bar, the SL is taken first (conservative).
  - Leave-symbols-out and time-split modes are provided for honest out-of-sample verification.

Config knobs (the search space):
  { "tau": abstain if |P-0.5|<tau, "tp_mult": TP at tp_mult*Rfav ATR, "sl_mult": SL at sl_mult*Radv ATR,
    "with_trend_only": bool, "rsi_gate": 0|1 (only enter when RSI extreme supports the call),
    "min_ts": float (require trend_strength>=x), "H": horizon }

Run (baseline):  PYTHONIOENCODING=utf-8 python backtest/swing_trade_eval.py
CLI for agents:  PYTHONIOENCODING=utf-8 python backtest/swing_trade_eval.py --eval '{"tf":"M5","tau":0.04,...}'
"""
import os, sys, json, importlib.util
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_spec = importlib.util.spec_from_file_location("swing_lib", os.path.join(ROOT, "backtest", "swing_lib.py"))
SW = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(SW)
exp2 = SW.exp2

TUNED = json.load(open(os.path.join(ROOT, "web", "tuned.json"), encoding="utf-8"))
PROJ = TUNED["projection_model"]
SWING = TUNED["swing_model"]
HSWING = SW.HSWING


def _p_up(X, tf):
    m = PROJ[tf]
    z = float(m["intercept"]) + X @ np.asarray(m["weights"], float)
    return 1.0 / (1.0 + np.exp(-np.clip(z, -30, 30)))


def per_symbol(tf):
    """Causal per-symbol arrays: c,h,l,atr,P(up),rsi_z,ts(trend strength),rsi_pull. One dict per symbol."""
    out = {}
    for sym in exp2.SYMBOLS:
        o = SW.load_ohlc(sym, tf)
        if o is None:
            continue
        t, op, h, l, c = o
        atr = SW.atr_of(h, l, c, 14)
        X, *_ = exp2.feature_matrix(t, op, h, l, c, tf)
        P = _p_up(X, tf)
        # feature columns (exp2 order): 0 bias,1 slope,2 rsi_pull,3 rsi_z,4 div,5 ts,6 ts*slope,7 ...
        out[sym] = dict(t=t, h=h, l=l, c=c, atr=atr, P=P,
                        bias=X[:, 0], rsi_pull=X[:, 2], rsi_z=X[:, 3], ts=X[:, 5])
    return out


def eval_rule(tf, cfg, data=None, syms=None, t_lo=None, t_hi=None):
    """Simulate trades for one TF under cfg. Optional symbol subset and time window (causal).
    Returns winrate / expectancy(R) / coverage / n."""
    H = int(cfg.get("H", HSWING[tf]))
    tau = float(cfg.get("tau", 0.0))
    tp_mult = float(cfg.get("tp_mult", 1.0))
    sl_mult = float(cfg.get("sl_mult", 1.0))
    wto = bool(cfg.get("with_trend_only", False))
    rsi_gate = bool(cfg.get("rsi_gate", False))
    min_ts = float(cfg.get("min_ts", 0.0))
    sm = SWING[tf]; Rup = float(sm["up"]); Rdn = float(sm["dn"])
    data = data or per_symbol(tf)
    syms = syms or list(data.keys())

    wins = 0; n = 0; total_bars = 0; Rsum = 0.0
    for s in syms:
        d = data.get(s)
        if d is None:
            continue
        c, h, l, atr, P = d["c"], d["h"], d["l"], d["atr"], d["P"]
        bias, rsi_z, ts = d["bias"], d["rsi_z"], d["ts"]
        t = d["t"]; N = len(c); last = N - H - 1
        for i in range(SW.WARMUP, last):
            if t_lo is not None and t[i] < t_lo:
                continue
            if t_hi is not None and t[i] >= t_hi:
                continue
            total_bars += 1
            conf = abs(P[i] - 0.5)
            if conf < tau:
                continue
            dirn = 1 if P[i] >= 0.5 else -1
            if wto and np.sign(bias[i]) != 0 and np.sign(bias[i]) != dirn:
                continue
            if min_ts and ts[i] < min_ts:
                continue
            if rsi_gate:
                # only enter when RSI extreme supports the call (oversold->long, overbought->short)
                if dirn > 0 and rsi_z[i] > -0.6:   # rsi> ~41 -> not oversold enough
                    continue
                if dirn < 0 and rsi_z[i] < 0.6:    # rsi< ~59 -> not overbought enough
                    continue
            a = atr[i]
            if not (a > 0):
                continue
            entry = c[i]
            Rfav = (Rup if dirn > 0 else Rdn)
            Radv = (Rdn if dirn > 0 else Rup)
            tp = entry + dirn * tp_mult * Rfav * a
            sl = entry - dirn * sl_mult * Radv * a
            risk = abs(entry - sl)
            if risk <= 0:
                continue
            wH = h[i + 1:i + 1 + H]; wL = l[i + 1:i + 1 + H]
            if dirn > 0:
                tp_hits = wH >= tp; sl_hits = wL <= sl
            else:
                tp_hits = wL <= tp; sl_hits = wH >= sl
            ktp = int(np.argmax(tp_hits)) if tp_hits.any() else 10 ** 9
            ksl = int(np.argmax(sl_hits)) if sl_hits.any() else 10 ** 9
            n += 1
            if ktp == 10 ** 9 and ksl == 10 ** 9:
                exitp = c[i + H]                          # timed out -> close
                R = (exitp - entry) * dirn / risk
            elif ksl <= ktp:                              # SL first (ties -> SL, conservative)
                R = -1.0 * sl_mult * Radv / (sl_mult * Radv)   # = -1R
                R = (sl - entry) * dirn / risk
            else:                                         # TP first
                R = (tp - entry) * dirn / risk
            Rsum += R
            if R > 0:
                wins += 1
    return dict(tf=tf, winrate=(wins / n if n else None), expR=(Rsum / n if n else None),
                coverage=(n / total_bars if total_bars else 0.0), n=int(n), trades=int(n),
                cfg=cfg)


def loso(tf, cfg, nfolds=5, seed=42):
    """Leave-symbols-out: report pooled winrate/expR on held-out symbols only."""
    data = per_symbol(tf)
    syms = list(data.keys())
    rng = np.random.RandomState(seed); order = syms[:]; rng.shuffle(order)
    folds = [order[i::nfolds] for i in range(nfolds)]
    w = nn = 0; Rsum = 0.0; cov_n = cov_d = 0
    for held in folds:
        r = eval_rule(tf, cfg, data=data, syms=held)
        if r["n"]:
            w += r["winrate"] * r["n"]; Rsum += r["expR"] * r["n"]; nn += r["n"]
        cov_n += r["n"]; cov_d += r["n"] / r["coverage"] if r["coverage"] else 0
    return dict(tf=tf, winrate=(w / nn if nn else None), expR=(Rsum / nn if nn else None),
                coverage=(cov_n / cov_d if cov_d else 0.0), n=int(nn), cfg=cfg)


# fixed per-TF TIME split (70% train / 30% test), agent-proof: every caller gets the SAME boundary
# so a config tuned on "train" is honestly scored out-of-time on "test".
_SPLIT_CACHE = {}
def split_time(tf, frac=0.70):
    if tf in _SPLIT_CACHE:
        return _SPLIT_CACHE[tf]
    data = per_symbol(tf); H = HSWING[tf]
    ts = []
    for d in data.values():
        N = len(d["c"]); ts.append(d["t"][SW.WARMUP:max(SW.WARMUP, N - H - 1)])
    allt = np.concatenate(ts) if ts else np.array([0.0])
    st = float(np.quantile(allt, frac))
    _SPLIT_CACHE[tf] = st
    return st


def eval_split(tf, cfg, which):
    """which in {'train','test'}: eval on the time side of the fixed 70/30 split (PURGED by H)."""
    st = split_time(tf)
    H = HSWING[tf]
    data = per_symbol(tf)
    # purge: train ends H*tf before st so its labels don't peek into test
    if which == "train":
        return eval_rule(tf, cfg, data=data, t_hi=st)
    return eval_rule(tf, cfg, data=data, t_lo=st)


def main():
    if "--eval" in sys.argv:
        cfg = json.loads(sys.argv[sys.argv.index("--eval") + 1])
        tf = cfg.get("tf", "M5")
        mode = cfg.get("mode", "all")
        if mode in ("train", "test"):
            r = eval_split(tf, cfg, mode)
        elif mode == "loso":
            r = loso(tf, cfg)
        elif mode == "split":   # both sides at once (pick on train, read test)
            r = {"train": eval_split(tf, cfg, "train"), "test": eval_split(tf, cfg, "test")}
        else:
            r = eval_rule(tf, cfg)
        print(json.dumps(r))
        return
    print("=" * 76)
    print("BASELINE trade backtest — projection direction + reach-sized TP/SL (1R:1R)")
    print("=" * 76)
    for tf in exp2.TFS:
        base = {"tau": PROJ[tf]["tau"], "tp_mult": 1.0, "sl_mult": 1.0, "H": HSWING[tf]}
        r = eval_rule(tf, base)
        lo = loso(tf, base)
        print(f"\n[{tf}]  tau={base['tau']}  TP=1*Rfav  SL=1*Radv")
        print(f"   in-sample : winrate={r['winrate']:.3f}  expR={r['expR']:+.3f}  cov={r['coverage']:.3f}  n={r['n']}")
        print(f"   leave-sym : winrate={lo['winrate']:.3f}  expR={lo['expR']:+.3f}  n={lo['n']}")


if __name__ == "__main__":
    main()
