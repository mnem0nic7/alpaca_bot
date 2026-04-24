from __future__ import annotations

from datetime import date, datetime, timezone
import json

from alpaca_bot.config import TradingMode
from alpaca_bot.storage import (
    AuditEvent,
    AuditEventStore,
    DailySessionState,
    DailySessionStateStore,
    OrderRecord,
    OrderStore,
    PositionRecord,
    PositionStore,
    PostgresAdvisoryLock,
    TradingStatus,
    TradingStatusStore,
    TradingStatusValue,
    advisory_lock_key,
)


class FakeCursor:
    def __init__(self, connection: "FakeConnection") -> None:
        self._connection = connection

    def execute(self, sql: str, params: tuple[object, ...] | None = None) -> None:
        self._connection.executed.append((sql, params))

    def fetchone(self) -> object:
        if not self._connection.responses:
            return None
        return self._connection.responses.pop(0)

    def fetchall(self) -> list[object]:
        if not self._connection.responses:
            return []
        response = self._connection.responses.pop(0)
        if isinstance(response, list):
            return response
        return [response]


class FakeConnection:
    def __init__(self, responses: list[object] | None = None) -> None:
        self.responses = list(responses or [])
        self.executed: list[tuple[str, tuple[object, ...] | None]] = []
        self.commit_count = 0

    def cursor(self) -> FakeCursor:
        return FakeCursor(self)

    def commit(self) -> None:
        self.commit_count += 1


def test_advisory_lock_reports_success_and_release() -> None:
    connection = FakeConnection(responses=[(True,), (True,)])

    lock = PostgresAdvisoryLock(
        connection,
        strategy_version="v1-breakout",
        trading_mode=TradingMode.PAPER,
    )

    assert lock.try_acquire() is True
    assert lock.release() is True
    assert connection.executed[0][1] == (lock.key,)
    assert connection.executed[1][1] == (lock.key,)


def test_advisory_lock_key_changes_by_mode() -> None:
    paper_key = advisory_lock_key(
        strategy_version="v1-breakout",
        trading_mode=TradingMode.PAPER,
    )
    live_key = advisory_lock_key(
        strategy_version="v1-breakout",
        trading_mode=TradingMode.LIVE,
    )

    assert paper_key != live_key


def test_trading_status_store_round_trip() -> None:
    now = datetime(2026, 4, 24, 20, 0, tzinfo=timezone.utc)
    connection = FakeConnection(
        responses=[
            (
                "paper",
                "v1-breakout",
                "halted",
                True,
                "manual intervention",
                now,
            )
        ]
    )
    store = TradingStatusStore(connection)
    status = TradingStatus(
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        status=TradingStatusValue.HALTED,
        kill_switch_enabled=True,
        status_reason="manual intervention",
        updated_at=now,
    )

    store.save(status)
    loaded = store.load(trading_mode=TradingMode.PAPER, strategy_version="v1-breakout")

    assert loaded == status
    assert connection.commit_count == 1


def test_audit_event_store_appends_json_payload() -> None:
    connection = FakeConnection()
    store = AuditEventStore(connection)
    event = AuditEvent(
        event_type="signal_detected",
        symbol="AAPL",
        payload={"relative_volume": 1.77, "breakout_level": 110.0},
        created_at=datetime(2026, 4, 24, 19, 0, tzinfo=timezone.utc),
    )

    store.append(event)

    assert connection.commit_count == 1
    assert connection.executed[0][1] is not None
    assert connection.executed[0][1][2] == json.dumps(event.payload, sort_keys=True)


