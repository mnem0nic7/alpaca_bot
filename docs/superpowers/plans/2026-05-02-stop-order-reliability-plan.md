# Stop Order Reliability — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminate the four root causes that produced 143 dispatch failures, 24 hard-failed exits, 99 reconciliation mismatches, and one wash trade on May 1, 2026.

**Architecture:** Four targeted one- to four-line fixes, each independently testable. No schema changes, no new DB columns, no new API calls. All new state transitions emit `AuditEvent` rows.

**Tech Stack:** Python 3.12, alpaca-py, pytest, psycopg2, Postgres.

---

## File Map

| Fix | File | Change |
|-----|------|--------|
| Fix 1 | `src/alpaca_bot/execution/alpaca.py` | Add `limit=500` to `GetOrdersRequest` |
| Fix 1 (test) | `tests/unit/test_alpaca_execution.py` | Assert `limit=500` captured in stub |
| Fix 2 | `src/alpaca_bot/runtime/startup_recovery.py` | Build `broker_sell_symbols`; skip recovery stop if symbol present |
| Fix 2 (test) | `tests/unit/test_startup_recovery.py` | Verify suppression + audit event when broker has sell order |
| Fix 4 | `src/alpaca_bot/runtime/cycle_intent_execution.py` | Queue recovery stop in exit-failure path |
| Fix 4 (test) | `tests/unit/test_cycle_intent_execution.py` | Update existing test; add audit event assertion |
| Fix 5 | `src/alpaca_bot/runtime/supervisor.py` | Add active stop-sell symbols to `working_order_symbols` |
| Fix 5 (test) | `tests/unit/test_cycle_engine.py` | Assert entry blocked when stop symbol in `working_order_symbols` |

---

## Task 1: Fix 1 — Paginate `list_open_orders()` fully

**Files:**
- Modify: `src/alpaca_bot/execution/alpaca.py:231`
- Test: `tests/unit/test_alpaca_execution.py`

Context: `get_orders(GetOrdersRequest(status="open"))` without `limit` returns at most 50 orders (Alpaca default). On May 1 with 50+ concurrent open orders, every order past position 50 was silently dropped. Fix: pass `limit=500`.

- [ ] **Step 1: Write the failing test**

In `tests/unit/test_alpaca_execution.py`, the existing `TradingClientStub.get_orders` at line 112 only captures the `status` field in `order_filter`. Update it to also capture `limit`, then add a new test:

```python
# In TradingClientStub.get_orders — update to capture limit:
def get_orders(self, filter: object | None = None) -> list[OrderStub]:
    if hasattr(filter, "status"):
        self.order_filter = {
            "status": str(filter.status.value if hasattr(filter.status, "value") else filter.status),
            "limit": getattr(filter, "limit", None),
        }
    else:
        self.order_filter = filter
    return self.orders
```

Add this test after `test_resolve_credentials_selects_paper_keys`:

```python
def test_list_open_orders_requests_limit_500() -> None:
    """list_open_orders must pass limit=500 to GetOrdersRequest to avoid truncation at Alpaca default of 50."""
    from alpaca_bot.execution.alpaca import AlpacaExecutionAdapter

    client = TradingClientStub()
    adapter = AlpacaExecutionAdapter.__new__(AlpacaExecutionAdapter)
    adapter._trading = client
    adapter._historical = None

    adapter.list_open_orders()

    assert client.order_filter == {"status": "open", "limit": 500}, (
        f"list_open_orders must request limit=500; got {client.order_filter!r}"
    )
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/unit/test_alpaca_execution.py::test_list_open_orders_requests_limit_500 -v
```

Expected: FAIL — `AssertionError: list_open_orders must request limit=500; got {'status': 'open', 'limit': None}`

- [ ] **Step 3: Implement the fix**

In `src/alpaca_bot/execution/alpaca.py` line 231, change:
```python
# Before:
            filters = GetOrdersRequest(status="open")
# After:
            filters = GetOrdersRequest(status="open", limit=500)
```

- [ ] **Step 4: Run test to verify it passes**

```bash
pytest tests/unit/test_alpaca_execution.py::test_list_open_orders_requests_limit_500 -v
```

