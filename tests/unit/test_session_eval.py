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
    scenario = ReplayScenario(
        name="test", symbol="AAPL", starting_equity=100_000.0,
        daily_bars=[], intraday_bars=[],
    )
    result = ReplayResult(scenario=scenario, events=events, final_position=None, traded_symbols=set())

    backtest_report = build_backtest_report(result)

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
