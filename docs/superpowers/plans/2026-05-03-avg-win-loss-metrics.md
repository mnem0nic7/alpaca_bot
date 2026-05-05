# Avg Win / Avg Loss Return Metrics — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `avg_win_return_pct` and `avg_loss_return_pct` to `BacktestReport`, expose them in all output surfaces (replay CLI JSON/CSV, sweep display), and aggregate them in multi-scenario sweeps.

**Architecture:** Same incremental pattern as `profit_factor`, `exit_type_segmentation`, and `max_consecutive_losses` — new optional fields with `None` defaults on `BacktestReport`, computed in `build_backtest_report()`, aggregated by averaging in `_aggregate_reports()`, displayed as a compact R-multiple (`R=avgW/abs(avgL)`) in the sweep top-candidates line.

**Tech Stack:** Pure Python dataclasses; no new dependencies.

---

## File Map

| File | Change |
|---|---|
| `src/alpaca_bot/replay/report.py` | Add 2 fields to `BacktestReport`, compute in `build_backtest_report()` |
| `src/alpaca_bot/replay/cli.py` | Add both fields to `_report_to_dict()`, `_compare_row()`, `_format_compare_csv()` fieldnames |
| `src/alpaca_bot/tuning/sweep.py` | Add both fields to `_aggregate_reports()` |
| `src/alpaca_bot/tuning/cli.py` | Show R-multiple in `_print_top_candidates()` |
| `tests/unit/test_replay_report.py` | 4 new tests |
| `tests/unit/test_backtest_cli.py` | 2 contract test updates |
| `tests/unit/test_tuning_sweep.py` | 1 new test |

---

## Task 1: Add `avg_win_return_pct` / `avg_loss_return_pct` to BacktestReport

**Files:**
- Modify: `src/alpaca_bot/replay/report.py`
- Test: `tests/unit/test_replay_report.py`

- [ ] **Step 1: Write the four failing tests**

Append after `test_max_consecutive_losses_break_even_counts_as_loss` in `tests/unit/test_replay_report.py`:

```python
# ---------------------------------------------------------------------------
# Avg win / avg loss return pct
# ---------------------------------------------------------------------------


def test_avg_win_return_pct_none_when_no_winners() -> None:
    """All-loser scenario → avg_win_return_pct is None, avg_loss_return_pct is computed."""
    from alpaca_bot.replay.report import _compute_avg_win_loss_return
    trades = [_make_trade(-5.0), _make_trade(-3.0)]
    avg_win, avg_loss = _compute_avg_win_loss_return(trades)
    assert avg_win is None
    assert avg_loss == pytest.approx((-5.0 / 100.0 + -3.0 / 100.0) / 2)


def test_avg_loss_return_pct_none_when_no_losers() -> None:
    """All-winner scenario → avg_loss_return_pct is None, avg_win_return_pct is computed."""
    from alpaca_bot.replay.report import _compute_avg_win_loss_return
    trades = [_make_trade(10.0), _make_trade(4.0)]
    avg_win, avg_loss = _compute_avg_win_loss_return(trades)
    assert avg_win == pytest.approx((10.0 / 100.0 + 4.0 / 100.0) / 2)
    assert avg_loss is None


def test_avg_win_loss_correct_values() -> None:
    """Mixed trades: wins=[+10, +4], losses=[-5] → avg_win=+0.07, avg_loss=-0.05."""
    from alpaca_bot.replay.report import _compute_avg_win_loss_return
    trades = [_make_trade(10.0), _make_trade(-5.0), _make_trade(4.0)]
    avg_win, avg_loss = _compute_avg_win_loss_return(trades)
    assert avg_win == pytest.approx((10.0 / 100.0 + 4.0 / 100.0) / 2)
    assert avg_loss == pytest.approx(-5.0 / 100.0)


def test_avg_loss_includes_break_even_trades() -> None:
    """pnl=0.0 trade (return_pct=0.0) belongs to the loss bucket (pnl <= 0 convention)."""
    from alpaca_bot.replay.report import _compute_avg_win_loss_return
    trades = [_make_trade(10.0), _make_trade(0.0), _make_trade(-4.0)]
    avg_win, avg_loss = _compute_avg_win_loss_return(trades)
    assert avg_win == pytest.approx(10.0 / 100.0)
    assert avg_loss == pytest.approx((0.0 / 100.0 + -4.0 / 100.0) / 2)
```

