from __future__ import annotations

import json
import logging
import uuid as _uuid_module

logger = logging.getLogger(__name__)
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from alpaca_bot.config import TradingMode
from alpaca_bot.domain.models import MarketContext
from alpaca_bot.storage.db import ConnectionProtocol, execute, fetch_all, fetch_one
from alpaca_bot.storage.models import (
    AuditEvent,
    ConfidenceFloor,
    DailySessionState,
    EQUITY_SESSION_STATE_STRATEGY_NAME,
    OptionOrderRecord,
    OrderRecord,
    PositionRecord,
    StrategyFlag,
    StrategyWeight,
    TradingStatus,
    TradingStatusValue,
)


class TradingStatusStore:
    def __init__(self, connection: ConnectionProtocol) -> None:
        self._connection = connection

    def save(self, status: TradingStatus, *, commit: bool = True) -> None:
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
            commit=commit,
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

    def append(self, event: AuditEvent, *, commit: bool = True) -> None:
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
            commit=commit,
        )

    def list_recent(self, *, limit: int = 20, offset: int = 0) -> list[AuditEvent]:
        rows = fetch_all(
            self._connection,
            """
            SELECT event_type, symbol, payload, created_at
            FROM audit_events
            ORDER BY created_at DESC, event_id DESC
            LIMIT %s
            OFFSET %s
            """,
            (limit, offset),
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

    def load_latest(self, *, event_types: list[str]) -> AuditEvent | None:
        if not event_types:
            return None
        placeholders = ", ".join(["%s"] * len(event_types))
        row = fetch_one(
            self._connection,
            f"""
            SELECT event_type, symbol, payload, created_at
            FROM audit_events
            WHERE event_type IN ({placeholders})
            ORDER BY created_at DESC, event_id DESC
            LIMIT 1
            """,
            tuple(event_types),
        )
        if row is None:
            return None
        return AuditEvent(
            event_type=row[0],
            symbol=row[1],
            payload=_load_json_payload(row[2]),
            created_at=row[3],
        )

    def list_by_event_types(
        self,
        *,
        event_types: list[str],
        limit: int = 20,
        offset: int = 0,
    ) -> list[AuditEvent]:
        if not event_types:
            return []
        placeholders = ", ".join(["%s"] * len(event_types))
        rows = fetch_all(
            self._connection,
            f"""
            SELECT event_type, symbol, payload, created_at
            FROM audit_events
            WHERE event_type IN ({placeholders})
            ORDER BY created_at DESC, event_id DESC
            LIMIT %s
            OFFSET %s
            """,
            (*event_types, limit, offset),
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


_ORDER_SELECT_COLUMNS = """
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
    signal_timestamp,
    fill_price,
    filled_quantity,
    strategy_name,
    reconciliation_miss_count
"""


def _row_to_order_record(row: Any) -> OrderRecord:
    return OrderRecord(
        client_order_id=row[0],
        symbol=row[1],
        side=row[2],
        intent_type=row[3],
        status=row[4],
        quantity=float(row[5]),
        trading_mode=TradingMode(row[6]),
        strategy_version=row[7],
        created_at=row[8],
        updated_at=row[9],
        stop_price=float(row[10]) if row[10] is not None else None,
        limit_price=float(row[11]) if row[11] is not None else None,
        initial_stop_price=float(row[12]) if row[12] is not None else None,
        broker_order_id=row[13],
        signal_timestamp=row[14],
        fill_price=float(row[15]) if row[15] is not None else None,
        filled_quantity=float(row[16]) if row[16] is not None else None,
        strategy_name=row[17] if row[17] is not None else "breakout",
        reconciliation_miss_count=int(row[18]) if row[18] is not None else 0,
    )


class OrderStore:
    def __init__(self, connection: ConnectionProtocol) -> None:
        self._connection = connection

    def save(self, order: OrderRecord, *, commit: bool = True) -> None:
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
                strategy_name,
                stop_price,
                limit_price,
                initial_stop_price,
                broker_order_id,
                signal_timestamp,
                fill_price,
                filled_quantity,
                created_at,
                updated_at,
                reconciliation_miss_count
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (client_order_id)
            DO UPDATE SET
                status = EXCLUDED.status,
                quantity = EXCLUDED.quantity,
                stop_price = COALESCE(EXCLUDED.stop_price, orders.stop_price),
                limit_price = COALESCE(EXCLUDED.limit_price, orders.limit_price),
                initial_stop_price = COALESCE(EXCLUDED.initial_stop_price, orders.initial_stop_price),
                broker_order_id = EXCLUDED.broker_order_id,
                signal_timestamp = EXCLUDED.signal_timestamp,
                fill_price = EXCLUDED.fill_price,
                filled_quantity = EXCLUDED.filled_quantity,
                updated_at = EXCLUDED.updated_at,
                reconciliation_miss_count = EXCLUDED.reconciliation_miss_count
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
                order.strategy_name,
                order.stop_price,
                order.limit_price,
                order.initial_stop_price,
                order.broker_order_id,
                order.signal_timestamp,
                order.fill_price,
                order.filled_quantity,
                order.created_at,
                order.updated_at,
                order.reconciliation_miss_count,
            ),
            commit=commit,
        )

    def load(self, client_order_id: str) -> OrderRecord | None:
        row = fetch_one(
            self._connection,
            f"SELECT {_ORDER_SELECT_COLUMNS} FROM orders WHERE client_order_id = %s",
            (client_order_id,),
        )
        return _row_to_order_record(row) if row is not None else None

    def load_by_broker_order_id(self, broker_order_id: str) -> OrderRecord | None:
        row = fetch_one(
            self._connection,
            f"""
            SELECT {_ORDER_SELECT_COLUMNS}
            FROM orders
            WHERE broker_order_id = %s
            ORDER BY updated_at DESC
            LIMIT 1
            """,
            (broker_order_id,),
        )
        return _row_to_order_record(row) if row is not None else None

    def list_by_status(
        self,
        *,
        trading_mode: TradingMode,
        strategy_version: str,
        statuses: list[str],
        strategy_name: str | None = None,
    ) -> list[OrderRecord]:
        if not statuses:
            return []
        placeholders = ", ".join(["%s"] * len(statuses))
        strategy_clause = "AND strategy_name IS NOT DISTINCT FROM %s" if strategy_name is not None else ""
        strategy_params = (strategy_name,) if strategy_name is not None else ()
        rows = fetch_all(
            self._connection,
            f"""
            SELECT {_ORDER_SELECT_COLUMNS}
            FROM orders
            WHERE trading_mode = %s
              AND strategy_version = %s
              AND status IN ({placeholders})
              {strategy_clause}
            ORDER BY created_at, client_order_id
            """,
            (trading_mode.value, strategy_version, *statuses, *strategy_params),
        )
        return [_row_to_order_record(row) for row in rows]

    def list_pending_submit(
        self,
        *,
        trading_mode: TradingMode,
        strategy_version: str,
        strategy_name: str | None = None,
    ) -> list[OrderRecord]:
        return self.list_by_status(
            trading_mode=trading_mode,
            strategy_version=strategy_version,
            statuses=["pending_submit"],
            strategy_name=strategy_name,
        )

    def list_recent(
        self,
        *,
        trading_mode: TradingMode,
        strategy_version: str,
        limit: int = 20,
    ) -> list[OrderRecord]:
        rows = fetch_all(
            self._connection,
            f"""
            SELECT {_ORDER_SELECT_COLUMNS}
            FROM orders
            WHERE trading_mode = %s
              AND strategy_version = %s
            ORDER BY created_at DESC, client_order_id DESC
            LIMIT %s
            """,
            (trading_mode.value, strategy_version, limit),
        )
        return [_row_to_order_record(row) for row in rows]

    def daily_realized_pnl(
        self,
        *,
        trading_mode: TradingMode,
        strategy_version: str,
        session_date: date,
        strategy_name: str | None = None,
        market_timezone: str = "America/New_York",
    ) -> float:
        """Return sum of closed-trade PnL for a session date.

        For each exit/stop order with a fill price, looks up the most recent
        filled entry for the same symbol via correlated subquery (safe even if
        the one-trade-per-symbol invariant is ever violated).
        Returns 0.0 when no completed round-trip trades exist.
        Pass strategy_name to restrict to a single strategy; omit for portfolio-wide PnL.
        """
        if strategy_name is not None:
            strategy_clause = "AND x.strategy_name IS NOT DISTINCT FROM %s"
            strategy_params: tuple = (strategy_name,)
        else:
            strategy_clause = ""
            strategy_params = ()
        rows = fetch_all(
            self._connection,
            f"""
            SELECT
                x.symbol,
                (
                    SELECT e.fill_price
                    FROM orders e
                    WHERE e.symbol = x.symbol
                      AND e.trading_mode = x.trading_mode
                      AND e.strategy_version = x.strategy_version
                      AND e.strategy_name IS NOT DISTINCT FROM x.strategy_name
                      AND e.intent_type = 'entry'
                      AND e.fill_price IS NOT NULL
                      AND e.status = 'filled'
                      AND e.updated_at <= x.updated_at
                    ORDER BY e.updated_at DESC
                    LIMIT 1
                ) AS entry_fill,
                x.fill_price AS exit_fill,
                COALESCE(x.filled_quantity, x.quantity) AS qty
            FROM orders x
            WHERE x.trading_mode = %s
              AND x.strategy_version = %s
              AND x.intent_type IN ('stop', 'exit')
              AND x.fill_price IS NOT NULL
              AND x.status = 'filled'
              AND DATE(x.updated_at AT TIME ZONE %s) = %s
              {strategy_clause}
            """,
            (
                trading_mode.value,
                strategy_version,
                market_timezone,
                session_date,
                *strategy_params,
            ),
        )
        missing_entry = [row for row in rows if row[1] is None]
        if missing_entry:
            logger.error(
                "daily_realized_pnl: %d exit row(s) have no correlated entry fill "
                "(symbols: %s); treating as full loss to fail safe on loss-limit check",
                len(missing_entry),
                [row[0] for row in missing_entry],
            )
        return sum(
            (float(row[2]) - float(row[1])) * float(row[3])
            if row[1] is not None
            else -(float(row[2]) * float(row[3]))
            for row in rows
            if row[2] is not None
        )

    def daily_realized_pnl_by_symbol(
        self,
        *,
        trading_mode: TradingMode,
        strategy_version: str,
        session_date: date,
        strategy_name: str | None = None,
        market_timezone: str = "America/New_York",
    ) -> dict[str, float]:
        """Return realized PnL keyed by symbol for a session date.

        Uses the same correlated-subquery pattern as daily_realized_pnl.
        Symbols with no correlated entry fill are treated as full losses (fail-safe).
        Returns an empty dict when no completed round-trip trades exist.
        """
        if strategy_name is not None:
            strategy_clause = "AND x.strategy_name IS NOT DISTINCT FROM %s"
            strategy_params: tuple = (strategy_name,)
        else:
            strategy_clause = ""
            strategy_params = ()
        rows = fetch_all(
            self._connection,
            f"""
            SELECT
                x.symbol,
                (
                    SELECT e.fill_price
                    FROM orders e
                    WHERE e.symbol = x.symbol
                      AND e.trading_mode = x.trading_mode
                      AND e.strategy_version = x.strategy_version
                      AND e.strategy_name IS NOT DISTINCT FROM x.strategy_name
                      AND e.intent_type = 'entry'
                      AND e.fill_price IS NOT NULL
                      AND e.status = 'filled'
                      AND e.updated_at <= x.updated_at
                    ORDER BY e.updated_at DESC
                    LIMIT 1
                ) AS entry_fill,
                x.fill_price AS exit_fill,
                COALESCE(x.filled_quantity, x.quantity) AS qty
            FROM orders x
            WHERE x.trading_mode = %s
              AND x.strategy_version = %s
              AND x.intent_type IN ('stop', 'exit')
              AND x.fill_price IS NOT NULL
              AND x.status = 'filled'
              AND DATE(x.updated_at AT TIME ZONE %s) = %s
              {strategy_clause}
            """,
            (
                trading_mode.value,
                strategy_version,
                market_timezone,
                session_date,
                *strategy_params,
            ),
        )
        missing_entry = [row for row in rows if row[1] is None]
        if missing_entry:
            logger.error(
                "daily_realized_pnl_by_symbol: %d exit row(s) have no correlated entry fill "
                "(symbols: %s); treating as full loss to fail safe on per-symbol loss-limit check",
                len(missing_entry),
                [row[0] for row in missing_entry],
            )
        result: dict[str, float] = {}
        for row in rows:
            if row[2] is None:
                continue
            symbol = row[0]
            entry_fill = row[1]
            exit_fill = float(row[2])
            qty = float(row[3])
            pnl = (
                (exit_fill - float(entry_fill)) * qty
                if entry_fill is not None
                else -(exit_fill * qty)
            )
            result[symbol] = result.get(symbol, 0.0) + pnl
        return result

    def list_closed_trades(
        self,
        *,
        trading_mode: TradingMode,
        strategy_version: str,
        session_date: date,
        strategy_name: str | None = None,
        market_timezone: str = "America/New_York",
    ) -> list[dict]:
        """Return one dict per closed round-trip trade for a session date.

        Uses the same correlated-subquery pattern as daily_realized_pnl to
        look up entry fill data without risking Cartesian-product duplicates.
        Rows where entry_fill or exit_fill is NULL are excluded.
        """
        strategy_clause = "AND x.strategy_name IS NOT DISTINCT FROM %s" if strategy_name is not None else ""
        strategy_params = (strategy_name,) if strategy_name is not None else ()
        rows = fetch_all(
            self._connection,
            f"""
            SELECT
                x.symbol,
                x.strategy_name,
                x.intent_type,
                (
                    SELECT e.fill_price
                    FROM orders e
                    WHERE e.symbol = x.symbol
                      AND e.trading_mode = x.trading_mode
                      AND e.strategy_version = x.strategy_version
                      AND e.strategy_name IS NOT DISTINCT FROM x.strategy_name
                      AND e.intent_type = 'entry'
                      AND e.fill_price IS NOT NULL
                      AND e.status = 'filled'
                      AND e.updated_at <= x.updated_at
                    ORDER BY e.updated_at DESC LIMIT 1
                ) AS entry_fill,
                (
                    SELECT e.limit_price
                    FROM orders e
                    WHERE e.symbol = x.symbol
                      AND e.trading_mode = x.trading_mode
                      AND e.strategy_version = x.strategy_version
                      AND e.strategy_name IS NOT DISTINCT FROM x.strategy_name
                      AND e.intent_type = 'entry'
                      AND e.fill_price IS NOT NULL
                      AND e.status = 'filled'
                      AND e.updated_at <= x.updated_at
                    ORDER BY e.updated_at DESC LIMIT 1
                ) AS entry_limit,
                (
                    SELECT e.updated_at
                    FROM orders e
                    WHERE e.symbol = x.symbol
                      AND e.trading_mode = x.trading_mode
                      AND e.strategy_version = x.strategy_version
                      AND e.strategy_name IS NOT DISTINCT FROM x.strategy_name
                      AND e.intent_type = 'entry'
                      AND e.fill_price IS NOT NULL
                      AND e.status = 'filled'
                      AND e.updated_at <= x.updated_at
                    ORDER BY e.updated_at DESC LIMIT 1
                ) AS entry_time,
                x.fill_price AS exit_fill,
                x.updated_at AS exit_time,
                COALESCE(x.filled_quantity, x.quantity) AS qty
            FROM orders x
            WHERE x.trading_mode = %s
              AND x.strategy_version = %s
              AND x.intent_type IN ('stop', 'exit')
              AND x.fill_price IS NOT NULL
              AND x.status = 'filled'
              AND DATE(x.updated_at AT TIME ZONE %s) = %s
              {strategy_clause}
            ORDER BY x.updated_at
            """,
            (
                trading_mode.value,
                strategy_version,
                market_timezone,
                session_date,
                *strategy_params,
            ),
        )
        return [
            {
                "symbol": row[0],
                "strategy_name": row[1],
                "intent_type": row[2],
                "entry_fill": float(row[3]) if row[3] is not None else None,
                "entry_limit": float(row[4]) if row[4] is not None else None,
                "entry_time": row[5],
                "exit_fill": float(row[6]) if row[6] is not None else None,
                "exit_time": row[7],
                "qty": float(row[8]),
            }
            for row in rows
            if row[3] is not None and row[6] is not None
        ]

    def list_trade_exits_in_range(
        self,
        *,
        trading_mode: TradingMode,
        strategy_version: str,
        start_date: date,
        end_date: date,
        market_timezone: str = "America/New_York",
    ) -> list[dict]:
        """Return one dict per exit (stop/exit) order in the date range.

        Derives entry_fill from the most recent correlated entry order.
        Filters out rows where entry_fill is NULL (no correlated entry).
        Each dict contains: exit_time, pnl.
        """
        rows = fetch_all(
            self._connection,
            """
            SELECT x.updated_at AS exit_time,
                   COALESCE(x.filled_quantity, x.quantity) AS qty,
                   x.fill_price AS exit_fill,
                   (SELECT e.fill_price
                      FROM orders e
                     WHERE e.symbol = x.symbol
                       AND e.trading_mode = x.trading_mode
                       AND e.strategy_version = x.strategy_version
                       AND e.strategy_name IS NOT DISTINCT FROM x.strategy_name
                       AND e.intent_type = 'entry'
                       AND e.fill_price IS NOT NULL
                       AND e.status = 'filled'
                       AND e.updated_at <= x.updated_at
                     ORDER BY e.updated_at DESC
                     LIMIT 1) AS entry_fill
              FROM orders x
             WHERE x.trading_mode = %s
               AND x.strategy_version = %s
               AND x.intent_type IN ('stop', 'exit')
               AND x.fill_price IS NOT NULL
               AND x.status = 'filled'
               AND DATE(x.updated_at AT TIME ZONE %s) >= %s
               AND DATE(x.updated_at AT TIME ZONE %s) <= %s
             ORDER BY x.updated_at
            """,
            (
                trading_mode.value,
                strategy_version,
                market_timezone,
                start_date,
                market_timezone,
                end_date,
            ),
        )
        return [
            {"exit_time": row[0], "pnl": (float(row[2]) - float(row[3])) * float(row[1])}
            for row in rows
            if row[3] is not None
        ]

    def list_trade_pnl_by_strategy(
        self,
        *,
        trading_mode: TradingMode,
        strategy_version: str,
        start_date: date,
        end_date: date,
        market_timezone: str = "America/New_York",
    ) -> list[dict]:
        """Return one dict per closed trade in the date range with strategy attribution.

        Each dict: {strategy_name: str, exit_date: date, pnl: float}
        Filters out trades where entry_fill is NULL (no correlated entry order).
        """
        rows = fetch_all(
            self._connection,
            """
            SELECT x.strategy_name,
                   DATE(x.updated_at AT TIME ZONE %s) AS exit_date,
                   COALESCE(x.filled_quantity, x.quantity) AS qty,
                   x.fill_price AS exit_fill,
                   (SELECT e.fill_price
                      FROM orders e
                     WHERE e.symbol = x.symbol
                       AND e.trading_mode = x.trading_mode
                       AND e.strategy_version = x.strategy_version
                       AND e.strategy_name IS NOT DISTINCT FROM x.strategy_name
                       AND e.intent_type = 'entry'
                       AND e.fill_price IS NOT NULL
                       AND e.status = 'filled'
                       AND e.updated_at <= x.updated_at
                     ORDER BY e.updated_at DESC
                     LIMIT 1) AS entry_fill
              FROM orders x
             WHERE x.trading_mode = %s
               AND x.strategy_version = %s
               AND x.intent_type IN ('stop', 'exit')
               AND x.fill_price IS NOT NULL
               AND x.status = 'filled'
               AND DATE(x.updated_at AT TIME ZONE %s) >= %s
               AND DATE(x.updated_at AT TIME ZONE %s) <= %s
             ORDER BY x.updated_at
            """,
            (
                market_timezone,
                trading_mode.value,
                strategy_version,
                market_timezone,
                start_date,
                market_timezone,
                end_date,
            ),
        )
        return [
            {
                "strategy_name": row[0],
                "exit_date": row[1],
                "pnl": (float(row[3]) - float(row[4])) * float(row[2]),
            }
            for row in rows
            if row[4] is not None
        ]

    def win_loss_counts_by_strategy(
        self,
        *,
        trading_mode: TradingMode,
        strategy_version: str,
    ) -> dict[str, tuple[int, int]]:
        rows = fetch_all(
            self._connection,
            """
            WITH trade_pnl AS (
                SELECT x.strategy_name,
                       (x.fill_price - e.fill_price)
                           * COALESCE(x.filled_quantity, x.quantity) AS pnl
                  FROM orders x
                  JOIN LATERAL (
                      SELECT fill_price
                        FROM orders e
                       WHERE e.symbol = x.symbol
                         AND e.trading_mode = x.trading_mode
                         AND e.strategy_version = x.strategy_version
                         AND e.strategy_name IS NOT DISTINCT FROM x.strategy_name
                         AND e.intent_type = 'entry'
                         AND e.fill_price IS NOT NULL
                         AND e.status = 'filled'
                         AND e.updated_at <= x.updated_at
                       ORDER BY e.updated_at DESC
                       LIMIT 1
                  ) e ON true
                 WHERE x.trading_mode = %s
                   AND x.strategy_version = %s
                   AND x.intent_type IN ('stop', 'exit')
                   AND x.fill_price IS NOT NULL
                   AND x.status = 'filled'
            )
            SELECT strategy_name,
                   COUNT(*) FILTER (WHERE pnl > 0)  AS wins,
                   COUNT(*) FILTER (WHERE pnl <= 0) AS losses
              FROM trade_pnl
             GROUP BY strategy_name
            """,
            (trading_mode.value, strategy_version),
        )
        return {row[0]: (int(row[1]), int(row[2])) for row in rows}

    def lifetime_pnl_by_strategy(
        self,
        *,
        trading_mode: TradingMode,
        strategy_version: str,
    ) -> dict[str, float]:
        rows = fetch_all(
            self._connection,
            """
            WITH trade_pnl AS (
                SELECT x.strategy_name,
                       (x.fill_price - e.fill_price)
                           * COALESCE(x.filled_quantity, x.quantity) AS pnl
                  FROM orders x
                  JOIN LATERAL (
                      SELECT fill_price
                        FROM orders e
                       WHERE e.symbol = x.symbol
                         AND e.trading_mode = x.trading_mode
                         AND e.strategy_version = x.strategy_version
                         AND e.strategy_name IS NOT DISTINCT FROM x.strategy_name
                         AND e.intent_type = 'entry'
                         AND e.fill_price IS NOT NULL
                         AND e.status = 'filled'
                         AND e.updated_at <= x.updated_at
                       ORDER BY e.updated_at DESC
                       LIMIT 1
                  ) e ON true
                 WHERE x.trading_mode = %s
                   AND x.strategy_version = %s
                   AND x.intent_type IN ('stop', 'exit')
                   AND x.fill_price IS NOT NULL
                   AND x.status = 'filled'
            )
            SELECT strategy_name,
                   SUM(pnl) AS total_pnl
              FROM trade_pnl
             GROUP BY strategy_name
            """,
            (trading_mode.value, strategy_version),
        )
        return {row[0]: float(row[1]) for row in rows if row[0] is not None}


