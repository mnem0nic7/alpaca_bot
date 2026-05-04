# Spec: Partial-Fill Wash Trade Fix

**Date:** 2026-05-04  
**Branch:** stop-order-reliability-fixes

---

## Problem

When an entry order is **partially filled**, the unfilled remainder stays open at Alpaca as a live buy-side limit order. If the bot then attempts to submit a sell-side order (stop or market exit) for the same symbol, Alpaca rejects with error **40310000: "potential wash trade detected. use complex orders"**.

This was observed live on 2026-05-04 with SONO:
- Entry order: `partially_filled`, 187 shares @ $14.88, remaining buy-limit still open at broker
- Stop order: `pending_submit`, stop @ $14.00
- Loop every ~2 min: `dispatch_pending_orders` submits stop → Alpaca rejects → `status="error"` → next cycle emits EXIT intent → `_execute_exit` submits market exit → Alpaca rejects → `recovery_stop_queued_after_exit_failure` → new stop `pending_submit` → repeat

---

## Root Cause

Two submission paths both fail with wash trade rejection:

1. **`dispatch_pending_orders()`** (`runtime/order_dispatch.py`): tries `broker.submit_stop_order()` while entry buy-limit is open
2. **`_execute_exit()`** (`runtime/cycle_intent_execution.py`): tries `broker.submit_market_exit()` while entry buy-limit is open

Neither path currently checks whether there is an open (partially_filled) entry order before submitting the sell-side order.

---

## Fix

Before submitting any sell-side order for a symbol, detect and cancel the open buy-side partial-fill entry order at the broker first.

### Path 1 — `dispatch_pending_orders()` (stop dispatch)

Before calling `_submit_order()` for a `stop`-type order:
1. Query `order_store.list_by_status(statuses=["partially_filled"])`, filter for `intent_type == "entry"` and `symbol == order.symbol`
2. For each matching order that has a `broker_order_id`: call `broker.cancel_order(entry.broker_order_id)`
3. Save the entry order with `status="canceled"` and emit `partial_fill_entry_canceled` audit event (atomic with DB save)
4. If the broker cancel raises: emit `partial_fill_cancel_failed` audit event and **skip** the stop order (leave it `pending_submit` for the next cycle — submitting it would fail with wash trade anyway)

### Path 2 — `_execute_exit()` (market/limit exit)

Just before calling `broker.submit_market_exit()` / `broker.submit_limit_exit()`:
1. Same query and cancel logic as Path 1
2. If cancel raises: emit `partial_fill_cancel_failed` audit event and **proceed anyway** (market exit might still work if Alpaca auto-cleared the partial fill; existing exception handling for market exit failure is already robust)

---

## Audit Events

| Event | Context | Payload |
|---|---|---|
| `partial_fill_entry_canceled` | Cancel succeeded | `{"entry_client_order_id": ..., "entry_broker_order_id": ..., "context": "stop_dispatch"\|"exit"}` |
| `partial_fill_cancel_failed` | Cancel raised | `{"entry_client_order_id": ..., "entry_broker_order_id": ..., "error": ..., "context": "stop_dispatch"\|"exit"}` |

---

## Files Changed

| File | Change |
|---|---|
| `src/alpaca_bot/runtime/order_dispatch.py` | Add `cancel_order` to `BrokerProtocol`; add `_cancel_partial_fill_entry()` helper; call it before `_submit_order()` for stop orders |
| `src/alpaca_bot/runtime/cycle_intent_execution.py` | Add `_cancel_partial_fill_entry()` helper; call it before `submit_market_exit/limit_exit` |
| `tests/unit/test_order_dispatch.py` | Add `list_by_status()` + `cancel_order()` to fakes; add 2 new tests |
| `tests/unit/test_cycle_intent_execution.py` | Add 1 new test for exit path |

---

## Design Decisions

**Inline vs. shared helper**: Logic is inlined in each file rather than extracted to a shared module. The two call sites use different `RuntimeProtocol` variants and different exception-handling strategies. Sharing via import would couple `cycle_intent_execution.py` to `order_dispatch.py` with no real benefit at 2 call sites.

**Skip vs. error on cancel failure (dispatch)**: Leave the stop as `pending_submit` (not `status="error"`) when the partial fill cancel fails. This avoids triggering the recovery stop queue for a transient cancel failure — the next dispatch cycle will retry.

**Proceed vs. abort on cancel failure (exit)**: The exit path already has robust recovery logic (recovery stop queuing). Proceeding to attempt the market exit gives Alpaca a chance to succeed if the partial fill was auto-cleared. The existing exception handler covers the case where it still fails.

**`partially_filled` entries without `broker_order_id`**: Guard with `if entry.broker_order_id is not None`. A partially_filled entry will always have a broker_order_id (set at dispatch time), but defensive code is warranted.

**No new migration needed**: No schema changes.

---

## Safety Analysis

- **`evaluate_cycle()` purity**: Not affected — changes are in dispatch/execution layer.
- **Intent/dispatch separation**: Respected — we're adding pre-checks to the dispatch step, not bypassing the queue.
- **Audit trail**: Every cancel attempt is recorded (success or failure).
- **Paper vs. live mode**: Identical behavior — no mode-gating needed.
- **Wash trade idempotency**: If the entry was already canceled (e.g., by a prior cycle), `broker.cancel_order()` raises "already canceled" → the cancel loop already handles "already canceled" phrases with a warning and continues. We apply the same logic here.
- **Double-cancel risk**: We only cancel `partially_filled` entries — not `filled` or `canceled` ones. A `filled` entry means all shares were purchased and the buy-limit is gone; no wash trade blocker.
