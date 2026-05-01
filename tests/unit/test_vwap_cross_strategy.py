from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from alpaca_bot.domain.models import Bar, EntrySignal
from alpaca_bot.strategy.vwap_cross import evaluate_vwap_cross_signal


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
    )
    defaults.update(overrides)
    return Settings(**defaults)


def _make_daily_bars(n: int = 10, prior_close: float = 100.0) -> list[Bar]:
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
    signal_ts: datetime | None = None,
    signal_close: float = 103.0,
    signal_volume: float = 20_000.0,
    prior_bar_close: float = 95.0,
    prior_bar_above_vwap: bool = False,
) -> tuple[list[Bar], int]:
    """
    5 baseline bars (yesterday) + 1 pre_prior today bar + 1 prior today bar + 1 signal bar.

    Scenario numbers:
      pre_prior: high=100, low=100, close=100 → tp=100.0, vol=10_000
      prior: high=100, low=96, close=95 → tp=97.0, vol=10_000
      prior_vwap = calculate_vwap([pre_prior]) = 100.0
      prior_bar_close=95 < prior_vwap=100.0 ✓
      signal: high=104, low=99, close=103 → tp=102.0, vol=20_000
      current_vwap = (100.0*10_000 + 97.0*10_000 + 102.0*20_000) / 40_000 = 100.25
      signal_bar.close=103 >= 100.25 ✓
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
    today_base = datetime(2026, 1, 2, 10, 0, tzinfo=ny)
    pre_prior_today = _make_bar(
        ts=today_base,
        open_=100.0,
        high=100.0,
        low=100.0,
        close=100.0,
        volume=10_000.0,
    )
    if prior_bar_above_vwap:
        # prior_bar.close=100 >= pre_prior_vwap=100.0 → not a cross from below (should reject)
        prior_today = _make_bar(
            ts=today_base + timedelta(minutes=15),
            open_=98.0,
            high=100.0,
            low=96.0,
            close=100.0,
            volume=10_000.0,
        )
    else:
        prior_today = _make_bar(
            ts=today_base + timedelta(minutes=15),
            open_=98.0,
            high=100.0,
            low=96.0,
            close=prior_bar_close,
            volume=10_000.0,
        )
    if signal_ts is None:
        signal_ts = today_base + timedelta(minutes=30)
    signal_bar = _make_bar(
        ts=signal_ts,
        open_=100.0,
        high=104.0,
        low=99.0,
        close=signal_close,
        volume=signal_volume,
    )
    bars = baseline_bars + [pre_prior_today, prior_today, signal_bar]
    return bars, len(bars) - 1


def test_vwap_cross_fires_when_all_conditions_met() -> None:
    settings = _make_settings()
    daily_bars = _make_daily_bars()
    intraday_bars, signal_index = _make_scenario()
    result = evaluate_vwap_cross_signal(
        symbol="AAPL",
        intraday_bars=intraday_bars,
        signal_index=signal_index,
        daily_bars=daily_bars,
        settings=settings,
    )
    assert result is not None
    assert isinstance(result, EntrySignal)
    assert result.symbol == "AAPL"


def test_vwap_cross_entry_level_equals_current_vwap_rounded() -> None:
    settings = _make_settings()
    daily_bars = _make_daily_bars()
    intraday_bars, signal_index = _make_scenario()
    result = evaluate_vwap_cross_signal(
        symbol="AAPL",
        intraday_bars=intraday_bars,
        signal_index=signal_index,
        daily_bars=daily_bars,
        settings=settings,
    )
    assert result is not None
    # current_vwap = (100.0*10_000 + 97.0*10_000 + 102.0*20_000) / 40_000 = 100.25
    assert result.entry_level == pytest.approx(100.25, abs=0.01)


def test_vwap_cross_initial_stop_below_signal_bar_low() -> None:
    settings = _make_settings(atr_period=3)
    daily_bars = _make_daily_bars()
    intraday_bars, signal_index = _make_scenario()
    result = evaluate_vwap_cross_signal(
        symbol="AAPL",
        intraday_bars=intraday_bars,
        signal_index=signal_index,
        daily_bars=daily_bars,
        settings=settings,
    )
    assert result is not None
    assert result.initial_stop_price < 99.0


def test_vwap_cross_returns_none_outside_entry_window() -> None:
    settings = _make_settings()
    daily_bars = _make_daily_bars()
    late_ts = datetime(2026, 1, 2, 16, 0, tzinfo=ZoneInfo("America/New_York"))
    intraday_bars, signal_index = _make_scenario(signal_ts=late_ts)
    result = evaluate_vwap_cross_signal(
        symbol="AAPL",
        intraday_bars=intraday_bars,
        signal_index=signal_index,
        daily_bars=daily_bars,
        settings=settings,
    )
    assert result is None


def test_vwap_cross_returns_none_when_trend_filter_fails() -> None:
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
    result = evaluate_vwap_cross_signal(
        symbol="AAPL",
        intraday_bars=intraday_bars,
        signal_index=signal_index,
        daily_bars=daily_bars,
        settings=settings,
    )
    assert result is None


def test_vwap_cross_returns_none_when_first_bar_of_day() -> None:
    """Signal is the first today bar — no prior today bar to check below VWAP."""
    settings = _make_settings()
    daily_bars = _make_daily_bars()
    ny = ZoneInfo("America/New_York")
    yesterday_base = datetime(2026, 1, 1, 10, 0, tzinfo=ny)
    baseline_bars = [
        _make_bar(
            ts=yesterday_base + timedelta(minutes=15 * i),
            open_=100.0,
            high=100.5,
            low=99.5,
            close=100.0,
        )
        for i in range(5)
    ]
    signal_bar = _make_bar(
        ts=datetime(2026, 1, 2, 10, 0, tzinfo=ny),
        open_=100.0,
        high=104.0,
        low=99.0,
        close=103.0,
        volume=20_000.0,
    )
    bars = baseline_bars + [signal_bar]
    result = evaluate_vwap_cross_signal(
        symbol="AAPL",
        intraday_bars=bars,
        signal_index=len(bars) - 1,
        daily_bars=daily_bars,
        settings=settings,
    )
    assert result is None


def test_vwap_cross_returns_none_when_prior_bar_above_vwap() -> None:
    """Prior bar already above VWAP — this is not a cross from below."""
    settings = _make_settings()
    daily_bars = _make_daily_bars()
    intraday_bars, signal_index = _make_scenario(prior_bar_above_vwap=True)
    result = evaluate_vwap_cross_signal(
        symbol="AAPL",
        intraday_bars=intraday_bars,
        signal_index=signal_index,
        daily_bars=daily_bars,
        settings=settings,
    )
    assert result is None


def test_vwap_cross_returns_none_when_signal_bar_below_vwap() -> None:
    """Signal bar close does not reach current VWAP — no cross."""
    settings = _make_settings()
    daily_bars = _make_daily_bars()
    # signal_close=90 which is well below current_vwap≈100.33
    intraday_bars, signal_index = _make_scenario(signal_close=90.0)
    result = evaluate_vwap_cross_signal(
        symbol="AAPL",
        intraday_bars=intraday_bars,
        signal_index=signal_index,
        daily_bars=daily_bars,
        settings=settings,
    )
    assert result is None


def test_vwap_cross_returns_none_when_volume_too_low() -> None:
    settings = _make_settings(relative_volume_threshold=3.0)
    # signal_volume=20_000, baseline_avg=10_000, rv=2.0 < 3.0 → reject
    daily_bars = _make_daily_bars()
    intraday_bars, signal_index = _make_scenario(signal_volume=20_000.0)
    result = evaluate_vwap_cross_signal(
        symbol="AAPL",
        intraday_bars=intraday_bars,
        signal_index=signal_index,
        daily_bars=daily_bars,
        settings=settings,
    )
    assert result is None


def test_vwap_cross_returns_none_when_only_one_prior_today_bar() -> None:
    """Only 2 today bars (pre_prior + signal) — prior bar missing, len < 3 → None."""
    settings = _make_settings()
    daily_bars = _make_daily_bars()
    ny = ZoneInfo("America/New_York")
    yesterday_base = datetime(2026, 1, 1, 10, 0, tzinfo=ny)
    baseline_bars = [
        _make_bar(
            ts=yesterday_base + timedelta(minutes=15 * i),
            open_=100.0,
            high=100.5,
            low=99.5,
            close=100.0,
        )
        for i in range(5)
    ]
    today_base = datetime(2026, 1, 2, 10, 0, tzinfo=ny)
    pre_prior = _make_bar(ts=today_base, open_=100.0, high=100.0, low=100.0, close=100.0)
    signal_bar = _make_bar(
        ts=today_base + timedelta(minutes=15),
        open_=100.0,
        high=104.0,
        low=99.0,
        close=103.0,
        volume=20_000.0,
    )
    bars = baseline_bars + [pre_prior, signal_bar]
    result = evaluate_vwap_cross_signal(
        symbol="AAPL",
        intraday_bars=bars,
        signal_index=len(bars) - 1,
        daily_bars=daily_bars,
        settings=settings,
    )
    assert result is None


def test_vwap_cross_fires_when_signal_close_equals_vwap() -> None:
    """signal_close exactly equals current_vwap — the >= check must fire the signal."""
    settings = _make_settings()
    daily_bars = _make_daily_bars()
    # With pre_prior tp=100.0, prior tp=97.0, signal(close=99.7) tp=100.9:
    # current_vwap = (100*10000 + 97*10000 + 100.9*20000) / 40000 = 99.7 = signal_close
    intraday_bars, signal_index = _make_scenario(signal_close=99.7)
    result = evaluate_vwap_cross_signal(
        symbol="AAPL",
        intraday_bars=intraday_bars,
        signal_index=signal_index,
        daily_bars=daily_bars,
        settings=settings,
    )
    assert result is not None
    assert result.entry_level == pytest.approx(99.7, abs=0.01)


def test_vwap_cross_returns_none_when_atr_insufficient() -> None:
    settings = _make_settings(atr_period=3, daily_sma_period=2)
    daily_bars = _make_daily_bars(n=3)  # 3 < atr_period+1=4 → ATR None
    intraday_bars, signal_index = _make_scenario()
    result = evaluate_vwap_cross_signal(
        symbol="AAPL",
        intraday_bars=intraday_bars,
        signal_index=signal_index,
        daily_bars=daily_bars,
        settings=settings,
    )
    assert result is None


def test_vwap_cross_in_strategy_registry() -> None:
    from alpaca_bot.strategy import STRATEGY_REGISTRY

    assert "vwap_cross" in STRATEGY_REGISTRY
