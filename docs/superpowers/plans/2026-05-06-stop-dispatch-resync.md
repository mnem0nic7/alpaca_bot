# Stop-Dispatch Resync: Self-Healing Exception Recovery — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add self-healing exception recovery to `_execute_update_stop()` and `_execute_exit()` so that 40010001 ("client_order_id must be unique") and 40310000 ("insufficient qty available") broker errors are resolved within the same cycle instead of accumulating as `stop_update_failed` and `exit_hard_failed` audit events.

**Architecture:** Three independent fixes in `cycle_intent_execution.py`: (1) on 40010001 in Path C, fetch the conflicting broker stop, UPSERT it to DB, then `replace_order()` to the correct price; (2) on 40310000 in the stop-update path, parse `related_orders` from the error JSON, cancel each blocking order, and update their DB status; (3) on 40310000 in the exit path, do the same cancel step then immediately retry `submit_market_exit()` once before declaring hard failure. A new `get_open_orders_for_symbol()` method on `AlpacaExecutionAdapter` supports the first fix.

**Tech Stack:** Python, Alpaca SDK (`alpaca.trading.requests.GetOrdersRequest`), Postgres (via existing `OrderStore.save()` UPSERT), project's DI fake-callable test pattern.

---

## Files

| Action | File |
|---|---|
| Modify | `src/alpaca_bot/execution/alpaca.py` |
| Modify | `src/alpaca_bot/runtime/cycle_intent_execution.py` |
| Modify | `tests/unit/test_cycle_intent_execution.py` |
| Modify | `tests/unit/test_alpaca_order_execution.py` |

---

## Task 1: Add `get_open_orders_for_symbol()` to broker adapter

**Files:**
- Modify: `src/alpaca_bot/execution/alpaca.py` (after `list_open_orders()` at line 243)
- Modify: `src/alpaca_bot/runtime/cycle_intent_execution.py` (`BrokerProtocol` at line 64)
- Modify: `tests/unit/test_alpaca_order_execution.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/test_alpaca_order_execution.py` (at end of file):

```python
def test_get_open_orders_for_symbol_returns_orders_matching_symbol() -> None:
    class TradingClientStub:
        def __init__(self) -> None:
            self.last_filter = None

        def get_orders(self, filter=None) -> list:
            self.last_filter = filter
            # Return two order stubs for AAPL
            from types import SimpleNamespace
            o = SimpleNamespace(
                client_order_id="v1-breakout:breakout:2026-05-06:AAPL:stop:2026-05-06T14:00:00+00:00",
                id="broker-stop-abc",
                symbol="AAPL",
                side=SimpleNamespace(value="sell"),
                status=SimpleNamespace(value="new"),
                qty="50",
            )
            return [o]

    trading_client = TradingClientStub()
    adapter = AlpacaExecutionAdapter(trading_client=trading_client)

    orders = adapter.get_open_orders_for_symbol("AAPL")

    assert len(orders) == 1
    assert orders[0] == BrokerOrder(
        client_order_id="v1-breakout:breakout:2026-05-06:AAPL:stop:2026-05-06T14:00:00+00:00",
        broker_order_id="broker-stop-abc",
        symbol="AAPL",
        side="sell",
        status="new",
        quantity=50,
    )
    # Verify the filter included the symbol
    filter_obj = trading_client.last_filter
    assert filter_obj is not None
    symbols_attr = getattr(filter_obj, "symbols", None) or (filter_obj.get("symbols") if isinstance(filter_obj, dict) else None)
    assert symbols_attr == ["AAPL"]
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
pytest tests/unit/test_alpaca_order_execution.py::test_get_open_orders_for_symbol_returns_orders_matching_symbol -v
```

Expected: FAIL with `AttributeError: 'AlpacaExecutionAdapter' object has no attribute 'get_open_orders_for_symbol'`

- [ ] **Step 3: Implement `get_open_orders_for_symbol()` in `alpaca.py`**

Add after `list_open_orders()` (after line 243 in `src/alpaca_bot/execution/alpaca.py`):

```python
    def get_open_orders_for_symbol(self, symbol: str) -> list[BrokerOrder]:
        try:
            from alpaca.trading.requests import GetOrdersRequest
        except ModuleNotFoundError:
            filters: Any = {"status": "open", "symbols": [symbol], "limit": 500}
        else:
            filters = GetOrdersRequest(status="open", symbols=[symbol], limit=500)
        raw_orders = _retry_with_backoff(lambda: self._trading.get_orders(filter=filters))
        return [
            BrokerOrder(
                client_order_id=str(getattr(order, "client_order_id", "")),
                broker_order_id=str(getattr(order, "id", "")) or None,
                symbol=str(order.symbol).upper(),
                side=order.side.value if hasattr(order.side, "value") else str(order.side),
                status=order.status.value if hasattr(order.status, "value") else str(order.status),
                quantity=int(float(order.qty)),
            )
            for order in raw_orders
        ]
```

- [ ] **Step 4: Add method to `BrokerProtocol` in `cycle_intent_execution.py`**

In `src/alpaca_bot/runtime/cycle_intent_execution.py`, update `BrokerProtocol` (lines 64–74) to add the new method:

```python
class BrokerProtocol(Protocol):
    def replace_order(self, **kwargs): ...

    def submit_stop_order(self, **kwargs): ...

    def submit_market_exit(self, **kwargs): ...

    def submit_limit_exit(self, **kwargs): ...

    def cancel_order(self, order_id: str) -> None: ...

    def get_open_orders_for_symbol(self, symbol: str) -> list: ...
```

- [ ] **Step 5: Run the test to verify it passes**

```bash
pytest tests/unit/test_alpaca_order_execution.py::test_get_open_orders_for_symbol_returns_orders_matching_symbol -v
```

Expected: PASS

- [ ] **Step 6: Run all tests to verify no regressions**

```bash
pytest tests/unit/test_alpaca_order_execution.py -v
```

Expected: all PASS

- [ ] **Step 7: Commit**

```bash
git add src/alpaca_bot/execution/alpaca.py src/alpaca_bot/runtime/cycle_intent_execution.py tests/unit/test_alpaca_order_execution.py
git commit -m "feat: add get_open_orders_for_symbol() to broker adapter and protocol"
```

---

## Task 2: Add error-parsing helpers and hoist Path C client_order_id

**Files:**
- Modify: `src/alpaca_bot/runtime/cycle_intent_execution.py`

These helpers are small, purely functional, and tested indirectly by the integration tests in Tasks 3–5.

- [ ] **Step 1: Add `import json` to `cycle_intent_execution.py`**

In `src/alpaca_bot/runtime/cycle_intent_execution.py`, the imports block (line 1 area) already has `from __future__ import annotations` etc. Add `import json` to the standard library imports block. The current imports at lines 1–17 do not include `json`. Add it after `import contextlib`:

```python
from __future__ import annotations

import contextlib
import json
import logging
from dataclasses import dataclass, replace as dataclass_replace
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Callable, Protocol, Sequence
```

