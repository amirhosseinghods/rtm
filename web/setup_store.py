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


def record(sig, now=None):
    """Save each zone the system places (deduped while still OPEN)."""
    now = now or int(time.time())
    rows = _read()
    open_keys = {_key(r["symbol"], r["tf"], r["dir"], r["src"], r["entry"])
                 for r in rows if r["status"] == "OPEN"}
    added = 0
    for z in (sig.get("zones") or []):
        if z.get("entry") is None or z.get("sl") is None or z.get("tp2") is None:
            continue
        k = _key(sig["symbol"], sig["tf"], z["dir"], z["src"], z["entry"])
        if k in open_keys:
            continue
        rows.append({
            "ts": now, "symbol": sig["symbol"], "tf": sig["tf"], "dir": z["dir"],
            "src": z["src"], "grade": z.get("grade"), "confidence": z.get("confidence"),
            "combo_score": z.get("combo_score"), "combo_confirmed": bool(z.get("combo_confirmed")),
            "with_trend": bool(z.get("with_trend")), "setup_type": z.get("setup_type"),
            "entry": z["entry"], "sl": z["sl"], "tp2": z["tp2"], "tp3": z.get("tp3"),
            "risk": z.get("risk"), "status": "OPEN", "R": None, "exit_ts": None, "origin": "live",
        })
        open_keys.add(k); added += 1
    if added:
        _write(rows)
    return added


def _simulate(s, t, h, l, c):
    """Replay forward OHLC: fill the entry (limit), then SL vs 2R-target, first touch wins.
    Returns (status, R, exit_ts) or None if still unresolved within the window."""
    dr = 1 if s["dir"] == "LONG" else -1
    entry, sl, tp = s["entry"], s["sl"], s["tp2"]
    filled = False
    for j in range(min(len(t), MAXBARS)):
        if not filled:
            hit_entry = (l[j] <= entry) if dr == 1 else (h[j] >= entry)
            if not hit_entry:
                continue
            filled = True
        if dr == 1:
            hit_sl, hit_tp = (l[j] <= sl), (h[j] >= tp)
        else:
            hit_sl, hit_tp = (h[j] >= sl), (l[j] <= tp)
        if hit_sl:                       # conservative: stop takes priority on a same-bar tie
            return ("LOSS", -1.0, int(t[j]))
        if hit_tp:
            return ("WIN", 2.0, int(t[j]))
    # ran out of bars: if it never even filled and the window is used up, expire it
    if not filled and len(t) >= MAXBARS:
        return ("EXPIRED", None, int(t[min(len(t), MAXBARS) - 1]))
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
        s["status"], s["R"], s["exit_ts"] = out
        changed += 1
        wins += s["status"] == "WIN"; losses += s["status"] == "LOSS"
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
    """Seed the store with REAL past zone outcomes (incl. stops) from the validated engine."""
    os.environ.setdefault("FD_SRC", "1h"); os.environ.setdefault("FD_CONF", "none")
    import importlib, numpy as np
    import rtm_bt as B, bt_structure as BS
    importlib.reload(BS)
    SRC = symbols or ["BTCUSDT", "XRPUSDT", "ETHUSD", "SOLUSD", "BNBUSD", "ADAUSD",
                      "AVAXUSD", "LINKUSD", "LTCUSD", "DOTUSD", "ATOMUSD", "DOGEUSD", "XAUUSD"]
    rows = [r for r in _read() if r.get("origin") != "train"]   # refresh training rows
    added = 0
    for sym in SRC:
        try:
            D = B.prep_symbol(sym, tf)
        except Exception as e:
            print(f"  skip {sym}: {str(e)[:50]}"); continue
        recs = BS.collect(D, sym, "london" if sym == "XAUUSD" else "ny")
        styles = _combo_votes(D)
        for r in recs:
            R = r.get("2R")
            if R is None:
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
                "status": "WIN" if R > 0 else "LOSS", "R": float(R),
                "exit_ts": None, "origin": "train",
            })
            added += 1
        print(f"  {sym:9} +{len([1 for r in recs if r.get('2R') is not None]):4} resolved setups")
    _write(rows)
    return added


def _agg(rows):
    n = len(rows)
    if n == 0:
        return None
    wins = sum(1 for r in rows if r["status"] == "WIN")
    Rs = [r["R"] for r in rows if r.get("R") is not None]
    expR = round(sum(Rs) / len(Rs), 3) if Rs else None
    return {"n": n, "stops": n - wins, "win_rate": round(100 * wins / n, 1),
            "stop_rate": round(100 * (n - wins) / n, 1), "expR": expR}


def lessons():
    """What the system has learned from its zones — overall + which kinds stop out most."""
    rows = [r for r in _read() if r["status"] in ("WIN", "LOSS")]
    out = {"overall": _agg(rows), "by": {}}
    for f in FEATURES:
        groups = {}
        for r in rows:
            v = r.get(f)
            if v is None:
                continue
            groups.setdefault(str(v), []).append(r)
        out["by"][f] = {k: _agg(v) for k, v in groups.items() if len(v) >= 20}
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
