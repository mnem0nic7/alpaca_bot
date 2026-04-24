from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Protocol, Sequence

from alpaca_bot.config import Settings
from alpaca_bot.core.engine import CycleIntentType
from alpaca_bot.storage import AuditEvent, OrderRecord, PositionRecord


ACTIVE_STOP_STATUSES = ("pending_submit", "new", "accepted", "submitted", "partially_filled")


class OrderStoreProtocol(Protocol):
    def save(self, order: OrderRecord) -> None: ...

    def list_by_status(
        self,
        *,
        trading_mode,
        strategy_version: str,
        statuses: list[str],
    ) -> list[OrderRecord]: ...


class PositionStoreProtocol(Protocol):
    def save(self, position: PositionRecord) -> None: ...

    def list_all(
        self,
        *,
        trading_mode,
        strategy_version: str,
    ) -> list[PositionRecord]: ...


class AuditEventStoreProtocol(Protocol):
    def append(self, event: AuditEvent) -> None: ...


class RuntimeProtocol(Protocol):
    order_store: OrderStoreProtocol
    position_store: PositionStoreProtocol
    audit_event_store: AuditEventStoreProtocol


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

    intents = list(getattr(cycle_result, "intents", []) or [])
    for intent in intents:
        intent_type = getattr(intent, "intent_type", None)
        symbol = getattr(intent, "symbol", None)
        if symbol is None:
            continue

        if intent_type is CycleIntentType.UPDATE_STOP:
            if positions_by_symbol is None:
                positions_by_symbol = _positions_by_symbol(runtime, settings)
            action = _execute_update_stop(
                settings=settings,
                runtime=runtime,
                broker=broker,
                symbol=symbol,
                stop_price=getattr(intent, "stop_price", None),
                intent_timestamp=getattr(intent, "timestamp", timestamp),
                position=positions_by_symbol.get(symbol),
                now=timestamp,
            )
            if action == "replaced":
                replaced_stop_count += 1
            elif action == "submitted":
                submitted_stop_count += 1
        elif intent_type is CycleIntentType.EXIT:
            if positions_by_symbol is None:
                positions_by_symbol = _positions_by_symbol(runtime, settings)
            canceled, submitted = _execute_exit(
                settings=settings,
                runtime=runtime,
                broker=broker,
                symbol=symbol,
                intent_timestamp=getattr(intent, "timestamp", timestamp),
                reason=getattr(intent, "reason", None),
                position=positions_by_symbol.get(symbol),
                now=timestamp,
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
) -> str | None:
    if position is None or stop_price is None:
        return None

    active_stop = _latest_active_stop_order(runtime, settings, symbol)
    if active_stop is not None and active_stop.broker_order_id:
        broker_order = broker.replace_order(
            order_id=active_stop.broker_order_id,
            stop_price=stop_price,
            client_order_id=active_stop.client_order_id,
        )
        runtime.order_store.save(
            OrderRecord(
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
            )
        )
        action = "replaced"
    else:
        client_order_id = _stop_client_order_id(
            settings=settings,
            symbol=symbol,
            timestamp=intent_timestamp,
        )
        broker_order = broker.submit_stop_order(
            symbol=symbol,
            quantity=position.quantity,
            stop_price=stop_price,
            client_order_id=client_order_id,
        )
        runtime.order_store.save(
            OrderRecord(
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
            )
        )
        action = "submitted"

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
        )
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
        )
    )
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
) -> tuple[int, int]:
    if position is None:
        return 0, 0

    canceled_stop_count = 0
    for stop_order in _active_stop_orders(runtime, settings, symbol):
        if stop_order.broker_order_id:
            broker.cancel_order(stop_order.broker_order_id)
        runtime.order_store.save(
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
            )
        )
        canceled_stop_count += 1

    client_order_id = _exit_client_order_id(
        settings=settings,
        symbol=symbol,
        timestamp=intent_timestamp,
    )
    broker_order = broker.submit_market_exit(
        symbol=symbol,
        quantity=position.quantity,
        client_order_id=client_order_id,
    )
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
        )
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
        )
    )
    return canceled_stop_count, 1


def _positions_by_symbol(runtime: RuntimeProtocol, settings: Settings) -> dict[str, PositionRecord]:
    positions = runtime.position_store.list_all(
        trading_mode=settings.trading_mode,
        strategy_version=settings.strategy_version,
    )
    return {position.symbol: position for position in positions}


def _active_stop_orders(
    runtime: RuntimeProtocol,
    settings: Settings,
    symbol: str,
) -> list[OrderRecord]:
    orders = runtime.order_store.list_by_status(
        trading_mode=settings.trading_mode,
        strategy_version=settings.strategy_version,
        statuses=list(ACTIVE_STOP_STATUSES),
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
) -> OrderRecord | None:
    orders = _active_stop_orders(runtime, settings, symbol)
    if not orders:
        return None
    return max(orders, key=lambda order: (order.updated_at, order.created_at, order.client_order_id))


def _stop_client_order_id(
    *,
    settings: Settings,
    symbol: str,
    timestamp: datetime,
) -> str:
    return (
        f"{settings.strategy_version}:"
        f"{timestamp.date().isoformat()}:"
        f"{symbol}:stop:{timestamp.isoformat()}"
    )


def _exit_client_order_id(
    *,
    settings: Settings,
    symbol: str,
    timestamp: datetime,
) -> str:
    return (
        f"{settings.strategy_version}:"
        f"{timestamp.date().isoformat()}:"
        f"{symbol}:exit:{timestamp.isoformat()}"
    )


def _resolve_now(now: datetime | Callable[[], datetime] | None) -> datetime:
    if isinstance(now, datetime):
        return now
    if callable(now):
        return now()
    return datetime.now(timezone.utc)
