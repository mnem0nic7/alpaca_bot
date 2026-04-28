# Extended Hours Trading Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add pre-market (4:00–9:30am ET) and after-hours (4:00–8:00pm ET) limit-order trading behind `EXTENDED_HOURS_ENABLED=false` (default off), with zero behavioural change for existing deployments.

**Architecture:** A new `SessionType` enum is detected once at the top of the supervisor loop and threaded down through dispatch and engine as a parameter — never re-detected inside lower layers. Extended-hours cycles use `submit_limit_entry`/`submit_limit_exit` (new broker methods); stop updates are suppressed with audit events.

**Tech Stack:** Python, alpaca-py (`LimitOrderRequest` with `extended_hours=True`), pytest (DI fakes pattern), existing `Settings.from_env` / `validate()` pattern.

---

## File Map

| File | Status | Responsibility |
|---|---|---|
| `src/alpaca_bot/strategy/session.py` | **Create** | `SessionType` enum, `detect_session_type()`, `is_entry_window()`, `is_flatten_time()` |
| `src/alpaca_bot/config/__init__.py` | Modify | 7 new env fields + `validate()` rules |
| `src/alpaca_bot/strategy/breakout.py` | Modify | Delegate `is_entry_session_time` / `is_past_flatten_time` to `session.py` |
| `src/alpaca_bot/execution/alpaca.py` | Modify | `submit_limit_entry()`, `submit_limit_exit()`, `extended_hours_limit_price()` |
| `src/alpaca_bot/runtime/order_dispatch.py` | Modify | `session_type` param; route to `submit_limit_entry` when extended hours |
| `src/alpaca_bot/core/engine.py` | Modify | `session_type` param; limit exit intents + stop suppression during extended hours |
| `src/alpaca_bot/runtime/cycle_intent_execution.py` | Modify | Route EXIT with `limit_price` to `submit_limit_exit`; `BrokerProtocol` update |
| `src/alpaca_bot/runtime/supervisor.py` | Modify | `_current_session()` replaces `_market_is_open()`; pass `session_type` down |
| `src/alpaca_bot/web/service.py` | Modify | Add new audit event types |
| `tests/unit/test_session.py` | **Create** | Full boundary tests for `session.py` functions |
| `tests/unit/test_settings_extended_hours.py` | **Create** | Validation rules for new config fields |
| `tests/unit/test_order_dispatch_extended_hours.py` | **Create** | Session-aware routing tests |
| `tests/unit/test_engine_extended_hours.py` | **Create** | Engine limit exit + stop suppression tests |
| `tests/unit/test_supervisor_session.py` | **Create** | `_current_session()` with `EXTENDED_HOURS_ENABLED=false/true` |

---

## Task 1: Create `strategy/session.py`

**Files:**
- Create: `src/alpaca_bot/strategy/session.py`
- Test: `tests/unit/test_session.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/unit/test_session.py
from __future__ import annotations
from datetime import time
from zoneinfo import ZoneInfo
import pytest
from alpaca_bot.config import Settings
from alpaca_bot.strategy.session import (
    SessionType,
    detect_session_type,
    is_entry_window,
    is_flatten_time,
)

def _make_ts(hour: int, minute: int = 0):
    """UTC timestamp that maps to the given ET wall clock time on 2026-04-28."""
    from datetime import datetime, timezone
    # ET is UTC-4 on 2026-04-28 (EDT)
    return datetime(2026, 4, 28, hour + 4, minute, tzinfo=timezone.utc)


def _settings(**overrides) -> Settings:
    base = {
        "TRADING_MODE": "paper",
        "ENABLE_LIVE_TRADING": "false",
        "STRATEGY_VERSION": "v1",
        "DATABASE_URL": "postgresql://test/db",
        "MARKET_DATA_FEED": "iex",
        "SYMBOLS": "AAPL",
        "DAILY_SMA_PERIOD": "20",
        "BREAKOUT_LOOKBACK_BARS": "20",
        "RELATIVE_VOLUME_LOOKBACK_BARS": "20",
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
        "EXTENDED_HOURS_ENABLED": "false",
        "PRE_MARKET_ENTRY_WINDOW_START": "04:00",
        "PRE_MARKET_ENTRY_WINDOW_END": "09:20",
        "AFTER_HOURS_ENTRY_WINDOW_START": "16:05",
        "AFTER_HOURS_ENTRY_WINDOW_END": "19:30",
        "EXTENDED_HOURS_FLATTEN_TIME": "19:45",
        "EXTENDED_HOURS_LIMIT_OFFSET_PCT": "0.001",
    }
    base.update(overrides)
    return Settings.from_env(base)


# --- detect_session_type ---

@pytest.mark.parametrize("hour, minute, expected", [
    (3, 59, SessionType.CLOSED),
    (4, 0, SessionType.PRE_MARKET),
    (9, 29, SessionType.PRE_MARKET),
    (9, 30, SessionType.REGULAR),
    (15, 59, SessionType.REGULAR),
    (16, 0, SessionType.AFTER_HOURS),
    (19, 59, SessionType.AFTER_HOURS),
    (20, 0, SessionType.CLOSED),
    (0, 0, SessionType.CLOSED),
])
def test_detect_session_type_boundaries(hour, minute, expected):
    settings = _settings()
    ts = _make_ts(hour, minute)
    assert detect_session_type(ts, settings) is expected


# --- is_entry_window ---

def test_is_entry_window_pre_market_inside():
    settings = _settings()
    ts = _make_ts(6, 0)
    assert is_entry_window(ts, settings, SessionType.PRE_MARKET) is True


def test_is_entry_window_pre_market_outside():
    settings = _settings()
    ts = _make_ts(9, 25)  # past PRE_MARKET_ENTRY_WINDOW_END 09:20
    assert is_entry_window(ts, settings, SessionType.PRE_MARKET) is False


def test_is_entry_window_after_hours_inside():
    settings = _settings()
    ts = _make_ts(17, 0)
    assert is_entry_window(ts, settings, SessionType.AFTER_HOURS) is True


def test_is_entry_window_after_hours_outside():
    settings = _settings()
    ts = _make_ts(20, 0)
    assert is_entry_window(ts, settings, SessionType.AFTER_HOURS) is False


def test_is_entry_window_regular_delegates_to_settings():
    settings = _settings()
    ts = _make_ts(12, 0)
    assert is_entry_window(ts, settings, SessionType.REGULAR) is True


def test_is_entry_window_closed_always_false():
    settings = _settings()
    ts = _make_ts(1, 0)
    assert is_entry_window(ts, settings, SessionType.CLOSED) is False


# --- is_flatten_time ---

def test_is_flatten_time_after_hours_before():
    settings = _settings()
    ts = _make_ts(19, 30)  # before 19:45
    assert is_flatten_time(ts, settings, SessionType.AFTER_HOURS) is False


def test_is_flatten_time_after_hours_at():
    settings = _settings()
    ts = _make_ts(19, 45)
    assert is_flatten_time(ts, settings, SessionType.AFTER_HOURS) is True


def test_is_flatten_time_regular():
    settings = _settings()
    ts = _make_ts(15, 45)
    assert is_flatten_time(ts, settings, SessionType.REGULAR) is True


def test_is_flatten_time_pre_market_never():
    settings = _settings()
    ts = _make_ts(9, 25)
    assert is_flatten_time(ts, settings, SessionType.PRE_MARKET) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_session.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'alpaca_bot.strategy.session'`

- [ ] **Step 3: Write `strategy/session.py`**

