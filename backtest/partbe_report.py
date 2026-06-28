#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Exact partial+BE metrics from a bt_structure rows CSV (uses the simulated R_partbe /
partstat columns, NOT an MFE estimate). Prints one JSON line for fan-out reducers.

Usage:  python3 partbe_report.py <rows.csv> [label]
"""
import sys, json
import numpy as np, pandas as pd


def pf_of(R):
    R = np.asarray(R, float)
    pos = R[R > 0].sum(); neg = R[R <= 0].sum()
    return round(float(pos / -neg), 2) if neg < 0 else None


def main():
    path = sys.argv[1]
    label = sys.argv[2] if len(sys.argv) > 2 else path.split("/")[-1]
    df = pd.read_csv(path)
    out = {"label": label, "n": int(len(df))}
    # exact partial+BE (simulated runner with break-even stop)
    if "R_partbe" in df and df["R_partbe"].notna().any():
        pb = df[df["R_partbe"].notna()].copy()
        R = pb["R_partbe"].astype(float)
        st = pb["partstat"].astype(str)
        out.update(
            pb_n=int(len(pb)),
            net_win=round(100*(R > 0).mean(), 1),
            full_hit=round(100*(st == "WIN").mean(), 1),
            partial_only=round(100*(st == "PARTIAL_WIN").mean(), 1),
            clean_loss=round(100*(st == "LOSS").mean(), 1),
            expR=round(float(R.mean()), 3),
            pf=pf_of(R),
            netR=round(float(R.sum()), 1),
        )
    # plain 2R reference
    if "2R" in df:
        r2 = df[df["2R"].notna()]["2R"].astype(float)
        if len(r2):
            out.update(wr_2r=round(100*(r2 > 0).mean(), 1), pf_2r=pf_of(r2),
                       expr_2r=round(float(r2.mean()), 3))
    print(json.dumps(out))


if __name__ == "__main__":
    main()
