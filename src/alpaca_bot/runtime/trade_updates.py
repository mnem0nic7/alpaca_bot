from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Protocol

logger = logging.getLogger(__name__)

from alpaca_bot.config import Settings
from alpaca_bot.notifications import Notifier
from alpaca_bot.storage import AuditEvent, OrderRecord, PositionRecord


class OrderStoreProtocol(Protocol):
    def load(self, client_order_id: str) -> OrderRecord | None: ...

    def save(self, order: OrderRecord) -> None: ...

    def load_by_broker_order_id(self, broker_order_id: str) -> OrderRecord | None: ...


class PositionStoreProtocol(Protocol):
    def save(self, position: PositionRecord) -> None: ...

    def delete(
        self, *, symbol: str, trading_mode, strategy_version: str, strategy_name: str
    ) -> None: ...


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
    notifier: Notifier | None = None,
) -> dict[str, Any]:
    store_lock = getattr(runtime, "store_lock", None)
    with store_lock if store_lock is not None else _nullcontext():
        return _apply_trade_update_locked(
            settings=settings,
            runtime=runtime,
            update=update,
            now=now,
            notifier=notifier,
        )


class _nullcontext:
    def __enter__(self): return self
    def __exit__(self, *_): pass


def _apply_trade_update_locked(
    *,
    settings: Settings,
    runtime: RuntimeProtocol,
    update: Any,
    now: datetime | Callable[[], datetime] | None = None,
    notifier: Notifier | None = None,
) -> dict[str, Any]:
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

    _is_fill_event = normalized.status in {"filled", "partially_filled"}
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
        fill_price=(
            normalized.filled_avg_price if _is_fill_event else matched_order.fill_price
        ),
        filled_quantity=(
            normalized.filled_qty
            if _is_fill_event and normalized.filled_qty is not None
            else matched_order.filled_quantity
        ),
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
                strategy_name=matched_order.strategy_name,
                quantity=normalized.filled_qty if normalized.filled_qty is not None else matched_order.quantity,
                entry_price=normalized.filled_avg_price,
                stop_price=initial_stop_price,
                initial_stop_price=initial_stop_price,
                opened_at=normalized.timestamp,
                updated_at=timestamp,
            )
        )
        position_updated = True
        if notifier is not None:
            fill_price = normalized.filled_avg_price
            qty = normalized.filled_qty or matched_order.quantity
            slippage = (
                (matched_order.limit_price - fill_price)
                if matched_order.limit_price is not None and fill_price is not None
                else None
            )
            slippage_msg = ""
            if (
                slippage is not None
                and fill_price is not None
                and fill_price > 0
                and slippage < -(settings.notify_slippage_threshold_pct * fill_price)
            ):
                slippage_msg = f"  \u26a0 Adverse slippage: {slippage:.3f}"
            try:
                notifier.send(
                    subject=f"Fill: {matched_order.symbol} {qty}@{fill_price}",
                    body=(
                        f"{matched_order.symbol}: {qty} shares filled at {fill_price}"
                        f"{slippage_msg}"
                    ),
                )
            except Exception:
                logger.exception("Notifier failed to send entry fill alert for %s", matched_order.symbol)
        if matched_order.initial_stop_price is not None:
            protective_stop_client_order_id = _protective_stop_client_order_id(
                matched_order.client_order_id
            )
            existing_stop = runtime.order_store.load(protective_stop_client_order_id)
            if existing_stop is None:
                runtime.order_store.save(
                    OrderRecord(
                        client_order_id=protective_stop_client_order_id,
                        symbol=matched_order.symbol,
                        side="sell",
                        intent_type="stop",
                        status="pending_submit",
                        quantity=normalized.filled_qty if normalized.filled_qty is not None else matched_order.quantity,
                        trading_mode=matched_order.trading_mode,
                        strategy_version=matched_order.strategy_version,
                        strategy_name=matched_order.strategy_name,
                        created_at=timestamp,
                        updated_at=timestamp,
                        stop_price=matched_order.initial_stop_price,
                        initial_stop_price=matched_order.initial_stop_price,
                        signal_timestamp=matched_order.signal_timestamp,
                    )
                )
                protective_stop_queued = True
            else:
                if (
                    normalized.status == "filled"
                    and existing_stop.status == "pending_submit"
                    and normalized.filled_qty is not None
                    and existing_stop.quantity != normalized.filled_qty
                ):
                    runtime.order_store.save(
                        OrderRecord(
                            client_order_id=existing_stop.client_order_id,
                            symbol=existing_stop.symbol,
                            side=existing_stop.side,
                            intent_type=existing_stop.intent_type,
                            status=existing_stop.status,
                            quantity=normalized.filled_qty,
                            trading_mode=existing_stop.trading_mode,
                            strategy_version=existing_stop.strategy_version,
                            strategy_name=existing_stop.strategy_name,
                            created_at=existing_stop.created_at,
                            updated_at=timestamp,
                            stop_price=existing_stop.stop_price,
                            limit_price=existing_stop.limit_price,
                            initial_stop_price=existing_stop.initial_stop_price,
                            broker_order_id=existing_stop.broker_order_id,
                            signal_timestamp=existing_stop.signal_timestamp,
                        )
                    )
                protective_stop_client_order_id = None
    elif matched_order.intent_type in {"stop", "exit"} and normalized.status == "filled":
        runtime.position_store.delete(
            symbol=matched_order.symbol,
            trading_mode=matched_order.trading_mode,
            strategy_version=matched_order.strategy_version,
            strategy_name=matched_order.strategy_name or "breakout",
        )
        position_updated = True
        position_cleared = True
        if notifier is not None:
            fill_price = normalized.filled_avg_price
            qty = normalized.filled_qty
            try:
                notifier.send(
                    subject=f"Position closed: {matched_order.symbol}",
                    body=(
                        f"{matched_order.intent_type.upper()} fill on {matched_order.symbol}: "
                        f"{qty} shares @ {fill_price}"
                    ),
                )
            except Exception:
                logger.exception("Notifier failed to send exit fill alert for %s", matched_order.symbol)
    elif (
        matched_order.intent_type == "entry"
        and normalized.status in {"cancelled", "expired"}
    ):
        runtime.position_store.delete(
            symbol=matched_order.symbol,
            trading_mode=matched_order.trading_mode,
            strategy_version=matched_order.strategy_version,
            strategy_name=matched_order.strategy_name or "breakout",
        )
        protective_stop_client_order_id_cancel = _protective_stop_client_order_id(
            matched_order.client_order_id
        )
        pending_stop = runtime.order_store.load(protective_stop_client_order_id_cancel)
        if pending_stop is not None:
            runtime.order_store.save(
                OrderRecord(
                    client_order_id=pending_stop.client_order_id,
                    symbol=pending_stop.symbol,
                    side=pending_stop.side,
                    intent_type=pending_stop.intent_type,
                    status="cancelled",
                    quantity=pending_stop.quantity,
                    trading_mode=pending_stop.trading_mode,
                    strategy_version=pending_stop.strategy_version,
                    created_at=pending_stop.created_at,
                    updated_at=timestamp,
                    stop_price=pending_stop.stop_price,
                    limit_price=pending_stop.limit_price,
                    initial_stop_price=pending_stop.initial_stop_price,
                    broker_order_id=pending_stop.broker_order_id,
                    signal_timestamp=pending_stop.signal_timestamp,
                )
            )
        position_updated = True
        position_cleared = True

    audit_payload = {
        "client_order_id": matched_order.client_order_id,
        "broker_order_id": normalized.broker_order_id or matched_order.broker_order_id,
        "event": normalized.event,
        "status": normalized.status,
    }
    if saved_order.fill_price is not None:
        audit_payload["fill_price"] = saved_order.fill_price
    if saved_order.filled_quantity is not None:
        audit_payload["filled_quantity"] = saved_order.filled_quantity
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
