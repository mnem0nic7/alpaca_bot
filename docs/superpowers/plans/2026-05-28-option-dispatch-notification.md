# Option Dispatch Failure Notification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Notify the operator when option order dispatch fails, completing notification coverage for the option trading lifecycle.

**Architecture:** Two-part change: (1) add `failed_count` field to `OptionDispatchReport` in `option_dispatch.py`; (2) capture return values at both `dispatch_pending_option_orders()` call sites in supervisor.py and notify when `total_failed > 0`.

**Tech Stack:** Python 3.12, existing `Notifier` protocol, pytest.

---

### Task 1: Add `failed_count` to `OptionDispatchReport`

**Files:**
- Modify: `src/alpaca_bot/runtime/option_dispatch.py`
- Modify: `tests/unit/test_option_dispatch.py` (if it exists, else create test in new file)

- [ ] **Step 1: Write the failing tests**

Check if `tests/unit/test_option_dispatch.py` exists:
```bash
ls tests/unit/test_option_dispatch.py 2>/dev/null || echo "not found"
```

If the file exists, add to it. If not, create `tests/unit/test_option_option_dispatch_notification.py`.

Tests to add:

```python
import dataclasses
from datetime import datetime, timezone
from types import SimpleNamespace

from alpaca_bot.runtime.option_dispatch import OptionDispatchReport, dispatch_pending_option_orders
from alpaca_bot.config import TradingMode
from tests.unit.helpers import _base_env
from alpaca_bot.config import Settings


def _make_settings():
    from tests.unit.helpers import _base_env
    return Settings.from_env(_base_env())


def test_option_dispatch_report_has_failed_count():
    """OptionDispatchReport has failed_count field defaulting to 0."""
    r = OptionDispatchReport(submitted_count=1)
    assert r.failed_count == 0
    r2 = OptionDispatchReport(submitted_count=0, failed_count=2)
    assert r2.failed_count == 2


def test_dispatch_increments_failed_count_on_broker_error():
    """When broker raises, failed_count is incremented and submitted_count is not."""
    settings = _make_settings()
    saved = []
    audit_events = []

    class _FakeBroker:
        def submit_option_limit_entry(self, **kw):
            raise RuntimeError("Insufficient buying power")

    fake_record = SimpleNamespace(
        client_order_id="test-001",
        occ_symbol="AAPL240621P00200000",
        underlying_symbol="AAPL",
        option_type="put",
        strike=200.0,
        expiry="2024-06-21",
        side="buy",
        status="pending_submit",
        quantity=1,
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        strategy_name="bear_orb",
        limit_price=1.50,
        broker_order_id=None,
        fill_price=None,
        filled_quantity=None,
        created_at=datetime(2026, 5, 28, 14, 0, tzinfo=timezone.utc),
        updated_at=datetime(2026, 5, 28, 14, 0, tzinfo=timezone.utc),
    )

    runtime = SimpleNamespace(
        option_order_store=SimpleNamespace(
            list_by_status=lambda **kw: [fake_record],
            save=lambda rec, commit=False: saved.append(rec),
        ),
        audit_event_store=SimpleNamespace(
            append=lambda event, commit=False: audit_events.append(event),
        ),
    )

    report = dispatch_pending_option_orders(
        settings=settings,
        runtime=runtime,
        broker=_FakeBroker(),
        now=datetime(2026, 5, 28, 14, 0, tzinfo=timezone.utc),
    )

    assert report.submitted_count == 0
    assert report.failed_count == 1
    # Order should be marked failed
    failed_records = [r for r in saved if r.status == "failed"]
    assert len(failed_records) == 1
    # Audit event should be emitted
    assert any(e.event_type == "option_order_dispatch_failed" for e in audit_events)


def test_dispatch_zero_failed_count_on_success():
    """When dispatch succeeds, failed_count remains 0."""
    settings = _make_settings()
    saved = []
    audit_events = []

    class _FakeBroker:
        def submit_option_limit_entry(self, **kw):
            return SimpleNamespace(broker_order_id="broker-001")

    fake_record = SimpleNamespace(
        client_order_id="test-002",
        occ_symbol="AAPL240621P00200000",
        underlying_symbol="AAPL",
        option_type="put",
        strike=200.0,
        expiry="2024-06-21",
        side="buy",
        status="pending_submit",
        quantity=1,
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        strategy_name="bear_orb",
        limit_price=1.50,
        broker_order_id=None,
        fill_price=None,
        filled_quantity=None,
        created_at=datetime(2026, 5, 28, 14, 0, tzinfo=timezone.utc),
        updated_at=datetime(2026, 5, 28, 14, 0, tzinfo=timezone.utc),
    )

    runtime = SimpleNamespace(
        option_order_store=SimpleNamespace(
            list_by_status=lambda **kw: [fake_record],
            save=lambda rec, commit=False: saved.append(rec),
        ),
        audit_event_store=SimpleNamespace(
            append=lambda event, commit=False: audit_events.append(event),
        ),
    )

    report = dispatch_pending_option_orders(
        settings=settings,
        runtime=runtime,
        broker=_FakeBroker(),
        now=datetime(2026, 5, 28, 14, 0, tzinfo=timezone.utc),
    )

    assert report.submitted_count == 1
    assert report.failed_count == 0
```

