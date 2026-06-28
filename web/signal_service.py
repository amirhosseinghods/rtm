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
                                  "bot", "top", "entry", "sl", "tp2", "tp3", "dist_atr", "room_R",
                                  "combo_score", "combo_confirmed", "combo_styles",
                                  "setup_type", "rev_target")} | {
        "risk_level": z["risk_rating"]["level"]}


def _verdict(zones, primary, price, atr, rstate, tf):
    """The headline answer: buy now / sell now / wait — so the user knows what to DO."""
    order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
    rank = {"کم": 0, "متوسط": 1, "زیاد": 2}
    ready = [z for z in zones if z["actionable_now"] and z["confidence"] != "LOW"
             and z["risk_rating"]["level"] != "زیاد"]
    if ready:
        ready.sort(key=lambda z: (order[z["confidence"]], rank[z["risk_rating"]["level"]]))
        z = ready[0]
        state = "BUY_NOW" if z["action"] == "BUY" else "SELL_NOW"
        txt = (f"الان قیمت داخل/کنارِ ناحیهٔ {z['action_fa']} است — آمادهٔ ورود. "
               f"ورود ~{z['entry']}، استاپ {z['sl']}، هدفِ اصلی TP2 {z['tp2']}. "
               f"ریسکِ این ناحیه: {z['risk_rating']['level']}. اول تأییدیهٔ کندلی بگیر.")
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


def _plan(dr, zt, zb, price, atr, gold):
    """Build entry/SL/TP (R-grid) for a zone. Entry = proximal edge (limit)."""
    buf = 0.3
    minStop = 2.5 if gold else 1.0
    if dr == 1:           # demand / long: proximal edge = top
        entry = zt
        sl = min(zb - buf * atr, entry - minStop * atr)
        risk = entry - sl
        if risk <= 0: return None
        tps = [round(entry + k * risk, 6) for k in (1, 2, 3)]
    else:                 # supply / short: proximal edge = bottom
        entry = zb
        sl = max(zt + buf * atr, entry + minStop * atr)
        risk = sl - entry
        if risk <= 0: return None
        tps = [round(entry - k * risk, 6) for k in (1, 2, 3)]
    return dict(entry=round(entry, 6), sl=round(sl, 6),
                tp1=tps[0], tp2=tps[1], tp3=tps[2], risk=round(risk, 6),
                dist_atr=round(abs(price - entry) / atr, 2) if atr else None)


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
    def add(kind, dr, zt, zb, g, src):
        if zt is None or zb is None or np.isnan(zt) or np.isnan(zb) or zt <= zb:
            return
        # drop MITIGATED zones (price already closed through them -> dead):
        #   demand is support -> invalid once price is below its bottom
        #   supply is resistance -> invalid once price is above its top
        if dr == 1 and price < zb:
            return
        if dr == -1 and price > zt:
            return
        plan = _plan(dr, zt, zb, price, a, gold)
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
                      tf_weight=tfw, dom_bias=dom_bias, zones=zones, price=price)

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
