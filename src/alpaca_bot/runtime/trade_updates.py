from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Protocol

from alpaca_bot.config import Settings
from alpaca_bot.storage import AuditEvent, OrderRecord, PositionRecord


class OrderStoreProtocol(Protocol):
    def load(self, client_order_id: str) -> OrderRecord | None: ...

    def save(self, order: OrderRecord) -> None: ...

    def load_by_broker_order_id(self, broker_order_id: str) -> OrderRecord | None: ...


class PositionStoreProtocol(Protocol):
    def save(self, position: PositionRecord) -> None: ...

    def delete(self, *, symbol: str, trading_mode, strategy_version: str) -> None: ...


class AuditEventStoreProtocol(Protocol):
    def append(self, event: AuditEvent) -> None: ...


class RuntimeProtocol(Protocol):
    order_store: OrderStoreProtocol
    position_store: PositionStoreProtocol
    audit_event_store: AuditEventStoreProtocol


@dataclass(frozen=True)
class TradeUpdate:
    event: str
    client_order_id: str | None
    broker_order_id: str | None
    symbol: str
    side: str | None
    status: str
    quantity: int | None
    filled_qty: int | None
    filled_avg_price: float | None
    timestamp: datetime


def apply_trade_update(
    *,
    settings: Settings,
    runtime: RuntimeProtocol,
    update: Any,
    now: datetime | Callable[[], datetime] | None = None,
) -> dict[str, Any]:
    del settings
    normalized = _normalize_trade_update(update)
    timestamp = _resolve_now(now, fallback=normalized.timestamp)
    matched_order = _find_order(runtime.order_store, normalized)
    if matched_order is None:
        runtime.audit_event_store.append(
            AuditEvent(
                event_type="trade_update_unmatched",
                symbol=normalized.symbol or None,
                payload={
                    "client_order_id": normalized.client_order_id,
                    "broker_order_id": normalized.broker_order_id,
                    "event": normalized.event,
                    "status": normalized.status,
                },
                created_at=timestamp,
            )
        )
        return {
            "matched_order_id": None,
            "status": normalized.status,
            "position_updated": False,
            "order_updated": False,
            "unmatched": True,
        }

    saved_order = OrderRecord(
        client_order_id=matched_order.client_order_id,
        symbol=matched_order.symbol,
        side=matched_order.side,
        intent_type=matched_order.intent_type,
        status=normalized.status,
        quantity=normalized.quantity or matched_order.quantity,
        trading_mode=matched_order.trading_mode,
        strategy_version=matched_order.strategy_version,
        created_at=matched_order.created_at,
        updated_at=timestamp,
        stop_price=matched_order.stop_price,
        limit_price=matched_order.limit_price,
        initial_stop_price=matched_order.initial_stop_price,
        broker_order_id=normalized.broker_order_id or matched_order.broker_order_id,
        signal_timestamp=matched_order.signal_timestamp,
    )
    runtime.order_store.save(saved_order)

    position_updated = False
    protective_stop_queued = False
    protective_stop_client_order_id: str | None = None
    position_cleared = False
    if (
        matched_order.intent_type == "entry"
        and normalized.status in {"filled", "partially_filled"}
        and normalized.filled_avg_price is not None
    ):
        initial_stop_price = matched_order.initial_stop_price or 0.0
        runtime.position_store.save(
            PositionRecord(
                symbol=matched_order.symbol,
                trading_mode=matched_order.trading_mode,
                strategy_version=matched_order.strategy_version,
                quantity=normalized.filled_qty or matched_order.quantity,
                entry_price=normalized.filled_avg_price,
                stop_price=initial_stop_price,
                initial_stop_price=initial_stop_price,
                opened_at=normalized.timestamp,
                updated_at=timestamp,
            )
        )
        position_updated = True
        if normalized.status == "filled" and matched_order.initial_stop_price is not None:
            protective_stop_client_order_id = _protective_stop_client_order_id(
                matched_order.client_order_id
            )
            runtime.order_store.save(
                OrderRecord(
                    client_order_id=protective_stop_client_order_id,
                    symbol=matched_order.symbol,
                    side="sell",
                    intent_type="stop",
                    status="pending_submit",
                    quantity=normalized.filled_qty or matched_order.quantity,
                    trading_mode=matched_order.trading_mode,
                    strategy_version=matched_order.strategy_version,
                    created_at=timestamp,
                    updated_at=timestamp,
                    stop_price=matched_order.initial_stop_price,
                    initial_stop_price=matched_order.initial_stop_price,
                    signal_timestamp=matched_order.signal_timestamp,
                )
            )
            protective_stop_queued = True
    elif matched_order.intent_type in {"stop", "exit"} and normalized.status == "filled":
        runtime.position_store.delete(
            symbol=matched_order.symbol,
            trading_mode=matched_order.trading_mode,
            strategy_version=matched_order.strategy_version,
        )
        position_updated = True
        position_cleared = True

    audit_payload = {
        "client_order_id": matched_order.client_order_id,
        "broker_order_id": normalized.broker_order_id or matched_order.broker_order_id,
        "event": normalized.event,
        "status": normalized.status,
    }
    if protective_stop_client_order_id is not None:
        audit_payload["protective_stop_client_order_id"] = protective_stop_client_order_id
    if position_cleared:
        audit_payload["position_cleared"] = True
    runtime.audit_event_store.append(
        AuditEvent(
            event_type="trade_update_applied",
            symbol=matched_order.symbol,
            payload=audit_payload,
            created_at=timestamp,
        )
    )
    return {
        "matched_order_id": matched_order.client_order_id,
        "status": normalized.status,
        "position_updated": position_updated,
        "protective_stop_queued": protective_stop_queued,
        "protective_stop_client_order_id": protective_stop_client_order_id,
        "position_cleared": position_cleared,
        "order_updated": True,
        "unmatched": False,
    }


