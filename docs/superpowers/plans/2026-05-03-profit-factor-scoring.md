# Profit Factor Scoring Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `profit_factor: float | None` to `BacktestReport` and use it in `score_report()` as a penalty multiplier when `profit_factor < 1.0`, so net-losing strategies rank below profitable ones even if Sharpe is high.

**Architecture:** Computed bottom-up: `build_backtest_report()` calculates it from trades; `_aggregate_reports()` averages it across scenarios; `score_report()` uses it as a multiplier; display functions in both CLIs expose it to operators.

**Tech Stack:** Python, pytest, existing `alpaca_bot.replay` and `alpaca_bot.tuning` modules.

---

### Task 1: Failing tests for profit_factor computation

**Files:**
- Modify: `tests/unit/test_replay_report.py` (append after existing tests)

- [ ] **Step 1: Write the 4 failing tests**

Append to `tests/unit/test_replay_report.py`:

```python
# ---------------------------------------------------------------------------
# profit_factor
# ---------------------------------------------------------------------------


def test_profit_factor_none_for_zero_trades() -> None:
    result = _make_result([])
    assert build_backtest_report(result).profit_factor is None


def test_profit_factor_none_when_no_losses() -> None:
    """All winners → no losses to divide by → None (not penalized)."""
    result = _make_result([
        _fill(entry_price=150.0, quantity=10, t=_T0),
        _eod_exit(exit_price=155.0, t=_T1),
    ])
    report = build_backtest_report(result)
    assert report.profit_factor is None


def test_profit_factor_zero_when_all_losers() -> None:
    """All losers → gross_wins = 0 → profit_factor = 0.0."""
    result = _make_result([
        _fill(entry_price=150.0, quantity=10, t=_T0),
        _stop_exit(exit_price=148.0, t=_T1),  # pnl = -20
    ])
    report = build_backtest_report(result)
    assert report.profit_factor == pytest.approx(0.0)


def test_profit_factor_correct_with_mixed_trades() -> None:
    """2 wins (+50 + +50) against 1 loss (-20) → profit_factor = 100/20 = 5.0."""
    result = _make_result([
        _fill(entry_price=150.0, quantity=10, t=_T0),
        _eod_exit(exit_price=155.0, t=_T1),  # pnl = +50
        ReplayEvent(
            event_type=IntentType.ENTRY_FILLED, symbol="AAPL", timestamp=_T1,
            details={"entry_price": 155.0, "quantity": 10, "initial_stop_price": 153.0},
        ),
        _eod_exit(exit_price=160.0, t=_T2),  # pnl = +50
        ReplayEvent(
            event_type=IntentType.ENTRY_FILLED, symbol="AAPL",
            timestamp=datetime(2026, 4, 24, 14, 45, tzinfo=timezone.utc),
            details={"entry_price": 160.0, "quantity": 10, "initial_stop_price": 158.0},
        ),
        ReplayEvent(
            event_type=IntentType.STOP_HIT, symbol="AAPL",
            timestamp=datetime(2026, 4, 24, 15, 0, tzinfo=timezone.utc),
            details={"exit_price": 158.0},  # pnl = -20
        ),
    ])
    report = build_backtest_report(result)
    assert report.total_trades == 3
    assert report.profit_factor == pytest.approx(100.0 / 20.0)
```

- [ ] **Step 2: Run to verify tests fail**

```
pytest tests/unit/test_replay_report.py::test_profit_factor_none_for_zero_trades tests/unit/test_replay_report.py::test_profit_factor_none_when_no_losses tests/unit/test_replay_report.py::test_profit_factor_zero_when_all_losers tests/unit/test_replay_report.py::test_profit_factor_correct_with_mixed_trades -v
```

Expected: `AttributeError: 'BacktestReport' object has no attribute 'profit_factor'` or similar.

---

### Task 2: Failing tests for score_report() with profit_factor

**Files:**
- Modify: `tests/unit/test_tuning_sweep.py` (append after existing tests)

- [ ] **Step 1: Write the 3 failing tests**

Append to `tests/unit/test_tuning_sweep.py`:

