from __future__ import annotations

import contextlib
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Protocol, Sequence

from alpaca_bot.config import Settings
from alpaca_bot.core.engine import CycleIntentType
from alpaca_bot.storage import AuditEvent, OrderRecord, PositionRecord

logger = logging.getLogger(__name__)


ACTIVE_STOP_STATUSES = ("pending_submit", "new", "accepted", "submitted", "partially_filled")


class OrderStoreProtocol(Protocol):
    def save(self, order: OrderRecord, *, commit: bool = True) -> None: ...

    def list_by_status(
        self,
        *,
        trading_mode,
        strategy_version: str,
        statuses: list[str],
        strategy_name: str | None = None,
    ) -> list[OrderRecord]: ...


class PositionStoreProtocol(Protocol):
    def save(self, position: PositionRecord, *, commit: bool = True) -> None: ...

    def list_all(
        self,
        *,
        trading_mode,
        strategy_version: str,
    ) -> list[PositionRecord]: ...


class AuditEventStoreProtocol(Protocol):
    def append(self, event: AuditEvent, *, commit: bool = True) -> None: ...


class ConnectionProtocol(Protocol):
    def commit(self) -> None: ...


class RuntimeProtocol(Protocol):
    order_store: OrderStoreProtocol
    position_store: PositionStoreProtocol
    audit_event_store: AuditEventStoreProtocol
    connection: ConnectionProtocol


class BrokerProtocol(Protocol):
    def replace_order(self, **kwargs): ...

    def submit_stop_order(self, **kwargs): ...

    def submit_market_exit(self, **kwargs): ...

    def cancel_order(self, order_id: str) -> None: ...


@dataclass(frozen=True)
class CycleIntentExecutionReport:
    replaced_stop_count: int
    submitted_stop_count: int
    submitted_exit_count: int
    canceled_stop_count: int


def execute_cycle_intents(
    *,
    settings: Settings,
    runtime: RuntimeProtocol,
    broker: BrokerProtocol,
    cycle_result: object,
    now: datetime | Callable[[], datetime] | None = None,
) -> CycleIntentExecutionReport:
    timestamp = _resolve_now(now)
    positions_by_symbol: dict[str, PositionRecord] | None = None

    replaced_stop_count = 0
    submitted_stop_count = 0
    submitted_exit_count = 0
    canceled_stop_count = 0

    store_lock = getattr(runtime, "store_lock", None)
    lock_ctx = store_lock if store_lock is not None else contextlib.nullcontext()

    intents = list(getattr(cycle_result, "intents", []) or [])
    for intent in intents:
        intent_type = getattr(intent, "intent_type", None)
        symbol = getattr(intent, "symbol", None)
        if symbol is None:
            continue
        strategy_name = getattr(intent, "strategy_name", "breakout")

        if intent_type is CycleIntentType.UPDATE_STOP:
            if positions_by_symbol is None:
                with lock_ctx:
                    positions_by_symbol = _positions_by_symbol(runtime, settings)
            action = _execute_update_stop(
                settings=settings,
                runtime=runtime,
                broker=broker,
                symbol=symbol,
                stop_price=getattr(intent, "stop_price", None),
                intent_timestamp=getattr(intent, "timestamp", timestamp),
                position=positions_by_symbol.get((symbol, strategy_name)),
                now=timestamp,
                strategy_name=strategy_name,
                lock_ctx=lock_ctx,
            )
            if action == "replaced":
                replaced_stop_count += 1
            elif action == "submitted":
                submitted_stop_count += 1
        elif intent_type is CycleIntentType.EXIT:
            if positions_by_symbol is None:
                with lock_ctx:
                    positions_by_symbol = _positions_by_symbol(runtime, settings)
            canceled, submitted = _execute_exit(
                settings=settings,
                runtime=runtime,
                broker=broker,
                symbol=symbol,
                intent_timestamp=getattr(intent, "timestamp", timestamp),
                reason=getattr(intent, "reason", None),
                position=positions_by_symbol.get((symbol, strategy_name)),
                now=timestamp,
                strategy_name=strategy_name,
                lock_ctx=lock_ctx,
            )
            canceled_stop_count += canceled
            submitted_exit_count += submitted

    return CycleIntentExecutionReport(
        replaced_stop_count=replaced_stop_count,
        submitted_stop_count=submitted_stop_count,
        submitted_exit_count=submitted_exit_count,
        canceled_stop_count=canceled_stop_count,
    )


