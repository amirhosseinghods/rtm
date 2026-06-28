#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Backtest the NEW R-based grid (RR_TP3=3.0, slBreath=0.5) vs the OLD W-based grid
(RR_TP3=1.5, SL=2W) on real data. Same FTR/grade2/score100/no-sharp entry; only the
exit geometry differs. Scale-out 1/3 each, BE after TP1."""
import rtm_bt as B
import pandas as pd, numpy as np
SYMS=["XAUUSD","BTCUSDT","XRPUSDT"]
preps={}
def getp(s):
    if s not in preps: preps[s]=B.prep_symbol(s,"M5")
    return preps[s]
def sess(s): return "london" if s=="XAUUSD" else "ny"
def base(s):
    g=s=="XAUUSD"
    return dict(B.DEF, session=sess(s), noSharpHTF=True, sharpVelH4=0.3, minScore=100,
               minATRpct=0.15, minGrade=2, zoneBuf=0.3, minStop=(2.5 if g else 1.0),
               rr=(1.5 if g else 2.0), roundTrip=True)

VARIANTS = {
  "OLD W-grid (RR1.5)": dict(tpMode="W", slBreathBT=1.0),
  "NEW R-grid (RR3.0)": dict(tpMode="R", slBreathBT=0.5),
  "R-grid slB=1.0"    : dict(tpMode="R", slBreathBT=1.0),
}

def halves(D,tr):
    if len(tr)==0: return 0,0
    hf=len(D["c"])//2
    def pf(t):
        w=t[t.R>0].R.sum(); l=-t[t.R<=0].R.sum(); return round(w/l,2) if l>0 else 0
    return pf(tr[tr.i<hf]),pf(tr[tr.i>=hf])

print(f"{'variant / sym':24s} {'n':>4s} {'WR%':>5s} {'PF':>5s} {'exp':>7s} {'netR':>6s} {'avgTP':>5s} {'maxDD':>6s} {'OOS H1|H2':>11s}")
print("="*92)
pools = {v:[] for v in VARIANTS}
for s in SYMS:
    D=getp(s)
    for vname,vp in VARIANTS.items():
        cfg=dict(base(s), **vp)
        tr,st=B.backtest_grid(D,cfg); h1,h2=halves(D,tr)
        print(f"{vname[:13]+' '+s:24s} {st.get('n',0):4d} {str(st.get('wr','-')):>5} {str(st.get('pf')):>5} "
              f"{st.get('exp',0):+7.3f} {str(st.get('netR','-')):>6} {str(st.get('avgTPs','-')):>5} {str(st.get('maxDD','-')):>6} {h1}|{h2}")
        if st.get('n'): pools[vname].append(tr.assign(sym=s))
    print("-"*92)

def pooled(name,lst):
    if not lst: print(f"{name:24s}: none"); return
    A=pd.concat(lst,ignore_index=True); w=A[A.R>0]; lo=A[A.R<=0]
    pf=w.R.sum()/(-lo.R.sum()) if len(lo) and lo.R.sum()<0 else float('nan')
    bal=1000.0; peak=1000.0; mdd=0
    for r in A.sort_values("exit_i").R: bal*=(1+0.01*r); peak=max(peak,bal); mdd=min(mdd,(bal/peak-1)*100)
    print(f"{name:24s}: n={len(A):3d} WR={round(100*len(w)/len(A),1):4} PF={round(pf,2)} "
          f"netR={round(A.R.sum(),1):6} exp={round(A.R.mean(),3):+} | $1000->${bal:6.0f} ({(bal/1000-1)*100:+.1f}%) maxDD={mdd:.1f}%")
print("\n=== POOLED (XAU+BTC+XRP) ===")
for v in VARIANTS: pooled(v, pools[v])
