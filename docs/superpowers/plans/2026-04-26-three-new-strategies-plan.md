# Plan: Three New Trading Strategies

**Date:** 2026-04-26  
**Spec:** `2026-04-26-three-new-strategies-spec.md`  
**Status:** Grilled and refined

## Execution Order

Tasks 1 → 2 must run before Tasks 3–5 (Settings fields and supervisor fix are required by the signal functions). Tasks 3–5 are independent of each other. Tasks 6–8 depend on Tasks 3–5. Run `pytest` after Task 8.

---

## Task 1 — Add new Settings fields

**File:** `src/alpaca_bot/config/__init__.py`

Add three fields to the `Settings` dataclass after `prior_day_high_lookback_bars`:

```python
orb_opening_bars: int = 2
high_watermark_lookback_days: int = 252
ema_period: int = 9
```

In `from_env()`, after the `prior_day_high_lookback_bars` line:

```python
orb_opening_bars=int(values.get("ORB_OPENING_BARS", "2")),
high_watermark_lookback_days=int(values.get("HIGH_WATERMARK_LOOKBACK_DAYS", "252")),
ema_period=int(values.get("EMA_PERIOD", "9")),
```

In `validate()`, after the `prior_day_high_lookback_bars` check:

```python
if self.orb_opening_bars < 1:
    raise ValueError("ORB_OPENING_BARS must be at least 1")
if self.high_watermark_lookback_days < 5:
    raise ValueError("HIGH_WATERMARK_LOOKBACK_DAYS must be at least 5")
if self.ema_period < 2:
    raise ValueError("EMA_PERIOD must be at least 2")
```

**Test command:** `pytest tests/unit/test_momentum_strategy.py -v` (existing tests must still pass; new Settings test in Task 7)

---

## Task 2 — Widen daily bar fetch window in supervisor

**File:** `src/alpaca_bot/runtime/supervisor.py`, line 246

Change:
```python
start=timestamp - timedelta(days=max(self.settings.daily_sma_period * 3, 60)),
```
To:
```python
start=timestamp - timedelta(days=max(
    self.settings.daily_sma_period * 3,
    60,
    self.settings.high_watermark_lookback_days + 10,
)),
```

The `+10` covers weekends and holidays. With default `high_watermark_lookback_days=252`, this fetches 262 calendar days — enough to return ~252 trading days.

**No test required for this line alone** — it is covered implicitly by the high_watermark strategy tests (which will verify the strategy fires when given sufficient daily bars).

---

## Task 3 — Opening Range Breakout strategy

**File:** `src/alpaca_bot/strategy/orb.py` (new file)

**Critical design note:** `intraday_bars` spans up to 5 calendar days (supervisor fetches `start=now - 5 days`). Using `intraday_bars[:orb_opening_bars]` would grab bars from 5 days ago, not today's session open. ORB must filter to the current session date before computing the opening range.

```python
from __future__ import annotations

from collections.abc import Sequence

from alpaca_bot.config import Settings
from alpaca_bot.domain.models import Bar, EntrySignal
from alpaca_bot.strategy.breakout import daily_trend_filter_passes, is_entry_session_time, session_day


def evaluate_orb_signal(
    *,
    symbol: str,
    intraday_bars: Sequence[Bar],
    signal_index: int,
    daily_bars: Sequence[Bar],
    settings: Settings,
) -> EntrySignal | None:
    if symbol not in settings.symbols:
        return None
    if not intraday_bars or signal_index < 0 or signal_index >= len(intraday_bars):
        return None

    signal_bar = intraday_bars[signal_index]
    if signal_bar.symbol != symbol:
        return None
    if not is_entry_session_time(signal_bar.timestamp, settings):
        return None
    if not daily_trend_filter_passes(daily_bars, settings):
        return None

    # Filter to today's session — intraday_bars spans multiple days
    signal_date = session_day(signal_bar.timestamp, settings)
    today_bars = [
        bar for bar in intraday_bars[: signal_index + 1]
        if session_day(bar.timestamp, settings) == signal_date
    ]

    # Signal bar must follow at least orb_opening_bars bars within today's session
    if len(today_bars) <= settings.orb_opening_bars:
        return None

    opening_range_bars = today_bars[: settings.orb_opening_bars]
    opening_range_high = max(bar.high for bar in opening_range_bars)
    opening_range_low = min(bar.low for bar in opening_range_bars)

    if signal_bar.high <= opening_range_high:
        return None
    if signal_bar.close <= opening_range_high:
        return None

    avg_volume = sum(bar.volume for bar in opening_range_bars) / len(opening_range_bars)
    relative_volume = signal_bar.volume / avg_volume if avg_volume > 0 else 0.0
    if relative_volume < settings.relative_volume_threshold:
        return None

    entry_level = opening_range_high
    stop_price = round(signal_bar.high + settings.entry_stop_price_buffer, 2)
    limit_price = round(stop_price * (1 + settings.stop_limit_buffer_pct), 2)
    stop_buffer = max(0.01, opening_range_low * settings.breakout_stop_buffer_pct)
    initial_stop_price = round(opening_range_low - stop_buffer, 2)

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

**Key design notes:**
- Session-date filter is essential: without it, `intraday_bars[:orb_opening_bars]` would use bars from days ago.
- Volume baseline uses opening range bars (not `relative_volume_lookback_bars`) so the signal can fire immediately after the range — typical ORB entries happen within the first hour.
- `initial_stop_price` is below the opening range low — a structural stop tied to session support.

---

## Task 4 — N-Day High Watermark strategy

**File:** `src/alpaca_bot/strategy/high_watermark.py` (new file)

```python
from __future__ import annotations