- [ ] **Step 2: Add helper functions to end of `cycle_intent_execution.py`**

After `_resolve_now()` at line 1042 (end of file), add:

```python
def _parse_related_orders_from_error(exc: Exception) -> list[str]:
    """Extract related_orders broker order IDs from a broker error response body.

    Alpaca encodes the error body as JSON in the exception string for insufficient-qty
    errors: {"code": 40310000, "message": "...", "related_orders": ["uuid-1", ...]}.
    Returns empty list if the body cannot be parsed or has no related_orders.
    """
    try:
        body = json.loads(str(exc))
        return [str(oid) for oid in body.get("related_orders", [])]
    except (json.JSONDecodeError, ValueError, TypeError, AttributeError):
        return []
```

- [ ] **Step 3: Hoist Path C `client_order_id` tracking variable in `_execute_update_stop()`**

In `_execute_update_stop()`, add `_path_c_client_order_id: str | None = None` just before the try block (currently at line 220).

The block starting at line 212 reads:
```python
    if lock_ctx is None:
        lock_ctx = contextlib.nullcontext()

    # Read active stop under lock — same psycopg2 connection as stream thread.
    with lock_ctx:
        active_stop = _latest_active_stop_order(runtime, settings, symbol, strategy_name=strategy_name)

    # Broker calls happen outside the lock to avoid blocking the stream thread.
    try:
```

Change to:
```python
    if lock_ctx is None:
        lock_ctx = contextlib.nullcontext()

    # Read active stop under lock — same psycopg2 connection as stream thread.
    with lock_ctx:
        active_stop = _latest_active_stop_order(runtime, settings, symbol, strategy_name=strategy_name)

    # Tracks the client_order_id generated in Path C (no active stop); used by the
    # exception handler to detect 40010001 resync scenarios vs. Path A/B errors.
    _path_c_client_order_id: str | None = None

    # Broker calls happen outside the lock to avoid blocking the stream thread.
    try:
```

Then in the `else` block (Path C, currently lines 267–307), set `_path_c_client_order_id` right after computing `client_order_id`:

```python
        else:
            client_order_id = _stop_client_order_id(
                settings=settings,
                symbol=symbol,
                timestamp=intent_timestamp,
                strategy_name=strategy_name,
            )
            _path_c_client_order_id = client_order_id  # mark: Path C is executing
            _cancel_partial_fill_entry(
                symbol=symbol,
                strategy_name=strategy_name,
                runtime=runtime,
                broker=broker,
                settings=settings,
                now=now,
                lock_ctx=lock_ctx,
                context="update_stop",
            )
            broker_order = broker.submit_stop_order(
                symbol=symbol,
                quantity=position.quantity,
                stop_price=stop_price,
                client_order_id=client_order_id,
            )
```

- [ ] **Step 4: Run all existing tests to confirm no regressions from variable hoisting**

```bash
pytest tests/unit/test_cycle_intent_execution.py -x -q
```

Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/alpaca_bot/runtime/cycle_intent_execution.py
git commit -m "refactor: hoist Path C client_order_id tracking and add error-parsing helper"
```

---

## Task 3: Path C 40010001 resync handler

**Files:**
- Modify: `src/alpaca_bot/runtime/cycle_intent_execution.py`
- Modify: `tests/unit/test_cycle_intent_execution.py`

When `submit_stop_order()` raises "client_order_id must be unique", the broker already holds a stop under that ID that our DB doesn't track. This task adds: fetch the conflicting broker order, UPSERT it to DB, `replace_order()` to the correct stop price, write DB state, and log `stop_order_resynced`. If the conflicting order isn't found or any step fails, fall back to `stop_update_failed`.

- [ ] **Step 1: Write two failing tests**

Add at the end of `tests/unit/test_cycle_intent_execution.py`:

```python
def test_path_c_duplicate_client_order_id_resyncs_stop_and_emits_stop_order_resynced() -> None:
    """When submit_stop_order raises 40010001 (client_order_id must be unique),
    the handler must: fetch broker orders for symbol, find matching stop, UPSERT to DB,
    replace_order() to correct price, emit stop_order_resynced, NOT emit stop_update_failed."""
    execute_cycle_intents = load_cycle_intent_execution_api()
    settings = make_settings()
    now = datetime(2026, 5, 6, 19, 30, tzinfo=timezone.utc)
    bar_ts = datetime(2026, 5, 6, 19, 15, tzinfo=timezone.utc)

    position = PositionRecord(
        symbol="ACHR",
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        strategy_name="breakout",
        quantity=100,
        entry_price=6.00,
        stop_price=5.50,
        initial_stop_price=5.50,
        opened_at=now,
        updated_at=now,
    )
    # No active stop in DB → Path C fires
    order_store = RecordingOrderStore(orders=[])
    audit_store = RecordingAuditEventStore()

    conflicting_client_order_id = f"v1-breakout:breakout:{now.date().isoformat()}:ACHR:stop:{bar_ts.isoformat()}"
    conflicting_broker_order = SimpleNamespace(
        client_order_id=conflicting_client_order_id,
        broker_order_id="broker-conflicting-stop-1",
        symbol="ACHR",
        side="sell",
        status="new",
        quantity=100,
    )

    replace_calls: list[dict] = []
    open_orders_for_symbol_calls: list[str] = []

    class ResyncBroker:
        def submit_stop_order(self, **kwargs):
            raise Exception("client_order_id must be unique")

        def get_open_orders_for_symbol(self, symbol: str):
            open_orders_for_symbol_calls.append(symbol)
            return [conflicting_broker_order]

        def replace_order(self, **kwargs):
            replace_calls.append(dict(kwargs))
            return SimpleNamespace(
                client_order_id=conflicting_client_order_id,
                broker_order_id="broker-conflicting-stop-1",
                symbol="ACHR",
                side="sell",
                status="accepted",
                quantity=100,
            )

        def cancel_order(self, order_id: str) -> None: pass
        def submit_market_exit(self, **kwargs): pass
        def submit_limit_exit(self, **kwargs): pass

    runtime = SimpleNamespace(
        order_store=order_store,
        position_store=RecordingPositionStore(positions=[position]),
        audit_event_store=audit_store,
        connection=FakeConnection(),
    )

    execute_cycle_intents(
        settings=settings,
        runtime=runtime,
        broker=ResyncBroker(),
        cycle_result=CycleResult(
            as_of=now,
            intents=[CycleIntent(
                intent_type=CycleIntentType.UPDATE_STOP,
                symbol="ACHR",
                timestamp=bar_ts,
                stop_price=5.75,
                strategy_name="breakout",
            )],
        ),
        now=now,
    )

    assert open_orders_for_symbol_calls == ["ACHR"], (
        "get_open_orders_for_symbol must be called with the symbol on 40010001"
    )
    assert len(replace_calls) == 1, "replace_order must be called once with found broker_order_id"
    assert replace_calls[0]["order_id"] == "broker-conflicting-stop-1"
    assert replace_calls[0]["stop_price"] == pytest.approx(5.75)

    event_types = [e.event_type for e in audit_store.appended]
    assert "stop_order_resynced" in event_types, "stop_order_resynced audit event must be emitted"
    assert "stop_update_failed" not in event_types, "stop_update_failed must NOT be emitted on successful resync"

    # Order was saved to DB with correct stop_price
    saved_stops = [o for o in order_store.saved if o.intent_type == "stop"]
    assert any(o.stop_price == pytest.approx(5.75) for o in saved_stops), (
        "DB order record must reflect the new stop_price after resync"
    )