Expected: PASS

- [ ] **Step 5: Run full test suite**

```bash
pytest
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/alpaca_bot/execution/alpaca.py tests/unit/test_alpaca_execution.py
git commit -m "fix: request limit=500 in list_open_orders to prevent silent truncation at 50 (RC-1)"
```

---

## Task 2: Fix 2 — Suppress recovery stop when broker has active sell order

**Files:**
- Modify: `src/alpaca_bot/runtime/startup_recovery.py` (around lines 253–314)
- Test: `tests/unit/test_startup_recovery.py`

Context: After Fix 1 corrects `list_open_orders()` pagination, Fix 2 provides defense-in-depth. If reconciliation ever incorrectly marks a stop as `reconciled_missing`, the broker-presence check prevents a duplicate stop from being submitted. The `broker_open_orders` list is already fully fetched (now with `limit=500`) and passed into `recover_startup_state()`.

`BrokerOrder` has no `intent_type` field — only `side`. A sell order at the broker (stop or exit) means the position is covered; no recovery stop is needed.

- [ ] **Step 1: Write the failing test**

Add this test to `tests/unit/test_startup_recovery.py`:

```python
def test_recovery_stop_suppressed_when_broker_has_sell_order_for_symbol() -> None:
    """If broker_open_orders already contains a sell order for a symbol, the second-pass
    recovery stop loop must NOT queue a new stop for that symbol — and must emit an
    audit event recovery_stop_suppressed_broker_has_stop."""
    from alpaca_bot.runtime.startup_recovery import recover_startup_state

    settings = make_settings()
    now = datetime(2026, 5, 1, 19, 10, tzinfo=timezone.utc)

    # Position exists locally with no local active stop (simulates a reconciled_missing stop
    # that disappeared from local DB). stop_price > 0 so recovery would normally be queued.
    position = PositionRecord(
        symbol="MRVL",
        trading_mode=settings.trading_mode,
        strategy_version=settings.strategy_version,
        quantity=50,
        entry_price=80.0,
        stop_price=75.0,
        initial_stop_price=75.0,
        opened_at=now,
        updated_at=now,
    )

    # Broker reports the position and an active sell (stop) order for MRVL.
    broker_position = BrokerPosition(
        symbol="MRVL",
        quantity=50,
        entry_price=80.0,
        market_value=4000.0,
    )
    broker_sell_order = BrokerOrder(
        client_order_id="orb:v1-breakout:2026-05-01:MRVL:stop:original",
        broker_order_id="4c9a5044",
        symbol="MRVL",
        side="sell",
        status="new",
        quantity=50,
    )

    order_store = RecordingOrderStore()
    audit_store = RecordingAuditEventStore()
    runtime = make_runtime_context(
        settings,
        position_store=RecordingPositionStore(existing_positions=[position]),
        order_store=order_store,
        audit_event_store=audit_store,
    )

    recover_startup_state(
        settings=settings,
        runtime=runtime,
        broker_open_positions=[broker_position],
        broker_open_orders=[broker_sell_order],
        now=now,
    )

    # No recovery stop must have been queued for MRVL.
    queued_stops = [
        o for o in order_store.saved
        if o.symbol == "MRVL" and o.intent_type == "stop" and o.status == "pending_submit"
    ]
    assert queued_stops == [], (
        f"Expected no recovery stop for MRVL when broker already has sell order; got {queued_stops}"
    )

    # Suppression audit event must have been emitted.
    suppression_events = [
        e for e in audit_store.appended
        if e.event_type == "recovery_stop_suppressed_broker_has_stop"
        and e.symbol == "MRVL"
    ]
    assert len(suppression_events) == 1, (
        f"Expected 1 suppression audit event; got {suppression_events}"
    )
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/unit/test_startup_recovery.py::test_recovery_stop_suppressed_when_broker_has_sell_order_for_symbol -v
```

Expected: FAIL — recovery stop is queued and suppression event is missing.

- [ ] **Step 3: Implement the fix**

In `src/alpaca_bot/runtime/startup_recovery.py`, after the `pending_entry_symbols` block (around line 257), add the broker sell symbols set:

```python
    # Fix 2: Build a set of symbols with an active sell order at the broker.
    # If the broker already holds a sell order (stop or exit) for a symbol,
    # do not queue a recovery stop — the position is already covered.
    broker_sell_symbols = {o.symbol for o in broker_open_orders if o.side == "sell"}
```

Then in the second-pass recovery loop (starting around line 313), after the `pending_entry_symbols` check, add the broker-sell check:

```python
        if pos.symbol in broker_sell_symbols:
            runtime.audit_event_store.append(
                AuditEvent(
                    event_type="recovery_stop_suppressed_broker_has_stop",
                    symbol=pos.symbol,
                    payload={"symbol": pos.symbol},
                    created_at=timestamp,
                ),
                commit=False,
            )
            continue
```

The full second-pass loop entry logic (lines 313–341) now reads:

```python
        for pos in synced_positions:
            if pos.symbol in active_stop_symbols:
                continue
            if pos.symbol in pending_entry_symbols:
                _log.warning(
                    "startup_recovery: skipping recovery stop for %s — pending_submit entry order exists",
                    pos.symbol,
                )
                continue
            if pos.symbol in broker_sell_symbols:
                runtime.audit_event_store.append(
                    AuditEvent(
                        event_type="recovery_stop_suppressed_broker_has_stop",
                        symbol=pos.symbol,
                        payload={"symbol": pos.symbol},
                        created_at=timestamp,
                    ),
                    commit=False,
                )
                continue
            if pos.stop_price <= 0:
                _log.error(...)
                continue
            # ... idempotency check + queue stop (unchanged)
```

- [ ] **Step 4: Run test to verify it passes**

```bash
pytest tests/unit/test_startup_recovery.py::test_recovery_stop_suppressed_when_broker_has_sell_order_for_symbol -v
```

Expected: PASS

- [ ] **Step 5: Run full test suite**

```bash
pytest
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/alpaca_bot/runtime/startup_recovery.py tests/unit/test_startup_recovery.py
git commit -m "fix: skip recovery stop queuing when broker already has sell order for symbol (RC-3)"
```

---

## Task 3: Fix 4 — Re-queue protective stop on exit submission failure

**Files:**
- Modify: `src/alpaca_bot/runtime/cycle_intent_execution.py` (exit-failure path, around line 633–670)
- Test: `tests/unit/test_cycle_intent_execution.py` (update existing test at line 1214)

Context: `_execute_exit()` cancels the broker stop, then submits a market exit. If the exit submission raises, the position is left open with no stop protection. Fix 4 queues a `pending_submit` recovery stop immediately before returning from the failure path, so `dispatch_pending_orders` restores protection on the next cycle.

The existing test at line 1214 (`test_execute_exit_returns_without_db_write_when_submit_market_exit_raises`) has a comment that says "next cycle will detect the missing stop." That comment was aspirational — the next cycle would re-queue via startup recovery. After Fix 4, the recovery stop is queued immediately in the same transaction.

- [ ] **Step 1: Update the existing test to assert recovery stop is queued**

Find `test_execute_exit_returns_without_db_write_when_submit_market_exit_raises` at line 1214 in `tests/unit/test_cycle_intent_execution.py`. Replace the assertions starting at line 1279 with:

```python
    assert broker.cancel_calls, "stop cancel should have been attempted"
    assert broker.exit_calls == [], "submit_market_exit raised — no exit_calls recorded"
    assert report.submitted_exit_count == 0

    # No exit OrderRecord written to DB.
    exit_writes = [o for o in order_store.saved if o.intent_type == "exit"]
    assert exit_writes == [], "No exit record must be written when submit_market_exit raises"

    # A recovery stop must have been queued immediately (position is unprotected).
    recovery_stops = [
        o for o in order_store.saved
        if o.intent_type == "stop" and o.side == "sell" and o.status == "pending_submit"
    ]
    assert len(recovery_stops) == 1, (
        f"Expected 1 recovery stop queued after exit failure; got {recovery_stops}"
    )
    assert recovery_stops[0].symbol == "AAPL"
    assert recovery_stops[0].stop_price == 109.89  # matches the canceled stop's stop_price
    assert recovery_stops[0].quantity == 25

    # Audit event must record the recovery.
    recovery_events = [
        e for e in runtime.audit_event_store.appended
        if e.event_type == "recovery_stop_queued_after_exit_failure"
    ]
    assert len(recovery_events) == 1, (
        f"Expected 1 recovery_stop_queued_after_exit_failure event; got {recovery_events}"
    )
    assert recovery_events[0].symbol == "AAPL"
```