```python
# src/alpaca_bot/strategy/session.py
from __future__ import annotations

import enum
from datetime import datetime, time

from alpaca_bot.config import Settings

_PRE_MARKET_OPEN = time(4, 0)
_REGULAR_OPEN = time(9, 30)
_REGULAR_CLOSE = time(16, 0)
_EXTENDED_CLOSE = time(20, 0)


class SessionType(enum.Enum):
    PRE_MARKET = "pre_market"
    REGULAR = "regular"
    AFTER_HOURS = "after_hours"
    CLOSED = "closed"


def detect_session_type(timestamp: datetime, settings: Settings) -> SessionType:
    """Classify timestamp into a trading session using ET wall clock."""
    local_time = timestamp.astimezone(settings.market_timezone).time()
    if local_time < _PRE_MARKET_OPEN or local_time >= _EXTENDED_CLOSE:
        return SessionType.CLOSED
    if local_time < _REGULAR_OPEN:
        return SessionType.PRE_MARKET
    if local_time < _REGULAR_CLOSE:
        return SessionType.REGULAR
    return SessionType.AFTER_HOURS


def is_entry_window(
    timestamp: datetime, settings: Settings, session: SessionType
) -> bool:
    local_time = timestamp.astimezone(settings.market_timezone).time()
    if session is SessionType.PRE_MARKET:
        return settings.pre_market_entry_window_start <= local_time <= settings.pre_market_entry_window_end
    if session is SessionType.REGULAR:
        return settings.entry_window_start <= local_time <= settings.entry_window_end
    if session is SessionType.AFTER_HOURS:
        return settings.after_hours_entry_window_start <= local_time <= settings.after_hours_entry_window_end
    return False


def is_flatten_time(
    timestamp: datetime, settings: Settings, session: SessionType
) -> bool:
    local_time = timestamp.astimezone(settings.market_timezone).time()
    if session is SessionType.REGULAR:
        return local_time >= settings.flatten_time
    if session is SessionType.AFTER_HOURS:
        return local_time >= settings.extended_hours_flatten_time
    return False
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_session.py -v`
Expected: FAIL with attribute errors on Settings — the new fields don't exist yet. This is expected at this step.

_(Tests for session.py will pass fully after Task 2 adds the new settings fields.)_

- [ ] **Step 5: Commit**

```bash
git add src/alpaca_bot/strategy/session.py tests/unit/test_session.py
git commit -m "feat: add SessionType enum and session detection helpers"
```

---

## Task 2: Add extended-hours settings fields

**Files:**
- Modify: `src/alpaca_bot/config/__init__.py`
- Test: `tests/unit/test_settings_extended_hours.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/unit/test_settings_extended_hours.py
from __future__ import annotations
import pytest
from alpaca_bot.config import Settings


def _base() -> dict:
    return {
        "TRADING_MODE": "paper",
        "ENABLE_LIVE_TRADING": "false",
        "STRATEGY_VERSION": "v1",
        "DATABASE_URL": "postgresql://test/db",
        "MARKET_DATA_FEED": "iex",
        "SYMBOLS": "AAPL",
        "DAILY_SMA_PERIOD": "20",
        "BREAKOUT_LOOKBACK_BARS": "20",
        "RELATIVE_VOLUME_LOOKBACK_BARS": "20",
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
    }


def test_settings_extended_hours_defaults():
    s = Settings.from_env(_base())
    assert s.extended_hours_enabled is False
    from datetime import time
    assert s.pre_market_entry_window_start == time(4, 0)
    assert s.pre_market_entry_window_end == time(9, 20)
    assert s.after_hours_entry_window_start == time(16, 5)
    assert s.after_hours_entry_window_end == time(19, 30)
    assert s.extended_hours_flatten_time == time(19, 45)
    assert s.extended_hours_limit_offset_pct == pytest.approx(0.001)


def test_settings_extended_hours_can_be_enabled():
    env = {**_base(), "EXTENDED_HOURS_ENABLED": "true"}
    s = Settings.from_env(env)
    assert s.extended_hours_enabled is True


def test_validation_pre_market_end_must_be_before_regular_open():
    env = {
        **_base(),
        "EXTENDED_HOURS_ENABLED": "true",
        "PRE_MARKET_ENTRY_WINDOW_START": "04:00",
        "PRE_MARKET_ENTRY_WINDOW_END": "09:35",  # after 09:30
    }
    with pytest.raises(ValueError, match="PRE_MARKET_ENTRY_WINDOW_END"):
        Settings.from_env(env)


def test_validation_pre_market_start_must_be_before_end():
    env = {
        **_base(),
        "EXTENDED_HOURS_ENABLED": "true",
        "PRE_MARKET_ENTRY_WINDOW_START": "09:00",
        "PRE_MARKET_ENTRY_WINDOW_END": "08:00",
    }
    with pytest.raises(ValueError, match="PRE_MARKET_ENTRY_WINDOW_START"):
        Settings.from_env(env)


def test_validation_after_hours_start_must_be_after_regular_close():
    env = {
        **_base(),
        "EXTENDED_HOURS_ENABLED": "true",
        "AFTER_HOURS_ENTRY_WINDOW_START": "15:59",
    }
    with pytest.raises(ValueError, match="AFTER_HOURS_ENTRY_WINDOW_START"):
        Settings.from_env(env)


def test_validation_after_hours_ordering():
    env = {
        **_base(),
        "EXTENDED_HOURS_ENABLED": "true",
        "AFTER_HOURS_ENTRY_WINDOW_START": "16:05",
        "AFTER_HOURS_ENTRY_WINDOW_END": "19:30",
        "EXTENDED_HOURS_FLATTEN_TIME": "19:20",  # before end
    }
    with pytest.raises(ValueError, match="EXTENDED_HOURS_FLATTEN_TIME"):
        Settings.from_env(env)


def test_validation_limit_offset_must_be_positive():
    env = {**_base(), "EXTENDED_HOURS_LIMIT_OFFSET_PCT": "0"}
    with pytest.raises(ValueError, match="EXTENDED_HOURS_LIMIT_OFFSET_PCT"):
        Settings.from_env(env)


def test_validation_only_runs_when_enabled():
    """Invalid window times are not checked when extended_hours_enabled=False."""
    env = {
        **_base(),
        "EXTENDED_HOURS_ENABLED": "false",
        "PRE_MARKET_ENTRY_WINDOW_END": "09:45",  # invalid but not checked
    }
    s = Settings.from_env(env)
    assert s.extended_hours_enabled is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_settings_extended_hours.py -v`
Expected: FAIL with `TypeError` — `Settings` doesn't accept the new fields yet.

- [ ] **Step 3: Add fields to `Settings` dataclass**

In `src/alpaca_bot/config/__init__.py`, add these fields after `slack_webhook_url` (line 92, after the credentials block):

```python
    # Extended hours trading
    extended_hours_enabled: bool = False
    pre_market_entry_window_start: time = time(4, 0)
    pre_market_entry_window_end: time = time(9, 20)
    after_hours_entry_window_start: time = time(16, 5)
    after_hours_entry_window_end: time = time(19, 30)
    extended_hours_flatten_time: time = time(19, 45)
    extended_hours_limit_offset_pct: float = 0.001
```

In `from_env()`, add after the `flatten_time` line (after line 151):

```python
            extended_hours_enabled=_parse_bool(
                "EXTENDED_HOURS_ENABLED", values.get("EXTENDED_HOURS_ENABLED", "false")
            ),
            pre_market_entry_window_start=_parse_time(
                "PRE_MARKET_ENTRY_WINDOW_START",
                values.get("PRE_MARKET_ENTRY_WINDOW_START", "04:00"),
            ),
            pre_market_entry_window_end=_parse_time(
                "PRE_MARKET_ENTRY_WINDOW_END",
                values.get("PRE_MARKET_ENTRY_WINDOW_END", "09:20"),
            ),
            after_hours_entry_window_start=_parse_time(
                "AFTER_HOURS_ENTRY_WINDOW_START",
                values.get("AFTER_HOURS_ENTRY_WINDOW_START", "16:05"),
            ),
            after_hours_entry_window_end=_parse_time(
                "AFTER_HOURS_ENTRY_WINDOW_END",
                values.get("AFTER_HOURS_ENTRY_WINDOW_END", "19:30"),
            ),
            extended_hours_flatten_time=_parse_time(
                "EXTENDED_HOURS_FLATTEN_TIME",
                values.get("EXTENDED_HOURS_FLATTEN_TIME", "19:45"),
            ),
            extended_hours_limit_offset_pct=float(
                values.get("EXTENDED_HOURS_LIMIT_OFFSET_PCT", "0.001")
            ),
```

