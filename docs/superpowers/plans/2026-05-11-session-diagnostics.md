# Session Diagnostics Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend `alpaca-bot-session-eval` with an operational "Diagnostics" section that surfaces cycle errors, dispatch failures, unfilled entries, stream interruptions, open positions, and reconciliation issues for the evaluated session.

**Architecture:** Add `since`/`until` params to `AuditEventStore.list_by_event_types`, add `OrderStore.list_failed_entries`, build a `SessionDiagnostics` dataclass in `session_eval_cli.py`, and wire the diagnostics print into `main()`. No new CLI entry point, no migrations.

**Tech Stack:** Python 3.13, psycopg2, ZoneInfo, existing repository pattern (`fetch_all`, `_row_to_order_record`).

---

## File Map

| File | Change |
|---|---|
| `src/alpaca_bot/storage/repositories.py` | Add `since`/`until` to `AuditEventStore.list_by_event_types` (lines 158–188); add `OrderStore.list_failed_entries` (after line 389) |
| `src/alpaca_bot/admin/session_eval_cli.py` | Add imports, `SessionDiagnostics` dataclass, `_build_session_diagnostics`, `_print_session_diagnostics`; extend `main()` |
| `tests/unit/test_session_eval_diagnostics.py` | New file — unit tests for all new functions |
| `tests/unit/test_session_eval.py` | Update `_patch_cli_deps` to also patch `AuditEventStore` and `PositionStore` (needed because `main()` now calls `_build_session_diagnostics`) |

---

## Task 1: `AuditEventStore.list_by_event_types` — add `since`/`until` time window

**Files:**
- Modify: `src/alpaca_bot/storage/repositories.py:158-188`
- Test: `tests/unit/test_session_eval_diagnostics.py`

- [ ] **Step 1.1: Create the test file with the first failing test**

Create `tests/unit/test_session_eval_diagnostics.py`:

```python
from __future__ import annotations

from datetime import datetime, timezone

from alpaca_bot.storage.models import AuditEvent
from alpaca_bot.storage.repositories import AuditEventStore


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_audit_event(event_type: str, created_at: datetime, symbol: str | None = None) -> AuditEvent:
    return AuditEvent(
        event_type=event_type,
        payload={"msg": "test"},
        symbol=symbol,
        created_at=created_at,
    )


class _RecordingAuditConn:
    """Connection that records the params tuple passed to cursor.execute()."""

    def __init__(self, rows: list[tuple]) -> None:
        self._rows = rows
        self.last_params: tuple | None = None

    def cursor(self):
        conn_ref = self

        class _Cur:
            def execute(self, sql, params=None):
                conn_ref.last_params = params

            def fetchall(self):
                return conn_ref._rows

        return _Cur()


# ---------------------------------------------------------------------------
# Task 1 — list_by_event_types since/until
# ---------------------------------------------------------------------------

_T_MID = datetime(2026, 5, 11, 14, 0, tzinfo=timezone.utc)


def test_list_by_event_types_since_passes_param_to_sql():
    """since datetime is forwarded as a SQL bind param, not applied in Python."""
    conn = _RecordingAuditConn([])
    store = AuditEventStore(conn)
    since_dt = datetime(2026, 5, 11, 4, 0, tzinfo=timezone.utc)
    store.list_by_event_types(
        event_types=["supervisor_cycle_error"],
        since=since_dt,
        limit=100,
    )
    assert conn.last_params is not None
    assert since_dt in conn.last_params


def test_list_by_event_types_accepts_none_since_until():
    """Calling without since/until works exactly as before (no regression)."""
    rows = [
        ("supervisor_cycle_error", None, '{"msg": "test"}', _T_MID),
    ]
    conn = _RecordingAuditConn(rows)
    store = AuditEventStore(conn)
    result = store.list_by_event_types(
        event_types=["supervisor_cycle_error"],
        limit=100,
    )
    assert len(result) == 1
    assert result[0].event_type == "supervisor_cycle_error"
```

- [ ] **Step 1.2: Run the test to confirm it fails for the right reason**

```bash
pytest tests/unit/test_session_eval_diagnostics.py::test_list_by_event_types_since_passes_param_to_sql tests/unit/test_session_eval_diagnostics.py::test_list_by_event_types_accepts_none_since_until -v
```

Expected: FAIL with `TypeError` (unexpected keyword argument `since`) — this confirms the param doesn't exist yet. If both tests PASS unexpectedly, the method already accepts `since`; proceed to verify the SQL includes it.