```python
# ---------------------------------------------------------------------------
# score_report: profit_factor penalty
# ---------------------------------------------------------------------------

def test_score_report_penalizes_subunit_profit_factor() -> None:
    """profit_factor=0.7 with sharpe=2.0 → score = 2.0 * 0.7 = 1.4."""
    report = BacktestReport(
        trades=(), total_trades=5, winning_trades=3, losing_trades=2,
        win_rate=0.6, mean_return_pct=0.02, max_drawdown_pct=0.05,
        sharpe_ratio=2.0, profit_factor=0.7,
    )
    assert score_report(report, min_trades=3) == pytest.approx(1.4)


def test_score_report_no_penalty_when_profit_factor_at_or_above_one() -> None:
    """profit_factor=1.5 with sharpe=2.0 → score = 2.0 (no upward scaling)."""
    report = BacktestReport(
        trades=(), total_trades=5, winning_trades=3, losing_trades=2,
        win_rate=0.6, mean_return_pct=0.02, max_drawdown_pct=0.05,
        sharpe_ratio=2.0, profit_factor=1.5,
    )
    assert score_report(report, min_trades=3) == pytest.approx(2.0)


def test_score_report_no_penalty_when_profit_factor_none() -> None:
    """profit_factor=None (no losses) → score is unchanged from Sharpe."""
    report = BacktestReport(
        trades=(), total_trades=5, winning_trades=5, losing_trades=0,
        win_rate=1.0, mean_return_pct=0.05, max_drawdown_pct=None,
        sharpe_ratio=3.0, profit_factor=None,
    )
    assert score_report(report, min_trades=3) == pytest.approx(3.0)
```

- [ ] **Step 2: Run to verify tests fail**

```
pytest tests/unit/test_tuning_sweep.py::test_score_report_penalizes_subunit_profit_factor tests/unit/test_tuning_sweep.py::test_score_report_no_penalty_when_profit_factor_at_or_above_one tests/unit/test_tuning_sweep.py::test_score_report_no_penalty_when_profit_factor_none -v
```

Expected: TypeError — `BacktestReport` doesn't accept `profit_factor` kwarg yet. Two of
three may also fail on assertion (penalty not applied).

---

### Task 3: Add profit_factor to BacktestReport

**Files:**
- Modify: `src/alpaca_bot/replay/report.py`

- [ ] **Step 1: Add the field and computation**

In `BacktestReport`, add after `sharpe_ratio`:
```python
    profit_factor: float | None = None  # gross_wins_pnl / abs(gross_losses_pnl); None when no losses
```

In `build_backtest_report()`, after computing `max_drawdown_pct`, add:
```python
    gross_wins_pnl = sum(t.pnl for t in trades if t.pnl > 0)
    gross_losses_pnl = abs(sum(t.pnl for t in trades if t.pnl < 0))
    profit_factor = gross_wins_pnl / gross_losses_pnl if gross_losses_pnl > 0 else None
```

And pass it in the `BacktestReport(...)` constructor call:
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
        strategy_name=strategy_name,
    )
```

(The zero-trades early return already produces a BacktestReport without `profit_factor`, which
defaults to `None` — no change needed there.)

- [ ] **Step 2: Run the report tests to verify they pass**

```
pytest tests/unit/test_replay_report.py::test_profit_factor_none_for_zero_trades tests/unit/test_replay_report.py::test_profit_factor_none_when_no_losses tests/unit/test_replay_report.py::test_profit_factor_zero_when_all_losers tests/unit/test_replay_report.py::test_profit_factor_correct_with_mixed_trades -v
```

Expected: PASS.

---

### Task 4: Update score_report() and _aggregate_reports()

**Files:**
- Modify: `src/alpaca_bot/tuning/sweep.py`

- [ ] **Step 1: Update score_report()**

Replace the current body of `score_report()`:
```python
def score_report(report: BacktestReport, *, min_trades: int = 3) -> float | None:
    """Sharpe-first composite score; None if disqualified (< min_trades)."""
    if report.total_trades < min_trades:
        return None
    if report.sharpe_ratio is not None:
        return report.sharpe_ratio
    if report.mean_return_pct is None:
        return None
    drawdown = report.max_drawdown_pct or 0.0
    return report.mean_return_pct / (drawdown + 0.001)
```

With:
```python
def score_report(report: BacktestReport, *, min_trades: int = 3) -> float | None:
    """Sharpe-first composite score; None if disqualified (< min_trades).

    When profit_factor < 1.0 (net-losing strategy), the score is scaled down
    by profit_factor so that net-profitable strategies rank higher at equal Sharpe.
    """
    if report.total_trades < min_trades:
        return None
    if report.sharpe_ratio is not None:
        base = report.sharpe_ratio
    elif report.mean_return_pct is None:
        return None
    else:
        drawdown = report.max_drawdown_pct or 0.0
        base = report.mean_return_pct / (drawdown + 0.001)
    if report.profit_factor is not None and report.profit_factor < 1.0:
        base *= report.profit_factor
    return base
