#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Single-TP exit (the validated edge) swept over exact R targets: 1:1, 1.5:1, 2:1, 3:1.
TP = entry + rr*risk exactly. Same FTR/grade2/score100/no-sharp entry."""
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
               minATRpct=0.15, minGrade=2, zoneBuf=0.3, minStop=(2.5 if g else 1.0), roundTrip=True)
RRS=[1.0,1.5,2.0,3.0]
def halves(D,tr):
    if len(tr)==0: return 0,0
    hf=len(D["c"])//2
    def pf(t):
        w=t[t.R>0].R.sum(); l=-t[t.R<=0].R.sum(); return round(w/l,2) if l>0 else 0
    return pf(tr[tr.i<hf]),pf(tr[tr.i>=hf])
print(f"{'RR / sym':18s} {'n':>4s} {'WR%':>5s} {'PF':>5s} {'exp':>7s} {'netR':>6s} {'maxDD':>6s} {'OOS H1|H2':>11s}")
print("="*78)
pools={rr:[] for rr in RRS}
for s in SYMS:
    D=getp(s)
    for rr in RRS:
        tr,st=B.backtest(D, dict(base(s), rr=rr)); h1,h2=halves(D,tr)
        print(f"{('1:'+str(rr)+' '+s):18s} {st.get('n',0):4d} {str(st.get('wr','-')):>5} {str(st.get('pf')):>5} "
              f"{st.get('exp',0):+7.3f} {str(st.get('netR','-')):>6} {str(st.get('maxDD','-')):>6} {h1}|{h2}")
        if st.get('n'): pools[rr].append(tr.assign(sym=s))
    print("-"*78)
def pooled(rr,lst):
    if not lst: print(f"1:{rr}: none"); return
    A=pd.concat(lst,ignore_index=True); w=A[A.R>0]; lo=A[A.R<=0]
    pf=w.R.sum()/(-lo.R.sum()) if len(lo) and lo.R.sum()<0 else float('nan')
    bal=1000.0; peak=1000.0; mdd=0
    for r in A.sort_values("exit_i").R: bal*=(1+0.01*r); peak=max(peak,bal); mdd=min(mdd,(bal/peak-1)*100)
    print(f"RR 1:{rr:<4}: n={len(A):3d} WR={round(100*len(w)/len(A),1):4} PF={round(pf,2):4} netR={round(A.R.sum(),1):6} exp={round(A.R.mean(),3):+} | $1000->${bal:6.0f} ({(bal/1000-1)*100:+.1f}%) maxDD={mdd:.1f}%")
print("\n=== POOLED single-TP (XAU+BTC+XRP) ===")
for rr in RRS: pooled(rr,pools[rr])