from collections.abc import Sequence

from alpaca_bot.config import Settings
from alpaca_bot.domain.models import Bar, EntrySignal
from alpaca_bot.strategy.breakout import daily_trend_filter_passes, is_entry_session_time


def evaluate_high_watermark_signal(
    *,
    symbol: str,
    intraday_bars: Sequence[Bar],
    signal_index: int,
    daily_bars: Sequence[Bar],
    settings: Settings,
) -> EntrySignal | None:
    if symbol not in settings.symbols:
        return None
    if not intraday_bars or signal_index < 0 or signal_index >= len(intraday_bars):
        return None

    signal_bar = intraday_bars[signal_index]
    if signal_bar.symbol != symbol:
        return None
    if not is_entry_session_time(signal_bar.timestamp, settings):
        return None
    if not daily_trend_filter_passes(daily_bars, settings):
        return None

    if len(daily_bars) < settings.high_watermark_lookback_days:
        return None

    historical_bars = daily_bars[-settings.high_watermark_lookback_days :]
    historical_high = max(bar.high for bar in historical_bars)

    if signal_bar.high <= historical_high:
        return None
    if signal_bar.close <= historical_high:
        return None

    if signal_index < settings.relative_volume_lookback_bars:
        return None
    prior_bars = intraday_bars[
        signal_index - settings.relative_volume_lookback_bars : signal_index
    ]
    avg_volume = sum(bar.volume for bar in prior_bars) / len(prior_bars)
    relative_volume = signal_bar.volume / avg_volume if avg_volume > 0 else 0.0
    if relative_volume < settings.relative_volume_threshold:
        return None

    entry_level = historical_high
    stop_price = round(signal_bar.high + settings.entry_stop_price_buffer, 2)
    limit_price = round(stop_price * (1 + settings.stop_limit_buffer_pct), 2)
    stop_buffer = max(0.01, historical_high * settings.breakout_stop_buffer_pct)
    initial_stop_price = round(historical_high - stop_buffer, 2)

    return EntrySignal(
        symbol=symbol,
        signal_bar=signal_bar,
        entry_level=historical_high,
        relative_volume=relative_volume,
        stop_price=stop_price,
        limit_price=limit_price,
        initial_stop_price=initial_stop_price,
    )
```

**Key design note:** `daily_bars[-lookback:]` uses the most recent N complete daily bars, consistent with how `daily_trend_filter_passes` treats `daily_bars[-1]` as yesterday's close.

---

## Task 5 — EMA Pullback strategy (with user contribution point)

**File:** `src/alpaca_bot/strategy/ema_pullback.py` (new file)

The file will be created with the EMA calculation helper and the full evaluator, but `_detect_ema_pullback` is left as a TODO for user contribution (see below).

```python
from __future__ import annotations

from collections.abc import Sequence

from alpaca_bot.config import Settings
from alpaca_bot.domain.models import Bar, EntrySignal
from alpaca_bot.strategy.breakout import daily_trend_filter_passes, is_entry_session_time


def _calculate_ema(closes: Sequence[float], period: int) -> float:
    alpha = 2.0 / (period + 1)
    ema = closes[0]
    for close in closes[1:]:
        ema = alpha * close + (1.0 - alpha) * ema
    return ema


def _detect_ema_pullback(
    bars: Sequence[Bar],
    signal_index: int,
    ema_period: int,
) -> bool:
    """
    Return True if a pullback to the EMA occurred before the signal bar.

    A pullback means the prior bar was at or near the EMA (close-based strict
    definition: prior bar close <= prior EMA). Implementors may choose:
      - Strict: prior_bar.close <= prior_ema (fewer signals, cleaner)
      - Loose:  prior_bar.low <= prior_ema  (catches wick touches, more signals)
      - Multi-bar: any of the last N bars had low <= ema  (widest, most noise)

    Parameters
    ----------
    bars : intraday bars up to and including the signal bar
    signal_index : index of the signal bar in the original bars sequence
    ema_period : EMA period used for current and prior EMA calculations
    """
    # TODO: implement pullback detection
    raise NotImplementedError