class DailySessionStateStore:
    def __init__(self, connection: ConnectionProtocol) -> None:
        self._connection = connection

    def save(self, state: DailySessionState, *, commit: bool = True) -> None:
        execute(
            self._connection,
            """
            INSERT INTO daily_session_state (
                session_date,
                trading_mode,
                strategy_version,
                strategy_name,
                entries_disabled,
                flatten_complete,
                last_reconciled_at,
                notes,
                equity_baseline,
                updated_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (session_date, trading_mode, strategy_version, strategy_name)
            DO UPDATE SET
                entries_disabled = EXCLUDED.entries_disabled,
                flatten_complete = EXCLUDED.flatten_complete,
                last_reconciled_at = EXCLUDED.last_reconciled_at,
                notes = EXCLUDED.notes,
                equity_baseline = COALESCE(EXCLUDED.equity_baseline, daily_session_state.equity_baseline),
                updated_at = EXCLUDED.updated_at
            """,
            (
                state.session_date,
                state.trading_mode.value,
                state.strategy_version,
                state.strategy_name,
                state.entries_disabled,
                state.flatten_complete,
                state.last_reconciled_at,
                state.notes,
                state.equity_baseline,
                state.updated_at,
            ),
            commit=commit,
        )

    def load(
        self,
        *,
        session_date: Any,
        trading_mode: TradingMode,
        strategy_version: str,
        strategy_name: str = "breakout",
    ) -> DailySessionState | None:
        row = fetch_one(
            self._connection,
            """
            SELECT
                session_date,
                trading_mode,
                strategy_version,
                strategy_name,
                entries_disabled,
                flatten_complete,
                last_reconciled_at,
                notes,
                equity_baseline,
                updated_at
            FROM daily_session_state
            WHERE session_date = %s AND trading_mode = %s AND strategy_version = %s
              AND strategy_name IS NOT DISTINCT FROM %s
            """,
            (session_date, trading_mode.value, strategy_version, strategy_name),
        )
        if row is None:
            return None
        return DailySessionState(
            session_date=row[0],
            trading_mode=TradingMode(row[1]),
            strategy_version=row[2],
            strategy_name=row[3],
            entries_disabled=bool(row[4]),
            flatten_complete=bool(row[5]),
            last_reconciled_at=row[6],
            notes=row[7],
            equity_baseline=float(row[8]) if row[8] is not None else None,
            updated_at=row[9],
        )


    def list_by_session(
        self,
        *,
        session_date: Any,
        trading_mode: TradingMode,
        strategy_version: str,
    ) -> list[DailySessionState]:
        rows = fetch_all(
            self._connection,
            """
            SELECT
                session_date, trading_mode, strategy_version, strategy_name,
                entries_disabled, flatten_complete, last_reconciled_at,
                notes, equity_baseline, updated_at
            FROM daily_session_state
            WHERE session_date = %s
              AND trading_mode = %s
              AND strategy_version = %s
            """,
            (session_date, trading_mode.value, strategy_version),
        )
        return [
            DailySessionState(
                session_date=row[0],
                trading_mode=TradingMode(row[1]),
                strategy_version=row[2],
                strategy_name=row[3],
                entries_disabled=bool(row[4]),
                flatten_complete=bool(row[5]),
                last_reconciled_at=row[6],
                notes=row[7],
                equity_baseline=float(row[8]) if row[8] is not None else None,
                updated_at=row[9],
            )
            for row in rows
        ]

    def list_equity_baselines(
        self,
        *,
        trading_mode: TradingMode,
        strategy_version: str,
        start_date: date,
        end_date: date,
    ) -> dict[date, float]:
        """Return dict mapping session_date to equity_baseline for the date range.

        Filters to EQUITY_SESSION_STATE_STRATEGY_NAME and non-NULL equity_baseline values.
        Returns {date: float, ...} keyed by session_date.
        """
        rows = fetch_all(
            self._connection,
            """
            SELECT session_date, equity_baseline
              FROM daily_session_state
             WHERE trading_mode = %s
               AND strategy_version = %s
               AND strategy_name IS NOT DISTINCT FROM %s
               AND equity_baseline IS NOT NULL
               AND session_date >= %s
               AND session_date <= %s
             ORDER BY session_date
            """,
            (
                trading_mode.value,
                strategy_version,
                EQUITY_SESSION_STATE_STRATEGY_NAME,
                start_date,
                end_date,
            ),
        )
        return {row[0]: float(row[1]) for row in rows}


