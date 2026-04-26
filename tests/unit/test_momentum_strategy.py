from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from alpaca_bot.domain.models import Bar, EntrySignal


def _make_settings(**overrides):
    from alpaca_bot.config import Settings, TradingMode, MarketDataFeed
    from datetime import time
    defaults = dict(
        trading_mode=TradingMode.PAPER,
        enable_live_trading=False,
        strategy_version="v1",
        database_url="postgresql://localhost/test",
        market_data_feed=MarketDataFeed.SIP,
        symbols=("AAPL",),
        daily_sma_period=5,
        breakout_lookback_bars=5,
        relative_volume_lookback_bars=5,
        relative_volume_threshold=1.5,
        entry_timeframe_minutes=15,
        risk_per_trade_pct=0.01,
        max_position_pct=0.1,
        max_open_positions=3,
        daily_loss_limit_pct=0.01,
        stop_limit_buffer_pct=0.001,
        breakout_stop_buffer_pct=0.001,
        entry_stop_price_buffer=0.01,
        entry_window_start=time(10, 0),
        entry_window_end=time(15, 30),
        flatten_time=time(15, 45),
        prior_day_high_lookback_bars=1,
    )
    defaults.update(overrides)
    return Settings(**defaults)


def _make_daily_bar(high: float, close: float = None) -> Bar:
    return Bar(
        symbol="AAPL",
        timestamp=datetime(2026, 1, 1, 21, 0, tzinfo=timezone.utc),
        open=close or high - 1.0,
        high=high,
        low=high - 2.0,
        close=close or high - 0.5,
        volume=1_000_000.0,
    )


def _make_intraday_bar(
    high: float,
    close: float,
    ts: datetime = None,
    volume: float = 200_000.0,
) -> Bar:
    if ts is None:
        ts = datetime(2026, 1, 2, 14, 0, tzinfo=ZoneInfo("America/New_York"))
    return Bar(
        symbol="AAPL",
        timestamp=ts,
        open=close - 1.0,
        high=high,
        low=close - 2.0,
        close=close,
        volume=volume,
    )


def _make_daily_bars(n: int = 10, high: float = 100.0) -> list[Bar]:
    return [_make_daily_bar(high=high + i * 0.1) for i in range(n)]


def _make_intraday_bars(n: int = 6, high: float = 102.0, close: float = 101.5) -> list[Bar]:
    base = datetime(2026, 1, 2, 10, 0, tzinfo=ZoneInfo("America/New_York"))
    from datetime import timedelta
    bars = []
    for i in range(n):
        ts = base + timedelta(minutes=15 * i)
        vol = 50_000.0 if i < n - 1 else 200_000.0
        bars.append(_make_intraday_bar(high=high, close=close, ts=ts, volume=vol))
    return bars


def test_momentum_evaluator_returns_entry_signal_when_all_conditions_met():
    from alpaca_bot.strategy.momentum import evaluate_momentum_signal
    settings = _make_settings()
    daily_bars = _make_daily_bars(n=10, high=100.0)
    intraday_bars = _make_intraday_bars(n=6, high=102.0, close=101.5)
    result = evaluate_momentum_signal(
        symbol="AAPL",
        intraday_bars=intraday_bars,
        signal_index=len(intraday_bars) - 1,
        daily_bars=daily_bars,
        settings=settings,
    )
    assert result is not None
    assert isinstance(result, EntrySignal)
    assert result.symbol == "AAPL"


def test_momentum_entry_level_equals_prior_day_high():
    from alpaca_bot.strategy.momentum import evaluate_momentum_signal
    settings = _make_settings()
    daily_bars = _make_daily_bars(n=10, high=100.0)
    prior_day_high = daily_bars[-1].high
    intraday_bars = _make_intraday_bars(n=6, high=prior_day_high + 2.0, close=prior_day_high + 1.5)
    result = evaluate_momentum_signal(
        symbol="AAPL",
        intraday_bars=intraday_bars,
        signal_index=len(intraday_bars) - 1,
        daily_bars=daily_bars,
        settings=settings,
    )
    assert result is not None
    assert result.entry_level == prior_day_high


