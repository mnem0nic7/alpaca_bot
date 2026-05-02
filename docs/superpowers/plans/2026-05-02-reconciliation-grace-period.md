# Reconciliation Grace Period for Stop Orders — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop orders accumulate a per-order miss counter for 3 consecutive cycles before being cleared to `reconciled_missing`, preventing transient API gaps from stripping stop protection.

**Architecture:** Add `reconciliation_miss_count INTEGER NOT NULL DEFAULT 0` to the `orders` table via migration; add the matching field to `OrderRecord`; update `OrderStore` to read/write it; replace the unconditional `reconciled_missing` write in `startup_recovery.py` with a threshold-gated branch that emits two new audit events.

**Tech Stack:** Python 3.13, PostgreSQL, psycopg2, pytest

---

## File Map

| File | Change |
|---|---|
| `migrations/011_add_reconciliation_miss_count.sql` | **Create** — ADD COLUMN migration |
| `src/alpaca_bot/storage/models.py` | **Modify** — add `reconciliation_miss_count: int = 0` to `OrderRecord` |
| `src/alpaca_bot/storage/repositories.py` | **Modify** — add column to `_ORDER_SELECT_COLUMNS`, `_row_to_order_record`, `OrderStore.save()` |
| `src/alpaca_bot/runtime/startup_recovery.py` | **Modify** — add `RECONCILIATION_MISS_THRESHOLD = 3`; add grace-period guard to mismatch loop; replace unconditional write with grace-period logic |
| `tests/unit/test_startup_recovery.py` | **Modify** — add 5 new tests |

---

## Task 1: Create the Migration

**Files:**
- Create: `migrations/011_add_reconciliation_miss_count.sql`

- [ ] **Step 1: Create migration file**

```sql
-- migrations/011_add_reconciliation_miss_count.sql
ALTER TABLE orders ADD COLUMN IF NOT EXISTS reconciliation_miss_count INTEGER NOT NULL DEFAULT 0;
```

- [ ] **Step 2: Verify the file exists and looks correct**

```bash
cat migrations/011_add_reconciliation_miss_count.sql
```

Expected output:
```
ALTER TABLE orders ADD COLUMN IF NOT EXISTS reconciliation_miss_count INTEGER NOT NULL DEFAULT 0;
```

- [ ] **Step 3: Commit**

```bash
git add migrations/011_add_reconciliation_miss_count.sql
git commit -m "feat: migration 011 — add reconciliation_miss_count to orders"
```

---

## Task 2: Add `reconciliation_miss_count` to `OrderRecord` and `OrderStore`

**Files:**
- Modify: `src/alpaca_bot/storage/models.py:58`
- Modify: `src/alpaca_bot/storage/repositories.py:186-295`

These two files are one conceptual unit: the Python model and its DB mapping. Change both together.

### 2a — models.py

- [ ] **Step 1: Add field to `OrderRecord`**

In `src/alpaca_bot/storage/models.py`, find the `OrderRecord` dataclass. After `filled_quantity: int | None = None` (the last field), add one line:

```python
    filled_quantity: int | None = None
    reconciliation_miss_count: int = 0
```

The full updated tail of `OrderRecord` will look like:

```python
@dataclass(frozen=True)
class OrderRecord:
    client_order_id: str
    symbol: str
    side: str
    intent_type: str
    status: str
    quantity: int
    trading_mode: TradingMode
    strategy_version: str
    strategy_name: str = "breakout"
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    stop_price: float | None = None
    limit_price: float | None = None
    initial_stop_price: float | None = None
    broker_order_id: str | None = None
    signal_timestamp: datetime | None = None
    fill_price: float | None = None
    filled_quantity: int | None = None
    reconciliation_miss_count: int = 0
```

### 2b — repositories.py

There are three locations to change. All three are in `src/alpaca_bot/storage/repositories.py`.

- [ ] **Step 2: Add column to `_ORDER_SELECT_COLUMNS`**

Find `_ORDER_SELECT_COLUMNS` (around line 186). Add `reconciliation_miss_count` as the 19th entry, after `strategy_name`:

```python
_ORDER_SELECT_COLUMNS = """
    client_order_id,
    symbol,
    side,
    intent_type,
    status,
    quantity,
    trading_mode,
    strategy_version,
    created_at,
    updated_at,
    stop_price,
    limit_price,
    initial_stop_price,
    broker_order_id,
    signal_timestamp,
    fill_price,
    filled_quantity,
    strategy_name,
    reconciliation_miss_count
"""
```

