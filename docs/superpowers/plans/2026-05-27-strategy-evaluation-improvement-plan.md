# Strategy Evaluation and Improvement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Align backtest scoring with the capital-allocation Sharpe metric and add a signal-funnel analytics CLI that reads `decision_log`.

**Architecture:** Add `annualized_sharpe` to `BacktestReport` using daily PnL bucketing with `sqrt(252)` annualization (matching `risk/weighting.py`); update `score_report()` and `_aggregate_reports()` in `sweep.py` to prefer it; add `DecisionLogStore.funnel_by_strategy()` SQL query and a new `alpaca-bot-funnel-report` CLI.

**Tech Stack:** Python 3.11, psycopg (Postgres), argparse, plain-text table formatting (no new dependencies)

---

## File Map

| File | Action | What changes |
|------|--------|-------------|
| `src/alpaca_bot/replay/report.py` | Modify | Add `import math`, `date` import; add `annualized_sharpe` field to `BacktestReport`; add `_compute_annualized_sharpe()`; call it in `report_from_records()` |
| `src/alpaca_bot/tuning/sweep.py` | Modify | `score_report()` prefers `annualized_sharpe`; `_aggregate_reports()` averages it across scenarios |
| `src/alpaca_bot/storage/repositories.py` | Modify | Add `DecisionLogStore.funnel_by_strategy()` |
| `src/alpaca_bot/admin/funnel_report_cli.py` | Create | `main()` CLI: argparse, DB connect, calls `funnel_by_strategy()`, prints table |
| `pyproject.toml` | Modify | Add `alpaca-bot-funnel-report` entry point |
| `tests/unit/test_annualized_sharpe.py` | Create | Tests for `_compute_annualized_sharpe()`, `BacktestReport.annualized_sharpe`, `score_report()` preference |
| `tests/unit/test_funnel_report.py` | Create | Tests for `funnel_by_strategy()` and CLI output |

---

## Task 1: Annualized Sharpe — tests and `report.py` changes

**Files:**
- Create: `tests/unit/test_annualized_sharpe.py`
- Modify: `src/alpaca_bot/replay/report.py`

- [ ] **Step 1: Write failing tests**

Create `tests/unit/test_annualized_sharpe.py`:

```python
from __future__ import annotations

import math
from datetime import datetime, timezone

import pytest

from alpaca_bot.replay.report import (
    BacktestReport,
    ReplayTradeRecord,
    _compute_annualized_sharpe,
    report_from_records,
)


def _trade(*, pnl: float, exit_day: int, month: int = 5) -> ReplayTradeRecord:
    return ReplayTradeRecord(
        symbol="AAPL",
        entry_price=100.0,
        exit_price=100.0 + pnl,
        quantity=1,
        entry_time=datetime(2026, month, exit_day, 13, 0, tzinfo=timezone.utc),
        exit_time=datetime(2026, month, exit_day, 15, 0, tzinfo=timezone.utc),
        exit_reason="eod",
        pnl=pnl,
        return_pct=pnl / 100.0,
    )


def test_annualized_sharpe_groups_trades_by_exit_day() -> None:
    # Day 1 (May 1): pnl sums to 200.0; Day 2 (May 2): pnl sums to -100.0
    # mean([200, -100]) = 50; variance = (150^2 + 150^2)/1 = 45000; std = sqrt(45000)
    # annualized = 50 / sqrt(45000) * sqrt(252)
    trades = [
        _trade(pnl=120.0, exit_day=1),
        _trade(pnl=80.0, exit_day=1),   # day 1 total = 200.0
        _trade(pnl=-60.0, exit_day=2),
        _trade(pnl=-40.0, exit_day=2),  # day 2 total = -100.0
    ]
    expected = 50.0 / math.sqrt(45000) * math.sqrt(252)
    result = _compute_annualized_sharpe(trades)
    assert result == pytest.approx(expected)


def test_annualized_sharpe_none_when_all_trades_same_day() -> None:
    trades = [_trade(pnl=100.0, exit_day=1), _trade(pnl=-50.0, exit_day=1)]
    assert _compute_annualized_sharpe(trades) is None


def test_annualized_sharpe_none_when_fewer_than_two_trades() -> None:
    assert _compute_annualized_sharpe([]) is None
    assert _compute_annualized_sharpe([_trade(pnl=100.0, exit_day=1)]) is None


def test_annualized_sharpe_none_when_all_days_identical_pnl() -> None:
    # std == 0 when all daily sums are equal
    trades = [_trade(pnl=100.0, exit_day=1), _trade(pnl=100.0, exit_day=2)]
    assert _compute_annualized_sharpe(trades) is None


def test_report_from_records_populates_annualized_sharpe() -> None:
    trades = [
        _trade(pnl=200.0, exit_day=1),
        _trade(pnl=-100.0, exit_day=2),
    ]
    report = report_from_records(trades, starting_equity=100_000.0)
    assert report.annualized_sharpe is not None
    expected = 50.0 / math.sqrt(45000) * math.sqrt(252)
    assert report.annualized_sharpe == pytest.approx(expected)


def test_report_from_records_annualized_sharpe_none_when_zero_trades() -> None:
    report = report_from_records([], starting_equity=100_000.0)
    assert report.annualized_sharpe is None


def test_backtest_report_annualized_sharpe_defaults_to_none() -> None:
    report = BacktestReport(
        trades=(), total_trades=5, winning_trades=3, losing_trades=2,
        win_rate=0.6, mean_return_pct=0.01, max_drawdown_pct=0.05, sharpe_ratio=1.0,
    )
    assert report.annualized_sharpe is None
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/unit/test_annualized_sharpe.py -v
```

