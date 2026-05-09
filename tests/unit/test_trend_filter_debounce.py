from __future__ import annotations

from datetime import datetime, timezone

import pytest

from alpaca_bot.config import Settings, TradingMode, MarketDataFeed
from alpaca_bot.domain.models import Bar
from alpaca_bot.strategy.breakout import daily_trend_filter_exit_passes


def _make_settings(sma_period: int = 5, lookback_days: int = 1) -> Settings:
    from datetime import time

    return Settings(
        trading_mode=TradingMode.PAPER,
        enable_live_trading=False,
        strategy_version="v1",
        database_url="postgresql://localhost/test",
        market_data_feed=MarketDataFeed.SIP,
        symbols=("AAPL",),
        daily_sma_period=sma_period,
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
        trend_filter_exit_lookback_days=lookback_days,
    )


def _bar(close: float, i: int = 0) -> Bar:
    return Bar(
        symbol="AAPL",
        timestamp=datetime(2026, 1, 1 + i, 21, 0, tzinfo=timezone.utc),
        open=close - 0.5,
        high=close + 0.5,
        low=close - 0.5,
        close=close,
        volume=1_000_000.0,
    )


def _make_bars(closes: list[float]) -> list[Bar]:
    """Build a daily bar list from close prices, plus a partial (intraday) bar at the end."""
    bars = [_bar(c, i) for i, c in enumerate(closes)]
    # Append a partial bar that should be excluded from SMA/close checks.
    bars.append(_bar(closes[-1] * 2, len(closes)))
    return bars


# SMA_PERIOD=5 → need 5 bars in window. With lookback_days=1 we need 5+1=6 completed bars
# plus 1 partial bar at end → 7 bars total minimum.


def test_lookback1_single_day_below_triggers_exit():
    # All 5 window closes = 100, last completed close = 90 (below SMA 100) → exit warranted
    settings = _make_settings(sma_period=5, lookback_days=1)
    closes = [100.0] * 5 + [90.0]  # 6 completed bars
    bars = _make_bars(closes)
    # Returns False → exit warranted
    assert daily_trend_filter_exit_passes(bars, settings) is False


def test_lookback1_single_day_above_holds():
    settings = _make_settings(sma_period=5, lookback_days=1)
    closes = [100.0] * 5 + [110.0]  # last close above SMA
    bars = _make_bars(closes)
    assert daily_trend_filter_exit_passes(bars, settings) is True


def test_lookback2_one_day_below_holds():
    # Only 1 day below SMA with lookback=2 → should hold (True)
    settings = _make_settings(sma_period=5, lookback_days=2)
    # bars[-3] close = 90 (below SMA 100), bars[-2] close = 110 (above SMA) → hold
    closes = [100.0] * 4 + [110.0, 90.0]  # 6 completed
    bars = _make_bars(closes)
    # The last completed bar (bars[-2] after partial) = 90, second-to-last = 110
    # → one above → hold
    assert daily_trend_filter_exit_passes(bars, settings) is True


def test_lookback2_two_consecutive_days_below_exits():
    # Both last 2 completed closes below SMA → exit
    settings = _make_settings(sma_period=5, lookback_days=2)
    closes = [100.0] * 4 + [90.0, 85.0]  # 6 completed
    bars = _make_bars(closes)
    # SMA window for offset=0: bars[-6:-1] = [100,100,100,100,90] → sma=98, close=90 < 98 ✓
    # SMA window for offset=1: bars[-7:-2] = [100,100,100,100,90] → sma=98, close=90 < 98 ✓ → exit
    assert daily_trend_filter_exit_passes(bars, settings) is False


def test_lookback2_one_below_one_above_holds():
    settings = _make_settings(sma_period=5, lookback_days=2)
    # Day N-1 (bars[-2]) = 90 (below SMA ~100); Day N-2 (bars[-3]) = 110 (above SMA ~102)
    # → second day is above → hold
    closes = [100.0] * 3 + [100.0, 110.0, 90.0]  # 6 completed
    bars = _make_bars(closes)
    # offset=0: window bars[-6:-1]=[100,100,100,110,90] → sma=100, close=bars[-2]=90 < 100 → below
    # offset=1: window bars[-7:-2]=[100,100,100,100,110] → sma=102, close=bars[-3]=110 > 102 → hold
    assert daily_trend_filter_exit_passes(bars, settings) is True


def test_insufficient_history_holds():
    # Too few bars → cannot compute → hold
    settings = _make_settings(sma_period=5, lookback_days=1)
    closes = [100.0] * 4  # only 4 completed bars (need 6)
    bars = _make_bars(closes)
    assert daily_trend_filter_exit_passes(bars, settings) is True


def test_lookback2_insufficient_history_holds():
    # required = sma_period + lookback_days = 7; 5 completed + 1 partial = 6 < 7 → hold
    settings = _make_settings(sma_period=5, lookback_days=2)
    closes = [100.0] * 5  # 5 completed + 1 partial = 6 total, need 7
    bars = _make_bars(closes)
    assert daily_trend_filter_exit_passes(bars, settings) is True
