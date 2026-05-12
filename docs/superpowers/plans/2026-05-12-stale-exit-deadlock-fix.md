# Stale Exit Deadlock Fix — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the `_execute_exit()` guard in `cycle_intent_execution.py` so that exit orders from a prior trading session (stale) are detected, canceled at the broker, and cleared in the DB before a fresh exit is submitted — preventing the permanent deadlock that kept OKLO/IRDM/RMBS open all day on 2026-05-12.

**Architecture:** One surgical change to `_execute_exit()` in `src/alpaca_bot/runtime/cycle_intent_execution.py`. The guard currently does a flat "any active exit → block" check. We replace it with a stale/fresh classification: if the existing exit order is from a prior ET session date (`status in ("new","held")` + `broker_order_id is not None` + prior-day date), cancel it and proceed; if it is fresh, block as before. No schema changes, no new env vars.

**Tech Stack:** Python 3.12, psycopg2 (Postgres), existing fake-callable test pattern.

---

## Files

- **Modify:** `src/alpaca_bot/runtime/cycle_intent_execution.py` — replace guard block in `_execute_exit()` (~lines 447–475)
- **Modify (tests):** `tests/unit/test_cycle_intent_execution.py` — add 6 new tests; existing tests are unaffected

---

## Task 1: Write failing tests for stale exit detection

**Files:**
- Test: `tests/unit/test_cycle_intent_execution.py`

### Background