def evaluate_ema_pullback_signal(
    *,
    symbol: str,
    intraday_bars: Sequence[Bar],
    signal_index: int,
    daily_bars: Sequence[Bar],
    settings: Settings,
) -> EntrySignal | None:
    if symbol not in settings.symbols:
        return None
    if not intraday_bars or signal_index < 0 or signal_index >= len(intraday_bars):
        return None
    if signal_index < settings.ema_period:
        return None

    signal_bar = intraday_bars[signal_index]
    if signal_bar.symbol != symbol:
        return None
    if not is_entry_session_time(signal_bar.timestamp, settings):
        return None
    if not daily_trend_filter_passes(daily_bars, settings):
        return None

    closes_up_to_signal = [bar.close for bar in intraday_bars[: signal_index + 1]]
    current_ema = _calculate_ema(closes_up_to_signal, settings.ema_period)

    if signal_bar.close <= current_ema:
        return None

    if not _detect_ema_pullback(intraday_bars, signal_index, settings.ema_period):
        return None

    if signal_index < settings.relative_volume_lookback_bars:
        return None
    prior_vol_bars = intraday_bars[
        signal_index - settings.relative_volume_lookback_bars : signal_index
    ]
    avg_volume = sum(bar.volume for bar in prior_vol_bars) / len(prior_vol_bars)
    relative_volume = signal_bar.volume / avg_volume if avg_volume > 0 else 0.0
    if relative_volume < settings.relative_volume_threshold:
        return None

    prior_bar = intraday_bars[signal_index - 1]
    entry_level = round(current_ema, 2)
    stop_price = round(signal_bar.high + settings.entry_stop_price_buffer, 2)
    limit_price = round(stop_price * (1 + settings.stop_limit_buffer_pct), 2)
    stop_buffer = max(0.01, prior_bar.low * settings.breakout_stop_buffer_pct)
    initial_stop_price = round(prior_bar.low - stop_buffer, 2)

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

**User contribution point:** After creating this file, ask the user to implement `_detect_ema_pullback`. The function is 5–10 lines and captures the key design decision of how strictly to define a "pullback to EMA."

---

## Task 6 — Register all three new strategies

**File:** `src/alpaca_bot/strategy/__init__.py`

Replace the entire file with:

```python
from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from alpaca_bot.config import Settings
from alpaca_bot.domain.models import Bar, EntrySignal
from alpaca_bot.strategy.breakout import evaluate_breakout_signal
from alpaca_bot.strategy.ema_pullback import evaluate_ema_pullback_signal
from alpaca_bot.strategy.high_watermark import evaluate_high_watermark_signal
from alpaca_bot.strategy.momentum import evaluate_momentum_signal
from alpaca_bot.strategy.orb import evaluate_orb_signal


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
}
```

---

## Task 7 — Tests for ORB strategy

**File:** `tests/unit/test_orb_strategy.py` (new file)