- [ ] **Step 3: Add field mapping in `_row_to_order_record`**

Find `_row_to_order_record` (around line 208). Add `reconciliation_miss_count` as `row[18]`:

```python
def _row_to_order_record(row: Any) -> OrderRecord:
    return OrderRecord(
        client_order_id=row[0],
        symbol=row[1],
        side=row[2],
        intent_type=row[3],
        status=row[4],
        quantity=int(row[5]),
        trading_mode=TradingMode(row[6]),
        strategy_version=row[7],
        created_at=row[8],
        updated_at=row[9],
        stop_price=float(row[10]) if row[10] is not None else None,
        limit_price=float(row[11]) if row[11] is not None else None,
        initial_stop_price=float(row[12]) if row[12] is not None else None,
        broker_order_id=row[13],
        signal_timestamp=row[14],
        fill_price=float(row[15]) if row[15] is not None else None,
        filled_quantity=int(row[16]) if row[16] is not None else None,
        strategy_name=row[17] if row[17] is not None else "breakout",
        reconciliation_miss_count=int(row[18]) if row[18] is not None else 0,
    )
```

- [ ] **Step 4: Update `OrderStore.save()` — INSERT column list, VALUES, DO UPDATE SET, and params tuple**

Find `OrderStore.save()` (around line 235). Replace the entire method body with:

```python
    def save(self, order: OrderRecord, *, commit: bool = True) -> None:
        execute(
            self._connection,
            """
            INSERT INTO orders (
                client_order_id,
                symbol,
                side,
                intent_type,
                status,
                quantity,
                trading_mode,
                strategy_version,
                strategy_name,
                stop_price,
                limit_price,
                initial_stop_price,
                broker_order_id,
                signal_timestamp,
                fill_price,
                filled_quantity,
                created_at,
                updated_at,
                reconciliation_miss_count
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (client_order_id)
            DO UPDATE SET
                status = EXCLUDED.status,
                quantity = EXCLUDED.quantity,
                stop_price = COALESCE(EXCLUDED.stop_price, orders.stop_price),
                limit_price = COALESCE(EXCLUDED.limit_price, orders.limit_price),
                initial_stop_price = COALESCE(EXCLUDED.initial_stop_price, orders.initial_stop_price),
                broker_order_id = EXCLUDED.broker_order_id,
                signal_timestamp = EXCLUDED.signal_timestamp,
                fill_price = EXCLUDED.fill_price,
                filled_quantity = EXCLUDED.filled_quantity,
                updated_at = EXCLUDED.updated_at,
                reconciliation_miss_count = EXCLUDED.reconciliation_miss_count
            """,
            (
                order.client_order_id,
                order.symbol,
                order.side,
                order.intent_type,
                order.status,
                order.quantity,
                order.trading_mode.value,
                order.strategy_version,
                order.strategy_name,
                order.stop_price,
                order.limit_price,
                order.initial_stop_price,
                order.broker_order_id,
                order.signal_timestamp,
                order.fill_price,
                order.filled_quantity,
                order.created_at,
                order.updated_at,
                order.reconciliation_miss_count,
            ),
            commit=commit,
        )
```

- [ ] **Step 5: Run the existing test suite to verify nothing is broken**

```bash
pytest tests/unit/ -x -q
```

Expected: all existing tests pass. No new tests yet.

- [ ] **Step 6: Commit**

```bash
git add src/alpaca_bot/storage/models.py src/alpaca_bot/storage/repositories.py
git commit -m "feat: add reconciliation_miss_count field to OrderRecord and OrderStore"
```

---

## Task 3: Write the 5 Failing Tests

**Files:**
- Modify: `tests/unit/test_startup_recovery.py`

Add these 5 tests to the bottom of `tests/unit/test_startup_recovery.py`. The file already imports `TradingMode`, `OrderRecord`, `RecordingOrderStore`, `RecordingPositionStore`, `RecordingAuditEventStore`, `make_runtime_context`, `make_settings`, `recover_startup_state`, `BrokerOrder`, and `datetime`/`timezone` — no new imports needed.

- [ ] **Step 1: Append all 5 tests to `tests/unit/test_startup_recovery.py`**

