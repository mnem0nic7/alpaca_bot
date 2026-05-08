# Recovery Stop Above Market — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Break the `startup_recovery` infinite loop when a position's stored `stop_price` is at or above the current market price by queuing a market exit instead of a stale stop, and extend `order_dispatch` to actually submit that exit to the broker.

**Architecture:** Two-file surgical change. `startup_recovery.py` gains (a) an `active_exit_symbols` skip-set, (b) a current-price check from `BrokerPosition.market_value`, and (c) a branch that writes a `pending_submit` exit `OrderRecord` instead of a stop when `stop_price >= current_price`. `order_dispatch.py` gains `submit_market_exit` in its `BrokerProtocol` and a matching dispatch branch in `_submit_order_to_broker`. Both changes follow the existing two-phase intent/dispatch pattern — no direct broker calls from recovery logic.

**Tech Stack:** Python 3.12, pytest, existing fake-callable DI pattern (`RecordingOrderStore`, `RecordingAuditEventStore`, `RecordingBroker`).

---

## File Map

| File | Change |
|---|---|
| `tests/unit/test_startup_recovery.py` | Add 5 new tests |
| `src/alpaca_bot/runtime/startup_recovery.py` | active_exit_symbols set + current_price check + exit-queue branch |
| `tests/unit/test_order_dispatch.py` | Add `submit_market_exit` to `RecordingBroker`; add 1 new test; update 1 existing test |
| `src/alpaca_bot/runtime/order_dispatch.py` | Add `submit_market_exit` to `BrokerProtocol`; add exit branch in `_submit_order_to_broker` |

---

### Task 1: Failing tests — startup_recovery stale-stop detection

**Files:**
- Modify: `tests/unit/test_startup_recovery.py` (append after line 1450)

- [ ] **Step 1: Write the 5 new failing tests**

Append these tests at the bottom of `tests/unit/test_startup_recovery.py`:

```python
# ---------------------------------------------------------------------------
# Recovery exit when stop_price >= current market price (infinite-loop fix)
# ---------------------------------------------------------------------------

def test_startup_recovery_queues_exit_when_stop_above_market() -> None:
    """When a position's stored stop_price is at or above the current market price
    (as derived from BrokerPosition.market_value), startup_recovery must queue a
    market exit order instead of the stale stop.  Without this guard the stale stop
    is re-queued every cycle, Alpaca rejects it with 42210000, and the loop never
    terminates."""
    settings = make_settings()
    now = datetime(2026, 5, 8, 16, 0, tzinfo=timezone.utc)

    # DB position: stop_price=$16.34, but market has fallen to ~$15.47
    arlo_position = PositionRecord(
        symbol="ARLO",
        trading_mode=settings.trading_mode,
        strategy_version=settings.strategy_version,
        strategy_name="bull_flag",
        quantity=10,
        entry_price=16.27,
        stop_price=16.34,   # stale — above current market
        initial_stop_price=16.34,
        opened_at=now,
        updated_at=now,
    )
    # Broker reports market_value=$154.70, so current_price = 154.70/10 = $15.47
    broker_position = BrokerPosition(
        symbol="ARLO",
        quantity=10,
        entry_price=16.27,
        market_value=154.70,
    )
    position_store = RecordingPositionStore(existing_positions=[arlo_position])
    order_store = RecordingOrderStore()
    audit_event_store = RecordingAuditEventStore()
    runtime = make_runtime_context(
        settings,
        position_store=position_store,
        order_store=order_store,
        audit_event_store=audit_event_store,
    )

    recover_startup_state(
        settings=settings,
        runtime=runtime,
        broker_open_positions=[broker_position],
        broker_open_orders=[],
        now=now,
    )

    # Must queue an exit, not a stop
    exit_saves = [
        o for o in order_store.saved
        if o.intent_type == "exit" and o.symbol == "ARLO"
    ]
    stop_saves = [
        o for o in order_store.saved
        if o.intent_type == "stop" and o.symbol == "ARLO"
    ]
    assert len(exit_saves) == 1, "Must queue exactly one exit order when stop is above market"
    assert stop_saves == [], "Must NOT queue a stop order when stop_price >= current_price"

    ex = exit_saves[0]
    assert ex.status == "pending_submit"
    assert ex.side == "sell"
    assert ex.quantity == 10
    assert ex.stop_price is None
    assert ":exit" in ex.client_order_id

    # Audit event must be emitted
    exit_audit = [
        e for e in audit_event_store.appended
        if e.event_type == "recovery_exit_queued_stop_above_market" and e.symbol == "ARLO"
    ]
    assert len(exit_audit) == 1, "Must emit recovery_exit_queued_stop_above_market audit event"
    assert exit_audit[0].payload["stop_price"] == 16.34
    assert abs(exit_audit[0].payload["current_price"] - 15.47) < 0.01


def test_startup_recovery_falls_back_to_stop_when_market_value_none() -> None:
    """When broker_position.market_value is None, current_price cannot be computed.
    Startup_recovery must fall back to the existing stop-queue path — the safe default
    preserves protection even when the Alpaca response omits market_value."""
    settings = make_settings()
    now = datetime(2026, 5, 8, 16, 0, tzinfo=timezone.utc)

    pos = PositionRecord(
        symbol="NVDA",
        trading_mode=settings.trading_mode,
        strategy_version=settings.strategy_version,
        strategy_name="bull_flag",
        quantity=5,
        entry_price=900.0,
        stop_price=880.0,
        initial_stop_price=880.0,
        opened_at=now,
        updated_at=now,
    )
    broker_position = BrokerPosition(
        symbol="NVDA",
        quantity=5,
        entry_price=900.0,
        market_value=None,   # market_value unavailable
    )
    position_store = RecordingPositionStore(existing_positions=[pos])
    order_store = RecordingOrderStore()
    runtime = make_runtime_context(
        settings,
        position_store=position_store,
        order_store=order_store,
    )

    recover_startup_state(
        settings=settings,
        runtime=runtime,
        broker_open_positions=[broker_position],
        broker_open_orders=[],
        now=now,
    )

    stop_saves = [
        o for o in order_store.saved
        if o.intent_type == "stop" and o.symbol == "NVDA"
    ]
    exit_saves = [
        o for o in order_store.saved
        if o.intent_type == "exit" and o.symbol == "NVDA"
    ]
    assert len(stop_saves) == 1, "Must queue stop when market_value is None (safe fallback)"
    assert exit_saves == [], "Must NOT queue exit when market_value is unavailable"


def test_startup_recovery_skips_symbol_with_active_exit() -> None:
    """When a pending_submit exit order already exists for a symbol (e.g. queued in a
    prior cycle but not yet dispatched), startup_recovery must not queue another exit
    or stop.  Without this guard, a second exit order would be submitted and the
    position could be double-sold."""
    settings = make_settings()
    now = datetime(2026, 5, 8, 16, 0, tzinfo=timezone.utc)

    pos = PositionRecord(
        symbol="ARLO",
        trading_mode=settings.trading_mode,
        strategy_version=settings.strategy_version,
        strategy_name="bull_flag",
        quantity=10,
        entry_price=16.27,
        stop_price=16.34,
        initial_stop_price=16.34,
        opened_at=now,
        updated_at=now,
    )
    existing_exit = OrderRecord(
        client_order_id=f"startup_recovery:{settings.strategy_version}:{now.date().isoformat()}:ARLO:exit",
        symbol="ARLO",
        side="sell",
        intent_type="exit",
        status="pending_submit",
        quantity=10,
        trading_mode=TradingMode.PAPER,
        strategy_version=settings.strategy_version,
        created_at=now,
        updated_at=now,
        stop_price=None,
        initial_stop_price=None,
        signal_timestamp=None,
        broker_order_id=None,
    )
    broker_position = BrokerPosition(
        symbol="ARLO",
        quantity=10,
        entry_price=16.27,
        market_value=154.70,   # would trigger exit branch if not already guarded
    )
    position_store = RecordingPositionStore(existing_positions=[pos])
    order_store = RecordingOrderStore(existing_orders=[existing_exit])
    runtime = make_runtime_context(
        settings,
        position_store=position_store,
        order_store=order_store,
    )

    recover_startup_state(
        settings=settings,
        runtime=runtime,
        broker_open_positions=[broker_position],
        broker_open_orders=[],
        now=now,
    )

    new_orders = [
        o for o in order_store.saved
        if o.symbol == "ARLO"
    ]
    assert new_orders == [], "Must not queue any new order when active exit already exists"


def test_startup_recovery_queues_stop_when_stop_below_market() -> None:
    """Regression guard: when stop_price is legitimately below the current market price,
    startup_recovery must queue the normal stop order, not an exit."""
    settings = make_settings()
    now = datetime(2026, 5, 8, 16, 0, tzinfo=timezone.utc)

    pos = PositionRecord(
        symbol="MSFT",
        trading_mode=settings.trading_mode,
        strategy_version=settings.strategy_version,
        strategy_name="bull_flag",
        quantity=5,
        entry_price=420.0,
        stop_price=410.0,   # well below market
        initial_stop_price=410.0,
        opened_at=now,
        updated_at=now,
    )
    broker_position = BrokerPosition(
        symbol="MSFT",
        quantity=5,
        entry_price=420.0,
        market_value=2150.0,   # 2150/5 = $430 current price; stop=$410 < $430 → valid
    )
    position_store = RecordingPositionStore(existing_positions=[pos])
    order_store = RecordingOrderStore()
    runtime = make_runtime_context(
        settings,
        position_store=position_store,
        order_store=order_store,
    )

    recover_startup_state(
        settings=settings,
        runtime=runtime,
        broker_open_positions=[broker_position],
        broker_open_orders=[],
        now=now,
    )

    stop_saves = [
        o for o in order_store.saved
        if o.intent_type == "stop" and o.symbol == "MSFT"
    ]
    exit_saves = [
        o for o in order_store.saved
        if o.intent_type == "exit" and o.symbol == "MSFT"
    ]
    assert len(stop_saves) == 1, "Must queue a stop when stop_price is below current market price"
    assert exit_saves == [], "Must NOT queue an exit when stop is valid"


def test_startup_recovery_queues_exit_when_stop_equals_market() -> None:
    """Edge: stop_price == current_price (boundary condition).
    Alpaca rejects sell stops at exactly the current price (42210000 requires strictly less),
    so the boundary must route to the exit path."""
    settings = make_settings()
    now = datetime(2026, 5, 8, 16, 0, tzinfo=timezone.utc)

    pos = PositionRecord(
        symbol="GME",
        trading_mode=settings.trading_mode,
        strategy_version=settings.strategy_version,
        strategy_name="bull_flag",
        quantity=10,
        entry_price=20.0,
        stop_price=18.50,   # exactly matches current price
        initial_stop_price=18.50,
        opened_at=now,
        updated_at=now,
    )
    broker_position = BrokerPosition(
        symbol="GME",
        quantity=10,
        entry_price=20.0,
        market_value=185.0,   # 185/10 = $18.50 exactly
    )
    position_store = RecordingPositionStore(existing_positions=[pos])
    order_store = RecordingOrderStore()
    runtime = make_runtime_context(
        settings,
        position_store=position_store,
        order_store=order_store,
    )

    recover_startup_state(
        settings=settings,
        runtime=runtime,
        broker_open_positions=[broker_position],
        broker_open_orders=[],
        now=now,
    )

    exit_saves = [
        o for o in order_store.saved
        if o.intent_type == "exit" and o.symbol == "GME"
    ]
    assert len(exit_saves) == 1, "stop_price == current_price must route to exit (Alpaca requires strictly less)"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /workspace/alpaca_bot
pytest tests/unit/test_startup_recovery.py::test_startup_recovery_queues_exit_when_stop_above_market tests/unit/test_startup_recovery.py::test_startup_recovery_falls_back_to_stop_when_market_value_none tests/unit/test_startup_recovery.py::test_startup_recovery_skips_symbol_with_active_exit tests/unit/test_startup_recovery.py::test_startup_recovery_queues_stop_when_stop_below_market tests/unit/test_startup_recovery.py::test_startup_recovery_queues_exit_when_stop_equals_market -v 2>&1 | tail -20
```

