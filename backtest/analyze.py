#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The validated 'brain' for any symbol: bias + nearest demand/supply zones +
a LONG and a SHORT setup (entry/SL/TP with the symbol's validated R:R).
Usage: python3 analyze.py BTCUSDT [ny|london]   -> prints human table + JSON."""
import rtm_bt as B
import numpy as np, json, sys

def analyze(sym, sess="ny"):
    D = B.prep_symbol(sym, "M5")
    i = len(D["c"]) - 1
    price = float(D["c"][i]); atr = float(D["atr"][i])
    b1,b2,b3 = int(D["b1"][i]), int(D["b2"][i]), int(D["b3"][i])
    bias = int(np.sign(1*b1 + 2*b2 + 2*b3))
    pH,pL = D["pH"][i], D["pL"][i]; mid = (pL + 0.5*(pH-pL)) if (pH==pH and pL==pL) else float("nan")
    gold = (sess == "london")
    rr = 1.5 if gold else 2.0; buf = 0.3; minStop = 2.5 if gold else 1.0
    def pick(prefix):
        cb,ct,cg = D[f"c_{prefix}B"][i], D[f"c_{prefix}T"][i], D[f"c_{prefix}G"][i]
        ab,at,ag = D[f"a_{prefix}B"][i], D[f"a_{prefix}T"][i], D[f"a_{prefix}G"][i]
        if not np.isnan(cb): return float(cb), float(ct), int(cg), "1h"
        if not np.isnan(ab): return float(ab), float(at), int(ag), "15m"
        return None
    dem, sup = pick("dem"), pick("sup")
    out = dict(sym=sym, price=round(price,2), atr=round(atr,4),
               bias=("LONG" if bias>0 else "SHORT" if bias<0 else "NEUTRAL"),
               bias_parts=dict(h1=b1,h4=b2,d=b3),
               zone_mid=(round(mid,2) if mid==mid else None),
               location=(None if mid!=mid else ("discount" if price<mid else "premium")),
               rr=rr)
    if dem:
        db,dt,dg,dtf = dem
        entry = dt; stop = db - buf*atr; risk = entry - stop
        if risk > 0:
            out["long"] = dict(zone=[round(db,2),round(dt,2)], grade=dg, tf=dtf,
                entry=round(entry,2), stop=round(stop,2), target=round(entry+rr*risk,2),
                risk=round(risk,2), withtrend=(bias>0), dist_to_entry=round(entry-price,2))
    if sup:
        sb,st,sg,stf = sup
        entry = sb; stop = st + buf*atr; risk = stop - entry
        if risk > 0:
            out["short"] = dict(zone=[round(sb,2),round(st,2)], grade=sg, tf=stf,
                entry=round(entry,2), stop=round(stop,2), target=round(entry-rr*risk,2),
                risk=round(risk,2), withtrend=(bias<0), dist_to_entry=round(sb-price,2))
    return out

if __name__ == "__main__":
    sym = sys.argv[1] if len(sys.argv)>1 else "BTCUSDT"
    sess = sys.argv[2] if len(sys.argv)>2 else ("london" if sym=="XAUUSD" else "ny")
    a = analyze(sym, sess)
    print(f"=== {a['sym']}  price {a['price']}  bias {a['bias']} (H1{a['bias_parts']['h1']:+d} H4{a['bias_parts']['h4']:+d} D{a['bias_parts']['d']:+d})  {a['location']}  R:R {a['rr']} ===")
    for side in ("long","short"):
        if side in a:
            s = a[side]; wt = "WITH-trend ✓" if s["withtrend"] else "counter-trend ⚠"
            print(f"  {side.upper():5s} [{wt}] zone {s['zone']} grade{s['grade']} {s['tf']} | entry {s['entry']} SL {s['stop']} TP {s['target']} (risk {s['risk']}, dist {s['dist_to_entry']})")
        else:
            print(f"  {side.upper():5s}: no active zone")
    print("\nJSON:", json.dumps(a))
