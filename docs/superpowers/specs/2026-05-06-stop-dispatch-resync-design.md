# Stop-Dispatch Resync: Self-Healing Exception Recovery

**Date:** 2026-05-06  
**Status:** Approved  
**Impact:** Eliminates 1,131+ `stop_update_failed` events/day and EOD position flatten failures

---

## Problem Statement

Two broker API errors are causing cascading failures in the stop-dispatch path:

- **40010001 "client_order_id must be unique"**: Fires on Path C of `_execute_update_stop()` when `submit_stop_order()` attempts to submit a stop that the broker already holds under the same `client_order_id`. Root cause: a prior Path C submission succeeded at the broker but failed before the DB write, leaving the broker and DB de-synced. Because `list_open_orders()` uses `status="open"` and Alpaca excludes "pending_cancel" orders from that filter, reconciliation never heals the gap. Within the same 15-minute bar, `latest_bar.timestamp` is fixed, so every cycle regenerates the identical `client_order_id` and retries the failing submission forever.

- **40310000 "insufficient qty available"**: Fires when a stop order is in Alpaca's "pending_cancel" state (marked "canceled" in DB but not yet released by the broker). The broker order holds all shares for the position; any new submit — stop or market exit — fails. The error JSON includes a `related_orders` array of blocking broker order IDs. This causes EOD flatten failures (`exit_hard_failed`) because `_execute_exit()` tries to submit a market exit while the broker holds shares in a pending_cancel stop.

Both errors share the same reconciliation blindspot: **pending_cancel orders are invisible to `list_open_orders(status="open")`**.

---

## Root Cause Detail

### Reconciliation gap

`recover_startup_state()` imports broker open orders via `list_open_orders()`, which calls `GET /v2/orders?status=open`. Alpaca returns orders in states: `new`, `accepted`, `partially_filled`, `held` — but NOT `pending_cancel`. A stop that was cancelled on our side but not yet acknowledged by the broker lives in "pending_cancel" limbo and is completely invisible to reconciliation.

`ACTIVE_STOP_STATUSES` in `cycle_intent_execution.py` is:
```python
("pending_submit", "new", "accepted", "submitted", "partially_filled", "held")
```
"pending_cancel" is not included, so `_latest_active_stop_order()` returns `None`, and Path C fires.

### Path C client_order_id collision

`_stop_client_order_id()` produces:
```
{strategy_version}:{strategy_name}:{date}:{symbol}:stop:{latest_bar.timestamp.isoformat()}
```
`latest_bar.timestamp` is fixed for the entire 15-minute bar period. All cycles within the same bar generate the same ID. When Path C previously succeeded at the broker but the DB write failed, every subsequent cycle in the same bar attempts to re-submit the same ID → 40010001.

### EOD flatten: market exit blocked by phantom stop

`_execute_exit()` queries `_latest_active_stop_order()` to find stops to cancel before submitting the market exit. If the DB says the stop is "canceled" but the broker holds it in "pending_cancel", the cancel step is skipped. The subsequent `submit_market_exit()` fails with 40310000 because the broker order still holds all shares. `exit_hard_failed` is recorded; the position is not flattened.

---

## Fix Design

### Principle

**Self-healing exception recovery at the point of failure.** When a broker API call fails with a known resolvable error, the exception handler queries the broker for the blocking order(s), syncs them to the DB, and immediately retries or routes around the error. No new background reconciliation loop; no schema changes; no protocol changes.

### Fix 1 — New broker method: `get_open_orders_for_symbol()`

**File:** `src/alpaca_bot/execution/alpaca.py`

```python
def get_open_orders_for_symbol(self, symbol: str) -> list[BrokerOrder]:
    """Fetch all open orders for a single symbol, including held/pending states."""
    request = GetOrdersRequest(status="open", symbols=[symbol])
    raw = self._trading_client.get_orders(request)
    return [_map_order(o) for o in raw]
```

This is a targeted query (single symbol) that returns the same set as the existing reconciliation but scoped to one symbol. It is called only in exception handlers, not in the hot cycle path.

### Fix 2 — Path C exception handler: 40010001 resync

**File:** `src/alpaca_bot/runtime/cycle_intent_execution.py`, `_execute_update_stop()`

When `submit_stop_order()` raises a 40010001 error:

1. Call `broker.get_open_orders_for_symbol(symbol)` to fetch current broker state.
2. Find the order whose `client_order_id` matches the one we tried to submit.
3. UPSERT it into the DB (same path as `recover_startup_state()` uses for reconciliation).
4. Call `replace_order(broker_order_id=found.broker_order_id, stop_price=new_stop_price)` to move the stop to the correct price.
5. Audit-log `stop_order_resynced` on success.
6. If the order cannot be found (broker rejected between retry and query), fall through to existing `stop_update_failed` logging.

This converts a recurring failure into a one-time resync event that self-corrects within the same cycle.

### Fix 3 — `_execute_update_stop()` Path C: 40310000 unblock

**File:** `src/alpaca_bot/runtime/cycle_intent_execution.py`, `_execute_update_stop()`

When `submit_stop_order()` raises a 40310000 error:

1. Parse `related_orders` from the error JSON response body.
2. For each broker order ID in `related_orders`:
   a. Call `broker.cancel_order(broker_order_id)` (best-effort; ignore "not found").
   b. Update the matching DB order's status to `"pending_cancel"`.
3. Audit-log `blocking_stop_canceled` for each canceled order.
4. Do NOT immediately retry the stop submission in this cycle (avoid cascade). The next cycle will either find the stop cleared (proceeds normally) or encounter a now-trackable state.

Rationale for no-retry: after `cancel_order()` the broker puts the order in "pending_cancel" and shares may still be held for a few seconds. Retrying in the same cycle invites another 40310000. The next cycle (60s later) is the safe retry point.