```python
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from alpaca_bot.domain.models import Bar, EntrySignal


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
        orb_opening_bars=2,
    )
    defaults.update(overrides)
    return Settings(**defaults)


def _make_daily_bar(high: float = 100.0, close: float = None) -> Bar:
    return Bar(
        symbol="AAPL",
        timestamp=datetime(2026, 1, 1, 21, 0, tzinfo=timezone.utc),
        open=close or high - 1.0,
        high=high,
        low=high - 2.0,
        close=close or high - 0.5,
        volume=1_000_000.0,
    )


def _make_daily_bars(n: int = 10) -> list[Bar]:
    return [_make_daily_bar(high=100.0 + i * 0.1) for i in range(n)]


def _make_intraday_bars_with_orb(
    orb_high: float = 100.0,
    orb_low: float = 98.0,
    signal_high: float = 102.0,
    signal_close: float = 101.5,
    signal_volume: float = 300_000.0,
    orb_volume: float = 100_000.0,
    n_orb_bars: int = 2,
    n_prior_bars: int = 3,
) -> list[Bar]:
    """Build a bar sequence: n_orb_bars opening range bars + n_prior_bars filler + 1 signal bar."""
    tz = ZoneInfo("America/New_York")
    base = datetime(2026, 1, 2, 9, 30, tzinfo=tz)
    bars = []
    for i in range(n_orb_bars):
        bars.append(Bar(
            symbol="AAPL",
            timestamp=base + timedelta(minutes=15 * i),
            open=orb_low + 1.0,
            high=orb_high,
            low=orb_low,
            close=orb_low + 1.5,
            volume=orb_volume,
        ))
    for i in range(n_prior_bars):
        ts = base + timedelta(minutes=15 * (n_orb_bars + i))
        bars.append(Bar(
            symbol="AAPL",
            timestamp=ts,
            open=orb_low + 1.0,
            high=orb_high - 0.5,
            low=orb_low + 0.5,
            close=orb_low + 1.0,
            volume=orb_volume,
        ))
    signal_ts = base + timedelta(minutes=15 * (n_orb_bars + n_prior_bars))
    bars.append(Bar(
        symbol="AAPL",
        timestamp=signal_ts,
        open=orb_high,
        high=signal_high,
        low=orb_high - 0.5,
        close=signal_close,
        volume=signal_volume,
    ))
    return bars


def test_orb_returns_entry_signal_when_all_conditions_met():
    from alpaca_bot.strategy.orb import evaluate_orb_signal
    settings = _make_settings()
    daily_bars = _make_daily_bars()
    bars = _make_intraday_bars_with_orb(orb_high=100.0, signal_high=102.0, signal_close=101.5)
    result = evaluate_orb_signal(
        symbol="AAPL",
        intraday_bars=bars,
        signal_index=len(bars) - 1,
        daily_bars=daily_bars,
        settings=settings,
    )
    assert result is not None
    assert isinstance(result, EntrySignal)
    assert result.symbol == "AAPL"


def test_orb_entry_level_equals_opening_range_high():
    from alpaca_bot.strategy.orb import evaluate_orb_signal
    settings = _make_settings()
    daily_bars = _make_daily_bars()
    orb_high = 100.0
    bars = _make_intraday_bars_with_orb(orb_high=orb_high, signal_high=102.0, signal_close=101.5)
    result = evaluate_orb_signal(
        symbol="AAPL",
        intraday_bars=bars,
        signal_index=len(bars) - 1,
        daily_bars=daily_bars,
        settings=settings,
    )
    assert result is not None
    assert result.entry_level == orb_high


def test_orb_returns_none_when_signal_bar_is_within_opening_range():
    from alpaca_bot.strategy.orb import evaluate_orb_signal
    # orb_opening_bars=4 → bars[0:4] form the range; bar[3] is 10:15 AM (in entry window)
    settings = _make_settings(orb_opening_bars=4)
    daily_bars = _make_daily_bars()
    bars = _make_intraday_bars_with_orb(n_orb_bars=4, n_prior_bars=0, signal_high=102.0, signal_close=101.5)
    # signal_index=3 is bar[3] = 10:15 AM — inside the opening range and inside entry window
    result = evaluate_orb_signal(
        symbol="AAPL",
        intraday_bars=bars,
        signal_index=3,
        daily_bars=daily_bars,
        settings=settings,
    )
    assert result is None


def test_orb_returns_none_when_close_does_not_exceed_range_high():
    from alpaca_bot.strategy.orb import evaluate_orb_signal
    settings = _make_settings()
    daily_bars = _make_daily_bars()
    bars = _make_intraday_bars_with_orb(orb_high=100.0, signal_high=101.0, signal_close=99.5)
    result = evaluate_orb_signal(
        symbol="AAPL",
        intraday_bars=bars,
        signal_index=len(bars) - 1,
        daily_bars=daily_bars,
        settings=settings,
    )
    assert result is None


def test_orb_returns_none_when_volume_below_threshold():
    from alpaca_bot.strategy.orb import evaluate_orb_signal
    settings = _make_settings()
    daily_bars = _make_daily_bars()
    bars = _make_intraday_bars_with_orb(
        orb_high=100.0, signal_high=102.0, signal_close=101.5,
        signal_volume=50_000.0, orb_volume=100_000.0,  # relative vol = 0.5 < 1.5
    )
    result = evaluate_orb_signal(
        symbol="AAPL",
        intraday_bars=bars,
        signal_index=len(bars) - 1,
        daily_bars=daily_bars,
        settings=settings,
    )
    assert result is None


def test_orb_returns_none_when_trend_filter_fails():
    from alpaca_bot.strategy.orb import evaluate_orb_signal
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
    bars = _make_intraday_bars_with_orb(orb_high=100.0, signal_high=102.0, signal_close=101.5)
    result = evaluate_orb_signal(
        symbol="AAPL",
        intraday_bars=bars,
        signal_index=len(bars) - 1,
        daily_bars=daily_bars,
        settings=settings,
    )
    assert result is None


def test_orb_returns_none_outside_entry_window():
    from alpaca_bot.strategy.orb import evaluate_orb_signal
    from datetime import time
    settings = _make_settings()
    daily_bars = _make_daily_bars()
    bars = _make_intraday_bars_with_orb(orb_high=100.0, signal_high=102.0, signal_close=101.5)
    # Overwrite last bar's timestamp to be outside window
    from dataclasses import replace
    late_ts = datetime(2026, 1, 2, 16, 0, tzinfo=ZoneInfo("America/New_York"))
    last = replace(bars[-1], timestamp=late_ts)
    bars = bars[:-1] + [last]
    result = evaluate_orb_signal(
        symbol="AAPL",
        intraday_bars=bars,
        signal_index=len(bars) - 1,
        daily_bars=daily_bars,
        settings=settings,
    )
    assert result is None


def test_orb_initial_stop_below_opening_range_low():
    from alpaca_bot.strategy.orb import evaluate_orb_signal
    settings = _make_settings()
    daily_bars = _make_daily_bars()
    orb_low = 98.0
    bars = _make_intraday_bars_with_orb(orb_high=100.0, orb_low=orb_low, signal_high=102.0, signal_close=101.5)
    result = evaluate_orb_signal(
        symbol="AAPL",
        intraday_bars=bars,
        signal_index=len(bars) - 1,
        daily_bars=daily_bars,
        settings=settings,
    )
    assert result is not None
    assert result.initial_stop_price < orb_low


def test_orb_in_strategy_registry():
    from alpaca_bot.strategy import STRATEGY_REGISTRY
    assert "orb" in STRATEGY_REGISTRY
```

