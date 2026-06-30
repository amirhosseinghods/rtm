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

    # 4) LEARN FROM THE STOPS: per source, are stops sweeps (too tight) or givebacks (too loose)?
    #    Widen/tighten stops.buf_atr accordingly; auto-disable a source that keeps stopping at a loss.
    #    Guardrails: need >=MIN_N samples, bounded ±0.05 step + [0.15,0.8] clamp, revert by deleting key.
    try:
        ls = SU.lessons_stops(min_n=MIN_N)
    except Exception as e:
        ls = {"by_src": {}, "error": str(e)}
    stops = tuned.get("stops") or {"buf_atr": {"default": 0.3}, "min_atr": {"default": 1.0, "XAUUSD": 2.5}, "disabled_sources": []}
    stops.setdefault("buf_atr", {"default": 0.3}); stops.setdefault("disabled_sources", [])
    for src, g in (ls.get("by_src") or {}).items():
        if not g or g["n"] < MIN_N:
            continue
        cur = float(stops["buf_atr"].get(src, stops["buf_atr"].get("default", 0.3)))
        new = cur
        if g["sweep_frac"] > 0.5:                              # stops cluster at MFE<0.3R -> too tight
            new = round(min(cur + 0.05, cur * 1.5, 0.8), 3)
        elif g["giveback_frac"] > 0.5 and g["stop_rate"] > 55: # reached >=1R then reversed -> too loose / late
            new = round(max(cur - 0.05, cur * 0.66, 0.15), 3)
        if abs(new - cur) > 1e-9:
            stops["buf_atr"][src] = new
            changes.append(f"stops.buf_atr[{src}] {cur}->{new} (sweep{g['sweep_frac']} gb{g['giveback_frac']})")
        # auto-mute a chronically losing source — but JUDGE IT ON ITS ACTIONABLE (recommended) trades,
        # not the raw context-zone population. Muting happens BEFORE the actionable gate, so a source
        # whose gated trades win (e.g. OB-1h ~80%) must NOT be disabled by its un-traded zones.
        ga = (ls.get("by_src_actionable") or {}).get(src)
        gj = ga if (ga and ga["n"] >= MIN_N) else None     # prefer actionable verdict; skip if too few
        if gj is not None:
            if gj["stop_rate"] > 70 and (gj["expR"] or 0) < 0 and src not in stops["disabled_sources"]:
                stops["disabled_sources"].append(src)
                changes.append(f"source {src} auto-disabled (actionable stop {gj['stop_rate']}% expR {gj['expR']:+})")
            elif gj["stop_rate"] <= 60 and (gj["expR"] or 0) > 0 and src in stops["disabled_sources"]:
                stops["disabled_sources"].remove(src)            # recovered -> re-enable
                changes.append(f"source {src} re-enabled (actionable stop {gj['stop_rate']}% expR {gj['expR']:+})")
    tuned["stops"] = stops
    if ls.get("overall"):
        log.append(f"- استاپ‌ها: { {k: ls['by_src'][k]['stop_rate'] for k in ls.get('by_src', {})} } "
                   f"(sweep/giveback از روی MFE)")

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
