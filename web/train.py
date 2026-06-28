#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
train.py — TRAIN the learning store from REAL history (honest backfill).

The live recorder learns slowly (each prediction must wait out its horizon). To train the
system NOW, we replay the projection's directional call across the full historical timeline
and score each call against the price that ACTUALLY came `H` bars later — then write the
results into the learning store so accuracy()/annotate() calibrate live confidence
immediately, and we can see which symbol / which confluence count the system is good at.

Honest by construction: the call at bar i is scored only against bar i+H (no look-ahead),
and the directional formula mirrors rsi_tools.project's backbone.

Run:  cd ~/Desktop/trade/web && python3 train.py          # uses ~/Downloads history
"""
import os, sys, time, json
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "backtest")))
import numpy as np
import rtm_bt as B
import rsi_tools as RT
import learning_store as LS

# live bucket name  ->  history file in ~/Downloads
SRC = {
    "BTCUSDT": "BTCUSDT", "XRPUSDT": "XRPUSDT", "ETHUSDT": "ETHUSD", "SOLUSDT": "SOLUSD",
    "BNBUSDT": "BNBUSD", "ADAUSDT": "ADAUSD", "DOGEUSDT": "DOGEUSD", "AVAXUSDT": "AVAXUSD",
    "LINKUSDT": "LINKUSD", "LTCUSDT": "LTCUSD", "DOTUSDT": "DOTUSD",
    "XAUUSD": "XAUUSD", "PAXGUSDT": "XAUUSD",     # PAXG ≈ spot gold -> train from gold history
}
TF = "M5"; H = 48; THR = 0.0005; TARGET_PER_SYM = 1200; TFW = 0.6


def build_calls(D):
    """Per-bar directional call (+1/-1/0) mirroring the live projection backbone, plus the
    confluence count from the same 3 independent styles the live signal uses."""
    o, h, l, c, atr = D["o"], D["h"], D["l"], D["c"], D["atr"]
    n = len(c)
    bias = np.sign(1.0 * D["b1"] + 2.0 * D["b2"] + 2.0 * D["b3"])
    # slope over 20 bars
    slope = np.zeros(n)
    slope[20:] = np.sign(c[20:] - c[:-20])
    rsi = RT.rsi(c, 14)
    rsi_pull = np.where(rsi < 30, 1.0, np.where(rsi > 70, -1.0, 0.0))
    # divergence pull, carried forward 12 bars
    div = np.zeros(n)
    try:
        for d in RT.divergences(h, l, c, rsi, L=5, recent_bars=n):
            b = d["bar"]; div[b:min(n, b + 12)] = 1.0 if d["type"] == "bull" else -1.0
    except Exception:
        pass
    # discount/premium
    pH, pL = D["pH"], D["pL"]
    dp = np.zeros(n)
    with np.errstate(invalid="ignore"):
        mid = pL + 0.5 * (pH - pL)
        ok = np.isfinite(mid) & (pH > pL)
        dp[ok & (c < mid)] = 1.0; dp[ok & (c > mid)] = -1.0

    score = 1.0 * np.nan_to_num(bias) + 0.4 * slope + 0.9 * TFW * rsi_pull + 0.7 * TFW * div
    call = np.sign(score)
    # confluence among the 3 independent styles (matches signal_service combo)
    styles = np.vstack([rsi_pull, dp, div])
    return call, styles, c


def train():
    records = []
    per_combo = {}     # combo_count -> [hits, n]
    for live_sym, src in SRC.items():
        try:
            D = B.prep_symbol(src, TF)
        except Exception as e:
            print(f"  skip {live_sym}: {str(e)[:50]}"); continue
        call, styles, c = build_calls(D)
        n = len(c)
        last = n - H - 1
        if last < 1000:
            print(f"  skip {live_sym}: only {n} bars"); continue
        stride = max(6, last // TARGET_PER_SYM)
        hits = cnt = 0
        for i in range(50, last, stride):
            d = call[i]
            if d == 0:
                continue
            fut = c[i + H]
            move = (fut - c[i]) / c[i]
            if abs(move) < THR:
                correct = False
            else:
                correct = bool((move > 0) == (d > 0))
            combo = int(np.sum(styles[:, i] == d))
            records.append({
                "ts": int(D["time"][i].value // 10**9), "symbol": live_sym, "tf": TF,
                "ref_price": float(c[i]), "dir": int(d), "conf": None,
                "eval_t": int(D["time"][i + H].value // 10**9),
                "bucket": f"{live_sym}|{TF}", "combo": combo,
                "scored": True, "correct": correct, "exit_price": float(fut),
                "src": "train",
            })
            pc = per_combo.setdefault(combo, [0, 0]); pc[1] += 1; pc[0] += int(correct)
            hits += int(correct); cnt += 1
        print(f"  {live_sym:9} trained on {cnt:5} calls  acc {100*hits/cnt:4.1f}%")
    return records, per_combo


def main():
    purge = "--keep" not in sys.argv
    existing = [json.loads(l) for l in open(LS.PRED)] if os.path.exists(LS.PRED) else []
    if purge:
        existing = [p for p in existing if p.get("src") != "train"]   # drop prior training rows
    print("Training from ~/Downloads history (mirrors live projection direction)\n")
    recs, per_combo = train()
    allp = existing + recs
    with open(LS.PRED, "w", encoding="utf-8") as f:
        for p in allp:
            f.write(json.dumps(p, ensure_ascii=False) + "\n")
    print(f"\nwrote {len(recs)} training predictions ({len(allp)} total in store)")
    s = LS.summary()
    print(f"\n=== LEARNED: overall {s['overall']['rate']} on {s['overall']['n']} scored calls ===")
    print("per-symbol learned accuracy (n>=50):")
    for b, v in sorted(s["buckets"].items(), key=lambda kv: -(kv[1]["rate"] or 0)):
        if v["n"] >= 50:
            print(f"   {b:16} n={v['n']:5}  rate={v['rate']}")
    print("\nLEARNED by confluence count (how many independent styles agreed):")
    for k in sorted(per_combo):
        hh, nn = per_combo[k]
        if nn >= 30:
            print(f"   {k} styles agree: n={nn:6}  accuracy={100*hh/nn:4.1f}%")


if __name__ == "__main__":
    main()