def test_order_store_upserts_and_loads_record() -> None:
    now = datetime(2026, 4, 24, 19, 15, tzinfo=timezone.utc)
    signal_timestamp = datetime(2026, 4, 24, 19, 0, tzinfo=timezone.utc)
    connection = FakeConnection(
        responses=[
            (
                "paper:v1:AAPL:2026-04-24T19:00:00Z:entry",
                "AAPL",
                "buy",
                "entry",
                "filled",
                45,
                "paper",
                "v1-breakout",
                now,
                now,
                109.89,
                111.12,
                109.89,
                "broker-123",
                signal_timestamp,
            )
        ]
    )
    store = OrderStore(connection)
    order = OrderRecord(
        client_order_id="paper:v1:AAPL:2026-04-24T19:00:00Z:entry",
        symbol="AAPL",
        side="buy",
        intent_type="entry",
        status="filled",
        quantity=45,
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        stop_price=109.89,
        limit_price=111.12,
        initial_stop_price=109.89,
        broker_order_id="broker-123",
        signal_timestamp=signal_timestamp,
        created_at=now,
        updated_at=now,
    )

    store.save(order)
    loaded = store.load(order.client_order_id)

    assert loaded == order
    assert connection.commit_count == 1


def test_order_store_lists_records_by_status() -> None:
    now = datetime(2026, 4, 24, 19, 15, tzinfo=timezone.utc)
    signal_timestamp = datetime(2026, 4, 24, 19, 0, tzinfo=timezone.utc)
    connection = FakeConnection(
        responses=[
            [
                (
                    "paper:v1:AAPL:2026-04-24T19:00:00Z:entry",
                    "AAPL",
                    "buy",
                    "entry",
                    "pending_submit",
                    45,
                    "paper",
                    "v1-breakout",
                    now,
                    now,
                    109.89,
                    111.12,
                    109.89,
                    None,
                    signal_timestamp,
                ),
                (
                    "paper:v1:AAPL:2026-04-24T19:15:00Z:stop",
                    "AAPL",
                    "sell",
                    "stop",
                    "pending_submit",
                    45,
                    "paper",
                    "v1-breakout",
                    now,
                    now,
                    109.89,
                    None,
                    None,
                    None,
                    signal_timestamp,
                ),
            ]
        ]
    )
    store = OrderStore(connection)

    orders = store.list_by_status(
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        statuses=["pending_submit"],
    )

    assert [order.client_order_id for order in orders] == [
        "paper:v1:AAPL:2026-04-24T19:00:00Z:entry",
        "paper:v1:AAPL:2026-04-24T19:15:00Z:stop",
    ]


def test_daily_session_state_store_round_trip() -> None:
    now = datetime(2026, 4, 24, 20, 0, tzinfo=timezone.utc)
    session = DailySessionState(
        session_date=date(2026, 4, 24),
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        entries_disabled=True,
        flatten_complete=False,
        last_reconciled_at=now,
        notes="reconciliation pending",
        updated_at=now,
    )
    connection = FakeConnection(
        responses=[
            (
                date(2026, 4, 24),
                "paper",
                "v1-breakout",
                True,
                False,
                now,
                "reconciliation pending",
                now,
            )
        ]
    )
    store = DailySessionStateStore(connection)

    store.save(session)
    loaded = store.load(
        session_date=date(2026, 4, 24),
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
    )

    assert loaded == session
    assert connection.commit_count == 1


def test_position_store_save_list_and_delete() -> None:
    now = datetime(2026, 4, 24, 20, 0, tzinfo=timezone.utc)
    connection = FakeConnection(
        responses=[
            [
                (
                    "AAPL",
                    "paper",
                    "v1-breakout",
                    45,
                    111.02,
                    109.89,
                    109.89,
                    now,
                    now,
                )
            ]
        ]
    )
    store = PositionStore(connection)
    position = PositionRecord(
        symbol="AAPL",
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        quantity=45,
        entry_price=111.02,
        stop_price=109.89,
        initial_stop_price=109.89,
        opened_at=now,
        updated_at=now,
    )

    store.save(position)
    listed = store.list_all(
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
    )
    store.delete(
        symbol="AAPL",
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
    )

    assert listed == [position]
    assert connection.commit_count == 2
