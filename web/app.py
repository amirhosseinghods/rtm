#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FastAPI backend for the local RTM trading assistant.

Run:  cd ~/Desktop/trade/web && uvicorn app:app --reload --port 8000
Open: http://localhost:8000

Serves the Lightweight-Charts frontend + a JSON API that reuses the validated
Python engine (rtm_bt) and the RTM concept layer. It NEVER places orders.
"""
import os, sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "backtest")))
# point the engine's load() at our live-fetched CSVs
os.environ.setdefault("RTM_DATA_DIR", os.path.join(os.path.dirname(__file__), "data"))

from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles

import live_feed as F
import signal_service as S
import assistant as A
import journal_store as J
import learning_store as LS
import setup_store as SU

app = FastAPI(title="RTM Trading Assistant (local)")
STATIC = os.path.join(os.path.dirname(__file__), "static")

TIMEFRAMES = ["M1", "M5", "M15", "H1", "H4"]


@app.get("/api/symbols")
def symbols():
    return {"symbols": F.list_symbols()}


@app.get("/api/timeframes")
def timeframes():
    # M5 flagged as the proven default
    return {"timeframes": TIMEFRAMES, "default": "M5", "proven": "M5"}


@app.get("/api/ohlcv")
def ohlcv(symbol: str, tf: str = "M5", limit: int = 400):
    F.refresh(symbol)
    deep = limit > 1500                       # large request -> serve deep history (months)
    if deep:
        F.ensure_history(symbol, tf)
    return {"symbol": symbol, "tf": tf, "bars": F.read_ohlcv(symbol, tf, limit, deep=deep)}


@app.get("/api/quote")
def quote(symbol: str):
    px, delayed = F.last_price(symbol)
    return {"symbol": symbol, "price": px, "delayed": delayed}


@app.get("/api/signal")
def signal(symbol: str, tf: str = "M5"):
    try:
        sig = S.compute(symbol, tf)
        sig = J.adjusted_confidence(sig)
        sig = LS.annotate(sig)          # attach learned hit-rate
        sig = SU.annotate(sig)          # attach learned stop-rate for this kind of zone
        LS.record(sig)                  # remember this analysis (throttled)
        SU.record(sig)                  # remember the ZONES it placed (to resolve later)
        return sig
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.get("/api/assistant")
def assistant(symbol: str, tf: str = "M5"):
    try:
        sig = S.compute(symbol, tf)
        sig = J.adjusted_confidence(sig)
        sig = LS.annotate(sig)
        sig = SU.annotate(sig)
        LS.record(sig, force=True)      # a deliberate analysis is always recorded
        SU.record(sig)                  # remember the ZONES it placed (to resolve later)
        return {"text": A.narrate(sig), "signal": sig}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.get("/api/learning")
def learning():
    """The system's accumulated memory + how accurate its past predictions were.
    Scores any due predictions first, each against the real price at its own horizon."""
    try:
        LS.score_due(F.price_at)
    except Exception:
        pass
    return LS.summary()


@app.get("/api/setups")
def setups():
    """Zone-level learning: resolve any open setups (did the STOP or the target hit?),
    then return the lessons + the most recent stops the system remembers."""
    res = {}
    try:
        res = SU.resolve()              # mark OPEN zones that have since hit SL/TP
    except Exception as e:
        res = {"error": str(e)}
    return {"resolved": res, "lessons": SU.lessons(), "recent_stops": SU.recent_stops(12)}


@app.get("/api/journal")
def journal_get():
    return {"entries": J.entries(), "learn": J.relearn()}


@app.post("/api/journal")
def journal_post(payload: dict):
    """Body: {action:'log', symbol, tf}  OR  {action:'outcome', id, outcome}"""
    action = payload.get("action")
    if action == "log":
        sig = S.compute(payload["symbol"], payload.get("tf", "M5"))
        eid = J.log_setup(sig)
        return {"ok": eid is not None, "id": eid}
    if action == "outcome":
        J.set_outcome(int(payload["id"]), payload["outcome"])
        return {"ok": True, "learn": J.relearn()}
    return JSONResponse(status_code=400, content={"error": "unknown action"})


@app.post("/api/relearn")
def relearn():
    return J.relearn()


@app.get("/")
def index():
    return FileResponse(os.path.join(STATIC, "index.html"))


app.mount("/", StaticFiles(directory=STATIC, html=True), name="static")
