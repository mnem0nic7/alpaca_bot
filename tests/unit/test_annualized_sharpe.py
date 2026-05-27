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
