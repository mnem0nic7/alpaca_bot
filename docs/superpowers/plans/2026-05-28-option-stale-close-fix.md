# Option Stale-Position Close and Dispatch Guard Fix — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix two bugs in `supervisor.py` that prevent stale OCC option positions from ever being closed and that fire option dispatch outside market hours.

**Architecture:** Both fixes are single-method changes in `supervisor.py`. Fix 1 removes an overly conservative OCC skip from `_close_stale_carryover_positions` — the market-hours guard already exists in `_execute_exit`. Fix 2 adds a `session_type is SessionType.REGULAR` guard before `dispatch_pending_option_orders`. No new files, no migrations.

**Tech Stack:** Python, pytest, existing fake-callable DI pattern.

---

## File Map

| File | Change |
|---|---|
| `tests/unit/test_supervisor_stale_positions.py` | Replace 2 broken-behavior tests with 3 correct-behavior tests |
| `tests/unit/test_supervisor_option_dispatch_guard.py` | New: 2 tests for the session-type dispatch guard |
| `src/alpaca_bot/runtime/supervisor.py` | Fix `_close_stale_carryover_positions` (lines 1488–1533) and dispatch guard (line 1083) |

---

## Background: what to read before starting

- `src/alpaca_bot/runtime/supervisor.py` lines 1474–1548 — `_close_stale_carryover_positions` (the full method you will change)
- `src/alpaca_bot/runtime/supervisor.py` lines 1080–1088 — the unguarded `dispatch_pending_option_orders` call
- `src/alpaca_bot/runtime/cycle_intent_execution.py` — `_execute_exit()` already has a market-hours guard for BTC at lines ~826–854; you are relying on this guard, so skim it for confidence
- `tests/unit/test_supervisor_stale_positions.py` — existing stale-position tests; you will delete 2 of them

---

## Task 1: Replace broken OCC-skip tests with correct-behavior tests

The tests `test_stale_occ_position_skips_executor_and_emits_audit` and `test_stale_mixed_list_only_equity_goes_to_executor` assert the **broken** behavior (OCC skipped). Delete them and add tests for the correct behavior.

**Files:**
- Modify: `tests/unit/test_supervisor_stale_positions.py`

- [ ] **Step 1: Delete the two broken-behavior tests**

Remove lines 431–484 (the `_OCC` constant plus both failing tests) from `tests/unit/test_supervisor_stale_positions.py`. Keep all tests above line 431 intact.

The block to delete starts at:
```python
# OCC symbol representing a short put
_OCC = "ALHC260618P00017500"


def test_stale_occ_position_skips_executor_and_emits_audit():
```
and ends at the end of the file.

- [ ] **Step 2: Add three new tests at the end of the file**

Append to `tests/unit/test_supervisor_stale_positions.py`:

```python
# OCC symbol representing a short put
_OCC = "ALHC260618P00017500"


def test_stale_occ_position_exits_via_executor():
    """OCC stale position → executor IS called with EXIT intent (market-hours guard
    is in _execute_exit, not here). Regression test for commit 8a04791 which
    disabled OCC exits unnecessarily."""
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

    assert len(executor.calls) == 1
    intents = executor.calls[0]["cycle_result"].intents
    assert len(intents) == 1
    assert intents[0].symbol == _OCC
    assert intents[0].intent_type == CycleIntentType.EXIT
    assert intents[0].reason == "stale_position_carryover"


def test_stale_mixed_list_includes_occ_in_exit_intents():
    """Mixed stale list: BOTH OCC and equity symbols go to the executor."""
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
    symbols = {i.symbol for i in executor.calls[0]["cycle_result"].intents}
    assert symbols == {_OCC, "AAPL"}


def test_stale_audit_event_has_option_and_equity_counts():
    """Audit event payload includes option_symbol_count and equity_symbol_count
    fields (replaces the removed skipped_exit_option_count field)."""
    supervisor, _, settings = _make_supervisor()
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

    stale_events = [
        e for e in supervisor._test_audit_store.appended
        if e.event_type == "stale_positions_detected"
    ]
    assert len(stale_events) == 1
    payload = stale_events[0].payload
    assert payload["option_symbol_count"] == 1
    assert payload["equity_symbol_count"] == 1
    assert "skipped_exit_option_count" not in payload
```

- [ ] **Step 3: Run the three new tests to verify they FAIL (expected — fix not implemented yet)**

```bash
pytest tests/unit/test_supervisor_stale_positions.py::test_stale_occ_position_exits_via_executor tests/unit/test_supervisor_stale_positions.py::test_stale_mixed_list_includes_occ_in_exit_intents tests/unit/test_supervisor_stale_positions.py::test_stale_audit_event_has_option_and_equity_counts -v
```

Expected: all three FAIL. If they pass, the fix was already applied — stop and verify.