Expected: `ImportError: cannot import name '_compute_annualized_sharpe' from 'alpaca_bot.replay.report'`

- [ ] **Step 3: Implement changes in `report.py`**

Edit `src/alpaca_bot/replay/report.py`.

**3a. Update imports** — change the header section:

Old:
```python
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from alpaca_bot.domain.enums import IntentType
from alpaca_bot.domain.models import ReplayEvent, ReplayResult
```

New:
```python
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, datetime

from alpaca_bot.domain.enums import IntentType
from alpaca_bot.domain.models import ReplayEvent, ReplayResult
```

**3b. Add `annualized_sharpe` field after `sharpe_ratio`** in `BacktestReport`:

Old:
```python
    sharpe_ratio: float | None = None
    profit_factor: float | None = None  # gross_wins_pnl / abs(gross_losses_pnl); None when no losses
```

New:
```python
    sharpe_ratio: float | None = None
    annualized_sharpe: float | None = None  # daily-bucketed, sqrt(252) annualized
    profit_factor: float | None = None  # gross_wins_pnl / abs(gross_losses_pnl); None when no losses
```

**3c. Add `annualized_sharpe` to `report_from_records()` return** — the BacktestReport constructor call starting around line 104:

Old:
```python
        sharpe_ratio=_compute_sharpe(trades),
        profit_factor=profit_factor,
```

New:
```python
        sharpe_ratio=_compute_sharpe(trades),
        annualized_sharpe=_compute_annualized_sharpe(trades),
        profit_factor=profit_factor,
```

**3d. Add `_compute_annualized_sharpe()` function** — insert after `_compute_sharpe()` (around line 216):

Old:
```python
def _compute_max_drawdown(
    trades: list[ReplayTradeRecord], starting_equity: float
) -> float | None:
```

New:
```python
def _compute_annualized_sharpe(trades: list[ReplayTradeRecord]) -> float | None:
    """Daily-bucketed Sharpe ratio annualized by sqrt(252).

    Groups trades by exit date, sums PnL per day — consistent with weighting.py.
    Returns None when fewer than 2 distinct trading days or zero std.
    """
    if len(trades) < 2:
        return None
    daily: dict[date, float] = {}
    for t in trades:
        d = t.exit_time.date()
        daily[d] = daily.get(d, 0.0) + t.pnl
    if len(daily) < 2:
        return None
    values = list(daily.values())
    n = len(values)
    mean = sum(values) / n
    variance = sum((v - mean) ** 2 for v in values) / (n - 1)
    std = variance ** 0.5
    if std == 0.0:
        return None
    return mean / std * math.sqrt(252)


def _compute_max_drawdown(
    trades: list[ReplayTradeRecord], starting_equity: float
) -> float | None:
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/unit/test_annualized_sharpe.py -v
```