Also update the docstring at line 1215–1217 to read:
```python
    """When submit_market_exit raises after stops are already canceled, _execute_exit
    must queue a recovery stop immediately (position is unprotected), write the
    exit_hard_failed audit event, and return submitted_exit_count=0."""
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/unit/test_cycle_intent_execution.py::test_execute_exit_returns_without_db_write_when_submit_market_exit_raises -v
```

Expected: FAIL — no recovery stop is currently queued.

- [ ] **Step 3: Implement the fix**

In `src/alpaca_bot/runtime/cycle_intent_execution.py`, locate the exit-failure `except Exception` block around line 633. Inside the `with lock_ctx:` block, after writing the `exit_hard_failed` audit event (`commit=False`) but BEFORE `runtime.connection.commit()`, add:

```python
                # Re-queue a protective stop — the broker stop was canceled but exit failed,
                # leaving the position open and unprotected.
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
```

After this insertion the block reads:

```python
    except Exception:
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
                raise
        return canceled_stop_count, 0, 1
```

- [ ] **Step 4: Run test to verify it passes**

```bash
pytest tests/unit/test_cycle_intent_execution.py::test_execute_exit_returns_without_db_write_when_submit_market_exit_raises -v
```

Expected: PASS

- [ ] **Step 5: Run full test suite**

```bash
pytest
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/alpaca_bot/runtime/cycle_intent_execution.py tests/unit/test_cycle_intent_execution.py
git commit -m "fix: queue recovery stop immediately when exit submission fails after stop cancel (RC-4)"
```

---

## Task 4: Fix 5 — Exclude symbols with active broker stop-sells from entry candidates

**Files:**
- Modify: `src/alpaca_bot/runtime/supervisor.py` (around line 381)
- Test: `tests/unit/test_cycle_engine.py` (engine-level test)
- Test: `tests/unit/test_supervisor_stop_symbol_guard.py` (new file, supervisor-level test)

Context: The AGX wash trade happened because entry candidate screening checks `open_position_symbols` and `working_order_symbols`, but `working_order_symbols` is populated only from broker open orders (which may be truncated, per RC-1) and pending-submit orders. Active local stop-sell orders — which hold shares at the broker — were not included. If the broker already has shares held for a stop, submitting an entry buy triggers wash trade detection.

Fix 5 adds all locally-tracked active stop-sell orders' symbols to `working_order_symbols` before calling `run_cycle()`. No changes to `evaluate_cycle()` — it already filters on `working_order_symbols`. This fix works even when `list_open_orders()` is truncated (it reads from local DB, not from broker).

`ACTIVE_STOP_STATUSES` is already defined in `cycle_intent_execution.py` and captures the set of statuses where a stop order is "live" from the local DB's perspective: `("pending_submit", "new", "accepted", "submitted", "partially_filled", "held")`.

**Important:** The `list_by_status` call must be wrapped in `store_lock` (same pattern as `daily_realized_pnl_by_symbol` at supervisor.py:389–396) because the supervisor cycle thread and the trade update stream thread share one psycopg2 connection — unprotected reads cause "another query is already in progress" errors.

- [ ] **Step 1: Write the engine-level test (documents existing behavior)**

Add the following test to `tests/unit/test_cycle_engine.py`. (Import `make_breakout_intraday_bars` and `make_daily_bars` from the same file — they are already defined there.)

