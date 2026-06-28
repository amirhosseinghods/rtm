#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
RSI (Wilder) + regular RSI/price divergence + an honest trend projection.

Divergence is a CONFLUENCE signal (like the RTM concept tags): it is shown on the
chart and cited by the assistant, but it does NOT change the validated confidence
tier on its own. The projection is an explicitly-labelled hypothesis, never a promise.
"""
import numpy as np

try:
    from rtm_bt import pivots
except Exception:
    from backtest.rtm_bt import pivots


def rsi(close, n=14):
    """Wilder's RSI. Returns an array aligned to `close` (NaN until warmed up)."""
    c = np.asarray(close, float)
    out = np.full(len(c), np.nan)
    if len(c) <= n:
        return out
    d = np.diff(c)
    gain = np.where(d > 0, d, 0.0)
    loss = np.where(d < 0, -d, 0.0)
    ag = gain[:n].mean(); al = loss[:n].mean()
    rs = (ag / al) if al > 0 else np.inf
    out[n] = 100 - 100 / (1 + rs)
    for i in range(n + 1, len(c)):
        ag = (ag * (n - 1) + gain[i - 1]) / n
        al = (al * (n - 1) + loss[i - 1]) / n
        rs = (ag / al) if al > 0 else np.inf
        out[i] = 100 - 100 / (1 + rs)
    return out


def _pivot_points(h, l, rsi_arr, L=5):
    """Confirmed swing highs/lows with the RSI value sampled at the pivot bar.
    Returns (highs, lows) as lists of dicts {bar, price, rsi}."""
    ph, pl = pivots(h, l, L, L)
    highs, lows = [], []
    n = len(h)
    for conf in range(n):
        if not np.isnan(ph[conf]):
            b = conf - L
            if 0 <= b < n and not np.isnan(rsi_arr[b]):
                highs.append({"bar": b, "price": float(ph[conf]), "rsi": float(rsi_arr[b])})
        if not np.isnan(pl[conf]):
            b = conf - L
            if 0 <= b < n and not np.isnan(rsi_arr[b]):
                lows.append({"bar": b, "price": float(pl[conf]), "rsi": float(rsi_arr[b])})
    return highs, lows


def divergences(h, l, c, rsi_arr, L=5, recent_bars=160, max_gap=80):
    """Regular RSI divergence between two consecutive same-type swings.
      bullish: price lower-low  but RSI higher-low   (reversal up potential)
      bearish: price higher-high but RSI lower-high  (reversal down potential)
    Only divergences whose second pivot is within `recent_bars` of the end are returned."""
    n = len(c)
    highs, lows = _pivot_points(h, l, rsi_arr, L)
    out = []
    for seq, kind in ((lows, "bull"), (highs, "bear")):
        for a, b in zip(seq, seq[1:]):
            if b["bar"] - a["bar"] > max_gap:
                continue
            if b["bar"] < n - recent_bars:
                continue
            if kind == "bull" and b["price"] < a["price"] and b["rsi"] > a["rsi"] + 1:
                out.append({"type": "bull", "bar": b["bar"], "price": b["price"],
                            "rsi": round(b["rsi"], 1),
                            "fa": "واگراییِ صعودی (RSI) — کفِ پایین‌تر اما RSI بالاتر"})
            if kind == "bear" and b["price"] > a["price"] and b["rsi"] < a["rsi"] - 1:
                out.append({"type": "bear", "bar": b["bar"], "price": b["price"],
                            "rsi": round(b["rsi"], 1),
                            "fa": "واگراییِ نزولی (RSI) — سقفِ بالاتر اما RSI پایین‌تر"})
    out.sort(key=lambda d: d["bar"])
    return out


def rsi_state(rsi_last):
    """Overbought/oversold classification. <30 → expect bounce UP, >70 → expect drop."""
    if rsi_last is None or np.isnan(rsi_last):
        return {"zone": "نامشخص", "pull": 0, "fa": ""}
    if rsi_last < 30:
        return {"zone": "اشباعِ فروش (<۳۰)", "pull": +1,
                "fa": "RSI زیرِ ۳۰ — اشباعِ فروش؛ انتظارِ جمع‌شدنِ سفارش و برگشتِ صعودی."}
    if rsi_last > 70:
        return {"zone": "اشباعِ خرید (>۷۰)", "pull": -1,
                "fa": "RSI بالای ۷۰ — اشباعِ خرید؛ انتظارِ تخلیه و برگشتِ نزولی."}
    return {"zone": "خنثی", "pull": 0, "fa": ""}


