# Stop Order Reliability: Root Cause Analysis & Design

**Date:** 2026-05-02  
**Context:** Post-mortem on May 1, 2026 trading day failures  
**Status:** Spec

---

## Executive Summary

On May 1, 143 `order_dispatch_failed` errors (Alpaca 40310000), 24 `exit_hard_failed` events, 99 reconciliation mismatches, and one AGX wash trade were traced to five root causes. The primary cause was a single missing parameter (`limit=500`) in `list_open_orders()`. The secondary causes were a cascading recovery loop, an unprotected post-exit window, and a missing wash-trade guard.

---

## Root Cause Analysis

### RC-1 (Primary): `list_open_orders()` truncated at 50 orders

**File:** `src/alpaca_bot/execution/alpaca.py:231`

`get_orders(GetOrdersRequest(status="open"))` is called without a `limit` parameter. Alpaca's API defaults to 50. On a busy trading day with 50+ concurrent open orders (protective stops + pending entries), this silently drops all orders beyond the 50th.

**Evidence:** `runtime_reconciliation_detected` at 19:09:06 UTC shows `synced_order_count: 50`. The 51st+ orders — including MRVL, MSTR, SOUN protective stops — were classified as `local order missing at broker` and cleared to `reconciled_missing`.

### RC-2: Reconciliation clears active broker stops

**File:** `src/alpaca_bot/runtime/startup_recovery.py:469-489`

After RC-1 incorrectly marks a stop as missing, the "clear" pass writes `status="reconciled_missing"` to the local DB. Since `reconciled_missing` is not in `ACTIVE_ORDER_STATUSES`, the stop disappears from `active_stop_symbols` on the NEXT cycle.

**Mechanism:** `active_stop_symbols` is computed at line 247-249 from the pre-write `local_active_orders` snapshot, so the clearing stop is NOT re-queued in the SAME cycle it is cleared. But the NEXT cycle builds `active_stop_symbols` from the now-cleared DB → recovery stop queued.

### RC-3: Recovery stop infinite re-queue loop

**File:** `src/alpaca_bot/runtime/startup_recovery.py:338-341`

Idempotency check allows re-queuing the recovery stop when the previous attempt's status is `"error"`. After dispatch fails with 40310000 (broker has the real stop), the recovery stop status is set to `error` → next cycle re-queues → dispatch fails again → infinite loop.

**Evidence:** 15 identical `order_dispatch_failed` events for MRVL at ~2-minute intervals from 19:11 to 19:50. Alpaca consistently reported `related_orders: ["4c9a5044"]` — the original stop was active at the broker throughout.

### RC-4: EOD exit leaves position unprotected after stop cancel

**File:** `src/alpaca_bot/runtime/cycle_intent_execution.py` (`_execute_exit`)

At 19:47:39, the EOD flatten cancelled the protective stop (`canceled_stop_count: 1`) and then failed to submit the market exit (`submit_market_exit_failed`). The position was now open at Alpaca without any stop for ~2 minutes until the next cycle retried at 19:50.

**Note:** In this case the next retry succeeded, but there is no circuit-breaker: repeated failures in extended hours would leave the position permanently unprotected until the next cycle.

### RC-5: Wash trade — entry submitted while stop-sell is live for same symbol

**File:** `src/alpaca_bot/core/engine.py:253`

AGX: an entry limit order was submitted while a pending stop-sell existed at the broker for the same symbol. Alpaca rejected with wash trade error. The entry candidate screening (line 253) checks `open_position_symbols` and `working_order_symbols` but does not check for active stop orders for the same symbol.

---

## Design

### Fix 1: Paginate `list_open_orders()` fully

**Files to change:** `src/alpaca_bot/execution/alpaca.py`