### Fix 4 — `_execute_exit()` market exit: 40310000 unblock + retry

**File:** `src/alpaca_bot/runtime/cycle_intent_execution.py`, `_execute_exit()`

When `submit_market_exit()` raises a 40310000 error (shares blocked by phantom stop):

1. Parse `related_orders` from error JSON.
2. For each blocking broker order ID:
   a. Call `broker.cancel_order(broker_order_id)`.
   b. Update DB order status to `"pending_cancel"`.
3. Audit-log `blocking_stop_canceled_for_exit` for each.
4. **Retry `submit_market_exit()` once** — EOD context makes the retry worth the risk; the phantom stop should be released within milliseconds of cancel acknowledgment.
5. If retry also fails: log `exit_hard_failed` as before; the position will be covered by broker risk controls or next-session opening.

The EOD retry distinguishes this from Fix 3 (stop update path) because flatten time is time-constrained — waiting 60 seconds for the next cycle risks missing market hours entirely.

### Error detection

40010001 and 40310000 are detected by inspecting the raised exception. The Alpaca SDK raises `APIError` with a body containing `{"code": <int>, ...}`. Extract via:

```python
error_code = getattr(exc, "status_code", None) or _parse_alpaca_error_code(exc)
```

Where `_parse_alpaca_error_code()` parses JSON from the exception string if `status_code` is unavailable. Both error codes are documented in Alpaca's API reference.

---

## Data Flow

```
_execute_update_stop() Path C
    │
    ├─ submit_stop_order() → 40010001
    │       └─ get_open_orders_for_symbol()
    │               ├─ UPSERT conflicting order to DB
    │               └─ replace_order(broker_order_id, new_stop_price)
    │
    └─ submit_stop_order() → 40310000
            ├─ parse related_orders from error JSON
            ├─ cancel_order() each blocking order
            └─ update DB status → "pending_cancel"

_execute_exit()
    └─ submit_market_exit() → 40310000
            ├─ parse related_orders from error JSON
            ├─ cancel_order() each blocking order
            ├─ update DB status → "pending_cancel"
            └─ retry submit_market_exit() once
```

---

## Audit Events

| Event | When emitted |
|---|---|
| `stop_order_resynced` | Path C 40010001: conflicting stop found, upserted, replaced |
| `blocking_stop_canceled` | Path C or exit 40310000: blocking order canceled |
| `blocking_stop_canceled_for_exit` | Exit 40310000: blocking order canceled before retry |
| `stop_update_failed` | Existing — retained for errors outside these two codes |
| `exit_hard_failed` | Existing — retained for exit retry failure |

---

## Error Handling

- `get_open_orders_for_symbol()` can raise if broker is unavailable. Wrap in try/except; log `stop_update_failed` on failure (existing behavior), do not raise.
- `cancel_order()` in the 40310000 handler is best-effort: if the cancel itself fails (order already gone), swallow the error and continue.
- `replace_order()` in the 40010001 handler can fail if the found stop is already filled or canceled. Wrap in try/except; log `stop_update_failed` on failure.
- The exit retry (Fix 4) wraps both calls in one try/except to ensure `exit_hard_failed` is still emitted if the retry fails.

---

## Testing

All tests use the project's DI pattern (fake callables, in-memory stores). No mocks of internal classes.

**Test 1 — 40010001 resync (stop update path):**
- Fake broker: `get_open_orders_for_symbol()` returns one order matching the client_order_id; `replace_order()` succeeds.
- Assert: `replace_order()` called with correct broker_order_id and stop_price; `stop_order_resynced` audit event emitted; no `stop_update_failed`.

**Test 2 — 40010001 resync fails (no matching order found):**
- Fake broker: `get_open_orders_for_symbol()` returns empty list.
- Assert: `stop_update_failed` audit event emitted; no crash.

**Test 3 — 40310000 unblock (stop update path):**
- Fake broker: `submit_stop_order()` raises 40310000 with `related_orders: ["broker-id-1"]`; `cancel_order()` succeeds.
- Assert: `cancel_order("broker-id-1")` called; DB order status updated to "pending_cancel"; `blocking_stop_canceled` emitted; no retry of stop submission.

**Test 4 — 40310000 unblock (exit path, retry succeeds):**
- Fake broker: first `submit_market_exit()` raises 40310000 with `related_orders: ["broker-id-2"]`; `cancel_order()` succeeds; second `submit_market_exit()` succeeds.
- Assert: `blocking_stop_canceled_for_exit` emitted; exit order created; no `exit_hard_failed`.

**Test 5 — 40310000 unblock (exit path, retry fails):**
- Fake broker: first `submit_market_exit()` raises 40310000; cancel succeeds; second `submit_market_exit()` also fails.
- Assert: `exit_hard_failed` emitted; no crash.

**Test 6 — `get_open_orders_for_symbol()` broker error during resync:**
- Fake broker: `get_open_orders_for_symbol()` raises; `submit_stop_order()` already raised 40010001.
- Assert: `stop_update_failed` emitted (falls back to existing behavior); no crash.

---

## Out of Scope

- Fixing the underlying `list_open_orders()` reconciliation to include "pending_cancel" orders — that is a broader change with wider surface area and is deferred.
- Adding `pending_cancel` to `ACTIVE_STOP_STATUSES` — would require understanding how that state interacts with Path A and B logic throughout the intent execution flow.
- Changing the Path C `client_order_id` scheme — the timestamp-based scheme is correct; the collision is caused by the DB/broker desync, which this fix resolves at the source.
- Deduplication of `get_open_orders_for_symbol()` calls — this is called at most once per symbol per cycle (only on error), so batching is not needed.