---

## Task 8 — Tests for High Watermark strategy

**File:** `tests/unit/test_high_watermark_strategy.py` (new file)

```python
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from alpaca_bot.domain.models import Bar, EntrySignal


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
        high_watermark_lookback_days=10,  # small for test speed
    )
    defaults.update(overrides)
    return Settings(**defaults)


def _make_daily_bars(n: int, high: float = 100.0) -> list[Bar]:
    return [
        Bar(
            symbol="AAPL",
            timestamp=datetime(2026, 1, 1, 21, 0, tzinfo=timezone.utc),
            open=high - 1.0,
            high=high,
            low=high - 2.0,
            close=high - 0.5,
            volume=1_000_000.0,
        )
        for _ in range(n)
    ]


def _make_intraday_bars(n: int = 6, high: float = 105.0, close: float = 104.5) -> list[Bar]:
    base = datetime(2026, 1, 2, 10, 0, tzinfo=ZoneInfo("America/New_York"))
    bars = []
    for i in range(n):
        ts = base + timedelta(minutes=15 * i)
        vol = 50_000.0 if i < n - 1 else 200_000.0
        bars.append(Bar(
            symbol="AAPL",
            timestamp=ts,
            open=close - 1.0,
            high=high,
            low=close - 2.0,
            close=close,
            volume=vol,
        ))
    return bars


def test_high_watermark_returns_entry_signal_when_all_conditions_met():
    from alpaca_bot.strategy.high_watermark import evaluate_high_watermark_signal
    settings = _make_settings()
    daily_bars = _make_daily_bars(n=10, high=100.0)  # historical high = 100.0
    intraday_bars = _make_intraday_bars(high=105.0, close=104.5)  # close > 100
    result = evaluate_high_watermark_signal(
        symbol="AAPL",
        intraday_bars=intraday_bars,
        signal_index=len(intraday_bars) - 1,
        daily_bars=daily_bars,
        settings=settings,
    )
    assert result is not None
    assert isinstance(result, EntrySignal)
    assert result.symbol == "AAPL"


def test_high_watermark_entry_level_equals_historical_high():
    from alpaca_bot.strategy.high_watermark import evaluate_high_watermark_signal
    settings = _make_settings()
    daily_bars = _make_daily_bars(n=10, high=100.0)
    historical_high = max(b.high for b in daily_bars)
    intraday_bars = _make_intraday_bars(high=historical_high + 3.0, close=historical_high + 2.5)
    result = evaluate_high_watermark_signal(
        symbol="AAPL",
        intraday_bars=intraday_bars,
        signal_index=len(intraday_bars) - 1,
        daily_bars=daily_bars,
        settings=settings,
    )
    assert result is not None
    assert result.entry_level == historical_high


def test_high_watermark_returns_none_when_insufficient_daily_bars():
    from alpaca_bot.strategy.high_watermark import evaluate_high_watermark_signal
    settings = _make_settings(high_watermark_lookback_days=20)
    daily_bars = _make_daily_bars(n=10, high=100.0)  # only 10 < 20
    intraday_bars = _make_intraday_bars(high=105.0, close=104.5)
    result = evaluate_high_watermark_signal(
        symbol="AAPL",
        intraday_bars=intraday_bars,
        signal_index=len(intraday_bars) - 1,
        daily_bars=daily_bars,
        settings=settings,
    )
    assert result is None


def test_high_watermark_returns_none_when_close_does_not_exceed_historical_high():
    from alpaca_bot.strategy.high_watermark import evaluate_high_watermark_signal
    settings = _make_settings()
    daily_bars = _make_daily_bars(n=10, high=100.0)
    intraday_bars = _make_intraday_bars(high=101.0, close=99.5)  # close < 100 = historical high
    result = evaluate_high_watermark_signal(
        symbol="AAPL",
        intraday_bars=intraday_bars,
        signal_index=len(intraday_bars) - 1,
        daily_bars=daily_bars,
        settings=settings,
    )
    assert result is None


def test_high_watermark_returns_none_when_volume_below_threshold():
    from alpaca_bot.strategy.high_watermark import evaluate_high_watermark_signal
    settings = _make_settings()
    daily_bars = _make_daily_bars(n=10, high=100.0)
    base = datetime(2026, 1, 2, 10, 0, tzinfo=ZoneInfo("America/New_York"))
    low_vol_bars = [
        Bar(
            symbol="AAPL",
            timestamp=base + timedelta(minutes=15 * i),
            open=103.0, high=105.0, low=102.0, close=104.5,
            volume=10_000.0,  # uniform low volume → relative vol = 1.0 < 1.5
        )
        for i in range(6)
    ]
    result = evaluate_high_watermark_signal(
        symbol="AAPL",
        intraday_bars=low_vol_bars,
        signal_index=len(low_vol_bars) - 1,
        daily_bars=daily_bars,
        settings=settings,
    )
    assert result is None


def test_high_watermark_returns_none_when_trend_filter_fails():
    from alpaca_bot.strategy.high_watermark import evaluate_high_watermark_signal
    settings = _make_settings()
    bearish_daily = [
        Bar(
            symbol="AAPL",
            timestamp=datetime(2026, 1, 1, 21, 0, tzinfo=timezone.utc),
            open=110.0, high=111.0, low=89.0, close=90.0,
            volume=1_000_000.0,
        )
        for _ in range(10)
    ]
    intraday_bars = _make_intraday_bars(high=105.0, close=104.5)
    result = evaluate_high_watermark_signal(
        symbol="AAPL",
        intraday_bars=intraday_bars,
        signal_index=len(intraday_bars) - 1,
        daily_bars=bearish_daily,
        settings=settings,
    )
    assert result is None


def test_high_watermark_initial_stop_below_historical_high():
    from alpaca_bot.strategy.high_watermark import evaluate_high_watermark_signal
    settings = _make_settings()
    daily_bars = _make_daily_bars(n=10, high=100.0)
    intraday_bars = _make_intraday_bars(high=105.0, close=104.5)
    result = evaluate_high_watermark_signal(
        symbol="AAPL",
        intraday_bars=intraday_bars,
        signal_index=len(intraday_bars) - 1,
        daily_bars=daily_bars,
        settings=settings,
    )
    assert result is not None
    assert result.initial_stop_price < 100.0


def test_high_watermark_in_strategy_registry():
    from alpaca_bot.strategy import STRATEGY_REGISTRY
    assert "high_watermark" in STRATEGY_REGISTRY
```

