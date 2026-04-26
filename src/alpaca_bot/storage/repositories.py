from __future__ import annotations

import json
import uuid as _uuid_module
from datetime import date, datetime
from typing import Any

from alpaca_bot.config import TradingMode
from alpaca_bot.storage.db import ConnectionProtocol, execute, fetch_all, fetch_one
from alpaca_bot.storage.models import (
    AuditEvent,
    DailySessionState,
    OrderRecord,
    PositionRecord,
    StrategyFlag,
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

    def list_recent(self, *, limit: int = 20) -> list[AuditEvent]:
        rows = fetch_all(
            self._connection,
            """
            SELECT event_type, symbol, payload, created_at
            FROM audit_events
            ORDER BY created_at DESC, event_id DESC
            LIMIT %s
            """,
            (limit,),
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
            """,
            (*event_types, limit),
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
    strategy_name
"""


def _row_to_order_record(row: Any) -> OrderRecord:
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
        stop_price=float(row[10]) if row[10] is not None else None,
        limit_price=float(row[11]) if row[11] is not None else None,
        initial_stop_price=float(row[12]) if row[12] is not None else None,
        broker_order_id=row[13],
        signal_timestamp=row[14],
        fill_price=float(row[15]) if row[15] is not None else None,
        filled_quantity=int(row[16]) if row[16] is not None else None,
        strategy_name=row[17] if row[17] is not None else "breakout",
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
                updated_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (client_order_id)
            DO UPDATE SET
                status = EXCLUDED.status,
                quantity = EXCLUDED.quantity,
                stop_price = EXCLUDED.stop_price,
                limit_price = EXCLUDED.limit_price,
                initial_stop_price = EXCLUDED.initial_stop_price,
                broker_order_id = EXCLUDED.broker_order_id,
                signal_timestamp = EXCLUDED.signal_timestamp,
                fill_price = EXCLUDED.fill_price,
                filled_quantity = EXCLUDED.filled_quantity,
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
        strategy_clause = "AND strategy_name = %s" if strategy_name is not None else ""
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
    ) -> float:
        """Return sum of closed-trade PnL for a session date.

        For each exit/stop order with a fill price, looks up the most recent
        filled entry for the same symbol via correlated subquery (safe even if
        the one-trade-per-symbol invariant is ever violated).
        Returns 0.0 when no completed round-trip trades exist.
        """
        rows = fetch_all(
            self._connection,
            """
            SELECT
                x.symbol,
                (
                    SELECT e.fill_price
                    FROM orders e
                    WHERE e.symbol = x.symbol
                      AND e.trading_mode = x.trading_mode
                      AND e.strategy_version = x.strategy_version
                      AND e.strategy_name = x.strategy_name
                      AND e.intent_type = 'entry'
                      AND e.fill_price IS NOT NULL
                      AND e.status = 'filled'
                      AND e.updated_at < x.updated_at
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
              AND DATE(x.updated_at AT TIME ZONE 'America/New_York') = %s
            """,
            (
                trading_mode.value,
                strategy_version,
                session_date,
            ),
        )
        return sum(
            (float(row[2]) - float(row[1])) * int(row[3])
            for row in rows
            if row[1] is not None and row[2] is not None
        )

    def list_closed_trades(
        self,
        *,
        trading_mode: TradingMode,
        strategy_version: str,
        session_date: date,
        strategy_name: str | None = None,
    ) -> list[dict]:
        """Return one dict per closed round-trip trade for a session date.

        Uses the same correlated-subquery pattern as daily_realized_pnl to
        look up entry fill data without risking Cartesian-product duplicates.
        Rows where entry_fill or exit_fill is NULL are excluded.
        """
        strategy_clause = "AND x.strategy_name = %s" if strategy_name is not None else ""
        strategy_params = (strategy_name,) if strategy_name is not None else ()
        rows = fetch_all(
            self._connection,
            f"""
            SELECT
                x.symbol,
                x.strategy_name,
                (
                    SELECT e.fill_price
                    FROM orders e
                    WHERE e.symbol = x.symbol
                      AND e.trading_mode = x.trading_mode
                      AND e.strategy_version = x.strategy_version
                      AND e.strategy_name = x.strategy_name
                      AND e.intent_type = 'entry'
                      AND e.fill_price IS NOT NULL
                      AND e.status = 'filled'
                      AND e.updated_at < x.updated_at
                    ORDER BY e.updated_at DESC LIMIT 1
                ) AS entry_fill,
                (
                    SELECT e.limit_price
                    FROM orders e
                    WHERE e.symbol = x.symbol
                      AND e.trading_mode = x.trading_mode
                      AND e.strategy_version = x.strategy_version
                      AND e.strategy_name = x.strategy_name
                      AND e.intent_type = 'entry'
                      AND e.fill_price IS NOT NULL
                      AND e.status = 'filled'
                      AND e.updated_at < x.updated_at
                    ORDER BY e.updated_at DESC LIMIT 1
                ) AS entry_limit,
                (
                    SELECT e.updated_at
                    FROM orders e
                    WHERE e.symbol = x.symbol
                      AND e.trading_mode = x.trading_mode
                      AND e.strategy_version = x.strategy_version
                      AND e.strategy_name = x.strategy_name
                      AND e.intent_type = 'entry'
                      AND e.fill_price IS NOT NULL
                      AND e.status = 'filled'
                      AND e.updated_at < x.updated_at
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
              AND DATE(x.updated_at AT TIME ZONE 'America/New_York') = %s
              {strategy_clause}
            ORDER BY x.updated_at
            """,
            (
                trading_mode.value,
                strategy_version,
                session_date,
                *strategy_params,
            ),
        )
        return [
            {
                "symbol": row[0],
                "strategy_name": row[1],
                "entry_fill": float(row[2]) if row[2] is not None else None,
                "entry_limit": float(row[3]) if row[3] is not None else None,
                "entry_time": row[4],
                "exit_fill": float(row[5]) if row[5] is not None else None,
                "exit_time": row[6],
                "qty": int(row[7]),
            }
            for row in rows
            if row[2] is not None and row[5] is not None
        ]


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
              AND strategy_name = %s
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
                updated_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (symbol, trading_mode, strategy_version, strategy_name)
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
                position.strategy_name,
                position.quantity,
                position.entry_price,
                position.stop_price,
                position.initial_stop_price,
                position.opened_at,
                position.updated_at,
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
                        updated_at
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (symbol, trading_mode, strategy_version, strategy_name)
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
                        position.strategy_name,
                        position.quantity,
                        position.entry_price,
                        position.stop_price,
                        position.initial_stop_price,
                        position.opened_at,
                        position.updated_at,
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

    def list_all(
        self,
        *,
        trading_mode: TradingMode,
        strategy_version: str,
        strategy_name: str | None = None,
    ) -> list[PositionRecord]:
        strategy_clause = "AND strategy_name = %s" if strategy_name is not None else ""
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
                updated_at
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
                quantity=int(row[4]),
                entry_price=float(row[5]),
                stop_price=float(row[6]),
                initial_stop_price=float(row[7]),
                opened_at=row[8],
                updated_at=row[9],
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
            )
        return rid

    def load_latest_best(self, *, trading_mode: str) -> dict | None:
        """Return the most recent is_best=TRUE row as a plain dict, or None."""
        row = fetch_one(
            self._connection,
            """
            SELECT params, score, total_trades, win_rate, sharpe_ratio, created_at
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
            "sharpe_ratio": row[4],
            "created_at": row[5],
        }


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


def _load_json_payload(raw_payload: Any) -> dict[str, Any]:
    if isinstance(raw_payload, dict):
        return raw_payload
    if isinstance(raw_payload, str):
        return json.loads(raw_payload)
    return dict(raw_payload or {})
