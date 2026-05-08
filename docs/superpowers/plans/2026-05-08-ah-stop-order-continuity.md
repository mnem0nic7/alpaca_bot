# AH Stop Order Continuity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix two post-fix gaps in after-hours stop order handling: (1) AH-deferred pending stops are incorrectly expired by the stale-stop check when regular session opens the next day, and (2) the AH/PM stop deferral path emits no audit event, making it invisible in the audit trail.

**Architecture:** Both fixes live entirely in `src/alpaca_bot/runtime/order_dispatch.py`. Bug 3 adds a `broker_order_id is not None` guard inside the existing stale-stop expiration block so that never-submitted stops pass through to dispatch instead of being expired. Gap 4 adds an `AuditEvent` and a `logger.debug` call before the `continue` that defers stops during extended hours.

**Tech Stack:** Python, pytest, SQLAlchemy (via `OrderRecord`/`AuditEvent` dataclasses), existing fake-callable test harness in `tests/unit/test_order_dispatch_extended_hours.py`.

---

## File Map

| File | Change |
|---|---|
| `src/alpaca_bot/runtime/order_dispatch.py` | Bug 3: guard expiration with `broker_order_id is not None`; Gap 4: add audit event + debug log on deferral |
| `tests/unit/test_order_dispatch_extended_hours.py` | Add three new tests (one for Bug 3 pass-through, one for Bug 3 still-expires-submitted, one for Gap 4 audit event) |

---

## Background (read before touching any code)

`dispatch_pending_orders` in `order_dispatch.py` processes every `pending_submit` order each cycle. For stop orders it performs two sequential guards:

**Guard 1 — stale-stop expiration (lines 158–218):**
```python
if order.intent_type == "stop":
    ref_ts = order.signal_timestamp if order.signal_timestamp is not None else order.created_at
    ...
    created_date_et = ref_ts.astimezone(settings.market_timezone).date()
    if ref_ts is not None and created_date_et < session_date_et:
        logger.warning(...)
        # EXPIRE — saves status="expired", appends AuditEvent, continue
```
This was designed to prevent re-submitting a **submitted** stop (with `broker_order_id`) that vanished from the broker. It was never intended to expire stops that were never sent to the broker. AH-deferred stops have `broker_order_id = None` because they were queued during AH but the dispatch-skip (fix 2A from the prior session) held them in `pending_submit`.

**Guard 2 — AH/PM deferral (lines 230–233):**
```python
if order.intent_type == "stop" and session_type is not None:
    from alpaca_bot.strategy.session import SessionType as _ST
    if session_type in (_ST.PRE_MARKET, _ST.AFTER_HOURS):
        continue  # silent — no log, no audit event
```

**Bug 3 execution path (why it breaks):**
- May 7 AH: 13 positions filled → `trade_updates.py` creates `pending_submit` stops with `signal_timestamp = May 7 17:42 ET`, `broker_order_id = None`
- May 7–8 overnight: Guard 2 fires every cycle and silently defers — correct
- May 8 09:30 regular open: `session_date_et = May 8`, Guard 1 fires: `May 7 < May 8` → **EXPIRE** — wrong; the stop was never submitted

**Fix 3 semantics:** `broker_order_id is None` means "never sent to broker" — these stops are always safe to dispatch. Normal exit paths (`_execute_exit`) always cancel the `pending_submit` stop record (sets `status = "canceled"`) before submitting an exit order, so a dangling pending stop can never create a naked short.

---

## Task 1: Bug 3 — guard stale-stop expiration by `broker_order_id`

**Files:**
- Modify: `src/alpaca_bot/runtime/order_dispatch.py:168-218`
- Test: `tests/unit/test_order_dispatch_extended_hours.py`

### Context for the implementer

The `_pending_stop_order()` fixture in the test file creates a stop with `broker_order_id=None` (the default for `OrderRecord`). For these tests you need variants:
- One with yesterday's `signal_timestamp`, `broker_order_id=None` — should be dispatched, not expired
- One with yesterday's `signal_timestamp`, `broker_order_id="brk123"` — should be expired

The fake runtime in `_fake_runtime()` captures `saved` (calls to `order_store.save`) and `audits` (calls to `audit_event_store.append`). An expiry saves an `OrderRecord(status="expired")` and appends an `AuditEvent(event_type="order_expired_stale_stop")`.

For the "dispatched" assertion: the broker's `submit_stop_order` must be called. Check `broker.calls` for `("stop_order", ...)`.

`session_date_et` in `dispatch_pending_orders` is derived from `now.astimezone(settings.market_timezone).date()`. Pass `now` as today 10:00 AM ET (= 14:00 UTC) to make `session_date_et = today`. Pass `signal_timestamp` as yesterday 17:42 ET.

