#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
USDT dominance (USDT.D) context for crypto.

Principle the user gave: USDT dominance is INVERSELY correlated with crypto — when
USDT.D rises, money is sitting in stablecoins → crypto falls; when USDT.D falls,
money rotates into crypto → crypto rises. So a rising USDT.D is a bearish backdrop
for BTC/alts and vice-versa.

Source: CoinGecko /global (free, no key) → market_cap_percentage.usdt.
We persist successive readings to web/data/dominance_history.jsonl so we can measure
the short-term TREND (and later, support/resistance on dominance itself).
"""
import os, json, time
import requests

HIST = os.path.join(os.path.dirname(__file__), "data", "dominance_history.jsonl")
_cache = {"ts": 0, "val": None}
CACHE_SEC = 120


def _fetch():
    r = requests.get("https://api.coingecko.com/api/v3/global", timeout=10)
    r.raise_for_status()
    d = r.json()["data"]["market_cap_percentage"]
    return {"usdt": float(d.get("usdt", 0.0)),
            "usdc": float(d.get("usdc", 0.0)),
            "btc": float(d.get("btc", 0.0))}


def _append_hist(rec):
    try:
        os.makedirs(os.path.dirname(HIST), exist_ok=True)
        with open(HIST, "a") as f:
            f.write(json.dumps(rec) + "\n")
    except Exception:
        pass


def _recent(n=40):
    if not os.path.exists(HIST):
        return []
    try:
        lines = open(HIST).read().strip().splitlines()[-n:]
        return [json.loads(x) for x in lines]
    except Exception:
        return []


def get(ts_now=None):
    """Return current USDT.D + a trend label from recent history. Cached ~2 min."""
    now = time.time()
    if _cache["val"] is not None and now - _cache["ts"] < CACHE_SEC:
        cur = _cache["val"]
    else:
        try:
            cur = _fetch()
            cur["t"] = ts_now or int(now)
            _append_hist(cur)
            _cache.update(ts=now, val=cur)
        except Exception as e:
            print(f"[dominance] fetch failed: {str(e)[:60]}", flush=True)
            hist = _recent(1)
            cur = hist[-1] if hist else None
    if not cur:
        return None
    hist = _recent(40)
    trend = "نامشخص"; slope = None
    usable = [h for h in hist if "usdt" in h]
    if len(usable) >= 3:
        first = usable[0]["usdt"]; last = usable[-1]["usdt"]
        slope = last - first
        if slope > 0.03:
            trend = "صعودی"      # USDT.D up → bearish for crypto
        elif slope < -0.03:
            trend = "نزولی"      # USDT.D down → bullish for crypto
        else:
            trend = "خنثی"
    # inverse bias for crypto: USDT.D up => crypto down (-1), down => up (+1)
    crypto_bias = 0
    if trend == "صعودی": crypto_bias = -1
    elif trend == "نزولی": crypto_bias = +1
    note = {
        "صعودی": "دامیننسِ تتر صعودی است → فشارِ نزولی روی کریپتو (پولْ در استیبل می‌ماند).",
        "نزولی": "دامیننسِ تتر نزولی است → بَک‌دراپِ صعودی برای کریپتو (پول به کریپتو می‌چرخد).",
        "خنثی": "دامیننسِ تتر کم‌تغییر است → اثرِ خنثی روی کریپتو.",
        "نامشخص": "تاریخچهٔ دامیننس هنوز کافی نیست (با هر بروزرسانی کامل‌تر می‌شود).",
    }[trend]
    return {"usdt_d": round(cur["usdt"], 2), "trend": trend,
            "slope": round(slope, 3) if slope is not None else None,
            "crypto_bias": crypto_bias, "note": note,
            "samples": len(usable)}


if __name__ == "__main__":
    print(json.dumps(get(), ensure_ascii=False, indent=2))
