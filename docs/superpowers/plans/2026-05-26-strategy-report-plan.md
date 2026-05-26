# Multi-Period Strategy Report CLI — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `alpaca-bot-strategy-report` CLI that aggregates multi-day equity and option trade history by strategy, computes option premium retention, shows a daily P&L sparkline, and exports CSVs.

**Architecture:** Two new read-only SQL query methods on existing repository classes; a new CLI file with pure compute functions (testable without DB) and render functions; a new entrypoint registered in `pyproject.toml`. Follows the exact same patterns as `session_eval_cli.py`.

**Tech Stack:** Python 3.12, psycopg3 (via `connect_postgres`), `argparse`, `csv` stdlib, pytest.

---

## File Map

| Action  | File                                                          | Responsibility                                    |
|---------|---------------------------------------------------------------|---------------------------------------------------|
| Create  | `src/alpaca_bot/admin/strategy_report_cli.py`                 | Dataclasses, pure compute functions, render, main |
| Modify  | `src/alpaca_bot/storage/repositories.py` (after line 812)     | Add `OrderStore.list_closed_trade_records`         |
| Modify  | `src/alpaca_bot/storage/repositories.py` (after line 1946)    | Add `OptionOrderRepository.list_closed_option_trade_records` |
| Modify  | `pyproject.toml`                                              | Register `alpaca-bot-strategy-report` entrypoint  |
| Create  | `tests/unit/test_strategy_report.py`                          | Unit tests for pure compute functions             |

---

## Task 1: Pure compute functions (TDD)

**Files:**
- Create: `tests/unit/test_strategy_report.py`
- Create: `src/alpaca_bot/admin/strategy_report_cli.py` (dataclasses + compute functions only)

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_strategy_report.py`:

```python
from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from alpaca_bot.admin.strategy_report_cli import (
    EquityStrategyStats,
    OptionUnderlyingStats,
    compute_daily_pnl,
    compute_equity_stats,
    compute_option_stats,
)

_NOW = datetime(2026, 5, 26, 14, 0, tzinfo=timezone.utc)


def _eq(symbol: str, strategy: str, entry: float, exit_: float, qty: float, pnl: float, hold: float) -> dict:
    return {
        "symbol": symbol,
        "strategy_name": strategy,
        "qty": qty,
        "entry_price": entry,
        "exit_price": exit_,
        "entry_time": _NOW,
        "exit_time": _NOW,
        "pnl": pnl,
        "hold_seconds": hold,
    }


def _opt(underlying: str, strategy: str, qty: int, collected: float, cost: float, pnl: float) -> dict:
    return {
        "occ_symbol": f"{underlying}260618P00017500",
        "underlying": underlying,
        "strategy_name": strategy,
        "qty": qty,
        "premium_collected": collected,
        "close_cost": cost,
        "pnl": pnl,
        "opened_at": _NOW,
        "closed_at": _NOW,
    }


def test_compute_equity_stats_no_trades():
    assert compute_equity_stats([]) == []


def test_compute_equity_stats_single_strategy():
    records = [
        _eq("AAPL", "breakout", 100.0, 102.0, 10, 20.0, 1800),
        _eq("MSFT", "breakout", 200.0, 198.0, 5, -10.0, 900),
    ]
    stats = compute_equity_stats(records)
    assert len(stats) == 1
    s = stats[0]
    assert s.strategy_name == "breakout"
    assert s.trades == 2
    assert s.winning_trades == 1
    assert s.total_pnl == pytest.approx(10.0)
    assert s.avg_hold_minutes == pytest.approx(22.5)


def test_compute_equity_stats_multiple_strategies():
    records = [
        _eq("AAPL", "breakout", 100.0, 105.0, 10, 50.0, 3600),
        _eq("SPY",  "bear_orb",  400.0, 398.0, 5, -10.0, 600),
    ]
    stats = compute_equity_stats(records)
    assert len(stats) == 2
    names = {s.strategy_name for s in stats}
    assert names == {"breakout", "bear_orb"}


def test_compute_equity_stats_profit_factor():
    records = [
        _eq("A", "breakout", 10.0, 12.0, 10, 20.0, 60),
        _eq("B", "breakout", 10.0,  9.0, 10, -10.0, 60),
    ]
    stats = compute_equity_stats(records)
    s = stats[0]
    assert s.profit_factor == pytest.approx(2.0)


def test_compute_equity_stats_no_losses():
    records = [_eq("A", "breakout", 10.0, 12.0, 10, 20.0, 60)]
    stats = compute_equity_stats(records)
    assert stats[0].profit_factor is None  # no losses → undefined


def test_compute_option_stats_no_trades():
    assert compute_option_stats([]) == []


def test_compute_option_stats_retention_negative():
    records = [_opt("ALHC", "bear_orb", 5, 210.0, 890.0, -680.0)]
    stats = compute_option_stats(records)
    assert len(stats) == 1
    s = stats[0]
    assert s.underlying == "ALHC"
    assert s.strategy_name == "bear_orb"
    assert s.contracts == 5
    assert s.premium_collected == pytest.approx(210.0)
    assert s.close_cost == pytest.approx(890.0)
    assert s.net_pnl == pytest.approx(-680.0)
    assert s.retention_pct == pytest.approx(-680.0 / 210.0 * 100, abs=0.1)


