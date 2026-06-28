#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DAILY RANKED SCANNER — the tool you actually use.
Auto-discovers every symbol with data in ~/Downloads, finds recent A-grade setups
(validated v1.2 config), ranks them best-first, and attaches the historical WR/PF for
that setup bucket so you can pick your top ~3. Refresh the CSVs -> rerun -> today's list.

Usage: python3 scanner.py [recent_days]   (default 10)
"""
import os, sys, glob, re
import rtm_bt as B
import numpy as np, pandas as pd

DL=B.DL
RECENT_DAYS=int(sys.argv[1]) if len(sys.argv)>1 else 10
WL=os.path.join(os.path.dirname(__file__),"watchlist.txt")

def discover():
    """Use curated watchlist.txt if present (quality-first); else all *_M5.csv."""
    if os.path.exists(WL):
        out=[]
        for ln in open(WL):
            ln=ln.split("#")[0].strip()
            if not ln: continue
            parts=[x.strip() for x in ln.split(",")]
            out.append((parts[0], parts[1] if len(parts)>1 else "ny"))
        return out
    res=[]
    for p in glob.glob(f"{DL}/*_M5.csv"):
        m=re.match(r"(.+)_M5\.csv$", os.path.basename(p))
        if m: res.append((m.group(1), "london" if m.group(1)=="XAUUSD" else "ny"))
    return sorted(res)

def cfg_for(sess):
    gold = sess=="london"
    return dict(B.DEF, session=sess, noSharpHTF=True, sharpVelH4=0.3,
               minScore=100, minATRpct=0.15, minGrade=2, zoneBuf=0.3,
               minStop=(2.5 if gold else 1.0), rr=(1.5 if gold else 2.0), roundTrip=True)

def hist_bucket(D, p):
    """historical WR/PF/exp for THIS symbol's A-grade bucket (so the user sees the base rate)."""
    tr,_=B.backtest(D,p)
    if len(tr)==0: return (0,0,0,0)
    w=tr[tr.R>0]; lo=tr[tr.R<=0]
    pf=w.R.sum()/(-lo.R.sum()) if len(lo) and lo.R.sum()<0 else float('nan')
    return (len(tr), round(100*len(w)/len(tr),1), round(pf,2) if pf==pf else 0, round(tr.R.mean(),3))

def main():
    syms=discover()
    if not syms:
        print(f"No *_M5.csv found in {DL}. Export symbols as {{SYM}}_{{TF}}.csv first."); return
    print(f"Scanning {len(syms)} curated symbols for A-grade setups in the last {RECENT_DAYS} days...\n")
    allsig=[]
    for s,sess in syms:
        # need M5/M15/H1/H4 at minimum
        if not all(os.path.exists(f"{DL}/{s}_{tf}.csv") for tf in ("M5","M15","H1","H4")):
            print(f"  (skip {s}: missing HTF/zone CSVs)"); continue
        try:
            D=B.prep_symbol(s,"M5"); p=cfg_for(sess)
        except Exception as e:
            print(f"  (skip {s}: {e})"); continue
        bars_per_day=int(24*60/5)
        sig=B.scan_signals(D,p,last_n=RECENT_DAYS*bars_per_day)
        hn,hwr,hpf,hexp=hist_bucket(D,p)
        if len(sig):
            sig=sig.assign(sym=s, histWR=hwr, histPF=hpf, histExp=hexp, histN=hn)
            allsig.append(sig)
    if not allsig:
        print("\nNo A-grade setups in the recent window. (Normal — edge is rare; ~1 per 5 days per 3 syms.)")
        print("Don't force a trade. Wait for quality, or add more symbols.")
        return
    A=pd.concat(allsig,ignore_index=True)
    # rank: score, then with-trend, then historical expectancy, then recency
    A["rank_key"]=A.score*1.0 + A.withtrend*5 + A.histExp*20 + (A.bar/A.bar.max())*2
    A=A.sort_values("rank_key",ascending=False).reset_index(drop=True)
    print(f"=== {len(A)} A-GRADE SETUPS (ranked best-first) — take your top ~3 ===\n")
    cols=["sym","time","dir","type","grade","score","withtrend","entry","sl","tp","rr","histWR","histPF","histN"]
    pd.set_option("display.width",200); pd.set_option("display.max_columns",30)
    print(A[cols].head(15).to_string(index=False))
    print("\nNotes:")
    print(" • histWR/histPF = this symbol's historical hit-rate for the SAME A-grade bucket (your base rate).")
    print(" • withtrend=1 means aligned with HTF bias (prefer these).")
    print(" • EXECUTION: drop to 1m, wait for sweep+reclaim of a micro swing INTO the zone, enter, stop = sl.")
    print(" • Expect ~45-52% win-rate — half hit SL; the edge is the 1.5-2R winners. Don't over-trade.")

if __name__=="__main__":
    main()
