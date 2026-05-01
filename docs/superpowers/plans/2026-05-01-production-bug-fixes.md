# Production Bug Fixes — 2026-05-01

## Root Cause Summary

| Bug | Root Cause | Fix File(s) |
|---|---|---|
| 1 — Naked positions (97 min) | Stale-stop expiry marks prior-day pending stops "expired"; recovery logic only queues stops for *brand-new broker-only* positions, not for existing local positions whose stop was just expired | `runtime/startup_recovery.py` |
| 2 — Reconciliation lock (entries_disabled forever) | `ACTIVE_ORDER_STATUSES` missing `"held"` + `"pending_new"`; stop orders in Alpaca's "held" state are excluded from the local active-order query, so every reconciliation cycle sees them as "missing locally" → mismatches → `entries_disabled=True` | `runtime/startup_recovery.py`, `runtime/cycle_intent_execution.py` |
| 3 — No trailing stop | Not a separate code bug. Trailing ratchet only fires when `latest_bar.high >= profit_trigger`; positions never crossed the threshold. Combined with Bug 1 (no stop at all), positions had zero protection. Resolved by Bug 1 fix restoring stops. | — |
| 4 — Overfill observation | `filled_quantity` (202/17/13) matches `calculate_position_size` with correct equity ($100 k). `quantity` (20/7/4) is from a stale prior-session DB record. No position sizing bug. Add lifecycle logging for future diagnosis. | `runtime/trade_updates.py`, `runtime/order_dispatch.py` (logging only) |
| 5 — Supervisor instability (9 restarts) | Recovery exceptions propagate through `run_cycle_once()` → increment `_consecutive_cycle_failures` → `SystemExit(1)` after 10 → Docker restarts. Root cause is Bug 2. Fix Bug 2 + isolate recovery exceptions. | `runtime/supervisor.py` |

---

## Task 1 — Add `"held"` and `"pending_new"` to `ACTIVE_ORDER_STATUSES`

**File**: `src/alpaca_bot/runtime/startup_recovery.py` line ~16

Alpaca stop/stop-limit orders transition `new → accepted → held`. After reconciliation writes `status="held"`, the order disappears from `list_by_status(ACTIVE_ORDER_STATUSES)` on the next cycle, causing perpetual "broker order missing locally" mismatches.

```python
# Replace:
ACTIVE_ORDER_STATUSES = [
    "pending_submit", "submitting", "new", "accepted", "submitted", "partially_filled"
]

# With:
ACTIVE_ORDER_STATUSES = [
    "pending_submit", "submitting", "new", "accepted", "submitted",
    "partially_filled", "held", "pending_new",
]
```

---

## Task 2 — Add `"held"` to `ACTIVE_STOP_STATUSES`

**File**: `src/alpaca_bot/runtime/cycle_intent_execution.py` line ~20

`_latest_active_stop_order()` checks `ACTIVE_STOP_STATUSES` before submitting a new stop. Missing `"held"` causes it to not recognise the existing stop and attempt a double-submission.

```python
# Replace:
ACTIVE_STOP_STATUSES = ("pending_submit", "new", "accepted", "submitted", "partially_filled")

# With:
ACTIVE_STOP_STATUSES = (
    "pending_submit", "new", "accepted", "submitted", "partially_filled", "held",
)
```

---

## Task 3 — Queue recovery stop for any open position with no active stop

**File**: `src/alpaca_bot/runtime/startup_recovery.py`

**Problem**: After `order_dispatch.py` expires a stale `pending_submit` stop (created_date < session_date), the position is unprotected. The existing recovery code only catches brand-new broker positions with no local record — not the case of "local position + filled entry + no stop".

**Implementation**: Add a second recovery pass inside the existing `try:` write block (lines ~254+), AFTER `replace_all(synced_positions, ...)` and AFTER the `new_positions_needing_stop` loop.

Use `synced_positions` (already computed from broker reconciliation). Use `pos.stop_price` from `PositionRecord` (set from `initial_stop_price` at fill time; may have been ratcheted higher by trailing stop logic). Reuse the existing `startup_recovery:...` client_order_id format.

