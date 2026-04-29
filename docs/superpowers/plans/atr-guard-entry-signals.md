# Plan: Block Entry When ATR Unavailable

Spec: docs/superpowers/specs/atr-guard-entry-signals.md

## Summary

Add a 2-line ATR availability guard to each of the 5 entry-signal functions. When
`calculate_atr()` returns `None` (insufficient daily bars), the strategy returns `None`
immediately — no entry signal, no trade. The `atr_stop_buffer()` function is not changed.

Five existing tests that assert "fallback fires → signal returned" are updated to assert
"fallback fires → signal is None". No new files are created.

---

## Task 1 — Guard breakout strategy

**File:** `src/alpaca_bot/strategy/breakout.py`

Add `calculate_atr` to the existing import and insert the guard just before `atr_stop_buffer`.

```python
# Change import line from:
from alpaca_bot.risk.atr import atr_stop_buffer
# to:
from alpaca_bot.risk.atr import atr_stop_buffer, calculate_atr
```

In `evaluate_breakout_signal`, after the `relative_volume` check and before computing
`stop_price`:

```python
    if calculate_atr(daily_bars, settings.atr_period) is None:
        return None

    stop_price = round(breakout_level + settings.entry_stop_price_buffer, 2)
```

**Test command:** `pytest tests/unit/test_breakout_strategy.py -v`

---

## Task 2 — Guard momentum strategy

**File:** `src/alpaca_bot/strategy/momentum.py`

Same pattern. `prior_daily` is the bar sequence used for ATR in this strategy.

```python
# Change import:
from alpaca_bot.risk.atr import atr_stop_buffer, calculate_atr
```

In `evaluate_momentum_signal`, after the `relative_volume` check and before `stop_price`:

```python
    if calculate_atr(prior_daily, settings.atr_period) is None:
        return None

    stop_price = round(signal_bar.high + settings.entry_stop_price_buffer, 2)
```

**Test command:** `pytest tests/unit/test_momentum_strategy.py -v`

---

## Task 3 — Guard ORB strategy

**File:** `src/alpaca_bot/strategy/orb.py`

```python
from alpaca_bot.risk.atr import atr_stop_buffer, calculate_atr
```

In `evaluate_orb_signal`, after the `relative_volume` check and before `stop_price`:

```python
    if calculate_atr(daily_bars, settings.atr_period) is None:
        return None

    stop_price = round(signal_bar.high + settings.entry_stop_price_buffer, 2)
```

**Test command:** `pytest tests/unit/test_orb_strategy.py -v`

---

## Task 4 — Guard EMA pullback strategy

**File:** `src/alpaca_bot/strategy/ema_pullback.py`

```python
from alpaca_bot.risk.atr import atr_stop_buffer, calculate_atr
```

In `evaluate_ema_pullback_signal`, after the `relative_volume` check and before `stop_buffer`:

```python
    if calculate_atr(daily_bars, settings.atr_period) is None:
        return None

    prior_bar = intraday_bars[signal_index - 1]
    stop_buffer = atr_stop_buffer(...)
```

**Test command:** `pytest tests/unit/test_ema_pullback_strategy.py -v`

---

## Task 5 — Guard high_watermark strategy

**File:** `src/alpaca_bot/strategy/high_watermark.py`

```python
from alpaca_bot.risk.atr import atr_stop_buffer, calculate_atr
```

In `evaluate_high_watermark_signal`, after the `relative_volume` check and before `stop_price`:

```python
    if calculate_atr(daily_bars, settings.atr_period) is None:
        return None

    stop_price = round(historical_high + settings.entry_stop_price_buffer, 2)
```

**Test command:** `pytest tests/unit/test_high_watermark_strategy.py -v`

---

## Task 6 — Update tests: flip 5 fallback tests

Each of the 5 strategy test files has a `test_*_falls_back_to_buffer_pct_when_atr_returns_none`
test that currently asserts `result is not None` and checks the fallback stop price.
Rename and invert each to assert `result is None`.

### test_breakout_strategy.py

Rename `test_breakout_initial_stop_falls_back_to_buffer_pct_when_atr_returns_none`
→ `test_breakout_returns_none_when_atr_insufficient`

```python
def test_breakout_returns_none_when_atr_insufficient():
    settings = _make_settings(atr_period=3, daily_sma_period=2)
    daily_bars = _make_daily_bars(n=3)  # 3 < atr_period+1=4 → ATR returns None
    intraday_bars, signal_index = _make_breakout_intraday_bars()

    assert calculate_atr(daily_bars, 3) is None

    result = evaluate_breakout_signal(
        symbol="AAPL",
        intraday_bars=intraday_bars,
        signal_index=signal_index,
        daily_bars=daily_bars,
        settings=settings,
    )
    assert result is None
```

### test_momentum_strategy.py

Rename `test_momentum_initial_stop_falls_back_to_buffer_pct_when_atr_returns_none`
→ `test_momentum_returns_none_when_atr_insufficient`