def test_compute_option_stats_retention_positive():
    records = [_opt("XYZ", "bear_orb", 2, 100.0, 30.0, 70.0)]
    stats = compute_option_stats(records)
    assert stats[0].retention_pct == pytest.approx(70.0)


def test_compute_option_stats_groups_by_underlying_and_strategy():
    records = [
        _opt("ALHC", "bear_orb", 2, 100.0, 200.0, -100.0),
        _opt("AMLX", "bear_orb", 1,  50.0, 100.0,  -50.0),
        _opt("ALHC", "bear_orb", 1,  60.0, 120.0,  -60.0),
    ]
    stats = compute_option_stats(records)
    assert len(stats) == 2
    alhc = next(s for s in stats if s.underlying == "ALHC")
    assert alhc.contracts == 3
    assert alhc.premium_collected == pytest.approx(160.0)


def test_compute_daily_pnl_empty():
    result = compute_daily_pnl([], [], "America/New_York")
    assert result == {}


def test_compute_daily_pnl_groups_by_date():
    equity = [
        {"exit_time": datetime(2026, 5, 26, 14, 0, tzinfo=timezone.utc), "pnl": 100.0,
         "symbol": "AAPL", "strategy_name": "breakout", "qty": 1.0,
         "entry_price": 100.0, "exit_price": 101.0, "entry_time": _NOW, "hold_seconds": 60.0},
    ]
    option = [
        {"closed_at": datetime(2026, 5, 26, 18, 0, tzinfo=timezone.utc), "pnl": -50.0,
         "occ_symbol": "X260618P00017500", "underlying": "X", "strategy_name": "bear_orb",
         "qty": 1, "premium_collected": 100.0, "close_cost": 150.0, "opened_at": _NOW},
    ]
    daily = compute_daily_pnl(equity, option, "America/New_York")
    assert date(2026, 5, 26) in daily
    assert daily[date(2026, 5, 26)] == pytest.approx(50.0)


def test_compute_daily_pnl_two_days():
    day1 = datetime(2026, 5, 26, 14, 0, tzinfo=timezone.utc)
    day2 = datetime(2026, 5, 27, 14, 0, tzinfo=timezone.utc)
    equity = [
        {"exit_time": day1, "pnl": 10.0, "symbol": "A", "strategy_name": "b",
         "qty": 1.0, "entry_price": 10.0, "exit_price": 11.0, "entry_time": day1, "hold_seconds": 60.0},
        {"exit_time": day2, "pnl": -5.0, "symbol": "B", "strategy_name": "b",
         "qty": 1.0, "entry_price": 10.0, "exit_price": 9.5, "entry_time": day2, "hold_seconds": 60.0},
    ]
    daily = compute_daily_pnl(equity, [], "America/New_York")
    assert len(daily) == 2
    assert daily[date(2026, 5, 26)] == pytest.approx(10.0)
    assert daily[date(2026, 5, 27)] == pytest.approx(-5.0)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/unit/test_strategy_report.py -v 2>&1 | head -20
```

Expected: `ImportError: cannot import name 'EquityStrategyStats' from 'alpaca_bot.admin.strategy_report_cli'`

- [ ] **Step 3: Create `src/alpaca_bot/admin/strategy_report_cli.py` with dataclasses and compute functions**

```python
from __future__ import annotations

import argparse
import csv
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Sequence
from zoneinfo import ZoneInfo

from alpaca_bot.config import Settings, TradingMode
from alpaca_bot.storage.db import connect_postgres
from alpaca_bot.storage.repositories import (
    AuditEventStore,
    OptionOrderRepository,
    OrderStore,
)


@dataclass(frozen=True)
class EquityStrategyStats:
    strategy_name: str
    trades: int
    winning_trades: int
    total_pnl: float
    profit_factor: float | None
    expectancy_pct: float | None
    avg_hold_minutes: float | None


@dataclass(frozen=True)
class OptionUnderlyingStats:
    underlying: str
    strategy_name: str
    contracts: int
    premium_collected: float
    close_cost: float
    net_pnl: float
    retention_pct: float


def compute_equity_stats(records: list[dict]) -> list[EquityStrategyStats]:
    """Group equity trade records by strategy_name, compute per-strategy metrics."""
    by_strategy: dict[str, list[dict]] = defaultdict(list)
    for r in records:
        by_strategy[r["strategy_name"]].append(r)

    result = []
    for name, trades in sorted(by_strategy.items()):
        pnls = [t["pnl"] for t in trades]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]
        gross_wins = sum(wins)
        gross_losses = abs(sum(losses))
        profit_factor = gross_wins / gross_losses if gross_losses > 0 else None

        win_returns = [
            (t["exit_price"] - t["entry_price"]) / t["entry_price"]
            for t in trades if t["pnl"] > 0
        ]
        loss_returns = [
            (t["exit_price"] - t["entry_price"]) / t["entry_price"]
            for t in trades if t["pnl"] <= 0
        ]
        if trades:
            win_rate = len(wins) / len(trades)
            avg_win = sum(win_returns) / len(win_returns) if win_returns else 0.0
            avg_loss = sum(loss_returns) / len(loss_returns) if loss_returns else 0.0
            expectancy_pct = win_rate * avg_win + (1 - win_rate) * avg_loss
        else:
            expectancy_pct = None

        holds = [t["hold_seconds"] for t in trades if t.get("hold_seconds") is not None]
        avg_hold_minutes = sum(holds) / len(holds) / 60 if holds else None

        result.append(EquityStrategyStats(
            strategy_name=name,
            trades=len(trades),
            winning_trades=len(wins),
            total_pnl=sum(pnls),
            profit_factor=profit_factor,
            expectancy_pct=expectancy_pct,
            avg_hold_minutes=avg_hold_minutes,
        ))
    return result


