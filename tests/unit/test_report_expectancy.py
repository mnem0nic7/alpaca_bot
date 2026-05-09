from __future__ import annotations

from datetime import datetime, timezone

import pytest

from alpaca_bot.replay.report import BacktestReport, ReplayTradeRecord, report_from_records


def _trade(pnl: float, return_pct: float, exit_reason: str = "stop") -> ReplayTradeRecord:
    t0 = datetime(2026, 5, 9, 10, 0, tzinfo=timezone.utc)
    t1 = datetime(2026, 5, 9, 15, 0, tzinfo=timezone.utc)
    return ReplayTradeRecord(
        symbol="AAPL",
        entry_price=100.0,
        exit_price=100.0 + return_pct * 100.0,
        quantity=10,
        entry_time=t0,
        exit_time=t1,
        exit_reason=exit_reason,
        pnl=pnl,
        return_pct=return_pct,
    )


def test_expectancy_zero_trades():
    report = report_from_records([], starting_equity=100_000.0)
    assert report.expectancy_pct is None


def test_expectancy_100_percent_win_rate():
    # All trades win: expectancy = 1.0 * avg_win_pct + 0.0 * avg_loss_pct = avg_win_pct
    trades = [_trade(pnl=50.0, return_pct=0.05), _trade(pnl=30.0, return_pct=0.03)]
    report = report_from_records(trades, starting_equity=100_000.0)
    assert report.win_rate == pytest.approx(1.0)
    assert report.avg_loss_return_pct is None
    # Expectancy is None when either avg component is None
    assert report.expectancy_pct is None


def test_expectancy_0_percent_win_rate():
    # All trades lose: expectancy = 0.0 * avg_win_pct + 1.0 * avg_loss_pct = avg_loss_pct
    trades = [_trade(pnl=-50.0, return_pct=-0.05), _trade(pnl=-30.0, return_pct=-0.03)]
    report = report_from_records(trades, starting_equity=100_000.0)
    assert report.win_rate == pytest.approx(0.0)
    assert report.avg_win_return_pct is None
    # Expectancy is None when avg_win_return_pct is None
    assert report.expectancy_pct is None


def test_expectancy_50_50():
    # 50% win rate, avg_win=+0.10, avg_loss=-0.05
    # expectancy = 0.5 * 0.10 + 0.5 * (-0.05) = 0.05 - 0.025 = 0.025
    trades = [
        _trade(pnl=100.0, return_pct=0.10),
        _trade(pnl=-50.0, return_pct=-0.05),
    ]
    report = report_from_records(trades, starting_equity=100_000.0)
    assert report.win_rate == pytest.approx(0.5)
    assert report.expectancy_pct == pytest.approx(0.025)


def test_expectancy_mixed():
    # 2 wins @ 0.08, 1 loss @ -0.04 → win_rate=2/3, avg_win=0.08, avg_loss=-0.04
    # expectancy = (2/3)*0.08 + (1/3)*(-0.04) = 0.0533 - 0.0133 = 0.04
    trades = [
        _trade(pnl=80.0, return_pct=0.08),
        _trade(pnl=80.0, return_pct=0.08),
        _trade(pnl=-40.0, return_pct=-0.04),
    ]
    report = report_from_records(trades, starting_equity=100_000.0)
    expected = (2 / 3) * 0.08 + (1 / 3) * (-0.04)
    assert report.expectancy_pct == pytest.approx(expected, rel=1e-6)


def test_profit_target_wins_counted():
    # Two profit_target wins, one stop loss
    # exit_reason="profit_target" → counted in profit_target_wins
    trades = [
        _trade(pnl=100.0, return_pct=0.10, exit_reason="profit_target"),
        _trade(pnl=80.0, return_pct=0.08, exit_reason="profit_target"),
        _trade(pnl=-40.0, return_pct=-0.04, exit_reason="stop"),
    ]
    report = report_from_records(trades, starting_equity=100_000.0)
    assert report.profit_target_wins == 2
    assert report.profit_target_losses == 0


def test_profit_target_losses_counted():
    # A profit_target exit can produce a loss if price drops after order
    # (rare in simulation since fill is at target_price, but test the counter)
    trades = [
        _trade(pnl=-10.0, return_pct=-0.01, exit_reason="profit_target"),
        _trade(pnl=50.0, return_pct=0.05, exit_reason="stop"),
    ]
    report = report_from_records(trades, starting_equity=100_000.0)
    assert report.profit_target_wins == 0
    assert report.profit_target_losses == 1
