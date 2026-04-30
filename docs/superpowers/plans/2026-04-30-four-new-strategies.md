# Plan: Four New Intraday Strategies

**Spec:** `docs/superpowers/specs/2026-04-30-four-new-strategies.md`
**Strategies:** bull_flag, vwap_cross, bb_squeeze, failed_breakdown

---

## Task 1 — Create `src/alpaca_bot/strategy/indicators.py`

```python
from __future__ import annotations

import math
from collections.abc import Sequence

from alpaca_bot.domain.models import Bar


def calculate_vwap(bars: Sequence[Bar]) -> float | None:
    total_vp = sum((b.high + b.low + b.close) / 3 * b.volume for b in bars)
    total_v = sum(b.volume for b in bars)
    return total_vp / total_v if total_v > 0 else None


def calculate_bollinger_bands(
    bars: Sequence[Bar], period: int, std_dev: float
) -> tuple[float, float, float] | None:
    """Return (lower, midline, upper) using population std dev. None if len(bars) < period."""
    if len(bars) < period:
        return None
    window = [b.close for b in bars[-period:]]
    midline = sum(window) / period
    variance = sum((c - midline) ** 2 for c in window) / period
    sigma = math.sqrt(variance)
    upper = midline + std_dev * sigma
    lower = midline - std_dev * sigma
    return lower, midline, upper
```

---

## Task 2 — Update `src/alpaca_bot/strategy/vwap_reversion.py`

Replace:
```python
def _calculate_vwap(bars: Sequence[Bar]) -> float | None:
    total_vp = sum((b.high + b.low + b.close) / 3 * b.volume for b in bars)
    total_v = sum(b.volume for b in bars)
    return total_vp / total_v if total_v > 0 else None
```

With:
```python
from alpaca_bot.strategy.indicators import calculate_vwap

_calculate_vwap = calculate_vwap  # backward compat — tests import _calculate_vwap from here
```

All internal calls to `_calculate_vwap(...)` remain valid since the alias is identical.

---

## Task 3 — Add 9 new Settings fields

### In `src/alpaca_bot/config/__init__.py`

**After `gap_volume_threshold: float = 2.0`**, add:

```python
    bull_flag_min_run_pct: float = 0.02
    bull_flag_consolidation_volume_ratio: float = 0.6
    bull_flag_consolidation_range_pct: float = 0.5
    bb_period: int = 20
    bb_std_dev: float = 2.0
    bb_squeeze_threshold_pct: float = 0.03
    bb_squeeze_min_bars: int = 5
    failed_breakdown_volume_ratio: float = 2.0
    failed_breakdown_recapture_buffer_pct: float = 0.001
```

**In `from_env()`**, after the `gap_volume_threshold=...` line, add:
```python
            bull_flag_min_run_pct=float(values.get("BULL_FLAG_MIN_RUN_PCT", "0.02")),
            bull_flag_consolidation_volume_ratio=float(
                values.get("BULL_FLAG_CONSOLIDATION_VOLUME_RATIO", "0.6")
            ),
            bull_flag_consolidation_range_pct=float(
                values.get("BULL_FLAG_CONSOLIDATION_RANGE_PCT", "0.5")
            ),
            bb_period=int(values.get("BB_PERIOD", "20")),
            bb_std_dev=float(values.get("BB_STD_DEV", "2.0")),
            bb_squeeze_threshold_pct=float(values.get("BB_SQUEEZE_THRESHOLD_PCT", "0.03")),
            bb_squeeze_min_bars=int(values.get("BB_SQUEEZE_MIN_BARS", "5")),
            failed_breakdown_volume_ratio=float(
                values.get("FAILED_BREAKDOWN_VOLUME_RATIO", "2.0")
            ),
            failed_breakdown_recapture_buffer_pct=float(
                values.get("FAILED_BREAKDOWN_RECAPTURE_BUFFER_PCT", "0.001")
            ),
```

