from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from alpaca_bot.domain.models import Bar, EntrySignal
from alpaca_bot.strategy.vwap_reversion import _calculate_vwap, evaluate_vwap_reversion_signal


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
        vwap_dip_threshold_pct=0.015,
    )
    defaults.update(overrides)
    return Settings(**defaults)


def _make_daily_bars(n: int = 10, high: float = 100.0) -> list[Bar]:
    return [
        Bar(
            symbol="AAPL",
            timestamp=datetime(2026, 1, 1, 21, 0, tzinfo=timezone.utc),
            open=high - 1.0 + i * 0.1,
            high=high + i * 0.1,
            low=high - 2.0,
            close=high - 0.5 + i * 0.1,
            volume=1_000_000.0,
        )
        for i in range(n)
    ]


def _make_bar(
    ts: datetime,
    high: float,
    low: float,
    close: float,
    volume: float = 10_000.0,
) -> Bar:
    return Bar(
        symbol="AAPL",
        timestamp=ts,
        open=(high + low) / 2,
        high=high,
        low=low,
        close=close,
        volume=volume,
    )


def _base_ts(hour: int = 10, minute: int = 0) -> datetime:
    return datetime(2026, 1, 2, hour, minute, tzinfo=ZoneInfo("America/New_York"))


def _make_scenario(
    *,
    signal_low: float = 97.0,
    signal_close: float = 101.5,
    signal_high: float = 103.0,
    signal_volume: float = 100_000.0,
    n_prior_today: int = 5,
    prior_today_close: float = 100.0,
    signal_ts: datetime | None = None,
) -> tuple[list[Bar], int]:
    """Build intraday bars: n_prior_today yesterday bars + 5 today bars + 1 signal bar.

    Yesterday bars provide the relative-volume baseline; today-prior bars establish
    VWAP context; signal bar is today within the entry window.
    """
    ny = ZoneInfo("America/New_York")
    yesterday_base = datetime(2026, 1, 1, 10, 0, tzinfo=ny)
    prior_bars = [
        _make_bar(
            ts=yesterday_base + timedelta(minutes=15 * i),
            high=prior_today_close + 0.5,
            low=prior_today_close - 0.5,
            close=prior_today_close,
            volume=10_000.0,
        )
        for i in range(n_prior_today)
    ]
    today_base = datetime(2026, 1, 2, 10, 0, tzinfo=ny)
    today_prior_bars = [
        _make_bar(
            ts=today_base + timedelta(minutes=15 * i),
            high=prior_today_close + 0.5,
            low=prior_today_close - 0.5,
            close=prior_today_close,
            volume=10_000.0,
        )
        for i in range(5)
    ]
    if signal_ts is None:
        signal_ts = today_base + timedelta(minutes=15 * 5)
    signal_bar = _make_bar(
        ts=signal_ts,
        high=signal_high,
        low=signal_low,
        close=signal_close,
        volume=signal_volume,
    )
    bars = prior_bars + today_prior_bars + [signal_bar]
    return bars, len(bars) - 1


def test_vwap_reversion_fires_when_all_conditions_met():
    settings = _make_settings()
    daily_bars = _make_daily_bars(n=10)
    intraday_bars, signal_index = _make_scenario()
    result = evaluate_vwap_reversion_signal(
        symbol="AAPL",
        intraday_bars=intraday_bars,
        signal_index=signal_index,
        daily_bars=daily_bars,
        settings=settings,
    )
    assert result is not None
    assert isinstance(result, EntrySignal)
    assert result.symbol == "AAPL"


def test_vwap_reversion_entry_level_is_vwap():
    settings = _make_settings()
    daily_bars = _make_daily_bars(n=10)
    intraday_bars, signal_index = _make_scenario()
    result = evaluate_vwap_reversion_signal(
        symbol="AAPL",
        intraday_bars=intraday_bars,
        signal_index=signal_index,
        daily_bars=daily_bars,
        settings=settings,
    )
    assert result is not None
    today_bars = intraday_bars[5 : signal_index + 1]  # skip 5 yesterday bars
    expected_vwap = _calculate_vwap(today_bars)
    assert expected_vwap is not None
    assert result.entry_level == round(expected_vwap, 2)


def test_vwap_reversion_initial_stop_below_signal_low():
    settings = _make_settings(atr_period=3)
    daily_bars = _make_daily_bars(n=10)
    intraday_bars, signal_index = _make_scenario(signal_low=97.0)
    result = evaluate_vwap_reversion_signal(
        symbol="AAPL",
        intraday_bars=intraday_bars,
        signal_index=signal_index,
        daily_bars=daily_bars,
        settings=settings,
    )
    assert result is not None
    assert result.initial_stop_price < 97.0


