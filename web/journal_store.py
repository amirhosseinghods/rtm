#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Trade journal + the 'learn from trades' loop.

Every setup the user logs is stored in web/journal_ledger.json. When the user marks
an outcome (WIN/LOSS/SKIP), we recompute the empirical win-rate per setup BUCKET
(source + direction + confidence tier). relearn() exposes those real-world rates so
the live signal can show an *adjusted* confidence that blends backtest priors with
the user's own results — the assistant genuinely improves as trades accumulate.

This never touches money. It only records what the user reports.
"""
import os, json, time

LEDGER = os.path.join(os.path.dirname(__file__), "journal_ledger.json")

# Backtest priors (from journal/LEARNINGS.md) used until enough real samples exist.
PRIOR_WR = {"HIGH": 0.45, "MEDIUM": 0.40, "LOW": 0.33}   # 2R win-rate priors
PRIOR_STRENGTH = 8   # pseudo-count: real samples outweigh the prior after ~8 trades


def _load():
    if not os.path.exists(LEDGER):
        return {"entries": [], "seq": 0}
    try:
        with open(LEDGER, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"entries": [], "seq": 0}


def _save(d):
    tmp = LEDGER + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
    os.replace(tmp, LEDGER)


def bucket(src, dr, conf):
    return f"{src}|{dr}|{conf}"


def log_setup(sig, ts=None):
    """Record the primary plan of a signal as a PENDING journal entry. Returns id."""
    p = sig.get("primary")
    if not p:
        return None
    d = _load(); d["seq"] += 1
    eid = d["seq"]
    d["entries"].append({
        "id": eid, "ts": ts or time.strftime("%Y-%m-%d %H:%M:%S"),
        "symbol": sig["symbol"], "tf": sig["tf"], "dir": p["dir"], "src": p["src"],
        "grade": p["grade"], "confidence": p["confidence"],
        "entry": p["entry"], "sl": p["sl"], "tp2": p["tp2"], "risk": p["risk"],
        "room_R": p.get("room_R"),
        "bucket": bucket(p["src"], p["dir"], p["confidence"]),
        "status": "PENDING", "outcome": None,
    })
    _save(d)
    return eid


def set_outcome(eid, outcome):
    """outcome in {WIN, LOSS, SKIP}. WIN/LOSS feed the learning loop."""
    outcome = outcome.upper()
    if outcome not in ("WIN", "LOSS", "SKIP"):
        raise ValueError("outcome must be WIN/LOSS/SKIP")
    d = _load(); found = False
    for e in d["entries"]:
        if e["id"] == eid:
            e["outcome"] = outcome
            e["status"] = "CLOSED" if outcome in ("WIN", "LOSS") else "SKIPPED"
            e["closed_ts"] = time.strftime("%Y-%m-%d %H:%M:%S")
            found = True
            break
    if not found:
        raise KeyError(f"entry {eid} not found")
    _save(d)
    return True


def entries():
    return _load()["entries"]


def relearn():
    """Recompute empirical win-rate per bucket and a blended (prior+real) win-rate.
    Returns {bucket: {n, wins, real_wr, blended_wr}} plus overall stats."""
    d = _load()
    buckets = {}
    for e in d["entries"]:
        if e.get("outcome") not in ("WIN", "LOSS"):
            continue
        b = e["bucket"]; bk = buckets.setdefault(b, {"n": 0, "wins": 0})
        bk["n"] += 1; bk["wins"] += 1 if e["outcome"] == "WIN" else 0
    out = {}
    for b, bk in buckets.items():
        conf = b.split("|")[-1]
        prior = PRIOR_WR.get(conf, 0.4)
        real_wr = bk["wins"] / bk["n"] if bk["n"] else None
        blended = (bk["wins"] + prior * PRIOR_STRENGTH) / (bk["n"] + PRIOR_STRENGTH)
        out[b] = {"n": bk["n"], "wins": bk["wins"],
                  "real_wr": round(real_wr, 3) if real_wr is not None else None,
                  "blended_wr": round(blended, 3)}
    closed = [e for e in d["entries"] if e.get("outcome") in ("WIN", "LOSS")]
    wins = sum(1 for e in closed if e["outcome"] == "WIN")
    # equity sim: $1000, 1% risk, +2R win / -1R loss, in log order
    bal = 1000.0
    for e in closed:
        bal *= (1 + 0.01 * (2.0 if e["outcome"] == "WIN" else -1.0))
    overall = {"closed": len(closed), "wins": wins,
               "win_rate": round(wins / len(closed), 3) if closed else None,
               "sim_balance": round(bal, 2)}
    return {"buckets": out, "overall": overall}


def adjusted_confidence(sig):
    """Annotate each zone with a real-world win-rate from relearn(), if available."""
    learned = relearn()["buckets"]
    for z in sig.get("zones", []):
        b = bucket(z["src"], z["dir"], z["confidence"])
        info = learned.get(b)
        z["learned_wr"] = info["blended_wr"] if info else None
        z["learned_n"] = info["n"] if info else 0
    if sig.get("primary"):
        b = bucket(sig["primary"]["src"], sig["primary"]["dir"], sig["primary"]["confidence"])
        info = learned.get(b)
        sig["primary"]["learned_wr"] = info["blended_wr"] if info else None
        sig["primary"]["learned_n"] = info["n"] if info else 0
    return sig


if __name__ == "__main__":
    print(json.dumps(relearn(), ensure_ascii=False, indent=2))
