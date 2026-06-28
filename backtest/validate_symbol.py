#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Vet a NEW symbol before adding it to the live watchlist.
Usage: python3 validate_symbol.py ETHUSDT [ny|london|off]
Checks (1) data quality, (2) does the VALIDATED config show an edge on it.
Verdict KEEP only if PF>1.2 with non-broken OOS halves & enough trades."""
import sys, os
import rtm_bt as B
import numpy as np, pandas as pd
DL = B.DL
TFS = ["M1","M5","M15","H1","H4"]

def data_quality(sym):
    print(f"=== DATA QUALITY: {sym} ===")
    ok = True
    for tf in TFS:
        p = f"{DL}/{sym}_{tf}.csv"
        if not os.path.exists(p):
            print(f"  {tf:4s} MISSING ({p})");
            if tf in ("M5","M15","H1","H4"): ok=False
            continue
        try:
            df = B.load(sym, tf)
        except Exception as e:
            print(f"  {tf:4s} LOAD-ERROR {e}"); ok=False; continue
        n=len(df); span=(df.index[-1]-df.index[0]).days
        dup=df.index.duplicated().sum()
        # gap check: median bar spacing vs expected
        d=df.index.to_series().diff().dropna()
        exp=pd.Timedelta(minutes=B.TF_MIN[tf]); big=(d>exp*3).sum()
        bad_ohlc=((df.High<df.Low)|(df.High<df.Close)|(df.Low>df.Close)).sum()
        print(f"  {tf:4s} bars={n:6d} span={span:4d}d dup={dup} gaps>{3}x={big:4d} badOHLC={bad_ohlc}  {df.index[0].date()}->{df.index[-1].date()}")
        if bad_ohlc>0: ok=False
    return ok

def stt(tr):
    if len(tr)==0: return dict(n=0,wr=0,pf=0,exp=0,maxDD=0)
    w=tr[tr.R>0]; lo=tr[tr.R<=0]
    pf=w.R.sum()/(-lo.R.sum()) if len(lo) and lo.R.sum()<0 else float('nan')
    return dict(n=len(tr),wr=round(100*len(w)/len(tr),1),pf=round(pf,2),
                exp=round(tr.R.mean(),3),maxDD=round(B.dd(tr.R.values),1))
def halfpf(t):
    w=t[t.R>0].R.sum(); l=-t[t.R<=0].R.sum(); return round(w/l,2) if l>0 else 0

def edge(sym, sess):
    print(f"\n=== EDGE TEST (validated v1.2 config, M5 signal): {sym} sess={sess} ===")
    D=B.prep_symbol(sym,"M5")
    is_gold = sess=="london"
    cfg=dict(B.DEF, session=sess, noSharpHTF=True, sharpVelH4=0.3, minScore=100,
             minATRpct=0.15, minGrade=2, zoneBuf=0.3,
             minStop=(2.5 if is_gold else 1.0), rr=(1.5 if is_gold else 2.0),
             roundTrip=True)
    tr,_=B.backtest(D,cfg); st=stt(tr)
    half=len(D["c"])//2
    h1=halfpf(tr[tr.i<half]) if len(tr) else 0; h2=halfpf(tr[tr.i>=half]) if len(tr) else 0
    span=(D["time"][-1]-D["time"][0]).total_seconds()/86400.0
    print(f"  n={st['n']} WR={st['wr']}% PF={st['pf']} exp={st['exp']:+.3f} maxDD={st['maxDD']} "
          f"trd/day={st['n']/span:.3f}  OOS pf H1={h1} H2={h2}")
    # verdict
    keep = (st['n']>=20 and st['pf'] is not None and st['pf']>=1.2 and h1>=0.9 and h2>=0.9)
    caution = (st['n']>=12 and st['pf'] and st['pf']>=1.1)
    v = "✅ KEEP — add to watchlist" if keep else ("⚠️ CAUTION — small sample / borderline, watch live" if caution else "❌ DROP — no validated edge")
    print(f"  VERDICT: {v}")
    return st, keep

if __name__=="__main__":
    sym = sys.argv[1] if len(sys.argv)>1 else "BTCUSDT"
    sess = sys.argv[2] if len(sys.argv)>2 else "ny"
    if not data_quality(sym):
        print("\n⛔ data quality issues — fix the export before trusting the edge test.")
    edge(sym, sess)
    print("\n(tip: gold-like metals -> use 'london'; crypto/indices -> 'ny'; unsure -> try both)")
