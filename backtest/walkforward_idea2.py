#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
walkforward_idea2.py -- OUT-OF-TIME validation of idea-2 (fitted-logistic direction + abstention).

exp_idea-2.py validated cross-SYMBOL (leave-symbols-out). The one gap that kept it at 1/2 was
cross-TIME robustness (an H1 time-split dipped negative). This does the missing test honestly:

  Expanding, PURGED walk-forward per TF:
    - pool all symbols' causal features, sort by bar time,
    - fold k: train on the earliest k/NFOLDS of time, PURGE the last H train bars (their label
      close[i+H] overlaps the test window), test on the next 1/NFOLDS block,
    - fit the logistic ONLY on train, pick the abstention tau on a held-out TAIL of train (never
      the test block), apply to the test block.
  Reports, out-of-time and pooled across folds: baseline vs improved full-coverage accuracy, and
  the abstention-gate accuracy/coverage. This is the live scenario (refit on past, apply forward).

Reuses exp_idea-2.py's exact feature/fit functions (imported by path; the filename has a hyphen).

Run: PYTHONIOENCODING=utf-8 python backtest/walkforward_idea2.py
"""
import os, sys, json, importlib.util
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location("exp2", os.path.join(ROOT, "backtest", "exp_idea-2.py"))
exp2 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(exp2)

NFOLDS = 6
MINTRAIN = 3000
MINTEST = 400


def pooled(tf, H):
    Xs, mv, fl, bc, ts = [], [], [], [], []
    for sym in exp2.SYMBOLS:
        a = exp2.load_json(sym, tf)
        if a is None:
            continue
        t, o, h, l, c = a[:, 0], a[:, 1], a[:, 2], a[:, 3], a[:, 4]
        n = len(c)
        X, _c, score, atr, rsi, slope, div = exp2.feature_matrix(t, o, h, l, c, tf)
        bcall = np.sign(score)
        last = n - H - 1
        idx = np.arange(exp2.WARMUP, last)
        if len(idx) == 0:
            continue
        move = (c[idx + H] - c[idx]) / c[idx]
        Xs.append(X[idx]); mv.append(move); fl.append(np.abs(move) <= exp2.THR)
        bc.append(bcall[idx]); ts.append(t[idx])
    X = np.vstack(Xs); move = np.concatenate(mv); flat = np.concatenate(fl)
    bcall = np.concatenate(bc); tt = np.concatenate(ts)
    order = np.argsort(tt, kind="stable")
    return X[order], move[order], flat[order], bcall[order], tt[order]


def walkforward(tf, H):
    X, move, flat, bcall, tt = pooled(tf, H)
    N = len(tt)
    bounds = [int(N * k / NFOLDS) for k in range(NFOLDS + 1)]
    folds = []
    for k in range(1, NFOLDS):
        tr_end = bounds[k]
        te0, te1 = bounds[k], bounds[k + 1]
        tr_idx = np.arange(0, max(0, tr_end - H))          # PURGE last H train bars
        te_idx = np.arange(te0, te1)
        if len(tr_idx) < MINTRAIN or len(te_idx) < MINTEST:
            continue
        keep = ~flat[tr_idx]
        w0, w = exp2.fit_logistic(X[tr_idx][keep], (move[tr_idx][keep] > 0).astype(float), C=1.0)
        # pick tau on a held-out TAIL of train (last 30%), never the test block
        val = tr_idx[int(len(tr_idx) * 0.7):]
        Pv = exp2.predict_p(X[val], w0, w)
        best_tau, best_acc = 0.0, -1.0
        for tau in exp2.TAUS:
            m = np.abs(Pv - 0.5) >= tau
            if m.sum() and m.sum() / len(val) >= 0.20:
                ok = (~flat[val]) & ((Pv >= 0.5) == (move[val] > 0))
                acc = (ok & m).sum() / m.sum()
                if acc > best_acc:
                    best_acc, best_tau = acc, tau
        # apply to the future test block
        Pte = exp2.predict_p(X[te_idx], w0, w)
        ok_imp = (~flat[te_idx]) & ((Pte >= 0.5) == (move[te_idx] > 0))
        imp_full = ok_imp.sum() / len(te_idx)
        bmask = bcall[te_idx] != 0
        bok = (~flat[te_idx]) & ((bcall[te_idx] > 0) == (move[te_idx] > 0))
        base_full = (bok & bmask).sum() / bmask.sum() if bmask.sum() else float("nan")
        gm = np.abs(Pte - 0.5) >= best_tau
        gacc = (ok_imp & gm).sum() / gm.sum() if gm.sum() else None
        gcov = gm.sum() / len(te_idx)
        folds.append(dict(fold=k, base=base_full, imp=imp_full, delta=imp_full - base_full,
                          gacc=gacc, gcov=gcov, tau=best_tau, n=int(len(te_idx))))
    return folds


def agg(folds, key, nkey="n"):
    num = sum((f[key] or 0) * f[nkey] for f in folds if f[key] is not None)
    den = sum(f[nkey] for f in folds if f[key] is not None)
    return num / den if den else None


def main():
    print("=" * 76)
    print("WALK-FORWARD (out-of-time, purged) validation of idea-2")
    print("=" * 76)
    out = {}
    pooled_full_base = pooled_full_imp = pooled_full_n = 0.0
    pooled_g_hit = pooled_g_n = pooled_total = 0.0
    for tf in exp2.TFS:
        H = exp2.HMAIN[tf]
        folds = walkforward(tf, H)
        if not folds:
            print(f"\n[{tf}] no usable folds"); continue
        base = agg(folds, "base"); imp = agg(folds, "imp")
        gacc = agg(folds, "gacc"); gcov = agg(folds, "gcov")
        out[tf] = dict(base=base, imp=imp, delta=imp - base, gacc=gacc, gcov=gcov,
                       folds=[{k: (round(v, 4) if isinstance(v, float) else v) for k, v in f.items()} for f in folds])
        print(f"\n[{tf}] H={H}  ({len(folds)} out-of-time folds)")
        print(f"   baseline full-cov = {base:.4f}")
        print(f"   improved full-cov = {imp:.4f}   delta = {imp - base:+.4f}")
        print(f"   abstention gate   = {gacc:.4f}  at coverage {gcov:.3f}" if gacc else "   gate n/a")
        print("   per-fold delta:", [f"{f['delta']:+.4f}" for f in folds])
        print("   per-fold gate acc:", [f"{(f['gacc'] or 0):.3f}@{f['gcov']:.2f}" for f in folds])
        for f in folds:
            pooled_full_base += (f["base"] or 0) * f["n"]; pooled_full_imp += f["imp"] * f["n"]; pooled_full_n += f["n"]
            if f["gacc"] is not None:
                gn = f["gcov"] * f["n"]
                pooled_g_hit += f["gacc"] * gn; pooled_g_n += gn
            pooled_total += f["n"]
    print("\n" + "=" * 76)
    bo = pooled_full_base / pooled_full_n; io = pooled_full_imp / pooled_full_n
    print(f"OVERALL out-of-time full-cov: baseline={bo:.4f}  improved={io:.4f}  delta={io - bo:+.4f}  n={int(pooled_full_n)}")
    if pooled_g_n:
        print(f"OVERALL out-of-time GATE:     acc={pooled_g_hit / pooled_g_n:.4f}  coverage={pooled_g_n / pooled_total:.3f}  n={int(pooled_g_n)}")
    out["overall"] = {"base": bo, "improved": io, "delta": io - bo,
                      "gate_acc": (pooled_g_hit / pooled_g_n) if pooled_g_n else None,
                      "gate_cov": (pooled_g_n / pooled_total) if pooled_total else None,
                      "n": int(pooled_full_n)}
    json.dump(out, open(os.path.join(os.path.dirname(__file__), "walkforward_idea2_result.json"), "w"),
              default=str, indent=2)
    print("\nwrote walkforward_idea2_result.json")


if __name__ == "__main__":
    main()