def compute_option_stats(records: list[dict]) -> list[OptionUnderlyingStats]:
    """Group option trade records by (underlying, strategy_name), compute premium retention."""
    key_map: dict[tuple, list[dict]] = defaultdict(list)
    for r in records:
        key_map[(r["underlying"], r["strategy_name"])].append(r)

    result = []
    for (underlying, strategy), trades in sorted(key_map.items()):
        collected = sum(t["premium_collected"] for t in trades)
        cost = sum(t["close_cost"] for t in trades)
        net = sum(t["pnl"] for t in trades)
        retention = (net / collected * 100) if collected != 0 else 0.0
        result.append(OptionUnderlyingStats(
            underlying=underlying,
            strategy_name=strategy,
            contracts=sum(t["qty"] for t in trades),
            premium_collected=collected,
            close_cost=cost,
            net_pnl=net,
            retention_pct=retention,
        ))
    return result


def compute_daily_pnl(
    equity_records: list[dict],
    option_records: list[dict],
    market_timezone: str,
) -> dict[date, float]:
    """Sum net P&L (equity + option) by exit date in the given timezone."""
    tz = ZoneInfo(market_timezone)
    daily: dict[date, float] = defaultdict(float)
    for r in equity_records:
        d = r["exit_time"].astimezone(tz).date()
        daily[d] += r["pnl"]
    for r in option_records:
        d = r["closed_at"].astimezone(tz).date()
        daily[d] += r["pnl"]
    return dict(daily)


def main(argv: Sequence[str] | None = None) -> int:
    raise NotImplementedError("CLI main not yet implemented")
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/unit/test_strategy_report.py -v
```

Expected: all 12 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/unit/test_strategy_report.py src/alpaca_bot/admin/strategy_report_cli.py
git commit -m "feat: add compute functions for multi-period strategy report"
```

---

## Task 2: OrderStore.list_closed_trade_records

**Files:**
- Modify: `src/alpaca_bot/storage/repositories.py` (insert after line 812, inside `OrderStore`)

> **Context:** `OrderStore` ends around line 900. The method `list_trade_pnl_by_strategy` ends at line 812. Insert the new method immediately after it, before the `win_loss_counts_by_strategy` method. Look for `    def win_loss_counts_by_strategy` to find the insertion point.

- [ ] **Step 1: Add `list_closed_trade_records` to `OrderStore` in `repositories.py`**

Find the line `    def win_loss_counts_by_strategy(` (around line 814 in `repositories.py`) and insert the following method **before** it:

```python
    def list_closed_trade_records(
        self,
        *,
        trading_mode: TradingMode,
        strategy_version: str,
        since_date: date,
        until_date: date,
        market_timezone: str = "America/New_York",
    ) -> list[dict]:
        """Return one dict per closed equity round-trip in a date range.

        Anchors on exit orders; correlated subquery finds the most recent
        matching entry fill. Rows without a correlated entry are excluded.
        Each dict: symbol, strategy_name, qty, entry_price, exit_price,
                   entry_time, exit_time, pnl, hold_seconds.
        """
        rows = fetch_all(
            self._connection,
            """
            SELECT
                x.symbol,
                x.strategy_name,
                COALESCE(x.filled_quantity, x.quantity) AS qty,
                x.fill_price AS exit_fill,
                x.updated_at AS exit_time,
                (SELECT e.fill_price
                   FROM orders e
                  WHERE e.symbol = x.symbol
                    AND e.trading_mode = x.trading_mode
                    AND e.strategy_version = x.strategy_version
                    AND e.strategy_name IS NOT DISTINCT FROM x.strategy_name
                    AND e.intent_type = 'entry'
                    AND e.fill_price IS NOT NULL
                    AND e.status = 'filled'
                    AND e.updated_at <= x.updated_at
                  ORDER BY e.updated_at DESC LIMIT 1) AS entry_fill,
                (SELECT e.updated_at
                   FROM orders e
                  WHERE e.symbol = x.symbol
                    AND e.trading_mode = x.trading_mode
                    AND e.strategy_version = x.strategy_version
                    AND e.strategy_name IS NOT DISTINCT FROM x.strategy_name
                    AND e.intent_type = 'entry'
                    AND e.fill_price IS NOT NULL
                    AND e.status = 'filled'
                    AND e.updated_at <= x.updated_at
                  ORDER BY e.updated_at DESC LIMIT 1) AS entry_time
            FROM orders x
            WHERE x.trading_mode = %s
              AND x.strategy_version = %s
              AND x.intent_type IN ('stop', 'exit')
              AND x.fill_price IS NOT NULL
              AND x.status = 'filled'
              AND DATE(x.updated_at AT TIME ZONE %s) >= %s
              AND DATE(x.updated_at AT TIME ZONE %s) <= %s
            ORDER BY x.updated_at
            """,
            (
                trading_mode.value,
                strategy_version,
                market_timezone,
                since_date,
                market_timezone,
                until_date,
            ),
        )
        result = []
        for row in rows:
            if row[5] is None:
                continue
            qty = float(row[2])
            exit_fill = float(row[3])
            exit_time = row[4]
            entry_fill = float(row[5])
            entry_time = row[6]
            hold_seconds = (exit_time - entry_time).total_seconds() if entry_time else 0.0
            result.append({
                "symbol": row[0],
                "strategy_name": row[1],
                "qty": qty,
                "entry_price": entry_fill,
                "exit_price": exit_fill,
                "entry_time": entry_time,
                "exit_time": exit_time,
                "pnl": (exit_fill - entry_fill) * qty,
                "hold_seconds": hold_seconds,
            })
        return result

```