def test_path_c_duplicate_client_order_id_falls_back_to_stop_update_failed_when_order_not_found() -> None:
    """When submit_stop_order raises 40010001 but get_open_orders_for_symbol returns no
    matching order, the handler must fall back to stop_update_failed (order may have been
    filled or expired between the collision and the resync query)."""
    execute_cycle_intents = load_cycle_intent_execution_api()
    settings = make_settings()
    now = datetime(2026, 5, 6, 19, 30, tzinfo=timezone.utc)
    bar_ts = datetime(2026, 5, 6, 19, 15, tzinfo=timezone.utc)

    position = PositionRecord(
        symbol="ACHR",
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        strategy_name="breakout",
        quantity=100,
        entry_price=6.00,
        stop_price=5.50,
        initial_stop_price=5.50,
        opened_at=now,
        updated_at=now,
    )
    order_store = RecordingOrderStore(orders=[])
    audit_store = RecordingAuditEventStore()

    class NotFoundResyncBroker:
        def submit_stop_order(self, **kwargs):
            raise Exception("client_order_id must be unique")

        def get_open_orders_for_symbol(self, symbol: str):
            return []  # matching order is gone (filled/expired)

        def replace_order(self, **kwargs): pass
        def cancel_order(self, order_id: str) -> None: pass
        def submit_market_exit(self, **kwargs): pass
        def submit_limit_exit(self, **kwargs): pass

    runtime = SimpleNamespace(
        order_store=order_store,
        position_store=RecordingPositionStore(positions=[position]),
        audit_event_store=audit_store,
        connection=FakeConnection(),
    )

    execute_cycle_intents(
        settings=settings,
        runtime=runtime,
        broker=NotFoundResyncBroker(),
        cycle_result=CycleResult(
            as_of=now,
            intents=[CycleIntent(
                intent_type=CycleIntentType.UPDATE_STOP,
                symbol="ACHR",
                timestamp=bar_ts,
                stop_price=5.75,
                strategy_name="breakout",
            )],
        ),
        now=now,
    )

    event_types = [e.event_type for e in audit_store.appended]
    assert "stop_update_failed" in event_types, (
        "stop_update_failed must be emitted when resync cannot find the conflicting order"
    )
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
pytest tests/unit/test_cycle_intent_execution.py::test_path_c_duplicate_client_order_id_resyncs_stop_and_emits_stop_order_resynced tests/unit/test_cycle_intent_execution.py::test_path_c_duplicate_client_order_id_falls_back_to_stop_update_failed_when_order_not_found -v
```

Expected: both FAIL

- [ ] **Step 3: Add `_resync_duplicate_stop_order()` helper to `cycle_intent_execution.py`**

Add this function after `_parse_related_orders_from_error()` at the end of `src/alpaca_bot/runtime/cycle_intent_execution.py`:

```python
def _resync_duplicate_stop_order(
    *,
    settings: Settings,
    runtime: RuntimeProtocol,
    broker: BrokerProtocol,
    symbol: str,
    client_order_id: str,
    stop_price: float,
    position: "PositionRecord",
    now: datetime,
    strategy_name: str,
    lock_ctx: Any,
) -> bool:
    """Handle 40010001: broker already has a stop under this client_order_id.

    Fetch open orders for the symbol, find the conflicting order, UPSERT to DB,
    replace_order() to the correct stop_price, and write audit events.

    Returns True on success (caller can return "replaced"), False on any failure
    (caller should fall back to stop_update_failed logging).
    """
    try:
        broker_orders = broker.get_open_orders_for_symbol(symbol)
    except Exception as fetch_exc:
        logger.exception(
            "cycle_intent_execution: get_open_orders_for_symbol failed during 40010001 resync for %s: %s",
            symbol, fetch_exc,
        )
        return False

    found = next((o for o in broker_orders if o.client_order_id == client_order_id), None)
    if found is None:
        logger.warning(
            "cycle_intent_execution: 40010001 resync for %s — no order matching client_order_id=%s found; "
            "order may have been filled or expired",
            symbol, client_order_id,
        )
        return False

    if not found.broker_order_id:
        logger.warning(
            "cycle_intent_execution: 40010001 resync for %s — found order has no broker_order_id; skipping replace",
            symbol,
        )
        return False

    try:
        replaced = broker.replace_order(order_id=found.broker_order_id, stop_price=stop_price)
    except Exception as replace_exc:
        logger.exception(
            "cycle_intent_execution: replace_order failed during 40010001 resync for %s: %s",
            symbol, replace_exc,
        )
        return False

    # Write success: UPSERT the resynced order with the new stop_price, update position,
    # and emit audit events. All writes under lock to serialize with the stream thread.
    with lock_ctx:
        try:
            runtime.order_store.save(
                OrderRecord(
                    client_order_id=found.client_order_id,
                    symbol=symbol,
                    side=found.side if hasattr(found, "side") else "sell",
                    intent_type="stop",
                    status=str(replaced.status).lower(),
                    quantity=found.quantity if hasattr(found, "quantity") else position.quantity,
                    trading_mode=settings.trading_mode,
                    strategy_version=settings.strategy_version,
                    strategy_name=strategy_name,
                    created_at=now,
                    updated_at=now,
                    stop_price=stop_price,
                    initial_stop_price=position.initial_stop_price,
                    broker_order_id=replaced.broker_order_id,
                    signal_timestamp=None,
                ),
                commit=False,
            )
            runtime.position_store.save(
                PositionRecord(
                    symbol=position.symbol,
                    trading_mode=position.trading_mode,
                    strategy_version=position.strategy_version,
                    quantity=position.quantity,
                    entry_price=position.entry_price,
                    stop_price=stop_price,
                    initial_stop_price=position.initial_stop_price,
                    opened_at=position.opened_at,
                    updated_at=now,
                    strategy_name=strategy_name,
                ),
                commit=False,
            )
            runtime.audit_event_store.append(
                AuditEvent(
                    event_type="stop_order_resynced",
                    symbol=symbol,
                    payload={
                        "client_order_id": found.client_order_id,
                        "broker_order_id": found.broker_order_id,
                        "stop_price": stop_price,
                        "strategy_name": strategy_name,
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
            logger.exception(
                "cycle_intent_execution: DB write failed during 40010001 resync for %s", symbol
            )
            return False
    return True
```

- [ ] **Step 4: Restructure the exception handler in `_execute_update_stop()`**

Replace the `except Exception as exc:` block (lines 308–343) in `_execute_update_stop()` with:

```python
    except Exception as exc:
        exc_msg = str(exc).lower()

        if "client_order_id must be unique" in exc_msg and _path_c_client_order_id is not None:
            # 40010001: broker has our stop under this client_order_id but DB doesn't track it.
            # The prior Path C submission succeeded at broker but the DB write failed.
            success = _resync_duplicate_stop_order(
                settings=settings,
                runtime=runtime,
                broker=broker,
                symbol=symbol,
                client_order_id=_path_c_client_order_id,
                stop_price=stop_price,
                position=position,
                now=now,
                strategy_name=strategy_name,
                lock_ctx=lock_ctx,
            )
            if not success:
                _emit_stop_update_failed(
                    runtime=runtime,
                    symbol=symbol,
                    exc=exc,
                    now=now,
                    lock_ctx=lock_ctx,
                    notifier=notifier,
                )
        elif any(phrase in exc_msg for phrase in (
            "not found", "already filled", "already canceled", "does not exist",
            "has been filled", "is filled", "order is", "order was",
        )):
            logger.debug("update_stop skipped for %s — order already gone: %s", symbol, exc)
        else:
            logger.exception("Broker call failed for update_stop on %s; skipping", symbol)
            _emit_stop_update_failed(
                runtime=runtime,
                symbol=symbol,
                exc=exc,
                now=now,
                lock_ctx=lock_ctx,
                notifier=notifier,
            )
        return None
```

Note: "insufficient qty" is removed from the known-gone phrases. This is intentional — 40310000 is NOT an "order already gone" scenario and will be handled in Task 4.

- [ ] **Step 5: Extract `_emit_stop_update_failed()` helper**

The existing `stop_update_failed` logging block (lines 314–342) is now called from two places. Extract it to a private helper. Add after `_resync_duplicate_stop_order()` at the end of the file:

```python
def _emit_stop_update_failed(
    *,
    runtime: RuntimeProtocol,
    symbol: str,
    exc: Exception,
    now: datetime,
    lock_ctx: Any,
    notifier: Any,
) -> None:
    """Log stop_update_failed audit event and optionally send notification."""
    with lock_ctx:
        try:
            runtime.audit_event_store.append(
                AuditEvent(
                    event_type="stop_update_failed",
                    symbol=symbol,
                    payload={
                        "error": str(exc),
                        "symbol": symbol,
                        "timestamp": now.isoformat(),
                    },
                    created_at=now,
                ),
                commit=True,
            )
        except Exception:
            logger.exception("Failed to write stop_update_failed audit event for %s", symbol)
    if notifier is not None:
        try:
            notifier.send(
                subject=f"Stop update failed: {symbol}",
                body=(
                    f"Broker call failed for UPDATE_STOP on {symbol}.\n"
                    f"Position may be losing stop protection.\n"
                    f"Error: {exc}"
                ),
            )
        except Exception:
            logger.exception("Notifier failed to send stop_update_failed alert for %s", symbol)
```

Also update the original else branch in the exception handler that used the inline logging to call `_emit_stop_update_failed()` instead (already reflected in Step 4 above). Make sure you delete the old inline logging block from `_execute_update_stop()`.

- [ ] **Step 6: Run the failing tests to verify they pass**

```bash
pytest tests/unit/test_cycle_intent_execution.py::test_path_c_duplicate_client_order_id_resyncs_stop_and_emits_stop_order_resynced tests/unit/test_cycle_intent_execution.py::test_path_c_duplicate_client_order_id_falls_back_to_stop_update_failed_when_order_not_found -v
```

Expected: both PASS

- [ ] **Step 7: Run full test suite to verify no regressions**

```bash
pytest tests/unit/test_cycle_intent_execution.py -x -q
```

Expected: all PASS

- [ ] **Step 8: Commit**

```bash
git add src/alpaca_bot/runtime/cycle_intent_execution.py tests/unit/test_cycle_intent_execution.py
git commit -m "feat: add 40010001 resync handler to _execute_update_stop Path C"
```

---

## Task 4: Path C 40310000 unblock handler

**Files:**
- Modify: `src/alpaca_bot/runtime/cycle_intent_execution.py`
- Modify: `tests/unit/test_cycle_intent_execution.py`

When `submit_stop_order()` raises "insufficient qty available", a broker order in pending_cancel state is holding all shares. Parse `related_orders` from the error JSON, cancel each, update their DB status to "pending_cancel", and log `blocking_stop_canceled`. Do NOT retry the stop submission — the shares may still be held briefly; the next cycle (60s later) will resubmit safely.

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/test_cycle_intent_execution.py`:

```python
def test_path_c_insufficient_qty_cancels_blocking_orders_and_emits_blocking_stop_canceled() -> None:
    """When submit_stop_order raises 40310000 (insufficient qty available), the handler must:
    - parse related_orders from the error JSON
    - call cancel_order() for each blocking broker order ID
    - update their DB status to 'pending_cancel'
    - emit blocking_stop_canceled audit event
    - NOT emit stop_update_failed
    - NOT retry submit_stop_order"""
    execute_cycle_intents = load_cycle_intent_execution_api()
    settings = make_settings()
    now = datetime(2026, 5, 6, 19, 30, tzinfo=timezone.utc)
    bar_ts = datetime(2026, 5, 6, 19, 15, tzinfo=timezone.utc)

    position = PositionRecord(
        symbol="CLOV",
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        strategy_name="breakout",
        quantity=101,
        entry_price=2.63,
        stop_price=2.40,
        initial_stop_price=2.40,
        opened_at=now,
        updated_at=now,
    )
    # No active stop in DB (marked canceled) → Path C fires
    order_store = RecordingOrderStore(orders=[])
    audit_store = RecordingAuditEventStore()

    cancel_calls: list[str] = []
    submit_stop_calls: list[dict] = []

    import json as _json
    error_body = _json.dumps({
        "code": 40310000,
        "message": "insufficient qty available for order",
        "related_orders": ["broker-phantom-stop-99"],
    })

    class InsufficientQtyBroker:
        def submit_stop_order(self, **kwargs):
            submit_stop_calls.append(dict(kwargs))
            raise Exception(error_body)

        def get_open_orders_for_symbol(self, symbol: str):
            return []

        def cancel_order(self, order_id: str) -> None:
            cancel_calls.append(order_id)

        def replace_order(self, **kwargs): pass
        def submit_market_exit(self, **kwargs): pass
        def submit_limit_exit(self, **kwargs): pass

    runtime = SimpleNamespace(
        order_store=order_store,
        position_store=RecordingPositionStore(positions=[position]),
        audit_event_store=audit_store,
        connection=FakeConnection(),
    )

    execute_cycle_intents(
        settings=settings,
        runtime=runtime,
        broker=InsufficientQtyBroker(),
        cycle_result=CycleResult(
            as_of=now,
            intents=[CycleIntent(
                intent_type=CycleIntentType.UPDATE_STOP,
                symbol="CLOV",
                timestamp=bar_ts,
                stop_price=2.50,
                strategy_name="breakout",
            )],
        ),
        now=now,
    )

    assert len(submit_stop_calls) == 1, "submit_stop_order must be called exactly once (no retry)"
    assert cancel_calls == ["broker-phantom-stop-99"], (
        "cancel_order must be called for each blocking order in related_orders"
    )

    event_types = [e.event_type for e in audit_store.appended]
    assert "blocking_stop_canceled" in event_types, "blocking_stop_canceled audit event must be emitted"
    assert "stop_update_failed" not in event_types, "stop_update_failed must NOT be emitted for 40310000"
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
pytest tests/unit/test_cycle_intent_execution.py::test_path_c_insufficient_qty_cancels_blocking_orders_and_emits_blocking_stop_canceled -v
```

Expected: FAIL (40310000 currently falls through to existing known-gone check which silently swallows it, so `blocking_stop_canceled` is not emitted)

- [ ] **Step 3: Add `_cancel_blocking_orders()` helper to `cycle_intent_execution.py`**

Add after `_emit_stop_update_failed()` at the end of the file:

```python
def _cancel_blocking_orders(
    *,
    exc: Exception,
    settings: Settings,
    runtime: RuntimeProtocol,
    broker: BrokerProtocol,
    symbol: str,
    event_type: str,
    now: datetime,
    lock_ctx: Any,
) -> None:
    """Handle 40310000: cancel broker orders that are holding shares for symbol.

    Parses related_orders from the error JSON, calls cancel_order() for each
    (best-effort: ignores cancel failures), then tries to update their DB status
    to pending_cancel (best-effort: a no-op if the order is already "canceled" in
    the DB, which is the primary 40310000 scenario), and emits one audit event
    per canceled order. The broker cancel and audit event are the critical operations.
    """
    related = _parse_related_orders_from_error(exc)
    if not related:
        logger.warning(
            "cycle_intent_execution: 40310000 for %s but no related_orders in error body: %s",
            symbol, exc,
        )
        return

    for broker_order_id in related:
        try:
            broker.cancel_order(broker_order_id)
        except Exception as cancel_exc:
            logger.warning(
                "cycle_intent_execution: cancel_order(%s) failed during 40310000 unblock for %s: %s",
                broker_order_id, symbol, cancel_exc,
            )

        # Update DB status to pending_cancel for any order with this broker_order_id.
        # List all active orders (cheap: called only on error) and filter client-side.
        with lock_ctx:
            try:
                all_active = runtime.order_store.list_by_status(
                    trading_mode=settings.trading_mode,
                    strategy_version=settings.strategy_version,
                    statuses=list(ACTIVE_STOP_STATUSES),
                )
                for order in all_active:
                    if order.broker_order_id == broker_order_id:
                        runtime.order_store.save(
                            OrderRecord(
                                client_order_id=order.client_order_id,
                                symbol=order.symbol,
                                side=order.side,
                                intent_type=order.intent_type,
                                status="pending_cancel",
                                quantity=order.quantity,
                                trading_mode=order.trading_mode,
                                strategy_version=order.strategy_version,
                                strategy_name=order.strategy_name,
                                created_at=order.created_at,
                                updated_at=now,
                                stop_price=order.stop_price,
                                limit_price=order.limit_price,
                                initial_stop_price=order.initial_stop_price,
                                broker_order_id=order.broker_order_id,
                                signal_timestamp=order.signal_timestamp,
                            ),
                            commit=False,
                        )
            except Exception:
                logger.exception(
                    "cycle_intent_execution: DB update failed during 40310000 unblock for %s broker_order_id=%s",
                    symbol, broker_order_id,
                )

            try:
                runtime.audit_event_store.append(
                    AuditEvent(
                        event_type=event_type,
                        symbol=symbol,
                        payload={
                            "broker_order_id": broker_order_id,
                            "symbol": symbol,
                        },
                        created_at=now,
                    ),
                    commit=False,
                )
                runtime.connection.commit()
            except Exception:
                logger.exception(
                    "cycle_intent_execution: audit event failed during 40310000 unblock for %s", symbol
                )
                try:
                    runtime.connection.rollback()
                except Exception:
                    pass
```

- [ ] **Step 4: Add 40310000 branch to the exception handler in `_execute_update_stop()`**

Update the restructured exception handler from Task 3, Step 4, to add the `elif "insufficient qty available"` branch between the 40010001 handler and the known-gone phrases handler:

```python
    except Exception as exc:
        exc_msg = str(exc).lower()

        if "client_order_id must be unique" in exc_msg and _path_c_client_order_id is not None:
            # 40010001: broker has our stop under this client_order_id but DB doesn't track it.
            success = _resync_duplicate_stop_order(
                settings=settings,
                runtime=runtime,
                broker=broker,
                symbol=symbol,
                client_order_id=_path_c_client_order_id,
                stop_price=stop_price,
                position=position,
                now=now,
                strategy_name=strategy_name,
                lock_ctx=lock_ctx,
            )
            if not success:
                _emit_stop_update_failed(
                    runtime=runtime,
                    symbol=symbol,
                    exc=exc,
                    now=now,
                    lock_ctx=lock_ctx,
                    notifier=notifier,
                )
        elif "insufficient qty available" in exc_msg:
            # 40310000: a broker order holds all shares (pending_cancel state invisible to
            # reconciliation). Cancel the blocking orders and update DB status so the next
            # cycle can resubmit cleanly. Do NOT retry here — shares may still be held briefly.
            _cancel_blocking_orders(
                exc=exc,
                settings=settings,
                runtime=runtime,
                broker=broker,
                symbol=symbol,
                event_type="blocking_stop_canceled",
                now=now,
                lock_ctx=lock_ctx,
            )
        elif any(phrase in exc_msg for phrase in (
            "not found", "already filled", "already canceled", "does not exist",
            "has been filled", "is filled", "order is", "order was",
        )):
            logger.debug("update_stop skipped for %s — order already gone: %s", symbol, exc)
        else:
            logger.exception("Broker call failed for update_stop on %s; skipping", symbol)
            _emit_stop_update_failed(
                runtime=runtime,
                symbol=symbol,
                exc=exc,
                now=now,
                lock_ctx=lock_ctx,
                notifier=notifier,
            )
        return None
```

- [ ] **Step 5: Run the new test to verify it passes**

```bash
pytest tests/unit/test_cycle_intent_execution.py::test_path_c_insufficient_qty_cancels_blocking_orders_and_emits_blocking_stop_canceled -v
```

Expected: PASS

- [ ] **Step 6: Run all stop-dispatch tests**

```bash
pytest tests/unit/test_cycle_intent_execution.py -x -q
```

Expected: all PASS

- [ ] **Step 7: Commit**

```bash
git add src/alpaca_bot/runtime/cycle_intent_execution.py tests/unit/test_cycle_intent_execution.py
git commit -m "feat: add 40310000 unblock handler to _execute_update_stop; remove insufficient qty from known-gone list"
```

---

## Task 5: Exit 40310000 unblock + retry handler

**Files:**
- Modify: `src/alpaca_bot/runtime/cycle_intent_execution.py`
- Modify: `tests/unit/test_cycle_intent_execution.py`

When `submit_market_exit()` raises "insufficient qty available", cancel blocking orders then retry once. The EOD context makes the immediate retry necessary — waiting 60 seconds would miss market hours.

- [ ] **Step 1: Write three failing tests**

Add to `tests/unit/test_cycle_intent_execution.py`:

```python
def test_exit_insufficient_qty_cancels_blocking_orders_retries_and_succeeds() -> None:
    """When submit_market_exit raises 40310000, the handler must cancel blocking orders
    (related_orders from error JSON), emit blocking_stop_canceled_for_exit, retry
    submit_market_exit once, and succeed without emitting exit_hard_failed."""
    execute_cycle_intents = load_cycle_intent_execution_api()
    settings = make_settings()
    now = datetime(2026, 5, 6, 19, 50, tzinfo=timezone.utc)

    position = PositionRecord(
        symbol="CLOV",
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        strategy_name="breakout",
        quantity=101,
        entry_price=2.63,
        stop_price=2.40,
        initial_stop_price=2.40,
        opened_at=now,
        updated_at=now,
    )
    order_store = RecordingOrderStore(orders=[])
    audit_store = RecordingAuditEventStore()

    cancel_calls: list[str] = []
    exit_attempt = [0]

    import json as _json
    error_body = _json.dumps({
        "code": 40310000,
        "message": "insufficient qty available for order",
        "related_orders": ["broker-phantom-stop-77"],
    })

    class BlockedExitBroker:
        def submit_market_exit(self, **kwargs):
            exit_attempt[0] += 1
            if exit_attempt[0] == 1:
                raise Exception(error_body)
            return SimpleNamespace(
                client_order_id=kwargs["client_order_id"],
                broker_order_id="broker-exit-ok",
                symbol=kwargs["symbol"],
                side="sell",
                status="accepted",
                quantity=kwargs["quantity"],
            )

        def cancel_order(self, order_id: str) -> None:
            cancel_calls.append(order_id)

        def replace_order(self, **kwargs): pass
        def submit_stop_order(self, **kwargs): pass
        def submit_limit_exit(self, **kwargs): pass
        def get_open_orders_for_symbol(self, symbol: str): return []

    runtime = SimpleNamespace(
        order_store=order_store,
        position_store=RecordingPositionStore(positions=[position]),
        audit_event_store=audit_store,
        connection=FakeConnection(),
    )

    report = execute_cycle_intents(
        settings=settings,
        runtime=runtime,
        broker=BlockedExitBroker(),
        cycle_result=CycleResult(
            as_of=now,
            intents=[CycleIntent(
                intent_type=CycleIntentType.EXIT,
                symbol="CLOV",
                timestamp=now,
                reason="eod_flatten",
                strategy_name="breakout",
            )],
        ),
        now=now,
    )

    assert exit_attempt[0] == 2, "submit_market_exit must be called twice (first fails, retry succeeds)"
    assert cancel_calls == ["broker-phantom-stop-77"], "cancel_order must be called for the blocking order"

    event_types = [e.event_type for e in audit_store.appended]
    assert "blocking_stop_canceled_for_exit" in event_types, (
        "blocking_stop_canceled_for_exit must be emitted before retry"
    )
    assert "exit_hard_failed" not in event_types, "exit_hard_failed must NOT be emitted when retry succeeds"
    assert report.submitted_exit_count == 1, "submitted_exit_count must be 1 after successful retry"


def test_exit_insufficient_qty_emits_exit_hard_failed_when_retry_also_fails() -> None:
    """When submit_market_exit raises 40310000 and the retry also fails,
    exit_hard_failed must be emitted and submitted_exit_count must be 0."""
    execute_cycle_intents = load_cycle_intent_execution_api()
    settings = make_settings()
    now = datetime(2026, 5, 6, 19, 50, tzinfo=timezone.utc)

    position = PositionRecord(
        symbol="CLOV",
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        strategy_name="breakout",
        quantity=101,
        entry_price=2.63,
        stop_price=2.40,
        initial_stop_price=2.40,
        opened_at=now,
        updated_at=now,
    )
    order_store = RecordingOrderStore(orders=[])
    audit_store = RecordingAuditEventStore()

    import json as _json
    error_body = _json.dumps({
        "code": 40310000,
        "message": "insufficient qty available for order",
        "related_orders": ["broker-phantom-stop-88"],
    })

    class DoubleFailExitBroker:
        def submit_market_exit(self, **kwargs):
            raise Exception(error_body)

        def cancel_order(self, order_id: str) -> None: pass
        def replace_order(self, **kwargs): pass
        def submit_stop_order(self, **kwargs): pass
        def submit_limit_exit(self, **kwargs): pass
        def get_open_orders_for_symbol(self, symbol: str): return []

    runtime = SimpleNamespace(
        order_store=order_store,
        position_store=RecordingPositionStore(positions=[position]),
        audit_event_store=audit_store,
        connection=FakeConnection(),
    )

    report = execute_cycle_intents(
        settings=settings,
        runtime=runtime,
        broker=DoubleFailExitBroker(),
        cycle_result=CycleResult(
            as_of=now,
            intents=[CycleIntent(
                intent_type=CycleIntentType.EXIT,
                symbol="CLOV",
                timestamp=now,
                reason="eod_flatten",
                strategy_name="breakout",
            )],
        ),
        now=now,
    )

    event_types = [e.event_type for e in audit_store.appended]
    assert "blocking_stop_canceled_for_exit" in event_types, (
        "blocking_stop_canceled_for_exit must be emitted even when retry also fails"
    )
    assert "exit_hard_failed" in event_types, "exit_hard_failed must be emitted when retry also fails"
    assert report.submitted_exit_count == 0
    # The position has stop_price=2.40 and _cancel_blocking_orders() canceled the phantom
    # broker stop, leaving the position unprotected. A recovery stop must always be queued
    # when retry fails after 40310000 unblock, regardless of canceled_stop_count (which
    # is 0 in this scenario because the DB stop was already "canceled").
    assert "recovery_stop_queued_after_exit_failure" in event_types, (
        "recovery_stop_queued_after_exit_failure must be emitted when retry fails "
        "and position has a stop_price — canceled_stop_count==0 must not suppress it"
    )


def test_path_c_get_open_orders_for_symbol_raises_falls_back_to_stop_update_failed() -> None:
    """When submit_stop_order raises 40010001 but get_open_orders_for_symbol also raises,
    the handler must fall back to stop_update_failed (existing behavior)."""
    execute_cycle_intents = load_cycle_intent_execution_api()
    settings = make_settings()
    now = datetime(2026, 5, 6, 19, 30, tzinfo=timezone.utc)
    bar_ts = datetime(2026, 5, 6, 19, 15, tzinfo=timezone.utc)

    position = PositionRecord(
        symbol="ACHR",
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        strategy_name="breakout",
        quantity=100,
        entry_price=6.00,
        stop_price=5.50,
        initial_stop_price=5.50,
        opened_at=now,
        updated_at=now,
    )
    order_store = RecordingOrderStore(orders=[])
    audit_store = RecordingAuditEventStore()

    class BrokerFetchFails:
        def submit_stop_order(self, **kwargs):
            raise Exception("client_order_id must be unique")

        def get_open_orders_for_symbol(self, symbol: str):
            raise RuntimeError("broker unavailable")

        def cancel_order(self, order_id: str) -> None: pass
        def replace_order(self, **kwargs): pass
        def submit_market_exit(self, **kwargs): pass
        def submit_limit_exit(self, **kwargs): pass

    runtime = SimpleNamespace(
        order_store=order_store,
        position_store=RecordingPositionStore(positions=[position]),
        audit_event_store=audit_store,
        connection=FakeConnection(),
    )

    execute_cycle_intents(
        settings=settings,
        runtime=runtime,
        broker=BrokerFetchFails(),
        cycle_result=CycleResult(
            as_of=now,
            intents=[CycleIntent(
                intent_type=CycleIntentType.UPDATE_STOP,
                symbol="ACHR",
                timestamp=bar_ts,
                stop_price=5.75,
                strategy_name="breakout",
            )],
        ),
        now=now,
    )

    event_types = [e.event_type for e in audit_store.appended]
    assert "stop_update_failed" in event_types, (
        "stop_update_failed must be emitted when get_open_orders_for_symbol raises during resync"
    )
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/unit/test_cycle_intent_execution.py::test_exit_insufficient_qty_cancels_blocking_orders_retries_and_succeeds tests/unit/test_cycle_intent_execution.py::test_exit_insufficient_qty_emits_exit_hard_failed_when_retry_also_fails tests/unit/test_cycle_intent_execution.py::test_path_c_get_open_orders_for_symbol_raises_falls_back_to_stop_update_failed -v
```

Expected: all FAIL

- [ ] **Step 3: Restructure the exit exception handler in `_execute_exit()`**

In `src/alpaca_bot/runtime/cycle_intent_execution.py`, find the `except Exception:` block in `_execute_exit()` that starts at approximately line 669. The current handler unconditionally writes `exit_hard_failed`. Replace it:

Current (lines 669–759):
```python
    except Exception:
        # Stops are already canceled at the broker (position is unprotected). Persist
        # canceled stop records and an audit event unconditionally ...
        exit_method = "submit_limit_exit" if limit_price is not None else "submit_market_exit"
        logger.exception(...)
        with lock_ctx:
            try:
                for record in canceled_order_records:
                    runtime.order_store.save(record, commit=False)
                runtime.audit_event_store.append(
                    AuditEvent(event_type="exit_hard_failed", ...),
                    commit=False,
                )
                # Re-queue recovery stop ...
                runtime.connection.commit()
            except Exception:
                ...
        if notifier is not None:
            ...
        return canceled_stop_count, 0, 1
```

Replace with:

```python
    except Exception as exit_exc:
        exc_msg = str(exit_exc).lower()
        if "insufficient qty available" in exc_msg and limit_price is None:
            # 40310000: a broker order in pending_cancel holds all shares. Cancel it
            # and retry the market exit once — EOD context requires immediate action.
            _cancel_blocking_orders(
                exc=exit_exc,
                settings=settings,
                runtime=runtime,
                broker=broker,
                symbol=symbol,
                event_type="blocking_stop_canceled_for_exit",
                now=now,
                lock_ctx=lock_ctx,
            )
            try:
                broker_order = broker.submit_market_exit(
                    symbol=symbol,
                    quantity=position.quantity,
                    client_order_id=client_order_id,
                )
            except Exception:
                logger.exception(
                    "cycle_intent_execution: submit_market_exit retry also failed for %s/%s "
                    "after 40310000 unblock; position is unprotected",
                    symbol, strategy_name,
                )
                with lock_ctx:
                    try:
                        for record in canceled_order_records:
                            runtime.order_store.save(record, commit=False)
                        runtime.audit_event_store.append(
                            AuditEvent(
                                event_type="exit_hard_failed",
                                symbol=symbol,
                                payload={
                                    "intent_type": "exit",
                                    "action": "submit_market_exit_failed_after_40310000_unblock",
                                    "reason": reason,
                                    "canceled_stop_count": canceled_stop_count,
                                },
                                created_at=now,
                            ),
                            commit=False,
                        )
                        # Always queue a recovery stop when retry fails after 40310000 unblock.
                        # canceled_stop_count is 0 in this scenario (the phantom stop was
                        # already "canceled" in DB, so _active_stop_orders returned empty).
                        # The _cancel_blocking_orders() call above actually canceled the broker
                        # stop, leaving the position unprotected — queue recovery unconditionally.
                        if position.stop_price is not None and position.stop_price > 0:
                            _recovery_stop_id = (
                                f"exit_failed_recovery:{settings.strategy_version}:"
                                f"{now.date().isoformat()}:{symbol}:stop"
                            )
                            runtime.order_store.save(
                                OrderRecord(
                                    client_order_id=_recovery_stop_id,
                                    symbol=symbol,
                                    side="sell",
                                    intent_type="stop",
                                    status="pending_submit",
                                    quantity=position.quantity,
                                    trading_mode=settings.trading_mode,
                                    strategy_version=settings.strategy_version,
                                    strategy_name=strategy_name,
                                    created_at=now,
                                    updated_at=now,
                                    stop_price=position.stop_price,
                                    initial_stop_price=position.initial_stop_price,
                                ),
                                commit=False,
                            )
                            runtime.audit_event_store.append(
                                AuditEvent(
                                    event_type="recovery_stop_queued_after_exit_failure",
                                    symbol=symbol,
                                    payload={
                                        "client_order_id": _recovery_stop_id,
                                        "stop_price": position.stop_price,
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
                if notifier is not None:
                    try:
                        notifier.send(
                            subject=f"Exit HARD FAILED: {symbol}/{strategy_name} — position UNPROTECTED",
                            body=(
                                f"40310000 unblock: stop cancel succeeded but market exit retry "
                                f"raised for {symbol} ({strategy_name}).\n"
                                f"Position is live and unprotected. A recovery stop has been queued.\n"
                                f"Reason: {reason}"
                            ),
                        )
                    except Exception:
                        logger.exception(
                            "cycle_intent_execution: notifier failed for exit retry failure on %s", symbol
                        )
                return canceled_stop_count, 0, 1  # hard_failed: retry also raised
            # Retry succeeded — broker_order is now set; fall through to success path below.
        else:
            # Not a 40310000 or it's a limit exit — existing hard-fail behavior.
            exit_method = "submit_limit_exit" if limit_price is not None else "submit_market_exit"
            logger.exception(
                "cycle_intent_execution: %s failed for %s/%s; "
                "position is unprotected — manual intervention required",
                exit_method,
                symbol,
                strategy_name,
            )
            with lock_ctx:
                try:
                    for record in canceled_order_records:
                        runtime.order_store.save(record, commit=False)
                    runtime.audit_event_store.append(
                        AuditEvent(
                            event_type="exit_hard_failed",
                            symbol=symbol,
                            payload={
                                "intent_type": "exit",
                                "action": f"{exit_method}_failed",
                                "reason": reason,
                                "canceled_stop_count": canceled_stop_count,
                            },
                            created_at=now,
                        ),
                        commit=False,
                    )
                    if canceled_stop_count > 0 and position.stop_price is not None and position.stop_price > 0:
                        _recovery_stop_id = (
                            f"exit_failed_recovery:{settings.strategy_version}:"
                            f"{now.date().isoformat()}:{symbol}:stop"
                        )
                        runtime.order_store.save(
                            OrderRecord(
                                client_order_id=_recovery_stop_id,
                                symbol=symbol,
                                side="sell",
                                intent_type="stop",
                                status="pending_submit",
                                quantity=position.quantity,
                                trading_mode=settings.trading_mode,
                                strategy_version=settings.strategy_version,
                                strategy_name=strategy_name,
                                created_at=now,
                                updated_at=now,
                                stop_price=position.stop_price,
                                initial_stop_price=position.initial_stop_price,
                            ),
                            commit=False,
                        )
                        runtime.audit_event_store.append(
                            AuditEvent(
                                event_type="recovery_stop_queued_after_exit_failure",
                                symbol=symbol,
                                payload={
                                    "client_order_id": _recovery_stop_id,
                                    "stop_price": position.stop_price,
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
            if notifier is not None:
                try:
                    notifier.send(
                        subject=f"Exit HARD FAILED: {symbol}/{strategy_name} — position UNPROTECTED",
                        body=(
                            f"Stop cancel succeeded but {exit_method} raised for {symbol} ({strategy_name}).\n"
                            f"Position is live and unprotected. A recovery stop has been queued.\n"
                            f"Manual verification required.\n"
                            f"Reason: {reason}"
                        ),
                    )
                except Exception:
                    logger.exception(
                        "cycle_intent_execution: notifier failed for exit submission failure on %s", symbol
                    )
            return canceled_stop_count, 0, 1  # hard_failed: exit submission raised
```

The success path (lines 761–858 in original, now starting after the `except` block) remains unchanged — when the retry succeeds, `broker_order` is set and execution falls through to it normally.

- [ ] **Step 4: Run the three new tests**

```bash
pytest tests/unit/test_cycle_intent_execution.py::test_exit_insufficient_qty_cancels_blocking_orders_retries_and_succeeds tests/unit/test_cycle_intent_execution.py::test_exit_insufficient_qty_emits_exit_hard_failed_when_retry_also_fails tests/unit/test_cycle_intent_execution.py::test_path_c_get_open_orders_for_symbol_raises_falls_back_to_stop_update_failed -v
```

Expected: all PASS

- [ ] **Step 5: Run the full test suite**

```bash
pytest -x -q
```

Expected: all PASS. Final count should be prior count + 6 new tests.

- [ ] **Step 6: Commit**

```bash
git add src/alpaca_bot/runtime/cycle_intent_execution.py tests/unit/test_cycle_intent_execution.py
git commit -m "feat: add 40310000 unblock+retry to _execute_exit; closes stop-dispatch resync loop"
```

---

## Task 6: Deploy

**Files:**
- None (env file already has `ENABLE_PROFIT_TRAIL=true`)

- [ ] **Step 1: Run full test suite one final time**

```bash
pytest -q
```

Expected: all PASS

- [ ] **Step 2: Deploy**

```bash
./scripts/deploy.sh /etc/alpaca_bot/alpaca-bot.env
```

Expected: all services healthy

- [ ] **Step 3: Verify `stop_update_failed` rate drops**

Wait for one full cycle (60s), then check:

```bash
docker exec deploy-postgres-1 psql -U alpaca_bot -d alpaca_bot -c "SELECT COUNT(*) FROM audit_events WHERE event_type = 'stop_update_failed' AND created_at > NOW() - INTERVAL '5 minutes';"
```

Expected: count significantly lower than pre-deploy (was ~11/cycle)

- [ ] **Step 4: Confirm new audit events appear**

```bash
docker exec deploy-postgres-1 psql -U alpaca_bot -d alpaca_bot -c "SELECT event_type, symbol, created_at FROM audit_events WHERE event_type IN ('stop_order_resynced', 'blocking_stop_canceled', 'blocking_stop_canceled_for_exit') ORDER BY created_at DESC LIMIT 20;"
```

These events may not appear immediately if the broker de-sync has already resolved between yesterday and today. The absence of `stop_update_failed` events is the primary success signal.

---

## Self-Review

**Spec coverage check:**

| Spec requirement | Task covering it |
|---|---|
| Fix 1: `get_open_orders_for_symbol()` broker method | Task 1 |
| Fix 2: 40010001 resync in Path C | Task 3 |
| Fix 3: 40310000 unblock in stop update path | Task 4 |
| Fix 4: 40310000 unblock + retry in exit path | Task 5 |
| Audit events: `stop_order_resynced`, `blocking_stop_canceled`, `blocking_stop_canceled_for_exit` | Tasks 3, 4, 5 |
| Error handling: `get_open_orders` raises → fallback to `stop_update_failed` | Task 5 (Test 3) |
| Error handling: `cancel_order` fails → best-effort, continue | Task 4 helper |
| Error handling: `replace_order` fails → fallback to `stop_update_failed` | Task 3 helper |
| Remove "insufficient qty" from known-gone phrases | Task 4 handler restructure |
| Test 1 (40010001 resync success) | Task 3 |
| Test 2 (40010001 resync no matching order) | Task 3 |
| Test 3 (40310000 stop update) | Task 4 |
| Test 4 (40310000 exit retry succeeds) | Task 5 |
| Test 5 (40310000 exit retry fails) | Task 5 |
| Test 6 (get_open_orders raises) | Task 5 |

**Placeholder scan:** No TBDs or incomplete sections.

**Type consistency:** `BrokerProtocol.get_open_orders_for_symbol()` returns `list` (untyped to avoid circular import with `BrokerOrder`); `AlpacaExecutionAdapter.get_open_orders_for_symbol()` returns `list[BrokerOrder]`. Consistent with existing `list_open_orders()` pattern. `_cancel_blocking_orders()` receives `settings: Settings` for `list_by_status` calls — matches all call sites in Tasks 4 and 5.
