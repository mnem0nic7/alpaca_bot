# Update-Stop Partial-Fill Cancel Gap — Spec

**Date:** 2026-05-05

## Problem

`_execute_update_stop` in `cycle_intent_execution.py` has three code branches:

1. **Active stop with broker ID** → `broker.replace_order()` — safe, replaces in-place
2. **Active stop, no broker ID (pending_submit)** → update price in-place, dispatch will submit
3. **No active stop at all** → `broker.submit_stop_order()` — **missing cancel guard**

Branch 3 is reached when a position has no tracked stop order (e.g., the original stop was
never recorded, or the stop record was lost). It submits a fresh sell-side stop directly, which
Alpaca rejects with error 40310000 ("insufficient qty available for order") when a partially-filled
entry order is still open and holding all shares.

Both other sell-side submission sites already guard against this:
- `order_dispatch._cancel_partial_fill_entry` is called before `dispatch_pending_orders` submits
  a pending_submit stop (returns False on failure → skip/retry next cycle)
- `cycle_intent_execution._cancel_partial_fill_entry` is called at line 635 before
  `submit_market_exit` / `submit_limit_exit` (proceeds on failure — the outer error handler
  catches the 40310000 and fires `stop_update_failed` + notifier anyway)

Branch 3 lacks this guard, so the supervisor cycles every 60 seconds, hits 40310000, logs
"Broker call failed for update_stop on %s; skipping", and the position never gets a stop.

## Root Cause Observed

QQQ: 4 shares held entirely by a partial-fill entry order
(`related_orders: ["1b65d011-00a1-443b-bf64-006de1605165"]`). When `_execute_update_stop`
found no active stop record and tried to submit a new one, it got 40310000.

## Fix

1. **Add `context` parameter to `_cancel_partial_fill_entry` in `cycle_intent_execution.py`.**
   The function currently hardcodes `"context": "exit"` in both audit event payloads
   (`partial_fill_cancel_failed` and `partial_fill_entry_canceled`). Adding `context: str`
   (no default — require callers to be explicit) ensures each call site can be identified
   in the audit log.

2. **Update the existing exit-path call at line 635** to pass `context="exit"` explicitly.

3. **Call `_cancel_partial_fill_entry(... context="update_stop")` before `broker.submit_stop_order()`**
   in the `else` branch at line 268. Semantics: proceed on cancel failure (same as exit path),
   because:
   - The existing `except Exception` at line 299 already handles 40310000: fires
     `stop_update_failed` audit event + notifier alert.
   - Blocking on cancel failure would silently leave the position unprotected without
     any notifier alert — worse than letting the next error handler fire.

## Scope

- One file changed: `src/alpaca_bot/runtime/cycle_intent_execution.py`
  - `_cancel_partial_fill_entry`: add `context: str` parameter, propagate to payload dicts
  - Exit-path call (line ~635): add `context="exit"`
  - `_execute_update_stop` else branch (line ~268): add `_cancel_partial_fill_entry` call with
    `context="update_stop"` before `broker.submit_stop_order()`
- One test file extended: `tests/unit/test_cycle_intent_execution.py` — one new test
- No migration, no env var, no Settings change.

## Tests

One new test:
`test_execute_update_stop_cancels_partial_fill_entry_before_submitting_new_stop`:
- Setup: position with no active stop order, one partially_filled entry in the order store
- Action: CycleIntentType.UPDATE_STOP with a higher stop_price
- Assert:
  - `broker.cancel_calls` contains the partial entry's broker_order_id
  - `broker.stop_calls` has exactly one entry (submit_stop_order was called)
  - `partial_fill_entry_canceled` audit event present with `"context": "update_stop"`

## Safety Analysis

- **Financial safety**: This change only adds a cancel call before an already-attempted broker
  submission. No change to position sizing or stop price calculation. Proceed-on-failure
  semantics ensure no regression: the existing error handler (audit + notifier) still fires
  on 40310000.
- **Paper vs. live**: Identical behavior. The partial fill cancel check queries order status from
  Postgres; both modes use the same Postgres store.
- **Audit trail**: `partial_fill_entry_canceled` and `partial_fill_cancel_failed` events are
  appended unconditionally (success and failure respectively). No silent data loss.
- **Pure engine boundary**: `evaluate_cycle()` is untouched. This change is in the execution
  layer only.
- **No new env vars, no migrations.**