Pass `limit=500` (Alpaca's maximum) to `GetOrdersRequest`. This is the simplest, highest-leverage fix. The system never holds 500+ open orders; even on the busiest day observed, fewer than 60 were open simultaneously.

```python
filters = GetOrdersRequest(status="open", limit=500)
```

If the codebase ever scales past 500 orders, proper pagination should be implemented (fetch until response is shorter than limit). For now, `limit=500` is sufficient and safe.

### Fix 2: Suppress recovery stop when broker has an active stop for the symbol

**Files to change:** `src/alpaca_bot/runtime/startup_recovery.py`

Pass the `broker_open_orders` list into the recovery-stop queuing decision. Before queuing a recovery stop for a symbol, check whether any broker open order for that symbol has `side="sell"` and `intent_type="stop"` (or equivalent). If the broker already has a stop, do NOT queue a recovery stop regardless of what the local DB says.

This closes the loop: even if reconciliation incorrectly clears a stop, the broker-presence check prevents the recovery stop from being submitted.

**Implementation:**
```python
broker_stop_symbols = {
    o.symbol
    for o in broker_open_orders
    if o.side == "sell" and "stop" in str(o.intent_type).lower()
}
# In the recovery-stop loop:
if pos.symbol in broker_stop_symbols:
    continue
```

Note: `broker_open_orders` is already fetched at the top of `run_startup_recovery()`; no additional API call needed.

### Fix 3: Treat `reconciled_missing` as a soft alert, not a hard clear for stops

**Files to change:** `src/alpaca_bot/runtime/startup_recovery.py`

When a local **stop** order is not found in broker open orders, emit an audit event (already done via `mismatches`) but do NOT immediately clear it to `reconciled_missing`. Instead, check it against a counter: only clear after it has been missing for N consecutive reconciliation passes (proposed: N=3, configurable via `RECONCILIATION_MISS_THRESHOLD`).

This is a defense-in-depth complement to Fix 1. Even with Fix 1, transient API glitches can return incomplete results; counting consecutive misses avoids false clearing.

For **entry** orders, the existing behavior (clear after 1 miss) is appropriate because stale entries should be expired.

**Implementation approach:** Add a `reconciliation_miss_count` counter to `OrderRecord` (or use a separate in-memory dict in the recovery function keyed by session date + client_order_id). Persist in `daily_session_state` or as a transient counter that resets on supervisor restart.

**Simpler alternative (preferred for V1):** Only apply the grace period for stop orders; keep the existing behavior for entry orders. This minimizes DB schema changes.

**Schema note:** A new column `reconciliation_miss_count INTEGER DEFAULT 0` on the `orders` table is needed.

### Fix 4: Re-queue protective stop on exit submission failure

**Files to change:** `src/alpaca_bot/runtime/cycle_intent_execution.py` (`_execute_exit`)

When the market exit submission fails after a stop cancel, queue a new stop order (recovery stop) immediately before returning the failure. This ensures the position is never left without stop protection.

```python
except Exception as exc:
    # Re-queue protective stop since we cancelled it but failed to exit
    _queue_recovery_stop_after_exit_failure(runtime, position, settings, now)
    raise
```

The new recovery stop uses the same client_order_id format as `startup_recovery` to benefit from the existing idempotency logic.

### Fix 5: Exclude symbols with active broker stop-sell orders from entry candidates

**Files to change:** `src/alpaca_bot/core/engine.py` (`evaluate_cycle`)

Extend the entry candidate filter to skip symbols with active stop-sell orders in `working_order_symbols`. Currently `working_order_symbols` is a set of symbols that already have working orders, but it is populated only from the caller. Ensure the caller populates it with symbols that have active STOP SELL orders too, not just entry/exit orders.

**Alternative (simpler):** Add a `stop_order_symbols` parameter to `run_cycle()` that is a set of symbols with active stop-sell orders. Entry candidates skip these symbols. This avoids modifying the pure `evaluate_cycle()` interface unnecessarily — the caller already has this information.

---

## Implementation Priority

| Fix | Risk | Leverage | Priority |
|-----|------|----------|----------|
| Fix 1: `limit=500` | Very low | Eliminates RC-1, unblocks RC-2 and RC-3 | **P0 — ship first** |
| Fix 2: Broker-stop check in recovery | Low | Eliminates RC-3 even if RC-1 recurs | **P1** |
| Fix 3: Grace period for stop clearing | Medium (schema change) | Defense-in-depth for RC-1+RC-2 | **P2** |
| Fix 4: Re-queue stop on exit failure | Low | Eliminates RC-4 | **P1** |
| Fix 5: Wash trade guard | Low | Eliminates RC-5 | **P1** |

---

## Data Model Changes

### `orders` table (Fix 3, optional)

```sql
ALTER TABLE orders ADD COLUMN reconciliation_miss_count INTEGER NOT NULL DEFAULT 0;
```

Migration is safe: `DEFAULT 0` means no backfill needed; existing rows get 0 on read.

---

## Testing Plan

### Fix 1
- Unit test: mock `get_orders` to return exactly 50 orders; assert `GetOrdersRequest` is called with `limit=500`
- Integration test: run startup_recovery with 55 mock broker orders; assert all 55 are synced

### Fix 2
- Unit test: build `broker_open_orders` with a stop-sell for MRVL; assert no recovery stop is queued for MRVL even when local MRVL stop is `reconciled_missing`

### Fix 3
- Unit test: run startup_recovery 2 consecutive times with MRVL stop absent from broker; assert status is NOT cleared after 1 miss, IS cleared after N misses

### Fix 4
- Unit test: `_execute_exit` fails on market exit submission after stop cancel; assert a recovery stop is queued in the same transaction

### Fix 5  
- Unit test: `evaluate_cycle` with a stop-sell active for AGX; assert AGX is not in entry candidates
- Extend existing wash-trade scenario test

---

## Audit Trail

All fixes emit audit events:
- Fix 2: `recovery_stop_suppressed_broker_has_stop` when broker-stop check fires
- Fix 3: `reconciliation_miss_count_incremented` (per-miss) and `reconciled_missing_stop_cleared` (when threshold hit)
- Fix 4: `recovery_stop_queued_after_exit_failure`
- Fix 5: logged via existing `cycle_completed` intent payload

---

## Non-Goals (out of scope)

- Full pagination for `list_open_orders()` (not needed below 500 open orders)
- Replaying missed trade stream events
- Automated post-mortem reports