def test_momentum_returns_none_outside_entry_window():
    from alpaca_bot.strategy.momentum import evaluate_momentum_signal
    settings = _make_settings()
    daily_bars = _make_daily_bars(n=10, high=100.0)
    from datetime import timedelta
    ts = datetime(2026, 1, 2, 16, 0, tzinfo=ZoneInfo("America/New_York"))
    base_bars = _make_intraday_bars(n=5, high=102.0, close=101.5)
    late_bar = _make_intraday_bar(high=102.0, close=101.5, ts=ts)
    intraday_bars = base_bars + [late_bar]
    result = evaluate_momentum_signal(
        symbol="AAPL",
        intraday_bars=intraday_bars,
        signal_index=len(intraday_bars) - 1,
        daily_bars=daily_bars,
        settings=settings,
    )
    assert result is None


def test_momentum_returns_none_when_trend_filter_fails():
    from alpaca_bot.strategy.momentum import evaluate_momentum_signal
    settings = _make_settings()
    daily_bars = [
        Bar(
            symbol="AAPL",
            timestamp=datetime(2026, 1, 1, 21, 0, tzinfo=timezone.utc),
            open=110.0, high=111.0, low=109.0, close=90.0,
            volume=1_000_000.0,
        )
        for _ in range(10)
    ]
    intraday_bars = _make_intraday_bars(n=6, high=102.0, close=101.5)
    result = evaluate_momentum_signal(
        symbol="AAPL",
        intraday_bars=intraday_bars,
        signal_index=len(intraday_bars) - 1,
        daily_bars=daily_bars,
        settings=settings,
    )
    assert result is None


def test_momentum_returns_none_when_bar_does_not_cross_prior_day_high():
    from alpaca_bot.strategy.momentum import evaluate_momentum_signal
    settings = _make_settings()
    daily_bars = _make_daily_bars(n=10, high=105.0)
    intraday_bars = _make_intraday_bars(n=6, high=103.0, close=102.5)
    result = evaluate_momentum_signal(
        symbol="AAPL",
        intraday_bars=intraday_bars,
        signal_index=len(intraday_bars) - 1,
        daily_bars=daily_bars,
        settings=settings,
    )
    assert result is None


def test_momentum_returns_none_when_volume_below_threshold():
    from alpaca_bot.strategy.momentum import evaluate_momentum_signal
    settings = _make_settings()
    daily_bars = _make_daily_bars(n=10, high=100.0)
    base = datetime(2026, 1, 2, 10, 0, tzinfo=ZoneInfo("America/New_York"))
    from datetime import timedelta
    intraday_bars = [
        Bar(
            symbol="AAPL",
            timestamp=base + timedelta(minutes=15 * i),
            open=100.0, high=102.0, low=99.0, close=101.5,
            volume=10_000.0,
        )
        for i in range(6)
    ]
    result = evaluate_momentum_signal(
        symbol="AAPL",
        intraday_bars=intraday_bars,
        signal_index=len(intraday_bars) - 1,
        daily_bars=daily_bars,
        settings=settings,
    )
    assert result is None


def test_settings_has_prior_day_high_lookback_bars():
    settings = _make_settings(prior_day_high_lookback_bars=2)
    assert settings.prior_day_high_lookback_bars == 2


def test_settings_validates_prior_day_high_lookback_bars():
    with pytest.raises(ValueError, match="PRIOR_DAY_HIGH_LOOKBACK_BARS"):
        _make_settings(prior_day_high_lookback_bars=0)


def test_settings_no_longer_requires_15_minute_timeframe():
    settings = _make_settings(entry_timeframe_minutes=5)
    assert settings.entry_timeframe_minutes == 5


def test_momentum_in_strategy_registry():
    from alpaca_bot.strategy import STRATEGY_REGISTRY
    assert "momentum" in STRATEGY_REGISTRY