- [ ] **Step 2: Run existing tests to confirm no regressions**

```bash
pytest tests/unit/ -x -q 2>&1 | tail -5
```

Expected: all tests PASS (new method is additive, no existing tests broken).

- [ ] **Step 3: Commit**

```bash
git add src/alpaca_bot/storage/repositories.py
git commit -m "feat: add OrderStore.list_closed_trade_records for date-range query"
```

---

## Task 3: OptionOrderRepository.list_closed_option_trade_records

**Files:**
- Modify: `src/alpaca_bot/storage/repositories.py` (insert after line 1946, inside `OptionOrderRepository`)

> **Context:** `OptionOrderRepository.list_trade_pnl_by_strategy` ends around line 1946. Insert the new method immediately after it, before `load_by_broker_order_id`. Look for `    def load_by_broker_order_id` to find the insertion point.

- [ ] **Step 1: Add `list_closed_option_trade_records` to `OptionOrderRepository` in `repositories.py`**

Find the line `    def load_by_broker_order_id(` (around line 1948) and insert the following method **before** it:

```python
    def list_closed_option_trade_records(
        self,
        *,
        trading_mode: TradingMode,
        strategy_version: str,
        since_date: date,
        until_date: date,
        market_timezone: str = "America/New_York",
    ) -> list[dict]:
        """Return one dict per closed option round-trip in a date range.

        Anchors on buy-to-close orders; correlated subquery finds the matching
        sell fill (initial short sale). Rows without a correlated sell are excluded.
        Each dict: occ_symbol, underlying, strategy_name, qty,
                   premium_collected, close_cost, pnl, opened_at, closed_at.
        pnl = (sell_fill - buy_fill) * qty * 100
        """
        rows = fetch_all(
            self._connection,
            """
            SELECT x.occ_symbol,
                   x.underlying_symbol,
                   x.strategy_name,
                   COALESCE(x.filled_quantity, x.quantity) AS qty,
                   x.fill_price AS buy_fill,
                   x.updated_at AS closed_at,
                   (SELECT s.fill_price
                      FROM option_orders s
                     WHERE s.occ_symbol = x.occ_symbol
                       AND s.trading_mode = x.trading_mode
                       AND s.strategy_version = x.strategy_version
                       AND s.strategy_name IS NOT DISTINCT FROM x.strategy_name
                       AND s.side = 'sell'
                       AND s.fill_price IS NOT NULL
                       AND s.status = 'filled'
                       AND s.updated_at <= x.updated_at
                     ORDER BY s.updated_at DESC
                     LIMIT 1) AS sell_fill,
                   (SELECT s.updated_at
                      FROM option_orders s
                     WHERE s.occ_symbol = x.occ_symbol
                       AND s.trading_mode = x.trading_mode
                       AND s.strategy_version = x.strategy_version
                       AND s.strategy_name IS NOT DISTINCT FROM x.strategy_name
                       AND s.side = 'sell'
                       AND s.fill_price IS NOT NULL
                       AND s.status = 'filled'
                       AND s.updated_at <= x.updated_at
                     ORDER BY s.updated_at DESC
                     LIMIT 1) AS opened_at
              FROM option_orders x
             WHERE x.trading_mode = %s
               AND x.strategy_version = %s
               AND x.side = 'buy'
               AND x.fill_price IS NOT NULL
               AND x.status = 'filled'
               AND DATE(x.updated_at AT TIME ZONE %s) >= %s
               AND DATE(x.updated_at AT TIME ZONE %s) <= %s
             ORDER BY x.updated_at
            """,
            (
                trading_mode.value,
                strategy_version,
                market_timezone,
                since_date,
                market_timezone,
                until_date,
            ),
        )
        result = []
        for row in rows:
            if row[6] is None:
                continue
            qty = int(row[3])
            buy_fill = float(row[4])
            closed_at = row[5]
            sell_fill = float(row[6])
            opened_at = row[7]
            premium_collected = sell_fill * qty * 100
            close_cost = buy_fill * qty * 100
            result.append({
                "occ_symbol": row[0],
                "underlying": row[1],
                "strategy_name": row[2],
                "qty": qty,
                "premium_collected": premium_collected,
                "close_cost": close_cost,
                "pnl": premium_collected - close_cost,
                "opened_at": opened_at,
                "closed_at": closed_at,
            })
        return result

```

