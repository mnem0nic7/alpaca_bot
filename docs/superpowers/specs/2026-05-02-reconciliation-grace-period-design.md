# Reconciliation Grace Period for Stop Orders — Design Spec

**Date:** 2026-05-02
**Status:** Approved for implementation
**Priority:** P1 — stop-order reliability follow-on

---

## Problem

`startup_recovery.py` reconciles local order state against the broker's open order list every cycle. When a local active order has no matching broker order, the reconciliation loop immediately writes `status="reconciled_missing"`.

For stop orders this is too aggressive. A stop at the broker may be temporarily invisible due to:
- Network blip between the bot and Alpaca's API
- Mid-fill window (stop is filling but not yet in the open orders list)
- Alpaca API pagination delay or transient 5xx
- Race between WebSocket trade update and the HTTP open-order poll

When `reconciled_missing` fires incorrectly on a valid stop, the position is unprotected until the next recovery cycle re-queues a recovery stop (added in the May 1 bug fixes). The Fix 2 recovery stop addresses the naked-position risk, but it introduces unnecessary churn: a stop is cleared, a recovery stop is queued, dispatched, and tracked — all for a transient API glitch.

**Goal:** Add a per-order miss counter so a stop order must be absent from the broker for N consecutive cycles before being cleared to `reconciled_missing`.

---

## Non-Goals

- Entry orders retain immediate `reconciled_missing` behavior (they are never the stop).
- No new env var. Threshold is a hard constant.
- No change to the Fix 2 recovery-stop logic. After a stop is cleared to `reconciled_missing`, Fix 2 will still queue a recovery stop on the next cycle.
- No change to `evaluate_cycle()`. It remains a pure function.

---

## Design

### Constant

```python
RECONCILIATION_MISS_THRESHOLD = 3
```

Three consecutive recovery cycles (≈3 minutes at 60s poll interval) before clearing a stop.

### New DB column

```sql
ALTER TABLE orders ADD COLUMN IF NOT EXISTS reconciliation_miss_count INTEGER NOT NULL DEFAULT 0;
```

Migration file: `migrations/011_add_reconciliation_miss_count.sql`

### OrderRecord change

Add `reconciliation_miss_count: int = 0` as the last field in the frozen dataclass. The default ensures all existing `OrderRecord(...)` constructions (without this kwarg) are backward-compatible.

```python
@dataclass(frozen=True)
class OrderRecord:
    ...
    reconciliation_miss_count: int = 0
```

### OrderStore changes (`repositories.py`)

1. Add `reconciliation_miss_count` to `_ORDER_SELECT_COLUMNS` (position 18, row index 18).
2. Add it to `_row_to_order_record()`: `reconciliation_miss_count=int(row[18]) if row[18] is not None else 0`.
3. Add it to the INSERT column list in `OrderStore.save()` (19th column, 19th `%s`).
4. Add `reconciliation_miss_count = EXCLUDED.reconciliation_miss_count` to the `DO UPDATE SET` clause.

The upsert always writes the caller-supplied count. This means:
- When the broker-open-orders write loop saves a matched order (constructing `OrderRecord` with default `reconciliation_miss_count=0`), the upsert resets the counter to 0 — the "found at broker" reset.
- When the grace-period logic increments the counter and saves, the upsert updates it.

### startup_recovery.py changes

Add constant at module top:

```python
RECONCILIATION_MISS_THRESHOLD = 3
```

In the unmatched-order write loop (`for order in local_active_orders: if order not in matched`), replace the unconditional `reconciled_missing` write with:

```python
# Grace period: stop orders accumulate misses before being cleared.
# Entry orders (and any non-stop order) keep immediate reconciled_missing.
is_stop_order = order.intent_type == "stop" and order.side == "sell"
new_miss_count = order.reconciliation_miss_count + 1

if is_stop_order and new_miss_count < RECONCILIATION_MISS_THRESHOLD:
    # Below threshold — increment and continue; do NOT write reconciled_missing.
    runtime.order_store.save(
        OrderRecord(
            ...same fields as existing reconciled_missing save...
            status=order.status,           # preserve current status
            reconciliation_miss_count=new_miss_count,
        ),
        commit=False,
    )
    runtime.audit_event_store.append(
        AuditEvent(
            event_type="reconciliation_miss_count_incremented",
            symbol=order.symbol,
            payload={
                "client_order_id": order.client_order_id,
                "reconciliation_miss_count": new_miss_count,
                "threshold": RECONCILIATION_MISS_THRESHOLD,
            },
            created_at=timestamp,
        ),
        commit=False,
    )
else:
    # Threshold reached (or non-stop order) — write reconciled_missing.
    runtime.order_store.save(
        OrderRecord(
            ...same fields as existing reconciled_missing save...
            status="reconciled_missing",
            reconciliation_miss_count=new_miss_count,  # carry the count for audit
        ),
        commit=False,
    )
    if is_stop_order:
        runtime.audit_event_store.append(
            AuditEvent(
                event_type="reconciled_missing_stop_cleared",
                symbol=order.symbol,
                payload={
                    "client_order_id": order.client_order_id,
                    "reconciliation_miss_count": new_miss_count,
                },
                created_at=timestamp,
            ),
            commit=False,
        )
```

### Reset on broker confirmation

