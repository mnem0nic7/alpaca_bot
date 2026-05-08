---
title: Recovery Stop Above Market — Market Exit Fallback
date: 2026-05-08
status: approved
---

## Problem

`startup_recovery` runs every cycle (both at startup and inside `run_cycle_once`). Its second
pass finds open positions with no active stop order and queues a recovery stop at the position's
stored `stop_price`.

When `stop_price >= current_market_price` (e.g. a long position whose stop was set during an
earlier run at $16.34, but the stock has since fallen to $15.47), Alpaca rejects the stop with
error **42210000** ("stop price must be less than the current market price for a sell stop
order"). The order record goes to status `"error"`. On the next cycle, `startup_recovery` sees
the `"error"` status and re-queues the identical stale stop — creating an infinite loop where
the position is permanently unprotected.

Live incident: ARLO, entry $16.27, DB `stop_price` $16.34, market $15.47 — failing every
cycle since ~15:55 UTC 2026-05-08.

---

## Root Cause

The re-queue guard in `startup_recovery.py` (second pass):

```python
if existing_recovery_stop is not None and existing_recovery_stop.status not in {
    "expired", "cancelled", "canceled", "error"
}:
    continue
```

`"error"` is in the exclusion set, so the guard _falls through_ when status is `"error"`,
causing re-queue. The recovery stop is re-submitted at the same stale `stop_price`, which
Alpaca rejects again with 42210000.

---

## Design

### Two-file change

#### 1. `src/alpaca_bot/runtime/startup_recovery.py`

**A. Build `active_exit_symbols` alongside `active_stop_symbols`.**

Currently only stop-intent active orders populate a skip-set. Exits queued in a prior cycle
need the same protection.

```python
active_exit_symbols = {
    o.symbol for o in local_active_orders if o.intent_type == "exit"
}
```

**B. Skip symbols already covered by an active exit.**

After the `active_stop_symbols` check, add:

```python
if pos.symbol in active_exit_symbols:
    continue
```

**C. Compute `current_price` from `BrokerPosition.market_value`.**

`broker_positions_by_symbol` (already built in the first pass) contains the live Alpaca
position, which carries `market_value`. Divide by `quantity` to get an approximate current
price without an extra API call.

```python
broker_pos = broker_positions_by_symbol.get(pos.symbol)
current_price: float | None = None
if broker_pos and broker_pos.market_value is not None and broker_pos.quantity > 0:
    current_price = broker_pos.market_value / broker_pos.quantity
```

**D. Branch on whether the stop would be above-market.**

```python
if current_price is not None and pos.stop_price >= current_price:
    # Stale stop — would be rejected 42210000. Queue market exit instead.
    order = OrderRecord(
        intent_type="exit",
        side="sell",
        symbol=pos.symbol,
        quantity=pos.quantity,
        stop_price=None,
        client_order_id=f"startup_recovery:{version}:{today}:{pos.symbol}:exit",
        ...
    )
    runtime.order_store.save(order)
    runtime.audit_store.append(AuditEvent(
        event_type="recovery_exit_queued_stop_above_market",
        payload={
            "symbol": pos.symbol,
            "stop_price": pos.stop_price,
            "current_price": current_price,
        },
    ))
else:
    # Normal path — queue recovery stop as before.
    ...
```

**E. Preserve fallback.** If `current_price` is `None` (market_value unavailable), fall
through to the existing stop-queue path unchanged. This is the safe default and keeps
behavior identical when Alpaca doesn't return a position value.

#### 2. `src/alpaca_bot/runtime/order_dispatch.py`

`_submit_order_to_broker` currently raises `ValueError` for any `intent_type` other than
`"entry"` or `"stop"`. Add `"exit"` handling before the raise:

```python
if order.intent_type == "exit":
    return broker.submit_market_exit(
        symbol=order.symbol,
        quantity=order.quantity,
        client_order_id=order.client_order_id,
    )
```

---

## Data Flow

```
startup_recovery (second pass, per symbol)
  │
  ├─ active stop exists? → continue (no change)
  ├─ active exit exists? → continue (NEW guard B)
  │
  ├─ compute current_price from broker_positions_by_symbol (NEW C)
  │
  ├─ stop_price >= current_price AND current_price known?
  │     YES → save exit OrderRecord + audit event (NEW D)
  │           → order_dispatch picks it up next cycle
  │           → broker.submit_market_exit() called (NEW in order_dispatch)
  │     NO  → existing stop-queue path (unchanged)
  └─
```

---

## Intent/Dispatch Separation

The exit is written as a `pending_submit` `OrderRecord` in Postgres (same two-phase pattern
as entries and stops). `dispatch_pending_orders()` picks it up and calls
`broker.submit_market_exit()`. This means:
- The intent survives a crash between the write and the dispatch.
- No direct broker call from `startup_recovery` (pure intent-writing only).
- The dispatch path is extended, not bypassed.

---

## Audit Trail

Every stale-stop → exit downgrade emits `recovery_exit_queued_stop_above_market` with
`symbol`, `stop_price`, and `current_price`. This makes the fallback observable without
requiring log-scraping.

---

## Edge Cases

| Scenario | Behaviour |
|---|---|
| `market_value` is None | Falls back to stop queue (same as today) |
| `quantity == 0` | `current_price` stays None → fallback |
| Exit order already queued (prior cycle) | `active_exit_symbols` guard skips re-queue |
| Stop becomes valid again next cycle | Not possible — once exited, position is gone |
| Extended-hours market (AH stop already on engine) | Engine software stop handles AH; this path only fires when no active stop exists |

---

## Tests

- `test_startup_recovery_queues_exit_when_stop_above_market` — broker_pos.market_value puts
  current price below stop_price; assert `OrderRecord(intent_type="exit")` is saved, audit
  event `recovery_exit_queued_stop_above_market` appended, no stop OrderRecord saved.
- `test_startup_recovery_falls_back_to_stop_when_market_value_none` — broker_pos has
  `market_value=None`; assert stop OrderRecord saved (existing path unchanged).
- `test_startup_recovery_skips_symbol_with_active_exit` — active exit already in order store;
  assert nothing new queued.
- `test_dispatch_exit_order_calls_submit_market_exit` — `order_dispatch` given exit
  OrderRecord; assert `broker.submit_market_exit` called with correct args.
- `test_startup_recovery_queues_stop_when_stop_below_market` — market price above stop;
  assert normal stop path (regression guard).

---

## Files Changed

| File | Change |
|---|---|
| `src/alpaca_bot/runtime/startup_recovery.py` | Add exit-symbols guard, current_price computation, stale-stop → exit branch, audit event |
| `src/alpaca_bot/runtime/order_dispatch.py` | Add `intent_type == "exit"` dispatch branch |
| `tests/unit/test_startup_recovery.py` | 5 new unit tests covering all branches |
