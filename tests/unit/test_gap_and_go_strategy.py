from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from alpaca_bot.domain.models import Bar, EntrySignal
from alpaca_bot.strategy.gap_and_go import evaluate_gap_and_go_signal


def _make_settings(**overrides):
    from datetime import time

    from alpaca_bot.config import MarketDataFeed, Settings, TradingMode

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
        atr_period=3,
        gap_threshold_pct=0.02,
        gap_volume_threshold=2.0,
    )
    defaults.update(overrides)
    return Settings(**defaults)


def _make_daily_bars(
    n: int = 10,
    prior_close: float = 100.0,
    prior_high: float = 101.0,
) -> list[Bar]:
    bars = []
    for i in range(n - 1):
        bars.append(
            Bar(
                symbol="AAPL",
                timestamp=datetime(2026, 1, 1, 21, 0, tzinfo=timezone.utc),
                open=prior_close - 1.0 + i * 0.1,
                high=prior_close + i * 0.1,
                low=prior_close - 2.0,
                close=prior_close - 0.5 + i * 0.1,  # increasing so trend filter passes
                volume=1_000_000.0,
            )
        )
    # Last bar is yesterday — same Jan 1 UTC date so prior_daily filter includes it
    bars.append(
        Bar(
            symbol="AAPL",
            timestamp=datetime(2026, 1, 1, 21, 0, tzinfo=timezone.utc),
            open=prior_close - 0.5,
            high=prior_high,
            low=prior_close - 1.0,
            close=prior_close,
            volume=1_000_000.0,
        )
    )
    return bars


def _make_bar(
    ts: datetime,
    open_: float,
    high: float,
    low: float,
    close: float,
    volume: float = 10_000.0,
) -> Bar:
    return Bar(
        symbol="AAPL",
        timestamp=ts,
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=volume,
    )


def _make_scenario(
    *,
    prior_close: float = 100.0,
    prior_high: float = 101.0,
    gap_open: float = 103.5,  # > prior_close * 1.02 = 102.0
    signal_close: float = 102.5,  # > prior_high = 101.0
    signal_high: float = 104.0,
    signal_volume: float = 60_000.0,  # 60k vs avg 10k = 6× > gap_volume_threshold 2.0
    signal_ts: datetime | None = None,
    add_second_today_bar: bool = False,
) -> tuple[list[Bar], int]:
    """Build intraday bars: 5 yesterday bars (volume baseline) + first today bar (signal)."""
    ny = ZoneInfo("America/New_York")
    yesterday_base = datetime(2026, 1, 1, 10, 0, tzinfo=ny)
    prior_session_bars = [
        _make_bar(
            ts=yesterday_base + timedelta(minutes=15 * i),
            open_=prior_close - 0.5,
            high=prior_close + 0.5,
            low=prior_close - 0.5,
            close=prior_close,
            volume=10_000.0,
        )
        for i in range(5)
    ]
    if signal_ts is None:
        signal_ts = datetime(2026, 1, 2, 10, 0, tzinfo=ny)
    signal_bar = _make_bar(
        ts=signal_ts,
        open_=gap_open,
        high=signal_high,
        low=gap_open - 0.5,
        close=signal_close,
        volume=signal_volume,
    )
    bars = prior_session_bars + [signal_bar]
    if add_second_today_bar:
        second_ts = signal_ts + timedelta(minutes=15)
        bars.append(
            _make_bar(
                ts=second_ts,
                open_=signal_close,
                high=signal_high + 0.5,
                low=signal_close - 0.5,
                close=signal_close + 0.3,
                volume=signal_volume,
            )
        )
    signal_index = 5  # always the first today bar
    return bars, signal_index


def test_gap_and_go_fires_when_all_conditions_met():
    settings = _make_settings()
    daily_bars = _make_daily_bars(prior_close=100.0, prior_high=101.0)
    intraday_bars, signal_index = _make_scenario()
    result = evaluate_gap_and_go_signal(
        symbol="AAPL",
        intraday_bars=intraday_bars,
        signal_index=signal_index,
        daily_bars=daily_bars,
        settings=settings,
    )
    assert result is not None
    assert isinstance(result, EntrySignal)
    assert result.symbol == "AAPL"


def test_gap_and_go_entry_level_equals_prior_day_high():
    settings = _make_settings()
    daily_bars = _make_daily_bars(prior_close=100.0, prior_high=101.0)
    intraday_bars, signal_index = _make_scenario()
    result = evaluate_gap_and_go_signal(
        symbol="AAPL",
        intraday_bars=intraday_bars,
        signal_index=signal_index,
        daily_bars=daily_bars,
        settings=settings,
    )
    assert result is not None
    assert result.entry_level == 101.0


def test_gap_and_go_initial_stop_below_prior_day_high():
    settings = _make_settings(atr_period=3)
    daily_bars = _make_daily_bars(prior_close=100.0, prior_high=101.0)
    intraday_bars, signal_index = _make_scenario()
    result = evaluate_gap_and_go_signal(
        symbol="AAPL",
        intraday_bars=intraday_bars,
        signal_index=signal_index,
        daily_bars=daily_bars,
        settings=settings,
    )
    assert result is not None
    assert result.initial_stop_price < 101.0


def test_gap_and_go_returns_none_outside_entry_window():
    settings = _make_settings()
    daily_bars = _make_daily_bars()
    late_ts = datetime(2026, 1, 2, 16, 0, tzinfo=ZoneInfo("America/New_York"))
    intraday_bars, signal_index = _make_scenario(signal_ts=late_ts)
    result = evaluate_gap_and_go_signal(
        symbol="AAPL",
        intraday_bars=intraday_bars,
        signal_index=signal_index,
        daily_bars=daily_bars,
        settings=settings,
    )
    assert result is None


