# Profit Factor Scoring Design

## Problem

`score_report()` ranks candidates by Sharpe ratio alone (with a Calmar fallback). A strategy
that generates high Sharpe on small, consistent returns can score above one with a high average
return but large total losses — because Sharpe measures return/volatility, not whether the
system makes money in aggregate.

**Profit factor** = gross winning PnL / gross losing PnL is the most widely used secondary
validation metric in algorithmic trading. A value below 1.0 means the strategy loses more than
it wins in dollar terms. Such strategies should rank lower than profitable ones at the same
Sharpe.

Currently, `BacktestReport` does not track profit factor at all — it is invisible to operators
running sweeps and replays.

## Goal

1. Add `profit_factor: float | None` to `BacktestReport`, computed at report-build time.
2. Use it in `score_report()` as a multiplier penalty when `profit_factor < 1.0`.
3. Display it in sweep CLI output (`_print_top_candidates`) and replay CLI output (`_report_to_dict`, `_compare_row`).
4. Aggregate it across scenarios in `_aggregate_reports()`.

## Scope

Four source files change, two test files gain new tests. No DB migration, no env vars, no
changes to Settings, strategy logic, or runtime.

### Files

| File | Change |
|---|---|
| `src/alpaca_bot/replay/report.py` | Add `profit_factor: float | None = None` field; compute in `build_backtest_report()` |
| `src/alpaca_bot/tuning/sweep.py` | `score_report()` — apply penalty; `_aggregate_reports()` — include profit_factor |
| `src/alpaca_bot/tuning/cli.py` | `_print_top_candidates()` — add pf column |
| `src/alpaca_bot/replay/cli.py` | `_report_to_dict()`, `_compare_row()`, `_format_compare_csv()` — add profit_factor |
| `tests/unit/test_replay_report.py` | 4 new tests |
| `tests/unit/test_tuning_sweep.py` | 3 new tests |

## Profit Factor Definition

```
profit_factor = sum(pnl for pnl > 0) / abs(sum(pnl for pnl < 0))
```

- `None` when there are no losing trades (no losses to divide by — perfectly good)
- `0.0` when there are no winning trades (all losses)
- `< 1.0` means the strategy loses more money than it makes
- `≥ 1.0` means profitable in aggregate

Computed over PnL dollar values, not return percentages. This captures position-size effects
(a small number of large-loss trades can dominate even if win rate looks acceptable).

## Scoring Change

Current `score_report()`:
```python
base = sharpe_ratio or calmar_fallback
return base
```

After:
```python
base = sharpe_ratio or calmar_fallback
# Penalize net-losing strategies: scale score by profit_factor when < 1.0
if report.profit_factor is not None and report.profit_factor < 1.0:
    base *= report.profit_factor
return base
```

Rationale: A strategy with Sharpe=2.0 but profit_factor=0.7 (loses 30 cents per dollar won)
gets score = 1.4, ranking below strategies with Sharpe=1.5 and profit_factor ≥ 1.0 (score
1.5). This prevents optimizing for low-variance losers.

When profit_factor is None (no losses), no penalty — all-winner strategies are never penalised.

## Aggregation Across Scenarios

In `_aggregate_reports()`, profit_factor for the combined report = mean of non-None
per-scenario profit factors. This is an approximation (weighted mean by scenario PnL would
be more accurate) but sufficient for display; the aggregate report is only used for human
review and DB storage, not re-scored.

## Display Changes

**`_print_top_candidates()` (tuning/cli.py):**
```
  [ 1] score=  2.1345  trades= 7  win=57%   pf=1.45  BREAKOUT_LOOKBACK_BARS=20 ...
```
Add `pf=1.45` column (2 decimal places, or `—` when None).

**`_report_to_dict()` (replay/cli.py):**
Add `"profit_factor": report.profit_factor` to the JSON output dict.

**`_compare_row()` and `_format_compare_csv()` (replay/cli.py):**
Add `profit_factor` as a column in compare output.

## Backward Compatibility

- `profit_factor` defaults to `None` in `BacktestReport`. All existing test constructors that
  don't pass it continue to work.
- `score_report()` is unchanged when `profit_factor is None` — all existing test cases pass.
- The DB schema (`tuning_results` table) is not modified in this change. `profit_factor` is
  visible in CLI output but not persisted. A follow-up migration can add the column later.

## Safety

- No financial path changes. Scoring is only used by the offline tuning pipeline.
- `evaluate_cycle()` is not touched.
- No new env vars.
- Paper/live mode identical.
