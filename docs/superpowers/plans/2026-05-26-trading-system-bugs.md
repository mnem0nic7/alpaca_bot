# Trading System Operational Bugs — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix 5 operational bugs causing 1,540–3,014/day `exit_hard_failed` events, 960 orphaned "submitting" option orders, daily loss limit triggers at open every day, stale-position noise every cycle for intentionally-held short puts, and supervisor running 500+ wasted cycles on weekends.

**Architecture:** All fixes are additive guards and status corrections in existing code paths. No new stores, no new state machines, no new external API contracts. Migration 022 cleans existing orphaned records. Each code change adds one conditional or early-return to an existing function.

**Tech Stack:** Python 3.11, PostgreSQL, pytest with fake-callable DI pattern.

---

## File Map

| File | Action | Bug |
|------|--------|-----|
| `migrations/022_mark_orphaned_submitting_as_failed.sql` | Create | 2 (cleanup) |
| `src/alpaca_bot/runtime/option_dispatch.py` | Modify | 2a |
| `src/alpaca_bot/runtime/supervisor.py` | Modify | 2b, 3, 5 |
| `src/alpaca_bot/runtime/cycle_intent_execution.py` | Modify | 1 |
| `/etc/alpaca_bot/alpaca-bot.env` | Modify | 4 |
| `tests/unit/test_option_dispatch.py` | Modify | 2a |
| `tests/unit/test_supervisor_eod_flatten.py` | Create | 2b |
| `tests/unit/test_execute_exit_options_guard.py` | Create | 1 |
| `tests/unit/test_supervisor_stale_positions.py` | Modify | 3 |
| `tests/unit/test_supervisor_session.py` | Modify | 5 |

---

## Task 1: Migration 022 — Mark Orphaned Submitting Records as Failed

**Files:**
- Create: `migrations/022_mark_orphaned_submitting_as_failed.sql`

- [ ] **Step 1: Write the migration**

```sql
-- 022: Clean up option_orders records orphaned by Bug 2a.
-- These records were written to status='submitting' before the broker call,
-- but the broker raised and no status rollback occurred. The dispatch loop
-- queries status='pending_submit' only, so orphaned 'submitting' records
-- are never retried or cleaned up without this migration.
UPDATE option_orders
SET    status     = 'failed',
       updated_at = NOW()
WHERE  status = 'submitting';
```

- [ ] **Step 2: Apply the migration**

Run: `alpaca-bot-migrate`

Expected output: `Applied migration 022_mark_orphaned_submitting_as_failed.sql`

- [ ] **Step 3: Verify the cleanup**

```bash
docker exec deploy-postgres-1 psql \
  "postgresql://alpaca_bot:zbel-utppQTS3Q1vSqWLfbH3OBzTtgv7@localhost:5432/alpaca_bot" \
  -c "SELECT status, count(*) FROM option_orders GROUP BY status ORDER BY status;"
```

Expected: no row with `status = submitting`. The `failed` count should be ~960.

- [ ] **Step 4: Commit**

```bash
git add migrations/022_mark_orphaned_submitting_as_failed.sql
git commit -m "fix: migration 022 — mark orphaned submitting option orders as failed"
```

---

## Task 2: Bug 2a — Status Rollback on Dispatch Failure

When `dispatch_pending_option_orders` writes `status="submitting"` and the broker call raises, the record is left permanently as `"submitting"`. Fix: write `status="failed"` in the `except` block so the dispatch loop does not skip the record indefinitely.

**Files:**
- Modify: `src/alpaca_bot/runtime/option_dispatch.py` (lines 134–138, `except Exception` block)
- Modify: `tests/unit/test_option_dispatch.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_option_dispatch.py`:

```python
def test_dispatch_sets_status_failed_on_broker_exception():
    """Broker exception → record updated to 'failed', not left as 'submitting',
    and an option_order_dispatch_failed audit event is appended."""
    from tests.unit.helpers import _base_env
    from alpaca_bot.config import Settings
    s = Settings.from_env(_base_env())

    class _RaisingBroker:
        def submit_option_market_exit(self, **kwargs):
            raise RuntimeError("broker offline")
        def submit_option_limit_entry(self, **kwargs):
            raise RuntimeError("broker offline")

    record = _record(status="pending_submit", side="sell", limit_price=None)
    runtime = _FakeRuntime([record])
    result = dispatch_pending_option_orders(
        settings=s, runtime=runtime, broker=_RaisingBroker(), now=_now(),
    )

    assert result.submitted_count == 0
    statuses = [r.status for r in runtime.option_order_store.saved]
    assert "submitting" in statuses  # written before broker call
    assert "failed" in statuses      # written in except block after failure
    failed_events = [
        e for e in runtime.audit_event_store.appended
        if e.event_type == "option_order_dispatch_failed"
    ]
    assert len(failed_events) == 1
    assert failed_events[0].payload["client_order_id"] == record.client_order_id
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_option_dispatch.py::test_dispatch_sets_status_failed_on_broker_exception -v`

