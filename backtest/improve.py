#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Combine best levers -> improved per-asset configs; OOS-validate (1st half vs 2nd half)."""
import rtm_bt as B
import pandas as pd, numpy as np

SYMS=["XAUUSD","BTCUSDT","XRPUSDT"]
preps={}
def getp(s,tf="M5"):
    if (s,tf) not in preps: preps[(s,tf)]=B.prep_symbol(s,tf)
    return preps[(s,tf)]
def sess(s): return "london" if s=="XAUUSD" else "ny"
def sharp(s): return 1.5 if s=="XAUUSD" else 2.5

def stt(tr):
    if len(tr)==0: return dict(n=0,wr=0,pf=0,netR=0,exp=0)
    w=tr[tr.R>0]; lo=tr[tr.R<=0]
    pf=w.R.sum()/(-lo.R.sum()) if len(lo) and lo.R.sum()<0 else float('nan')
    return dict(n=len(tr),wr=round(100*len(w)/len(tr),1),pf=round(pf,2),
                netR=round(tr.R.sum(),1),exp=round(tr.R.mean(),3),
                avgLoss=round(lo.R.mean(),2) if len(lo) else 0)

# ---- dimensional analysis on baseline trades ----
allt=pd.read_csv("trades_baseline_M5.csv")
def buck(df,col):
    g=df.groupby(col).agg(n=("R","size"),wr=("R",lambda x:round(100*(x>0).mean(),1)),
        exp=("R",lambda x:round(x.mean(),3)),sumR=("R",lambda x:round(x.sum(),1)))
    return g.sort_values("sumR",ascending=False)
print("=== DIMENSIONAL ANALYSIS (baseline, pooled %d trades) ==="%len(allt))
for col in ["type","grade","withtrend","dir"]:
    print(f"\n-- by {col} --"); print(buck(allt,col).to_string())
print("\n-- by entry hour NY (top/bottom) --")
bh=buck(allt,"hour_ny"); print(bh.to_string())

# ---- improved configs (from sweep) ----
IMP={
 "XAUUSD": dict(B.DEF, session="london", sharpATR=1.5, minScore=90, minStop=2.5, rr=1.5, roundTrip=False),
 "BTCUSDT":dict(B.DEF, session="ny",     sharpATR=2.5, minScore=80, minStop=1.0, rr=2.0, roundTrip=False),
 "XRPUSDT":dict(B.DEF, session="ny",     sharpATR=2.5, minScore=90, minStop=1.0, rr=2.0, roundTrip=True),
}
def base(s):
    p=dict(B.DEF); p["session"]=sess(s); p["sharpATR"]=sharp(s); return p

print("\n\n=== BASELINE vs IMPROVED (full history) + OOS stability (1st half | 2nd half) ===")
rows=[]
for s in SYMS:
    D=getp(s); nb=len(D["c"]); half=nb//2
    for label,cfg in [("base",base(s)),("IMPROVED",IMP[s])]:
        tr,st=B.backtest(D,cfg)
        h1=tr[tr.i<half]; h2=tr[tr.i>=half]
        s1=stt(h1); s2=stt(h2)
        rows.append(dict(sym=s,cfg=label,**st,
            H1=f"n{s1['n']} wr{s1['wr']} pf{s1['pf']} R{s1['netR']}",
            H2=f"n{s2['n']} wr{s2['wr']} pf{s2['pf']} R{s2['netR']}"))
df=pd.DataFrame(rows)
print(df[["sym","cfg","n","wr","pf","netR","exp","avgLoss","maxDD"]].to_string(index=False))
print("\n--- OOS stability (positive in BOTH halves = robust) ---")
print(df[["sym","cfg","H1","H2"]].to_string(index=False))

# pooled improved portfolio
print("\n=== POOLED PORTFOLIO (improved, all 3 symbols) ===")
allimp=[]
for s in SYMS:
    tr,_=B.backtest(getp(s),IMP[s]); tr["sym"]=s; allimp.append(tr)
ai=pd.concat(allimp,ignore_index=True)
ai.to_csv("trades_improved_M5.csv",index=False)
print(stt(ai))
print("by sym:", {s:stt(ai[ai.sym==s])['netR'] for s in SYMS})