Expected: 5 FAILED (AssertionError — no exit queued / active_exit_symbols not built yet)

---

### Task 2: Implement startup_recovery fix

**Files:**
- Modify: `src/alpaca_bot/runtime/startup_recovery.py`

The second pass starts around line 352. The changes are:
1. Build `active_exit_symbols` set after `active_stop_symbols` (around line 273–274).
2. Add `active_exit_symbols` skip check before the stale-stop re-queue guard.
3. Compute `current_price` from `broker_positions_by_symbol` before queuing.
4. Branch: if `stop_price >= current_price` → write exit; else → write stop (existing path).

- [ ] **Step 1: Add `active_exit_symbols` after `active_stop_symbols`**

Find these lines (around line 273):
```python
    active_stop_symbols = {
        o.symbol for o in local_active_orders if o.intent_type == "stop"
    }
```

Replace with:
```python
    active_stop_symbols = {
        o.symbol for o in local_active_orders if o.intent_type == "stop"
    }
    active_exit_symbols = {
        o.symbol for o in local_active_orders if o.intent_type == "exit"
    }
```

- [ ] **Step 2: Add exit-symbol skip at the top of the second pass**

Find this block (around line 354):
```python
        for pos in synced_positions:
            if pos.symbol in active_stop_symbols:
                continue
            if pos.symbol in pending_entry_symbols:
```