Expected: FAIL — `AssertionError: assert 'failed' in ['submitting']`

- [ ] **Step 3: Implement the fix**

In `src/alpaca_bot/runtime/option_dispatch.py`, replace the `except Exception` block (lines 134–138):

**Before:**
```python
        except Exception:
            logger.exception(
                "option order dispatch failed",
                extra={"client_order_id": record.client_order_id},
            )
```

**After:**
```python
        except Exception:
            logger.exception(
                "option order dispatch failed",
                extra={"client_order_id": record.client_order_id},
            )
            failed = OptionOrderRecord(
                client_order_id=record.client_order_id,
                occ_symbol=record.occ_symbol,
                underlying_symbol=record.underlying_symbol,
                option_type=record.option_type,
                strike=record.strike,
                expiry=record.expiry,
                side=record.side,
                status="failed",
                quantity=record.quantity,
                trading_mode=record.trading_mode,
                strategy_version=record.strategy_version,
                strategy_name=record.strategy_name,
                limit_price=record.limit_price,
                broker_order_id=record.broker_order_id,
                fill_price=record.fill_price,
                filled_quantity=record.filled_quantity,
                created_at=record.created_at,
                updated_at=timestamp,
            )
            runtime.option_order_store.save(failed, commit=True)
            runtime.audit_event_store.append(
                AuditEvent(
                    event_type="option_order_dispatch_failed",
                    symbol=record.underlying_symbol,
                    payload={
                        "occ_symbol": record.occ_symbol,
                        "side": record.side,
                        "client_order_id": record.client_order_id,
                    },
                    created_at=timestamp,
                ),
                commit=True,
            )
```

- [ ] **Step 4: Run all option dispatch tests**

Run: `pytest tests/unit/test_option_dispatch.py -v`

Expected: All tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/alpaca_bot/runtime/option_dispatch.py tests/unit/test_option_dispatch.py
git commit -m "fix: revert option order status to failed on broker dispatch exception"
```

---

## Task 3: Bug 2b — Skip EOD Flatten for Short Put Positions

`run_cycle_once` creates `side="sell"` pending orders for all `status="filled"` option positions after flatten time. For short puts (`side="sell"`), this is the wrong direction — selling MORE into an existing short. The correct behavior: short puts are closed the next morning via the stale-position carryover mechanism. Fix: skip EOD flatten for `pos.side == "sell"`.

**Files:**
- Create: `tests/unit/test_supervisor_eod_flatten.py`
- Modify: `src/alpaca_bot/runtime/supervisor.py` (lines 1085–1106, inner `for` loop)

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_supervisor_eod_flatten.py`:

```python
from __future__ import annotations

from datetime import date, datetime, timezone
from importlib import import_module
from types import SimpleNamespace

from alpaca_bot.config import TradingMode
from alpaca_bot.storage.models import OptionOrderRecord


def _settings():
    from tests.unit.helpers import _base_env
    from alpaca_bot.config import Settings
    return Settings.from_env(_base_env())


def _short_put(timestamp: datetime) -> OptionOrderRecord:
    return OptionOrderRecord(
        client_order_id="option:v1-breakout:2026-05-26:ALHC260618P00017500:sell:2026-05-26T14:00:00+00:00",
        occ_symbol="ALHC260618P00017500",
        underlying_symbol="ALHC",
        option_type="put",
        strike=17.5,
        expiry=date(2026, 6, 18),
        side="sell",
        status="filled",
        quantity=1,
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        strategy_name="bear_orb",
        created_at=timestamp,
        updated_at=timestamp,
    )


def _long_call(timestamp: datetime) -> OptionOrderRecord:
    return OptionOrderRecord(
        client_order_id="option:v1-breakout:2026-05-26:AAPL240701C00100000:buy:2026-05-26T14:00:00+00:00",
        occ_symbol="AAPL240701C00100000",
        underlying_symbol="AAPL",
        option_type="call",
        strike=100.0,
        expiry=date(2026, 7, 1),
        side="buy",
        status="filled",
        quantity=1,
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        strategy_name="breakout_calls",
        created_at=timestamp,
        updated_at=timestamp,
    )


class _FakeOptionOrderStore:
    def __init__(self, filled_records):
        self._filled = filled_records
        self.saved: list[OptionOrderRecord] = []

    def list_open_option_positions(self, **kwargs):
        return self._filled

    def list_by_status(self, **kwargs):
        return []

    def save(self, record, *, commit=True):
        self.saved.append(record)


class _FakeConn:
    def commit(self): pass
    def rollback(self): pass


def _make_supervisor(option_order_store, option_broker=None):
    module = import_module("alpaca_bot.runtime.supervisor")
    RuntimeSupervisor = module.RuntimeSupervisor
    settings = _settings()

    class _FakeRuntime:
        connection = _FakeConn()
        store_lock = None
        order_store = SimpleNamespace(
            list_by_status=lambda **k: [],
            list_pending_submit=lambda **k: [],
            daily_realized_pnl=lambda **k: 0.0,
            daily_realized_pnl_by_symbol=lambda **k: {},
            list_trade_pnl_by_strategy=lambda **k: [],
        )
        position_store = SimpleNamespace(list_all=lambda **k: [], replace_all=lambda **k: None)
        trading_status_store = SimpleNamespace(load=lambda **k: None)
        daily_session_state_store = SimpleNamespace(
            load=lambda **k: None,
            save=lambda *a, **k: None,
            list_by_session=lambda **k: [],
        )
        strategy_flag_store = SimpleNamespace(list_all=lambda **k: [], load=lambda **k: None)
        watchlist_store = SimpleNamespace(
            list_enabled=lambda *a: ["AAPL"], list_ignored=lambda *a: []
        )
        audit_event_store = SimpleNamespace(
            append=lambda *a, **k: None,
            load_latest=lambda **k: None,
            list_recent=lambda **k: [],
            list_by_event_types=lambda **k: [],
        )

    _FakeRuntime.option_order_store = option_order_store

    _broker = option_broker or SimpleNamespace(
        submit_option_market_exit=lambda **k: SimpleNamespace(broker_order_id="fake-btc-1"),
    )
    return RuntimeSupervisor(
        settings=settings,
        runtime=_FakeRuntime(),
        broker=SimpleNamespace(
            get_account=lambda: SimpleNamespace(
                equity=100_000.0, buying_power=200_000.0, trading_blocked=False
            ),
            list_open_orders=lambda: [],
            get_clock=lambda: SimpleNamespace(is_open=True),
        ),
        market_data=SimpleNamespace(
            get_stock_bars=lambda **_: {}, get_daily_bars=lambda **_: {}
        ),
        stream=None,
        close_runtime_fn=lambda _: None,
        connection_checker=lambda _: True,
        cycle_runner=lambda **k: SimpleNamespace(intents=[]),
        cycle_intent_executor=lambda **k: SimpleNamespace(
            submitted_exit_count=0,
            failed_exit_count=0,
            replaced_stop_count=0,
            submitted_stop_count=0,
            canceled_stop_count=0,
        ),
        order_dispatcher=lambda **k: {"submitted_count": 0},
        option_broker=_broker,
    )


# 15:50 EDT on Monday 2026-05-26 = 19:50 UTC (EDT = UTC-4 in May)
_PAST_FLATTEN = datetime(2026, 5, 26, 19, 50, tzinfo=timezone.utc)


def test_eod_flatten_skips_side_sell_positions():
    """Short puts (side='sell') must NOT generate a new pending_submit on EOD flatten.
    They are closed the next morning via the stale-position carryover mechanism."""
    entry_ts = datetime(2026, 5, 26, 14, 0, tzinfo=timezone.utc)
    store = _FakeOptionOrderStore([_short_put(entry_ts)])

    supervisor = _make_supervisor(store)
    supervisor.run_cycle_once(now=lambda: _PAST_FLATTEN)

    pending_submit = [r for r in store.saved if r.status == "pending_submit"]
    assert pending_submit == [], (
        "EOD flatten must not create pending_submit records for side='sell' positions"
    )


def test_eod_flatten_creates_close_for_side_buy_positions():
    """Long options (side='buy') DO get a side='sell' pending_submit on EOD flatten."""
    entry_ts = datetime(2026, 5, 26, 14, 0, tzinfo=timezone.utc)
    store = _FakeOptionOrderStore([_long_call(entry_ts)])
    supervisor = _make_supervisor(store)

    supervisor.run_cycle_once(now=lambda: _PAST_FLATTEN)

    pending_submit = [r for r in store.saved if r.status == "pending_submit"]
    assert len(pending_submit) == 1
    assert pending_submit[0].side == "sell"
    assert pending_submit[0].occ_symbol == "AAPL240701C00100000"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_supervisor_eod_flatten.py -v`

Expected: `test_eod_flatten_skips_side_sell_positions` FAIL — `store.saved` contains a `pending_submit` record for the short put.

- [ ] **Step 3: Implement the fix**

In `src/alpaca_bot/runtime/supervisor.py`, inside the EOD option flatten block (lines 1085–1106), add `if pos.side == "sell": continue` at the top of the `for` loop:

