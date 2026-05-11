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
