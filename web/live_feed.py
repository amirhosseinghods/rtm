#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Live OHLCV feed for the local trading assistant.

Fetches candles from FREE sources and writes them to web/data/{SYM}_{TF}.csv in the
EXACT format rtm_bt.load() expects (header line + tab-separated Time O H L C Dur Vol,
naive UTC timestamps), so the validated engine runs unchanged on live data.

  Crypto -> Binance public REST klines (real-time, no API key).
  Gold   -> Yahoo Finance via yfinance (XAUUSD; ~delayed, intraday history limited).

Each refresh writes the TFs prep_symbol needs: the entry TF + M15, H1, H4.
Results are cached for REFRESH_SEC so rapid UI polling doesn't hammer the APIs.
"""
import os, time, io
import requests
import pandas as pd
import numpy as np

DATA_DIR = os.environ.get("RTM_DATA_DIR",
                          os.path.join(os.path.dirname(__file__), "data"))
os.makedirs(DATA_DIR, exist_ok=True)
REFRESH_SEC = int(os.environ.get("FEED_REFRESH_SEC", "45"))

# TFs the engine needs for any entry TF (prep_symbol loads M15,H1,H4 + entry TF).
ENGINE_TFS = ["M1", "M5", "M15", "H1", "H4"]
BINANCE_INTERVAL = {"M1": "1m", "M5": "5m", "M15": "15m", "H1": "1h", "H4": "4h"}
# how much history to pull per TF (enough for ATR/structure + a useful chart)
TF_LIMIT = {"M1": 1000, "M5": 1500, "M15": 1000, "H1": 1500, "H4": 1000}

# --- symbol registry -------------------------------------------------------- #
# display symbol -> (kind, provider-ticker)
CRYPTO = {
    "BTCUSDT": "BTCUSDT", "ETHUSDT": "ETHUSDT", "XRPUSDT": "XRPUSDT",
    "SOLUSDT": "SOLUSDT", "BNBUSDT": "BNBUSDT", "ADAUSDT": "ADAUSDT",
    "DOGEUSDT": "DOGEUSDT", "AVAXUSDT": "AVAXUSDT", "LINKUSDT": "LINKUSDT",
    "LTCUSDT": "LTCUSDT", "DOTUSDT": "DOTUSDT",
}
GOLD = {"XAUUSD": "GC=F"}        # Yahoo gold futures front month — FREE but ~10min DELAYED
GOLD_RT = {"PAXGUSDT": "PAXGUSDT"}  # PAX Gold on Binance: 1 token = 1oz gold, REAL-TIME, no delay

SYMBOLS = {**{s: ("crypto", t) for s, t in CRYPTO.items()},
           **{s: ("goldrt", t) for s, t in GOLD_RT.items()},
           **{s: ("gold", t) for s, t in GOLD.items()}}

def _is_binance(kind):    # which kinds come from the real-time Binance feed
    return kind in ("crypto", "goldrt")

_last_refresh = {}   # (sym) -> ts


def list_symbols():
    out = []
    for s, (kind, _) in SYMBOLS.items():
        if kind == "crypto":
            label = s.replace("USDT", "/USDT")
        elif kind == "goldrt":
            label = "طلا (PAXG · لحظه‌ای)"
        else:
            label = "XAU/USD (طلا · تأخیری)"
        out.append({"symbol": s, "kind": kind, "label": label,
                    "delayed": kind == "gold"})
    return out


# --- Binance ---------------------------------------------------------------- #
def _binance_klines(ticker, interval, limit):
    url = "https://api.binance.com/api/v3/klines"
    r = requests.get(url, params={"symbol": ticker, "interval": interval,
                                  "limit": limit}, timeout=15)
    r.raise_for_status()
    rows = r.json()
    # kline: [openTime, o, h, l, c, vol, closeTime, ...]
    df = pd.DataFrame(rows, columns=["ot", "o", "h", "l", "c", "v", "ct",
                                     "qv", "n", "tb", "tq", "ig"])
    df["Time"] = pd.to_datetime(df["ot"].astype("int64"), unit="ms")  # UTC naive
    for col, src in [("Open", "o"), ("High", "h"), ("Low", "l"), ("Close", "c"), ("Vol", "v")]:
        df[col] = df[src].astype(float)
    df["Dur"] = 0
    return df[["Time", "Open", "High", "Low", "Close", "Dur", "Vol"]]


# deep history (chart scroll-back of ~1–2 months); separate cache so engine stays fast
HIST_DIR = os.path.join(DATA_DIR, "hist")
try: os.makedirs(HIST_DIR, exist_ok=True)
except Exception: pass
HIST_BARS = {"M1": 5000, "M5": 16000, "M15": 6000, "H1": 2200, "H4": 2500}  # ≈ 4d/55d/62d/91d/416d
HIST_TTL = 21600        # deep history rarely changes (old bars are fixed); the live right
                        # edge is kept fresh by merging the engine CSV in read_ohlcv. So we
                        # reuse the cached deep file for 6h and avoid the ~16s re-pagination.
_hist_ts = {}


def _binance_klines_deep(ticker, interval, total):
    """Paginate Binance klines backward (1000/req) to assemble deep history."""
    frames = []; end = None; got = 0
    while got < total:
        n = min(1000, total - got)
        params = {"symbol": ticker, "interval": interval, "limit": n}
        if end is not None:
            params["endTime"] = end
        r = requests.get("https://api.binance.com/api/v3/klines", params=params, timeout=15)
        r.raise_for_status()
        rows = r.json()
        if not rows:
            break
        frames.append(rows)
        end = int(rows[0][0]) - 1     # next page ends just before the oldest bar we have
        got += len(rows)
        if len(rows) < n:
            break
    if not frames:
        raise RuntimeError(f"no klines for {ticker} {interval}")
    rows = [x for chunk in reversed(frames) for x in chunk]   # oldest → newest
    df = pd.DataFrame(rows, columns=["ot", "o", "h", "l", "c", "v", "ct",
                                     "qv", "n", "tb", "tq", "ig"])
    df["Time"] = pd.to_datetime(df["ot"].astype("int64"), unit="ms")
    for col, src in [("Open", "o"), ("High", "h"), ("Low", "l"), ("Close", "c"), ("Vol", "v")]:
        df[col] = df[src].astype(float)
    df["Dur"] = 0
    return df[["Time", "Open", "High", "Low", "Close", "Dur", "Vol"]]


def ensure_history(sym, tf):
    """Make sure a deep-history CSV exists for charting (Binance symbols only; Yahoo
    already pulls a long period). Cached on disk + in memory for HIST_TTL."""
    kind, ticker = SYMBOLS.get(sym, (None, None))
    if not _is_binance(kind):
        return None
    path = os.path.join(HIST_DIR, f"{sym}_{tf}.csv")
    now = time.time()
    last = _hist_ts.get((sym, tf)) or (os.path.getmtime(path) if os.path.exists(path) else 0)
    if os.path.exists(path) and (now - last < HIST_TTL):
        return path
    try:
        df = _binance_klines_deep(ticker, BINANCE_INTERVAL[tf], HIST_BARS.get(tf, 5000))
        df = df.dropna().drop_duplicates("Time").sort_values("Time")
        df["Time"] = pd.to_datetime(df["Time"]).dt.strftime("%Y-%m-%d %H:%M:%S")
        df.to_csv(path, sep="\t", index=False)
        _hist_ts[(sym, tf)] = now
    except Exception as e:
        print(f"[live_feed] history {sym} {tf} failed: {str(e)[:80]}", flush=True)
    return path if os.path.exists(path) else None


# --- Yahoo (gold) ----------------------------------------------------------- #
def _yahoo_ohlc(ticker, tf):
    import yfinance as yf
    interval = {"M1": "1m", "M5": "5m", "M15": "15m", "H1": "60m", "H4": "60m"}[tf]
    # pull Yahoo's maximum allowed window per interval so gold also scrolls back months
    period = {"M1": "7d", "M5": "60d", "M15": "60d", "H1": "730d", "H4": "730d"}[tf]
    df = yf.download(ticker, interval=interval, period=period,
                     progress=False, auto_adjust=False)
    if df is None or len(df) == 0:
        raise RuntimeError(f"yahoo empty for {ticker} {tf}")
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df.reset_index()
    tcol = "Datetime" if "Datetime" in df.columns else "Date"
    out = pd.DataFrame()
    t = pd.to_datetime(df[tcol])
    if getattr(t.dt, "tz", None) is not None:
        t = t.dt.tz_convert("UTC").dt.tz_localize(None)
    out["Time"] = t
    for col in ["Open", "High", "Low", "Close"]:
        out[col] = df[col].astype(float)
    out["Dur"] = 0
    out["Vol"] = df["Volume"].astype(float) if "Volume" in df.columns else 0.0
    if tf == "H4":   # Yahoo has no 4h intraday -> resample 1h to 4h
        out = (out.set_index("Time")
                  .resample("4h").agg({"Open": "first", "High": "max", "Low": "min",
                                       "Close": "last", "Dur": "first", "Vol": "sum"})
                  .dropna().reset_index())
    return out.dropna()


def _write_csv(sym, tf, df):
    path = os.path.join(DATA_DIR, f"{sym}_{tf}.csv")
    df = df.dropna().drop_duplicates("Time").sort_values("Time")
    df["Time"] = pd.to_datetime(df["Time"]).dt.strftime("%Y-%m-%d %H:%M:%S")
    df.to_csv(path, sep="\t", index=False)
    return path


def refresh(sym, force=False):
    """Fetch all engine TFs for `sym` and write CSVs. Cached for REFRESH_SEC."""
    if sym not in SYMBOLS:
        raise KeyError(f"unknown symbol {sym}")
    now = time.time()
    if not force and (now - _last_refresh.get(sym, 0) < REFRESH_SEC):
        return False
    kind, ticker = SYMBOLS[sym]
    for tf in ENGINE_TFS:
        try:
            if _is_binance(kind):
                df = _binance_klines(ticker, BINANCE_INTERVAL[tf], TF_LIMIT[tf])
            else:
                df = _yahoo_ohlc(ticker, tf)
            _write_csv(sym, tf, df)
        except Exception as e:
            # keep any previously-written CSV; log and continue
            print(f"[live_feed] {sym} {tf} fetch failed: {str(e)[:80]}", flush=True)
    _last_refresh[sym] = now
    # tell the engine to re-read this symbol's frames
    try:
        import rtm_bt as B
        B.clear_cache(sym)
    except Exception:
        pass
    return True


def last_price(sym):
    """Cheap real-time last price (Binance ticker / Yahoo fast_info)."""
    kind, ticker = SYMBOLS[sym]
    try:
        if _is_binance(kind):
            r = requests.get("https://api.binance.com/api/v3/ticker/price",
                             params={"symbol": ticker}, timeout=8)
            r.raise_for_status()
            return float(r.json()["price"]), False
        else:
            import yfinance as yf
            fi = yf.Ticker(ticker).fast_info
            px = fi.get("last_price") or fi.get("lastPrice")
            return (float(px) if px else None), True
    except Exception as e:
        print(f"[live_feed] last_price {sym} failed: {str(e)[:60]}", flush=True)
        return None, (kind == "gold")


def read_ohlcv(sym, tf, limit=500, deep=False):
    """Read a written CSV back as records for the chart. With deep=True, serve the
    deep-history file (months of bars) and overlay the fresh engine tail so the newest
    candle stays live."""
    eng_path = os.path.join(DATA_DIR, f"{sym}_{tf}.csv")
    hist_path = os.path.join(HIST_DIR, f"{sym}_{tf}.csv")
    if deep and os.path.exists(hist_path):
        df = pd.read_csv(hist_path, sep="\t")
        if os.path.exists(eng_path):     # keep the right edge fresh (≤30s old)
            eng = pd.read_csv(eng_path, sep="\t")
            df = (pd.concat([df, eng]).drop_duplicates("Time", keep="last")
                    .sort_values("Time"))
    elif os.path.exists(eng_path):
        df = pd.read_csv(eng_path, sep="\t")
    else:
        return []
    df = df.tail(limit)
    out = []
    for _, r in df.iterrows():
        out.append({"time": str(r["Time"]),
                    "open": float(r["Open"]), "high": float(r["High"]),
                    "low": float(r["Low"]), "close": float(r["Close"])})
    return out


# --- historical price lookup (for honest prediction scoring) ---------------- #
_series_cache = {}   # (path, mtime) -> (np int64 times, np float closes)

def _series(path):
    if not os.path.exists(path):
        return None
    mt = os.path.getmtime(path)
    ck = (path, mt)
    cached = _series_cache.get(ck)
    if cached is None:
        try:
            df = pd.read_csv(path, sep="\t")
            t = (pd.to_datetime(df["Time"]).astype("int64") // 10**9).to_numpy()
            c = df["Close"].astype(float).to_numpy()
        except Exception:
            return None
        order = np.argsort(t)
        cached = (t[order], c[order])
        if len(_series_cache) > 60:
            _series_cache.clear()
        _series_cache[ck] = cached
    return cached


_ohlc_cache = {}   # (path, mtime) -> (t, h, l, c)

def _ohlc(path):
    if not os.path.exists(path):
        return None
    mt = os.path.getmtime(path)
    ck = (path, mt)
    cached = _ohlc_cache.get(ck)
    if cached is None:
        try:
            df = pd.read_csv(path, sep="\t")
            t = (pd.to_datetime(df["Time"]).astype("int64") // 10**9).to_numpy()
            h = df["High"].astype(float).to_numpy()
            l = df["Low"].astype(float).to_numpy()
            c = df["Close"].astype(float).to_numpy()
        except Exception:
            return None
        order = np.argsort(t)
        cached = (t[order], h[order], l[order], c[order])
        if len(_ohlc_cache) > 40:
            _ohlc_cache.clear()
        _ohlc_cache[ck] = cached
    return cached


def forward_path(sym, tf, ts):
    """OHLC bars with time > ts (merged deep+engine history) — for resolving a zone setup's
    SL/TP outcome going forward. Returns (t, high, low, close) arrays or None."""
    best = None
    for p in (os.path.join(HIST_DIR, f"{sym}_{tf}.csv"),
              os.path.join(DATA_DIR, f"{sym}_{tf}.csv")):
        s = _ohlc(p)
        if s is None:
            continue
        t, h, l, c = s
        if best is None or len(t) > len(best[0]):
            best = (t, h, l, c)
    if best is None:
        return None
    t, h, l, c = best
    m = t > ts
    return (t[m], h[m], l[m], c[m])


def price_at(sym, tf, eval_t):
    """Close of the first bar at/after `eval_t` (unix sec) from stored history — the price
    AT a prediction's horizon. Returns None when the horizon isn't covered by data yet, so
    a prediction stays pending instead of being scored against the wrong (live) price."""
    best_t = best_c = None
    for p in (os.path.join(DATA_DIR, f"{sym}_{tf}.csv"),
              os.path.join(HIST_DIR, f"{sym}_{tf}.csv")):
        s = _series(p)
        if s is None:
            continue
        t, c = s
        if len(t) == 0 or t[-1] < eval_t:
            continue                          # this file doesn't reach the horizon
        idx = int(np.searchsorted(t, eval_t, side="left"))
        if idx >= len(t):
            continue
        tt = int(t[idx])
        if best_t is None or tt < best_t:
            best_t, best_c = tt, float(c[idx])
    return best_c


if __name__ == "__main__":
    import sys
    s = sys.argv[1] if len(sys.argv) > 1 else "BTCUSDT"
    refresh(s, force=True)
    print(f"refreshed {s}; last price = {last_price(s)}")
    print(f"M5 bars written: {len(read_ohlcv(s, 'M5'))}")