- [ ] **Step 1.3: Modify `AuditEventStore.list_by_event_types` in `repositories.py`**

Replace lines 158–188 (the entire `list_by_event_types` method) with:

```python
def list_by_event_types(
    self,
    *,
    event_types: list[str],
    limit: int = 20,
    offset: int = 0,
    since: datetime | None = None,
    until: datetime | None = None,
) -> list[AuditEvent]:
    if not event_types:
        return []
    placeholders = ", ".join(["%s"] * len(event_types))
    since_clause = "AND created_at >= %s" if since is not None else ""
    until_clause = "AND created_at < %s" if until is not None else ""
    since_params = (since,) if since is not None else ()
    until_params = (until,) if until is not None else ()
    rows = fetch_all(
        self._connection,
        f"""
        SELECT event_type, symbol, payload, created_at
        FROM audit_events
        WHERE event_type IN ({placeholders})
          {since_clause}
          {until_clause}
        ORDER BY created_at DESC, event_id DESC
        LIMIT %s
        OFFSET %s
        """,
        (*event_types, *since_params, *until_params, limit, offset),
    )
    return [
        AuditEvent(
            event_type=row[0],
            symbol=row[1],
            payload=_load_json_payload(row[2]),
            created_at=row[3],
        )
        for row in rows
    ]
```

The `datetime` import is already present in `repositories.py` (it imports from `datetime` at the top). Confirm: `from datetime import date, datetime` — if only `date` is imported, add `datetime`.

- [ ] **Step 1.4: Confirm `datetime` is imported in `repositories.py`**

```bash
grep "^from datetime\|^import datetime" src/alpaca_bot/storage/repositories.py
```

Expected output includes `datetime` in the import. If only `date` is present, change:
```python
from datetime import date
```
to:
```python
from datetime import date, datetime
```

- [ ] **Step 1.5: Run tests**

```bash
pytest tests/unit/test_session_eval_diagnostics.py -v
```

Expected: both tests PASS.

- [ ] **Step 1.6: Run full suite to confirm no regressions**

```bash
pytest --tb=short -q
```

Expected: all existing tests pass (the method signature change is backwards-compatible — `since` and `until` default to `None`).

- [ ] **Step 1.7: Commit**

```bash
git add src/alpaca_bot/storage/repositories.py tests/unit/test_session_eval_diagnostics.py
git commit -m "feat: add since/until time window to AuditEventStore.list_by_event_types"
```

---

## Task 2: `OrderStore.list_failed_entries` — new method

**Files:**
- Modify: `src/alpaca_bot/storage/repositories.py` (after line 389, inside `OrderStore`)
- Test: `tests/unit/test_session_eval_diagnostics.py`

- [ ] **Step 2.1: Add failing tests to the test file**

Append to `tests/unit/test_session_eval_diagnostics.py`:

```python
# ---------------------------------------------------------------------------
# Task 2 — OrderStore.list_failed_entries
# ---------------------------------------------------------------------------

from datetime import date
from alpaca_bot.config import TradingMode
from alpaca_bot.storage.models import OrderRecord
from alpaca_bot.storage.repositories import OrderStore


class _FakeOrderConn:
    """In-memory connection returning pre-loaded order rows."""

    def __init__(self, rows: list[tuple]) -> None:
        self._rows = rows

    def cursor(self):
        rows = self._rows

        class _Cur:
            def execute(self, sql, params=None):
                pass

            def fetchall(self):
                return rows

            def close(self):
                pass

        return _Cur()


def _make_order_row(
    *,
    symbol: str = "AAPL",
    intent_type: str = "entry",
    status: str = "canceled",
    fill_price: float | None = None,
    filled_quantity: float | None = None,
) -> tuple:
    """Build a tuple matching _ORDER_SELECT_COLUMNS column order."""
    t = datetime(2026, 5, 11, 14, 0, tzinfo=timezone.utc)
    return (
        "client-id-1",   # client_order_id
        symbol,          # symbol
        "buy",           # side
        intent_type,     # intent_type
        status,          # status
        10.0,            # quantity
        "paper",         # trading_mode
        "v1",            # strategy_version
        t,               # created_at
        t,               # updated_at
        95.0,            # stop_price
        100.05,          # limit_price
        95.0,            # initial_stop_price
        None,            # broker_order_id
        t,               # signal_timestamp
        fill_price,      # fill_price
        filled_quantity, # filled_quantity
        "breakout",      # strategy_name
        0,               # reconciliation_miss_count
    )


def test_list_failed_entries_returns_canceled_entry_orders():
    """Canceled entry orders appear in list_failed_entries result."""
    rows = [_make_order_row(status="canceled")]
    conn = _FakeOrderConn(rows)
    store = OrderStore(conn)
    result = store.list_failed_entries(
        trading_mode=TradingMode.PAPER,
        strategy_version="v1",
        session_date=date(2026, 5, 11),
    )
    assert len(result) == 1
    assert result[0].symbol == "AAPL"
    assert result[0].status == "canceled"
    assert result[0].intent_type == "entry"


def test_list_failed_entries_empty_when_no_rows():
    """Returns an empty list when no failed entries exist."""
    conn = _FakeOrderConn([])
    store = OrderStore(conn)
    result = store.list_failed_entries(
        trading_mode=TradingMode.PAPER,
        strategy_version="v1",
        session_date=date(2026, 5, 11),
    )
    assert result == []
```

