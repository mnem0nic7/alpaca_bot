from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from alpaca_bot.domain.models import Bar, EntrySignal
from alpaca_bot.strategy.bull_flag import evaluate_bull_flag_signal


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
    signal_volume: float = 5_000.0,
    signal_high: float = 108.0,
    signal_low: float = 107.5,
    signal_close: float = 109.0,
    pole_vol: tuple[float, float, float] = (30_000.0, 25_000.0, 22_000.0),
    baseline_volume: float = 10_000.0,
    only_signal_today: bool = False,
) -> tuple[list[Bar], int]:
    """5 baseline (yesterday) bars + 3 pole bars + 1 signal bar = 9 bars total."""
    ny = ZoneInfo("America/New_York")
    yesterday_base = datetime(2026, 1, 1, 10, 0, tzinfo=ny)
    # Baseline bars — all from Jan 1 (yesterday)
    baseline_bars = [
        _make_bar(
            ts=yesterday_base + timedelta(minutes=15 * i),
            open_=100.0,
            high=100.5,
            low=99.5,
            close=100.0,
            volume=baseline_volume,
        )
        for i in range(5)
    ]

    if signal_ts is None:
        signal_ts = datetime(2026, 1, 2, 11, 15, tzinfo=ny)

    today_base = datetime(2026, 1, 2, 10, 0, tzinfo=ny)

    if only_signal_today:
        signal_bar = _make_bar(
            ts=signal_ts,
            open_=108.0,
            high=signal_high,
            low=signal_low,
            close=signal_close,
            volume=signal_volume,
        )
        bars = baseline_bars + [signal_bar]
        return bars, len(bars) - 1

    # Pole bars — 3 bars building up strongly
    pole_bar_0 = _make_bar(
        ts=today_base,
        open_=100.0,
        high=104.0,
        low=99.0,
        close=103.0,
        volume=pole_vol[0],
    )
    pole_bar_1 = _make_bar(
        ts=today_base + timedelta(minutes=15),
        open_=103.0,
        high=107.0,
        low=102.0,
        close=106.0,
        volume=pole_vol[1],
    )
    pole_bar_2 = _make_bar(
        ts=today_base + timedelta(minutes=30),
        open_=106.0,
        high=109.0,
        low=105.0,
        close=108.0,
        volume=pole_vol[2],
    )
    signal_bar = _make_bar(
        ts=signal_ts,
        open_=108.0,
        high=signal_high,
        low=signal_low,
        close=signal_close,
        volume=signal_volume,
    )
    bars = baseline_bars + [pole_bar_0, pole_bar_1, pole_bar_2, signal_bar]
    return bars, len(bars) - 1


def test_bull_flag_fires_when_all_conditions_met() -> None:
    settings = _make_settings()
    daily_bars = _make_daily_bars()
    intraday_bars, signal_index = _make_scenario()
    result = evaluate_bull_flag_signal(
        symbol="AAPL",
        intraday_bars=intraday_bars,
        signal_index=signal_index,
        daily_bars=daily_bars,
        settings=settings,
    )
    assert result is not None
    assert isinstance(result, EntrySignal)
    assert result.symbol == "AAPL"


def test_bull_flag_entry_level_equals_pole_high() -> None:
    settings = _make_settings()
    daily_bars = _make_daily_bars()
    intraday_bars, signal_index = _make_scenario()
    result = evaluate_bull_flag_signal(
        symbol="AAPL",
        intraday_bars=intraday_bars,
        signal_index=signal_index,
        daily_bars=daily_bars,
        settings=settings,
    )
    assert result is not None
    # pole_high = max(104.0, 107.0, 109.0) = 109.0 from the three pole bars
    assert result.entry_level == pytest.approx(109.0)


def test_bull_flag_initial_stop_below_signal_bar_low() -> None:
    settings = _make_settings(atr_period=3)
    daily_bars = _make_daily_bars()
    intraday_bars, signal_index = _make_scenario(signal_low=107.5)
    result = evaluate_bull_flag_signal(
        symbol="AAPL",
        intraday_bars=intraday_bars,
        signal_index=signal_index,
        daily_bars=daily_bars,
        settings=settings,
    )
    assert result is not None
    assert result.initial_stop_price < 107.5


