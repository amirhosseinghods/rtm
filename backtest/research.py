#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Learning corpus: record EVERY zone touch (thousands) + mine feature edges + funnel."""
import rtm_bt as B
import pandas as pd, numpy as np
SYMS=["XAUUSD","BTCUSDT","XRPUSDT"]; TFS=["M1","M5","M15"]
preps={}
def getp(s,tf):
    if (s,tf) not in preps: preps[(s,tf)]=B.prep_symbol(s,tf)
    return preps[(s,tf)]

frames=[]
for s in SYMS:
    for tf in TFS:
        df=B.research_scan(getp(s,tf), rr=1.5, minStop=1.0)
        df["sym"]=s; df["tf"]=tf; frames.append(df)
corp=pd.concat(frames, ignore_index=True)
corp.to_csv("research_trades.csv", index=False)
print(f"=== RESEARCH CORPUS: {len(corp)} labeled trades (every zone touch, no filters) ===")
print(corp.groupby(["sym","tf"]).size().unstack().to_string())
print(f"\noverall: WR {100*(corp.R>0).mean():.1f}%  exp {corp.R.mean():.3f}R  (UNFILTERED = baseline noise)")

def edge(df, col, bins=None, lab=None):
    d=df.copy()
    if bins is not None: d[col]=pd.cut(d[col], bins)
    g=d.groupby(col, observed=True).agg(n=("R","size"), wr=("R",lambda x:round(100*(x>0).mean(),1)),
        exp=("R",lambda x:round(x.mean(),3)), sumR=("R",lambda x:round(x.sum(),1)))
    return g

# ---- FUNNEL: why so few selective trades (gold M5) ----
gm5=corp[(corp.sym=="XAUUSD")&(corp.tf=="M5")]
print(f"\n=== FUNNEL (gold M5): {len(gm5)} raw zone touches -> selective trades ===")
steps=[("raw touches", gm5),
       ("grade==2", gm5[gm5.grade==2]),
       ("+ score>=80", gm5[(gm5.grade==2)&(gm5.score>=80)]),
       ("+ approach<1.5 (no-sharp)", gm5[(gm5.grade==2)&(gm5.score>=80)&(gm5.approach<1.5)]),
       ("+ withtrend", gm5[(gm5.grade==2)&(gm5.score>=80)&(gm5.approach<1.5)&(gm5.withtrend==1)])]
for nm,d in steps: print(f"  {nm:32s} n={len(d):5d}  exp={d.R.mean():+.3f}R" if len(d) else f"  {nm:32s} n=0")

print("\n=== FEATURE EDGE ANALYSIS — 'what decision -> what result, and why' ===")
print("\n-- by TYPE (1h vs 15m zones) --"); print(edge(corp,"type").to_string())
print("\n-- by GRADE --"); print(edge(corp,"grade").to_string())
print("\n-- by SCORE --"); print(edge(corp,"score").to_string())
print("\n-- by WITHTREND (aligned w/ HTF bias) --"); print(edge(corp,"withtrend").to_string())
corp["dpAligned"]=(((corp.dr==1)&(corp.disc==1))|((corp.dr==-1)&(corp.prem==1))).astype(int)
print("\n-- by DISCOUNT/PREMIUM aligned --"); print(edge(corp,"dpAligned").to_string())
print("\n-- by APPROACH sharpness ENTRY-TF (range/ATR) [OLD/WRONG proxy] --"); print(edge(corp,"approach",bins=[0,1,1.5,2,2.5,3,5,100]).to_string())
print("\n-- by HTF-H1 sharp-INTO-zone velocity (ATRs/bar) [CORRECT proxy] --"); print(edge(corp,"hsharp1",bins=[-0.01,0.001,0.2,0.4,0.6,0.8,1.2,9]).to_string())
print("\n-- by HTF-H4 sharp-INTO-zone velocity --"); print(edge(corp,"hsharp4",bins=[-0.01,0.001,0.2,0.4,0.6,0.8,1.2,9]).to_string())
print("\n-- by HTF-H1 leg efficiency (straightness) --"); print(edge(corp,"heff1",bins=[-0.01,0.2,0.4,0.6,0.8,1.01]).to_string())
print("\n-- by DIRECTION --"); print(edge(corp,"dr").to_string())
print("\n-- by ATR% (volatility regime) --"); print(edge(corp,"atrpct",bins=[0,0.03,0.06,0.1,0.15,0.25,0.5,5]).to_string())
print("\n-- by HOUR NY (best/worst) --")
eh=edge(corp,"hour").sort_values("exp",ascending=False); print(eh.to_string())
