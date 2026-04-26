# ATR-Based Stops — Implementation Plan

**Spec:** `docs/superpowers/specs/2026-04-26-atr-stops-design.md`
**Date:** 2026-04-26
**Revised:** 2026-04-26 (grilling pass — fixed test helper names, missing test file)

Tasks in order: 1 → 2 → 3 → 4 → 5. Run `pytest` after each task.

---

## Task 1 — Add `atr_period` and `atr_stop_multiplier` to Settings

**File:** `src/alpaca_bot/config/__init__.py`

### 1a — Add fields after `ema_period` (line 81)

```python
    ema_period: int = 9
    atr_period: int = 14
    atr_stop_multiplier: float = 1.5
```

### 1b — Add `from_env()` parsing after `ema_period` line (line 135)

```python
            ema_period=int(values.get("EMA_PERIOD", "9")),
            atr_period=int(values.get("ATR_PERIOD", "14")),
            atr_stop_multiplier=float(values.get("ATR_STOP_MULTIPLIER", "1.5")),
```

### 1c — Add validation after `ema_period` check (line 205-206)

```python
        if self.ema_period < 2:
            raise ValueError("EMA_PERIOD must be at least 2")
        if self.atr_period < 2:
            raise ValueError("ATR_PERIOD must be at least 2")
        if self.atr_stop_multiplier <= 0:
            raise ValueError("ATR_STOP_MULTIPLIER must be positive")
```

**Test command:** `pytest tests/unit/test_momentum_strategy.py -v` (settings validation tests live there by convention)

---

## Task 2 — Create `risk/atr.py`

**File:** `src/alpaca_bot/risk/atr.py` (new)

```python
from __future__ import annotations

from collections.abc import Sequence

from alpaca_bot.domain.models import Bar


def calculate_atr(bars: Sequence[Bar], period: int) -> float | None:
    """Return Wilder's ATR for the last bar in `bars`, or None if insufficient data.

    Requires at least period + 1 bars (period bars to compute TRs, plus a
    prev_close for the first TR).
    """
    if len(bars) < period + 1:
        return None

    true_ranges: list[float] = []
    for i in range(1, len(bars)):
        bar = bars[i]
        prev_close = bars[i - 1].close
        tr = max(
            bar.high - bar.low,
            abs(bar.high - prev_close),
            abs(bar.low - prev_close),
        )
        true_ranges.append(tr)

    # Seed with simple mean of first `period` TRs, then apply Wilder's smoothing.
    atr = sum(true_ranges[:period]) / period
    for tr in true_ranges[period:]:
        atr = (atr * (period - 1) + tr) / period

    return atr
```

**Test command:** `pytest tests/unit/test_atr.py -v`

---

## Task 3 — Create `tests/unit/test_atr.py`

**File:** `tests/unit/test_atr.py` (new)