```python
# ── Grace-period tests (Fix 3) ─────────────────────────────────────────────


def test_reconciliation_grace_period_first_miss_increments_count() -> None:
    """First consecutive miss: count 0→1, status unchanged, audit event emitted, no mismatch."""
    settings = make_settings()
    now = datetime(2026, 5, 2, 14, 0, tzinfo=timezone.utc)
    stop = OrderRecord(
        client_order_id="orb:v1-breakout:2026-05-02:MRVL:stop:original",
        symbol="MRVL",
        side="sell",
        intent_type="stop",
        status="new",
        quantity=50,
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        broker_order_id="broker-stop-1",
        stop_price=75.0,
        initial_stop_price=75.0,
        reconciliation_miss_count=0,
    )
    order_store = RecordingOrderStore(existing_orders=[stop])
    audit_store = RecordingAuditEventStore()
    runtime = make_runtime_context(
        settings,
        position_store=RecordingPositionStore(),
        order_store=order_store,
        audit_event_store=audit_store,
    )

    report = recover_startup_state(
        settings=settings,
        runtime=runtime,
        broker_open_positions=[],
        broker_open_orders=[],
        now=now,
    )

    saved = next((o for o in order_store.saved if o.client_order_id == stop.client_order_id), None)
    assert saved is not None, "Expected a saved record for the stop"
    assert saved.status == "new", f"Expected status='new', got {saved.status!r}"
    assert saved.reconciliation_miss_count == 1, (
        f"Expected reconciliation_miss_count=1, got {saved.reconciliation_miss_count}"
    )
    miss_events = [e for e in audit_store.appended if e.event_type == "reconciliation_miss_count_incremented"]
    assert len(miss_events) == 1, f"Expected 1 miss event, got {len(miss_events)}"
    assert miss_events[0].payload["reconciliation_miss_count"] == 1
    assert miss_events[0].payload["threshold"] == 3
    assert not any(e.event_type == "reconciled_missing_stop_cleared" for e in audit_store.appended)
    assert not any(o.status == "reconciled_missing" for o in order_store.saved), (
        "Stop must NOT be cleared to reconciled_missing on first miss"
    )
    assert not any(stop.client_order_id in m for m in report.mismatches), (
        "Grace-period stop must not appear in report.mismatches on first miss"
    )


def test_reconciliation_grace_period_second_miss_increments_count() -> None:
    """Second consecutive miss: count 1→2, status unchanged, audit event emitted, no mismatch."""
    settings = make_settings()
    now = datetime(2026, 5, 2, 14, 0, tzinfo=timezone.utc)
    stop = OrderRecord(
        client_order_id="orb:v1-breakout:2026-05-02:MRVL:stop:original",
        symbol="MRVL",
        side="sell",
        intent_type="stop",
        status="new",
        quantity=50,
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        broker_order_id="broker-stop-1",
        stop_price=75.0,
        initial_stop_price=75.0,
        reconciliation_miss_count=1,
    )
    order_store = RecordingOrderStore(existing_orders=[stop])
    audit_store = RecordingAuditEventStore()
    runtime = make_runtime_context(
        settings,
        position_store=RecordingPositionStore(),
        order_store=order_store,
        audit_event_store=audit_store,
    )

    report = recover_startup_state(
        settings=settings,
        runtime=runtime,
        broker_open_positions=[],
        broker_open_orders=[],
        now=now,
    )

    saved = next((o for o in order_store.saved if o.client_order_id == stop.client_order_id), None)
    assert saved is not None
    assert saved.status == "new"
    assert saved.reconciliation_miss_count == 2, (
        f"Expected reconciliation_miss_count=2, got {saved.reconciliation_miss_count}"
    )
    miss_events = [e for e in audit_store.appended if e.event_type == "reconciliation_miss_count_incremented"]
    assert len(miss_events) == 1
    assert miss_events[0].payload["reconciliation_miss_count"] == 2
    assert not any(e.event_type == "reconciled_missing_stop_cleared" for e in audit_store.appended)
    assert not any(o.status == "reconciled_missing" for o in order_store.saved)
    assert not any(stop.client_order_id in m for m in report.mismatches), (
        "Grace-period stop must not appear in report.mismatches on second miss"
    )


def test_reconciliation_grace_period_third_miss_clears_to_reconciled_missing() -> None:
    """Third consecutive miss: count=2 → threshold reached → status='reconciled_missing'."""
    settings = make_settings()
    now = datetime(2026, 5, 2, 14, 0, tzinfo=timezone.utc)
    stop = OrderRecord(
        client_order_id="orb:v1-breakout:2026-05-02:MRVL:stop:original",
        symbol="MRVL",
        side="sell",
        intent_type="stop",
        status="new",
        quantity=50,
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        broker_order_id="broker-stop-1",
        stop_price=75.0,
        initial_stop_price=75.0,
        reconciliation_miss_count=2,
    )
    order_store = RecordingOrderStore(existing_orders=[stop])
    audit_store = RecordingAuditEventStore()
    runtime = make_runtime_context(
        settings,
        position_store=RecordingPositionStore(),
        order_store=order_store,
        audit_event_store=audit_store,
    )

    recover_startup_state(
        settings=settings,
        runtime=runtime,
        broker_open_positions=[],
        broker_open_orders=[],
        now=now,
    )

    saved = next((o for o in order_store.saved if o.client_order_id == stop.client_order_id), None)
    assert saved is not None
    assert saved.status == "reconciled_missing", (
        f"Expected status='reconciled_missing' on 3rd miss, got {saved.status!r}"
    )
    cleared_events = [e for e in audit_store.appended if e.event_type == "reconciled_missing_stop_cleared"]
    assert len(cleared_events) == 1, f"Expected 1 cleared event, got {len(cleared_events)}"
    assert cleared_events[0].payload["client_order_id"] == stop.client_order_id
    assert not any(e.event_type == "reconciliation_miss_count_incremented" for e in audit_store.appended)


def test_reconciliation_grace_period_does_not_apply_to_entry_orders() -> None:
    """Entry orders must be cleared to reconciled_missing immediately on the first miss."""
    settings = make_settings()
    now = datetime(2026, 5, 2, 14, 0, tzinfo=timezone.utc)
    entry = OrderRecord(
        client_order_id="orb:v1-breakout:2026-05-02:MRVL:entry:2026-05-02T14:00:00+00:00",
        symbol="MRVL",
        side="buy",
        intent_type="entry",
        status="new",
        quantity=50,
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        broker_order_id="broker-entry-1",
        reconciliation_miss_count=0,
    )
    order_store = RecordingOrderStore(existing_orders=[entry])
    audit_store = RecordingAuditEventStore()
    runtime = make_runtime_context(
        settings,
        position_store=RecordingPositionStore(),
        order_store=order_store,
        audit_event_store=audit_store,
    )

    recover_startup_state(
        settings=settings,
        runtime=runtime,
        broker_open_positions=[],
        broker_open_orders=[],
        now=now,
    )

    saved = next((o for o in order_store.saved if o.client_order_id == entry.client_order_id), None)
    assert saved is not None
    assert saved.status == "reconciled_missing", (
        f"Entry orders must be cleared immediately; got {saved.status!r}"
    )
    assert not any(e.event_type == "reconciliation_miss_count_incremented" for e in audit_store.appended)
    assert not any(e.event_type == "reconciled_missing_stop_cleared" for e in audit_store.appended)


def test_reconciliation_grace_period_resets_count_when_stop_found_at_broker() -> None:
    """When broker confirms the stop exists, reconciliation_miss_count resets to 0."""
    settings = make_settings()
    now = datetime(2026, 5, 2, 14, 0, tzinfo=timezone.utc)
    stop = OrderRecord(
        client_order_id="orb:v1-breakout:2026-05-02:MRVL:stop:original",
        symbol="MRVL",
        side="sell",
        intent_type="stop",
        status="new",
        quantity=50,
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        broker_order_id="broker-stop-1",
        stop_price=75.0,
        initial_stop_price=75.0,
        reconciliation_miss_count=2,
    )
    order_store = RecordingOrderStore(existing_orders=[stop])
    audit_store = RecordingAuditEventStore()
    runtime = make_runtime_context(
        settings,
        position_store=RecordingPositionStore(),
        order_store=order_store,
        audit_event_store=audit_store,
    )
    broker_stop = BrokerOrder(
        client_order_id=stop.client_order_id,
        broker_order_id="broker-stop-1",
        symbol="MRVL",
        side="sell",
        status="new",
        quantity=50,
    )

    recover_startup_state(
        settings=settings,
        runtime=runtime,
        broker_open_positions=[],
        broker_open_orders=[broker_stop],
        now=now,
    )

    # broker_open_orders write loop saves OrderRecord with reconciliation_miss_count=0 (default)
    saved = next((o for o in order_store.saved if o.client_order_id == stop.client_order_id), None)
    assert saved is not None
    assert saved.reconciliation_miss_count == 0, (
        f"Expected reset to 0 when broker confirms stop; got {saved.reconciliation_miss_count}"
    )
    assert not any(e.event_type == "reconciliation_miss_count_incremented" for e in audit_store.appended)
    assert not any(e.event_type == "reconciled_missing_stop_cleared" for e in audit_store.appended)
```