class PositionStore:
    def __init__(self, connection: ConnectionProtocol) -> None:
        self._connection = connection

    def save(self, position: PositionRecord, *, commit: bool = True) -> None:
        execute(
            self._connection,
            """
            INSERT INTO positions (
                symbol,
                trading_mode,
                strategy_version,
                strategy_name,
                quantity,
                entry_price,
                stop_price,
                initial_stop_price,
                opened_at,
                updated_at,
                highest_price
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (symbol, trading_mode, strategy_version, strategy_name)
            DO UPDATE SET
                quantity = EXCLUDED.quantity,
                entry_price = EXCLUDED.entry_price,
                stop_price = EXCLUDED.stop_price,
                initial_stop_price = EXCLUDED.initial_stop_price,
                opened_at = EXCLUDED.opened_at,
                updated_at = EXCLUDED.updated_at,
                highest_price = COALESCE(EXCLUDED.highest_price, positions.highest_price)
            """,
            (
                position.symbol,
                position.trading_mode.value,
                position.strategy_version,
                position.strategy_name,
                position.quantity,
                position.entry_price,
                position.stop_price,
                position.initial_stop_price,
                position.opened_at,
                position.updated_at,
                position.highest_price,
            ),
            commit=commit,
        )

    def replace_all(
        self,
        *,
        positions: list[PositionRecord],
        trading_mode: TradingMode,
        strategy_version: str,
        strategy_name: str | None = None,
        commit: bool = True,
    ) -> None:
        try:
            if strategy_name is not None:
                execute(
                    self._connection,
                    "DELETE FROM positions WHERE trading_mode = %s AND strategy_version = %s AND strategy_name = %s",
                    (trading_mode.value, strategy_version, strategy_name),
                    commit=False,
                )
            else:
                execute(
                    self._connection,
                    "DELETE FROM positions WHERE trading_mode = %s AND strategy_version = %s",
                    (trading_mode.value, strategy_version),
                    commit=False,
                )
            for position in positions:
                execute(
                    self._connection,
                    """
                    INSERT INTO positions (
                        symbol,
                        trading_mode,
                        strategy_version,
                        strategy_name,
                        quantity,
                        entry_price,
                        stop_price,
                        initial_stop_price,
                        opened_at,
                        updated_at,
                        highest_price
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (symbol, trading_mode, strategy_version, strategy_name)
                    DO UPDATE SET
                        quantity = EXCLUDED.quantity,
                        entry_price = EXCLUDED.entry_price,
                        stop_price = EXCLUDED.stop_price,
                        initial_stop_price = EXCLUDED.initial_stop_price,
                        opened_at = EXCLUDED.opened_at,
                        updated_at = EXCLUDED.updated_at,
                        highest_price = COALESCE(EXCLUDED.highest_price, positions.highest_price)
                    """,
                    (
                        position.symbol,
                        position.trading_mode.value,
                        position.strategy_version,
                        position.strategy_name,
                        position.quantity,
                        position.entry_price,
                        position.stop_price,
                        position.initial_stop_price,
                        position.opened_at,
                        position.updated_at,
                        position.highest_price,
                    ),
                    commit=False,
                )
            if commit:
                self._connection.commit()
        except Exception:
            try:
                self._connection.rollback()
            except Exception:
                pass
            raise

    def delete(
        self,
        *,
        symbol: str,
        trading_mode: TradingMode,
        strategy_version: str,
        strategy_name: str = "breakout",
        commit: bool = True,
    ) -> None:
        execute(
            self._connection,
            """
            DELETE FROM positions
            WHERE symbol = %s AND trading_mode = %s AND strategy_version = %s AND strategy_name = %s
            """,
            (symbol, trading_mode.value, strategy_version, strategy_name),
            commit=commit,
        )

    def update_highest_price(
        self,
        *,
        symbol: str,
        trading_mode: TradingMode,
        strategy_version: str,
        strategy_name: str,
        highest_price: float,
        commit: bool = True,
    ) -> None:
        execute(
            self._connection,
            """
            UPDATE positions
               SET highest_price = %s,
                   updated_at = NOW()
             WHERE symbol = %s
               AND trading_mode = %s
               AND strategy_version = %s
               AND strategy_name = %s
            """,
            (highest_price, symbol, trading_mode.value, strategy_version, strategy_name),
            commit=commit,
        )

    def list_all(
        self,
        *,
        trading_mode: TradingMode,
        strategy_version: str,
        strategy_name: str | None = None,
    ) -> list[PositionRecord]:
        strategy_clause = "AND strategy_name IS NOT DISTINCT FROM %s" if strategy_name is not None else ""
        strategy_params = (strategy_name,) if strategy_name is not None else ()
        cursor = self._connection.cursor()
        cursor.execute(
            f"""
            SELECT
                symbol,
                trading_mode,
                strategy_version,
                strategy_name,
                quantity,
                entry_price,
                stop_price,
                initial_stop_price,
                opened_at,
                updated_at,
                highest_price
            FROM positions
            WHERE trading_mode = %s AND strategy_version = %s
              {strategy_clause}
            ORDER BY symbol
            """,
            (trading_mode.value, strategy_version, *strategy_params),
        )
        rows = cursor.fetchall()
        return [
            PositionRecord(
                symbol=row[0],
                trading_mode=TradingMode(row[1]),
                strategy_version=row[2],
                strategy_name=row[3],
                quantity=float(row[4]),
                entry_price=float(row[5]),
                stop_price=float(row[6]),
                initial_stop_price=float(row[7]),
                opened_at=row[8],
                updated_at=row[9],
                highest_price=float(row[10]) if row[10] is not None else None,
            )
            for row in rows
        ]