- [ ] **Step 2: Run existing tests to confirm no regressions**

```bash
pytest tests/unit/ -x -q 2>&1 | tail -5
```

Expected: all tests PASS.

- [ ] **Step 3: Commit**

```bash
git add src/alpaca_bot/storage/repositories.py
git commit -m "feat: add OptionOrderRepository.list_closed_option_trade_records"
```

---

## Task 4: Render functions and CLI main

**Files:**
- Modify: `src/alpaca_bot/admin/strategy_report_cli.py` (replace the `main` stub + add all render functions)

- [ ] **Step 1: Replace the stub `main` and add render functions in `strategy_report_cli.py`**

Replace everything from (and including) the `_SPARK_CHARS` line and the stub `main` function at the bottom of the file. The file currently ends with `def main(...): raise NotImplementedError(...)`. Replace that stub with the complete implementation below.

**Full final content of `src/alpaca_bot/admin/strategy_report_cli.py`:**

```python
from __future__ import annotations

import argparse
import csv
import os
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Sequence
from zoneinfo import ZoneInfo

from alpaca_bot.config import Settings, TradingMode
from alpaca_bot.storage.db import connect_postgres
from alpaca_bot.storage.repositories import (
    AuditEventStore,
    OptionOrderRepository,
    OrderStore,
)


@dataclass(frozen=True)
class EquityStrategyStats:
    strategy_name: str
    trades: int
    winning_trades: int
    total_pnl: float
    profit_factor: float | None
    expectancy_pct: float | None
    avg_hold_minutes: float | None


@dataclass(frozen=True)
class OptionUnderlyingStats:
    underlying: str
    strategy_name: str
    contracts: int
    premium_collected: float
    close_cost: float
    net_pnl: float
    retention_pct: float


def compute_equity_stats(records: list[dict]) -> list[EquityStrategyStats]:
    """Group equity trade records by strategy_name, compute per-strategy metrics."""
    by_strategy: dict[str, list[dict]] = defaultdict(list)
    for r in records:
        by_strategy[r["strategy_name"]].append(r)

    result = []
    for name, trades in sorted(by_strategy.items()):
        pnls = [t["pnl"] for t in trades]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]
        gross_wins = sum(wins)
        gross_losses = abs(sum(losses))
        profit_factor = gross_wins / gross_losses if gross_losses > 0 else None

        win_returns = [
            (t["exit_price"] - t["entry_price"]) / t["entry_price"]
            for t in trades if t["pnl"] > 0
        ]
        loss_returns = [
            (t["exit_price"] - t["entry_price"]) / t["entry_price"]
            for t in trades if t["pnl"] <= 0
        ]
        if trades:
            win_rate = len(wins) / len(trades)
            avg_win = sum(win_returns) / len(win_returns) if win_returns else 0.0
            avg_loss = sum(loss_returns) / len(loss_returns) if loss_returns else 0.0
            expectancy_pct = win_rate * avg_win + (1 - win_rate) * avg_loss
        else:
            expectancy_pct = None

        holds = [t["hold_seconds"] for t in trades if t.get("hold_seconds") is not None]
        avg_hold_minutes = sum(holds) / len(holds) / 60 if holds else None

        result.append(EquityStrategyStats(
            strategy_name=name,
            trades=len(trades),
            winning_trades=len(wins),
            total_pnl=sum(pnls),
            profit_factor=profit_factor,
            expectancy_pct=expectancy_pct,
            avg_hold_minutes=avg_hold_minutes,
        ))
    return result


def compute_option_stats(records: list[dict]) -> list[OptionUnderlyingStats]:
    """Group option trade records by (underlying, strategy_name), compute premium retention."""
    key_map: dict[tuple, list[dict]] = defaultdict(list)
    for r in records:
        key_map[(r["underlying"], r["strategy_name"])].append(r)

    result = []
    for (underlying, strategy), trades in sorted(key_map.items()):
        collected = sum(t["premium_collected"] for t in trades)
        cost = sum(t["close_cost"] for t in trades)
        net = sum(t["pnl"] for t in trades)
        retention = (net / collected * 100) if collected != 0 else 0.0
        result.append(OptionUnderlyingStats(
            underlying=underlying,
            strategy_name=strategy,
            contracts=sum(t["qty"] for t in trades),
            premium_collected=collected,
            close_cost=cost,
            net_pnl=net,
            retention_pct=retention,
        ))
    return result


def compute_daily_pnl(
    equity_records: list[dict],
    option_records: list[dict],
    market_timezone: str,
) -> dict[date, float]:
    """Sum net P&L (equity + option) by exit date in the given timezone."""
    tz = ZoneInfo(market_timezone)
    daily: dict[date, float] = defaultdict(float)
    for r in equity_records:
        d = r["exit_time"].astimezone(tz).date()
        daily[d] += r["pnl"]
    for r in option_records:
        d = r["closed_at"].astimezone(tz).date()
        daily[d] += r["pnl"]
    return dict(daily)


_SPARK_CHARS = "▁▂▃▄▅▆▇█"


def _sparkline(values: list[float]) -> str:
    if not values:
        return ""
    lo, hi = min(values), max(values)
    rng = hi - lo
    if rng == 0:
        return _SPARK_CHARS[3] * len(values)
    return "".join(_SPARK_CHARS[int((v - lo) / rng * 7)] for v in values)


def _fmt_pnl(v: float) -> str:
    return f"+${v:,.2f}" if v >= 0 else f"-${abs(v):,.2f}"


def _render_header(
    since_date: date,
    until_date: date,
    trading_mode: str,
    strategy_version: str,
    equity_count: int,
    option_count: int,
    total_pnl: float,
) -> None:
    header = f"Strategy Report — {since_date} to {until_date}  [{trading_mode} / {strategy_version}]"
    print()
    print(header)
    print("═" * len(header))
    days = (until_date - since_date).days + 1
    pnl_str = _fmt_pnl(total_pnl)
    print(
        f" Period: {days} days   "
        f"Equity trades: {equity_count}   "
        f"Option contracts: {option_count}   "
        f"Total P&L: {pnl_str}"
    )


def _render_equity_table(stats: list[EquityStrategyStats]) -> None:
    print()
    print(" Equity Strategies")
    print(" " + "─" * 70)
    if not stats:
        print(" (no closed equity trades in period)")
        return
    print(f" {'Strategy':<20} {'Trades':>6}  {'Win%':>5}  {'P&L':>10}  {'PF':>5}  {'Expect%':>8}  {'AvgHold':>8}")
    print(f" {'-'*20} {'-'*6}  {'-'*5}  {'-'*10}  {'-'*5}  {'-'*8}  {'-'*8}")
    for s in stats:
        win_pct = f"{s.winning_trades / s.trades:.0%}" if s.trades else "—"
        pnl_str = _fmt_pnl(s.total_pnl)
        pf_str = f"{s.profit_factor:.2f}" if s.profit_factor is not None else "—"
        exp_str = (
            (f"+{s.expectancy_pct:.2%}" if s.expectancy_pct >= 0 else f"{s.expectancy_pct:.2%}")
            if s.expectancy_pct is not None else "—"
        )
        hold_str = f"{s.avg_hold_minutes:.0f}min" if s.avg_hold_minutes is not None else "—"
        print(f" {s.strategy_name:<20} {s.trades:>6}  {win_pct:>5}  {pnl_str:>10}  {pf_str:>5}  {exp_str:>8}  {hold_str:>8}")


def _render_option_table(stats: list[OptionUnderlyingStats]) -> None:
    print()
    print(" Option Premium")
    print(" " + "─" * 78)
    if not stats:
        print(" (no closed option positions in period)")
        return
    print(f" {'Underlying':<12} {'Strategy':<16} {'Cts':>4}  {'Collected':>10}  {'CloseCost':>10}  {'Net P&L':>10}  {'Retain%':>8}")
    print(f" {'-'*12} {'-'*16} {'-'*4}  {'-'*10}  {'-'*10}  {'-'*10}  {'-'*8}")
    for s in stats:
        ret_str = f"{s.retention_pct:+.0f}%"
        print(
            f" {s.underlying:<12} {s.strategy_name:<16} {s.contracts:>4}  "
            f"{_fmt_pnl(s.premium_collected):>10}  "
            f"${s.close_cost:>9,.2f}  "
            f"{_fmt_pnl(s.net_pnl):>10}  "
            f"{ret_str:>8}"
        )
    total_collected = sum(s.premium_collected for s in stats)
    total_cost = sum(s.close_cost for s in stats)
    total_net = sum(s.net_pnl for s in stats)
    total_ret = (total_net / total_collected * 100) if total_collected != 0 else 0.0
    print(f" {'TOTAL':<12} {'':<16} {sum(s.contracts for s in stats):>4}  "
          f"{_fmt_pnl(total_collected):>10}  "
          f"${total_cost:>9,.2f}  "
          f"{_fmt_pnl(total_net):>10}  "
          f"{total_ret:+.0f}%")


def _render_sparkline(daily_pnl: dict[date, float], since_date: date, until_date: date) -> None:
    print()
    print(" Daily P&L (last 14 trading days)")
    print(" " + "─" * 40)
    all_dates = sorted(daily_pnl.keys())
    last14 = all_dates[-14:] if len(all_dates) > 14 else all_dates
    if not last14:
        print(" (no data)")
        return
    values = [daily_pnl[d] for d in last14]
    spark = _sparkline(values)
    lo, hi = min(values), max(values)
    print(f" {spark}  [range: {_fmt_pnl(lo)} to {_fmt_pnl(hi)}]")
    if len(last14) >= 2:
        print(f" {last14[0].strftime('%m/%d'):<20} {last14[-1].strftime('%m/%d'):>20}")


def _render_operational_health(
    audit_store: AuditEventStore,
    since_dt: datetime,
    until_dt: datetime,
) -> None:
    print()
    print(" Operational Health (period)")
    print(" " + "─" * 40)

    cycles = audit_store.list_by_event_types(
        event_types=["supervisor_cycle"],
        since=since_dt,
        until=until_dt,
        limit=10000,
    )
    errors = audit_store.list_by_event_types(
        event_types=["supervisor_cycle_error", "strategy_cycle_error"],
        since=since_dt,
        until=until_dt,
        limit=10000,
    )
    dispatch_failures = audit_store.list_by_event_types(
        event_types=["order_dispatch_failed", "option_order_dispatch_failed"],
        since=since_dt,
        until=until_dt,
        limit=10000,
    )
    skipped_events = audit_store.list_by_event_types(
        event_types=["cycle_intent_skipped"],
        since=since_dt,
        until=until_dt,
        limit=10000,
    )
    options_skipped = sum(
        1 for e in skipped_events
        if e.payload.get("reason") == "options_market_closed"
    )
    stale_events = audit_store.list_by_event_types(
        event_types=["stale_positions_detected"],
        since=since_dt,
        until=until_dt,
        limit=10000,
    )
    stale_skipped = sum(
        1 for e in stale_events
        if e.payload.get("skipped_exit_option_count", 0) > 0
    )

    print(f" Total cycles:       {len(cycles):>6}")
    print(f" Cycle errors:       {len(errors):>6}")
    print(f" Dispatch failures:  {len(dispatch_failures):>6}")
    print(f" Skipped exits (OCC):{options_skipped:>6}     ← options-market-closed guard")
    print(f" Stale exits skipped:{stale_skipped:>6}     ← stale OCC positions")
    print()


def _export_csv(
    equity_records: list[dict],
    option_records: list[dict],
    daily_pnl: dict[date, float],
    csv_dir: str,
) -> None:
    os.makedirs(csv_dir, exist_ok=True)

    equity_path = os.path.join(csv_dir, "equity_trades.csv")
    equity_fields = ["symbol", "strategy_name", "qty", "entry_price", "exit_price",
                     "entry_time", "exit_time", "pnl", "hold_seconds"]
    with open(equity_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=equity_fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(equity_records)
    print(f" Wrote {len(equity_records)} rows → {equity_path}")

    option_path = os.path.join(csv_dir, "option_trades.csv")
    option_fields = ["occ_symbol", "underlying", "strategy_name", "qty",
                     "premium_collected", "close_cost", "pnl", "opened_at", "closed_at"]
    with open(option_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=option_fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(option_records)
    print(f" Wrote {len(option_records)} rows → {option_path}")

    daily_path = os.path.join(csv_dir, "daily_pnl.csv")
    with open(daily_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date", "net_pnl"])
        for d in sorted(daily_pnl.keys()):
            w.writerow([d.isoformat(), f"{daily_pnl[d]:.2f}"])
    print(f" Wrote {len(daily_pnl)} rows → {daily_path}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="alpaca-bot-strategy-report",
        description="Multi-period strategy performance report from Postgres data",
    )
    parser.add_argument("--days", type=int, default=30, metavar="N",
                        help="Number of calendar days to look back (default: 30)")
    parser.add_argument("--since", metavar="YYYY-MM-DD",
                        help="Start date (overrides --days)")
    parser.add_argument("--until", metavar="YYYY-MM-DD",
                        help="End date inclusive (default: today ET)")
    parser.add_argument("--mode", default="paper", choices=["paper", "live"],
                        help="Trading mode (default: paper)")
    parser.add_argument("--strategy-version", metavar="VERSION",
                        help="Strategy version (default: STRATEGY_VERSION env var)")
    parser.add_argument("--csv-dir", metavar="PATH",
                        help="Export equity_trades.csv, option_trades.csv, daily_pnl.csv here")
    args = parser.parse_args(list(argv) if argv is not None else sys.argv[1:])

    settings = Settings.from_env()
    tz = settings.market_timezone
    today_et = datetime.now(tz).date()
    until_date = date.fromisoformat(args.until) if args.until else today_et
    since_date = (
        date.fromisoformat(args.since)
        if args.since
        else until_date - timedelta(days=args.days - 1)
    )
    trading_mode = TradingMode(args.mode)
    strategy_version = args.strategy_version or settings.strategy_version
    market_timezone = settings.market_timezone.key

    since_dt = datetime.combine(since_date, time.min, tzinfo=tz).astimezone(timezone.utc)
    until_dt = datetime.combine(until_date + timedelta(days=1), time.min, tzinfo=tz).astimezone(timezone.utc)

    conn = connect_postgres(settings.database_url)
    try:
        order_store = OrderStore(conn)
        option_repo = OptionOrderRepository(conn)
        audit_store = AuditEventStore(conn)

        equity_records = order_store.list_closed_trade_records(
            trading_mode=trading_mode,
            strategy_version=strategy_version,
            since_date=since_date,
            until_date=until_date,
            market_timezone=market_timezone,
        )
        option_records = option_repo.list_closed_option_trade_records(
            trading_mode=trading_mode,
            strategy_version=strategy_version,
            since_date=since_date,
            until_date=until_date,
            market_timezone=market_timezone,
        )

        equity_stats = compute_equity_stats(equity_records)
        option_stats = compute_option_stats(option_records)
        daily_pnl = compute_daily_pnl(equity_records, option_records, market_timezone)

        total_pnl = (
            sum(r["pnl"] for r in equity_records)
            + sum(r["pnl"] for r in option_records)
        )
        total_option_contracts = sum(r["qty"] for r in option_records)

        _render_header(
            since_date=since_date,
            until_date=until_date,
            trading_mode=args.mode,
            strategy_version=strategy_version,
            equity_count=len(equity_records),
            option_count=total_option_contracts,
            total_pnl=total_pnl,
        )
        _render_equity_table(equity_stats)
        _render_option_table(option_stats)
        _render_sparkline(daily_pnl, since_date, until_date)
        _render_operational_health(audit_store, since_dt, until_dt)

        if args.csv_dir:
            _export_csv(equity_records, option_records, daily_pnl, args.csv_dir)

    finally:
        close = getattr(conn, "close", None)
        if callable(close):
            close()

    return 0
```