**In `validate()`**, after the `gap_volume_threshold` check, add:
```python
        if self.bull_flag_min_run_pct <= 0 or self.bull_flag_min_run_pct >= 1.0:
            raise ValueError("BULL_FLAG_MIN_RUN_PCT must be between 0 and 1")
        if (
            self.bull_flag_consolidation_volume_ratio <= 0
            or self.bull_flag_consolidation_volume_ratio >= 1.0
        ):
            raise ValueError("BULL_FLAG_CONSOLIDATION_VOLUME_RATIO must be between 0 and 1")
        if (
            self.bull_flag_consolidation_range_pct <= 0
            or self.bull_flag_consolidation_range_pct >= 1.0
        ):
            raise ValueError("BULL_FLAG_CONSOLIDATION_RANGE_PCT must be between 0 and 1")
        if self.bb_period < 2:
            raise ValueError("BB_PERIOD must be at least 2")
        if self.bb_std_dev <= 0 or self.bb_std_dev > 5.0:
            raise ValueError("BB_STD_DEV must be between 0 (exclusive) and 5.0 (inclusive)")
        if self.bb_squeeze_threshold_pct <= 0 or self.bb_squeeze_threshold_pct >= 1.0:
            raise ValueError("BB_SQUEEZE_THRESHOLD_PCT must be between 0 and 1")
        if self.bb_squeeze_min_bars < 1:
            raise ValueError("BB_SQUEEZE_MIN_BARS must be at least 1")
        if self.failed_breakdown_volume_ratio <= 0:
            raise ValueError("FAILED_BREAKDOWN_VOLUME_RATIO must be positive")
        if (
            self.failed_breakdown_recapture_buffer_pct <= 0
            or self.failed_breakdown_recapture_buffer_pct >= 1.0
        ):
            raise ValueError("FAILED_BREAKDOWN_RECAPTURE_BUFFER_PCT must be between 0 and 1")
```

---

## Task 4 — Create `src/alpaca_bot/strategy/bull_flag.py`

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