Replace with:
```python
        for pos in synced_positions:
            if pos.symbol in active_stop_symbols:
                continue
            if pos.symbol in active_exit_symbols:
                continue
            if pos.symbol in pending_entry_symbols:
```

- [ ] **Step 3: Replace the stop-queue block with price-aware branching**

Find the existing stop-queue block starting at the `recovery_stop_id` assignment (around line 382):
```python
            recovery_stop_id = (
                f"startup_recovery:{settings.strategy_version}:"
                f"{timestamp.date().isoformat()}:{pos.symbol}:stop"
            )
            # Belt-and-suspenders: don't re-queue if a non-terminal stop already exists
            # for this exact recovery ID (prevents duplicate write on repeated cycles
            # before dispatch, and prevents duplicating the new_positions_needing_stop pass).
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
            active_stop_symbols.add(pos.symbol)
```

Replace the entire block with:
```python
            # Compute approximate current price from broker market_value (no extra API call).
            broker_pos = broker_positions_by_symbol.get(pos.symbol)
            current_price: float | None = None
            if broker_pos and broker_pos.market_value is not None and broker_pos.quantity > 0:
                current_price = broker_pos.market_value / broker_pos.quantity

            if current_price is not None and pos.stop_price >= current_price:
                # Stale stop: Alpaca 42210000 would reject it. Queue a market exit instead.
                recovery_exit_id = (
                    f"startup_recovery:{settings.strategy_version}:"
                    f"{timestamp.date().isoformat()}:{pos.symbol}:exit"
                )
                _log.warning(
                    "startup_recovery: position %s stop_price=%.4f >= current_price=%.4f — "
                    "queuing market exit instead of stale stop (qty=%d)",
                    pos.symbol,
                    pos.stop_price,
                    current_price,
                    pos.quantity,
                )
                runtime.order_store.save(
                    OrderRecord(
                        client_order_id=recovery_exit_id,
                        symbol=pos.symbol,
                        side="sell",
                        intent_type="exit",
                        status="pending_submit",
                        quantity=pos.quantity,
                        trading_mode=settings.trading_mode,
                        strategy_version=settings.strategy_version,
                        strategy_name=pos.strategy_name,
                        created_at=timestamp,
                        updated_at=timestamp,
                        stop_price=None,
                        initial_stop_price=None,
                        signal_timestamp=None,
                    ),
                    commit=False,
                )
                runtime.audit_event_store.append(
                    AuditEvent(
                        event_type="recovery_exit_queued_stop_above_market",
                        symbol=pos.symbol,
                        payload={
                            "client_order_id": recovery_exit_id,
                            "stop_price": pos.stop_price,
                            "current_price": current_price,
                            "quantity": pos.quantity,
                        },
                        created_at=timestamp,
                    ),
                    commit=False,
                )
                active_exit_symbols.add(pos.symbol)
            else:
                recovery_stop_id = (
                    f"startup_recovery:{settings.strategy_version}:"
                    f"{timestamp.date().isoformat()}:{pos.symbol}:stop"
                )
                # Belt-and-suspenders: don't re-queue if a non-terminal stop already exists
                # for this exact recovery ID (prevents duplicate write on repeated cycles
                # before dispatch, and prevents duplicating the new_positions_needing_stop pass).
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
                active_stop_symbols.add(pos.symbol)
```

