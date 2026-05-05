# Partial-Fill Wash Trade Fix — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Cancel a partially-filled entry order's open buy-limit at the broker before dispatching a stop or market exit, eliminating Alpaca error 40310000 ("potential wash trade detected").

**Architecture:** Two fix sites share the same pattern: query for `partially_filled` entry orders matching the symbol, cancel their broker orders, record the cancellation in DB, and emit an audit event. The dispatch path skips the stop on cancel failure; the exit path proceeds anyway (existing recovery handles failure). Logic is inlined in each file — no shared module.

**Tech Stack:** Python, psycopg2, Alpaca Trading API, pytest

---

### Task 1: Update `BrokerProtocol` in `order_dispatch.py` and add the partial-fill cancel helper

**Files:**
- Modify: `src/alpaca_bot/runtime/order_dispatch.py:43-49`
- Modify (add helper): `src/alpaca_bot/runtime/order_dispatch.py` (after `_submit_order`, before `_resolve_now`)

- [ ] **Step 1: Add `cancel_order` to `BrokerProtocol`**

In `src/alpaca_bot/runtime/order_dispatch.py`, replace lines 43–49:

```python
class BrokerProtocol(Protocol):
    def submit_stop_limit_entry(self, **kwargs) -> BrokerOrder: ...

    def submit_limit_entry(self, **kwargs) -> BrokerOrder: ...

    def submit_stop_order(self, **kwargs) -> BrokerOrder: ...

    def cancel_order(self, order_id: str) -> None: ...
```

- [ ] **Step 2: Add the `_cancel_partial_fill_entry` helper function**

Add this function to `src/alpaca_bot/runtime/order_dispatch.py` after the `_submit_order` function (around line 425):

```python
def _cancel_partial_fill_entry(
    *,
    order: "OrderRecord",
    runtime: RuntimeProtocol,
    broker: BrokerProtocol,
    settings: "Settings",
    now: datetime,
    lock_ctx: Any,
) -> bool:
    """Cancel the open buy-limit for a partially-filled entry of the same symbol.

    Returns True if at least one entry was canceled (or was already gone at broker).
    Returns False if a cancel raised an unrecognized error — caller should skip the
    sell-side submission to avoid a wash trade rejection.
    """
    if lock_ctx is None:
        lock_ctx = contextlib.nullcontext()

    with lock_ctx:
        all_partial = runtime.order_store.list_by_status(
            trading_mode=settings.trading_mode,
            strategy_version=settings.strategy_version,
            statuses=["partially_filled"],
        )
    partial_entries = [
        o for o in all_partial
        if o.intent_type == "entry" and o.symbol == order.symbol
    ]
    if not partial_entries:
        return True  # nothing to cancel, safe to proceed

    canceled_any = False
    for entry in partial_entries:
        if not entry.broker_order_id:
            continue
        try:
            broker.cancel_order(entry.broker_order_id)
        except Exception as exc:
            exc_msg = str(exc).lower()
            if any(phrase in exc_msg for phrase in ("already canceled", "not found", "does not exist")):
                logger.warning(
                    "order_dispatch: partial-fill entry for %s already gone at broker (%s) — proceeding",
                    order.symbol, exc,
                )
            else:
                logger.exception(
                    "order_dispatch: failed to cancel partial-fill entry %s for %s before stop dispatch",
                    entry.broker_order_id, order.symbol,
                )
                with lock_ctx:
                    try:
                        runtime.audit_event_store.append(
                            AuditEvent(
                                event_type="partial_fill_cancel_failed",
                                symbol=order.symbol,
                                payload={
                                    "entry_client_order_id": entry.client_order_id,
                                    "entry_broker_order_id": entry.broker_order_id,
                                    "error": str(exc),
                                    "context": "stop_dispatch",
                                },
                                created_at=now,
                            ),
                            commit=True,
                        )
                    except Exception:
                        pass
                return False  # tell caller to skip the stop
        canceled_record = OrderRecord(
            client_order_id=entry.client_order_id,
            symbol=entry.symbol,
            side=entry.side,
            intent_type=entry.intent_type,
            status="canceled",
            quantity=entry.quantity,
            trading_mode=entry.trading_mode,
            strategy_version=entry.strategy_version,
            strategy_name=entry.strategy_name,
            created_at=entry.created_at,
            updated_at=now,
            stop_price=entry.stop_price,
            limit_price=entry.limit_price,
            initial_stop_price=entry.initial_stop_price,
            broker_order_id=entry.broker_order_id,
            signal_timestamp=entry.signal_timestamp,
        )
        with lock_ctx:
            try:
                runtime.order_store.save(canceled_record, commit=False)
                runtime.audit_event_store.append(
                    AuditEvent(
                        event_type="partial_fill_entry_canceled",
                        symbol=order.symbol,
                        payload={
                            "entry_client_order_id": entry.client_order_id,
                            "entry_broker_order_id": entry.broker_order_id,
                            "context": "stop_dispatch",
                        },
                        created_at=now,
                    ),
                    commit=False,
                )
                runtime.connection.commit()
            except Exception:
                try:
                    runtime.connection.rollback()
                except Exception:
                    pass
                raise
        canceled_any = True

    return canceled_any or not partial_entries
```