The `for broker_order in broker_open_orders:` loop already saves each matched order as an `OrderRecord` with `reconciliation_miss_count` defaulting to 0. Because `DO UPDATE SET` includes `reconciliation_miss_count = EXCLUDED.reconciliation_miss_count`, the counter resets to 0 whenever the broker confirms the order is present. No extra code required.

---

## Audit Events

| Event | When emitted | Key payload fields |
|---|---|---|
| `reconciliation_miss_count_incremented` | Stop order missed for 1st or 2nd cycle | `client_order_id`, `reconciliation_miss_count`, `threshold` |
| `reconciled_missing_stop_cleared` | Stop order missed for 3rd cycle (threshold reached) | `client_order_id`, `reconciliation_miss_count` |

Entry orders that reach `reconciled_missing` emit no new event — the existing reconciliation mismatch reporting covers them.

---

## Interaction with Existing Logic

**Fix 2 (recovery stop for open positions):**
- After a stop is cleared to `reconciled_missing`, its symbol is removed from `ACTIVE_ORDER_STATUSES`-based queries on the NEXT cycle.
- On that next cycle, Fix 2 sees no active stop for the open position and queues a recovery stop.
- One-cycle delay between clearing and re-queuing is acceptable — the broker's stop was already absent for 3+ cycles.

**`active_stop_symbols` snapshot:**
- Built from `local_active_orders` at the start of each cycle (before writes).
- On the third-miss cycle, the stop is still in `active_stop_symbols` (loaded before the reconciliation write).
- Fix 2 recovery-stop loop skips that symbol. ✓
- On the next cycle, the stop is `reconciled_missing` → not in `ACTIVE_ORDER_STATUSES` → not in `active_stop_symbols` → Fix 2 queues a recovery stop. ✓

**`ACTIVE_ORDER_STATUSES`:**
- `reconciled_missing` is NOT in this set — cleared stops drop out of future reconciliation passes automatically. ✓

**Idempotency across cycles:**
- Cycle 1 miss: `reconciliation_miss_count` → 1, status unchanged.
- Cycle 2 miss: count → 2, status unchanged.
- Cycle 3 miss: count → 3, status → `reconciled_missing`.
- If the cycle crashes between the DB write and commit, the `commit=False` pattern means the connection rolls back, and the count is re-incremented on the next cycle (same cycle number). This is safe — the worst case is the counter is 1 short of what it should be, requiring one extra cycle. ✓

---

## OrderStoreProtocol

No changes. `reconciliation_miss_count` is a field on `OrderRecord`, not a method.

---

## Test Scenarios

### Test 1 — First miss: count incremented, status unchanged
- Setup: local stop order `reconciliation_miss_count=0`, status=`new`, no matching broker order
- Call `recover_startup_state(broker_open_orders=[])`
- Assert: saved order has `status="new"`, `reconciliation_miss_count=1`
- Assert: audit event `reconciliation_miss_count_incremented` appended, payload `reconciliation_miss_count=1`
- Assert: no order with `status="reconciled_missing"`

### Test 2 — Second miss: count incremented again, still not cleared
- Setup: local stop order `reconciliation_miss_count=1`, status=`new`
- Assert: saved order has `reconciliation_miss_count=2`, status unchanged

### Test 3 — Third miss: threshold reached, status written as reconciled_missing
- Setup: local stop order `reconciliation_miss_count=2`
- Assert: saved order has `status="reconciled_missing"`
- Assert: audit event `reconciled_missing_stop_cleared` appended

### Test 4 — Entry order misses immediately (no grace period)
- Setup: local entry order `reconciliation_miss_count=0`, no matching broker order
- Assert: saved order has `status="reconciled_missing"` on first miss
- Assert: no `reconciliation_miss_count_incremented` event

### Test 5 — Stop found at broker after 2 misses: counter reset to 0
- Setup: local stop order `reconciliation_miss_count=2`, status=`new`, broker_order_id=`broker-stop-1`
- `broker_open_orders` contains one order with `broker_order_id="broker-stop-1"`, status=`new`
- Assert: saved order has `reconciliation_miss_count=0` (reset via broker-open-orders write loop)
- Assert: no `reconciliation_miss_count_incremented` event
- Assert: no `reconciled_missing_stop_cleared` event

---

## File Change Summary

| File | Change |
|---|---|
| `migrations/011_add_reconciliation_miss_count.sql` | ADD COLUMN `reconciliation_miss_count INTEGER NOT NULL DEFAULT 0` |
| `src/alpaca_bot/storage/models.py` | Add `reconciliation_miss_count: int = 0` field to `OrderRecord` |
| `src/alpaca_bot/storage/repositories.py` | Add column to SELECT, INSERT, DO UPDATE SET; update `_row_to_order_record` |
| `src/alpaca_bot/runtime/startup_recovery.py` | Add `RECONCILIATION_MISS_THRESHOLD = 3`; replace unconditional `reconciled_missing` write with grace-period logic |
| `tests/unit/test_startup_recovery.py` | Add 5 new tests (Tests 1–5 above) |

---

## Deployment Safety

- Migration uses `ADD COLUMN IF NOT EXISTS` with `DEFAULT 0` — idempotent, zero-downtime.
- Existing rows get `reconciliation_miss_count=0` automatically.
- `migrate` service runs before `supervisor` in Docker Compose — no code/schema mismatch window.
- Rollback: `ALTER TABLE orders DROP COLUMN reconciliation_miss_count` (safe, field has default 0 in code too — but requires dropping the code change simultaneously).
