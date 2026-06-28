#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Test the Fibonacci-OTE ladder method (user's new idea) vs the validated single-entry.
With-trend only; ladder 0.25/0.5/0.25 at 0.5/0.618/0.786; stop behind swing, TP near swing."""
import rtm_bt as B
import pandas as pd, numpy as np
SYMS=["XAUUSD","BTCUSDT","XRPUSDT"]
preps={}
def getp(s,tf="M5"):
    if (s,tf) not in preps: preps[(s,tf)]=B.prep_symbol(s,tf)
    return preps[(s,tf)]
def sess(s): return "london" if s=="XAUUSD" else "ny"
def span_days(s):
    df=B.load(s,"M5"); return (df.index[-1]-df.index[0]).total_seconds()/86400.0
def halves(D,tr):
    if len(tr)==0: return 0,0
    hf=len(D["c"])//2
    def pf(t):
        w=t[t.R>0].R.sum(); l=-t[t.R<=0].R.sum(); return round(w/l,2) if l>0 else 0
    return pf(tr[tr.i<hf]),pf(tr[tr.i>=hf])

print(f"=== Fib-OTE LADDER vs validated single-entry (M5, with-trend) ===\n")
print(f"{'method / sym':22s} {'n':>4s} {'WR':>5s} {'PF':>5s} {'exp':>7s} {'maxDD':>6s} {'avgFills':>8s} {'trd/day':>7s} {'OOS H1|H2':>11s}")

VARIANTS={
 "FIB raw":        lambda s: dict(B.DEF, session=sess(s), zoneBuf=0.5, tpBuf=0.15, requireZone=False),
 "FIB +zone":      lambda s: dict(B.DEF, session=sess(s), zoneBuf=0.5, tpBuf=0.15, requireZone=True, minGrade=2),
 "FIB +zone+filt": lambda s: dict(B.DEF, session=sess(s), zoneBuf=0.5, tpBuf=0.15, requireZone=True, minGrade=2,
                                  noSharpHTF=True, sharpVelH4=0.3, minATRpct=0.15),
 "FIB +zone +RRtp": lambda s: dict(B.DEF, session=sess(s), zoneBuf=0.5, requireZone=True, minGrade=2,
                                  noSharpHTF=True, sharpVelH4=0.3, minATRpct=0.15, tpMode="rr"),
}
pools={k:[] for k in VARIANTS}; pooled_base=[]
for s in SYMS:
    D=getp(s)
    for name,mk in VARIANTS.items():
        cfg=mk(s); cfg["rr"]=(1.5 if s=="XAUUSD" else 2.0)
        trf,stf=B.backtest_fib(D,cfg); h1,h2=halves(D,trf); tpd=stf['n']/span_days(s) if stf['n'] else 0
        print(f"{name+' '+s:22s} {stf.get('n',0):4d} {stf.get('wr',0):5} {str(stf.get('pf')):>5} {stf.get('exp',0):+7.3f} {stf.get('maxDD',0):6} {stf.get('avgFills',0):8} {tpd:7.2f}   {h1}|{h2}")
        if stf['n']: pools[name].append(trf.assign(sym=s))
    bcfg=dict(B.DEF, session=sess(s), noSharpHTF=True, sharpVelH4=0.3, minScore=100,
              minATRpct=0.15, minGrade=2, zoneBuf=0.3, minStop=(2.5 if s=="XAUUSD" else 1.0),
              rr=(1.5 if s=="XAUUSD" else 2.0), roundTrip=True)
    trb,stb=B.backtest(D,bcfg); hb1,hb2=halves(D,trb); tpdb=stb['n']/span_days(s) if stb.get('n') else 0
    print(f"{'base '+s:22s} {stb.get('n',0):4d} {stb.get('wr',0):5} {str(stb.get('pf')):>5} {stb.get('exp',0):+7.3f} {stb.get('maxDD',0):6} {'-':>8} {tpdb:7.2f}   {hb1}|{hb2}")
    if stb.get('n'): pooled_base.append(trb.assign(sym=s))
    print()

def pooled(name, lst):
    if not lst: print(f"{name}: none"); return
    A=pd.concat(lst,ignore_index=True); w=A[A.R>0]; lo=A[A.R<=0]
    pf=w.R.sum()/(-lo.R.sum()) if len(lo) and lo.R.sum()<0 else float('nan')
    bal=1000.0; peak=1000.0; mdd=0
    for r in A.sort_values("exit_i").R:
        bal*=(1+0.01*r); peak=max(peak,bal); mdd=min(mdd,(bal/peak-1)*100)
    print(f"{name}: n={len(A)} WR={round(100*len(w)/len(A),1)} PF={round(pf,2)} netR={round(A.R.sum(),1)} "
          f"$1000->${bal:.0f} ({(bal/1000-1)*100:+.1f}%) maxDD={mdd:.1f}%")
print()
for name in VARIANTS: pooled(f"POOLED {name:14s}", pools[name])
pooled("POOLED base          ", pooled_base)
