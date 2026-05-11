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