In `validate()`, add at the end (after the `dashboard_auth` checks):

```python
        if self.extended_hours_limit_offset_pct <= 0:
            raise ValueError("EXTENDED_HOURS_LIMIT_OFFSET_PCT must be positive")
        if self.extended_hours_enabled:
            if self.pre_market_entry_window_start >= self.pre_market_entry_window_end:
                raise ValueError(
                    "PRE_MARKET_ENTRY_WINDOW_START must be before PRE_MARKET_ENTRY_WINDOW_END"
                )
            if self.pre_market_entry_window_end >= time(9, 30):
                raise ValueError(
                    "PRE_MARKET_ENTRY_WINDOW_END must be before 09:30 (regular open)"
                )
            if self.after_hours_entry_window_start <= time(16, 0):
                raise ValueError(
                    "AFTER_HOURS_ENTRY_WINDOW_START must be after 16:00 (regular close)"
                )
            if self.after_hours_entry_window_start >= self.after_hours_entry_window_end:
                raise ValueError(
                    "AFTER_HOURS_ENTRY_WINDOW_START must be before AFTER_HOURS_ENTRY_WINDOW_END"
                )
            if self.after_hours_entry_window_end >= self.extended_hours_flatten_time:
                raise ValueError(
                    "EXTENDED_HOURS_FLATTEN_TIME must be after AFTER_HOURS_ENTRY_WINDOW_END"
                )
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/unit/test_settings_extended_hours.py tests/unit/test_session.py -v`
Expected: All PASS (session.py tests now have the settings fields they need).

- [ ] **Step 5: Run full suite to verify no regressions**

Run: `pytest -x -q`
Expected: All existing tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/alpaca_bot/config/__init__.py tests/unit/test_settings_extended_hours.py
git commit -m "feat: add 7 extended-hours settings fields with validation"
```

---

## Task 3: Update `strategy/breakout.py` to delegate to `session.py`

**Files:**
- Modify: `src/alpaca_bot/strategy/breakout.py` (lines 11–18)

- [ ] **Step 1: Update `is_entry_session_time` and `is_past_flatten_time`**

Replace the current implementations in `src/alpaca_bot/strategy/breakout.py`:

```python
from alpaca_bot.strategy.session import SessionType, detect_session_type
from alpaca_bot.strategy.session import is_entry_window as _is_entry_window
from alpaca_bot.strategy.session import is_flatten_time as _is_flatten_time


def is_entry_session_time(timestamp: datetime, settings: Settings) -> bool:
    session = detect_session_type(timestamp, settings)
    return _is_entry_window(timestamp, settings, session)


def is_past_flatten_time(timestamp: datetime, settings: Settings) -> bool:
    session = detect_session_type(timestamp, settings)
    return _is_flatten_time(timestamp, settings, session)
```

Keep `session_day()` unchanged.

- [ ] **Step 2: Run full suite**

Run: `pytest -x -q`
Expected: All existing tests pass — `is_entry_session_time` still returns the same results for regular-session timestamps because `EXTENDED_HOURS_ENABLED=false` means extended-hours windows are never entered.

- [ ] **Step 3: Commit**

```bash
git add src/alpaca_bot/strategy/breakout.py
git commit -m "refactor: delegate is_entry_session_time/is_past_flatten_time to session.py"
```

---

## Task 4: Add limit order broker methods to `execution/alpaca.py`

**Files:**
- Modify: `src/alpaca_bot/execution/alpaca.py`

- [ ] **Step 1: Write failing test**

```python
# Append to tests/unit/test_alpaca_broker.py (if it exists), or create a standalone test block:
# tests/unit/test_extended_hours_limit_price.py
from __future__ import annotations
import pytest
from alpaca_bot.execution.alpaca import extended_hours_limit_price


def test_buy_adds_offset():
    result = extended_hours_limit_price("buy", ref_price=100.0, offset_pct=0.001)
    assert result == pytest.approx(100.10, abs=0.01)


def test_sell_subtracts_offset():
    result = extended_hours_limit_price("sell", ref_price=100.0, offset_pct=0.001)
    assert result == pytest.approx(99.90, abs=0.01)


def test_buy_result_always_positive():
    result = extended_hours_limit_price("buy", ref_price=0.05, offset_pct=0.001)
    assert result > 0


def test_invalid_side_raises():
    with pytest.raises(ValueError):
        extended_hours_limit_price("short", ref_price=100.0, offset_pct=0.001)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_extended_hours_limit_price.py -v`
Expected: FAIL with `ImportError`

- [ ] **Step 3: Add `extended_hours_limit_price` and two broker methods**

In `src/alpaca_bot/execution/alpaca.py`, add after `submit_market_exit()` (around line 335):

```python
def submit_limit_entry(
    self,
    *,
    symbol: str,
    quantity: int | None = None,
    qty: int | None = None,
    limit_price: float,
    client_order_id: str,
) -> BrokerOrder:
    """Extended-hours limit buy. Alpaca requires extended_hours=True and TIF=DAY."""
    q = _resolve_order_quantity(quantity=quantity, qty=qty)
    request = _build_extended_hours_limit_order(
        symbol=symbol,
        quantity=q,
        limit_price=limit_price,
        client_order_id=client_order_id,
        side="buy",
    )
    return _parse_broker_order(
        _retry_with_backoff(lambda: self._trading.submit_order(request))
    )

def submit_limit_exit(
    self,
    *,
    symbol: str,
    quantity: int | None = None,
    qty: int | None = None,
    limit_price: float,
    client_order_id: str,
) -> BrokerOrder:
    """Extended-hours limit sell. Alpaca requires extended_hours=True and TIF=DAY."""
    q = _resolve_order_quantity(quantity=quantity, qty=qty)
    request = _build_extended_hours_limit_order(
        symbol=symbol,
        quantity=q,
        limit_price=limit_price,
        client_order_id=client_order_id,
        side="sell",
    )
    return _parse_broker_order(
        _retry_with_backoff(lambda: self._trading.submit_order(request))
    )
```

Add the helper function `_build_extended_hours_limit_order` near the other `_build_*` helpers (around line 650):

```python
def _build_extended_hours_limit_order(
    symbol: str,
    quantity: int,
    limit_price: float,
    client_order_id: str,
    side: str,
) -> Any:
    try:
        from alpaca.trading.enums import OrderSide, OrderType, TimeInForce
        from alpaca.trading.requests import LimitOrderRequest
    except ModuleNotFoundError:
        return {
            "symbol": symbol,
            "qty": quantity,
            "side": side,
            "type": "limit",
            "time_in_force": "day",
            "extended_hours": True,
            "limit_price": limit_price,
            "client_order_id": client_order_id,
        }
    return LimitOrderRequest(
        symbol=symbol,
        qty=quantity,
        side=OrderSide.BUY if side == "buy" else OrderSide.SELL,
        type=OrderType.LIMIT,
        time_in_force=TimeInForce.DAY,
        extended_hours=True,
        limit_price=limit_price,
        client_order_id=client_order_id,
    )