```python
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from alpaca_bot.domain.models import Bar
from alpaca_bot.risk.atr import calculate_atr


def _bar(close: float, high: float | None = None, low: float | None = None) -> Bar:
    h = high if high is not None else close + 1.0
    l = low if low is not None else close - 1.0
    return Bar(
        symbol="TEST",
        timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
        open=close - 0.5,
        high=h,
        low=l,
        close=close,
        volume=1_000.0,
    )


def test_calculate_atr_returns_none_with_insufficient_bars():
    bars = [_bar(100.0 + i) for i in range(3)]  # need period+1=5 for period=4
    assert calculate_atr(bars, period=4) is None


def test_calculate_atr_returns_none_when_exactly_period_bars():
    # Need period+1 bars; exactly period bars is insufficient
    bars = [_bar(100.0 + i) for i in range(3)]
    assert calculate_atr(bars, period=3) is None


def test_calculate_atr_basic():
    # period=2, constant TR=4 bars → ATR should be 4.0
    # bars[0]: close=100
    # bars[1]: high=104, low=100, close=102 → TR = max(4, 4, 0) = 4
    # bars[2]: high=106, low=102, close=104 → TR = max(4, 4, 0) = 4
    # First ATR = (4+4)/2 = 4.0
    bars = [
        _bar(100.0, high=101.0, low=99.0),  # baseline prev_close
        _bar(102.0, high=104.0, low=100.0),
        _bar(104.0, high=106.0, low=102.0),
    ]
    result = calculate_atr(bars, period=2)
    assert result == pytest.approx(4.0)


def test_calculate_atr_uses_true_range_not_high_minus_low():
    # bars[1] gaps up: high-low=2, but |high-prev_close|=10 → TR=10
    bars = [
        _bar(100.0, high=101.0, low=99.0),
        _bar(109.0, high=110.0, low=108.0),  # gap up: prev_close=100, high=110 → TR=10
        _bar(110.0, high=111.0, low=109.0),  # TR = max(2, 2, 1) = 2
    ]
    result = calculate_atr(bars, period=2)
    # First ATR = (10+2)/2 = 6.0; if naively high-low it would be (2+2)/2 = 2.0
    assert result == pytest.approx(6.0)


def test_calculate_atr_wilders_smoothing():
    # period=2
    # bars[0]: close=100
    # bars[1]: TR = 4  (high=104, low=100)
    # bars[2]: TR = 4  (high=106, low=102)
    # → First ATR = 4.0
    # bars[3]: TR = 6  (high=110, low=106, prev_close=104 → max(4,6,2)=6)
    # → ATR = (4.0 * 1 + 6) / 2 = 5.0
    bars = [
        _bar(100.0, high=101.0, low=99.0),
        _bar(102.0, high=104.0, low=100.0),
        _bar(104.0, high=106.0, low=102.0),
        _bar(108.0, high=110.0, low=106.0),
    ]
    result = calculate_atr(bars, period=2)
    assert result == pytest.approx(5.0)


def test_calculate_atr_returns_float_not_none_when_exactly_period_plus_one_bars():
    bars = [_bar(100.0 + i, high=100.0 + i + 1.0, low=100.0 + i - 1.0) for i in range(4)]
    result = calculate_atr(bars, period=3)
    assert result is not None
    assert isinstance(result, float)
```

**Test command:** `pytest tests/unit/test_atr.py -v`

---

## Task 4 — Update all five strategies to use ATR stops

For each strategy, replace the stop buffer calculation with the ATR-based version.
If ATR is unavailable (insufficient daily bars), fall back to the existing buffer-pct logic.

### 4a — `strategy/breakout.py`

Add import at top:
```python
from alpaca_bot.risk.atr import calculate_atr
```

Replace (lines 71-72):
```python
    breakout_stop_buffer = max(0.01, breakout_level * settings.breakout_stop_buffer_pct)
    initial_stop_price = round(breakout_level - breakout_stop_buffer, 2)
```
With:
```python
    atr = calculate_atr(daily_bars, settings.atr_period)
    if atr is not None:
        breakout_stop_buffer = max(0.01, settings.atr_stop_multiplier * atr)
    else:
        breakout_stop_buffer = max(0.01, breakout_level * settings.breakout_stop_buffer_pct)
    initial_stop_price = round(breakout_level - breakout_stop_buffer, 2)
```

### 4b — `strategy/momentum.py`

Add import at top:
```python
from alpaca_bot.risk.atr import calculate_atr
```

Replace (lines 51-52):
```python
    stop_buffer = max(0.01, yesterday_high * settings.breakout_stop_buffer_pct)
    initial_stop_price = round(yesterday_high - stop_buffer, 2)
```
With:
```python
    atr = calculate_atr(daily_bars, settings.atr_period)
    if atr is not None:
        stop_buffer = max(0.01, settings.atr_stop_multiplier * atr)
    else:
        stop_buffer = max(0.01, yesterday_high * settings.breakout_stop_buffer_pct)
    initial_stop_price = round(yesterday_high - stop_buffer, 2)
```

### 4c — `strategy/orb.py`

Add import at top:
```python
from alpaca_bot.risk.atr import calculate_atr
```

Replace (lines 59-60):
```python
    stop_buffer = max(0.01, opening_range_low * settings.breakout_stop_buffer_pct)
    initial_stop_price = round(opening_range_low - stop_buffer, 2)
```
With:
```python
    atr = calculate_atr(daily_bars, settings.atr_period)
    if atr is not None:
        stop_buffer = max(0.01, settings.atr_stop_multiplier * atr)
    else:
        stop_buffer = max(0.01, opening_range_low * settings.breakout_stop_buffer_pct)
    initial_stop_price = round(opening_range_low - stop_buffer, 2)
```

### 4d — `strategy/high_watermark.py`

Add import at top:
```python
from alpaca_bot.risk.atr import calculate_atr
```