`_execute_exit()` is invoked via `execute_cycle_intents()`. The existing
`RecordingBroker` in the test file has `submit_limit_exit` missing — add it
so tests that involve a `limit_price` can compile. (Existing tests all use
market exits so this won't break them.)

All test timestamps use UTC. `settings.market_timezone` defaults to
`America/New_York` (UTC−4 in April/May). To produce a "yesterday" signal
timestamp, use `datetime(2026, 4, 23, 23, 0, tzinfo=timezone.utc)` (April 23,
19:00 ET = prior calendar day in ET). "Today" = `datetime(2026, 4, 24, 14, 0,
tzinfo=timezone.utc)` (April 24, 10:00 ET).

### Step 1: Add `submit_limit_exit` to `RecordingBroker`

- [ ] Open `tests/unit/test_cycle_intent_execution.py`
- [ ] Find `class RecordingBroker` (around line 103). After `submit_market_exit`, add:

```python
    def submit_limit_exit(self, **kwargs):
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

### Step 2: Write the 6 new tests

- [ ] Append the following 6 test functions to the end of `tests/unit/test_cycle_intent_execution.py`:

```python
# ── Stale exit detection tests ────────────────────────────────────────────────

def test_stale_exit_detected_canceled_and_resubmitted() -> None:
    """A 'new' exit order from the prior trading session must be canceled at the
    broker, marked canceled in DB, and a fresh market exit submitted."""
    execute_cycle_intents = load_cycle_intent_execution_api()
    settings = make_settings()
    now = datetime(2026, 4, 24, 14, 0, tzinfo=timezone.utc)  # April 24, 10:00 ET

    stale_exit = OrderRecord(
        client_order_id="v1-breakout:2026-04-23:AAPL:exit:stale",
        symbol="AAPL",
        side="sell",
        intent_type="exit",
        status="new",
        quantity=25,
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        created_at=datetime(2026, 4, 23, 23, 0, tzinfo=timezone.utc),
        updated_at=datetime(2026, 4, 23, 23, 0, tzinfo=timezone.utc),
        initial_stop_price=109.89,
        broker_order_id="broker-stale-exit-1",
        signal_timestamp=datetime(2026, 4, 23, 23, 0, tzinfo=timezone.utc),
    )
    active_stop = OrderRecord(
        client_order_id="v1-breakout:2026-04-23:AAPL:stop:stale",
        symbol="AAPL",
        side="sell",
        intent_type="stop",
        status="accepted",
        quantity=25,
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        created_at=datetime(2026, 4, 23, 23, 0, tzinfo=timezone.utc),
        updated_at=datetime(2026, 4, 23, 23, 0, tzinfo=timezone.utc),
        stop_price=109.89,
        initial_stop_price=109.89,
        broker_order_id="broker-stop-1",
        signal_timestamp=datetime(2026, 4, 23, 23, 0, tzinfo=timezone.utc),
    )
    position = PositionRecord(
        symbol="AAPL",
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        quantity=25,
        entry_price=111.02,
        stop_price=109.89,
        initial_stop_price=109.89,
        opened_at=datetime(2026, 4, 23, 23, 0, tzinfo=timezone.utc),
        updated_at=datetime(2026, 4, 23, 23, 0, tzinfo=timezone.utc),
    )
    runtime = SimpleNamespace(
        order_store=RecordingOrderStore(orders=[stale_exit, active_stop]),
        position_store=RecordingPositionStore(positions=[position]),
        audit_event_store=RecordingAuditEventStore(),
        connection=FakeConnection(),
    )
    broker = RecordingBroker()

    report = execute_cycle_intents(
        settings=settings,
        runtime=runtime,
        broker=broker,
        cycle_result=CycleResult(
            as_of=now,
            intents=[CycleIntent(
                intent_type=CycleIntentType.EXIT,
                symbol="AAPL",
                timestamp=now,
                reason="eod_flatten",
            )],
        ),
        now=now,
    )

    assert report.submitted_exit_count == 1, "Fresh exit must be submitted after stale is cleared"
    # Stale exit canceled first, then active stop canceled, then market exit submitted
    assert "broker-stale-exit-1" in broker.cancel_calls, "Stale exit must be canceled at broker"
    assert "broker-stop-1" in broker.cancel_calls, "Active stop must also be canceled"
    assert broker.exit_calls, "Market exit must be submitted"

    stale_exit_saved = [
        o for o in runtime.order_store.saved
        if o.client_order_id == stale_exit.client_order_id
    ]
    assert stale_exit_saved, "Stale exit must be saved to DB"
    assert stale_exit_saved[0].status == "canceled"

    stale_cancel_events = [
        e for e in runtime.audit_event_store.appended
        if e.event_type == "stale_exit_canceled_for_resubmission"
    ]
    assert len(stale_cancel_events) == 1
    assert stale_cancel_events[0].payload["client_order_id"] == stale_exit.client_order_id
    assert stale_cancel_events[0].payload["broker_order_id"] == "broker-stale-exit-1"
    assert stale_cancel_events[0].payload["original_status"] == "new"

    skipped = [
        e for e in runtime.audit_event_store.appended
        if e.event_type == "cycle_intent_skipped"
    ]
    assert not skipped, "Must NOT emit cycle_intent_skipped when stale exit is cleared"


def test_fresh_exit_same_day_still_blocks() -> None:
    """A 'new' exit order created today (same ET session date) must still block
    resubmission — it is actively being processed, not stale."""
    execute_cycle_intents = load_cycle_intent_execution_api()
    settings = make_settings()
    now = datetime(2026, 4, 24, 19, 50, tzinfo=timezone.utc)  # April 24, 15:50 ET

    # created_at is also April 24 ET — same session, not stale
    fresh_exit = OrderRecord(
        client_order_id="v1-breakout:2026-04-24:AAPL:exit:fresh",
        symbol="AAPL",
        side="sell",
        intent_type="exit",
        status="new",
        quantity=25,
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        created_at=datetime(2026, 4, 24, 14, 0, tzinfo=timezone.utc),  # same day in ET
        updated_at=datetime(2026, 4, 24, 14, 0, tzinfo=timezone.utc),
        initial_stop_price=109.89,
        broker_order_id="broker-fresh-exit-1",
        signal_timestamp=datetime(2026, 4, 24, 14, 0, tzinfo=timezone.utc),
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
        order_store=RecordingOrderStore(orders=[fresh_exit]),
        position_store=RecordingPositionStore(positions=[position]),
        audit_event_store=RecordingAuditEventStore(),
        connection=FakeConnection(),
    )
    broker = RecordingBroker()

    report = execute_cycle_intents(
        settings=settings,
        runtime=runtime,
        broker=broker,
        cycle_result=CycleResult(
            as_of=now,
            intents=[CycleIntent(
                intent_type=CycleIntentType.EXIT,
                symbol="AAPL",
                timestamp=now,
                reason="eod_flatten",
            )],
        ),
        now=now,
    )

    assert report.submitted_exit_count == 0, "Same-day 'new' exit must still block"
    assert not broker.cancel_calls, "Must NOT cancel a fresh exit"
    skipped = [
        e for e in runtime.audit_event_store.appended
        if e.event_type == "cycle_intent_skipped"
    ]
    assert len(skipped) == 1
    assert skipped[0].payload["reason"] == "active_exit_order_exists"


def test_pending_submit_exit_blocks_resubmission() -> None:
    """A 'pending_submit' exit order (not yet dispatched to broker) must be treated
    as fresh and block resubmission — the dispatcher will handle it shortly."""
    execute_cycle_intents = load_cycle_intent_execution_api()
    settings = make_settings()
    now = datetime(2026, 4, 24, 14, 0, tzinfo=timezone.utc)

    pending_exit = OrderRecord(
        client_order_id="v1-breakout:2026-04-24:AAPL:exit:pending",
        symbol="AAPL",
        side="sell",
        intent_type="exit",
        status="pending_submit",
        quantity=25,
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        # created_at is yesterday — but it's pending_submit with no broker_order_id,
        # so it must NOT be classified as stale
        created_at=datetime(2026, 4, 23, 23, 0, tzinfo=timezone.utc),
        updated_at=datetime(2026, 4, 23, 23, 0, tzinfo=timezone.utc),
        initial_stop_price=109.89,
        broker_order_id=None,  # not yet submitted
        signal_timestamp=None,
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
        order_store=RecordingOrderStore(orders=[pending_exit]),
        position_store=RecordingPositionStore(positions=[position]),
        audit_event_store=RecordingAuditEventStore(),
        connection=FakeConnection(),
    )
    broker = RecordingBroker()

    report = execute_cycle_intents(
        settings=settings,
        runtime=runtime,
        broker=broker,
        cycle_result=CycleResult(
            as_of=now,
            intents=[CycleIntent(
                intent_type=CycleIntentType.EXIT,
                symbol="AAPL",
                timestamp=now,
                reason="eod_flatten",
            )],
        ),
        now=now,
    )

    assert report.submitted_exit_count == 0, "pending_submit exit must block (it will be dispatched)"
    assert not broker.cancel_calls
    skipped = [
        e for e in runtime.audit_event_store.appended
        if e.event_type == "cycle_intent_skipped"
    ]
    assert len(skipped) == 1
    assert skipped[0].payload["reason"] == "active_exit_order_exists"


def test_stale_and_fresh_exit_coexist_fresh_wins() -> None:
    """When both a stale exit and a fresh exit exist, the fresh exit takes priority
    and must block — do not attempt to cancel the stale one."""
    execute_cycle_intents = load_cycle_intent_execution_api()
    settings = make_settings()
    now = datetime(2026, 4, 24, 14, 0, tzinfo=timezone.utc)

    stale_exit = OrderRecord(
        client_order_id="v1-breakout:2026-04-23:AAPL:exit:stale",
        symbol="AAPL",
        side="sell",
        intent_type="exit",
        status="new",
        quantity=25,
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        created_at=datetime(2026, 4, 23, 23, 0, tzinfo=timezone.utc),
        updated_at=datetime(2026, 4, 23, 23, 0, tzinfo=timezone.utc),
        initial_stop_price=109.89,
        broker_order_id="broker-stale-exit-1",
        signal_timestamp=datetime(2026, 4, 23, 23, 0, tzinfo=timezone.utc),
    )
    fresh_exit = OrderRecord(
        client_order_id="v1-breakout:2026-04-24:AAPL:exit:pending",
        symbol="AAPL",
        side="sell",
        intent_type="exit",
        status="pending_submit",
        quantity=25,
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        created_at=datetime(2026, 4, 24, 13, 0, tzinfo=timezone.utc),
        updated_at=datetime(2026, 4, 24, 13, 0, tzinfo=timezone.utc),
        initial_stop_price=109.89,
        broker_order_id=None,
        signal_timestamp=None,
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
        order_store=RecordingOrderStore(orders=[stale_exit, fresh_exit]),
        position_store=RecordingPositionStore(positions=[position]),
        audit_event_store=RecordingAuditEventStore(),
        connection=FakeConnection(),
    )
    broker = RecordingBroker()

    report = execute_cycle_intents(
        settings=settings,
        runtime=runtime,
        broker=broker,
        cycle_result=CycleResult(
            as_of=now,
            intents=[CycleIntent(
                intent_type=CycleIntentType.EXIT,
                symbol="AAPL",
                timestamp=now,
                reason="eod_flatten",
            )],
        ),
        now=now,
    )

    assert report.submitted_exit_count == 0, "Fresh exit must block even when stale also exists"
    assert not broker.cancel_calls, "Must NOT cancel the stale exit when fresh exit is present"
    skipped = [
        e for e in runtime.audit_event_store.appended
        if e.event_type == "cycle_intent_skipped"
    ]
    assert len(skipped) == 1
    assert skipped[0].payload["reason"] == "active_exit_order_exists"


def test_stale_exit_cancel_fails_unrecognized_error_blocks() -> None:
    """If canceling the stale exit at the broker raises an unrecognized error,
    resubmission must be blocked (the stale order may still be live at the broker)."""
    execute_cycle_intents = load_cycle_intent_execution_api()
    settings = make_settings()
    now = datetime(2026, 4, 24, 14, 0, tzinfo=timezone.utc)

    stale_exit = OrderRecord(
        client_order_id="v1-breakout:2026-04-23:AAPL:exit:stale",
        symbol="AAPL",
        side="sell",
        intent_type="exit",
        status="new",
        quantity=25,
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        created_at=datetime(2026, 4, 23, 23, 0, tzinfo=timezone.utc),
        updated_at=datetime(2026, 4, 23, 23, 0, tzinfo=timezone.utc),
        initial_stop_price=109.89,
        broker_order_id="broker-stale-exit-1",
        signal_timestamp=datetime(2026, 4, 23, 23, 0, tzinfo=timezone.utc),
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
        order_store=RecordingOrderStore(orders=[stale_exit]),
        position_store=RecordingPositionStore(positions=[position]),
        audit_event_store=RecordingAuditEventStore(),
        connection=FakeConnection(),
    )
    broker = RecordingBroker(cancel_raises=RuntimeError("connection refused"))

    report = execute_cycle_intents(
        settings=settings,
        runtime=runtime,
        broker=broker,
        cycle_result=CycleResult(
            as_of=now,
            intents=[CycleIntent(
                intent_type=CycleIntentType.EXIT,
                symbol="AAPL",
                timestamp=now,
                reason="eod_flatten",
            )],
        ),
        now=now,
    )

    assert report.submitted_exit_count == 0, "Must block when stale exit cancel raised unrecognized error"
    assert broker.cancel_calls == ["broker-stale-exit-1"], "One cancel attempt must be made"
    assert not broker.exit_calls, "No exit must be submitted"
    skipped = [
        e for e in runtime.audit_event_store.appended
        if e.event_type == "cycle_intent_skipped"
    ]
    assert len(skipped) == 1
    assert skipped[0].payload["reason"] == "stale_exit_cancel_failed"
    assert skipped[0].payload["client_order_id"] == stale_exit.client_order_id


def test_stale_exit_cancel_fails_already_canceled_proceeds() -> None:
    """If the stale exit cancel raises 'already canceled', the broker has already
    removed it. Mark it canceled in DB and proceed with fresh exit submission."""
    execute_cycle_intents = load_cycle_intent_execution_api()
    settings = make_settings()
    now = datetime(2026, 4, 24, 14, 0, tzinfo=timezone.utc)

    stale_exit = OrderRecord(
        client_order_id="v1-breakout:2026-04-23:AAPL:exit:stale",
        symbol="AAPL",
        side="sell",
        intent_type="exit",
        status="new",
        quantity=25,
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        created_at=datetime(2026, 4, 23, 23, 0, tzinfo=timezone.utc),
        updated_at=datetime(2026, 4, 23, 23, 0, tzinfo=timezone.utc),
        initial_stop_price=109.89,
        broker_order_id="broker-stale-exit-1",
        signal_timestamp=datetime(2026, 4, 23, 23, 0, tzinfo=timezone.utc),
    )
    # No stop orders — focusing on the stale exit cancel behavior alone.
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
        order_store=RecordingOrderStore(orders=[stale_exit]),
        position_store=RecordingPositionStore(positions=[position]),
        audit_event_store=RecordingAuditEventStore(),
        connection=FakeConnection(),
    )
    broker = RecordingBroker(cancel_raises=Exception("already canceled"))

    report = execute_cycle_intents(
        settings=settings,
        runtime=runtime,
        broker=broker,
        cycle_result=CycleResult(
            as_of=now,
            intents=[CycleIntent(
                intent_type=CycleIntentType.EXIT,
                symbol="AAPL",
                timestamp=now,
                reason="eod_flatten",
            )],
        ),
        now=now,
    )

    assert report.submitted_exit_count == 1, "Exit must proceed when stale cancel returns 'already canceled'"
    stale_saved = [
        o for o in runtime.order_store.saved
        if o.client_order_id == stale_exit.client_order_id
    ]
    assert stale_saved and stale_saved[0].status == "canceled", (
        "Stale exit must be saved as canceled even when broker returns already-canceled"
    )
    assert broker.exit_calls, "Market exit must be submitted"
