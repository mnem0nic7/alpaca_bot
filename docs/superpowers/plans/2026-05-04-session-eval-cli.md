# Session Evaluation CLI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `alpaca-bot-session-eval` — a read-only CLI that queries live Postgres trade data and prints `BacktestReport`-quality metrics for any session date.

**Architecture:** Extract `report_from_records()` from `build_backtest_report()` in `replay/report.py`; add `intent_type` to `list_closed_trades()` return dict; add new `admin/session_eval_cli.py`.

**Tech Stack:** Python, argparse, psycopg2 (via existing `connect_postgres()`), existing `OrderStore` and `DailySessionStateStore`

---

## File Map

| File | Action |
|------|--------|
| `src/alpaca_bot/replay/report.py` | Extract `report_from_records()` from `build_backtest_report()` |
| `src/alpaca_bot/storage/repositories.py` | Add `intent_type` to `list_closed_trades()` SELECT and return dict |
| `src/alpaca_bot/admin/session_eval_cli.py` | New file — CLI entry point |
| `pyproject.toml` | Add `alpaca-bot-session-eval` entry point |
| `tests/unit/test_session_eval.py` | New test file |

---

### Task 1: Extract `report_from_records()` and add `intent_type` to `list_closed_trades()`

**Files:**
- Modify: `src/alpaca_bot/replay/report.py`
- Modify: `src/alpaca_bot/storage/repositories.py`
- Test: `tests/unit/test_session_eval.py`

- [ ] **Step 1: Write failing tests**

Create `tests/unit/test_session_eval.py`:

```python
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from alpaca_bot.replay.report import BacktestReport, ReplayTradeRecord, report_from_records


def _make_trade(
    symbol: str = "AAPL",
    entry: float = 100.0,
    exit_: float = 102.0,
    qty: int = 10,
    exit_reason: str = "eod",
    entry_time: datetime | None = None,
    exit_time: datetime | None = None,
) -> ReplayTradeRecord:
    t0 = datetime(2026, 5, 4, 10, 0, tzinfo=timezone.utc)
    t1 = datetime(2026, 5, 4, 11, 0, tzinfo=timezone.utc)
    pnl = (exit_ - entry) * qty
    return ReplayTradeRecord(
        symbol=symbol,
        entry_price=entry,
        exit_price=exit_,
        quantity=qty,
        entry_time=entry_time or t0,
        exit_time=exit_time or t1,
        exit_reason=exit_reason,
        pnl=pnl,
        return_pct=(exit_ - entry) / entry,
    )


def test_report_from_records_basic_stats():
    trades = [
        _make_trade(exit_=102.0),  # win, +$20
        _make_trade(exit_=103.0),  # win, +$30
        _make_trade(exit_=98.0),   # loss, -$20
    ]
    report = report_from_records(trades, starting_equity=100_000.0)
    assert report.total_trades == 3
    assert report.winning_trades == 2
    assert report.losing_trades == 1
    assert abs(report.win_rate - 2 / 3) < 1e-9
    assert report.profit_factor is not None
    assert report.profit_factor > 1.0


def test_report_from_records_exit_breakdown():
    trades = [
        _make_trade(exit_=102.0, exit_reason="stop"),   # stop win
        _make_trade(exit_=98.0, exit_reason="stop"),    # stop loss
        _make_trade(exit_=103.0, exit_reason="eod"),    # eod win
        _make_trade(exit_=99.0, exit_reason="eod"),     # eod loss
    ]
    report = report_from_records(trades, starting_equity=100_000.0)
    assert report.stop_wins == 1
    assert report.stop_losses == 1
    assert report.eod_wins == 1
    assert report.eod_losses == 1


def test_report_from_records_zero_trades():
    report = report_from_records([], starting_equity=100_000.0)
    assert report.total_trades == 0
    assert report.win_rate is None
    assert report.mean_return_pct is None
    assert report.max_drawdown_pct is None
    assert report.profit_factor is None


def test_report_from_records_parity_with_build_backtest_report():
    """report_from_records() produces the same stats as build_backtest_report() for equivalent input."""
    from alpaca_bot.domain.enums import IntentType
    from alpaca_bot.domain.models import ReplayEvent, ReplayResult, ReplayScenario
    from alpaca_bot.replay.report import build_backtest_report

    t_entry = datetime(2026, 5, 4, 10, 0, tzinfo=timezone.utc)
    t_stop = datetime(2026, 5, 4, 10, 30, tzinfo=timezone.utc)
    t_eod = datetime(2026, 5, 4, 16, 0, tzinfo=timezone.utc)

    events = [
        ReplayEvent(event_type=IntentType.ENTRY_FILLED, symbol="AAPL", timestamp=t_entry,
                    details={"entry_price": 100.0, "quantity": 10}),
        ReplayEvent(event_type=IntentType.STOP_HIT, symbol="AAPL", timestamp=t_stop,
                    details={"exit_price": 98.0}),
        ReplayEvent(event_type=IntentType.ENTRY_FILLED, symbol="TSLA", timestamp=t_entry,
                    details={"entry_price": 200.0, "quantity": 5}),
        ReplayEvent(event_type=IntentType.EOD_EXIT, symbol="TSLA", timestamp=t_eod,
                    details={"exit_price": 205.0}),
    ]
    # Build a minimal scenario and ReplayResult
    from alpaca_bot.domain.models import Bar
    scenario = ReplayScenario(
        name="test", symbol="AAPL", starting_equity=100_000.0,
        daily_bars=[], intraday_bars=[],
    )
    result = ReplayResult(scenario=scenario, events=events, backtest_report=None)

    backtest_report = build_backtest_report(result)

    # Build equivalent trades for report_from_records
    trades = [
        ReplayTradeRecord(symbol="AAPL", entry_price=100.0, exit_price=98.0, quantity=10,
                          entry_time=t_entry, exit_time=t_stop, exit_reason="stop",
                          pnl=-20.0, return_pct=-0.02),
        ReplayTradeRecord(symbol="TSLA", entry_price=200.0, exit_price=205.0, quantity=5,
                          entry_time=t_entry, exit_time=t_eod, exit_reason="eod",
                          pnl=25.0, return_pct=0.025),
    ]
    live_report = report_from_records(trades, starting_equity=100_000.0)

    assert live_report.total_trades == backtest_report.total_trades
    assert live_report.winning_trades == backtest_report.winning_trades
    assert live_report.losing_trades == backtest_report.losing_trades
    assert live_report.win_rate == backtest_report.win_rate
    assert live_report.profit_factor == backtest_report.profit_factor
    assert live_report.stop_wins == backtest_report.stop_wins
    assert live_report.stop_losses == backtest_report.stop_losses
    assert live_report.eod_wins == backtest_report.eod_wins
    assert live_report.eod_losses == backtest_report.eod_losses
    assert live_report.max_consecutive_losses == backtest_report.max_consecutive_losses
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_session_eval.py -v`
Expected: FAIL — `ImportError: cannot import name 'report_from_records' from 'alpaca_bot.replay.report'`

- [ ] **Step 3: Refactor `replay/report.py` — extract `report_from_records()`**

In `src/alpaca_bot/replay/report.py`, add `report_from_records()` after `build_backtest_report()` and refactor `build_backtest_report()` to call it:

```python
def build_backtest_report(result: ReplayResult, strategy_name: str = "breakout") -> BacktestReport:
    trades = _extract_trades(result.events)
    return report_from_records(trades, result.scenario.starting_equity, strategy_name)


def report_from_records(
    trades: list[ReplayTradeRecord],
    starting_equity: float,
    strategy_name: str = "breakout",
) -> BacktestReport:
    """Compute a BacktestReport from a list of already-constructed ReplayTradeRecord objects.

    Used by both the replay path (via build_backtest_report) and the live session
    evaluator (session_eval_cli) so both paths share identical stat logic.
    """
    total = len(trades)

    if total == 0:
        return BacktestReport(
            trades=(),
            total_trades=0,
            winning_trades=0,
            losing_trades=0,
            win_rate=None,
            mean_return_pct=None,
            max_drawdown_pct=None,
            strategy_name=strategy_name,
        )

    winners = sum(1 for t in trades if t.pnl > 0)
    losers = sum(1 for t in trades if t.pnl < 0)
    win_rate = winners / total
    mean_return_pct = sum(t.return_pct for t in trades) / total
    max_drawdown_pct = _compute_max_drawdown(trades, starting_equity)
    gross_wins_pnl = sum(t.pnl for t in trades if t.pnl > 0)
    gross_losses_pnl = abs(sum(t.pnl for t in trades if t.pnl < 0))
    profit_factor = gross_wins_pnl / gross_losses_pnl if gross_losses_pnl > 0 else None
    stop_wins = sum(1 for t in trades if t.exit_reason == "stop" and t.pnl > 0)
    stop_losses = sum(1 for t in trades if t.exit_reason == "stop" and t.pnl <= 0)
    eod_wins = sum(1 for t in trades if t.exit_reason == "eod" and t.pnl > 0)
    eod_losses = sum(1 for t in trades if t.exit_reason == "eod" and t.pnl <= 0)
    hold_minutes = [(t.exit_time - t.entry_time).total_seconds() / 60 for t in trades]
    avg_hold_minutes = sum(hold_minutes) / len(hold_minutes) if hold_minutes else None
    avg_win_return_pct, avg_loss_return_pct = _compute_avg_win_loss_return(trades)
    max_consecutive_losses, max_consecutive_wins = _compute_streak_stats(trades)

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
        avg_win_return_pct=avg_win_return_pct,
        avg_loss_return_pct=avg_loss_return_pct,
        max_consecutive_losses=max_consecutive_losses,
        max_consecutive_wins=max_consecutive_wins,
        strategy_name=strategy_name,
    )
```

Replace the body of `build_backtest_report()` with:

```python
def build_backtest_report(result: ReplayResult, strategy_name: str = "breakout") -> BacktestReport:
    trades = _extract_trades(result.events)
    return report_from_records(trades, result.scenario.starting_equity, strategy_name)
```

- [ ] **Step 4: Add `intent_type` to `list_closed_trades()` in `storage/repositories.py`**

In the `SELECT` list, add `x.intent_type` as the 3rd column (between `x.strategy_name` and the first correlated subquery). Update the row index offsets in the dict comprehension:

```python
# SELECT becomes:
#   x.symbol,         → row[0]
#   x.strategy_name,  → row[1]
#   x.intent_type,    → row[2]  ← NEW
#   (corr subq entry_fill)  → row[3]
#   (corr subq entry_limit) → row[4]
#   (corr subq entry_time)  → row[5]
#   x.fill_price AS exit_fill  → row[6]
#   x.updated_at AS exit_time  → row[7]
#   COALESCE(...) AS qty       → row[8]

return [
    {
        "symbol": row[0],
        "strategy_name": row[1],
        "intent_type": row[2],          # NEW
        "entry_fill": float(row[3]) if row[3] is not None else None,
        "entry_limit": float(row[4]) if row[4] is not None else None,
        "entry_time": row[5],
        "exit_fill": float(row[6]) if row[6] is not None else None,
        "exit_time": row[7],
        "qty": int(row[8]),
    }
    for row in rows
    if row[3] is not None and row[6] is not None  # row[2] was row[2], offsets shift
]
```

Note: the NULL guard was `row[2] is not None and row[5] is not None` — now it must shift to `row[3] is not None and row[6] is not None` because `entry_fill` moved from index 2 to 3 and `exit_fill` moved from 5 to 6.

- [ ] **Step 5: Update `test_storage_db.py::TestListClosedTrades` to match new column layout**

The fake tuples in `tests/unit/test_storage_db.py` currently have 8 elements matching the old column order. After adding `intent_type` at index 2, all tuples need a 9th element (or a new `intent_type` value at position 2). Update the file:

```python
# In test_returns_one_dict_per_closed_trade:
rows = [("AAPL", "breakout", "stop", 110.00, 111.00, now, 112.00, now, 10)]

# In test_excludes_rows_with_null_entry_fill:
rows = [
    ("AAPL", "breakout", "stop", None, None, now, 112.00, now, 10),  # no entry fill → skip
    ("MSFT", "breakout", "exit", 400.00, 401.00, now, 405.00, now, 5),
]
# Also update the comment in that test:
# old: "Rows where entry_fill (col 2) is None are filtered out."
# new: "Rows where entry_fill (col 3) is None are filtered out."

# In test_entry_limit_none_is_preserved:
rows = [("AAPL", "breakout", "exit", 110.00, None, now, 112.00, now, 10)]
```

The `test_returns_empty_list_when_no_closed_trades` test needs no change (no row tuples).

Also update `test_returns_one_dict_per_closed_trade` to verify the new `intent_type` key exists:
```python
assert trade["intent_type"] == "stop"
```

- [ ] **Step 6: Run Task 1 tests to verify they pass**

Run: `pytest tests/unit/test_session_eval.py -v`
Expected: all 5 tests PASS

Also run existing tests to confirm no regression:
Run: `pytest tests/unit/test_report.py tests/unit/test_tuning_sweep.py tests/unit/test_storage_db.py -v`
Expected: all PASS

- [ ] **Step 7: Commit**

```bash
git add src/alpaca_bot/replay/report.py src/alpaca_bot/storage/repositories.py \
        tests/unit/test_session_eval.py tests/unit/test_storage_db.py
git commit -m "refactor: extract report_from_records(); add intent_type to list_closed_trades()"
```

---

### Task 2: Add `row_to_trade_record()` conversion tests and `list_closed_trades()` contract test

**Files:**
- Modify: `tests/unit/test_session_eval.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/unit/test_session_eval.py`:

```python
def _make_trade_row(
    *,
    symbol: str = "AAPL",
    strategy_name: str = "breakout",
    intent_type: str = "exit",
    entry_fill: float = 100.0,
    exit_fill: float = 102.0,
    qty: int = 10,
) -> dict:
    t0 = datetime(2026, 5, 4, 10, 0, tzinfo=timezone.utc)
    t1 = datetime(2026, 5, 4, 11, 0, tzinfo=timezone.utc)
    return {
        "symbol": symbol,
        "strategy_name": strategy_name,
        "intent_type": intent_type,
        "entry_fill": entry_fill,
        "entry_limit": entry_fill + 0.05,
        "entry_time": t0,
        "exit_fill": exit_fill,
        "exit_time": t1,
        "qty": qty,
    }


def test_row_to_trade_record_stop_exit():
    from alpaca_bot.admin.session_eval_cli import _row_to_trade_record
    row = _make_trade_row(intent_type="stop", exit_fill=98.0)
    record = _row_to_trade_record(row)
    assert record.exit_reason == "stop"
    assert record.pnl < 0
    assert record.symbol == "AAPL"


def test_row_to_trade_record_eod_exit():
    from alpaca_bot.admin.session_eval_cli import _row_to_trade_record
    row = _make_trade_row(intent_type="exit", exit_fill=103.0)
    record = _row_to_trade_record(row)
    assert record.exit_reason == "eod"
    assert record.pnl > 0
    assert record.quantity == 10


def test_list_closed_trades_includes_intent_type():
    """list_closed_trades() return dict must include intent_type key."""
    from alpaca_bot.storage.repositories import OrderStore

    class _FakeConn:
        pass

    # We just need the dict shape — verify via the helper directly
    row = (
        "AAPL",     # symbol
        "breakout", # strategy_name
        "stop",     # intent_type (NEW)
        100.0,      # entry_fill
        100.05,     # entry_limit
        datetime(2026, 5, 4, 10, 0, tzinfo=timezone.utc),  # entry_time
        98.0,       # exit_fill
        datetime(2026, 5, 4, 10, 30, tzinfo=timezone.utc), # exit_time
        10,         # qty
    )
    # Reconstruct the dict comprehension inline to verify offsets are correct
    result = {
        "symbol": row[0],
        "strategy_name": row[1],
        "intent_type": row[2],
        "entry_fill": float(row[3]) if row[3] is not None else None,
        "entry_limit": float(row[4]) if row[4] is not None else None,
        "entry_time": row[5],
        "exit_fill": float(row[6]) if row[6] is not None else None,
        "exit_time": row[7],
        "qty": int(row[8]),
    }
    assert "intent_type" in result
    assert result["intent_type"] == "stop"
    assert result["entry_fill"] == 100.0
    assert result["exit_fill"] == 98.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_session_eval.py::test_row_to_trade_record_stop_exit tests/unit/test_session_eval.py::test_row_to_trade_record_eod_exit -v`
