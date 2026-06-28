#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""User's real MTF model: zones from H1+M15, ENTER on M1, manage on M5.
Test M1-entry vs M5/M15 to see if M1 gives more trades AND keeps the edge.
(M5-manage == M1-exit for a fixed SL/TP level, so M1 entry/exit is a faithful proxy.)"""
import rtm_bt as B
import pandas as pd, numpy as np
SYMS=["XAUUSD","BTCUSDT","XRPUSDT"]
preps={}
def getp(s,tf):
    if (s,tf) not in preps: preps[(s,tf)]=B.prep_symbol(s,tf)
    return preps[(s,tf)]
def span_days(s,tf="M5"):
    df=B.load(s,tf); return (df.index[-1]-df.index[0]).total_seconds()/86400.0
def stt(tr):
    if len(tr)==0: return dict(n=0,wr=0,pf=0,exp=0,maxDD=0)
    w=tr[tr.R>0]; lo=tr[tr.R<=0]
    pf=w.R.sum()/(-lo.R.sum()) if len(lo) and lo.R.sum()<0 else float('nan')
    return dict(n=len(tr),wr=round(100*len(w)/len(tr),1),pf=round(pf,2),
                exp=round(tr.R.mean(),3),maxDD=round(B.dd(tr.R.values),1))
def halves(D,tr):
    h=len(D["c"])//2; a=tr[tr.i<h]; b=tr[tr.i>=h]
    def pf(t):
        w=t[t.R>0].R.sum(); l=-t[t.R<=0].R.sum(); return round(w/l,2) if l>0 else 0
    return pf(a),pf(b)
def cfg(s, ms, sess_on, vrel, trig, buf):
    """vrel>0 => TF-relative vol gate (M1); trig => M1 sweep+reclaim confirmation."""
    return dict(B.DEF, session=(("london" if s=="XAUUSD" else "ny") if sess_on else "off"),
                noSharp=False, noSharpHTF=True, sharpVelH4=0.3, minScore=ms,
                minATRpct=(0.0 if vrel>0 else 0.15), minATRrel=vrel, zoneBuf=buf,
                useTrigger=trig, armBars=15, invalidATR=1.0,
                minGrade=2, minStop=(2.0 if s=="XAUUSD" else 0.7),
                rr=(1.5 if s=="XAUUSD" else 2.0), roundTrip=(s=="XRPUSDT"))

days = np.mean([span_days(s) for s in SYMS])
print(f"~{days:.0f} days of data. Target 3 trd/day = ~{3*days:.0f} trades over the span.\n")
print(f"{'entry/score/vol/trig/buf':30s} {'n':>4s} {'WR':>5s} {'PF':>5s} {'exp':>7s} {'maxDD':>6s} {'trd/day':>8s} {'OOSpf H1|H2':>12s}")
# (entry_tf, score, sess_on, vrel, trig, buf)
PLAN=[("M1",90,False,1.0,True,0.3), ("M1",90,False,1.0,True,0.5), ("M1",90,False,1.0,True,0.8),
      ("M1",100,False,1.0,True,0.5),
      ("M1",90,False,1.0,False,0.3),                 # M1 no-trigger (baseline, the loser)
      ("M5",100,True,0.0,False,0.3), ("M5",90,False,0.0,False,0.3)]
for etf,ms,sess_on,vrel,trig,buf in PLAN:
    allt=[]; pfs=[]
    for s in SYMS:
        D=getp(s,etf); tr,_=B.backtest(D,cfg(s,ms,sess_on,vrel,trig,buf))
        if len(tr):
            p1,p2=halves(D,tr); pfs.append((p1,p2)); allt.append(tr.assign(sym=s))
    A=pd.concat(allt,ignore_index=True) if allt else pd.DataFrame()
    st=stt(A); tpd=st["n"]/days
    h1=np.mean([p[0] for p in pfs]) if pfs else 0; h2=np.mean([p[1] for p in pfs]) if pfs else 0
    vg=f"rel{vrel}" if vrel>0 else "atr%"
    lab=f"{etf} s{ms} {vg} {'TRIG' if trig else 'touch'} b{buf}"
    print(f"{lab:30s} {st['n']:4d} {st['wr']:5.1f} {st['pf']:5.2f} {st['exp']:+7.3f} {st['maxDD']:6.1f} {tpd:8.2f}   {h1:.2f}|{h2:.2f}")