def _bounce_decision(zone, into_dir, rsi_last):
    """When the projected path reaches `zone` moving in `into_dir` (+1 up / -1 down),
    decide deterministically whether price REACTS off it (bounce/return) or BREAKS through.
    Returns (will_bounce: bool, score: int, reason: str). A zone only opposes the path if
    price is rising into a SUPPLY (resistance) or falling into a DEMAND (support)."""
    zdir = 1 if zone["dir"] == "LONG" else -1     # +1 demand/support, -1 supply/resistance
    opposes = (into_dir > 0 and zdir < 0) or (into_dir < 0 and zdir > 0)
    if not opposes:
        return (False, 0, "هم‌جهت با ناحیه — عبور")
    s = 0; why = []
    g = zone.get("grade", 0) or 0
    if g >= 2: s += 2; why.append("ناحیهٔ باکیفیت (g≥۲)")
    elif g >= 1: s += 1
    cf = zone.get("confidence")
    if cf == "HIGH": s += 2; why.append("اعتمادِ بالا")
    elif cf == "MEDIUM": s += 1
    if zone.get("with_trend"): s += 1; why.append("هم‌جهت با روندِ HTF")
    if zone.get("room_R") is not None and zone["room_R"] >= 2: s += 1
    # RSI extreme that supports the reaction (bounce direction = -into_dir)
    if rsi_last is not None:
        if into_dir > 0 and rsi_last > 65: s += 1; why.append("RSI اشباعِ خرید")
        if into_dir < 0 and rsi_last < 35: s += 1; why.append("RSI اشباعِ فروش")
    will = s >= 3
    reason = ("واکنش/برگشت محتمل — " + "، ".join(why)) if will else \
             "ناحیهٔ ضعیف — احتمالِ شکست و ادامه"
    return (will, s, reason)


def _proj_tf_key(tf_minutes):
    return {5: "M5", 15: "M15", 60: "H1"}.get(int(tf_minutes))


def proj_features(c, atr, rsi_last, divs, bias_val, tf_minutes):
    """The 8 causal features the fitted projection model (idea-2) consumes, at the LAST bar.
    MUST mirror backtest/exp_idea-2.py:feature_matrix exactly (order = FEAT_NAMES there)."""
    c = np.asarray(c, float); n = len(c)
    if n < 22 or rsi_last is None or not np.isfinite(rsi_last):
        return None
    slope_sign = float(np.sign(c[-1] - c[-1 - 20]))
    rsi_pull = 1.0 if rsi_last < 30 else (-1.0 if rsi_last > 70 else 0.0)
    rsi_z = (float(rsi_last) - 50.0) / 15.0
    # div at the last bar. Mirror the ARRAY-OVERWRITE precedence of exp_idea-2.feature_matrix /
    # build_calls (div[b+L:b+L+12] = v): every CONFIRMED divergence whose active window covers the
    # last bar writes div, and the LAST such one in list order wins (NOT the most-recent pivot).
    div = 0.0
    if divs:
        for d in divs:
            b = d.get("bar")
            if b is None:
                continue
            start = b + 5
            if start <= n - 1 < start + 12:
                div = 1.0 if d.get("type") == "bull" else -1.0
    a = float(atr[-1]) if (atr is not None and len(atr) and np.isfinite(atr[-1]) and atr[-1] > 0) else 0.0
    ts = min(5.0, abs(c[-1] - c[-1 - 20]) / a) if a > 0 else 0.0
    is_h1 = 1.0 if int(tf_minutes) == 60 else 0.0
    return [float(bias_val), slope_sign, rsi_pull, rsi_z, div, ts, ts * slope_sign, rsi_z * is_h1]


