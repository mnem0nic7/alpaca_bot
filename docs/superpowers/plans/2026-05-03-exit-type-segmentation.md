# Exit Type Segmentation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `stop_wins`, `stop_losses`, `eod_wins`, `eod_losses`, `avg_hold_minutes` to `BacktestReport` so operators can distinguish signal-driven exits (stop-based) from hold-to-close exits (EOD), enabling better assessment of entry signal quality during sweeps.

**Architecture:** Computed bottom-up: `build_backtest_report()` fills the new fields from trade records; `_aggregate_reports()` sums/averages them across scenarios; CLI display functions expose them to operators.

**Tech Stack:** Python, pytest, existing `alpaca_bot.replay` and `alpaca_bot.tuning` modules.

---

### Task 1: Failing tests for exit-type fields in BacktestReport

**Files:**
- Modify: `tests/unit/test_replay_report.py` (append after existing tests)

- [ ] **Step 1: Write the 5 failing tests**

Append to `tests/unit/test_replay_report.py`:

```python
# ---------------------------------------------------------------------------
# exit type segmentation
# ---------------------------------------------------------------------------


def test_exit_type_fields_zero_for_no_trades() -> None:
    result = _make_result([])
    report = build_backtest_report(result)
    assert report.stop_wins == 0
    assert report.stop_losses == 0
    assert report.eod_wins == 0
    assert report.eod_losses == 0
    assert report.avg_hold_minutes is None


def test_exit_type_fields_stop_win() -> None:
    result = _make_result([_fill(entry_price=150.0, quantity=10, t=_T0),
                           _eod_exit(exit_price=155.0, t=_T1)])
    report = build_backtest_report(result)
    assert report.stop_wins == 0
    assert report.eod_wins == 1
    assert report.stop_losses == 0
    assert report.eod_losses == 0


def test_exit_type_fields_stop_loss() -> None:
    result = _make_result([_fill(entry_price=150.0, quantity=10, t=_T0),
                           _stop_exit(exit_price=148.0, t=_T1)])
    report = build_backtest_report(result)
    assert report.stop_wins == 0
    assert report.stop_losses == 1
    assert report.eod_wins == 0
    assert report.eod_losses == 0


def test_exit_type_fields_mixed() -> None:
    """2 eod wins + 1 stop loss."""
    _T3 = datetime(2026, 4, 24, 14, 45, tzinfo=timezone.utc)
    _T4 = datetime(2026, 4, 24, 15, 0, tzinfo=timezone.utc)
    result = _make_result([
        _fill(entry_price=150.0, quantity=10, t=_T0),
        _eod_exit(exit_price=155.0, t=_T1),        # eod win
        ReplayEvent(event_type=IntentType.ENTRY_FILLED, symbol="AAPL", timestamp=_T1,
                    details={"entry_price": 155.0, "quantity": 10, "initial_stop_price": 153.0}),
        _eod_exit(exit_price=160.0, t=_T2),        # eod win
        ReplayEvent(event_type=IntentType.ENTRY_FILLED, symbol="AAPL", timestamp=_T3,
                    details={"entry_price": 160.0, "quantity": 10, "initial_stop_price": 158.0}),
        ReplayEvent(event_type=IntentType.STOP_HIT, symbol="AAPL", timestamp=_T4,
                    details={"exit_price": 158.0}),  # stop loss
    ])
    report = build_backtest_report(result)
    assert report.eod_wins == 2
    assert report.eod_losses == 0
    assert report.stop_wins == 0
    assert report.stop_losses == 1


def test_avg_hold_minutes_correct() -> None:
    """_T0 to _T1 = 15 min; _T1 to _T2 = 15 min → avg = 15.0."""
    result = _make_result([
        _fill(entry_price=150.0, quantity=10, t=_T0),
        _eod_exit(exit_price=155.0, t=_T1),
        ReplayEvent(event_type=IntentType.ENTRY_FILLED, symbol="AAPL", timestamp=_T1,
                    details={"entry_price": 155.0, "quantity": 10, "initial_stop_price": 153.0}),
        _eod_exit(exit_price=160.0, t=_T2),
    ])
    report = build_backtest_report(result)
    assert report.avg_hold_minutes == pytest.approx(15.0)
```

- [ ] **Step 2: Run to verify tests fail**

```
pytest tests/unit/test_replay_report.py::test_exit_type_fields_zero_for_no_trades tests/unit/test_replay_report.py::test_exit_type_fields_stop_win tests/unit/test_replay_report.py::test_exit_type_fields_stop_loss tests/unit/test_replay_report.py::test_exit_type_fields_mixed tests/unit/test_replay_report.py::test_avg_hold_minutes_correct -v
```

Expected: `AttributeError: 'BacktestReport' object has no attribute 'stop_wins'` or similar.

---

### Task 2: Failing test for aggregate exit-type sums

**Files:**
- Modify: `tests/unit/test_tuning_sweep.py` (append after existing tests)

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_tuning_sweep.py`:

```python
# ---------------------------------------------------------------------------
# _aggregate_reports: exit type fields
# ---------------------------------------------------------------------------