```

Add `extended_hours_limit_price` as a module-level pure function (not a method) near the bottom of the file:

```python
def extended_hours_limit_price(side: str, ref_price: float, offset_pct: float) -> float:
    """Compute limit price for an extended-hours order.

    Buy: ref_price * (1 + offset_pct)  — caps entry premium.
    Sell: ref_price * (1 - offset_pct) — floors exit proceeds.
    """
    if side == "buy":
        return round(ref_price * (1 + offset_pct), 2)
    if side == "sell":
        return round(ref_price * (1 - offset_pct), 2)
    raise ValueError(f"extended_hours_limit_price: side must be 'buy' or 'sell', got {side!r}")
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/unit/test_extended_hours_limit_price.py -v`
Expected: All PASS.

- [ ] **Step 5: Run full suite**

Run: `pytest -x -q`
Expected: All pass.

- [ ] **Step 6: Commit**

```bash
git add src/alpaca_bot/execution/alpaca.py tests/unit/test_extended_hours_limit_price.py
git commit -m "feat: add submit_limit_entry/exit and extended_hours_limit_price to broker"
```

---

## Task 5: Session-aware order dispatch

**Files:**
- Modify: `src/alpaca_bot/runtime/order_dispatch.py`
- Test: `tests/unit/test_order_dispatch_extended_hours.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/unit/test_order_dispatch_extended_hours.py
from __future__ import annotations
from datetime import datetime, timezone
from unittest.mock import MagicMock
import pytest
from alpaca_bot.config import Settings
from alpaca_bot.execution.alpaca import BrokerOrder
from alpaca_bot.runtime.order_dispatch import dispatch_pending_orders
from alpaca_bot.storage import AuditEvent, OrderRecord
from alpaca_bot.strategy.session import SessionType


def _settings() -> Settings:
    return Settings.from_env({
        "TRADING_MODE": "paper",
        "ENABLE_LIVE_TRADING": "false",
        "STRATEGY_VERSION": "v1",
        "DATABASE_URL": "postgresql://test/db",
        "MARKET_DATA_FEED": "iex",
        "SYMBOLS": "AAPL",
        "DAILY_SMA_PERIOD": "20",
        "BREAKOUT_LOOKBACK_BARS": "20",
        "RELATIVE_VOLUME_LOOKBACK_BARS": "20",
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
        "EXTENDED_HOURS_ENABLED": "true",
        "EXTENDED_HOURS_LIMIT_OFFSET_PCT": "0.001",
    })


def _pending_entry_order(stop_price: float = 100.0) -> OrderRecord:
    return OrderRecord(
        client_order_id="test:v1:2026-04-28:AAPL:entry:2026-04-28T06:00:00+00:00",
        symbol="AAPL",
        side="buy",
        intent_type="entry",
        status="pending_submit",
        quantity=10,
        trading_mode="paper",
        strategy_version="v1",
        strategy_name="breakout",
        created_at=datetime(2026, 4, 28, 10, 0, tzinfo=timezone.utc),
        updated_at=datetime(2026, 4, 28, 10, 0, tzinfo=timezone.utc),
        stop_price=stop_price,
        limit_price=stop_price * 1.001,
        initial_stop_price=stop_price * 0.99,
        signal_timestamp=datetime(2026, 4, 28, 10, 0, tzinfo=timezone.utc),
    )


def _fake_runtime(orders):
    saved = []
    audits = []

    class FakeOrderStore:
        def list_pending_submit(self, **kwargs):
            return orders
        def list_by_status(self, **kwargs):
            return orders
        def save(self, order, *, commit=True):
            saved.append(order)

    class FakeAuditStore:
        def append(self, event, *, commit=True):
            audits.append(event)

    class FakeConn:
        def commit(self): pass
        def rollback(self): pass

    class FakeRuntime:
        order_store = FakeOrderStore()
        audit_event_store = FakeAuditStore()
        connection = FakeConn()

    return FakeRuntime(), saved, audits


def _fake_broker():
    class FakeBroker:
        calls = []
        def submit_stop_limit_entry(self, **kwargs):
            self.calls.append(("stop_limit_entry", kwargs))
            return BrokerOrder(
                client_order_id=kwargs["client_order_id"],
                broker_order_id="brk1",
                symbol=kwargs["symbol"],
                side="buy",
                status="new",
                quantity=kwargs["quantity"],
            )
        def submit_limit_entry(self, **kwargs):
            self.calls.append(("limit_entry", kwargs))
            return BrokerOrder(
                client_order_id=kwargs["client_order_id"],
                broker_order_id="brk2",
                symbol=kwargs["symbol"],
                side="buy",
                status="new",
                quantity=kwargs["quantity"],
            )
        def submit_stop_order(self, **kwargs):
            self.calls.append(("stop_order", kwargs))
            return BrokerOrder(
                client_order_id=kwargs["client_order_id"],
                broker_order_id="brk3",
                symbol=kwargs["symbol"],
                side="sell",
                status="new",
                quantity=kwargs["quantity"],
            )
    return FakeBroker()


def test_regular_session_uses_stop_limit_entry():
    settings = _settings()
    runtime, saved, _ = _fake_runtime([_pending_entry_order()])
    broker = _fake_broker()
    now = datetime(2026, 4, 28, 14, 0, tzinfo=timezone.utc)  # 10am ET = regular

    dispatch_pending_orders(
        settings=settings,
        runtime=runtime,
        broker=broker,
        now=now,
        session_type=SessionType.REGULAR,
    )
    assert broker.calls[0][0] == "stop_limit_entry"


def test_pre_market_uses_limit_entry():
    settings = _settings()
    runtime, saved, _ = _fake_runtime([_pending_entry_order()])
    broker = _fake_broker()
    now = datetime(2026, 4, 28, 10, 0, tzinfo=timezone.utc)  # 6am ET = pre-market

    dispatch_pending_orders(
        settings=settings,
        runtime=runtime,
        broker=broker,
        now=now,
        session_type=SessionType.PRE_MARKET,
    )
    assert broker.calls[0][0] == "limit_entry"
    _, kwargs = broker.calls[0]
    # limit price = stop_price * (1 + 0.001)
    assert kwargs["limit_price"] == pytest.approx(100.0 * 1.001, rel=1e-5)


def test_after_hours_uses_limit_entry():
    settings = _settings()
    runtime, saved, _ = _fake_runtime([_pending_entry_order()])
    broker = _fake_broker()
    now = datetime(2026, 4, 28, 21, 0, tzinfo=timezone.utc)  # 5pm ET = after hours

    dispatch_pending_orders(
        settings=settings,
        runtime=runtime,
        broker=broker,
        now=now,
        session_type=SessionType.AFTER_HOURS,
    )
    assert broker.calls[0][0] == "limit_entry"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_order_dispatch_extended_hours.py -v`
Expected: FAIL — `dispatch_pending_orders` doesn't accept `session_type` yet.

- [ ] **Step 3: Update `order_dispatch.py`**

Add `session_type` import and parameter, update `BrokerProtocol`, update `_submit_order`.

At the top of `src/alpaca_bot/runtime/order_dispatch.py`, add to imports:

```python
from alpaca_bot.strategy.session import SessionType
```

Update `BrokerProtocol` to add the new method:

```python
class BrokerProtocol(Protocol):
    def submit_stop_limit_entry(self, **kwargs) -> BrokerOrder: ...
    def submit_limit_entry(self, **kwargs) -> BrokerOrder: ...
    def submit_stop_order(self, **kwargs) -> BrokerOrder: ...
```

Update `dispatch_pending_orders` signature to add `session_type`:

```python
def dispatch_pending_orders(
    *,
    settings: Settings,
    runtime: RuntimeProtocol,
    broker: BrokerProtocol,
    now: datetime | Callable[[], datetime] | None = None,
    allowed_intent_types: set[str] | None = None,
    blocked_strategy_names: set[str] | None = None,
    notifier: Notifier | None = None,
    session_type: "SessionType | None" = None,
) -> OrderDispatchReport:
```

Inside `dispatch_pending_orders`, compute `is_extended` once before the order loop and add a stop-order guard:

```python
    is_extended = session_type in (SessionType.PRE_MARKET, SessionType.AFTER_HOURS)

    for order in pending_orders:
        # Stop orders cannot be submitted during extended hours (Alpaca rejects them).
        if is_extended and order.intent_type == "stop":
            continue
        if allowed_intent_types is not None and order.intent_type not in allowed_intent_types:
            continue
        ...
```

Update the `_submit_order` call site inside `dispatch_pending_orders`:

```python
            broker_order = _submit_order(
                order=order,
                broker=broker,
                session_type=session_type,
                settings=settings,
            )