- [ ] **Step 2: Run to confirm failure**

```bash
pytest tests/unit/test_replay_report.py::test_avg_win_return_pct_none_when_no_winners -v
```

Expected: `ImportError` or `AttributeError` — `_compute_avg_win_loss_return` does not exist yet.

- [ ] **Step 3: Add the two fields to BacktestReport and the helper function**

In `src/alpaca_bot/replay/report.py`, add two fields after `avg_hold_minutes` (line 38), before `max_consecutive_losses`:

```python
    avg_win_return_pct: float | None = None   # mean return_pct of winning trades; None if no winners
    avg_loss_return_pct: float | None = None  # mean return_pct of losing/break-even trades; None if no losers
```

Add the helper function **before** `_compute_streak_stats` (i.e., above line where `_compute_streak_stats` is defined):

```python
def _compute_avg_win_loss_return(
    trades: list[ReplayTradeRecord],
) -> tuple[float | None, float | None]:
    """Return (avg_win_return_pct, avg_loss_return_pct).

    Win bucket: pnl > 0.  Loss bucket: pnl <= 0 (includes break-even, consistent
    with the winning_trades / losing_trades field convention).
    Returns None for each bucket when no trades in that bucket.
    """
    win_returns = [t.return_pct for t in trades if t.pnl > 0]
    loss_returns = [t.return_pct for t in trades if t.pnl <= 0]
    avg_win = sum(win_returns) / len(win_returns) if win_returns else None
    avg_loss = sum(loss_returns) / len(loss_returns) if loss_returns else None
    return avg_win, avg_loss
```

Update `build_backtest_report()` — add the call immediately **after** the `avg_hold_minutes` line and **before** the `_compute_streak_stats` call (to follow the order fields are declared):

```python
    avg_win_return_pct, avg_loss_return_pct = _compute_avg_win_loss_return(trades)
```

Add both kwargs to the `BacktestReport(...)` constructor call, after `avg_hold_minutes=avg_hold_minutes` and before `max_consecutive_losses=max_consecutive_losses`:

```python
        avg_win_return_pct=avg_win_return_pct,
        avg_loss_return_pct=avg_loss_return_pct,
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
pytest tests/unit/test_replay_report.py -v
```

Expected: all existing tests + 4 new tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/alpaca_bot/replay/report.py tests/unit/test_replay_report.py
git commit -m "feat: add avg_win_return_pct / avg_loss_return_pct to BacktestReport"
```

---

## Task 2: Expose in replay CLI JSON/CSV output + update contract tests

**Files:**
- Modify: `src/alpaca_bot/replay/cli.py`
- Modify: `tests/unit/test_backtest_cli.py`

- [ ] **Step 1: Update test_backtest_cli.py contract tests (they will now fail)**

In `tests/unit/test_backtest_cli.py`, find `test_compare_json_output_shape` (line ~392).

Change the expected key set from:
```python
    assert set(row.keys()) == {
        "strategy", "total_trades", "win_rate",
        "mean_return_pct", "max_drawdown_pct", "sharpe_ratio", "profit_factor",
        "stop_wins", "stop_losses", "eod_wins", "eod_losses", "avg_hold_minutes",
        "max_consecutive_losses", "max_consecutive_wins",
    }