Replace (lines 52-53):
```python
    stop_buffer = max(0.01, historical_high * settings.breakout_stop_buffer_pct)
    initial_stop_price = round(historical_high - stop_buffer, 2)
```
With:
```python
    atr = calculate_atr(daily_bars, settings.atr_period)
    if atr is not None:
        stop_buffer = max(0.01, settings.atr_stop_multiplier * atr)
    else:
        stop_buffer = max(0.01, historical_high * settings.breakout_stop_buffer_pct)
    initial_stop_price = round(historical_high - stop_buffer, 2)
```

### 4e — `strategy/ema_pullback.py`

Add import at top:
```python
from alpaca_bot.risk.atr import calculate_atr
```

Replace (lines 96-97):
```python
    stop_buffer = max(0.01, prior_bar.low * settings.breakout_stop_buffer_pct)
    initial_stop_price = round(prior_bar.low - stop_buffer, 2)
```
With:
```python
    atr = calculate_atr(daily_bars, settings.atr_period)
    if atr is not None:
        stop_buffer = max(0.01, settings.atr_stop_multiplier * atr)
    else:
        stop_buffer = max(0.01, prior_bar.low * settings.breakout_stop_buffer_pct)
    initial_stop_price = round(prior_bar.low - stop_buffer, 2)
```

**Test command after all 4a-4e:** `pytest -q`

---

## Task 5 — ATR stop tests for each strategy

### 5a — CREATE `tests/unit/test_breakout_strategy.py` (new file)

No existing dedicated breakout strategy test file — breakout tests currently live in
`test_strategy_rules.py`. Create a new file with the ATR-specific tests and minimal
required helpers.

```python
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from alpaca_bot.domain.models import Bar
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


def _make_breakout_bars(
    breakout_level: float = 110.0,
    n_lookback: int = 5,
) -> list[Bar]:
    """Build n_lookback bars at breakout_level then a signal bar that breaks out."""
    ny = ZoneInfo("America/New_York")
    base = datetime(2026, 1, 2, 10, 0, tzinfo=ny)
    bars = []
    for i in range(n_lookback):
        ts = base + timedelta(minutes=15 * i)
        bars.append(Bar(
            symbol="AAPL", timestamp=ts,
            open=breakout_level - 1.0, high=breakout_level, low=breakout_level - 2.0,
            close=breakout_level - 0.5, volume=50_000.0,
        ))
    signal_ts = base + timedelta(minutes=15 * n_lookback)
    bars.append(Bar(
        symbol="AAPL", timestamp=signal_ts,
        open=breakout_level + 0.5, high=breakout_level + 2.0, low=breakout_level,
        close=breakout_level + 1.5, volume=200_000.0,
    ))
    return bars


def test_breakout_stop_uses_atr_when_enough_daily_bars():
    from alpaca_bot.risk.atr import calculate_atr
    settings = _make_settings(atr_period=3, atr_stop_multiplier=1.5)
    daily_bars = _make_daily_bars(n=10)
    intraday_bars = _make_breakout_bars()
    result = evaluate_breakout_signal(
        symbol="AAPL",
        intraday_bars=intraday_bars,
        signal_index=len(intraday_bars) - 1,
        daily_bars=daily_bars,
        settings=settings,
    )
    assert result is not None
    atr = calculate_atr(daily_bars, 3)
    assert atr is not None
    expected_buffer = max(0.01, 1.5 * atr)
    assert result.initial_stop_price == round(result.entry_level - expected_buffer, 2)


def test_breakout_stop_falls_back_to_buffer_pct_when_insufficient_bars():
    settings = _make_settings(atr_period=50, breakout_stop_buffer_pct=0.001)
    daily_bars = _make_daily_bars(n=5)  # 5 < 50+1
    intraday_bars = _make_breakout_bars()
    result = evaluate_breakout_signal(
        symbol="AAPL",
        intraday_bars=intraday_bars,
        signal_index=len(intraday_bars) - 1,
        daily_bars=daily_bars,
        settings=settings,
    )
    assert result is not None
    expected_buffer = max(0.01, result.entry_level * 0.001)
    assert result.initial_stop_price == round(result.entry_level - expected_buffer, 2)
```

### 5b — Append to `tests/unit/test_momentum_strategy.py`

Note: `_make_daily_bars(n=10, high=100.0)` and `_make_intraday_bars()` are existing helpers
in this file. Use them directly — do not invent new helpers.