- [ ] **Step 2: Run the new tests to verify they fail (before implementation)**

```bash
pytest tests/unit/test_startup_recovery.py -k "grace_period" -v
```

Expected: tests 1, 2, 3 FAIL; tests 4 and 5 may PASS (they test paths the current code already handles correctly). At minimum, the first three must fail.

```
FAILED test_reconciliation_grace_period_first_miss_increments_count
FAILED test_reconciliation_grace_period_second_miss_increments_count
FAILED test_reconciliation_grace_period_third_miss_clears_to_reconciled_missing
```

If all 5 pass at this point, stop — `startup_recovery.py` already has the grace-period logic from a prior edit. Investigate before continuing.

---

## Task 4: Implement the Grace-Period Logic in `startup_recovery.py`

**Files:**
- Modify: `src/alpaca_bot/runtime/startup_recovery.py`

### 4a — Add the threshold constant

- [ ] **Step 1: Add `RECONCILIATION_MISS_THRESHOLD` after `ACTIVE_ORDER_STATUSES`**

Find `ACTIVE_ORDER_STATUSES` (line 16). After its closing `]`, add one blank line and then:

```python
ACTIVE_ORDER_STATUSES = [
    "pending_submit", "submitting", "new", "accepted", "submitted",
    "partially_filled", "held", "pending_new",
]

RECONCILIATION_MISS_THRESHOLD = 3
```

