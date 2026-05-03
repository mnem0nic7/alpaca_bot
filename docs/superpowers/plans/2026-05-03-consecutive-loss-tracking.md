# Consecutive Loss/Win Streak Tracking Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `max_consecutive_losses` and `max_consecutive_wins` to `BacktestReport` so operators can see worst-case losing streaks for each parameter set and validate the "pause after N losses" operational rule.

**Architecture:** Two new default-zero integer fields on the frozen `BacktestReport` dataclass, computed via a single-pass streak counter in `report.py`. Aggregated with `max()` across scenarios in `sweep.py`. Displayed as a `maxcl=N` column in the tuning CLI top-candidates table.

**Tech Stack:** Python dataclasses, existing `ReplayTradeRecord.pnl` field, pytest TDD.

---

### Task 1: Write failing tests for streak computation

**Files:**
- Modify: `tests/unit/test_replay_report.py`

- [ ] **Step 1: Append 4 failing tests to test_replay_report.py**

Add these tests after the last test in the file (after `test_avg_hold_minutes_correct`):

```python
# ---------------------------------------------------------------------------
# Streak stats: max_consecutive_losses / max_consecutive_wins
# ---------------------------------------------------------------------------


def _make_trade(pnl: float) -> "ReplayTradeRecord":
    from alpaca_bot.replay.report import ReplayTradeRecord
    return ReplayTradeRecord(
        symbol="AAPL",
        entry_price=100.0,
        exit_price=100.0 + pnl,
        quantity=1,
        entry_time=_T0,
        exit_time=_T1,
        exit_reason="eod",
        pnl=pnl,
        return_pct=pnl / 100.0,
    )


def test_max_consecutive_losses_zero_for_no_trades() -> None:
    """Zero trades → both streak fields are 0."""
    report = BacktestReport(
        trades=(),
        total_trades=0, winning_trades=0, losing_trades=0,
        win_rate=None, mean_return_pct=None, max_drawdown_pct=None,
    )
    assert report.max_consecutive_losses == 0
    assert report.max_consecutive_wins == 0


def test_max_consecutive_losses_all_winners() -> None:
    """3 consecutive wins → max_consecutive_losses=0, max_consecutive_wins=3."""
    from alpaca_bot.replay.report import _compute_streak_stats
    losses, wins = _compute_streak_stats([_make_trade(10.0)] * 3)
    assert losses == 0
    assert wins == 3


def test_max_consecutive_losses_mixed_streak() -> None:
    """W L L W L L L → max_consecutive_losses=3, max_consecutive_wins=1."""
    from alpaca_bot.replay.report import _compute_streak_stats
    trades = [
        _make_trade(10.0),   # W
        _make_trade(-5.0),   # L
        _make_trade(-3.0),   # L
        _make_trade(8.0),    # W
        _make_trade(-1.0),   # L
        _make_trade(-2.0),   # L
        _make_trade(-4.0),   # L
    ]
    losses, wins = _compute_streak_stats(trades)
    assert losses == 3
    assert wins == 1


def test_max_consecutive_losses_break_even_counts_as_loss() -> None:
    """pnl=0.0 increments the loss streak (not a win)."""
    from alpaca_bot.replay.report import _compute_streak_stats
    trades = [
        _make_trade(10.0),  # W
        _make_trade(0.0),   # break-even → counts as loss
        _make_trade(0.0),   # L
    ]
    losses, wins = _compute_streak_stats(trades)
    assert losses == 2
    assert wins == 1
```

- [ ] **Step 2: Run tests to verify they fail**

```
pytest tests/unit/test_replay_report.py::test_max_consecutive_losses_zero_for_no_trades tests/unit/test_replay_report.py::test_max_consecutive_losses_all_winners tests/unit/test_replay_report.py::test_max_consecutive_losses_mixed_streak tests/unit/test_replay_report.py::test_max_consecutive_losses_break_even_counts_as_loss -v
```

Expected: FAIL with `AttributeError: max_consecutive_losses` and `ImportError: cannot import name '_compute_streak_stats'`.

---

### Task 2: Implement streak fields in BacktestReport and report.py

**Files:**
- Modify: `src/alpaca_bot/replay/report.py`

- [ ] **Step 1: Add two fields to BacktestReport**

In `BacktestReport`, add these two fields after `avg_hold_minutes` and before `strategy_name`:

```python
    avg_hold_minutes: float | None = None
    max_consecutive_losses: int = 0
    max_consecutive_wins: int = 0
    strategy_name: str = "breakout"
```

- [ ] **Step 2: Add _compute_streak_stats helper function**

Add this function after `_compute_sharpe` and before `_compute_max_drawdown` (or at the end of the private helpers section):

```python
def _compute_streak_stats(trades: list[ReplayTradeRecord]) -> tuple[int, int]:
    """Return (max_consecutive_losses, max_consecutive_wins).

    Break-even trades (pnl == 0.0) count as losses, consistent with
    winning_trades which counts only pnl > 0.
    """
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

- [ ] **Step 3: Call _compute_streak_stats in build_backtest_report**

In `build_backtest_report()`, add the streak computation after the `hold_minutes` / `avg_hold_minutes` lines, before the `return BacktestReport(...)` call:

```python
    hold_minutes = [(t.exit_time - t.entry_time).total_seconds() / 60 for t in trades]
    avg_hold_minutes = sum(hold_minutes) / len(hold_minutes) if hold_minutes else None
    max_consecutive_losses, max_consecutive_wins = _compute_streak_stats(list(trades))

    return BacktestReport(
        trades=tuple(trades),
        total_trades=total,
        winning_trades=winners,
        losing_trades=losers,
        win_rate=win_rate,
        mean_return_pct=mean_return_pct,
        max_drawdown_pct=max_drawdown_pct,
        sharpe_ratio=_compute_sharpe(trades),
        profit_factor=profit_factor,
        stop_wins=stop_wins,
        stop_losses=stop_losses,
        eod_wins=eod_wins,
        eod_losses=eod_losses,
        avg_hold_minutes=avg_hold_minutes,
        max_consecutive_losses=max_consecutive_losses,
        max_consecutive_wins=max_consecutive_wins,
        strategy_name=strategy_name,
    )
```

- [ ] **Step 4: Run the streak tests to verify they pass**

```
pytest tests/unit/test_replay_report.py::test_max_consecutive_losses_zero_for_no_trades tests/unit/test_replay_report.py::test_max_consecutive_losses_all_winners tests/unit/test_replay_report.py::test_max_consecutive_losses_mixed_streak tests/unit/test_replay_report.py::test_max_consecutive_losses_break_even_counts_as_loss -v
```

Expected: 4 PASSED.

- [ ] **Step 5: Run full report test suite**

```
pytest tests/unit/test_replay_report.py -v
```

Expected: all existing tests still pass (new fields have defaults, no existing fixtures break).

- [ ] **Step 6: Commit**

```bash
git add src/alpaca_bot/replay/report.py tests/unit/test_replay_report.py
git commit -m "feat: add max_consecutive_losses/wins streak tracking to BacktestReport"
```

---

### Task 3: Update _aggregate_reports in sweep.py

**Files:**
- Modify: `src/alpaca_bot/tuning/sweep.py`
- Test: `tests/unit/test_tuning_sweep.py`

- [ ] **Step 1: Write failing aggregation test**

Append to `tests/unit/test_tuning_sweep.py` after `test_aggregate_reports_sums_exit_type_fields`:

```python


def test_aggregate_reports_max_consecutive_losses_uses_worst_case() -> None:
    """Aggregated max_consecutive_losses is max across scenarios (worst case)."""
    from alpaca_bot.tuning.sweep import _aggregate_reports

    r1 = BacktestReport(
        trades=(), total_trades=3, winning_trades=2, losing_trades=1,
        win_rate=0.67, mean_return_pct=0.02, max_drawdown_pct=None,
        max_consecutive_losses=2, max_consecutive_wins=3,
    )
    r2 = BacktestReport(
        trades=(), total_trades=4, winning_trades=2, losing_trades=2,
        win_rate=0.5, mean_return_pct=0.01, max_drawdown_pct=None,
        max_consecutive_losses=4, max_consecutive_wins=1,
    )
    agg = _aggregate_reports([r1, r2])
    assert agg is not None
    assert agg.max_consecutive_losses == 4
    assert agg.max_consecutive_wins == 3
