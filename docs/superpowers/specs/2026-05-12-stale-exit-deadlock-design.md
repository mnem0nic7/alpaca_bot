# Stale Exit Order Deadlock — Design Spec

**Date:** 2026-05-12  
**Incident:** OKLO, IRDM, RMBS sat open all day with prices below their stops.

---

## Root Cause

`_execute_exit()` in `cycle_intent_execution.py` guards against duplicate exit
submission by checking whether any exit order with an `ACTIVE_STOP_STATUSES`
status exists for the symbol. If one does, it logs `cycle_intent_skipped` and
returns without submitting a new exit.

The guard does **not** check whether the existing exit is from a prior trading
session. When an AH EOD-flatten exit order lands in Alpaca as `"new"` and is
never filled (Alpaca paper-trading does not honor `time_in_force=DAY` across
session boundaries for AH limit orders), it sits in the DB indefinitely at
status `"new"` with a live `broker_order_id`. Every subsequent cycle sees it as
"active" and blocks re-exit — a permanent deadlock until manual intervention.

---

## Failure Chain (2026-05-11 → 2026-05-12)

1. Positions entered during after-hours. Protective stops deferred
   (`pending_submit`, no broker ID).
2. EOD flatten (7:46 PM ET) fires during AH. Deferred stops are canceled. AH
   limit exits are submitted to Alpaca and accepted as `"new"` with broker IDs.
3. Alpaca paper-trading never fills or cancels these `DAY` limit orders at the
   next session open.
4. The next trading day, all cycles see status=`"new"` exits and log
   `cycle_intent_skipped / active_exit_order_exists` every 2 minutes.
5. Startup recovery detects `stop_price >= current_price` and queues
   `pending_submit` recovery exits. `dispatch_pending_orders()` submits them,
   but Alpaca rejects with `40310000` (insufficient qty) because the stale `"new"`
   AH exits are still live at the broker and hold the position.
6. Positions remain open all day. Manual cancellation was required.

---

## Scope

**One change in one file:** `_execute_exit()` in
`src/alpaca_bot/runtime/cycle_intent_execution.py`.

No schema changes, no new env vars, no new config keys.

---

## Design

### Staleness Classification

After reading `active_exit_orders` under the lock, classify each exit order for
the affected symbol as **stale** or **fresh**:

**Stale** — all three conditions must hold:
- `order.status in ("new", "held")`
- `order.broker_order_id is not None` (was actually submitted to the broker)
- ET date of `(order.signal_timestamp or order.created_at)` < today's ET session
  date

**Fresh** — everything else:
- `status == "pending_submit"` (queued, not yet submitted)
- `status in ("accepted", "submitted", "partially_filled")` (actively live)
- `status == "new"` but same-day (current-session, still being processed)
- `status == "new"` but no `broker_order_id` (shouldn't happen, but safe)

### Decision Logic

| Symbol exit orders | Action |
|---|---|
| None | Proceed with normal exit flow |
| Any fresh | Block: emit `cycle_intent_skipped / active_exit_order_exists`, return |
| Only stale | Cancel stale at broker; mark `canceled` in DB; proceed with exit |

If stale exit cancel fails with an **unrecognized** broker error (not "already
canceled / filled / not found"), the stale order may still be live. Block with
`cycle_intent_skipped / stale_exit_cancel_failed` to prevent double-sell.

If stale cancel fails with a **known-gone** phrase, the broker has already
removed it — mark it `canceled` in DB and proceed.

### New Audit Event

`stale_exit_canceled_for_resubmission` — emitted once per stale exit that is
successfully canceled. Payload:

```json
{
  "client_order_id": "<id>",
  "broker_order_id": "<id>",
  "original_status": "new"
}
```

### Code Flow After Classification

After stale exits are canceled and written to DB (in one lock block with
`commit`), execution resumes at the existing stop-order cancellation step. The
cycle then submits a fresh exit (market order during regular hours, limit order
during AH — driven by whether `limit_price` is set in the intent, unchanged from
today).

---

## Safety Analysis

**Double-sell prevention**: Fresh exit orders still block unconditionally.
Stale exits are only canceled when `status in ("new","held")` + prior day +
`broker_order_id` — a combination that cannot describe a same-session live order.

**Crash recovery**: If the supervisor crashes between broker cancel and DB
write, the stale exit remains `"new"` in DB. On the next cycle, the same staleness
check runs again, we attempt broker cancel again (Alpaca returns "already
canceled"), and we proceed — self-healing.

**Concurrent cycles**: The advisory lock prevents two supervisor instances.
Within one instance, the store lock serializes DB reads/writes with the trade
stream thread. Broker cancel calls happen outside the lock (same as today's
existing cancel-stop calls). The re-verify position check (already present)
guards against position-gone during broker calls.

**Paper vs. live**: Behavior is identical. In live trading, Alpaca cancels DAY
orders at session close, so stale exits would never accumulate. The staleness
check would find zero stale orders and proceed as today.

**No new env vars or Settings fields**: Uses existing `settings.market_timezone`
(defaults to `America/New_York`).

---

## Tests

New tests in `tests/unit/test_cycle_intent_execution.py` (following existing
DI-fake-callables pattern):

1. `test_stale_exit_detected_canceled_and_resubmitted` — core happy path
2. `test_fresh_exit_same_day_still_blocks` — same-day `"new"` exit blocks
3. `test_pending_submit_exit_blocks_resubmission` — pending_submit is fresh
4. `test_stale_and_fresh_exit_coexist_fresh_wins` — fresh takes priority over stale
5. `test_stale_exit_cancel_fails_unrecognized_error_blocks` — unrecognized cancel error → skip
6. `test_stale_exit_cancel_fails_already_canceled_proceeds` — known-gone → proceed
7. `test_stale_exit_skipped_when_active_exit_order_exists_rollback` — update of existing test (status `"accepted"` same-day should still block)

Existing test `test_execute_exit_skipped_when_active_exit_order_exists` remains
valid: it uses `status="accepted"` which is always fresh → still blocks. No
change needed to that test.

---

## What This Does NOT Change

- EOD flatten limit order submission (Alpaca requires limits in AH — correct)
- `time_in_force` on any order type
- Stop deferral logic (`pending_submit` stays `pending_submit` during AH)
- Startup recovery queuing behavior
- `dispatch_pending_orders()` — no changes there
- Any DB schema or migrations