```python
def test_momentum_stop_uses_atr_when_enough_daily_bars():
    from alpaca_bot.risk.atr import calculate_atr
    from alpaca_bot.strategy.momentum import evaluate_momentum_signal
    settings = _make_settings(atr_period=3, atr_stop_multiplier=1.5)
    daily_bars = _make_daily_bars(n=10, high=100.0)
    intraday_bars = _make_intraday_bars(n=6, high=102.0, close=101.5)
    result = evaluate_momentum_signal(
        symbol="AAPL",
        intraday_bars=intraday_bars,
        signal_index=len(intraday_bars) - 1,
        daily_bars=daily_bars,
        settings=settings,
    )
    assert result is not None
    atr = calculate_atr(daily_bars, 3)
    assert atr is not None
    expected_buffer = max(0.01, 1.5 * atr)
    # entry_level == yesterday_high for momentum strategy
    assert result.initial_stop_price == round(result.entry_level - expected_buffer, 2)


def test_momentum_stop_falls_back_to_buffer_pct_when_insufficient_bars():
    from alpaca_bot.strategy.momentum import evaluate_momentum_signal
    settings = _make_settings(atr_period=50, breakout_stop_buffer_pct=0.001)
    daily_bars = _make_daily_bars(n=5, high=100.0)  # 5 < 50+1
    intraday_bars = _make_intraday_bars(n=6, high=102.0, close=101.5)
    result = evaluate_momentum_signal(
        symbol="AAPL",
        intraday_bars=intraday_bars,
        signal_index=len(intraday_bars) - 1,
        daily_bars=daily_bars,
        settings=settings,
    )
    assert result is not None
    expected_buffer = max(0.01, result.entry_level * 0.001)
    assert result.initial_stop_price == round(result.entry_level - expected_buffer, 2)
```

### 5c — Append to `tests/unit/test_orb_strategy.py`

Note: use `_make_intraday_bars_with_orb(orb_low=99.0)` — this is the existing helper.
The ORB anchor is `opening_range_low` (not `entry_level` which is `opening_range_high`).
Assert against the known `orb_low` value directly.

```python
def test_orb_stop_uses_atr_when_enough_daily_bars():
    from alpaca_bot.risk.atr import calculate_atr
    settings = _make_settings(atr_period=3, atr_stop_multiplier=1.5)
    daily_bars = _make_daily_bars(n=10)
    orb_low = 99.0
    intraday_bars, signal_index = _make_intraday_bars_with_orb(orb_low=orb_low)
    result = evaluate_orb_signal(
        symbol="AAPL",
        intraday_bars=intraday_bars,
        signal_index=signal_index,
        daily_bars=daily_bars,
        settings=settings,
    )
    assert result is not None
    atr = calculate_atr(daily_bars, 3)
    assert atr is not None
    expected_buffer = max(0.01, 1.5 * atr)
    # ORB anchor is opening_range_low, not entry_level (which is opening_range_high)
    assert result.initial_stop_price == round(orb_low - expected_buffer, 2)


def test_orb_stop_falls_back_to_buffer_pct_when_insufficient_bars():
    settings = _make_settings(atr_period=50, breakout_stop_buffer_pct=0.001)
    daily_bars = _make_daily_bars(n=5)  # 5 < 50+1
    orb_low = 99.0
    intraday_bars, signal_index = _make_intraday_bars_with_orb(orb_low=orb_low)
    result = evaluate_orb_signal(
        symbol="AAPL",
        intraday_bars=intraday_bars,
        signal_index=signal_index,
        daily_bars=daily_bars,
        settings=settings,
    )
    assert result is not None
    expected_buffer = max(0.01, orb_low * 0.001)
    assert result.initial_stop_price == round(orb_low - expected_buffer, 2)
```

### 5d — Append to `tests/unit/test_high_watermark_strategy.py`

