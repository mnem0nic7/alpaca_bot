from __future__ import annotations

import json
from typing import Any

from alpaca_bot.config import TradingMode
from alpaca_bot.storage.db import ConnectionProtocol, execute, fetch_all, fetch_one
from alpaca_bot.storage.models import (
    AuditEvent,
    DailySessionState,
    OrderRecord,
    PositionRecord,
    TradingStatus,
    TradingStatusValue,
)


class TradingStatusStore:
    def __init__(self, connection: ConnectionProtocol) -> None:
        self._connection = connection

    def save(self, status: TradingStatus) -> None:
        execute(
            self._connection,
            """
            INSERT INTO trading_status (
                trading_mode,
                strategy_version,
                status,
                kill_switch_enabled,
                status_reason,
                updated_at
            )
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (trading_mode, strategy_version)
            DO UPDATE SET
                status = EXCLUDED.status,
                kill_switch_enabled = EXCLUDED.kill_switch_enabled,
                status_reason = EXCLUDED.status_reason,
                updated_at = EXCLUDED.updated_at
            """,
            (
                status.trading_mode.value,
                status.strategy_version,
                status.status.value,
                status.kill_switch_enabled,
                status.status_reason,
                status.updated_at,
            ),
        )

    def load(self, *, trading_mode: TradingMode, strategy_version: str) -> TradingStatus | None:
        row = fetch_one(
            self._connection,
            """
            SELECT trading_mode, strategy_version, status, kill_switch_enabled, status_reason, updated_at
            FROM trading_status
            WHERE trading_mode = %s AND strategy_version = %s
            """,
            (trading_mode.value, strategy_version),
        )
        if row is None:
            return None
        return TradingStatus(
            trading_mode=TradingMode(row[0]),
            strategy_version=row[1],
            status=TradingStatusValue(row[2]),
            kill_switch_enabled=bool(row[3]),
            status_reason=row[4],
            updated_at=row[5],
        )


class AuditEventStore:
    def __init__(self, connection: ConnectionProtocol) -> None:
        self._connection = connection

    def append(self, event: AuditEvent) -> None:
        execute(
            self._connection,
            """
            INSERT INTO audit_events (
                event_type,
                symbol,
                payload,
                created_at
            )
            VALUES (%s, %s, %s::jsonb, %s)
            """,
            (
                event.event_type,
                event.symbol,
                json.dumps(event.payload, sort_keys=True),
                event.created_at,
            ),
        )


