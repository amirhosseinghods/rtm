#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""How many A-grade setups/day does the VALIDATED rule actually produce?
Answers the user's '3 trades/day' question honestly across syms x timeframes."""
import rtm_bt as B
import pandas as pd, numpy as np

corp = pd.read_csv("research_trades.csv")
# session hour windows (NY hour): crypto NY 8-17, gold London (NY 3-12)
def in_sess(row):
    h = row.hour
    if row.sym == "XAUUSD": return 3 <= h < 12
    return 8 <= h < 17
corp["sess"] = corp.apply(in_sess, axis=1)

# HTF no-sharp (H4 directional leg into zone < 0.3) — hsharp4 already directional-into-zone
corp["htf_ok"] = corp.hsharp4 < 0.3

RULES = {
 "RAW (every touch)":      lambda d: d.index==d.index,
 "grade2+score90":          lambda d: (d.grade>=2)&(d.score>=90),
 "VALIDATED s100":          lambda d: (d.grade>=2)&(d.score>=100)&(d.atrpct>=0.15)&d.htf_ok&d.sess,
 "VALIDATED s90 (looser)":  lambda d: (d.grade>=2)&(d.score>=90)&(d.atrpct>=0.15)&d.htf_ok&d.sess,
 "A-grade no session":      lambda d: (d.grade>=2)&(d.score>=100)&(d.atrpct>=0.15)&d.htf_ok,
}

# day-span per sym (from M5 data)
span_days = {}
for s in ["XAUUSD","BTCUSDT","XRPUSDT"]:
    df = B.load(s, "M5")
    span_days[s] = (df.index[-1]-df.index[0]).total_seconds()/86400.0
total_days = np.mean(list(span_days.values()))
print(f"data span ~{total_days:.0f} days per symbol  (3 symbols)\n")

print(f"{'rule':26s} {'trades':>7s} {'WR%':>5s} {'exp':>7s} {'PF':>5s} {'trd/day(3sym,M5)':>17s} {'trd/day(3sym,M1+M5+M15)':>24s}")
for name, fn in RULES.items():
    sub_all = corp[fn(corp)]
    m5 = sub_all[sub_all.tf=="M5"]
    # per-day rates
    def perday(d, tfset):
        dd = d[d.tf.isin(tfset)]
        # each sym contributes over its span; sum trades / (sum spans)
        tot = sum(len(dd[dd.sym==s]) for s in span_days)
        days = sum(span_days[s] for s in span_days)
        return tot/days if days else 0
    wr = 100*(sub_all.R>0).mean() if len(sub_all) else 0
    w=sub_all[sub_all.R>0].R.sum(); l=-sub_all[sub_all.R<=0].R.sum()
    pf = w/l if l>0 else float('nan')
    print(f"{name:26s} {len(sub_all):7d} {wr:5.1f} {sub_all.R.mean():+7.3f} {pf:5.2f} "
          f"{perday(sub_all,['M5']):17.2f} {perday(sub_all,['M1','M5','M15']):24.2f}")

print("\n-- VALIDATED s100 by symbol (trades/day each) --")
v = corp[(corp.grade>=2)&(corp.score>=100)&(corp.atrpct>=0.15)&corp.htf_ok&corp.sess]
for s in span_days:
    vs = v[v.sym==s]
    for tfset,lab in [(['M5'],'M5'),(['M1','M5','M15'],'M1+M5+M15')]:
        vv = vs[vs.tf.isin(tfset)]
        wr = 100*(vv.R>0).mean() if len(vv) else 0
        print(f"  {s:8s} {lab:10s} n={len(vv):4d}  {len(vv)/span_days[s]:.2f}/day  WR={wr:.0f}%  exp={vv.R.mean() if len(vv) else 0:+.3f}")