---

## Task 2: Write option dispatch guard tests (new test file)

**Files:**
- Create: `tests/unit/test_supervisor_option_dispatch_guard.py`

- [ ] **Step 1: Create the test file**

```python
from __future__ import annotations

from datetime import datetime, timezone
from importlib import import_module
from types import SimpleNamespace

from alpaca_bot.strategy.session import SessionType
from tests.unit.helpers import _base_env
from alpaca_bot.config import Settings


def _make_settings() -> Settings:
    return Settings.from_env(_base_env())


def _make_supervisor_with_option_broker():
    """Build a supervisor wired with a fake _option_broker and option_order_store.
    Monkeypatching of dispatch_pending_option_orders must be done by the caller
    after construction using monkeypatch."""
    module = import_module("alpaca_bot.runtime.supervisor")
    RuntimeSupervisor = module.RuntimeSupervisor
    settings = _make_settings()

    class _FakeConn:
        def commit(self): pass
        def rollback(self): pass

    class _FakeOptionOrderStore:
        def list_open_option_positions(self, **kw): return []
        def list_pending_submit(self, **kw): return []

    class _FakeRuntime:
        connection = _FakeConn()
        store_lock = None
        audit_event_store = SimpleNamespace(
            append=lambda *a, **k: None,
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
            load=lambda **kw: None, save=lambda *a, **kw: None, list_by_session=lambda **kw: []
        )
        strategy_flag_store = SimpleNamespace(list_all=lambda **kw: [], load=lambda **kw: None)
        watchlist_store = SimpleNamespace(
            list_enabled=lambda *a: ["AAPL"], list_ignored=lambda *a: []
        )
        option_order_store = _FakeOptionOrderStore()

    fake_option_broker = SimpleNamespace()

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
        market_data=SimpleNamespace(get_stock_bars=lambda **kw: {}, get_daily_bars=lambda **kw: {}),
        stream=None,
        close_runtime_fn=lambda _: None,
        connection_checker=lambda _: True,
        cycle_runner=lambda **kw: SimpleNamespace(intents=[]),
        cycle_intent_executor=lambda **kw: SimpleNamespace(
            submitted_exit_count=0, failed_exit_count=0,
            replaced_stop_count=0, submitted_stop_count=0, canceled_stop_count=0,
        ),
        order_dispatcher=lambda **kw: {"submitted_count": 0},
        option_broker=fake_option_broker,
    )
    return supervisor, module


def test_option_dispatch_skipped_when_not_regular_session(monkeypatch):
    """dispatch_pending_option_orders must NOT be called when session_type is
    AFTER_HOURS (or any non-REGULAR session). Regression test for the missing
    session guard at supervisor.py line ~1082."""
    dispatch_calls: list = []
    supervisor, module = _make_supervisor_with_option_broker()

    monkeypatch.setattr(
        module, "recover_startup_state",
        lambda **kw: module.StartupRecoveryReport(
            mismatches=(), synced_position_count=0, synced_order_count=0,
            cleared_position_count=0, cleared_order_count=0,
        ),
    )
    monkeypatch.setattr(
        module, "dispatch_pending_option_orders",
        lambda **kw: dispatch_calls.append(kw),
    )

    # 20:00 UTC = 16:00 ET — AFTER_HOURS
    ts = datetime(2026, 5, 27, 20, 0, tzinfo=timezone.utc)
    supervisor.run_cycle_once(now=lambda: ts, session_type=SessionType.AFTER_HOURS)

    assert dispatch_calls == [], (
        "dispatch_pending_option_orders must not fire outside REGULAR market hours"
    )


def test_option_dispatch_called_during_regular_session(monkeypatch):
    """dispatch_pending_option_orders IS called when session_type is REGULAR."""
    dispatch_calls: list = []
    supervisor, module = _make_supervisor_with_option_broker()

    monkeypatch.setattr(
        module, "recover_startup_state",
        lambda **kw: module.StartupRecoveryReport(
            mismatches=(), synced_position_count=0, synced_order_count=0,
            cleared_position_count=0, cleared_order_count=0,
        ),
    )
    monkeypatch.setattr(
        module, "dispatch_pending_option_orders",
        lambda **kw: dispatch_calls.append(kw),
    )

    # 15:00 UTC+1 = 10:00 ET — REGULAR market hours
    ts = datetime(2026, 5, 27, 14, 0, tzinfo=timezone.utc)
    supervisor.run_cycle_once(now=lambda: ts, session_type=SessionType.REGULAR)

    assert len(dispatch_calls) >= 1, (
        "dispatch_pending_option_orders must be called once per cycle during REGULAR session"
    )
```

- [ ] **Step 2: Run the new tests to verify they FAIL**

```bash
pytest tests/unit/test_supervisor_option_dispatch_guard.py -v
```

