#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Test QM and Flag-Limit as ALTERNATIVE HTF (H1) zone sources, aligned to the M5
entry TF exactly like prep_symbol aligns the OB zones, then run the SAME evaluation
(first fresh touch -> resolve at 2R, with-trend, grade>=1, ATR%>=0.15, room>=2R when
an opposing zone exists). Compares each detector's standalone edge to the OB baseline.

Run: python3 qm_fl_test.py [M5]
"""
import os, sys
import numpy as np, pandas as pd
import rtm_bt as B, rtm_concepts as RC

ETF = sys.argv[1] if len(sys.argv) > 1 else "M5"
L, IMP = 5, 1.0


def syms():
    out = []
    import glob
    for p in sorted(glob.glob(os.path.expanduser(f"~/Downloads/*_{ETF}.csv"))):
        s = os.path.basename(p)[:-(len(ETF)+5)]
        if not s: continue
        if all(os.path.exists(os.path.expanduser(f"~/Downloads/{s}_{tf}.csv")) for tf in ("H1", "H4", "M15")):
            out.append((s, "london" if s == "XAUUSD" else "ny"))
    return out


def htf_zones_aligned(sym, idx, which):
    """Compute demand/supply zone arrays on H1 with the chosen detector, align to idx."""
    h1 = B.load(sym, "H1")
    oo, hh, ll, cc = (h1[x].values.astype(float) for x in ["Open", "High", "Low", "Close"])
    aa = B.atr_rma(hh, ll, cc, 14)
    if which == "OB":
        z = B.zone_engine(oo, hh, ll, cc, L, IMP, aa)
    elif which == "QM":
        z = RC.qm_zones(oo, hh, ll, cc, L, IMP, aa)
    elif which == "FL":
        z = RC.flag_limit_zones(oo, hh, ll, cc, L, IMP, aa)
    z.index = h1.index
    ct = h1.index + pd.Timedelta(minutes=60)
    a = B.asof_align(idx, z, ct)
    return a


def evaluate(D, sym, sess, which, minGrade=1, comm=0.00015, minStop=2.5):
    n = len(D["c"]); o, h, l, c, atr = D["o"], D["h"], D["l"], D["c"], D["atr"]
    b1, b2, b3 = D["b1"], D["b2"], D["b3"]
    bias = np.sign(1*b1 + 2*b2 + 2*b3).astype(int)
    insess = B.london_session(D["time"]) if sess == "london" else B.ny_session(D["time"])
    atrpct = np.where(c > 0, atr/c*100.0, 0.0)
    gold = sym == "XAUUSD"; ms = 2.5 if gold else 1.0; buf = 0.3
    z = htf_zones_aligned(sym, D["time"], which)
    dT, dB, dG = z["demT"].values, z["demB"].values, z["demG"].values
    sT, sB, sG = z["supT"].values, z["supB"].values, z["supG"].values
    zones = [("dem", 1, dT, dB, dG), ("sup", -1, sT, sB, sG)]
    out = []
    for _, dr, ZT, ZB, ZG in zones:
        prev = np.nan; tested = False
        for i in range(n):
            zt, zb = ZT[i], ZB[i]
            if not np.isnan(zb) and (np.isnan(prev) or zb != prev): tested = False; prev = zb
            if tested or np.isnan(zb) or np.isnan(atr[i]): continue
            touch = (l[i] <= zt and h[i] >= zb) if dr == 1 else (h[i] >= zb and l[i] <= zt)
            if not touch: continue
            tested = True
            if not insess[i] or atrpct[i] < 0.15: continue
            if ZG[i] < minGrade: continue
            wt = (dr == bias[i])
            if not (wt or bias[i] == 0): continue
            e = c[i]
            if dr == 1: sl = min(zb - buf*atr[i], e - ms*atr[i]); risk = e - sl
            else:       sl = max(zt + buf*atr[i], e + ms*atr[i]); risk = sl - e
            if risk <= 0: continue
            # opposing zone (same detector) for room gate
            if dr == 1:
                opp = sB[i] if (not np.isnan(sB[i]) and sB[i] > e) else np.nan
            else:
                opp = dT[i] if (not np.isnan(dT[i]) and dT[i] < e) else np.nan
            kk = ((opp - e)/risk) if (dr == 1 and not np.isnan(opp)) else (((e - opp)/risk) if (dr == -1 and not np.isnan(opp)) else np.nan)
            if (not np.isnan(kk)) and kk < 2.0: continue
            tp = e + dr*2.0*risk; res = None
            for j in range(i+1, min(i+1+4000, n)):
                if dr == 1:
                    if l[j] <= sl: res = -1.0; break
                    if h[j] >= tp: res = 2.0; break
                else:
                    if h[j] >= sl: res = -1.0; break
                    if l[j] <= tp: res = 2.0; break
            if res is None: continue
            out.append((res, 0 if i < n//2 else 1))
    return out


def stat(rows):
    R = np.asarray([r for r, _ in rows], float)
    if len(R) == 0: return "  (0 trades)"
    w = R[R > 0]; lo = R[R <= 0]
    pf = w.sum()/(-lo.sum()) if lo.sum() < 0 else float("inf")
    return (f"n={len(R):4d}  stop={100*(R<0).mean():5.1f}%  WR={100*(R>0).mean():5.1f}%  "
            f"expR={R.mean():+.3f}  PF={'inf' if pf==float('inf') else f'{pf:.2f}'}  netR={R.sum():+.1f}")


def oos(rows):
    def e(hv):
        R = np.asarray([r for r, h in rows if h == hv], float)
        if len(R) == 0: return "n/a"
        w = R[R > 0]; lo = R[R <= 0]
        pf = w.sum()/(-lo.sum()) if lo.sum() < 0 else float("inf")
        return f"{R.mean():+.2f}/{'inf' if pf==float('inf') else f'{pf:.2f}'}"
    return f"h0={e(0)}  h1={e(1)}"


SS = syms()
print(f"prepping {len(SS)} symbols ({ETF})...", flush=True)
prepped = {}
for s, sess in SS:
    try: prepped[s] = (B.prep_symbol(s, ETF), sess)
    except Exception as e: print(f"  {s} err {str(e)[:30]}")
for mg in (1, 2):
    print(f"\n=== minGrade={mg} ===")
    for which in ["OB", "QM", "FL"]:
        rows = []
        for s, (D, sess) in prepped.items():
            try: rows += evaluate(D, s, sess, which, minGrade=mg)
            except Exception as e: print(f"  {which} {s} err {str(e)[:40]}")
        print(f"{which:3s}  {stat(rows)}   OOS[{oos(rows)}]")
