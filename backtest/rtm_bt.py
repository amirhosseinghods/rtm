#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
RTM-SMC v1.1 backtester on real CSV data (offline, unlimited sample).
Faithfully ports the Pine logic: pivots -> structure -> zones (FTR-grade) ->
HTF bias -> entry (FTR/no-sharp/score/discount-premium/round-trip/session) -> 1.5R exit.
Data: /Users/amirhosseinghods/Downloads/{SYM}_{TF}.csv  (tab-sep: Time O H L C Dur Vol)
"""
import os, sys, json
import numpy as np
import pandas as pd
from numpy.lib.stride_tricks import sliding_window_view

# Data dir is env-overridable so the live web app can point load() at freshly
# fetched CSVs (web/data/) instead of the manual Downloads exports, without
# changing any backtest behaviour (default stays Downloads).
DL = os.environ.get("RTM_DATA_DIR", "/Users/amirhosseinghods/Downloads")
OUT = "/Users/amirhosseinghods/Desktop/trade/backtest"
TF_MIN = {"M1":1, "M5":5, "M15":15, "H1":60, "H4":240}

_cache = {}
def clear_cache(sym=None):
    """Drop cached frames so the next load() re-reads from disk (live refresh)."""
    if sym is None:
        _cache.clear()
    else:
        for k in [k for k in _cache if k[0] == sym]:
            del _cache[k]

def load(sym, tf):
    k = (sym, tf)
    if k in _cache: return _cache[k]
    base = os.environ.get("RTM_DATA_DIR", DL)   # resolved per-call: import order safe
    p = f"{base}/{sym}_{tf}.csv"
    df = pd.read_csv(p, sep="\t", skiprows=1,
                     names=["Time","Open","High","Low","Close","Dur","Vol"],
                     usecols=[0,1,2,3,4])
    df["Time"] = pd.to_datetime(df["Time"])
    df = df.dropna().drop_duplicates("Time").set_index("Time").sort_index()
    _cache[k] = df
    return df

def resample(df, rule):
    a = df.resample(rule).agg({"Open":"first","High":"max","Low":"min","Close":"last"}).dropna()
    return a

def atr_rma(high, low, close, n=14):
    pc = np.empty_like(close); pc[0] = close[0]; pc[1:] = close[:-1]
    tr = np.maximum(high - low, np.maximum(np.abs(high - pc), np.abs(low - pc)))
    a = np.full_like(tr, np.nan)
    if len(tr) >= n:
        a[n-1] = tr[:n].mean()
        for i in range(n, len(tr)):
            a[i] = (a[i-1]*(n-1) + tr[i]) / n
    return a

def htf_sharp(df, K=6):
    """Sharpness of the approach leg on a HIGHER timeframe (user's real definition):
    net displacement per bar in ATRs (velocity) * straightness (efficiency), signed.
    vel = |close[t]-close[t-K]| / (K*ATR)   -> ATRs of NET travel per bar (steepness)
    eff = |net move| / path length          -> 1=straight/impulsive, ~0=choppy/grind
    sgn = direction of the leg (+1 up, -1 down). Sharp INTO a demand = sgn<0."""
    cc = df["Close"].values.astype(float)
    hh = df["High"].values.astype(float); ll = df["Low"].values.astype(float)
    aa = atr_rma(hh, ll, cc, 14)
    n = len(cc)
    vel = np.full(n, np.nan); eff = np.full(n, np.nan); sgn = np.zeros(n)
    if n > K:
        ad = np.abs(np.diff(cc))                       # per-bar abs moves (n-1)
        cs = np.concatenate([[0.0], np.cumsum(ad)])    # cs[t] = sum ad[:t]
        d = cc[K:] - cc[:-K]                           # net move over last K bars
        path = cs[K:] - cs[:-K]                        # total path length
        aaK = aa[K:]
        with np.errstate(invalid="ignore", divide="ignore"):
            vel[K:] = np.where(aaK > 0, np.abs(d)/(K*aaK), np.nan)
            eff[K:] = np.where(path > 0, np.abs(d)/path, 0.0)
        sgn[K:] = np.sign(d)
    return pd.DataFrame({"vel": vel, "eff": eff, "sgn": sgn}, index=df.index)

def pivots(high, low, L, R):
    n = len(high); ph = np.full(n, np.nan); pl = np.full(n, np.nan)
    w = L + R + 1
    if n < w: return ph, pl
    hw = sliding_window_view(high, w); lw = sliding_window_view(low, w)
    ctrH = hw[:, L]; ctrL = lw[:, L]
    isph = (ctrH > hw[:, :L].max(1)) & (ctrH > hw[:, L+1:].max(1))
    ispl = (ctrL < lw[:, :L].min(1)) & (ctrL < lw[:, L+1:].min(1))
    base = np.arange(len(hw))
    conf = base + (w - 1)        # confirm bar index
    cen  = base + L              # pivot bar index
    ph[conf[isph]] = high[cen[isph]]
    pl[conf[ispl]] = low[cen[ispl]]
    return ph, pl

def struct_engine(o, h, l, c, L):
    n = len(c); ph, pl = pivots(h, l, L, L)
    tr = np.zeros(n, int); pH = np.full(n, np.nan); pL = np.full(n, np.nan)
    bu = np.zeros(n, bool); bd = np.zeros(n, bool); cu = np.zeros(n, bool); cd = np.zeros(n, bool)
    cpH = np.nan; cpL = np.nan; ctr = 0; pHx = False; pLx = False; legLow = np.nan
    for i in range(n):
        if np.isnan(legLow) or l[i] < legLow: legLow = l[i]
        if not np.isnan(ph[i]): cpH = ph[i]; pHx = False
        if not np.isnan(pl[i]) and ctr != 1: cpL = pl[i]; pLx = False
        if (not np.isnan(cpH)) and c[i] > cpH and not pHx:
            if ctr == -1: cu[i] = True
            else: bu[i] = True
            ctr = 1; pHx = True; pLx = False
            if not np.isnan(legLow): cpL = legLow
            legLow = l[i]
        if (not np.isnan(cpL)) and c[i] < cpL and not pLx:
            if ctr == 1: cd[i] = True
            else: bd[i] = True
            ctr = -1; pLx = True; pHx = False
        tr[i] = ctr; pH[i] = cpH; pL[i] = cpL
    return tr, pH, pL, bu, bd, cu, cd

def zone_engine(o, h, l, c, L, impulse, atr):
    tr, pH, pL, bu, bd, cu, cd = struct_engine(o, h, l, c, L)
    n = len(c)
    DemT = np.full(n, np.nan); DemB = np.full(n, np.nan); DemG = np.zeros(n)
    SupT = np.full(n, np.nan); SupB = np.full(n, np.nan); SupG = np.zeros(n)
    lbT = lbB = luT = luB = np.nan
    cdT = cdB = csT = csB = np.nan; cdG = csG = 0.0
    for i in range(n):
        bF = (i >= 2) and (l[i] > h[i-2])
        sF = (i >= 2) and (h[i] < l[i-2])
        if c[i] < o[i]: lbT = max(o[i], c[i]); lbB = l[i]
        if c[i] > o[i]: luT = h[i]; luB = min(o[i], c[i])
        if (bu[i] or cu[i]) and not np.isnan(lbB):
            cdT = lbT; cdB = lbB
            cdG = (1.0 if bF else 0.0) + (1.0 if (h[i]-lbB) >= impulse*atr[i] else 0.0)
        if (bd[i] or cd[i]) and not np.isnan(luT):
            csT = luT; csB = luB
            csG = (1.0 if sF else 0.0) + (1.0 if (luT-l[i]) >= impulse*atr[i] else 0.0)
        DemT[i]=cdT; DemB[i]=cdB; DemG[i]=cdG; SupT[i]=csT; SupB[i]=csB; SupG[i]=csG
    return pd.DataFrame({"demT":DemT,"demB":DemB,"demG":DemG,"supT":SupT,"supB":SupB,"supG":SupG})

def trend_engine(o, h, l, c, L):
    tr, *_ = struct_engine(o, h, l, c, L)
    return tr

def asof_align(target_index, src_df, src_index_closetime):
    """request.security(lookahead_off): value valid from HTF bar CLOSE time."""
    s = src_df.copy()
    s["_ct"] = pd.DatetimeIndex(src_index_closetime)
    s = s.sort_values("_ct").reset_index(drop=True)
    tgt = pd.DataFrame({"_t": pd.DatetimeIndex(target_index)}).sort_values("_t")
    m = pd.merge_asof(tgt, s, left_on="_t", right_on="_ct", direction="backward")
    return m.set_index("_t")

def prep_symbol(sym, entry_tf, L=5, impulse=1.0):
    """Build entry-TF arrays with aligned HTF zones (15m,1h) + bias (1h,4h,D)."""
    edf = load(sym, entry_tf)
    e = edf.copy()
    o,h,l,c = (e[x].values.astype(float) for x in ["Open","High","Low","Close"])
    atr_e = atr_rma(h,l,c,14)
    # premium/discount from entry-TF structure
    tr,pH,pL,*_ = struct_engine(o,h,l,c,L)
    # entry-TF self-zones (for MTF refinement: the small LTF OB inside the HTF zone)
    ez = zone_engine(o,h,l,c,L,impulse,atr_e)
    # zones from M15 and H1
    m15 = load(sym, "M15"); h1 = load(sym, "H1"); h4 = load(sym, "H4")
    def zeng(df, tf):
        oo,hh,ll,cc = (df[x].values.astype(float) for x in ["Open","High","Low","Close"])
        aa = atr_rma(hh,ll,cc,14)
        z = zone_engine(oo,hh,ll,cc,L,impulse,aa)
        z.index = df.index
        ct = df.index + pd.Timedelta(minutes=TF_MIN[tf])
        return z, ct
    z15,ct15 = zeng(m15,"M15"); zh1,cth1 = zeng(h1,"H1")
    a = asof_align(e.index, z15, ct15)      # 15m zones -> a_*
    cc = asof_align(e.index, zh1, cth1)     # 1h zones  -> c_*
    # bias: trends on H1, H4, D
    def teng(df, tf):
        oo,hh,ll,cc2 = (df[x].values.astype(float) for x in ["Open","High","Low","Close"])
        t = trend_engine(oo,hh,ll,cc2,L)
        s = pd.DataFrame({"tr":t}, index=df.index)
        ct = df.index + pd.Timedelta(minutes=TF_MIN[tf])
        return s, ct
    dD = resample(load(sym,"H1"), "1D")
    def teng_d(df):
        oo,hh,ll,cc2 = (df[x].values.astype(float) for x in ["Open","High","Low","Close"])
        t = trend_engine(oo,hh,ll,cc2,L)
        s = pd.DataFrame({"tr":t}, index=df.index)
        ct = df.index + pd.Timedelta(days=1)
        return s, ct
    sh1,c1 = teng(h1,"H1"); sh4,c4 = teng(h4,"H4"); sd,cd = teng_d(dD)
    b1 = asof_align(e.index, sh1, c1)["tr"].fillna(0).values
    b2 = asof_align(e.index, sh4, c4)["tr"].fillna(0).values
    b3 = asof_align(e.index, sd, cd)["tr"].fillna(0).values
    # HTF approach-sharpness (the CORRECT no-sharp: steepness of the leg INTO the zone
    # measured on H1 & H4, not on the entry-TF touch candle)
    sp1 = htf_sharp(h1, K=6); sp4 = htf_sharp(h4, K=6)
    a1 = asof_align(e.index, sp1, cth1)
    a4 = asof_align(e.index, sp4, h4.index + pd.Timedelta(minutes=TF_MIN["H4"]))
    out = dict(time=e.index, o=o,h=h,l=l,c=c, atr=atr_e, pH=pH, pL=pL,
               sh1_vel=a1["vel"].values, sh1_eff=a1["eff"].values, sh1_sgn=a1["sgn"].values,
               sh4_vel=a4["vel"].values, sh4_eff=a4["eff"].values, sh4_sgn=a4["sgn"].values,
               a_demT=a["demT"].values, a_demB=a["demB"].values, a_demG=a["demG"].values,
               a_supT=a["supT"].values, a_supB=a["supB"].values, a_supG=a["supG"].values,
               c_demT=cc["demT"].values, c_demB=cc["demB"].values, c_demG=cc["demG"].values,
               c_supT=cc["supT"].values, c_supB=cc["supB"].values, c_supG=cc["supG"].values,
               e_demT=ez["demT"].values, e_demB=ez["demB"].values, e_demG=ez["demG"].values,
               e_supT=ez["supT"].values, e_supB=ez["supB"].values, e_supG=ez["supG"].values,
               b1=b1, b2=b2, b3=b3)
    return out

def ny_session(idx, start_h=8, end_h=17, tz="America/New_York"):
    t = idx.tz_localize("UTC").tz_convert(tz)
    hh = np.asarray(t.hour) + np.asarray(t.minute)/60.0
    wd = np.asarray(t.weekday) < 5
    return (hh >= start_h) & (hh < end_h) & wd

def london_session(idx):  # 03:00-12:00 NY ~ 08:00-17:00 London
    return ny_session(idx, 3, 12)

def backtest(D, p):
    """D = prep dict; p = params. Returns (trades_df, stats)."""
    n = len(D["c"]); o,h,l,c,atr = D["o"],D["h"],D["l"],D["c"],D["atr"]
    pH,pL = D["pH"],D["pL"]
    b1,b2,b3 = D["b1"],D["b2"],D["b3"]
    bias = np.sign(p["wB1"]*b1 + p["wB2"]*b2 + p["wB3"]*b3).astype(int)
    # premium/discount
    mid = pL + 0.5*(pH-pL)
    inDisc = (~np.isnan(pH)) & (~np.isnan(pL)) & (pH>pL) & (c < mid)
    inPrem = (~np.isnan(pH)) & (~np.isnan(pL)) & (pH>pL) & (c > mid)
    # session
    if p["session"]=="ny": insess = ny_session(D["time"])
    elif p["session"]=="london": insess = london_session(D["time"])
    else: insess = np.ones(n, bool)
    nyh = D.get("nyhour")
    if nyh is None:
        _t = D["time"].tz_localize("UTC").tz_convert("America/New_York")
        nyh = np.asarray(_t.hour); D["nyhour"] = nyh
    bad = set(p.get("bad_hours", ()))
    atrpct_arr = np.where(c>0, atr/c*100.0, 0.0)
    minATRpct = p.get("minATRpct", 0.0)
    # TF-independent volatility gate: ATR relative to its own rolling median
    minATRrel = p.get("minATRrel", 0.0)
    if minATRrel > 0:
        med = pd.Series(atr).rolling(500, min_periods=50).median().values
        relok = ~(med>0) | (atr >= minATRrel*med)   # pass if no median yet, else atr>=k*median
    else:
        relok = np.ones(n, bool)
    # no-sharp (OLD entry-TF proxy, kept for back-compat)
    rng = h-l; prng = np.empty(n); prng[0]=rng[0]; prng[1:]=rng[:-1]
    sharp_big = np.maximum(rng, prng) >= p["sharpATR"]*atr
    # no-sharp (CORRECT: steepness of the leg INTO the zone, on H4 — the user's definition)
    sh4v = D.get("sh4_vel"); sh4s = D.get("sh4_sgn")
    noSharpHTF = p.get("noSharpHTF", False); sharpVelH4 = p.get("sharpVelH4", 0.3)
    if noSharpHTF and sh4v is not None:
        sin_long  = np.where((sh4s==-1) & ~np.isnan(sh4v), sh4v, 0.0)  # leg falling into demand
        sin_short = np.where((sh4s== 1) & ~np.isnan(sh4v), sh4v, 0.0)  # leg rising into supply
        htfOKlong = sin_long < sharpVelH4; htfOKshort = sin_short < sharpVelH4
    else:
        htfOKlong = np.ones(n, bool); htfOKshort = np.ones(n, bool)
    aD,aDb,aDg = D["a_demT"],D["a_demB"],D["a_demG"]
    aS,aSt,aSg = D["a_supB"],D["a_supT"],D["a_supG"]  # note naming
    cD,cDb,cDg = D["c_demT"],D["c_demB"],D["c_demG"]
    cS,cSt,cSg = D["c_supB"],D["c_supT"],D["c_supG"]
    minScore=p["minScore"]; minGrade=p["minGrade"]; rr=p["rr"]; risk_pct=p["riskPct"]
    buf=p["zoneBuf"]; minStop=p["minStop"]; comm=p["comm"]; noSharp=p["noSharp"]; rt=p["roundTrip"]
    # M1 confirmation trigger (user's method: tap zone -> wait for sweep+reclaim, stop behind M1 swing)
    useTrig=p.get("useTrigger",False); armBars=p.get("armBars",12); invalidATR=p.get("invalidATR",1.0)
    armed=None

    def score(grade, ztf, dr):
        s = 50
        s += 20 if grade>=2 else (10 if grade>=1 else 0)
        s += 20 if ztf>=60 else 8
        s += 10 if ((dr==1 and inDisc[i]) or (dr==-1 and inPrem[i])) else 0
        return s

    pos=0; jE=jSL=jTP=jR=0.0; jDir=0; jType=""; jGrade=0; jWT=False; jEntryI=0
    lastTP=False; modelBal=1000.0; bal=1000.0
    aDemPrev=cDemPrev=aSupPrev=cSupPrev=np.nan
    aDemTested=cDemTested=aSupTested=cSupTested=False
    trades=[]
    for i in range(n):
        # reset tested when zone bottom changes
        if not np.isnan(aDb[i]) and (np.isnan(aDemPrev) or aDb[i]!=aDemPrev): aDemTested=False; aDemPrev=aDb[i]
        if not np.isnan(cDb[i]) and (np.isnan(cDemPrev) or cDb[i]!=cDemPrev): cDemTested=False; cDemPrev=cDb[i]
        if not np.isnan(aSt[i]) and (np.isnan(aSupPrev) or aSt[i]!=aSupPrev): aSupTested=False; aSupPrev=aSt[i]
        if not np.isnan(cSt[i]) and (np.isnan(cSupPrev) or cSt[i]!=cSupPrev): cSupTested=False; cSupPrev=cSt[i]
        # ---- exit check (in position) ----
        if pos!=0:
            hit=None; xp=None
            if pos==1:
                if l[i] <= jSL: hit="SL"; xp=min(o[i], jSL) if o[i]<jSL else jSL
                elif h[i] >= jTP: hit="TP"; xp=max(o[i], jTP) if o[i]>jTP else jTP
            else:
                if h[i] >= jSL: hit="SL"; xp=max(o[i], jSL) if o[i]>jSL else jSL
                elif l[i] <= jTP: hit="TP"; xp=min(o[i], jTP) if o[i]<jTP else jTP
            if hit:
                pnl_price = (xp - jE)*jDir - comm*(jE+xp)
                rmult = pnl_price / jR
                pnlD = rmult * (modelBal*risk_pct/100.0)
                modelBal += pnlD
                lastTP = rmult > 0.3
                trades.append(dict(i=jEntryI, exit_i=i, dir=jDir, type=jType, grade=jGrade,
                    withtrend=jWT, entry=jE, sl=jSL, tp=jTP, exit=xp, reason=hit,
                    R=rmult, bal=modelBal, hour_ny=int(D["time"][jEntryI].tz_localize("UTC").tz_convert("America/New_York").hour)))
                pos=0
        # ---- M1 trigger: a setup is armed, wait for sweep+reclaim confirmation ----
        if pos==0 and useTrig and armed is not None:
            dr=armed['dr']
            if dr==1:
                if l[i] < armed['swExt']: armed['swExt']=l[i]; armed['swRef']=h[i]
                triggered = (c[i] > armed['swRef']) and (c[i] > o[i])           # reclaim + bullish close
                invalid = (i-armed['bar0']>armBars) or (c[i] < armed['zedge']-invalidATR*atr[i])
            else:
                if h[i] > armed['swExt']: armed['swExt']=h[i]; armed['swRef']=l[i]
                triggered = (c[i] < armed['swRef']) and (c[i] < o[i])           # reclaim + bearish close
                invalid = (i-armed['bar0']>armBars) or (c[i] > armed['zedge']+invalidATR*atr[i])
            if triggered:
                # stop = OUTSIDE the zone AND behind the M1 swing, + breathing-room buffer (user's rule)
                if dr==1:
                    sl=min(armed['swExt'], armed['zedge'])-buf*atr[i]; sl=min(sl, c[i]-minStop*atr[i]); risk=c[i]-sl
                else:
                    sl=max(armed['swExt'], armed['zedge'])+buf*atr[i]; sl=max(sl, c[i]+minStop*atr[i]); risk=sl-c[i]
                if risk>0:
                    jDir=dr; jType=armed['ty']; jGrade=armed['g']; jE=c[i]; jSL=sl; jR=risk
                    jTP=jE+dr*rr*risk; jWT=armed['wt']; jEntryI=i; pos=dr
                armed=None
            elif invalid:
                armed=None
        # ---- entry check (flat, not already armed) ----
        if pos==0 and (not useTrig or armed is None):
            counterOK = (rt and lastTP)
            canL = (bias[i]==1) or counterOK
            canS = (bias[i]==-1) or counterOK
            sharpOK = (not noSharp) or (not sharp_big[i])
            if insess[i] and (not bad or nyh[i] not in bad) and (minATRpct<=0 or atrpct_arr[i]>=minATRpct) and relok[i]:
                cands=[]
                # c_dem (1h long), a_dem (15m long), c_sup (1h short), a_sup (15m short)
                if canL and htfOKlong[i] and not cDemTested and not np.isnan(cDb[i]) and l[i]<=cD[i] and h[i]>=cDb[i]:
                    cDemTested=True
                    g = cDg[i] if sharpOK else -1.0
                    if g>=minGrade and score(g,60,1)>=minScore:
                        sl=min(cDb[i]-buf*atr[i], c[i]-minStop*atr[i]); risk=c[i]-sl
                        if risk>0: cands.append((1,"OB-1h",g,sl,risk,cDb[i]))
                if not cands and canL and htfOKlong[i] and not aDemTested and not np.isnan(aDb[i]) and l[i]<=aD[i] and h[i]>=aDb[i]:
                    aDemTested=True
                    g = aDg[i] if sharpOK else -1.0
                    if g>=minGrade and score(g,15,1)>=minScore:
                        sl=min(aDb[i]-buf*atr[i], c[i]-minStop*atr[i]); risk=c[i]-sl
                        if risk>0: cands.append((1,"OB-15m",g,sl,risk,aDb[i]))
                if not cands and canS and htfOKshort[i] and not cSupTested and not np.isnan(cSt[i]) and h[i]>=cS[i] and l[i]<=cSt[i]:
                    cSupTested=True
                    g = cSg[i] if sharpOK else -1.0
                    if g>=minGrade and score(g,60,-1)>=minScore:
                        sl=max(cSt[i]+buf*atr[i], c[i]+minStop*atr[i]); risk=sl-c[i]
                        if risk>0: cands.append((-1,"OB-1h",g,sl,risk,cSt[i]))
                if not cands and canS and htfOKshort[i] and not aSupTested and not np.isnan(aSt[i]) and h[i]>=aS[i] and l[i]<=aSt[i]:
                    aSupTested=True
                    g = aSg[i] if sharpOK else -1.0
                    if g>=minGrade and score(g,15,-1)>=minScore:
                        sl=max(aSt[i]+buf*atr[i], c[i]+minStop*atr[i]); risk=sl-c[i]
                        if risk>0: cands.append((-1,"OB-15m",g,sl,risk,aSt[i]))
                if cands:
                    dr,ty,g,sl,risk,zedge = cands[0]
                    if useTrig:   # arm and wait for the M1 confirmation trigger
                        armed=dict(dr=dr,ty=ty,g=int(g),zedge=zedge,bar0=i,wt=(dr==bias[i]),
                                   swExt=(l[i] if dr==1 else h[i]), swRef=(h[i] if dr==1 else l[i]))
                    else:         # immediate entry on touch (M5 path)
                        jDir=dr; jType=ty; jGrade=int(g); jE=c[i]; jSL=sl; jR=risk
                        jTP = jE + dr*rr*risk; jWT = (dr==bias[i]); jEntryI=i; pos=dr
    # stats
    tr = pd.DataFrame(trades)
    if len(tr)==0:
        return tr, dict(n=0)
    wins = tr[tr.R>0]; losses = tr[tr.R<=0]
    pf = wins.R.sum() / (-losses.R.sum()) if len(losses) and losses.R.sum()<0 else np.nan
    st = dict(n=len(tr), wr=round(100*len(wins)/len(tr),1), pf=round(pf,2) if pf==pf else None,
              netR=round(tr.R.sum(),1), exp=round(tr.R.mean(),3),
              bal=round(modelBal,1), avgWin=round(wins.R.mean(),2) if len(wins) else 0,
              avgLoss=round(losses.R.mean(),2) if len(losses) else 0,
              maxDD=round(dd(tr.R.values),1))
    return tr, st

def dd(R):
    eq = np.cumsum(R); peak = np.maximum.accumulate(eq); return (eq-peak).min()

def score_val(g, ztf, dr, disc, prem):
    s = 50; s += 20 if g>=2 else (10 if g>=1 else 0); s += 20 if ztf>=60 else 8
    s += 10 if ((dr==1 and disc) or (dr==-1 and prem)) else 0
    return s

def research_scan(D, rr=1.5, minStop=1.0, buf=0.3, comm=0.00015, maxbars=3000):
    """Record EVERY fresh zone-touch as an independent labeled trade (no filters)
    -> thousands of samples for learning. Each row = decision features + outcome R."""
    n=len(D["c"]); o,h,l,c,atr=D["o"],D["h"],D["l"],D["c"],D["atr"]
    pH,pL=D["pH"],D["pL"]; b1,b2,b3=D["b1"],D["b2"],D["b3"]
    bias=np.sign(1*b1+2*b2+2*b3).astype(int)
    mid=pL+0.5*(pH-pL)
    inDisc=(~np.isnan(pH))&(~np.isnan(pL))&(pH>pL)&(c<mid)
    inPrem=(~np.isnan(pH))&(~np.isnan(pL))&(pH>pL)&(c>mid)
    rng=h-l; prng=np.empty(n); prng[0]=rng[0]; prng[1:]=rng[:-1]
    appr=np.maximum(rng,prng)/np.where(atr>0,atr,np.nan)
    sh1v=D["sh1_vel"]; sh1s=D["sh1_sgn"]; sh1e=D["sh1_eff"]
    sh4v=D["sh4_vel"]; sh4s=D["sh4_sgn"]
    t=D["time"].tz_localize("UTC").tz_convert("America/New_York"); nyh=np.asarray(t.hour)
    zones=[("OB-15m",1,D["a_demT"],D["a_demB"],D["a_demG"]),
           ("OB-15m",-1,D["a_supT"],D["a_supB"],D["a_supG"]),
           ("OB-1h",1,D["c_demT"],D["c_demB"],D["c_demG"]),
           ("OB-1h",-1,D["c_supT"],D["c_supB"],D["c_supG"])]
    rows=[]
    for name,dr,ZT,ZB,ZG in zones:
        prev=np.nan; tested=False
        for i in range(n):
            zb=ZB[i]; zt=ZT[i]
            if not np.isnan(zb) and (np.isnan(prev) or zb!=prev): tested=False; prev=zb
            if tested or np.isnan(zb) or np.isnan(atr[i]): continue
            touch = (l[i]<=zt and h[i]>=zb) if dr==1 else (h[i]>=zb and l[i]<=zt)
            if not touch: continue
            tested=True; e=c[i]
            if dr==1: sl=min(zb-buf*atr[i], e-minStop*atr[i]); risk=e-sl
            else:     sl=max(zt+buf*atr[i], e+minStop*atr[i]); risk=sl-e
            if risk<=0: continue
            tp=e+dr*rr*risk; res=None; xp=None; bars=0
            for j in range(i+1, min(i+1+maxbars,n)):
                bars=j-i
                if dr==1:
                    if l[j]<=sl: res="SL"; xp=(o[j] if o[j]<sl else sl); break
                    if h[j]>=tp: res="TP"; xp=(o[j] if o[j]>tp else tp); break
                else:
                    if h[j]>=sl: res="SL"; xp=(o[j] if o[j]>sl else sl); break
                    if l[j]<=tp: res="TP"; xp=(o[j] if o[j]<tp else tp); break
            if res is None: continue
            R=((xp-e)*dr - comm*(e+xp))/risk; g=ZG[i]
            ztf=60 if name=="OB-1h" else 15
            # HTF sharpness INTO the zone: leg pushing into zone has sgn == -dr
            hs1 = float(sh1v[i]) if (sh1s[i]==-dr and not np.isnan(sh1v[i])) else 0.0
            hs4 = float(sh4v[i]) if (sh4s[i]==-dr and not np.isnan(sh4v[i])) else 0.0
            he1 = float(sh1e[i]) if not np.isnan(sh1e[i]) else 0.0
            rows.append(dict(i=i,type=name,dr=dr,grade=int(g),
                score=score_val(g,ztf,dr,inDisc[i],inPrem[i]),
                withtrend=int(dr==bias[i]), biasUp=int(bias[i]==1),
                disc=int(inDisc[i]),prem=int(inPrem[i]),
                approach=round(float(appr[i]),2) if not np.isnan(appr[i]) else 99,
                hsharp1=round(hs1,2), heff1=round(he1,2), hsharp4=round(hs4,2),
                hour=int(nyh[i]), atrpct=round(float(atr[i]/c[i]*100),3),
                R=round(R,3),reason=res,bars=bars))
    return pd.DataFrame(rows)

def backtest_grid(D, p):
    """User's measured-move GRID exit on top of the validated FTR/grade2 entry.
    W = zone width. SL = far_edge -/+ 1W. TP1/2/3 = near_edge +/- 1W/2W/3W. Scale out 1/3 at
    each TP. After TP1 -> SL to breakeven. R measured vs initial risk |entry-SL0|."""
    n=len(D["c"]); o,h,l,c,atr=D["o"],D["h"],D["l"],D["c"],D["atr"]
    pH,pL=D["pH"],D["pL"]; b1,b2,b3=D["b1"],D["b2"],D["b3"]
    bias=np.sign(p["wB1"]*b1+p["wB2"]*b2+p["wB3"]*b3).astype(int)
    mid=pL+0.5*(pH-pL)
    inDisc=(~np.isnan(pH))&(~np.isnan(pL))&(pH>pL)&(c<mid)
    inPrem=(~np.isnan(pH))&(~np.isnan(pL))&(pH>pL)&(c>mid)
    if p["session"]=="ny": insess=ny_session(D["time"])
    elif p["session"]=="london": insess=london_session(D["time"])
    else: insess=np.ones(n,bool)
    atrpct=np.where(c>0,atr/c*100.0,0.0); minATRpct=p.get("minATRpct",0.0)
    sh4v=D.get("sh4_vel"); sh4s=D.get("sh4_sgn"); sv=p.get("sharpVelH4",0.3); nsharp=p.get("noSharpHTF",False)
    aD,aDb,aDg=D["a_demT"],D["a_demB"],D["a_demG"]; aS,aSt,aSg=D["a_supB"],D["a_supT"],D["a_supG"]
    cD,cDb,cDg=D["c_demT"],D["c_demB"],D["c_demG"]; cS,cSt,cSg=D["c_supB"],D["c_supT"],D["c_supG"]
    minScore=p["minScore"]; minGrade=p["minGrade"]; rt=p["roundTrip"]; comm=p["comm"]
    w13=p.get("gridW",(1/3,1/3,1/3)); beAfterTP1=p.get("beAfterTP1",True)
    def score(g,ztf,dr,i):
        s=50; s+=20 if g>=2 else (10 if g>=1 else 0); s+=20 if ztf>=60 else 8
        s+=10 if ((dr==1 and inDisc[i]) or (dr==-1 and inPrem[i])) else 0; return s
    def htfok(dr,i):
        if not nsharp or sh4v is None: return True
        if np.isnan(sh4v[i]): return True
        return not (sh4s[i]==-dr and sh4v[i]>=sv)
    pos=0; trades=[]; lastTP=False
    aDt=cDt=aSt2=cSt2=False; aDp=cDp=aSp=cSp=np.nan
    # position state
    e=zb=zt=W=SL0=0.0; near=0.0; tp=[0,0,0]; curSL=0.0; filled=0; remaining=1.0; realR=0.0; jdir=0; ji=0; jtype=""
    for i in range(n):
        if not np.isnan(aDb[i]) and (np.isnan(aDp) or aDb[i]!=aDp): aDt=False; aDp=aDb[i]
        if not np.isnan(cDb[i]) and (np.isnan(cDp) or cDb[i]!=cDp): cDt=False; cDp=cDb[i]
        if not np.isnan(aSt[i]) and (np.isnan(aSp) or aSt[i]!=aSp): aSt2=False; aSp=aSt[i]
        if not np.isnan(cSt[i]) and (np.isnan(cSp) or cSt[i]!=cSp): cSt2=False; cSp=cSt[i]
        if pos!=0:
            done=False
            if jdir==1:
                if l[i]<=curSL:
                    realR += remaining*((curSL-e) - comm*(e+curSL))/SL0; done=True
                else:
                    while filled<3 and h[i]>=tp[filled]:
                        realR += w13[filled]*((tp[filled]-e) - comm*(e+tp[filled]))/SL0
                        remaining-=w13[filled]; filled+=1
                        if filled==1 and beAfterTP1: curSL=e
                    if filled>=3: done=True
            else:
                if h[i]>=curSL:
                    realR += remaining*((e-curSL) - comm*(e+curSL))/SL0; done=True
                else:
                    while filled<3 and l[i]<=tp[filled]:
                        realR += w13[filled]*((e-tp[filled]) - comm*(e+tp[filled]))/SL0
                        remaining-=w13[filled]; filled+=1
                        if filled==1 and beAfterTP1: curSL=e
                    if filled>=3: done=True
            if done:
                lastTP = realR>0.1
                trades.append(dict(i=ji,exit_i=i,dir=jdir,type=jtype,R=round(realR,3),tps=filled))
                pos=0
        if pos==0:
            counterOK=(rt and lastTP); canL=(bias[i]==1) or counterOK; canS=(bias[i]==-1) or counterOK
            ok = insess[i] and (minATRpct<=0 or atrpct[i]>=minATRpct)
            cand=None
            if ok:
                if canL and htfok(1,i) and not cDt and not np.isnan(cDb[i]) and l[i]<=cD[i] and h[i]>=cDb[i]:
                    cDt=True
                    if cDg[i]>=minGrade and score(cDg[i],60,1,i)>=minScore: cand=(1,"OB-1h",cDb[i],cD[i])
                if cand is None and canL and htfok(1,i) and not aDt and not np.isnan(aDb[i]) and l[i]<=aD[i] and h[i]>=aDb[i]:
                    aDt=True
                    if aDg[i]>=minGrade and score(aDg[i],15,1,i)>=minScore: cand=(1,"OB-15m",aDb[i],aD[i])
                if cand is None and canS and htfok(-1,i) and not cSt2 and not np.isnan(cSt[i]) and h[i]>=cS[i] and l[i]<=cSt[i]:
                    cSt2=True
                    if cSg[i]>=minGrade and score(cSg[i],60,-1,i)>=minScore: cand=(-1,"OB-1h",cS[i],cSt[i])
                if cand is None and canS and htfok(-1,i) and not aSt2 and not np.isnan(aSt[i]) and h[i]>=aS[i] and l[i]<=aSt[i]:
                    aSt2=True
                    if aSg[i]>=minGrade and score(aSg[i],15,-1,i)>=minScore: cand=(-1,"OB-15m",aS[i],aSt[i])
            if cand is not None:
                dr,ty,zlo,zhi=cand; zlo0,zhi0=zlo,zhi  # keep HTF bounds for breathing-room stop
                # MTF refinement: replace the big HTF zone with the small entry-TF OB inside it
                if p.get("refineLTF",False):
                    rb,rt = (D["e_demB"][i],D["e_demT"][i]) if dr==1 else (D["e_supB"][i],D["e_supT"][i])
                    if (not np.isnan(rb)) and (rt-rb)>0 and (rt-rb)<(zhi-zlo) and rt>=zlo and rb<=zhi:
                        nlo,nhi = max(rb,zlo), min(rt,zhi)
                        if nhi-nlo>0: zlo,zhi = nlo,nhi
                W=zhi-zlo; Whtf=zhi0-zlo0
                if W<=0: continue
                jdir=dr; jtype=ty; ji=i
                tpMode=p.get("tpMode","W"); slMul=p.get("slBreathBT",1.0)
                slpad = (Whtf if p.get("slFromHTF",False) else W) * slMul   # breathing room
                if dr==1:
                    near=zhi
                    e = near if tpMode=="R" else ((zlo+zhi)/2.0 if p.get("gridEntryMid",False) else c[i])
                    stop0=(zlo0 if p.get("slFromHTF",False) else zlo)-slpad; SL0=e-stop0
                    tp=[e+SL0,e+2*SL0,e+3*SL0] if tpMode=="R" else [near+W,near+2*W,near+3*W]
                else:
                    near=zlo
                    e = near if tpMode=="R" else ((zlo+zhi)/2.0 if p.get("gridEntryMid",False) else c[i])
                    stop0=(zhi0 if p.get("slFromHTF",False) else zhi)+slpad; SL0=stop0-e
                    tp=[e-SL0,e-2*SL0,e-3*SL0] if tpMode=="R" else [near-W,near-2*W,near-3*W]
                if SL0<=0: continue
                curSL = stop0
                filled=0; remaining=1.0; realR=0.0; pos=dr
    tr=pd.DataFrame(trades)
    if len(tr)==0: return tr, dict(n=0)
    w=tr[tr.R>0]; lo=tr[tr.R<=0]
    pf=w.R.sum()/(-lo.R.sum()) if len(lo) and lo.R.sum()<0 else np.nan
    st=dict(n=len(tr),wr=round(100*len(w)/len(tr),1),pf=round(pf,2) if pf==pf else None,
            netR=round(tr.R.sum(),1),exp=round(tr.R.mean(),3),maxDD=round(dd(tr.R.values),1),
            avgTPs=round(tr.tps.mean(),2))
    return tr, st

def backtest_fib(D, p):
    """Fibonacci-OTE LADDER entry (user's method): with-trend only, ladder limit fills at
    0.5/0.618/0.786 retracement of the impulse leg (weights 0.25/0.5/0.25), ONE shared stop
    behind the swing extreme, ONE TP near the opposite extreme.
    R = Σ_filled w_i(exit-entry_i)·dir / Σ_all w_i(entry_i-stop)  (full-ladder risk budget,
    so partial fills risk less -> the ladder's risk-management is rewarded)."""
    n=len(D["c"]); o,h,l,c,atr=D["o"],D["h"],D["l"],D["c"],D["atr"]
    b1,b2,b3=D["b1"],D["b2"],D["b3"]
    bias=np.sign(p["wB1"]*b1+p["wB2"]*b2+p["wB3"]*b3).astype(int)
    Lp=5
    ph,pl=pivots(h,l,Lp,Lp)
    if p["session"]=="ny": insess=ny_session(D["time"])
    elif p["session"]=="london": insess=london_session(D["time"])
    else: insess=np.ones(n,bool)
    lv=p.get("fibLevels",(0.5,0.618,0.786)); wt=p.get("fibW",(0.25,0.5,0.25))
    buf=p["zoneBuf"]; tpBuf=p.get("tpBuf",0.1); maxWait=p.get("fibMaxWait",300); comm=p["comm"]
    requireZone=p.get("requireZone",False); minGrade=p.get("minGrade",2)
    tpMode=p.get("tpMode","high"); rr=p.get("rr",1.5)
    cdB,cdT,cdG=D["c_demB"],D["c_demT"],D["c_demG"]; adB,adT,adG=D["a_demB"],D["a_demT"],D["a_demG"]
    csB,csT,csG=D["c_supB"],D["c_supT"],D["c_supG"]; asB,asT,asG=D["a_supB"],D["a_supT"],D["a_supG"]
    atrpct=np.where(c>0,atr/c*100.0,0.0); minATRpct=p.get("minATRpct",0.0)
    sh4v=D.get("sh4_vel"); sh4s=D.get("sh4_sgn"); sv=p.get("sharpVelH4",0.3); nsharp=p.get("noSharpHTF",False)
    def in_zone(level,zb,zt,zg):
        return (not np.isnan(zb)) and zg>=minGrade and zb<=level<=zt
    def gp_on_zone(i,dr,lev):  # golden pocket (0.618/0.786) overlaps a valid grade>=minGrade zone?
        if dr==1:
            return any(in_zone(lev[k],cdB[i],cdT[i],cdG[i]) or in_zone(lev[k],adB[i],adT[i],adG[i]) for k in (1,2))
        return any(in_zone(lev[k],csB[i],csT[i],csG[i]) or in_zone(lev[k],asB[i],asT[i],asG[i]) for k in (1,2))
    lastPL=np.nan; lastPH=np.nan; armed=None; trades=[]
    def close_trade(armed,xp,reason,i):
        dr=armed['dr']; stop=armed['stop']
        denom=sum(wt[k]*abs(armed['lev'][k]-stop) for k in range(3))
        numer=sum(wt[k]*((xp-armed['lev'][k])*dr - comm*(armed['lev'][k]+xp)) for k in range(3) if armed['filled'][k])
        R=numer/denom if denom>0 else 0.0
        trades.append(dict(i=armed['bar0'],exit_i=i,dir=dr,fills=sum(armed['filled']),
            entry=round(armed['lev'][1],5),stop=round(stop,5),tp=round(armed['tp'],5),
            reason=reason,R=round(R,3),withtrend=1))
    for i in range(n):
        if not np.isnan(pl[i]): lastPL=pl[i]
        if not np.isnan(ph[i]): lastPH=ph[i]
        if armed is not None:
            dr=armed['dr']
            for k in range(3):
                if not armed['filled'][k]:
                    lev=armed['lev'][k]
                    if (dr==1 and l[i]<=lev) or (dr==-1 and h[i]>=lev): armed['filled'][k]=True
            anyFill=any(armed['filled'])
            if anyFill:
                stop=armed['stop']; tp=armed['tp']; hit=None; xp=None
                if dr==1:
                    if l[i]<=stop: hit="SL"; xp=min(o[i],stop) if o[i]<stop else stop
                    elif h[i]>=tp: hit="TP"; xp=max(o[i],tp) if o[i]>tp else tp
                else:
                    if h[i]>=stop: hit="SL"; xp=max(o[i],stop) if o[i]>stop else stop
                    elif l[i]<=tp: hit="TP"; xp=min(o[i],tp) if o[i]<tp else tp
                if hit: close_trade(armed,xp,hit,i); armed=None; continue
            if armed is not None and i-armed['bar0']>maxWait:
                if anyFill: close_trade(armed,c[i],"TIME",i)
                armed=None
        okv = (minATRpct<=0 or atrpct[i]>=minATRpct)
        if armed is None and insess[i] and okv and not np.isnan(lastPL) and not np.isnan(lastPH):
            if bias[i]==1 and not np.isnan(ph[i]) and lastPH>lastPL:
                rng=lastPH-lastPL; lev=[lastPH-lv[k]*rng for k in range(3)]
                sharpOK = (not nsharp) or sh4v is None or np.isnan(sh4v[i]) or not (sh4s[i]==-1 and sh4v[i]>=sv)
                if c[i]>lev[0] and sharpOK and (not requireZone or gp_on_zone(i,1,lev)):
                    stop=lastPL-buf*atr[i]
                    tp=(lev[1]+rr*(lev[1]-stop)) if tpMode=="rr" else (lastPH-tpBuf*atr[i])
                    armed=dict(dr=1,lev=lev,stop=stop,tp=tp,filled=[False,False,False],bar0=i)
            elif bias[i]==-1 and not np.isnan(pl[i]) and lastPH>lastPL:
                rng=lastPH-lastPL; lev=[lastPL+lv[k]*rng for k in range(3)]
                sharpOK = (not nsharp) or sh4v is None or np.isnan(sh4v[i]) or not (sh4s[i]==1 and sh4v[i]>=sv)
                if c[i]<lev[0] and sharpOK and (not requireZone or gp_on_zone(i,-1,lev)):
                    stop=lastPH+buf*atr[i]
                    tp=(lev[1]-rr*(stop-lev[1])) if tpMode=="rr" else (lastPL+tpBuf*atr[i])
                    armed=dict(dr=-1,lev=lev,stop=stop,tp=tp,filled=[False,False,False],bar0=i)
    tr=pd.DataFrame(trades)
    if len(tr)==0: return tr, dict(n=0)
    w=tr[tr.R>0]; lo=tr[tr.R<=0]
    pf=w.R.sum()/(-lo.R.sum()) if len(lo) and lo.R.sum()<0 else np.nan
    st=dict(n=len(tr),wr=round(100*len(w)/len(tr),1),pf=round(pf,2) if pf==pf else None,
            netR=round(tr.R.sum(),1),exp=round(tr.R.mean(),3),maxDD=round(dd(tr.R.values),1),
            avgFills=round(tr.fills.mean(),2))
    return tr, st

def scan_signals(D, p, last_n=3000):
    """Emit VALID A-grade setups in the last `last_n` bars (no forward sim) — for the
    daily live scanner. Same gates as backtest entry, but lists every fresh qualifying touch
    with entry/SL/TP/score so they can be ranked across symbols."""
    n=len(D["c"]); o,h,l,c,atr=D["o"],D["h"],D["l"],D["c"],D["atr"]
    pH,pL=D["pH"],D["pL"]; b1,b2,b3=D["b1"],D["b2"],D["b3"]
    bias=np.sign(p["wB1"]*b1+p["wB2"]*b2+p["wB3"]*b3).astype(int)
    mid=pL+0.5*(pH-pL)
    inDisc=(~np.isnan(pH))&(~np.isnan(pL))&(pH>pL)&(c<mid)
    inPrem=(~np.isnan(pH))&(~np.isnan(pL))&(pH>pL)&(c>mid)
    if p["session"]=="ny": insess=ny_session(D["time"])
    elif p["session"]=="london": insess=london_session(D["time"])
    else: insess=np.ones(n,bool)
    atrpct=np.where(c>0,atr/c*100.0,0.0); minATRpct=p.get("minATRpct",0.0)
    minATRrel=p.get("minATRrel",0.0)
    relok=np.ones(n,bool)
    if minATRrel>0:
        med=pd.Series(atr).rolling(500,min_periods=50).median().values
        relok=~(med>0)|(atr>=minATRrel*med)
    sh4v=D.get("sh4_vel"); sh4s=D.get("sh4_sgn"); sv=p.get("sharpVelH4",0.3)
    nsharp=p.get("noSharpHTF",False)
    buf=p["zoneBuf"]; minStop=p["minStop"]; minScore=p["minScore"]; minGrade=p["minGrade"]; rr=p["rr"]
    zones=[("OB-1h",1,D["c_demT"],D["c_demB"],D["c_demG"],60),
           ("OB-15m",1,D["a_demT"],D["a_demB"],D["a_demG"],15),
           ("OB-1h",-1,D["c_supT"],D["c_supB"],D["c_supG"],60),
           ("OB-15m",-1,D["a_supT"],D["a_supB"],D["a_supG"],15)]
    start=max(0,n-last_n); rows=[]
    for name,dr,ZT,ZB,ZG,ztf in zones:
        prev=np.nan; tested=False
        for i in range(n):
            zb=ZB[i]; zt=ZT[i]
            if not np.isnan(zb) and (np.isnan(prev) or zb!=prev): tested=False; prev=zb
            if tested or np.isnan(zb) or np.isnan(atr[i]): continue
            touch=(l[i]<=zt and h[i]>=zb) if dr==1 else (h[i]>=zb and l[i]<=zt)
            if not touch: continue
            tested=True
            if i<start: continue
            if not insess[i] or not relok[i]: continue
            if minATRpct>0 and atrpct[i]<minATRpct: continue
            if nsharp and sh4v is not None and not np.isnan(sh4v[i]) and sh4s[i]==-dr and sh4v[i]>=sv: continue
            g=ZG[i]; sc=score_val(g,ztf,dr,inDisc[i],inPrem[i])
            wt=(dr==bias[i])
            if g<minGrade or sc<minScore or not (wt or bias[i]==0): continue
            e=c[i]
            if dr==1: sl=min(zb-buf*atr[i], e-minStop*atr[i]); risk=e-sl
            else:     sl=max(zt+buf*atr[i], e+minStop*atr[i]); risk=sl-e
            if risk<=0: continue
            tp=e+dr*rr*risk
            rows.append(dict(bar=i, time=D["time"][i], dir=("LONG" if dr==1 else "SHORT"),
                type=name, grade=int(g), score=sc, withtrend=int(wt),
                entry=round(e,5), sl=round(sl,5), tp=round(tp,5), rr=rr,
                atrpct=round(atrpct[i],3), disc=int(inDisc[i]), prem=int(inPrem[i])))
    df=pd.DataFrame(rows)
    return df.sort_values("bar").reset_index(drop=True) if len(df) else df

DEF = dict(wB1=1,wB2=2,wB3=2, minScore=80, minGrade=2, rr=1.5, riskPct=1.0,
           zoneBuf=0.3, minStop=1.0, comm=0.00015, noSharp=True, sharpATR=2.5,
           roundTrip=True, session="off", bad_hours=(), minATRpct=0.0, minATRrel=0.0,
           noSharpHTF=False, sharpVelH4=0.3,
           useTrigger=False, armBars=12, invalidATR=1.0,
           fibLevels=(0.5,0.618,0.786), fibW=(0.25,0.5,0.25), tpBuf=0.1, fibMaxWait=300)

if __name__ == "__main__":
    import time as _t
    sym = sys.argv[1] if len(sys.argv)>1 else "XAUUSD"
    tf  = sys.argv[2] if len(sys.argv)>2 else "M5"
    t0=_t.time()
    print(f"prep {sym} {tf} ...", flush=True)
    D = prep_symbol(sym, tf)
    print(f"  prepped {len(D['c'])} bars in {_t.time()-t0:.1f}s", flush=True)
    p = dict(DEF)
    if sym=="XAUUSD": p["session"]="london"; p["sharpATR"]=1.5
    else: p["session"]="ny"; p["sharpATR"]=2.5
    tr, st = backtest(D, p)
    print("STATS:", json.dumps(st))
    if len(tr):
        print(tr.tail(8).to_string())
        print("\nby reason:", tr.reason.value_counts().to_dict())
        print("by type:", tr.type.value_counts().to_dict())