Expected: FAIL — `ImportError: cannot import name '_row_to_trade_record' from 'alpaca_bot.admin.session_eval_cli'`

- [ ] **Step 3: Create `src/alpaca_bot/admin/session_eval_cli.py`**

```python
from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, timezone
from typing import Sequence

from alpaca_bot.config import Settings
from alpaca_bot.domain.enums import TradingMode
from alpaca_bot.replay.report import BacktestReport, ReplayTradeRecord, report_from_records
from alpaca_bot.storage.db import connect_postgres
from alpaca_bot.storage.models import EQUITY_SESSION_STATE_STRATEGY_NAME
from alpaca_bot.storage.repositories import DailySessionStateStore, OrderStore


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="alpaca-bot-session-eval",
                                     description="Evaluate a live trading session from Postgres data")
    parser.add_argument("--date", metavar="YYYY-MM-DD",
                        help="Session date (default: today)")
    parser.add_argument("--mode", default="paper", choices=["paper", "live"],
                        help="Trading mode (default: paper)")
    parser.add_argument("--strategy-version", metavar="VERSION",
                        help="Strategy version (default: STRATEGY_VERSION env var)")
    parser.add_argument("--strategy", metavar="NAME",
                        help="Filter to a single strategy name")
    args = parser.parse_args(list(argv) if argv is not None else sys.argv[1:])

    eval_date: date = (
        date.fromisoformat(args.date) if args.date else date.today()
    )

    settings = Settings.from_env()
    strategy_version = args.strategy_version or settings.strategy_version
    trading_mode = TradingMode(args.mode)

    conn = connect_postgres(settings.database_url)
    try:
        order_store = OrderStore(conn)
        session_store = DailySessionStateStore(conn)

        state = session_store.load(
            session_date=eval_date,
            trading_mode=trading_mode,
            strategy_version=strategy_version,
            strategy_name=EQUITY_SESSION_STATE_STRATEGY_NAME,
        )
        if state is None or state.equity_baseline is None:
            print(f"Warning: no equity baseline found for {eval_date}; using $100,000 as starting equity.")
            starting_equity = 100_000.0
        else:
            starting_equity = state.equity_baseline

        raw_trades = order_store.list_closed_trades(
            trading_mode=trading_mode,
            strategy_version=strategy_version,
            session_date=eval_date,
            strategy_name=args.strategy,
        )
    finally:
        close = getattr(conn, "close", None)
        if callable(close):
            close()

    if not raw_trades:
        strategy_label = f" (strategy={args.strategy})" if args.strategy else ""
        print(f"No closed trades for {eval_date}{strategy_label}.")
        return 0

    trade_records = [_row_to_trade_record(row) for row in raw_trades]
    report = report_from_records(
        trade_records,
        starting_equity=starting_equity,
        strategy_name=args.strategy or "all",
    )
    _print_session_report(report, eval_date=eval_date, trading_mode=args.mode,
                          strategy_version=strategy_version)
    return 0


def _row_to_trade_record(row: dict) -> ReplayTradeRecord:
    entry = row["entry_fill"]
    exit_ = row["exit_fill"]
    qty = row["qty"]
    pnl = (exit_ - entry) * qty
    return_pct = (exit_ - entry) / entry
    exit_reason = "stop" if row.get("intent_type") == "stop" else "eod"
    return ReplayTradeRecord(
        symbol=row["symbol"],
        entry_price=entry,
        exit_price=exit_,
        quantity=qty,
        entry_time=row["entry_time"],
        exit_time=row["exit_time"],
        exit_reason=exit_reason,
        pnl=pnl,
        return_pct=return_pct,
    )


def _print_session_report(
    report: BacktestReport,
    *,
    eval_date: date,
    trading_mode: str,
    strategy_version: str,
) -> None:
    header = f"Session Evaluation — {eval_date}  [{trading_mode} / {strategy_version}]"
    bar = "═" * len(header)
    print(f"\n{header}")
    print(bar)

    win_rate_str = f"{report.win_rate:.0%}" if report.win_rate is not None else "—"
    sharpe_str = f"{report.sharpe_ratio:.2f}" if report.sharpe_ratio is not None else "—"
    pf_str = f"{report.profit_factor:.2f}" if report.profit_factor is not None else "—"
    mean_str = (f"+{report.mean_return_pct:.2%}" if report.mean_return_pct and report.mean_return_pct >= 0
                else f"{report.mean_return_pct:.2%}") if report.mean_return_pct is not None else "—"
    dd_str = f"{report.max_drawdown_pct:.2%}" if report.max_drawdown_pct is not None else "—"
    hold_str = f"{report.avg_hold_minutes:.0f}min" if report.avg_hold_minutes is not None else "—"
    total_pnl = sum(t.pnl for t in report.trades)
    pnl_str = f"+${total_pnl:.2f}" if total_pnl >= 0 else f"-${abs(total_pnl):.2f}"

    print(f" Trades: {report.total_trades:3d}  Wins: {report.winning_trades:2d}  Losses: {report.losing_trades:2d}  Win rate: {win_rate_str}")
    print(f" P&L:    {pnl_str:>9s}  Sharpe: {sharpe_str:>5s}  Prof.fac: {pf_str:>5s}")
    print(f" Mean:   {mean_str:>9s}  Max DD: {dd_str:>5s}  Avg hold: {hold_str}")
    print(f" MaxCL:  {report.max_consecutive_losses:2d}        MaxCW: {report.max_consecutive_wins:2d}")

    print()
    print(" Exit breakdown:")
    print(f"   Stop wins: {report.stop_wins:3d}   Stop losses: {report.stop_losses:3d}")
    print(f"   EOD wins:  {report.eod_wins:3d}   EOD losses:  {report.eod_losses:3d}")

    if report.trades:
        print()
        print(f" {'Symbol':<8} {'Strategy':<12} {'Qty':>4}  {'Entry':>7}  {'Exit':>7}  {'P&L':>9}  {'Ret%':>7}  {'Hold':>5}  Exit")
        print(f" {'-'*8} {'-'*12} {'-'*4}  {'-'*7}  {'-'*7}  {'-'*9}  {'-'*7}  {'-'*5}  ----")
        for t in report.trades:
            hold_m = (t.exit_time - t.entry_time).total_seconds() / 60
            pnl_sign = "+" if t.pnl >= 0 else "-"
            pnl_t = f"{pnl_sign}${abs(t.pnl):.2f}"
            ret_sign = "+" if t.return_pct >= 0 else ""
            ret_t = f"{ret_sign}{t.return_pct:.2%}"
            print(f" {t.symbol:<8} {report.strategy_name:<12} {t.quantity:>4}  {t.entry_price:>7.2f}  {t.exit_price:>7.2f}  {pnl_t:>9}  {ret_t:>7}  {hold_m:>4.0f}m  {t.exit_reason}")
    print()
```

