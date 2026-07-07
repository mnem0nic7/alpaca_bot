from __future__ import annotations

import math
from datetime import date, datetime, timezone, timedelta

import pytest

from alpaca_bot.domain.models import Bar, ReplayScenario
from alpaca_bot.replay.splitter import split_scenario


def _make_bar(symbol: str, ts: datetime, price: float = 100.0) -> Bar:
    return Bar(
        symbol=symbol,
        timestamp=ts,
        open=price,
        high=price + 1,
        low=price - 1,
        close=price,
        volume=10_000.0,
    )


def _make_scenario(n_trading_days: int, intraday_per_day: int = 26) -> ReplayScenario:
    """Build a synthetic scenario with n_trading_days of data.

    Each trading day has `intraday_per_day` 15-min bars (09:30–16:00 ≈ 26 bars).
    Daily bars align one-to-one with trading days.
    """
    intraday: list[Bar] = []
    daily: list[Bar] = []
    base_date = date(2026, 1, 5)
    for day_idx in range(n_trading_days):
        trading_date = base_date + timedelta(days=day_idx)
        daily_ts = datetime(
            trading_date.year, trading_date.month, trading_date.day, 20, 0,
            tzinfo=timezone.utc,
        )
        daily.append(_make_bar("AAPL", daily_ts, 150.0 + day_idx))
        for bar_idx in range(intraday_per_day):
            bar_ts = datetime(
                trading_date.year, trading_date.month, trading_date.day,
                9, 30 + bar_idx * 15 // 60,
                (bar_idx * 15) % 60,
                tzinfo=timezone.utc,
            )
            intraday.append(_make_bar("AAPL", bar_ts, 151.0 + day_idx + bar_idx * 0.01))
    return ReplayScenario(
        name="test_scenario",
        symbol="AAPL",
        starting_equity=100_000.0,
        daily_bars=daily,
        intraday_bars=intraday,
    )


def test_split_respects_ratio():
    scenario = _make_scenario(100)
    is_s, oos_s = split_scenario(scenario, in_sample_ratio=0.8)
    is_dates = {b.timestamp.date() for b in is_s.intraday_bars}
    oos_dates = {b.timestamp.date() for b in oos_s.intraday_bars}
    assert len(is_dates) == 80
    assert len(oos_dates) == 20


def test_split_intraday_bars_no_overlap():
    scenario = _make_scenario(50)
    is_s, oos_s = split_scenario(scenario)
    is_dates = {b.timestamp.date() for b in is_s.intraday_bars}
    oos_dates = {b.timestamp.date() for b in oos_s.intraday_bars}
    assert is_dates.isdisjoint(oos_dates)


def test_split_oos_daily_includes_warmup_prefix():
    scenario = _make_scenario(50, intraday_per_day=4)
    is_s, oos_s = split_scenario(scenario, in_sample_ratio=0.8, daily_warmup=30)
    # IS has 40 trading days → 40 daily bars; OOS has 10 trading days → 10 daily bars
    # OOS daily_bars = last 30 IS daily bars (warmup) + 10 OOS daily bars = 40 total
    is_daily_ts = {b.timestamp for b in is_s.daily_bars}
    oos_daily_ts = [b.timestamp for b in oos_s.daily_bars]
    warmup_ts = oos_daily_ts[:30]
    assert all(ts in is_daily_ts for ts in warmup_ts)
    assert len(oos_s.daily_bars) == 30 + 10


def test_split_preserves_regime_daily_warmup():
    scenario = _make_scenario(50, intraday_per_day=4)
    scenario.regime_daily_bars = [
        _make_bar("SPY", bar.timestamp, 400.0 + idx)
        for idx, bar in enumerate(scenario.daily_bars)
    ]

    is_s, oos_s = split_scenario(
        scenario,
        in_sample_ratio=0.8,
        daily_warmup=30,
    )

    assert is_s.regime_daily_bars is not None
    assert oos_s.regime_daily_bars is not None
    is_regime_ts = {b.timestamp for b in is_s.regime_daily_bars}
    oos_regime_ts = [b.timestamp for b in oos_s.regime_daily_bars]
    assert all(ts in is_regime_ts for ts in oos_regime_ts[:30])
    assert len(oos_s.regime_daily_bars) == 30 + 10


def test_split_preserves_market_context_daily_warmup():
    scenario = _make_scenario(50, intraday_per_day=4)
    scenario.vix_daily_bars = [
        _make_bar("VIXY", bar.timestamp, 20.0 + idx)
        for idx, bar in enumerate(scenario.daily_bars)
    ]
    scenario.sector_daily_bars_by_etf = {
        "XLK": [
            _make_bar("XLK", bar.timestamp, 200.0 + idx)
            for idx, bar in enumerate(scenario.daily_bars)
        ]
    }

    is_s, oos_s = split_scenario(
        scenario,
        in_sample_ratio=0.8,
        daily_warmup=30,
    )

    assert is_s.vix_daily_bars is not None
    assert oos_s.vix_daily_bars is not None
    is_vix_ts = {b.timestamp for b in is_s.vix_daily_bars}
    oos_vix_ts = [b.timestamp for b in oos_s.vix_daily_bars]
    assert all(ts in is_vix_ts for ts in oos_vix_ts[:30])
    assert len(oos_s.vix_daily_bars) == 30 + 10

    assert is_s.sector_daily_bars_by_etf is not None
    assert oos_s.sector_daily_bars_by_etf is not None
    is_sector_ts = {b.timestamp for b in is_s.sector_daily_bars_by_etf["XLK"]}
    oos_sector_ts = [
        b.timestamp for b in oos_s.sector_daily_bars_by_etf["XLK"]
    ]
    assert all(ts in is_sector_ts for ts in oos_sector_ts[:30])
    assert len(oos_s.sector_daily_bars_by_etf["XLK"]) == 30 + 10


def test_split_names_suffixed():
    scenario = _make_scenario(20)
    is_s, oos_s = split_scenario(scenario)
    assert is_s.name == "test_scenario_is"
    assert oos_s.name == "test_scenario_oos"


def test_split_raises_on_too_short_scenario():
    scenario = _make_scenario(9)
    with pytest.raises(ValueError, match="too short"):
        split_scenario(scenario)


def test_split_oos_has_at_least_one_date():
    scenario = _make_scenario(10)
    is_s, oos_s = split_scenario(scenario, in_sample_ratio=0.99)
    oos_dates = {b.timestamp.date() for b in oos_s.intraday_bars}
    assert len(oos_dates) >= 1
