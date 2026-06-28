#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Self-learning store — the system's memory of what it saw and what it predicted.

For every analysis it records:
  • a SNAPSHOT  (zones, bias, RSI, divergence, verdict, dominance) — the full picture,
    so later you can sit down, review, and train the system on real history.
  • a PREDICTION (projected direction + confidence + horizon) — which is later SCORED
    against what price actually did. Correct guesses raise the pattern's hit-rate;
    wrong ones are kept so the system learns and future confidence is calibrated.

Everything is plain JSONL under web/learning/, so it accumulates even when nobody is
watching (the recorder writes here on a schedule). Nothing here trades.
"""
import os, json, time

DIR = os.path.join(os.path.dirname(__file__), "learning")
SNAP = os.path.join(DIR, "snapshots.jsonl")
PRED = os.path.join(DIR, "predictions.jsonl")
os.makedirs(DIR, exist_ok=True)

_last_rec = {}          # (symbol, tf) -> ts   (in-process throttle)
THROTTLE_SEC = 240
_TF_SEC = {"M1": 60, "M5": 300, "M15": 900, "H1": 3600, "H4": 14400}   # bar length per TF
STALE_SEC = 7 * 86400   # abandon a prediction we still can't price this long after its horizon


def _append(path, rec):
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def _read(path):
    if not os.path.exists(path):
        return []
    out = []
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if line:
            try: out.append(json.loads(line))
            except Exception: pass
    return out


def record(sig, ts=None, force=False):
    """Append a compact snapshot + a prediction for this signal. Throttled per sym/tf."""
    ts = ts or int(time.time())
    key = (sig["symbol"], sig["tf"])
    if not force and ts - _last_rec.get(key, 0) < THROTTLE_SEC:
        return False
    _last_rec[key] = ts
    p = sig.get("primary") or {}
    proj = sig.get("projection") or {}
    snap = {
        "ts": ts, "symbol": sig["symbol"], "tf": sig["tf"], "price": sig.get("price"),
        "bias": sig.get("bias_val"), "rsi": (sig.get("rsi") or {}).get("last"),
        "n_div": len(sig.get("divergences") or []),
        "verdict": (sig.get("verdict") or {}).get("state"),
        "primary": {k: p.get(k) for k in ("dir", "src", "grade", "confidence",
                                          "entry", "sl", "tp2", "room_R",
                                          "combo_score", "combo_confirmed")} if p else None,
        "n_zones": len(sig.get("zones") or []),
        "dominance": sig.get("dominance"),
        "proj_dir": proj.get("dir_val"), "proj_conf": proj.get("confidence"),
    }
    _append(SNAP, snap)
    # a scoreable prediction: where does the system think price goes, by when
    pts = proj.get("points") or []
    if pts and proj.get("dir_val"):
        eval_t = pts[-1]["time"]
        # guard: a stale/delayed feed (e.g. Yahoo gold) can build a projection whose horizon
        # lands in the PAST (eval_t <= now). Such a prediction can never be scored honestly,
        # so skip it instead of letting it clog the pending queue forever.
        if eval_t > ts + _TF_SEC.get(sig["tf"], 60):
            _append(PRED, {
                "ts": ts, "symbol": sig["symbol"], "tf": sig["tf"],
                "ref_price": sig.get("price"), "dir": proj.get("dir_val"),
                "conf": proj.get("confidence"), "eval_t": eval_t,
                "bucket": f"{sig['symbol']}|{sig['tf']}",
                "combo": p.get("combo_score"),   # confluence count -> feeds LIVE by_combo learning
                "scored": False, "correct": None,
            })
    return True


def score_due(price_at, now=None):
    """Score predictions whose horizon has passed, each against the price AT ITS OWN
    horizon (`price_at(symbol, tf, eval_t) -> float|None`) — NOT one shared live price
    (that bug bucketed every symbol to 0% or 100%). A prediction is CORRECT if price moved
    in the predicted direction by > 0.05% over its horizon. If the horizon isn't covered by
    stored history yet, price_at returns None and the prediction stays pending."""
    now = now or int(time.time())
    preds = _read(PRED)
    out = []                                    # rebuilt store (lets us drop dead rows)
    n_scored = n_dropped = 0
    for pr in preds:
        if pr.get("scored") or pr["eval_t"] > now:
            out.append(pr); continue            # already done, or horizon not reached yet
        fut = price_at(pr["symbol"], pr.get("tf", "M5"), pr["eval_t"])
        if fut is None or not pr.get("ref_price"):
            # can't price it yet. Keep waiting — UNLESS it's hopelessly stale (the data will
            # never cover this horizon), in which case drop it so pending doesn't grow forever.
            if now - pr["eval_t"] > STALE_SEC:
                n_dropped += 1
            else:
                out.append(pr)
            continue
        move = (fut - pr["ref_price"]) / pr["ref_price"]
        thr = 0.0005
        if abs(move) < thr:
            correct = False                     # flat = the directional call didn't pay
        else:
            correct = (move > 0) == (pr["dir"] > 0)
        pr["scored"] = True; pr["correct"] = bool(correct); pr["exit_price"] = fut
        n_scored += 1; out.append(pr)
    if n_scored or n_dropped:
        with open(PRED, "w", encoding="utf-8") as f:
            for pr in out:
                f.write(json.dumps(pr, ensure_ascii=False) + "\n")
    return {"scored": n_scored, "dropped": n_dropped,
            "pending": sum(1 for p in out if not p.get("scored"))}


def accuracy(symbol=None, tf=None):
    """Hit-rate of scored predictions, optionally filtered to a bucket."""
    preds = [p for p in _read(PRED) if p.get("scored")]
    if symbol: preds = [p for p in preds if p["symbol"] == symbol]
    if tf:     preds = [p for p in preds if p["tf"] == tf]
    n = len(preds)
    hits = sum(1 for p in preds if p.get("correct"))
    return {"n": n, "hits": hits, "rate": round(hits / n, 3) if n else None}


def combo_accuracy(combo=None):
    """Learned hit-rate conditioned on confluence count (how many independent styles agreed)."""
    preds = [p for p in _read(PRED) if p.get("scored") and p.get("combo") is not None]
    if combo is not None:
        preds = [p for p in preds if p["combo"] >= combo]   # >=combo (e.g. >=2 = confirmed)
    n = len(preds); hits = sum(1 for p in preds if p.get("correct"))
    return {"n": n, "rate": round(hits / n, 3) if n else None}


def summary():
    """Overall + per-bucket + per-confluence accuracy, for the review/training step."""
    preds = [p for p in _read(PRED) if p.get("scored")]
    buckets = {}
    for p in preds:
        b = p["bucket"]; bk = buckets.setdefault(b, {"n": 0, "hits": 0})
        bk["n"] += 1; bk["hits"] += 1 if p.get("correct") else 0
    for b, bk in buckets.items():
        bk["rate"] = round(bk["hits"] / bk["n"], 3) if bk["n"] else None
    by_combo = {}
    for p in preds:
        ck = p.get("combo")
        if ck is None: continue
        bk = by_combo.setdefault(ck, {"n": 0, "hits": 0})
        bk["n"] += 1; bk["hits"] += 1 if p.get("correct") else 0
    for k, bk in by_combo.items():
        bk["rate"] = round(bk["hits"] / bk["n"], 3) if bk["n"] else None
    snaps = _read(SNAP)
    return {"snapshots": len(snaps), "scored_predictions": len(preds),
            "overall": accuracy(), "buckets": buckets, "by_combo": by_combo,
            "pending": sum(1 for p in _read(PRED) if not p.get("scored"))}


def annotate(sig):
    """Attach the learned hit-rate for this bucket so the UI/assistant can show it and
    the projection confidence can be calibrated against real outcomes."""
    acc = accuracy(sig["symbol"], sig["tf"])
    proj = sig.get("projection")
    if proj is not None:
        proj["learned_rate"] = acc["rate"]
        proj["learned_n"] = acc["n"]
        if acc["rate"] is not None and acc["n"] >= 8:
            # blend model confidence with realised hit-rate (50/50) once enough samples
            proj["confidence"] = round(0.5 * proj["confidence"] + 0.5 * acc["rate"], 2)
    # learned hit-rate conditioned on the primary zone's confluence (the real edge lever)
    p = sig.get("primary")
    if p is not None and p.get("combo_score") is not None:
        ca = combo_accuracy(p["combo_score"])
        p["combo_learned_rate"] = ca["rate"]
        p["combo_learned_n"] = ca["n"]
    return sig


if __name__ == "__main__":
    print(json.dumps(summary(), ensure_ascii=False, indent=2))