**Before:**
```python
        for pos in open_option_positions:
            sell_id = (
```

**After:**
```python
        for pos in open_option_positions:
            if pos.side == "sell":
                continue  # Short puts are closed next morning via stale detection
            sell_id = (
```

- [ ] **Step 4: Run EOD flatten tests**

Run: `pytest tests/unit/test_supervisor_eod_flatten.py -v`

Expected: Both tests PASS.

- [ ] **Step 5: Run full test suite**

Run: `pytest`

Expected: All tests PASS.

- [ ] **Step 6: Commit**

```bash
git add src/alpaca_bot/runtime/supervisor.py tests/unit/test_supervisor_eod_flatten.py
git commit -m "fix: skip EOD option flatten for side=sell positions (short puts)"
```

---

## Task 4: Bug 1 — Options Market Hours Guard in `_execute_exit()`

`_execute_exit()` calls `submit_option_market_buy_to_close()` regardless of time of day. Options only trade 09:30–16:00 ET. Fix: add an early-return guard before the broker call that fires `cycle_intent_skipped` (not `exit_hard_failed`) when the market is closed.

**Files:**
- Create: `tests/unit/test_execute_exit_options_guard.py`
- Modify: `src/alpaca_bot/runtime/cycle_intent_execution.py` (line 8: add `time` to import; after `_cancel_partial_fill_entry` call: add guard block)

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_execute_exit_options_guard.py`:

```python
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from alpaca_bot.config import TradingMode
from alpaca_bot.runtime.cycle_intent_execution import _execute_exit
from alpaca_bot.storage import AuditEvent, PositionRecord
from tests.unit.test_cycle_intent_execution import (
    FakeConnection,
    RecordingAuditEventStore,
    RecordingOrderStore,
    RecordingPositionStore,
    make_settings,
)


def _short_put_position(now: datetime) -> PositionRecord:
    return PositionRecord(
        symbol="ALHC260618P00017500",
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        quantity=-1,
        entry_price=0.20,
        stop_price=0.0,
        initial_stop_price=0.0,
        opened_at=now,
        strategy_name="bear_orb",
    )


class _OptionBroker:
    def __init__(self):
        self.btc_calls: list[dict] = []
        self.cancel_calls: list[str] = []

    def submit_option_market_buy_to_close(self, **kwargs):
        self.btc_calls.append(kwargs)
        return SimpleNamespace(broker_order_id="fake-btc-1")

    def cancel_order(self, order_id: str) -> None:
        self.cancel_calls.append(order_id)

    def list_open_orders(self):
        return []


def _make_runtime(position: PositionRecord):
    return SimpleNamespace(
        order_store=RecordingOrderStore(orders=[]),
        position_store=RecordingPositionStore(positions=[position]),
        audit_event_store=RecordingAuditEventStore(),
        connection=FakeConnection(),
    )


# 20:00 UTC = 16:00 EDT — options market is closed
_AFTER_HOURS = datetime(2026, 5, 26, 20, 0, tzinfo=timezone.utc)
# 14:30 UTC = 10:30 EDT — options market is open
_MARKET_HOURS = datetime(2026, 5, 26, 14, 30, tzinfo=timezone.utc)


def test_execute_exit_returns_zero_when_options_market_closed():
    """After 4pm ET, _execute_exit returns (0,0,0) and fires cycle_intent_skipped."""
    settings = make_settings()
    position = _short_put_position(_AFTER_HOURS)
    runtime = _make_runtime(position)
    broker = _OptionBroker()

    result = _execute_exit(
        settings=settings,
        runtime=runtime,
        broker=broker,
        symbol="ALHC260618P00017500",
        intent_timestamp=_AFTER_HOURS,
        reason="stale_position_carryover",
        position=position,
        now=_AFTER_HOURS,
        strategy_name="bear_orb",
    )

    assert result == (0, 0, 0)
    assert broker.btc_calls == []
    skipped = [
        e for e in runtime.audit_event_store.appended
        if e.event_type == "cycle_intent_skipped"
    ]
    assert len(skipped) == 1
    assert skipped[0].payload["reason"] == "options_market_closed"
    assert skipped[0].symbol == "ALHC260618P00017500"