def _execute_update_stop(
    *,
    settings: Settings,
    runtime: RuntimeProtocol,
    broker: BrokerProtocol,
    symbol: str,
    stop_price: float | None,
    intent_timestamp: datetime,
    position: PositionRecord | None,
    now: datetime,
    strategy_name: str = "breakout",
    lock_ctx: Any = None,
) -> str | None:
    if position is None or stop_price is None:
        return None
    if stop_price <= position.stop_price:
        return None

    if lock_ctx is None:
        lock_ctx = contextlib.nullcontext()

    # Read active stop under lock — same psycopg2 connection as stream thread.
    with lock_ctx:
        active_stop = _latest_active_stop_order(runtime, settings, symbol, strategy_name=strategy_name)

    # Broker calls happen outside the lock to avoid blocking the stream thread.
    try:
        if active_stop is not None and active_stop.broker_order_id:
            broker_order = broker.replace_order(
                order_id=active_stop.broker_order_id,
                stop_price=stop_price,
                client_order_id=active_stop.client_order_id,
            )
            updated_order = OrderRecord(
                client_order_id=active_stop.client_order_id,
                symbol=symbol,
                side=active_stop.side,
                intent_type="stop",
                status=str(broker_order.status).lower(),
                quantity=active_stop.quantity,
                trading_mode=active_stop.trading_mode,
                strategy_version=active_stop.strategy_version,
                created_at=active_stop.created_at,
                updated_at=now,
                stop_price=stop_price,
                initial_stop_price=active_stop.initial_stop_price,
                broker_order_id=broker_order.broker_order_id,
                signal_timestamp=active_stop.signal_timestamp,
                strategy_name=strategy_name,
            )
            action = "replaced"
        else:
            client_order_id = _stop_client_order_id(
                settings=settings,
                symbol=symbol,
                timestamp=intent_timestamp,
                strategy_name=strategy_name,
            )
            broker_order = broker.submit_stop_order(
                symbol=symbol,
                quantity=position.quantity,
                stop_price=stop_price,
                client_order_id=client_order_id,
            )
            updated_order = OrderRecord(
                client_order_id=client_order_id,
                symbol=symbol,
                side="sell",
                intent_type="stop",
                status=str(broker_order.status).lower(),
                quantity=position.quantity,
                trading_mode=settings.trading_mode,
                strategy_version=settings.strategy_version,
                created_at=now,
                updated_at=now,
                stop_price=stop_price,
                initial_stop_price=position.initial_stop_price,
                broker_order_id=broker_order.broker_order_id,
                signal_timestamp=intent_timestamp,
                strategy_name=strategy_name,
            )
            action = "submitted"
    except Exception as exc:
        exc_msg = str(exc).lower()
        if any(phrase in exc_msg for phrase in ("not found", "already filled", "already canceled", "does not exist")):
            logger.debug("update_stop skipped for %s — order already gone: %s", symbol, exc)
        else:
            logger.exception("Broker call failed for update_stop on %s; skipping", symbol)
        return None

    # All store writes under lock — serializes with the trade-update stream thread.
    with lock_ctx:
        # Re-check: position may have been filled/closed while the broker call was in-flight.
        current_positions = _positions_by_symbol(runtime, settings)
        if (symbol, strategy_name) not in current_positions:
            logger.warning(
                "Position for %s/%s disappeared during broker stop update; skipping write",
                symbol,
                strategy_name,
            )
            return None
        runtime.order_store.save(updated_order, commit=False)
        runtime.position_store.save(
            PositionRecord(
                symbol=position.symbol,
                trading_mode=position.trading_mode,
                strategy_version=position.strategy_version,
                quantity=position.quantity,
                entry_price=position.entry_price,
                stop_price=stop_price,
                initial_stop_price=position.initial_stop_price,
                opened_at=position.opened_at,
                updated_at=now,
                strategy_name=strategy_name,
            ),
            commit=False,
        )
        runtime.audit_event_store.append(
            AuditEvent(
                event_type="cycle_intent_executed",
                symbol=symbol,
                payload={
                    "intent_type": "update_stop",
                    "action": action,
                    "stop_price": stop_price,
                },
                created_at=now,
            ),
            commit=False,
        )
        runtime.connection.commit()
    return action


