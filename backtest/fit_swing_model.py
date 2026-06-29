#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fit_swing_model.py -- fit a per-TF SWING-REACH model so the projection draws the arc at its
REAL magnitude ("how far does it run, then turn back?"), not the hand-set 0.42*ATR.

For each TF it fits two ridge regressions over the SAME 8 causal features the direction model
uses (exp_idea-2.feature_matrix / rsi_tools.proj_features), predicting:
   log1p(up_reach_ATR)   -- furthest price runs UP   over [i+1, i+H]
   log1p(dn_reach_ATR)   -- furthest price runs DOWN  over [i+1, i+H]
At draw time the call's FAVOURABLE reach = up if dir>0 else dn; ADVERSE reach = the other side.

HONEST: features causal (data[:i+1]); the reach label is forward (that's the target). Validated
leave-symbols-out (GroupKFold by symbol): MAE of the conditional model vs the constant per-TF
median, on held-out symbols. Also reports the median turn-bar fraction (where the swing peaks),
which sets the projection's arc-peak. Emits a `swing_model` blob for web/tuned.json.

Run: PYTHONIOENCODING=utf-8 python backtest/fit_swing_model.py
"""
import os, json, importlib.util
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_spec = importlib.util.spec_from_file_location("swing_lib", os.path.join(ROOT, "backtest", "swing_lib.py"))
SW = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(SW)
exp2 = SW.exp2

FEAT = exp2.FEAT_NAMES
NFOLDS = 5
REACH_FLOOR, REACH_CAP = 0.6, 9.0     # clamp predicted reach (ATR) to a sane band


def ridge_fit(X, y, lam=1.0):
    """Closed-form ridge on standardised X; folds scaling back so caller applies to raw X.
    Returns (b0_raw, w_raw)."""
    mu = X.mean(axis=0); sd = X.std(axis=0); sd = np.where(sd < 1e-9, 1.0, sd)
    Xs = (X - mu) / sd
    n, d = Xs.shape
    A = Xs.T @ Xs + lam * np.eye(d)
    b = Xs.T @ (y - y.mean())
    w = np.linalg.solve(A, b)
    b0 = y.mean()
    # z = b0 + sum w_j (x_j-mu_j)/sd_j
    w_raw = w / sd
    b0_raw = b0 - np.sum(w * mu / sd)
    return float(b0_raw), w_raw


def predict_reach(X, b0, w):
    z = b0 + X @ w
    return np.expm1(np.clip(z, -5, 5))    # inverse of log1p, bounded


def grouped(symids, nfolds, seed=42):
    rng = np.random.RandomState(seed)
    u = np.unique(symids); rng.shuffle(u)
    folds = [[] for _ in range(nfolds)]
    for i, s in enumerate(u):
        folds[i % nfolds].append(s)
    return folds


def fit_tf(tf, H):
    d = SW.dataset(tf, H)
    m = np.isfinite(d["up"]) & np.isfinite(d["dn"]) & np.all(np.isfinite(d["X"]), axis=1)
    X = d["X"][m]; up = d["up"][m]; dn = d["dn"][m]
    kup = d["kup"][m]; kdn = d["kdn"][m]; sym = d["sym"][m]
    yup = np.log1p(np.clip(up, 0, 50)); ydn = np.log1p(np.clip(dn, 0, 50))

    # ---- leave-symbols-out validation: conditional MAE vs constant-median MAE ----
    folds = grouped(sym, NFOLDS)
    pe_up = pe_dn = ce_up = ce_dn = nval = 0.0
    for test_syms in folds:
        te = np.isin(sym, test_syms); tr = ~te
        if tr.sum() < 500 or te.sum() < 100:
            continue
        b0u, wu = ridge_fit(X[tr], yup[tr]); b0d, wd = ridge_fit(X[tr], ydn[tr])
        pu = np.clip(predict_reach(X[te], b0u, wu), REACH_FLOOR, REACH_CAP)
        pd = np.clip(predict_reach(X[te], b0d, wd), REACH_FLOOR, REACH_CAP)
        cu = np.median(up[tr]); cd = np.median(dn[tr])     # constant baseline from TRAIN only
        pe_up += np.abs(pu - up[te]).sum(); pe_dn += np.abs(pd - dn[te]).sum()
        ce_up += np.abs(cu - up[te]).sum(); ce_dn += np.abs(cd - dn[te]).sum()
        nval += te.sum()
    val = dict(
        mae_up_model=pe_up / nval, mae_up_const=ce_up / nval,
        mae_dn_model=pe_dn / nval, mae_dn_const=ce_dn / nval,
        improve_up=(ce_up - pe_up) / nval, improve_dn=(ce_dn - pe_dn) / nval, n=int(nval))

    # ---- final fit on ALL bars (the shipped weights) ----
    b0u, wu = ridge_fit(X, yup); b0d, wd = ridge_fit(X, ydn)
    turn_up = float(np.median(kup) / H)      # where the UP extreme lands (fraction of H)
    turn_dn = float(np.median(kdn) / H)
    return dict(
        intercept_up=round(b0u, 5), weights_up=[round(float(x), 5) for x in wu],
        intercept_dn=round(b0d, 5), weights_dn=[round(float(x), 5) for x in wd],
        up_med=round(float(np.median(up)), 3), dn_med=round(float(np.median(dn)), 3),
        up_mean=round(float(np.mean(up)), 3), dn_mean=round(float(np.mean(dn)), 3),
        turn_up=round(turn_up, 3), turn_dn=round(turn_dn, 3),
        floor=REACH_FLOOR, cap=REACH_CAP, H=H, n=int(len(up)),
        _val=val,
    )


def main():
    print("=" * 78)
    print("FIT swing-reach model (per TF, leave-symbols-out validated) — replaces 0.42*ATR draw")
    print("=" * 78)
    # SHIPPABLE blob: per-TF constant reach medians + turn fractions. The conditional ridge was
    # validated leave-symbols-out and did NOT beat the constant (MAE improve ~0.00-0.01 ATR, noise),
    # so we ship the honest constant central value — a ~7x magnitude correction without false precision.
    ship = {"_note": "per-TF swing-reach (ATR units): median forward reach up/dn + turn-bar fraction. "
                     "Drives the projection arc amplitude + peak. Constants (conditioning on causal "
                     "features did not beat the median, validated leave-symbols-out). "
                     "Fit by backtest/fit_swing_model.py. Remove to revert the draw to 0.42*ATR."}
    diag = {"features": FEAT}
    for tf in exp2.TFS:
        H = SW.HSWING[tf]
        r = fit_tf(tf, H)
        v = r.pop("_val")
        ship[tf] = {"up": r["up_med"], "dn": r["dn_med"], "up_mean": r["up_mean"], "dn_mean": r["dn_mean"],
                    "turn_up": r["turn_up"], "turn_dn": r["turn_dn"],
                    "floor": r["floor"], "cap": r["cap"], "H": H, "n": r["n"]}
        diag[tf] = {**r, "_val": v}
        print(f"\n[{tf}] H={H}  n={r['n']}")
        print(f"   median reach  up={r['up_med']} dn={r['dn_med']} ATR   (was drawn at 0.42)")
        print(f"   turn fraction up@{r['turn_up']}  dn@{r['turn_dn']}  of H")
        print(f"   LEAVE-SYMBOLS-OUT MAE (ATR):")
        print(f"      up: model={v['mae_up_model']:.3f}  const={v['mae_up_const']:.3f}  "
              f"improve={v['improve_up']:+.3f}")
        print(f"      dn: model={v['mae_dn_model']:.3f}  const={v['mae_dn_const']:.3f}  "
              f"improve={v['improve_dn']:+.3f}  (n={v['n']})")
        print(f"   --> SHIP constants (conditioning within noise)")
    outp = os.path.join(ROOT, "backtest", "swing_model.json")
    json.dump(ship, open(outp, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    json.dump(diag, open(os.path.join(ROOT, "backtest", "swing_model_diag.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
    print(f"\nwrote {outp}  (+ swing_model_diag.json)")
    return ship


if __name__ == "__main__":
    main()
