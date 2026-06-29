#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
swing_lib.py -- shared, LEAK-FREE primitives for the swing-amplitude ("how far does it run,
then turn back?") study. The projection's wavy path currently uses a hand-set amplitude
(0.42*ATR) that under-draws the real counter-swing the user pointed at. This library measures
the REAL forward excursion (in ATR units) and exposes causal features so a model can predict it.

HONEST RULES (the whole study leans on these):
  - Features at bar i use ONLY data[:i+1]  (we reuse exp_idea-2.feature_matrix, which is causal).
  - Excursion at bar i is the LABEL (it looks forward over [i+1, i+H]); that's fine for a target,
    but any predictor of it must be built from the causal features only, and validated
    leave-symbols-out and/or out-of-time.
  - Reach is measured on intrabar High/Low (the true distance price travelled), not just closes.

Reused from exp_idea-2.py (imported by path; the filename has a hyphen):
  load_json, feature_matrix, build_score, fit_logistic, predict_p, SYMBOLS, TFS, WARMUP, THR, HMAIN.

Run (stats):  PYTHONIOENCODING=utf-8 python backtest/swing_lib.py
"""
import os, json, importlib.util
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_spec = importlib.util.spec_from_file_location("exp2", os.path.join(ROOT, "backtest", "exp_idea-2.py"))
exp2 = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(exp2)

SYMBOLS, TFS, WARMUP, THR, HMAIN = exp2.SYMBOLS, exp2.TFS, exp2.WARMUP, exp2.THR, exp2.HMAIN
# horizons over which we measure "how far it runs then turns" — match the projection's reach
HSWING = {"M5": 48, "M15": 48, "H1": 48}


def load_ohlc(sym, tf):
    a = exp2.load_json(sym, tf)
    if a is None:
        return None
    return a[:, 0], a[:, 1], a[:, 2], a[:, 3], a[:, 4]   # t, o, h, l, c


def atr_of(h, l, c, p=14):
    import rtm_bt as B
    return B.atr_rma(h, l, c, p)


def excursions(h, l, c, atr, H):
    """Per-bar forward reach over [i+1, i+H], on intrabar High/Low, normalised by ATR[i].
    Returns (up_atr, dn_atr, kup, kdn):
      up_atr[i] = (max High in window - c[i]) / atr[i]   -> furthest price ran UP
      dn_atr[i] = (c[i] - min Low in window)  / atr[i]   -> furthest price ran DOWN
      kup[i]/kdn[i] = bar offset (1..H) where that up/down extreme occurred (the turn point).
    NaN where the window is incomplete or atr<=0."""
    n = len(c)
    up = np.full(n, np.nan); dn = np.full(n, np.nan)
    kup = np.zeros(n, int); kdn = np.zeros(n, int)
    for i in range(n):
        j = min(n, i + 1 + H)
        if i + 1 >= j:
            continue
        a = atr[i]
        if not (a > 0):
            continue
        fh = h[i + 1:j]; fl = l[i + 1:j]
        up[i] = (fh.max() - c[i]) / a; kup[i] = int(fh.argmax()) + 1
        dn[i] = (c[i] - fl.min()) / a; kdn[i] = int(fl.argmin()) + 1
    return up, dn, kup, kdn


def dataset(tf, H):
    """Pooled, causal feature matrix + excursion labels for all symbols of one TF.
    Returns dict with X (causal features), atr, c, up_atr, dn_atr, kup, kdn, move (terminal),
    flat mask, symbol id per row, and bar time. Rows are the evaluable bars (WARMUP..n-H-1)."""
    Xs, ats, cs, ups, dns, kus, kds, mvs, fls, sid, tts = ([] for _ in range(11))
    for si, sym in enumerate(SYMBOLS):
        o = load_ohlc(sym, tf)
        if o is None:
            continue
        t, op, h, l, c = o
        n = len(c)
        atr = atr_of(h, l, c, 14)
        X, *_ = exp2.feature_matrix(t, op, h, l, c, tf)
        up, dn, kup, kdn = excursions(h, l, c, atr, H)
        last = n - H - 1
        idx = np.arange(WARMUP, last)
        if len(idx) == 0:
            continue
        move = (c[idx + H] - c[idx]) / c[idx]
        Xs.append(X[idx]); ats.append(atr[idx]); cs.append(c[idx])
        ups.append(up[idx]); dns.append(dn[idx]); kus.append(kup[idx]); kds.append(kdn[idx])
        mvs.append(move); fls.append(np.abs(move) <= THR)
        sid.append(np.full(len(idx), si)); tts.append(t[idx])
    if not Xs:
        return None
    return dict(X=np.vstack(Xs), atr=np.concatenate(ats), c=np.concatenate(cs),
                up=np.concatenate(ups), dn=np.concatenate(dns),
                kup=np.concatenate(kus), kdn=np.concatenate(kds),
                move=np.concatenate(mvs), flat=np.concatenate(fls),
                sym=np.concatenate(sid).astype(int), t=np.concatenate(tts))


def main():
    print("=" * 76)
    print("SWING-AMPLITUDE baseline stats — real forward reach (ATR units) vs the 0.42*ATR draw")
    print("=" * 76)
    out = {}
    for tf in TFS:
        H = HSWING[tf]
        d = dataset(tf, H)
        if d is None:
            print(f"[{tf}] no data"); continue
        m = np.isfinite(d["up"]) & np.isfinite(d["dn"])
        up, dn = d["up"][m], d["dn"][m]
        kup, kdn = d["kup"][m], d["kdn"][m]
        def q(a, p): return float(np.nanpercentile(a, p))
        rec = dict(
            n=int(m.sum()), H=H,
            up_med=q(up, 50), up_mean=float(np.nanmean(up)), up_p25=q(up, 25), up_p75=q(up, 75), up_p90=q(up, 90),
            dn_med=q(dn, 50), dn_mean=float(np.nanmean(dn)), dn_p25=q(dn, 25), dn_p75=q(dn, 75), dn_p90=q(dn, 90),
            kup_med=float(np.median(kup)), kdn_med=float(np.median(kdn)),
            both_med=q(np.maximum(up, dn), 50),
        )
        out[tf] = rec
        print(f"\n[{tf}]  H={H}  n={rec['n']}")
        print(f"   UP reach  (ATR):  p25={rec['up_p25']:.2f}  median={rec['up_med']:.2f}  "
              f"mean={rec['up_mean']:.2f}  p75={rec['up_p75']:.2f}  p90={rec['up_p90']:.2f}")
        print(f"   DOWN reach(ATR):  p25={rec['dn_p25']:.2f}  median={rec['dn_med']:.2f}  "
              f"mean={rec['dn_mean']:.2f}  p75={rec['dn_p75']:.2f}  p90={rec['dn_p90']:.2f}")
        print(f"   turn bar (median): up@{rec['kup_med']:.0f}  down@{rec['kdn_med']:.0f}  of {H}")
        print(f"   --> current draw amplitude = 0.42 ATR; real median reach ~ {rec['both_med']:.2f} ATR "
              f"({rec['both_med']/0.42:.1f}x bigger)")
    json.dump(out, open(os.path.join(os.path.dirname(__file__), "swing_stats.json"), "w"), indent=2)
    print("\nwrote swing_stats.json")
    return out


if __name__ == "__main__":
    main()
