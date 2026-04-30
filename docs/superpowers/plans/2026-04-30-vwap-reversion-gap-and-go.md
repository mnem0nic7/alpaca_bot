# Plan: VWAP Reversion and Gap & Go Strategies

Spec: `docs/superpowers/specs/2026-04-30-vwap-reversion-gap-and-go.md`

## Task 1 — Add settings fields for the two new strategies

**File:** `src/alpaca_bot/config/__init__.py`

After `extended_hours_limit_offset_pct: float = 0.001` (line 108), add the three new fields:

```python
    vwap_dip_threshold_pct: float = 0.015
    gap_threshold_pct: float = 0.02
    gap_volume_threshold: float = 2.0
```

In `from_env()`, after the `extended_hours_limit_offset_pct` line, add:

```python
            vwap_dip_threshold_pct=float(
                values.get("VWAP_DIP_THRESHOLD_PCT", "0.015")
            ),
            gap_threshold_pct=float(values.get("GAP_THRESHOLD_PCT", "0.02")),
            gap_volume_threshold=float(values.get("GAP_VOLUME_THRESHOLD", "2.0")),
```

In `validate()`, after the `extended_hours_limit_offset_pct` check, add:

```python
        if self.vwap_dip_threshold_pct <= 0:
            raise ValueError("VWAP_DIP_THRESHOLD_PCT must be positive")
        if self.vwap_dip_threshold_pct >= 1.0:
            raise ValueError("VWAP_DIP_THRESHOLD_PCT must be less than 1.0")
        if self.gap_threshold_pct <= 0:
            raise ValueError("GAP_THRESHOLD_PCT must be positive")
        if self.gap_threshold_pct >= 1.0:
            raise ValueError("GAP_THRESHOLD_PCT must be less than 1.0")
        if self.gap_volume_threshold <= 0:
            raise ValueError("GAP_VOLUME_THRESHOLD must be positive")
```

## Task 2 — Implement VWAP Reversion evaluator

**File:** `src/alpaca_bot/strategy/vwap_reversion.py` (new file)

```python
from __future__ import annotations

from collections.abc import Sequence

from alpaca_bot.config import Settings
from alpaca_bot.domain.models import Bar, EntrySignal
from alpaca_bot.risk.atr import atr_stop_buffer, calculate_atr
from alpaca_bot.strategy.breakout import (
    daily_trend_filter_passes,
    is_entry_session_time,
    session_day,
)


def _calculate_vwap(bars: Sequence[Bar]) -> float | None:
    total_vp = sum((b.high + b.low + b.close) / 3 * b.volume for b in bars)
    total_v = sum(b.volume for b in bars)
    return total_vp / total_v if total_v > 0 else None


def evaluate_vwap_reversion_signal(
    *,
    symbol: str,
    intraday_bars: Sequence[Bar],
    signal_index: int,
    daily_bars: Sequence[Bar],
    settings: Settings,
) -> EntrySignal | None:
    if not intraday_bars or signal_index < 0 or signal_index >= len(intraday_bars):
        return None

    signal_bar = intraday_bars[signal_index]
    if signal_bar.symbol != symbol:
        return None
    if not is_entry_session_time(signal_bar.timestamp, settings):
        return None
    if not daily_trend_filter_passes(daily_bars, settings):
        return None

    today = session_day(signal_bar.timestamp, settings)
    today_bars = [
        b for b in intraday_bars[: signal_index + 1]
        if session_day(b.timestamp, settings) == today
    ]

    vwap = _calculate_vwap(today_bars)
    if vwap is None:
        return None

    if signal_bar.low > vwap * (1 - settings.vwap_dip_threshold_pct):
        return None
    if signal_bar.close < vwap:
        return None

    if signal_index < settings.relative_volume_lookback_bars:
        return None
    prior_bars = intraday_bars[
        signal_index - settings.relative_volume_lookback_bars : signal_index
    ]
    avg_volume = sum(b.volume for b in prior_bars) / len(prior_bars)
    relative_volume = signal_bar.volume / avg_volume if avg_volume > 0 else 0.0
    if relative_volume < settings.relative_volume_threshold:
        return None

    if calculate_atr(daily_bars, settings.atr_period) is None:
        return None

    stop_buffer = atr_stop_buffer(
        daily_bars,
        settings.atr_period,
        settings.atr_stop_multiplier,
        signal_bar.low,
        settings.breakout_stop_buffer_pct,
    )
    initial_stop_price = round(max(0.01, signal_bar.low - stop_buffer), 2)
    stop_price = round(signal_bar.high + settings.entry_stop_price_buffer, 2)
    limit_price = round(stop_price * (1 + settings.stop_limit_buffer_pct), 2)
    entry_level = round(vwap, 2)

    return EntrySignal(
        symbol=symbol,
        signal_bar=signal_bar,
        entry_level=entry_level,
        relative_volume=relative_volume,
        stop_price=stop_price,
        limit_price=limit_price,
        initial_stop_price=initial_stop_price,
    )
```

