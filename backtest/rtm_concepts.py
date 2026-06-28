#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
RTM concepts extracted from the KohanFx RTM books (readingthemarket / forvil /
SND Setup King / Flag Order Character / My Supply & Demand Note / price-is-everything).

Each detector is PURE numpy/pandas and works on the same o,h,l,c,atr arrays that
`rtm_bt.prep_symbol` already produces, so it can be:
  (a) used as an *entry confirmation gate* in the validated backtest harness
      (bt_structure.collect / confirmation_test), and
  (b) called live by the web signal_service to tag confluences the Persian
      assistant can cite by name.

NOTHING here changes the proven OB/FTR entry. These are refinements layered on top,
each one backtested in isolation and kept ONLY if it improves expR/PF
(same discipline as the existing CONF=reclaim/nosharp/room gates).

Concepts mechanized
-------------------
  engulfing(o,h,l,c)        -> (bull, bear)  two-candle body engulf  (entry trigger)
  sweep_reclaim(h,l,c,L)    -> (bull, bear)  liquidity sweep of a swing then reclaim
  compression(h,l,atr,...)  -> (incompr, ratio)  N tight bars = coiled energy
  pin_rejection(o,h,l,c,...)-> (bull, bear)  long-wick rejection candle
  qm_zones(...)             -> DataFrame  Quasimodo reversal zones (demT/B/G, supT/B/G)
  flag_limit_zones(...)     -> DataFrame  Flag-Limit zones (base proximal edge before impulse)
  freshness(ZB)             -> int array   number of prior touches of the live zone (0=fresh)