def evaluate_bull_flag_signal(
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
    pole_bars = today_bars[:-1]  # exclude signal bar; these are the "pole"

    if not pole_bars:
        return None  # signal is the first today bar — no pole exists

    pole_open = pole_bars[0].open
    if pole_open <= 0:
        return None
    pole_high = max(b.high for b in pole_bars)
    if (pole_high - pole_open) / pole_open < settings.bull_flag_min_run_pct:
        return None

    pole_low = min(b.low for b in pole_bars)
    pole_range = pole_high - pole_low
    signal_range = signal_bar.high - signal_bar.low
    if pole_range <= 0 or signal_range > pole_range * settings.bull_flag_consolidation_range_pct:
        return None

    pole_avg_volume = sum(b.volume for b in pole_bars) / len(pole_bars)
    if signal_bar.volume > pole_avg_volume * settings.bull_flag_consolidation_volume_ratio:
        return None

    first_today_index = signal_index - len(today_bars) + 1
    if first_today_index < settings.relative_volume_lookback_bars:
        return None
    baseline_bars = intraday_bars[
        first_today_index - settings.relative_volume_lookback_bars : first_today_index
    ]
    baseline_avg = sum(b.volume for b in baseline_bars) / len(baseline_bars) if baseline_bars else 0.0
    if baseline_avg <= 0:
        return None
    relative_volume = pole_avg_volume / baseline_avg
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
    entry_level = signal_bar.high
    stop_price = round(entry_level + settings.entry_stop_price_buffer, 2)
    limit_price = round(stop_price * (1 + settings.stop_limit_buffer_pct), 2)

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

---

## Task 5 — Create `src/alpaca_bot/strategy/vwap_cross.py`

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
from alpaca_bot.strategy.indicators import calculate_vwap


def evaluate_vwap_cross_signal(
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

    if len(today_bars) < 2:
        return None  # need a prior today bar to check it was below VWAP

    prior_today_bars = today_bars[:-1]
    prior_vwap = calculate_vwap(prior_today_bars)
    if prior_vwap is None:
        return None
    current_vwap = calculate_vwap(today_bars)
    if current_vwap is None:
        return None

    if prior_today_bars[-1].close >= prior_vwap:
        return None  # prior bar was not below VWAP — no cross occurred
    if signal_bar.close < current_vwap:
        return None  # signal bar did not close above VWAP

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
    entry_level = round(current_vwap, 2)
    stop_price = round(signal_bar.high + settings.entry_stop_price_buffer, 2)
    limit_price = round(stop_price * (1 + settings.stop_limit_buffer_pct), 2)

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

---

## Task 6 — Create `src/alpaca_bot/strategy/bb_squeeze.py`

```python
from __future__ import annotations

from collections.abc import Sequence

from alpaca_bot.config import Settings
from alpaca_bot.domain.models import Bar, EntrySignal
from alpaca_bot.risk.atr import atr_stop_buffer, calculate_atr
from alpaca_bot.strategy.breakout import daily_trend_filter_passes, is_entry_session_time
from alpaca_bot.strategy.indicators import calculate_bollinger_bands


def evaluate_bb_squeeze_signal(
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

    # Need bb_period bars before the earliest squeeze bar
    min_required = settings.bb_period + settings.bb_squeeze_min_bars - 1
    if signal_index < min_required:
        return None

    # All bb_squeeze_min_bars bars immediately before signal must be in squeeze
    for i in range(signal_index - settings.bb_squeeze_min_bars, signal_index):
        bands = calculate_bollinger_bands(
            intraday_bars[: i + 1], settings.bb_period, settings.bb_std_dev
        )
        if bands is None:
            return None
        lower, midline, upper = bands
        if midline <= 0:
            return None
        if (upper - lower) / midline >= settings.bb_squeeze_threshold_pct:
            return None  # this bar was not in squeeze

    # Signal bar must close above the upper band computed from prior data only
    prior_bands = calculate_bollinger_bands(
        intraday_bars[:signal_index], settings.bb_period, settings.bb_std_dev
    )
    if prior_bands is None:
        return None
    _, _, upper_prior = prior_bands
    if signal_bar.close <= upper_prior:
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
    entry_level = round(upper_prior, 2)
    stop_price = round(signal_bar.high + settings.entry_stop_price_buffer, 2)
    limit_price = round(stop_price * (1 + settings.stop_limit_buffer_pct), 2)

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

---

## Task 7 — Create `src/alpaca_bot/strategy/failed_breakdown.py`

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


def evaluate_failed_breakdown_signal(
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
    prior_daily = [b for b in daily_bars if b.timestamp.date() < today]
    if not prior_daily:
        return None
    prior_session_low = prior_daily[-1].low

    if signal_bar.low >= prior_session_low:
        return None  # no breakdown below prior session low
    if signal_bar.close < prior_session_low * (1 + settings.failed_breakdown_recapture_buffer_pct):
        return None  # close did not recapture the level

    if signal_index < settings.relative_volume_lookback_bars:
        return None
    prior_bars = intraday_bars[
        signal_index - settings.relative_volume_lookback_bars : signal_index
    ]
    avg_volume = sum(b.volume for b in prior_bars) / len(prior_bars)
    relative_volume = signal_bar.volume / avg_volume if avg_volume > 0 else 0.0
    if relative_volume < settings.failed_breakdown_volume_ratio:
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
    entry_level = round(prior_session_low, 2)
    stop_price = round(signal_bar.high + settings.entry_stop_price_buffer, 2)
    limit_price = round(stop_price * (1 + settings.stop_limit_buffer_pct), 2)

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

---

## Task 8 — Update `src/alpaca_bot/strategy/__init__.py`

Add imports and registry entries:

```python
from alpaca_bot.strategy.bb_squeeze import evaluate_bb_squeeze_signal
from alpaca_bot.strategy.bull_flag import evaluate_bull_flag_signal
from alpaca_bot.strategy.failed_breakdown import evaluate_failed_breakdown_signal
from alpaca_bot.strategy.vwap_cross import evaluate_vwap_cross_signal

STRATEGY_REGISTRY: dict[str, StrategySignalEvaluator] = {
    "breakout": evaluate_breakout_signal,
    "momentum": evaluate_momentum_signal,
    "orb": evaluate_orb_signal,
    "high_watermark": evaluate_high_watermark_signal,
    "ema_pullback": evaluate_ema_pullback_signal,
    "vwap_reversion": evaluate_vwap_reversion_signal,
    "gap_and_go": evaluate_gap_and_go_signal,
    "bull_flag": evaluate_bull_flag_signal,
    "vwap_cross": evaluate_vwap_cross_signal,
    "bb_squeeze": evaluate_bb_squeeze_signal,
    "failed_breakdown": evaluate_failed_breakdown_signal,
}
```

---

## Task 9 — Create `tests/unit/test_indicators.py`

Tests for `calculate_vwap` and `calculate_bollinger_bands`.

```python
from __future__ import annotations
from datetime import datetime, timezone
import pytest
from alpaca_bot.domain.models import Bar
from alpaca_bot.strategy.indicators import calculate_bollinger_bands, calculate_vwap


def _bar(close: float, high: float | None = None, low: float | None = None, volume: float = 1.0) -> Bar:
    return Bar(
        symbol="AAPL",
        timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
        open=close,
        high=close if high is None else high,
        low=close if low is None else low,
        close=close,
        volume=volume,
    )


def test_calculate_vwap_single_bar():
    bar = _bar(close=100.0, high=102.0, low=98.0, volume=1000.0)
    result = calculate_vwap([bar])
    expected = (102.0 + 98.0 + 100.0) / 3  # typical price = 100.0
    assert result == pytest.approx(expected)


def test_calculate_vwap_volume_weighted():
    bars = [
        _bar(close=100.0, high=101.0, low=99.0, volume=1000.0),  # typical=100
        _bar(close=110.0, high=111.0, low=109.0, volume=9000.0),  # typical=110
    ]
    # VWAP = (100*1000 + 110*9000) / 10000 = 1_090_000 / 10000 = 109.0
    assert calculate_vwap(bars) == pytest.approx(109.0)


def test_calculate_vwap_returns_none_for_zero_volume():
    bars = [_bar(close=100.0, volume=0.0)]
    assert calculate_vwap(bars) is None


def test_calculate_vwap_empty_bars():
    assert calculate_vwap([]) is None


def test_calculate_bollinger_bands_returns_none_when_insufficient():
    bars = [_bar(100.0) for _ in range(4)]
    assert calculate_bollinger_bands(bars, period=5, std_dev=2.0) is None


def test_calculate_bollinger_bands_flat_series():
    bars = [_bar(100.0) for _ in range(10)]
    result = calculate_bollinger_bands(bars, period=5, std_dev=2.0)
    assert result is not None
    lower, midline, upper = result
    assert midline == pytest.approx(100.0)
    assert upper == pytest.approx(100.0)  # std dev = 0
    assert lower == pytest.approx(100.0)


def test_calculate_bollinger_bands_symmetric():
    import math
    closes = [98.0, 99.0, 100.0, 101.0, 102.0]
    bars = [_bar(c) for c in closes]
    result = calculate_bollinger_bands(bars, period=5, std_dev=2.0)
    assert result is not None
    lower, midline, upper = result
    assert midline == pytest.approx(100.0)
    variance = sum((c - 100.0) ** 2 for c in closes) / 5
    sigma = math.sqrt(variance)
    assert upper == pytest.approx(100.0 + 2.0 * sigma)
    assert lower == pytest.approx(100.0 - 2.0 * sigma)


def test_calculate_bollinger_bands_uses_last_period_bars():
    # First 5 bars at 100, last 5 bars at 200 — period=5 should use only last 5
    bars = [_bar(100.0) for _ in range(5)] + [_bar(200.0) for _ in range(5)]
    result = calculate_bollinger_bands(bars, period=5, std_dev=2.0)
    assert result is not None
    _, midline, _ = result
    assert midline == pytest.approx(200.0)
```

---

## Task 10 — Create `tests/unit/test_bull_flag_strategy.py`

Scenario: 5 yesterday bars (volume baseline=10_000) + 3 pole bars (strong up, high volume) + 1 signal bar (tight range, low volume).

```
yesterday: close=100, high=101, low=99, volume=10_000 (×5)
pole[0]:   open=100, high=103, low=99.5, close=102.5, volume=30_000
pole[1]:   open=102.5, high=106, low=102, close=105.5, volume=25_000
pole[2]:   open=105.5, high=109, low=105, close=108, volume=22_000
signal:    open=108, high=108.5, low=106.5, close=107, volume=5_000

signal_index = 8

pole_open = 100, pole_high = 109 → run = 9% > 2% ✓
pole_low = 99.5, pole_range = 9.5
signal_range = 2.0 ≤ 9.5 × 0.5 = 4.75 ✓
pole_avg_volume = 25_667
signal_volume 5_000 ≤ 25_667 × 0.6 = 15_400 ✓
baseline_avg = 10_000, relative_volume = 2.57 ≥ 1.5 ✓
```

Key tests:
- fires_when_all_conditions_met
- entry_level_equals_signal_bar_high (108.5)
- initial_stop_below_signal_bar_low (< 106.5)
- returns_none_outside_entry_window
- returns_none_when_trend_filter_fails
- returns_none_when_no_pole (signal is first today bar)
- returns_none_when_pole_run_too_small (pole_high=101 → 1% < 2%)
- returns_none_when_consolidation_range_too_wide (signal_range=10 > pole_range×0.5)
- returns_none_when_consolidation_volume_too_high (signal_volume=40_000 > threshold)
- returns_none_when_pole_volume_below_threshold (pole_volume=10_000 = baseline → rv=1.0 < 1.5)
- returns_none_when_atr_insufficient
- in_strategy_registry
- 3 settings validation tests (bull_flag_min_run_pct=0, =1.0; consolidation_volume_ratio=0)

---

## Task 11 — Create `tests/unit/test_vwap_cross_strategy.py`

Scenario: 5 yesterday bars + 4 normal today bars + 1 prior-bar-below-VWAP + signal bar.

```
yesterday:       high=101, low=99, close=100, volume=10_000 (×5)
today[0..3]:     high=101, low=99, close=100, volume=10_000 (×4)
today[4](prior): high=101, low=95, close=95, volume=10_000
signal[10]:      high=104, low=99, close=103, volume=30_000

prior_vwap:
  bars[5..8]: typical=100, vol=10_000 → VP=400_000
  bar[9]:     typical=(101+95+95)/3=97, vol=10_000 → VP+=97_000
  sum_vp=497_000 / 50_000 = 9.94 ... → WAIT: 4,970,000 / 50,000 = 99.4
  prior_vwap = 99.4; bar[9].close=95 < 99.4 ✓

current_vwap:
  signal: typical=(104+99+103)/3=102, vol=30_000 → VP=3_060_000
  total_vp=4,970,000+3,060,000=8,030,000 / 80,000 = 100.375
  signal.close=103 ≥ 100.375 ✓

relative_volume: bars[5..9] avg=10_000; signal=30_000 → rv=3.0 ✓
```

Key tests:
- fires_when_all_conditions_met
- entry_level_is_vwap (≈ 100.38 rounded to 2dp)
- initial_stop_below_signal_bar_low
- returns_none_outside_entry_window
- returns_none_when_trend_filter_fails
- returns_none_when_only_one_today_bar (first today bar — no prior)
- returns_none_when_prior_bar_above_vwap (prior bar close=102 > prior_vwap=100.13)
- returns_none_when_signal_close_below_vwap (signal close=97 < current_vwap)
- returns_none_when_volume_below_threshold (signal volume=5_000 → rv=0.5 < 1.5)
- returns_none_when_atr_insufficient
- in_strategy_registry

---

## Task 12 — Create `tests/unit/test_bb_squeeze_strategy.py`

Use `bb_period=5`, `bb_squeeze_min_bars=3` in settings for manageable test sizes.
`min_required = 5 + 3 - 1 = 7`, so signal_index must be ≥ 7.

Scenario: 25 flat bars (close=100, vol=10_000) + 1 signal bar (close=103, vol=30_000).

```
All prior bars: close=100 → BB midline=100, std_dev=0, upper=100 → band_width_pct=0 < 0.03 ✓
prior_bands(bars[:25], period=5): upper=100
signal.close=103 > 100 ✓
relative_volume: bars[20..24] avg=10_000; signal=30_000 → rv=3.0 ✓
signal_index=25
```

Key tests:
- fires_when_all_conditions_met
- entry_level_is_upper_band (≈ 100.0)
- initial_stop_below_signal_low
- returns_none_outside_entry_window
- returns_none_when_trend_filter_fails
- returns_none_when_insufficient_bars (signal_index < min_required)
- returns_none_when_no_squeeze (make bars[22..24] have close alternating 95/105 → std_dev ≈ 4 → band_width ≈ 8% > 3%)
- returns_none_when_close_at_or_below_upper_band (signal close=99.5 ≤ upper=100)
- returns_none_when_volume_below_threshold
- returns_none_when_atr_insufficient
- in_strategy_registry
- 4 settings validation tests (bb_period<2, bb_std_dev=0, bb_std_dev=5.1, bb_squeeze_min_bars<1)

For "no squeeze" scenario: change bars[22..24] closes to alternate 95,105,95.
  bar 22 contributes to squeeze check at i=22: BB over bars[18..22]=[100,100,100,100,95]
  midline=99, variance=(4×1+16)/5=4, sigma=2, upper=103, band_width_pct=8/99≈0.081 > 0.03 → not in squeeze → None ✓

---

## Task 13 — Create `tests/unit/test_failed_breakdown_strategy.py`

```
daily bars: n=10 with prior_low=99.0 on last daily bar
yesterday bars: high=102, low=99, close=100, volume=10_000 (×5, baseline)
signal bar: high=103, low=97.5, close=100.5, volume=30_000, ts=today 10:00 ET

prior_session_low = 99.0
signal_bar.low=97.5 < 99.0 ✓ (breakdown)
signal_bar.close=100.5 ≥ 99.0 × 1.001 = 99.099 ✓ (recapture)
relative_volume: avg=10_000; signal=30_000 → rv=3.0 ≥ 2.0 ✓
```

Key tests:
- fires_when_all_conditions_met
- entry_level_equals_prior_session_low (99.0)
- initial_stop_below_signal_bar_low (< 97.5)
- returns_none_outside_entry_window
- returns_none_when_trend_filter_fails
- returns_none_when_no_prior_daily_bar
- returns_none_when_no_breakdown (signal_bar.low=99.5 ≥ 99.0)
- returns_none_when_no_recapture (signal_bar.close=98.5 < 99.099)
- returns_none_when_volume_below_threshold (signal volume=5_000 → rv=0.5 < 2.0)
- returns_none_when_atr_insufficient
- in_strategy_registry
- 2 settings validation tests (failed_breakdown_volume_ratio=0, recapture_buffer_pct=0)

---

## Task 14 — Update `tests/unit/test_strategy_flags.py`

Add 4 new entries to the `snapshot.strategy_flags` assertion:

```python
    assert snapshot.strategy_flags == [
        ("breakout", None),
        ("momentum", None),
        ("orb", None),
        ("high_watermark", None),
        ("ema_pullback", None),
        ("vwap_reversion", None),
        ("gap_and_go", None),
        ("bull_flag", None),
        ("vwap_cross", None),
        ("bb_squeeze", None),
        ("failed_breakdown", None),
    ]
```

---

## Test command

```bash
pytest tests/unit/test_indicators.py tests/unit/test_bull_flag_strategy.py tests/unit/test_vwap_cross_strategy.py tests/unit/test_bb_squeeze_strategy.py tests/unit/test_failed_breakdown_strategy.py tests/unit/test_strategy_flags.py -v
pytest  # full suite — all 872+ tests must pass
```
