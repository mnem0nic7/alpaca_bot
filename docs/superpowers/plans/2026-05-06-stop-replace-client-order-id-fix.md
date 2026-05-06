# Stop-Update client_order_id Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminate ~11 `stop_update_failed` audit events per cycle caused by `replace_order` reusing the old `client_order_id` and "insufficient qty" not being a recognized skip phrase.

**Architecture:** Three focused code changes: (1) remove `client_order_id` from `replace_order` calls so Alpaca automatically transfers the existing ID to the replacement order, (2) add "insufficient qty" to the exception skip-phrase list, (3) fix the Alpaca SDK path in `_replace_order_request` to not send `client_order_id=null` in the request body. Tests come first.

**Tech Stack:** Python, pytest, psycopg2, Alpaca Trading SDK (alpaca-py).

---

## Files

| File | Change |
|---|---|
| `tests/unit/test_cycle_intent_execution.py` | Fix `RecordingBroker.replace_order` fake; update one existing test assertion; add two new tests |
| `src/alpaca_bot/runtime/cycle_intent_execution.py` | Remove `client_order_id` from `replace_order` call; add "insufficient qty" to skip phrases |
| `src/alpaca_bot/execution/alpaca.py` | Fix `_replace_order_request` SDK path to exclude `client_order_id` when `None` |

---

## Background: Why the Bug Exists

