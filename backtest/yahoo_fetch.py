#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Fetch a symbol's OHLC from Yahoo Finance -> write {NAME}_{TF}.csv in ~/Downloads
in the same tab-separated format the backtester expects.
Usage: python3 yahoo_fetch.py SOL-USD SOLUSD
NOTE: Yahoo intraday history is SHORT (M5/M15 ~60d, M1 ~7d); H1 ~2y. Good for adding many
symbols to the POOLED portfolio, not for deep single-symbol validation."""
import urllib.request, json, sys, os
import pandas as pd
DL=os.path.expanduser("~/Downloads"); HDR={"User-Agent":"Mozilla/5.0"}

def fetch_raw(ysym, interval, rng):
    url=f"https://query1.finance.yahoo.com/v8/finance/chart/{ysym}?range={rng}&interval={interval}"
    d=json.load(urllib.request.urlopen(urllib.request.Request(url,headers=HDR),timeout=30))
    r=d["chart"]["result"][0]; ts=r["timestamp"]; q=r["indicators"]["quote"][0]
    df=pd.DataFrame({"Time":pd.to_datetime(ts,unit="s"),
                     "Open":q["open"],"High":q["high"],"Low":q["low"],"Close":q["close"]})
    return df.dropna().drop_duplicates("Time").reset_index(drop=True)

def write_csv(df, name, tf):
    p=f"{DL}/{name}_{tf}.csv"
    out=df[["Time","Open","High","Low","Close"]].copy(); out["Dur"]=0; out["Vol"]=0
    out.to_csv(p, sep="\t", index=False)
    print(f"  {tf:4s}: {len(df):6d} bars  {df.Time.iloc[0]} -> {df.Time.iloc[-1]}  -> {os.path.basename(p)}")

def fetch_symbol(ysym, name):
    print(f"Fetching {ysym} -> {name}_*.csv")
    plan=[("1h","2y","H1"),("15m","60d","M15"),("5m","60d","M5"),("1m","7d","M1")]
    h1=None
    for iv,rng,tf in plan:
        try:
            df=fetch_raw(ysym,iv,rng); write_csv(df,name,tf)
            if tf=="H1": h1=df
        except Exception as e:
            print(f"  {tf:4s}: ERROR {str(e)[:70]}")
    # H4 by resampling H1
    if h1 is not None:
        h4=h1.set_index("Time").resample("4h").agg({"Open":"first","High":"max","Low":"min","Close":"last"}).dropna().reset_index()
        write_csv(h4,name,"H4")
    print("done.\n")

if __name__=="__main__":
    ysym=sys.argv[1] if len(sys.argv)>1 else "SOL-USD"
    name=sys.argv[2] if len(sys.argv)>2 else ysym.replace("-","")
    fetch_symbol(ysym,name)