- [ ] **Step 2.2: Run the tests to confirm they fail**

```bash
pytest tests/unit/test_session_eval_diagnostics.py::test_list_failed_entries_returns_canceled_entry_orders tests/unit/test_session_eval_diagnostics.py::test_list_failed_entries_empty_when_no_rows -v
```

Expected: FAIL with `AttributeError: 'OrderStore' object has no attribute 'list_failed_entries'`.

- [ ] **Step 2.3: Add `list_failed_entries` to `OrderStore` in `repositories.py`**

Insert after the `list_recent` method (after line 389), inside the `OrderStore` class:

```python
def list_failed_entries(
    self,
    *,
    trading_mode: TradingMode,
    strategy_version: str,
    session_date: date,
    market_timezone: str = "America/New_York",
) -> list[OrderRecord]:
    """Return entry orders that were canceled or rejected on the given session date."""
    rows = fetch_all(
        self._connection,
        f"""
        SELECT {_ORDER_SELECT_COLUMNS}
        FROM orders
        WHERE trading_mode = %s
          AND strategy_version = %s
          AND intent_type = 'entry'
          AND status IN ('canceled', 'rejected')
          AND DATE(updated_at AT TIME ZONE %s) = %s
        ORDER BY updated_at
        """,
        (trading_mode.value, strategy_version, market_timezone, session_date),
    )
    return [_row_to_order_record(row) for row in rows]
```

- [ ] **Step 2.4: Run tests**

```bash
pytest tests/unit/test_session_eval_diagnostics.py -v
```

Expected: all 4 tests PASS.

- [ ] **Step 2.5: Run full suite**

```bash
pytest --tb=short -q
```

Expected: all tests pass.

- [ ] **Step 2.6: Commit**

```bash
git add src/alpaca_bot/storage/repositories.py tests/unit/test_session_eval_diagnostics.py
git commit -m "feat: add OrderStore.list_failed_entries for session diagnostics"
```

---

## Task 3: `SessionDiagnostics` + `_build_session_diagnostics` in `session_eval_cli.py`

**Files:**
- Modify: `src/alpaca_bot/admin/session_eval_cli.py`
- Test: `tests/unit/test_session_eval_diagnostics.py`

- [ ] **Step 3.1: Add failing tests**

Append to `tests/unit/test_session_eval_diagnostics.py`:

```python
# ---------------------------------------------------------------------------
# Task 3 — SessionDiagnostics and _build_session_diagnostics
# ---------------------------------------------------------------------------


def test_build_session_diagnostics_no_issues(monkeypatch):
    """_build_session_diagnostics returns empty SessionDiagnostics when no issues exist."""
    import alpaca_bot.admin.session_eval_cli as cli_module
    from types import SimpleNamespace

    fake_audit_store = SimpleNamespace(
        list_by_event_types=lambda **kw: [],
    )
    fake_order_store = SimpleNamespace(
        list_failed_entries=lambda **kw: [],
    )
    fake_position_store = SimpleNamespace(
        list_all=lambda **kw: [],
    )

    monkeypatch.setattr(cli_module, "AuditEventStore", lambda conn: fake_audit_store)
    monkeypatch.setattr(cli_module, "OrderStore", lambda conn: fake_order_store)
    monkeypatch.setattr(cli_module, "PositionStore", lambda conn: fake_position_store)

    diag = cli_module._build_session_diagnostics(
        object(),  # conn — not used because stores are patched
        trading_mode=TradingMode.PAPER,
        strategy_version="v1",
        eval_date=date(2026, 5, 11),
        market_timezone="America/New_York",
    )
    assert not diag.has_issues
    assert diag.cycle_errors == []
    assert diag.dispatch_failures == []
    assert diag.failed_entries == []
    assert diag.stream_issues == []
    assert diag.open_positions == []
    assert diag.reconciliation_issues == []


def test_build_session_diagnostics_cycle_errors(monkeypatch):
    """Cycle errors returned by AuditEventStore appear in SessionDiagnostics."""
    import alpaca_bot.admin.session_eval_cli as cli_module
    from types import SimpleNamespace

    cycle_event = AuditEvent(
        event_type="supervisor_cycle_error",
        payload={"error": "ZeroDivisionError"},
        created_at=datetime(2026, 5, 11, 14, 0, tzinfo=timezone.utc),
    )

    call_log: list[dict] = []

    def fake_list_by_event_types(**kw):
        call_log.append(kw)
        if "supervisor_cycle_error" in kw.get("event_types", []):
            return [cycle_event]
        return []

    fake_audit_store = SimpleNamespace(list_by_event_types=fake_list_by_event_types)
    fake_order_store = SimpleNamespace(list_failed_entries=lambda **kw: [])
    fake_position_store = SimpleNamespace(list_all=lambda **kw: [])

    monkeypatch.setattr(cli_module, "AuditEventStore", lambda conn: fake_audit_store)
    monkeypatch.setattr(cli_module, "OrderStore", lambda conn: fake_order_store)
    monkeypatch.setattr(cli_module, "PositionStore", lambda conn: fake_position_store)

    diag = cli_module._build_session_diagnostics(
        object(),
        trading_mode=TradingMode.PAPER,
        strategy_version="v1",
        eval_date=date(2026, 5, 11),
        market_timezone="America/New_York",
    )
    assert diag.has_issues
    assert len(diag.cycle_errors) == 1
    assert diag.cycle_errors[0].event_type == "supervisor_cycle_error"
    # since/until must be passed to the audit store
    assert any("since" in call for call in call_log)


def test_build_session_diagnostics_open_positions(monkeypatch):
    """Open positions from PositionStore appear in SessionDiagnostics."""
    import alpaca_bot.admin.session_eval_cli as cli_module
    from types import SimpleNamespace
    from alpaca_bot.storage.models import PositionRecord

    t = datetime(2026, 5, 11, 10, 0, tzinfo=timezone.utc)
    pos = PositionRecord(
        symbol="TSLA",
        trading_mode=TradingMode.PAPER,
        strategy_version="v1",
        quantity=10.0,
        entry_price=100.0,
        stop_price=95.0,
        initial_stop_price=95.0,
        opened_at=t,
    )

    monkeypatch.setattr(cli_module, "AuditEventStore", lambda conn: SimpleNamespace(list_by_event_types=lambda **kw: []))
    monkeypatch.setattr(cli_module, "OrderStore", lambda conn: SimpleNamespace(list_failed_entries=lambda **kw: []))
    monkeypatch.setattr(cli_module, "PositionStore", lambda conn: SimpleNamespace(list_all=lambda **kw: [pos]))

    diag = cli_module._build_session_diagnostics(
        object(),
        trading_mode=TradingMode.PAPER,
        strategy_version="v1",
        eval_date=date(2026, 5, 11),
        market_timezone="America/New_York",
    )
    assert diag.has_issues
    assert len(diag.open_positions) == 1
    assert diag.open_positions[0].symbol == "TSLA"
```

- [ ] **Step 3.2: Run the tests to confirm they fail**

```bash
pytest tests/unit/test_session_eval_diagnostics.py::test_build_session_diagnostics_no_issues tests/unit/test_session_eval_diagnostics.py::test_build_session_diagnostics_cycle_errors tests/unit/test_session_eval_diagnostics.py::test_build_session_diagnostics_open_positions -v
```

Expected: FAIL with `AttributeError: module 'alpaca_bot.admin.session_eval_cli' has no attribute '_build_session_diagnostics'`.

- [ ] **Step 3.3: Update imports in `session_eval_cli.py`**

Replace the current import block (lines 1–12 of the file) with:

```python
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from typing import Sequence
from zoneinfo import ZoneInfo

from alpaca_bot.config import Settings, TradingMode
from alpaca_bot.replay.report import BacktestReport, ReplayTradeRecord, report_from_records
from alpaca_bot.storage.db import ConnectionProtocol, connect_postgres
from alpaca_bot.storage.models import (
    EQUITY_SESSION_STATE_STRATEGY_NAME,
    AuditEvent,
    OrderRecord,
    PositionRecord,
)
from alpaca_bot.storage.repositories import (
    AuditEventStore,
    DailySessionStateStore,
    OrderStore,
    PositionStore,
)
```