---

## Task 9 — Tests for EMA Pullback strategy

**File:** `tests/unit/test_ema_pullback_strategy.py` (new file)

Note: these tests depend on the user's implementation of `_detect_ema_pullback`. Write them after that contribution is complete.

```python
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from alpaca_bot.domain.models import Bar, EntrySignal


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
        ema_period=3,  # small period for test determinism
    )
    defaults.update(overrides)
    return Settings(**defaults)


def _make_daily_bars(n: int = 10) -> list[Bar]:
    return [
        Bar(
            symbol="AAPL",
            timestamp=datetime(2026, 1, 1, 21, 0, tzinfo=timezone.utc),
            open=99.0, high=101.0, low=98.0, close=100.0,
            volume=1_000_000.0,
        )
        for _ in range(n)
    ]


def _ema(closes, period):
    alpha = 2.0 / (period + 1)
    val = closes[0]
    for c in closes[1:]:
        val = alpha * c + (1 - alpha) * val
    return val


def _make_pullback_bars(
    n_trend: int = 6,
    n_dip: int = 1,
    trend_close: float = 105.0,
    dip_close: float = 99.0,
    signal_close: float = 106.0,
    signal_volume: float = 300_000.0,
    base_volume: float = 100_000.0,
) -> list[Bar]:
    """
    Build bars: n_trend bars above EMA, n_dip bar(s) at/below EMA, then signal bar above EMA.
    EMA period=3, so we need enough bars for the EMA to track price.
    """
    tz = ZoneInfo("America/New_York")
    base = datetime(2026, 1, 2, 10, 0, tzinfo=tz)
    bars = []
    for i in range(n_trend):
        bars.append(Bar(
            symbol="AAPL",
            timestamp=base + timedelta(minutes=15 * i),
            open=trend_close - 1.0,
            high=trend_close + 1.0,
            low=trend_close - 2.0,
            close=trend_close,
            volume=base_volume,
        ))
    for j in range(n_dip):
        ts = base + timedelta(minutes=15 * (n_trend + j))
        bars.append(Bar(
            symbol="AAPL",
            timestamp=ts,
            open=dip_close - 1.0,
            high=dip_close + 0.5,
            low=dip_close - 2.0,
            close=dip_close,
            volume=base_volume,
        ))
    signal_ts = base + timedelta(minutes=15 * (n_trend + n_dip))
    bars.append(Bar(
        symbol="AAPL",
        timestamp=signal_ts,
        open=signal_close - 1.0,
        high=signal_close + 1.0,
        low=signal_close - 2.0,
        close=signal_close,
        volume=signal_volume,
    ))
    return bars


def test_ema_pullback_returns_entry_signal_when_all_conditions_met():
    from alpaca_bot.strategy.ema_pullback import evaluate_ema_pullback_signal
    settings = _make_settings()
    daily_bars = _make_daily_bars()
    bars = _make_pullback_bars(n_trend=6, n_dip=1, trend_close=105.0, dip_close=99.0, signal_close=106.0)
    result = evaluate_ema_pullback_signal(
        symbol="AAPL",
        intraday_bars=bars,
        signal_index=len(bars) - 1,
        daily_bars=daily_bars,
        settings=settings,
    )
    assert result is not None
    assert isinstance(result, EntrySignal)
    assert result.symbol == "AAPL"


def test_ema_pullback_returns_none_when_no_pullback_occurred():
    from alpaca_bot.strategy.ema_pullback import evaluate_ema_pullback_signal
    settings = _make_settings(ema_period=3)
    daily_bars = _make_daily_bars()
    # Gently rising bars: close = 100 + 0.5*i  → close always > EMA (EMA lags behind)
    tz = ZoneInfo("America/New_York")
    base = datetime(2026, 1, 2, 10, 0, tzinfo=tz)
    rising_bars = [
        Bar(
            symbol="AAPL",
            timestamp=base + timedelta(minutes=15 * i),
            open=100.0 + 0.5 * i - 0.1,
            high=100.0 + 0.5 * i + 0.5,
            low=100.0 + 0.5 * i - 0.5,
            close=100.0 + 0.5 * i,
            volume=200_000.0,
        )
        for i in range(8)
    ]
    # With ema_period=3 (alpha=0.5) and closes=[100,100.5,101,...,103.5]:
    # EMA lags: at index 6 EMA≈102.5, close=103.0 > EMA → no pullback at prior bar
    result = evaluate_ema_pullback_signal(
        symbol="AAPL",
        intraday_bars=rising_bars,
        signal_index=len(rising_bars) - 1,
        daily_bars=daily_bars,
        settings=settings,
    )
    assert result is None


def test_ema_pullback_returns_none_when_signal_index_too_small():
    from alpaca_bot.strategy.ema_pullback import evaluate_ema_pullback_signal
    settings = _make_settings(ema_period=10)
    daily_bars = _make_daily_bars()
    bars = _make_pullback_bars(n_trend=6, n_dip=1, signal_close=106.0)
    result = evaluate_ema_pullback_signal(
        symbol="AAPL",
        intraday_bars=bars,
        signal_index=2,  # less than ema_period=10
        daily_bars=daily_bars,
        settings=settings,
    )
    assert result is None


def test_ema_pullback_returns_none_when_signal_close_below_ema():
    from alpaca_bot.strategy.ema_pullback import evaluate_ema_pullback_signal
    settings = _make_settings()
    daily_bars = _make_daily_bars()
    # Signal bar close below EMA — no recovery
    bars = _make_pullback_bars(n_trend=6, n_dip=1, trend_close=105.0, dip_close=99.0, signal_close=100.0)
    result = evaluate_ema_pullback_signal(
        symbol="AAPL",
        intraday_bars=bars,
        signal_index=len(bars) - 1,
        daily_bars=daily_bars,
        settings=settings,
    )
    assert result is None


def test_ema_pullback_returns_none_when_trend_filter_fails():
    from alpaca_bot.strategy.ema_pullback import evaluate_ema_pullback_signal
    settings = _make_settings()
    bearish_daily = [
        Bar(
            symbol="AAPL",
            timestamp=datetime(2026, 1, 1, 21, 0, tzinfo=timezone.utc),
            open=110.0, high=111.0, low=89.0, close=90.0,
            volume=1_000_000.0,
        )
        for _ in range(10)
    ]
    bars = _make_pullback_bars(n_trend=6, n_dip=1, trend_close=105.0, dip_close=99.0, signal_close=106.0)
    result = evaluate_ema_pullback_signal(
        symbol="AAPL",
        intraday_bars=bars,
        signal_index=len(bars) - 1,
        daily_bars=bearish_daily,
        settings=settings,
    )
    assert result is None


def test_ema_pullback_initial_stop_below_prior_bar_low():
    from alpaca_bot.strategy.ema_pullback import evaluate_ema_pullback_signal
    settings = _make_settings()
    daily_bars = _make_daily_bars()
    bars = _make_pullback_bars(n_trend=6, n_dip=1, trend_close=105.0, dip_close=99.0, signal_close=106.0)
    prior_bar_low = bars[-2].low
    result = evaluate_ema_pullback_signal(
        symbol="AAPL",
        intraday_bars=bars,
        signal_index=len(bars) - 1,
        daily_bars=daily_bars,
        settings=settings,
    )
    assert result is not None
    assert result.initial_stop_price < prior_bar_low


def test_ema_pullback_in_strategy_registry():
    from alpaca_bot.strategy import STRATEGY_REGISTRY
    assert "ema_pullback" in STRATEGY_REGISTRY
```