```python
# After the new_positions_needing_stop loop (~line 305), still inside the try: block:

# Second pass: queue recovery stops for any open position with no active stop
# (covers positions whose prior-day pending stop was expired by order_dispatch).
for pos in synced_positions:
    if pos.symbol in active_stop_symbols:
        continue
    if pos.symbol in pending_entry_symbols:
        _log.warning(
            "startup_recovery: skipping recovery stop for %s — pending_submit entry order exists",
            pos.symbol,
        )
        continue
    if pos.stop_price <= 0:
        _log.error(
            "startup_recovery: position %s has no valid stop_price (%.4f) — "
            "cannot queue recovery stop; position is unprotected",
            pos.symbol,
            pos.stop_price,
        )
        continue
    recovery_stop_id = (
        f"startup_recovery:{settings.strategy_version}:"
        f"{timestamp.date().isoformat()}:{pos.symbol}:stop"
    )
    # Belt-and-suspenders: don't re-queue if a non-terminal stop already exists
    # for this exact recovery ID (prevents duplicate write on repeated cycles before dispatch).
    existing_recovery_stop = runtime.order_store.load(recovery_stop_id)
    if existing_recovery_stop is not None and existing_recovery_stop.status not in {
        "expired", "cancelled", "canceled", "error"
    }:
        continue
    _log.warning(
        "startup_recovery: position %s has no active stop — "
        "queuing recovery stop at %.4f (qty=%d)",
        pos.symbol,
        pos.stop_price,
        pos.quantity,
    )
    runtime.order_store.save(
        OrderRecord(
            client_order_id=recovery_stop_id,
            symbol=pos.symbol,
            side="sell",
            intent_type="stop",
            status="pending_submit",
            quantity=pos.quantity,
            trading_mode=settings.trading_mode,
            strategy_version=settings.strategy_version,
            strategy_name=pos.strategy_name,
            created_at=timestamp,
            updated_at=timestamp,
            stop_price=pos.stop_price,
            initial_stop_price=pos.stop_price,
            signal_timestamp=None,
        ),
        commit=False,
    )
    runtime.audit_event_store.append(
        AuditEvent(
            event_type="recovery_stop_queued_for_open_position",
            symbol=pos.symbol,
            payload={
                "client_order_id": recovery_stop_id,
                "stop_price": pos.stop_price,
                "quantity": pos.quantity,
            },
            created_at=timestamp,
        ),
        commit=False,
    )
    active_stop_symbols.add(pos.symbol)  # prevent duplicate for same symbol in this loop
```

**Protocol update**: `OrderStoreProtocol` in `startup_recovery.py` must expose `load()`:

```python
class OrderStoreProtocol(Protocol):
    def save(self, order: OrderRecord, *, commit: bool = True) -> None: ...
    def list_by_status(self, *, trading_mode, strategy_version: str, statuses: list[str]) -> list[OrderRecord]: ...
    def load(self, client_order_id: str) -> OrderRecord | None: ...  # ← ADD THIS
```

**Test infrastructure update**: `RecordingOrderStore` in `tests/unit/test_startup_recovery.py` does NOT have `load()`. Any test with broker positions will now trigger Task 3's code path and call `load()`. Add `load()` to that fake class:

```python
def load(self, client_order_id: str) -> OrderRecord | None:
    # Check saved first (orders written during this recovery call), then pre-existing orders.
    # This matches UPSERT semantics: a stop queued by new_positions_needing_stop is visible
    # to the subsequent Task 3 load() check within the same recovery call.
    for order in reversed(self.saved):
        if order.client_order_id == client_order_id:
            return order
    for order in self.existing_orders:
        if order.client_order_id == client_order_id:
            return order
    return None
```

This ensures that when `new_positions_needing_stop` queues a stop for a brand-new position (writing to `self.saved`), Task 3's subsequent `load(recovery_stop_id)` call finds it and skips the duplicate.

