#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
build_static.py — run by GitHub Actions (where Python IS available). Computes the signal +
assistant text for every symbol/timeframe using the VALIDATED engine, advances the learning
loop, and writes small JSON files into site/data/ that the PHP host just serves. The browser
fetches candles + live price directly from Binance, so these JSONs stay tiny (a few KB each).

No Python needed on the cPanel host — only GitHub Actions runs this.
"""
import os, sys, json, datetime
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "backtest")))
os.environ.setdefault("RTM_DATA_DIR", os.path.join(HERE, "data"))

import live_feed as F
import signal_service as S
import assistant as A
import learning_store as LS
import setup_store as SU
try:
    import optimize as OPT
except Exception:
    OPT = None

OUT = os.path.abspath(os.path.join(HERE, "..", "site", "data"))
TFS = os.environ.get("RTM_TFS", "M5,M15,H1").split(",")
os.makedirs(OUT, exist_ok=True)


def write(name, obj):
    with open(os.path.join(OUT, name), "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))


def main():
    syms = F.list_symbols()
    write("symbols.json", {"symbols": syms})
    write("timeframes.json", {"timeframes": TFS, "default": "M5", "proven": "M5"})

    built = 0; errors = []
    for s in syms:
        sym = s["symbol"]
        for tf in TFS:
            try:
                sig = S.compute(sym, tf)
                sig = LS.annotate(sig)
                sig = SU.annotate(sig)
                text = A.narrate(sig)
                LS.record(sig)          # journal the prediction (auto-learning)
                SU.record(sig)          # remember the zones it placed
                write(f"sig_{sym}_{tf}.json", {"text": text, "signal": sig})
                # non-USDT symbols (XAUUSD) have no Binance ticker -> emit candles for the chart
                if not sym.endswith("USDT"):
                    write(f"ohlcv_{sym}_{tf}.json", {"bars": F.read_ohlcv(sym, tf, 600)})
                built += 1
            except Exception as e:
                errors.append(f"{sym}/{tf}: {str(e)[:80]}")

    # advance the learning loop each run (resolve what played out, re-tune)
    try: LS.score_due(F.price_at)
    except Exception as e: errors.append(f"score_due: {e}")
    try: SU.resolve()
    except Exception as e: errors.append(f"resolve: {e}")
    if OPT:
        try: OPT.run()
        except Exception as e: errors.append(f"optimize: {e}")

    lsum = LS.summary()
    write("manifest.json", {
        "updated": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ"),
        "built": built, "tfs": TFS, "errors": errors[:20],
        "learning": {
            "overall": lsum.get("overall"),
            "scored": lsum.get("scored_predictions"),
            "pending": lsum.get("pending"),
            # learned hit-rate per confluence count (how many independent styles agreed)
            "by_combo": {str(k): v.get("rate") for k, v in (lsum.get("by_combo") or {}).items()},
        },
    })
    print(f"built {built} signal files, {len(errors)} errors")
    for e in errors[:20]: print("  -", e)


if __name__ == "__main__":
    main()