---

## Task 10 — Tests for new Settings fields

**Add to** `tests/unit/test_momentum_strategy.py` (append after existing tests):

```python
def test_settings_has_orb_opening_bars():
    settings = _make_settings(orb_opening_bars=4)
    assert settings.orb_opening_bars == 4


def test_settings_validates_orb_opening_bars():
    with pytest.raises(ValueError, match="ORB_OPENING_BARS"):
        _make_settings(orb_opening_bars=0)


def test_settings_has_high_watermark_lookback_days():
    settings = _make_settings(high_watermark_lookback_days=100)
    assert settings.high_watermark_lookback_days == 100


def test_settings_validates_high_watermark_lookback_days():
    with pytest.raises(ValueError, match="HIGH_WATERMARK_LOOKBACK_DAYS"):
        _make_settings(high_watermark_lookback_days=3)


def test_settings_has_ema_period():
    settings = _make_settings(ema_period=21)
    assert settings.ema_period == 21


def test_settings_validates_ema_period():
    with pytest.raises(ValueError, match="EMA_PERIOD"):
        _make_settings(ema_period=1)
```

---

## Task 11 — Verify

```bash
pytest tests/unit/test_orb_strategy.py tests/unit/test_high_watermark_strategy.py tests/unit/test_ema_pullback_strategy.py tests/unit/test_momentum_strategy.py -v
pytest  # full suite
```