def _normalize_trade_update(update: Any) -> TradeUpdate:
    payload = update if isinstance(update, dict) else update.__dict__
    timestamp_value = payload.get("timestamp") or payload.get("at")
    if timestamp_value is None:
        raise ValueError("trade update timestamp is required")
    return TradeUpdate(
        event=str(payload.get("event", "")).lower(),
        client_order_id=_optional_str(payload.get("client_order_id")),
        broker_order_id=_optional_str(
            payload.get("broker_order_id") or payload.get("order_id") or payload.get("id")
        ),
        symbol=str(payload.get("symbol", "")).upper(),
        side=_optional_str(payload.get("side")),
        status=str(payload.get("status", payload.get("event", ""))).lower(),
        quantity=_optional_int(payload.get("qty")),
        filled_qty=_optional_int(payload.get("filled_qty")),
        filled_avg_price=_optional_float(payload.get("filled_avg_price")),
        timestamp=_as_datetime(timestamp_value),
    )


def _find_order(order_store: OrderStoreProtocol, update: TradeUpdate) -> OrderRecord | None:
    order = None
    if update.client_order_id:
        order = order_store.load(update.client_order_id)
    if order is None and update.broker_order_id:
        order = order_store.load_by_broker_order_id(update.broker_order_id)
    return order


def _resolve_now(
    now: datetime | Callable[[], datetime] | None,
    *,
    fallback: datetime,
) -> datetime:
    if isinstance(now, datetime):
        return now
    if callable(now):
        return now()
    return fallback.astimezone(timezone.utc) if fallback.tzinfo else fallback.replace(tzinfo=timezone.utc)


def _optional_str(value: Any) -> str | None:
    if value is None or value == "":
        return None
    return str(value)


def _optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    return int(float(value))


def _optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    return float(value)


def _as_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    raise TypeError(f"Unsupported trade update timestamp: {value!r}")


def _protective_stop_client_order_id(entry_client_order_id: str) -> str:
    if ":entry:" in entry_client_order_id:
        return entry_client_order_id.replace(":entry:", ":stop:", 1)
    return f"{entry_client_order_id}:stop"
