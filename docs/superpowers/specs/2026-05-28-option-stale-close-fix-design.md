# Option Stale-Position Close and Dispatch Guard Fix — Design Spec

**Date:** 2026-05-28  
**Author:** auto (plan-and-refine pipeline)  
**Status:** Ready for implementation

---

## Problem Statement

Two bugs in `runtime/supervisor.py` prevent open short-put option positions from ever being closed:

1. **Stale OCC positions silently skipped**: `_close_stale_carryover_positions()` intentionally omits OCC (option) symbols from the EXIT intents it submits. Positions from prior sessions are detected but never acted on, accumulating indefinitely.

2. **Option dispatch fires at market close**: `dispatch_pending_option_orders()` is called unconditionally every cycle with no market-hours guard, causing order-submission failures when the cycle runs at or after 16:00 ET.

**Observed consequence**: 10 `short_option` positions from 2026-05-22 are stuck open. Three `bear_orb` short puts opened 2026-05-26 (AMLX, ALHC, AROC) have no close path.

---

## Root Cause Analysis

### Bug 1 — Stale OCC skip (supervisor.py, _close_stale_carryover_positions)

Commit `8a04791` (2026-05-26) added this split:

```python
stale_equity = [p for p in stale if not _is_occ_symbol(p.symbol)]
stale_options = [p for p in stale if _is_occ_symbol(p.symbol)]
# ... only stale_equity gets EXIT intents
```

The intent was to stop a "failure storm" of BTC (buy-to-close) orders being submitted after market close. However, commit `6cac7f4` (same day, earlier commit) had already added a proper market-hours guard inside `_execute_exit()` in `cycle_intent_execution.py`:

```python
if is_short and _is_short_option_symbol(symbol):
    local_time = now.astimezone(settings.market_timezone).time()
    if local_time < time(9, 30) or local_time >= time(16, 0):
        runtime.audit_event_store.append(AuditEvent(event_type="cycle_intent_skipped", ...))
        return (0, 0, 0)
```

The fix in `8a04791` was unnecessary — it disabled the close path entirely instead of trusting the existing market-hours guard. The correct behavior is to let EXIT intents flow for all stale positions and rely on `_execute_exit`'s guard to skip outside trading hours.

### Bug 2 — Unguarded option dispatch (supervisor.py line ~1082)

```python
option_broker = getattr(self, "_option_broker", None)
if option_broker is not None and option_order_store is not None:
    dispatch_pending_option_orders(
        settings=self.settings,
        runtime=self.runtime,
        broker=option_broker,
    )
```

No `session_type` check. When a cycle runs at 16:00 ET (boundary of REGULAR/AFTER_HOURS), dispatch is called against Alpaca with already-closed markets. `bear_orb` is an ORB (Opening Range Breakout) strategy — option entries only occur during REGULAR market hours. There is no legitimate reason to dispatch option orders outside REGULAR session.

---

## Fix Design

### Fix 1: Remove OCC skip from `_close_stale_carryover_positions`

Remove the equity/option partition. Submit EXIT intents for ALL stale positions regardless of symbol type. The market-hours guard in `_execute_exit` handles the timing safely:
- During REGULAR hours (09:30–16:00 ET): BTC order submitted normally.
- Outside REGULAR hours: `cycle_intent_skipped` audit event emitted, returns `(0, 0, 0)`, retry on next cycle.

**Audit event update**: Remove `skipped_exit_option_count` from the `stale_positions_detected` payload (it will always be 0 after the fix). Add `option_symbol_count` and `equity_symbol_count` for observability.

### Fix 2: Guard option dispatch with `session_type is SessionType.REGULAR`

Wrap the `dispatch_pending_option_orders` call:

```python
option_broker = getattr(self, "_option_broker", None)
if option_broker is not None and option_order_store is not None and session_type is SessionType.REGULAR:
    dispatch_pending_option_orders(
        settings=self.settings,
        runtime=self.runtime,
        broker=option_broker,
    )
```

`session_type` is already in scope as a parameter to `run_cycle_once`. `SessionType` is already imported in supervisor.py.

---

## Scope

**In scope:**
- Fix 1: `_close_stale_carryover_positions` in `supervisor.py`
- Fix 2: `dispatch_pending_option_orders` session guard in `supervisor.py`
- Unit tests for both changes

**Out of scope:**
- Legacy `reconciled_missing` position cleanup (separate ops task — positions in the `positions` table that were loaded from broker at startup recovery)
- `option_orders` table deduplication / idempotency (no evidence of duplicate fills causing financial harm; separate concern)
- Strategy circuit breakers / weighting changes

---

## Data Flow After Fix

```
Cycle runs (any session type)
  └─ _close_stale_carryover_positions()
       └─ All stale positions → EXIT intents (equity + OCC)
            └─ _execute_exit() [cycle_intent_execution.py]
                 ├─ REGULAR hours: submit BTC via submit_option_market_buy_to_close()
                 └─ Outside hours: cycle_intent_skipped audit event, retry next cycle

Cycle runs (REGULAR only)
  └─ dispatch_pending_option_orders()
       └─ pending_submit option orders → submitted to Alpaca
```

---

## Testing

**Test 1**: `_close_stale_carryover_positions` submits EXIT intents for OCC stale symbols (not just equity).  
**Test 2**: `_close_stale_carryover_positions` audit event payload contains `option_symbol_count` field.  
**Test 3**: `run_cycle_once` does NOT call `dispatch_pending_option_orders` when `session_type` is `AFTER_HOURS` or `PRE_MARKET`.  
**Test 4**: `run_cycle_once` DOES call `dispatch_pending_option_orders` when `session_type` is `REGULAR`.  

All tests use fake callables / in-memory stores per project DI pattern. No mocking of own classes.

---

## Risk Assessment

- **Financial risk**: Low. Fix 1 enables BTC orders that were always intended to be submitted. The market-hours guard in `_execute_exit` prevents any order from reaching Alpaca outside trading hours. Fix 2 reduces order failures and does not affect filled positions.
- **Audit trail**: Preserved. EXIT intents write to Postgres before broker submission. `cycle_intent_skipped` events are logged for each skipped BTC. `stale_positions_detected` audit event updated to include option count.
- **Rollback**: Both changes are single-method edits to `supervisor.py`. Revert is a one-line change each.
- **Paper vs. live**: Identical behavior — both go through same `_execute_exit` path, same market-hours guard.