def _execute_exit(
    *,
    settings: Settings,
    runtime: RuntimeProtocol,
    broker: BrokerProtocol,
    symbol: str,
    intent_timestamp: datetime,
    reason: str | None,
    position: PositionRecord | None,
    now: datetime,
    strategy_name: str = "breakout",
    lock_ctx: Any = None,
) -> tuple[int, int]:
    if position is None:
        return 0, 0

    if lock_ctx is None:
        lock_ctx = contextlib.nullcontext()

    # Guard against duplicate EXIT dispatch — read under lock.
    with lock_ctx:
        active_exit_orders = runtime.order_store.list_by_status(
            trading_mode=settings.trading_mode,
            strategy_version=settings.strategy_version,
            statuses=list(ACTIVE_STOP_STATUSES),
            strategy_name=strategy_name,
        )
        if any(o.symbol == symbol and o.intent_type == "exit" for o in active_exit_orders):
            runtime.audit_event_store.append(
                AuditEvent(
                    event_type="cycle_intent_skipped",
                    symbol=symbol,
                    payload={"intent_type": "exit", "reason": "active_exit_order_exists"},
                    created_at=now,
                ),
                commit=False,
            )
            runtime.connection.commit()
            return 0, 0

        stop_orders = _active_stop_orders(runtime, settings, symbol, strategy_name=strategy_name)

    # Cancel broker stops outside the lock; collect which ones succeeded.
    canceled_order_records: list[OrderRecord] = []
    position_already_gone = False
    cancel_hard_failed = False
    for stop_order in stop_orders:
        if stop_order.broker_order_id:
            try:
                broker.cancel_order(stop_order.broker_order_id)
            except Exception as exc:
                exc_msg = str(exc).lower()
                if any(
                    phrase in exc_msg
                    for phrase in ("not found", "already filled", "already canceled", "does not exist")
                ):
                    logger.warning(
                        "cycle_intent_execution: stop already gone for %s broker_order_id=%s: %s",
                        symbol,
                        stop_order.broker_order_id,
                        exc,
                    )
                    position_already_gone = True
                    # Still record the cancellation in DB even if the broker order is already gone.
                else:
                    # Unknown error: stop may still be live at the broker. Aborting the exit
                    # to prevent a double-sell (live stop + new market exit order).
                    logger.exception(
                        "cycle_intent_execution: cancel_order failed with unrecognized error "
                        "for %s broker_order_id=%s; aborting exit to prevent double-sell",
                        symbol,
                        stop_order.broker_order_id,
                    )
                    cancel_hard_failed = True
                    continue
        canceled_order_records.append(
            OrderRecord(
                client_order_id=stop_order.client_order_id,
                symbol=stop_order.symbol,
                side=stop_order.side,
                intent_type=stop_order.intent_type,
                status="canceled",
                quantity=stop_order.quantity,
                trading_mode=stop_order.trading_mode,
                strategy_version=stop_order.strategy_version,
                created_at=stop_order.created_at,
                updated_at=now,
                stop_price=stop_order.stop_price,
                limit_price=stop_order.limit_price,
                initial_stop_price=stop_order.initial_stop_price,
                broker_order_id=stop_order.broker_order_id,
                signal_timestamp=stop_order.signal_timestamp,
                strategy_name=stop_order.strategy_name,
            )
        )

    canceled_stop_count = len(canceled_order_records)

    if cancel_hard_failed:
        # At least one stop cancel failed with an unrecognized broker error: the stop
        # may still be live. Submitting a market exit now would risk a double-sell / naked-short.
        # Still persist the stops that DID successfully cancel so the next cycle doesn't
        # see them as active and try to cancel them again (which would yield "already canceled",
        # set position_already_gone, and permanently abandon the exit).
        if canceled_order_records:
            with lock_ctx:
                for record in canceled_order_records:
                    runtime.order_store.save(record, commit=False)
                runtime.connection.commit()
        return canceled_stop_count, 0

    if position_already_gone:
        with lock_ctx:
            for record in canceled_order_records:
                runtime.order_store.save(record, commit=False)
            runtime.audit_event_store.append(
                AuditEvent(
                    event_type="cycle_intent_executed",
                    symbol=symbol,
                    payload={
                        "intent_type": "exit",
                        "action": "skipped_position_already_gone",
                        "reason": reason,
                        "canceled_stop_count": canceled_stop_count,
                    },
                    created_at=now,
                ),
                commit=False,
            )
            runtime.connection.commit()
        return canceled_stop_count, 0

    # Re-verify position still exists before submitting exit — prevents naked-short
    # if the fill stream closed the position between cancel_order and here.
    with lock_ctx:
        if (symbol, strategy_name) not in _positions_by_symbol(runtime, settings):
            for record in canceled_order_records:
                runtime.order_store.save(record, commit=False)
            runtime.audit_event_store.append(
                AuditEvent(
                    event_type="cycle_intent_executed",
                    symbol=symbol,
                    payload={
                        "intent_type": "exit",
                        "action": "skipped_position_already_gone",
                        "reason": reason,
                        "canceled_stop_count": canceled_stop_count,
                    },
                    created_at=now,
                ),
                commit=False,
            )
            runtime.connection.commit()
            return canceled_stop_count, 0

    client_order_id = _exit_client_order_id(
        settings=settings,
        symbol=symbol,
        timestamp=intent_timestamp,
        strategy_name=strategy_name,
    )
    # Submit exit outside the lock.
    try:
        broker_order = broker.submit_market_exit(
            symbol=symbol,
            quantity=position.quantity,
            client_order_id=client_order_id,
        )
    except Exception:
        # Stops are already canceled at the broker (position is unprotected). Log a critical
        # alert so the operator can intervene manually.  Do not write DB records — the next
        # cycle will detect the missing stop and attempt to re-file one.
        logger.exception(
            "cycle_intent_execution: submit_market_exit failed for %s/%s; "
            "position is unprotected — manual intervention required",
            symbol,
            strategy_name,
        )
        return canceled_stop_count, 0

    # Write all results under lock.
    with lock_ctx:
        # Re-check: position may have been filled/closed while broker calls were in-flight.
        current_positions = _positions_by_symbol(runtime, settings)
        if (symbol, strategy_name) not in current_positions:
            logger.warning(
                "Position for %s/%s disappeared during broker exit; skipping write",
                symbol,
                strategy_name,
            )
            return canceled_stop_count, 0
        for record in canceled_order_records:
            runtime.order_store.save(record, commit=False)
        runtime.order_store.save(
            OrderRecord(
                client_order_id=client_order_id,
                symbol=symbol,
                side="sell",
                intent_type="exit",
                status=str(broker_order.status).lower(),
                quantity=position.quantity,
                trading_mode=settings.trading_mode,
                strategy_version=settings.strategy_version,
                created_at=now,
                updated_at=now,
                initial_stop_price=position.initial_stop_price,
                broker_order_id=broker_order.broker_order_id,
                signal_timestamp=intent_timestamp,
                strategy_name=strategy_name,
            ),
            commit=False,
        )
        runtime.audit_event_store.append(
            AuditEvent(
                event_type="cycle_intent_executed",
                symbol=symbol,
                payload={
                    "intent_type": "exit",
                    "action": "submitted",
                    "reason": reason,
                    "canceled_stop_count": canceled_stop_count,
                    "client_order_id": client_order_id,
                },
                created_at=now,
            ),
            commit=False,
        )
        runtime.connection.commit()
    return canceled_stop_count, 1


