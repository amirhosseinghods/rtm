#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""100+ backtests + dimensional analysis to find what reduces stops / lifts WR."""
import rtm_bt as B
import pandas as pd, numpy as np, json

SYMS = ["XAUUSD","BTCUSDT","XRPUSDT"]
def sess(s):  return "london" if s=="XAUUSD" else "ny"
def sharp(s): return 1.5 if s=="XAUUSD" else 2.5

preps = {}
def getp(sym, tf):
    if (sym,tf) not in preps: preps[(sym,tf)] = B.prep_symbol(sym, tf)
    return preps[(sym,tf)]

def base_cfg(sym):
    p = dict(B.DEF); p["session"]=sess(sym); p["sharpATR"]=sharp(sym); return p

# ---------- Phase 1: baseline M5 + save trades ----------
runs = []
all_tr = []
for sym in SYMS:
    D = getp(sym,"M5"); tr,st = B.backtest(D, base_cfg(sym))
    if len(tr): tr["sym"]=sym; all_tr.append(tr)
    runs.append(dict(sym=sym, tf="M5", cfg="baseline", **st))
allt = pd.concat(all_tr, ignore_index=True)
allt.to_csv("trades_baseline_M5.csv", index=False)

print("=== BASELINE (per-asset session, FTR-g2, no-sharp, rr1.5) — full history ===")
print(pd.DataFrame(runs)[["sym","tf","n","wr","pf","netR","exp","bal","avgWin","avgLoss","maxDD"]].to_string(index=False))

def buck(df, col):
    g = df.groupby(col).agg(n=("R","size"), wr=("R", lambda x: round(100*(x>0).mean(),1)),
                            exp=("R", lambda x: round(x.mean(),3)), sumR=("R", lambda x: round(x.sum(),1)))
    return g.sort_values("sumR", ascending=False)

print("\n=== DIMENSIONAL ANALYSIS (pooled 3 symbols, %d trades) ===" % len(allt))
for col in ["type","grade","withtrend","dir","reason"]:
    print(f"\n-- by {col} --"); print(buck(allt, col).to_string())
print("\n-- by entry hour (NY) --"); print(buck(allt, "hour_ny").to_string())
# loss-size analysis (the 'reduce stops' question)
sl = allt[allt.reason=="SL"]
print(f"\n-- SL slippage: {len(sl)} stops, avg R={sl.R.mean():.3f}, "
      f"worse-than--1.1R: {(sl.R< -1.1).mean()*100:.0f}%  worse-than--1.3R: {(sl.R< -1.3).mean()*100:.0f}% --")

# ---------- Phase 2: one-axis parameter sweep ----------
axes = {
 "session":  ["off","london","ny"],
 "noSharp":  [False],                      # no-sharp OFF
 "sharpATR": [1.5,2.0,2.5,3.0],            # no-sharp ON, vary threshold
 "minGrade": [1,2],
 "rr":       [1.0,1.5,2.0,2.5,3.0],
 "minStop":  [1.0,1.5,2.0,2.5],
 "roundTrip":[True,False],
 "minScore": [70,80,90],
}
sweep = []
for sym in SYMS:
    D = getp(sym,"M5"); base = base_cfg(sym)
    for ax, vals in axes.items():
        for v in vals:
            p = dict(base)
            if ax=="sharpATR": p["noSharp"]=True; p["sharpATR"]=v
            else: p[ax]=v
            tr,st = B.backtest(D, p)
            sweep.append(dict(sym=sym, axis=ax, val=str(v), **st))

# ---------- Phase 3: entry-TF variations ----------
for sym in SYMS:
    for tf in ["M1","M15"]:
        D = getp(sym,tf); tr,st = B.backtest(D, base_cfg(sym))
        sweep.append(dict(sym=sym, axis="entryTF", val=tf, **st))

# ---------- Phase 4: 2D grid rr x minStop on BTC (best) ----------
Dbtc = getp("BTCUSDT","M5")
for rr in [1.0,1.5,2.0,2.5]:
    for ms in [1.0,1.5,2.0,2.5,3.0]:
        p = base_cfg("BTCUSDT"); p["rr"]=rr; p["minStop"]=ms
        tr,st = B.backtest(Dbtc, p)
        sweep.append(dict(sym="BTCUSDT", axis="rrXminStop", val=f"rr{rr}_ms{ms}", **st))

sw = pd.DataFrame(sweep)
sw.to_csv("sweep_M5.csv", index=False)
total = len(runs) + len(sw)
print(f"\n=== SWEEP COMPLETE: {total} total backtests ===")

print("\n--- session effect (per symbol) ---")
print(sw[sw.axis=="session"][["sym","val","n","wr","pf","netR","maxDD"]].to_string(index=False))
print("\n--- rr effect (per symbol) ---")
print(sw[sw.axis=="rr"][["sym","val","n","wr","pf","netR","maxDD"]].to_string(index=False))
print("\n--- minStop effect (per symbol) — REDUCE STOPS lever ---")
print(sw[sw.axis=="minStop"][["sym","val","n","wr","pf","netR","avgLoss","maxDD"]].to_string(index=False))
print("\n--- minGrade / noSharp / roundTrip / minScore ---")
print(sw[sw.axis.isin(["minGrade","noSharp","roundTrip","minScore"])][["sym","axis","val","n","wr","pf","netR"]].to_string(index=False))
print("\n--- entry TF ---")
print(sw[sw.axis=="entryTF"][["sym","val","n","wr","pf","netR","maxDD"]].to_string(index=False))
print("\n--- BTC rr x minStop grid (top 8 by netR) ---")
print(sw[sw.axis=="rrXminStop"].nlargest(8,"netR")[["val","n","wr","pf","netR","avgLoss","maxDD"]].to_string(index=False))
