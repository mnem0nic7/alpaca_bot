# Exit Type Segmentation in BacktestReport

## Problem

All trades in `BacktestReport` are aggregated together — stops and EOD-flattened exits are
indistinguishable. This masks a critical signal quality issue: a strategy that exits 90% of
positions at market close (EOD) never tested its hypothesis. It may score well on Sharpe simply
because holding overnight is net-positive in a bull regime, not because the entry signal has
edge.

Operators running sweeps cannot currently distinguish:
- Parameters that generate true stop-based exits (signal hypothesis confirmed or stopped out)
- Parameters that just hold to close every day (thesis never resolved)

## Goal

Add per-exit-type breakdowns to `BacktestReport` and expose them in CLI output, so operators
can assess entry signal quality independently of hold-to-close bias.

## Scope

Four files change. No changes to Settings, strategy logic, scoring, evaluate_cycle(), or DB.

### Files

| File | Change |
|---|---|
| `src/alpaca_bot/replay/report.py` | Add 5 new fields; compute in `build_backtest_report()`; aggregate in `_aggregate_reports()` (via sweep.py) |
| `src/alpaca_bot/tuning/sweep.py` | Update `_aggregate_reports()` to sum/mean the new fields |
| `src/alpaca_bot/tuning/cli.py` | Show stop% in `_print_top_candidates()` |
| `src/alpaca_bot/replay/cli.py` | Add fields to `_report_to_dict()`, `_compare_row()`, `_format_compare_csv()` |
| `tests/unit/test_replay_report.py` | 5 new tests |
| `tests/unit/test_tuning_sweep.py` | 1 new test (aggregate sums exit-type counts) |
| `tests/unit/test_backtest_cli.py` | Update 2 existing compare-shape tests |

## New Fields

```python
stop_wins: int = 0
stop_losses: int = 0
eod_wins: int = 0
eod_losses: int = 0
avg_hold_minutes: float | None = None
```

- `stop_wins` / `stop_losses`: trades where `exit_reason == "stop"` and pnl > 0 / pnl <= 0
- `eod_wins` / `eod_losses`: trades where `exit_reason == "eod"` and pnl > 0 / pnl <= 0
- `avg_hold_minutes`: mean of `(exit_time - entry_time).total_seconds() / 60` across all trades

A pnl == 0 trade (flat) is counted as a loss in stop_losses/eod_losses (conservative).

## Computation in `build_backtest_report()`

```python
stop_wins = sum(1 for t in trades if t.exit_reason == "stop" and t.pnl > 0)
stop_losses = sum(1 for t in trades if t.exit_reason == "stop" and t.pnl <= 0)
eod_wins = sum(1 for t in trades if t.exit_reason == "eod" and t.pnl > 0)
eod_losses = sum(1 for t in trades if t.exit_reason == "eod" and t.pnl <= 0)
hold_minutes = [(t.exit_time - t.entry_time).total_seconds() / 60 for t in trades]
avg_hold_minutes = sum(hold_minutes) / len(hold_minutes) if hold_minutes else None
```

Zero-trades early return already produces `BacktestReport()` with defaults — no change needed.

## Aggregation in `_aggregate_reports()`

```python
stop_wins = sum(r.stop_wins for r in valid)
stop_losses = sum(r.stop_losses for r in valid)
eod_wins = sum(r.eod_wins for r in valid)
eod_losses = sum(r.eod_losses for r in valid)
hold_minutes = [r.avg_hold_minutes for r in valid if r.avg_hold_minutes is not None]
avg_hold_minutes = sum(hold_minutes) / len(hold_minutes) if hold_minutes else None
```

## CLI Display

**`_print_top_candidates()` (tuning/cli.py):**
Add `stop%` column showing what fraction of all trades exited via stop:
```
  [ 1] score=  2.1345  trades= 7  win=57%   pf=1.45  stop%=43%  BREAKOUT_LOOKBACK_BARS=20 ...
```
`stop_pct = (stop_wins + stop_losses) / total_trades if total_trades > 0 else None`

**`_report_to_dict()` (replay/cli.py):**
Add all 5 new fields to the JSON dict.

**`_compare_row()` and `_format_compare_csv()` (replay/cli.py):**
Add `stop_wins`, `stop_losses`, `eod_wins`, `eod_losses`, `avg_hold_minutes` as columns.

## Backward Compatibility

All new fields default to 0 / None. All existing BacktestReport constructors that omit them
continue to work. `score_report()` is not changed — no scoring impact.

## Safety

No changes to evaluate_cycle(), order dispatch, or runtime. Purely offline analytics. No new
env vars, no DB migration.
