#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Frequency<->quality frontier for the user's M1+confirmation method.
Loosen zone gates (grade, score, 15m zones) WITH the M1 trigger as quality backstop,
to find the most trades/day that still keeps PF>1.2 (toward 3 trades/day)."""
import rtm_bt as B
import pandas as pd, numpy as np
SYMS=["XAUUSD","BTCUSDT","XRPUSDT"]
preps={}
def getp(s):
    if s not in preps: preps[s]=B.prep_symbol(s,"M1")
    return preps[s]
def span_days(s):
    df=B.load(s,"M5"); return (df.index[-1]-df.index[0]).total_seconds()/86400.0
def stt(tr):
    if len(tr)==0: return dict(n=0,wr=0,pf=0,exp=0,maxDD=0)
    w=tr[tr.R>0]; lo=tr[tr.R<=0]
    pf=w.R.sum()/(-lo.R.sum()) if len(lo) and lo.R.sum()<0 else float('nan')
    return dict(n=len(tr),wr=round(100*len(w)/len(tr),1),pf=round(pf,2),
                exp=round(tr.R.mean(),3),maxDD=round(B.dd(tr.R.values),1))
def halves(D,tr):
    h=len(D["c"])//2
    def pf(t):
        w=t[t.R>0].R.sum(); l=-t[t.R<=0].R.sum(); return round(w/l,2) if l>0 else 0
    return pf(tr[tr.i<h]),pf(tr[tr.i>=h])
def cfg(s, mg, ms, vrel):
    return dict(B.DEF, session="off", noSharp=False, noSharpHTF=True, sharpVelH4=0.3,
                minScore=ms, minGrade=mg, minATRpct=0.0, minATRrel=vrel, zoneBuf=0.3,
                useTrigger=True, armBars=15, invalidATR=1.0,
                minStop=(2.0 if s=="XAUUSD" else 0.7),
                rr=(1.5 if s=="XAUUSD" else 2.0), roundTrip=(s=="XRPUSDT"))

days=np.mean([span_days(s) for s in SYMS])
print(f"M1 + sweep/reclaim TRIGGER. ~{days:.0f} days x 3 syms. (3/day target = ~{3*days:.0f} trades)\n")
print(f"{'grade / score / vol':22s} {'n':>4s} {'WR':>5s} {'PF':>5s} {'exp':>7s} {'maxDD':>6s} {'trd/day':>8s} {'OOSpf H1|H2':>12s}")
# loosen progressively: grade2->1, score 90->60, relvol 1.0->0
PLAN=[(2,90,1.0),(2,78,1.0),(1,78,1.0),(1,68,1.0),(1,60,1.0),(1,60,0.0)]
for mg,ms,vrel in PLAN:
    allt=[]; pfs=[]
    for s in SYMS:
        D=getp(s); tr,_=B.backtest(D,cfg(s,mg,ms,vrel))
        if len(tr): pfs.append(halves(D,tr)); allt.append(tr.assign(sym=s))
    A=pd.concat(allt,ignore_index=True) if allt else pd.DataFrame()
    st=stt(A); tpd=st["n"]/days
    h1=np.mean([p[0] for p in pfs]) if pfs else 0; h2=np.mean([p[1] for p in pfs]) if pfs else 0
    lab=f"g{mg} s{ms} rel{vrel}"
    print(f"{lab:22s} {st['n']:4d} {st['wr']:5.1f} {st['pf']:5.2f} {st['exp']:+7.3f} {st['maxDD']:6.1f} {tpd:8.2f}   {h1:.2f}|{h2:.2f}")

print("\n-- per-symbol at the loosest viable gate (g1 s68 rel1.0) --")
for s in SYMS:
    D=getp(s); tr,_=B.backtest(D,cfg(s,1,68,1.0))
    st=stt(tr); h1,h2=halves(D,tr) if len(tr) else (0,0)
    print(f"  {s:8s} n={st['n']:3d} WR={st['wr']} PF={st['pf']} exp={st['exp']:+.3f} {st['n']/span_days(s):.2f}/day  OOS {h1}|{h2}")