Note: Add `from typing import Any` to the top of `order_dispatch.py` if not already present (check line 7).

- [ ] **Step 3: Call the helper BEFORE the "submitting" DB write for stop orders**

In `dispatch_pending_orders()`, locate the `with lock_ctx:` block that marks the order as "submitting" (currently line 217). The check must go BEFORE this block — a `continue` after the "submitting" write would leave the stop permanently stuck in "submitting" status, since `dispatch_pending_orders` only picks up `pending_submit` orders.

Replace:

```python
        with lock_ctx:
            try:
                runtime.order_store.save(
                    OrderRecord(
                        client_order_id=order.client_order_id,
                        symbol=order.symbol,
                        side=order.side,
                        intent_type=order.intent_type,
                        status="submitting",
```

with:

```python
        if order.intent_type == "stop":
            if not _cancel_partial_fill_entry(
                order=order,
                runtime=runtime,
                broker=broker,
                settings=settings,
                now=timestamp,
                lock_ctx=lock_ctx,
            ):
                # Cancel failed — leave stop as pending_submit for next cycle retry.
                continue
        with lock_ctx:
            try:
                runtime.order_store.save(
                    OrderRecord(
                        client_order_id=order.client_order_id,
                        symbol=order.symbol,
                        side=order.side,
                        intent_type=order.intent_type,
                        status="submitting",
```

- [ ] **Step 4: Add `Any` to the typing import in `order_dispatch.py`**

Line 7 currently reads `from typing import Callable, Protocol`. The helper uses `Any`. Update to:

```python
from typing import Any, Callable, Protocol
```

---

### Task 2: Write failing tests for the dispatch partial-fill cancel

**Files:**
- Modify: `tests/unit/test_order_dispatch.py`
- Test: `tests/unit/test_order_dispatch.py`

- [ ] **Step 1: Add `list_by_status()` and `cancel_order()` to existing fakes**

In `test_order_dispatch.py`, update `RecordingOrderStore` to support `list_by_status()`:

```python
class RecordingOrderStore:
    def __init__(
        self,
        pending_orders: list[OrderRecord],
        extra_orders: list[OrderRecord] | None = None,
    ) -> None:
        self.pending_orders = list(pending_orders)
        self._all_orders = list(pending_orders) + list(extra_orders or [])
        self.find_pending_submit_calls: list[tuple[TradingMode, str]] = []
        self.saved: list[OrderRecord] = []

    def list_pending_submit(
        self, *, trading_mode: TradingMode, strategy_version: str
    ) -> list[OrderRecord]:
        self.find_pending_submit_calls.append((trading_mode, strategy_version))
        return list(self.pending_orders)

    def list_by_status(
        self,
        *,
        trading_mode: TradingMode,
        strategy_version: str,
        statuses: list[str],
        **kwargs: object,
    ) -> list[OrderRecord]:
        return [o for o in self._all_orders if o.status in statuses]

    def save(self, order: OrderRecord, *, commit: bool = True) -> None:
        self.saved.append(order)
```

