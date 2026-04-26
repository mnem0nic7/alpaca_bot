from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

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


def test_breakout_initial_stop_falls_back_to_buffer_pct_when_atr_returns_none():
    settings = _make_settings(atr_period=3, daily_sma_period=3)
    daily_bars = _make_daily_bars(n=3)  # 3 < atr_period+1=4 → ATR returns None
    intraday_bars, signal_index = _make_breakout_intraday_bars()

    assert calculate_atr(daily_bars, 3) is None
    breakout_level = 100.0
    expected_stop = round(breakout_level - max(0.01, breakout_level * 0.001), 2)

    result = evaluate_breakout_signal(
        symbol="AAPL",
        intraday_bars=intraday_bars,
        signal_index=signal_index,
        daily_bars=daily_bars,
        settings=settings,
    )
    assert result is not None
    assert result.initial_stop_price == expected_stop