### 4b — Guard the mismatch reporting loop

The mismatch reporting loop runs **before** the write loop, at lines 222-231. It feeds `report.mismatches` which triggers the notifier. Without a guard here, grace-period cycles 1 and 2 would fire "Startup mismatch detected" alerts for a stop that isn't being cleared.

- [ ] **Step 2: Add grace-period guard to the mismatch reporting loop**

Find the mismatch reporting loop:

```python
    cleared_order_count = 0
    for order in local_active_orders:
        if order.client_order_id not in matched_local_client_ids:
            if _is_never_submitted(order):
                continue
            mismatches.append(f"local order missing at broker: {order.client_order_id}")
            cleared_order_count += 1
```

Replace it with:

```python
    cleared_order_count = 0
    for order in local_active_orders:
        if order.client_order_id not in matched_local_client_ids:
            if _is_never_submitted(order):
                continue
            is_stop = order.intent_type == "stop" and order.side == "sell"
            if is_stop and (order.reconciliation_miss_count + 1) < RECONCILIATION_MISS_THRESHOLD:
                continue
            mismatches.append(f"local order missing at broker: {order.client_order_id}")
            cleared_order_count += 1
```

### 4c — Replace the unconditional `reconciled_missing` write

The target block is inside the `for order in local_active_orders:` loop, at the very end of that loop body — after the `if _is_never_submitted(order): ... continue` guard. It is the unconditional `runtime.order_store.save(OrderRecord(..., status="reconciled_missing", ...), commit=False)` block.

- [ ] **Step 3: Replace the unconditional `reconciled_missing` save with grace-period logic**

Find this block (the one immediately after the `continue` that follows the `_is_never_submitted` guard):

```python
            runtime.order_store.save(
                OrderRecord(
                    client_order_id=order.client_order_id,
                    symbol=order.symbol,
                    side=order.side,
                    intent_type=order.intent_type,
                    status="reconciled_missing",
                    quantity=order.quantity,
                    trading_mode=order.trading_mode,
                    strategy_version=order.strategy_version,
                    strategy_name=order.strategy_name,
                    created_at=order.created_at,
                    updated_at=timestamp,
                    stop_price=order.stop_price,
                    limit_price=order.limit_price,
                    initial_stop_price=order.initial_stop_price,
                    broker_order_id=order.broker_order_id,
                    signal_timestamp=order.signal_timestamp,
                ),
                commit=False,
            )
```

