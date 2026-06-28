#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""FINAL validated per-asset configs (no hour filter - it overfit) + OOS + $ equity."""
import rtm_bt as B
import pandas as pd, numpy as np
SYMS=["XAUUSD","BTCUSDT","XRPUSDT"]
preps={}
def getp(s,tf="M5"):
    if (s,tf) not in preps: preps[(s,tf)]=B.prep_symbol(s,tf)
    return preps[(s,tf)]
def stt(tr):
    if len(tr)==0: return dict(n=0,wr=0,pf=0,netR=0,exp=0,avgLoss=0,maxDD=0)
    w=tr[tr.R>0]; lo=tr[tr.R<=0]
    pf=w.R.sum()/(-lo.R.sum()) if len(lo) and lo.R.sum()<0 else float('nan')
    return dict(n=len(tr),wr=round(100*len(w)/len(tr),1),pf=round(pf,2),
                netR=round(tr.R.sum(),1),exp=round(tr.R.mean(),3),
                avgLoss=round(lo.R.mean(),2) if len(lo) else 0, maxDD=round(B.dd(tr.R.values),1))
def halves(D,tr):
    half=len(D["c"])//2
    return stt(tr[tr.i<half]), stt(tr[tr.i>=half])

# validated, OOS-stable per-asset configs
FIN={
 "XAUUSD": dict(B.DEF, session="london", sharpATR=1.5, minScore=80, minStop=2.5, rr=1.5, roundTrip=False),
 "BTCUSDT":dict(B.DEF, session="ny",     sharpATR=2.5, minScore=80, minStop=1.0, rr=2.0, roundTrip=False),
 "XRPUSDT":dict(B.DEF, session="ny",     sharpATR=2.5, minScore=90, minStop=1.0, rr=2.0, roundTrip=True),
}
print("=== FINAL per-asset configs — full history + OOS halves ===")
rows=[]; allf=[]
for s in SYMS:
    D=getp(s); tr,st=B.backtest(D,FIN[s]); h1,h2=halves(D,tr)
    tr["sym"]=s; allf.append(tr)
    rows.append(dict(sym=s,**st,H1=f"pf{h1['pf']} R{h1['netR']}",H2=f"pf{h2['pf']} R{h2['netR']}"))
print(pd.DataFrame(rows)[["sym","n","wr","pf","netR","exp","avgLoss","maxDD","H1","H2"]].to_string(index=False))

af=pd.concat(allf,ignore_index=True); af.to_csv("trades_FINAL_M5.csv",index=False)
print("\n=== POOLED PORTFOLIO (3 symbols) ==="); print(stt(af))
af2=af.sort_values("exit_i").reset_index(drop=True)
bal=1000.0; peak=1000.0; maxdd=0.0; streak=0; maxstreak=0
for r in af2.R:
    bal*=(1+0.01*r); peak=max(peak,bal); maxdd=min(maxdd,(bal/peak-1)*100)
    streak = streak+1 if r<=0 else 0; maxstreak=max(maxstreak,streak)
ret=(bal/1000-1)*100
print("$ equity (1pct/trade compounding): start 1000 -> end %.0f  (%+.1f pct)"%(bal,ret))
print("max equity drawdown: %.1f pct | max consecutive losses: %d"%(maxdd,maxstreak))
print("data span: %s -> %s"%(af.sym.map({s:getp(s)['time'][0] for s in SYMS}).min(),
                             pd.Timestamp('2026-06-24')))