`settings.market_timezone` is `ZoneInfo("America/New_York")`. Yesterday in the test: use `datetime(2026, 5, 7, 21, 42, tzinfo=timezone.utc)` which is 17:42 ET on May 7; today's `now`: `datetime(2026, 5, 8, 14, 0, tzinfo=timezone.utc)` which is 10:00 ET on May 8.

- [ ] **Step 1: Write the two failing tests**

Append to `tests/unit/test_order_dispatch_extended_hours.py`:

```python
# ---------------------------------------------------------------------------
# Bug 3 — AH stop expiration at regular-session open
# ---------------------------------------------------------------------------

def _pending_stop_order_from_ah(broker_order_id: str | None = None) -> OrderRecord:
    """Stop created during AH session (yesterday evening) — may or may not be submitted."""
    return OrderRecord(
        client_order_id="test:v1:2026-05-07:AAPL:stop:2026-05-07T21:42:00+00:00",
        symbol="AAPL",
        side="sell",
        intent_type="stop",
        status="pending_submit",
        quantity=10,
        trading_mode="paper",
        strategy_version="v1",
        strategy_name="breakout",
        created_at=datetime(2026, 5, 7, 21, 42, tzinfo=timezone.utc),   # 17:42 ET May 7
        updated_at=datetime(2026, 5, 7, 21, 42, tzinfo=timezone.utc),
        stop_price=95.0,
        limit_price=None,
        initial_stop_price=95.0,
        signal_timestamp=datetime(2026, 5, 7, 21, 42, tzinfo=timezone.utc),  # 17:42 ET May 7
        broker_order_id=broker_order_id,
    )


def test_ah_stop_not_expired_at_regular_session_open():
    """
    A pending_submit stop with no broker_order_id (never submitted during AH) must be
    dispatched — not expired — when regular session opens the next morning.
    """
    settings = _settings()
    order = _pending_stop_order_from_ah(broker_order_id=None)
    runtime, saved, audits = _fake_runtime([order])
    broker = _fake_broker()
    now = datetime(2026, 5, 8, 14, 0, tzinfo=timezone.utc)  # 10:00 ET May 8 = regular session

    dispatch_pending_orders(
        settings=settings,
        runtime=runtime,
        broker=broker,
        now=now,
        session_type=SessionType.REGULAR,
    )

    stop_calls = [c for c in broker.calls if c[0] == "stop_order"]
    assert len(stop_calls) == 1, (
        "AH-deferred stop (broker_order_id=None) must be submitted at regular-session open"
    )
    expired_saves = [s for s in saved if s.status == "expired"]
    assert expired_saves == [], "AH-deferred stop must NOT be expired"
    expired_audit = [a for a in audits if a.event_type == "order_expired_stale_stop"]
    assert expired_audit == [], "AH-deferred stop must NOT emit order_expired_stale_stop audit event"


def test_submitted_stop_still_expires_at_next_session():
    """
    A stop that was previously submitted to the broker (broker_order_id set) but has a
    signal_timestamp from a prior session must still be expired — this is the original
    stale-stop guard for submitted-then-disappeared orders.
    """
    settings = _settings()
    order = _pending_stop_order_from_ah(broker_order_id="brk123")
    runtime, saved, audits = _fake_runtime([order])
    broker = _fake_broker()
    now = datetime(2026, 5, 8, 14, 0, tzinfo=timezone.utc)  # 10:00 ET May 8 = regular session

    dispatch_pending_orders(
        settings=settings,
        runtime=runtime,
        broker=broker,
        now=now,
        session_type=SessionType.REGULAR,
    )

    stop_calls = [c for c in broker.calls if c[0] == "stop_order"]
    assert stop_calls == [], "Stale submitted stop must NOT be re-submitted"
    expired_saves = [s for s in saved if s.status == "expired"]
    assert len(expired_saves) == 1, "Stale submitted stop must be saved with status='expired'"
    expired_audit = [a for a in audits if a.event_type == "order_expired_stale_stop"]
    assert len(expired_audit) == 1, "Stale submitted stop must emit order_expired_stale_stop audit event"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/unit/test_order_dispatch_extended_hours.py::test_ah_stop_not_expired_at_regular_session_open tests/unit/test_order_dispatch_extended_hours.py::test_submitted_stop_still_expires_at_next_session -v
```