class TuningResultStore:
    def __init__(self, connection: ConnectionProtocol) -> None:
        self._connection = connection

    def save_run(
        self,
        *,
        scenario_name: str,
        trading_mode: str,
        candidates: list,  # list[TuningCandidate] — avoid circular import
        created_at: datetime,
        run_id: str | None = None,
    ) -> str:
        """Persist all candidates for one sweep run. Returns the run_id used."""
        rid = run_id or str(_uuid_module.uuid4())
        scored = [c for c in candidates if c.score is not None]
        best_params = scored[0].params if scored else None

        try:
            for candidate in candidates:
                is_best = bool(best_params and candidate.params == best_params)
                report = candidate.report
                execute(
                    self._connection,
                    """
                    INSERT INTO tuning_results (
                        run_id, created_at, scenario_name, trading_mode,
                        params, score, total_trades, win_rate,
                        mean_return_pct, max_drawdown_pct, sharpe_ratio, is_best
                    ) VALUES (%s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        rid,
                        created_at,
                        scenario_name,
                        trading_mode,
                        json.dumps(candidate.params),
                        candidate.score,
                        report.total_trades if report is not None else 0,
                        report.win_rate if report is not None else None,
                        report.mean_return_pct if report is not None else None,
                        report.max_drawdown_pct if report is not None else None,
                        report.sharpe_ratio if report is not None else None,
                        is_best,
                    ),
                    commit=False,
                )
            self._connection.commit()
        except Exception:
            try:
                self._connection.rollback()
            except Exception:
                pass
            raise
        return rid

    def load_latest_best(self, *, trading_mode: str) -> dict | None:
        """Return the most recent is_best=TRUE row as a plain dict, or None."""
        row = fetch_one(
            self._connection,
            """
            SELECT params, score, total_trades, win_rate,
                   mean_return_pct, max_drawdown_pct, sharpe_ratio, created_at
            FROM tuning_results
            WHERE trading_mode = %s AND is_best = TRUE
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (trading_mode,),
        )
        if row is None:
            return None
        params = row[0] if isinstance(row[0], dict) else json.loads(row[0])
        return {
            "params": params,
            "score": row[1],
            "total_trades": row[2],
            "win_rate": row[3],
            "mean_return_pct": row[4],
            "max_drawdown_pct": row[5],
            "sharpe_ratio": row[6],
            "created_at": row[7],
        }

    def load_all_scored(
        self,
        *,
        trading_mode: str,
        limit: int = 5000,
    ) -> list[dict]:
        """Return all scored rows as [{params, score}, ...] for surrogate training.

        Ordered most-recent first; capped at limit to bound memory.
        """
        rows = fetch_all(
            self._connection,
            """
            SELECT params, score
            FROM tuning_results
            WHERE trading_mode = %s AND score IS NOT NULL
            ORDER BY created_at DESC
            LIMIT %s
            """,
            (trading_mode, limit),
        )
        return [
            {
                "params": row[0] if isinstance(row[0], dict) else json.loads(row[0]),
                "score": float(row[1]),
            }
            for row in rows
        ]