```python
def test_momentum_returns_none_when_atr_insufficient():
    settings = _make_settings(atr_period=3, daily_sma_period=2)
    daily_bars = _make_daily_bars(n=3, high=100.0)  # ATR returns None
    intraday_bars, signal_index = _make_momentum_intraday_bars()

    assert calculate_atr(daily_bars, 3) is None

    result = evaluate_momentum_signal(
        symbol="AAPL",
        intraday_bars=intraday_bars,
        signal_index=signal_index,
        daily_bars=daily_bars,
        settings=settings,
    )
    assert result is None
```

### test_orb_strategy.py

Rename `test_orb_initial_stop_falls_back_to_buffer_pct_when_atr_returns_none`
→ `test_orb_returns_none_when_atr_insufficient`

```python
def test_orb_returns_none_when_atr_insufficient():
    settings = _make_settings(atr_period=3, daily_sma_period=2)
    daily_bars = _make_daily_bars(n=3)  # ATR returns None
    intraday_bars, signal_index = _make_orb_intraday_bars()

    assert calculate_atr(daily_bars, 3) is None

    result = evaluate_orb_signal(
        symbol="AAPL",
        intraday_bars=intraday_bars,
        signal_index=signal_index,
        daily_bars=daily_bars,
        settings=settings,
    )
    assert result is None
```

### test_ema_pullback_strategy.py

Rename `test_ema_pullback_initial_stop_falls_back_to_buffer_pct_when_atr_returns_none`
→ `test_ema_pullback_returns_none_when_atr_insufficient`

```python
def test_ema_pullback_returns_none_when_atr_insufficient():
    settings = _make_settings(atr_period=3, daily_sma_period=2)
    daily_bars = _make_daily_bars(n=3)  # ATR returns None
    intraday_bars, signal_index = _make_ema_pullback_intraday_bars()

    assert calculate_atr(daily_bars, 3) is None

    result = evaluate_ema_pullback_signal(
        symbol="AAPL",
        intraday_bars=intraday_bars,
        signal_index=signal_index,
        daily_bars=daily_bars,
        settings=settings,
    )
    assert result is None
```

### test_high_watermark_strategy.py

Rename `test_high_watermark_initial_stop_falls_back_to_buffer_pct_when_atr_returns_none`
→ `test_high_watermark_returns_none_when_atr_insufficient`

```python
def test_high_watermark_returns_none_when_atr_insufficient():
    settings = _make_settings(atr_period=50, daily_sma_period=2)
    # enough bars for high_watermark_lookback_days and trend filter, but atr_period=50 → None
    daily_bars = _make_daily_bars(n=11)

    assert calculate_atr(daily_bars, 50) is None

    result = evaluate_high_watermark_signal(
        symbol="AAPL",
        intraday_bars=...,  # use existing _make_high_watermark_intraday_bars() helper
        signal_index=...,
        daily_bars=daily_bars,
        settings=settings,
    )
    assert result is None
```

**Test command:** `pytest tests/unit/ -v`

---

## Task 7 — Fix test fixtures that relied on the fallback path

The happy-path replay tests and golden tests used daily bar counts that are below the ATR
period threshold, silently relying on the fallback. The guard now blocks those signals.

### test_replay_strategies.py — extend DAILY_BARS

`DAILY_BARS` currently has 7 bars; `ATR_PERIOD=10` needs 11. Prepend 4 bars (Jan 4–7) that
preserve the trend filter result (close values that don't upset the SMA > close condition).

```python
DAILY_BARS = [
    _daily(close=99.0, day=4),   # ← new
    _daily(close=99.0, day=5),   # ← new
    _daily(close=99.0, day=6),   # ← new
    _daily(close=99.0, day=7),   # ← new
    _daily(close=99.0, day=8),
    _daily(close=99.0, day=9),
    _daily(close=99.0, day=10),
    _daily(close=99.0, day=11),
    _daily(close=99.0, day=12),
    _daily(close=101.0, day=13),
    _daily(close=100.0, day=14),
]
# 11 bars ≥ ATR_PERIOD+1=11 → ATR computable ✓
# SMA window (daily_bars[-6:-1]) = Jan 9–13 → unchanged ✓
# prior_daily (dates < Jan 15) = all 11 bars → yesterday_high = bar[day=14].high=102.0 ✓
```

### test_replay_golden.py — lower ATR_PERIOD in make_settings()

Golden JSON fixtures have 21 daily bars; `ATR_PERIOD=50` needs 51. Lower ATR_PERIOD to `"14"`
(needs 15 bars; 21 >= 15). Golden test assertions check entry fill price, trailing stop value,
and exit price — none depend on ATR directly, so this does not break any assertion.

```python
# In make_settings() in test_replay_golden.py, change:
"ATR_PERIOD": "50",
# to:
"ATR_PERIOD": "14",
```

**Test command:** `pytest tests/unit/test_replay_strategies.py tests/unit/test_replay_golden.py -v`

---

## Task 8 — Full test suite

```
pytest tests/unit/ -v
```

All tests must pass.
