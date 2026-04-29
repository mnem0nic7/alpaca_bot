from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from alpaca_bot.domain.models import Bar
from alpaca_bot.risk.atr import calculate_atr
from alpaca_bot.strategy.breakout import evaluate_breakout_signal


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


def _make_daily_bars(n: int) -> list[Bar]:
    """Uniform high-low range of 2.0 → TR=2 for all interior bars → predictable ATR."""
    return [
        Bar(
            symbol="AAPL",
            timestamp=datetime(2026, 1, 1, 21, 0, tzinfo=timezone.utc),
            open=99.0 + i,
            high=100.0 + i,
            low=98.0 + i,
            close=100.0 + i,
            volume=1_000_000.0,
        )
        for i in range(n)
    ]


def _make_breakout_intraday_bars(
    lookback_high: float = 100.0,
    signal_high: float = 101.5,
    signal_close: float = 101.0,
) -> tuple[list[Bar], int]:
    ny = ZoneInfo("America/New_York")
    base = datetime(2026, 1, 2, 10, 0, tzinfo=ny)
    bars = []
    for i in range(5):
        ts = base + timedelta(minutes=15 * i)
        bars.append(Bar(
            symbol="AAPL", timestamp=ts,
            open=lookback_high - 0.5, high=lookback_high,
            low=lookback_high - 1.0, close=lookback_high - 0.2,
            volume=50_000.0,
        ))
    signal_ts = base + timedelta(minutes=15 * 5)
    bars.append(Bar(
        symbol="AAPL", timestamp=signal_ts,
        open=signal_close - 0.5, high=signal_high,
        low=signal_close - 1.0, close=signal_close,
        volume=200_000.0,
    ))
    return bars, len(bars) - 1


def test_breakout_initial_stop_uses_atr_when_enough_daily_bars():
    settings = _make_settings(atr_period=3)
    daily_bars = _make_daily_bars(n=6)  # 6 >= atr_period+1=4 → ATR computable
    intraday_bars, signal_index = _make_breakout_intraday_bars()

    atr = calculate_atr(daily_bars, 3)
    assert atr is not None
    breakout_level = 100.0
    expected_stop = round(breakout_level - max(0.01, 1.5 * atr), 2)

    result = evaluate_breakout_signal(
        symbol="AAPL",
        intraday_bars=intraday_bars,
        signal_index=signal_index,
        daily_bars=daily_bars,
        settings=settings,
    )
    assert result is not None
    assert result.initial_stop_price == expected_stop


def test_breakout_entry_stop_anchored_to_breakout_level_not_signal_bar_high():
    """Entry stop trigger must use breakout_level + buffer, not signal_bar.high + buffer.

    When the signal bar runs up well above the breakout level, using signal_bar.high
    would inflate the trigger price and reduce fill rate on subsequent bars.
    """
    settings = _make_settings(atr_period=3)
    daily_bars = _make_daily_bars(n=6)  # 6 >= atr_period+1=4 → ATR guard passes
    # Signal bar high (102.0) is 2.0 above breakout_level (100.0)
    intraday_bars, signal_index = _make_breakout_intraday_bars(
        lookback_high=100.0, signal_high=102.0, signal_close=101.5
    )

    result = evaluate_breakout_signal(
        symbol="AAPL",
        intraday_bars=intraday_bars,
        signal_index=signal_index,
        daily_bars=daily_bars,
        settings=settings,
    )
    assert result is not None
    breakout_level = 100.0
    assert result.stop_price == round(breakout_level + settings.entry_stop_price_buffer, 2)
    assert result.limit_price == round(result.stop_price * (1 + settings.stop_limit_buffer_pct), 2)


def test_breakout_volume_lookback_uses_relative_volume_lookback_bars_not_breakout_lookback_bars():
    """RELATIVE_VOLUME_LOOKBACK_BARS must be the window for average volume, not BREAKOUT_LOOKBACK_BARS.

    When the two configs differ, relative_volume is computed only from the
    relative_volume_lookback_bars window.
    """
    # Use a short vol lookback (2) and a longer price lookback (5)
    settings = _make_settings(atr_period=3, breakout_lookback_bars=5, relative_volume_lookback_bars=2)
    daily_bars = _make_daily_bars(n=6)  # 6 >= atr_period+1=4 → ATR guard passes
    ny = ZoneInfo("America/New_York")
    base = datetime(2026, 1, 2, 10, 0, tzinfo=ny)

    volumes = [50_000.0, 60_000.0, 70_000.0, 80_000.0, 90_000.0]
    bars = []
    for i, vol in enumerate(volumes):
        bars.append(Bar(
            symbol="AAPL",
            timestamp=base + timedelta(minutes=15 * i),
            open=99.5, high=100.0, low=98.5, close=99.8,
            volume=vol,
        ))
    # Signal bar: high breaks above lookback, volume >> avg of last 2 bars (80k+90k)/2=85k
    bars.append(Bar(
        symbol="AAPL",
        timestamp=base + timedelta(minutes=15 * 5),
        open=100.0, high=102.0, low=99.5, close=101.5,
        volume=300_000.0,  # 300k / 85k = 3.5x > threshold=1.5 ✓
    ))
    signal_index = len(bars) - 1

    result = evaluate_breakout_signal(
        symbol="AAPL",
        intraday_bars=bars,
        signal_index=signal_index,
        daily_bars=daily_bars,
        settings=settings,
    )
    assert result is not None
    # Relative volume = 300k / ((80k + 90k) / 2) = 300k / 85k ≈ 3.53
    assert result.relative_volume == pytest.approx(300_000.0 / 85_000.0, rel=1e-3)


def test_breakout_returns_none_when_atr_insufficient():
    settings = _make_settings(atr_period=3, daily_sma_period=2)
    daily_bars = _make_daily_bars(n=3)  # 3 < atr_period+1=4 → ATR returns None; 3 >= sma_period+1=3 → trend filter passes
    intraday_bars, signal_index = _make_breakout_intraday_bars()

    assert calculate_atr(daily_bars, 3) is None

    result = evaluate_breakout_signal(
        symbol="AAPL",
        intraday_bars=intraday_bars,
        signal_index=signal_index,
        daily_bars=daily_bars,
        settings=settings,
    )
    assert result is None