- [ ] **Step 4: Run the 5 new tests**

```bash
cd /workspace/alpaca_bot
pytest tests/unit/test_startup_recovery.py::test_startup_recovery_queues_exit_when_stop_above_market tests/unit/test_startup_recovery.py::test_startup_recovery_falls_back_to_stop_when_market_value_none tests/unit/test_startup_recovery.py::test_startup_recovery_skips_symbol_with_active_exit tests/unit/test_startup_recovery.py::test_startup_recovery_queues_stop_when_stop_below_market tests/unit/test_startup_recovery.py::test_startup_recovery_queues_exit_when_stop_equals_market -v 2>&1 | tail -20
```

Expected: 5 PASSED

- [ ] **Step 5: Run the full startup_recovery test file**

```bash
cd /workspace/alpaca_bot
pytest tests/unit/test_startup_recovery.py -v 2>&1 | tail -20
```

Expected: all existing tests still PASS (no regressions)

- [ ] **Step 6: Commit**

```bash
git add src/alpaca_bot/runtime/startup_recovery.py tests/unit/test_startup_recovery.py
git commit -m "fix: queue market exit when recovery stop_price >= current market price

Breaks the startup_recovery infinite loop: when stop_price >= current_price
(Alpaca 42210000), queue a pending_submit exit instead of the stale stop.
Adds active_exit_symbols skip-set to prevent double-queuing across cycles."
```

---

### Task 3: Failing test and updated test for order_dispatch exit handling

**Files:**
- Modify: `tests/unit/test_order_dispatch.py`

Two changes:
1. Add `submit_market_exit` to `RecordingBroker` (class definition, around line 97).
2. Update `test_dispatch_unsupported_intent_type_sets_order_to_error_status` — currently tests that `intent_type="exit"` goes to error status. After the fix, exit orders will be dispatched successfully. The test must be updated to assert success.
3. Add a new test `test_dispatch_exit_order_calls_submit_market_exit`.

- [ ] **Step 1: Add `submit_market_exit` to `RecordingBroker`**

Find `RecordingBroker` (around line 97):
```python
class RecordingBroker:
    def __init__(self, *, cancel_raises: Exception | None = None) -> None:
        self.entry_calls: list[dict[str, object]] = []
        self.stop_calls: list[dict[str, object]] = []
        self.cancel_calls: list[str] = []
        self._cancel_raises = cancel_raises
```

Replace with:
```python
class RecordingBroker:
    def __init__(self, *, cancel_raises: Exception | None = None) -> None:
        self.entry_calls: list[dict[str, object]] = []
        self.stop_calls: list[dict[str, object]] = []
        self.exit_calls: list[dict[str, object]] = []
        self.cancel_calls: list[str] = []
        self._cancel_raises = cancel_raises
```

Then find:
```python
    def cancel_order(self, order_id: str) -> None:
        self.cancel_calls.append(order_id)
        if self._cancel_raises is not None:
            raise self._cancel_raises
```

