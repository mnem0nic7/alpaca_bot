from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from alpaca_bot.domain.models import Bar, EntrySignal
from alpaca_bot.strategy.high_watermark import evaluate_high_watermark_signal


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
        high_watermark_lookback_days=10,
    )
    defaults.update(overrides)
    return Settings(**defaults)


def _make_daily_bars(n: int, high_peak: float = 150.0, high_base: float = 100.0) -> list[Bar]:
    """
    Build n daily bars with steadily increasing closes (high_base+i) except the last bar
    which is high_peak. This ensures the trend filter (SMA) passes: the last close (high_peak-0.5)
    is well above the SMA of the trailing daily_sma_period=5 bars.
    """
    bars = []
    for i in range(n):
        h = high_peak if i == n - 1 else high_base + i
        bars.append(
            Bar(
                symbol="AAPL",
                timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(days=i),
                open=h - 1.0,
                high=h,
                low=h - 2.0,
                close=h - 0.5,
                volume=1_000_000.0,
            )
        )
    return bars


def _make_intraday_bars(
    n: int = 6,
    signal_high: float = 155.0,
    signal_close: float = 154.0,
    signal_volume: float = 200_000.0,
) -> tuple[list[Bar], int]:
    ny = ZoneInfo("America/New_York")
    base = datetime(2026, 1, 2, 10, 0, tzinfo=ny)
    bars = []
    for i in range(n - 1):
        ts = base + timedelta(minutes=15 * i)
        bars.append(
            Bar(
                symbol="AAPL",
                timestamp=ts,
                open=100.0, high=101.0, low=99.0, close=100.5,
                volume=50_000.0,
            )
        )
    signal_ts = base + timedelta(minutes=15 * (n - 1))
    bars.append(
        Bar(
            symbol="AAPL",
            timestamp=signal_ts,
            open=signal_close - 0.5,
            high=signal_high,
            low=signal_close - 1.0,
            close=signal_close,
            volume=signal_volume,
        )
    )
    return bars, n - 1


def test_high_watermark_returns_signal_when_all_conditions_met():
    settings = _make_settings()
    daily_bars = _make_daily_bars(n=10, high_peak=150.0, high_base=100.0)
    intraday_bars, signal_index = _make_intraday_bars(signal_high=155.0, signal_close=154.0)
    result = evaluate_high_watermark_signal(
        symbol="AAPL",
        intraday_bars=intraday_bars,
        signal_index=signal_index,
        daily_bars=daily_bars,
        settings=settings,
    )
    assert result is not None
    assert isinstance(result, EntrySignal)
    assert result.symbol == "AAPL"


def test_high_watermark_entry_level_equals_historical_high():
    settings = _make_settings(high_watermark_lookback_days=10)
    daily_bars = _make_daily_bars(n=10, high_peak=150.0, high_base=100.0)
    intraday_bars, signal_index = _make_intraday_bars(signal_high=155.0, signal_close=154.0)
    result = evaluate_high_watermark_signal(
        symbol="AAPL",
        intraday_bars=intraday_bars,
        signal_index=signal_index,
        daily_bars=daily_bars,
        settings=settings,
    )
    assert result is not None
    assert result.entry_level == 150.0


def test_high_watermark_stop_below_historical_high():
    settings = _make_settings()
    daily_bars = _make_daily_bars(n=10, high_peak=150.0)
    intraday_bars, signal_index = _make_intraday_bars(signal_high=155.0, signal_close=154.0)
    result = evaluate_high_watermark_signal(
        symbol="AAPL",
        intraday_bars=intraday_bars,
        signal_index=signal_index,
        daily_bars=daily_bars,
        settings=settings,
    )
    assert result is not None
    assert result.initial_stop_price < 150.0


def test_high_watermark_returns_none_when_bar_does_not_cross_historical_high():
    settings = _make_settings()
    daily_bars = _make_daily_bars(n=10, high_peak=150.0)
    intraday_bars, signal_index = _make_intraday_bars(signal_high=148.0, signal_close=147.0)
    result = evaluate_high_watermark_signal(
        symbol="AAPL",
        intraday_bars=intraday_bars,
        signal_index=signal_index,
        daily_bars=daily_bars,
        settings=settings,
    )
    assert result is None


def test_high_watermark_returns_none_when_close_below_historical_high():
    settings = _make_settings()
    daily_bars = _make_daily_bars(n=10, high_peak=150.0)
    # high crosses but close stays below
    intraday_bars, signal_index = _make_intraday_bars(signal_high=152.0, signal_close=149.0)
    result = evaluate_high_watermark_signal(
        symbol="AAPL",
        intraday_bars=intraday_bars,
        signal_index=signal_index,
        daily_bars=daily_bars,
        settings=settings,
    )
    assert result is None


def test_high_watermark_returns_none_with_insufficient_daily_bars():
    settings = _make_settings(high_watermark_lookback_days=10)
    # only 5 daily bars but we need 10
    daily_bars = _make_daily_bars(n=5, high_peak=150.0)
    intraday_bars, signal_index = _make_intraday_bars(signal_high=155.0, signal_close=154.0)
    result = evaluate_high_watermark_signal(
        symbol="AAPL",
        intraday_bars=intraday_bars,
        signal_index=signal_index,
        daily_bars=daily_bars,
        settings=settings,
    )
    assert result is None


def test_high_watermark_returns_none_when_trend_filter_fails():
    settings = _make_settings(daily_sma_period=5)
    # all bars close below their high → SMA > close → trend filter fails
    daily_bars = [
        Bar(
            symbol="AAPL",
            timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(days=i),
            open=110.0, high=160.0, low=80.0, close=85.0,
            volume=1_000_000.0,
        )
        for i in range(10)
    ]
    intraday_bars, signal_index = _make_intraday_bars(signal_high=165.0, signal_close=164.0)
    result = evaluate_high_watermark_signal(
        symbol="AAPL",
        intraday_bars=intraday_bars,
        signal_index=signal_index,
        daily_bars=daily_bars,
        settings=settings,
    )
    assert result is None


def test_high_watermark_returns_none_when_volume_below_threshold():
    settings = _make_settings(relative_volume_threshold=1.5)
    daily_bars = _make_daily_bars(n=10, high_peak=150.0)
    # all bars including signal have low volume → rv < 1.5
    intraday_bars, signal_index = _make_intraday_bars(
        signal_high=155.0, signal_close=154.0, signal_volume=10_000.0
    )
    result = evaluate_high_watermark_signal(
        symbol="AAPL",
        intraday_bars=intraday_bars,
        signal_index=signal_index,
        daily_bars=daily_bars,
        settings=settings,
    )
    assert result is None


def test_high_watermark_returns_none_outside_entry_window():
    settings = _make_settings()
    daily_bars = _make_daily_bars(n=10, high_peak=150.0)
    ny = ZoneInfo("America/New_York")
    intraday_bars, _ = _make_intraday_bars(signal_high=155.0, signal_close=154.0)
    late_ts = datetime(2026, 1, 2, 16, 0, tzinfo=ny)
    intraday_bars[-1] = Bar(
        symbol="AAPL",
        timestamp=late_ts,
        open=153.5, high=155.0, low=153.0, close=154.0,
        volume=200_000.0,
    )
    result = evaluate_high_watermark_signal(
        symbol="AAPL",
        intraday_bars=intraday_bars,
        signal_index=len(intraday_bars) - 1,
        daily_bars=daily_bars,
        settings=settings,
    )
    assert result is None