class StrategyFlagStore:
    def __init__(self, connection: ConnectionProtocol) -> None:
        self._connection = connection

    def save(self, flag: StrategyFlag, *, commit: bool = True) -> None:
        execute(
            self._connection,
            """
            INSERT INTO strategy_flags (
                strategy_name, trading_mode, strategy_version, enabled, updated_at
            )
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (strategy_name, trading_mode, strategy_version)
            DO UPDATE SET
                enabled = EXCLUDED.enabled,
                updated_at = EXCLUDED.updated_at
            """,
            (
                flag.strategy_name,
                flag.trading_mode.value,
                flag.strategy_version,
                flag.enabled,
                flag.updated_at,
            ),
            commit=commit,
        )

    def load(
        self,
        *,
        strategy_name: str,
        trading_mode: TradingMode,
        strategy_version: str,
    ) -> StrategyFlag | None:
        row = fetch_one(
            self._connection,
            """
            SELECT strategy_name, trading_mode, strategy_version, enabled, updated_at
            FROM strategy_flags
            WHERE strategy_name = %s AND trading_mode = %s AND strategy_version = %s
            """,
            (strategy_name, trading_mode.value, strategy_version),
        )
        if row is None:
            return None
        return StrategyFlag(
            strategy_name=row[0],
            trading_mode=TradingMode(row[1]),
            strategy_version=row[2],
            enabled=bool(row[3]),
            updated_at=row[4],
        )

    def list_all(
        self,
        *,
        trading_mode: TradingMode,
        strategy_version: str,
    ) -> list[StrategyFlag]:
        rows = fetch_all(
            self._connection,
            """
            SELECT strategy_name, trading_mode, strategy_version, enabled, updated_at
            FROM strategy_flags
            WHERE trading_mode = %s AND strategy_version = %s
            ORDER BY strategy_name
            """,
            (trading_mode.value, strategy_version),
        )
        return [
            StrategyFlag(
                strategy_name=row[0],
                trading_mode=TradingMode(row[1]),
                strategy_version=row[2],
                enabled=bool(row[3]),
                updated_at=row[4],
            )
            for row in rows
        ]