```
To:
```python
    assert set(row.keys()) == {
        "strategy", "total_trades", "win_rate",
        "mean_return_pct", "max_drawdown_pct", "sharpe_ratio", "profit_factor",
        "stop_wins", "stop_losses", "eod_wins", "eod_losses", "avg_hold_minutes",
        "avg_win_return_pct", "avg_loss_return_pct",
        "max_consecutive_losses", "max_consecutive_wins",
    }
```

Find `test_compare_csv_output_has_header_and_rows` (line ~426).

Change the expected fieldnames set from:
```python
    assert set(reader.fieldnames) == {
        "strategy", "total_trades", "win_rate",
        "mean_return_pct", "max_drawdown_pct", "sharpe_ratio", "profit_factor",
        "stop_wins", "stop_losses", "eod_wins", "eod_losses", "avg_hold_minutes",
        "max_consecutive_losses", "max_consecutive_wins",
    }
```
To:
```python
    assert set(reader.fieldnames) == {
        "strategy", "total_trades", "win_rate",
        "mean_return_pct", "max_drawdown_pct", "sharpe_ratio", "profit_factor",
        "stop_wins", "stop_losses", "eod_wins", "eod_losses", "avg_hold_minutes",
        "avg_win_return_pct", "avg_loss_return_pct",
        "max_consecutive_losses", "max_consecutive_wins",
    }
```

- [ ] **Step 2: Run to confirm both contract tests fail**

```bash
pytest tests/unit/test_backtest_cli.py::test_compare_json_output_shape tests/unit/test_backtest_cli.py::test_compare_csv_output_has_header_and_rows -v
```

Expected: both FAIL (new keys not yet in output).

- [ ] **Step 3: Update replay/cli.py**

In `_report_to_dict()`, add after `"avg_hold_minutes": report.avg_hold_minutes,` and before `"max_consecutive_losses"`:

```python
        "avg_win_return_pct": report.avg_win_return_pct,
        "avg_loss_return_pct": report.avg_loss_return_pct,
```

In `_compare_row()`, add after `"avg_hold_minutes": report.avg_hold_minutes,` and before `"max_consecutive_losses"`:

```python
        "avg_win_return_pct": report.avg_win_return_pct,
        "avg_loss_return_pct": report.avg_loss_return_pct,
```

In `_format_compare_csv()`, update `fieldnames` to include both new fields after `"avg_hold_minutes"` and before `"max_consecutive_losses"`:

```python
    fieldnames = [
        "strategy", "total_trades", "win_rate",
        "mean_return_pct", "max_drawdown_pct", "sharpe_ratio", "profit_factor",
        "stop_wins", "stop_losses", "eod_wins", "eod_losses", "avg_hold_minutes",
        "avg_win_return_pct", "avg_loss_return_pct",
        "max_consecutive_losses", "max_consecutive_wins",
    ]
```

- [ ] **Step 4: Run to confirm contract tests pass**

```bash
pytest tests/unit/test_backtest_cli.py -v
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/alpaca_bot/replay/cli.py tests/unit/test_backtest_cli.py
git commit -m "feat: expose avg_win_return_pct / avg_loss_return_pct in replay CLI JSON/CSV output"
```

---

## Task 3: Aggregate in `_aggregate_reports()` + test

**Files:**
- Modify: `src/alpaca_bot/tuning/sweep.py`
- Modify: `tests/unit/test_tuning_sweep.py`

- [ ] **Step 1: Write the failing test**

Append after `test_aggregate_reports_max_consecutive_losses_uses_worst_case` in `tests/unit/test_tuning_sweep.py`:

```python
def test_aggregate_reports_averages_win_loss_return_pct() -> None:
    """Aggregated avg_win/avg_loss are means of non-None per-scenario values."""
    from alpaca_bot.tuning.sweep import _aggregate_reports

    r1 = BacktestReport(
        trades=(), total_trades=3, winning_trades=2, losing_trades=1,
        win_rate=0.67, mean_return_pct=0.02, max_drawdown_pct=None,
        avg_win_return_pct=0.04, avg_loss_return_pct=-0.02,
    )
    r2 = BacktestReport(
        trades=(), total_trades=2, winning_trades=1, losing_trades=1,
        win_rate=0.5, mean_return_pct=0.01, max_drawdown_pct=None,
        avg_win_return_pct=0.02, avg_loss_return_pct=-0.01,
    )
    agg = _aggregate_reports([r1, r2])
    assert agg is not None
    assert agg.avg_win_return_pct == pytest.approx((0.04 + 0.02) / 2)
    assert agg.avg_loss_return_pct == pytest.approx((-0.02 + -0.01) / 2)
