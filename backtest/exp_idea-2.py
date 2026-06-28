#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
exp_idea-2.py -- Cross-symbol fitted logistic weights vs the hand-tuned project() backbone.

HYPOTHESIS: the 6 fixed score coefficients in project()/build_calls are a hand-set prior
never fit to data. Per-TF features carry conditional, sign-flippable edge (RSI is right on H1,
wrong on M5). A per-TF logistic regression on the SAME look-ahead-safe features can re-sign and
re-scale each term, and its calibrated P(up) gives an honest abstention lever.

HONEST RULES (enforced):
  - Every feature at bar i uses ONLY data[:i+1] (build_score is causal; htf_bias uses merge_asof
    backward; rsi/atr/slope/div all causal).
  - Scored vs close[i+H]: correct iff |move|>THR AND (move>0)==(P>=0.5); flat (|move|<=THR) counts WRONG.
  - Leave-symbols-out GroupKFold (groups=symbol): a symbol's bars are NEVER in its own training fold,
    so the fitted weights applied to a symbol never saw that symbol -> no cross-split leakage.
  - Logistic weights are fit ONLY on training-fold symbols; held-out fold touched once for scoring.
  - tau (abstention threshold) is swept on TRAIN-fold symbols only and applied out-of-fold.

Baseline = EXACT same bars/features/scoring, only the call differs (fixed-weight sign(score)
from build_score vs fitted-logit sign(P-0.5)).