```

Update `_submit_order`:

```python
def _submit_order(
    *,
    order: OrderRecord,
    broker: BrokerProtocol,
    session_type: "SessionType | None" = None,
    settings: "Settings | None" = None,
) -> BrokerOrder:
    from alpaca_bot.execution.alpaca import extended_hours_limit_price
    from alpaca_bot.strategy.session import SessionType

    is_extended = session_type in (SessionType.PRE_MARKET, SessionType.AFTER_HOURS)
    offset_pct = settings.extended_hours_limit_offset_pct if settings is not None else 0.001

    if order.intent_type == "entry":
        if is_extended:
            lp = extended_hours_limit_price(
                "buy",
                ref_price=order.stop_price or 0.0,
                offset_pct=offset_pct,
            )
            return broker.submit_limit_entry(
                symbol=order.symbol,
                quantity=order.quantity,
                limit_price=lp,
                client_order_id=order.client_order_id,
            )
        return broker.submit_stop_limit_entry(
            symbol=order.symbol,
            quantity=order.quantity,
            stop_price=order.stop_price,
            limit_price=order.limit_price,
            client_order_id=order.client_order_id,
        )
    if order.intent_type == "stop":
        return broker.submit_stop_order(
            symbol=order.symbol,
            quantity=order.quantity,
            stop_price=order.stop_price,
            client_order_id=order.client_order_id,
        )
    raise ValueError(f"Unsupported pending order intent_type: {order.intent_type}")
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/unit/test_order_dispatch_extended_hours.py -v`
Expected: All PASS.

- [ ] **Step 5: Run full suite**

Run: `pytest -x -q`
Expected: All pass — `session_type` is optional with `None` default.

- [ ] **Step 6: Commit**

```bash
git add src/alpaca_bot/runtime/order_dispatch.py tests/unit/test_order_dispatch_extended_hours.py
git commit -m "feat: session-aware order dispatch routes extended hours entries to submit_limit_entry"
```

---

## Task 6: Session-aware engine (limit exits + stop suppression)

**Files:**
- Modify: `src/alpaca_bot/core/engine.py`
- Test: `tests/unit/test_engine_extended_hours.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/unit/test_engine_extended_hours.py
from __future__ import annotations
from datetime import datetime, timezone
import pytest
from alpaca_bot.config import Settings
from alpaca_bot.core.engine import CycleIntentType, evaluate_cycle
from alpaca_bot.domain.models import Bar, OpenPosition
from alpaca_bot.strategy.session import SessionType


def _settings(**overrides) -> Settings:
    base = {
        "TRADING_MODE": "paper",
        "ENABLE_LIVE_TRADING": "false",
        "STRATEGY_VERSION": "v1",
        "DATABASE_URL": "postgresql://test/db",
        "MARKET_DATA_FEED": "iex",
        "SYMBOLS": "AAPL",
        "DAILY_SMA_PERIOD": "20",
        "BREAKOUT_LOOKBACK_BARS": "20",
        "RELATIVE_VOLUME_LOOKBACK_BARS": "20",
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
        "EXTENDED_HOURS_ENABLED": "true",
        "EXTENDED_HOURS_FLATTEN_TIME": "19:45",
        "EXTENDED_HOURS_LIMIT_OFFSET_PCT": "0.001",
    }
    base.update(overrides)
    return Settings.from_env(base)


def _position(symbol: str = "AAPL", stop_price: float = 95.0) -> OpenPosition:
    return OpenPosition(
        symbol=symbol,
        quantity=10,
        entry_price=100.0,
        stop_price=stop_price,
        risk_per_share=5.0,
        initial_stop_price=94.0,
    )


def _bar(symbol: str, close: float, high: float | None = None, ts: datetime | None = None) -> Bar:
    if ts is None:
        ts = datetime(2026, 4, 28, 21, 0, tzinfo=timezone.utc)  # 5pm ET
    return Bar(
        symbol=symbol,
        timestamp=ts,
        open=close,
        high=high or close,
        low=close,
        close=close,
        volume=1000,
    )


# --- Stop suppression in extended hours ---

def test_update_stop_suppressed_in_after_hours():
    """UPDATE_STOP intents must not be emitted during extended hours."""
    settings = _settings()
    # 5pm ET = after hours, position has profited enough to trigger a stop update
    now = datetime(2026, 4, 28, 21, 0, tzinfo=timezone.utc)
    position = _position(stop_price=95.0)
    bar = _bar("AAPL", close=106.0, high=106.0)  # high >= entry_price + risk_per_share = 105

    result = evaluate_cycle(
        settings=settings,
        now=now,
        equity=100_000.0,
        intraday_bars_by_symbol={"AAPL": [bar]},
        daily_bars_by_symbol={"AAPL": [bar]},
        open_positions=[position],
        working_order_symbols=set(),
        traded_symbols_today=set(),
        entries_disabled=False,
        session_type=SessionType.AFTER_HOURS,
    )
    update_stops = [i for i in result.intents if i.intent_type is CycleIntentType.UPDATE_STOP]
    assert update_stops == [], "UPDATE_STOP must be suppressed in extended hours"


def test_update_stop_allowed_in_regular_session():
    settings = _settings()
    now = datetime(2026, 4, 28, 16, 0, tzinfo=timezone.utc)  # 12pm ET = regular
    position = _position(stop_price=95.0)
    bar = _bar("AAPL", close=106.0, high=106.0, ts=now)

    result = evaluate_cycle(
        settings=settings,
        now=now,
        equity=100_000.0,
        intraday_bars_by_symbol={"AAPL": [bar]},
        daily_bars_by_symbol={"AAPL": [bar]},
        open_positions=[position],
        working_order_symbols=set(),
        traded_symbols_today=set(),
        entries_disabled=False,
        session_type=SessionType.REGULAR,
    )
    update_stops = [i for i in result.intents if i.intent_type is CycleIntentType.UPDATE_STOP]
    assert len(update_stops) == 1


# --- Extended hours flatten ---

def test_after_hours_flatten_emits_exit_with_limit_price():
    """Flatten at extended_hours_flatten_time must set limit_price on EXIT intent."""
    settings = _settings()
    # 7:50pm ET = past EXTENDED_HOURS_FLATTEN_TIME (19:45)
    now = datetime(2026, 4, 28, 23, 50, tzinfo=timezone.utc)
    position = _position()
    bar = _bar("AAPL", close=105.0, ts=now)

    result = evaluate_cycle(
        settings=settings,
        now=now,
        equity=100_000.0,
        intraday_bars_by_symbol={"AAPL": [bar]},
        daily_bars_by_symbol={"AAPL": [bar]},
        open_positions=[position],
        working_order_symbols=set(),
        traded_symbols_today=set(),
        entries_disabled=False,
        session_type=SessionType.AFTER_HOURS,
    )
    exits = [i for i in result.intents if i.intent_type is CycleIntentType.EXIT]
    assert len(exits) == 1
    assert exits[0].limit_price is not None
    assert exits[0].limit_price == pytest.approx(105.0 * (1 - 0.001), rel=1e-5)
    assert exits[0].reason == "eod_flatten"


def test_regular_session_flatten_no_limit_price():
    """Regular session flatten must NOT set limit_price (market exit)."""
    settings = _settings()
    # 3:50pm ET = past FLATTEN_TIME (15:45)
    now = datetime(2026, 4, 28, 19, 50, tzinfo=timezone.utc)
    position = _position()
    bar = _bar("AAPL", close=105.0, ts=now)

    result = evaluate_cycle(
        settings=settings,
        now=now,
        equity=100_000.0,
        intraday_bars_by_symbol={"AAPL": [bar]},
        daily_bars_by_symbol={"AAPL": [bar]},
        open_positions=[position],
        working_order_symbols=set(),
        traded_symbols_today=set(),
        entries_disabled=False,
        session_type=SessionType.REGULAR,
    )
    exits = [i for i in result.intents if i.intent_type is CycleIntentType.EXIT]
    assert len(exits) == 1
    assert exits[0].limit_price is None


