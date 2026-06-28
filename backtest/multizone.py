#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Multi-zone scanner: collect ALL unmitigated order-block zones from HTF (H1,M15,H4)
near current price — the way the user hand-draws several supplies above + demands below.
Compares output to the user's hand-drawn gold zones."""
import rtm_bt as B
import numpy as np, sys

def collect_zones(sym, tf, L=5, impulse=1.0):
    df = B.load(sym, tf)
    o,h,l,c = (df[x].values.astype(float) for x in ["Open","High","Low","Close"])
    atr = B.atr_rma(h,l,c,14)
    tr,pH,pL,bu,bd,cu,cd = B.struct_engine(o,h,l,c,L)
    n = len(c)
    zones = []
    lbT=lbB=luT=luB=np.nan
    for i in range(n):
        bF = (i>=2) and (l[i] > h[i-2])
        sF = (i>=2) and (h[i] < l[i-2])
        if c[i] < o[i]: lbT=max(o[i],c[i]); lbB=l[i]
        if c[i] > o[i]: luT=h[i]; luB=min(o[i],c[i])
        if (bu[i] or cu[i]) and not np.isnan(lbB):
            g = (1.0 if bF else 0.0) + (1.0 if (h[i]-lbB)>=impulse*atr[i] else 0.0)
            zones.append(dict(kind="demand", top=lbT, bot=lbB, grade=int(g), tf=tf, i=i, t=df.index[i]))
        if (bd[i] or cd[i]) and not np.isnan(luT):
            g = (1.0 if sF else 0.0) + (1.0 if (luT-l[i])>=impulse*atr[i] else 0.0)
            zones.append(dict(kind="supply", top=luT, bot=luB, grade=int(g), tf=tf, i=i, t=df.index[i]))
    # mitigation: a zone is "fresh" until price later trades through its far edge
    for z in zones:
        future_l = l[z["i"]+1:]; future_h = h[z["i"]+1:]
        if z["kind"]=="demand":
            z["mitigated"] = bool(len(future_l) and future_l.min() <= z["bot"])
            # touched = wick into zone but not through
            z["touched"] = bool(len(future_l) and future_l.min() <= z["top"])
        else:
            z["mitigated"] = bool(len(future_h) and future_h.max() >= z["top"])
            z["touched"] = bool(len(future_h) and future_h.max() >= z["bot"])
    return zones, float(c[-1])

def scan(sym="XAUUSD", near_pct=4.0):
    allz=[]; price=None
    for tf in ["H4","H1","M15"]:
        z,p = collect_zones(sym, tf); allz += z; price = p
    lo = price*(1-near_pct/100); hi = price*(1+near_pct/100)
    # keep UNmitigated zones whose midpoint is within near window
    fresh=[]
    for z in allz:
        mid=(z["top"]+z["bot"])/2
        if z["mitigated"]: continue
        if not (lo <= mid <= hi): continue
        # zone on correct side of price (supply above, demand below)
        if z["kind"]=="supply" and z["bot"] < price: continue
        if z["kind"]=="demand" and z["top"] > price: continue
        fresh.append(z)
    # merge overlapping same-kind zones (keep widest / highest grade); prefer HTF
    tford={"H4":3,"H1":2,"M15":1}
    fresh.sort(key=lambda z:(-tford[z["tf"]], -z["grade"]))
    kept=[]
    for z in fresh:
        ov=False
        for k in kept:
            if z["kind"]==k["kind"] and not (z["top"]<k["bot"] or z["bot"]>k["top"]):
                ov=True; break
        if not ov: kept.append(z)
    sup=sorted([z for z in kept if z["kind"]=="supply"], key=lambda z:z["bot"])
    dem=sorted([z for z in kept if z["kind"]=="demand"], key=lambda z:-z["top"])
    return price, sup, dem

if __name__=="__main__":
    sym = sys.argv[1] if len(sys.argv)>1 else "XAUUSD"
    near = float(sys.argv[2]) if len(sys.argv)>2 else 4.0
    price, sup, dem = scan(sym, near)
    print(f"=== {sym}  price {price:.2f}  (window ±{near}%) ===\n")
    print("SUPPLY (above price), nearest first:")
    for z in sup[:6]:
        print(f"  {z['bot']:.3f} - {z['top']:.3f}  g{z['grade']} [{z['tf']}] {z['t']}  {'·touched' if z['touched'] else ''}")
    print("\nDEMAND (below price), nearest first:")
    for z in dem[:6]:
        print(f"  {z['bot']:.3f} - {z['top']:.3f}  g{z['grade']} [{z['tf']}] {z['t']}  {'·touched' if z['touched'] else ''}")
