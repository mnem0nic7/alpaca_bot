from __future__ import annotations

from datetime import datetime, timedelta, timezone

from alpaca_bot.config import Settings
from alpaca_bot.core.engine import _filter_valid_bars
from alpaca_bot.domain.models import Bar, ReplayScenario
from alpaca_bot.replay.portfolio import PortfolioReplayRunner, _BarPrefix


def _settings() -> Settings:
    return Settings.from_env({
        "TRADING_MODE": "paper",
        "ENABLE_LIVE_TRADING": "false",
        "STRATEGY_VERSION": "v1-breakout",
        "DATABASE_URL": "postgresql://u:p@h:5432/d",
        "MARKET_DATA_FEED": "sip",
        "SYMBOLS": "AAA",
    })


def _bar(symbol: str, ts: datetime, close: float = 100.0) -> Bar:
    return Bar(
        symbol=symbol,
        timestamp=ts,
        open=close,
        high=close + 1.0,
        low=max(close - 1.0, 0.0),
        close=close,
        volume=1000,
    )


def test_filter_valid_bars_still_drops_zero_close_for_plain_sequences() -> None:
    ts = datetime(2026, 1, 2, tzinfo=timezone.utc)
    bars = [_bar("AAA", ts, 100.0), _bar("AAA", ts + timedelta(days=1), 0.0)]

    filtered = _filter_valid_bars(bars, label="AAA")

    assert len(filtered) == 1
    assert filtered[0].close == 100.0


def test_portfolio_daily_slice_marks_known_clean_bars_for_engine_fast_path() -> None:
    settings = _settings()
    runner = PortfolioReplayRunner(settings)
    daily = [
        _bar("AAA", datetime(2026, 1, 2, tzinfo=timezone.utc)),
        _bar("AAA", datetime(2026, 1, 3, tzinfo=timezone.utc)),
    ]
    scenario = ReplayScenario(
        name="AAA",
        symbol="AAA",
        starting_equity=100_000.0,
        daily_bars=daily,
        intraday_bars=[_bar("AAA", datetime(2026, 1, 4, 14, 30, tzinfo=timezone.utc))],
    )
    runner._index_scenarios([scenario])

    clean_slice = runner._daily_slice_for("AAA", datetime(2026, 1, 4, 14, 30, tzinfo=timezone.utc))

    assert getattr(clean_slice, "all_closes_positive", False) is True
    assert isinstance(clean_slice, _BarPrefix)
    assert clean_slice._bars is daily
    assert _filter_valid_bars(clean_slice, label="AAA") is clean_slice


def test_portfolio_daily_slice_does_not_mark_dirty_bars_clean() -> None:
    settings = _settings()
    runner = PortfolioReplayRunner(settings)
    daily = [
        _bar("AAA", datetime(2026, 1, 2, tzinfo=timezone.utc)),
        _bar("AAA", datetime(2026, 1, 3, tzinfo=timezone.utc), close=0.0),
    ]
    scenario = ReplayScenario(
        name="AAA",
        symbol="AAA",
        starting_equity=100_000.0,
        daily_bars=daily,
        intraday_bars=[_bar("AAA", datetime(2026, 1, 4, 14, 30, tzinfo=timezone.utc))],
    )
    runner._index_scenarios([scenario])

    dirty_slice = runner._daily_slice_for("AAA", datetime(2026, 1, 4, 14, 30, tzinfo=timezone.utc))
    filtered = _filter_valid_bars(dirty_slice, label="AAA")

    assert getattr(dirty_slice, "all_closes_positive", False) is False
    assert isinstance(dirty_slice, _BarPrefix)
    assert dirty_slice._bars is daily
    assert len(filtered) == 1
    assert filtered[0].close == 100.0


def test_portfolio_index_reuses_already_sorted_bar_lists() -> None:
    settings = _settings()
    runner = PortfolioReplayRunner(settings)
    intraday = [
        _bar("AAA", datetime(2026, 1, 4, 14, 30, tzinfo=timezone.utc)),
        _bar("AAA", datetime(2026, 1, 4, 14, 45, tzinfo=timezone.utc)),
    ]
    daily = [
        _bar("AAA", datetime(2026, 1, 2, tzinfo=timezone.utc)),
        _bar("AAA", datetime(2026, 1, 3, tzinfo=timezone.utc)),
    ]
    scenario = ReplayScenario(
        name="AAA",
        symbol="AAA",
        starting_equity=100_000.0,
        daily_bars=daily,
        intraday_bars=intraday,
    )

    runner._index_scenarios([scenario])
    lane = runner._lanes["AAA"]

    assert lane.intraday is intraday
    assert lane.daily is daily


def test_portfolio_index_sorts_unsorted_bar_lists() -> None:
    settings = _settings()
    runner = PortfolioReplayRunner(settings)
    later = _bar("AAA", datetime(2026, 1, 4, 14, 45, tzinfo=timezone.utc))
    earlier = _bar("AAA", datetime(2026, 1, 4, 14, 30, tzinfo=timezone.utc))
    scenario = ReplayScenario(
        name="AAA",
        symbol="AAA",
        starting_equity=100_000.0,
        daily_bars=[later, earlier],
        intraday_bars=[later, earlier],
    )

    runner._index_scenarios([scenario])
    lane = runner._lanes["AAA"]

    assert lane.intraday == [earlier, later]
    assert lane.daily == [earlier, later]
