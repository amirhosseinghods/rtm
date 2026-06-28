#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Focused validation of the chosen setup: single-TP at exactly 1:1 (TP=entry+1*risk),
across the full curated watchlist, per-symbol + pooled with OOS halves + equity curve."""
import rtm_bt as B
import pandas as pd, numpy as np, os

WL=os.path.expanduser("~/Desktop/trade/backtest/watchlist.txt")
syms=[]
for line in open(WL):
    line=line.strip()
    if not line or line.startswith("#"): continue
    for tok in line.split():
        s,_,sess=tok.partition(",")
        syms.append((s, sess or "ny"))

def base(s,sess):
    g=s=="XAUUSD"
    return dict(B.DEF, session=sess, noSharpHTF=True, sharpVelH4=0.3, minScore=100,
               minATRpct=0.15, minGrade=2, zoneBuf=0.3, minStop=(2.5 if g else 1.0),
               rr=1.0, roundTrip=True)
def halves(D,tr):
    if len(tr)==0: return 0,0
    hf=len(D["c"])//2
    def pf(t):
        w=t[t.R>0].R.sum(); l=-t[t.R<=0].R.sum(); return round(w/l,2) if l>0 else 0
    return pf(tr[tr.i<hf]),pf(tr[tr.i>=hf])

print("=== Single-TP @ exactly 1:1  (TP = entry + 1R) ===\n")
print(f"{'sym':10s} {'n':>4s} {'WR%':>5s} {'PF':>5s} {'exp':>7s} {'netR':>6s} {'maxDD':>6s} {'OOS H1|H2':>11s}")
print("-"*64)
pool=[]
for s,sess in syms:
    try: D=B.prep_symbol(s,"M5")
    except Exception as e:
        print(f"{s:10s} data err {str(e)[:30]}"); continue
    tr,st=B.backtest(D, base(s,sess)); h1,h2=halves(D,tr)
    print(f"{s:10s} {st.get('n',0):4d} {str(st.get('wr','-')):>5} {str(st.get('pf')):>5} "
          f"{st.get('exp',0):+7.3f} {str(st.get('netR','-')):>6} {str(st.get('maxDD','-')):>6} {h1}|{h2}")
    if st.get('n'): pool.append(tr.assign(sym=s))

A=pd.concat(pool,ignore_index=True); w=A[A.R>0]; lo=A[A.R<=0]
pf=w.R.sum()/(-lo.R.sum()) if len(lo) and lo.R.sum()<0 else float('nan')
hf=len(A)//2; As=A.sort_values("exit_i").reset_index(drop=True)
def pf2(t):
    ww=t[t.R>0].R.sum(); ll=-t[t.R<=0].R.sum(); return round(ww/ll,2) if ll>0 else 0
bal=1000.0; peak=1000.0; mdd=0
for r in As.R: bal*=(1+0.01*r); peak=max(peak,bal); mdd=min(mdd,(bal/peak-1)*100)
print("-"*64)
print(f"\n=== POOLED ({len(pool)} symbols) ===")
print(f"n={len(A)}  WR={round(100*len(w)/len(A),1)}%  PF={round(pf,2)}  netR={round(A.R.sum(),1)}  exp={round(A.R.mean(),3)}")
print(f"$1000 -> ${bal:.0f}  ({(bal/1000-1)*100:+.1f}%)   maxDD={mdd:.1f}%")
print(f"OOS split: first-half PF={pf2(As.iloc[:hf])}  |  second-half PF={pf2(As.iloc[hf:])}")
