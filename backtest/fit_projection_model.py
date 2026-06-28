#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fit_projection_model.py -- freeze the idea-2 per-TF logistic into a shippable tuned.json blob.

Fits the SAME causal 8-feature logistic (exp_idea-2.py) on ALL available bars per TF (the live
model uses a fixed coefficient set, refit periodically), and picks a per-TF abstention threshold
tau targeting >=0.53 precision at >=25% coverage. Emits:
  { "features":[...], "M5":{intercept,weights,tau}, "M15":{...}, "H1":{...} }
which the live project() / build_calls() consume. Walk-forward already validated generalization.

Run: PYTHONIOENCODING=utf-8 python backtest/fit_projection_model.py
"""
import os, sys, json, importlib.util
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location("exp2", os.path.join(ROOT, "backtest", "exp_idea-2.py"))
exp2 = importlib.util.module_from_spec(spec); spec.loader.exec_module(exp2)


def fit_tf(tf, H):
    Xs, mv, fl = [], [], []
    for sym in exp2.SYMBOLS:
        a = exp2.load_json(sym, tf)
        if a is None:
            continue
        t, o, h, l, c = a[:, 0], a[:, 1], a[:, 2], a[:, 3], a[:, 4]
        n = len(c)
        X, *_ = exp2.feature_matrix(t, o, h, l, c, tf)
        last = n - H - 1
        idx = np.arange(exp2.WARMUP, last)
        if len(idx) == 0:
            continue
        move = (c[idx + H] - c[idx]) / c[idx]
        Xs.append(X[idx]); mv.append(move); fl.append(np.abs(move) <= exp2.THR)
    X = np.vstack(Xs); move = np.concatenate(mv); flat = np.concatenate(fl)
    keep = ~flat
    w0, w = exp2.fit_logistic(X[keep], (move[keep] > 0).astype(float), C=1.0)
    P = exp2.predict_p(X, w0, w)
    # pick tau: smallest threshold reaching >=0.53 precision with >=0.25 coverage; else maximise acc@cov>=0.25
    conf = np.abs(P - 0.5)
    best = (0.0, 0.0, 0.0)  # (tau, acc, cov)
    fallback = (0.0, -1.0, 0.0)
    for tau in [0.0, 0.02, 0.03, 0.04, 0.05, 0.06, 0.08, 0.10, 0.12]:
        m = conf >= tau
        cov = m.mean()
        if m.sum() == 0 or cov < 0.25:
            continue
        ok = (~flat) & ((P >= 0.5) == (move > 0))
        acc = (ok & m).sum() / m.sum()
        if acc > fallback[1]:
            fallback = (tau, acc, cov)
        if acc >= 0.53:
            best = (tau, acc, cov); break
    tau, acc, cov = best if best[1] > 0 else fallback
    return dict(intercept=round(float(w0), 5),
                weights=[round(float(x), 5) for x in w],
                tau=round(float(tau), 3),
                fit_acc_full=round(float(((~flat) & ((P >= 0.5) == (move > 0))).mean()), 4),
                fit_gate_acc=round(float(acc), 4), fit_gate_cov=round(float(cov), 4),
                n=int(len(move)))


def main():
    model = {"_note": "idea-2 per-TF logistic over causal features; live project()/build_calls() use it. "
                      "Fit by backtest/fit_projection_model.py; validated out-of-time by walkforward_idea2.py.",
             "features": exp2.FEAT_NAMES}
    for tf in exp2.TFS:
        r = fit_tf(tf, exp2.HMAIN[tf])
        model[tf] = r
        print(f"[{tf}] tau={r['tau']} full_acc={r['fit_acc_full']} gate={r['fit_gate_acc']}@{r['fit_gate_cov']} n={r['n']}")
        print(f"     intercept={r['intercept']} weights={dict(zip(exp2.FEAT_NAMES, r['weights']))}")
    outp = os.path.join(ROOT, "backtest", "projection_model.json")
    json.dump(model, open(outp, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"\nwrote {outp}")
    return model


if __name__ == "__main__":
    main()
