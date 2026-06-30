#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Signal service — turns live data into a structured trade plan using the VALIDATED
engine (OB grade-2 + room>=2R + R-grid), plus RTM concept layers:

  HIGH confidence  : OB-1h zone, grade>=2, with-trend, room>=2R   (the proven edge)
  MEDIUM           : OB-1h grade>=2 w/o room, OB-15m grade2, or Flag-Limit zone
  LOW              : counter-trend or grade<2

QM / engulf / sweep / compression are attached as DISPLAY-ONLY confluence tags
(they did NOT add mechanical edge in backtest — see journal/LEARNINGS.md), so they
inform the human but never raise the confidence tier on their own.

Nothing here places orders. It returns a plan; the user trades manually.
"""
import os, sys, json
import numpy as np

# ---- tuned knobs (written by optimize.py / the scheduled optimizer; read LIVE, no restart) ---
_TUNED_PATH = os.path.join(os.path.dirname(__file__), "tuned.json")
_tuned_cache = {"mtime": None, "data": {}}
def TUNED():
    try:
        mt = os.path.getmtime(_TUNED_PATH)
        if _tuned_cache["mtime"] != mt:
            with open(_TUNED_PATH, encoding="utf-8") as f:
                _tuned_cache["data"] = json.load(f)
            _tuned_cache["mtime"] = mt
    except Exception:
        pass
    return _tuned_cache["data"]

BT = os.path.join(os.path.dirname(__file__), "..", "backtest")
sys.path.insert(0, os.path.abspath(BT))
import rtm_bt as B
import rtm_concepts as RC
import rsi_tools as RT
import live_feed as F
try:
    import dominance as DOM
except Exception:
    DOM = None

# higher timeframes are more reliable for RSI/divergence (user's guidance)
TF_WEIGHT = {"M1": 0.3, "M5": 0.5, "M15": 0.8, "H1": 1.0, "H4": 1.2}

# REVERSAL setup (counter-trend short at supply / long at demand, proximal-edge entry, 3R target).
# Backtested honestly in backtest/reversal_eval.py: the counter-trend case is the ONLY positive
# one — expR +0.143R over 224 trades (WR ~29%: stops often, the 3R winners pay for it). This is
# the user's own trade style; the system now recognises it instead of suppressing it as "LOW".
REVERSAL_EDGE = {"expR": 0.143, "wr": 28.6, "n": 224, "rr": 3.0}

# per-TF honesty badge: only M5 has a backtested edge (crypto+gold)
TF_HEALTH = {
    "M5":  ("green",  "اج اثبات‌شده (بک‌تستِ OOS-پایدار)"),
    "M1":  ("grey",   "آزمایشی — ورودِ خام روی M1 در بک‌تست ضعیف بود؛ نیاز به تریگر"),
    "M15": ("grey",   "آزمایشی — M15 در بک‌تست اجِ پایدار نداشت"),
    "H1":  ("amber",  "ساختار/سوگیری معتبر، ولی اجِ ورود روی H1 جداگانه بک‌تست نشده"),
    "H4":  ("amber",  "فقط برای زمینهٔ HTF؛ به‌عنوان تایم‌فریمِ ورود بک‌تست نشده"),
}


def _last(a):
    a = np.asarray(a, float)
    for v in a[::-1]:
        if not np.isnan(v):
            return float(v)
    return None


def _fl_zone(sym):
    """Latest Flag-Limit demand/supply zone on H1 (secondary source)."""
    try:
        h1 = B.load(sym, "H1")
        oo, hh, ll, cc = (h1[x].values.astype(float) for x in ["Open", "High", "Low", "Close"])
        aa = B.atr_rma(hh, ll, cc, 14)
        z = RC.flag_limit_zones(oo, hh, ll, cc, 5, 1.0, aa)
        return (_last(z["demT"]), _last(z["demB"]), _last(z["demG"]),
                _last(z["supT"]), _last(z["supB"]), _last(z["supG"]))
    except Exception:
        return (None,) * 6


def _confluence_tags(D, dr):
    """Display-only RTM tags evaluated on the last ~6 bars (reaction window)."""
    o, h, l, c, atr = D["o"], D["h"], D["l"], D["c"], D["atr"]
    bull_e, bear_e = RC.engulfing(o, h, l, c, min_body_atr=0.3, atr=atr)
    bull_s, bear_s = RC.sweep_reclaim(h, l, c, L=5)
    bull_p, bear_p = RC.pin_rejection(o, h, l, c)
    incompr, _ = RC.compression(h, l, atr)
    w = slice(max(0, len(c) - 6), len(c))
    tags = []
    if dr == 1:
        if bull_e[w].any(): tags.append(("engulf", "کندلِ اِنگالفِ صعودی (واکنشِ خریداران)"))
        if bull_s[w].any(): tags.append(("sweep", "شکارِ نقدینگیِ کفِ قبلی و بازگشت (Fakeout)"))
        if bull_p[w].any(): tags.append(("pin", "کندلِ پین‌بارِ صعودی (سایهٔ پایینیِ بلند)"))
    else:
        if bear_e[w].any(): tags.append(("engulf", "کندلِ اِنگالفِ نزولی (واکنشِ فروشندگان)"))
        if bear_s[w].any(): tags.append(("sweep", "شکارِ نقدینگیِ سقفِ قبلی و بازگشت (Fakeout)"))
        if bear_p[w].any(): tags.append(("pin", "کندلِ پین‌بارِ نزولی (سایهٔ بالاییِ بلند)"))
    if incompr[w].any():
        tags.append(("compression", "فشردگیِ رنج (انرژیِ متراکم برای حرکت)"))
    return tags


def _risk(z, rsi_last):
    """Deterministic risk rating for a zone (lower score = safer). Returns level+reasons.

    Risk = quality of the SETUP, not its distance from price. A good zone that is
    far away is still a good zone — you just wait for price to reach it (that is what
    `actionable_now`/`dist_atr` convey separately). So distance does NOT add risk here;
    otherwise every patient limit setup looks 'high risk' and the spread collapses."""
    score = 0; reasons = []
    if z["confidence"] == "LOW": score += 2; reasons.append("اعتمادِ پایین")
    elif z["confidence"] == "MEDIUM": score += 1
    if z["grade"] < 2: score += 1; reasons.append("کیفیتِ ناحیه زیرِ ۲")
    if z.get("room_R") is not None and z["room_R"] < 2:
        score += 1; reasons.append("فضای کم تا ناحیهٔ مقابل (<۲R)")
    if not z["with_trend"]: score += 2; reasons.append("بازگشتی/خلافِ روند — اغلب استاپ، اج از تارگتِ ۳R")
    if z.get("combo_confirmed"):                  # ≥2 independent styles agree → measured expR lift
        score -= 1; reasons.append("تأییدِ ترکیبی (≥۲ سبک هم‌جهت)")
    if rsi_last is not None:
        if z["dir"] == "LONG" and rsi_last < 35: score -= 1; reasons.append("RSI پایین — حمایتِ خرید")
        if z["dir"] == "LONG" and rsi_last > 70: score += 1; reasons.append("RSI بالا — خریدِ پرریسک")
        if z["dir"] == "SHORT" and rsi_last > 65: score -= 1; reasons.append("RSI بالا — حمایتِ فروش")
        if z["dir"] == "SHORT" and rsi_last < 30: score += 1; reasons.append("RSI پایین — فروشِ پرریسک")
    # thresholds (tunable via tuned.json) so the three buckets actually populate:
    #   کم   = a clean with-trend setup (HIGH, or MEDIUM with grade/room ok)
    #   متوسط = one weak leg (lower grade OR tight room OR MEDIUM tier)
    #   زیاد  = counter-trend, or LOW confidence, or several weak legs stacked
    t = TUNED()
    lo = t.get("risk_low_max", 1); md = t.get("risk_med_max", 3)
    level = "کم" if score <= lo else ("متوسط" if score <= md else "زیاد")
    return {"level": level, "score": score, "reasons": reasons}


def _zref(z):
    return {k: z.get(k) for k in ("action", "action_fa", "dir", "src", "confidence",
                                  "bot", "top", "entry", "sl", "tp1", "tp2", "tp3", "partial",
                                  "dist_atr", "room_R",
                                  "combo_score", "combo_confirmed", "combo_styles",
                                  "setup_type", "rev_target",
                                  "model_p_up", "model_agree", "model_against", "model_gate")} | {
        "risk_level": z["risk_rating"]["level"]}


def _verdict(zones, primary, price, atr, rstate, tf):
    """The headline answer: buy now / sell now / wait — so the user knows what to DO."""
    order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
    rank = {"کم": 0, "متوسط": 1, "زیاد": 2}
    # selectivity gate = the validated >=70%-winrate operating point: HTF zone + clear room +
    # no behavioural-model veto. Weaker zones still render for context but don't fire a signal.
    sel = TUNED().get("selectivity", {})
    rmin = float(sel.get("room_min", 2.0)); need_htf = bool(sel.get("require_htf", True))
    need_agree = bool(sel.get("require_model_agree", True))
    act_tfs = sel.get("actionable_tf", ["M5"])
    tf_ok = (not act_tfs) or (tf in act_tfs)   # operating point is M5-only; don't fire BUY_NOW off-regime
    def passes(z):
        if not (z["actionable_now"] and z["confidence"] != "LOW" and z["risk_rating"]["level"] != "زیاد"):
            return False
        if need_htf and not str(z["src"]).endswith("-1h"):
            return False
        # FAIL-CLOSED room: missing room is not a pass (it can't be claimed at the validated edge)
        if z.get("room_R") is None or z["room_R"] < rmin:
            return False
        if z.get("model_against"):
            return False
        # validated ~76% gate: require a CONFIDENT model agreement — FAIL-CLOSED (missing model ⇒ no fire)
        if need_agree and not z.get("model_agree"):
            return False
        return True
    ready = [z for z in zones if passes(z)] if tf_ok else []
    if ready:
        ready.sort(key=lambda z: (order[z["confidence"]], rank[z["risk_rating"]["level"]]))
        z = ready[0]
        state = "BUY_NOW" if z["action"] == "BUY" else "SELL_NOW"
        # The user asked NOT to be handed entry/stop automatically — only when they click the marked
        # zone. So the verdict announces the direction + which zone + risk, and tells them to click
        # the zone on the chart to reveal entry / stop / partial ladder. (Levels still ride on the
        # zone object for the click popover; we just don't print them in the headline.)
        txt = (f"یک ناحیهٔ {z['action_fa']} ({z['src']}) آماده است و قیمت کنار/داخلِ آن است. "
               f"ریسکِ این ناحیه: {z['risk_rating']['level']}. "
               f"برای دیدنِ ورود، استاپ و پله‌ها روی همین ناحیه روی چارت کلیک کن — اول تأییدیهٔ کندلی بگیر.")
        return {"state": state, "action": z["action"], "action_fa": z["action_fa"],
                "risk_level": z["risk_rating"]["level"], "zone": _zref(z), "text": txt}
    # no trend setup ready → is there a REVERSAL setup right at price? (the user's style,
    # backtested +0.143R with a 3R target). Surfaced instead of being suppressed as "LOW".
    rev_ready = ([z for z in zones if z["actionable_now"] and z.get("setup_type") == "reversal"]
                 if TUNED().get("reversal_enabled", True) else [])
    if rev_ready:
        rev_ready.sort(key=lambda z: z["dist_atr"])
        z = rev_ready[0]; e = TUNED().get("reversal_edge", REVERSAL_EDGE)
        state = "BUY_NOW" if z["action"] == "BUY" else "SELL_NOW"
        txt = (f"ستاپِ بازگشتی (مثلِ سبکِ خودت): قیمت به ناحیهٔ {z['action_fa']}ِ خلافِ روند رسیده. "
               f"ورود ~{z['entry']}، استاپ {z['sl']} (آن‌سویِ ناحیه)، هدفِ ۳R {z.get('rev_target')}. "
               f"این سبک اغلب استاپ می‌خورد (WR~{e['wr']}٪) ولی تارگتِ ۳R جبران می‌کند "
               f"(expR {e['expR']:+} در بک‌تست). کوچک ریسک کن و تأییدیهٔ کندلی بگیر.")
        return {"state": state, "action": z["action"], "action_fa": z["action_fa"],
                "reversal": True, "risk_level": z["risk_rating"]["level"],
                "zone": _zref(z), "text": txt}
    # nothing actionable right now → WAIT, point to the nearest zone
    cand = [z for z in zones if z.get("dist_atr") is not None]
    near = min(cand, key=lambda z: z["dist_atr"]) if cand else None
    if near:
        txt = (f"الان نه بخر نه بفروش. نزدیک‌ترین ناحیه: {near['action_fa']} در محدودهٔ "
               f"{near['bot']}–{near['top']} که حدود {near['dist_atr']} ATR دورتر است "
               f"(ریسک {near['risk_rating']['level']}). صبر کن قیمت به ناحیه برسد و واکنش/تأییدیه بدهد.")
        zref = _zref(near)
    else:
        txt = "الان ناحیهٔ فعالِ نزدیکی نیست. منتظرِ شکل‌گیریِ ستاپ بمان."
        zref = None
    if rstate.get("pull"):
        txt += " " + rstate.get("fa", "")
    return {"state": "WAIT", "action": None, "zone": zref, "text": txt}


def _stops(sym, src):
    """SL geometry from tuned.json (learned per source/symbol), falling back to defaults."""
    s = TUNED().get("stops", {})
    bufm = s.get("buf_atr", {}) if isinstance(s.get("buf_atr"), dict) else {}
    minm = s.get("min_atr", {}) if isinstance(s.get("min_atr"), dict) else {}
    buf = float(bufm.get(src, bufm.get("default", 0.3)))
    minStop = float(minm.get(sym, minm.get("default", 1.0)))
    return buf, minStop


def _plan(dr, zt, zb, price, atr, sym, src):
    """Build entry/SL/TP for a zone. Entry = proximal edge (limit). Emits both the R-grid
    (tp1=1R, tp2=2R, tp3=3R — for the chart/tests) AND the validated partial-exit ladder
    (`partial`): bank tp1_frac at tp1_R, move stop to break-even, runner to tp2_R."""
    buf, minStop = _stops(sym, src)
    if dr == 1:           # demand / long: proximal edge = top
        entry = zt
        sl = min(zb - buf * atr, entry - minStop * atr)
        risk = entry - sl
        if risk <= 0: return None
        sgn = 1
    else:                 # supply / short: proximal edge = bottom
        entry = zb
        sl = max(zt + buf * atr, entry + minStop * atr)
        risk = sl - entry
        if risk <= 0: return None
        sgn = -1
    tps = [round(entry + sgn * k * risk, 6) for k in (1, 2, 3)]
    # partial-exit ladder (the >=70%-winrate plan)
    pt = TUNED().get("partial", {})
    tp1R = float(pt.get("tp1_R", 0.5)); frac = float(pt.get("tp1_frac", 0.3333))
    runnerR = (float(pt.get("tp2_R", 2.0)) if pt.get("tp2_R") != "struct" else 2.0)
    move_be = bool(pt.get("move_be", True))
    partial = dict(
        scale_R=tp1R, scale_frac=round(frac, 4),
        scale_price=round(entry + sgn * tp1R * risk, 6),
        move_be=move_be, be_price=round(entry, 6),
        runner_R=runnerR, runner_tp=round(entry + sgn * runnerR * risk, 6),
        full_R=round(frac * tp1R + (1 - frac) * runnerR, 3))
    return dict(entry=round(entry, 6), sl=round(sl, 6),
                tp1=tps[0], tp2=tps[1], tp3=tps[2], risk=round(risk, 6),
                partial=partial,
                dist_atr=round(abs(price - entry) / atr, 2) if atr else None)


# source priority for de-overlap: H1 order-block > H1 flag-limit > M15 order-block
_SRC_RANK = {"OB-1h": 3, "FL-1h": 2, "OB-15m": 1}


def merge_overlapping(zones, price):
    """Keep zones NON-OVERLAPPING within each kind (demand/supply). Port of
    backtest/multizone.py: when two same-direction zones overlap, keep the one with the
    higher source rank (HTF first), then higher grade, then nearer to price, then wider.
    Optionally clamp to the nearest N per side. Toggleable via tuned.json `zones`."""
    zc = TUNED().get("zones", {})
    if not zc.get("merge", True):
        return zones

    def key(z):
        return (-_SRC_RANK.get(z["src"], 0), -int(z.get("grade") or 0),
                abs(price - z["entry"]), -(z["top"] - z["bot"]))

    kept = []
    for z in sorted(zones, key=key):
        if any(z["dir"] == k["dir"] and not (z["top"] < k["bot"] or z["bot"] > k["top"])
               for k in kept):
            continue
        kept.append(z)
    n = zc.get("max_per_side")
    if n:
        dem = sorted([z for z in kept if z["dir"] == "LONG"], key=lambda z: abs(price - z["entry"]))[:n]
        sup = sorted([z for z in kept if z["dir"] == "SHORT"], key=lambda z: abs(price - z["entry"]))[:n]
        kept = dem + sup
    return kept


def compute(sym, tf="M5"):
    F.refresh(sym)
    gold = F.SYMBOLS.get(sym, ("crypto", ""))[0].startswith("gold")  # "gold" (Yahoo) + "goldrt" (PAXG)
    D = B.prep_symbol(sym, tf)
    o, h, l, c, atr = D["o"], D["h"], D["l"], D["c"], D["atr"]
    price = float(c[-1]); a = _last(atr) or 0.0
    b1, b2, b3 = D["b1"][-1], D["b2"][-1], D["b3"][-1]
    biasv = int(np.sign(1 * b1 + 2 * b2 + 2 * b3))
    bias = {1: "صعودی", -1: "نزولی", 0: "خنثی"}[biasv]
    # discount/premium of current range
    pH, pL = _last(D["pH"]), _last(D["pL"])
    disc = prem = False
    if pH and pL and pH > pL:
        mid = pL + 0.5 * (pH - pL)
        disc = price < mid; prem = price > mid

    # ---- gather active zones ----
    zones = []
    _disabled = set(TUNED().get("stops", {}).get("disabled_sources", []))
    def add(kind, dr, zt, zb, g, src):
        if src in _disabled:                       # source auto-muted by stop-learning
            return
        if zt is None or zb is None or np.isnan(zt) or np.isnan(zb) or zt <= zb:
            return
        # drop MITIGATED zones (price already closed through them -> dead):
        #   demand is support -> invalid once price is below its bottom
        #   supply is resistance -> invalid once price is above its top
        if dr == 1 and price < zb:
            return
        if dr == -1 and price > zt:
            return
        plan = _plan(dr, zt, zb, price, a, sym, src)
        if not plan:
            return
        # room to opposing zone in R (use the same-source opposing edge if present)
        zones.append(dict(kind=kind, dir=("LONG" if dr == 1 else "SHORT"),
                          src=src, top=round(zt, 6), bot=round(zb, 6),
                          grade=int(g or 0), **plan))

    add("demand", 1, _last(D["c_demT"]), _last(D["c_demB"]), _last(D["c_demG"]), "OB-1h")
    add("supply", -1, _last(D["c_supT"]), _last(D["c_supB"]), _last(D["c_supG"]), "OB-1h")
    add("demand", 1, _last(D["a_demT"]), _last(D["a_demB"]), _last(D["a_demG"]), "OB-15m")
    add("supply", -1, _last(D["a_supT"]), _last(D["a_supB"]), _last(D["a_supG"]), "OB-15m")
    fdT, fdB, fdG, fsT, fsB, fsG = _fl_zone(sym)
    add("demand", 1, fdT, fdB, fdG, "FL-1h")
    add("supply", -1, fsT, fsB, fsG, "FL-1h")

    # de-overlap: never stack two same-kind zones (keep HTF/higher-grade/nearer/wider)
    zones = merge_overlapping(zones, price)

    # room (R to nearest opposing zone) + confidence tier per zone
    sup_bots = [z["bot"] for z in zones if z["dir"] == "SHORT" and z["bot"] > price]
    dem_tops = [z["top"] for z in zones if z["dir"] == "LONG" and z["top"] < price]
    for z in zones:
        dr = 1 if z["dir"] == "LONG" else -1
        risk = z["risk"]
        if dr == 1:
            opp = min([b for b in sup_bots if b > z["entry"]], default=None)
            room = (opp - z["entry"]) / risk if (opp and risk) else None
        else:
            opp = max([t for t in dem_tops if t < z["entry"]], default=None)
            room = (z["entry"] - opp) / risk if (opp and risk) else None
        z["room_R"] = round(room, 2) if room is not None else None
        wt = (dr == biasv) or (biasv == 0)
        z["with_trend"] = bool(dr == biasv)
        # confidence tier
        if z["src"] == "OB-1h" and z["grade"] >= 2 and wt and (room is None or room >= 2.0):
            z["confidence"] = "HIGH"
        elif z["src"].startswith(("OB", "FL")) and z["grade"] >= 1 and wt:
            z["confidence"] = "MEDIUM"
        else:
            z["confidence"] = "LOW"
        z["confluence"] = [{"key": k, "fa": v} for k, v in _confluence_tags(D, dr)]
        z["disc"] = bool(disc) if dr == 1 else None
        z["prem"] = bool(prem) if dr == -1 else None

    # ---- RSI (needed for risk rating + verdict + projection) ----
    n = len(c)
    rsi_arr = RT.rsi(c, 14)
    rsi_last = _last(rsi_arr)
    rstate = RT.rsi_state(rsi_last)
    tsec = (D["time"].view("int64") // 10**9).astype("int64")   # bar times in unix sec
    CHART = 400
    rsi_series = [{"time": int(tsec[k]), "value": round(float(rsi_arr[k]), 2)}
                  for k in range(max(0, n - CHART), n) if not np.isnan(rsi_arr[k])]
    divs = RT.divergences(h, l, c, rsi_arr, L=5, recent_bars=CHART)
    div_markers = [{"time": int(tsec[d["bar"]]), "type": d["type"],
                    "price": round(d["price"], 6), "rsi": d["rsi"], "fa": d["fa"]}
                   for d in divs if d["bar"] >= n - CHART]

    # ---- multi-style confluence (تلفیق) — measured to lift expR on real entries ----
    # Independent styles vote a direction; a zone with >=2 agreeing styles historically
    # raised expR from +0.261 to +0.317 on the proven 1h+grade2 setup (backtest/method_eval).
    div_vote = 0
    if divs:
        _d = divs[-1]
        if _d["bar"] >= n - 14:
            div_vote = 1 if _d["type"] == "bull" else -1
    dp_vote = 1 if disc else (-1 if prem else 0)
    rsi_vote = rstate.get("pull", 0)
    STYLES = [("RSI ۳۰/۷۰", rsi_vote), ("تخفیف/پریمیوم", dp_vote), ("واگرایی", div_vote)]

    # ---- behavioural future-prediction gate ----
    # The per-TF logistic (projection_model) votes a direction with an honest abstention band
    # (tau). A zone the model CONTRADICTS is demoted to LOW (won't fire BUY_NOW); a MEDIUM zone
    # the model AGREES with (with room) is promoted to HIGH. Legacy behaviour when no model.
    tfmin0 = B.TF_MIN.get(tf, 5)
    model = TUNED().get("projection_model")
    p_up = RT.proj_predict(model, c, atr, rsi_last, divs, biasv, tfmin0) if model else None
    _sel = TUNED().get("selectivity", {})
    _proj_tau = float(((model or {}).get(RT._proj_tf_key(tfmin0), {}) or {}).get("tau", 0.0))
    tau = max(_proj_tau, float(_sel.get("model_tau", 0.05)))   # confident-agreement band (validated 0.05)
    use_gate = _sel.get("use_model_gate", True)
    if p_up is not None and use_gate:
        for z in zones:
            pv = 1 if z["dir"] == "LONG" else -1
            agree = (p_up >= 0.5 + tau) if pv == 1 else (p_up <= 0.5 - tau)
            against = (p_up <= 0.5 - tau) if pv == 1 else (p_up >= 0.5 + tau)
            z["model_p_up"] = round(float(p_up), 4)
            z["model_agree"] = bool(agree)
            z["model_against"] = bool(against)
            if against:
                z["confidence"] = "LOW"; z["model_gate"] = "veto"
            elif agree and z["confidence"] == "MEDIUM" and z.get("with_trend") \
                    and (z.get("room_R") is None or z["room_R"] >= 2):
                z["confidence"] = "HIGH"; z["model_gate"] = "boost"
            else:
                z["model_gate"] = "neutral"

    # ---- per-zone action + actionable-now + confluence + risk ----
    for z in zones:
        z["action"] = "BUY" if z["dir"] == "LONG" else "SELL"
        z["action_fa"] = "خرید" if z["dir"] == "LONG" else "فروش"
        z["actionable_now"] = bool(z.get("dist_atr") is not None and z["dist_atr"] <= 1.5)
        dv = 1 if z["dir"] == "LONG" else -1
        agree = [name for name, v in STYLES if v == dv]
        z["combo_score"] = len(agree)            # 0..3 independent styles agreeing
        z["combo_styles"] = agree
        z["combo_confirmed"] = len(agree) >= TUNED().get("combo_min", 2)   # tunable threshold
        # setup type: with-trend = the proven 2R trend play; counter-trend = REVERSAL (user's
        # style) — backtested +0.143R with a 3R target (tp3). Recognised, not suppressed.
        z["setup_type"] = "trend" if z["with_trend"] else "reversal"
        if z["setup_type"] == "reversal":
            z["rev_target"] = z["tp3"]           # the validated 3R reversal target
            z["rev_edge"] = TUNED().get("reversal_edge", REVERSAL_EDGE)
        z["risk_rating"] = _risk(z, rsi_last)

    # primary = best zone, ranked by confidence then nearness
    order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
    ranked = sorted(zones, key=lambda z: (order[z["confidence"]], z.get("dist_atr") if z.get("dist_atr") is not None else 9e9))
    primary = ranked[0] if ranked else None

    # ---- dominance (crypto only) ----
    dom = None
    if not gold and DOM is not None:
        try: dom = DOM.get()
        except Exception: dom = None
    dom_bias = (dom or {}).get("crypto_bias", 0)

    # ---- trend projection (organic, HTF-weighted, dominance-aware) ----
    tfmin = B.TF_MIN.get(tf, 5)
    tfw = TF_WEIGHT.get(tf, 0.6)
    proj = RT.project(D["time"], c, atr, biasv, rsi_last, divs, primary, tfmin,
                      tf_weight=tfw, dom_bias=dom_bias, zones=zones, price=price,
                      model=TUNED().get("projection_model"), swing=TUNED().get("swing_model"),
                      trade=TUNED().get("swing_trade"), calib=TUNED().get("proj_calibration"))

    # ---- zones consistent with the projection: tag each zone hم‌جهت / خلافِ پیش‌بینی ----
    pdir = proj.get("dir_val", 0)
    for z in zones:
        zd = 1 if z["dir"] == "LONG" else -1
        z["proj_aligned"] = (pdir != 0 and zd == pdir)
        z["proj_against"] = (pdir != 0 and zd == -pdir)

    # ---- clear ACTION VERDICT: buy now / sell now / wait ----
    verdict = _verdict(zones, primary, price, a, rstate, tf)

    health = TF_HEALTH.get(tf, ("grey", "بک‌تست‌نشده"))
    return dict(symbol=sym, tf=tf, price=round(price, 6), atr=round(a, 6),
                bias=bias, bias_val=biasv, discount=bool(disc), premium=bool(prem),
                gold=gold, tf_health={"color": health[0], "note": health[1]},
                zones=zones, primary=primary, verdict=verdict,
                rsi={"last": round(rsi_last, 1) if rsi_last is not None else None,
                     "state": rstate["zone"], "state_fa": rstate["fa"], "series": rsi_series},
                divergences=div_markers, projection=proj, dominance=dom)


if __name__ == "__main__":
    import json
    s = sys.argv[1] if len(sys.argv) > 1 else "BTCUSDT"
    tf = sys.argv[2] if len(sys.argv) > 2 else "M5"
    print(json.dumps(compute(s, tf), ensure_ascii=False, indent=2))