class StrategyWeightStore:
    def __init__(self, connection: ConnectionProtocol) -> None:
        self._connection = connection

    def upsert_many(
        self,
        *,
        weights: dict[str, float],
        sharpes: dict[str, float],
        trading_mode: TradingMode,
        strategy_version: str,
        computed_at: datetime,
        commit: bool = True,
    ) -> None:
        try:
            for strategy_name, weight in weights.items():
                execute(
                    self._connection,
                    """
                    INSERT INTO strategy_weights (
                        strategy_name, trading_mode, strategy_version,
                        weight, sharpe, computed_at
                    )
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT (strategy_name, trading_mode, strategy_version)
                    DO UPDATE SET
                        weight = EXCLUDED.weight,
                        sharpe = EXCLUDED.sharpe,
                        computed_at = EXCLUDED.computed_at
                    """,
                    (
                        strategy_name,
                        trading_mode.value,
                        strategy_version,
                        weight,
                        sharpes.get(strategy_name, 0.0),
                        computed_at,
                    ),
                    commit=False,
                )
            if commit:
                self._connection.commit()
        except Exception:
            try:
                self._connection.rollback()
            except Exception:
                pass
            raise

    def load_all(
        self,
        *,
        trading_mode: TradingMode,
        strategy_version: str,
    ) -> list[StrategyWeight]:
        rows = fetch_all(
            self._connection,
            """
            SELECT strategy_name, trading_mode, strategy_version,
                   weight, sharpe, computed_at
              FROM strategy_weights
             WHERE trading_mode = %s AND strategy_version = %s
             ORDER BY strategy_name
            """,
            (trading_mode.value, strategy_version),
        )
        return [
            StrategyWeight(
                strategy_name=row[0],
                trading_mode=TradingMode(row[1]),
                strategy_version=row[2],
                weight=float(row[3]),
                sharpe=float(row[4]),
                computed_at=row[5],
            )
            for row in rows
        ]


class ConfidenceFloorStore:
    def __init__(self, connection: ConnectionProtocol) -> None:
        self._connection = connection

    def upsert(self, rec: ConfidenceFloor, *, commit: bool = True) -> None:
        execute(
            self._connection,
            """
            INSERT INTO confidence_floor_store (
                trading_mode, strategy_version, floor_value,
                manual_floor_baseline, equity_high_watermark,
                set_by, reason, updated_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (trading_mode, strategy_version)
            DO UPDATE SET
                floor_value = EXCLUDED.floor_value,
                manual_floor_baseline = EXCLUDED.manual_floor_baseline,
                equity_high_watermark = EXCLUDED.equity_high_watermark,
                set_by = EXCLUDED.set_by,
                reason = EXCLUDED.reason,
                updated_at = EXCLUDED.updated_at
            """,
            (
                rec.trading_mode.value,
                rec.strategy_version,
                rec.floor_value,
                rec.manual_floor_baseline,
                rec.equity_high_watermark,
                rec.set_by,
                rec.reason,
                rec.updated_at,
            ),
            commit=commit,
        )

    def load(
        self, *, trading_mode: TradingMode, strategy_version: str
    ) -> ConfidenceFloor | None:
        row = fetch_one(
            self._connection,
            """
            SELECT trading_mode, strategy_version, floor_value,
                   manual_floor_baseline, equity_high_watermark,
                   set_by, reason, updated_at
            FROM confidence_floor_store
            WHERE trading_mode = %s AND strategy_version = %s
            """,
            (trading_mode.value, strategy_version),
        )
        if row is None:
            return None
        return ConfidenceFloor(
            trading_mode=TradingMode(row[0]),
            strategy_version=row[1],
            floor_value=float(row[2]),
            manual_floor_baseline=float(row[3]),
            equity_high_watermark=float(row[4]),
            set_by=row[5],
            reason=row[6],
            updated_at=row[7],
        )


