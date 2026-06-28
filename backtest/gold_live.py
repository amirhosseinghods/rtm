#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Live analysis snapshot for gold: current price, bias, premium/discount, active zones
(1h & 15m demand/supply with FTR grades), no-sharp H4, and any A-grade setup w/ entry/SL/TP."""
import rtm_bt as B
import numpy as np
s="XAUUSD"
D=B.prep_symbol(s,"M5")
i=len(D["c"])-1
c=D["c"]; t=D["time"]
b1,b2,b3=D["b1"][i],D["b2"][i],D["b3"][i]
bias=int(np.sign(1*b1+2*b2+2*b3))
pH,pL=D["pH"][i],D["pL"][i]; mid=pL+0.5*(pH-pL) if pH==pH and pL==pL else np.nan
price=c[i]
print(f"=== GOLD (XAUUSD) live snapshot — data through {t[i]} ===")
print(f"current price (last M5 close): {price:.2f}")
print(f"bias: H1={int(b1):+d} H4={int(b2):+d} D={int(b3):+d}  => NET {'LONG' if bias>0 else 'SHORT' if bias<0 else 'NEUTRAL'}")
if mid==mid:
    zone = "DISCOUNT (lower half - longs favored)" if price<mid else "PREMIUM (upper half - shorts favored)"
    print(f"range: swingLow={pL:.2f}  mid(0.5)={mid:.2f}  swingHigh={pH:.2f}  -> price in {zone}")
print(f"\n--- ACTIVE ZONES at current bar ---")
def g(name): return D[name][i]
print(f"1h DEMAND : {g('c_demB'):.2f} - {g('c_demT'):.2f}  grade={int(g('c_demG'))}   (dist {price-g('c_demT'):+.2f} to top)")
print(f"15m DEMAND: {g('a_demB'):.2f} - {g('a_demT'):.2f}  grade={int(g('a_demG'))}")
print(f"1h SUPPLY : {g('c_supB'):.2f} - {g('c_supT'):.2f}  grade={int(g('c_supG'))}   (dist {g('c_supB')-price:+.2f} to bottom)")
print(f"15m SUPPLY: {g('a_supB'):.2f} - {g('a_supT'):.2f}  grade={int(g('a_supG'))}")
sv=D['sh4_vel'][i]; sg=D['sh4_sgn'][i]
print(f"\nH4 approach sharpness: vel={sv:.2f} dir={'up' if sg>0 else 'down' if sg<0 else 'flat'} (no-sharp gate skips if vel>=0.3 INTO a zone)")
# recent A-grade setups
cfg=dict(B.DEF, session="london", noSharpHTF=True, sharpVelH4=0.3, minScore=100,
         minATRpct=0.15, minGrade=2, zoneBuf=0.3, minStop=2.5, rr=1.5, roundTrip=True)
sig=B.scan_signals(D,cfg,last_n=20*288)  # last ~20 days
print(f"\n--- A-GRADE setups in last ~20 days: {len(sig)} ---")
if len(sig):
    print(sig[["time","dir","type","grade","score","entry","sl","tp"]].tail(6).to_string(index=False))
