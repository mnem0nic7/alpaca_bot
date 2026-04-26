from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from alpaca_bot.domain.models import Bar, EntrySignal
from alpaca_bot.strategy.orb import evaluate_orb_signal


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
        orb_opening_bars=2,
    )
    defaults.update(overrides)
    return Settings(**defaults)


def _make_daily_bars(n: int = 10, high: float = 100.0) -> list[Bar]:
    return [
        Bar(
            symbol="AAPL",
            timestamp=datetime(2026, 1, 1, 21, 0, tzinfo=timezone.utc),
            open=high - 1.0,
            high=high + i * 0.1,
            low=high - 2.0,
            close=high - 0.5 + i * 0.1,
            volume=1_000_000.0,
        )
        for i in range(n)
    ]


def _make_bar(
    symbol: str,
    ts: datetime,
    high: float,
    low: float,
    close: float,
    volume: float = 100_000.0,
) -> Bar:
    return Bar(
        symbol=symbol,
        timestamp=ts,
        open=close - 0.5,
        high=high,
        low=low,
        close=close,
        volume=volume,
    )


def _make_intraday_bars_with_orb(
    orb_high: float = 105.0,
    orb_low: float = 99.0,
    signal_high: float = 107.0,
    signal_close: float = 106.5,
    signal_volume: float = 200_000.0,
    orb_opening_bars: int = 2,
    include_prior_day: bool = True,
    within_opening_range: bool = False,
) -> tuple[list[Bar], int]:
    """
    Build a list of intraday bars spanning yesterday + today.

    Returns (bars, signal_index) where signal_index points to the signal bar.
    If within_opening_range=True, the signal bar is placed inside the opening range
    (orb_opening_bars+1 = 4, signal at 3rd today bar so len(today_bars)=3 <= 4).
    """
    ny = ZoneInfo("America/New_York")
    bars: list[Bar] = []

    if include_prior_day:
        # 3 yesterday bars starting at 10:00 AM Jan 1
        yesterday_base = datetime(2026, 1, 1, 10, 0, tzinfo=ny)
        for i in range(3):
            ts = yesterday_base + timedelta(minutes=15 * i)
            bars.append(_make_bar("AAPL", ts, high=103.0, low=97.0, close=102.0, volume=80_000.0))

    # Today starts at 9:30 AM
    today_base = datetime(2026, 1, 2, 9, 30, tzinfo=ny)

    if within_opening_range:
        # Add orb_opening_bars-1 range bars, then signal as the last range bar at 10:00
        # → len(today_bars) == orb_opening_bars ≤ orb_opening_bars → returns None
        for i in range(orb_opening_bars - 1):
            ts = today_base + timedelta(minutes=15 * i)
            h = orb_high if i == 0 else orb_high - 1.0
            l = orb_low if i == 1 else orb_low + 1.0
            bars.append(_make_bar("AAPL", ts, high=h, low=l, close=(h + l) / 2, volume=50_000.0))
        # Signal at 10:00 AM (within entry window) — still inside the opening range by count
        signal_ts = today_base + timedelta(minutes=15 * (orb_opening_bars - 1))
        bars.append(
            _make_bar("AAPL", signal_ts, high=signal_high, low=signal_close - 1.0,
                      close=signal_close, volume=signal_volume)
        )
    else:
        # Full opening range bars followed by signal bar within entry window
        for i in range(orb_opening_bars):
            ts = today_base + timedelta(minutes=15 * i)
            h = orb_high if i == 0 else orb_high - 1.0
            l = orb_low if i == 1 else orb_low + 1.0
            bars.append(_make_bar("AAPL", ts, high=h, low=l, close=(h + l) / 2, volume=50_000.0))
        signal_ts = today_base + timedelta(minutes=15 * orb_opening_bars)
        bars.append(
            _make_bar("AAPL", signal_ts, high=signal_high, low=signal_close - 1.0,
                      close=signal_close, volume=signal_volume)
        )

    signal_index = len(bars) - 1
    return bars, signal_index


def test_orb_returns_signal_when_all_conditions_met():
    settings = _make_settings()
    daily_bars = _make_daily_bars(n=10)
    intraday_bars, signal_index = _make_intraday_bars_with_orb()
    result = evaluate_orb_signal(
        symbol="AAPL",
        intraday_bars=intraday_bars,
        signal_index=signal_index,
        daily_bars=daily_bars,
        settings=settings,
    )
    assert result is not None
    assert isinstance(result, EntrySignal)
    assert result.symbol == "AAPL"


def test_orb_entry_level_equals_opening_range_high():
    settings = _make_settings(orb_opening_bars=2)
    daily_bars = _make_daily_bars(n=10)
    intraday_bars, signal_index = _make_intraday_bars_with_orb(orb_high=105.0)
    result = evaluate_orb_signal(
        symbol="AAPL",
        intraday_bars=intraday_bars,
        signal_index=signal_index,
        daily_bars=daily_bars,
        settings=settings,
    )
    assert result is not None
    assert result.entry_level == 105.0