@dataclass(frozen=True)
class WatchlistRecord:
    symbol: str
    trading_mode: str
    enabled: bool
    ignored: bool
    added_at: datetime
    added_by: str


class WatchlistStore:
    def __init__(self, connection: ConnectionProtocol) -> None:
        self._connection = connection

    def list_enabled(self, trading_mode: str) -> list[str]:
        rows = fetch_all(
            self._connection,
            "SELECT symbol FROM symbol_watchlist "
            "WHERE trading_mode = %s AND enabled = TRUE "
            "ORDER BY symbol",
            (trading_mode,),
        )
        return [row[0] for row in rows]

    def list_all(self, trading_mode: str) -> list[WatchlistRecord]:
        rows = fetch_all(
            self._connection,
            "SELECT symbol, trading_mode, enabled, ignored, added_at, added_by "
            "FROM symbol_watchlist "
            "WHERE trading_mode = %s "
            "ORDER BY symbol",
            (trading_mode,),
        )
        return [
            WatchlistRecord(
                symbol=row[0],
                trading_mode=row[1],
                enabled=bool(row[2]),
                ignored=bool(row[3]),
                added_at=row[4],
                added_by=row[5],
            )
            for row in rows
        ]

    def add(self, symbol: str, trading_mode: str, *, added_by: str = "system", commit: bool = True) -> None:
        """Insert or re-enable a symbol. Idempotent."""
        execute(
            self._connection,
            "INSERT INTO symbol_watchlist (symbol, trading_mode, enabled, added_at, added_by) "
            "VALUES (%s, %s, TRUE, NOW(), %s) "
            "ON CONFLICT (symbol, trading_mode) DO UPDATE "
            "SET enabled = TRUE, added_at = NOW(), added_by = EXCLUDED.added_by",
            (symbol, trading_mode, added_by),
            commit=commit,
        )

    def remove(self, symbol: str, trading_mode: str, *, commit: bool = True) -> None:
        """Soft delete — sets enabled=FALSE, preserves history."""
        execute(
            self._connection,
            "UPDATE symbol_watchlist SET enabled = FALSE "
            "WHERE symbol = %s AND trading_mode = %s",
            (symbol, trading_mode),
            commit=commit,
        )

    def list_ignored(self, trading_mode: str) -> list[str]:
        rows = fetch_all(
            self._connection,
            "SELECT symbol FROM symbol_watchlist "
            "WHERE trading_mode = %s AND enabled = TRUE AND ignored = TRUE "
            "ORDER BY symbol",
            (trading_mode,),
        )
        return [row[0] for row in rows]

    def ignore(self, symbol: str, trading_mode: str, *, commit: bool = True) -> None:
        """Mark an enabled symbol as ignored for new entries. Idempotent."""
        execute(
            self._connection,
            "UPDATE symbol_watchlist SET ignored = TRUE "
            "WHERE symbol = %s AND trading_mode = %s",
            (symbol, trading_mode),
            commit=commit,
        )

    def unignore(self, symbol: str, trading_mode: str, *, commit: bool = True) -> None:
        """Clear the ignore flag; the symbol resumes normal entry evaluation."""
        execute(
            self._connection,
            "UPDATE symbol_watchlist SET ignored = FALSE "
            "WHERE symbol = %s AND trading_mode = %s",
            (symbol, trading_mode),
            commit=commit,
        )

    def seed(self, symbols: tuple[str, ...], trading_mode: str, *, commit: bool = True) -> None:
        """Insert symbols that don't yet exist. Does not re-enable disabled ones."""
        for symbol in symbols:
            execute(
                self._connection,
                "INSERT INTO symbol_watchlist (symbol, trading_mode, enabled, added_at, added_by) "
                "VALUES (%s, %s, TRUE, NOW(), 'system') "
                "ON CONFLICT (symbol, trading_mode) DO NOTHING",
                (symbol, trading_mode),
                commit=False,
            )
        if commit:
            self._connection.commit()