## Task 3 — Implement Gap & Go evaluator

**File:** `src/alpaca_bot/strategy/gap_and_go.py` (new file)

```python
from __future__ import annotations

from collections.abc import Sequence

from alpaca_bot.config import Settings
from alpaca_bot.domain.models import Bar, EntrySignal
from alpaca_bot.risk.atr import atr_stop_buffer, calculate_atr
from alpaca_bot.strategy.breakout import (
    daily_trend_filter_passes,
    is_entry_session_time,
    session_day,
)


def evaluate_gap_and_go_signal(
    *,
    symbol: str,
    intraday_bars: Sequence[Bar],
    signal_index: int,
    daily_bars: Sequence[Bar],
    settings: Settings,
) -> EntrySignal | None:
    if not intraday_bars or signal_index < 0 or signal_index >= len(intraday_bars):
        return None

    signal_bar = intraday_bars[signal_index]
    if signal_bar.symbol != symbol:
        return None
    if not is_entry_session_time(signal_bar.timestamp, settings):
        return None
    if not daily_trend_filter_passes(daily_bars, settings):
        return None

    today = session_day(signal_bar.timestamp, settings)
    today_bars = [
        b for b in intraday_bars[: signal_index + 1]
        if session_day(b.timestamp, settings) == today
    ]

    # Only fire on the first bar of today's session
    if len(today_bars) != 1:
        return None

    prior_daily = [b for b in daily_bars if b.timestamp.date() < today]
    if not prior_daily:
        return None
    prior_day_close = prior_daily[-1].close
    prior_day_high = prior_daily[-1].high

    if signal_bar.open <= prior_day_close * (1 + settings.gap_threshold_pct):
        return None
    if signal_bar.close <= prior_day_high:
        return None

    if signal_index < settings.relative_volume_lookback_bars:
        return None
    prior_bars = intraday_bars[
        signal_index - settings.relative_volume_lookback_bars : signal_index
    ]
    avg_volume = sum(b.volume for b in prior_bars) / len(prior_bars)
    relative_volume = signal_bar.volume / avg_volume if avg_volume > 0 else 0.0
    if relative_volume < settings.gap_volume_threshold:
        return None

    if calculate_atr(daily_bars, settings.atr_period) is None:
        return None

    stop_buffer = atr_stop_buffer(
        daily_bars,
        settings.atr_period,
        settings.atr_stop_multiplier,
        prior_day_high,
        settings.breakout_stop_buffer_pct,
    )
    initial_stop_price = round(max(0.01, prior_day_high - stop_buffer), 2)
    stop_price = round(signal_bar.high + settings.entry_stop_price_buffer, 2)
    limit_price = round(stop_price * (1 + settings.stop_limit_buffer_pct), 2)
    entry_level = prior_day_high

    return EntrySignal(
        symbol=symbol,
        signal_bar=signal_bar,
        entry_level=entry_level,
        relative_volume=relative_volume,
        stop_price=stop_price,
        limit_price=limit_price,
        initial_stop_price=initial_stop_price,
    )
```

## Task 4 — Register new strategies in STRATEGY_REGISTRY

**File:** `src/alpaca_bot/strategy/__init__.py`

Replace the entire file:

```python
from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from alpaca_bot.config import Settings
from alpaca_bot.domain.models import Bar, EntrySignal
from alpaca_bot.strategy.breakout import evaluate_breakout_signal
from alpaca_bot.strategy.ema_pullback import evaluate_ema_pullback_signal
from alpaca_bot.strategy.gap_and_go import evaluate_gap_and_go_signal
from alpaca_bot.strategy.high_watermark import evaluate_high_watermark_signal
from alpaca_bot.strategy.momentum import evaluate_momentum_signal
from alpaca_bot.strategy.orb import evaluate_orb_signal
from alpaca_bot.strategy.vwap_reversion import evaluate_vwap_reversion_signal


@runtime_checkable
class StrategySignalEvaluator(Protocol):
    def __call__(
        self,
        *,
        symbol: str,
        intraday_bars: Sequence[Bar],
        signal_index: int,
        daily_bars: Sequence[Bar],
        settings: Settings,
    ) -> EntrySignal | None: ...


STRATEGY_REGISTRY: dict[str, StrategySignalEvaluator] = {
    "breakout": evaluate_breakout_signal,
    "momentum": evaluate_momentum_signal,
    "orb": evaluate_orb_signal,
    "high_watermark": evaluate_high_watermark_signal,
    "ema_pullback": evaluate_ema_pullback_signal,
    "vwap_reversion": evaluate_vwap_reversion_signal,
    "gap_and_go": evaluate_gap_and_go_signal,
}
```

