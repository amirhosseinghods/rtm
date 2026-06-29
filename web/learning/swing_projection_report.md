# Swing-Amplitude Projection + Win-Rate Study
گزارشِ ارتقای «قوسِ پیش‌بینی» و نرخِ برد

Date: 2026-06-29 · Scope: projection arc amplitude (`rsi_tools.project`), the reach/target display, and a high-volume out-of-time win-rate search.

---

## TL;DR / خلاصه

1. **The arc was drawn ~7–13× too small.** Measured over **13 symbols × 3 TFs (~140k bars)**, the real median forward reach is **~2.6–3.7 ATR** (mean ~3.5–4.6), but the projection drew a fixed **0.42 ATR** wave. Price typically turns around the **mid-point** of the horizon. → The projection now sizes the counter-swing to the median adverse reach and runs the net trend to the median favourable reach, peaking at the empirical turn-bar. Live path range went from ~2 ATR to **~3.3–3.8 ATR**.
2. **Honest reach model.** Conditioning the reach on the 8 causal features did **not** beat the constant median (leave-symbols-out MAE improved only ~0.00–0.01 ATR — noise), so the **per-TF constant median** ships (`tuned.json::swing_model`), no false precision.
3. **Win-rate search (47 agents, out-of-time + leave-symbols-out).** Only **M5** earned a robust upgrade: with-trend, high-conviction, TP=0.5·favReach / SL=1.5·advReach, τ=0.06 → test win-rate **0.497 → 0.717** (LOSO 0.717, expR +0.028, coverage 0.177 ≈ half baseline). **M15 & H1 honestly stay at baseline** (every high-win-rate alt collapsed coverage below half). The verifiers correctly **rejected the traps** — e.g. TP 0.4 / SL 2 hit 0.76 win-rate but **negative** expR ("win often, lose big").

---

## What measured the reach / اندازه‌گیری

`backtest/swing_lib.py` — for each bar *i*, forward reach over `[i+1, i+H]` on intrabar High/Low, ATR-normalised:
- `up_atr = (max High − close[i]) / ATR[i]`, `dn_atr = (close[i] − min Low) / ATR[i]`, plus the turn-bar offset.
- Causal features reused from `exp_idea-2.feature_matrix`; reach is the forward label (validated leave-symbols-out / out-of-time, never used for the features).

| TF | median UP reach | median DOWN reach | turn bar | legacy draw |
|----|----|----|----|----|
| M5 | 2.91 ATR | 3.18 ATR | ~mid (0.42–0.48) | **0.42 ATR** |
| M15 | 2.60 ATR | 3.70 ATR | ~mid (0.38–0.52) | 0.42 ATR |
| H1 | 2.79 ATR | 3.24 ATR | ~mid (0.44–0.48) | 0.42 ATR |

`backtest/fit_swing_model.py` confirmed conditioning ≈ constant → ship constants (`swing_model`).

## The new projection shape / شکلِ جدید

`rsi_tools.project()` now (when `swing` is supplied):
- `amp = clip(0.55 · adverseReach, 0.42, 4.5) · ATR` — the **counter-swing leads against the trend** at realistic magnitude (the user's "price pops up, THEN drops", and the reverse), rising to the peak at the empirical turn-bar then returning.
- `drift` is scaled so the **net trend reaches ~favourableReach** by the horizon.
- Zone snap / bounce-break events and the Persian scenario are unchanged.
- A new **`reach`** block is returned: `{target, stop, turn_time, turn_price, fav_atr, adv_atr, tp_mult, sl_mult, winrate, regime_ok}` — the "how far does it run, then turn back?" estimate, drawn as a target line + a "چرخشِ احتمالی" marker, with an honest win-rate badge shown only when the setup matches the regime the win-rate was measured on.

## Win-rate backtest / بک‌تستِ نرخِ برد

`backtest/swing_trade_eval.py` — leak-free TP/SL trade simulation (projection direction from the fixed, walk-forward-validated logistic; TP/SL sized from the reach model; intrabar fill; fixed 70/30 time split + leave-symbols-out). The **swing-winrate-search** workflow ran **47 agents**: 24 searched config lanes on the TRAIN split, finalists were adversarially re-scored out-of-time + LOSO, then synthesised.

| TF | shipped cfg | test win-rate | expR | coverage | verdict |
|----|----|----|----|----|----|
| M5 | with_trend, tp 0.5, sl 1.5, τ 0.06 | **0.717** (was 0.497) | +0.028 | 0.177 | **upgrade (LOSO 0.717)** |
| M15 | baseline (τ 0.02, 1/1) | 0.509 | +0.057 | 0.815 | keep baseline |
| H1 | baseline (τ 0, 1/1) | 0.523 | +0.086 | 1.00 | keep baseline |

Honest caveat: the M5 winner trades ~half as often as baseline and its per-trade expR is thin (+0.028) though positive. A higher-expR alternative exists (τ 0.08, tp 0.6, sl 1.0 → win-rate 0.646, **expR +0.085**, coverage 0.078) if expectancy is preferred over raw win-rate. Frozen in `tuned.json::swing_trade`.

## Zones consistent with the projection / سازگاریِ نواحی

`signal_service` tags every zone `proj_aligned` / `proj_against` (its direction vs the projection's). The chart labels and the zone popover show «✓ هم‌جهت با پیش‌بینی» / «⚠ خلافِ پیش‌بینی». The win-rate study found **with-trend (aligned) entries are exactly what carries the M5 edge**, so alignment is the actionable signal.

## Rollback / بازگشت
- Remove `swing_model` from `tuned.json` → projection arc reverts to the legacy 0.42·ATR draw.
- Remove `swing_trade` → the reach target/badge disappears (path unchanged).
- Both are read live (no restart); the CI optimizer preserves them.
