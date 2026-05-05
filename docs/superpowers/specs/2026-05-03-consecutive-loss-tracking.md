# Consecutive Loss / Win Streak Tracking

## Goal

Add `max_consecutive_losses` and `max_consecutive_wins` to `BacktestReport` so operators can detect regime-dependent blow-up risk and validate the "pause after N losses" operational rule.

## Motivation

Aggregate metrics like Sharpe ratio and profit_factor describe the distribution of returns but not their temporal order. A strategy that wins 60% of the time could still produce an 8-trade losing streak before its winners cluster — behaviorally unacceptable and operationally dangerous even if the Sharpe looks healthy. Tracking the longest loss streak makes this risk visible:

1. **Operational rule validation** — the "pause after N consecutive losses" rule (referenced in the safety-observability spec) needs a data-driven baseline for N. Without historical streak data from backtests, the threshold is a guess.
2. **Parameter selection tiebreaker** — when two candidates have similar Sharpe and profit_factor, prefer the one with a shorter max loss streak.
3. **Regime detection signal** — a very long max loss streak relative to total trades suggests the strategy is regime-sensitive; broad market conditions matter more than the parameters.

## Design

### New fields on BacktestReport

```python
max_consecutive_losses: int = 0  # longest run of trades with pnl <= 0
max_consecutive_wins: int = 0    # longest run of trades with pnl > 0
```

Both default to `0` (no streak). Breaking-even trades (`pnl == 0.0`) count as losses, consistent with `winning_trades` which counts only `pnl > 0`.

### Computation

A single-pass streak counter over the chronologically-ordered trade list (already guaranteed by `_extract_trades()` which processes events in event order):

```python
def _compute_streak_stats(trades: list[ReplayTradeRecord]) -> tuple[int, int]:
    max_losses = max_wins = 0
    cur_losses = cur_wins = 0
    for t in trades:
        if t.pnl > 0:
            cur_wins += 1
            cur_losses = 0
        else:
            cur_losses += 1
            cur_wins = 0
        max_losses = max(max_losses, cur_losses)
        max_wins = max(max_wins, cur_wins)
    return max_losses, max_wins
```

Empty trade list → `(0, 0)`, consistent with other zero-trade defaults.

### Aggregation across scenarios

In `_aggregate_reports()` in `tuning/sweep.py`:

- `max_consecutive_losses = max(r.max_consecutive_losses for r in valid)` — worst case across scenarios
- `max_consecutive_wins = max(r.max_consecutive_wins for r in valid)` — best case across scenarios

Rationale: `max()` for losses because operational risk is determined by the worst-case scenario any parameter set encountered. `max()` for wins is symmetric and correct for reporting purposes.

### No scoring penalty

The streak fields are **informational only** — no change to `score_report()`. The existing Sharpe + profit_factor penalty already penalises strategies with poor risk-adjusted returns. Adding a streak penalty introduces a new threshold parameter that would need tuning and could interfere with the well-calibrated existing score. Operators can use streak data for manual triage after the ranked list is produced.

### CLI display

`_print_top_candidates()` in `tuning/cli.py` gains a `maxcl=N` column:

```
  [ 1] score=  2.3400  trades=12  win=  75%  pf= 2.50  stop%= 83%  maxcl= 2  BREAKOUT_LOOKBACK_BARS=20 ...
```

The column shows `max_consecutive_losses` — this is the operationally relevant number.

## Files changed

| File | Change |
|------|--------|
| `src/alpaca_bot/replay/report.py` | Add 2 fields, `_compute_streak_stats()`, call in `build_backtest_report()` |
| `src/alpaca_bot/tuning/sweep.py` | Update `_aggregate_reports()` to include both fields |
| `src/alpaca_bot/tuning/cli.py` | Add `maxcl=N` column in `_print_top_candidates()` |
| `tests/unit/test_replay_report.py` | 4 new tests for streak computation |
| `tests/unit/test_tuning_sweep.py` | 1 new test for aggregation |

## Safety assessment

This change is entirely within the backtesting/replay layer. It does not touch:
- `evaluate_cycle()` — no change to the pure engine
- Order submission, position sizing, stop placement — no risk path
- Postgres / audit log — no state change
- Settings / env vars — no new configuration
- Paper vs. live mode — replay is offline; no broker calls

No migration, no deployment change, no safety concern.

## Test plan

1. `test_max_consecutive_losses_zero_for_no_trades` — zero trades → both fields are 0
2. `test_max_consecutive_losses_all_winners` — 3 winning trades → max_consecutive_losses=0, max_consecutive_wins=3
3. `test_max_consecutive_losses_mixed_streak` — W L L W L L L → max_consecutive_losses=3, max_consecutive_wins=1
4. `test_max_consecutive_losses_break_even_counts_as_loss` — pnl=0.0 increments the loss streak
5. `test_aggregate_reports_max_consecutive_losses_uses_worst_case` — two reports with streaks 2 and 4 → aggregated = 4
