#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Validate the CURATED watchlist (quality-first). Reads watchlist.txt.
Per-symbol table + pooled crypto edge (PF/WR/maxDD + time-split OOS) + gold separately."""
import os, re
import rtm_bt as B
import pandas as pd, numpy as np
DL=B.DL
WL=os.path.join(os.path.dirname(__file__),"watchlist.txt")

def watchlist():
    out=[]
    for ln in open(WL):
        ln=ln.split("#")[0].strip()
        if not ln: continue
        p=[x.strip() for x in ln.split(",")]
        out.append((p[0], p[1] if len(p)>1 else "ny"))
    return out

def cfg(sess):
    gold = sess=="london"
    return dict(B.DEF, session=sess, noSharpHTF=True, sharpVelH4=0.3, minScore=100,
               minATRpct=0.15, minGrade=2, zoneBuf=0.3, minStop=(2.5 if gold else 1.0),
               rr=(1.5 if gold else 2.0), roundTrip=True)
def run(s,sess):
    D=B.prep_symbol(s,"M5"); tr,st=B.backtest(D,cfg(sess))
    span=(D["time"][-1]-D["time"][0]).total_seconds()/86400.0
    if len(tr): tr=tr.assign(sym=s, t=[D["time"][i] for i in tr.i])
    return tr, st, span
def stt(A):
    w=A[A.R>0]; lo=A[A.R<=0]
    pf=w.R.sum()/(-lo.R.sum()) if len(lo) and lo.R.sum()<0 else float('nan')
    return len(A), round(100*len(w)/len(A),1), round(pf,2), round(A.R.mean(),3), round(B.dd(A.R.values),1)

wl=watchlist()
print(f"=== CURATED watchlist ({len(wl)} symbols), validated v1.2 ===")
print(f"{'sym':9s} {'sess':7s} {'spanD':>5s} {'n':>3s} {'WR':>5s} {'PF':>5s} {'exp':>7s}")
poolC=[]; gold=None
for s,sess in wl:
    if not all(os.path.exists(f"{DL}/{s}_{tf}.csv") for tf in ("M5","M15","H1","H4")):
        print(f"{s:9s} {sess:7s}  (missing CSVs)"); continue
    try: tr,st,span=run(s,sess)
    except Exception as e: print(f"{s:9s} {sess:7s} ERROR {str(e)[:35]}"); continue
    n=st.get('n',0)
    if n:
        _,wr,pf,exp,_=stt(tr)
        print(f"{s:9s} {sess:7s} {span:5.0f} {n:3d} {wr:5} {pf:>5} {exp:+7.3f}")
        if sess=="london": gold=tr
        else: poolC.append(tr)
    else:
        print(f"{s:9s} {sess:7s} {span:5.0f}   0     -     -        -")

def report(name, A):
    if A is None or len(A)==0: print(f"\n{name}: no trades"); return
    A=A.sort_values("t").reset_index(drop=True)
    n,wr,pf,exp,mdd=stt(A)
    mid=A.t.iloc[len(A)//2]; h1=A[A.t<mid]; h2=A[A.t>=mid]
    def pf_(x):
        w=x[x.R>0].R.sum(); l=-x[x.R<=0].R.sum(); return round(w/l,2) if l>0 else 0
    bal=1000.0; peak=1000.0; m=0
    for r in A.R: bal*=(1+0.01*r); peak=max(peak,bal); m=min(m,(bal/peak-1)*100)
    days=(A.t.iloc[-1]-A.t.iloc[0]).total_seconds()/86400.0
    print(f"\n{name}: n={n} WR={wr} PF={pf} exp={exp} maxDD={m:.1f}% | $1000->${bal:.0f} ({(bal/1000-1)*100:+.1f}%)")
    print(f"   {len(A)/days:.2f} trd/day  |  OOS time-split: H1 PF={pf_(h1)}(n{len(h1)})  H2 PF={pf_(h2)}(n{len(h2)})")

report("POOLED CRYPTO (curated)", pd.concat(poolC,ignore_index=True) if poolC else None)
report("GOLD (london)", gold)
