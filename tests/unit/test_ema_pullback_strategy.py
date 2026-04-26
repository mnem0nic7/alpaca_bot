from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from alpaca_bot.domain.models import Bar, EntrySignal
from alpaca_bot.strategy.ema_pullback import _calculate_ema, _detect_ema_pullback, evaluate_ema_pullback_signal


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
        ema_period=3,
    )
    defaults.update(overrides)
    return Settings(**defaults)


def _make_daily_bars(n: int = 10) -> list[Bar]:
    return [
        Bar(
            symbol="AAPL",
            timestamp=datetime(2026, 1, 1, 21, 0, tzinfo=timezone.utc),
            open=99.0, high=101.0 + i * 0.1, low=98.0, close=100.5 + i * 0.1,
            volume=1_000_000.0,
        )
        for i in range(n)
    ]


def _make_pullback_bars(
    n_trend: int = 8,
    trend_close: float = 110.0,
    pullback_close: float = 108.0,
    signal_close: float = 112.0,
    signal_volume: float = 200_000.0,
) -> tuple[list[Bar], int]:
    """
    Build a bar sequence with an uptrend, one pullback bar below EMA, then a recovery bar.

    Pattern: n_trend rising bars → 1 pullback bar (close below EMA) → 1 signal bar (close above EMA)
    """
    ny = ZoneInfo("America/New_York")
    base = datetime(2026, 1, 2, 10, 0, tzinfo=ny)
    bars: list[Bar] = []

    # Uptrend bars — gently rising so EMA < close throughout
    for i in range(n_trend):
        ts = base + timedelta(minutes=15 * i)
        c = trend_close + i * 0.5
        bars.append(Bar(
            symbol="AAPL", timestamp=ts,
            open=c - 0.3, high=c + 0.5, low=c - 0.5, close=c,
            volume=50_000.0,
        ))

    # Pullback bar — closes below trend to trigger pullback detection
    pullback_ts = base + timedelta(minutes=15 * n_trend)
    bars.append(Bar(
        symbol="AAPL", timestamp=pullback_ts,
        open=pullback_close + 0.2, high=pullback_close + 0.5,
        low=pullback_close - 1.0, close=pullback_close,
        volume=50_000.0,
    ))

    # Signal bar — closes well above EMA
    signal_ts = base + timedelta(minutes=15 * (n_trend + 1))
    bars.append(Bar(
        symbol="AAPL", timestamp=signal_ts,
        open=signal_close - 0.5, high=signal_close + 1.0,
        low=signal_close - 1.0, close=signal_close,
        volume=signal_volume,
    ))

    return bars, len(bars) - 1


def test_ema_pullback_returns_signal_when_all_conditions_met():
    settings = _make_settings()
    daily_bars = _make_daily_bars()
    intraday_bars, signal_index = _make_pullback_bars(trend_close=110.0, pullback_close=106.0, signal_close=115.0)
    result = evaluate_ema_pullback_signal(
        symbol="AAPL",
        intraday_bars=intraday_bars,
        signal_index=signal_index,
        daily_bars=daily_bars,
        settings=settings,
    )
    assert result is not None
    assert isinstance(result, EntrySignal)
    assert result.symbol == "AAPL"


def test_ema_pullback_entry_level_is_current_ema():
    settings = _make_settings(ema_period=3)
    daily_bars = _make_daily_bars()
    intraday_bars, signal_index = _make_pullback_bars(trend_close=110.0, pullback_close=106.0, signal_close=115.0)
    _, current_ema = _calculate_ema(intraday_bars, signal_index, 3)
    result = evaluate_ema_pullback_signal(
        symbol="AAPL",
        intraday_bars=intraday_bars,
        signal_index=signal_index,
        daily_bars=daily_bars,
        settings=settings,
    )
    assert result is not None
    assert result.entry_level == round(current_ema, 2)


def test_ema_pullback_stop_below_prior_bar_low():
    settings = _make_settings()
    daily_bars = _make_daily_bars()
    intraday_bars, signal_index = _make_pullback_bars(trend_close=110.0, pullback_close=106.0, signal_close=115.0)
    prior_bar_low = intraday_bars[signal_index - 1].low
    result = evaluate_ema_pullback_signal(
        symbol="AAPL",
        intraday_bars=intraday_bars,
        signal_index=signal_index,
        daily_bars=daily_bars,
        settings=settings,
    )
    assert result is not None
    assert result.initial_stop_price < prior_bar_low


def test_ema_pullback_returns_none_when_no_pullback_occurred():
    """Gently rising bars: EMA lags price so prior_bar.close > prior_ema throughout."""
    settings = _make_settings(ema_period=3)
    daily_bars = _make_daily_bars()
    ny = ZoneInfo("America/New_York")
    base = datetime(2026, 1, 2, 10, 0, tzinfo=ny)
    # Strictly rising — close always above EMA, no pullback bar
    bars = [
        Bar(
            symbol="AAPL",
            timestamp=base + timedelta(minutes=15 * i),
            open=100.0 + i * 0.5 - 0.3,
            high=100.0 + i * 0.5 + 0.5,
            low=100.0 + i * 0.5 - 0.5,
            close=100.0 + i * 0.5,
            volume=200_000.0,
        )
        for i in range(10)
    ]
    result = evaluate_ema_pullback_signal(
        symbol="AAPL",
        intraday_bars=bars,
        signal_index=9,
        daily_bars=daily_bars,
        settings=settings,
    )
    assert result is None