- [ ] **Step 2: Run tests to verify no regressions**

```bash
pytest tests/unit/test_strategy_report.py tests/unit/ -x -q 2>&1 | tail -5
```

Expected: all tests PASS (compute function tests still green; render functions not unit-tested since they are display-only).

- [ ] **Step 3: Commit**

```bash
git add src/alpaca_bot/admin/strategy_report_cli.py
git commit -m "feat: add render functions and CLI main for strategy report"
```

---

## Task 5: Register entrypoint and verify installation

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Add entrypoint to `pyproject.toml`**

In `pyproject.toml`, find the `[project.scripts]` section and add one line after the `alpaca-bot-session-eval` entry:

```toml
alpaca-bot-strategy-report = "alpaca_bot.admin.strategy_report_cli:main"
```

The `[project.scripts]` section should look like this after the change:

```toml
[project.scripts]
alpaca-bot-admin = "alpaca_bot.admin.cli:main"
alpaca-bot-migrate = "alpaca_bot.storage.migrations:main"
alpaca-bot-ops-check = "alpaca_bot.admin.ops_check:main"
alpaca-bot-sync-credentials = "alpaca_bot.admin.credential_sync:main"
alpaca-bot-supervisor = "alpaca_bot.runtime.supervisor_cli:main"
alpaca-bot-trader = "alpaca_bot.runtime.cli:main"
alpaca-bot-web = "alpaca_bot.web.cli:main"
alpaca-bot-web-hash-password = "alpaca_bot.web.password_cli:main"
alpaca-bot-web-rotate-password = "alpaca_bot.web.password_rotate_cli:main"
alpaca-bot-backtest = "alpaca_bot.replay.cli:main"
alpaca-bot-evolve = "alpaca_bot.tuning.cli:main"
alpaca-bot-backfill = "alpaca_bot.backfill.cli:main"
alpaca-bot-sweep    = "alpaca_bot.tuning.sweep_cli:main"
alpaca-bot-session-eval = "alpaca_bot.admin.session_eval_cli:main"
alpaca-bot-strategy-report = "alpaca_bot.admin.strategy_report_cli:main"
alpaca-bot-nightly = "alpaca_bot.nightly.cli:main"
alpaca-bot-premarket = "alpaca_bot.nightly.premarket_cli:main"
```