class OptionOrderRepository:
    def __init__(self, connection: ConnectionProtocol) -> None:
        self._connection = connection

    def save(self, record: OptionOrderRecord, *, commit: bool = True) -> None:
        execute(
            self._connection,
            """
            INSERT INTO option_orders (
                client_order_id, occ_symbol, underlying_symbol, option_type,
                strike, expiry, side, status, quantity, trading_mode,
                strategy_version, strategy_name, limit_price, broker_order_id,
                fill_price, filled_quantity, created_at, updated_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (client_order_id)
            DO UPDATE SET
                status = EXCLUDED.status,
                broker_order_id = EXCLUDED.broker_order_id,
                fill_price = EXCLUDED.fill_price,
                filled_quantity = EXCLUDED.filled_quantity,
                updated_at = EXCLUDED.updated_at
            """,
            (
                record.client_order_id,
                record.occ_symbol,
                record.underlying_symbol,
                record.option_type,
                record.strike,
                record.expiry,
                record.side,
                record.status,
                record.quantity,
                record.trading_mode.value,
                record.strategy_version,
                record.strategy_name,
                record.limit_price,
                record.broker_order_id,
                record.fill_price,
                record.filled_quantity,
                record.created_at,
                record.updated_at,
            ),
            commit=commit,
        )

    def update_fill(
        self,
        *,
        client_order_id: str,
        broker_order_id: str,
        fill_price: float,
        filled_quantity: int,
        status: str,
        updated_at: datetime,
    ) -> None:
        execute(
            self._connection,
            """
            UPDATE option_orders
            SET status = %s,
                broker_order_id = %s,
                fill_price = %s,
                filled_quantity = %s,
                updated_at = %s
            WHERE client_order_id = %s
            """,
            (status, broker_order_id, fill_price, filled_quantity, updated_at, client_order_id),
            commit=True,
        )

    def list_by_status(
        self,
        *,
        trading_mode: TradingMode,
        strategy_version: str,
        statuses: list[str],
    ) -> list[OptionOrderRecord]:
        if not statuses:
            return []
        placeholders = ", ".join(["%s"] * len(statuses))
        rows = fetch_all(
            self._connection,
            f"""
            SELECT
                client_order_id, occ_symbol, underlying_symbol, option_type,
                strike, expiry, side, status, quantity, trading_mode,
                strategy_version, strategy_name, limit_price, broker_order_id,
                fill_price, filled_quantity, created_at, updated_at
            FROM option_orders
            WHERE trading_mode = %s
              AND strategy_version = %s
              AND status IN ({placeholders})
            ORDER BY created_at, client_order_id
            """,
            (trading_mode.value, strategy_version, *statuses),
        )
        return [_row_to_option_order_record(row) for row in rows]

    def list_open_option_positions(
        self,
        *,
        trading_mode: TradingMode,
        strategy_version: str,
    ) -> list[OptionOrderRecord]:
        return self.list_by_status(
            trading_mode=trading_mode,
            strategy_version=strategy_version,
            statuses=["filled"],
        )

    def list_trade_pnl_by_strategy(
        self,
        *,
        trading_mode: TradingMode,
        strategy_version: str,
        start_date: date,
        end_date: date,
        market_timezone: str = "America/New_York",
    ) -> list[dict]:
        """Return one dict per closed option trade in the date range with strategy attribution.

        Each dict: {strategy_name: str, exit_date: date, pnl: float}
        pnl = (sell_fill_price - buy_fill_price) * qty * 100
        Rows where the correlated buy has no fill_price are excluded.
        """
        rows = fetch_all(
            self._connection,
            """
            SELECT x.strategy_name,
                   DATE(x.updated_at AT TIME ZONE %s) AS exit_date,
                   COALESCE(x.filled_quantity, x.quantity) AS qty,
                   x.fill_price AS exit_fill,
                   (SELECT e.fill_price
                      FROM option_orders e
                     WHERE e.occ_symbol = x.occ_symbol
                       AND e.trading_mode = x.trading_mode
                       AND e.strategy_version = x.strategy_version
                       AND e.strategy_name IS NOT DISTINCT FROM x.strategy_name
                       AND e.side = 'buy'
                       AND e.fill_price IS NOT NULL
                       AND e.status = 'filled'
                       AND e.updated_at <= x.updated_at
                     ORDER BY e.updated_at DESC
                     LIMIT 1) AS entry_fill
              FROM option_orders x
             WHERE x.trading_mode = %s
               AND x.strategy_version = %s
               AND x.side = 'sell'
               AND x.fill_price IS NOT NULL
               AND x.status = 'filled'
               AND DATE(x.updated_at AT TIME ZONE %s) >= %s
               AND DATE(x.updated_at AT TIME ZONE %s) <= %s
             ORDER BY x.updated_at
            """,
            (
                market_timezone,
                trading_mode.value,
                strategy_version,
                market_timezone,
                start_date,
                market_timezone,
                end_date,
            ),
        )
        return [
            {
                "strategy_name": row[0],
                "exit_date": row[1],
                "pnl": (float(row[3]) - float(row[4])) * float(row[2]) * 100,
            }
            for row in rows
            if row[4] is not None
        ]

    def load_by_broker_order_id(self, broker_order_id: str) -> OptionOrderRecord | None:
        row = fetch_one(
            self._connection,
            """
            SELECT
                client_order_id, occ_symbol, underlying_symbol, option_type,
                strike, expiry, side, status, quantity, trading_mode,
                strategy_version, strategy_name, limit_price, broker_order_id,
                fill_price, filled_quantity, created_at, updated_at
            FROM option_orders
            WHERE broker_order_id = %s
            ORDER BY updated_at DESC
            LIMIT 1
            """,
            (broker_order_id,),
        )
        return _row_to_option_order_record(row) if row is not None else None


def _row_to_option_order_record(row: Any) -> OptionOrderRecord:
    return OptionOrderRecord(
        client_order_id=row[0],
        occ_symbol=row[1],
        underlying_symbol=row[2],
        option_type=row[3],
        strike=float(row[4]),
        expiry=row[5],
        side=row[6],
        status=row[7],
        quantity=int(row[8]),
        trading_mode=TradingMode(row[9]),
        strategy_version=row[10],
        strategy_name=row[11],
        limit_price=float(row[12]) if row[12] is not None else None,
        broker_order_id=row[13],
        fill_price=float(row[14]) if row[14] is not None else None,
        filled_quantity=int(row[15]) if row[15] is not None else None,
        created_at=row[16],
        updated_at=row[17],
    )


def _load_json_payload(raw_payload: Any) -> dict[str, Any]:
    if isinstance(raw_payload, dict):
        return raw_payload
    if isinstance(raw_payload, str):
        return json.loads(raw_payload)
    return dict(raw_payload or {})


class DecisionLogStore:
    def __init__(self, connection: ConnectionProtocol) -> None:
        self._connection = connection

    def bulk_insert(self, records: list, conn: ConnectionProtocol) -> None:
        if not records:
            return
        sql = """
            INSERT INTO decision_log (
                cycle_at, symbol, strategy_name, trading_mode, strategy_version,
                decision, reject_stage, reject_reason,
                entry_level, signal_bar_close, relative_volume, atr,
                stop_price, limit_price, initial_stop_price,
                quantity, risk_per_share, equity, filter_results,
                vix_close, vix_above_sma, sector_passing_pct,
                vwap_at_signal, signal_bar_above_vwap
            ) VALUES (
                %s, %s, %s, %s, %s,
                %s, %s, %s,
                %s, %s, %s, %s,
                %s, %s, %s,
                %s, %s, %s, %s,
                %s, %s, %s,
                %s, %s
            )
        """
        params = [
            (
                r.cycle_at,
                r.symbol,
                r.strategy_name,
                r.trading_mode,
                r.strategy_version,
                r.decision,
                r.reject_stage,
                r.reject_reason,
                r.entry_level,
                r.signal_bar_close,
                r.relative_volume,
                r.atr,
                r.stop_price,
                r.limit_price,
                r.initial_stop_price,
                r.quantity,
                r.risk_per_share,
                r.equity,
                json.dumps(r.filter_results),
                r.vix_close,
                r.vix_above_sma,
                r.sector_passing_pct,
                r.vwap_at_signal,
                r.signal_bar_above_vwap,
            )
            for r in records
        ]
        cur = conn.cursor()
        cur.executemany(sql, params)


class MarketContextStore:
    def __init__(self, connection: ConnectionProtocol) -> None:
        self._connection = connection

    def save(self, ctx: MarketContext, *, trading_mode: str) -> None:
        execute(
            self._connection,
            """
            INSERT INTO market_context (
                as_of, trading_mode, vix_close, vix_sma, vix_above_sma,
                sector_etf_states, sector_passing_pct
            ) VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s)
            """,
            (
                ctx.as_of,
                trading_mode,
                ctx.vix_close,
                ctx.vix_sma,
                ctx.vix_above_sma,
                json.dumps(ctx.sector_etf_states) if ctx.sector_etf_states else None,
                ctx.sector_passing_pct,
            ),
        )