class OrderStore:
    def __init__(self, connection: ConnectionProtocol) -> None:
        self._connection = connection

    def save(self, order: OrderRecord) -> None:
        execute(
            self._connection,
            """
            INSERT INTO orders (
                client_order_id,
                symbol,
                side,
                intent_type,
                status,
                quantity,
                trading_mode,
                strategy_version,
                stop_price,
                limit_price,
                initial_stop_price,
                broker_order_id,
                signal_timestamp,
                created_at,
                updated_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (client_order_id)
            DO UPDATE SET
                status = EXCLUDED.status,
                quantity = EXCLUDED.quantity,
                stop_price = EXCLUDED.stop_price,
                limit_price = EXCLUDED.limit_price,
                initial_stop_price = EXCLUDED.initial_stop_price,
                broker_order_id = EXCLUDED.broker_order_id,
                signal_timestamp = EXCLUDED.signal_timestamp,
                updated_at = EXCLUDED.updated_at
            """,
            (
                order.client_order_id,
                order.symbol,
                order.side,
                order.intent_type,
                order.status,
                order.quantity,
                order.trading_mode.value,
                order.strategy_version,
                order.stop_price,
                order.limit_price,
                order.initial_stop_price,
                order.broker_order_id,
                order.signal_timestamp,
                order.created_at,
                order.updated_at,
            ),
        )

    def load(self, client_order_id: str) -> OrderRecord | None:
        row = fetch_one(
            self._connection,
            """
            SELECT
                client_order_id,
                symbol,
                side,
                intent_type,
                status,
                quantity,
                trading_mode,
                strategy_version,
                created_at,
                updated_at,
                stop_price,
                limit_price,
                initial_stop_price,
                broker_order_id,
                signal_timestamp
            FROM orders
            WHERE client_order_id = %s
            """,
            (client_order_id,),
        )
        if row is None:
            return None
        return OrderRecord(
            client_order_id=row[0],
            symbol=row[1],
            side=row[2],
            intent_type=row[3],
            status=row[4],
            quantity=int(row[5]),
            trading_mode=TradingMode(row[6]),
            strategy_version=row[7],
            created_at=row[8],
            updated_at=row[9],
            stop_price=row[10],
            limit_price=row[11],
            initial_stop_price=row[12],
            broker_order_id=row[13],
            signal_timestamp=row[14],
        )

    def load_by_broker_order_id(self, broker_order_id: str) -> OrderRecord | None:
        row = fetch_one(
            self._connection,
            """
            SELECT
                client_order_id,
                symbol,
                side,
                intent_type,
                status,
                quantity,
                trading_mode,
                strategy_version,
                created_at,
                updated_at,
                stop_price,
                limit_price,
                initial_stop_price,
                broker_order_id,
                signal_timestamp
            FROM orders
            WHERE broker_order_id = %s
            ORDER BY updated_at DESC
            LIMIT 1
            """,
            (broker_order_id,),
        )
        if row is None:
            return None
        return OrderRecord(
            client_order_id=row[0],
            symbol=row[1],
            side=row[2],
            intent_type=row[3],
            status=row[4],
            quantity=int(row[5]),
            trading_mode=TradingMode(row[6]),
            strategy_version=row[7],
            created_at=row[8],
            updated_at=row[9],
            stop_price=row[10],
            limit_price=row[11],
            initial_stop_price=row[12],
            broker_order_id=row[13],
            signal_timestamp=row[14],
        )

    def list_by_status(
        self,
        *,
        trading_mode: TradingMode,
        strategy_version: str,
        statuses: list[str],
    ) -> list[OrderRecord]:
        if not statuses:
            return []
        placeholders = ", ".join(["%s"] * len(statuses))
        rows = fetch_all(
            self._connection,
            f"""
            SELECT
                client_order_id,
                symbol,
                side,
                intent_type,
                status,
                quantity,
                trading_mode,
                strategy_version,
                created_at,
                updated_at,
                stop_price,
                limit_price,
                initial_stop_price,
                broker_order_id,
                signal_timestamp
            FROM orders
            WHERE trading_mode = %s
              AND strategy_version = %s
              AND status IN ({placeholders})
            ORDER BY created_at, client_order_id
            """,
            (trading_mode.value, strategy_version, *statuses),
        )
        return [
            OrderRecord(
                client_order_id=row[0],
                symbol=row[1],
                side=row[2],
                intent_type=row[3],
                status=row[4],
                quantity=int(row[5]),
                trading_mode=TradingMode(row[6]),
                strategy_version=row[7],
                created_at=row[8],
                updated_at=row[9],
                stop_price=row[10],
                limit_price=row[11],
                initial_stop_price=row[12],
                broker_order_id=row[13],
                signal_timestamp=row[14],
            )
            for row in rows
        ]

    def list_pending_submit(
        self,
        *,
        trading_mode: TradingMode,
        strategy_version: str,
    ) -> list[OrderRecord]:
        return self.list_by_status(
            trading_mode=trading_mode,
            strategy_version=strategy_version,
            statuses=["pending_submit"],
        )