The `_execute_update_stop` function in `cycle_intent_execution.py` calls `broker.replace_order(...)` to move a stop to a new price. It currently passes `client_order_id=active_stop.client_order_id` (the existing stop's ID). Alpaca's replace-order API cancels the old order and creates a **new** replacement order. When the patch request includes `client_order_id`, Alpaca tries to assign that ID to the **new** order — but the old order (now "replaced" status) still holds that ID in Alpaca's system. Two concurrent orders cannot share an ID → "client_order_id must be unique." Fix: don't pass `client_order_id` at all; Alpaca transfers it automatically.

The "insufficient qty available" error means a working stop is already holding the full position qty at the broker. The position is protected. This phrase isn't in the recognized skip list, so `stop_update_failed` is written every cycle. Fix: add the phrase to the skip list.

---

## Task 1: Fix replace_order — no client_order_id in replace calls

**Files:**
- Modify: `tests/unit/test_cycle_intent_execution.py:111-120` (RecordingBroker.replace_order)
- Modify: `tests/unit/test_cycle_intent_execution.py:214-220` (update existing test assertion)
- Modify: `tests/unit/test_cycle_intent_execution.py` (add new regression test, end of file)
- Modify: `src/alpaca_bot/runtime/cycle_intent_execution.py:222-226`

- [ ] **Step 1: Fix RecordingBroker.replace_order — use kwargs.get() instead of kwargs[]**

`RecordingBroker.replace_order` (lines 111–120) currently does `client_order_id=kwargs["client_order_id"]`. After the production fix, `client_order_id` won't be in kwargs → `KeyError`. Fix the fake first so the test setup doesn't crash.

In `tests/unit/test_cycle_intent_execution.py`, locate `RecordingBroker.replace_order` at ~line 111 and change:

```python
    def replace_order(self, **kwargs):
        self.replace_calls.append(dict(kwargs))
        return SimpleNamespace(
            client_order_id=kwargs.get("client_order_id", ""),
            broker_order_id=kwargs["order_id"],
            symbol="AAPL",
            side="sell",
            status="ACCEPTED",
            quantity=25,
        )
```

- [ ] **Step 2: Update the existing test assertion to expect no client_order_id**

`test_execute_cycle_intents_replaces_active_stop_and_updates_position` (line 156) currently asserts:

```python
    assert broker.replace_calls == [
        {
            "order_id": "broker-stop-1",
            "stop_price": 111.7,
            "client_order_id": active_stop.client_order_id,
        }
    ]
```

Change to:

```python
    assert broker.replace_calls == [
        {
            "order_id": "broker-stop-1",
            "stop_price": 111.7,
        }
    ]
```

- [ ] **Step 3: Add new regression test at end of test file**

Append after line 2820 in `tests/unit/test_cycle_intent_execution.py`:

```python

# ---------------------------------------------------------------------------
# Regression: replace_order must NOT pass client_order_id
# ---------------------------------------------------------------------------

def test_replace_stop_does_not_pass_client_order_id() -> None:
    """replace_order must never include client_order_id in kwargs.

    Passing the old ID causes Alpaca to reject with 'client_order_id must be
    unique' because the old order (now 'replaced' status) still holds that ID.
    Omitting it tells Alpaca to transfer the original ID automatically.
    """
    execute_cycle_intents = load_cycle_intent_execution_api()
    settings = make_settings()
    now = datetime(2026, 4, 24, 19, 30, tzinfo=timezone.utc)
    active_stop = OrderRecord(
        client_order_id="v1-breakout:2026-04-24:AAPL:stop:2026-04-24T19:00:00+00:00",
        symbol="AAPL",
        side="sell",
        intent_type="stop",
        status="accepted",
        quantity=25,
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        created_at=now,
        updated_at=now,
        stop_price=109.89,
        initial_stop_price=109.89,
        broker_order_id="broker-stop-1",
        signal_timestamp=now,
    )
    position = PositionRecord(
        symbol="AAPL",
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        quantity=25,
        entry_price=111.02,
        stop_price=109.89,
        initial_stop_price=109.89,
        opened_at=now,
        updated_at=now,
    )
    runtime = SimpleNamespace(
        order_store=RecordingOrderStore(orders=[active_stop]),
        position_store=RecordingPositionStore(positions=[position]),
        audit_event_store=RecordingAuditEventStore(),
        connection=FakeConnection(),
    )
    broker = RecordingBroker()

    execute_cycle_intents(
        settings=settings,
        runtime=runtime,
        broker=broker,
        cycle_result=CycleResult(
            as_of=now,
            intents=[CycleIntent(
                intent_type=CycleIntentType.UPDATE_STOP,
                symbol="AAPL",
                timestamp=now,
                stop_price=111.7,
            )],
        ),
        now=now,
    )

    assert len(broker.replace_calls) == 1
    call = broker.replace_calls[0]
    assert call["order_id"] == "broker-stop-1"
    assert call["stop_price"] == 111.7
    assert "client_order_id" not in call, (
        "replace_order must NOT pass client_order_id — "
        "Alpaca transfers the original ID automatically; "
        "passing the old ID triggers 'client_order_id must be unique'"
    )
```

- [ ] **Step 4: Run the two affected tests to confirm red**

```bash
pytest tests/unit/test_cycle_intent_execution.py::test_execute_cycle_intents_replaces_active_stop_and_updates_position tests/unit/test_cycle_intent_execution.py::test_replace_stop_does_not_pass_client_order_id -v
```

Expected: **2 FAILED** — the existing test fails because `client_order_id` is still in `replace_calls`; the new regression test also fails for the same reason.

- [ ] **Step 5: Fix the production code — remove client_order_id from replace_order call**

In `src/alpaca_bot/runtime/cycle_intent_execution.py`, locate the `broker.replace_order(...)` call in `_execute_update_stop` (lines ~222–226). Change:

```python
            broker_order = broker.replace_order(
                order_id=active_stop.broker_order_id,
                stop_price=stop_price,
                client_order_id=active_stop.client_order_id,
            )
```

To:

```python
            broker_order = broker.replace_order(
                order_id=active_stop.broker_order_id,
                stop_price=stop_price,
            )
```

The `updated_order` construction below this line is unchanged — it already uses `active_stop.client_order_id` (the same ID that Alpaca transferred to the replacement order), not `broker_order.client_order_id`.

- [ ] **Step 6: Run the two tests to confirm green**

```bash
pytest tests/unit/test_cycle_intent_execution.py::test_execute_cycle_intents_replaces_active_stop_and_updates_position tests/unit/test_cycle_intent_execution.py::test_replace_stop_does_not_pass_client_order_id -v
```

Expected: **2 PASSED**.

- [ ] **Step 7: Run the full test suite**

```bash
pytest tests/unit/test_cycle_intent_execution.py -v
```

Expected: all tests PASS. If any test fails with `KeyError: 'client_order_id'`, that test has its own `replace_order` override that also reads `kwargs["client_order_id"]` — update that override to use `kwargs.get("client_order_id", "")`.

- [ ] **Step 8: Commit**

```bash
git add tests/unit/test_cycle_intent_execution.py src/alpaca_bot/runtime/cycle_intent_execution.py
git commit -m "fix: remove client_order_id from replace_order call to fix uniqueness error"
```

---

## Task 2: Add "insufficient qty" to skip-phrase list

**Files:**
- Modify: `tests/unit/test_cycle_intent_execution.py` (add new test, end of file)
- Modify: `src/alpaca_bot/runtime/cycle_intent_execution.py:311`

- [ ] **Step 1: Add new test at end of test file**

Append after the Task 1 regression test in `tests/unit/test_cycle_intent_execution.py`:

```python

# ---------------------------------------------------------------------------
# Regression: "insufficient qty" must be silently skipped
# ---------------------------------------------------------------------------

def test_replace_stop_insufficient_qty_silently_skipped() -> None:
    """'insufficient qty available' means a working stop already holds the full
    position qty at the broker. Position is protected. Must NOT write
    stop_update_failed or raise — skip at debug level only.

    Production scenario: CLOV had 'insufficient qty available for order
    (requested: 101, available: 0, held_for_orders: 101)' every cycle for 4+ hours.
    """
    execute_cycle_intents = load_cycle_intent_execution_api()
    settings = make_settings()
    now = datetime(2026, 4, 24, 19, 30, tzinfo=timezone.utc)

    active_stop = OrderRecord(
        client_order_id="v1-breakout:2026-04-24:CLOV:stop:2026-04-24T14:30:00+00:00",
        symbol="CLOV",
        side="sell",
        intent_type="stop",
        status="accepted",
        quantity=101,
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        created_at=now,
        updated_at=now,
        stop_price=2.20,
        initial_stop_price=2.20,
        broker_order_id="broker-stop-clov",
        signal_timestamp=now,
    )
    position = PositionRecord(
        symbol="CLOV",
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        quantity=101,
        entry_price=2.63,
        stop_price=2.20,
        initial_stop_price=2.20,
        opened_at=now,
        updated_at=now,
    )

    class InsufficientQtyBroker(RecordingBroker):
        def replace_order(self, **kwargs):
            raise RuntimeError(
                "insufficient qty available for order "
                "(requested: 101, available: 0, held_for_orders: 101)"
            )

    audit_store = RecordingAuditEventStore()
    runtime = SimpleNamespace(
        order_store=RecordingOrderStore(orders=[active_stop]),
        position_store=RecordingPositionStore(positions=[position]),
        audit_event_store=audit_store,
        connection=FakeConnection(),
    )

    report = execute_cycle_intents(
        settings=settings,
        runtime=runtime,
        broker=InsufficientQtyBroker(),
        cycle_result=CycleResult(
            as_of=now,
            intents=[CycleIntent(
                intent_type=CycleIntentType.UPDATE_STOP,
                symbol="CLOV",
                timestamp=now,
                stop_price=2.50,
            )],
        ),
        now=now,
    )

    stop_failed_events = [
        e for e in audit_store.appended if e.event_type == "stop_update_failed"
    ]
    assert stop_failed_events == [], (
        "insufficient qty means a working stop already holds the full qty; "
        "position is protected; must not write stop_update_failed"
    )
    assert report.replaced_stop_count == 0
```

- [ ] **Step 2: Run the new test to confirm red**

```bash
pytest tests/unit/test_cycle_intent_execution.py::test_replace_stop_insufficient_qty_silently_skipped -v
```

Expected: **FAILED** — `stop_update_failed` is written because "insufficient qty" is not yet in the skip list.

- [ ] **Step 3: Add "insufficient qty" to the skip-phrase list**

In `src/alpaca_bot/runtime/cycle_intent_execution.py`, locate the exception handler in `_execute_update_stop` at ~line 311:

```python
        if any(phrase in exc_msg for phrase in ("not found", "already filled", "already canceled", "does not exist", "has been filled", "is filled", "order is", "order was")):
```

Change to:

```python
        if any(phrase in exc_msg for phrase in ("not found", "already filled", "already canceled", "does not exist", "has been filled", "is filled", "order is", "order was", "insufficient qty")):
```

- [ ] **Step 4: Run the new test to confirm green**

```bash
pytest tests/unit/test_cycle_intent_execution.py::test_replace_stop_insufficient_qty_silently_skipped -v
```

Expected: **PASSED**.

- [ ] **Step 5: Run the full test suite**

```bash
pytest tests/unit/test_cycle_intent_execution.py -v
```

Expected: all tests PASS.

- [ ] **Step 6: Commit**

```bash
git add tests/unit/test_cycle_intent_execution.py src/alpaca_bot/runtime/cycle_intent_execution.py
git commit -m "fix: treat 'insufficient qty' as protected-stop skip in update_stop handler"
```

---

## Task 3: Fix _replace_order_request SDK path

**Files:**
- Modify: `src/alpaca_bot/execution/alpaca.py:927-932`

The fake broker in unit tests bypasses the Alpaca SDK entirely, so there is no
TDD-runnable test for this change. It's a defensive fix to ensure the SDK path is
consistent with the already-correct fallback dict path.

- [ ] **Step 1: Fix the SDK path in _replace_order_request**

In `src/alpaca_bot/execution/alpaca.py`, locate `_replace_order_request` at ~line 927–932.
The current SDK path is:

```python
    return ReplaceOrderRequest(
        qty=quantity,
        limit_price=limit_price,
        stop_price=stop_price,
        client_order_id=client_order_id,
    )
```

Replace with:

```python
    _req_kwargs: dict[str, Any] = {}
    if quantity is not None:
        _req_kwargs["qty"] = quantity
    if limit_price is not None:
        _req_kwargs["limit_price"] = limit_price
    if stop_price is not None:
        _req_kwargs["stop_price"] = stop_price
    if client_order_id is not None:
        _req_kwargs["client_order_id"] = client_order_id
    return ReplaceOrderRequest(**_req_kwargs)
```

This makes the SDK path consistent with the fallback dict path above it (which already
guards all four fields with `if ... is not None`).

- [ ] **Step 2: Run the full test suite**

```bash
pytest -x
```

Expected: all tests PASS.

- [ ] **Step 3: Commit**

```bash
git add src/alpaca_bot/execution/alpaca.py
git commit -m "fix: exclude None fields from ReplaceOrderRequest to match fallback path"
```

---

## Verification

After all three tasks, verify the full suite passes:

```bash
pytest
```

Expected: all existing tests pass + 2 new tests pass.

**Production signal to watch:** After deploying, the `stop_update_failed` count in the audit log should drop from ~11/cycle to 0. Check with:

```sql
SELECT COUNT(*), MAX(created_at)
FROM audit_events
WHERE event_type = 'stop_update_failed'
  AND created_at > NOW() - INTERVAL '10 minutes';
```
