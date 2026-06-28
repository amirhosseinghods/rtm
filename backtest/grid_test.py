#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""User's measured-move GRID exit (1/3 at 1W/2W/3W, SL=2W, BE after TP1) vs validated single-TP.
Same FTR/grade2/score100/no-sharp entry; only the exit differs."""
import rtm_bt as B
import pandas as pd, numpy as np
SYMS=["XAUUSD","BTCUSDT","XRPUSDT"]
preps={}
def getp(s):
    if s not in preps: preps[s]=B.prep_symbol(s,"M5")
    return preps[s]
def sess(s): return "london" if s=="XAUUSD" else "ny"
def halves(D,tr):
    if len(tr)==0: return 0,0
    hf=len(D["c"])//2
    def pf(t):
        w=t[t.R>0].R.sum(); l=-t[t.R<=0].R.sum(); return round(w/l,2) if l>0 else 0
    return pf(tr[tr.i<hf]),pf(tr[tr.i>=hf])
def base_cfg(s):
    g=s=="XAUUSD"
    return dict(B.DEF, session=sess(s), noSharpHTF=True, sharpVelH4=0.3, minScore=100,
               minATRpct=0.15, minGrade=2, zoneBuf=0.3, minStop=(2.5 if g else 1.0),
               rr=(1.5 if g else 2.0), roundTrip=True)

print("=== GRID exit (your algo) vs SINGLE-TP (validated) — per symbol + pooled ===\n")
print(f"{'method / sym':20s} {'n':>4s} {'WR':>5s} {'PF':>5s} {'exp':>7s} {'maxDD':>6s} {'extra':>9s} {'OOS H1|H2':>11s}")
poolG=[]; poolB=[]
for s in SYMS:
    D=getp(s)
    trg,stg=B.backtest_grid(D, base_cfg(s)); h1,h2=halves(D,trg)
    print(f"{'GRID '+s:20s} {stg.get('n',0):4d} {stg.get('wr',0):5} {str(stg.get('pf')):>5} {stg.get('exp',0):+7.3f} {stg.get('maxDD',0):6} {('avgTP '+str(stg.get('avgTPs',0))):>9} {h1}|{h2}")
    if stg.get('n'): poolG.append(trg.assign(sym=s))
    trb,stb=B.backtest(D, base_cfg(s)); hb1,hb2=halves(D,trb)
    print(f"{'  single '+s:20s} {stb.get('n',0):4d} {stb.get('wr',0):5} {str(stb.get('pf')):>5} {stb.get('exp',0):+7.3f} {stb.get('maxDD',0):6} {'-':>9} {hb1}|{hb2}")
    if stb.get('n'): poolB.append(trb.assign(sym=s))

def pooled(name,lst):
    if not lst: print(f"{name}: none"); return
    A=pd.concat(lst,ignore_index=True); w=A[A.R>0]; lo=A[A.R<=0]
    pf=w.R.sum()/(-lo.R.sum()) if len(lo) and lo.R.sum()<0 else float('nan')
    bal=1000.0; peak=1000.0; mdd=0
    for r in A.sort_values("exit_i").R: bal*=(1+0.01*r); peak=max(peak,bal); mdd=min(mdd,(bal/peak-1)*100)
    print(f"{name}: n={len(A)} WR={round(100*len(w)/len(A),1)} PF={round(pf,2)} netR={round(A.R.sum(),1)} exp={round(A.R.mean(),3)} | $1000->${bal:.0f} ({(bal/1000-1)*100:+.1f}%) maxDD={mdd:.1f}%")
print()
pooled("POOLED GRID  ", poolG)
pooled("POOLED single", poolB)