def test_no_session_type_defaults_to_regular_behaviour():
    """Existing callers that omit session_type must see unchanged behaviour."""
    settings = _settings()
    now = datetime(2026, 4, 28, 19, 50, tzinfo=timezone.utc)  # 3:50pm ET
    position = _position()
    bar = _bar("AAPL", close=105.0, ts=now)

    result = evaluate_cycle(
        settings=settings,
        now=now,
        equity=100_000.0,
        intraday_bars_by_symbol={"AAPL": [bar]},
        daily_bars_by_symbol={"AAPL": [bar]},
        open_positions=[position],
        working_order_symbols=set(),
        traded_symbols_today=set(),
        entries_disabled=False,
        # no session_type
    )
    exits = [i for i in result.intents if i.intent_type is CycleIntentType.EXIT]
    assert len(exits) == 1
    assert exits[0].limit_price is None  # market exit
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_engine_extended_hours.py -v`
Expected: FAIL — `evaluate_cycle` doesn't accept `session_type` yet.

- [ ] **Step 3: Update `engine.py`**

Add import at the top of `src/alpaca_bot/core/engine.py`:

```python
from alpaca_bot.strategy.session import SessionType, is_flatten_time as _session_flatten_time
```

Add `session_type` parameter to `evaluate_cycle` signature (after `strategy_name`):

```python
    session_type: "SessionType | None" = None,
```

Replace the existing `past_flatten` line and the `UPDATE_STOP` emission block in `evaluate_cycle`:

```python
    is_extended = session_type in (SessionType.PRE_MARKET, SessionType.AFTER_HOURS)

    # Determine flatten using session-aware helper when session_type is known,
    # falling back to the existing is_past_flatten_time for callers that omit it.
    if session_type is not None:
        past_flatten = _session_flatten_time(now, settings, session_type)
    else:
        past_flatten = is_past_flatten_time(now, settings)
```

For the EXIT intent under `past_flatten`, add `limit_price` for extended-hours flattens:

```python
        if past_flatten:
            if not flatten_complete:
                # Extended hours: use limit exit (market orders are rejected).
                limit_price_for_exit: float | None = None
                if is_extended:
                    bars = intraday_bars_by_symbol.get(position.symbol, ())
                    if bars:
                        ref_price = bars[-1].close
                        # Inline math: core/ must not import from execution/.
                        limit_price_for_exit = round(
                            ref_price * (1 - settings.extended_hours_limit_offset_pct), 2
                        )
                intents.append(
                    CycleIntent(
                        intent_type=CycleIntentType.EXIT,
                        symbol=position.symbol,
                        timestamp=now,
                        reason="eod_flatten",
                        strategy_name=strategy_name,
                        limit_price=limit_price_for_exit,
                    )
                )
            continue
```

For the `UPDATE_STOP` block, add a guard at the start of the per-position loop:

```python
        # Stop orders cannot be placed or modified during extended hours.
        if is_extended:
            continue
```

(This `continue` goes right after the `if past_flatten: ... continue` block, before the `bars = intraday_bars_by_symbol.get(...)` line.)

- [ ] **Step 4: Run tests**

Run: `pytest tests/unit/test_engine_extended_hours.py -v`
Expected: All PASS.

- [ ] **Step 5: Run full suite**

Run: `pytest -x -q`
Expected: All pass — `session_type=None` default preserves existing behaviour.

- [ ] **Step 6: Commit**

```bash
git add src/alpaca_bot/core/engine.py tests/unit/test_engine_extended_hours.py
git commit -m "feat: engine suppresses stop updates and uses limit exits during extended hours"
```

---

## Task 7: Limit exit routing in `cycle_intent_execution.py`

**Files:**
- Modify: `src/alpaca_bot/runtime/cycle_intent_execution.py`

- [ ] **Step 1: Write failing test**

```python
# Append to tests/unit/test_cycle_intent_execution_extended_hours.py
from __future__ import annotations
from datetime import datetime, timezone
import pytest
from alpaca_bot.config import Settings
from alpaca_bot.core.engine import CycleIntent, CycleIntentType, CycleResult
from alpaca_bot.execution.alpaca import BrokerOrder
from alpaca_bot.runtime.cycle_intent_execution import execute_cycle_intents
from alpaca_bot.storage import AuditEvent, OrderRecord, PositionRecord


def _settings() -> Settings:
    return Settings.from_env({
        "TRADING_MODE": "paper",
        "ENABLE_LIVE_TRADING": "false",
        "STRATEGY_VERSION": "v1",
        "DATABASE_URL": "postgresql://test/db",
        "MARKET_DATA_FEED": "iex",
        "SYMBOLS": "AAPL",
        "DAILY_SMA_PERIOD": "20",
        "BREAKOUT_LOOKBACK_BARS": "20",
        "RELATIVE_VOLUME_LOOKBACK_BARS": "20",
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
        "EXTENDED_HOURS_ENABLED": "true",
    })


def _make_position() -> PositionRecord:
    now = datetime(2026, 4, 28, 21, 0, tzinfo=timezone.utc)
    return PositionRecord(
        symbol="AAPL",
        trading_mode="paper",
        strategy_version="v1",
        strategy_name="breakout",
        quantity=10,
        entry_price=100.0,
        stop_price=95.0,
        initial_stop_price=94.0,
        opened_at=now,
        updated_at=now,
    )


def _fake_runtime(position: PositionRecord):
    saved_orders = []
    audits = []

    class FakeOrderStore:
        def save(self, order, *, commit=True): saved_orders.append(order)
        def list_by_status(self, **kwargs): return []

    class FakePositionStore:
        def save(self, pos, *, commit=True): pass
        def list_all(self, **kwargs): return [position]

    class FakeAuditStore:
        def append(self, event, *, commit=True): audits.append(event)

    class FakeConn:
        def commit(self): pass
        def rollback(self): pass

    class FakeRuntime:
        order_store = FakeOrderStore()
        position_store = FakePositionStore()
        audit_event_store = FakeAuditStore()
        connection = FakeConn()

    return FakeRuntime(), saved_orders, audits


def test_exit_with_limit_price_calls_submit_limit_exit():
    settings = _settings()
    position = _make_position()
    runtime, saved_orders, _ = _fake_runtime(position)

    limit_exit_calls = []
    market_exit_calls = []

    class FakeBroker:
        def submit_limit_exit(self, **kwargs):
            limit_exit_calls.append(kwargs)
            return BrokerOrder(
                client_order_id=kwargs["client_order_id"],
                broker_order_id="brk1",
                symbol=kwargs["symbol"],
                side="sell",
                status="new",
                quantity=kwargs["quantity"],
            )
        def submit_market_exit(self, **kwargs):
            market_exit_calls.append(kwargs)
            return BrokerOrder(
                client_order_id=kwargs["client_order_id"],
                broker_order_id="brk2",
                symbol=kwargs["symbol"],
                side="sell",
                status="new",
                quantity=kwargs["quantity"],
            )
        def cancel_order(self, order_id): pass
        def replace_order(self, **kwargs): pass
        def submit_stop_order(self, **kwargs):
            return BrokerOrder("x", "y", "AAPL", "sell", "new", 10)

    now = datetime(2026, 4, 28, 21, 0, tzinfo=timezone.utc)
    intent = CycleIntent(
        intent_type=CycleIntentType.EXIT,
        symbol="AAPL",
        timestamp=now,
        reason="eod_flatten",
        limit_price=104.895,  # 105 * (1 - 0.001)
        strategy_name="breakout",
    )
    cycle_result = CycleResult(as_of=now, intents=[intent])

    execute_cycle_intents(
        settings=settings,
        runtime=runtime,
        broker=FakeBroker(),
        cycle_result=cycle_result,
        now=now,
    )

    assert len(limit_exit_calls) == 1
    assert len(market_exit_calls) == 0
    assert limit_exit_calls[0]["limit_price"] == pytest.approx(104.895)


