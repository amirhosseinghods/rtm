#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Apply what the ~14k-trade loop learned: score>=100, ATR%>=0.15, NO-SHARP REMOVED. Validate OOS."""
import rtm_bt as B
import pandas as pd, numpy as np
SYMS=["XAUUSD","BTCUSDT","XRPUSDT"]
preps={}
def getp(s,tf="M5"):
    if (s,tf) not in preps: preps[(s,tf)]=B.prep_symbol(s,tf)
    return preps[(s,tf)]
def sess(s): return "london" if s=="XAUUSD" else "ny"
def stt(tr):
    if len(tr)==0: return dict(n=0,wr=0,pf=0,netR=0,exp=0,avgLoss=0,maxDD=0)
    w=tr[tr.R>0]; lo=tr[tr.R<=0]
    pf=w.R.sum()/(-lo.R.sum()) if len(lo) and lo.R.sum()<0 else float('nan')
    return dict(n=len(tr),wr=round(100*len(w)/len(tr),1),pf=round(pf,2),netR=round(tr.R.sum(),1),
                exp=round(tr.R.mean(),3),avgLoss=round(lo.R.mean(),2) if len(lo) else 0,maxDD=round(B.dd(tr.R.values),1))
def halves(D,tr):
    half=len(D["c"])//2; return stt(tr[tr.i<half]), stt(tr[tr.i>=half])

FIN={
 "XAUUSD": dict(B.DEF, session="london", sharpATR=1.5, minScore=80, minStop=2.5, rr=1.5, roundTrip=False),
 "BTCUSDT":dict(B.DEF, session="ny", sharpATR=2.5, minScore=80, minStop=1.0, rr=2.0, roundTrip=False),
 "XRPUSDT":dict(B.DEF, session="ny", sharpATR=2.5, minScore=90, minStop=1.0, rr=2.0, roundTrip=True),
}
def learned(s, ms):
    # CORRECTED no-sharp: entry-TF proxy OFF, HTF (H4) directional no-sharp ON (validated OOS)
    return dict(B.DEF, session=sess(s), noSharp=False, noSharpHTF=True, sharpVelH4=0.3,
                minScore=ms, minATRpct=0.15, minGrade=2,
                minStop=(2.5 if s=="XAUUSD" else 1.0), rr=(1.5 if s=="XAUUSD" else 2.0),
                roundTrip=(s=="XRPUSDT"))

print("=== FINAL (s80, no-sharp ON) vs LEARNED (s90/s100, ATR%>=0.15, no-sharp OFF) + OOS halves ===")
rows=[]; pooled={}
for s in SYMS:
    D=getp(s)
    for lab,cfg in [("FINAL", FIN[s]), ("LEARNED-s90", learned(s,90)), ("LEARNED-s100", learned(s,100))]:
        tr,st=B.backtest(D,cfg); h1,h2=halves(D,tr)
        rows.append(dict(sym=s,cfg=lab,**st,OOS_H1=f"pf{h1['pf']}/R{h1['netR']}",OOS_H2=f"pf{h2['pf']}/R{h2['netR']}"))
        pooled.setdefault(lab,[]).append(tr.assign(sym=s))
df=pd.DataFrame(rows)
print(df[["sym","cfg","n","wr","pf","netR","exp","avgLoss","maxDD","OOS_H1","OOS_H2"]].to_string(index=False))

print("\n=== POOLED PORTFOLIOS ===")
for lab in ["FINAL","LEARNED-s90","LEARNED-s100"]:
    ai=pd.concat(pooled[lab],ignore_index=True); s=stt(ai)
    bal=1000.0; peak=1000.0; mdd=0.0
    for r in ai.sort_values("exit_i").R:
        bal*=(1+0.01*r); peak=max(peak,bal); mdd=min(mdd,(bal/peak-1)*100)
    print(f"{lab:13s}: n={s['n']:4d} WR={s['wr']} PF={s['pf']} netR={s['netR']} avgLoss={s['avgLoss']} | $1000->${bal:.0f} ({(bal/1000-1)*100:+.1f}%) maxDD={mdd:.1f}%")
    if lab=="LEARNED-s90": ai.to_csv("trades_LEARNED_M5.csv",index=False)