Discretionary RTM ideas NOT mechanized here (need judgement): "market story",
eureka reads, psychological round numbers, sentiment. Left to the human.
"""
import numpy as np
import pandas as pd

try:
    from rtm_bt import pivots, struct_engine
except Exception:                       # allow `python3 -m`/path variations
    from backtest.rtm_bt import pivots, struct_engine


# --------------------------------------------------------------------------- #
# 1. Engulfing  (entry trigger inside a zone)
# --------------------------------------------------------------------------- #
def engulfing(o, h, l, c, min_body_atr=0.0, atr=None):
    """Two-candle engulf on the BODIES (RTM 'engulf' = reaction confirmation).
    bull[i]: prior candle red, current green, current body fully covers prior body.
    bear[i]: prior candle green, current red, current body fully covers prior body.
    Optional: require current body >= min_body_atr*ATR (filter dojis)."""
    o = np.asarray(o, float); c = np.asarray(c, float)
    n = len(c); bull = np.zeros(n, bool); bear = np.zeros(n, bool)
    body = np.abs(c - o)
    big = np.ones(n, bool)
    if min_body_atr > 0 and atr is not None:
        a = np.asarray(atr, float)
        big = body >= (min_body_atr * np.where(np.isnan(a), np.inf, a))
    for i in range(1, n):
        pr_red = c[i-1] < o[i-1]; pr_grn = c[i-1] > o[i-1]
        cu_grn = c[i] > o[i]; cu_red = c[i] < o[i]
        if cu_grn and pr_red and o[i] <= c[i-1] and c[i] >= o[i-1] and big[i]:
            bull[i] = True
        if cu_red and pr_grn and o[i] >= c[i-1] and c[i] <= o[i-1] and big[i]:
            bear[i] = True
    return bull, bear


# --------------------------------------------------------------------------- #
# 2. Liquidity sweep + reclaim  (RTM fakeout / stop-hunt entry trigger)
# --------------------------------------------------------------------------- #
def sweep_reclaim(h, l, c, L=5):
    """Wick takes out the most recent confirmed swing then the candle CLOSES back
    inside (the trap). bull = sweep a swing LOW then reclaim (long trigger);
    bear = sweep a swing HIGH then reclaim (short trigger).
    Uses confirmed pivots (look-back/forward L) so the swing exists before the sweep."""
    h = np.asarray(h, float); l = np.asarray(l, float); c = np.asarray(c, float)
    ph, pl = pivots(h, l, L, L)
    n = len(c); bull = np.zeros(n, bool); bear = np.zeros(n, bool)
    last_ph = np.nan; last_pl = np.nan
    for i in range(n):
        # pivots[i] is confirmed AT bar i (centered L bars back) -> usable from i on
        if not np.isnan(ph[i]): last_ph = ph[i]
        if not np.isnan(pl[i]): last_pl = pl[i]
        if not np.isnan(last_pl) and l[i] < last_pl and c[i] > last_pl:
            bull[i] = True
        if not np.isnan(last_ph) and h[i] > last_ph and c[i] < last_ph:
            bear[i] = True
    return bull, bear


# --------------------------------------------------------------------------- #
# 3. Compression  (coiled range = high-probability breakout, RTM 'CP')
# --------------------------------------------------------------------------- #
def compression(h, l, atr, n_bars=3, k=0.6):
    """incompr[i] True when each of the last n_bars has range (h-l) < k*ATR.
    ratio[i] = mean(range)/ATR over the window (smaller = tighter coil)."""
    h = np.asarray(h, float); l = np.asarray(l, float); a = np.asarray(atr, float)
    n = len(h); rng = h - l
    incompr = np.zeros(n, bool); ratio = np.full(n, np.nan)
    for i in range(n_bars - 1, n):
        win = rng[i - n_bars + 1:i + 1]
        ai = a[i]
        if np.isnan(ai) or ai <= 0:
            continue
        ratio[i] = win.mean() / ai
        if np.all(win < k * ai):
            incompr[i] = True
    return incompr, ratio


# --------------------------------------------------------------------------- #
# 4. Pin / rejection candle  (long wick into a zone)
# --------------------------------------------------------------------------- #
def pin_rejection(o, h, l, c, wick_mult=2.0):
    """bull pin: lower wick >= wick_mult*body and closes in upper half.
    bear pin: upper wick >= wick_mult*body and closes in lower half."""
    o = np.asarray(o, float); h = np.asarray(h, float)
    l = np.asarray(l, float); c = np.asarray(c, float)
    n = len(c); bull = np.zeros(n, bool); bear = np.zeros(n, bool)
    body = np.abs(c - o) + 1e-12
    up_wick = h - np.maximum(o, c)
    dn_wick = np.minimum(o, c) - l
    rng = (h - l) + 1e-12
    bull = (dn_wick >= wick_mult * body) & ((c - l) / rng >= 0.5)
    bear = (up_wick >= wick_mult * body) & ((h - c) / rng >= 0.5)
    return bull, bear


# --------------------------------------------------------------------------- #
# 5. Quasimodo (QM) reversal zones
# --------------------------------------------------------------------------- #
def qm_zones(o, h, l, c, L, impulse, atr):
    """Quasimodo / 'over-and-under' reversal.

    Bearish QM (supply, for shorts): left-shoulder High (H1) -> pullback Low ->
    Head that makes a HIGHER high (sweeps H1's buy-side liquidity) -> price then
    breaks BELOW the pullback Low (left shoulder broken). The supply zone is the
    origin of that break-down leg (the head's last up-candle body->high).

    Bullish QM (demand, for longs): mirror — left-shoulder Low (L1) -> pullback
    High -> Head LOWER low (sweeps L1) -> break ABOVE pullback High. Demand zone =
    origin of the break-up leg.

    Returns the SAME schema as rtm_bt.zone_engine so it can be merged/aligned
    identically. Grade: +1 if the break leg >= impulse*ATR (strong), +1 if the
    head clearly swept the shoulder (>0.1*ATR beyond)."""
    o = np.asarray(o, float); h = np.asarray(h, float)
    l = np.asarray(l, float); c = np.asarray(c, float); atr = np.asarray(atr, float)
    n = len(c)
    ph, pl = pivots(h, l, L, L)
    DemT = np.full(n, np.nan); DemB = np.full(n, np.nan); DemG = np.zeros(n)
    SupT = np.full(n, np.nan); SupB = np.full(n, np.nan); SupG = np.zeros(n)
    # rolling memory of the last few confirmed pivots
    Hs = []  # list of (idx, price)
    Ls = []
    cdT = cdB = csT = csB = np.nan; cdG = csG = 0.0
    luT = luB = lbT = lbB = np.nan
    for i in range(n):
        # track last bullish/bearish candle extents as zone candidates (like zone_engine)
        if c[i] < o[i]: lbT = max(o[i], c[i]); lbB = l[i]
        if c[i] > o[i]: luT = h[i]; luB = min(o[i], c[i])
        if not np.isnan(ph[i]): Hs.append((i, ph[i])); Hs = Hs[-4:]
        if not np.isnan(pl[i]): Ls.append((i, pl[i])); Ls = Ls[-4:]
        ai = atr[i] if not np.isnan(atr[i]) else np.nan
        # Bearish QM: need H1 (shoulder) , Low (neckline) , Head HH that swept H1
        if len(Hs) >= 2 and len(Ls) >= 1 and not np.isnan(ai):
            (i_h1, h1), (i_head, head) = Hs[-2], Hs[-1]
            neck = [p for (ix, p) in Ls if i_h1 < ix < i_head]
            if neck and head > h1 + 0.1 * ai:           # head swept the shoulder highs
                nlow = min(neck)
                if c[i] < nlow:                          # neckline broken to the downside
                    csT = luT if not np.isnan(luT) else head
                    csB = luB if not np.isnan(luB) else h1
                    strong = 1.0 if (head - l[i]) >= impulse * ai else 0.0
                    csG = 1.0 + strong
        # Bullish QM
        if len(Ls) >= 2 and len(Hs) >= 1 and not np.isnan(ai):
            (i_l1, l1), (i_head, head) = Ls[-2], Ls[-1]
            neck = [p for (ix, p) in Hs if i_l1 < ix < i_head]
            if neck and head < l1 - 0.1 * ai:
                nhigh = max(neck)
                if c[i] > nhigh:
                    cdT = lbT if not np.isnan(lbT) else l1
                    cdB = lbB if not np.isnan(lbB) else head
                    strong = 1.0 if (h[i] - head) >= impulse * ai else 0.0
                    cdG = 1.0 + strong
        DemT[i] = cdT; DemB[i] = cdB; DemG[i] = cdG
        SupT[i] = csT; SupB[i] = csB; SupG[i] = csG
    return pd.DataFrame({"demT": DemT, "demB": DemB, "demG": DemG,
                         "supT": SupT, "supB": SupB, "supG": SupG})


# --------------------------------------------------------------------------- #
# 6. Flag Limit (FL) zones  — base proximal edge before an impulse (FTR family)
# --------------------------------------------------------------------------- #
def flag_limit_zones(o, h, l, c, L, impulse, atr, max_base=4):
    """Flag Limit: a small BASE (<=max_base candles, each range < ATR) followed by a
    strong impulse leg (>= impulse*ATR). The FL is the proximal edge of that base —
    price is expected to 'fail to return' past it. This is a stricter, base-anchored
    cousin of the OB. Returns the zone_engine schema.

    Grade: +1 always (valid FL), +1 if the impulse leg out of the base is >= 1.5*ATR
    (clean, low-overlap departure = stronger FL)."""
    o = np.asarray(o, float); h = np.asarray(h, float)
    l = np.asarray(l, float); c = np.asarray(c, float); atr = np.asarray(atr, float)
    n = len(c); rng = h - l
    DemT = np.full(n, np.nan); DemB = np.full(n, np.nan); DemG = np.zeros(n)
    SupT = np.full(n, np.nan); SupB = np.full(n, np.nan); SupG = np.zeros(n)
    cdT = cdB = csT = csB = np.nan; cdG = csG = 0.0
    for i in range(2, n):
        ai = atr[i]
        if np.isnan(ai) or ai <= 0:
            DemT[i]=cdT; DemB[i]=cdB; DemG[i]=cdG; SupT[i]=csT; SupB[i]=csB; SupG[i]=csG
            continue
        # look back for a tight base of 1..max_base candles ending at i-1
        for b in range(1, max_base + 1):
            s = i - b
            if s < 0:
                break
            base = rng[s:i]
            if not np.all(base < ai):           # base must be tight (each < ATR)
                continue
            base_hi = h[s:i].max(); base_lo = l[s:i].min()
            # bullish FL: strong up-impulse out of the base
            if (h[i] - base_lo) >= impulse * ai and c[i] > base_hi:
                cdT = base_hi; cdB = base_lo
                cdG = 1.0 + (1.0 if (h[i] - base_lo) >= 1.5 * ai else 0.0)
            # bearish FL: strong down-impulse out of the base
            if (base_hi - l[i]) >= impulse * ai and c[i] < base_lo:
                csT = base_hi; csB = base_lo
                csG = 1.0 + (1.0 if (base_hi - l[i]) >= 1.5 * ai else 0.0)
            break  # smallest valid base wins
        DemT[i]=cdT; DemB[i]=cdB; DemG[i]=cdG; SupT[i]=csT; SupB[i]=csB; SupG[i]=csG
    return pd.DataFrame({"demT": DemT, "demB": DemB, "demG": DemG,
                         "supT": SupT, "supB": SupB, "supG": SupG})


# --------------------------------------------------------------------------- #
# 7. Freshness — prior-touch count of the current live zone bottom
# --------------------------------------------------------------------------- #
def freshness(ZB, ZT, h, l):
    """For each bar, how many times THIS live zone (identified by its bottom price)
    has already been touched. 0 = fresh (first touch). Resets when the zone changes."""
    ZB = np.asarray(ZB, float); ZT = np.asarray(ZT, float)
    h = np.asarray(h, float); l = np.asarray(l, float)
    n = len(ZB); out = np.zeros(n, int)
    prev = np.nan; cnt = 0
    for i in range(n):
        zb = ZB[i]; zt = ZT[i]
        if not np.isnan(zb) and (np.isnan(prev) or zb != prev):
            prev = zb; cnt = 0
        out[i] = cnt
        if not np.isnan(zb) and (l[i] <= zt and h[i] >= zb):
            cnt += 1
    return out


# --------------------------------------------------------------------------- #
# Convenience: compute all per-bar trigger arrays a signal needs from D
# --------------------------------------------------------------------------- #
def triggers_from_D(D, L=5, impulse=1.0):
    """Return a dict of per-bar concept arrays computed from a prep_symbol() dict D.
    Used live by signal_service to tag confluences at a touch bar."""
    o, h, l, c, atr = D["o"], D["h"], D["l"], D["c"], D["atr"]
    bull_eng, bear_eng = engulfing(o, h, l, c, min_body_atr=0.3, atr=atr)
    bull_sw, bear_sw = sweep_reclaim(h, l, c, L=L)
    incompr, cratio = compression(h, l, atr)
    bull_pin, bear_pin = pin_rejection(o, h, l, c)
    return dict(bull_eng=bull_eng, bear_eng=bear_eng,
                bull_sweep=bull_sw, bear_sweep=bear_sw,
                compression=incompr, comp_ratio=cratio,
                bull_pin=bull_pin, bear_pin=bear_pin)