Run: PYTHONIOENCODING=utf-8 python backtest/exp_idea-2.py
"""
import os, sys, json
import numpy as np
import pandas as pd

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
HMAIN = {"M5": 24, "M15": 24, "H1": 24}        # primary horizons (match baseline)
M5_EXTRA_H = [12, 48]                            # horizon-robustness check on M5
L = 5
NFOLDS = 5
TAUS = [0.0, 0.02, 0.04, 0.06, 0.08, 0.10, 0.12, 0.15]
WARMUP = 60


# ---------------------------------------------------------------------------
# data / features (build_score cloned from baseline_acc.py -- identical causality)
# ---------------------------------------------------------------------------
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


def build_score(t, o, h, l, c, tf):
    """Verbatim clone of baseline_acc.build_score -> identical causality + baseline call."""
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
            start = b + 5   # = b + L; pivot only confirmable here (no 5-bar look-ahead)
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


def feature_matrix(t, o, h, l, c, tf):
    """Per-bar features for the logit. All causal (from build_score outputs)."""
    n = len(c)
    score, atr, rsi, slope, rsi_pull, div, bias = build_score(t, o, h, l, c, tf)
    rsi_z = np.nan_to_num((rsi - 50.0) / 15.0)
    # trend strength = clip(|c[i]-c[i-20]|/atr[i], 0, 5)   (causal)
    ts = np.zeros(n)
    with np.errstate(divide="ignore", invalid="ignore"):
        mv = np.zeros(n)
        mv[20:] = np.abs(c[20:] - c[:-20])
        ratio = np.where(atr > 0, mv / atr, 0.0)
    ts = np.clip(np.nan_to_num(ratio), 0.0, 5.0)
    is_h1 = 1.0 if tf == "H1" else 0.0
    # x columns: bias, slope_sign, rsi_pull, rsi_z, div, trend_strength,
    #            trend_strength*slope_sign, rsi_z*1{H1}
    X = np.column_stack([
        bias, slope, rsi_pull, rsi_z, div, ts, ts * slope, rsi_z * is_h1,
    ])
    return X, c, score, atr, rsi, slope, div


FEAT_NAMES = ["bias", "slope_sign", "rsi_pull", "rsi_z", "div",
              "trend_strength", "ts*slope", "rsi_z*1{H1}"]


# ---------------------------------------------------------------------------
# logistic regression in numpy (L2, class_weight='balanced'), IRLS-ish via GD
# ---------------------------------------------------------------------------
def fit_logistic(X, y, C=1.0, iters=300, lr=0.5):
    """L2-regularised logistic regression with balanced class weights.
    Standardises features (using train stats) for conditioning; folds the scaling
    back into the returned (w0, w) so caller can apply to raw X.
    Returns (w0_raw, w_raw)."""
    n, d = X.shape
    mu = X.mean(axis=0)
    sd = X.std(axis=0)
    sd = np.where(sd < 1e-9, 1.0, sd)
    Xs = (X - mu) / sd
    # balanced class weights: weight_c = n / (2 * n_c)
    npos = max(1, int(y.sum())); nneg = max(1, int(n - y.sum()))
    wpos = n / (2.0 * npos); wneg = n / (2.0 * nneg)
    sw = np.where(y == 1, wpos, wneg)
    lam = 1.0 / (C * n)        # sklearn-like: C scales the data term; here L2 = lam*||w||^2
    b0 = 0.0
    w = np.zeros(d)
    for _ in range(iters):
        z = b0 + Xs @ w
        p = 1.0 / (1.0 + np.exp(-np.clip(z, -30, 30)))
        g = sw * (p - y)
        gw = Xs.T @ g / n + 2.0 * lam * w
        gb = g.sum() / n
        # Hessian diag-ish step: use simple scaled GD; fairly stable on standardised X
        w -= lr * gw
        b0 -= lr * gb
    # fold standardisation back: z = b0 + sum w_j*(x_j-mu_j)/sd_j
    w_raw = w / sd
    b0_raw = b0 - np.sum(w * mu / sd)
    return b0_raw, w_raw


def predict_p(X, w0, w):
    z = w0 + X @ w
    return 1.0 / (1.0 + np.exp(-np.clip(z, -30, 30)))


# ---------------------------------------------------------------------------
# build per-(tf) pooled arrays once
# ---------------------------------------------------------------------------
def build_tf_dataset(tf, H):
    """Returns dict per symbol: X, c, baseline_call, valid index mask of evaluable bars."""
    out = {}
    for sym in SYMBOLS:
        a = load_json(sym, tf)
        if a is None:
            continue
        t, o, h, l, c = a[:, 0], a[:, 1], a[:, 2], a[:, 3], a[:, 4]
        n = len(c)
        X, c2, score, atr, rsi, slope, div = feature_matrix(t, o, h, l, c, tf)
        bcall = np.sign(score)
        last = n - H - 1
        idx = np.arange(WARMUP, last)
        if len(idx) == 0:
            continue
        move = (c[idx + H] - c[idx]) / c[idx]
        y_up = (move > 0).astype(float)
        flat = np.abs(move) <= THR
        out[sym] = dict(X=X[idx], bcall=bcall[idx], move=move, y_up=y_up,
                        flat=flat, n=len(idx))
    return out


def grouped_folds(symbols, nfolds, seed=0):
    rng = np.random.RandomState(seed)
    syms = list(symbols)
    rng.shuffle(syms)
    folds = [[] for _ in range(nfolds)]
    for i, s in enumerate(syms):
        folds[i % nfolds].append(s)
    return folds


def run_tf(tf, H, verbose=False):
    ds = build_tf_dataset(tf, H)
    syms = [s for s in SYMBOLS if s in ds]
    folds = grouped_folds(syms, NFOLDS, seed=42)

    # out-of-fold collectors
    P_all = {}        # sym -> P(up) out of fold
    coef_accum = []   # store coefs per fold for sign audit
    for fi, test_syms in enumerate(folds):
        train_syms = [s for s in syms if s not in test_syms]
        if not train_syms or not test_syms:
            continue
        # fit on train symbols (exclude flat bars from FITTING only)
        Xtr = []; ytr = []
        for s in train_syms:
            d = ds[s]
            keep = ~d["flat"]
            Xtr.append(d["X"][keep]); ytr.append(d["y_up"][keep])
        Xtr = np.vstack(Xtr); ytr = np.concatenate(ytr)
        w0, w = fit_logistic(Xtr, ytr, C=1.0)
        coef_accum.append((w0, w))
        for s in test_syms:
            P_all[s] = predict_p(ds[s]["X"], w0, w)

    # ----- full-coverage scoring (tau=0), pooled out-of-fold -----
    def score_calls(tau):
        bh = bn = ih = inn = 0          # baseline hits/n, improved hits/n (calls made)
        for s in syms:
            if s not in P_all:
                continue
            d = ds[s]; P = P_all[s]
            move = d["move"]; flat = d["flat"]; bcall = d["bcall"]
            correct_up = (move > 0)
            # baseline: full coverage where bcall!=0
            bmask = bcall != 0
            b_ok = (~flat) & ((move > 0) == (bcall > 0))
            bh += int((b_ok & bmask).sum()); bn += int(bmask.sum())
            # improved: call only where |P-0.5|>=tau
            conf = np.abs(P - 0.5)
            imask = conf >= tau
            i_dir_up = P >= 0.5
            i_ok = (~flat) & (i_dir_up == correct_up)
            ih += int((i_ok & imask).sum()); inn += int(imask.sum())
        return bh, bn, ih, inn

    bh, bn, ih, inn = score_calls(0.0)
    base_acc = bh / bn if bn else None
    imp_acc_full = ih / inn if inn else None

    # ----- per-symbol out-of-fold improved acc (tau=0) for sanity -----
    per_sym = {}
    for s in syms:
        if s not in P_all:
            continue
        d = ds[s]; P = P_all[s]
        flat = d["flat"]; move = d["move"]
        i_ok = (~flat) & ((P >= 0.5) == (move > 0))
        b_ok = (~flat) & ((d["bcall"] > 0) == (move > 0)) & (d["bcall"] != 0)
        bmask = d["bcall"] != 0
        per_sym[s] = dict(imp=float(i_ok.mean()),
                          base=float(b_ok.sum() / bmask.sum()) if bmask.sum() else None,
                          n=int(d["n"]))

    # ----- abstention curve (tau swept; reported out-of-fold) -----
    # NOTE: tau is an honest lever because P comes from a fold that never saw the symbol.
    # We additionally pick tau* on TRAIN symbols per fold to report an honest applied-tau number.
    curve = []
    total_bars = sum(ds[s]["n"] for s in syms if s in P_all)
    for tau in TAUS:
        _, _, ih2, inn2 = score_calls(tau)
        cov = inn2 / total_bars if total_bars else 0.0
        curve.append((tau, ih2 / inn2 if inn2 else None, cov, inn2))

    # honest applied-tau: choose tau* on train-fold symbols (maximise acc s.t. cov>=0.20),
    # apply to held-out fold. Re-run per fold.
    applied_h = applied_n = 0
    for fi, test_syms in enumerate(folds):
        train_syms = [s for s in syms if s not in test_syms and s in P_all]
        test_in = [s for s in test_syms if s in P_all]
        if not train_syms or not test_in:
            continue
        # sweep tau on train symbols
        best_tau = 0.0; best_acc = -1
        for tau in TAUS:
            th = tn = 0
            for s in train_syms:
                d = ds[s]; P = P_all[s]
                conf = np.abs(P - 0.5); m = conf >= tau
                ok = (~d["flat"]) & ((P >= 0.5) == (d["move"] > 0))
                th += int((ok & m).sum()); tn += int(m.sum())
            cov = tn / sum(ds[s]["n"] for s in train_syms)
            if cov >= 0.20 and tn:
                acc = th / tn
                if acc > best_acc:
                    best_acc = acc; best_tau = tau
        for s in test_in:
            d = ds[s]; P = P_all[s]
            conf = np.abs(P - 0.5); m = conf >= best_tau
            ok = (~d["flat"]) & ((P >= 0.5) == (d["move"] > 0))
            applied_h += int((ok & m).sum()); applied_n += int(m.sum())

    # ----- sign-flip audit: averaged coefs -----
    if coef_accum:
        W = np.mean([c[1] for c in coef_accum], axis=0)
        W0 = np.mean([c[0] for c in coef_accum])
    else:
        W = np.zeros(len(FEAT_NAMES)); W0 = 0.0

    return dict(
        tf=tf, H=H, base_acc=base_acc, imp_acc_full=imp_acc_full,
        n=bn, n_full=inn, cov_full=inn / total_bars if total_bars else 0,
        curve=curve, per_sym=per_sym, coef=dict(zip(FEAT_NAMES, [round(float(x),4) for x in W])),
        intercept=round(float(W0),4), applied_acc=applied_h/applied_n if applied_n else None,
        applied_cov=applied_n/total_bars if total_bars else 0, applied_n=applied_n,
        total_bars=total_bars,
    )


def se(p, n):
    if not n or p is None:
        return None
    return (p * (1 - p) / n) ** 0.5


def main():
    print("=" * 74)
    print("EXP idea-2 : per-TF fitted logistic weights vs fixed-weight backbone")
    print("leave-symbols-out GroupKFold (5), out-of-fold pooled scoring")
    print("=" * 74)

    results = {}
    for tf in TFS:
        r = run_tf(tf, HMAIN[tf])
        results[tf] = r
        s = se(r["imp_acc_full"], r["n_full"])
        sb = se(r["base_acc"], r["n"])
        print(f"\n[{tf}] H={r['H']}  n={r['n']}")
        print(f"   baseline_acc (fixed weights)   = {r['base_acc']:.4f}  (SE~{sb:.4f})")
        print(f"   improved_acc (logit, full cov) = {r['imp_acc_full']:.4f}  (SE~{s:.4f})  cov={r['cov_full']:.3f}")
        d = r["imp_acc_full"] - r["base_acc"]
        print(f"   delta = {d:+.4f}   (2*SE gate ~ {2*s:.4f})")
        print(f"   abstention curve (tau, acc_on_calls, coverage, n):")
        for tau, acc, cov, nn in r["curve"]:
            am = f"{acc:.4f}" if acc is not None else " n/a "
            print(f"      tau={tau:.2f}  acc={am}  cov={cov:.3f}  n={nn}")
        print(f"   honest applied-tau (tau* on train, cov>=20%): acc={r['applied_acc']:.4f} "
              f"cov={r['applied_cov']:.3f} n={r['applied_n']}")
        print(f"   fitted coefs (avg over folds): intercept={r['intercept']}")
        for fn, cv in r["coef"].items():
            print(f"      {fn:16} {cv:+.4f}")

    # per-symbol sanity on worst baseline M5 symbols
    print("\n" + "-" * 74)
    print("per-symbol out-of-fold sanity (M5 worst baseline symbols):")
    m5 = results["M5"]["per_sym"]
    for s in ["PAXGUSDT", "ETHUSDT", "BTCUSDT"]:
        if s in m5:
            v = m5[s]
            bb = f"{v['base']:.4f}" if v['base'] is not None else "n/a"
            print(f"   {s:10} base={bb}  improved(oof)={v['imp']:.4f}  n={v['n']}")

    # M5 horizon robustness
    print("\n" + "-" * 74)
    print("M5 horizon robustness (improved full-cov vs baseline):")
    m5_rob = {}
    for H in M5_EXTRA_H:
        r = run_tf("M5", H)
        m5_rob[H] = r
        print(f"   H={H:2d}  baseline={r['base_acc']:.4f}  improved={r['imp_acc_full']:.4f}  "
              f"delta={r['imp_acc_full']-r['base_acc']:+.4f}  n={r['n']}")

    # ---- pooled overall (main horizons) ----
    tot_bn = sum(results[tf]["n"] for tf in TFS)
    tot_bh = sum(results[tf]["base_acc"] * results[tf]["n"] for tf in TFS)
    tot_in = sum(results[tf]["n_full"] for tf in TFS)
    tot_ih = sum(results[tf]["imp_acc_full"] * results[tf]["n_full"] for tf in TFS)
    base_overall = tot_bh / tot_bn
    imp_overall = tot_ih / tot_in
    print("\n" + "=" * 74)
    print(f"OVERALL (pooled, main H): baseline={base_overall:.4f}  improved(full)={imp_overall:.4f}  "
          f"delta={imp_overall-base_overall:+.4f}  n={tot_bn}")

    # pooled applied-tau coverage/acc
    ap_h = sum((results[tf]["applied_acc"] or 0) * results[tf]["applied_n"] for tf in TFS)
    ap_n = sum(results[tf]["applied_n"] for tf in TFS)
    ap_tb = sum(results[tf]["total_bars"] for tf in TFS)
    print(f"OVERALL applied-tau: acc={ap_h/ap_n:.4f}  cov={ap_n/ap_tb:.3f}  n={ap_n}")

    blob = {
        "by_tf": {tf: {k: results[tf][k] for k in
                       ["base_acc","imp_acc_full","n","n_full","cov_full","coef","intercept",
                        "applied_acc","applied_cov","applied_n"]} for tf in TFS},
        "overall": {"base": base_overall, "improved_full": imp_overall, "n": tot_bn},
        "applied_overall": {"acc": ap_h/ap_n, "cov": ap_n/ap_tb, "n": ap_n},
        "m5_horizon": {H: {"base": m5_rob[H]["base_acc"], "imp": m5_rob[H]["imp_acc_full"]}
                       for H in M5_EXTRA_H},
    }
    outp = os.path.join(os.path.dirname(__file__), "exp_idea-2_result.json")
    json.dump(blob, open(outp, "w", encoding="utf-8"), default=str, indent=2)
    print(f"\nwrote {outp}")
    return blob


if __name__ == "__main__":
    main()