```

- [ ] **Step 2: Run to confirm failure**

```bash
pytest tests/unit/test_tuning_sweep.py::test_aggregate_reports_averages_win_loss_return_pct -v
```

Expected: FAIL — `_aggregate_reports` does not yet return these fields.

- [ ] **Step 3: Update `_aggregate_reports()` in tuning/sweep.py**

Add these two lines inside `_aggregate_reports()`, immediately before the `return BacktestReport(...)` call, after the `max_consecutive_wins` line:

```python
    win_avgs = [r.avg_win_return_pct for r in valid if r.avg_win_return_pct is not None]
    avg_win_return_pct: float | None = sum(win_avgs) / len(win_avgs) if win_avgs else None
    loss_avgs = [r.avg_loss_return_pct for r in valid if r.avg_loss_return_pct is not None]
    avg_loss_return_pct: float | None = sum(loss_avgs) / len(loss_avgs) if loss_avgs else None
```

Add both kwargs to the `BacktestReport(...)` constructor inside `_aggregate_reports()`, after `avg_hold_minutes=avg_hold_minutes` and before `max_consecutive_losses=max_consecutive_losses`:

```python
        avg_win_return_pct=avg_win_return_pct,
        avg_loss_return_pct=avg_loss_return_pct,
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
pytest tests/unit/test_tuning_sweep.py -v
```

Expected: all existing tests + 1 new test pass.

- [ ] **Step 5: Commit**

```bash
git add src/alpaca_bot/tuning/sweep.py tests/unit/test_tuning_sweep.py
git commit -m "feat: aggregate avg_win_return_pct / avg_loss_return_pct across scenarios"
```

---

## Task 4: Show R-multiple in sweep top-candidates display

**Files:**
- Modify: `src/alpaca_bot/tuning/cli.py`

No new unit test (display formatting; covered by smoke run).

- [ ] **Step 1: Update `_print_top_candidates()`**

In `src/alpaca_bot/tuning/cli.py`, in `_print_top_candidates()`, add the R-multiple calculation after the `max_cl` line:

```python
        if (report and report.avg_win_return_pct is not None
                and report.avg_loss_return_pct is not None
                and report.avg_loss_return_pct != 0.0):
            r_multiple = report.avg_win_return_pct / abs(report.avg_loss_return_pct)
            r_str = f"{r_multiple:.2f}"
        else:
            r_str = "—"
```

Update the `print(...)` call to include `R={r_str}` after `pf=`:

```python
        print(f"  [{i:2d}] score={sharpe:>8s}  trades={trades:2d}  win={win:>5s}  pf={pf:>5s}  R={r_str:>5s}  stop%={stop_pct:>4s}  maxcl={max_cl:>2d}  {params_str}")
```

- [ ] **Step 2: Smoke test**

```bash
pytest tests/unit/test_tuning_sweep.py tests/unit/test_backtest_cli.py tests/unit/test_replay_report.py -q
```

Expected: all tests pass.

- [ ] **Step 3: Full regression**

```bash
pytest -q
```

Expected: 1063+ passed (4 new in test_replay_report.py + 1 new in test_tuning_sweep.py = 5 new).

- [ ] **Step 4: Commit**

```bash
git add src/alpaca_bot/tuning/cli.py
git commit -m "feat: show R-multiple (avg_win / avg_loss) in sweep top-candidates display"
```