def test_aggregate_reports_sums_exit_type_fields() -> None:
    """Aggregated report sums stop_wins/losses and eod_wins/losses across scenarios."""
    from alpaca_bot.tuning.sweep import _aggregate_reports

    r1 = BacktestReport(
        trades=(), total_trades=3, winning_trades=2, losing_trades=1,
        win_rate=0.67, mean_return_pct=0.02, max_drawdown_pct=None,
        stop_wins=1, stop_losses=1, eod_wins=1, eod_losses=0, avg_hold_minutes=20.0,
    )
    r2 = BacktestReport(
        trades=(), total_trades=2, winning_trades=1, losing_trades=1,
        win_rate=0.5, mean_return_pct=0.01, max_drawdown_pct=None,
        stop_wins=0, stop_losses=1, eod_wins=1, eod_losses=0, avg_hold_minutes=30.0,
    )
    agg = _aggregate_reports([r1, r2])
    assert agg is not None
    assert agg.stop_wins == 1
    assert agg.stop_losses == 2
    assert agg.eod_wins == 2
    assert agg.eod_losses == 0
    assert agg.avg_hold_minutes == pytest.approx(25.0)
```

- [ ] **Step 2: Run to verify test fails**

```
pytest tests/unit/test_tuning_sweep.py::test_aggregate_reports_sums_exit_type_fields -v
```

Expected: TypeError or AttributeError.

---

### Task 3: Add exit-type fields to BacktestReport and build_backtest_report()

**Files:**
- Modify: `src/alpaca_bot/replay/report.py`

- [ ] **Step 1: Add the 5 fields to BacktestReport**

In `BacktestReport`, add after `profit_factor`:
```python
    stop_wins: int = 0
    stop_losses: int = 0
    eod_wins: int = 0
    eod_losses: int = 0
    avg_hold_minutes: float | None = None
```

- [ ] **Step 2: Add computation in build_backtest_report()**

In `build_backtest_report()`, after the `profit_factor` computation, add:
```python
    stop_wins = sum(1 for t in trades if t.exit_reason == "stop" and t.pnl > 0)
    stop_losses = sum(1 for t in trades if t.exit_reason == "stop" and t.pnl <= 0)
    eod_wins = sum(1 for t in trades if t.exit_reason == "eod" and t.pnl > 0)
    eod_losses = sum(1 for t in trades if t.exit_reason == "eod" and t.pnl <= 0)
    hold_minutes = [(t.exit_time - t.entry_time).total_seconds() / 60 for t in trades]
    avg_hold_minutes = sum(hold_minutes) / len(hold_minutes) if hold_minutes else None
```

And pass all new fields in the `BacktestReport(...)` return:
```python
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
        strategy_name=strategy_name,
    )
```

(The zero-trades early return already uses all defaults — no change needed.)

- [ ] **Step 3: Run the 5 report tests to verify they pass**

```
pytest tests/unit/test_replay_report.py::test_exit_type_fields_zero_for_no_trades tests/unit/test_replay_report.py::test_exit_type_fields_stop_win tests/unit/test_replay_report.py::test_exit_type_fields_stop_loss tests/unit/test_replay_report.py::test_exit_type_fields_mixed tests/unit/test_replay_report.py::test_avg_hold_minutes_correct -v
```

Expected: 5 PASS.

---

### Task 4: Update _aggregate_reports() in sweep.py

**Files:**
- Modify: `src/alpaca_bot/tuning/sweep.py`

- [ ] **Step 1: Add exit-type aggregation**

In `_aggregate_reports()`, after the `profit_factor` computation, add:
```python
    stop_wins = sum(r.stop_wins for r in valid)
    stop_losses = sum(r.stop_losses for r in valid)
    eod_wins = sum(r.eod_wins for r in valid)
    eod_losses = sum(r.eod_losses for r in valid)
    hold_mins = [r.avg_hold_minutes for r in valid if r.avg_hold_minutes is not None]
    avg_hold_minutes: float | None = sum(hold_mins) / len(hold_mins) if hold_mins else None
```

And in the `BacktestReport(...)` constructor call, add:
```python
        stop_wins=stop_wins,
        stop_losses=stop_losses,
        eod_wins=eod_wins,
        eod_losses=eod_losses,
        avg_hold_minutes=avg_hold_minutes,
```

- [ ] **Step 2: Run the aggregate test to verify it passes**

```
pytest tests/unit/test_tuning_sweep.py::test_aggregate_reports_sums_exit_type_fields -v
```

Expected: PASS.

---

### Task 5: Update CLI display functions

**Files:**
- Modify: `src/alpaca_bot/tuning/cli.py`
- Modify: `src/alpaca_bot/replay/cli.py`

- [ ] **Step 1: Update _print_top_candidates() in tuning/cli.py**

Replace the body of the print line in `_print_top_candidates()`:

Current:
```python
        pf = f"{report.profit_factor:.2f}" if (report and report.profit_factor is not None) else "—"
        params_str = " ".join(f"{k}={v}" for k, v in c.params.items())
        print(f"  [{i:2d}] score={sharpe:>8s}  trades={trades:2d}  win={win:>5s}  pf={pf:>5s}  {params_str}")