- [ ] **Step 2: Reinstall the package**

```bash
pip install -e ".[dev]" -q
```

Expected: installs cleanly. No errors.

- [ ] **Step 3: Verify the CLI is registered**

```bash
alpaca-bot-strategy-report --help
```

Expected output:
```
usage: alpaca-bot-strategy-report [-h] [--days N] [--since YYYY-MM-DD]
                                   [--until YYYY-MM-DD] [--mode {paper,live}]
                                   [--strategy-version VERSION] [--csv-dir PATH]

Multi-period strategy performance report from Postgres data
...
```

- [ ] **Step 4: Run full test suite**

```bash
pytest tests/unit/ -q 2>&1 | tail -5
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml
git commit -m "feat: register alpaca-bot-strategy-report entrypoint"
```

---

## Self-Review

### Spec coverage check

| Spec requirement | Covered by task |
|---|---|
| `--days`, `--since`, `--until`, `--mode`, `--strategy-version`, `--csv-dir` flags | Task 4 (main) |
| Header section with period, counts, total P&L | Task 4 (_render_header) |
| Per-strategy equity table: trades, win%, P&L, PF, expect, avg hold | Task 1 + Task 4 |
| Option premium table: underlying, strategy, contracts, collected, cost, net, retention | Task 1 + Task 4 |
| Daily P&L sparkline (last 14 days) | Task 4 (_render_sparkline) |
| Operational health counts | Task 4 (_render_operational_health) |
| CSV export (equity_trades.csv, option_trades.csv, daily_pnl.csv) | Task 4 (_export_csv) |
| `OrderStore.list_closed_trade_records` | Task 2 |
| `OptionOrderRepository.list_closed_option_trade_records` | Task 3 |
| Registered entrypoint | Task 5 |

### Placeholder scan

No TBDs, no "implement later", no stubs. Every step contains complete runnable code.

### Type consistency

- `EquityStrategyStats` defined in Task 1 Step 3, used in Task 4 Step 1 render — consistent.
- `OptionUnderlyingStats` defined in Task 1 Step 3, used in Task 4 Step 1 render — consistent.
- `list_closed_trade_records` added in Task 2, called in Task 4 main — consistent signature.
- `list_closed_option_trade_records` added in Task 3, called in Task 4 main — consistent signature.
- Dict keys from repo methods (`exit_time`, `closed_at`, `pnl`, etc.) match usage in compute functions — consistent.