Expected: all 7 tests PASS.

- [ ] **Step 5: Run the full test suite to check for regressions**

```bash
pytest tests/unit/ -x -q
```

Expected: all tests pass. No regressions from the new `annualized_sharpe` field (it has a default of `None`, so existing BacktestReport constructors that don't pass it continue to work).

- [ ] **Step 6: Commit**

```bash
git add tests/unit/test_annualized_sharpe.py src/alpaca_bot/replay/report.py
git commit -m "feat: add annualized_sharpe to BacktestReport using daily PnL bucketing"
```

---

## Task 2: Update `score_report()` and `_aggregate_reports()` in sweep.py

**Files:**
- Modify: `src/alpaca_bot/tuning/sweep.py`
- Modify: `tests/unit/test_annualized_sharpe.py` (add 2 more tests)

- [ ] **Step 1: Add failing tests for score_report preference logic**

Append to `tests/unit/test_annualized_sharpe.py`:

```python
from alpaca_bot.tuning.sweep import score_report


def test_score_report_prefers_annualized_sharpe_when_both_set() -> None:
    report = BacktestReport(
        trades=(), total_trades=5, winning_trades=3, losing_trades=2,
        win_rate=0.6, mean_return_pct=0.01, max_drawdown_pct=0.05,
        sharpe_ratio=1.0,
        annualized_sharpe=3.5,  # annualized > sharpe_ratio — should win
    )
    result = score_report(report, min_trades=3)
    assert result == pytest.approx(3.5)


def test_score_report_falls_back_to_sharpe_when_annualized_none() -> None:
    report = BacktestReport(
        trades=(), total_trades=5, winning_trades=3, losing_trades=2,
        win_rate=0.6, mean_return_pct=0.01, max_drawdown_pct=0.05,
        sharpe_ratio=2.5,
        annualized_sharpe=None,
    )
    result = score_report(report, min_trades=3)
    assert result == pytest.approx(2.5)
```

- [ ] **Step 2: Run new tests to verify they fail**

```bash
pytest tests/unit/test_annualized_sharpe.py::test_score_report_prefers_annualized_sharpe_when_both_set tests/unit/test_annualized_sharpe.py::test_score_report_falls_back_to_sharpe_when_annualized_none -v
```

Expected: `test_score_report_prefers_annualized_sharpe_when_both_set` FAILS (returns 1.0, not 3.5). The fallback test PASSES already.

- [ ] **Step 3: Update `score_report()` in `sweep.py`**

In `src/alpaca_bot/tuning/sweep.py`, the current `score_report()` body (around line 146):

Old:
```python
    if report.sharpe_ratio is not None:
        base = report.sharpe_ratio
    elif report.mean_return_pct is None:
```

New:
```python
    if report.annualized_sharpe is not None:
        base = report.annualized_sharpe
    elif report.sharpe_ratio is not None:
        base = report.sharpe_ratio
    elif report.mean_return_pct is None:
```

- [ ] **Step 4: Update `_aggregate_reports()` in `sweep.py`**

In `_aggregate_reports()`, add `annualized_sharpe` aggregation and pass it to the returned `BacktestReport`.

Old (around line 177–201):
```python
    sharpes = [r.sharpe_ratio for r in valid if r.sharpe_ratio is not None]
    sharpe_ratio: float | None = sum(sharpes) / len(sharpes) if sharpes else None
    profit_factors = [r.profit_factor for r in valid if r.profit_factor is not None]
    profit_factor: float | None = sum(profit_factors) / len(profit_factors) if profit_factors else None
```

New:
```python
    sharpes = [r.sharpe_ratio for r in valid if r.sharpe_ratio is not None]
    sharpe_ratio: float | None = sum(sharpes) / len(sharpes) if sharpes else None
    ann_sharpes = [r.annualized_sharpe for r in valid if r.annualized_sharpe is not None]
    annualized_sharpe: float | None = sum(ann_sharpes) / len(ann_sharpes) if ann_sharpes else None
    profit_factors = [r.profit_factor for r in valid if r.profit_factor is not None]
    profit_factor: float | None = sum(profit_factors) / len(profit_factors) if profit_factors else None
```

Then in the `return BacktestReport(...)` call at the bottom of `_aggregate_reports()`:

Old:
```python
        sharpe_ratio=sharpe_ratio,
        profit_factor=profit_factor,
```

New:
```python
        sharpe_ratio=sharpe_ratio,
        annualized_sharpe=annualized_sharpe,
        profit_factor=profit_factor,
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
pytest tests/unit/test_annualized_sharpe.py -v
```

Expected: all 9 tests PASS.

- [ ] **Step 6: Run the full test suite**

```bash
pytest tests/unit/ -x -q
```

Expected: all tests pass.

- [ ] **Step 7: Commit**

```bash
git add tests/unit/test_annualized_sharpe.py src/alpaca_bot/tuning/sweep.py
git commit -m "feat: score_report prefers annualized_sharpe; aggregate_reports propagates it"
```

---

## Task 3: `DecisionLogStore.funnel_by_strategy()` — tests and implementation

**Files:**
- Create: `tests/unit/test_funnel_report.py`
- Modify: `src/alpaca_bot/storage/repositories.py`

- [ ] **Step 1: Write failing test for `funnel_by_strategy()`**

Create `tests/unit/test_funnel_report.py`:

```python
from __future__ import annotations

from datetime import date

from alpaca_bot.storage.repositories import DecisionLogStore


class _FakeCursor:
    """Cursor that returns predefined rows from fetchall()."""

    def __init__(self, rows: list[tuple]) -> None:
        self._rows = rows

    def execute(self, sql: str, params) -> None:
        pass  # no-op; rows are predefined

    def fetchall(self) -> list[tuple]:
        return self._rows


class _FakeConn:
    def __init__(self, rows: list[tuple]) -> None:
        self._rows = rows

    def cursor(self) -> _FakeCursor:
        return _FakeCursor(self._rows)


def _make_rows() -> list[tuple]:
    # Columns: strategy_name, evaluated, not_skipped, not_prefiltered,
    #          signal_fired, passed_entry_filter, sized, accepted
    return [
        ("breakout", 10, 8, 5, 4, 3, 3, 2),
        ("orb",       5, 5, 5, 3, 2, 1, 1),
    ]


def test_funnel_by_strategy_returns_dicts_with_correct_counts() -> None:
    conn = _FakeConn(_make_rows())
    store = DecisionLogStore(conn)
    result = store.funnel_by_strategy(
        start_date=date(2026, 5, 1),
        end_date=date(2026, 5, 7),
        trading_mode="paper",
    )
    assert len(result) == 2

    brk = next(r for r in result if r["strategy_name"] == "breakout")
    assert brk["evaluated"] == 10
    assert brk["not_skipped"] == 8
    assert brk["not_prefiltered"] == 5
    assert brk["signal_fired"] == 4
    assert brk["passed_entry_filter"] == 3
    assert brk["sized"] == 3
    assert brk["accepted"] == 2

    orb = next(r for r in result if r["strategy_name"] == "orb")
    assert orb["accepted"] == 1


def test_funnel_by_strategy_empty_result() -> None:
    conn = _FakeConn([])
    store = DecisionLogStore(conn)
    result = store.funnel_by_strategy(
        start_date=date(2026, 5, 1),
        end_date=date(2026, 5, 7),
        trading_mode="paper",
    )
    assert result == []
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/unit/test_funnel_report.py::test_funnel_by_strategy_returns_dicts_with_correct_counts -v
```

Expected: `AttributeError: 'DecisionLogStore' object has no attribute 'funnel_by_strategy'`

- [ ] **Step 3: Implement `funnel_by_strategy()` in `repositories.py`**

In `src/alpaca_bot/storage/repositories.py`, inside `class DecisionLogStore`, after `list_recent()` (around line 2283, before `class MarketContextStore`):

```python
    def funnel_by_strategy(
        self,
        *,
        start_date: "date",
        end_date: "date",
        trading_mode: str,
        market_timezone: str = "America/New_York",
    ) -> list[dict]:
        """Per-strategy funnel counts for a date range.

        Each row: strategy_name, evaluated, not_skipped, not_prefiltered,
        signal_fired, passed_entry_filter, sized, accepted.
        Counts represent rows that passed *through* each stage — monotonically
        non-increasing from left to right.
        """
        _cols = (
            "strategy_name", "evaluated", "not_skipped", "not_prefiltered",
            "signal_fired", "passed_entry_filter", "sized", "accepted",
        )
        rows = fetch_all(
            self._connection,
            """
            SELECT
                strategy_name,
                COUNT(*) AS evaluated,
                COUNT(*) FILTER (
                    WHERE decision NOT IN (
                        'skipped_existing_position', 'skipped_already_traded'
                    )
                ) AS not_skipped,
                COUNT(*) FILTER (
                    WHERE decision NOT IN (
                        'skipped_existing_position', 'skipped_already_traded'
                    )
                      AND reject_stage IS DISTINCT FROM 'pre_filter'
                      AND reject_stage IS DISTINCT FROM 'stale_data'
                ) AS not_prefiltered,
                COUNT(*) FILTER (
                    WHERE decision NOT IN (
                        'skipped_existing_position', 'skipped_already_traded',
                        'skipped_no_signal'
                    )
                      AND reject_stage IS DISTINCT FROM 'pre_filter'
                      AND reject_stage IS DISTINCT FROM 'stale_data'
                ) AS signal_fired,
                COUNT(*) FILTER (
                    WHERE decision NOT IN (
                        'skipped_existing_position', 'skipped_already_traded',
                        'skipped_no_signal'
                    )
                      AND reject_stage IS DISTINCT FROM 'pre_filter'
                      AND reject_stage IS DISTINCT FROM 'stale_data'
                      AND reject_stage IS DISTINCT FROM 'vwap_filter'
                ) AS passed_entry_filter,
                COUNT(*) FILTER (
                    WHERE decision NOT IN (
                        'skipped_existing_position', 'skipped_already_traded',
                        'skipped_no_signal'
                    )
                      AND reject_stage IS DISTINCT FROM 'pre_filter'
                      AND reject_stage IS DISTINCT FROM 'stale_data'
                      AND reject_stage IS DISTINCT FROM 'vwap_filter'
                      AND reject_stage IS DISTINCT FROM 'sizing'
                ) AS sized,
                COUNT(*) FILTER (WHERE decision = 'accepted') AS accepted
            FROM decision_log
            WHERE DATE(cycle_at AT TIME ZONE %s) BETWEEN %s AND %s
              AND trading_mode = %s
            GROUP BY strategy_name
            ORDER BY strategy_name
            """,
            (market_timezone, start_date, end_date, trading_mode),
        )
        return [dict(zip(_cols, row)) for row in rows]
```

The `date` type annotation here uses a string (`"date"`) to avoid a forward-reference import issue if `date` isn't imported at the top of the type-annotation context. But `date` **is** already imported at the top of `repositories.py` (line 9: `from datetime import date, datetime`), so you may use `date` directly without quotes.

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/unit/test_funnel_report.py -v
```

Expected: both tests PASS.

- [ ] **Step 5: Run full test suite**

```bash
pytest tests/unit/ -x -q
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add tests/unit/test_funnel_report.py src/alpaca_bot/storage/repositories.py
git commit -m "feat: add DecisionLogStore.funnel_by_strategy() SQL query"
```

---

## Task 4: `funnel_report_cli.py` — tests and implementation

**Files:**
- Create: `src/alpaca_bot/admin/funnel_report_cli.py`
- Modify: `tests/unit/test_funnel_report.py` (add CLI tests)

- [ ] **Step 1: Add failing CLI test to `test_funnel_report.py`**

Append to `tests/unit/test_funnel_report.py`:

```python
def test_funnel_cli_prints_header_and_rows(monkeypatch, capsys) -> None:
    """main() prints strategy funnel table to stdout."""
    from types import SimpleNamespace
    import alpaca_bot.admin.funnel_report_cli as cli_module

    fake_rows = [
        {
            "strategy_name": "breakout",
            "evaluated": 100,
            "not_skipped": 90,
            "not_prefiltered": 60,
            "signal_fired": 30,
            "passed_entry_filter": 28,
            "sized": 27,
            "accepted": 15,
        },
    ]

    class _FakeSettings:
        database_url = "postgresql://x:x@localhost/x"
        market_timezone = SimpleNamespace(key="America/New_York")

    class _FakeStore:
        def __init__(self, conn):
            pass

        def funnel_by_strategy(self, **kwargs):
            return fake_rows

    # Patch on cli_module (the bound names), not on the source modules.
    # Project pattern: monkeypatch.setattr(cli_module, "Settings", ...) so the
    # reference already imported into the CLI module namespace is replaced.
    monkeypatch.setattr(cli_module, "Settings", SimpleNamespace(from_env=lambda: _FakeSettings()))
    monkeypatch.setattr(cli_module, "connect_postgres", lambda url: None)
    monkeypatch.setattr(cli_module, "DecisionLogStore", _FakeStore)

    exit_code = cli_module.main(["--days", "7"])

    output = capsys.readouterr().out
    assert "breakout" in output
    assert "Strategy" in output  # header
    assert "100" in output        # evaluated count
    assert "15" in output         # accepted count
    assert exit_code == 0
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/unit/test_funnel_report.py::test_funnel_cli_prints_header_and_rows -v
```

Expected: `ModuleNotFoundError: No module named 'alpaca_bot.admin.funnel_report_cli'`

- [ ] **Step 3: Create `funnel_report_cli.py`**

Create `src/alpaca_bot/admin/funnel_report_cli.py`:

```python
from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from typing import Sequence

from alpaca_bot.config import Settings
from alpaca_bot.storage.db import connect_postgres
from alpaca_bot.storage.repositories import DecisionLogStore


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="alpaca-bot-funnel-report",
        description="Per-strategy signal funnel from decision_log",
    )
    parser.add_argument("--days", type=int, default=7,
                        help="Number of trailing days to include (default: 7)")
    parser.add_argument("--start", metavar="YYYY-MM-DD",
                        help="Start date (overrides --days)")
    parser.add_argument("--end", metavar="YYYY-MM-DD",
                        help="End date (default: today; requires --start)")
    parser.add_argument("--mode", default="paper", choices=["paper", "live"],
                        help="Trading mode filter (default: paper)")
    args = parser.parse_args(list(argv) if argv is not None else sys.argv[1:])

    end_date = date.fromisoformat(args.end) if args.end else date.today()
    start_date = (
        date.fromisoformat(args.start) if args.start
        else end_date - timedelta(days=args.days - 1)
    )

    settings = Settings.from_env()
    conn = connect_postgres(settings.database_url)
    try:
        store = DecisionLogStore(conn)
        rows = store.funnel_by_strategy(
            start_date=start_date,
            end_date=end_date,
            trading_mode=args.mode,
            market_timezone=settings.market_timezone.key,
        )
    finally:
        close_fn = getattr(conn, "close", None)
        if callable(close_fn):
            close_fn()

    _print_table(rows, start_date, end_date)
    return 0