def test_ema_pullback_returns_none_when_signal_close_below_ema():
    settings = _make_settings()
    daily_bars = _make_daily_bars()
    # Use pullback_close and signal_close both low so signal doesn't cross back above EMA
    intraday_bars, signal_index = _make_pullback_bars(
        trend_close=110.0, pullback_close=106.0, signal_close=107.0
    )
    result = evaluate_ema_pullback_signal(
        symbol="AAPL",
        intraday_bars=intraday_bars,
        signal_index=signal_index,
        daily_bars=daily_bars,
        settings=settings,
    )
    assert result is None


def test_ema_pullback_returns_none_before_warmup():
    settings = _make_settings(ema_period=9)
    daily_bars = _make_daily_bars()
    # signal_index=5 < ema_period=9 → warmup guard fires
    intraday_bars, _ = _make_pullback_bars(n_trend=4, trend_close=110.0, pullback_close=106.0, signal_close=115.0)
    result = evaluate_ema_pullback_signal(
        symbol="AAPL",
        intraday_bars=intraday_bars,
        signal_index=5,
        daily_bars=daily_bars,
        settings=settings,
    )
    assert result is None


def test_ema_pullback_returns_none_when_volume_below_threshold():
    settings = _make_settings(relative_volume_threshold=1.5)
    daily_bars = _make_daily_bars()
    intraday_bars, signal_index = _make_pullback_bars(
        trend_close=110.0, pullback_close=106.0, signal_close=115.0,
        signal_volume=10_000.0,
    )
    result = evaluate_ema_pullback_signal(
        symbol="AAPL",
        intraday_bars=intraday_bars,
        signal_index=signal_index,
        daily_bars=daily_bars,
        settings=settings,
    )
    assert result is None


def test_detect_ema_pullback_returns_true_when_prior_close_at_or_below_ema():
    """Unit test for _detect_ema_pullback directly."""
    ny = ZoneInfo("America/New_York")
    base = datetime(2026, 1, 2, 10, 0, tzinfo=ny)
    # Rising bars then one deep dip
    bars = [
        Bar(symbol="AAPL", timestamp=base + timedelta(minutes=15 * i),
            open=110.0, high=111.0, low=109.0, close=110.0 + i * 0.5,
            volume=50_000.0)
        for i in range(8)
    ]
    # Replace last bar with a dip well below EMA
    bars[-1] = Bar(symbol="AAPL", timestamp=base + timedelta(minutes=15 * 7),
                   open=100.0, high=101.0, low=99.0, close=100.0, volume=50_000.0)
    # Signal bar at index 8
    bars.append(Bar(symbol="AAPL", timestamp=base + timedelta(minutes=15 * 8),
                    open=115.0, high=116.0, low=114.0, close=115.0, volume=200_000.0))
    assert _detect_ema_pullback(bars, signal_index=8, ema_period=3) is True


def test_ema_pullback_initial_stop_uses_atr_when_enough_daily_bars():
    from alpaca_bot.risk.atr import calculate_atr
    settings = _make_settings(atr_period=3)
    daily_bars = _make_daily_bars(n=10)  # 10 >= atr_period+1=4 → ATR computable
    intraday_bars, signal_index = _make_pullback_bars(
        trend_close=110.0, pullback_close=106.0, signal_close=115.0
    )

    atr = calculate_atr(daily_bars, 3)
    assert atr is not None
    prior_bar_low = intraday_bars[signal_index - 1].low  # pullback_close - 1.0 = 105.0
    expected_stop = round(prior_bar_low - max(0.01, 1.5 * atr), 2)

    result = evaluate_ema_pullback_signal(
        symbol="AAPL",
        intraday_bars=intraday_bars,
        signal_index=signal_index,
        daily_bars=daily_bars,
        settings=settings,
    )
    assert result is not None
    assert result.initial_stop_price == expected_stop


def test_ema_pullback_initial_stop_falls_back_to_buffer_pct_when_atr_returns_none():
    from alpaca_bot.risk.atr import calculate_atr
    settings = _make_settings(atr_period=3, daily_sma_period=3)
    daily_bars = _make_daily_bars(n=3)  # 3 < atr_period+1=4 → ATR returns None
    intraday_bars, signal_index = _make_pullback_bars(
        trend_close=110.0, pullback_close=106.0, signal_close=115.0
    )

    assert calculate_atr(daily_bars, 3) is None
    prior_bar_low = intraday_bars[signal_index - 1].low  # pullback_close - 1.0 = 105.0
    expected_stop = round(prior_bar_low - max(0.01, prior_bar_low * 0.001), 2)

    result = evaluate_ema_pullback_signal(
        symbol="AAPL",
        intraday_bars=intraday_bars,
        signal_index=signal_index,
        daily_bars=daily_bars,
        settings=settings,
    )
    assert result is not None
    assert result.initial_stop_price == expected_stop