- [ ] **Step 3.4: Add `SessionDiagnostics` dataclass and `_build_session_diagnostics` function**

Add these after the existing `_row_to_trade_record` function and before `_print_session_report` in `session_eval_cli.py`:

```python
@dataclass
class SessionDiagnostics:
    cycle_errors: list[AuditEvent] = field(default_factory=list)
    dispatch_failures: list[AuditEvent] = field(default_factory=list)
    failed_entries: list[OrderRecord] = field(default_factory=list)
    stream_issues: list[AuditEvent] = field(default_factory=list)
    open_positions: list[PositionRecord] = field(default_factory=list)
    reconciliation_issues: list[AuditEvent] = field(default_factory=list)

    @property
    def has_issues(self) -> bool:
        return any([
            self.cycle_errors,
            self.dispatch_failures,
            self.failed_entries,
            self.stream_issues,
            self.open_positions,
            self.reconciliation_issues,
        ])


def _build_session_diagnostics(
    conn: ConnectionProtocol,
    *,
    trading_mode: TradingMode,
    strategy_version: str,
    eval_date: date,
    market_timezone: str,
) -> SessionDiagnostics:
    tz = ZoneInfo(market_timezone)
    session_start = datetime.combine(eval_date, time(0, 0), tzinfo=tz).astimezone(timezone.utc)
    session_end = datetime.combine(eval_date + timedelta(days=1), time(0, 0), tzinfo=tz).astimezone(timezone.utc)

    audit_store = AuditEventStore(conn)
    order_store = OrderStore(conn)
    position_store = PositionStore(conn)

    return SessionDiagnostics(
        cycle_errors=audit_store.list_by_event_types(
            event_types=["supervisor_cycle_error", "strategy_cycle_error"],
            since=session_start,
            until=session_end,
            limit=100,
        ),
        dispatch_failures=audit_store.list_by_event_types(
            event_types=["order_dispatch_failed"],
            since=session_start,
            until=session_end,
            limit=100,
        ),
        failed_entries=order_store.list_failed_entries(
            trading_mode=trading_mode,
            strategy_version=strategy_version,
            session_date=eval_date,
            market_timezone=market_timezone,
        ),
        stream_issues=audit_store.list_by_event_types(
            event_types=["stream_heartbeat_stale", "stream_restart_failed", "trade_update_stream_failed"],
            since=session_start,
            until=session_end,
            limit=100,
        ),
        open_positions=position_store.list_all(
            trading_mode=trading_mode,
            strategy_version=strategy_version,
        ),
        reconciliation_issues=audit_store.list_by_event_types(
            event_types=["reconciliation_miss_count_incremented", "runtime_reconciliation_detected"],
            since=session_start,
            until=session_end,
            limit=100,
        ),
    )
```

- [ ] **Step 3.5: Run tests**

```bash
pytest tests/unit/test_session_eval_diagnostics.py -v
```

Expected: all tests PASS.

- [ ] **Step 3.6: Run full suite**

```bash
pytest --tb=short -q
```

Expected: all tests pass.

- [ ] **Step 3.7: Commit**

```bash
git add src/alpaca_bot/admin/session_eval_cli.py tests/unit/test_session_eval_diagnostics.py
git commit -m "feat: add SessionDiagnostics dataclass and _build_session_diagnostics"
```

---

## Task 4: `_print_session_diagnostics` + wire into `main()`

**Files:**
- Modify: `src/alpaca_bot/admin/session_eval_cli.py`
- Modify: `tests/unit/test_session_eval.py` (update `_patch_cli_deps` to patch new stores)
- Test: `tests/unit/test_session_eval_diagnostics.py`

- [ ] **Step 4.1: Add print tests to the diagnostics test file**

Append to `tests/unit/test_session_eval_diagnostics.py`:

```python
# ---------------------------------------------------------------------------
# Task 4 — _print_session_diagnostics
# ---------------------------------------------------------------------------


def test_print_no_issues(capsys):
    """Clean diagnostics prints 'No operational issues found'."""
    from alpaca_bot.admin.session_eval_cli import _print_session_diagnostics, SessionDiagnostics
    diag = SessionDiagnostics()
    _print_session_diagnostics(diag)
    out = capsys.readouterr().out
    assert "No operational issues found" in out


def test_print_with_cycle_errors(capsys):
    """Cycle errors print a ⚠ line."""
    from alpaca_bot.admin.session_eval_cli import _print_session_diagnostics, SessionDiagnostics
    e = AuditEvent(
        event_type="supervisor_cycle_error",
        payload={"error": "ZeroDivisionError"},
        created_at=datetime(2026, 5, 11, 14, 0, tzinfo=timezone.utc),
    )
    diag = SessionDiagnostics(cycle_errors=[e])
    _print_session_diagnostics(diag)
    out = capsys.readouterr().out
    assert "Cycle errors: 1" in out
    assert "ZeroDivisionError" in out


def test_print_with_dispatch_failures(capsys):
    """Dispatch failures print a ⚠ line with symbol."""
    from alpaca_bot.admin.session_eval_cli import _print_session_diagnostics, SessionDiagnostics
    e = AuditEvent(
        event_type="order_dispatch_failed",
        payload={"error": "timeout"},
        symbol="NVDA",
        created_at=datetime(2026, 5, 11, 14, 0, tzinfo=timezone.utc),
    )
    diag = SessionDiagnostics(dispatch_failures=[e])
    _print_session_diagnostics(diag)
    out = capsys.readouterr().out
    assert "Dispatch failures: 1" in out
    assert "NVDA" in out


def test_print_with_unfilled_entries(capsys):
    """Unfilled (canceled) entry orders print a ⚠ line."""
    from alpaca_bot.admin.session_eval_cli import _print_session_diagnostics, SessionDiagnostics
    t = datetime(2026, 5, 11, 14, 0, tzinfo=timezone.utc)
    order = OrderRecord(
        client_order_id="id1",
        symbol="AAPL",
        side="buy",
        intent_type="entry",
        status="canceled",
        quantity=10.0,
        trading_mode=TradingMode.PAPER,
        strategy_version="v1",
        created_at=t,
        updated_at=t,
    )
    diag = SessionDiagnostics(failed_entries=[order])
    _print_session_diagnostics(diag)
    out = capsys.readouterr().out
    assert "Unfilled entries" in out
    assert "AAPL" in out
    assert "canceled" in out


def test_print_with_open_positions(capsys):
    """Open positions at EOD print a ⚠ line with symbol."""
    from alpaca_bot.admin.session_eval_cli import _print_session_diagnostics, SessionDiagnostics
    from alpaca_bot.storage.models import PositionRecord
    t = datetime(2026, 5, 11, 10, 0, tzinfo=timezone.utc)
    pos = PositionRecord(
        symbol="TSLA",
        trading_mode=TradingMode.PAPER,
        strategy_version="v1",
        quantity=5.0,
        entry_price=200.0,
        stop_price=195.0,
        initial_stop_price=195.0,
        opened_at=t,
    )
    diag = SessionDiagnostics(open_positions=[pos])
    _print_session_diagnostics(diag)
    out = capsys.readouterr().out
    assert "Open positions at EOD" in out
    assert "TSLA" in out
```

- [ ] **Step 4.2: Run the tests to confirm they fail**

```bash
pytest tests/unit/test_session_eval_diagnostics.py::test_print_no_issues tests/unit/test_session_eval_diagnostics.py::test_print_with_cycle_errors -v
```

Expected: FAIL with `ImportError: cannot import name '_print_session_diagnostics' from 'alpaca_bot.admin.session_eval_cli'`.

- [ ] **Step 4.3: Add `_print_session_diagnostics` to `session_eval_cli.py`**

Add after `_build_session_diagnostics`:

```python
def _print_session_diagnostics(diagnostics: SessionDiagnostics) -> None:
    print()
    print(" Diagnostics")
    print(" " + "─" * 60)

    if not diagnostics.has_issues:
        print(" ✓ No operational issues found")
        print()
        return

    if diagnostics.cycle_errors:
        print(f" ⚠ Cycle errors: {len(diagnostics.cycle_errors)}")
        for e in diagnostics.cycle_errors[:3]:
            ts = e.created_at.strftime("%H:%M:%SZ")
            msg = str(e.payload.get("error", ""))[:60]
            print(f"     {ts} — {msg}")

    if diagnostics.dispatch_failures:
        print(f" ⚠ Dispatch failures: {len(diagnostics.dispatch_failures)}")
        for e in diagnostics.dispatch_failures[:3]:
            sym = e.symbol or str(e.payload.get("symbol", "?"))
            msg = str(e.payload.get("error", ""))[:40]
            print(f"     {sym}: {msg}")

    if diagnostics.failed_entries:
        parts = []
        for o in diagnostics.failed_entries:
            if o.filled_quantity is not None and o.filled_quantity > 0:
                parts.append(f"{o.symbol} (partial {o.status})")
            else:
                parts.append(f"{o.symbol} ({o.status})")
        print(f" ⚠ Unfilled entries: {', '.join(parts)}")

    if diagnostics.stream_issues:
        print(f" ⚠ Stream interruptions: {len(diagnostics.stream_issues)}")

    if diagnostics.open_positions:
        syms = [p.symbol for p in diagnostics.open_positions]
        print(f" ⚠ Open positions at EOD: {', '.join(syms)}")

    if diagnostics.reconciliation_issues:
        print(f" ⚠ Reconciliation issues: {len(diagnostics.reconciliation_issues)}")

    print()
```

- [ ] **Step 4.4: Run the print tests**

```bash
pytest tests/unit/test_session_eval_diagnostics.py -v
```

Expected: all tests PASS.

- [ ] **Step 4.5: Wire `_build_session_diagnostics` into `main()`**

Replace the `main()` function in `session_eval_cli.py` with:

```python
def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="alpaca-bot-session-eval",
        description="Evaluate a live trading session from Postgres data",
    )
    parser.add_argument("--date", metavar="YYYY-MM-DD", help="Session date (default: today)")
    parser.add_argument("--mode", default="paper", choices=["paper", "live"],
                        help="Trading mode (default: paper)")
    parser.add_argument("--strategy-version", metavar="VERSION",
                        help="Strategy version (default: STRATEGY_VERSION env var)")
    parser.add_argument("--strategy", metavar="NAME", help="Filter to a single strategy name")
    args = parser.parse_args(list(argv) if argv is not None else sys.argv[1:])

    eval_date: date = date.fromisoformat(args.date) if args.date else date.today()

    settings = Settings.from_env()
    strategy_version = args.strategy_version or settings.strategy_version
    trading_mode = TradingMode(args.mode)
    market_timezone = settings.market_timezone.key

    conn = connect_postgres(settings.database_url)
    try:
        order_store = OrderStore(conn)
        session_store = DailySessionStateStore(conn)

        state = session_store.load(
            session_date=eval_date,
            trading_mode=trading_mode,
            strategy_version=strategy_version,
            strategy_name=EQUITY_SESSION_STATE_STRATEGY_NAME,
        )
        if state is None or state.equity_baseline is None:
            print(f"Warning: no equity baseline found for {eval_date}; using $100,000 as starting equity.")
            starting_equity = 100_000.0
        else:
            starting_equity = state.equity_baseline

        raw_trades = order_store.list_closed_trades(
            trading_mode=trading_mode,
            strategy_version=strategy_version,
            session_date=eval_date,
            strategy_name=args.strategy,
        )
        diagnostics = _build_session_diagnostics(
            conn,
            trading_mode=trading_mode,
            strategy_version=strategy_version,
            eval_date=eval_date,
            market_timezone=market_timezone,
        )
    finally:
        close = getattr(conn, "close", None)
        if callable(close):
            close()

    if not raw_trades:
        strategy_label = f" (strategy={args.strategy})" if args.strategy else ""
        print(f"No closed trades for {eval_date}{strategy_label}.")
        _print_session_diagnostics(diagnostics)
        return 0

    trade_records = [_row_to_trade_record(row) for row in raw_trades]
    report = report_from_records(
        trade_records,
        starting_equity=starting_equity,
        strategy_name=args.strategy or "all",
    )
    _print_session_report(report, eval_date=eval_date, trading_mode=args.mode,
                          strategy_version=strategy_version)
    _print_session_diagnostics(diagnostics)
    return 0
```

- [ ] **Step 4.6: Update `_patch_cli_deps` in `tests/unit/test_session_eval.py`**

The existing `_patch_cli_deps` function patches `Settings`, `connect_postgres`, `DailySessionStateStore`, and `OrderStore`. With `main()` now calling `_build_session_diagnostics`, it also needs `AuditEventStore` and `PositionStore` patched.

Find `_patch_cli_deps` in `tests/unit/test_session_eval.py` and replace it with:

```python
def _patch_cli_deps(monkeypatch, rows, *, equity_baseline: float | None = 100_000.0):
    """Stub all I/O dependencies for session_eval_cli.main()."""
    import alpaca_bot.admin.session_eval_cli as cli_module
    from types import SimpleNamespace

    fake_settings = SimpleNamespace(
        database_url="postgresql://fake/db",
        strategy_version="v1",
        market_timezone=SimpleNamespace(key="America/New_York"),
    )
    fake_settings_cls = SimpleNamespace(from_env=lambda: fake_settings)
    monkeypatch.setattr(cli_module, "Settings", fake_settings_cls)

    fake_conn = SimpleNamespace(close=lambda: None)
    monkeypatch.setattr(cli_module, "connect_postgres", lambda url: fake_conn)

    state = SimpleNamespace(equity_baseline=equity_baseline) if equity_baseline is not None else None
    fake_session_store = SimpleNamespace(load=lambda **kwargs: state)
    fake_order_store = SimpleNamespace(
        list_closed_trades=lambda **kwargs: rows,
        list_failed_entries=lambda **kwargs: [],
    )
    fake_audit_store = SimpleNamespace(list_by_event_types=lambda **kwargs: [])
    fake_position_store = SimpleNamespace(list_all=lambda **kwargs: [])

    monkeypatch.setattr(cli_module, "DailySessionStateStore", lambda conn: fake_session_store)
    monkeypatch.setattr(cli_module, "OrderStore", lambda conn: fake_order_store)
    monkeypatch.setattr(cli_module, "AuditEventStore", lambda conn: fake_audit_store)
    monkeypatch.setattr(cli_module, "PositionStore", lambda conn: fake_position_store)
```

> **Why:** `fake_settings.market_timezone` was previously just `SimpleNamespace(database_url=..., strategy_version=...)` without `market_timezone`. The new `main()` calls `settings.market_timezone.key`. Adding `market_timezone=SimpleNamespace(key="America/New_York")` fixes this.

- [ ] **Step 4.7: Run the full session eval test files**

```bash
pytest tests/unit/test_session_eval.py tests/unit/test_session_eval_diagnostics.py -v
```

Expected: all tests PASS. Specifically:
- `test_session_eval_cli_no_trades_exits_zero` — still passes, now also prints diagnostics section
- `test_session_eval_cli_produces_report` — still passes
- All new diagnostics tests — PASS

- [ ] **Step 4.8: Run the full suite**

```bash
pytest --tb=short -q
```

Expected: all tests pass with no regressions.

- [ ] **Step 4.9: Commit**

```bash
git add src/alpaca_bot/admin/session_eval_cli.py tests/unit/test_session_eval.py tests/unit/test_session_eval_diagnostics.py
git commit -m "feat: add session diagnostics section to alpaca-bot-session-eval"
```

---

## Self-Review

### Spec coverage

| Spec requirement | Task |
|---|---|
| Session window = midnight-to-midnight ET | Task 3, `_build_session_diagnostics` |
| `AuditEventStore.list_by_event_types` `since`/`until` params | Task 1 |
| `OrderStore.list_failed_entries` | Task 2 |
| `SessionDiagnostics` dataclass with 6 fields + `has_issues` | Task 3 |
| `_build_session_diagnostics` queries all 6 categories | Task 3 |
| `_print_session_diagnostics` — "No issues" when clean | Task 4 |
| `_print_session_diagnostics` — ⚠ lines for each category | Task 4 |
| `main()` calls both functions after performance report | Task 4 |
| Also prints diagnostics on "no trades" path | Task 4 |
| Strategy filter: diagnostics are session-wide | `_build_session_diagnostics` uses `strategy_version` not `strategy_name` |
| No new CLI entry point | ✓ (no new `pyproject.toml` changes) |
| No migrations | ✓ |
| Existing `test_session_eval.py` tests still pass | Task 4, step 4.6 |

All spec requirements covered.

### Placeholder scan

No TBD/TODO/placeholder patterns found. All code blocks are complete.

### Type consistency

- `SessionDiagnostics` defined in Task 3, imported in Task 4 print tests via `from alpaca_bot.admin.session_eval_cli import _print_session_diagnostics` — `SessionDiagnostics` is in scope as it was imported in the same test file earlier.
- `_build_session_diagnostics(conn, *, trading_mode, strategy_version, eval_date, market_timezone)` — signature used consistently in Task 3 tests and Task 4 `main()`.
- `list_by_event_types(..., since=..., until=..., limit=...)` — added in Task 1, used in Task 3 with the same param names.
- `list_failed_entries(trading_mode=..., strategy_version=..., session_date=...)` — added in Task 2, used in Task 3 with the same param names.
