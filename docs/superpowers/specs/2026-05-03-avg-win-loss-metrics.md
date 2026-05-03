# Avg Win / Avg Loss Return Metrics — Design Spec

## Goal

Add `avg_win_return_pct` and `avg_loss_return_pct` to `BacktestReport`, expose them in all
output surfaces (replay CLI JSON/CSV, sweep top-candidates display), and aggregate them
correctly across multi-scenario runs.

## Motivation

The current report captures *total* win/loss behaviour through:
- `win_rate` — frequency of wins
- `profit_factor` — ratio of total win PnL to total loss PnL

What is missing is the *per-trade magnitude* of each outcome type.  
`profit_factor = 2.0` is consistent with either:
- Typical win +1%, typical loss −0.5% (small moves, high turnover)  
- Typical win +10%, typical loss −5% (large moves, rare trades)

These are dramatically different risk profiles that affect:
1. Sensitivity to slippage and commission
2. How aggressively to size positions
3. How much single-trade tail risk exists

`avg_win_return_pct` and `avg_loss_return_pct` fill this gap cleanly and are sufficient to
derive the R-multiple (`avg_win / abs(avg_loss)`) without adding another field.

## Definitions

| Field | Formula | Value when None |
|---|---|---|
| `avg_win_return_pct` | mean of `t.return_pct` for trades where `t.pnl > 0` | No winning trades |
| `avg_loss_return_pct` | mean of `t.return_pct` for trades where `t.pnl <= 0` | No losing trades (break-even counts as loss, consistent with existing convention) |

`avg_loss_return_pct` will be ≤ 0.0 in practice (a break-even trade contributes return_pct = 0.0).

## Impact on Existing Code

### `BacktestReport` (replay/report.py)

Two optional fields with `= None` defaults — backward-compatible with all existing fixtures:

```python
avg_win_return_pct: float | None = None
avg_loss_return_pct: float | None = None
```

Added after `avg_hold_minutes`, before `max_consecutive_losses`.

### `build_backtest_report()` (replay/report.py)

Added immediately after the `avg_hold_minutes` computation:

```python
win_returns = [t.return_pct for t in trades if t.pnl > 0]
loss_returns = [t.return_pct for t in trades if t.pnl <= 0]
avg_win_return_pct = sum(win_returns) / len(win_returns) if win_returns else None
avg_loss_return_pct = sum(loss_returns) / len(loss_returns) if loss_returns else None
```

### `_aggregate_reports()` (tuning/sweep.py)

Average the non-None values across scenarios — same pattern as `sharpe_ratio`:

```python
win_avgs = [r.avg_win_return_pct for r in valid if r.avg_win_return_pct is not None]
avg_win_return_pct: float | None = sum(win_avgs) / len(win_avgs) if win_avgs else None
loss_avgs = [r.avg_loss_return_pct for r in valid if r.avg_loss_return_pct is not None]
avg_loss_return_pct: float | None = sum(loss_avgs) / len(loss_avgs) if loss_avgs else None
```

### `replay/cli.py` — `_report_to_dict()`, `_compare_row()`, `_format_compare_csv()`

Two new keys added after `avg_hold_minutes` in all three functions.  
Contract tests in `test_backtest_cli.py` must be updated to include them.

### `tuning/cli.py` — `_print_top_candidates()`

Display the R-multiple inline (`R=avg_win/abs(avg_loss)`) as a compact representation:

```
  [ 1] score=  2.3456  trades= 8  win=  75%  pf= 2.50  R=2.50  stop%=  50%  maxcl= 2  ...
```

If either avg is None, show `R=—`.

## Safety Assessment

- No order submission, position sizing, or stop placement is touched — pure analytics.
- No new env vars, no migrations, no audit events required.
- `evaluate_cycle()` remains untouched.
- All new fields have `None` defaults → all existing `BacktestReport(...)` call sites work unchanged.
- DB storage unchanged — `TuningResultStore.save_run()` stores only the 5 fixed columns; these fields are display-only like `profit_factor` and `max_consecutive_losses`.

## Test Plan

### `tests/unit/test_replay_report.py`

Four new tests appended after the existing streak tests:
1. `test_avg_win_return_pct_none_when_no_winners` — all losers → avg_win=None, avg_loss computed
2. `test_avg_loss_return_pct_none_when_no_losers` — all winners → avg_loss=None, avg_win computed
3. `test_avg_win_loss_correct_values` — mixed trades, verify mean computation
4. `test_avg_loss_includes_break_even_trades` — pnl=0.0 trade counted in loss bucket

### `tests/unit/test_tuning_sweep.py`

One new test appended after `test_aggregate_reports_max_consecutive_losses_uses_worst_case`:
- `test_aggregate_reports_averages_win_loss_return_pct` — two reports, verify aggregated values are means

### `tests/unit/test_backtest_cli.py`

Contract test updates (two tests, not new tests):
- `test_compare_json_output_shape` — add `"avg_win_return_pct"` and `"avg_loss_return_pct"` to expected key set
- `test_compare_csv_output_has_header_and_rows` — add both fields to expected fieldnames set