def test_vwap_reversion_returns_none_outside_entry_window():
    settings = _make_settings()
    daily_bars = _make_daily_bars(n=10)
    late_ts = datetime(2026, 1, 2, 16, 0, tzinfo=ZoneInfo("America/New_York"))
    intraday_bars, signal_index = _make_scenario(signal_ts=late_ts)
    result = evaluate_vwap_reversion_signal(
        symbol="AAPL",
        intraday_bars=intraday_bars,
        signal_index=signal_index,
        daily_bars=daily_bars,
        settings=settings,
    )
    assert result is None


def test_vwap_reversion_returns_none_when_trend_filter_fails():
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
    result = evaluate_vwap_reversion_signal(
        symbol="AAPL",
        intraday_bars=intraday_bars,
        signal_index=signal_index,
        daily_bars=daily_bars,
        settings=settings,
    )
    assert result is None


def test_vwap_reversion_returns_none_when_low_does_not_dip_below_vwap():
    """Bar low stays above the dip threshold — no meaningful reversion candidate."""
    settings = _make_settings(vwap_dip_threshold_pct=0.015)
    daily_bars = _make_daily_bars(n=10)
    # VWAP ≈ 100; bar low = 99.5 → only 0.5% below VWAP < 1.5% threshold
    intraday_bars, signal_index = _make_scenario(
        signal_low=99.5, signal_close=101.0, signal_high=103.0
    )
    result = evaluate_vwap_reversion_signal(
        symbol="AAPL",
        intraday_bars=intraday_bars,
        signal_index=signal_index,
        daily_bars=daily_bars,
        settings=settings,
    )
    assert result is None


def test_vwap_reversion_returns_none_when_close_below_vwap():
    """Bar dips below VWAP but close does not recover — no reversion confirmed."""
    settings = _make_settings()
    daily_bars = _make_daily_bars(n=10)
    # VWAP ≈ 99.1 with low=97; close=98.0 stays below VWAP → no reversion confirmed
    intraday_bars, signal_index = _make_scenario(
        signal_low=97.0, signal_close=98.0, signal_high=101.0
    )
    result = evaluate_vwap_reversion_signal(
        symbol="AAPL",
        intraday_bars=intraday_bars,
        signal_index=signal_index,
        daily_bars=daily_bars,
        settings=settings,
    )
    assert result is None


def test_vwap_reversion_returns_none_when_volume_below_threshold():
    settings = _make_settings(relative_volume_threshold=1.5)
    daily_bars = _make_daily_bars(n=10)
    # Prior bars volume = 10_000; signal volume = 5_000 → rv = 0.5 < 1.5
    intraday_bars, signal_index = _make_scenario(signal_volume=5_000.0)
    result = evaluate_vwap_reversion_signal(
        symbol="AAPL",
        intraday_bars=intraday_bars,
        signal_index=signal_index,
        daily_bars=daily_bars,
        settings=settings,
    )
    assert result is None


def test_vwap_reversion_returns_none_when_atr_insufficient():
    from alpaca_bot.risk.atr import calculate_atr

    settings = _make_settings(atr_period=3, daily_sma_period=2)
    daily_bars = _make_daily_bars(n=3)  # 3 < atr_period+1=4 → ATR is None
    assert calculate_atr(daily_bars, 3) is None
    intraday_bars, signal_index = _make_scenario()
    result = evaluate_vwap_reversion_signal(
        symbol="AAPL",
        intraday_bars=intraday_bars,
        signal_index=signal_index,
        daily_bars=daily_bars,
        settings=settings,
    )
    assert result is None


def test_vwap_reversion_in_strategy_registry():
    from alpaca_bot.strategy import STRATEGY_REGISTRY

    assert "vwap_reversion" in STRATEGY_REGISTRY


def test_settings_has_vwap_dip_threshold_pct():
    settings = _make_settings(vwap_dip_threshold_pct=0.02)
    assert settings.vwap_dip_threshold_pct == pytest.approx(0.02)


def test_settings_validates_vwap_dip_threshold_pct_zero():
    with pytest.raises(ValueError, match="VWAP_DIP_THRESHOLD_PCT"):
        _make_settings(vwap_dip_threshold_pct=0.0)


def test_settings_validates_vwap_dip_threshold_pct_too_large():
    with pytest.raises(ValueError, match="VWAP_DIP_THRESHOLD_PCT"):
        _make_settings(vwap_dip_threshold_pct=1.0)
