# Update-Stop Partial-Fill Cancel Gap — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a partial-fill entry cancel guard to `_execute_update_stop`'s `else` branch so a missing stop order can be submitted without hitting Alpaca error 40310000.

**Architecture:** Two-file change. (1) Add a `context: str` parameter to `_cancel_partial_fill_entry` in `cycle_intent_execution.py` so audit events identify the call site; update the existing exit-path caller to pass `context="exit"` explicitly; add a new call with `context="update_stop"` before `broker.submit_stop_order()` in the `else` branch of `_execute_update_stop`. (2) One new test in `test_cycle_intent_execution.py` verifying cancel fires before stop submission and audit event carries correct context.

**Tech Stack:** Python, pytest, existing `_cancel_partial_fill_entry` in `cycle_intent_execution.py`.

---

### Task 1: Add failing test

**Files:**
- Modify: `tests/unit/test_cycle_intent_execution.py` — add one test after `test_execute_exit_cancels_partial_fill_entry_before_market_exit`

- [ ] **Step 1: Write the failing test**

Add this test at the end of `tests/unit/test_cycle_intent_execution.py`:

```python
def test_execute_update_stop_cancels_partial_fill_entry_before_submitting_new_stop() -> None:
    """_execute_update_stop (else branch: no active stop) must cancel any partially-filled
    entry before submitting a new stop to prevent Alpaca error 40310000."""
    execute_cycle_intents = load_cycle_intent_execution_api()
    settings = make_settings()
    now = datetime(2026, 5, 5, 14, 0, tzinfo=timezone.utc)

    partial_entry = OrderRecord(
        client_order_id="paper:v1-breakout:QQQ:entry:1",
        symbol="QQQ",
        side="buy",
        intent_type="entry",
        status="partially_filled",
        quantity=4,
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        created_at=now,
        updated_at=now,
        stop_price=490.00,
        limit_price=495.00,
        broker_order_id="broker-entry-qqq-1",
        signal_timestamp=now,
        strategy_name="breakout",
    )
    position = PositionRecord(
        symbol="QQQ",
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        quantity=4,
        entry_price=495.00,
        stop_price=490.00,
        initial_stop_price=490.00,
        opened_at=now,
    )
    audit_store = RecordingAuditEventStore()
    runtime = SimpleNamespace(
        order_store=RecordingOrderStore(orders=[partial_entry]),
        position_store=RecordingPositionStore(positions=[position]),
        audit_event_store=audit_store,
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
                symbol="QQQ",
                timestamp=now,
                stop_price=496.00,  # higher than position.stop_price=490.00 → triggers update
            )],
        ),
        now=now,
    )

    # Partial-fill entry was canceled before stop was submitted.
    assert "broker-entry-qqq-1" in broker.cancel_calls
    assert len(broker.stop_calls) == 1
    assert broker.stop_calls[0]["symbol"] == "QQQ"

    # Audit trail: partial_fill_entry_canceled with context="update_stop"
    event_types = [e.event_type for e in audit_store.appended]
    assert "partial_fill_entry_canceled" in event_types
    canceled_event = next(
        e for e in audit_store.appended if e.event_type == "partial_fill_entry_canceled"
    )
    assert canceled_event.payload["context"] == "update_stop"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/unit/test_cycle_intent_execution.py::test_execute_update_stop_cancels_partial_fill_entry_before_submitting_new_stop -v
```

Expected: FAIL — `AssertionError: assert "broker-entry-qqq-1" in []` (cancel_calls is empty because `_cancel_partial_fill_entry` is not called in the `else` branch yet).

- [ ] **Step 3: Commit failing test**

```bash
git add tests/unit/test_cycle_intent_execution.py
git commit -m "test: add failing test for update_stop partial-fill cancel guard"
```

---

### Task 2: Implement the fix

**Files:**
- Modify: `src/alpaca_bot/runtime/cycle_intent_execution.py`
  - `_cancel_partial_fill_entry` (line ~892): add `context: str` parameter
  - Exit-path call (line ~635): add `context="exit"`
  - `_execute_update_stop` else branch (line ~268): add cancel call before `submit_stop_order`

