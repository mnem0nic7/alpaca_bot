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
