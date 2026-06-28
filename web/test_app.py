#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Comprehensive test suite for the RTM trading web app.
Layers:
  A. module smoke    — rtm_concepts detectors (incl. degenerate inputs)
  B. data + signal   — signal_service invariants across symbols/TFs
  C. assistant text  — headings, footer, no decorative emoji, all-Latin digits
  D. API (TestClient)— every endpoint; representative symbols x timeframes
  E. journal loop    — log -> outcome(WIN/LOSS/SKIP) -> relearn; error cases
  F. error handling  — bad symbol / bad tf

Run:  cd ~/Desktop/trade/web && python3 test_app.py
Exits non-zero if any check fails.
"""
import os, sys, math, re, json
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "backtest")))
os.environ.setdefault("RTM_DATA_DIR", os.path.join(os.path.dirname(__file__), "data"))

import numpy as np

PASS = 0; FAIL = 0; FAILS = []
def ok(cond, name):
    global PASS, FAIL
    if cond: PASS += 1
    else: FAIL += 1; FAILS.append(name); print(f"  ✗ FAIL: {name}", flush=True)
def section(t): print(f"\n=== {t} ===", flush=True)

NUMERIC_OK = lambda x: x is None or (isinstance(x, (int, float)) and not (isinstance(x, float) and math.isnan(x)))

# --------------------------------------------------------------------------- #
section("A. rtm_concepts — detectors + degenerate inputs")
import rtm_concepts as RC
# synthetic series
n = 200
rng = np.random.default_rng(0)
c = 100 + np.cumsum(rng.normal(0, 1, n))
o = c + rng.normal(0, .3, n); h = np.maximum(o, c) + abs(rng.normal(0, .5, n)); l = np.minimum(o, c) - abs(rng.normal(0, .5, n))
atr = np.full(n, 1.0)
for fn, args in [("engulfing", (o,h,l,c)), ("sweep_reclaim", (h,l,c)),
                 ("compression", (h,l,atr)), ("pin_rejection", (o,h,l,c))]:
    try:
        r = getattr(RC, fn)(*args); ok(r is not None, f"{fn} returns");
    except Exception as e:
        ok(False, f"{fn} raised {e}")
for fn in ["qm_zones", "flag_limit_zones"]:
    try:
        z = getattr(RC, fn)(o,h,l,c,5,1.0,atr); ok(len(z)==n, f"{fn} length")
    except Exception as e:
        ok(False, f"{fn} raised {e}")
# degenerate: constant prices, tiny arrays
for desc, (oo,hh,ll,cc,aa) in [
    ("flat", (np.full(50,5.0),)*4 + (np.full(50,1.0),)),
    ("tiny", (np.array([1.,2.,3.]),)*4 + (np.array([1.,1.,1.]),)),
]:
    try:
        RC.engulfing(oo,hh,ll,cc); RC.sweep_reclaim(hh,ll,cc); RC.compression(hh,ll,aa)
        RC.qm_zones(oo,hh,ll,cc,5,1.0,aa); RC.flag_limit_zones(oo,hh,ll,cc,5,1.0,aa)
        ok(True, f"degenerate({desc}) no crash")
    except Exception as e:
        ok(False, f"degenerate({desc}) raised {e}")

# --------------------------------------------------------------------------- #
section("A2. rsi_tools — RSI, divergence, projection")
import rsi_tools as RT
r = RT.rsi(c, 14)
ok(np.all(np.isnan(r[:14])), "rsi NaN during warmup")
valid = r[~np.isnan(r)]
ok(len(valid) > 0 and valid.min() >= 0 and valid.max() <= 100, "rsi in [0,100]")
ok(RT.rsi(np.array([1.,2.,3.]), 14).shape == (3,), "rsi short array no crash")
dv = RT.divergences(h, l, c, r, L=5)
ok(isinstance(dv, list) and all(d["type"] in ("bull","bear") for d in dv), "divergences typed")
import pandas as _pd
tt = _pd.date_range("2026-06-01", periods=n, freq="5min")
proj = RT.project(tt, c, atr, 1, 55.0, dv, None, 5)
ok(proj["dir_val"] in (-1,0,1), "projection dir_val")
ok(0 <= proj["confidence"] <= 1, "projection confidence in [0,1]")
ok(len(proj["points"]) > 0, "projection has points")
ptimes = [p["time"] for p in proj["points"]]
ok(all(ptimes[i] < ptimes[i+1] for i in range(len(ptimes)-1)), "projection times strictly increasing")
pv = [p["value"] for p in proj["points"]]
pdiffs = [pv[i+1]-pv[i] for i in range(len(pv)-1)]
ok(any(d > 0 for d in pdiffs) and any(d < 0 for d in pdiffs), "projection is wavy (not monotonic straight line)")
ok(RT.rsi_state(25)["pull"] == 1 and RT.rsi_state(80)["pull"] == -1 and RT.rsi_state(50)["pull"] == 0, "rsi_state 30/70 logic")

# zone-aware projection: reach a zone -> bounce (strong, opposing) vs break (weak) vs pass (same-dir)
strong_supply = {"dir": "SHORT", "grade": 2, "confidence": "HIGH", "with_trend": True, "room_R": 3}
weak_supply   = {"dir": "SHORT", "grade": 0, "confidence": "LOW",  "with_trend": False, "room_R": 0.5}
demand_below  = {"dir": "LONG",  "grade": 2, "confidence": "HIGH", "with_trend": True, "room_R": 3}
b_strong = RT._bounce_decision(strong_supply, +1, 72)   # rising into strong resistance + overbought
b_weak   = RT._bounce_decision(weak_supply,   +1, 50)   # rising into weak resistance
b_same   = RT._bounce_decision(demand_below,  +1, 50)   # rising, demand below doesn't oppose
ok(b_strong[0] is True, "strong opposing zone -> bounce")
ok(b_weak[0] is False, "weak opposing zone -> break")
ok(b_same[0] is False and b_same[1] == 0, "same-direction zone -> pass (no opposition)")
# full projection with zones returns well-formed events + scenario + longer horizon
zz = [{"dir": "SHORT", "grade": 2, "confidence": "HIGH", "with_trend": True, "room_R": 3,
       "bot": float(c[-1]) + 0.5*float(atr[-1]), "top": float(c[-1]) + 1.5*float(atr[-1]), "src": "OB-1h"}]
pj = RT.project(tt, c, atr, 1, 60.0, dv, None, 5, zones=zz, price=float(c[-1]))
ok(len(pj["points"]) >= 40, "projection horizon extended (>=40 bars)")
ok(isinstance(pj.get("events"), list), "projection has events list")
ok(isinstance(pj.get("scenario"), str) and len(pj["scenario"]) > 0, "projection has scenario text")
ok(all(e["type"] in ("bounce", "break") for e in pj["events"]), "event types valid")
ok(all("price" in e and "zone" in e and "reason" in e for e in pj["events"]), "events well-formed")

# --------------------------------------------------------------------------- #
section("B. signal_service — invariants across symbols/TFs")
import signal_service as S
import live_feed as F
CRYPTO = ["BTCUSDT", "ETHUSDT", "XRPUSDT"]
GOLD = ["XAUUSD"]
def check_signal(sig, label):
    ok(isinstance(sig.get("price"), (int,float)) and sig["price"] > 0, f"{label}: price>0")
    ok(sig.get("bias") in ("صعودی","نزولی","خنثی"), f"{label}: bias valid")
    ok(sig.get("tf_health") and "color" in sig["tf_health"], f"{label}: tf_health")
    for z in sig.get("zones", []):
        ok(z["confidence"] in ("HIGH","MEDIUM","LOW"), f"{label}: conf valid")
        for k in ("entry","sl","tp1","tp2","tp3","risk"):
            ok(NUMERIC_OK(z.get(k)), f"{label}: {k} numeric")
        if z["dir"] == "LONG":
            ok(z["sl"] < z["entry"], f"{label}: LONG sl<entry")
            ok(z["tp1"] < z["tp2"] < z["tp3"], f"{label}: LONG tp ascending")
            ok(z["tp1"] > z["entry"], f"{label}: LONG tp>entry")
            ok(sig["price"] >= z["bot"]-1e-6, f"{label}: LONG not mitigated")
        else:
            ok(z["sl"] > z["entry"], f"{label}: SHORT sl>entry")
            ok(z["tp1"] > z["tp2"] > z["tp3"], f"{label}: SHORT tp descending")
            ok(z["tp1"] < z["entry"], f"{label}: SHORT tp<entry")
            ok(sig["price"] <= z["top"]+1e-6, f"{label}: SHORT not mitigated")
        # RR math: |tp2-entry| ~= 2*risk
        rr = abs(z["tp2"]-z["entry"])/z["risk"] if z["risk"] else 0
        ok(abs(rr-2.0) < 0.05, f"{label}: tp2 ~= 2R")
    # RSI / divergence / projection structure
    rs = sig.get("rsi") or {}
    ok(rs.get("last") is None or (0 <= rs["last"] <= 100), f"{label}: rsi.last in [0,100]")
    ok(isinstance(rs.get("series"), list), f"{label}: rsi.series list")
    ok(all(0 <= p["value"] <= 100 for p in rs.get("series", [])), f"{label}: rsi series in [0,100]")
    for dv in sig.get("divergences", []):
        ok(dv["type"] in ("bull","bear") and NUMERIC_OK(dv.get("price")), f"{label}: divergence valid")
    # verdict + per-zone action/risk
    vd = sig.get("verdict") or {}
    ok(vd.get("state") in ("BUY_NOW","SELL_NOW","WAIT"), f"{label}: verdict state")
    ok(isinstance(vd.get("text"), str) and len(vd["text"]) > 0, f"{label}: verdict text")
    for z in sig.get("zones", []):
        ok(z.get("action") in ("BUY","SELL"), f"{label}: zone action")
        ok(isinstance(z.get("actionable_now"), bool), f"{label}: actionable_now bool")
        rr = z.get("risk_rating") or {}
        ok(rr.get("level") in ("کم","متوسط","زیاد"), f"{label}: risk level")
    # dominance: None (gold) or a dict with usdt_d
    dom = sig.get("dominance")
    ok(dom is None or ("usdt_d" in dom and "crypto_bias" in dom), f"{label}: dominance shape")
    pj = sig.get("projection") or {}
    ok(pj.get("dir_val") in (-1,0,1), f"{label}: projection dir")
    ok(0 <= pj.get("confidence",0) <= 1, f"{label}: projection confidence")
    pts = pj.get("points", [])
    if pts:
        ok(all(pts[i]["time"] < pts[i+1]["time"] for i in range(len(pts)-1)), f"{label}: proj times increasing")
        # projection must start AFTER the last rsi/candle bar (future)
        if rs.get("series"):
            ok(pts[0]["time"] > rs["series"][-1]["time"], f"{label}: projection in the future")

# all crypto on M5 (per-symbol data path)
for sym in CRYPTO:
    try:
        check_signal(S.compute(sym, "M5"), f"{sym}/M5")
    except Exception as e:
        ok(False, f"{sym}/M5 raised {e}")
# all timeframes on BTCUSDT (per-TF path)
for tf in ["M1","M5","M15","H1","H4"]:
    try:
        check_signal(S.compute("BTCUSDT", tf), f"BTCUSDT/{tf}")
    except Exception as e:
        ok(False, f"BTCUSDT/{tf} raised {e}")
# gold (Yahoo, delayed)
for tf in ["M5","H1"]:
    try:
        sig = S.compute("XAUUSD", tf); check_signal(sig, f"XAUUSD/{tf}")
        ok(sig.get("gold") is True, f"XAUUSD/{tf}: gold flag")
    except Exception as e:
        ok(False, f"XAUUSD/{tf} raised {e}")

# --------------------------------------------------------------------------- #
section("C. assistant text — structure + cleanliness")
import assistant as A
for sym, tf in [("BTCUSDT","M5"), ("XAUUSD","M5"), ("ETHUSDT","H1")]:
    try:
        sig = S.compute(sym, tf); txt = A.narrate(sig)
        ok("##" in txt, f"{sym}/{tf}: has heading")
        ok("RSI" in txt and "پیش‌بینیِ جهت" in txt, f"{sym}/{tf}: RSI+projection section present")
        ok("تحلیلِ سیستمی" in txt and "اجرا نمی‌شود" in txt, f"{sym}/{tf}: safety footer present")
        farsi = re.findall(r"[۰-۹٠-٩]", txt)
        ok(len(farsi)==0, f"{sym}/{tf}: no farsi digits (found {set(farsi)})")
        emoji = re.findall(r"[\U0001F300-\U0001FAFF☀-➿]", txt)
        ok(set(emoji) <= {"⚠"}, f"{sym}/{tf}: only ⚠ emoji (found {set(emoji)})")
    except Exception as e:
        ok(False, f"assistant {sym}/{tf} raised {e}")

# --------------------------------------------------------------------------- #
section("D. API endpoints — FastAPI TestClient")
from fastapi.testclient import TestClient
import app as APP
client = TestClient(APP.app)

r = client.get("/api/symbols"); ok(r.status_code==200 and len(r.json()["symbols"])>=10, "GET /api/symbols")
r = client.get("/api/timeframes"); ok(r.status_code==200 and r.json()["default"]=="M5", "GET /api/timeframes")
r = client.get("/"); ok(r.status_code==200 and "RTM" in r.text, "GET / (index)")
for path in ["/app.js","/style.css","/fonts.css","/fonts/IRANYekanRegular.ttf"]:
    ok(client.get(path).status_code==200, f"static {path}")
# data endpoints for a couple symbols/TFs
for sym, tf in [("BTCUSDT","M5"), ("ETHUSDT","M15"), ("XAUUSD","M5")]:
    r = client.get(f"/api/ohlcv?symbol={sym}&tf={tf}&limit=200")
    j = r.json(); ok(r.status_code==200 and len(j["bars"])>0, f"ohlcv {sym}/{tf}")
    bars = j["bars"]; mono = all(bars[i]["time"] <= bars[i+1]["time"] for i in range(len(bars)-1))
    ok(mono, f"ohlcv {sym}/{tf} time-sorted")
    ok(all(b["high"]>=b["low"] for b in bars), f"ohlcv {sym}/{tf} high>=low")
    r = client.get(f"/api/quote?symbol={sym}"); ok(r.status_code==200 and (r.json()["price"] is None or r.json()["price"]>0), f"quote {sym}")
    r = client.get(f"/api/signal?symbol={sym}&tf={tf}"); sj = r.json()
    ok(r.status_code==200 and "zones" in sj, f"signal {sym}/{tf}")
    ok("rsi" in sj and "divergences" in sj and "projection" in sj, f"signal {sym}/{tf} has rsi/div/proj")
    r = client.get(f"/api/assistant?symbol={sym}&tf={tf}"); ok(r.status_code==200 and "text" in r.json(), f"assistant {sym}/{tf}")

# --------------------------------------------------------------------------- #
section("E. journal loop — log/outcome/relearn")
import journal_store as J
LED = J.LEDGER
backup = open(LED).read() if os.path.exists(LED) else None
try:
    # start clean
    if os.path.exists(LED): os.remove(LED)
    r = client.get("/api/journal"); ok(r.status_code==200 and r.json()["entries"]==[], "journal empty start")
    # log
    r = client.post("/api/journal", json={"action":"log","symbol":"BTCUSDT","tf":"M5"})
    jid = r.json().get("id"); ok(r.json()["ok"] and jid, "journal log -> id")
    # WIN
    r = client.post("/api/journal", json={"action":"outcome","id":jid,"outcome":"WIN"})
    ok(r.status_code==200 and r.json()["ok"], "journal outcome WIN")
    learn = r.json()["learn"]; ok(learn["overall"]["closed"]==1 and learn["overall"]["wins"]==1, "relearn counts WIN")
    ok(learn["overall"]["sim_balance"] > 1000, "equity sim grows on WIN")
    # second log + LOSS
    jid2 = client.post("/api/journal", json={"action":"log","symbol":"ETHUSDT","tf":"M5"}).json().get("id")
    if jid2:
        client.post("/api/journal", json={"action":"outcome","id":jid2,"outcome":"LOSS"})
    # SKIP path
    jid3 = client.post("/api/journal", json={"action":"log","symbol":"XRPUSDT","tf":"M5"}).json().get("id")
    if jid3:
        rs = client.post("/api/journal", json={"action":"outcome","id":jid3,"outcome":"SKIP"})
        ok(rs.status_code==200, "journal outcome SKIP")
    # relearn endpoint
    r = client.post("/api/relearn"); ok(r.status_code==200 and "overall" in r.json(), "POST /api/relearn")
    # invalid outcome
    if jid:
        try:
            rb = client.post("/api/journal", json={"action":"outcome","id":jid,"outcome":"BOGUS"})
            ok(rb.status_code>=400 or rb.status_code==500, "invalid outcome rejected")
        except Exception:
            ok(True, "invalid outcome rejected (raised)")
    # unknown action
    r = client.post("/api/journal", json={"action":"frobnicate"}); ok(r.status_code==400, "unknown action -> 400")
finally:
    # restore clean (leave ledger empty for the user)
    if os.path.exists(LED): os.remove(LED)

# --------------------------------------------------------------------------- #
section("F. error handling — bad inputs")
r = client.get("/api/signal?symbol=NOPE&tf=M5"); ok(r.status_code==500 and "error" in r.json(), "bad symbol -> 500 error json")
r = client.get("/api/assistant?symbol=NOPE&tf=M5"); ok(r.status_code==500, "bad symbol assistant -> 500")
try:
    S.compute("BTCUSDT","ZZ"); ok(False, "bad tf should raise")
except Exception:
    ok(True, "bad tf raises (handled)")

# --------------------------------------------------------------------------- #
section("G. learning store — record/score/accuracy + /api/learning")
import learning_store as LS
import tempfile
_tmp = tempfile.mkdtemp()
LS.SNAP = os.path.join(_tmp, "s.jsonl"); LS.PRED = os.path.join(_tmp, "p.jsonl")  # isolate from real store
LS._last_rec = {}
# build a tiny synthetic sig and record it
fake = {"symbol": "TESTX", "tf": "M5", "price": 100.0, "bias_val": 1,
        "rsi": {"last": 55.0}, "divergences": [], "verdict": {"state": "WAIT"},
        "primary": {"dir": "LONG", "src": "OB-1h", "grade": 2, "confidence": "HIGH",
                    "entry": 100, "sl": 99, "tp2": 102, "room_R": 3},
        "zones": [], "dominance": None,
        "projection": {"dir_val": 1, "confidence": 0.7,
                       "points": [{"time": 1000, "value": 100.5}]}}
ok(LS.record(fake, ts=1, force=True) is True, "learning record snapshot+prediction")
# score it: price went up (predicted up) -> correct
LS.score_due(lambda s, tf, et: 101.0, now=10**12)
acc = LS.accuracy("TESTX", "M5")
ok(acc["n"] >= 1, "learning accuracy has scored prediction")
summ = LS.summary()
ok("overall" in summ and "buckets" in summ, "learning summary shape")
r = client.get("/api/learning"); ok(r.status_code == 200 and "snapshots" in r.json(), "GET /api/learning")
# dominance shape (cached or live)
import dominance as DM
dm = DM.get()
ok(dm is None or ("usdt_d" in dm and "trend" in dm and "crypto_bias" in dm), "dominance.get shape")

# --------------------------------------------------------------------------- #
section("H. setup_store — learn from the zones it places (stops)")
import setup_store as SU
# _simulate: a long that hits the stop first -> LOSS; one that hits 2R -> WIN
long_loss = {"dir": "LONG", "entry": 100.0, "sl": 99.0, "tp2": 102.0}
t = np.arange(5); hi = np.array([100, 100.5, 100.2, 100.1, 100.0]); lo = np.array([100, 99.8, 98.9, 99.5, 99.0]); cl = hi
out = SU._simulate(long_loss, t, hi, lo, cl)
ok(out and out[0] == "LOSS" and out[1] == -1.0, "setup _simulate detects a STOP (LOSS)")
long_win = {"dir": "LONG", "entry": 100.0, "sl": 99.0, "tp2": 101.0}
hi2 = np.array([100, 100.4, 101.2, 101.5, 101.0]); lo2 = np.array([100, 99.9, 100.1, 100.5, 100.2])
out2 = SU._simulate(long_win, t, hi2, lo2, hi2)
ok(out2 and out2[0] == "WIN" and out2[1] == 2.0, "setup _simulate detects 2R TARGET (WIN)")
short_loss = {"dir": "SHORT", "entry": 100.0, "sl": 101.0, "tp2": 98.0}
hiS = np.array([100, 100.6, 101.2, 101.0, 100.0]); loS = np.array([100, 99.7, 99.9, 100.2, 99.5])
outS = SU._simulate(short_loss, t, hiS, loS, hiS)
ok(outS and outS[0] == "LOSS", "setup _simulate STOP on a short")
# lessons() shape from the history-seeded store (if present) or empty-safe
les = SU.lessons()
ok(isinstance(les, dict) and "overall" in les and "by" in les, "setup lessons shape")
ok(les["overall"] is None or ("stop_rate" in les["overall"] and "expR" in les["overall"]), "setup lessons overall fields")
# record dedups while OPEN
fake_sig = {"symbol": "TESTZ", "tf": "M5", "zones": [
    {"dir": "LONG", "src": "OB-1h", "grade": 2, "confidence": "HIGH", "combo_score": 2,
     "combo_confirmed": True, "with_trend": True, "entry": 50.0, "sl": 49.0, "tp2": 52.0, "risk": 1.0}]}
n1 = SU.record(fake_sig, now=1); n2 = SU.record(fake_sig, now=2)
ok(n1 == 1 and n2 == 0, "setup record dedups an open zone")
r = client.get("/api/setups")
ok(r.status_code == 200 and "lessons" in r.json() and "recent_stops" in r.json(), "GET /api/setups")
# cleanup the test rows so they don't pollute the real store
_rows = [x for x in SU._read() if x.get("symbol") != "TESTZ"]; SU._write(_rows)

# --------------------------------------------------------------------------- #
print(f"\n{'='*50}\nRESULT: {PASS} passed, {FAIL} failed")
if FAILS:
    print("FAILURES:"); [print("  -", f) for f in FAILS]
sys.exit(1 if FAIL else 0)