And insert before it:
```python
    def submit_market_exit(self, **kwargs: object) -> SimpleNamespace:
        self.exit_calls.append(dict(kwargs))
        return SimpleNamespace(
            client_order_id=kwargs["client_order_id"],
            broker_order_id="broker-exit-1",
            symbol=kwargs["symbol"],
            side="sell",
            status="ACCEPTED",
            quantity=kwargs["quantity"],
        )

```

- [ ] **Step 2: Update the existing "unsupported intent type" test**

Find `test_dispatch_unsupported_intent_type_sets_order_to_error_status` (around line 778). The entire test currently expects `intent_type="exit"` to produce an error. After the fix, "exit" is a supported type. Replace the test body to use a genuinely unsupported type instead:

```python
def test_dispatch_unsupported_intent_type_sets_order_to_error_status() -> None:
    """An order with an unsupported intent_type (e.g. 'update_stop') that somehow reaches
    pending_submit must be marked as 'error' — not submitted — and must emit an
    order_dispatch_failed audit event."""
    _, dispatch_pending_orders = load_order_dispatch_api()
    settings = make_settings()
    now = datetime(2026, 4, 24, 19, 30, tzinfo=timezone.utc)

    rogue_order = OrderRecord(
        client_order_id="paper:v1-breakout:AAPL:update_stop:rogue",
        symbol="AAPL",
        side="sell",
        intent_type="update_stop",
        status="pending_submit",
        quantity=10,
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        created_at=now,
        updated_at=now,
        signal_timestamp=now,
    )
    order_store = RecordingOrderStore([rogue_order])
    audit_store = RecordingAuditEventStore()
    runtime = SimpleNamespace(
        order_store=order_store,
        audit_event_store=audit_store,
        connection=FakeConnection(),
    )

    report = dispatch_pending_orders(
        settings=settings,
        runtime=runtime,
        broker=RecordingBroker(),
        now=now,
    )

    assert report["submitted_count"] == 0
    # submitting stamp written first; error written after _submit_order raises for unsupported intent
    assert len(order_store.saved) == 2
    assert order_store.saved[0].status == "submitting"
    assert order_store.saved[1].status == "error"
    assert audit_store.appended[0].event_type == "order_dispatch_submitting"
    assert audit_store.appended[1].event_type == "order_dispatch_failed"
```

- [ ] **Step 3: Add the new dispatch exit test**

Append after the updated test:

```python
def test_dispatch_exit_order_calls_submit_market_exit() -> None:
    """A pending_submit exit order (queued by startup_recovery when stop_price >= market)
    must be dispatched via broker.submit_market_exit(), marked 'accepted', and counted
    in the report."""
    _, dispatch_pending_orders = load_order_dispatch_api()
    settings = make_settings()
    now = datetime(2026, 5, 8, 16, 0, tzinfo=timezone.utc)

    exit_order = OrderRecord(
        client_order_id="startup_recovery:v1-breakout:2026-05-08:ARLO:exit",
        symbol="ARLO",
        side="sell",
        intent_type="exit",
        status="pending_submit",
        quantity=10,
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        created_at=now,
        updated_at=now,
        stop_price=None,
        signal_timestamp=None,
    )
    order_store = RecordingOrderStore([exit_order])
    audit_store = RecordingAuditEventStore()
    broker = RecordingBroker()
    runtime = SimpleNamespace(
        order_store=order_store,
        audit_event_store=audit_store,
        connection=FakeConnection(),
    )

    report = dispatch_pending_orders(
        settings=settings,
        runtime=runtime,
        broker=broker,
        now=now,
    )

    assert report["submitted_count"] == 1
    assert len(broker.exit_calls) == 1
    call = broker.exit_calls[0]
    assert call["symbol"] == "ARLO"
    assert call["quantity"] == 10
    assert call["client_order_id"] == "startup_recovery:v1-breakout:2026-05-08:ARLO:exit"

    # Order must be saved as 'accepted' (or at minimum not 'error')
    final_status_saves = [o for o in order_store.saved if o.status not in {"submitting"}]
    assert final_status_saves, "Order must be updated after dispatch"
    assert final_status_saves[-1].status not in {"error", "pending_submit"}
```

- [ ] **Step 4: Run the new and updated tests to verify they fail**