All tests must pass before declaring done.

---

## User Contribution Point

After Task 5 creates `ema_pullback.py`, pause and ask the user to implement `_detect_ema_pullback`. Frame it as:

> **Context:** `evaluate_ema_pullback_signal` is built — it detects an EMA crossover-from-below. The one open question is how strictly to define "pullback." I've left `_detect_ema_pullback` as a TODO. This function is 5–8 lines and shapes the strategy's character significantly.
>
> **Request:** In `src/alpaca_bot/strategy/ema_pullback.py`, implement `_detect_ema_pullback(bars, signal_index, ema_period) -> bool`.
>
> **Trade-offs:**
> - **Strict (close-based):** `prior_bar.close <= prior_ema` — fewest signals, cleanest
> - **Loose (low-based):** `prior_bar.low <= prior_ema` — catches wick touches to EMA
> - **Multi-bar:** any of the last N bars touched the EMA — widest net, most noise

---

## Grilling Log

**Q: Financial safety — worst-case stale data?**  
A: All three strategies call `is_entry_session_time()` on the signal bar's timestamp. Stale bars with old timestamps will fail this check and return None. No trade fires on stale data.

**Q: Could two strategies submit conflicting orders on the same symbol simultaneously?**  
A: The supervisor fan-out is sequential (for loop), not concurrent. Each strategy's cycle filters `working_order_symbols` per strategy (client_order_id prefix isolation). Two strategies can hold the same symbol but their stops are independent. Engine already handles this case (tested in `test_exit_intent_does_not_cancel_other_strategy_stop`).

**Q: Do new strategies pollute the audit log?**  
A: No. `strategy_name` is already stored in `OrderRecord` and threaded through `CycleIntent`. No audit schema changes needed.

**Q: Does `evaluate_cycle()` remain pure?**  
A: Yes. Signal functions are pure functions. No I/O added.

**Q: Daily bar fetch window — will high_watermark get enough data?**  
A: Task 2 widens the fetch to `max(sma_period*3, 60, high_watermark_lookback_days + 10)`. With defaults this becomes 262 calendar days ≈ 252+ trading days. Resolved.

**Q: New strategies default to enabled — does this over-expose the portfolio?**  
A: Known architectural limitation pre-existing with breakout + momentum. Each strategy's `evaluate_cycle()` sees only its own positions. Operators control exposure via toggle endpoint. No plan change needed; document the behavior.

**Q: ORB uses `intraday_bars[:orb_opening_bars]` — does this grab today's opening bars or bars from prior days?**  
A: Critical bug found during grilling. The supervisor fetches intraday bars with `start=now - 5 days`, so `intraday_bars` can contain 5 sessions. `intraday_bars[:2]` would be bars from 5 days ago. Fixed in Task 3: ORB filters to `today_bars` using `session_day(bar.timestamp, settings) == signal_date`, then uses `today_bars[:orb_opening_bars]`. Plan updated.

**Q: Does ENABLE_LIVE_TRADING=false gate remain effective?**  
A: Yes. Signal functions don't touch the broker. The gate is at the broker call layer (unchanged).

**Q: No migration needed?**  
A: Confirmed. The `strategy_flags` table already exists with no strategy_name constraint — new names just get absent rows (= enabled). No DDL required.
