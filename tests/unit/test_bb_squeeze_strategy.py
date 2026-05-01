from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from alpaca_bot.domain.models import Bar, EntrySignal
from alpaca_bot.strategy.bb_squeeze import evaluate_bb_squeeze_signal


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
        bb_period=5,
        bb_std_dev=2.0,
        bb_squeeze_threshold_pct=0.03,
        bb_squeeze_min_bars=3,
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
    close: float,
    volume: float = 10_000.0,
    high: float | None = None,
    low: float | None = None,
) -> Bar:
    return Bar(
        symbol="AAPL",
        timestamp=ts,
        open=close,
        high=high if high is not None else close + 0.25,
        low=low if low is not None else close - 0.25,
        close=close,
        volume=volume,
    )


def _make_scenario(
    *,
    signal_ts: datetime | None = None,
    signal_close: float = 103.0,
    signal_volume: float = 30_000.0,
    signal_high: float | None = None,
    signal_low: float | None = None,
    squeeze_bars_count: int = 10,
    non_squeeze_indices: list[int] | None = None,
) -> tuple[list[Bar], int]:
    """
    Build scenario: 10 flat history bars (all squeeze) + 1 signal bar.

    With bb_period=5, bb_squeeze_min_bars=3:
      min_required = 5 + 3 - 1 = 7
      signal_index = 10 >= 7 ✓
      Squeeze check loop: i in range(7, 10) → bars 7,8,9 all flat → bb width=0 < 3% ✓
      prior_bands from intraday_bars[:10] → flat at 100 → upper_prior=100
      signal.close=103 > 100 ✓
      lookback_bars = bars[5:10], avg=10_000 → rv=30_000/10_000=3.0 ≥ 1.5 ✓
    """
    ny = ZoneInfo("America/New_York")
    base_ts = datetime(2026, 1, 2, 10, 0, tzinfo=ny)
    bars = []
    for i in range(squeeze_bars_count):
        close = 100.0
        if non_squeeze_indices and i in non_squeeze_indices:
            close = 95.0 if i % 2 == 0 else 105.0
        bars.append(
            _make_bar(
                ts=base_ts + timedelta(minutes=15 * i),
                close=close,
                volume=10_000.0,
            )
        )
    if signal_ts is None:
        signal_ts = base_ts + timedelta(minutes=15 * squeeze_bars_count)
    bars.append(
        _make_bar(
            ts=signal_ts,
            close=signal_close,
            volume=signal_volume,
            high=signal_high,
            low=signal_low,
        )
    )
    signal_index = len(bars) - 1
    return bars, signal_index


def test_bb_squeeze_fires_when_all_conditions_met() -> None:
    settings = _make_settings()
    daily_bars = _make_daily_bars()
    intraday_bars, signal_index = _make_scenario()
    result = evaluate_bb_squeeze_signal(
        symbol="AAPL",
        intraday_bars=intraday_bars,
        signal_index=signal_index,
        daily_bars=daily_bars,
        settings=settings,
    )
    assert result is not None
    assert isinstance(result, EntrySignal)
    assert result.symbol == "AAPL"


def test_bb_squeeze_entry_level_equals_upper_prior_band_rounded() -> None:
    settings = _make_settings()
    daily_bars = _make_daily_bars()
    intraday_bars, signal_index = _make_scenario()
    result = evaluate_bb_squeeze_signal(
        symbol="AAPL",
        intraday_bars=intraday_bars,
        signal_index=signal_index,
        daily_bars=daily_bars,
        settings=settings,
    )
    assert result is not None
    # All history bars flat at 100 → upper_prior=100.0 (zero std dev)
    assert result.entry_level == pytest.approx(100.0)


def test_bb_squeeze_initial_stop_below_signal_bar_low() -> None:
    settings = _make_settings(atr_period=3)
    daily_bars = _make_daily_bars()
    intraday_bars, signal_index = _make_scenario(signal_close=103.0, signal_low=99.0)
    result = evaluate_bb_squeeze_signal(
        symbol="AAPL",
        intraday_bars=intraday_bars,
        signal_index=signal_index,
        daily_bars=daily_bars,
        settings=settings,
    )
    assert result is not None
    assert result.initial_stop_price < 99.0


def test_bb_squeeze_returns_none_outside_entry_window() -> None:
    settings = _make_settings()
    daily_bars = _make_daily_bars()
    late_ts = datetime(2026, 1, 2, 16, 0, tzinfo=ZoneInfo("America/New_York"))
    intraday_bars, signal_index = _make_scenario(signal_ts=late_ts)
    result = evaluate_bb_squeeze_signal(
        symbol="AAPL",
        intraday_bars=intraday_bars,
        signal_index=signal_index,
        daily_bars=daily_bars,
        settings=settings,
    )
    assert result is None


def test_bb_squeeze_returns_none_when_trend_filter_fails() -> None:
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
    result = evaluate_bb_squeeze_signal(
        symbol="AAPL",
        intraday_bars=intraday_bars,
        signal_index=signal_index,
        daily_bars=daily_bars,
        settings=settings,
    )
    assert result is None