def _print_table(rows: list[dict], start_date: date, end_date: date) -> None:
    header = (
        f" Signal Funnel  {start_date} → {end_date}"
    )
    print()
    print(header)
    print(" " + "─" * (len(header) - 1))

    if not rows:
        print(" (no decision_log rows in period)")
        print()
        return

    col_w = 20
    num_w = 7
    print(
        f" {'Strategy':<{col_w}} "
        f"{'Eval':>{num_w}} "
        f"{'NotSkip':>{num_w}} "
        f"{'!PreFlt':>{num_w}} "
        f"{'Signal':>{num_w}} "
        f"{'!VWAP':>{num_w}} "
        f"{'Sized':>{num_w}} "
        f"{'Accept':>{num_w}}"
    )
    print(
        f" {'-'*col_w} "
        + (f"{'-'*num_w} " * 7).rstrip()
    )

    for row in rows:
        name = row["strategy_name"] or "(unknown)"
        print(
            f" {name:<{col_w}} "
            f"{row['evaluated']:>{num_w}} "
            f"{row['not_skipped']:>{num_w}} "
            f"{row['not_prefiltered']:>{num_w}} "
            f"{row['signal_fired']:>{num_w}} "
            f"{row['passed_entry_filter']:>{num_w}} "
            f"{row['sized']:>{num_w}} "
            f"{row['accepted']:>{num_w}}"
        )
    print()
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/unit/test_funnel_report.py -v
```

Expected: all 3 tests PASS.

- [ ] **Step 5: Run full test suite**

```bash
pytest tests/unit/ -x -q
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/alpaca_bot/admin/funnel_report_cli.py tests/unit/test_funnel_report.py
git commit -m "feat: add alpaca-bot-funnel-report CLI (decision_log funnel analytics)"
```

---

## Task 5: Register entry point and verify installation

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Add entry point to `pyproject.toml`**

In `pyproject.toml`, inside `[project.scripts]`, add after `alpaca-bot-strategy-report`:

Old:
```toml
alpaca-bot-strategy-report = "alpaca_bot.admin.strategy_report_cli:main"
alpaca-bot-nightly = "alpaca_bot.nightly.cli:main"
```

New:
```toml
alpaca-bot-strategy-report = "alpaca_bot.admin.strategy_report_cli:main"
alpaca-bot-funnel-report = "alpaca_bot.admin.funnel_report_cli:main"
alpaca-bot-nightly = "alpaca_bot.nightly.cli:main"
```

- [ ] **Step 2: Reinstall the package**

```bash
pip install -e ".[dev]" -q
```

Expected: no errors, package reinstalled.

- [ ] **Step 3: Verify the CLI is registered**

```bash
alpaca-bot-funnel-report --help
```

Expected output (exact wording may vary):
```
usage: alpaca-bot-funnel-report [-h] [--days DAYS] [--start YYYY-MM-DD]
                                 [--end YYYY-MM-DD] [--mode {paper,live}]

