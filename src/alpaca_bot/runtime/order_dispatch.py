from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Protocol

from alpaca_bot.config import Settings
from alpaca_bot.execution import BrokerOrder
from alpaca_bot.storage import AuditEvent, OrderRecord

logger = logging.getLogger(__name__)


class OrderStoreProtocol(Protocol):
    def list_by_status(
        self,
        *,
        trading_mode,
        strategy_version: str,
        statuses: list[str],
    ) -> list[OrderRecord]: ...

    def save(self, order: OrderRecord) -> None: ...


class AuditEventStoreProtocol(Protocol):
    def append(self, event: AuditEvent) -> None: ...


class RuntimeProtocol(Protocol):
    order_store: OrderStoreProtocol
    audit_event_store: AuditEventStoreProtocol


class BrokerProtocol(Protocol):
    def submit_stop_limit_entry(self, **kwargs) -> BrokerOrder: ...

    def submit_stop_order(self, **kwargs) -> BrokerOrder: ...


@dataclass(frozen=True)
class OrderDispatchReport:
    submitted_count: int

    def __getitem__(self, key: str) -> int:
        if key != "submitted_count":
            raise KeyError(key)
        return self.submitted_count


def dispatch_pending_orders(
    *,
    settings: Settings,
    runtime: RuntimeProtocol,
    broker: BrokerProtocol,
    now: datetime | Callable[[], datetime] | None = None,
    allowed_intent_types: set[str] | None = None,
    blocked_strategy_names: set[str] | None = None,
) -> OrderDispatchReport:
    timestamp = _resolve_now(now)
    pending_orders = _list_pending_submit_orders(runtime, settings)

    submitted_count = 0
    for order in pending_orders:
        if allowed_intent_types is not None and order.intent_type not in allowed_intent_types:
            continue
        if (
            blocked_strategy_names is not None
            and order.intent_type == "entry"
            and getattr(order, "strategy_name", "breakout") in blocked_strategy_names
        ):
            continue
        try:
            broker_order = _submit_order(order=order, broker=broker)
        except Exception as exc:
            logger.warning(
                "order_dispatch: broker submission failed for %s %s: %s",
                order.symbol,
                order.intent_type,
                exc,
            )
            runtime.audit_event_store.append(
                AuditEvent(
                    event_type="order_dispatch_failed",
                    symbol=order.symbol,
                    payload={
                        "error": str(exc),
                        "symbol": order.symbol,
                        "intent_type": order.intent_type,
                        "timestamp": timestamp.isoformat(),
                    },
                    created_at=timestamp,
                )
            )
            runtime.order_store.save(
                OrderRecord(
                    client_order_id=order.client_order_id,
                    symbol=order.symbol,
                    side=order.side,
                    intent_type=order.intent_type,
                    status="error",
                    quantity=order.quantity,
                    trading_mode=order.trading_mode,
                    strategy_version=order.strategy_version,
                    created_at=order.created_at,
                    updated_at=timestamp,
                    stop_price=order.stop_price,
                    limit_price=order.limit_price,
                    initial_stop_price=order.initial_stop_price,
                    broker_order_id=order.broker_order_id,
                    signal_timestamp=order.signal_timestamp,
                )
            )
            continue
        normalized_status = str(broker_order.status).lower()
        runtime.order_store.save(
            OrderRecord(
                client_order_id=order.client_order_id,
                symbol=order.symbol,
                side=order.side,
                intent_type=order.intent_type,
                status=normalized_status,
                quantity=int(broker_order.quantity),
                trading_mode=order.trading_mode,
                strategy_version=order.strategy_version,
                created_at=order.created_at,
                updated_at=timestamp,
                stop_price=order.stop_price,
                limit_price=order.limit_price,
                initial_stop_price=order.initial_stop_price,
                broker_order_id=broker_order.broker_order_id,
                signal_timestamp=order.signal_timestamp,
            )
        )
        runtime.audit_event_store.append(
            AuditEvent(
                event_type="order_submitted",
                symbol=order.symbol,
                payload={
                    "client_order_id": order.client_order_id,
                    "broker_order_id": broker_order.broker_order_id,
                    "intent_type": order.intent_type,
                    "status": normalized_status,
                },
                created_at=timestamp,
            )
        )
        submitted_count += 1

    return OrderDispatchReport(submitted_count=submitted_count)


def _submit_order(*, order: OrderRecord, broker: BrokerProtocol) -> BrokerOrder:
    if order.intent_type == "entry":
        return broker.submit_stop_limit_entry(
            symbol=order.symbol,
            quantity=order.quantity,
            stop_price=order.stop_price,
            limit_price=order.limit_price,
            client_order_id=order.client_order_id,
        )
    if order.intent_type == "stop":
        return broker.submit_stop_order(
            symbol=order.symbol,
            quantity=order.quantity,
            stop_price=order.stop_price,
            client_order_id=order.client_order_id,
        )
    raise ValueError(f"Unsupported pending order intent_type: {order.intent_type}")


def _resolve_now(now: datetime | Callable[[], datetime] | None) -> datetime:
    if isinstance(now, datetime):
        return now
    if callable(now):
        return now()
    return datetime.now(timezone.utc)


def _list_pending_submit_orders(runtime: RuntimeProtocol, settings: Settings) -> list[OrderRecord]:
    if hasattr(runtime.order_store, "list_pending_submit"):
        return runtime.order_store.list_pending_submit(
            trading_mode=settings.trading_mode,
            strategy_version=settings.strategy_version,
        )
    return runtime.order_store.list_by_status(
        trading_mode=settings.trading_mode,
        strategy_version=settings.strategy_version,
        statuses=["pending_submit"],
    )