def proj_predict(model, c, atr, rsi_last, divs, bias_val, tf_minutes):
    """P(up) from the fitted per-TF logistic in tuned.json projection_model, or None to fall back."""
    if not model:
        return None
    m = model.get(_proj_tf_key(tf_minutes))
    if not m or "weights" not in m:
        return None
    feats = proj_features(c, atr, rsi_last, divs, bias_val, tf_minutes)
    if feats is None:
        return None
    w = m["weights"]
    if len(w) != len(feats):
        return None
    z = float(m.get("intercept", 0.0)) + sum(wi * fi for wi, fi in zip(w, feats))
    return 1.0 / (1.0 + np.exp(-max(-30.0, min(30.0, z))))


def project(time, c, atr, bias_val, rsi_last, divs, primary, tf_minutes,
            tf_weight=1.0, dom_bias=0, zones=None, price=None, n_future=48, model=None):
    """Honest, ZONE-AWARE directional projection (a hypothesis, not a promise), drawn as an
    ORGANIC wavy path. The path runs toward the nearest opposing zone; on contact it either
    REACTS (bounces/returns) or BREAKS through and continues — decided by zone strength
    (grade/confidence/with-trend/room) + RSI. Up to two such events are drawn so the user
    sees the 'reach → react-or-break → continue' story. RSI 30/70 and divergence count more
    on higher timeframes (tf_weight). Returns label, future {time,value} points, and the
    contact `events` (type/time/price/zone/reason) plus a Persian `scenario`."""
    import pandas as pd
    c = np.asarray(c, float); n = len(c)
    K = min(20, n - 1)
    slope = (c[-1] - c[-1 - K]) / K if K > 0 else 0.0
    a = float(atr[-1]) if not np.isnan(atr[-1]) else (abs(slope) or 1.0)
    base = float(c[-1]); price = float(price) if price is not None else base

    rs = rsi_state(rsi_last)
    recent_div = divs[-1] if divs else None
    div_pull = 0
    if recent_div and recent_div["bar"] >= n - 14:
        div_pull = 1 if recent_div["type"] == "bull" else -1

    # weighted directional score (positive = up)
    contrib = []; score = 0.0
    if bias_val != 0:
        score += 1.0 * bias_val; contrib.append(("سوگیریِ HTF", bias_val))
    if abs(slope) > 1e-9:
        score += 0.4 * np.sign(slope)
    if rs["pull"]:
        score += 0.9 * tf_weight * rs["pull"]; contrib.append(("RSI اشباع", rs["pull"]))
    if div_pull:
        score += 0.7 * tf_weight * div_pull; contrib.append(("واگراییِ تازه", div_pull))
    if primary:
        pv = 1 if primary["dir"] == "LONG" else -1
        if primary["confidence"] in ("HIGH", "MEDIUM"):
            score += 0.6 * pv; contrib.append(("ستاپِ فعال", pv))
    if dom_bias:
        score += 0.6 * dom_bias; contrib.append(("دامیننسِ تتر", dom_bias))

    dirn = int(np.sign(score)) if abs(score) > 1e-9 else (bias_val or int(np.sign(slope)))
    if dirn == 0: dirn = 1
    conf = max(0.12, min(0.9, 0.45 + 0.13 * abs(score)))
    # fitted projection model (idea-2): re-weighted direction + honest abstention. Opt-in via
    # tuned.json `projection_model` (passed as `model`); validated out-of-time (walkforward_idea2).
    # Falls back to the legacy hand-tuned score above when no model is supplied.
    p_up = proj_predict(model, c, atr, rsi_last, divs, bias_val, tf_minutes)
    if p_up is not None:
        m = model.get(_proj_tf_key(tf_minutes), {})
        tau = float(m.get("tau", 0.0))
        dirn = 0 if abs(p_up - 0.5) < tau else (1 if p_up >= 0.5 else -1)
        conf = max(0.12, min(0.9, p_up if p_up >= 0.5 else 1.0 - p_up))
    label = {1: "صعودی", -1: "نزولی", 0: "خنثی/رنج"}[dirn if dirn in (-1, 0, 1) else 0]

    note = []
    for name, sgn in contrib:
        note.append(f"{name} ({'هم‌جهت' if sgn == dirn else 'مخالف'})")

    # ---- zone targets ahead of price, sorted by how soon the path meets them ----
    zlist = []
    for z in (zones or []):
        zb, zt = z.get("bot"), z.get("top")
        if zb is None or zt is None: continue
        # proximal edge price meets first: supply (resistance) from below = its bottom;
        # demand (support) from above = its top.
        edge = zb if z["dir"] == "SHORT" else zt
        zlist.append({**z, "edge": float(edge), "bot": float(zb), "top": float(zt)})

    last_t = time[-1]
    amp = 0.42 * a
    pts = []; events = []
    cur_dir = dirn; cur_conf = conf
    drift = cur_dir * cur_conf * 0.16 * a
    hit_ids = set()
    base_leg = base       # absolute anchor of the current leg
    k0 = 0                # bar index where the current leg started (decay/phase reset on events)

    def next_zone(p, d):
        """Nearest opposing zone the path will reach traveling in direction d from price p."""
        best = None
        for i, z in enumerate(zlist):
            if i in hit_ids: continue
            zdir = 1 if z["dir"] == "LONG" else -1
            opp = (d > 0 and zdir < 0) or (d < 0 and zdir > 0)
            if not opp: continue
            if d > 0 and z["edge"] > p + 0.05 * a:
                dist = z["edge"] - p
            elif d < 0 and z["edge"] < p - 0.05 * a:
                dist = p - z["edge"]
            else:
                continue
            if best is None or dist < best[1]:
                best = (i, dist)
        return best

    target = next_zone(base_leg, cur_dir)
    for k in range(1, n_future + 1):
        kk = k - k0                                 # bars since the current leg started
        decay = np.exp(-0.018 * kk)
        wave = amp * decay * (0.6 * np.sin(kk * 0.55) + 0.4 * np.sin(kk * 0.23 + 1.3))
        px = base_leg + drift * kk * (1.0 - 0.01 * kk) + wave   # absolute per-leg (stays wavy)
        ft = last_t + pd.Timedelta(minutes=tf_minutes * k)
        tsec = int(ft.value // 10**9)
        # did we reach the target zone's proximal edge on this bar?
        if target is not None and len(events) < 2:
            zi, _ = target; z = zlist[zi]
            reached = (cur_dir > 0 and px >= z["edge"]) or (cur_dir < 0 and px <= z["edge"])
            if reached:
                px = z["edge"]                      # snap to the edge (contact)
                will, sc, reason = _bounce_decision(z, cur_dir, rsi_last)
                hit_ids.add(zi)
                etype = "bounce" if will else "break"
                events.append({"type": etype, "time": tsec, "price": round(z["edge"], 6),
                               "zone": {"src": z.get("src"), "dir": z["dir"],
                                        "bot": z["bot"], "top": z["top"],
                                        "action_fa": "خرید" if z["dir"] == "LONG" else "فروش"},
                               "score": sc, "reason": reason})
                if will:                            # REACT: reverse, dampen, anchor at the edge
                    cur_dir = -cur_dir; cur_conf = max(0.18, cur_conf * 0.8)
                    base_leg = z["edge"]
                else:                               # BREAK: push through the zone, slight accel
                    cur_conf = min(0.9, cur_conf * 1.1)
                    base_leg = z["top"] + 0.15 * a if cur_dir > 0 else z["bot"] - 0.15 * a
                    px = base_leg
                drift = cur_dir * cur_conf * 0.16 * a
                k0 = k
                target = next_zone(base_leg, cur_dir)
        pts.append({"time": tsec, "value": round(float(px), 6)})

    # ---- Persian scenario narrative ----
    scenario = f"سناریو: مسیرِ کلی {label}."
    if events:
        bits = []
        for ev in events:
            z = ev["zone"]
            verb = "برخورد و واکنش/برگشت" if ev["type"] == "bounce" else "برخورد و شکست/عبور"
            bits.append(f"به ناحیهٔ {z['action_fa']} ({z['bot']}–{z['top']}) می‌رسد → "
                        f"{verb} ({ev['reason']})")
        scenario = "سناریو: " + " سپس ".join(bits) + "."
    else:
        scenario += " در این بازه به ناحیهٔ مخالفِ مشخصی نمی‌رسد."

    return {"dir": label, "dir_val": int(dirn), "confidence": round(conf, 2),
            "notes": note, "rsi_state": rs["zone"], "points": pts,
            "events": events, "scenario": scenario}
