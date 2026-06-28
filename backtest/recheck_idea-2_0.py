#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
recheck_idea-2_0.py -- ADVERSARIAL held-out recheck of exp_idea-2.

Two independent stress tests, both leak-aware:
  (A) SYMBOL HALF-SPLIT: fit the logit on the FIRST half of symbols (sorted), test on
      the SECOND half. Single clean out-of-sample split (no pooling tricks). Reports
      baseline vs improved on the held-out symbols only.
  (B) TIME 70/30 SPLIT: per symbol, fit on the first 70% of bars (pooled across symbols),
      test on the last 30%. This catches any regime/time leakage the symbol split misses.
  (C) LEAK-FREE features: the original build_score writes the divergence pull from the
      PIVOT bar b, but the pivot is only confirmed L=5 bars later -> a 5-bar look-ahead.
      We rebuild features with div shifted to start at b+L (causal). Re-run (A) with it.

Scoring identical to the experiment: correct iff |move|>THR AND (move>0)==(call up),
flat counts WRONG. Features causal except the div leak we explicitly fix in (C).
"""
import os, sys, json
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backtest"))
import rsi_tools as RT
import rtm_bt as B

DATA = os.path.join(ROOT, "site", "data")
SYMBOLS = ["BTCUSDT","ETHUSDT","XRPUSDT","SOLUSDT","BNBUSDT","ADAUSDT","DOGEUSDT",
           "AVAXUSDT","LINKUSDT","LTCUSDT","DOTUSDT","PAXGUSDT","XAUUSD"]
TFS = ["M5","M15","H1"]
THR = 0.0005
TFW = {"M5": 0.6, "M15": 0.8, "H1": 1.0}
HTF_MULT = {"M5": [3, 12], "M15": [4, 16], "H1": [4, 24]}
HMAIN = {"M5": 24, "M15": 24, "H1": 24}
L = 5
WARMUP = 60


def load_json(sym, tf):
    p = os.path.join(DATA, f"ohlcv_{sym}_{tf}.json")
    if not os.path.exists(p):
        return None
    d = json.load(open(p))
    a = np.array(d["bars"], float)
    if a.ndim != 2 or a.shape[0] < 200:
        return None
    return a


def htf_bias(t, o, h, l, c, mult):
    import pandas as pd
    idx = pd.to_datetime(t.astype("int64"), unit="s")
    df = pd.DataFrame({"Open": o, "High": h, "Low": l, "Close": c}, index=idx)
    n = len(c)
    grp = np.arange(n) // mult
    agg = pd.DataFrame({
        "Open": df["Open"].groupby(grp).first(),
        "High": df["High"].groupby(grp).max(),
        "Low":  df["Low"].groupby(grp).min(),
        "Close": df["Close"].groupby(grp).last(),
        "ct":   pd.Series(idx).groupby(grp).last().values,
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


def build_score(t, o, h, l, c, tf, fix_div_leak=False):
    n = len(c)
    atr = B.atr_rma(h, l, c, 14)
    rsi = RT.rsi(c, 14)
    slope = np.zeros(n)
    slope[20:] = np.sign(c[20:] - c[:-20])
    rsi_pull = np.where(rsi < 30, 1.0, np.where(rsi > 70, -1.0, 0.0))
    rsi_pull = np.nan_to_num(rsi_pull)
    div = np.zeros(n)
    try:
        for d in RT.divergences(h, l, c, rsi, L=5, recent_bars=n):
            b = d["bar"]
            start = (b + L) if fix_div_leak else b   # causal: divergence only known at b+L
            div[start:min(n, start + 12)] = 1.0 if d["type"] == "bull" else -1.0
    except Exception:
        pass
    m1, m2 = HTF_MULT[tf]
    b1 = htf_bias(t, o, h, l, c, m1)
    b2 = htf_bias(t, o, h, l, c, m2)
    bias = np.sign(1.0 * b1 + 2.0 * b2)
    w = TFW[tf]
    score = 1.0 * bias + 0.4 * slope + 0.9 * w * rsi_pull + 0.7 * w * div
    return score, atr, rsi, slope, rsi_pull, div, bias


def feature_matrix(t, o, h, l, c, tf, fix_div_leak=False):
    n = len(c)
    score, atr, rsi, slope, rsi_pull, div, bias = build_score(t, o, h, l, c, tf, fix_div_leak)
    rsi_z = np.nan_to_num((rsi - 50.0) / 15.0)
    with np.errstate(divide="ignore", invalid="ignore"):
        mv = np.zeros(n); mv[20:] = np.abs(c[20:] - c[:-20])
        ratio = np.where(atr > 0, mv / atr, 0.0)
    ts = np.clip(np.nan_to_num(ratio), 0.0, 5.0)
    is_h1 = 1.0 if tf == "H1" else 0.0
    X = np.column_stack([bias, slope, rsi_pull, rsi_z, div, ts, ts * slope, rsi_z * is_h1])
    return X, score


def fit_logistic(X, y, C=1.0, iters=300, lr=0.5):
    n, d = X.shape
    mu = X.mean(axis=0); sd = X.std(axis=0)
    sd = np.where(sd < 1e-9, 1.0, sd)
    Xs = (X - mu) / sd
    npos = max(1, int(y.sum())); nneg = max(1, int(n - y.sum()))
    wpos = n / (2.0 * npos); wneg = n / (2.0 * nneg)
    sw = np.where(y == 1, wpos, wneg)
    lam = 1.0 / (C * n)
    b0 = 0.0; w = np.zeros(d)
    for _ in range(iters):
        z = b0 + Xs @ w
        p = 1.0 / (1.0 + np.exp(-np.clip(z, -30, 30)))
        g = sw * (p - y)
        gw = Xs.T @ g / n + 2.0 * lam * w
        gb = g.sum() / n
        w -= lr * gw; b0 -= lr * gb
    w_raw = w / sd
    b0_raw = b0 - np.sum(w * mu / sd)
    return b0_raw, w_raw


def predict_p(X, w0, w):
    z = w0 + X @ w
    return 1.0 / (1.0 + np.exp(-np.clip(z, -30, 30)))


def build_tf_dataset(tf, H, fix_div_leak=False):
    out = {}
    for sym in SYMBOLS:
        a = load_json(sym, tf)
        if a is None:
            continue
        t, o, h, l, c = a[:, 0], a[:, 1], a[:, 2], a[:, 3], a[:, 4]
        n = len(c)
        X, score = feature_matrix(t, o, h, l, c, tf, fix_div_leak)
        bcall = np.sign(score)
        last = n - H - 1
        idx = np.arange(WARMUP, last)
        if len(idx) == 0:
            continue
        move = (c[idx + H] - c[idx]) / c[idx]
        y_up = (move > 0).astype(float)
        flat = np.abs(move) <= THR
        out[sym] = dict(X=X[idx], bcall=bcall[idx], move=move, y_up=y_up,
                        flat=flat, n=len(idx), idx=idx)
    return out


def score_split(ds, train_syms, test_syms):
    """Fit on train_syms (non-flat bars), score baseline+improved on test_syms (full cov)."""
    Xtr = []; ytr = []
    for s in train_syms:
        if s not in ds: continue
        d = ds[s]; keep = ~d["flat"]
        Xtr.append(d["X"][keep]); ytr.append(d["y_up"][keep])
    if not Xtr:
        return None
    Xtr = np.vstack(Xtr); ytr = np.concatenate(ytr)
    w0, w = fit_logistic(Xtr, ytr, C=1.0)
    bh = bn = ih = inn = 0
    for s in test_syms:
        if s not in ds: continue
        d = ds[s]
        move = d["move"]; flat = d["flat"]; bcall = d["bcall"]
        P = predict_p(d["X"], w0, w)
        bmask = bcall != 0
        b_ok = (~flat) & ((move > 0) == (bcall > 0))
        bh += int((b_ok & bmask).sum()); bn += int(bmask.sum())
        i_ok = (~flat) & ((P >= 0.5) == (move > 0))
        ih += int(i_ok.sum()); inn += int(len(P))
    return dict(base=bh / bn if bn else None, imp=ih / inn if inn else None,
                n_base=bn, n_imp=inn, w=w, w0=w0)


def time_split(ds, frac=0.70):
    """Per-symbol fit on first frac of evaluable bars (pooled), test on the rest (pooled)."""
    Xtr = []; ytr = []
    test = {}
    for s, d in ds.items():
        m = len(d["X"]); cut = int(m * frac)
        keep_tr = (~d["flat"][:cut])
        Xtr.append(d["X"][:cut][keep_tr]); ytr.append(d["y_up"][:cut][keep_tr])
        test[s] = dict(X=d["X"][cut:], move=d["move"][cut:], flat=d["flat"][cut:],
                       bcall=d["bcall"][cut:])
    Xtr = np.vstack(Xtr); ytr = np.concatenate(ytr)
    w0, w = fit_logistic(Xtr, ytr, C=1.0)
    bh = bn = ih = inn = 0
    for s, d in test.items():
        move = d["move"]; flat = d["flat"]; bcall = d["bcall"]
        P = predict_p(d["X"], w0, w)
        bmask = bcall != 0
        b_ok = (~flat) & ((move > 0) == (bcall > 0))
        bh += int((b_ok & bmask).sum()); bn += int(bmask.sum())
        i_ok = (~flat) & ((P >= 0.5) == (move > 0))
        ih += int(i_ok.sum()); inn += int(len(P))
    return dict(base=bh / bn if bn else None, imp=ih / inn if inn else None,
                n_base=bn, n_imp=inn)


def run_all(fix_div_leak):
    syms_sorted = sorted(SYMBOLS)
    half = len(syms_sorted) // 2
    train_half = syms_sorted[:half]      # fit here
    test_half = syms_sorted[half:]       # held-out
    # also reverse direction so neither half is "lucky"
    tag = "LEAK-FREE div" if fix_div_leak else "ORIGINAL (div leak present)"
    print("=" * 74)
    print(f"RECHECK [{tag}]")
    print(f"  symbol half-split: fit={train_half}")
    print(f"                     test(held-out)={test_half}")
    print("=" * 74)

    agg = {"A_fwd": [0, 0, 0, 0], "A_rev": [0, 0, 0, 0], "B_time": [0, 0, 0, 0]}
    # [base_h_equiv? ] we accumulate weighted acc via n; store (base_acc*n, n_base, imp_acc*n, n_imp)
    sums = {"A": dict(bh=0, bn=0, ih=0, inn=0),
            "Arev": dict(bh=0, bn=0, ih=0, inn=0),
            "B": dict(bh=0, bn=0, ih=0, inn=0)}

    for tf in TFS:
        ds = build_tf_dataset(tf, HMAIN[tf], fix_div_leak)
        # A: forward split
        rA = score_split(ds, train_half, test_half)
        # A reversed: fit on test_half, score on train_half (so all symbols get held-out once)
        rArev = score_split(ds, test_half, train_half)
        # B: time split
        rB = time_split(ds, 0.70)
        for key, r in (("A", rA), ("Arev", rArev), ("B", rB)):
            if r is None: continue
            sums[key]["bh"] += (r["base"] or 0) * r["n_base"]; sums[key]["bn"] += r["n_base"]
            sums[key]["ih"] += (r["imp"] or 0) * r["n_imp"]; sums[key]["inn"] += r["n_imp"]
        db = (rA["imp"] - rA["base"]) if rA else float("nan")
        print(f"\n[{tf}] H={HMAIN[tf]}")
        if rA:
            print(f"  (A) held-out symbols  base={rA['base']:.4f}  improved={rA['imp']:.4f}  "
                  f"delta={rA['imp']-rA['base']:+.4f}  n={rA['n_imp']}")
        if rArev:
            print(f"  (A') reversed split   base={rArev['base']:.4f}  improved={rArev['imp']:.4f}  "
                  f"delta={rArev['imp']-rArev['base']:+.4f}  n={rArev['n_imp']}")
        if rB:
            print(f"  (B) time 70/30        base={rB['base']:.4f}  improved={rB['imp']:.4f}  "
                  f"delta={rB['imp']-rB['base']:+.4f}  n={rB['n_imp']}")

    print("\n" + "-" * 74)
    out = {}
    for key, lbl in (("A", "held-out symbols (fwd)"),
                     ("Arev", "held-out symbols (rev)"),
                     ("B", "time 70/30")):
        s = sums[key]
        b = s["bh"] / s["bn"] if s["bn"] else None
        im = s["ih"] / s["inn"] if s["inn"] else None
        out[key] = dict(base=b, imp=im, n=s["inn"])
        if b is not None and im is not None:
            print(f"POOLED {lbl:24}: base={b:.4f}  improved={im:.4f}  "
                  f"delta={im-b:+.4f}  n={s['inn']}")
    # all-held-out (A fwd + A rev = every symbol scored exactly once out-of-sample)
    bh = sums["A"]["bh"] + sums["Arev"]["bh"]; bn = sums["A"]["bn"] + sums["Arev"]["bn"]
    ih = sums["A"]["ih"] + sums["Arev"]["ih"]; inn = sums["A"]["inn"] + sums["Arev"]["inn"]
    out["ALL_OOS"] = dict(base=bh / bn, imp=ih / inn, n=inn)
    print(f"POOLED {'ALL symbols out-of-sample':24}: base={bh/bn:.4f}  improved={ih/inn:.4f}  "
          f"delta={ih/inn-bh/bn:+.4f}  n={inn}")
    return out


def main():
    res_orig = run_all(fix_div_leak=False)
    print("\n\n")
    res_fixed = run_all(fix_div_leak=True)
    print("\n" + "=" * 74)
    print("SUMMARY")
    print("=" * 74)
    for tag, res in (("ORIGINAL", res_orig), ("LEAK-FREE", res_fixed)):
        a = res["ALL_OOS"]
        print(f"  {tag:10} all-symbols-OOS: base={a['base']:.4f} improved={a['imp']:.4f} "
              f"delta={a['imp']-a['base']:+.4f} n={a['n']}")
    blob = {"original": res_orig, "leak_free": res_fixed}
    json.dump(blob, open(os.path.join(os.path.dirname(__file__),
              "recheck_idea-2_0_result.json"), "w"), default=str, indent=2)


if __name__ == "__main__":
    main()