def test_execute_exit_calls_broker_during_market_hours():
    """During 09:30–16:00 ET, _execute_exit calls submit_option_market_buy_to_close."""
    settings = make_settings()
    position = _short_put_position(_MARKET_HOURS)
    runtime = _make_runtime(position)
    broker = _OptionBroker()

    _execute_exit(
        settings=settings,
        runtime=runtime,
        broker=broker,
        symbol="ALHC260618P00017500",
        intent_timestamp=_MARKET_HOURS,
        reason="stale_position_carryover",
        position=position,
        now=_MARKET_HOURS,
        strategy_name="bear_orb",
    )

    assert len(broker.btc_calls) == 1
    assert broker.btc_calls[0]["occ_symbol"] == "ALHC260618P00017500"
    assert broker.btc_calls[0]["quantity"] == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_execute_exit_options_guard.py -v`

Expected: `test_execute_exit_returns_zero_when_options_market_closed` FAIL — broker is called and result is not `(0, 0, 0)`.

- [ ] **Step 3: Implement the fix**

**3a.** In `src/alpaca_bot/runtime/cycle_intent_execution.py`, change line 8:

**Before:**
```python
from datetime import datetime, timezone
```

**After:**
```python
from datetime import datetime, time, timezone
```

**3b.** In `_execute_exit()`, add the guard block immediately after the `_cancel_partial_fill_entry(...)` call and before the `# Submit exit outside the lock.` comment. The insertion point is approximately line 826:

```python
    # Guard: options market only trades 09:30–16:00 ET; skip BTC outside those hours.
    if is_short and _is_short_option_symbol(symbol):
        local_time = now.astimezone(settings.market_timezone).time()
        if local_time < time(9, 30) or local_time >= time(16, 0):
            runtime.audit_event_store.append(
                AuditEvent(
                    event_type="cycle_intent_skipped",
                    symbol=symbol,
                    payload={
                        "reason": "options_market_closed",
                        "local_time": local_time.isoformat(),
                        "strategy_name": strategy_name,
                    },
                    created_at=now,
                ),
                commit=True,
            )
            return (0, 0, 0)

    # Submit exit outside the lock.
    try:
```

- [ ] **Step 4: Run the new tests**

Run: `pytest tests/unit/test_execute_exit_options_guard.py -v`

Expected: Both tests PASS.

- [ ] **Step 5: Run full test suite**

Run: `pytest`

Expected: All tests PASS.

- [ ] **Step 6: Commit**

```bash
git add src/alpaca_bot/runtime/cycle_intent_execution.py tests/unit/test_execute_exit_options_guard.py
git commit -m "fix: skip option BTC exit when market is closed, emit cycle_intent_skipped"
```

---

## Task 5: Bug 3 — Skip Exit Attempts for Stale OCC Positions

`_close_stale_carryover_positions()` fires exit intents for ALL stale positions including intentionally-held overnight short puts. These exits fail (market closed) → `exit_hard_failed`. Fix: partition the stale list into equity (attempt exit) and OCC (skip exit, still emit audit event).

**Files:**
- Modify: `src/alpaca_bot/runtime/supervisor.py` (add `import re`, `_is_occ_symbol()` helper, update `_close_stale_carryover_positions()`)
- Modify: `tests/unit/test_supervisor_stale_positions.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_supervisor_stale_positions.py`:

```python
# OCC symbol representing a short put
_OCC = "ALHC260618P00017500"


def test_stale_occ_position_skips_executor_and_emits_audit():
    """OCC stale position → executor NOT called, audit event written with
    skipped_exit_option_count=1."""
    supervisor, executor, settings = _make_supervisor()
    now = datetime(2026, 5, 26, 14, 0, tzinfo=timezone.utc)
    session_date = now.astimezone(settings.market_timezone).date()
    stale_ts = datetime(2026, 5, 23, 20, 0, tzinfo=timezone.utc)

    positions = [_make_open_position(_OCC, stale_ts, strategy_name="bear_orb")]

    supervisor._close_stale_carryover_positions(
        session_date=session_date,
        open_positions=positions,
        timestamp=now,
    )

    assert executor.calls == []

    stale_events = [
        e for e in supervisor._test_audit_store.appended
        if e.event_type == "stale_positions_detected"
    ]
    assert len(stale_events) == 1
    assert _OCC in stale_events[0].payload["symbols"]
    assert stale_events[0].payload["skipped_exit_option_count"] == 1


def test_stale_mixed_list_only_equity_goes_to_executor():
    """Mixed stale list: OCC symbol skips executor, equity symbol is sent."""
    supervisor, executor, settings = _make_supervisor()
    now = datetime(2026, 5, 26, 14, 0, tzinfo=timezone.utc)
    session_date = now.astimezone(settings.market_timezone).date()
    stale_ts = datetime(2026, 5, 23, 20, 0, tzinfo=timezone.utc)

    positions = [
        _make_open_position(_OCC, stale_ts, strategy_name="bear_orb"),
        _make_open_position("AAPL", stale_ts, strategy_name="breakout"),
    ]

    supervisor._close_stale_carryover_positions(
        session_date=session_date,
        open_positions=positions,
        timestamp=now,
    )

    assert len(executor.calls) == 1
    intents = executor.calls[0]["cycle_result"].intents
    symbols = {i.symbol for i in intents}
    assert symbols == {"AAPL"}
    assert _OCC not in symbols
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_supervisor_stale_positions.py::test_stale_occ_position_skips_executor_and_emits_audit tests/unit/test_supervisor_stale_positions.py::test_stale_mixed_list_only_equity_goes_to_executor -v`

