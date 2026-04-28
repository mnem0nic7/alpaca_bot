# Extended Hours Trading — Design Spec

**Date:** 2026-04-28  
**Status:** Draft

---

## Overview

Add pre-market (4:00–9:30am ET) and after-hours (4:00–8:00pm ET) trading support to the alpaca-bot supervisor loop. Extended hours trading is gated behind `EXTENDED_HOURS_ENABLED=true` and defaults to off, preserving all existing behaviour when disabled.

---

## Constraints (Alpaca API)

- Only **limit orders** are accepted during extended hours. Stop, stop-limit, and market orders are rejected.
- Orders must set `extended_hours=True` on the request.
- Time-in-force must be `DAY` (the order expires at the end of the current session, not end of the next regular session).
- Stop orders **cannot** be placed or modified during extended hours. Desired stop prices are persisted in the DB and applied at the next regular-session cycle.

---

## Session Model

Three session types replace the current binary market-open/closed:

| SessionType | ET window | Cycles run? | Entry orders | Exit orders | Stop updates |
|---|---|---|---|---|---|
| `PRE_MARKET` | 04:00–09:30 | if enabled | limit + `extended_hours=True` | limit + `extended_hours=True` | skipped |
| `REGULAR` | 09:30–16:00 | always | stop-limit (existing) | market (existing) | as today |
| `AFTER_HOURS` | 16:00–20:00 | if enabled | limit + `extended_hours=True` | limit + `extended_hours=True` | skipped |
| `CLOSED` | all other times | never | — | — | — |

Session detection uses wall-clock time in `America/New_York`, not the broker's `is_open` clock. The broker clock gates only the `REGULAR` session; extended hours sessions are gated by local time + `EXTENDED_HOURS_ENABLED`.

---

## Configuration

Seven new env vars, all safe-defaulted, all validated in `Settings.from_env()`:

```
EXTENDED_HOURS_ENABLED=false              # master switch (bool)
PRE_MARKET_ENTRY_WINDOW_START=04:00       # time HH:MM ET
PRE_MARKET_ENTRY_WINDOW_END=09:20         # stop 10 min before regular open
AFTER_HOURS_ENTRY_WINDOW_START=16:05      # wait 5 min after regular close
AFTER_HOURS_ENTRY_WINDOW_END=19:30        # time HH:MM ET
EXTENDED_HOURS_FLATTEN_TIME=19:45         # close all after-hours positions by this time
EXTENDED_HOURS_LIMIT_OFFSET_PCT=0.001     # 0.1% offset for limit price oracle
```

Existing `ENTRY_WINDOW_START`, `ENTRY_WINDOW_END`, `FLATTEN_TIME` continue to govern the regular session unchanged.

**Validation rules added to `Settings.validate()`:**
- `PRE_MARKET_ENTRY_WINDOW_START < PRE_MARKET_ENTRY_WINDOW_END`
- `PRE_MARKET_ENTRY_WINDOW_END < time(9, 30)` (must finish before regular open)
- `AFTER_HOURS_ENTRY_WINDOW_START > time(16, 0)` (must start after regular close)
- `AFTER_HOURS_ENTRY_WINDOW_START < AFTER_HOURS_ENTRY_WINDOW_END < EXTENDED_HOURS_FLATTEN_TIME`
- `EXTENDED_HOURS_LIMIT_OFFSET_PCT > 0`

---

## Architecture

### New file: `src/alpaca_bot/strategy/session.py`

```python
class SessionType(enum.Enum):
    PRE_MARKET = "pre_market"
    REGULAR = "regular"
    AFTER_HOURS = "after_hours"
    CLOSED = "closed"

def detect_session_type(timestamp: datetime, settings: Settings) -> SessionType: ...
def is_entry_window(timestamp: datetime, settings: Settings, session: SessionType) -> bool: ...
def is_flatten_time(timestamp: datetime, settings: Settings, session: SessionType) -> bool: ...
```

`is_entry_session_time()` in `breakout.py` delegates to `session.py`; all strategies continue to call the same function name.

### Changes to `src/alpaca_bot/domain/models.py`

`CycleIntent` gains an optional `limit_price: float | None = None` field. When `limit_price` is set, `execute_cycle_intents()` routes to the limit exit path. Regular-session intents leave it `None` (no change to existing call sites).

### Changes to `src/alpaca_bot/execution/alpaca.py`

Two new broker methods:

```python
def submit_limit_entry(
    self, *, symbol, quantity, limit_price, client_order_id
) -> dict:
    """Extended-hours limit buy with extended_hours=True, time_in_force=DAY."""

def submit_limit_exit(
    self, *, symbol, quantity, limit_price, client_order_id
) -> dict:
    """Extended-hours limit sell with extended_hours=True, time_in_force=DAY."""
```

New helper (pure function, `execution/alpaca.py` or `risk/`):

```python
def extended_hours_limit_price(
    side: str, ref_price: float, offset_pct: float
) -> float:
    """Buy: ref + offset (cap on entry premium). Sell: ref - offset (floor on exit)."""
```