```

- [ ] **Step 2: Update _aggregate_reports()**

After the `sharpe_ratio` line (line ~128), add:
```python
    profit_factors = [r.profit_factor for r in valid if r.profit_factor is not None]
    profit_factor: float | None = sum(profit_factors) / len(profit_factors) if profit_factors else None
```

And in the `BacktestReport(...)` constructor call, add:
```python
        profit_factor=profit_factor,
```

The full updated `return` in `_aggregate_reports()` should be:
```python
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
        strategy_name="aggregate",
    )
```

- [ ] **Step 3: Run the sweep scoring tests to verify they pass**

```
pytest tests/unit/test_tuning_sweep.py::test_score_report_penalizes_subunit_profit_factor tests/unit/test_tuning_sweep.py::test_score_report_no_penalty_when_profit_factor_at_or_above_one tests/unit/test_tuning_sweep.py::test_score_report_no_penalty_when_profit_factor_none -v
```

Expected: PASS.

---

### Task 5: Display profit_factor in CLI outputs

**Files:**
- Modify: `src/alpaca_bot/tuning/cli.py`
- Modify: `src/alpaca_bot/replay/cli.py`

- [ ] **Step 1: Update _print_top_candidates() in tuning/cli.py**

Replace the body of `_print_top_candidates`:
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
        params_str = " ".join(f"{k}={v}" for k, v in c.params.items())
        print(f"  [{i:2d}] score={sharpe:>8s}  trades={trades:2d}  win={win:>5s}  {params_str}")
```

With:
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
        params_str = " ".join(f"{k}={v}" for k, v in c.params.items())
        print(f"  [{i:2d}] score={sharpe:>8s}  trades={trades:2d}  win={win:>5s}  pf={pf:>5s}  {params_str}")
```

- [ ] **Step 2: Update replay/cli.py**

In `_report_to_dict()`, add `"profit_factor": report.profit_factor` after `"sharpe_ratio"`:
```python
def _report_to_dict(report: BacktestReport) -> dict:
    return {
        "strategy": report.strategy_name,
        "total_trades": report.total_trades,
        "winning_trades": report.winning_trades,
        "losing_trades": report.losing_trades,
        "win_rate": report.win_rate,
        "mean_return_pct": report.mean_return_pct,
        "max_drawdown_pct": report.max_drawdown_pct,
        "sharpe_ratio": report.sharpe_ratio,
        "profit_factor": report.profit_factor,
        "trades": [_trade_to_dict(t) for t in report.trades],
    }
```

In `_compare_row()`, add `"profit_factor": report.profit_factor` after `"sharpe_ratio"`:
```python
def _compare_row(report: BacktestReport) -> dict:
    return {
        "strategy": report.strategy_name,
        "total_trades": report.total_trades,
        "win_rate": report.win_rate,
        "mean_return_pct": report.mean_return_pct,
        "max_drawdown_pct": report.max_drawdown_pct,
        "sharpe_ratio": report.sharpe_ratio,
        "profit_factor": report.profit_factor,
    }
```

In `_format_compare_csv()`, add `"profit_factor"` to `fieldnames`:
```python
def _format_compare_csv(reports: list[BacktestReport]) -> str:
    fieldnames = [
        "strategy", "total_trades", "win_rate",
        "mean_return_pct", "max_drawdown_pct", "sharpe_ratio", "profit_factor",
    ]
    ...
```

---

### Task 6: Full regression and commit

- [ ] **Step 1: Run tuning and report test suites**

```
pytest tests/unit/test_replay_report.py tests/unit/test_tuning_sweep.py tests/unit/test_tuning_sweep_cli.py -v
```

Expected: 27 tests pass (20 existing + 7 new).

- [ ] **Step 2: Run full regression**

```
pytest --tb=short -q
```

Expected: 1048 tests pass (1041 prior + 7 new).

- [ ] **Step 3: Commit**

```bash
git add src/alpaca_bot/replay/report.py \
        src/alpaca_bot/tuning/sweep.py \
        src/alpaca_bot/tuning/cli.py \
        src/alpaca_bot/replay/cli.py \
        tests/unit/test_replay_report.py \
        tests/unit/test_tuning_sweep.py
git commit -m "feat: add profit_factor to BacktestReport and score_report() penalty

Strategies with gross_losses > gross_wins (profit_factor < 1.0) now have
their score multiplied by profit_factor, ranking below profitable strategies
at equal Sharpe. Profit factor is shown in sweep top-candidates output and
replay JSON/CSV compare output.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```