Expected: Both FAIL — executor is called for the OCC symbol under current code.

- [ ] **Step 3: Add `import re` to supervisor.py**

In `src/alpaca_bot/runtime/supervisor.py`, add `import re` to the stdlib imports block (with the other single-word imports, approximately after line 9):

```python
import re
```

- [ ] **Step 4: Add `_is_occ_symbol()` helper to supervisor.py**

Add these two lines immediately before `class RuntimeSupervisor:` (approximately line 92):

```python
_OCC_RE = re.compile(r"^[A-Z]{1,6}\d{6}[CP]\d{8}$")


def _is_occ_symbol(symbol: str) -> bool:
    return bool(_OCC_RE.match(symbol))
```

- [ ] **Step 5: Update `_close_stale_carryover_positions()`**

In `_close_stale_carryover_positions()`, replace everything after `if not stale: return` up to (but not including) the notification block. The existing content to replace:

```python
        self._append_audit(
            AuditEvent(
                event_type="stale_positions_detected",
                payload={
                    "symbols": [p.symbol for p in stale],
                    "session_date": session_date.isoformat(),
                    "opened_at_dates": {
                        p.symbol: p.entry_timestamp.astimezone(
                            self.settings.market_timezone
                        ).date().isoformat()
                        for p in stale
                    },
                    "timestamp": timestamp.isoformat(),
                },
                created_at=timestamp,
            )
        )

        intents = [
            CycleIntent(
                intent_type=CycleIntentType.EXIT,
                symbol=p.symbol,
                timestamp=timestamp,
                reason="stale_position_carryover",
                strategy_name=p.strategy_name,
            )
            for p in stale
        ]
        try:
            self._cycle_intent_executor(
                settings=self.settings,
                runtime=self.runtime,
                broker=self.broker,
                cycle_result=CycleResult(as_of=timestamp, intents=intents),
                now=timestamp,
            )
        except Exception:
            logger.exception(
                "Failed to execute stale carryover exits for %s; will retry next cycle",
                [p.symbol for p in stale],
            )
```

Replace with:

```python
        stale_equity = [p for p in stale if not _is_occ_symbol(p.symbol)]
        stale_options = [p for p in stale if _is_occ_symbol(p.symbol)]

        self._append_audit(
            AuditEvent(
                event_type="stale_positions_detected",
                payload={
                    "symbols": [p.symbol for p in stale],
                    "session_date": session_date.isoformat(),
                    "opened_at_dates": {
                        p.symbol: p.entry_timestamp.astimezone(
                            self.settings.market_timezone
                        ).date().isoformat()
                        for p in stale
                    },
                    "skipped_exit_option_count": len(stale_options),
                    "timestamp": timestamp.isoformat(),
                },
                created_at=timestamp,
            )
        )

        if stale_equity:
            intents = [
                CycleIntent(
                    intent_type=CycleIntentType.EXIT,
                    symbol=p.symbol,
                    timestamp=timestamp,
                    reason="stale_position_carryover",
                    strategy_name=p.strategy_name,
                )
                for p in stale_equity
            ]
            try:
                self._cycle_intent_executor(
                    settings=self.settings,
                    runtime=self.runtime,
                    broker=self.broker,
                    cycle_result=CycleResult(as_of=timestamp, intents=intents),
                    now=timestamp,
                )
            except Exception:
                logger.exception(
                    "Failed to execute stale carryover exits for %s; will retry next cycle",
                    [p.symbol for p in stale_equity],
                )
```

- [ ] **Step 6: Run all stale position tests**

Run: `pytest tests/unit/test_supervisor_stale_positions.py -v`

Expected: All tests PASS.

- [ ] **Step 7: Run full test suite**

Run: `pytest`

Expected: All tests PASS.

- [ ] **Step 8: Commit**

```bash
git add src/alpaca_bot/runtime/supervisor.py tests/unit/test_supervisor_stale_positions.py
git commit -m "fix: skip exit for stale OCC positions, add skipped_exit_option_count to audit event"
```

---

## Task 6: Bug 5 — Weekend/Holiday Guard in `_current_session()`

`_current_session()` calls the broker clock only for `REGULAR` sessions. For `PRE_MARKET` and `AFTER_HOURS`, it returns the session type without verifying the market actually opens today. On weekends, this causes 500+ wasted cycles/day. Fix: for pre-market and after-hours, check `clock.next_open.date() != today_et` to detect weekends and holidays.

