#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Self-updating learning loop: greedily select filters on TRAIN, validate on TEST (walk-forward).
Learns 'which decisions work' from the ~14k corpus WITHOUT overfitting (test = held-out future)."""
import pandas as pd, numpy as np, datetime, json
corp=pd.read_csv("research_trades.csv")
corp["dpAligned"]=(((corp.dr==1)&(corp.disc==1))|((corp.dr==-1)&(corp.prem==1))).astype(int)
# time split: within each sym/tf, first 60% bars = TRAIN (past), last 40% = TEST (future)
corp["rk"]=corp.groupby(["sym","tf"])["i"].rank(pct=True)
train=corp[corp.rk<=0.6].copy(); test=corp[corp.rk>0.6].copy()
def expo(d): return float(d.R.mean()) if len(d) else -9.0
def wr(d): return round(100*(d.R>0).mean(),1) if len(d) else 0

CANDS={
 "score>=88":   lambda d: d.score>=88,
 "score>=90":   lambda d: d.score>=90,
 "score>=100":  lambda d: d.score>=100,
 "grade==2":    lambda d: d.grade>=2,
 "type=1h":     lambda d: d.type=="OB-1h",
 "atr%>=0.06":  lambda d: d.atrpct>=0.06,
 "atr%>=0.10":  lambda d: d.atrpct>=0.10,
 "atr%>=0.15":  lambda d: d.atrpct>=0.15,
 "dpAligned":   lambda d: d.dpAligned==1,
 "sharp>=1.5":  lambda d: d.approach>=1.5,
 "sharp>=2.5":  lambda d: d.approach>=2.5,
 "gentle<1.5":  lambda d: d.approach<1.5,
 "Heff<0.6":    lambda d: d.heff1<0.6,
 "Heff<0.4":    lambda d: d.heff1<0.4,
 "Hvel_in<0.3": lambda d: d.hsharp1<0.3,
 "Hvel_in0":    lambda d: d.hsharp1<=0.001,
 "Hv4_in<0.3":  lambda d: d.hsharp4<0.3,
 "noSharpHTF":  lambda d: (d.hsharp1<0.4)&(d.heff1<0.7),
 "counter":     lambda d: d.withtrend==0,
 "withtrend":   lambda d: d.withtrend==1,
 "short":       lambda d: d.dr==-1,
 "long":        lambda d: d.dr==1,
}
print(f"corpus {len(corp)} | train {len(train)} | test {len(test)}")
print(f"baseline: train exp {expo(train):+.3f} (wr{wr(train)}) | TEST exp {expo(test):+.3f} (wr{wr(test)})\n")

sel=[]; ctr=train; cte=test; log=[]
for step in range(7):
    best=None; bestexp=expo(ctr)
    for nm,fn in CANDS.items():
        if nm in sel: continue
        sub=ctr[fn(ctr)]
        if len(sub)<250: continue
        if expo(sub)>bestexp+0.003:
            bestexp=expo(sub); best=nm
    if best is None: break
    sel.append(best); ctr=ctr[CANDS[best](ctr)]; cte=cte[CANDS[best](cte)]
    log.append(dict(step=step+1, add=best,
        train_exp=round(expo(ctr),3), train_n=len(ctr), train_wr=wr(ctr),
        TEST_exp=round(expo(cte),3), TEST_n=len(cte), TEST_wr=wr(cte)))

print("=== GREEDY FORWARD SELECTION (learn on TRAIN, validate on TEST/future) ===")
print(pd.DataFrame(log).to_string(index=False))
print("\nLearned rule:", "  AND  ".join(sel))
print(f"FINAL on held-out TEST: n={len(cte)}  WR={wr(cte)}%  exp={expo(cte):+.3f}R  "
      f"netR={cte.R.sum():.0f}  => {'GENERALIZES ✓' if expo(cte)>0 else 'FAILS OOS ✗'}")

# also: best single filters on test (sanity)
print("\n-- each filter's standalone TEST expectancy (vs baseline %.3f) --"%expo(test))
for nm,fn in CANDS.items():
    s=test[fn(test)]
    if len(s)>=200: print(f"  {nm:14s} n={len(s):5d}  exp={expo(s):+.3f}  wr={wr(s)}")

# append learning to a persistent log
with open("LEARNINGS_LOG.md","a") as f:
    f.write(f"\n## Iteration @ corpus={len(corp)} trades\n")
    f.write(f"- Learned rule: {' AND '.join(sel)}\n")
    f.write(f"- TEST(held-out): n={len(cte)} WR={wr(cte)}% exp={expo(cte):+.3f}R {'GENERALIZES' if expo(cte)>0 else 'FAILS'}\n")
print("\n(appended to LEARNINGS_LOG.md)")