def test_bull_flag_returns_none_outside_entry_window() -> None:
    settings = _make_settings()
    daily_bars = _make_daily_bars()
    late_ts = datetime(2026, 1, 2, 16, 0, tzinfo=ZoneInfo("America/New_York"))
    intraday_bars, signal_index = _make_scenario(signal_ts=late_ts)
    result = evaluate_bull_flag_signal(
        symbol="AAPL",
        intraday_bars=intraday_bars,
        signal_index=signal_index,
        daily_bars=daily_bars,
        settings=settings,
    )
    assert result is None


def test_bull_flag_returns_none_when_trend_filter_fails() -> None:
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
    result = evaluate_bull_flag_signal(
        symbol="AAPL",
        intraday_bars=intraday_bars,
        signal_index=signal_index,
        daily_bars=daily_bars,
        settings=settings,
    )
    assert result is None


def test_bull_flag_returns_none_when_no_prior_today_bars() -> None:
    """Signal is the only today bar → no pole → None."""
    settings = _make_settings()
    daily_bars = _make_daily_bars()
    intraday_bars, signal_index = _make_scenario(only_signal_today=True)
    result = evaluate_bull_flag_signal(
        symbol="AAPL",
        intraday_bars=intraday_bars,
        signal_index=signal_index,
        daily_bars=daily_bars,
        settings=settings,
    )
    assert result is None


def test_bull_flag_returns_none_when_pole_run_too_small() -> None:
    settings = _make_settings(bull_flag_min_run_pct=0.15)
    # pole: open=100, high=109 → run=9% < 15%
    daily_bars = _make_daily_bars()
    intraday_bars, signal_index = _make_scenario()
    result = evaluate_bull_flag_signal(
        symbol="AAPL",
        intraday_bars=intraday_bars,
        signal_index=signal_index,
        daily_bars=daily_bars,
        settings=settings,
    )
    assert result is None


def test_bull_flag_returns_none_when_pole_range_zero() -> None:
    """Pole bars all have same high and low → pole_range=0 → early return."""
    settings = _make_settings()
    daily_bars = _make_daily_bars()
    ny = ZoneInfo("America/New_York")
    today_base = datetime(2026, 1, 2, 10, 0, tzinfo=ny)
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
    # Pole bar: open=100, high=105, low=105 — run_pct=5% passes but range=0
    pole_bar = _make_bar(
        ts=today_base,
        open_=100.0,
        high=105.0,
        low=105.0,
        close=105.0,
        volume=30_000.0,
    )
    signal_bar = _make_bar(
        ts=today_base + timedelta(minutes=15),
        open_=105.0,
        high=105.5,
        low=104.5,
        close=105.0,
        volume=5_000.0,
    )
    bars = baseline_bars + [pole_bar, signal_bar]
    result = evaluate_bull_flag_signal(
        symbol="AAPL",
        intraday_bars=bars,
        signal_index=len(bars) - 1,
        daily_bars=daily_bars,
        settings=settings,
    )
    assert result is None


def test_bull_flag_returns_none_when_signal_range_too_wide() -> None:
    # signal_range = 116 - 107 = 9; pole_range=10, 9 > 10*0.5=5 → reject
    settings = _make_settings()
    daily_bars = _make_daily_bars()
    intraday_bars, signal_index = _make_scenario(signal_high=116.0, signal_low=107.0)
    result = evaluate_bull_flag_signal(
        symbol="AAPL",
        intraday_bars=intraday_bars,
        signal_index=signal_index,
        daily_bars=daily_bars,
        settings=settings,
    )
    assert result is None


