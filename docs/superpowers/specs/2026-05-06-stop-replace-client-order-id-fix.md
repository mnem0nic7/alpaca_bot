---
title: Fix Stop-Update client_order_id Uniqueness Failures
date: 2026-05-06
status: approved
---

# Fix Stop-Update client_order_id Uniqueness Failures

## Goal

Eliminate ~11 `stop_update_failed` audit events per cycle in production caused by two bugs
in `_execute_update_stop`:

1. **"client_order_id must be unique"** (10+ symbols) — `replace_order` passes
   `client_order_id=active_stop.client_order_id`, which conflicts with Alpaca's
   replace-order semantics.
2. **"insufficient qty available"** (CLOV) — a working stop already holds the full qty;
   the bot submits another stop on top of it; the error phrase is unrecognized so a
   `stop_update_failed` event is written every cycle.

Net effect: trailing stops (ATR and profit-trail) are not landing at the broker.

---

## Root Cause Analysis

### Bug 1: client_order_id reuse in replace_order (10+ symbols)

**Location:** `src/alpaca_bot/runtime/cycle_intent_execution.py`, Path 1 of
`_execute_update_stop` (~line 222–226).

**Mechanism:** Alpaca's replace-order API (`PATCH /v2/orders/{order_id}`) cancels the
original order and creates a new replacement order. When `client_order_id` is explicitly
included in the patch request body, Alpaca tries to assign that ID to the **new**
replacement order. The **original** order — now in "replaced" status — still holds that ID
in Alpaca's system. Two coexisting orders cannot share an ID → "client_order_id must be
unique."

**If not passed:** Alpaca automatically transfers the original `client_order_id` to the
replacement order. No conflict. Our DB record continues to use the same PK.

**Current code (buggy):**
```python
broker_order = broker.replace_order(
    order_id=active_stop.broker_order_id,
    stop_price=stop_price,
    client_order_id=active_stop.client_order_id,  # ← causes uniqueness violation
)
```

**Fix:**
```python
broker_order = broker.replace_order(
    order_id=active_stop.broker_order_id,
    stop_price=stop_price,
    # No client_order_id — Alpaca transfers the existing one automatically
)
```

### Bug 2: "insufficient qty available" not in skip-phrase list (CLOV)

**Location:** `src/alpaca_bot/runtime/cycle_intent_execution.py`, exception handler in
`_execute_update_stop` (~line 311).

**Mechanism:** A working stop already holds the full position qty at the broker
(`held_for_orders: 101`). The bot determines no active stop exists in Postgres (status
desync or reconciliation miss) and attempts to submit a new stop for the same qty. The
broker rejects: "insufficient qty available for order." This phrase is not in the
recognized skip-phrase list, so `stop_update_failed` is written to the audit table every
cycle. The position IS protected; the error is noise.

**Fix:** Add `"insufficient qty"` to the skip-phrase list. The position is already
protected; silently skip at DEBUG level.

### Bug 3: _replace_order_request SDK path passes client_order_id=None to Pydantic

**Location:** `src/alpaca_bot/execution/alpaca.py`, `_replace_order_request` (~line 927–932).

**Mechanism:** The fallback dict-based path already guards `if client_order_id is not None`.
The SDK path (`ReplaceOrderRequest`) receives `client_order_id=None` directly. Pydantic v2
includes fields set to `None` in the serialized request body as `"client_order_id": null`
unless `exclude_none=True` is applied. Sending `null` in the replace request body risks
Alpaca API rejecting or misinterpreting the field.

**Fix:** Apply the same conditional guard in the SDK path.

---

## Scope

Pure bug fix. Three code changes, three test changes. No schema migrations, no new env
vars, no new domain types, no Settings changes.

**Files:**
- Modify: `src/alpaca_bot/runtime/cycle_intent_execution.py`
- Modify: `src/alpaca_bot/execution/alpaca.py`
- Modify: `tests/unit/test_cycle_intent_execution.py`

