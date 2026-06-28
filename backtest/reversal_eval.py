#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
reversal_eval.py — honestly backtest the user's trade STYLE before teaching it:
a REVERSAL at a supply/demand zone (short the proximal edge of supply / long the proximal
edge of demand), stop beyond the zone, target = 3R. Tested with/without an RSI-extreme
confluence and split by with-trend vs counter-trend (the user's short was a reversal).

Run: cd ~/Desktop/trade/backtest && python3 reversal_eval.py [SYM ...] [--rr 3]
"""
import os, sys
import numpy as np
import rtm_bt as B
import rsi_tools as RT

CORE = ["BTCUSDT", "XRPUSDT", "ETHUSD", "SOLUSD", "XAUUSD", "BNBUSD", "ADAUSD",
        "AVAXUSD", "LINKUSD", "LTCUSD", "DOTUSD", "ATOMUSD", "DOGEUSD"]
BUF = 0.3; MAXBARS = 300


def _last_valid(a, i):
    return a[i] if not np.isnan(a[i]) else np.nan


def eval_symbol(sym, tf, RR, rsi_gate):
    D = B.prep_symbol(sym, tf)
    o, h, l, c, atr = D["o"], D["h"], D["l"], D["c"], D["atr"]
    n = len(c)
    rsi = RT.rsi(c, 14)
    bias = np.sign(1.0 * D["b1"] + 2.0 * D["b2"] + 2.0 * D["b3"])
    # combine 1h + 15m zones
    supB = np.where(~np.isnan(D["c_supB"]), D["c_supB"], D["a_supB"])
    supT = np.where(~np.isnan(D["c_supT"]), D["c_supT"], D["a_supT"])
    demB = np.where(~np.isnan(D["c_demB"]), D["c_demB"], D["a_demB"])
    demT = np.where(~np.isnan(D["c_demT"]), D["c_demT"], D["a_demT"])

    rows = []
    armed_sup = armed_dem = None   # avoid re-entering same zone repeatedly
    for i in range(30, n - 1):
        if np.isnan(atr[i]):
            continue
        # ---- SHORT reversal at supply: price rises to proximal (bottom) edge ----
        if not np.isnan(supB[i]) and not np.isnan(supT[i]) and supT[i] > supB[i]:
            if h[i] >= supB[i] and c[i] <= supT[i] and armed_sup != round(supB[i], 2):
                if (not rsi_gate) or rsi[i] >= 60:
                    entry = supB[i]; sl = supT[i] + BUF * atr[i]; risk = sl - entry
                    if risk > 0:
                        tgt = entry - RR * risk
                        rows.append(_resolve(-1, i, entry, sl, tgt, risk, h, l, n,
                                             bias[i], rsi[i]))
                        armed_sup = round(supB[i], 2)
        # ---- LONG reversal at demand: price falls to proximal (top) edge ----
        if not np.isnan(demB[i]) and not np.isnan(demT[i]) and demT[i] > demB[i]:
            if l[i] <= demT[i] and c[i] >= demB[i] and armed_dem != round(demT[i], 2):
                if (not rsi_gate) or rsi[i] <= 40:
                    entry = demT[i]; sl = demB[i] - BUF * atr[i]; risk = entry - sl
                    if risk > 0:
                        tgt = entry + RR * risk
                        rows.append(_resolve(1, i, entry, sl, tgt, risk, h, l, n,
                                             bias[i], rsi[i]))
                        armed_dem = round(demT[i], 2)
    return [r for r in rows if r]


def _resolve(dr, i, entry, sl, tgt, risk, h, l, n, bias_i, rsi_i):
    for j in range(i + 1, min(i + 1 + MAXBARS, n)):
        if dr == 1:
            hit_sl, hit_tp = (l[j] <= sl), (h[j] >= tgt)
        else:
            hit_sl, hit_tp = (h[j] >= sl), (l[j] <= tgt)
        if hit_sl:
            return {"dir": dr, "R": -1.0, "ct": int(dr != bias_i and bias_i != 0), "rsi": rsi_i}
        if hit_tp:
            return {"dir": dr, "R": float(abs(tgt - entry) / risk), "ct": int(dr != bias_i and bias_i != 0), "rsi": rsi_i}
    return None


def agg(rows):
    if not rows:
        return None
    R = np.array([r["R"] for r in rows])
    wins = (R > 0).sum()
    return {"n": len(rows), "wr": round(100 * wins / len(rows), 1),
            "expR": round(R.mean(), 3), "netR": round(R.sum(), 1)}


def main():
    argv = sys.argv[1:]
    RR = 3.0; skip = set()
    if "--rr" in argv:
        i = argv.index("--rr"); RR = float(argv[i + 1]); skip = {i, i + 1}
    args = [a for k, a in enumerate(argv) if k not in skip and not a.startswith("--")]
    syms = args or CORE
    print(f"Reversal-at-zone backtest (target={RR}R, stop beyond zone), {len(syms)} symbols\n")
    for gate in (False, True):
        allr = []
        for s in syms:
            try:
                allr += eval_symbol(s, "M5", RR, gate)
            except Exception as e:
                print(f"  skip {s}: {str(e)[:40]}")
        a = agg(allr)
        ct = agg([r for r in allr if r["ct"]])      # counter-trend = the reversal case
        wt = agg([r for r in allr if not r["ct"]])
        lo = agg([r for r in allr if r["dir"] == 1]); sh = agg([r for r in allr if r["dir"] == -1])
        tag = "WITH RSI-extreme confluence" if gate else "all zone touches"
        print(f"=== {tag} ===")
        print(f"  ALL         {a}")
        print(f"  counter-trend(reversal) {ct}")
        print(f"  with-trend  {wt}")
        print(f"  long {lo} | short {sh}\n")


if __name__ == "__main__":
    main()