def test_orb_stop_below_opening_range_low():
    settings = _make_settings(orb_opening_bars=2)
    daily_bars = _make_daily_bars(n=10)
    intraday_bars, signal_index = _make_intraday_bars_with_orb(orb_low=99.0)
    result = evaluate_orb_signal(
        symbol="AAPL",
        intraday_bars=intraday_bars,
        signal_index=signal_index,
        daily_bars=daily_bars,
        settings=settings,
    )
    assert result is not None
    assert result.initial_stop_price < 99.0


def test_orb_returns_none_when_bar_does_not_cross_opening_range_high():
    settings = _make_settings()
    daily_bars = _make_daily_bars(n=10)
    intraday_bars, signal_index = _make_intraday_bars_with_orb(
        orb_high=105.0, signal_high=104.0, signal_close=103.5
    )
    result = evaluate_orb_signal(
        symbol="AAPL",
        intraday_bars=intraday_bars,
        signal_index=signal_index,
        daily_bars=daily_bars,
        settings=settings,
    )
    assert result is None


def test_orb_returns_none_when_close_below_opening_range_high():
    settings = _make_settings()
    daily_bars = _make_daily_bars(n=10)
    # high crosses but close stays below
    intraday_bars, signal_index = _make_intraday_bars_with_orb(
        orb_high=105.0, signal_high=106.0, signal_close=104.5
    )
    result = evaluate_orb_signal(
        symbol="AAPL",
        intraday_bars=intraday_bars,
        signal_index=signal_index,
        daily_bars=daily_bars,
        settings=settings,
    )
    assert result is None


def test_orb_returns_none_within_opening_range():
    # orb_opening_bars=3: adds 2 range bars + signal at 10:00; len(today_bars)=3 ≤ 3 → None
    settings = _make_settings(orb_opening_bars=3)
    daily_bars = _make_daily_bars(n=10)
    intraday_bars, signal_index = _make_intraday_bars_with_orb(
        orb_opening_bars=3, within_opening_range=True,
        signal_high=107.0, signal_close=106.5,
    )
    result = evaluate_orb_signal(
        symbol="AAPL",
        intraday_bars=intraday_bars,
        signal_index=signal_index,
        daily_bars=daily_bars,
        settings=settings,
    )
    assert result is None


def test_orb_returns_none_outside_entry_window():
    settings = _make_settings()
    daily_bars = _make_daily_bars(n=10)
    ny = ZoneInfo("America/New_York")
    intraday_bars, _ = _make_intraday_bars_with_orb()
    # replace signal bar with one at 16:00
    late_ts = datetime(2026, 1, 2, 16, 0, tzinfo=ny)
    intraday_bars[-1] = _make_bar("AAPL", late_ts, high=107.0, low=105.5, close=106.5, volume=200_000.0)
    result = evaluate_orb_signal(
        symbol="AAPL",
        intraday_bars=intraday_bars,
        signal_index=len(intraday_bars) - 1,
        daily_bars=daily_bars,
        settings=settings,
    )
    assert result is None


def test_orb_returns_none_when_volume_below_threshold():
    settings = _make_settings(relative_volume_threshold=1.5)
    daily_bars = _make_daily_bars(n=10)
    # opening range avg volume = 50_000; signal volume = 10_000 → rv = 0.2 < 1.5
    intraday_bars, signal_index = _make_intraday_bars_with_orb(signal_volume=10_000.0)
    result = evaluate_orb_signal(
        symbol="AAPL",
        intraday_bars=intraday_bars,
        signal_index=signal_index,
        daily_bars=daily_bars,
        settings=settings,
    )
    assert result is None


def test_orb_ignores_bars_from_prior_sessions():
    """
    Prior-day bars have a very high price range; if session filtering fails,
    the opening range high would be 200.0 and the signal would be rejected.
    With correct session filtering, opening_range_high = 105.0 and signal fires.
    """
    ny = ZoneInfo("America/New_York")
    settings = _make_settings(orb_opening_bars=2)
    daily_bars = _make_daily_bars(n=10)

    # yesterday bars with high=200 — should not count as today's opening range
    yesterday_base = datetime(2026, 1, 1, 10, 0, tzinfo=ny)
    prior_bars = [
        _make_bar("AAPL", yesterday_base + timedelta(minutes=15 * i),
                  high=200.0, low=195.0, close=198.0, volume=80_000.0)
        for i in range(3)
    ]

    # today's opening range with orb_high=105
    today_base = datetime(2026, 1, 2, 9, 30, tzinfo=ny)
    opening_bars = [
        _make_bar("AAPL", today_base + timedelta(minutes=15 * i),
                  high=105.0, low=99.0, close=102.0, volume=50_000.0)
        for i in range(2)
    ]
    signal_ts = today_base + timedelta(minutes=30)
    signal_bar = _make_bar("AAPL", signal_ts, high=107.0, low=105.5, close=106.5, volume=200_000.0)

    intraday_bars = prior_bars + opening_bars + [signal_bar]
    signal_index = len(intraday_bars) - 1

    result = evaluate_orb_signal(
        symbol="AAPL",
        intraday_bars=intraday_bars,
        signal_index=signal_index,
        daily_bars=daily_bars,
        settings=settings,
    )
    assert result is not None
    assert result.entry_level == 105.0
