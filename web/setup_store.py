#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
setup_store.py — learn from the ACTUAL ZONES the system places.

Every analysis, each zone the system shows (entry / SL / TP2) is saved as a SETUP. On demand
("بررسی کن و آموزش بده") we replay the real forward price for each open setup and decide what
happened: price filled the entry and then hit the STOP (LOSS) or the 2R TARGET (WIN). Stops
are kept in memory. `lessons()` then aggregates which kinds of setups stop out most (by
confluence / confidence / source / direction) so the live signal can avoid repeating them.

A history backfill (`train_from_history`) seeds the same store with REAL past zone outcomes
from the validated engine (bt_structure.collect), so there are stop-lessons to learn from now.
"""
import os, sys, json, time
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "backtest")))

DIR = os.path.join(os.path.dirname(__file__), "learning")
os.makedirs(DIR, exist_ok=True)
SETUPS = os.path.join(DIR, "setups.jsonl")
MAXBARS = 400          # give a setup this many bars to fill + resolve before EXPIRING it
FEATURES = ["setup_type", "combo_confirmed", "confidence", "src", "dir", "with_trend"]
_TUNED_PATH = os.path.join(os.path.dirname(__file__), "tuned.json")


def _partial_cfg():
    """The deployed partial-exit ladder, read from tuned.json (so the live learning scores
    setups by the SAME rule the strategy trades). Defaults = the validated 0.5R/BE/2R."""
    try:
        p = (json.load(open(_TUNED_PATH, encoding="utf-8")) or {}).get("partial", {})
    except Exception:
        p = {}
    t2 = p.get("tp2_R", 2.0)
    return {"tp1_R": float(p.get("tp1_R", 0.5)), "tp1_frac": float(p.get("tp1_frac", 0.3333)),
            "tp2_R": float(t2) if t2 != "struct" else 2.0, "move_be": bool(p.get("move_be", True))}


def _sel_cfg():
    """Selectivity gate from tuned.json — the validated operating point's filters."""
    try:
        s = (json.load(open(_TUNED_PATH, encoding="utf-8")) or {}).get("selectivity", {})
    except Exception:
        s = {}
    return s


def _actionable(z, tf=None):
    """Does this zone match what the system actually RECOMMENDS — the validated ~76% operating
    point? FAIL-CLOSED so live signals can't sneak past the gate when a field is missing:
      • timeframe must be M5 (the ONLY validated TF for the partial-exit ladder; M15/H1 weren't),
      • HTF (OB-1h/FL-1h) with-trend zone,
      • clear room ≥ room_min R (missing room ⇒ NOT actionable),
      • behavioural model must CONFIDENTLY AGREE (missing/contradicting model ⇒ NOT actionable).
    The headline win-rate is measured on THESE — the trades the user is told to take, so a zone we
    can't fully verify is honestly excluded rather than counted as a 76% recommendation."""
    sel = _sel_cfg()
    tfs = sel.get("actionable_tf", ["M5"])
    if tf is not None and tfs and tf not in tfs:
        return False
    if not (str(z.get("src", "")).endswith("-1h") and z.get("with_trend")):
        return False
    if z.get("model_against"):
        return False
    room = z.get("room_R")
    rmin = float(sel.get("room_min", 2.0))
    if room is None or room < rmin:                       # fail-closed on room
        return False
    if sel.get("require_model_agree", True) and not z.get("model_agree"):   # fail-closed on model
        return False
    return True


def _read():
    if not os.path.exists(SETUPS):
        return []
    out = []
    for ln in open(SETUPS, encoding="utf-8"):
        ln = ln.strip()
        if ln:
            try: out.append(json.loads(ln))
            except Exception: pass
    return out