Expected: `test_ah_stop_not_expired_at_regular_session_open` FAILS (stop is expired instead of submitted). `test_submitted_stop_still_expires_at_next_session` PASSES (the existing code already does this correctly — confirming our fix won't break it).

- [ ] **Step 3: Apply the fix to `order_dispatch.py`**

Find the stale-stop expiration block at lines 168–218. Change it so expiration only fires when `broker_order_id is not None`:

**Before (lines 168–218):**
```python
            if ref_ts is not None and created_date_et < session_date_et:
                logger.warning(
                    "order_dispatch: expiring stale stop order for %s (created %s, today %s)",
                    order.symbol,
                    created_date_et,
                    session_date_et,
                )
                with lock_ctx:
                    try:
                        runtime.order_store.save(
                            OrderRecord(
                                client_order_id=order.client_order_id,
                                symbol=order.symbol,
                                side=order.side,
                                intent_type=order.intent_type,
                                status="expired",
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
                        runtime.audit_event_store.append(
                            AuditEvent(
                                event_type="order_expired_stale_stop",
                                symbol=order.symbol,
                                payload={
                                    "client_order_id": order.client_order_id,
                                    "created_date": created_date_et.isoformat(),
                                    "session_date": session_date_et.isoformat(),
                                },
                                created_at=timestamp,
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
                continue
```

**After:**
```python
            if ref_ts is not None and created_date_et < session_date_et:
                if order.broker_order_id is None:
                    # Never submitted to broker — this is an AH/PM-deferred stop that is
                    # safe to dispatch now. Fall through to the session-type guard below.
                    pass
                else:
                    # Previously submitted stop that disappeared from the broker — genuinely
                    # stale. Expire it to prevent re-submitting against a position that may
                    # no longer exist (naked short risk).
                    logger.warning(
                        "order_dispatch: expiring stale stop order for %s "
                        "(broker_order_id=%s, created %s, today %s)",
                        order.symbol,
                        order.broker_order_id,
                        created_date_et,
                        session_date_et,
                    )
                    with lock_ctx:
                        try:
                            runtime.order_store.save(
                                OrderRecord(
                                    client_order_id=order.client_order_id,
                                    symbol=order.symbol,
                                    side=order.side,
                                    intent_type=order.intent_type,
                                    status="expired",
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
                            runtime.audit_event_store.append(
                                AuditEvent(
                                    event_type="order_expired_stale_stop",
                                    symbol=order.symbol,
                                    payload={
                                        "client_order_id": order.client_order_id,
                                        "created_date": created_date_et.isoformat(),
                                        "session_date": session_date_et.isoformat(),
                                        "broker_order_id": order.broker_order_id,
                                    },
                                    created_at=timestamp,
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
                    continue
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/unit/test_order_dispatch_extended_hours.py::test_ah_stop_not_expired_at_regular_session_open tests/unit/test_order_dispatch_extended_hours.py::test_submitted_stop_still_expires_at_next_session -v
```

Expected: both PASS.

- [ ] **Step 5: Run the full extended-hours test file to check for regressions**

```bash
pytest tests/unit/test_order_dispatch_extended_hours.py -v
```

Expected: all tests PASS.

- [ ] **Step 6: Commit**

```bash
git add src/alpaca_bot/runtime/order_dispatch.py tests/unit/test_order_dispatch_extended_hours.py
git commit -m "fix: AH-deferred stops no longer expire at regular-session open

Never-submitted stops (broker_order_id=None) are safe to dispatch regardless of session
date. The stale-stop expiration guard now only fires for stops that were previously
submitted to the broker, which is the only case where re-submission creates naked-short
risk."
```

---

## Task 2: Gap 4 — emit audit event when stop dispatch is deferred during extended hours

**Files:**
- Modify: `src/alpaca_bot/runtime/order_dispatch.py:230-233`
- Test: `tests/unit/test_order_dispatch_extended_hours.py`

### Context for the implementer

Lines 230–233 of `order_dispatch.py` (after Task 1 the line numbers may shift slightly):

```python
        if order.intent_type == "stop" and session_type is not None:
            from alpaca_bot.strategy.session import SessionType as _ST
            if session_type in (_ST.PRE_MARKET, _ST.AFTER_HOURS):
                continue  # Alpaca rejects stops during extended hours; submit at regular-session open
```

**Per-cycle event volume:** The deferral audit event fires once per dispatch cycle. During a typical AH session (4pm–midnight ET = 8 hours with 60s cycles), each deferred stop generates ~480 events. With 13 concurrent positions that is ~6,240 rows per AH session. This is expected and acceptable — the audit log is append-only Postgres and the rows are small. Operators can query `WHERE event_type = 'stop_dispatch_deferred_extended_hours'` to confirm deferral without being flooded in tooling that filters by type.

The `lock_ctx` pattern used elsewhere in this function:

```python
with lock_ctx:
    try:
        runtime.audit_event_store.append(..., commit=False)
        runtime.connection.commit()
    except Exception:
        try:
            runtime.connection.rollback()
        except Exception:
            pass
        raise
```

`lock_ctx` is passed to `dispatch_pending_orders` as a parameter and defaults to `contextlib.nullcontext()` in production. The `_fake_runtime` in tests uses a `FakeConn` with no-op `commit`/`rollback` and a `FakeAuditStore` that appends events to the `audits` list.

The `timestamp` local variable (set near the top of `dispatch_pending_orders` as `timestamp = now`) is already in scope.

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_order_dispatch_extended_hours.py`:

```python
# ---------------------------------------------------------------------------
# Gap 4 — audit event on stop dispatch deferral during extended hours
# ---------------------------------------------------------------------------

def test_deferred_stop_emits_audit_event_after_hours():
    """
    When a stop is deferred during AFTER_HOURS, dispatch_pending_orders must append a
    stop_dispatch_deferred_extended_hours AuditEvent so operators can confirm in the
    audit trail that stops are being held — not silently dropped.
    """
    settings = _settings()
    runtime, saved, audits = _fake_runtime([_pending_stop_order()])
    broker = _fake_broker()
    now = datetime(2026, 4, 28, 21, 0, tzinfo=timezone.utc)  # 5pm ET = after hours

    dispatch_pending_orders(
        settings=settings,
        runtime=runtime,
        broker=broker,
        now=now,
        session_type=SessionType.AFTER_HOURS,
    )

    deferred_events = [
        a for a in audits if a.event_type == "stop_dispatch_deferred_extended_hours"
    ]
    assert len(deferred_events) == 1, (
        "dispatch_pending_orders must emit stop_dispatch_deferred_extended_hours "
        "audit event when deferring a stop during AFTER_HOURS"
    )
    assert deferred_events[0].symbol == "AAPL"
    payload = deferred_events[0].payload
    assert payload["session_type"] == str(SessionType.AFTER_HOURS)
    assert payload["stop_price"] == pytest.approx(95.0)
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/unit/test_order_dispatch_extended_hours.py::test_deferred_stop_emits_audit_event_after_hours -v
```

Expected: FAIL — `assert len(deferred_events) == 1` fails because no event is emitted yet.

- [ ] **Step 3: Apply the fix to `order_dispatch.py`**

Find the AH/PM deferral block (currently lines 230–233, may shift after Task 1). Replace the `continue` with an audit event + debug log before it:

**Before:**
```python
        if order.intent_type == "stop" and session_type is not None:
            from alpaca_bot.strategy.session import SessionType as _ST
            if session_type in (_ST.PRE_MARKET, _ST.AFTER_HOURS):
                continue  # Alpaca rejects stops during extended hours; submit at regular-session open
```

**After:**
```python
        if order.intent_type == "stop" and session_type is not None:
            from alpaca_bot.strategy.session import SessionType as _ST
            if session_type in (_ST.PRE_MARKET, _ST.AFTER_HOURS):
                logger.debug(
                    "order_dispatch: deferring stop for %s during %s — will submit at regular open",
                    order.symbol,
                    session_type,
                )
                with lock_ctx:
                    try:
                        runtime.audit_event_store.append(
                            AuditEvent(
                                event_type="stop_dispatch_deferred_extended_hours",
                                symbol=order.symbol,
                                payload={
                                    "client_order_id": order.client_order_id,
                                    "session_type": str(session_type),
                                    "stop_price": order.stop_price,
                                },
                                created_at=timestamp,
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
                continue  # Alpaca rejects stops during extended hours; submit at regular-session open
```

- [ ] **Step 4: Run test to verify it passes**

```bash
pytest tests/unit/test_order_dispatch_extended_hours.py::test_deferred_stop_emits_audit_event_after_hours -v
```

Expected: PASS.

- [ ] **Step 5: Run the full extended-hours test file to check for regressions**

```bash
pytest tests/unit/test_order_dispatch_extended_hours.py -v
```

Expected: all tests PASS.

- [ ] **Step 6: Run the full test suite**

```bash
pytest
```

Expected: all tests PASS.

- [ ] **Step 7: Commit**

```bash
git add src/alpaca_bot/runtime/order_dispatch.py tests/unit/test_order_dispatch_extended_hours.py
git commit -m "feat: emit audit event when stop dispatch is deferred during extended hours

Operators can now confirm from the audit trail that stops are being held during AH/PM
rather than silently dropped. Adds stop_dispatch_deferred_extended_hours AuditEvent
with symbol, session_type, stop_price, and client_order_id."
```
