#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Learn-from-TP/SL backtest for the v4 rules (structure target + reacted OB).
For every valid with-trend reacted-OB touch we simulate the forward path ONCE and
resolve the SAME entry under several TP rules at once:
   fixed 1R / 1.5R / 2R / 3R   AND   structure (proximal edge of the opposing live zone).
SL = distal edge of the zone -/+ slBreath*W (the user's stop), conservative SL-first on ties.

Run: python3 bt_structure.py            # whole watchlist, M5
     python3 bt_structure.py M15        # different entry TF
Outputs a learning report (which TP wins, where stops land) + appends to LEARNINGS.
"""
import os, sys, json, glob
import numpy as np, pandas as pd
import rtm_bt as B
import rtm_concepts as RC
import rsi_tools as RT

ETF = sys.argv[1] if len(sys.argv) > 1 else "M5"
SCOPE = sys.argv[2] if len(sys.argv) > 2 else "all"   # "all" = every Downloads symbol, else watchlist
WINDOW_DAYS = int(os.environ.get("FD_WIN", "0"))       # 0 = full history; >0 = only entries in last N days
SRC = os.environ.get("FD_SRC", "all")                  # all | 1h  (1h = only HTF/1h-anchored zones)
CONF = os.environ.get("FD_CONF", "none")               # none | reclaim | nosharp | room | all  (entry confirmations)
WL = os.path.expanduser("~/Desktop/trade/backtest/watchlist.txt")
LEARN = os.path.expanduser("~/Desktop/trade/journal/LEARNINGS.md")
RULES = [("1R", 1.0), ("1.5R", 1.5), ("2R", 2.0), ("3R", 3.0)]   # + "struct" per-entry
MAXBARS = 4000
# ---- partial-exit + stop knobs (env-overridable so the 10-agent fan-out can sweep them) ----
PARTIAL  = os.environ.get("FD_PARTIAL", "1") != "0"     # score the 1/3@1R + BE + runner ladder
TP1_R    = float(os.environ.get("FD_TP1", "1.0"))        # partial level (in R)
TP1_FRAC = float(os.environ.get("FD_TP1FRAC", "0.3333")) # fraction banked at TP1
MOVE_BE  = os.environ.get("FD_BE", "1") != "0"           # move stop to entry after TP1
RUNNER_R = float(os.environ.get("FD_RUNNER", "2.0"))     # runner target (in R) for the remaining size
BUF_ATR  = os.environ.get("FD_BUF")                      # None = per-source default (0.3); else override ×ATR
MINSTOP_ATR = os.environ.get("FD_MINSTOP")               # None = per-symbol default; else override ×ATR
OUT_CSV  = os.environ.get("FD_OUT", os.path.expanduser("~/Desktop/trade/backtest/bt_structure_rows.csv"))
MODEL_MODE = os.environ.get("FD_MODEL", "off")           # off | veto | agree  (behavioural-model gate)
MTAU = float(os.environ.get("FD_MTAU", "0.0"))           # agreement band: |p_up-0.5| must exceed this


def _proj_model(tfmin):
    """Load the per-TF projection_model from tuned.json (so the backtest can apply the SAME
    behavioural-model gate the live engine uses). Returns (intercept, weights) or None."""
    import json
    try:
        import rsi_tools as _RT
        t = json.load(open(os.path.join(os.path.dirname(__file__), "..", "web", "tuned.json"), encoding="utf-8"))
        m = (t.get("projection_model") or {}).get(_RT._proj_tf_key(tfmin))
        if m and "weights" in m:
            return float(m.get("intercept", 0.0)), [float(w) for w in m["weights"]]
    except Exception:
        pass
    return None

def score_partial(mfe_R, full_win, tp1_R=TP1_R, tp1_frac=TP1_FRAC, runner_R=RUNNER_R, move_be=MOVE_BE):
    """Realized R for the partial+BE rule (canonical, shared with the live setup_store sim).
    mfe_R = max favorable excursion in R before the trade resolved.
    full_win = the runner reached runner_R without being stopped first."""
    runner = 1.0 - tp1_frac
    if full_win:                       # tp1 banked AND runner hit target
        return tp1_frac*tp1_R + runner*runner_R          # e.g. 1/3*1 + 2/3*2 = +1.667R
    if mfe_R >= tp1_R:                  # tp1 banked, runner stopped at BE (or initial stop if move_be off)
        return tp1_frac*tp1_R + (runner*0.0 if move_be else runner*(-1.0))   # e.g. +0.333R
    return -1.0                        # never reached tp1 -> full initial stop

def syms():
    if SCOPE == "all":
        out = []
        for p in sorted(glob.glob(os.path.expanduser(f"~/Downloads/*_{ETF}.csv"))):
            s = os.path.basename(p)[:-(len(ETF)+5)]   # strip _{ETF}.csv
            if not s or any(ch.isdigit() for ch in s.split("USD")[0][:1]): continue
            h1 = os.path.expanduser(f"~/Downloads/{s}_H1.csv")
            h4 = os.path.expanduser(f"~/Downloads/{s}_H4.csv")
            m15 = os.path.expanduser(f"~/Downloads/{s}_M15.csv")
            if os.path.exists(h1) and os.path.exists(h4) and os.path.exists(m15):
                out.append((s, "london" if s == "XAUUSD" else "ny"))
        return out
    out = []
    for line in open(WL):
        line = line.strip()
        if not line or line.startswith("#"): continue
        for tok in line.split():
            s, _, sess = tok.partition(",")
            out.append((s, sess or "ny"))
    return out

def collect(D, sym, sess):
    """Yield one record per valid with-trend reacted-OB touch, with the forward path
    resolved under each TP rule. reacted == first fresh touch of the zone (by construction)."""
    n = len(D["c"]); o,h,l,c,atr = D["o"],D["h"],D["l"],D["c"],D["atr"]
    b1,b2,b3 = D["b1"],D["b2"],D["b3"]
    bias = np.sign(1*b1+2*b2+2*b3).astype(int)
    # ---- behavioural-model gate (same projection_model the live engine uses) ----
    tfmin = B.TF_MIN.get(ETF, 5) if hasattr(B, "TF_MIN") else {"M1":1,"M5":5,"M15":15,"H1":60,"H4":240}.get(ETF, 5)
    _pm = _proj_model(tfmin) if MODEL_MODE != "off" else None
    pup = None
    if _pm is not None:
        intercept, W = _pm
        rsiA = RT.rsi(c, 14)
        dv = np.zeros(n)
        try:
            for d in RT.divergences(h, l, c, rsiA, L=5, recent_bars=n):
                bb = d["bar"]; st = bb + 5
                dv[st:min(n, st + 12)] = 1.0 if d["type"] == "bull" else -1.0
        except Exception:
            pass
        is_h1 = 1.0 if int(tfmin) == 60 else 0.0
        pup = np.full(n, 0.5)
        for i in range(21, n):
            a = atr[i] if (np.isfinite(atr[i]) and atr[i] > 0) else 0.0
            r = rsiA[i]
            if not np.isfinite(r): continue
            slope = float(np.sign(c[i] - c[i - 20]))
            rsi_pull = 1.0 if r < 30 else (-1.0 if r > 70 else 0.0)
            rsi_z = (float(r) - 50.0) / 15.0
            ts = min(5.0, abs(c[i] - c[i - 20]) / a) if a > 0 else 0.0
            feats = [float(bias[i]), slope, rsi_pull, rsi_z, float(dv[i]), ts, ts * slope, rsi_z * is_h1]
            z = intercept + sum(w * f for w, f in zip(W, feats))
            pup[i] = 1.0 / (1.0 + np.exp(-max(-30.0, min(30.0, z))))
    if sess == "london": insess = B.london_session(D["time"])
    elif sess == "ny":   insess = B.ny_session(D["time"])
    else:                insess = np.ones(n, bool)
    gold = sym == "XAUUSD"
    minStop = (float(MINSTOP_ATR) if MINSTOP_ATR is not None else (2.5 if gold else 1.0))
    buf = (float(BUF_ATR) if BUF_ATR is not None else 0.3); minGrade = 2; minATRpct = 0.15
    # live opposing-zone proximal edges (1h preferred, else 15m) for the structure TP
    cDt, cDb = D["c_demT"], D["c_demB"]; cSt, cSb = D["c_supT"], D["c_supB"]
    aDt, aDb = D["a_demT"], D["a_demB"]; aSt, aSb = D["a_supT"], D["a_supB"]
    atrpct = np.where(c > 0, atr/c*100.0, 0.0)
    # ---- RTM concept trigger arrays (computed once per symbol) ----
    bull_eng, bear_eng = RC.engulfing(o, h, l, c, min_body_atr=0.3, atr=atr)
    bull_sw,  bear_sw  = RC.sweep_reclaim(h, l, c, L=5)
    bull_pin, bear_pin = RC.pin_rejection(o, h, l, c)
    incompr,  _cr      = RC.compression(h, l, atr)
    # zones to enter from: (name, dir, zoneTop, zoneBot, grade)
    zones = [("OB-1h", 1, cDt, cDb, D["c_demG"], 60),
             ("OB-15m",1, aDt, aDb, D["a_demG"], 15),
             ("OB-1h",-1, cSt, cSb, D["c_supG"], 60),
             ("OB-15m",-1,aSt, aSb, D["a_supG"], 15)]
    if SRC == "1h":
        zones = [z for z in zones if z[5] == 60]   # HTF-only: drop the 15m/chart-TF zones
    tcut = (D["time"].max() - pd.Timedelta(days=WINDOW_DAYS)) if WINDOW_DAYS > 0 else None
    recs = []
    for name, dr, ZT, ZB, ZG, ztf in zones:
        prev = np.nan; tested = False
        for i in range(n):
            zt, zb = ZT[i], ZB[i]
            if not np.isnan(zb) and (np.isnan(prev) or zb != prev): tested = False; prev = zb
            if tested or np.isnan(zb) or np.isnan(atr[i]): continue
            touch = (l[i] <= zt and h[i] >= zb) if dr == 1 else (h[i] >= zb and l[i] <= zt)
            if not touch: continue
            tested = True
            if tcut is not None and D["time"][i] < tcut: continue   # only entries inside the window
            # gates: session, vol, grade, with-trend
            if not insess[i] or atrpct[i] < minATRpct: continue
            if ZG[i] < minGrade: continue
            wt = (dr == bias[i]);
            if not (wt or bias[i] == 0): continue
            # ---- behavioural-model gate (evaluated when price reaches the zone, like live) ----
            if pup is not None:
                pu = pup[i]
                if MODEL_MODE == "veto":            # drop setups the model contradicts (mirrors live)
                    if (dr == 1 and pu <= 0.5 - MTAU) or (dr == -1 and pu >= 0.5 + MTAU): continue
                elif MODEL_MODE == "agree":         # stronger: only take setups the model AGREES with
                    if not ((pu >= 0.5 + MTAU) if dr == 1 else (pu <= 0.5 - MTAU)): continue
            # ---- entry confirmations ----
            if CONF in ("reclaim", "all"):
                # don't enter on the raw touch; require a reaction candle closing in trade dir, back inside the zone
                ok = (c[i] > o[i] and c[i] >= zb) if dr == 1 else (c[i] < o[i] and c[i] <= zt)
                if not ok: continue
            if CONF in ("nosharp", "all"):
                sv4 = D.get("sh4_vel"); sg4 = D.get("sh4_sgn")
                if sv4 is not None and not np.isnan(sv4[i]) and sg4[i] == -dr and sv4[i] >= 0.3:
                    continue   # approach leg into the zone was too sharp -> zone likely breaks
            # ---- RTM concept confirmations (windowed) ----
            # CONF tokens may be combined with '+' (e.g. "room+engulf"). Reaction tokens
            # are NOT required on the raw touch bar: we WAIT up to FD_ARM bars for the
            # confirmation candle and enter at IT (RTM practice). If price breaks the zone
            # distal before the trigger, the setup is invalidated (no entry). "room" is
            # handled later (needs kstruct). FD_ARM=0 keeps the legacy touch-bar behaviour.
            _toks = CONF.split("+")
            _need_trig = any(t in _toks for t in ("engulf", "sweep", "pin", "compress", "trigger"))
            ei = i
            if _need_trig:
                arm = int(os.environ.get("FD_ARM", "8"))
                sl0 = (zb - buf*atr[i]) if dr == 1 else (zt + buf*atr[i])   # zone-distal invalidation
                found = None
                for j in range(i, min(i + arm + 1, n)):
                    if dr == 1 and l[j] <= sl0: break
                    if dr == -1 and h[j] >= sl0: break
                    trig = True
                    if "engulf" in _toks:   trig = trig and (bull_eng[j] if dr == 1 else bear_eng[j])
                    if "sweep" in _toks:    trig = trig and (bull_sw[j] if dr == 1 else bear_sw[j])
                    if "pin" in _toks:      trig = trig and (bull_pin[j] if dr == 1 else bear_pin[j])
                    if "compress" in _toks: trig = trig and bool(incompr[j])
                    if "trigger" in _toks:
                        trig = trig and ((bull_eng[j] or bull_sw[j] or bull_pin[j]) if dr == 1
                                         else (bear_eng[j] or bear_sw[j] or bear_pin[j]))
                    if trig:
                        found = j; break
                if found is None: continue
                ei = found
            e = c[ei]
            if dr == 1:
                sl = min(zb - buf*atr[ei], e - minStop*atr[ei]); risk = e - sl
            else:
                sl = max(zt + buf*atr[ei], e + minStop*atr[ei]); risk = sl - e
            if risk <= 0: continue
            # structure TP = proximal edge of the opposing live zone (1h then 15m) at entry bar
            if dr == 1:
                opp = cSb[ei] if (not np.isnan(cSb[ei]) and cSb[ei] > e) else (aSb[ei] if (not np.isnan(aSb[ei]) and aSb[ei] > e) else np.nan)
            else:
                opp = cDt[ei] if (not np.isnan(cDt[ei]) and cDt[ei] < e) else (aDt[ei] if (not np.isnan(aDt[ei]) and aDt[ei] < e) else np.nan)
            kstruct = ((opp - e)/risk) if (dr == 1 and not np.isnan(opp)) else (((e - opp)/risk) if (dr == -1 and not np.isnan(opp)) else np.nan)
            if "room" in CONF.split("+") or CONF == "all":
                # require clear room to the 2R target: the opposing zone must be >=2R away (else 2R is blocked)
                if (not np.isnan(kstruct)) and kstruct < 2.0: continue
            # forward path -> resolve each rule
            targets = [k for _, k in RULES]
            has_struct = (not np.isnan(kstruct)) and kstruct >= 0.3
            if has_struct: targets = targets + [kstruct]
            resolved = {t: None for t in targets}; mfe = 0.0; mae = 0.0; bars_to_stop = None
            # partial+BE ladder state machine (conservative SL/BE-before-TP on intra-bar ties):
            #   stage 0 = pre-TP1 (original SL active);  stage 1 = runner after TP1 (BE stop active)
            p_stage = 0; p_R = None; p_status = None; be = e
            for j in range(ei+1, min(ei+1+MAXBARS, n)):
                favR = ((h[j]-e)/risk) if dr == 1 else ((e-l[j])/risk)
                advR = ((e-l[j])/risk) if dr == 1 else ((h[j]-e)/risk)   # adverse excursion (R), how far against
                advHit = (l[j] <= sl) if dr == 1 else (h[j] >= sl)
                mfe = max(mfe, favR); mae = max(mae, advR)
                for t in targets:
                    if resolved[t] is not None: continue
                    reach = favR >= t
                    if advHit and not reach: resolved[t] = ("SL", -1.0)
                    elif reach and not advHit: resolved[t] = ("TP", t)
                    elif reach and advHit: resolved[t] = ("SL", -1.0)  # conservative tie
                if PARTIAL and p_R is None:
                    if p_stage == 1:                       # runner active since a PRIOR bar
                        be_hit = (l[j] <= be) if dr == 1 else (h[j] >= be)
                        stop_active = be_hit if MOVE_BE else advHit
                        runner_hit = favR >= RUNNER_R
                        if runner_hit and not stop_active:
                            p_R = score_partial(mfe, True);  p_status = "WIN"
                        elif stop_active:                  # BE/runner-stop (conservative tie -> stop)
                            p_R = score_partial(mfe, False); p_status = "PARTIAL_WIN"
                    elif p_stage == 0:
                        hit_tp1 = favR >= TP1_R
                        if advHit:                         # stopped before banking TP1 (true loss)
                            p_R = -1.0; p_status = "LOSS"; bars_to_stop = j - ei
                        elif hit_tp1:
                            p_stage = 1                    # banked TP1 this bar; BE active from next bar
                done_rules = all(resolved[t] is not None for t in targets)
                if done_rules and (not PARTIAL or p_R is not None): break
            if PARTIAL and p_R is None:                    # ran out of bars
                if p_stage == 1: p_R = score_partial(mfe, False); p_status = "PARTIAL_WIN"  # runner unresolved -> flat
            comm = 0.00015 * (2)   # round-trip approx in R is negligible; keep simple
            rec = dict(sym=sym, i=ei, t=D["time"][ei], half=(0 if ei < n//2 else 1), type=name, dir=dr, grade=int(ZG[i]), withtrend=int(wt),
                       waited=int(ei - i), ztf=ztf, risk=risk, mfe=round(mfe, 2), mae=round(mae, 2),
                       bars_to_stop=bars_to_stop, R_partbe=(round(p_R, 4) if p_R is not None else None),
                       partstat=p_status, giveback=int(p_status == "PARTIAL_WIN"),
                       kstruct=round(float(kstruct), 2) if has_struct else None)
            for nm, k in RULES:
                r = resolved.get(k); rec[nm] = (r[1] if r else (k if mfe >= k else (-1.0 if mfe < k else None)))
                # if never resolved (ran out of bars) and never reached k and never SL -> mark unresolved as None
                if r is None: rec[nm] = None
            if has_struct:
                r = resolved.get(kstruct); rec["struct"] = (r[1] if r else None)
            else:
                rec["struct"] = None
            recs.append(rec)
    return recs

def agg(col, df):
    s = df[df[col].notna()]
    n = len(s)
    if n == 0: return None
    R = s[col].astype(float)
    wins = R[R > 0]; los = R[R <= 0]
    pf = wins.sum()/(-los.sum()) if len(los) and los.sum() < 0 else float('inf')
    return dict(n=n, wr=round(100*len(wins)/n, 1), pf=(round(pf, 2) if pf != float('inf') else None),
                expR=round(R.mean(), 3), netR=round(R.sum(), 1))

def main():
    allrecs = []
    for s, sess in syms():
        try:
            D = B.prep_symbol(s, ETF)
        except Exception as e:
            print(f"{s:9s} data err: {str(e)[:40]}", flush=True); continue
        r = collect(D, s, sess)
        allrecs += r
        print(f"{s:9s} {len(r):4d} setups", flush=True)
    df = pd.DataFrame(allrecs)
    if len(df) == 0:
        print("no setups"); return
    df.to_csv(OUT_CSV, index=False)
    print(f"\n=== {len(df)} total setups across watchlist ({ETF}) ===\n")
    print(f"{'TP rule':10s} {'n':>4s} {'WR%':>5s} {'PF':>5s} {'expR':>7s} {'netR':>7s}")
    print("-"*44)
    for nm, _ in RULES + [("struct", 0), ("R_partbe", 0)]:
        a = agg(nm, df)
        if a: print(f"{nm:10s} {a['n']:4d} {a['wr']:5} {str(a['pf']):>5} {a['expR']:+7.3f} {a['netR']:+7.1f}")
    # ---- partial+BE headline: report NET win-rate AND full-target hit-rate AND PF together (honest) ----
    if PARTIAL and "R_partbe" in df and df["R_partbe"].notna().any():
        pb = df[df["R_partbe"].notna()]
        net_win = 100*(pb["R_partbe"] > 0).mean()
        full_hit = 100*(pb["partstat"] == "WIN").mean()
        partial_hit = 100*(pb["partstat"] == "PARTIAL_WIN").mean()
        loss = 100*(pb["partstat"] == "LOSS").mean()
        a = agg("R_partbe", df)
        print(f"\n=== PARTIAL+BE (1/{round(1/TP1_FRAC)}@{TP1_R}R → BE → runner {RUNNER_R}R) ===")
        print(f"  NET win-rate   : {net_win:.1f}%   (R_partbe > 0  =  full wins + givebacks banked at +{round(TP1_FRAC*TP1_R,3)}R)")
        print(f"  full-target hit: {full_hit:.1f}%   (runner reached {RUNNER_R}R)")
        print(f"  partial-only   : {partial_hit:.1f}%   |   clean loss: {loss:.1f}%")
        print(f"  expR={a['expR']:+.3f}  PF={a['pf']}  netR={a['netR']:+.1f}   <- winrate is honest ONLY alongside these")
    # structure RR achieved
    sk = df["kstruct"].dropna()
    if len(sk):
        print(f"\nstructure RR target: median={sk.median():.2f}R  mean={sk.mean():.2f}R  "
              f"p25={sk.quantile(.25):.2f}  p75={sk.quantile(.75):.2f}")
    # MFE distribution -> where price actually goes (learn the natural target)
    mfe = df["mfe"]
    print(f"\nMFE (max favorable, in R) before resolution:")
    for thr in [0.5,1,1.5,2,3,5]:
        print(f"   reached >= {thr}R : {round(100*(mfe>=thr).mean(),1):5}%  of setups")
    # tiering: with-trend vs counter, grade, ztf  -- on the best fixed rule (expectancy)
    # include struct in the best-rule search
    cand = RULES + [("struct", 0)]
    best = max(cand, key=lambda r: (agg(r[0], df) or {'expR':-9})['expR'])[0]
    print(f"\n=== break-down on best rule = {best}  (SRC={SRC}, win={WINDOW_DAYS or 'full'}) ===")
    for key in ["withtrend", "grade", "ztf", "dir"]:
        print(f"  by {key}:")
        for v in sorted(df[key].dropna().unique()):
            a = agg(best, df[df[key] == v])
            if a: print(f"     {key}={v}: n={a['n']} WR={a['wr']}% PF={a['pf']} expR={a['expR']:+}")
    # OOS stability: first vs second half (per-symbol time split, pooled)
    print("  OOS halves (best rule):")
    for hv in (0, 1):
        a = agg(best, df[df["half"] == hv])
        if a: print(f"     half {hv}: n={a['n']} WR={a['wr']}% PF={a['pf']} expR={a['expR']:+} netR={a['netR']}")
    # ===== compounding equity: $1000, risk 1% per trade, trades in chronological order =====
    RULE = os.environ.get("FD_RULE", "2R")
    START = 1000.0; RISK = 0.01
    e = df[df[RULE].notna()].sort_values("t")
    bal = START; peak = START; mdd = 0.0; w = 0; nseq = 0
    for R in e[RULE].astype(float):
        bal *= (1 + RISK * R); peak = max(peak, bal); mdd = min(mdd, (bal/peak - 1) * 100)
        nseq += 1; w += 1 if R > 0 else 0
    print(f"\n=== EQUITY  rule={RULE}  start=${START:.0f}  risk={RISK*100:.0f}%/trade  (SRC={SRC}, win={WINDOW_DAYS or 'full'}) ===")
    if nseq:
        print(f"  trades={nseq}  wins={w} ({100*w/nseq:.1f}%)  final=${bal:.2f}  return={(bal/START-1)*100:+.1f}%  maxDD={mdd:.1f}%")
        print(f"  first trade {e['t'].iloc[0]}  ->  last {e['t'].iloc[-1]}")
    else:
        print("  no resolved trades for this rule")
    return df, best

if __name__ == "__main__":
    main()