- [ ] **Step 4: Add `context` parameter to `_cancel_partial_fill_entry`**

Find `_cancel_partial_fill_entry` (around line 892). Change the function signature and replace both `"context": "exit"` strings in the body with `"context": context`.

Current signature:
```python
def _cancel_partial_fill_entry(
    *,
    symbol: str,
    strategy_name: str,
    runtime: RuntimeProtocol,
    broker: BrokerProtocol,
    settings: "Settings",
    now: datetime,
    lock_ctx: Any,
) -> None:
```

New signature (add `context: str` as the last keyword-only param):
```python
def _cancel_partial_fill_entry(
    *,
    symbol: str,
    strategy_name: str,
    runtime: RuntimeProtocol,
    broker: BrokerProtocol,
    settings: "Settings",
    now: datetime,
    lock_ctx: Any,
    context: str,
) -> None:
```

In the function body there are two audit event payload dicts with `"context": "exit"` hardcoded. Replace both with `"context": context`:

First occurrence (in the `partial_fill_cancel_failed` event, around line 940):
```python
                        runtime.audit_event_store.append(
                            AuditEvent(
                                event_type="partial_fill_cancel_failed",
                                symbol=symbol,
                                payload={
                                    "entry_client_order_id": entry.client_order_id,
                                    "entry_broker_order_id": entry.broker_order_id,
                                    "error": str(exc),
                                    "context": context,
                                },
                                created_at=now,
                            ),
                            commit=True,
                        )
```

Second occurrence (in the `partial_fill_entry_canceled` event, around line 975):
```python
                runtime.audit_event_store.append(
                    AuditEvent(
                        event_type="partial_fill_entry_canceled",
                        symbol=symbol,
                        payload={
                            "entry_client_order_id": entry.client_order_id,
                            "entry_broker_order_id": entry.broker_order_id,
                            "context": context,
                        },
                        created_at=now,
                    ),
                    commit=False,
                )
```

- [ ] **Step 5: Update the exit-path caller to pass `context="exit"`**

Find the existing `_cancel_partial_fill_entry` call around line 635 (inside `_execute_exit`). Add `context="exit"`:

```python
    _cancel_partial_fill_entry(
        symbol=symbol,
        strategy_name=strategy_name,
        runtime=runtime,
        broker=broker,
        settings=settings,
        now=now,
        lock_ctx=lock_ctx,
        context="exit",
    )
```

- [ ] **Step 6: Add cancel call in `_execute_update_stop`'s `else` branch**

Find the `else` branch inside `_execute_update_stop` (around line 268). Add the `_cancel_partial_fill_entry` call immediately before `broker.submit_stop_order(...)`:

```python
        else:
            client_order_id = _stop_client_order_id(
                settings=settings,
                symbol=symbol,
                timestamp=intent_timestamp,
                strategy_name=strategy_name,
            )
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

- [ ] **Step 7: Run the new test to verify it passes**

```bash
pytest tests/unit/test_cycle_intent_execution.py::test_execute_update_stop_cancels_partial_fill_entry_before_submitting_new_stop -v
```

Expected: PASS.

- [ ] **Step 8: Run all partial-fill related tests to check no regressions**

```bash
pytest tests/unit/test_cycle_intent_execution.py -v -k "partial_fill"
```

Expected: all PASS (including `test_execute_exit_cancels_partial_fill_entry_before_market_exit` which exercises the exit-path call now updated with `context="exit"`).

- [ ] **Step 9: Run full test suite**

```bash
pytest tests/unit/ -q
```

Expected: all tests pass.

- [ ] **Step 10: Commit**

```bash
git add src/alpaca_bot/runtime/cycle_intent_execution.py tests/unit/test_cycle_intent_execution.py
git commit -m "fix: cancel partial-fill entry in update_stop else branch to prevent 40310000"
```