def _write(rows):
    with open(SETUPS, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def _key(symbol, tf, dr, src, entry):
    return f"{symbol}|{tf}|{dr}|{src}|{round(float(entry), 4)}"


# A level that just got stopped out keeps re-firing the same losing signal for a while (the live
# forward-test showed ONE level spawn 3 actionable shorts that ALL lost). Block an *actionable*
# re-entry at the same level for this long after it resolves as a LOSS — learn from the stop.
COOLDOWN_S = 6 * 3600


def record(sig, now=None):
    """Save each zone the system places. Deduped while OPEN, and an actionable re-entry is held
    off for COOLDOWN_S after the same level last stopped out (so a chop level can't rack up
    repeated losses in the headline)."""
    now = now or int(time.time())
    rows = _read()
    open_keys = {_key(r["symbol"], r["tf"], r["dir"], r["src"], r["entry"])
                 for r in rows if r["status"] == "OPEN"}
    # most-recent LOSS exit per level → cooldown window
    last_loss = {}
    for r in rows:
        if r.get("status") == "LOSS" and r.get("origin") == "live":
            k = _key(r["symbol"], r["tf"], r["dir"], r["src"], r["entry"])
            et = r.get("exit_ts") or r.get("ts") or 0
            if et > last_loss.get(k, 0):
                last_loss[k] = et
    added = 0
    for z in (sig.get("zones") or []):
        if z.get("entry") is None or z.get("sl") is None or z.get("tp2") is None:
            continue
        k = _key(sig["symbol"], sig["tf"], z["dir"], z["src"], z["entry"])
        if k in open_keys:
            continue
        act = _actionable(z, sig["tf"])
        if act and (now - last_loss.get(k, 0)) < COOLDOWN_S:   # recently stopped here → don't recommend again yet
            act = False
        rows.append({
            "ts": now, "symbol": sig["symbol"], "tf": sig["tf"], "dir": z["dir"],
            "src": z["src"], "grade": z.get("grade"), "confidence": z.get("confidence"),
            "combo_score": z.get("combo_score"), "combo_confirmed": bool(z.get("combo_confirmed")),
            "with_trend": bool(z.get("with_trend")), "setup_type": z.get("setup_type"),
            "entry": z["entry"], "sl": z["sl"], "tp2": z["tp2"], "tp3": z.get("tp3"),
            "risk": z.get("risk"), "actionable": act,
            # persist the gate inputs so live setups stay AUDITABLE (the old rows lost these → the
            # headline couldn't tell a real 76% trade from an un-verified one).
            "room_R": z.get("room_R"), "model_p_up": z.get("model_p_up"),
            "model_agree": z.get("model_agree"), "model_against": z.get("model_against"),
            "status": "OPEN", "R": None, "exit_ts": None, "origin": "live",
        })
        open_keys.add(k); added += 1
    if added:
        _write(rows)
    return added


def _simulate(s, t, h, l, c):
    """Replay forward OHLC under the DEPLOYED partial+BE ladder (the rule the backtest validated
    at ~70% net-win): fill entry → bank tp1_frac at tp1_R → move stop to break-even → runner to
    tp2_R. Tracks MFE/MAE (R) + bars-to-stop so we LEARN FROM THE STOPS we hit.
    Returns (status, R, exit_ts, mfe_R, mae_R, bars_to_stop); status ∈ WIN/PARTIAL_WIN/LOSS."""
    dr = 1 if s["dir"] == "LONG" else -1
    entry, sl = s["entry"], s["sl"]
    risk = s.get("risk") or abs(entry - sl) or None
    if not risk:
        return None
    p = _partial_cfg()
    tp1R, frac, runR, move_be = p["tp1_R"], p["tp1_frac"], p["tp2_R"], p["move_be"]
    filled = False; mfe = 0.0; mae = 0.0; fbar = 0; stage = 0
    for j in range(min(len(t), MAXBARS)):
        if not filled:
            hit_entry = (l[j] <= entry) if dr == 1 else (h[j] >= entry)
            if not hit_entry:
                continue
            filled = True; fbar = j
        fav = ((h[j] - entry) / risk) if dr == 1 else ((entry - l[j]) / risk)
        adv = ((entry - l[j]) / risk) if dr == 1 else ((h[j] - entry) / risk)
        mfe = max(mfe, fav); mae = max(mae, adv)
        sl_hit = (l[j] <= sl) if dr == 1 else (h[j] >= sl)
        if stage == 1:                              # runner: stop moved to break-even (entry)
            be_hit = (l[j] <= entry) if dr == 1 else (h[j] >= entry)
            stop_active = be_hit if move_be else sl_hit
            if fav >= runR and not stop_active:     # runner reached target = full win
                return ("WIN", round(frac * tp1R + (1 - frac) * runR, 3), int(t[j]), round(mfe, 2), round(mae, 2), None)
            if stop_active:                         # banked the partial, runner stopped at BE
                return ("PARTIAL_WIN", round(frac * tp1R, 3), int(t[j]), round(mfe, 2), round(mae, 2), None)
        else:                                       # pre-partial: original stop active
            if sl_hit:                              # conservative: stop wins same-bar tie
                return ("LOSS", -1.0, int(t[j]), round(mfe, 2), round(mae, 2), j - fbar)
            if fav >= tp1R:
                stage = 1                           # banked tp1; BE active from next bar
    if not filled and len(t) >= MAXBARS:
        return ("EXPIRED", None, int(t[min(len(t), MAXBARS) - 1]), 0.0, 0.0, None)
    if stage == 1:                                  # runner unresolved -> count the banked partial
        return ("PARTIAL_WIN", round(frac * tp1R, 3), int(t[-1]), round(mfe, 2), round(mae, 2), None)
    return None


def resolve(now=None):
    """Resolve OPEN live setups against real forward price. Returns counts."""
    import live_feed as F
    now = now or int(time.time())
    rows = _read()
    changed = wins = losses = 0
    for s in rows:
        if s["status"] != "OPEN":
            continue
        fp = F.forward_path(s["symbol"], s["tf"], s["ts"])
        if fp is None or len(fp[0]) == 0:
            continue
        out = _simulate(s, *fp)
        if out is None:
            continue
        s["status"], s["R"], s["exit_ts"], s["mfe_R"], s["mae_R"], s["bars_to_stop"] = out
        changed += 1
        wins += s["status"] in ("WIN", "PARTIAL_WIN"); losses += s["status"] == "LOSS"
    if changed:
        _write(rows)
    return {"resolved": changed, "wins": wins, "losses": losses}


def _combo_votes(D):
    """Confluence count per bar (mirrors signal_service combo) for history backfill."""
    import numpy as np, rsi_tools as RT
    c, h, l = D["c"], D["h"], D["l"]
    n = len(c)
    rsi = RT.rsi(c, 14)
    rp = np.where(rsi < 30, 1.0, np.where(rsi > 70, -1.0, 0.0))
    pH, pL = D["pH"], D["pL"]
    dp = np.zeros(n)
    with np.errstate(invalid="ignore"):
        mid = pL + 0.5 * (pH - pL); ok = np.isfinite(mid) & (pH > pL)
        dp[ok & (c < mid)] = 1.0; dp[ok & (c > mid)] = -1.0
    dv = np.zeros(n)
    try:
        for d in RT.divergences(h, l, c, rsi, L=5, recent_bars=n):
            b = d["bar"]
            start = b + 5   # = b + L; pivot only confirmable here (no 5-bar look-ahead)
            dv[start:min(n, start + 12)] = 1.0 if d["type"] == "bull" else -1.0
    except Exception:
        pass
    return np.vstack([rp, dp, dv])


def train_from_history(symbols=None, tf="M5"):
    """Seed the store with REAL past zone outcomes (incl. stops) from the validated engine,
    scored under the DEPLOYED strategy: HTF zones + clear-room gate + partial 0.5R→BE→2R
    (the 10-agent operating point). These are the trades the system actually recommends."""
    pc = _partial_cfg(); sc = _sel_cfg()                  # train under the SAME ladder/gate the strategy trades
    os.environ["FD_SRC"] = "1h"; os.environ["FD_CONF"] = "room"
    os.environ["FD_PARTIAL"] = "1"; os.environ["FD_TP1"] = str(pc["tp1_R"]); os.environ["FD_RUNNER"] = str(pc["tp2_R"])
    os.environ["FD_TP1FRAC"] = str(pc["tp1_frac"]); os.environ["FD_BE"] = "1" if pc["move_be"] else "0"
    os.environ["FD_MODEL"] = "agree"; os.environ["FD_MTAU"] = str(sc.get("model_tau", 0.05))   # confident model agreement
    import importlib, numpy as np
    import rtm_bt as B, bt_structure as BS
    importlib.reload(BS)
    SRC = symbols or ["BTCUSDT", "XRPUSDT", "ETHUSD", "SOLUSD", "BNBUSD", "ADAUSD",
                      "AVAXUSD", "LINKUSD", "LTCUSD", "DOTUSD", "ATOMUSD", "DOGEUSD", "XAUUSD"]
    rows = [r for r in _read() if r.get("origin") != "train"]   # refresh training rows
    # existing live rows were tagged 'actionable' under an OLDER gate and lack the model fields to
    # re-verify, so clear the stale tag here; new live setups get tagged under the current gate.
    for r in rows:
        if r.get("origin") == "live":
            r["actionable"] = False
    added = 0
    for sym in SRC:
        try:
            D = B.prep_symbol(sym, tf)
        except Exception as e:
            print(f"  skip {sym}: {str(e)[:50]}"); continue
        recs = BS.collect(D, sym, "london" if sym == "XAUUSD" else "ny")
        styles = _combo_votes(D)
        for r in recs:
            Rp = r.get("R_partbe")              # realized R under the partial+BE ladder
            if Rp is None:
                continue
            bar, dr = r["i"], r["dir"]
            combo = int(np.sum(styles[:, bar] == dr))
            rows.append({
                "ts": int(D["time"][bar].value // 10**9), "symbol": sym, "tf": tf,
                "dir": "LONG" if dr == 1 else "SHORT", "src": r["type"],
                "grade": r.get("grade"), "confidence": None,
                "combo_score": combo, "combo_confirmed": combo >= 2,
                "with_trend": bool(r.get("withtrend")),
                "setup_type": "trend" if r.get("withtrend") else "reversal",
                "entry": None, "sl": None, "tp2": None, "risk": r.get("risk"),
                "actionable": True,            # room-gated HTF = a recommended setup
                "status": r.get("partstat") or ("WIN" if Rp > 0 else "LOSS"), "R": float(Rp),
                "mfe_R": r.get("mfe"), "mae_R": r.get("mae"), "bars_to_stop": r.get("bars_to_stop"),
                "exit_ts": None, "origin": "train",
            })
            added += 1
        print(f"  {sym:9} +{len([1 for r in recs if r.get('R_partbe') is not None]):4} resolved setups")
    _write(rows)
    return added


def _agg(rows):
    n = len(rows)
    if n == 0:
        return None
    wins = sum(1 for r in rows if (r.get("R") or 0) > 0)   # net win incl. partial (R>0)
    Rs = [r["R"] for r in rows if r.get("R") is not None]
    expR = round(sum(Rs) / len(Rs), 3) if Rs else None
    return {"n": n, "stops": n - wins, "win_rate": round(100 * wins / n, 1),
            "stop_rate": round(100 * (n - wins) / n, 1), "expR": expR}


def lessons():
    """What the system has learned — the HEADLINE is the strategy it actually trades: the
    recommended (actionable) setups scored under the partial+BE rule. A separate `all` keeps
    the raw every-zone number for transparency."""
    rows = [r for r in _read() if r["status"] in ("WIN", "PARTIAL_WIN", "LOSS")]
    act = [r for r in rows if r.get("actionable")]
    base = act if len(act) >= 20 else rows        # fall back to all until enough recommended setups
    out = {"overall": _agg(base), "all": _agg(rows),
           "n_actionable": len(act), "is_strategy": len(act) >= 20, "by": {}}
    for f in FEATURES:
        groups = {}
        for r in base:
            v = r.get(f)
            if v is None:
                continue
            groups.setdefault(str(v), []).append(r)
        out["by"][f] = {k: _agg(v) for k, v in groups.items() if len(v) >= 20}
    return out


def _median(xs):
    xs = sorted(xs)
    n = len(xs)
    if n == 0: return None
    return xs[n // 2] if n % 2 else round((xs[n // 2 - 1] + xs[n // 2]) / 2, 3)


def _agg_stops(rows):
    """Stop diagnostics for a group: stop-rate, plus WHY the stops happened —
    sweep_frac (stopped at MFE<0.3R = stop too tight / liquidity sweep) vs
    giveback_frac (reached >=1R then reversed = stop too loose / exit too late)."""
    rows = [r for r in rows if r["status"] in ("WIN", "LOSS") and r.get("mfe_R") is not None]
    n = len(rows)
    if n < 1: return None
    stops = [r for r in rows if r["status"] == "LOSS"]
    ns = len(stops)
    sweep = [r for r in stops if r["mfe_R"] < 0.3]
    gb = [r for r in stops if r["mfe_R"] >= 1.0]
    Rs = [r["R"] for r in rows if r.get("R") is not None]
    return {"n": n, "stops": ns, "stop_rate": round(100 * ns / n, 1),
            "sweep_frac": round(len(sweep) / ns, 3) if ns else 0.0,
            "giveback_frac": round(len(gb) / ns, 3) if ns else 0.0,
            "median_mfe_stop": _median([r["mfe_R"] for r in stops]),
            "expR": round(sum(Rs) / len(Rs), 3) if Rs else None}


def lessons_stops(min_n=20):
    """Learn FROM THE STOPS: per source / symbol / combo, are stops sweeps (too tight) or
    givebacks (too loose)? optimize.run() reads this to widen/tighten stops.* per source."""
    rows = [r for r in _read() if r["status"] in ("WIN", "LOSS") and r.get("mfe_R") is not None]
    out = {"overall": _agg_stops(rows), "by_src": {}, "by_symbol": {}, "by_combo": {}}
    for key, field in (("by_src", "src"), ("by_symbol", "symbol"), ("by_combo", "combo_confirmed")):
        groups = {}
        for r in rows:
            v = r.get(field)
            if v is None: continue
            groups.setdefault(str(v), []).append(r)
        out[key] = {k: _agg_stops(v) for k, v in groups.items() if len(v) >= min_n}
    return out


def recent_stops(k=10):
    losses = [r for r in _read() if r["status"] == "LOSS"]
    losses.sort(key=lambda r: r.get("exit_ts") or r["ts"], reverse=True)
    return losses[:k]


def annotate(sig):
    """Attach the learned stop-rate for setups like the primary zone, so the app can warn."""
    p = sig.get("primary")
    les = lessons()
    if p is not None and les["overall"]:
        cc = str(bool(p.get("combo_confirmed")))
        grp = les["by"].get("combo_confirmed", {}).get(cc)
        p["learned_stop_rate"] = grp["stop_rate"] if grp else les["overall"]["stop_rate"]
        p["learned_expR"] = (grp or les["overall"]).get("expR")
        p["learned_setups_n"] = (grp or les["overall"])["n"]
    sig["setup_lessons"] = les
    return sig


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "lessons"
    if cmd == "train":
        print("Seeding setup store from real history…")
        print("added", train_from_history(), "resolved setups")
    elif cmd == "resolve":
        print(resolve())
    print(json.dumps(lessons(), ensure_ascii=False, indent=2))