def test_gap_and_go_returns_none_when_trend_filter_fails():
    settings = _make_settings()
    daily_bars = [
        Bar(
            symbol="AAPL",
            timestamp=datetime(2026, 1, 1, 21, 0, tzinfo=timezone.utc),
            open=110.0,
            high=111.0,
            low=109.0,
            close=90.0,
            volume=1_000_000.0,
        )
        for _ in range(10)
    ]
    intraday_bars, signal_index = _make_scenario()
    result = evaluate_gap_and_go_signal(
        symbol="AAPL",
        intraday_bars=intraday_bars,
        signal_index=signal_index,
        daily_bars=daily_bars,
        settings=settings,
    )
    assert result is None


def test_gap_and_go_returns_none_when_gap_too_small():
    settings = _make_settings(gap_threshold_pct=0.02)
    daily_bars = _make_daily_bars(prior_close=100.0, prior_high=101.0)
    # gap_open = 101.5 → 101.5 / 100 = 1.015 < 1.02 → no gap
    intraday_bars, signal_index = _make_scenario(gap_open=101.5, signal_close=102.5)
    result = evaluate_gap_and_go_signal(
        symbol="AAPL",
        intraday_bars=intraday_bars,
        signal_index=signal_index,
        daily_bars=daily_bars,
        settings=settings,
    )
    assert result is None


def test_gap_and_go_returns_none_when_close_at_or_below_prior_day_high():
    settings = _make_settings()
    daily_bars = _make_daily_bars(prior_close=100.0, prior_high=101.0)
    # gap exists but close = prior_high = 101.0 → not strictly above
    intraday_bars, signal_index = _make_scenario(signal_close=101.0)
    result = evaluate_gap_and_go_signal(
        symbol="AAPL",
        intraday_bars=intraday_bars,
        signal_index=signal_index,
        daily_bars=daily_bars,
        settings=settings,
    )
    assert result is None


def test_gap_and_go_returns_none_when_volume_below_threshold():
    settings = _make_settings(gap_volume_threshold=2.0)
    daily_bars = _make_daily_bars()
    # Prior bar avg volume = 10_000; signal volume = 15_000 → rv = 1.5 < 2.0
    intraday_bars, signal_index = _make_scenario(signal_volume=15_000.0)
    result = evaluate_gap_and_go_signal(
        symbol="AAPL",
        intraday_bars=intraday_bars,
        signal_index=signal_index,
        daily_bars=daily_bars,
        settings=settings,
    )
    assert result is None


def test_gap_and_go_returns_none_when_not_first_bar_of_session():
    """Signal must only fire on the first bar of the day."""
    settings = _make_settings()
    daily_bars = _make_daily_bars()
    intraday_bars, _ = _make_scenario(add_second_today_bar=True)
    # Point signal_index at the second today bar (index 6)
    result = evaluate_gap_and_go_signal(
        symbol="AAPL",
        intraday_bars=intraday_bars,
        signal_index=6,
        daily_bars=daily_bars,
        settings=settings,
    )
    assert result is None


def test_gap_and_go_returns_none_when_atr_insufficient():
    from alpaca_bot.risk.atr import calculate_atr

    settings = _make_settings(atr_period=3, daily_sma_period=2)
    daily_bars = _make_daily_bars(n=3)  # 3 < atr_period+1=4 → ATR is None
    assert calculate_atr(daily_bars, 3) is None
    intraday_bars, signal_index = _make_scenario()
    result = evaluate_gap_and_go_signal(
        symbol="AAPL",
        intraday_bars=intraday_bars,
        signal_index=signal_index,
        daily_bars=daily_bars,
        settings=settings,
    )
    assert result is None


def test_gap_and_go_returns_none_when_no_prior_daily_bars():
    settings = _make_settings(daily_sma_period=2)
    # Only today's daily bar — trend filter fails (1 bar < sma_period+1=3)
    # so function returns None before the prior_daily check
    today_bar = Bar(
        symbol="AAPL",
        timestamp=datetime(2026, 1, 2, 21, 0, tzinfo=timezone.utc),
        open=103.0,
        high=105.0,
        low=100.0,
        close=103.5,
        volume=1_000_000.0,
    )
    intraday_bars, signal_index = _make_scenario()
    result = evaluate_gap_and_go_signal(
        symbol="AAPL",
        intraday_bars=intraday_bars,
        signal_index=signal_index,
        daily_bars=[today_bar],
        settings=settings,
    )
    assert result is None


def test_gap_and_go_in_strategy_registry():
    from alpaca_bot.strategy import STRATEGY_REGISTRY

    assert "gap_and_go" in STRATEGY_REGISTRY


def test_settings_has_gap_threshold_pct():
    settings = _make_settings(gap_threshold_pct=0.03)
    assert settings.gap_threshold_pct == pytest.approx(0.03)


def test_settings_validates_gap_threshold_pct_zero():
    with pytest.raises(ValueError, match="GAP_THRESHOLD_PCT"):
        _make_settings(gap_threshold_pct=0.0)


def test_settings_validates_gap_threshold_pct_too_large():
    with pytest.raises(ValueError, match="GAP_THRESHOLD_PCT"):
        _make_settings(gap_threshold_pct=1.0)


def test_settings_has_gap_volume_threshold():
    settings = _make_settings(gap_volume_threshold=3.0)
    assert settings.gap_volume_threshold == pytest.approx(3.0)


def test_settings_validates_gap_volume_threshold_zero():
    with pytest.raises(ValueError, match="GAP_VOLUME_THRESHOLD"):
        _make_settings(gap_volume_threshold=0.0)