Replace it with:

```python
            is_stop_order = order.intent_type == "stop" and order.side == "sell"
            new_miss_count = order.reconciliation_miss_count + 1
            if is_stop_order and new_miss_count < RECONCILIATION_MISS_THRESHOLD:
                runtime.order_store.save(
                    OrderRecord(
                        client_order_id=order.client_order_id,
                        symbol=order.symbol,
                        side=order.side,
                        intent_type=order.intent_type,
                        status=order.status,
                        quantity=order.quantity,
                        trading_mode=order.trading_mode,
                        strategy_version=order.strategy_version,
                        strategy_name=order.strategy_name,
                        created_at=order.created_at,
                        updated_at=timestamp,
                        stop_price=order.stop_price,
                        limit_price=order.limit_price,
                        initial_stop_price=order.initial_stop_price,
                        broker_order_id=order.broker_order_id,
                        signal_timestamp=order.signal_timestamp,
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
                runtime.order_store.save(
                    OrderRecord(
                        client_order_id=order.client_order_id,
                        symbol=order.symbol,
                        side=order.side,
                        intent_type=order.intent_type,
                        status="reconciled_missing",
                        quantity=order.quantity,
                        trading_mode=order.trading_mode,
                        strategy_version=order.strategy_version,
                        strategy_name=order.strategy_name,
                        created_at=order.created_at,
                        updated_at=timestamp,
                        stop_price=order.stop_price,
                        limit_price=order.limit_price,
                        initial_stop_price=order.initial_stop_price,
                        broker_order_id=order.broker_order_id,
                        signal_timestamp=order.signal_timestamp,
                        reconciliation_miss_count=new_miss_count,
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

- [ ] **Step 4: Run the grace-period tests to verify they pass**

```bash
pytest tests/unit/test_startup_recovery.py -k "grace_period" -v
```

Expected: all 5 PASS.

- [ ] **Step 5: Run the full test suite to confirm no regressions**

```bash
pytest tests/unit/ -x -q
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/alpaca_bot/runtime/startup_recovery.py tests/unit/test_startup_recovery.py
git commit -m "feat: grace period for stop-order reconciliation (RECONCILIATION_MISS_THRESHOLD=3)"
```

---

## Task 5: Final Verification

- [ ] **Step 1: Run the complete test suite**

```bash
pytest -x -q
```

Expected: all tests pass with no failures or errors.

- [ ] **Step 2: Verify `RECONCILIATION_MISS_THRESHOLD` is exported from the module (so tests can import it)**

```bash
python -c "from alpaca_bot.runtime.startup_recovery import RECONCILIATION_MISS_THRESHOLD; print(RECONCILIATION_MISS_THRESHOLD)"
```

Expected output: `3`

- [ ] **Step 3: Confirm the new audit event names are correct**

```bash
grep -n "reconciliation_miss_count_incremented\|reconciled_missing_stop_cleared" \
    src/alpaca_bot/runtime/startup_recovery.py \
    tests/unit/test_startup_recovery.py
```

Expected: event strings appear in both source and test files, with matching spelling.

---

## Self-Review Checklist

- [x] **Spec coverage:**
  - Migration 011: Task 1 ✓
  - `OrderRecord.reconciliation_miss_count`: Task 2a ✓
  - `OrderStore` column (SELECT, INSERT, DO UPDATE SET): Task 2b steps 2–4 ✓
  - `RECONCILIATION_MISS_THRESHOLD = 3`: Task 4a ✓
  - Mismatch reporting loop suppression (no alert during grace period): Task 4b ✓
  - Grace-period logic (below threshold → increment; at threshold → reconciled_missing): Task 4c ✓
  - `reconciliation_miss_count_incremented` audit event: Task 4c ✓
  - `reconciled_missing_stop_cleared` audit event: Task 4c ✓
  - Reset on broker confirmation (via default=0 in broker_open_orders write loop): covered by existing code + DO UPDATE SET change ✓
  - Test 1 (first miss): ✓ | Test 2 (second miss): ✓ | Test 3 (third miss): ✓ | Test 4 (entry immediate): ✓ | Test 5 (reset): ✓

- [x] **No placeholders** — every step contains exact code

- [x] **Type consistency** — `reconciliation_miss_count: int = 0` in models.py, `int(row[18])` in repositories.py, `new_miss_count` is `int` throughout, payload values are `int` ✓
