#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Backtest each RTM concept as an ADDITION to the validated baseline
(M5 + 1h-anchored zones + room>=2R + grade2 + ATR% + with-trend), resolving every
entry at the 2R rule. Reports n / stop% / WR / expR / PF and an OOS first-vs-second
half split, so we keep ONLY concepts that improve expR/PF over the baseline.

Run:  python3 rtm_concept_test.py          # M5, whole Downloads watchlist
      python3 rtm_concept_test.py M15      # other entry TF
"""
import os, sys
os.environ["FD_SRC"] = "1h"                     # validated: 1h-anchored zones only
import numpy as np, pandas as pd
import rtm_bt as B, bt_structure as S

ETF = sys.argv[1] if len(sys.argv) > 1 else "M5"
S.ETF = ETF

# the gates to compare. baseline first; each row ANDs its tokens onto the baseline.
GATES = [
    ("room (baseline)",  "room"),
    ("room+engulf",      "room+engulf"),
    ("room+sweep",       "room+sweep"),
    ("room+pin",         "room+pin"),
    ("room+compress",    "room+compress"),
    ("room+trigger",     "room+trigger"),    # engulf OR sweep OR pin
]

print(f"prepping (once, {ETF})...", flush=True)
Ds = {}
for s, sess in S.syms():
    try:
        Ds[s] = (B.prep_symbol(s, ETF), sess)
    except Exception as e:
        print(f"  {s} err {str(e)[:30]}")
print(f"prepped {len(Ds)} symbols\n", flush=True)


def stats(R):
    R = np.asarray([x for x in R if x is not None], float)
    R = R[~np.isnan(R)]
    n = len(R)
    if n == 0:
        return None
    wins = R[R > 0]; los = R[R <= 0]
    pf = wins.sum() / (-los.sum()) if los.sum() < 0 else float("inf")
    return dict(n=n, stop=100 * (R < 0).mean(), wr=100 * (R > 0).mean(),
                expR=R.mean(), pf=pf, netR=R.sum())


def run(conf):
    S.CONF = conf
    rows = []
    for s, (D, sess) in Ds.items():
        rows += S.collect(D, s, sess)
    return pd.DataFrame(rows)


print(f"{'gate':18s} {'n':>4s} {'stop%':>6s} {'WR%':>5s} {'expR':>7s} {'PF':>5s} "
      f"{'netR':>7s} | {'OOS h0 expR/PF':>14s} {'OOS h1 expR/PF':>14s}")
print("-" * 96)
base_exp = None
for label, conf in GATES:
    df = run(conf)
    if len(df) == 0 or "2R" not in df:
        print(f"{label:18s} 0"); continue
    a = stats(df["2R"])
    if a is None:
        print(f"{label:18s} 0 resolved"); continue
    h0 = stats(df[df["half"] == 0]["2R"]); h1 = stats(df[df["half"] == 1]["2R"])
    def fmt(x):
        if x is None: return "  n/a"
        pf = "inf" if x["pf"] == float("inf") else f"{x['pf']:.2f}"
        return f"{x['expR']:+.2f}/{pf}"
    pf = "inf" if a["pf"] == float("inf") else f"{a['pf']:.2f}"
    flag = ""
    if label.startswith("room (base"):
        base_exp = a["expR"]
    elif base_exp is not None:
        flag = "  <= keeps edge" if a["expR"] >= base_exp else "  (drops edge)"
    print(f"{label:18s} {a['n']:4d} {a['stop']:6.1f} {a['wr']:5.1f} {a['expR']:+7.3f} "
          f"{pf:>5} {a['netR']:+7.1f} | {fmt(h0):>14s} {fmt(h1):>14s}{flag}")