```

- [ ] **Step 2: Run test to verify it fails**

```
pytest tests/unit/test_tuning_sweep.py::test_aggregate_reports_max_consecutive_losses_uses_worst_case -v
```

Expected: FAIL (aggregate ignores new fields).

- [ ] **Step 3: Update _aggregate_reports in sweep.py**

In `_aggregate_reports()`, add after the `avg_hold_minutes` computation and before the `return BacktestReport(...)` call:

```python
    hold_mins = [r.avg_hold_minutes for r in valid if r.avg_hold_minutes is not None]
    avg_hold_minutes: float | None = sum(hold_mins) / len(hold_mins) if hold_mins else None
    max_consecutive_losses = max(r.max_consecutive_losses for r in valid)
    max_consecutive_wins = max(r.max_consecutive_wins for r in valid)
    return BacktestReport(
        trades=(),
        total_trades=total_trades,
        winning_trades=winning_trades,
        losing_trades=losing_trades,
        win_rate=win_rate,
        mean_return_pct=mean_return_pct,
        max_drawdown_pct=max_drawdown_pct,
        sharpe_ratio=sharpe_ratio,
        profit_factor=profit_factor,
        stop_wins=stop_wins,
        stop_losses=stop_losses,
        eod_wins=eod_wins,
        eod_losses=eod_losses,
        avg_hold_minutes=avg_hold_minutes,
        max_consecutive_losses=max_consecutive_losses,
        max_consecutive_wins=max_consecutive_wins,
        strategy_name="aggregate",
    )
```

- [ ] **Step 4: Run aggregation test to verify it passes**

```
pytest tests/unit/test_tuning_sweep.py::test_aggregate_reports_max_consecutive_losses_uses_worst_case -v
```

Expected: PASSED.

- [ ] **Step 5: Run full sweep test suite**

```
pytest tests/unit/test_tuning_sweep.py -v
```

Expected: all existing tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/alpaca_bot/tuning/sweep.py tests/unit/test_tuning_sweep.py
git commit -m "feat: aggregate max_consecutive_losses/wins as worst/best case across scenarios"
```

---

### Task 4: Add maxcl column to tuning CLI display

**Files:**
- Modify: `src/alpaca_bot/tuning/cli.py`

- [ ] **Step 1: Update _print_top_candidates to show maxcl**

In `_print_top_candidates()`, modify the two lines that compute display values and the print statement:

```python
def _print_top_candidates(scored: list[TuningCandidate]) -> None:
    if not scored:
        return
    print("\nTop candidates:")
    for i, c in enumerate(scored, 1):
        report = c.report
        trades = report.total_trades if report else 0
        win = f"{report.win_rate:.0%}" if (report and report.win_rate is not None) else "—"
        sharpe = f"{c.score:.4f}" if c.score is not None else "—"
        pf = f"{report.profit_factor:.2f}" if (report and report.profit_factor is not None) else "—"
        stop_total = (report.stop_wins + report.stop_losses) if report else 0
        stop_pct = f"{stop_total / trades:.0%}" if trades > 0 else "—"
        max_cl = report.max_consecutive_losses if report else 0
        params_str = " ".join(f"{k}={v}" for k, v in c.params.items())
        print(f"  [{i:2d}] score={sharpe:>8s}  trades={trades:2d}  win={win:>5s}  pf={pf:>5s}  stop%={stop_pct:>4s}  maxcl={max_cl:>2d}  {params_str}")
```

- [ ] **Step 2: Run existing CLI tests**

```
pytest tests/unit/test_tuning_sweep_cli.py -v
```

Expected: all existing tests pass (no test pins the exact display format).

- [ ] **Step 3: Commit**

```bash
git add src/alpaca_bot/tuning/cli.py
git commit -m "feat: show maxcl (max consecutive losses) column in sweep top-candidates display"
```

---

### Task 5: Final regression check

- [ ] **Step 1: Run the full test suite**

```
pytest
```

Expected: all tests pass (1054 + 5 new = 1059 tests).

- [ ] **Step 2: Smoke test the backtest CLI**

```
cd /workspace/alpaca_bot && python -m alpaca_bot.replay.cli run --scenario tests/golden/breakout_success.json --format json | python -m json.tool | grep -E "total_trades|profit_factor|stop_wins|max_consecutive"
```

Expected: JSON output includes all the new fields.

- [ ] **Step 3: Final commit if any loose changes remain**

All changes should already be committed from Tasks 1-4. Verify with `git status`.
