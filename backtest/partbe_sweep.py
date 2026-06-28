#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Partial+BE operating-point sweep over a bt_structure rows CSV.

Given the per-setup rows (which carry `mfe` = max favorable excursion in R and the
per-rule outcomes), sweep the partial-exit ladder (tp1_R, tp1_frac, runner_R) and
report — for every cell — the HONEST trio together: NET win-rate, full-target hit-rate,
expectancy (expR) and profit-factor (PF).

Key exact identity:  net-win-rate = P(mfe >= tp1_R)   (banking the first partial is a
win regardless of the runner) — so the net win-rate column is EXACT. expR/PF use an
MFE-based estimate of whether the runner reaches its target (slightly optimistic on
full wins; the chosen cell is then re-verified by an exact bt_structure re-run).

Usage:  python3 partbe_sweep.py <rows.csv> [label]
        python3 partbe_sweep.py <rows.csv> --json     # one JSON line (for fan-out reducers)
"""
import sys, json
import numpy as np, pandas as pd

TP1S    = [0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 1.0]
RUNNERS = [1.5, 2.0, 2.5, 3.0]
FRACS   = [0.3333, 0.5]
TARGET_NET = 70.0   # the user's bar


def cell(mfe, tp1, frac, runner):
    """Vectorised realized-R under partial+BE for one (tp1,frac,runner) cell."""
    runner_w = 1.0 - frac
    banked = mfe >= tp1            # reached tp1 -> partial banked (net win, exact)
    full   = mfe >= runner         # runner reaches target (MFE estimate)
    R = np.where(full, frac*tp1 + runner_w*runner,
                 np.where(banked, frac*tp1, -1.0))
    n = len(R)
    wins = R[R > 0]; los = R[R <= 0]
    pf = (wins.sum() / -los.sum()) if (len(los) and los.sum() < 0) else float("inf")
    return dict(tp1=tp1, frac=round(frac, 3), runner=runner, n=int(n),
                net_win=round(100*banked.mean(), 1),
                full_hit=round(100*full.mean(), 1),
                expR=round(float(R.mean()), 3),
                pf=(round(float(pf), 2) if np.isfinite(pf) else None))


def sweep(df):
    mfe = df["mfe"].astype(float).values
    cells = [cell(mfe, t, f, r) for t in TP1S for f in FRACS for r in RUNNERS]
    return cells


def main():
    if len(sys.argv) < 2:
        print(__doc__); sys.exit(1)
    path = sys.argv[1]
    as_json = "--json" in sys.argv[2:]
    label = next((a for a in sys.argv[2:] if not a.startswith("--")), path.split("/")[-1])
    df = pd.read_csv(path)
    df = df[df["mfe"].notna()]
    cells = sweep(df)
    # operating points: net_win >= TARGET_NET AND expR>0 AND pf>1, best expR first
    ok = [c for c in cells if c["net_win"] >= TARGET_NET and c["expR"] > 0 and (c["pf"] or 0) > 1]
    ok.sort(key=lambda c: c["expR"], reverse=True)
    best_exp = max(cells, key=lambda c: c["expR"])
    best_at_target = ok[0] if ok else None
    out = dict(label=label, n=int(len(df)),
               mfe_ge_05=round(100*(df["mfe"] >= 0.5).mean(), 1),
               mfe_ge_1=round(100*(df["mfe"] >= 1.0).mean(), 1),
               mfe_ge_15=round(100*(df["mfe"] >= 1.5).mean(), 1),
               mfe_ge_2=round(100*(df["mfe"] >= 2.0).mean(), 1),
               best_expR=best_exp, best_at_target=best_at_target,
               n_target_cells=len(ok))
    if as_json:
        print(json.dumps(out)); return
    print(f"\n=== {label}  ({len(df)} setups) ===")
    print(f"  MFE reach: >=0.5R {out['mfe_ge_05']}%  >=1R {out['mfe_ge_1']}%  "
          f">=1.5R {out['mfe_ge_15']}%  >=2R {out['mfe_ge_2']}%")
    print(f"  {'tp1':>4} {'frac':>5} {'run':>4} {'net%':>6} {'full%':>6} {'expR':>7} {'PF':>5}")
    for c in sorted(cells, key=lambda c: (-c["net_win"], -c["expR"])):
        flag = "  <= TARGET+edge" if (c["net_win"] >= TARGET_NET and c["expR"] > 0 and (c["pf"] or 0) > 1) else ""
        print(f"  {c['tp1']:>4} {c['frac']:>5} {c['runner']:>4} {c['net_win']:>6} "
              f"{c['full_hit']:>6} {c['expR']:>+7.3f} {str(c['pf']):>5}{flag}")
    print(f"\n  best expR cell : {best_exp}")
    print(f"  >=70% net & +expR & PF>1 : {best_at_target if best_at_target else 'NONE in this config'}")


if __name__ == "__main__":
    main()
