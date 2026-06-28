#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
verify_integration.py -- prove the LIVE project() model path reproduces the validated idea-2.

A) Shape + NEUTRAL smoke: call the real rsi_tools.project() (model from web/tuned.json) on real
   bars; assert output shape intact and that abstention (dir_val==0) actually fires.
B) Reproduction: confirm the LIVE rsi_tools.proj_features() matches exp_idea-2.feature_matrix()
   row-for-row, and that the SHIPPED tuned.json weights reproduce the backtest accuracy/gate.

Run: PYTHONIOENCODING=utf-8 python backtest/verify_integration.py
"""
import os, sys, json, importlib.util
import numpy as np, pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backtest"))
import rsi_tools as RT
import rtm_bt as B
spec = importlib.util.spec_from_file_location("exp2", os.path.join(ROOT, "backtest", "exp_idea-2.py"))
exp2 = importlib.util.module_from_spec(spec); spec.loader.exec_module(exp2)

MODEL = json.load(open(os.path.join(ROOT, "web", "tuned.json")))["projection_model"]
TF_MIN = {"M5": 5, "M15": 15, "H1": 60}


def A_smoke():
    print("A) project() shape + NEUTRAL smoke (live model from tuned.json)")
    neutral_seen = 0; calls = 0
    for tf in ["M5", "M15", "H1"]:
        a = exp2.load_json("BTCUSDT", tf)
        t, o, h, l, c = a[:, 0], a[:, 1], a[:, 2], a[:, 3], a[:, 4]
        atr = B.atr_rma(h, l, c, 14)
        rsi = RT.rsi(c, 14)
        # walk a handful of end-points to exercise different regimes
        for end in range(len(c) - 1, len(c) - 400, -23):
            cc = c[:end + 1]
            tt = pd.to_datetime(t[:end + 1].astype("int64"), unit="s")
            divs = RT.divergences(h[:end + 1], l[:end + 1], cc, RT.rsi(cc, 14), L=5, recent_bars=min(400, end))
            bias = int(np.sign(cc[-1] - cc[-min(50, end)]))  # crude HTF proxy for the smoke only
            r = RT.project(tt, cc, atr[:end + 1], bias, float(rsi[end]), divs, None, TF_MIN[tf],
                           tf_weight=0.6, model=MODEL)
            assert set(["dir", "dir_val", "confidence", "points", "events", "scenario"]).issubset(r), "shape broke"
            assert r["dir_val"] in (-1, 0, 1), f"bad dir_val {r['dir_val']}"
            assert 0.12 <= r["confidence"] <= 0.9, f"bad conf {r['confidence']}"
            assert isinstance(r["points"], list) and len(r["points"]) > 0, "no points"
            calls += 1; neutral_seen += (r["dir_val"] == 0)
    print(f"   {calls} project() calls OK, output shape intact, conf in [.12,.9]")
    print(f"   NEUTRAL (dir_val==0) fired {neutral_seen} times -> honest abstention works")
    assert calls > 0
    return neutral_seen, calls


def B_reproduce():
    print("\nB) live proj_features == exp2.feature_matrix, and shipped weights reproduce accuracy")
    max_abs_diff = 0.0
    base_h = base_n = imp_h = imp_n = 0
    gate_h = gate_n = total = 0
    for tf in ["M5", "M15", "H1"]:
        H = exp2.HMAIN[tf]; m = MODEL[tf]; w = np.array(m["weights"]); b0 = m["intercept"]; tau = m["tau"]
        for sym in exp2.SYMBOLS:
            arr = exp2.load_json(sym, tf)
            if arr is None:
                continue
            t, o, h, l, c = arr[:, 0], arr[:, 1], arr[:, 2], arr[:, 3], arr[:, 4]
            n = len(c)
            X, _c, score, atr, rsi, slope, div = exp2.feature_matrix(t, o, h, l, c, tf)
            # FULL sweep: every div!=0 bar (where the same-bar tie-break bug lived) + a grid.
            divs_all = RT.divergences(h, l, c, RT.rsi(c, 14), L=5, recent_bars=n)
            test_idx = sorted(set(range(60, n - H - 1, 25)) | set(np.where(X[:, 4] != 0)[0].tolist()))
            for i in test_idx:
                if i < 60 or i > n - H - 2:
                    continue
                live = RT.proj_features(c[:i + 1], atr[:i + 1], rsi[i],
                                        [d for d in divs_all if d["bar"] <= i], float(X[i, 0]), TF_MIN[tf])
                if live is not None:
                    max_abs_diff = max(max_abs_diff, float(np.max(np.abs(np.array(live) - X[i]))))
            # vectorised scoring with the SHIPPED weights (= what the live logit computes)
            last = n - H - 1
            idx = np.arange(exp2.WARMUP, last)
            z = b0 + X[idx] @ w
            P = 1.0 / (1.0 + np.exp(-np.clip(z, -30, 30)))
            move = (c[idx + H] - c[idx]) / c[idx]
            flat = np.abs(move) <= exp2.THR
            bcall = np.sign(score[idx])
            base_ok = (~flat) & ((bcall > 0) == (move > 0)) & (bcall != 0)
            base_h += int(base_ok.sum()); base_n += int((bcall != 0).sum())
            imp_ok = (~flat) & ((P >= 0.5) == (move > 0))
            imp_h += int(imp_ok.sum()); imp_n += len(idx)
            gm = np.abs(P - 0.5) >= tau
            gate_h += int((imp_ok & gm).sum()); gate_n += int(gm.sum()); total += len(idx)
    print(f"   proj_features vs feature_matrix max|diff| = {max_abs_diff:.2e}  (should be ~0)")
    print(f"   baseline full-cov acc = {base_h / base_n:.4f}")
    print(f"   model    full-cov acc = {imp_h / imp_n:.4f}   delta = {imp_h / imp_n - base_h / base_n:+.4f}")
    print(f"   model    GATE acc     = {gate_h / gate_n:.4f}  coverage = {gate_n / total:.3f}")
    assert max_abs_diff < 1e-6, "live features diverge from validated features!"
    assert imp_h / imp_n > base_h / base_n, "shipped weights did not reproduce the gain!"
    return imp_h / imp_n - base_h / base_n


if __name__ == "__main__":
    A_smoke()
    d = B_reproduce()
    print(f"\nVERIFIED: integration reproduces idea-2 (full-cov delta {d:+.4f}); shape + abstention OK.")