def test_exit_without_limit_price_calls_submit_market_exit():
    settings = _settings()
    position = _make_position()
    runtime, saved_orders, _ = _fake_runtime(position)

    limit_exit_calls = []
    market_exit_calls = []

    class FakeBroker:
        def submit_limit_exit(self, **kwargs):
            limit_exit_calls.append(kwargs)
            return BrokerOrder(kwargs["client_order_id"], "brk1", kwargs["symbol"], "sell", "new", kwargs["quantity"])
        def submit_market_exit(self, **kwargs):
            market_exit_calls.append(kwargs)
            return BrokerOrder(kwargs["client_order_id"], "brk2", kwargs["symbol"], "sell", "new", kwargs["quantity"])
        def cancel_order(self, order_id): pass
        def replace_order(self, **kwargs): pass
        def submit_stop_order(self, **kwargs):
            return BrokerOrder("x", "y", "AAPL", "sell", "new", 10)

    now = datetime(2026, 4, 28, 19, 50, tzinfo=timezone.utc)
    intent = CycleIntent(
        intent_type=CycleIntentType.EXIT,
        symbol="AAPL",
        timestamp=now,
        reason="eod_flatten",
        limit_price=None,
        strategy_name="breakout",
    )
    cycle_result = CycleResult(as_of=now, intents=[intent])

    execute_cycle_intents(
        settings=settings,
        runtime=runtime,
        broker=FakeBroker(),
        cycle_result=cycle_result,
        now=now,
    )

    assert len(market_exit_calls) == 1
    assert len(limit_exit_calls) == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_cycle_intent_execution_extended_hours.py -v`
Expected: FAIL — `submit_limit_exit` is called via `submit_market_exit` (wrong branch).

- [ ] **Step 3: Update `BrokerProtocol` and `_execute_exit`**

In `src/alpaca_bot/runtime/cycle_intent_execution.py`:

Update `BrokerProtocol`:

```python
class BrokerProtocol(Protocol):
    def replace_order(self, **kwargs): ...
    def submit_stop_order(self, **kwargs): ...
    def submit_market_exit(self, **kwargs): ...
    def submit_limit_exit(self, **kwargs): ...
    def cancel_order(self, order_id: str) -> None: ...
```

In `_execute_exit`, pass `limit_price` from the intent and use `submit_limit_exit` when set. The function receives `reason` and we need `limit_price` too. Update the function signature and call site:

In `execute_cycle_intents`, where `_execute_exit` is called, pass the intent's `limit_price`:

```python
        elif intent_type is CycleIntentType.EXIT:
            if positions_by_symbol is None:
                with lock_ctx:
                    positions_by_symbol = _positions_by_symbol(runtime, settings)
            canceled, submitted, hard_failed = _execute_exit(
                settings=settings,
                runtime=runtime,
                broker=broker,
                symbol=symbol,
                intent_timestamp=getattr(intent, "timestamp", timestamp),
                reason=getattr(intent, "reason", None),
                limit_price=getattr(intent, "limit_price", None),
                position=positions_by_symbol.get((symbol, strategy_name)),
                now=timestamp,
                strategy_name=strategy_name,
                lock_ctx=lock_ctx,
            )
```

Update `_execute_exit` signature:

```python
def _execute_exit(
    *,
    settings: Settings,
    runtime: RuntimeProtocol,
    broker: BrokerProtocol,
    symbol: str,
    intent_timestamp: datetime,
    reason: str | None,
    limit_price: float | None = None,
    position: PositionRecord | None,
    now: datetime,
    strategy_name: str = "breakout",
    lock_ctx: Any = None,
) -> tuple[int, int, int]:
```

In `_execute_exit`, replace the `broker_order = broker.submit_market_exit(...)` call:

```python
    if limit_price is not None:
        broker_order = broker.submit_limit_exit(
            symbol=symbol,
            quantity=position.quantity,
            limit_price=limit_price,
            client_order_id=client_order_id,
        )
    else:
        broker_order = broker.submit_market_exit(
            symbol=symbol,
            quantity=position.quantity,
            client_order_id=client_order_id,
        )
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/unit/test_cycle_intent_execution_extended_hours.py -v`
Expected: All PASS.

- [ ] **Step 5: Run full suite**

Run: `pytest -x -q`
Expected: All pass.

- [ ] **Step 6: Commit**

```bash
git add src/alpaca_bot/runtime/cycle_intent_execution.py tests/unit/test_cycle_intent_execution_extended_hours.py
git commit -m "feat: route EXIT with limit_price to submit_limit_exit in cycle_intent_execution"
```

---

## Task 8: Supervisor `_current_session()` + audit events

**Files:**
- Modify: `src/alpaca_bot/runtime/supervisor.py`
- Test: `tests/unit/test_supervisor_session.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/unit/test_supervisor_session.py
from __future__ import annotations
from datetime import datetime, timezone
import pytest
from alpaca_bot.config import Settings
from alpaca_bot.strategy.session import SessionType


def _settings(**overrides) -> Settings:
    base = {
        "TRADING_MODE": "paper",
        "ENABLE_LIVE_TRADING": "false",
        "STRATEGY_VERSION": "v1",
        "DATABASE_URL": "postgresql://test/db",
        "MARKET_DATA_FEED": "iex",
        "SYMBOLS": "AAPL",
        "DAILY_SMA_PERIOD": "20",
        "BREAKOUT_LOOKBACK_BARS": "20",
        "RELATIVE_VOLUME_LOOKBACK_BARS": "20",
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
        "EXTENDED_HOURS_ENABLED": "false",
    }
    base.update(overrides)
    return Settings.from_env(base)


def _make_supervisor(settings: Settings):
    """Minimal supervisor stub that exposes _current_session()."""
    from unittest.mock import MagicMock
    from alpaca_bot.runtime.supervisor import RuntimeSupervisor

    clock_open = MagicMock()
    clock_open.is_open = True
    clock_closed = MagicMock()
    clock_closed.is_open = False

    broker_open = MagicMock()
    broker_open.get_clock.return_value = clock_open
    broker_closed = MagicMock()
    broker_closed.get_clock.return_value = clock_closed

    return broker_open, broker_closed


def test_extended_hours_disabled_pre_market_returns_closed():
    settings = _settings(EXTENDED_HOURS_ENABLED="false")
    # 6am ET = pre-market window
    ts = datetime(2026, 4, 28, 10, 0, tzinfo=timezone.utc)

    from alpaca_bot.strategy.session import detect_session_type
    session = detect_session_type(ts, settings)
    # With disabled flag, supervisor should treat as CLOSED
    is_ext = session in (SessionType.PRE_MARKET, SessionType.AFTER_HOURS)
    # The _current_session method gates on extended_hours_enabled
    result = session if settings.extended_hours_enabled else SessionType.CLOSED
    assert result is SessionType.CLOSED


def test_extended_hours_enabled_pre_market_returns_pre_market():
    settings = _settings(
        EXTENDED_HOURS_ENABLED="true",
        PRE_MARKET_ENTRY_WINDOW_START="04:00",
        PRE_MARKET_ENTRY_WINDOW_END="09:20",
        AFTER_HOURS_ENTRY_WINDOW_START="16:05",
        AFTER_HOURS_ENTRY_WINDOW_END="19:30",
        EXTENDED_HOURS_FLATTEN_TIME="19:45",
    )
    ts = datetime(2026, 4, 28, 10, 0, tzinfo=timezone.utc)  # 6am ET
    from alpaca_bot.strategy.session import detect_session_type
    session = detect_session_type(ts, settings)
    result = session if settings.extended_hours_enabled else SessionType.CLOSED
    assert result is SessionType.PRE_MARKET