- [ ] **Step 2: Run the 3 new tests to verify they fail**

```bash
pytest tests/unit/ -k "test_option_dispatch_report_has_failed_count or test_dispatch_increments_failed_count or test_dispatch_zero_failed_count" -v
```

Expected: `test_option_dispatch_report_has_failed_count` FAILS (no `failed_count` field yet),
`test_dispatch_increments_failed_count_on_broker_error` FAILS similarly.
`test_dispatch_zero_failed_count_on_success` may pass or fail.

- [ ] **Step 3: Implement `failed_count` in `option_dispatch.py`**

In `src/alpaca_bot/runtime/option_dispatch.py`:

**3a. Add `failed_count` to the dataclass (after `submitted_count: int`):**

```python
@dataclasses.dataclass
class OptionDispatchReport:
    submitted_count: int
    failed_count: int = 0
```

**3b. Add `failed_count = 0` local variable before the loop (after `submitted_count = 0`):**

```python
    submitted_count = 0
    failed_count = 0
```

**3c. Increment in the `except` block (after `runtime.option_order_store.save(failed, commit=True)` and the audit event append, at the end of the except block):**

```python
            failed_count += 1
```

**3d. Update the return statement:**

```python
    return OptionDispatchReport(submitted_count=submitted_count, failed_count=failed_count)
```

- [ ] **Step 4: Run the tests**

```bash
pytest tests/unit/ -k "test_option_dispatch_report_has_failed_count or test_dispatch_increments_failed_count or test_dispatch_zero_failed_count" -v
```

Expected: All 3 PASS

- [ ] **Step 5: Commit**

```bash
git add src/alpaca_bot/runtime/option_dispatch.py
git commit -m "feat: add failed_count to OptionDispatchReport"
```

(Include test file in this commit too.)

---

### Task 2: Capture dispatch reports and notify in supervisor

**Files:**
- Modify: `src/alpaca_bot/runtime/supervisor.py` (lines 1084–1128)
- Modify: `tests/unit/test_option_circuit_breaker.py` OR create `tests/unit/test_option_dispatch_notification.py`

- [ ] **Step 1: Write 3 failing supervisor notification tests**

Add to `tests/unit/test_option_dispatch_notification.py` (create this file):

