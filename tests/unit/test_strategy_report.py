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
