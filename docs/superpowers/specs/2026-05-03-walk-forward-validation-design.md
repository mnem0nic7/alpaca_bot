# Walk-Forward Validation — Design Spec

**Date:** 2026-05-03
**Status:** Draft

---

## Problem

The `alpaca-bot-evolve` parameter sweep evaluates every candidate against the **same** historical data it was optimized on. A parameter set can score a high Sharpe simply by fitting to noise in that specific window — there is no signal that it will generalize to future data. This is the classic in-sample overfitting problem and it makes the sweep results unreliable for live trading decisions.

---

## Goal

Add a `--validate-pct` flag to `alpaca-bot-evolve` that activates **walk-forward validation**:

1. Split each scenario's data chronologically into an in-sample (IS) window and an out-of-sample (OOS) window
2. Run the full parameter sweep on the IS window (as today)
3. Evaluate the top-10 IS candidates on the OOS window
4. Print a side-by-side comparison: IS rank | IS score | OOS score | OOS trades | held?

A candidate "held" if its OOS score is ≥ 50% of its IS score. This threshold is printed in the header and is not configurable (avoids over-engineering).

---

## Architecture

```
replay/
  splitter.py        split_scenario() — pure, no I/O

tuning/
  sweep.py           evaluate_candidates_oos() — new function
  cli.py             --validate-pct flag, walk-forward output block
```

No new Postgres tables. No new CLI entry points. No changes to `TuningCandidate`.

---

## Component: `replay/splitter.py`

Pure function. No I/O.

```python
def split_scenario(
    scenario: ReplayScenario,
    *,
    in_sample_ratio: float = 0.8,
    daily_warmup: int = 30,
) -> tuple[ReplayScenario, ReplayScenario]:
```

**Algorithm:**
1. Collect unique trading dates from `intraday_bars`, sort ascending.
2. `split_idx = max(1, ceil(n_dates * in_sample_ratio))` — at least 1 date in IS.
3. IS dates = first `split_idx` dates; OOS dates = remaining dates.
4. Filter `intraday_bars` by date membership into IS and OOS lists.
5. IS daily_bars = all daily bars up to and including the last IS date.
6. OOS daily_bars = last `daily_warmup` bars from IS daily_bars **plus** daily bars after the last IS date. The warmup prefix is required so that SMA and ATR lookback indicators have enough history at the start of the OOS window. `daily_warmup=30` covers `DAILY_SMA_PERIOD` up to 30 (the max in `STRATEGY_GRIDS`).
7. Raise `ValueError` if OOS has 0 intraday dates (scenario too short to split).
8. Return names `{name}_is` and `{name}_oos`.

**Error condition:** If the scenario has fewer than 10 unique trading dates, raise `ValueError("scenario too short to split: need at least 10 trading dates")`. This prevents degenerate OOS windows with only 1-2 dates.

---

## Component: `evaluate_candidates_oos()` in `tuning/sweep.py`

```python
def evaluate_candidates_oos(
    candidates: list[TuningCandidate],
    oos_scenarios: list[ReplayScenario],
    *,
    base_env: dict[str, str],
    min_trades: int,
    aggregate: str = "min",
    signal_evaluator: "StrategySignalEvaluator | None" = None,
) -> list[float | None]:
```

- Takes the top-N `TuningCandidate` objects (already have `.params`)
- For each candidate, merges `base_env` with `candidate.params`, creates `Settings`, runs `ReplayRunner` on each OOS scenario, computes `score_report`, aggregates (min or mean) exactly as `run_multi_scenario_sweep` does
- Returns a parallel list of OOS scores (same length as `candidates`; `None` if disqualified)
- Does NOT produce new `TuningCandidate` objects — it is a read-only scoring pass

---

## CLI: `alpaca-bot-evolve --validate-pct`

```
alpaca-bot-evolve --scenario-dir data/backfill/ --validate-pct 0.2
```

New argument:
```python
parser.add_argument("--validate-pct", type=float, default=0.0,
                    help="Fraction of each scenario held out for OOS validation (0 = disabled)")
```