```python
from __future__ import annotations

from datetime import date, datetime, timezone
from importlib import import_module
from types import SimpleNamespace

from alpaca_bot.config import Settings, TradingMode
from tests.unit.helpers import _base_env


def _make_supervisor_with_dispatch_failure(failed_count: int = 1):
    """Build a minimal supervisor where option dispatch returns a given failed_count.

    Returns (supervisor, notifier_sent_list).
    """
    module = import_module("alpaca_bot.runtime.supervisor")
    RuntimeSupervisor = module.RuntimeSupervisor

    settings = Settings.from_env(_base_env())

    sent: list[tuple[str, str]] = []

    class _FakeNotifier:
        def send(self, subject: str, body: str) -> None:
            sent.append((subject, body))

    from alpaca_bot.runtime.option_dispatch import OptionDispatchReport

    class _FakeRuntime:
        connection = SimpleNamespace(commit=lambda: None, rollback=lambda: None)
        store_lock = None
        audit_event_store = SimpleNamespace(
            append=lambda event, **_: None,
            load_latest=lambda **_: None,
            list_recent=lambda **_: [],
            list_by_event_types=lambda **_: [],
        )
        position_store = SimpleNamespace(list_all=lambda **kw: [])
        order_store = SimpleNamespace(
            list_by_status=lambda **kw: [],
            list_pending_submit=lambda **kw: [],
            daily_realized_pnl=lambda **kw: 0.0,
            daily_realized_pnl_by_symbol=lambda **kw: {},
            list_trade_pnl_by_strategy=lambda **kw: [],
        )
        trading_status_store = SimpleNamespace(load=lambda **kw: None)
        daily_session_state_store = SimpleNamespace(
            load=lambda **kw: None,
            save=lambda *a, **kw: None,
            list_by_session=lambda **kw: [],
        )
        strategy_flag_store = SimpleNamespace(
            load=lambda **kw: None,
            list_all=lambda **kw: [],
        )
        watchlist_store = SimpleNamespace(
            list_enabled=lambda *a: ["AAPL"],
            list_ignored=lambda *a: [],
        )
        option_order_store = SimpleNamespace(
            list_open_option_positions=lambda **kw: [],
            list_pending_submit=lambda **kw: [],
            list_trade_pnl_by_strategy=lambda **kw: [],
            list_by_status=lambda **kw: [],
            rolling_realized_pnl_by_strategy=lambda **kw: {},
        )

    def _fake_dispatch(**kw):
        return OptionDispatchReport(submitted_count=0, failed_count=failed_count)

    supervisor = RuntimeSupervisor(
        settings=settings,
        runtime=_FakeRuntime(),
        broker=SimpleNamespace(
            get_account=lambda: SimpleNamespace(
                equity=100_000.0, buying_power=200_000.0, trading_blocked=False
            ),
            list_open_orders=lambda: [],
            list_open_positions=lambda: [],
        ),
        market_data=SimpleNamespace(
            get_stock_bars=lambda **kw: {},
            get_daily_bars=lambda **kw: {},
        ),
        stream=None,
        close_runtime_fn=lambda _: None,
        connection_checker=lambda _: True,
        cycle_runner=lambda **kw: SimpleNamespace(intents=[]),
        cycle_intent_executor=lambda **kw: SimpleNamespace(
            submitted_exit_count=0,
            failed_exit_count=0,
            replaced_stop_count=0,
            submitted_stop_count=0,
            canceled_stop_count=0,
        ),
        order_dispatcher=lambda **kw: {"submitted_count": 0},
    )
    # Inject fake option broker and notifier
    supervisor._option_broker = SimpleNamespace()
    supervisor._notifier = _FakeNotifier()

    # Patch dispatch_pending_option_orders at the module level for this supervisor
    import alpaca_bot.runtime.supervisor as sup_mod
    sup_mod._test_option_dispatch_override = staticmethod(_fake_dispatch)

    return supervisor, sent


def test_supervisor_notifies_on_option_dispatch_failure():
    """When option dispatch returns failed_count > 0, notifier.send() is called."""
    # This test exercises the run_cycle_once path indirectly through the supervisor
    # by verifying that the supervisor sends a notification when dispatch fails.
    # We test _notify_option_dispatch_failures directly since run_cycle_once is complex.
    module = import_module("alpaca_bot.runtime.supervisor")
    RuntimeSupervisor = module.RuntimeSupervisor

    settings = Settings.from_env(_base_env())
    sent: list[tuple[str, str]] = []

    class _FakeNotifier:
        def send(self, subject: str, body: str) -> None:
            sent.append((subject, body))

    supervisor = RuntimeSupervisor.__new__(RuntimeSupervisor)
    supervisor.settings = settings
    supervisor._notifier = _FakeNotifier()

    supervisor._notify_option_dispatch_failures(total_failed=3)

    assert len(sent) == 1
    subject, body = sent[0]
    assert "3" in subject or "3" in body
    assert "dispatch" in subject.lower() or "dispatch" in body.lower()


def test_supervisor_no_notification_when_dispatch_zero_failures():
    """When failed_count == 0, notifier.send() is not called."""
    module = import_module("alpaca_bot.runtime.supervisor")
    RuntimeSupervisor = module.RuntimeSupervisor

    settings = Settings.from_env(_base_env())
    sent: list[tuple[str, str]] = []

    class _FakeNotifier:
        def send(self, subject: str, body: str) -> None:
            sent.append((subject, body))

    supervisor = RuntimeSupervisor.__new__(RuntimeSupervisor)
    supervisor.settings = settings
    supervisor._notifier = _FakeNotifier()

    supervisor._notify_option_dispatch_failures(total_failed=0)

    assert sent == []


def test_supervisor_dispatch_notification_failure_does_not_crash():
    """When notifier.send() raises, _notify_option_dispatch_failures does not propagate."""
    module = import_module("alpaca_bot.runtime.supervisor")
    RuntimeSupervisor = module.RuntimeSupervisor

    settings = Settings.from_env(_base_env())

    class _BrokenNotifier:
        def send(self, subject: str, body: str) -> None:
            raise RuntimeError("SMTP timeout")

    supervisor = RuntimeSupervisor.__new__(RuntimeSupervisor)
    supervisor.settings = settings
    supervisor._notifier = _BrokenNotifier()

    # Must not raise
    supervisor._notify_option_dispatch_failures(total_failed=2)
```