```python
def test_evaluate_cycle_skips_entry_when_symbol_has_active_stop_in_working_order_symbols() -> None:
    """Entry candidate must be skipped when its symbol is in working_order_symbols.
    This covers the case where an active local stop-sell is added to working_order_symbols
    by the supervisor (Fix 5), preventing a wash trade."""
    CycleIntentType, evaluate_cycle = load_engine_api()

    result = evaluate_cycle(
        settings=make_settings(),
        now=datetime(2026, 4, 24, 19, 0, tzinfo=timezone.utc),
        equity=100000.0,
        intraday_bars_by_symbol={"AAPL": make_breakout_intraday_bars()},
        daily_bars_by_symbol={"AAPL": make_daily_bars()},
        open_positions=[],
        working_order_symbols={"AAPL"},  # AAPL already "occupied" by an active stop
        traded_symbols_today=set(),
        entries_disabled=False,
    )

    assert result.intents == [], (
        "Expected no entry intent for AAPL when it is already in working_order_symbols"
    )
```

- [ ] **Step 2: Run engine test to verify it already passes**

```bash
pytest tests/unit/test_cycle_engine.py::test_evaluate_cycle_skips_entry_when_symbol_has_active_stop_in_working_order_symbols -v
```

Expected: PASS (the engine already filters on `working_order_symbols`; this test documents and locks the behavior). If it FAILs, there is a pre-existing bug — investigate before proceeding.

- [ ] **Step 3: Create the supervisor-level test file**

Create `tests/unit/test_supervisor_stop_symbol_guard.py` with these complete contents:

```python
from __future__ import annotations

from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest

from alpaca_bot.config import Settings, TradingMode
from alpaca_bot.storage import AuditEvent, DailySessionState, OrderRecord, PositionRecord


def make_settings(**overrides: str) -> Settings:
    values = {
        "TRADING_MODE": "paper",
        "ENABLE_LIVE_TRADING": "false",
        "STRATEGY_VERSION": "v1-breakout",
        "DATABASE_URL": "postgresql://alpaca_bot:secret@db.example.com:5432/alpaca_bot",
        "MARKET_DATA_FEED": "sip",
        "SYMBOLS": "AAPL,MRVL",
        # Disable optional filters so _FakeMarketData needs no get_news/get_latest_quotes.
        "ENABLE_NEWS_FILTER": "false",
        "ENABLE_SPREAD_FILTER": "false",
        "ENABLE_REGIME_FILTER": "false",
        "DAILY_SMA_PERIOD": "20",
        "BREAKOUT_LOOKBACK_BARS": "20",
        "RELATIVE_VOLUME_LOOKBACK_BARS": "20",
        "RELATIVE_VOLUME_THRESHOLD": "1.5",
        "ENTRY_TIMEFRAME_MINUTES": "15",
        "RISK_PER_TRADE_PCT": "0.0025",
        "MAX_POSITION_PCT": "0.05",
        "MAX_OPEN_POSITIONS": "3",
        "DAILY_LOSS_LIMIT_PCT": "0.01",
        "STOP_LIMIT_BUFFER_PCT": "0.001",
        "BREAKOUT_STOP_BUFFER_PCT": "0.001",
        "ENTRY_STOP_PRICE_BUFFER": "0.01",
        "ENTRY_WINDOW_START": "10:00",
        "ENTRY_WINDOW_END": "15:30",
        "FLATTEN_TIME": "15:45",
    }
    values.update(overrides)
    return Settings.from_env(values)


class _RecordingOrderStore:
    def __init__(self, existing_orders: list[OrderRecord] | None = None) -> None:
        self.existing_orders = list(existing_orders or [])
        self.saved: list[OrderRecord] = []

    def save(self, order: OrderRecord, *, commit: bool = True) -> None:
        self.saved.append(order)

    def list_by_status(self, *, trading_mode, strategy_version: str, statuses: list[str],
                       strategy_name: str | None = None) -> list[OrderRecord]:
        return [o for o in self.existing_orders if o.status in statuses]

    def daily_realized_pnl(self, *, trading_mode, strategy_version: str,
                           session_date: date, market_timezone: str) -> float:
        return 0.0

    def daily_realized_pnl_by_symbol(self, *, trading_mode, strategy_version: str,
                                     session_date: date, market_timezone: str) -> dict[str, float]:
        return {}


class _RecordingPositionStore:
    def list_all(self, *, trading_mode, strategy_version: str) -> list[PositionRecord]:
        return []

    def save(self, position: PositionRecord, *, commit: bool = True) -> None:
        pass

    def replace_all(self, *, positions, trading_mode, strategy_version: str,
                    commit: bool = True) -> None:
        pass


class _RecordingAuditEventStore:
    def __init__(self) -> None:
        self.appended: list[AuditEvent] = []

    def append(self, event: AuditEvent, *, commit: bool = True) -> None:
        self.appended.append(event)


class _FakeConnection:
    def commit(self) -> None:
        pass

    def rollback(self) -> None:
        pass


class _RecordingTradingStatusStore:
    def load(self, *, trading_mode, strategy_version: str):
        return None


class _RecordingDailySessionStateStore:
    def load(self, *, session_date: date, trading_mode, strategy_version: str):
        return None

    def save(self, state: DailySessionState) -> None:
        pass


def _make_runtime(settings: Settings, order_store: _RecordingOrderStore):
    from alpaca_bot.runtime import RuntimeContext
    return RuntimeContext(
        settings=settings,
        connection=_FakeConnection(),  # type: ignore[arg-type]
        lock=object(),  # type: ignore[arg-type]
        trading_status_store=_RecordingTradingStatusStore(),  # type: ignore[arg-type]
        audit_event_store=_RecordingAuditEventStore(),  # type: ignore[arg-type]
        order_store=order_store,  # type: ignore[arg-type]
        position_store=_RecordingPositionStore(),  # type: ignore[arg-type]
        daily_session_state_store=_RecordingDailySessionStateStore(),  # type: ignore[arg-type]
    )


class _Clock:
    is_open = True

    def __init__(self, now: datetime) -> None:
        self.timestamp = now
        self.next_open = now
        self.next_close = now


class _FakeBroker:
    def __init__(self, now: datetime) -> None:
        self._clock = _Clock(now)

    def get_clock(self):
        return self._clock

    def list_open_orders(self):
        return []

    def list_open_positions(self):
        return []

    def get_account(self):
        return SimpleNamespace(equity=100000.0, buying_power=90000.0, trading_blocked=False)


class _FakeMarketData:
    def get_stock_bars(self, **kwargs):
        return {}

    def get_daily_bars(self, **kwargs):
        return {}

    def get_latest_quotes(self, **kwargs):
        return {}


def test_supervisor_includes_active_stop_sell_symbols_in_working_order_symbols() -> None:
    """The supervisor must add symbols with active local stop-sell orders to
    working_order_symbols before calling run_cycle(), so evaluate_cycle() will
    skip them as entry candidates — preventing wash trades (RC-5)."""
    from alpaca_bot.runtime.supervisor import RuntimeSupervisor
    from alpaca_bot.core.engine import CycleResult

    settings = make_settings()
    now = datetime(2026, 5, 1, 14, 0, tzinfo=timezone.utc)

    active_stop = OrderRecord(
        client_order_id="orb:v1-breakout:2026-05-01:MRVL:stop:original",
        symbol="MRVL",
        side="sell",
        intent_type="stop",
        status="new",
        quantity=50,
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        strategy_name="breakout",
        created_at=now,
        updated_at=now,
        stop_price=75.0,
        initial_stop_price=75.0,
    )

    order_store = _RecordingOrderStore(existing_orders=[active_stop])
    runtime = _make_runtime(settings, order_store)

    captured: dict[str, object] = {}

    def fake_cycle_runner(**kwargs):
        captured["working_order_symbols"] = set(kwargs["working_order_symbols"])
        return CycleResult(as_of=kwargs["now"])

    sup = RuntimeSupervisor(
        settings=settings,
        runtime=runtime,
        broker=_FakeBroker(now),
        market_data=_FakeMarketData(),
        cycle_runner=fake_cycle_runner,
        connection_checker=lambda _conn: True,  # skip real DB connection check
    )

    sup.run_cycle_once()

    assert "MRVL" in captured.get("working_order_symbols", set()), (
        f"MRVL (active stop-sell status='new') must be in working_order_symbols passed to "
        f"run_cycle(); got {captured.get('working_order_symbols')!r}"
    )
```

- [ ] **Step 4: Run supervisor test to verify it fails**