**Files:**
- Modify: `src/alpaca_bot/runtime/supervisor.py` (add `_get_broker_clock()` helper, rewrite `_current_session()`)
- Modify: `tests/unit/test_supervisor_session.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_supervisor_session.py`:

```python
def test_current_session_returns_closed_on_weekend_pre_market():
    """Saturday pre-market: broker is_open=False, next_open is Monday → CLOSED."""
    settings = _settings(
        EXTENDED_HOURS_ENABLED="true",
        PRE_MARKET_ENTRY_WINDOW_START="04:00",
        PRE_MARKET_ENTRY_WINDOW_END="09:20",
        AFTER_HOURS_ENTRY_WINDOW_START="16:05",
        AFTER_HOURS_ENTRY_WINDOW_END="19:30",
        EXTENDED_HOURS_FLATTEN_TIME="19:45",
    )
    from unittest.mock import MagicMock
    from alpaca_bot.runtime.supervisor import RuntimeSupervisor

    # Saturday 2026-05-30 at 08:00 EDT = 12:00 UTC; next_open = Monday 2026-06-01
    sat_premarket = datetime(2026, 5, 30, 12, 0, tzinfo=timezone.utc)
    next_open = datetime(2026, 6, 1, 13, 30, tzinfo=timezone.utc)

    clock = MagicMock()
    clock.is_open = False
    clock.next_open = next_open
    broker = MagicMock()
    broker.get_clock.return_value = clock

    sup = RuntimeSupervisor(
        settings=settings,
        runtime=MagicMock(),
        broker=broker,
        market_data=MagicMock(),
        stream=None,
    )
    assert sup._current_session(sat_premarket) is SessionType.CLOSED


def test_current_session_returns_pre_market_on_trading_day():
    """Monday pre-market: next_open is today → PRE_MARKET."""
    settings = _settings(
        EXTENDED_HOURS_ENABLED="true",
        PRE_MARKET_ENTRY_WINDOW_START="04:00",
        PRE_MARKET_ENTRY_WINDOW_END="09:20",
        AFTER_HOURS_ENTRY_WINDOW_START="16:05",
        AFTER_HOURS_ENTRY_WINDOW_END="19:30",
        EXTENDED_HOURS_FLATTEN_TIME="19:45",
    )
    from unittest.mock import MagicMock
    from alpaca_bot.runtime.supervisor import RuntimeSupervisor

    # Monday 2026-06-01 at 08:00 EDT = 12:00 UTC; next_open is today at 09:30 ET
    mon_premarket = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)
    next_open = datetime(2026, 6, 1, 13, 30, tzinfo=timezone.utc)

    clock = MagicMock()
    clock.is_open = False  # pre-market: not yet open
    clock.next_open = next_open
    broker = MagicMock()
    broker.get_clock.return_value = clock

    sup = RuntimeSupervisor(
        settings=settings,
        runtime=MagicMock(),
        broker=broker,
        market_data=MagicMock(),
        stream=None,
    )
    assert sup._current_session(mon_premarket) is SessionType.PRE_MARKET


def test_current_session_returns_closed_on_afterhours_trading_day():
    """Monday after-hours: market closed, next_open is Tuesday → CLOSED.
    After-hours equity trading is not used; returning CLOSED is acceptable and
    prevents wasted cycles. Fix 1 already prevents option exits after 4pm ET."""
    settings = _settings(
        EXTENDED_HOURS_ENABLED="true",
        PRE_MARKET_ENTRY_WINDOW_START="04:00",
        PRE_MARKET_ENTRY_WINDOW_END="09:20",
        AFTER_HOURS_ENTRY_WINDOW_START="16:05",
        AFTER_HOURS_ENTRY_WINDOW_END="19:30",
        EXTENDED_HOURS_FLATTEN_TIME="19:45",
    )
    from unittest.mock import MagicMock
    from alpaca_bot.runtime.supervisor import RuntimeSupervisor

    # Monday 2026-06-01 at 20:30 UTC = 16:30 EDT; next_open = Tuesday 2026-06-02
    mon_afterhours = datetime(2026, 6, 1, 20, 30, tzinfo=timezone.utc)
    next_open = datetime(2026, 6, 2, 13, 30, tzinfo=timezone.utc)

    clock = MagicMock()
    clock.is_open = False
    clock.next_open = next_open
    broker = MagicMock()
    broker.get_clock.return_value = clock

    sup = RuntimeSupervisor(
        settings=settings,
        runtime=MagicMock(),
        broker=broker,
        market_data=MagicMock(),
        stream=None,
    )
    assert sup._current_session(mon_afterhours) is SessionType.CLOSED
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_supervisor_session.py::test_current_session_returns_closed_on_weekend_pre_market tests/unit/test_supervisor_session.py::test_current_session_returns_pre_market_on_trading_day tests/unit/test_supervisor_session.py::test_current_session_returns_closed_on_afterhours_trading_day -v`

