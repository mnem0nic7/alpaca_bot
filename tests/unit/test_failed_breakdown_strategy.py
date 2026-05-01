from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from alpaca_bot.domain.models import Bar, EntrySignal
from alpaca_bot.strategy.failed_breakdown import evaluate_failed_breakdown_signal


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
        failed_breakdown_volume_ratio=2.0,
        failed_breakdown_recapture_buffer_pct=0.001,
    )
    defaults.update(overrides)
    return Settings(**defaults)


def _make_daily_bars(n: int = 10, prior_close: float = 100.0, prior_low: float = 99.0) -> list[Bar]:
    bars = []
    for i in range(n - 1):
        bars.append(
            Bar(
                symbol="AAPL",
                timestamp=datetime(2026, 1, 1, 21, 0, tzinfo=timezone.utc),
                open=prior_close - 1.0 + i * 0.1,
                high=prior_close + i * 0.1,
                low=prior_close - 2.0,
                close=prior_close - 0.5 + i * 0.1,
                volume=1_000_000.0,
            )
        )
    bars.append(
        Bar(
            symbol="AAPL",
            timestamp=datetime(2026, 1, 1, 21, 0, tzinfo=timezone.utc),
            open=prior_close - 0.5,
            high=prior_close + 0.5,
            low=prior_low,
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
    signal_ts: datetime | None = None,
    signal_low: float = 97.5,
    signal_close: float = 100.5,
    signal_high: float = 102.0,
    signal_volume: float = 30_000.0,
    prior_low: float = 99.0,
) -> tuple[list[Bar], int]:
    """
    5 baseline lookback bars (yesterday) + 1 signal bar.

    prior_session_low = 99.0 (from daily_bars[-1].low, set separately).
    signal_bar.low=97.5 < 99.0 → breakdown ✓
    signal_bar.close=100.5 >= 99.0 * 1.001 = 99.099 → recapture ✓
    relative_volume = 30_000 / 10_000 = 3.0 >= 2.0 ✓
    """
    ny = ZoneInfo("America/New_York")
    yesterday_base = datetime(2026, 1, 1, 10, 0, tzinfo=ny)
    baseline_bars = [
        _make_bar(
            ts=yesterday_base + timedelta(minutes=15 * i),
            open_=100.0,
            high=100.5,
            low=99.5,
            close=100.0,
            volume=10_000.0,
        )
        for i in range(5)
    ]
    if signal_ts is None:
        signal_ts = datetime(2026, 1, 2, 10, 0, tzinfo=ny)
    signal_bar = _make_bar(
        ts=signal_ts,
        open_=100.0,
        high=signal_high,
        low=signal_low,
        close=signal_close,
        volume=signal_volume,
    )
    bars = baseline_bars + [signal_bar]
    return bars, len(bars) - 1


def test_failed_breakdown_fires_when_all_conditions_met() -> None:
    settings = _make_settings()
    daily_bars = _make_daily_bars(prior_low=99.0)
    intraday_bars, signal_index = _make_scenario()
    result = evaluate_failed_breakdown_signal(
        symbol="AAPL",
        intraday_bars=intraday_bars,
        signal_index=signal_index,
        daily_bars=daily_bars,
        settings=settings,
    )
    assert result is not None
    assert isinstance(result, EntrySignal)
    assert result.symbol == "AAPL"


def test_failed_breakdown_entry_level_equals_prior_session_low() -> None:
    settings = _make_settings()
    daily_bars = _make_daily_bars(prior_low=99.0)
    intraday_bars, signal_index = _make_scenario()
    result = evaluate_failed_breakdown_signal(
        symbol="AAPL",
        intraday_bars=intraday_bars,
        signal_index=signal_index,
        daily_bars=daily_bars,
        settings=settings,
    )
    assert result is not None
    assert result.entry_level == pytest.approx(99.0)


def test_failed_breakdown_initial_stop_below_signal_bar_low() -> None:
    settings = _make_settings(atr_period=3)
    daily_bars = _make_daily_bars(prior_low=99.0)
    intraday_bars, signal_index = _make_scenario(signal_low=97.5)
    result = evaluate_failed_breakdown_signal(
        symbol="AAPL",
        intraday_bars=intraday_bars,
        signal_index=signal_index,
        daily_bars=daily_bars,
        settings=settings,
    )
    assert result is not None
    assert result.initial_stop_price < 97.5


def test_failed_breakdown_returns_none_outside_entry_window() -> None:
    settings = _make_settings()
    daily_bars = _make_daily_bars(prior_low=99.0)
    late_ts = datetime(2026, 1, 2, 16, 0, tzinfo=ZoneInfo("America/New_York"))
    intraday_bars, signal_index = _make_scenario(signal_ts=late_ts)
    result = evaluate_failed_breakdown_signal(
        symbol="AAPL",
        intraday_bars=intraday_bars,
        signal_index=signal_index,
        daily_bars=daily_bars,
        settings=settings,
    )
    assert result is None


def test_failed_breakdown_returns_none_when_trend_filter_fails() -> None:
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
    result = evaluate_failed_breakdown_signal(
        symbol="AAPL",
        intraday_bars=intraday_bars,
        signal_index=signal_index,
        daily_bars=daily_bars,
        settings=settings,
    )
    assert result is None


def test_failed_breakdown_returns_none_when_no_prior_daily_bars() -> None:
    """All daily bars are from today — trend filter passes, but prior_daily=[] fires."""
    settings = _make_settings(daily_sma_period=2)
    # 5 today-dated bars with increasing closes so trend filter passes
    today_bars = [
        Bar(
            symbol="AAPL",
            timestamp=datetime(2026, 1, 2, 21, 0, tzinfo=timezone.utc),
            open=99.0 + i * 0.5,
            high=99.5 + i * 0.5,
            low=98.5 + i * 0.5,
            close=99.5 + i * 0.5,
            volume=1_000_000.0,
        )
        for i in range(5)
    ]
    intraday_bars, signal_index = _make_scenario()
    result = evaluate_failed_breakdown_signal(
        symbol="AAPL",
        intraday_bars=intraday_bars,
        signal_index=signal_index,
        daily_bars=today_bars,
        settings=settings,
    )
    assert result is None


def test_failed_breakdown_returns_none_when_no_breakdown() -> None:
    """signal_bar.low >= prior_session_low → no breakdown → None."""
    settings = _make_settings()
    daily_bars = _make_daily_bars(prior_low=99.0)
    # signal_low=99.5 >= prior_session_low=99.0 → no breakdown
    intraday_bars, signal_index = _make_scenario(signal_low=99.5)
    result = evaluate_failed_breakdown_signal(
        symbol="AAPL",
        intraday_bars=intraday_bars,
        signal_index=signal_index,
        daily_bars=daily_bars,
        settings=settings,
    )
    assert result is None


def test_failed_breakdown_returns_none_when_recapture_fails() -> None:
    """signal_bar.close < prior_low * (1+buffer) → didn't recapture → None."""
    settings = _make_settings(failed_breakdown_recapture_buffer_pct=0.01)
    daily_bars = _make_daily_bars(prior_low=99.0)
    # Need close >= 99.0 * 1.01 = 99.99; set close=99.5 < 99.99 → fail
    intraday_bars, signal_index = _make_scenario(signal_close=99.5)
    result = evaluate_failed_breakdown_signal(
        symbol="AAPL",
        intraday_bars=intraday_bars,
        signal_index=signal_index,
        daily_bars=daily_bars,
        settings=settings,
    )
    assert result is None


def test_failed_breakdown_returns_none_when_volume_too_low() -> None:
    settings = _make_settings(failed_breakdown_volume_ratio=5.0)
    # signal_volume=30_000, avg=10_000 → rv=3.0 < 5.0 → reject
    daily_bars = _make_daily_bars(prior_low=99.0)
    intraday_bars, signal_index = _make_scenario()
    result = evaluate_failed_breakdown_signal(
        symbol="AAPL",
        intraday_bars=intraday_bars,
        signal_index=signal_index,
        daily_bars=daily_bars,
        settings=settings,
    )
    assert result is None


def test_failed_breakdown_returns_none_when_atr_insufficient() -> None:
    settings = _make_settings(atr_period=3, daily_sma_period=2)
    daily_bars = _make_daily_bars(n=3, prior_low=99.0)  # 3 < atr_period+1=4 → ATR None
    intraday_bars, signal_index = _make_scenario()
    result = evaluate_failed_breakdown_signal(
        symbol="AAPL",
        intraday_bars=intraday_bars,
        signal_index=signal_index,
        daily_bars=daily_bars,
        settings=settings,
    )
    assert result is None


def test_failed_breakdown_in_strategy_registry() -> None:
    from alpaca_bot.strategy import STRATEGY_REGISTRY

    assert "failed_breakdown" in STRATEGY_REGISTRY


def test_settings_has_failed_breakdown_volume_ratio() -> None:
    settings = _make_settings(failed_breakdown_volume_ratio=3.0)
    assert settings.failed_breakdown_volume_ratio == pytest.approx(3.0)


def test_settings_validates_failed_breakdown_volume_ratio_zero() -> None:
    with pytest.raises(ValueError, match="FAILED_BREAKDOWN_VOLUME_RATIO"):
        _make_settings(failed_breakdown_volume_ratio=0.0)


def test_settings_has_failed_breakdown_recapture_buffer_pct() -> None:
    settings = _make_settings(failed_breakdown_recapture_buffer_pct=0.005)
    assert settings.failed_breakdown_recapture_buffer_pct == pytest.approx(0.005)


def test_settings_validates_failed_breakdown_recapture_buffer_pct_zero() -> None:
    with pytest.raises(ValueError, match="FAILED_BREAKDOWN_RECAPTURE_BUFFER_PCT"):
        _make_settings(failed_breakdown_recapture_buffer_pct=0.0)


def test_settings_validates_failed_breakdown_recapture_buffer_pct_too_large() -> None:
    with pytest.raises(ValueError, match="FAILED_BREAKDOWN_RECAPTURE_BUFFER_PCT"):
        _make_settings(failed_breakdown_recapture_buffer_pct=1.0)