**Idempotency analysis**:
- `active_stop_symbols` is populated from the DB *before* any writes (line 242). The first recovery cycle queues the stop (pending_submit). On the next cycle, `list_by_status(ACTIVE_ORDER_STATUSES)` returns the pending_submit stop (pending_submit IS in ACTIVE_ORDER_STATUSES), so `active_stop_symbols` contains the symbol → skip. ✓
- After dispatch submits the stop (status → new/held), `ACTIVE_ORDER_STATUSES` (after Task 1) includes those statuses → skip on all subsequent cycles. ✓

---

## Task 4 — Isolate recovery exceptions in `run_cycle_once`

**File**: `src/alpaca_bot/runtime/supervisor.py`

**Problem**: If `recover_startup_state()` raises (any exception), it propagates up through `run_cycle_once()` to `run_forever()`, which increments `_consecutive_cycle_failures`. After 10 failures, `SystemExit(1)` fires and Docker restarts the supervisor.

**Import change**:
```python
# In supervisor.py imports section, update the startup_recovery import:
from alpaca_bot.runtime.startup_recovery import (
    compose_startup_mismatch_detector,
    recover_startup_state,
    StartupRecoveryReport,  # ← ADD
)
```

**Code change**: wrap the recovery call inside the existing `with _rec_lock_ctx:` block:

```python
# Replace lines 212-247:
with _rec_lock_ctx:
    try:
        recovery_report = recover_startup_state(
            settings=self.settings,
            runtime=self.runtime,
            broker_open_positions=broker_open_positions,
            broker_open_orders=broker_open_orders,
            now=timestamp,
            audit_event_type=None,
        )
    except Exception:
        logger.exception("run_cycle_once: startup recovery raised — treating as empty report")
        try:
            self.runtime.connection.rollback()
        except Exception:
            pass
        recovery_report = StartupRecoveryReport(
            mismatches=(),
            synced_position_count=0,
            synced_order_count=0,
            cleared_position_count=0,
            cleared_order_count=0,
        )
        # Append audit event AFTER lock context (uses _append_audit which acquires its own lock).
        # Flag set here; appended below.
        _recovery_exception_occurred = True
    else:
        _recovery_exception_occurred = False
    if recovery_report.mismatches:
        try:
            self.runtime.audit_event_store.append(
                AuditEvent(
                    event_type="runtime_reconciliation_detected",
                    payload={
                        "mismatch_count": len(recovery_report.mismatches),
                        "mismatches": list(recovery_report.mismatches),
                        "synced_position_count": recovery_report.synced_position_count,
                        "synced_order_count": recovery_report.synced_order_count,
                        "cleared_position_count": recovery_report.cleared_position_count,
                        "cleared_order_count": recovery_report.cleared_order_count,
                        "timestamp": timestamp.isoformat(),
                    },
                    created_at=timestamp,
                )
            )
        except Exception:
            logger.exception(
                "Failed to append runtime_reconciliation_detected audit event; continuing"
            )
            try:
                self.runtime.connection.rollback()
            except Exception:
                pass
if _recovery_exception_occurred:
    self._append_audit(
        AuditEvent(
            event_type="recovery_exception",
            payload={"timestamp": timestamp.isoformat()},
            created_at=timestamp,
        )
    )
```

Note: `_append_audit` handles its own lock acquisition and commit internally — safe to call outside the `_rec_lock_ctx`.

---

## Task 5 — Order lifecycle quantity logging

**File**: `src/alpaca_bot/runtime/order_dispatch.py`

Add one INFO log after successful broker submission:
```python
# After: broker_order = _submit_order(order=order, ...)
# Before: normalized_status = str(broker_order.status).lower()
if order.intent_type == "entry":
    logger.info(
        "order_dispatch: entry submitted for %s — submitted_qty=%d broker_confirmed_qty=%s",
        order.symbol,
        order.quantity,
        broker_order.quantity,
    )
```

**File**: `src/alpaca_bot/runtime/trade_updates.py`