Expected: `test_option_dispatch_skipped_when_not_regular_session` FAILS (dispatch IS currently called regardless of session). `test_option_dispatch_called_during_regular_session` may pass or fail depending on internal details — both need to pass after the fix.

---

## Task 3: Implement Fix 1 — Remove OCC skip from `_close_stale_carryover_positions`

**Files:**
- Modify: `src/alpaca_bot/runtime/supervisor.py:1488-1533`

- [ ] **Step 1: Replace the method body**

Find the block starting at line 1488 (after `if not stale: return`) and replace it:

**Find this:**
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

**Replace with:**
```python
        occ_stale = [p for p in stale if _is_occ_symbol(p.symbol)]
        equity_stale = [p for p in stale if not _is_occ_symbol(p.symbol)]

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
                    "option_symbol_count": len(occ_stale),
                    "equity_symbol_count": len(equity_stale),
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

- [ ] **Step 2: Run the three new stale-position tests to verify they now PASS**

```bash
pytest tests/unit/test_supervisor_stale_positions.py::test_stale_occ_position_exits_via_executor tests/unit/test_supervisor_stale_positions.py::test_stale_mixed_list_includes_occ_in_exit_intents tests/unit/test_supervisor_stale_positions.py::test_stale_audit_event_has_option_and_equity_counts -v
```

Expected: all three PASS.

- [ ] **Step 3: Run the full stale-positions test file to check no regressions**

```bash
pytest tests/unit/test_supervisor_stale_positions.py -v
```

Expected: all tests PASS. (Note: no existing test checks for `skipped_exit_option_count` in the remaining tests — only the two tests we deleted did. If you see a failure about `skipped_exit_option_count` in a test you didn't delete, fix the payload key in that test to use `option_symbol_count` instead.)

- [ ] **Step 4: Commit**

```bash
git add tests/unit/test_supervisor_stale_positions.py src/alpaca_bot/runtime/supervisor.py
git commit -m "fix: close stale OCC option positions via EXIT intents

_close_stale_carryover_positions was skipping OCC symbols entirely
(commit 8a04791). The market-hours guard already exists in _execute_exit,
so this skip was unnecessary and left short puts open indefinitely.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

---

## Task 4: Implement Fix 2 — Session-type guard for option dispatch

**Files:**
- Modify: `src/alpaca_bot/runtime/supervisor.py:1082-1088`

- [ ] **Step 1: Add `session_type is SessionType.REGULAR` to the dispatch condition**

Find this block (around line 1082):
```python
        option_broker = getattr(self, "_option_broker", None)
        if option_broker is not None and option_order_store is not None:
            dispatch_pending_option_orders(
                settings=self.settings,
                runtime=self.runtime,
                broker=option_broker,
            )
```

Replace with:
```python
        option_broker = getattr(self, "_option_broker", None)
        if option_broker is not None and option_order_store is not None and session_type is SessionType.REGULAR:
            dispatch_pending_option_orders(
                settings=self.settings,
                runtime=self.runtime,
                broker=option_broker,
            )
```

`SessionType` is already imported at the top of `supervisor.py` (`from alpaca_bot.strategy.session import SessionType, detect_session_type`). No new import needed.

- [ ] **Step 2: Run the dispatch guard tests to verify both PASS**

```bash
pytest tests/unit/test_supervisor_option_dispatch_guard.py -v
```

Expected: both PASS.

- [ ] **Step 3: Run the full test suite**

```bash
pytest tests/unit/ -x -q
```

Expected: all tests PASS. If any test checks that dispatch fires outside REGULAR session, update it to reflect the new behavior.

- [ ] **Step 4: Commit**

```bash
git add tests/unit/test_supervisor_option_dispatch_guard.py src/alpaca_bot/runtime/supervisor.py
git commit -m "fix: guard option dispatch to REGULAR market hours only

dispatch_pending_option_orders was called unconditionally every cycle,
causing failed order submissions at and after 16:00 ET. bear_orb entries
only occur during REGULAR session, so dispatch outside that window is never
needed.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

---

## Task 5: Final verification

- [ ] **Step 1: Run the full test suite**

```bash
pytest tests/unit/ -q
```

Expected: all tests PASS with no new failures.

- [ ] **Step 2: Confirm both fix areas look correct in the file**

```bash
grep -n "skipped_exit_option_count\|stale_equity\|stale_options" src/alpaca_bot/runtime/supervisor.py
```

Expected: no output. All three names should be gone from the file.

```bash
grep -n "session_type is SessionType.REGULAR" src/alpaca_bot/runtime/supervisor.py
```

Expected: at least one hit at the dispatch guard line.

- [ ] **Step 3: Final commit (if any last-minute cleanups)**

If no changes needed, skip. Otherwise:
```bash
git add -p
git commit -m "chore: cleanup after option stale-close fix

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```