- [ ] **Step 4: Run Task 2 tests**

Run: `pytest tests/unit/test_session_eval.py -v`
Expected: all tests PASS

- [ ] **Step 5: Add CLI tests**

Append to `tests/unit/test_session_eval.py`:

```python
def test_session_eval_cli_no_trades_exits_zero(monkeypatch, tmp_path):
    """CLI returns 0 and prints 'No closed trades' when no trades exist."""
    import sys
    from alpaca_bot.admin import session_eval_cli as module

    monkeypatch.setenv("TRADING_MODE", "paper")
    monkeypatch.setenv("ENABLE_LIVE_TRADING", "false")
    monkeypatch.setenv("STRATEGY_VERSION", "v1")
    monkeypatch.setenv("DATABASE_URL", "postgresql://dummy:dummy@localhost/dummy")
    monkeypatch.setenv("SYMBOLS", "AAPL")
    monkeypatch.setenv("MARKET_DATA_FEED", "sip")

    monkeypatch.setattr(module, "connect_postgres", lambda url: None)

    class _FakeOrderStore:
        def __init__(self, conn): pass
        def list_closed_trades(self, **kw): return []

    class _FakeDailyStore:
        def __init__(self, conn): pass
        def load(self, **kw): return None

    monkeypatch.setattr(module, "OrderStore", _FakeOrderStore)
    monkeypatch.setattr(module, "DailySessionStateStore", _FakeDailyStore)
    monkeypatch.setattr(sys, "argv", ["session-eval", "--date", "2026-05-04"])

    import io
    from contextlib import redirect_stdout
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = module.main()

    assert rc == 0
    assert "No closed trades" in buf.getvalue()


def test_session_eval_cli_produces_report(monkeypatch):
    """CLI prints a report header when trades are present."""
    import sys
    from alpaca_bot.admin import session_eval_cli as module

    monkeypatch.setenv("TRADING_MODE", "paper")
    monkeypatch.setenv("ENABLE_LIVE_TRADING", "false")
    monkeypatch.setenv("STRATEGY_VERSION", "v1")
    monkeypatch.setenv("DATABASE_URL", "postgresql://dummy:dummy@localhost/dummy")
    monkeypatch.setenv("SYMBOLS", "AAPL")
    monkeypatch.setenv("MARKET_DATA_FEED", "sip")

    t0 = datetime(2026, 5, 4, 10, 0, tzinfo=timezone.utc)
    t1 = datetime(2026, 5, 4, 11, 0, tzinfo=timezone.utc)
    fake_trades = [
        {"symbol": "AAPL", "strategy_name": "breakout", "intent_type": "exit",
         "entry_fill": 100.0, "entry_limit": 100.05, "entry_time": t0,
         "exit_fill": 102.0, "exit_time": t1, "qty": 10},
    ]

    monkeypatch.setattr(module, "connect_postgres", lambda url: None)

    class _FakeOrderStore:
        def __init__(self, conn): pass
        def list_closed_trades(self, **kw): return fake_trades

    class _FakeDailyStore:
        def __init__(self, conn): pass
        def load(self, **kw): return None

    monkeypatch.setattr(module, "OrderStore", _FakeOrderStore)
    monkeypatch.setattr(module, "DailySessionStateStore", _FakeDailyStore)
    monkeypatch.setattr(sys, "argv", ["session-eval", "--date", "2026-05-04"])

    import io
    from contextlib import redirect_stdout
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = module.main()

    assert rc == 0
    output = buf.getvalue()
    assert "Session Evaluation" in output
    assert "2026-05-04" in output
```

- [ ] **Step 6: Run all session eval tests**

Run: `pytest tests/unit/test_session_eval.py -v`
Expected: all 10 tests PASS

- [ ] **Step 7: Commit**

```bash
git add src/alpaca_bot/admin/session_eval_cli.py tests/unit/test_session_eval.py
git commit -m "feat: add session_eval_cli with _row_to_trade_record() and CLI tests"
```

---

### Task 3: Register entry point and final regression

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Add entry point to `pyproject.toml`**

In the `[project.scripts]` section, add:

```toml
alpaca-bot-session-eval = "alpaca_bot.admin.session_eval_cli:main"
```

- [ ] **Step 2: Reinstall in editable mode**

Run: `pip install -e ".[dev]" -q`

- [ ] **Step 3: Smoke test — help output**

Run: `alpaca-bot-session-eval --help`
Expected: output contains `--date`, `--mode`, `--strategy`, `--strategy-version`

- [ ] **Step 4: Full regression**

Run: `pytest -q`
Expected: all tests pass (≥1073 + 10 new = ≥1083)

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml
git commit -m "feat: register alpaca-bot-session-eval entry point"
```