**Validation logic:**
- Must be in range `(0.0, 1.0)`. Exit with error otherwise.
- Only valid with `--scenario-dir` (single-scenario walk-forward is unsupported and confusing). Exit with error if used with `--scenario`.
- For each scenario in the dir, call `split_scenario(scenario, in_sample_ratio=1-validate_pct)`. Collect `is_scenarios` and `oos_scenarios` lists.
- Run `run_multi_scenario_sweep(scenarios=is_scenarios, ...)` to get IS candidates.
- Print the normal top-candidates table (IS results).
- Call `evaluate_candidates_oos(top10_is_candidates, oos_scenarios, ...)`.
- Print the walk-forward comparison block (see Output Format).

**No change to DB persistence:** `_save_to_db` is called with the IS candidates only. OOS results are display-only.

---

## Output Format (walk-forward block)

```
Walk-forward validation (OOS: 20% of each scenario, aggregate=min)
  IS score threshold for "held": OOS ≥ IS × 50%

  [Rank] IS-score   OOS-score  OOS-trades  held?   Params
  [  1]   0.4210      0.3180          8     ✓      BREAKOUT_LOOKBACK_BARS=20 ...
  [  2]   0.3990      0.0120          3     ✗      BREAKOUT_LOOKBACK_BARS=25 ...
  [  3]   0.3850       None           0     ✗      BREAKOUT_LOOKBACK_BARS=15 ...
```

Where:
- `OOS-score = None` → disqualified (< min_trades in OOS window); print as `None`
- `held?` → `✓` if `oos_score is not None and oos_score >= is_score * 0.5`; else `✗`
- Limit to top-10 IS candidates

---

## Testing

**New test file:** `tests/unit/test_replay_splitter.py`

Tests for `split_scenario`:
1. `test_split_respects_ratio` — 100 intraday dates; split_ratio=0.8 → IS has 80 dates, OOS has 20
2. `test_split_oos_daily_includes_warmup_prefix` — OOS daily_bars starts with warmup bars from end of IS daily period
3. `test_split_names_suffixed` — IS scenario name = `{name}_is`, OOS = `{name}_oos`
4. `test_split_intraday_bars_no_overlap` — no intraday bar appears in both IS and OOS
5. `test_split_raises_on_too_short_scenario` — scenario with 9 trading dates → ValueError
6. `test_split_oos_has_at_least_one_date` — split_ratio=0.99 on 10-date scenario still produces ≥1 OOS date

**Existing test file updates:** `tests/unit/test_tuning_sweep.py`

New test for `evaluate_candidates_oos`:
7. `test_evaluate_candidates_oos_scores_parallel_list` — 2 candidates; OOS scenarios produce different scores; verify parallel list length and values match expectation

**CLI smoke test** (existing pattern in `tests/unit/test_tuning_sweep_cli.py`):
8. `test_validate_pct_errors_with_single_scenario` — `--validate-pct` + `--scenario` → exits with error
9. `test_validate_pct_out_of_range` — `--validate-pct 1.5` → exits with error

---

## Settings Integration

No new env vars. No new `Settings` fields. `split_scenario` is a pure function that takes no `Settings` argument — it operates only on `ReplayScenario` bar data.

---

## Financial Safety

- Walk-forward validation is a read-only research tool. It touches no order submission, position sizing, or stop placement.
- `evaluate_cycle()` remains pure throughout — no I/O introduced.
- No new Postgres tables; no migrations; no risk of partial writes.
- `ENABLE_LIVE_TRADING` gate unaffected.

---

## Out of Scope

- K-fold / rolling-window cross-validation (YAGNI; a single IS/OOS split is sufficient to detect gross overfitting)
- Configurable `held` threshold (50% is a reasonable starting point; parameterising it adds complexity with little benefit)
- OOS results persisted to DB (research-only; the IS sweep result is what feeds live parameter selection)
- Regime-based splits (separate future concern)