```

Replace with:
```python
        pf = f"{report.profit_factor:.2f}" if (report and report.profit_factor is not None) else "—"
        stop_total = (report.stop_wins + report.stop_losses) if report else 0
        stop_pct = f"{stop_total / trades:.0%}" if (trades > 0) else "—"
        params_str = " ".join(f"{k}={v}" for k, v in c.params.items())
        print(f"  [{i:2d}] score={sharpe:>8s}  trades={trades:2d}  win={win:>5s}  pf={pf:>5s}  stop%={stop_pct:>4s}  {params_str}")
```

- [ ] **Step 2: Update _report_to_dict() in replay/cli.py**

In `_report_to_dict()`, add after `"profit_factor"`:
```python
        "stop_wins": report.stop_wins,
        "stop_losses": report.stop_losses,
        "eod_wins": report.eod_wins,
        "eod_losses": report.eod_losses,
        "avg_hold_minutes": report.avg_hold_minutes,
```

- [ ] **Step 3: Update _compare_row() in replay/cli.py**

In `_compare_row()`, add after `"profit_factor"`:
```python
        "stop_wins": report.stop_wins,
        "stop_losses": report.stop_losses,
        "eod_wins": report.eod_wins,
        "eod_losses": report.eod_losses,
        "avg_hold_minutes": report.avg_hold_minutes,
```

- [ ] **Step 4: Update _format_compare_csv() fieldnames in replay/cli.py**

Replace:
```python
    fieldnames = [
        "strategy", "total_trades", "win_rate",
        "mean_return_pct", "max_drawdown_pct", "sharpe_ratio", "profit_factor",
    ]
```

With:
```python
    fieldnames = [
        "strategy", "total_trades", "win_rate",
        "mean_return_pct", "max_drawdown_pct", "sharpe_ratio", "profit_factor",
        "stop_wins", "stop_losses", "eod_wins", "eod_losses", "avg_hold_minutes",
    ]
```

---

### Task 6: Update compare-shape tests in test_backtest_cli.py

**Files:**
- Modify: `tests/unit/test_backtest_cli.py`

- [ ] **Step 1: Update test_compare_json_output_shape**

Find the assertion block:
```python
    assert set(row.keys()) == {
        "strategy", "total_trades", "win_rate",
        "mean_return_pct", "max_drawdown_pct", "sharpe_ratio", "profit_factor",
    }
```

Replace with:
```python
    assert set(row.keys()) == {
        "strategy", "total_trades", "win_rate",
        "mean_return_pct", "max_drawdown_pct", "sharpe_ratio", "profit_factor",
        "stop_wins", "stop_losses", "eod_wins", "eod_losses", "avg_hold_minutes",
    }
```

- [ ] **Step 2: Update test_compare_csv_output_has_header_and_rows**

Find the assertion block:
```python
    assert set(reader.fieldnames) == {
        "strategy", "total_trades", "win_rate",
        "mean_return_pct", "max_drawdown_pct", "sharpe_ratio", "profit_factor",
    }
```

Replace with:
```python
    assert set(reader.fieldnames) == {
        "strategy", "total_trades", "win_rate",
        "mean_return_pct", "max_drawdown_pct", "sharpe_ratio", "profit_factor",
        "stop_wins", "stop_losses", "eod_wins", "eod_losses", "avg_hold_minutes",
    }
```

- [ ] **Step 3: Run the backtest CLI compare tests to verify they pass**

```
pytest tests/unit/test_backtest_cli.py::test_compare_json_output_shape tests/unit/test_backtest_cli.py::test_compare_csv_output_has_header_and_rows -v
```

Expected: 2 PASS.

---

### Task 7: Full regression and commit

- [ ] **Step 1: Run all affected test suites**

```
pytest tests/unit/test_replay_report.py tests/unit/test_tuning_sweep.py tests/unit/test_tuning_sweep_cli.py tests/unit/test_backtest_cli.py -v
```

Expected: 55 tests pass (43 existing + 6 new + 2 existing repaired + 4 existing confirm).

- [ ] **Step 2: Run full regression**

```
pytest --tb=short -q
```

Expected: 1054 tests pass (1048 prior + 6 new).

- [ ] **Step 3: Commit**

```bash
git add src/alpaca_bot/replay/report.py \
        src/alpaca_bot/tuning/sweep.py \
        src/alpaca_bot/tuning/cli.py \
        src/alpaca_bot/replay/cli.py \
        tests/unit/test_replay_report.py \
        tests/unit/test_tuning_sweep.py \
        tests/unit/test_backtest_cli.py \
        docs/superpowers/specs/2026-05-03-exit-type-segmentation.md \
        docs/superpowers/plans/2026-05-03-exit-type-segmentation.md
git commit -m "feat: add exit type segmentation to BacktestReport

Breakdowns by exit_reason (stop vs eod): stop_wins, stop_losses, eod_wins,
eod_losses, avg_hold_minutes. Shown in sweep top-candidates (stop%) and
replay compare CSV/JSON output. Aggregated in _aggregate_reports() via sums
(counts) and mean (hold minutes).

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```