Update `RecordingBroker` to add `cancel_calls` and `cancel_order`:

```python
class RecordingBroker:
    def __init__(self, *, cancel_raises: Exception | None = None) -> None:
        self.entry_calls: list[dict[str, object]] = []
        self.stop_calls: list[dict[str, object]] = []
        self.cancel_calls: list[str] = []
        self._cancel_raises = cancel_raises

    def submit_stop_limit_entry(self, **kwargs: object) -> SimpleNamespace:
        self.entry_calls.append(dict(kwargs))
        return SimpleNamespace(
            client_order_id=kwargs["client_order_id"],
            broker_order_id="broker-entry-1",
            symbol=kwargs["symbol"],
            side="buy",
            status="ACCEPTED",
            quantity=kwargs["quantity"],
        )

    def submit_stop_order(self, **kwargs: object) -> SimpleNamespace:
        self.stop_calls.append(dict(kwargs))
        return SimpleNamespace(
            client_order_id=kwargs["client_order_id"],
            broker_order_id="broker-stop-1",
            symbol=kwargs["symbol"],
            side="sell",
            status="NEW",
            quantity=kwargs["quantity"],
        )

    def cancel_order(self, order_id: str) -> None:
        self.cancel_calls.append(order_id)
        if self._cancel_raises is not None:
            raise self._cancel_raises
```

- [ ] **Step 2: Write the two new failing tests**

Add after the existing test in `test_order_dispatch.py`:

```python
def test_dispatch_stop_cancels_partial_fill_entry_first() -> None:
    """When a stop is pending_submit and the entry is partially_filled, dispatch
    cancels the entry's broker order and saves it as canceled before submitting the stop."""
    _, dispatch_pending_orders = load_order_dispatch_api()
    settings = make_settings()
    now = datetime(2026, 5, 4, 15, 30, tzinfo=timezone.utc)

    partial_entry = OrderRecord(
        client_order_id="paper:v1-breakout:SONO:entry:1",
        symbol="SONO",
        side="buy",
        intent_type="entry",
        status="partially_filled",
        quantity=187,
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        created_at=now,
        updated_at=now,
        stop_price=14.88,
        limit_price=14.90,
        broker_order_id="broker-entry-sono-1",
        signal_timestamp=now,
    )
    stop_order = OrderRecord(
        client_order_id="paper:v1-breakout:SONO:stop:1",
        symbol="SONO",
        side="sell",
        intent_type="stop",
        status="pending_submit",
        quantity=187,
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        created_at=now,
        updated_at=now,
        stop_price=14.00,
        signal_timestamp=now,
    )
    order_store = RecordingOrderStore([stop_order], extra_orders=[partial_entry])
    audit_store = RecordingAuditEventStore()
    runtime = SimpleNamespace(
        order_store=order_store,
        audit_event_store=audit_store,
        connection=FakeConnection(),
    )
    broker = RecordingBroker()

    report = dispatch_pending_orders(settings=settings, runtime=runtime, broker=broker, now=now)

    # Entry's broker order was canceled before stop was submitted.
    assert broker.cancel_calls == ["broker-entry-sono-1"]
    # Stop was submitted to broker.
    assert len(broker.stop_calls) == 1
    assert broker.stop_calls[0]["symbol"] == "SONO"
    assert report.submitted_count == 1

    # DB saves: entry→canceled, stop→submitting, stop→new
    saved_statuses = [(o.client_order_id, o.status) for o in order_store.saved]
    assert ("paper:v1-breakout:SONO:entry:1", "canceled") in saved_statuses
    assert ("paper:v1-breakout:SONO:stop:1", "submitting") in saved_statuses
    assert ("paper:v1-breakout:SONO:stop:1", "new") in saved_statuses

    # Audit trail
    event_types = [e.event_type for e in audit_store.appended]
    assert "partial_fill_entry_canceled" in event_types
    assert "order_dispatch_submitting" in event_types
    assert "order_submitted" in event_types


def test_dispatch_stop_skips_when_partial_fill_cancel_fails() -> None:
    """When cancel_order raises an unrecognized error, the stop is skipped (not submitted)."""
    _, dispatch_pending_orders = load_order_dispatch_api()
    settings = make_settings()
    now = datetime(2026, 5, 4, 15, 30, tzinfo=timezone.utc)

    partial_entry = OrderRecord(
        client_order_id="paper:v1-breakout:SONO:entry:1",
        symbol="SONO",
        side="buy",
        intent_type="entry",
        status="partially_filled",
        quantity=187,
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        created_at=now,
        updated_at=now,
        stop_price=14.88,
        limit_price=14.90,
        broker_order_id="broker-entry-sono-1",
        signal_timestamp=now,
    )
    stop_order = OrderRecord(
        client_order_id="paper:v1-breakout:SONO:stop:1",
        symbol="SONO",
        side="sell",
        intent_type="stop",
        status="pending_submit",
        quantity=187,
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        created_at=now,
        updated_at=now,
        stop_price=14.00,
        signal_timestamp=now,
    )
    order_store = RecordingOrderStore([stop_order], extra_orders=[partial_entry])
    audit_store = RecordingAuditEventStore()
    runtime = SimpleNamespace(
        order_store=order_store,
        audit_event_store=audit_store,
        connection=FakeConnection(),
    )
    broker = RecordingBroker(cancel_raises=RuntimeError("broker timeout"))

    report = dispatch_pending_orders(settings=settings, runtime=runtime, broker=broker, now=now)

    # Cancel was attempted.
    assert broker.cancel_calls == ["broker-entry-sono-1"]
    # Stop was NOT submitted — broker.submit_stop_order never called.
    assert broker.stop_calls == []
    assert report.submitted_count == 0

    # Stop was NOT saved as "submitting" — it remains pending_submit so next cycle retries.
    saved_statuses = [o.status for o in order_store.saved]
    assert "submitting" not in saved_statuses

    # Audit event for failed cancel was recorded.
    event_types = [e.event_type for e in audit_store.appended]
    assert "partial_fill_cancel_failed" in event_types
```

- [ ] **Step 3: Run tests to verify they fail (implementation not written yet)**

```bash
pytest tests/unit/test_order_dispatch.py::test_dispatch_stop_cancels_partial_fill_entry_first tests/unit/test_order_dispatch.py::test_dispatch_stop_skips_when_partial_fill_cancel_fails -v
```

Expected: FAIL (AttributeError or AssertionError — helper not implemented yet)

---

### Task 3: Implement the dispatch fix and verify tests pass

**Files:**
- Modify: `src/alpaca_bot/runtime/order_dispatch.py` (Task 1 code)

- [ ] **Step 1: Apply all changes from Task 1** (Steps 1–4 above are all to `order_dispatch.py`)

- [ ] **Step 2: Run the new dispatch tests**

```bash
pytest tests/unit/test_order_dispatch.py -v
```

Expected: ALL PASS (both new tests + existing tests)

- [ ] **Step 3: Commit**

```bash
git add src/alpaca_bot/runtime/order_dispatch.py tests/unit/test_order_dispatch.py
git commit -m "fix: cancel partial-fill entry before stop dispatch to prevent wash trade (40310000)"
```

---

### Task 4: Add partial-fill cancel to `_execute_exit()` in `cycle_intent_execution.py`

**Files:**
- Modify: `src/alpaca_bot/runtime/cycle_intent_execution.py`
- Test: `tests/unit/test_cycle_intent_execution.py`

- [ ] **Step 1: Write the failing test first**