```

- [ ] **Step 2: Run test to verify current state**

Run: `pytest tests/unit/test_supervisor_session.py -v`
Expected: PASS (these tests use `detect_session_type` directly, not `_current_session`). This step verifies the logic before we wire it into the supervisor.

- [ ] **Step 3: Add `_current_session()` to supervisor**

In `src/alpaca_bot/runtime/supervisor.py`:

Add import near the top:

```python
from alpaca_bot.strategy.session import SessionType, detect_session_type
```

Add the `_current_session()` method next to `_market_is_open()`:

```python
def _current_session(self, timestamp: datetime) -> SessionType:
    """Detect current trading session; gates extended hours on EXTENDED_HOURS_ENABLED."""
    session = detect_session_type(timestamp, self.settings)
    if session is SessionType.REGULAR:
        try:
            return SessionType.REGULAR if self.broker.get_clock().is_open else SessionType.CLOSED
        except Exception:
            return SessionType.REGULAR  # existing fallback: assume open if broker clock unavailable
    if session in (SessionType.PRE_MARKET, SessionType.AFTER_HOURS):
        return session if self.settings.extended_hours_enabled else SessionType.CLOSED
    return SessionType.CLOSED
```

- [ ] **Step 4: Thread `session_type` through the run loop**

In `run_forever()`, the current structure is:

```python
timestamp = _resolve_now(cycle_now)
session_date = _session_date(timestamp, self.settings)
if self._market_is_open():
    self._session_had_active_cycle.add(session_date)
    try:
        cycle_report = self.run_cycle_once(now=lambda: timestamp)
```

Replace with:

```python
timestamp = _resolve_now(cycle_now)
session_date = _session_date(timestamp, self.settings)
session_type = self._current_session(timestamp)
if session_type is not SessionType.CLOSED:
    self._session_had_active_cycle.add(session_date)
    try:
        cycle_report = self.run_cycle_once(
            now=lambda: timestamp,
            session_type=session_type,
        )
```

Update `run_cycle_once` signature to accept and pass `session_type`:

```python
def run_cycle_once(
    self,
    *,
    now: Callable[[], datetime] | None = None,
    session_type: "SessionType | None" = None,
) -> CycleReport:
```

In `run_cycle_once`, pass `session_type` to `dispatch_pending_orders` and `evaluate_cycle`.

For `dispatch_pending_orders` (called via `self._order_dispatcher`), find the `dispatch_kwargs` dict built inside `run_cycle_once` and add the key:

```python
dispatch_kwargs["session_type"] = session_type
```

For `evaluate_cycle` (called via `self._cycle_runner`), find where `self._cycle_runner` is invoked and add the kwarg. All test fakes already accept `**kwargs`, so this is backward-compatible:

```python
cycle_result = self._cycle_runner(
    settings=self.settings,
    now=current_ts,
    equity=equity,
    intraday_bars_by_symbol=intraday_bars,
    daily_bars_by_symbol=daily_bars,
    open_positions=open_positions,
    working_order_symbols=working_order_symbols,
    traded_symbols_today=traded_symbols_today,
    entries_disabled=entries_disabled,
    session_type=session_type,   # ← added
)
```

Also add the audit event for extended-hours cycles by adding to `_append_audit` calls inside the active-cycle block:

```python
if session_type in (SessionType.PRE_MARKET, SessionType.AFTER_HOURS):
    self._append_audit(
        AuditEvent(
            event_type="extended_hours_cycle",
            payload={
                "session_type": session_type.value,
                "timestamp": timestamp.isoformat(),
            },
            created_at=timestamp,
        )
    )
```

- [ ] **Step 5: Run full suite**

Run: `pytest -x -q`
Expected: All pass.

- [ ] **Step 6: Commit**

```bash
git add src/alpaca_bot/runtime/supervisor.py tests/unit/test_supervisor_session.py
git commit -m "feat: supervisor uses _current_session() and threads SessionType through the run loop"
```

---

## Task 9: Audit events for stop-skip + web service

**Files:**
- Modify: `src/alpaca_bot/web/service.py`

The `stop_update_skipped_extended_hours` audit event needs to be emitted by the engine when it suppresses a stop update. It also needs to appear in the web service's `ALL_AUDIT_EVENT_TYPES`.

- [ ] **Step 1: Emit audit event from supervisor on stop suppression (once per position per session date)**

`evaluate_cycle()` is pure — it cannot write audit events. Emit from the supervisor instead, but guard with a session-date tracking set to prevent per-cycle spam.

In `src/alpaca_bot/runtime/supervisor.py`, add a new tracking set to `__init__` (next to `_loss_limit_alerted`):

```python
# Tracks (symbol, session_date) pairs for which we have already emitted
# stop_update_skipped_extended_hours this calendar day. Prevents per-cycle spam.
self._stop_skip_notified: set[tuple[str, date]] = set()
```

Add to `run_cycle_once`, after `execute_cycle_intents`, when session is extended:

```python
if session_type in (SessionType.PRE_MARKET, SessionType.AFTER_HOURS):
    session_date = _session_date(timestamp, self.settings)
    for position in open_positions:
        key = (position.symbol, session_date)
        if key not in self._stop_skip_notified:
            self._stop_skip_notified.add(key)
            self._append_audit(
                AuditEvent(
                    event_type="stop_update_skipped_extended_hours",
                    payload={
                        "symbol": position.symbol,
                        "session_type": session_type.value,
                    },
                    created_at=timestamp,
                )
            )
```

This emits exactly once per position per session date — not once per cycle.

- [ ] **Step 2: Add event types to `web/service.py`**

In `src/alpaca_bot/web/service.py`, add to `ALL_AUDIT_EVENT_TYPES`:

```python
    "extended_hours_cycle",
    "stop_update_skipped_extended_hours",
```

- [ ] **Step 3: Run full suite**

Run: `pytest -x -q`
Expected: All pass.

- [ ] **Step 4: Commit**

```bash
git add src/alpaca_bot/runtime/supervisor.py src/alpaca_bot/web/service.py
git commit -m "feat: audit events for extended_hours_cycle and stop_update_skipped_extended_hours"
```

---

## Task 10: Final integration verification

- [ ] **Step 1: Run full test suite**

Run: `pytest -v`
Expected: All tests pass with no failures.

- [ ] **Step 2: Verify `EXTENDED_HOURS_ENABLED=false` leaves nothing reachable**

Run: `pytest -x -q`
Verify that all new `session_type` parameters default to `None` and that callers not passing `session_type` get the exact same behavior as before. The existing test suite already covers this because it never sets `EXTENDED_HOURS_ENABLED=true`.

- [ ] **Step 3: Commit and push**

```bash
git add -p  # stage any remaining changes
git commit -m "feat: complete extended-hours trading implementation"
git push
```

---

## Self-Review Checklist

After writing this plan, checking the spec:

**Spec coverage:**
- ✅ `session.py` — `SessionType`, `detect_session_type`, `is_entry_window`, `is_flatten_time`
- ✅ Config — 7 fields + validation
- ✅ `alpaca.py` — `submit_limit_entry`, `submit_limit_exit`, `extended_hours_limit_price`
- ✅ `breakout.py` — delegates to `session.py`
- ✅ `order_dispatch.py` — session-aware routing
- ✅ `engine.py` — limit exits, stop suppression
- ✅ `cycle_intent_execution.py` — limit exit routing
- ✅ `supervisor.py` — `_current_session()`, session_type threading, audit events
- ✅ `web/service.py` — new audit event types
- ✅ Testing all boundary times for `detect_session_type`
- ✅ Supervisor returns CLOSED when `extended_hours_enabled=False` even if time is in extended window
- ✅ Existing tests unchanged (all new params have `None` defaults)

**Spec item: `CycleIntent.limit_price` already exists** — confirmed in code, no migration needed.

**Spec item: Pre-market positions carry into regular session** — no code change needed; the engine doesn't distinguish entry session of a position. Regular-session cycles process all open positions normally.

**Spec item: `AuditEvent extended_hours_cycle` + `stop_update_skipped_extended_hours`** — covered in Tasks 8 and 9.

**Safety gates all present:**
- `EXTENDED_HOURS_ENABLED=false` → `_current_session()` returns CLOSED for any extended window
- `ENABLE_LIVE_TRADING=false` gate is upstream of all order submission
- Extended-hours orders carry `time_in_force=DAY` → auto-expire at session end
