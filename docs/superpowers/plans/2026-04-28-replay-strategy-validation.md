# Plan: Replay Strategy Validation

**Spec:** `docs/superpowers/specs/2026-04-28-replay-strategy-validation.md`  
**Date:** 2026-04-28

---

## Overview

Create `tests/unit/test_replay_strategies.py` with 8 tests (happy path + guard per strategy) that exercise all four non-breakout strategies through `ReplayRunner.run()`. No production code changes.

---

## Task 1 — Create `tests/unit/test_replay_strategies.py`

### File: `tests/unit/test_replay_strategies.py`

Run with: `pytest tests/unit/test_replay_strategies.py -v`

---

### Preamble — imports and `make_settings()`

```python
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from alpaca_bot.config import Settings
from alpaca_bot.domain.enums import IntentType
from alpaca_bot.domain.models import Bar, ReplayScenario
from alpaca_bot.replay import ReplayRunner


def make_settings(**overrides: str) -> Settings:
    base = {
        "TRADING_MODE": "paper",
        "ENABLE_LIVE_TRADING": "false",
        "STRATEGY_VERSION": "v1-breakout",
        "DATABASE_URL": "postgresql://alpaca_bot:secret@db.example.com:5432/alpaca_bot",
        "MARKET_DATA_FEED": "sip",
        "SYMBOLS": "TSLA",
        "DAILY_SMA_PERIOD": "5",
        "BREAKOUT_LOOKBACK_BARS": "5",
        "RELATIVE_VOLUME_LOOKBACK_BARS": "5",
        "RELATIVE_VOLUME_THRESHOLD": "1.5",
        "ENTRY_TIMEFRAME_MINUTES": "15",
        "RISK_PER_TRADE_PCT": "0.0025",
        "MAX_POSITION_PCT": "0.05",
        "MAX_OPEN_POSITIONS": "3",
        "DAILY_LOSS_LIMIT_PCT": "0.01",
        "STOP_LIMIT_BUFFER_PCT": "0.001",
        "BREAKOUT_STOP_BUFFER_PCT": "0.001",
        "ENTRY_STOP_PRICE_BUFFER": "0.01",
        "ENTRY_WINDOW_START": "10:00",
        "ENTRY_WINDOW_END": "15:30",
        "FLATTEN_TIME": "15:45",
        "ATR_PERIOD": "10",
        "ATR_STOP_MULTIPLIER": "1.0",
        "ORB_OPENING_BARS": "2",
        "HIGH_WATERMARK_LOOKBACK_DAYS": "5",
        "EMA_PERIOD": "5",
        "PRIOR_DAY_HIGH_LOOKBACK_BARS": "1",
    }
    base.update(overrides)
    return Settings.from_env(base)
```

---

### Bar helpers

All intraday bars use UTC timestamps on 2024-01-15 starting at 15:00 UTC (= 10:00 ET, the entry window open). January is ET = UTC-5.

```python
def _bar(
    symbol: str,
    hour: int,
    minute: int,
    *,
    open_: float,
    high: float,
    low: float,
    close: float,
    volume: int,
    day: int = 15,
) -> Bar:
    """Build a 2024-01-{day} intraday bar at the given UTC hour:minute."""
    ts = datetime(2024, 1, day, hour, minute, tzinfo=timezone.utc)
    return Bar(symbol=symbol, timestamp=ts, open=open_, high=high, low=low, close=close, volume=volume)


def _daily(close: float, high: float = 102.0, low: float = 98.0, day: int = 1) -> Bar:
    """Build a daily bar on 2024-01-{day}."""
    ts = datetime(2024, 1, day, 0, 0, tzinfo=timezone.utc)
    return Bar(symbol="TSLA", timestamp=ts, open=close - 0.5, high=high, low=low, close=close, volume=500_000)
```

---

### Shared daily bar fixture