def test_bull_flag_returns_none_when_consolidation_volume_too_high() -> None:
    # pole_avg = 25_667, signal_volume = 20_000 > 25_667 * 0.6 = 15_400 → reject
    settings = _make_settings()
    daily_bars = _make_daily_bars()
    intraday_bars, signal_index = _make_scenario(signal_volume=20_000.0)
    result = evaluate_bull_flag_signal(
        symbol="AAPL",
        intraday_bars=intraday_bars,
        signal_index=signal_index,
        daily_bars=daily_bars,
        settings=settings,
    )
    assert result is None


def test_bull_flag_returns_none_when_pole_volume_not_elevated() -> None:
    # baseline_avg = 40_000, pole_avg = 25_667 → rv = 0.64 < 1.5 → reject
    settings = _make_settings()
    daily_bars = _make_daily_bars()
    intraday_bars, signal_index = _make_scenario(baseline_volume=40_000.0)
    result = evaluate_bull_flag_signal(
        symbol="AAPL",
        intraday_bars=intraday_bars,
        signal_index=signal_index,
        daily_bars=daily_bars,
        settings=settings,
    )
    assert result is None


def test_bull_flag_returns_none_when_not_enough_baseline_bars() -> None:
    """first_today_index < relative_volume_lookback_bars → can't compute baseline."""
    settings = _make_settings(relative_volume_lookback_bars=10)
    daily_bars = _make_daily_bars()
    # Only 5 baseline bars, but lookback needs 10
    intraday_bars, signal_index = _make_scenario()
    result = evaluate_bull_flag_signal(
        symbol="AAPL",
        intraday_bars=intraday_bars,
        signal_index=signal_index,
        daily_bars=daily_bars,
        settings=settings,
    )
    assert result is None


def test_bull_flag_returns_none_when_atr_insufficient() -> None:
    settings = _make_settings(atr_period=3, daily_sma_period=2)
    daily_bars = _make_daily_bars(n=3)  # 3 bars < atr_period+1=4 → ATR is None
    intraday_bars, signal_index = _make_scenario()
    result = evaluate_bull_flag_signal(
        symbol="AAPL",
        intraday_bars=intraday_bars,
        signal_index=signal_index,
        daily_bars=daily_bars,
        settings=settings,
    )
    assert result is None


def test_bull_flag_in_strategy_registry() -> None:
    from alpaca_bot.strategy import STRATEGY_REGISTRY

    assert "bull_flag" in STRATEGY_REGISTRY


def test_settings_has_bull_flag_min_run_pct() -> None:
    settings = _make_settings(bull_flag_min_run_pct=0.05)
    assert settings.bull_flag_min_run_pct == pytest.approx(0.05)


def test_settings_validates_bull_flag_min_run_pct_zero() -> None:
    with pytest.raises(ValueError, match="BULL_FLAG_MIN_RUN_PCT"):
        _make_settings(bull_flag_min_run_pct=0.0)


def test_settings_validates_bull_flag_min_run_pct_too_large() -> None:
    with pytest.raises(ValueError, match="BULL_FLAG_MIN_RUN_PCT"):
        _make_settings(bull_flag_min_run_pct=1.0)


def test_settings_has_bull_flag_consolidation_volume_ratio() -> None:
    settings = _make_settings(bull_flag_consolidation_volume_ratio=0.4)
    assert settings.bull_flag_consolidation_volume_ratio == pytest.approx(0.4)


def test_settings_validates_bull_flag_consolidation_volume_ratio_zero() -> None:
    with pytest.raises(ValueError, match="BULL_FLAG_CONSOLIDATION_VOLUME_RATIO"):
        _make_settings(bull_flag_consolidation_volume_ratio=0.0)


def test_settings_has_bull_flag_consolidation_range_pct() -> None:
    settings = _make_settings(bull_flag_consolidation_range_pct=0.3)
    assert settings.bull_flag_consolidation_range_pct == pytest.approx(0.3)


def test_settings_validates_bull_flag_consolidation_range_pct_too_large() -> None:
    with pytest.raises(ValueError, match="BULL_FLAG_CONSOLIDATION_RANGE_PCT"):
        _make_settings(bull_flag_consolidation_range_pct=1.0)
