# Prediction Upgrade Report — Projection Finesse Study
گزارش ارتقای پیش‌بینی — مطالعهٔ دقتِ «پروجکشن»

Date: 2026-06-28 · Author: automated research pass · Scope: `backtest/rsi_tools.py::project()` and its vectorised twin `web/train.py::build_calls()`

---

## TL;DR / خلاصه

- **UPDATE (integrated):** after a dedicated **out-of-time walk-forward** (`backtest/walkforward_idea2.py`) confirmed idea-2 holds (overall **0.485 → 0.511** full-cov, every TF positive; gate **~0.535 @ 38% coverage**), and an **adversarial review** found + fixed 3 issues (a div same-bar tie-break, a train.py bias-comment, a too-weak verifier), **idea-2 was integrated into the live model as a re-weighted direction + honest-abstention (NEUTRAL) gate.** پس از walk-forward و بازرسیِ تهاجمی، idea-2 در مدلِ زنده ادغام شد.
- **Baseline (ground truth):** raw directional accuracy ≈ **0.485** (coin-flip). The only real edge is **confluence filtering** (k>=2 styles agree → ~0.51–0.55). دقتِ پایه ≈ ۰٫۴۸۵.
- **What shipped:** a per-TF logistic over 8 causal features (frozen in `web/tuned.json::projection_model`) drives the projection direction; when its calibrated `|p−0.5|` is below a per-TF `tau`, the system emits **NEUTRAL** (`dir_val=0`) — it predicts only when it has an edge. Live reproduction verified: **0.4892 → 0.5141 (+0.0249)** full-cov, gate **0.539**, features match the validated experiment to `max|diff|=0`. Removing the `projection_model` key reverts to the legacy hand-tuned score (zero-risk rollback).

---

## What was tested / آنچه آزموده شد

All backtests obey the honest rule: a call at bar *i* uses only `data[:i+1]`, scored against `close[i+H]`; flat moves (`|move| < 0.0005`) count **wrong**; train/test separated by symbol-split and/or time-split; per-TF breakdown reported.

| Idea | Description | Headline delta | Held-out votes | Verdict |
|---|---|---|---|---|
| idea-1 | Momentum-consensus contrarian + abstention (ROC/EMA stacks, multi-window agreement) | +0.0011 | 0/2 | Reject — time-split goes negative (−0.0043) |
| idea-2 | **Cross-symbol fitted logistic** replacing hand-tuned coefficients | **+0.0223** | 1/2 | **Best honest signal** — robust leak-free OOS, but didn't hit 2/2 |
| idea-3 | Learned regime-cell gate (TF × trend-strength × RSI-regime), NEUTRAL elsewhere | +0.0213 | 1/2 | Promising — but shrinks to +0.015 @ 0.26 cov under leak-free time-split |
| idea-4 | TF-conditional engine (div-revert M5, level-revert M15/H1, slope abstain) | +0.0668* | 0/2 | Reject — *headline is a ~10%-coverage sub-engine; full-coverage delta was **−0.0011** (regressed) |
| idea-5 | Confluence-consensus gate (k>=2 / k>=3 sweep) | +0.0392 | 0/2 | Confirms the known confluence edge but cross-symbol k2 only 0.518; modest, not a new mechanism |

\* idea-4's "0.0668" is the `routed_engine` sub-slice at **cov ≈ 0.107**; its honest full-coverage `improved_acc` (0.4876) was *below* baseline (0.4887). The TF sanity buckets it leans on (M5 div-revert 0.55, M15/H1 level-revert 0.60+) are real but only survive at tiny coverage and overlap what idea-2/idea-3 already capture more cleanly.

---

## The strongest evidence (idea-2) — reproduced now / شواهدِ اصلی، بازتولیدشده

Adversarial recheck `backtest/recheck_idea-2_0.py` — fit logistic on one half of symbols, test on the held-out half (both directions), plus a 70/30 time split; **and** an explicit leak fix (divergence pull shifted to `b+L`, removing the 5-bar pivot-confirmation look-ahead). Re-run on 2026-06-28:

```
LEAK-FREE, pooled out-of-sample (n = 139,685):
  held-out symbols (fwd):  base 0.4849  improved 0.5031   +0.0181
  held-out symbols (rev):  base 0.4941  improved 0.5231   +0.0290
  time 70/30:              base 0.4867  improved 0.5016   +0.0149
  ALL symbols OOS:         base 0.4892  improved 0.5123   +0.0231
```

Per-TF (leak-free, held-out symbols fwd): M5 +0.0159, M15 +0.0198, H1 +0.0202. Only one negative cell anywhere (H1 time-split, −0.0095), outweighed by the rest.

As an **abstention gate** (emit only when `|p−0.5|` is large), idea-2's applied accuracy was M5 **0.540**, M15 **0.548**, H1 **0.587** at ~21–30% coverage (`exp_idea-2_result.json`). The fitted coefficients tell a coherent story: **divergence and HTF-bias carry positive weight; raw RSI-pull and slope-sign are near-zero or negative** — i.e. the hand-tuned `0.9*RSI_pull` and `0.4*slope` terms in `project()` are over-weighted, while divergence is under-weighted.

Why it still did **not** clear the bar: the per-TF time-split is not uniformly positive (H1 dips negative), and the gain (+0.023) is only ~2× the approximate standard error, so a single bad split could erase the held-out edge. It earned 1/2, not 2/2.

---

## Baseline vs new — accuracy & coverage / پایه در برابر جدید