Add this test to `tests/unit/test_cycle_intent_execution.py` (after the existing market-exit tests):

```python
def test_execute_exit_cancels_partial_fill_entry_before_market_exit() -> None:
    """_execute_exit cancels an open partial-fill entry before submitting market exit."""
    execute_cycle_intents = load_cycle_intent_execution_api()
    settings = make_settings()
    now = datetime(2026, 5, 4, 15, 30, tzinfo=timezone.utc)

    partial_entry = OrderRecord(
        client_order_id="paper:v1-breakout:SONO:entry:1",
        symbol="SONO",
        side="buy",
        intent_type="entry",
        status="partially_filled",
        quantity=187,
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        created_at=now,
        updated_at=now,
        stop_price=14.88,
        limit_price=14.90,
        broker_order_id="broker-entry-sono-1",
        signal_timestamp=now,
    )
    position = PositionRecord(
        symbol="SONO",
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        quantity=187,
        entry_price=14.88,
        stop_price=14.00,
        initial_stop_price=14.00,
        opened_at=now,
    )
    order_store = RecordingOrderStore(orders=[partial_entry])
    position_store = RecordingPositionStore(positions=[position])
    audit_store = RecordingAuditEventStore()
    conn = FakeConnection()
    runtime = SimpleNamespace(
        order_store=order_store,
        position_store=position_store,
        audit_event_store=audit_store,
        connection=conn,
    )
    broker = RecordingBroker()

    from alpaca_bot.core.engine import CycleIntentType
    from alpaca_bot.domain.models import CycleIntent

    intents = [CycleIntent(intent_type=CycleIntentType.EXIT, symbol="SONO", strategy_name="breakout")]
    execute_cycle_intents(settings=settings, runtime=runtime, broker=broker, intents=intents, now=now)

    # Partial-fill entry was canceled at broker before market exit was submitted.
    assert "broker-entry-sono-1" in broker.cancel_calls
    assert len(broker.exit_calls) == 1
    assert broker.exit_calls[0]["symbol"] == "SONO"

    # Audit events: partial_fill_entry_canceled and cycle_intent_executed
    event_types = [e.event_type for e in audit_store.appended]
    assert "partial_fill_entry_canceled" in event_types
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/unit/test_cycle_intent_execution.py::test_execute_exit_cancels_partial_fill_entry_before_market_exit -v
```

Expected: FAIL (partial_fill_entry_canceled not in event_types)

- [ ] **Step 3: Add `_cancel_partial_fill_entry` helper to `cycle_intent_execution.py`**

Add this helper after `_active_stop_orders()` (look for it near the bottom of the file):