```

- [ ] Run the new tests to confirm they all fail (the guard change is not yet implemented):

```bash
cd /workspace/alpaca_bot && pytest tests/unit/test_cycle_intent_execution.py::test_stale_exit_detected_canceled_and_resubmitted tests/unit/test_cycle_intent_execution.py::test_fresh_exit_same_day_still_blocks tests/unit/test_cycle_intent_execution.py::test_pending_submit_exit_blocks_resubmission tests/unit/test_cycle_intent_execution.py::test_stale_and_fresh_exit_coexist_fresh_wins tests/unit/test_cycle_intent_execution.py::test_stale_exit_cancel_fails_unrecognized_error_blocks tests/unit/test_cycle_intent_execution.py::test_stale_exit_cancel_fails_already_canceled_proceeds -v 2>&1 | tail -20
```

Expected: some pass trivially (tests 2, 3, 4 — the guard still blocks), tests 1, 5, 6 FAIL because no stale detection exists yet.

---

## Task 2: Implement the staleness-aware guard in `_execute_exit()`

**Files:**
- Modify: `src/alpaca_bot/runtime/cycle_intent_execution.py:447-475`

### Step 1: Replace the guard block

- [ ] Open `src/alpaca_bot/runtime/cycle_intent_execution.py`
- [ ] Find the guard block that begins with the comment `# Guard against duplicate EXIT dispatch — read under lock.` (around line 447). It ends at the line `stop_orders = _active_stop_orders(runtime, settings, symbol, strategy_name=strategy_name)` (around line 475).
- [ ] Replace the entire block — from `# Guard against duplicate EXIT dispatch` through `stop_orders = _active_stop_orders(...)` — with the following:

```python
    # Guard against duplicate EXIT dispatch — read under lock.
    with lock_ctx:
        active_exit_orders = runtime.order_store.list_by_status(
            trading_mode=settings.trading_mode,
            strategy_version=settings.strategy_version,
            statuses=list(ACTIVE_STOP_STATUSES),
            strategy_name=strategy_name,
        )
        symbol_exit_orders = [
            o for o in active_exit_orders if o.symbol == symbol and o.intent_type == "exit"
        ]

    # Classify into stale (prior-session, submitted-but-unfilled) vs fresh (current session).
    stale_exits: list[OrderRecord] = []
    fresh_exits: list[OrderRecord] = []
    if symbol_exit_orders:
        session_date_et = now.astimezone(settings.market_timezone).date()
        for order in symbol_exit_orders:
            if order.status in ("new", "held") and order.broker_order_id is not None:
                ref_ts = order.signal_timestamp if order.signal_timestamp is not None else order.created_at
                if ref_ts.tzinfo is None:
                    ref_ts = ref_ts.replace(tzinfo=timezone.utc)
                if ref_ts.astimezone(settings.market_timezone).date() < session_date_et:
                    stale_exits.append(order)
                    continue
            fresh_exits.append(order)

    if fresh_exits:
        # A current-session exit is live — block to avoid double-sell.
        with lock_ctx:
            try:
                runtime.audit_event_store.append(
                    AuditEvent(
                        event_type="cycle_intent_skipped",
                        symbol=symbol,
                        payload={"intent_type": "exit", "reason": "active_exit_order_exists"},
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
        return 0, 0, 0

    # Cancel stale exits at broker outside the lock — they are from a prior session
    # and were never filled (e.g. paper-trading DAY limit orders placed after-hours).
    stale_canceled: list[tuple[OrderRecord, str]] = []
    for stale in stale_exits:
        if stale.broker_order_id:
            try:
                broker.cancel_order(stale.broker_order_id)
            except Exception as exc:
                exc_msg = str(exc).lower()
                if any(
                    phrase in exc_msg
                    for phrase in ("not found", "already canceled", "already filled", "does not exist")
                ):
                    logger.warning(
                        "cycle_intent_execution: stale exit %s already gone at broker: %s",
                        stale.client_order_id,
                        exc,
                    )
                else:
                    logger.exception(
                        "cycle_intent_execution: cancel_order failed with unrecognized error "
                        "for stale exit %s on %s; blocking resubmission to prevent double-sell",
                        stale.client_order_id,
                        symbol,
                    )
                    with lock_ctx:
                        try:
                            runtime.audit_event_store.append(
                                AuditEvent(
                                    event_type="cycle_intent_skipped",
                                    symbol=symbol,
                                    payload={
                                        "intent_type": "exit",
                                        "reason": "stale_exit_cancel_failed",
                                        "client_order_id": stale.client_order_id,
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
                    return 0, 0, 0
        stale_canceled.append((
            OrderRecord(
                client_order_id=stale.client_order_id,
                symbol=stale.symbol,
                side=stale.side,
                intent_type=stale.intent_type,
                status="canceled",
                quantity=stale.quantity,
                trading_mode=stale.trading_mode,
                strategy_version=stale.strategy_version,
                created_at=stale.created_at,
                updated_at=now,
                stop_price=stale.stop_price,
                limit_price=stale.limit_price,
                initial_stop_price=stale.initial_stop_price,
                broker_order_id=stale.broker_order_id,
                signal_timestamp=stale.signal_timestamp,
                strategy_name=stale.strategy_name,
            ),
            stale.status,
        ))

    # Persist stale exit cancellations and read active stop orders — all under one lock.
    with lock_ctx:
        try:
            for canceled_record, original_status in stale_canceled:
                runtime.order_store.save(canceled_record, commit=False)
                runtime.audit_event_store.append(
                    AuditEvent(
                        event_type="stale_exit_canceled_for_resubmission",
                        symbol=symbol,
                        payload={
                            "client_order_id": canceled_record.client_order_id,
                            "broker_order_id": canceled_record.broker_order_id,
                            "original_status": original_status,
                        },
                        created_at=now,
                    ),
                    commit=False,
                )
            stop_orders = _active_stop_orders(runtime, settings, symbol, strategy_name=strategy_name)
            if stale_canceled:
                runtime.connection.commit()
        except Exception:
            try:
                runtime.connection.rollback()
            except Exception:
                pass
            raise
```