def _positions_by_symbol(
    runtime: RuntimeProtocol, settings: Settings
) -> dict[tuple[str, str], PositionRecord]:
    positions = runtime.position_store.list_all(
        trading_mode=settings.trading_mode,
        strategy_version=settings.strategy_version,
    )
    return {(position.symbol, position.strategy_name): position for position in positions}


def _active_stop_orders(
    runtime: RuntimeProtocol,
    settings: Settings,
    symbol: str,
    strategy_name: str | None = None,
) -> list[OrderRecord]:
    orders = runtime.order_store.list_by_status(
        trading_mode=settings.trading_mode,
        strategy_version=settings.strategy_version,
        statuses=list(ACTIVE_STOP_STATUSES),
        strategy_name=strategy_name,
    )
    return [
        order
        for order in orders
        if order.symbol == symbol and order.intent_type == "stop" and order.side == "sell"
    ]


def _latest_active_stop_order(
    runtime: RuntimeProtocol,
    settings: Settings,
    symbol: str,
    strategy_name: str | None = None,
) -> OrderRecord | None:
    orders = _active_stop_orders(runtime, settings, symbol, strategy_name=strategy_name)
    if not orders:
        return None
    return max(orders, key=lambda order: (order.updated_at, order.created_at, order.client_order_id))


def _stop_client_order_id(
    *,
    settings: Settings,
    symbol: str,
    timestamp: datetime,
    strategy_name: str = "breakout",
) -> str:
    return (
        f"{settings.strategy_version}:"
        f"{strategy_name}:"
        f"{timestamp.date().isoformat()}:"
        f"{symbol}:stop:{timestamp.isoformat()}"
    )


def _exit_client_order_id(
    *,
    settings: Settings,
    symbol: str,
    timestamp: datetime,
    strategy_name: str = "breakout",
) -> str:
    return (
        f"{settings.strategy_version}:"
        f"{strategy_name}:"
        f"{timestamp.date().isoformat()}:"
        f"{symbol}:exit:{timestamp.isoformat()}"
    )


def _resolve_now(now: datetime | Callable[[], datetime] | None) -> datetime:
    if isinstance(now, datetime):
        return now
    if callable(now):
        return now()
    return datetime.now(timezone.utc)