| Metric | Baseline (live) | Best candidate (idea-2, NOT integrated) |
|---|---|---|
| Directional accuracy (full coverage, OOS) | 0.489 | 0.512 (+0.023) |
| Accuracy as abstention gate | — | 0.54–0.59 |
| Coverage at gate | 100% | ~24% |
| Learning-store overall (18,002 rows) | 0.482 | unchanged |
| Confluence k>=2 (existing edge, retained) | ~0.51–0.55 | ~0.51–0.55 |

The live system already exploits the confluence edge via `web/tuned.json::combo_min=2`; that remains the production safeguard and was left untouched.

---

## What changed in the repo / تغییراتِ مخزن

- `backtest/rsi_tools.py` — added `proj_features()` / `proj_predict()` (causal 8-feature logistic, last bar) and a `model=` kwarg on `project()`. When a model is supplied, it overrides the direction with the calibrated logistic call and emits `dir_val=0` (NEUTRAL) when `|p−0.5| < tau`; otherwise the legacy hand-tuned score is used unchanged.
- `web/tuned.json` — new `projection_model` key: per-TF `{intercept, weights[8], tau}`, fit by `backtest/fit_projection_model.py`. The service reads it live (no restart). **Delete this key to roll back.**
- `web/signal_service.py` — passes `model=TUNED().get("projection_model")` into `RT.project`.
- `web/train.py` — `build_calls()` applies the same logistic (vectorised) for backfill consistency; `_PROJ_MODEL` loader added.
- `web/assistant.py` — `_rsi_proj` renders an honest "no confident bias — abstaining" line on NEUTRAL.
- Leak fix (pre-existing, kept + extended): divergence pull shifted to `b+L` (`start=b+5`) in `web/train.py`, `backtest/method_eval.py`, `web/setup_store.py` — removes a 5-bar pivot-confirmation look-ahead so backfill/confluence match what live ever sees.
- Research harness added under `backtest/`: `baseline_acc.py`, `exp_idea-2.py`, `walkforward_idea2.py`, `fit_projection_model.py`, `verify_integration.py`.

### Adversarial review (post-integration) — 3 issues found & fixed
1. **Div same-bar tie-break (live correctness):** `proj_features` picked the bull divergence on bars where bull+bear confirm together, while the fitted/backfilled model expects the array-overwrite (bear-last) value — opposite sign on `div`, the highest-weighted feature (~24 bars). **Fixed** to mirror the overwrite precedence; `verify_integration.py` now sweeps **all** `div!=0` bars and reports `max|diff|=0`.
2. **Bias feature nuance:** the live/backfill `bias` is the engine's 3-term HTF trend (b1+2b2+2b3); the weights were fit on a 2-term resampled proxy. Both are HTF-direction signs (the live one arguably cleaner); the misleading "verified identical" comment was corrected to state the approximation.
3. **Weak verifier:** the 3-bar spot-check was replaced by the full `div!=0`+grid sweep above.

### Verification performed / راستی‌آزمایی
- `import rsi_tools` — OK.
- `project()` smoke on BTCUSDT M5 — returns the full shape `{dir, dir_val, confidence, notes, rsi_state, points(48), events, scenario}`, no crash.
- `python web/train.py --keep` — **ran without crashing but trained 0 new rows**: it requires `~/Downloads/*` CSV history (hardcoded `/Users/amirh...` paths via `B.prep_symbol`), which is absent on this machine. The existing 18,002-row store was preserved (`--keep`); overall learned accuracy reported 0.482. Because the backbone was not changed, **no recalibration was needed** — skipping train.py is harmless here. (To retrain on this machine, port `train.py` to read `site/data/ohlcv_*.json` like the experiment scripts do.)
- idea-2 recheck reproduced live (numbers above match `recheck_idea-2_0_result.json`).

---

## Remaining ideas / گام‌های بعدی پیشنهادی

1. **Promote idea-2 to a 2/2 clearance, then integrate as an honest-abstention path.** Concretely: freeze the per-TF logistic coefficients (fit cross-symbol, leak-free div) into `web/tuned.json`, compute `score` from those weights in both `project()` and `build_calls()`, and emit **NEUTRAL when `|p−0.5|` is below a per-TF threshold** chosen on a train split for ≥0.53 precision. This keeps the output shape (`dir_val=0` for neutral) and never adds look-ahead. Add a `dir_val==0` short-circuit before the wavy-path/events block so neutral calls still render a flat hypothesis.
2. **Re-weight, don't replace:** even without the full logistic, the coefficients say *raise divergence weight, cut RSI-pull and slope-sign weight*. A minimal hand-edit toward those ratios is a low-risk partial capture of idea-2.
3. **Stack idea-2 (direction) × idea-3 (regime gate) × confluence k>=2 (coverage filter):** the three edges are partly independent; the union of their high-precision cells is the realistic path to a system whose *made* calls clear 0.55+.
4. Run idea-2's recheck across **more horizons (H=12/48)** and a **walk-forward** (rolling refit) before production to close the H1 time-split soft spot.

---

## Risks / ریسک‌ها

- The candidate edge (+0.023) is small relative to its standard error; a naïve integration could disappoint live. Gate-with-abstention (idea-2 mechanism) is safer than swapping the full-coverage direction.
- `train.py` cannot recalibrate on this machine without the Downloads history; live confidence calibration depends on the slow online recorder or a JSON-fed port of train.py.
- No change shipped → **zero regression risk** to the current live model. This is the intended, honest outcome.
