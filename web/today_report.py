#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
«امروز چند استاپ/TP خورد؟» — for each symbol, take the system's live zones (the
validated 1h OB sources, same ones the app draws), find every fresh zone-touch whose
ENTRY happened TODAY (UTC), simulate the R-grid plan forward over the available bars,
and classify the result: TP2 (2R) hit / SL hit / still open.

Uses live data (Binance + Yahoo) via live_feed, so it matches what the chart shows.
Run:  cd ~/Desktop/trade/web && python3 today_report.py [M5]
"""
import os, sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "backtest")))
os.environ.setdefault("RTM_DATA_DIR", os.path.join(os.path.dirname(__file__), "data"))
import numpy as np, pandas as pd
import rtm_bt as B
import live_feed as F

TF = sys.argv[1] if len(sys.argv) > 1 else "M5"
RECENT_DAYS = int(os.environ.get("RECENT_DAYS", "3"))
SYMS = ["BTCUSDT", "ETHUSDT", "XRPUSDT", "SOLUSDT", "XAUUSD"]


def resolve(D, sym):
    """Return today's zone-touches with outcomes. 'Today' = last calendar day in data (UTC)."""
    n = len(D["c"]); o, h, l, c, atr = D["o"], D["h"], D["l"], D["c"], D["atr"]
    t = D["time"]
    today = t[-1].normalize()                       # midnight of the latest bar's day
    gold = sym == "XAUUSD"; ms = 2.5 if gold else 1.0; buf = 0.3
    b1, b2, b3 = D["b1"], D["b2"], D["b3"]
    bias = np.sign(1*b1 + 2*b2 + 2*b3).astype(int)
    zones = [("demand", 1, D["c_demT"], D["c_demB"], D["c_demG"]),
             ("supply", -1, D["c_supT"], D["c_supB"], D["c_supG"])]
    rows = []
    for kind, dr, ZT, ZB, ZG in zones:
        prev = np.nan; tested = False
        for i in range(n):
            zt, zb = ZT[i], ZB[i]
            if not np.isnan(zb) and (np.isnan(prev) or zb != prev):
                tested = False; prev = zb
            if tested or np.isnan(zb) or np.isnan(atr[i]):
                continue
            touch = (l[i] <= zt and h[i] >= zb) if dr == 1 else (h[i] >= zb and l[i] <= zt)
            if not touch:
                continue
            tested = True
            if t[i] < today - pd.Timedelta(days=RECENT_DAYS):   # keep today + recent window
                continue
            e = c[i]
            if dr == 1:
                sl = min(zb - buf*atr[i], e - ms*atr[i]); risk = e - sl
            else:
                sl = max(zt + buf*atr[i], e + ms*atr[i]); risk = sl - e
            if risk <= 0:
                continue
            tp2 = e + dr*2.0*risk
            res = "OPEN"; bars = 0
            for j in range(i+1, n):
                bars = j - i
                if dr == 1:
                    if l[j] <= sl: res = "SL"; break
                    if h[j] >= tp2: res = "TP"; break
                else:
                    if h[j] >= sl: res = "SL"; break
                    if l[j] <= tp2: res = "TP"; break
            rows.append(dict(sym=sym, time=t[i], kind=kind,
                             dir=("LONG" if dr == 1 else "SHORT"),
                             grade=int(ZG[i]), withtrend=int(dr == bias[i]),
                             entry=round(e, 4), sl=round(sl, 4), tp2=round(tp2, 4),
                             result=res, bars=bars, today=bool(t[i] >= today)))
    return rows


def main():
    allrows = []
    for sym in SYMS:
        try:
            F.refresh(sym)
            D = B.prep_symbol(sym, TF)
            r = resolve(D, sym)
            allrows += r
            rt = [x for x in r if x["today"]]
            tp = sum(x["result"] == "TP" for x in rt); sl = sum(x["result"] == "SL" for x in rt); op = sum(x["result"] == "OPEN" for x in rt)
            print(f"{sym:9s}  امروز: {len(rt):2d} لمس (TP {tp} SL {sl} باز {op})  | {RECENT_DAYS} روزِ اخیر: {len(r)} لمس", flush=True)
        except Exception as ex:
            print(f"{sym:9s}  err: {str(ex)[:50]}", flush=True)
    df = pd.DataFrame(allrows)
    print("\n" + "=" * 64)
    if len(df) == 0:
        print("در بازهٔ اخیر هیچ ناحیه‌ای لمس/فعال نشده."); return
    def tally(d, label):
        if len(d) == 0:
            print(f"{label}: هیچ لمسی نبود"); return
        tp = (d["result"] == "TP").sum(); sl = (d["result"] == "SL").sum(); op = (d["result"] == "OPEN").sum()
        closed = tp + sl
        wr = f"  | نرخِ برد {100*tp/closed:.0f}% ({tp}/{closed})" if closed else ""
        print(f"{label}: {len(d)} لمس → TP {tp}  SL {sl}  باز {op}{wr}")
    tdf = df[df["today"]]
    tally(tdf, f"امروز ({TF})")
    tally(df, f"{RECENT_DAYS} روزِ اخیر ({TF})")
    print("=" * 64)
    print("\nجزئیات (★ = امروز):")
    print(f"{'نماد':9s} {'زمان(UTC)':16s} {'جهت':5s} {'g':>1s} {'wt':>2s} {'ورود':>11s} {'استاپ':>11s} {'TP2':>11s} {'نتیجه':>5s} {'کندل':>5s}")
    for _, r in df.sort_values("time").iterrows():
        star = "★" if r["today"] else " "
        print(f"{star}{r['sym']:9s} {str(r['time'])[:16]:16s} {r['dir']:5s} {r['grade']:>1d} {r['withtrend']:>2d} "
              f"{r['entry']:>11} {r['sl']:>11} {r['tp2']:>11} {r['result']:>5s} {r['bars']:>5d}")


if __name__ == "__main__":
    main()
