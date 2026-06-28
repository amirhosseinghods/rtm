#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
optimize.py — the periodic optimization step (run by the scheduled agent or by cron).

It learns from the REAL forward data the 24/7 server accumulated:
  1. Close out due predictions + zone setups (score_due / setup_store.resolve).
  2. Recompute learned accuracy (per symbol, per confluence) and setup stop-rates.
  3. Re-derive the tuned knobs in tuned.json FROM THOSE REAL OUTCOMES — e.g. the reversal
     edge, and whether reversal setups should stay enabled (auto-disable if they stop paying).
  4. Write tuned.json (read live by the service) + optimize_report.md.

Honest + safe: only updates a knob when there's a meaningful sample; never invents an edge.
The service reads tuned.json live, so the next analysis uses the fresh knobs without restart.

Run:  cd ~/Desktop/trade/web && python3 optimize.py
"""
import os, sys, json, time, datetime
HERE = os.path.dirname(__file__)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "backtest")))
import learning_store as LS
import setup_store as SU
import live_feed as F

TUNED_PATH = os.path.join(HERE, "tuned.json")
REPORT = os.path.join(HERE, "learning", "optimize_report.md")
MIN_N = 40          # don't move a knob on fewer than this many resolved samples


def _load_tuned():
    try:
        return json.load(open(TUNED_PATH, encoding="utf-8"))
    except Exception:
        return {}


def run(stamp=None):
    stamp = stamp or datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    log = [f"# گزارشِ بهینه‌سازی — {stamp}\n"]

    # 1) close out everything that has resolved since last run
    try:
        sd = LS.score_due(F.price_at)
    except Exception as e:
        sd = {"error": str(e)}
    try:
        rs = SU.resolve()
    except Exception as e:
        rs = {"error": str(e)}
    log.append(f"- پیش‌بینی‌های امتیازخورده‌شده: {sd}")
    log.append(f"- ناحیه‌های resolve‌شده: {rs}\n")

    tuned = _load_tuned()
    changes = []

    # 2) learned direction accuracy per symbol  -> tuned.per_symbol_dir_rate
    lsum = LS.summary()
    psr = {b: v["rate"] for b, v in lsum.get("buckets", {}).items()
           if v.get("n", 0) >= MIN_N and v.get("rate") is not None}
    if psr:
        tuned["per_symbol_dir_rate"] = psr
        changes.append(f"نرخِ جهتیِ {len(psr)} نماد به‌روز شد")
    log.append(f"- جهتِ کلی: rate={lsum['overall']['rate']} روی {lsum['overall']['n']} | "
               f"by_combo={ {k: v.get('rate') for k, v in lsum.get('by_combo', {}).items()} }")

    # 3) reversal edge FROM REAL accumulated setups -> tuned.reversal_edge + reversal_enabled
    les = SU.lessons()
    rev = (les.get("by", {}).get("setup_type", {}) or {}).get("reversal")
    if rev and rev.get("n", 0) >= MIN_N and rev.get("expR") is not None:
        tuned["reversal_edge"] = {"expR": rev["expR"], "wr": rev["win_rate"],
                                  "n": rev["n"], "rr": tuned.get("reversal_edge", {}).get("rr", 3.0)}
        enabled = rev["expR"] > 0.0
        if enabled != tuned.get("reversal_enabled", True):
            changes.append(f"reversal_enabled -> {enabled} (expR {rev['expR']:+})")
        tuned["reversal_enabled"] = enabled
        changes.append(f"reversal_edge از دیتای واقعی: expR {rev['expR']:+}, WR {rev['win_rate']}%, n{rev['n']}")
        log.append(f"- بازگشتی (واقعی): {rev}")
    else:
        log.append(f"- بازگشتی: نمونهٔ کافی نیست (n<{MIN_N}) — اج seed دست‌نخورده ماند")

    # overall setup health
    ov = les.get("overall")
    if ov:
        log.append(f"- کلِ ناحیه‌ها: stop_rate {ov['stop_rate']}% expR {ov['expR']} روی {ov['n']}\n")
    if les.get("by", {}).get("combo_confirmed"):
        log.append(f"- combo_confirmed: { {k: v['expR'] for k, v in les['by']['combo_confirmed'].items()} }")

    tuned["updated"] = stamp
    json.dump(tuned, open(TUNED_PATH, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    log.append("\n## تغییرات\n" + ("\n".join(f"- {c}" for c in changes) if changes
                                   else "- تغییرِ معناداری لازم نشد (نمونهٔ کافی نبود)."))
    os.makedirs(os.path.dirname(REPORT), exist_ok=True)
    open(REPORT, "w", encoding="utf-8").write("\n".join(log) + "\n")
    print("\n".join(log))
    print(f"\nنوشته شد: {TUNED_PATH}\nگزارش: {REPORT}")


if __name__ == "__main__":
    run()