Add one INFO log in the entry fill branch:
```python
# After: qty = normalized.filled_qty if normalized.filled_qty is not None else matched_order.quantity
logger.info(
    "trade_updates: entry fill %s — order_qty=%d filled_qty=%s fill_price=%s",
    matched_order.symbol,
    matched_order.quantity,
    normalized.filled_qty,
    normalized.filled_avg_price,
)
```

---

## Task 6 — Unit tests

**File**: `tests/unit/test_production_bug_fixes.py`

### Test 1 — ACTIVE_ORDER_STATUSES contains held + pending_new

```python
def test_active_order_statuses_includes_held_and_pending_new():
    from alpaca_bot.runtime.startup_recovery import ACTIVE_ORDER_STATUSES
    assert "held" in ACTIVE_ORDER_STATUSES
    assert "pending_new" in ACTIVE_ORDER_STATUSES
```

### Test 2 — ACTIVE_STOP_STATUSES contains held

```python
def test_active_stop_statuses_includes_held():
    from alpaca_bot.runtime.cycle_intent_execution import ACTIVE_STOP_STATUSES
    assert "held" in ACTIVE_STOP_STATUSES
```

### Test 3 — Held stop order does not appear as reconciliation mismatch

Use the project's test infrastructure (fake stores, in-memory stores) to verify that a stop order in `status="held"` is correctly matched during reconciliation. Build:
- Local DB: one stop `OrderRecord(status="held", broker_order_id="broker-stop-1", ...)`
- Broker: one open order with `broker_order_id="broker-stop-1"`, `status="held"`
- No broker positions

Call `recover_startup_state(...)` and assert `report.mismatches == ()`.

### Test 4 — Recovery stop queued for open position with no active stop

Build:
- Local DB: filled entry `OrderRecord(status="filled", symbol="SOUN", initial_stop_price=3.50, ...)`
- Local DB: one `PositionRecord(symbol="SOUN", quantity=202, stop_price=3.50, ...)`
- Broker: open position for SOUN (quantity=202)
- No active stop order locally (simulates stale-stop expiry scenario)

Call `recover_startup_state(...)` and assert:
1. A new `pending_submit` stop order for SOUN exists in the order store
2. Its `stop_price == 3.50` and `quantity == 202`
3. An audit event `recovery_stop_queued_for_open_position` was appended

### Test 5 — Recovery stop NOT re-queued when active stop already exists

Same setup as Test 4, but add an existing `pending_submit` stop for SOUN.
Assert no duplicate stop is queued.

### Test 6 — Recovery exception does not crash `run_cycle_once`

Build a supervisor with a `_recovery_runner` that always raises `RuntimeError("test failure")`.
Call `run_cycle_once()` and assert:
1. No exception raised
2. Cycle result is returned
3. `recovery_exception` audit event was appended

---

## Execution Order

```
Task 1  → startup_recovery.py ACTIVE_ORDER_STATUSES (one line)
Task 2  → cycle_intent_execution.py ACTIVE_STOP_STATUSES (one line)
Task 3  → startup_recovery.py recovery stop logic
Task 4  → supervisor.py exception isolation + import
Task 5  → order_dispatch.py + trade_updates.py logging
Task 6  → tests/unit/test_production_bug_fixes.py
```

```bash
pytest tests/unit/test_production_bug_fixes.py -v
pytest
```

All existing tests must pass.

---

## Invariants (not changed)

- `order_dispatch.py` stale-stop expiry: **unchanged**. Expiring prior-day stops is correct. The fix is recovery reacting to the expiry, not preventing the expiry.
- `evaluate_cycle()` remains a pure function. No I/O added.
- Two-phase intent→dispatch design preserved. Recovery stops are queued as `pending_submit` to Postgres, dispatched by `order_dispatch.py` on the next pass.
- Bug 3: No engine change. Trailing ratchet logic is correct — it fires when positions cross the profit trigger. Once Bug 1 restores stops, the ratchet will operate normally on future profitable moves.
- Bug 4: No position sizing change. `calculate_position_size` is correct. Lifecycle logging only.