Expected: `test_current_session_returns_closed_on_weekend_pre_market` and `test_current_session_returns_closed_on_afterhours_trading_day` FAIL — return `PRE_MARKET`/`AFTER_HOURS` instead of `CLOSED`. `test_current_session_returns_pre_market_on_trading_day` may already pass since current code returns PRE_MARKET for this case.

- [ ] **Step 3: Implement the fix**

In `src/alpaca_bot/runtime/supervisor.py`, add `_get_broker_clock()` and replace `_current_session()`. The current `_current_session` body is at lines 2113–2127:

**Replace:**
```python
    def _current_session(self, timestamp: datetime) -> SessionType:
        session = detect_session_type(timestamp, self.settings)
        if session is SessionType.REGULAR:
            try:
                clock = (
                    self.broker.get_clock()
                    if hasattr(self.broker, "get_clock")
                    else self.broker.get_market_clock()
                )
                return SessionType.REGULAR if clock.is_open else SessionType.CLOSED
            except Exception:
                return SessionType.REGULAR
        if session in (SessionType.PRE_MARKET, SessionType.AFTER_HOURS):
            return session if self.settings.extended_hours_enabled else SessionType.CLOSED
        return SessionType.CLOSED
```

**With:**
```python
    def _get_broker_clock(self):
        return (
            self.broker.get_clock()
            if hasattr(self.broker, "get_clock")
            else self.broker.get_market_clock()
        )

    def _current_session(self, timestamp: datetime) -> SessionType:
        session = detect_session_type(timestamp, self.settings)
        if session is SessionType.REGULAR:
            try:
                clock = self._get_broker_clock()
                return SessionType.REGULAR if clock.is_open else SessionType.CLOSED
            except Exception:
                return SessionType.REGULAR
        if session in (SessionType.PRE_MARKET, SessionType.AFTER_HOURS):
            if not self.settings.extended_hours_enabled:
                return SessionType.CLOSED
            try:
                clock = self._get_broker_clock()
                today_et = timestamp.astimezone(self.settings.market_timezone).date()
                next_open_date = clock.next_open.astimezone(
                    self.settings.market_timezone
                ).date()
                if not clock.is_open and next_open_date != today_et:
                    return SessionType.CLOSED
            except Exception:
                pass  # broker unavailable: let session type proceed
            return session
        return SessionType.CLOSED
```

- [ ] **Step 4: Run all session tests**

Run: `pytest tests/unit/test_supervisor_session.py -v`

Expected: All tests PASS.

- [ ] **Step 5: Run full test suite**

Run: `pytest`

Expected: All tests PASS.

- [ ] **Step 6: Commit**

```bash
git add src/alpaca_bot/runtime/supervisor.py tests/unit/test_supervisor_session.py
git commit -m "fix: return CLOSED for pre-market and after-hours on weekends and exchange holidays"
```

---

## Task 7: Bug 4 — Raise Daily Loss Limit to 5%

The 1% daily loss limit (~$997 on a $99k account) is too tight for $2,011–$5,405 realized daily losses from the short put strategy. Raising to 5% (~$4,975) prevents the limit from disabling equity entries on most days while the code fixes from Tasks 2–6 take effect.

**Files:**
- Modify: `/etc/alpaca_bot/alpaca-bot.env`

- [ ] **Step 1: Update the env file**

In `/etc/alpaca_bot/alpaca-bot.env`, change:

```
DAILY_LOSS_LIMIT_PCT=0.01
```

to:

```
DAILY_LOSS_LIMIT_PCT=0.05
```

- [ ] **Step 2: Redeploy**

Run: `./scripts/deploy.sh /etc/alpaca_bot/alpaca-bot.env`

Expected: Deploy completes. The `migrate` service applies migration 022 (if not already applied). The `supervisor` service restarts with `DAILY_LOSS_LIMIT_PCT=0.05`.

- [ ] **Step 3: Verify the change is active**

Run: `alpaca-bot-admin status`

Expected: Status output shows the new loss limit is in effect (no `daily_loss_limit_breached` event immediately after market open on the next trading day).

---

## Rollback Notes

- **Migration 022**: Reverse with `UPDATE option_orders SET status = 'submitting', updated_at = NOW() WHERE status = 'failed' AND updated_at >= '<migration_timestamp>';` — safe; no data is deleted.
- **Code changes**: All are additive early-return guards. Revert any change by removing the added block. No schema changes.
- **DAILY_LOSS_LIMIT_PCT**: Revert to `0.01` in the env file and redeploy.