### Changes to `src/alpaca_bot/execution/order_dispatch.py`

`dispatch_pending_orders()` receives a `session_type: SessionType` argument. When `session_type` is `PRE_MARKET` or `AFTER_HOURS`:
- Use `broker.submit_limit_entry()` instead of `broker.submit_stop_limit_entry()`
- Limit price = `extended_hours_limit_price("buy", order.stop_price, settings.extended_hours_limit_offset_pct)`

### Changes to `src/alpaca_bot/core/engine.py`

`evaluate_cycle()` receives `session_type: SessionType`. When session is extended hours:
- Exit intent uses `limit` order type (carries `limit_price` in the intent payload)
- Stop update intents are suppressed (returned as no-ops)

`execute_cycle_intents()` routes exit intents to `broker.submit_limit_exit()` when limit_price is present in the intent.

### Changes to `src/alpaca_bot/runtime/supervisor.py`

`_market_is_open()` → replaced by `_current_session() -> SessionType`. Logic:

```python
def _current_session(self, timestamp: datetime) -> SessionType:
    session = detect_session_type(timestamp, self.settings)
    if session == SessionType.REGULAR:
        try:
            return SessionType.REGULAR if self.broker.get_clock().is_open else SessionType.CLOSED
        except Exception:
            return SessionType.REGULAR  # existing fallback
    if session in (SessionType.PRE_MARKET, SessionType.AFTER_HOURS):
        return session if self.settings.extended_hours_enabled else SessionType.CLOSED
    return SessionType.CLOSED
```

`run_forever()` passes `session_type` through to `run_cycle_once()`, which passes it to `dispatch_pending_orders()` and `evaluate_cycle()`.

### Changes to `src/alpaca_bot/strategy/breakout.py` (and all strategies)

`is_entry_session_time(timestamp, settings)` updated to use `session.py`:

```python
def is_entry_session_time(timestamp, settings) -> bool:
    session = detect_session_type(timestamp, settings)
    return is_entry_window(timestamp, settings, session)
```

No other changes to strategy files — signal logic is session-agnostic.

---

## Flatten-time behaviour

| Session | Flatten trigger | Action |
|---|---|---|
| Regular | `local_time >= settings.flatten_time` | market exit (existing) |
| After-hours | `local_time >= settings.extended_hours_flatten_time` | limit exit |
| Pre-market | carries into regular session | regular flatten applies |

Pre-market positions are **not** flattened at the regular-session open. They join the regular-session position pool and are governed by existing stop management + regular `FLATTEN_TIME`.

---

## Stop management during extended hours

Stop orders cannot be placed or cancelled during extended hours. The desired stop price is already stored in `OrderRecord.stop_price`. At the next regular-session cycle, `execute_cycle_intents()` will find open positions and apply stops as normal. No DB schema change required.

Risk disclosure (audit event): when a cycle runs in extended hours and stop updates are suppressed, an `AuditEvent(event_type="stop_update_skipped_extended_hours", ...)` is appended per affected position.

---

## Audit events

| event_type | When | Payload |
|---|---|---|
| `extended_hours_cycle` | Every extended-hours cycle | `{session_type, timestamp}` |
| `stop_update_skipped_extended_hours` | Each skipped stop update | `{symbol, session_type}` |

---

## Data feed

No change. `get_stock_bars()` returns extended-hours bars when the requested time range covers those periods. The existing intraday bar fetch already covers the data correctly.

---

## Safety gates

1. `EXTENDED_HOURS_ENABLED=false` (default) → all extended-hours code paths are unreachable; zero behavioural change for existing deployments.
2. `ENABLE_LIVE_TRADING=false` → paper mode gate is upstream of all order submission; applies equally to extended-hours orders.
3. Entry window validation in `Settings.validate()` prevents misconfigured windows from overlapping with regular session.
4. Extended-hours orders carry `time_in_force=DAY`, so they auto-expire at session end — no GTC orphan risk.

---

## Testing

New unit tests for:
- `detect_session_type()` across all boundary times (midnight, 3:59am, 4:00am, 9:29am, 9:30am, 16:00, 20:00)
- `is_entry_window()` for pre-market and after-hours windows
- `is_flatten_time()` for after-hours flatten
- `extended_hours_limit_price()` buy and sell, zero/negative guard
- `dispatch_pending_orders()` routes to `submit_limit_entry()` when session is PRE_MARKET/AFTER_HOURS
- `evaluate_cycle()` emits limit exit intents and suppresses stop updates during extended hours
- Supervisor `_current_session()` returns CLOSED when `extended_hours_enabled=False` even if time is in extended window
- `Settings.validate()` rejects invalid extended-hours window configs
- Existing tests unchanged — all pass with `extended_hours_enabled=False`

---

## Out of scope

- Crypto or options extended hours (Alpaca has separate APIs; out of scope)
- Carrying stop orders across the regular-session close into after-hours (Alpaca doesn't support it)
- Real-time quote streaming for limit price oracle (last bar close + offset is sufficient for v1)
- Cross-day position management (positions held overnight are a future concern)