class DailySessionStateStore:
    def __init__(self, connection: ConnectionProtocol) -> None:
        self._connection = connection

    def save(self, state: DailySessionState) -> None:
        execute(
            self._connection,
            """
            INSERT INTO daily_session_state (
                session_date,
                trading_mode,
                strategy_version,
                entries_disabled,
                flatten_complete,
                last_reconciled_at,
                notes,
                updated_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (session_date, trading_mode, strategy_version)
            DO UPDATE SET
                entries_disabled = EXCLUDED.entries_disabled,
                flatten_complete = EXCLUDED.flatten_complete,
                last_reconciled_at = EXCLUDED.last_reconciled_at,
                notes = EXCLUDED.notes,
                updated_at = EXCLUDED.updated_at
            """,
            (
                state.session_date,
                state.trading_mode.value,
                state.strategy_version,
                state.entries_disabled,
                state.flatten_complete,
                state.last_reconciled_at,
                state.notes,
                state.updated_at,
            ),
        )

    def load(
        self,
        *,
        session_date: Any,
        trading_mode: TradingMode,
        strategy_version: str,
    ) -> DailySessionState | None:
        row = fetch_one(
            self._connection,
            """
            SELECT
                session_date,
                trading_mode,
                strategy_version,
                entries_disabled,
                flatten_complete,
                last_reconciled_at,
                notes,
                updated_at
            FROM daily_session_state
            WHERE session_date = %s AND trading_mode = %s AND strategy_version = %s
            """,
            (session_date, trading_mode.value, strategy_version),
        )
        if row is None:
            return None
        return DailySessionState(
            session_date=row[0],
            trading_mode=TradingMode(row[1]),
            strategy_version=row[2],
            entries_disabled=bool(row[3]),
            flatten_complete=bool(row[4]),
            last_reconciled_at=row[5],
            notes=row[6],
            updated_at=row[7],
        )


class PositionStore:
    def __init__(self, connection: ConnectionProtocol) -> None:
        self._connection = connection

    def save(self, position: PositionRecord) -> None:
        execute(
            self._connection,
            """
            INSERT INTO positions (
                symbol,
                trading_mode,
                strategy_version,
                quantity,
                entry_price,
                stop_price,
                initial_stop_price,
                opened_at,
                updated_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (symbol, trading_mode, strategy_version)
            DO UPDATE SET
                quantity = EXCLUDED.quantity,
                entry_price = EXCLUDED.entry_price,
                stop_price = EXCLUDED.stop_price,
                initial_stop_price = EXCLUDED.initial_stop_price,
                opened_at = EXCLUDED.opened_at,
                updated_at = EXCLUDED.updated_at
            """,
            (
                position.symbol,
                position.trading_mode.value,
                position.strategy_version,
                position.quantity,
                position.entry_price,
                position.stop_price,
                position.initial_stop_price,
                position.opened_at,
                position.updated_at,
            ),
        )

    def replace_all(
        self,
        *,
        positions: list[PositionRecord],
        trading_mode: TradingMode,
        strategy_version: str,
    ) -> None:
        execute(
            self._connection,
            """
            DELETE FROM positions
            WHERE trading_mode = %s AND strategy_version = %s
            """,
            (trading_mode.value, strategy_version),
        )
        for position in positions:
            self.save(position)

    def delete(
        self,
        *,
        symbol: str,
        trading_mode: TradingMode,
        strategy_version: str,
    ) -> None:
        execute(
            self._connection,
            """
            DELETE FROM positions
            WHERE symbol = %s AND trading_mode = %s AND strategy_version = %s
            """,
            (symbol, trading_mode.value, strategy_version),
        )

    def list_all(
        self,
        *,
        trading_mode: TradingMode,
        strategy_version: str,
    ) -> list[PositionRecord]:
        cursor = self._connection.cursor()
        cursor.execute(
            """
            SELECT
                symbol,
                trading_mode,
                strategy_version,
                quantity,
                entry_price,
                stop_price,
                initial_stop_price,
                opened_at,
                updated_at
            FROM positions
            WHERE trading_mode = %s AND strategy_version = %s
            ORDER BY symbol
            """,
            (trading_mode.value, strategy_version),
        )
        rows = cursor.fetchall()
        return [
            PositionRecord(
                symbol=row[0],
                trading_mode=TradingMode(row[1]),
                strategy_version=row[2],
                quantity=int(row[3]),
                entry_price=float(row[4]),
                stop_price=float(row[5]),
                initial_stop_price=float(row[6]),
                opened_at=row[7],
                updated_at=row[8],
            )
            for row in rows
        ]