The trend filter (`daily_trend_filter_passes`) with `DAILY_SMA_PERIOD=5` checks:
- `window = daily_bars[-6:-1]` (5 bars, excludes last)
- SMA of those 5 bars' closes
- `daily_bars[-2].close > SMA`

Seven bars (Jan 8–14) satisfying the filter:
- Jan 8–12 close = 99.0 (5 bars)
- Jan 13 close = 101.0 (`daily_bars[-2]` when 7 bars passed, = last-before-today)
- Jan 14 close = 100.0 (today's partial, `daily_bars[-1]`)

Trend filter: SMA of Jan 9–13 = (99+99+99+99+101)/5 = 99.4; Jan 13 close (101.0) > 99.4 ✓

```python
DAILY_BARS = [
    _daily(close=99.0, day=8),
    _daily(close=99.0, day=9),
    _daily(close=99.0, day=10),
    _daily(close=99.0, day=11),
    _daily(close=99.0, day=12),
    _daily(close=101.0, day=13),  # daily_bars[-2] when 7 bars total
    _daily(close=100.0, day=14),  # today's partial bar
]
```

---

### Strategy-specific signal construction

#### momentum

Signal logic (from `strategy/momentum.py`):
- `prior_daily[-1].high` = yesterday's high (Jan 14) = `high=102.0` in `DAILY_BARS[-1]`
- Signal bar: `high > 102.0 AND close > 102.0 AND relative_volume ≥ 1.5`
- Volume baseline: 5 prior intraday bars (indices 0–4), each `volume=1000`

**Intraday bars** (Jan 15, all 10:00–15:30 ET):
```
index 0: 15:00 UTC — warm-up, close=100.0, volume=1000
index 1: 15:15 UTC — warm-up, close=100.0, volume=1000
index 2: 15:30 UTC — warm-up, close=100.0, volume=1000
index 3: 15:45 UTC — warm-up, close=100.0, volume=1000
index 4: 16:00 UTC — warm-up, close=100.0, volume=1000
index 5: 16:15 UTC — SIGNAL BAR: high=105.0, close=103.5, volume=2000
index 6: 16:30 UTC — EXECUTION BAR (fills entry order placed at index 5)
```

Signal bar: `high=105.0 > 102.0 ✓`, `close=103.5 > 102.0 ✓`, `relative_volume=2.0 > 1.5 ✓`

Signal produces:
- `stop_price = round(105.0 + 0.01, 2) = 105.01`
- `limit_price = round(105.01 * 1.001, 2) = 105.12`

Execution bar: `open=105.01, high=106.0, low=104.5` → `fill_price = max(105.01, 105.01) = 105.01 ✓`

**No more intraday bars** (no FLATTEN_TIME bar, so no EOD exit — result has just ENTRY_ORDER_PLACED + ENTRY_FILLED).

For momentum we pass `DAILY_BARS` (Jan 8–14). Inside `evaluate_momentum_signal`, `prior_daily` filters to `date < 2024-01-15`, which yields all 7 bars (all have raw UTC date ≤ Jan 14). `prior_daily[-2].close` = Jan 13 close = 101.0. Trend filter uses `prior_daily[-6:-1]` = Jan 9–13. SMA = 99.4. 101.0 > 99.4 ✓.

---

#### orb

Signal logic (from `strategy/orb.py`):
- Opening range = first `ORB_OPENING_BARS=2` bars of the session
- Signal bar: `high > opening_range_high AND close > opening_range_high AND relative_volume ≥ 1.5`
- Volume baseline: avg of opening range bars

Trend filter called on `DAILY_BARS` directly (not filtered by date): `daily_bars[-2].close` = Jan 13 close = 101.0 > 99.4 ✓

**Intraday bars** (Jan 15, all 10:00–15:30 ET):
```
index 0: 15:00 UTC — opening range bar 1: high=100.5, volume=1000
index 1: 15:15 UTC — opening range bar 2: high=100.8, volume=1000
index 2: 15:30 UTC — SIGNAL BAR: high=101.5, close=101.2, volume=2000
index 3: 15:45 UTC — EXECUTION BAR
```

Opening range high = 100.8. Signal bar: `high=101.5 > 100.8 ✓`, `close=101.2 > 100.8 ✓`, `relative_volume = 2000/1000 = 2.0 ✓`

`stop_price = round(101.5 + 0.01, 2) = 101.51`
`limit_price = round(101.51 * 1.001, 2) = 101.61`

Execution bar: `open=101.51, high=102.5, low=101.0` → fill at 101.51 ✓

---

#### high_watermark

Signal logic (from `strategy/high_watermark.py`):
- `completed_bars = daily_bars[:-1]` (excludes today's partial)
- `historical_bars = completed_bars[-HIGH_WATERMARK_LOOKBACK_DAYS:]` = last 5 completed
- `historical_high = max(bar.high for bar in historical_bars)`
- Signal bar: `high > historical_high AND close > historical_high AND relative_volume ≥ 1.5`

With `DAILY_BARS`:
- `completed_bars` = Jan 8–13 (6 bars, all with `high=102.0`)
- `historical_bars` = Jan 9–13 (last 5), all `high=102.0`
- `historical_high = 102.0`

Signal bar needs `high > 102.0 AND close > 102.0`.

Trend filter uses full `daily_bars` (Jan 8–14): window = Jan 9–13, SMA = 99.4, `daily_bars[-2].close` = Jan 13 close = 101.0 > 99.4 ✓

**Intraday bars** (Jan 15):
```
index 0: 15:00 UTC — warm-up, close=100.0, volume=1000
index 1: 15:15 UTC — warm-up, close=100.0, volume=1000
index 2: 15:30 UTC — warm-up, close=100.0, volume=1000
index 3: 15:45 UTC — warm-up, close=100.0, volume=1000
index 4: 16:00 UTC — warm-up, close=100.0, volume=1000
index 5: 16:15 UTC — SIGNAL BAR: high=103.5, close=103.0, volume=2000
index 6: 16:30 UTC — EXECUTION BAR
```

`relative_volume = 2000/1000 = 2.0 > 1.5 ✓`
`stop_price = round(102.0 + 0.01, 2) = 102.01`  (uses `historical_high`, not signal bar high)
`limit_price = round(102.01 * 1.001, 2) = 102.11`

Execution bar: `open=102.01, high=104.0, low=101.5` → fill at 102.01 ✓

---

#### ema_pullback

Signal logic (from `strategy/ema_pullback.py`):
- `signal_index >= ema_period = 5`
- `prior_bar.close <= ema_at_(signal_index - 1)` (pullback condition)
- `signal_bar.close > ema_at_signal_index`

EMA warmup with `alpha = 2/(5+1) = 1/3`:
- bars 0–3: close=100.0 → ema stays at 100.0
- bar 4 (prior_bar): close=95.0 → `ema_4 = 1/3*95 + 2/3*100 = 98.33`
- bar 5 (signal_bar): close=105.0 → `ema_5 = 1/3*105 + 2/3*98.33 = 100.55`

Prior bar: close=95.0 ≤ ema_4=98.33 ✓
Signal bar: close=105.0 > ema_5=100.55 ✓

`relative_volume`: prior 5 bars (indices 0–4), each volume=1000, signal bar volume=2000 → 2.0 ✓

`stop_price = round(signal_bar.high + 0.01, 2)`. Let `signal_bar.high=106.0` → `stop_price=106.01`
`limit_price = round(106.01 * 1.001, 2) = 106.12`

Trend filter on `DAILY_BARS`: `daily_bars[-2].close` = Jan 13 close = 101.0 > 99.4 ✓

**Intraday bars** (Jan 15):
```
index 0: 15:00 UTC — close=100.0, volume=1000
index 1: 15:15 UTC — close=100.0, volume=1000
index 2: 15:30 UTC — close=100.0, volume=1000
index 3: 15:45 UTC — close=100.0, volume=1000
index 4: 16:00 UTC — PRIOR BAR: close=95.0, low=94.0, volume=1000
index 5: 16:15 UTC — SIGNAL BAR: close=105.0, high=106.0, volume=2000
index 6: 16:30 UTC — EXECUTION BAR
```

Execution bar: `open=106.01, high=107.0, low=105.5` → fill at 106.01 ✓

---

### Guard tests (insufficient data → 0 trades)

Each guard test passes only 2 daily bars (fewer than `DAILY_SMA_PERIOD + 1 = 6`). The trend filter returns `False`, so no signal fires regardless of intraday bars.

---

### Complete test file

```python
from __future__ import annotations

from datetime import datetime, timezone

from alpaca_bot.config import Settings
from alpaca_bot.domain.enums import IntentType
from alpaca_bot.domain.models import Bar, ReplayScenario
from alpaca_bot.replay import ReplayRunner
from alpaca_bot.strategy import STRATEGY_REGISTRY


def make_settings(**overrides: str) -> Settings:
    base = {
        "TRADING_MODE": "paper",
        "ENABLE_LIVE_TRADING": "false",
        "STRATEGY_VERSION": "v1-breakout",
        "DATABASE_URL": "postgresql://alpaca_bot:secret@db.example.com:5432/alpaca_bot",
        "MARKET_DATA_FEED": "sip",
        "SYMBOLS": "TSLA",
        "DAILY_SMA_PERIOD": "5",
        "BREAKOUT_LOOKBACK_BARS": "5",
        "RELATIVE_VOLUME_LOOKBACK_BARS": "5",
        "RELATIVE_VOLUME_THRESHOLD": "1.5",
        "ENTRY_TIMEFRAME_MINUTES": "15",
        "RISK_PER_TRADE_PCT": "0.0025",
        "MAX_POSITION_PCT": "0.05",
        "MAX_OPEN_POSITIONS": "3",
        "DAILY_LOSS_LIMIT_PCT": "0.01",
        "STOP_LIMIT_BUFFER_PCT": "0.001",
        "BREAKOUT_STOP_BUFFER_PCT": "0.001",
        "ENTRY_STOP_PRICE_BUFFER": "0.01",
        "ENTRY_WINDOW_START": "10:00",
        "ENTRY_WINDOW_END": "15:30",
        "FLATTEN_TIME": "15:45",
        "ATR_PERIOD": "10",
        "ATR_STOP_MULTIPLIER": "1.0",
        "ORB_OPENING_BARS": "2",
        "HIGH_WATERMARK_LOOKBACK_DAYS": "5",
        "EMA_PERIOD": "5",
        "PRIOR_DAY_HIGH_LOOKBACK_BARS": "1",
    }
    base.update(overrides)
    return Settings.from_env(base)


def _bar(
    symbol: str,
    hour: int,
    minute: int,
    *,
    open_: float,
    high: float,
    low: float,
    close: float,
    volume: int,
    day: int = 15,
) -> Bar:
    ts = datetime(2024, 1, day, hour, minute, tzinfo=timezone.utc)
    return Bar(symbol=symbol, timestamp=ts, open=open_, high=high, low=low, close=close, volume=volume)


def _daily(close: float, high: float = 102.0, low: float = 98.0, day: int = 1) -> Bar:
    ts = datetime(2024, 1, day, 0, 0, tzinfo=timezone.utc)
    return Bar(symbol="TSLA", timestamp=ts, open=close - 0.5, high=high, low=low, close=close, volume=500_000)


# Seven daily bars (Jan 8–14) satisfying the DAILY_SMA_PERIOD=5 trend filter:
#   window = daily_bars[-6:-1] = Jan 9–13, SMA = 99.4
#   daily_bars[-2].close = Jan 13 close = 101.0 > 99.4  ✓
DAILY_BARS = [
    _daily(close=99.0, day=8),
    _daily(close=99.0, day=9),
    _daily(close=99.0, day=10),
    _daily(close=99.0, day=11),
    _daily(close=99.0, day=12),
    _daily(close=101.0, day=13),
    _daily(close=100.0, day=14),
]


# ─── momentum ───────────────────────────────────────────────────────────────

def test_momentum_happy_path_produces_entry_filled() -> None:
    """Full cycle: momentum signal fires, entry order placed, next bar fills it."""
    settings = make_settings()
    intraday_bars = [
        _bar("TSLA", 15, 0,  open_=99.8,  high=100.2, low=99.5,  close=100.0, volume=1000),
        _bar("TSLA", 15, 15, open_=99.9,  high=100.3, low=99.7,  close=100.0, volume=1000),
        _bar("TSLA", 15, 30, open_=100.0, high=100.4, low=99.8,  close=100.0, volume=1000),
        _bar("TSLA", 15, 45, open_=100.0, high=100.5, low=99.8,  close=100.0, volume=1000),
        _bar("TSLA", 16, 0,  open_=100.0, high=100.5, low=99.9,  close=100.0, volume=1000),
        # Signal bar: high=105.0 and close=103.5 both exceed yesterday_high=102.0
        _bar("TSLA", 16, 15, open_=102.0, high=105.0, low=101.8, close=103.5, volume=2000),
        # Execution bar: open triggers the stop-limit (stop=105.01, limit=105.12)
        _bar("TSLA", 16, 30, open_=105.01, high=106.0, low=104.5, close=105.5, volume=1500),
    ]
    scenario = ReplayScenario(
        name="momentum_happy",
        symbol="TSLA",
        starting_equity=100_000.0,
        daily_bars=DAILY_BARS,
        intraday_bars=intraday_bars,
    )
    result = ReplayRunner(settings, signal_evaluator=STRATEGY_REGISTRY["momentum"], strategy_name="momentum").run(scenario)
    event_types = [e.event_type for e in result.events]
    assert IntentType.ENTRY_ORDER_PLACED in event_types
    assert IntentType.ENTRY_FILLED in event_types


def test_momentum_insufficient_daily_bars_produces_no_trades() -> None:
    """Trend filter requires DAILY_SMA_PERIOD+1 = 6 daily bars; 2 bars → no signal."""
    settings = make_settings()
    intraday_bars = [
        _bar("TSLA", 15, 0,  open_=99.8,  high=100.2, low=99.5,  close=100.0, volume=1000),
        _bar("TSLA", 15, 15, open_=99.9,  high=100.3, low=99.7,  close=100.0, volume=1000),
        _bar("TSLA", 15, 30, open_=100.0, high=100.4, low=99.8,  close=100.0, volume=1000),
        _bar("TSLA", 15, 45, open_=100.0, high=100.5, low=99.8,  close=100.0, volume=1000),
        _bar("TSLA", 16, 0,  open_=100.0, high=100.5, low=99.9,  close=100.0, volume=1000),
        _bar("TSLA", 16, 15, open_=102.0, high=105.0, low=101.8, close=103.5, volume=2000),
        _bar("TSLA", 16, 30, open_=105.01, high=106.0, low=104.5, close=105.5, volume=1500),
    ]
    short_daily = [_daily(close=99.0, day=13), _daily(close=101.0, day=14)]
    scenario = ReplayScenario(
        name="momentum_no_daily",
        symbol="TSLA",
        starting_equity=100_000.0,
        daily_bars=short_daily,
        intraday_bars=intraday_bars,
    )
    result = ReplayRunner(settings, signal_evaluator=STRATEGY_REGISTRY["momentum"], strategy_name="momentum").run(scenario)
    assert result.events == []


# ─── orb ────────────────────────────────────────────────────────────────────

def test_orb_happy_path_produces_entry_filled() -> None:
    """Full cycle: ORB breakout fires after 2-bar opening range, next bar fills."""
    settings = make_settings()
    intraday_bars = [
        # Opening range (ORB_OPENING_BARS=2)
        _bar("TSLA", 15, 0,  open_=100.0, high=100.5, low=99.5, close=100.2, volume=1000),
        _bar("TSLA", 15, 15, open_=100.2, high=100.8, low=99.8, close=100.5, volume=1000),
        # Signal bar: high=101.5 and close=101.2 both exceed opening_range_high=100.8
        _bar("TSLA", 15, 30, open_=100.5, high=101.5, low=100.4, close=101.2, volume=2000),
        # Execution bar: stop=101.51, limit=101.61
        _bar("TSLA", 15, 45, open_=101.51, high=102.5, low=101.0, close=102.0, volume=1500),
    ]
    scenario = ReplayScenario(
        name="orb_happy",
        symbol="TSLA",
        starting_equity=100_000.0,
        daily_bars=DAILY_BARS,
        intraday_bars=intraday_bars,
    )
    result = ReplayRunner(settings, signal_evaluator=STRATEGY_REGISTRY["orb"], strategy_name="orb").run(scenario)
    event_types = [e.event_type for e in result.events]
    assert IntentType.ENTRY_ORDER_PLACED in event_types
    assert IntentType.ENTRY_FILLED in event_types


def test_orb_insufficient_daily_bars_produces_no_trades() -> None:
    """Trend filter blocks signal when fewer than DAILY_SMA_PERIOD+1 daily bars exist."""
    settings = make_settings()
    intraday_bars = [
        _bar("TSLA", 15, 0,  open_=100.0, high=100.5, low=99.5, close=100.2, volume=1000),
        _bar("TSLA", 15, 15, open_=100.2, high=100.8, low=99.8, close=100.5, volume=1000),
        _bar("TSLA", 15, 30, open_=100.5, high=101.5, low=100.4, close=101.2, volume=2000),
        _bar("TSLA", 15, 45, open_=101.51, high=102.5, low=101.0, close=102.0, volume=1500),
    ]
    short_daily = [_daily(close=99.0, day=13), _daily(close=101.0, day=14)]
    scenario = ReplayScenario(
        name="orb_no_daily",
        symbol="TSLA",
        starting_equity=100_000.0,
        daily_bars=short_daily,
        intraday_bars=intraday_bars,
    )
    result = ReplayRunner(settings, signal_evaluator=STRATEGY_REGISTRY["orb"], strategy_name="orb").run(scenario)
    assert result.events == []


# ─── high_watermark ──────────────────────────────────────────────────────────

def test_high_watermark_happy_path_produces_entry_filled() -> None:
    """Signal fires when price exceeds 5-day high watermark; execution bar fills."""
    settings = make_settings()
    # DAILY_BARS[-1].high=102.0 → historical_high for completed[-5:] = 102.0
    # Signal bar must have high>102.0 and close>102.0
    intraday_bars = [
        _bar("TSLA", 15, 0,  open_=100.0, high=100.5, low=99.8,  close=100.2, volume=1000),
        _bar("TSLA", 15, 15, open_=100.2, high=100.7, low=100.0, close=100.4, volume=1000),
        _bar("TSLA", 15, 30, open_=100.4, high=100.9, low=100.2, close=100.6, volume=1000),
        _bar("TSLA", 15, 45, open_=100.6, high=101.2, low=100.4, close=101.0, volume=1000),
        _bar("TSLA", 16, 0,  open_=101.0, high=101.5, low=100.8, close=101.2, volume=1000),
        # Signal bar: high=103.5 and close=103.0 exceed historical_high=102.0
        _bar("TSLA", 16, 15, open_=102.0, high=103.5, low=101.8, close=103.0, volume=2000),
        # Execution bar: stop_price=round(102.0+0.01,2)=102.01, limit=102.11
        _bar("TSLA", 16, 30, open_=102.01, high=104.0, low=101.5, close=103.5, volume=1500),
    ]
    scenario = ReplayScenario(
        name="high_watermark_happy",
        symbol="TSLA",
        starting_equity=100_000.0,
        daily_bars=DAILY_BARS,
        intraday_bars=intraday_bars,
    )
    result = ReplayRunner(settings, signal_evaluator=STRATEGY_REGISTRY["high_watermark"], strategy_name="high_watermark").run(scenario)
    event_types = [e.event_type for e in result.events]
    assert IntentType.ENTRY_ORDER_PLACED in event_types
    assert IntentType.ENTRY_FILLED in event_types


def test_high_watermark_insufficient_daily_bars_produces_no_trades() -> None:
    """Requires HIGH_WATERMARK_LOOKBACK_DAYS=5 completed bars + trend filter; 2 bars → no trades."""
    settings = make_settings()
    intraday_bars = [
        _bar("TSLA", 15, 0,  open_=100.0, high=100.5, low=99.8,  close=100.2, volume=1000),
        _bar("TSLA", 15, 15, open_=100.2, high=100.7, low=100.0, close=100.4, volume=1000),
        _bar("TSLA", 15, 30, open_=100.4, high=100.9, low=100.2, close=100.6, volume=1000),
        _bar("TSLA", 15, 45, open_=100.6, high=101.2, low=100.4, close=101.0, volume=1000),
        _bar("TSLA", 16, 0,  open_=101.0, high=101.5, low=100.8, close=101.2, volume=1000),
        _bar("TSLA", 16, 15, open_=102.0, high=103.5, low=101.8, close=103.0, volume=2000),
        _bar("TSLA", 16, 30, open_=102.01, high=104.0, low=101.5, close=103.5, volume=1500),
    ]
    short_daily = [_daily(close=99.0, day=13), _daily(close=101.0, day=14)]
    scenario = ReplayScenario(
        name="high_watermark_no_daily",
        symbol="TSLA",
        starting_equity=100_000.0,
        daily_bars=short_daily,
        intraday_bars=intraday_bars,
    )
    result = ReplayRunner(settings, signal_evaluator=STRATEGY_REGISTRY["high_watermark"], strategy_name="high_watermark").run(scenario)
    assert result.events == []


# ─── ema_pullback ────────────────────────────────────────────────────────────

def test_ema_pullback_happy_path_produces_entry_filled() -> None:
    """EMA pullback: prior bar closes below EMA, signal bar closes above EMA; fills."""
    settings = make_settings()
    # EMA warmup (alpha=1/3): bars 0-3 close=100.0 → ema_3=100.0
    # bar 4 (prior): close=95.0 → ema_4 = 1/3*95 + 2/3*100 = 98.33; close≤ema ✓
    # bar 5 (signal): close=105.0 → ema_5 = 1/3*105 + 2/3*98.33 = 100.55; close>ema ✓
    intraday_bars = [
        _bar("TSLA", 15, 0,  open_=99.8,  high=100.3, low=99.5,  close=100.0, volume=1000),
        _bar("TSLA", 15, 15, open_=99.9,  high=100.4, low=99.7,  close=100.0, volume=1000),
        _bar("TSLA", 15, 30, open_=100.0, high=100.5, low=99.8,  close=100.0, volume=1000),
        _bar("TSLA", 15, 45, open_=100.0, high=100.5, low=99.8,  close=100.0, volume=1000),
        # Prior bar: close=95.0 ≤ ema_4=98.33 (pullback)
        _bar("TSLA", 16, 0,  open_=97.0,  high=97.5,  low=94.0,  close=95.0,  volume=1000),
        # Signal bar: close=105.0 > ema_5=100.55, high=106.0
        _bar("TSLA", 16, 15, open_=100.0, high=106.0, low=99.5,  close=105.0, volume=2000),
        # Execution bar: stop=106.01, limit=106.17
        _bar("TSLA", 16, 30, open_=106.01, high=107.0, low=105.5, close=106.5, volume=1500),
    ]
    scenario = ReplayScenario(
        name="ema_pullback_happy",
        symbol="TSLA",
        starting_equity=100_000.0,
        daily_bars=DAILY_BARS,
        intraday_bars=intraday_bars,
    )
    result = ReplayRunner(settings, signal_evaluator=STRATEGY_REGISTRY["ema_pullback"], strategy_name="ema_pullback").run(scenario)
    event_types = [e.event_type for e in result.events]
    assert IntentType.ENTRY_ORDER_PLACED in event_types
    assert IntentType.ENTRY_FILLED in event_types


def test_ema_pullback_insufficient_daily_bars_produces_no_trades() -> None:
    """Trend filter with DAILY_SMA_PERIOD=5 requires 6+ bars; 2 bars → no trades."""
    settings = make_settings()
    intraday_bars = [
        _bar("TSLA", 15, 0,  open_=99.8,  high=100.3, low=99.5,  close=100.0, volume=1000),
        _bar("TSLA", 15, 15, open_=99.9,  high=100.4, low=99.7,  close=100.0, volume=1000),
        _bar("TSLA", 15, 30, open_=100.0, high=100.5, low=99.8,  close=100.0, volume=1000),
        _bar("TSLA", 15, 45, open_=100.0, high=100.5, low=99.8,  close=100.0, volume=1000),
        _bar("TSLA", 16, 0,  open_=97.0,  high=97.5,  low=94.0,  close=95.0,  volume=1000),
        _bar("TSLA", 16, 15, open_=100.0, high=106.0, low=99.5,  close=105.0, volume=2000),
        _bar("TSLA", 16, 30, open_=106.01, high=107.0, low=105.5, close=106.5, volume=1500),
    ]
    short_daily = [_daily(close=99.0, day=13), _daily(close=101.0, day=14)]
    scenario = ReplayScenario(
        name="ema_pullback_no_daily",
        symbol="TSLA",
        starting_equity=100_000.0,
        daily_bars=short_daily,
        intraday_bars=intraday_bars,
    )
    result = ReplayRunner(settings, signal_evaluator=STRATEGY_REGISTRY["ema_pullback"], strategy_name="ema_pullback").run(scenario)
    assert result.events == []
```

---

## Verification

```bash
pytest tests/unit/test_replay_strategies.py -v
pytest tests/unit/ -x -q   # full suite — no regressions
```

Expected: 8 new tests pass, all existing tests continue to pass.

---

## Notes

- **No production code changes.** This plan touches only the test file.
- **ATR_STOP_MULTIPLIER=1.0** keeps `initial_stop_price` predictable: `stop_buffer ≈ ATR * 1.0`.
- **`signal_evaluator` must be passed explicitly.** `evaluate_cycle()` defaults to `evaluate_breakout_signal` when `signal_evaluator=None`; `strategy_name` only labels the report. Mirror what `_cmd_compare` does: `ReplayRunner(settings, signal_evaluator=STRATEGY_REGISTRY[name], strategy_name=name)`.
- **ENTRY_WINDOW guard**: all signal bars are at 16:00–16:15 UTC = 11:00–11:15 ET, safely inside 10:00–15:30.
- **Execution bar design**: `open == stop_price` so `fill_price = max(open, stop_price) = stop_price`. This is the simplest fill scenario — no slippage or gap-open complications.
- **ema_pullback limit_price**: `round(106.01 * 1.001, 2)`. Python: `round(106.116, 2) = 106.12`. Execution bar `open=106.01 ≤ 106.12 ✓`.