---

## Section 1 — cycle_intent_execution.py

### Change A: Remove client_order_id from replace_order call

In `_execute_update_stop`, Path 1 (lines ~222–226):

```python
# Before
broker_order = broker.replace_order(
    order_id=active_stop.broker_order_id,
    stop_price=stop_price,
    client_order_id=active_stop.client_order_id,
)

# After
broker_order = broker.replace_order(
    order_id=active_stop.broker_order_id,
    stop_price=stop_price,
)
```

The `updated_order` construction below this call is unchanged — it correctly uses
`active_stop.client_order_id` (the same ID that Alpaca transferred to the replacement).

### Change B: Add "insufficient qty" to skip-phrase list

Exception handler (~line 311):

```python
# Before
if any(phrase in exc_msg for phrase in (
    "not found", "already filled", "already canceled", "does not exist",
    "has been filled", "is filled", "order is", "order was",
)):

# After
if any(phrase in exc_msg for phrase in (
    "not found", "already filled", "already canceled", "does not exist",
    "has been filled", "is filled", "order is", "order was",
    "insufficient qty",
)):
```

The `logger.debug` message on the next line already reads "order already gone" — leave the
log message as-is; "insufficient qty" semantically belongs to the same "skip, position is
protected" family.

---

## Section 2 — alpaca.py

### Change C: Guard client_order_id=None in SDK path

In `_replace_order_request` (~lines 927–932):

```python
# Before
return ReplaceOrderRequest(
    qty=quantity,
    limit_price=limit_price,
    stop_price=stop_price,
    client_order_id=client_order_id,
)

# After
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

No change to the fallback dict-based path (already correct).

---

## Section 3 — Trade Stream / DB Impact

No schema changes. After a replace without `client_order_id`:

- `active_stop.client_order_id` (DB primary key) stays unchanged.
- `broker_order.broker_order_id` is the new Alpaca-assigned replacement ID; the atomic
  write updates this column in-place.
- Trade stream handler (`_find_order`): tries `client_order_id` first → finds it. Falls
  back to `broker_order_id` if needed. Both paths remain correct.

---

## Section 4 — Tests

### Test change 1 (update existing): `test_execute_cycle_intents_replaces_active_stop_and_updates_position`

This test currently asserts the **buggy** behavior:
```python
assert broker.replace_calls == [
    {
        "order_id": "broker-stop-1",
        "stop_price": 111.7,
        "client_order_id": active_stop.client_order_id,  # ← remove this key
    }
]
```

Update to assert the fixed behavior (no `client_order_id` key):
```python
assert broker.replace_calls == [
    {
        "order_id": "broker-stop-1",
        "stop_price": 111.7,
    }
]
```

### Test change 2 (new): `test_replace_stop_does_not_pass_client_order_id`

Explicit regression test. Active stop with `broker_order_id` exists; UPDATE_STOP intent
fires; verify `replace_order` is called without `client_order_id`.

```python
def test_replace_stop_does_not_pass_client_order_id() -> None:
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

### Test change 3 (new): `test_replace_stop_insufficient_qty_silently_skipped`

Active stop with `broker_order_id` exists; `replace_order` raises "insufficient qty
available for order (requested: 25, available: 0, held_for_orders: 25)"; verify zero
`stop_update_failed` audit events and no exception propagated.

```python
def test_replace_stop_insufficient_qty_silently_skipped() -> None:
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
                "insufficient qty available for order (requested: 101, "
                "available: 0, held_for_orders: 101)"
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

---

## Non-Goals

- No change to capital deployment behavior (watchlist, fractional shares, risk params) —
  separate concern.
- No new `ACTIVE_STOP_STATUSES` entries.
- No DB migration.
- No reconciliation changes for the CLOV status desync root cause — that is a pre-existing
  concern handled by the reconciliation logic; the fix here prevents the audit spam while
  the position remains protected.