```python
def test_high_watermark_stop_uses_atr_when_enough_daily_bars():
    from alpaca_bot.risk.atr import calculate_atr
    settings = _make_settings(atr_period=3, atr_stop_multiplier=1.5)
    daily_bars = _make_daily_bars(n=10, high_peak=150.0, high_base=100.0)
    intraday_bars, signal_index = _make_intraday_bars(signal_high=155.0, signal_close=154.0)
    result = evaluate_high_watermark_signal(
        symbol="AAPL",
        intraday_bars=intraday_bars,
        signal_index=signal_index,
        daily_bars=daily_bars,
        settings=settings,
    )
    assert result is not None
    atr = calculate_atr(daily_bars, 3)
    assert atr is not None
    expected_buffer = max(0.01, 1.5 * atr)
    # entry_level == historical_high for this strategy
    assert result.initial_stop_price == round(result.entry_level - expected_buffer, 2)


def test_high_watermark_stop_falls_back_to_buffer_pct_when_insufficient_bars():
    # high_watermark_lookback_days=5 so 5 daily bars are enough for signal but
    # atr_period=50 means calculate_atr returns None → falls back to buffer_pct
    settings = _make_settings(
        atr_period=50, breakout_stop_buffer_pct=0.001, high_watermark_lookback_days=5
    )
    daily_bars = _make_daily_bars(n=5, high_peak=150.0, high_base=100.0)
    intraday_bars, signal_index = _make_intraday_bars(signal_high=155.0, signal_close=154.0)
    result = evaluate_high_watermark_signal(
        symbol="AAPL",
        intraday_bars=intraday_bars,
        signal_index=signal_index,
        daily_bars=daily_bars,
        settings=settings,
    )
    assert result is not None
    expected_buffer = max(0.01, result.entry_level * 0.001)
    assert result.initial_stop_price == round(result.entry_level - expected_buffer, 2)
```

### 5e — Append to `tests/unit/test_ema_pullback_strategy.py`

```python
def test_ema_pullback_stop_uses_atr_when_enough_daily_bars():
    from alpaca_bot.risk.atr import calculate_atr
    settings = _make_settings(atr_period=3, atr_stop_multiplier=1.5)
    daily_bars = _make_daily_bars(n=10)
    intraday_bars, signal_index = _make_pullback_bars(
        trend_close=110.0, pullback_close=106.0, signal_close=115.0
    )
    result = evaluate_ema_pullback_signal(
        symbol="AAPL",
        intraday_bars=intraday_bars,
        signal_index=signal_index,
        daily_bars=daily_bars,
        settings=settings,
    )
    assert result is not None
    atr = calculate_atr(daily_bars, 3)
    assert atr is not None
    expected_buffer = max(0.01, 1.5 * atr)
    prior_bar_low = intraday_bars[signal_index - 1].low
    assert result.initial_stop_price == round(prior_bar_low - expected_buffer, 2)


def test_ema_pullback_stop_falls_back_to_buffer_pct_when_insufficient_bars():
    settings = _make_settings(atr_period=50, breakout_stop_buffer_pct=0.001)
    daily_bars = _make_daily_bars(n=5)  # 5 < 50+1
    intraday_bars, signal_index = _make_pullback_bars(
        trend_close=110.0, pullback_close=106.0, signal_close=115.0
    )
    result = evaluate_ema_pullback_signal(
        symbol="AAPL",
        intraday_bars=intraday_bars,
        signal_index=signal_index,
        daily_bars=daily_bars,
        settings=settings,
    )
    assert result is not None
    prior_bar_low = intraday_bars[signal_index - 1].low
    expected_buffer = max(0.01, prior_bar_low * 0.001)
    assert result.initial_stop_price == round(prior_bar_low - expected_buffer, 2)
```

**Test command:** `pytest -q`

---

## Task 6 — Full test suite

**Test command:** `pytest -q`

All existing tests must continue to pass. The existing strategy tests use `_make_settings()` which picks up the default `atr_period=14`. The `_make_daily_bars(n=10)` helpers provide 10 bars — for `atr_period=14`, `calculate_atr` returns None (needs 15 bars), so all existing tests fall back to the buffer-pct path. **No existing test assertions will change.**

---

## Implementation Notes

- `calculate_atr` is pure — takes only `Sequence[Bar]` and an int. No I/O. Safe inside `evaluate_cycle()`.
- The `max(0.01, ...)` guard in both paths ensures the stop is never flush against the anchor.
- `breakout_stop_buffer_pct` remains in Settings — it's still the warm-up fallback.
- Existing tests with `_make_daily_bars(n=10)` and default `atr_period=14` continue to use the buffer-pct fallback — no assertions change.
- `test_breakout_strategy.py` is a new file (no existing breakout-dedicated test file existed).
- ORB anchor is `opening_range_low`, not `entry_level` (which is `opening_range_high`) — use the known `orb_low` fixture value in assertions.