### Step 2: Run the new tests — expect all 6 to pass

```bash
cd /workspace/alpaca_bot && pytest tests/unit/test_cycle_intent_execution.py::test_stale_exit_detected_canceled_and_resubmitted tests/unit/test_cycle_intent_execution.py::test_fresh_exit_same_day_still_blocks tests/unit/test_cycle_intent_execution.py::test_pending_submit_exit_blocks_resubmission tests/unit/test_cycle_intent_execution.py::test_stale_and_fresh_exit_coexist_fresh_wins tests/unit/test_cycle_intent_execution.py::test_stale_exit_cancel_fails_unrecognized_error_blocks tests/unit/test_cycle_intent_execution.py::test_stale_exit_cancel_fails_already_canceled_proceeds -v 2>&1 | tail -20
```

Expected: all 6 PASS

### Step 3: Run the full test file — confirm no regressions

```bash
cd /workspace/alpaca_bot && pytest tests/unit/test_cycle_intent_execution.py -v 2>&1 | tail -30
```

Expected: all tests PASS (the existing `test_execute_exit_skipped_when_active_exit_order_exists` uses `status="accepted"` which is always fresh → still blocks unchanged).

### Step 4: Run the full suite

```bash
cd /workspace/alpaca_bot && pytest 2>&1 | tail -20
```