def test_bb_squeeze_returns_none_when_insufficient_history() -> None:
    """signal_index < bb_period + bb_squeeze_min_bars - 1 → None."""
    settings = _make_settings(bb_period=5, bb_squeeze_min_bars=3)
    daily_bars = _make_daily_bars()
    # Build only 7 bars (indices 0-6), signal_index=6 < min_required=7 → None
    intraday_bars, signal_index = _make_scenario(squeeze_bars_count=6)
    result = evaluate_bb_squeeze_signal(
        symbol="AAPL",
        intraday_bars=intraday_bars,
        signal_index=signal_index,
        daily_bars=daily_bars,
        settings=settings,
    )
    assert result is None


def test_bb_squeeze_returns_none_when_no_squeeze() -> None:
    """One of the required squeeze bars has wide bands → not in squeeze → None."""
    settings = _make_settings()
    daily_bars = _make_daily_bars()
    # bars[7,8,9] are the squeeze check range; set them to alternating 95/105 → wide bands
    intraday_bars, signal_index = _make_scenario(non_squeeze_indices=[7, 8, 9])
    result = evaluate_bb_squeeze_signal(
        symbol="AAPL",
        intraday_bars=intraday_bars,
        signal_index=signal_index,
        daily_bars=daily_bars,
        settings=settings,
    )
    assert result is None


def test_bb_squeeze_returns_none_when_signal_not_above_upper_band() -> None:
    """signal.close = 100 = upper_prior (flat history) → not strictly above → None."""
    settings = _make_settings()
    daily_bars = _make_daily_bars()
    intraday_bars, signal_index = _make_scenario(signal_close=100.0)
    result = evaluate_bb_squeeze_signal(
        symbol="AAPL",
        intraday_bars=intraday_bars,
        signal_index=signal_index,
        daily_bars=daily_bars,
        settings=settings,
    )
    assert result is None


def test_bb_squeeze_returns_none_when_volume_too_low() -> None:
    settings = _make_settings(relative_volume_threshold=5.0)
    # signal_volume=30_000, avg=10_000 → rv=3.0 < 5.0 → reject
    daily_bars = _make_daily_bars()
    intraday_bars, signal_index = _make_scenario()
    result = evaluate_bb_squeeze_signal(
        symbol="AAPL",
        intraday_bars=intraday_bars,
        signal_index=signal_index,
        daily_bars=daily_bars,
        settings=settings,
    )
    assert result is None


def test_bb_squeeze_returns_none_when_atr_insufficient() -> None:
    settings = _make_settings(atr_period=3, daily_sma_period=2)
    daily_bars = _make_daily_bars(n=3)  # 3 < 4 → ATR None
    intraday_bars, signal_index = _make_scenario()
    result = evaluate_bb_squeeze_signal(
        symbol="AAPL",
        intraday_bars=intraday_bars,
        signal_index=signal_index,
        daily_bars=daily_bars,
        settings=settings,
    )
    assert result is None


def test_bb_squeeze_in_strategy_registry() -> None:
    from alpaca_bot.strategy import STRATEGY_REGISTRY

    assert "bb_squeeze" in STRATEGY_REGISTRY


def test_settings_has_bb_period() -> None:
    settings = _make_settings(bb_period=10)
    assert settings.bb_period == 10


def test_settings_validates_bb_period_too_small() -> None:
    with pytest.raises(ValueError, match="BB_PERIOD"):
        _make_settings(bb_period=1)


def test_settings_has_bb_std_dev() -> None:
    settings = _make_settings(bb_std_dev=1.5)
    assert settings.bb_std_dev == pytest.approx(1.5)


def test_settings_validates_bb_std_dev_zero() -> None:
    with pytest.raises(ValueError, match="BB_STD_DEV"):
        _make_settings(bb_std_dev=0.0)


def test_settings_validates_bb_std_dev_too_large() -> None:
    with pytest.raises(ValueError, match="BB_STD_DEV"):
        _make_settings(bb_std_dev=5.1)


def test_settings_has_bb_squeeze_threshold_pct() -> None:
    settings = _make_settings(bb_squeeze_threshold_pct=0.05)
    assert settings.bb_squeeze_threshold_pct == pytest.approx(0.05)


def test_settings_validates_bb_squeeze_threshold_pct_zero() -> None:
    with pytest.raises(ValueError, match="BB_SQUEEZE_THRESHOLD_PCT"):
        _make_settings(bb_squeeze_threshold_pct=0.0)


def test_settings_has_bb_squeeze_min_bars() -> None:
    settings = _make_settings(bb_squeeze_min_bars=10)
    assert settings.bb_squeeze_min_bars == 10


def test_settings_validates_bb_squeeze_min_bars_zero() -> None:
    with pytest.raises(ValueError, match="BB_SQUEEZE_MIN_BARS"):
        _make_settings(bb_squeeze_min_bars=0)