```python
def _cancel_partial_fill_entry(
    *,
    symbol: str,
    strategy_name: str,
    runtime: RuntimeProtocol,
    broker: BrokerProtocol,
    settings: Settings,
    now: datetime,
    lock_ctx: Any,
) -> None:
    """Cancel any open buy-limit from a partially-filled entry for this symbol.

    Called before market/limit exit submission to prevent Alpaca wash trade rejection
    (error 40310000).  Logs and continues on failure — the exit submission may still
    succeed if the partial-fill order was already cleared by the broker.
    """
    with lock_ctx:
        all_partial = runtime.order_store.list_by_status(
            trading_mode=settings.trading_mode,
            strategy_version=settings.strategy_version,
            statuses=["partially_filled"],
            strategy_name=strategy_name,
        )
    partial_entries = [
        o for o in all_partial
        if o.intent_type == "entry" and o.symbol == symbol
    ]
    for entry in partial_entries:
        if not entry.broker_order_id:
            continue
        try:
            broker.cancel_order(entry.broker_order_id)
        except Exception as exc:
            exc_msg = str(exc).lower()
            if any(phrase in exc_msg for phrase in ("already canceled", "not found", "does not exist")):
                logger.warning(
                    "cycle_intent_execution: partial-fill entry for %s already gone at broker (%s)",
                    symbol, exc,
                )
            else:
                logger.exception(
                    "cycle_intent_execution: failed to cancel partial-fill entry %s for %s before exit",
                    entry.broker_order_id, symbol,
                )
                with lock_ctx:
                    try:
                        runtime.audit_event_store.append(
                            AuditEvent(
                                event_type="partial_fill_cancel_failed",
                                symbol=symbol,
                                payload={
                                    "entry_client_order_id": entry.client_order_id,
                                    "entry_broker_order_id": entry.broker_order_id,
                                    "error": str(exc),
                                    "context": "exit",
                                },
                                created_at=now,
                            ),
                            commit=True,
                        )
                    except Exception:
                        pass
                continue  # proceed to exit anyway — existing exception handler covers failure
        canceled_record = OrderRecord(
            client_order_id=entry.client_order_id,
            symbol=entry.symbol,
            side=entry.side,
            intent_type=entry.intent_type,
            status="canceled",
            quantity=entry.quantity,
            trading_mode=entry.trading_mode,
            strategy_version=entry.strategy_version,
            strategy_name=entry.strategy_name,
            created_at=entry.created_at,
            updated_at=now,
            stop_price=entry.stop_price,
            limit_price=entry.limit_price,
            initial_stop_price=entry.initial_stop_price,
            broker_order_id=entry.broker_order_id,
            signal_timestamp=entry.signal_timestamp,
        )
        with lock_ctx:
            try:
                runtime.order_store.save(canceled_record, commit=False)
                runtime.audit_event_store.append(
                    AuditEvent(
                        event_type="partial_fill_entry_canceled",
                        symbol=symbol,
                        payload={
                            "entry_client_order_id": entry.client_order_id,
                            "entry_broker_order_id": entry.broker_order_id,
                            "context": "exit",
                        },
                        created_at=now,
                    ),
                    commit=False,
                )
                runtime.connection.commit()
            except Exception:
                try:
                    runtime.connection.rollback()
                except Exception:
                    pass
                raise
```

Note: `Any` is already imported in `cycle_intent_execution.py` via `from typing import TYPE_CHECKING, Any, ...`.

- [ ] **Step 4: Call the helper in `_execute_exit()` before market/limit exit**

In `_execute_exit()`, find the line that currently reads (around line 635):

```python
    # Submit exit outside the lock.
    try:
        if limit_price is not None:
            broker_order = broker.submit_limit_exit(
```

Insert the call just before `# Submit exit outside the lock.`:

```python
    _cancel_partial_fill_entry(
        symbol=symbol,
        strategy_name=strategy_name,
        runtime=runtime,
        broker=broker,
        settings=settings,
        now=now,
        lock_ctx=lock_ctx,
    )
    # Submit exit outside the lock.
    try:
        if limit_price is not None:
            broker_order = broker.submit_limit_exit(
```

- [ ] **Step 5: Run the new test**

```bash
pytest tests/unit/test_cycle_intent_execution.py::test_execute_exit_cancels_partial_fill_entry_before_market_exit -v
```

Expected: PASS

- [ ] **Step 6: Run full test suite for affected files**

```bash
pytest tests/unit/test_order_dispatch.py tests/unit/test_cycle_intent_execution.py -v
```

Expected: ALL PASS

- [ ] **Step 7: Commit**

```bash
git add src/alpaca_bot/runtime/cycle_intent_execution.py tests/unit/test_cycle_intent_execution.py
git commit -m "fix: cancel partial-fill entry before market exit to prevent wash trade (40310000)"
```

---

### Task 5: Full regression

- [ ] **Step 1: Run full test suite**

```bash
pytest
```

Expected: ALL PASS (no regressions)

- [ ] **Step 2: Commit spec and plan**

```bash
git add docs/superpowers/specs/2026-05-04-partial-fill-wash-trade-fix.md docs/superpowers/plans/2026-05-04-partial-fill-wash-trade-fix.md
git commit -m "docs: spec and plan for partial-fill wash trade fix"
```