Expected: all tests PASS.

### Step 5: Commit

```bash
cd /workspace/alpaca_bot && git add src/alpaca_bot/runtime/cycle_intent_execution.py tests/unit/test_cycle_intent_execution.py && git commit -m "fix: detect and cancel stale exit orders before resubmission in _execute_exit()

Prior-session exit orders with status='new' and a broker_order_id were blocking
all future exit attempts for the same symbol indefinitely. Paper-trading DAY
limit orders placed after-hours are never filled or canceled by Alpaca at the
next session open, causing a permanent deadlock.

The guard now classifies exit orders as stale (prior ET session date, submitted
to broker, status new/held) vs fresh (current session or pending_submit). Stale
exits are canceled at the broker and marked canceled in DB before proceeding
with a fresh exit submission.

Fixes the 2026-05-12 incident where OKLO/IRDM/RMBS remained open all day."
```

---

## Self-Review Checklist

- [x] **Spec coverage:** All spec requirements covered:
  - Stale = `status in ("new","held")` + `broker_order_id is not None` + prior ET date ✓
  - Fresh blocks (existing behavior preserved) ✓
  - Only stale → cancel → mark canceled → proceed ✓
  - Unrecognized cancel error → block with `stale_exit_cancel_failed` ✓
  - Known-gone cancel error → proceed ✓
  - `stale_exit_canceled_for_resubmission` audit event with correct payload ✓
  - `original_status` captured from `stale.status` (before creating canceled record) ✓

- [x] **Placeholder scan:** No TBDs, TODOs, or incomplete steps.

- [x] **Type consistency:**
  - `stale_canceled: list[tuple[OrderRecord, str]]` used consistently
  - `stop_orders` always defined before the stop-cancel loop that follows
  - `session_date_et` only computed when `symbol_exit_orders` is non-empty

- [x] **Safety:** `pending_submit` exits with prior-day `created_at` but no `broker_order_id` correctly route to `fresh_exits` (test 3 verifies this).

- [x] **Rollback path:** The `with lock_ctx:` block for stale persist wraps a `try/except` with `connection.rollback()` — same pattern as every other DB write in the file.