Per-strategy signal funnel from decision_log
```

- [ ] **Step 4: Run full test suite one final time**

```bash
pytest tests/unit/ -q
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml
git commit -m "chore: register alpaca-bot-funnel-report entry point"
```

---

## Self-Review Checklist

**Spec coverage:**
- ✅ `annualized_sharpe` in `BacktestReport` — Task 1
- ✅ `_compute_annualized_sharpe()` daily-bucketed formula — Task 1
- ✅ `score_report()` prefers `annualized_sharpe` — Task 2
- ✅ `_aggregate_reports()` propagates `annualized_sharpe` — Task 2
- ✅ `DecisionLogStore.funnel_by_strategy()` — Task 3
- ✅ `alpaca-bot-funnel-report` CLI with `--days`/`--start`/`--end`/`--mode` — Task 4
- ✅ Entry point registration — Task 5

**No placeholders:** All steps contain complete code. No TBD or "similar to Task N".

**Type consistency:**
- `_compute_annualized_sharpe(trades: list[ReplayTradeRecord]) -> float | None` — defined in Task 1, called in same task
- `funnel_by_strategy(...) -> list[dict]` — defined in Task 3, used in Task 4
- Column names in `_cols` tuple match column aliases in SQL — verified
- `_FakeStore.funnel_by_strategy(**kwargs)` returns `list[dict]` matching real signature