```bash
cd /workspace/alpaca_bot
pytest tests/unit/test_order_dispatch.py::test_dispatch_exit_order_calls_submit_market_exit tests/unit/test_order_dispatch.py::test_dispatch_unsupported_intent_type_sets_order_to_error_status -v 2>&1 | tail -20
```

Expected:
- `test_dispatch_exit_order_calls_submit_market_exit` → FAILED (AttributeError or wrong status)
- `test_dispatch_unsupported_intent_type_sets_order_to_error_status` → PASSED (uses `update_stop`, still unsupported)

---

### Task 4: Implement order_dispatch exit handling

**Files:**
- Modify: `src/alpaca_bot/runtime/order_dispatch.py`

Two changes:
1. Add `submit_market_exit` to `BrokerProtocol` (around line 43).
2. Add exit dispatch branch in `_submit_order_to_broker` (around line 473).

- [ ] **Step 1: Add `submit_market_exit` to `BrokerProtocol`**

Find (around line 43):
```python
class BrokerProtocol(Protocol):
    def submit_stop_limit_entry(self, **kwargs) -> BrokerOrder: ...

    def submit_limit_entry(self, **kwargs) -> BrokerOrder: ...

    def submit_stop_order(self, **kwargs) -> BrokerOrder: ...

    def cancel_order(self, order_id: str) -> None: ...
```

Replace with:
```python
class BrokerProtocol(Protocol):
    def submit_stop_limit_entry(self, **kwargs) -> BrokerOrder: ...

    def submit_limit_entry(self, **kwargs) -> BrokerOrder: ...

    def submit_stop_order(self, **kwargs) -> BrokerOrder: ...

    def submit_market_exit(self, **kwargs) -> BrokerOrder: ...

    def cancel_order(self, order_id: str) -> None: ...
```

- [ ] **Step 2: Add exit branch in `_submit_order_to_broker`**

Find (around line 473):
```python
    if order.intent_type == "stop":
        return broker.submit_stop_order(
            symbol=order.symbol,
            quantity=order.quantity,
            stop_price=order.stop_price,
            client_order_id=order.client_order_id,
        )
    raise ValueError(f"Unsupported pending order intent_type: {order.intent_type}")
```

Replace with:
```python
    if order.intent_type == "stop":
        return broker.submit_stop_order(
            symbol=order.symbol,
            quantity=order.quantity,
            stop_price=order.stop_price,
            client_order_id=order.client_order_id,
        )
    if order.intent_type == "exit":
        return broker.submit_market_exit(
            symbol=order.symbol,
            quantity=order.quantity,
            client_order_id=order.client_order_id,
        )
    raise ValueError(f"Unsupported pending order intent_type: {order.intent_type}")
```

- [ ] **Step 3: Run the failing tests**

```bash
cd /workspace/alpaca_bot
pytest tests/unit/test_order_dispatch.py::test_dispatch_exit_order_calls_submit_market_exit tests/unit/test_order_dispatch.py::test_dispatch_unsupported_intent_type_sets_order_to_error_status -v 2>&1 | tail -20
```

Expected: both PASSED

- [ ] **Step 4: Run the full order_dispatch test suite**

```bash
cd /workspace/alpaca_bot
pytest tests/unit/test_order_dispatch.py tests/unit/test_order_dispatch_extended_hours.py -v 2>&1 | tail -30
```

Expected: all PASSED

- [ ] **Step 5: Run the full test suite**

```bash
cd /workspace/alpaca_bot
pytest 2>&1 | tail -20
```

Expected: all tests pass (look for 0 failures)

- [ ] **Step 6: Commit**

```bash
git add src/alpaca_bot/runtime/order_dispatch.py tests/unit/test_order_dispatch.py
git commit -m "feat: dispatch pending exit orders via submit_market_exit

Add exit branch to _submit_order_to_broker and submit_market_exit to
BrokerProtocol so recovery-queued exit orders are dispatched to the broker.
Update test for unsupported intent_type to use 'update_stop' (exit is now valid)."
```
