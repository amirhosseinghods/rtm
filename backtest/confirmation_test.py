#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Test each entry-confirmation in isolation on the validated config (M5 + 1h zones).
Preps data once, then re-runs collect() under each FD_CONF setting and reports
n / stop% / expR(2R) / expR(partial+BE) so we keep only confirmations that add edge."""
import os
os.environ["FD_SRC"] = "1h"
import numpy as np, pandas as pd
import rtm_bt as B, bt_structure as S

syms = []
for line in open(os.path.expanduser("~/Desktop/trade/backtest/watchlist.txt")):
    line = line.strip()
    if not line or line.startswith("#"): continue
    for tok in line.split():
        s, _, sess = tok.partition(",")
        syms.append((s, sess or "ny"))

print("prepping (once)...", flush=True)
Ds = {}
for s, sess in syms:
    try: Ds[s] = B.prep_symbol(s, "M5")
    except Exception as e: print(f"  {s} err {str(e)[:30]}")
print(f"prepped {len(Ds)} symbols\n", flush=True)

def partbe(r):
    if r["2R"] > 0: return (1/3.0)*1 + (2/3.0)*2     # 1.667
    if r["mfe"] >= 1.0: return (1/3.0)*1             # 0.333
    return -1.0

print(f"{'confirmation':14s} {'n':>4s} {'stop%':>6s} {'WR2R%':>6s} {'exp2R':>7s} {'expPBE':>7s} {'netR(PBE)':>9s}")
print("-"*60)
for conf in ["none", "reclaim", "nosharp", "room", "all"]:
    S.CONF = conf
    rows = []
    for s, sess in syms:
        if s in Ds: rows += S.collect(Ds[s], s, sess)
    df = pd.DataFrame(rows)
    d = df[df["2R"].notna()].copy()
    if len(d) == 0:
        print(f"{conf:14s} 0"); continue
    d["pbe"] = d.apply(partbe, axis=1)
    n = len(d); stop = 100*(d["2R"] < 0).mean(); wr = 100*(d["2R"] > 0).mean()
    print(f"{conf:14s} {n:4d} {stop:6.1f} {wr:6.1f} {d['2R'].mean():+7.3f} {d['pbe'].mean():+7.3f} {d['pbe'].sum():+9.1f}")