## Task 5 — Add tests for VWAP Reversion

**File:** `tests/unit/test_vwap_reversion_strategy.py` (new file)

```python
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from alpaca_bot.domain.models import Bar, EntrySignal
from alpaca_bot.strategy.vwap_reversion import evaluate_vwap_reversion_signal, _calculate_vwap


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
    """Build intraday bars: n_prior_today flat bars + 1 signal bar.

    Prior bars are yesterday-timestamped for relative volume baseline; signal
    bar is today within the entry window.
    """
    ny = ZoneInfo("America/New_York")
    # Prior-session bars (yesterday) — used for relative volume baseline
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
    # Today's bars before signal bar (for VWAP)
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
    today_bars = intraday_bars[5 : signal_index + 1]  # today's bars (skip 5 yesterday)
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
    # All bars close below SMA (downtrend)
    daily_bars = [
        Bar(
            symbol="AAPL",
            timestamp=datetime(2026, 1, 1, 21, 0, tzinfo=timezone.utc),
            open=110.0, high=111.0, low=109.0, close=90.0,
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
    # VWAP ≈ 100; bar low = 99.5 → 99.5/100 = 0.995, only 0.5% below VWAP < 1.5%
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
```

## Task 6 — Add tests for Gap & Go

**File:** `tests/unit/test_gap_and_go_strategy.py` (new file)

```python
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from alpaca_bot.domain.models import Bar, EntrySignal
from alpaca_bot.strategy.gap_and_go import evaluate_gap_and_go_signal


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
        bars.append(Bar(
            symbol="AAPL",
            timestamp=datetime(2026, 1, 1, 21, 0, tzinfo=timezone.utc),
            open=prior_close - 1.0 + i * 0.1,
            high=prior_close + i * 0.1,
            low=prior_close - 2.0,
            close=prior_close - 0.5 + i * 0.1,  # increasing so trend filter passes
            volume=1_000_000.0,
        ))
    # Last bar is yesterday — same Jan 1 UTC date so prior_daily filter includes it
    bars.append(Bar(
        symbol="AAPL",
        timestamp=datetime(2026, 1, 1, 21, 0, tzinfo=timezone.utc),
        open=prior_close - 0.5,
        high=prior_high,
        low=prior_close - 1.0,
        close=prior_close,
        volume=1_000_000.0,
    ))
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
    gap_open: float = 103.5,      # > prior_close * 1.02 = 102.0
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
        bars.append(_make_bar(
            ts=second_ts,
            open_=signal_close,
            high=signal_high + 0.5,
            low=signal_close - 0.5,
            close=signal_close + 0.3,
            volume=signal_volume,
        ))
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
            open=110.0, high=111.0, low=109.0, close=90.0,
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
    # Pass only today's daily bar — no prior_daily → evaluator returns None
    today_bar = Bar(
        symbol="AAPL",
        timestamp=datetime(2026, 1, 2, 21, 0, tzinfo=timezone.utc),
        open=103.0, high=105.0, low=100.0, close=103.5,
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
```

## Task 7 — Run targeted test suite

```bash
pytest tests/unit/test_vwap_reversion_strategy.py tests/unit/test_gap_and_go_strategy.py -v
```

Expected: all new tests pass.

## Task 8 — Run full test suite

```bash
pytest -q
```

Expected: all 842+ existing tests pass plus new ones. No regressions.

## Task 9 — Commit

```bash
git add \
  src/alpaca_bot/config/__init__.py \
  src/alpaca_bot/strategy/vwap_reversion.py \
  src/alpaca_bot/strategy/gap_and_go.py \
  src/alpaca_bot/strategy/__init__.py \
  tests/unit/test_vwap_reversion_strategy.py \
  tests/unit/test_gap_and_go_strategy.py \
  docs/superpowers/specs/2026-04-30-vwap-reversion-gap-and-go.md \
  docs/superpowers/plans/2026-04-30-vwap-reversion-gap-and-go.md
git commit -m "feat: add vwap_reversion and gap_and_go strategies"
```