```bash
pytest tests/unit/test_supervisor_stop_symbol_guard.py::test_supervisor_includes_active_stop_sell_symbols_in_working_order_symbols -v
```

Expected: FAIL — MRVL not in `working_order_symbols` (supervisor doesn't add it yet).

- [ ] **Step 5: Implement the fix in the supervisor**

In `src/alpaca_bot/runtime/supervisor.py`, after the `working_order_symbols` assembly at lines 381–382:

```python
        working_order_symbols = {order.symbol for order in broker_open_orders}
        working_order_symbols.update(order.symbol for order in self._list_pending_submit_orders())
```

Add (note: wrap the DB read in `store_lock` to protect the shared psycopg2 connection):

```python
        # Fix 5: add symbols with active local stop-sell orders so evaluate_cycle()
        # skips them as entry candidates, preventing wash trades when list_open_orders()
        # is truncated or a stop is locally tracked but not yet visible at the broker.
        from alpaca_bot.runtime.cycle_intent_execution import ACTIVE_STOP_STATUSES
        _stop_lock = getattr(self.runtime, "store_lock", None)
        with _stop_lock if _stop_lock is not None else contextlib.nullcontext():
            _active_stop_sell_orders = self.runtime.order_store.list_by_status(
                trading_mode=self.settings.trading_mode,
                strategy_version=self.settings.strategy_version,
                statuses=list(ACTIVE_STOP_STATUSES),
            )
        working_order_symbols.update(
            o.symbol
            for o in _active_stop_sell_orders
            if o.intent_type == "stop" and o.side == "sell"
        )
```

- [ ] **Step 6: Run tests to verify they pass**

```bash
pytest tests/unit/test_supervisor_stop_symbol_guard.py::test_supervisor_includes_active_stop_sell_symbols_in_working_order_symbols tests/unit/test_cycle_engine.py::test_evaluate_cycle_skips_entry_when_symbol_has_active_stop_in_working_order_symbols -v
```

Expected: both PASS

- [ ] **Step 7: Run full test suite**

```bash
pytest
```

Expected: all tests pass.

- [ ] **Step 8: Commit**

```bash
git add src/alpaca_bot/runtime/supervisor.py tests/unit/test_supervisor_stop_symbol_guard.py tests/unit/test_cycle_engine.py
git commit -m "fix: include active stop-sell symbols in working_order_symbols to prevent wash trade entries (RC-5)"
```

---

## Self-Review

### Spec coverage
- RC-1 / Fix 1: Task 1 ✓
- RC-2 / Fix 2 (broker-stop check): Task 2 ✓
- RC-3 / Fix 3 (grace period): **Out of scope per spec — P2, schema change, explicitly excluded**
- RC-4 / Fix 4: Task 3 ✓
- RC-5 / Fix 5: Task 4 ✓

### Audit events
- Fix 1: no new state — no event needed
- Fix 2: `recovery_stop_suppressed_broker_has_stop` ✓
- Fix 4: `recovery_stop_queued_after_exit_failure` ✓
- Fix 5: no new event needed (filtering only; no state transition)

### Placeholder scan
None found — all steps contain complete code.

### Type consistency
- `BrokerOrder` has `side` field (str) — Fix 2 checks `o.side == "sell"` ✓
- `PositionRecord` has `stop_price` and `initial_stop_price` and `quantity` — Fix 4 reads all three ✓
- `ACTIVE_STOP_STATUSES` is a tuple of str in `cycle_intent_execution.py` — Fix 5 converts to `list()` before passing to `list_by_status()` ✓
- `OrderRecord` constructor takes `strategy_name` as optional str — Fix 4 passes `strategy_name` ✓

### `evaluate_cycle()` purity
- Fix 5 adds data to `working_order_symbols` in the supervisor BEFORE calling `run_cycle()`. `evaluate_cycle()` itself is unchanged and remains pure ✓

### Transaction safety
- Fix 2 audit event is appended with `commit=False` inside the existing try/finally block in `recover_startup_state()`; committed by the single `runtime.connection.commit()` at line 515 ✓
- Fix 4 recovery stop + audit event are written with `commit=False` inside the existing `with lock_ctx:` block; committed by `runtime.connection.commit()` in the same block ✓
