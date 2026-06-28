#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stop post-mortem: why do trades stop out? Pull every M5 setup, take the 2R rule,
split winners vs losers (stops), and find what distinguishes the stops — so we can
add rules to stop less. Also measures how many stops were 'givebacks' (reached >=1R
then reversed to SL) — i.e. would a partial+BE have saved them."""
import os, sys
import numpy as np, pandas as pd
import rtm_bt as B, bt_structure as S

def syms():
    out = []
    for line in open(os.path.expanduser("~/Desktop/trade/backtest/watchlist.txt")):
        line = line.strip()
        if not line or line.startswith("#"): continue
        for tok in line.split():
            s, _, sess = tok.partition(",")
            out.append((s, sess or "ny"))
    return out

rows = []
for sym, sess in syms():
    try:
        D = B.prep_symbol(sym, "M5")
    except Exception as e:
        print(f"{sym} err {str(e)[:30]}"); continue
    rows += S.collect(D, sym, sess)
    print(f"{sym:9s} {len(rows)} cum", flush=True)

df = pd.DataFrame(rows)
d = df[df["2R"].notna()].copy()
d["loss"] = (d["2R"] < 0).astype(int)          # a stop
N = len(d); stops = int(d["loss"].sum())
print(f"\n=== {N} resolved 2R trades | {stops} stops ({100*stops/N:.1f}% stop-rate) ===\n")

def rate(col):
    g = d.groupby(col)["loss"].agg(["mean", "count"])
    g["stop%"] = (100*g["mean"]).round(1)
    return g[["stop%", "count"]].sort_values("stop%", ascending=False)

print("STOP-RATE by zone source (ztf 60=H1, 15=M15):")
print(rate("ztf").to_string()); print()
print("STOP-RATE by direction:")
print(rate("dir").to_string()); print()
print("STOP-RATE by symbol (worst first):")
print(rate("sym").to_string()); print()

# how far did STOPS travel in our favor before reversing? (MFE of losers)
L = d[d["loss"] == 1]
print("\n=== of the STOPS, how far did price go our way first (MFE, R) ===")
for lo, hi in [(0,0.25),(0.25,0.5),(0.5,1.0),(1.0,1.5),(1.5,2.0)]:
    m = ((L["mfe"] >= lo) & (L["mfe"] < hi)).mean()
    print(f"  MFE {lo:.2f}-{hi:.2f}R : {100*m:5.1f}% of stops")
giveback = (L["mfe"] >= 1.0).mean()
imm = (L["mfe"] < 0.5).mean()
print(f"\n  -> {100*imm:.1f}% of stops were IMMEDIATE rejections (MFE<0.5R) = bad zone/entry")
print(f"  -> {100*giveback:.1f}% of stops first reached >=1R then reversed = GIVEBACKS (a partial+BE at 1R would save these)")

# what would partial 1/3 at 1R + BE-after-1R do to expectancy vs plain 2R?
def realized_partbe(r):
    # 1/3 banked at 1R, remaining 2/3 ride to 2R (TP2). BE after the 1R partial.
    mfe = r["mfe"]
    if r["2R"] > 0:   # full: 1/3 at +1R  +  2/3 at +2R = 1.667R
        return (1/3.0)*1.0 + (2/3.0)*2.0
    if mfe >= 1.0:    # banked 1/3 at +1R, remaining 2/3 stopped at BE(0)
        return (1/3.0)*1.0
    return -1.0       # never reached 1R -> full stop
d["R_partbe"] = d.apply(realized_partbe, axis=1)
exp2R = d["2R"].mean(); expPB = d["R_partbe"].mean()
wr2 = (d["2R"]>0).mean()*100
print(f"\n=== EXIT COMPARISON (same entries) ===")
print(f"  plain 2R      : expR={exp2R:+.3f}  WR={wr2:.1f}%")
print(f"  1/3@1R+BE+2R  : expR={expPB:+.3f}  (stops that gave back >=1R now bank +0.33R instead of -1R)")