- [ ] **Step 2: Run the 3 failing tests**

```bash
pytest tests/unit/test_option_dispatch_notification.py -v
```

Expected: All 3 FAIL with `AttributeError: 'RuntimeSupervisor' object has no attribute '_notify_option_dispatch_failures'`

- [ ] **Step 3: Commit the red-phase tests**

```bash
git add tests/unit/test_option_dispatch_notification.py
git commit -m "test(red): option dispatch failure notification — 3 failing tests"
```

- [ ] **Step 4: Add `_notify_option_dispatch_failures` to supervisor and update call sites**

**4a. Add the helper method** to `supervisor.py`, after `_check_option_strategy_circuit_breakers`
(i.e., after the `_effective_trading_status` method, or grouped with the other
notification helpers — before `_send_daily_summary`):

```python
def _notify_option_dispatch_failures(self, *, total_failed: int) -> None:
    if total_failed <= 0 or self._notifier is None:
        return
    try:
        self._notifier.send(
            subject=f"[alpaca-bot] Option dispatch failure: {total_failed} order(s) failed",
            body=(
                f"{total_failed} option order(s) failed to dispatch this cycle.\n\n"
                f"Check the audit log for 'option_order_dispatch_failed' events "
                f"to see which symbols and order IDs were affected."
            ),
        )
    except Exception:
        logger.exception("Notifier failed to send option dispatch failure alert")
```

**4b. Update the intraday dispatch call site** (lines 1084–1090):

Change:
```python
        if option_broker is not None and option_order_store is not None and session_type is SessionType.REGULAR:
            dispatch_pending_option_orders(
                settings=self.settings,
                runtime=self.runtime,
                broker=option_broker,
            )
```

To:
```python
        option_dispatch_report = None
        if option_broker is not None and option_order_store is not None and session_type is SessionType.REGULAR:
            option_dispatch_report = dispatch_pending_option_orders(
                settings=self.settings,
                runtime=self.runtime,
                broker=option_broker,
            )
```

**4c. Update the EOD flatten dispatch call site** (lines 1122–1127):

Change:
```python
            if option_broker is not None and open_option_positions:
                dispatch_pending_option_orders(
                    settings=self.settings,
                    runtime=self.runtime,
                    broker=option_broker,
                )
```

To:
```python
        option_dispatch_eod_report = None
            if option_broker is not None and open_option_positions:
                option_dispatch_eod_report = dispatch_pending_option_orders(
                    settings=self.settings,
                    runtime=self.runtime,
                    broker=option_broker,
                )
```

Note: `option_dispatch_eod_report = None` must be declared OUTSIDE the `if is_past_flatten_time` block so it is in scope for the aggregation below. Place it just before the `if is_past_flatten_time(...)` line.

**4d. Add aggregation and notification** just before `return SupervisorCycleReport(...)`:

```python
        self._notify_option_dispatch_failures(
            total_failed=(
                (option_dispatch_report.failed_count if option_dispatch_report is not None else 0)
                + (option_dispatch_eod_report.failed_count if option_dispatch_eod_report is not None else 0)
            )
        )
```

- [ ] **Step 5: Run the 3 notification tests**

```bash
pytest tests/unit/test_option_dispatch_notification.py -v
```

Expected: All 3 PASS

- [ ] **Step 6: Run the full test suite**

```bash
pytest --tb=short -q
```

Expected: All tests pass (no regressions)

- [ ] **Step 7: Commit the implementation**

```bash
git add src/alpaca_bot/runtime/supervisor.py \
        src/alpaca_bot/runtime/option_dispatch.py \
        tests/unit/test_option_dispatch_notification.py \
        docs/superpowers/specs/2026-05-28-option-dispatch-notification-design.md \
        docs/superpowers/plans/2026-05-28-option-dispatch-notification.md
git commit -m "feat: notify operator when option order dispatch fails"
```
