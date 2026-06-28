#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Background recorder — runs on a schedule (cron/launchd) so the system keeps learning
even when the web app is closed. Each run:
  1. computes signals for every symbol (M5 + H1) → records a snapshot + a prediction
  2. scores any predictions whose horizon has passed, against the current live price

Run once:   cd ~/Desktop/trade/web && python3 recorder.py
Loop mode:  python3 recorder.py --loop   (records every RECORD_EVERY sec, Ctrl-C to stop)
"""
import os, sys, time
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "backtest")))
os.environ.setdefault("RTM_DATA_DIR", os.path.join(os.path.dirname(__file__), "data"))

import signal_service as S
import journal_store as J
import learning_store as LS
import live_feed as F

SYMS = list(F.SYMBOLS.keys())
TFS = ["M5", "H1"]            # H1 predictions are more reliable (user's guidance)
RECORD_EVERY = int(os.environ.get("RECORD_EVERY", "900"))   # 15 min


def one_pass():
    rec = 0
    for sym in SYMS:
        for tf in TFS:
            try:
                sig = S.compute(sym, tf)
                sig = J.adjusted_confidence(sig)
                sig = LS.annotate(sig)
                if LS.record(sig, force=True):
                    rec += 1
            except Exception as e:
                print(f"  {sym}/{tf} skip: {str(e)[:50]}", flush=True)
    # score predictions that are now due
    try:
        LS.score_due(F.price_at)
    except Exception as e:
        print(f"  score err: {str(e)[:50]}", flush=True)
    s = LS.summary()
    print(f"recorded {rec} snapshots | total snaps {s['snapshots']} | "
          f"scored preds {s['scored_predictions']} (pending {s['pending']}) | "
          f"overall acc {s['overall']['rate']}", flush=True)


if __name__ == "__main__":
    if "--loop" in sys.argv:
        while True:
            try: one_pass()
            except Exception as e: print("pass err:", str(e)[:60], flush=True)
            time.sleep(RECORD_EVERY)
    else:
        one_pass()
