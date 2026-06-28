from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any, Callable, Protocol

logger = logging.getLogger(__name__)

_ENTRY_TERMINAL_STATUSES = {"cancelled", "canceled", "expired"}
_TERMINATED_STOP_STATUSES = {"expired", "cancelled", "canceled", "error"}

from alpaca_bot.config import Settings
from alpaca_bot.notifications import Notifier
from alpaca_bot.storage import AuditEvent, OrderRecord, PositionRecord


class OrderStoreProtocol(Protocol):
    def load(self, client_order_id: str) -> OrderRecord | None: ...

    def save(self, order: OrderRecord, *, commit: bool = True) -> None: ...

    def load_by_broker_order_id(self, broker_order_id: str) -> OrderRecord | None: ...


class PositionStoreProtocol(Protocol):
    def save(self, position: PositionRecord, *, commit: bool = True) -> None: ...

    def list_all(
        self, *, trading_mode, strategy_version: str, strategy_name: str | None = None
    ) -> list[PositionRecord]: ...

    def delete(
        self, *, symbol: str, trading_mode, strategy_version: str, strategy_name: str, commit: bool = True
    ) -> None: ...


class AuditEventStoreProtocol(Protocol):
    def append(self, event: AuditEvent, *, commit: bool = True) -> None: ...


class RuntimeProtocol(Protocol):
    order_store: OrderStoreProtocol
    position_store: PositionStoreProtocol
    audit_event_store: AuditEventStoreProtocol
    connection: Any


class BrokerProtocol(Protocol):
    def replace_order(self, **kwargs) -> Any: ...


@dataclass(frozen=True)
class TradeUpdate:
    event: str
    client_order_id: str | None
    broker_order_id: str | None
    symbol: str
    side: str | None
    status: str
    quantity: float | None
    filled_qty: float | None
    filled_avg_price: float | None
    timestamp: datetime


@dataclass(frozen=True)
class ProtectiveStopQuantityReplace:
    stop_order: OrderRecord
    entry_client_order_id: str
    quantity: float
    timestamp: datetime


def apply_trade_update(
    *,
    settings: Settings,
    runtime: RuntimeProtocol,
    update: Any,
    now: datetime | Callable[[], datetime] | None = None,
    notifier: Notifier | None = None,
    broker: BrokerProtocol | None = None,
) -> dict[str, Any]:
    store_lock = getattr(runtime, "store_lock", None)
    with store_lock if store_lock is not None else _nullcontext():
        result, pending_notifications, pending_stop_replacements = _apply_trade_update_locked(
            settings=settings,
            runtime=runtime,
            update=update,
            now=now,
        )
    result.update(
        _replace_broker_backed_stop_quantities(
            runtime=runtime,
            broker=broker,
            replacements=pending_stop_replacements,
            store_lock=store_lock,
        )
    )
    # Fire notifier outside the lock to avoid blocking the stream thread.
    if notifier is not None:
        for subject, body in pending_notifications:
            try:
                notifier.send(subject=subject, body=body)
            except Exception:
                logger.exception("Notifier failed to send: %s", subject)
    elif pending_notifications:
        logger.debug("Notifier not configured; suppressing %d fill notification(s)", len(pending_notifications))
    return result


class _nullcontext:
    def __enter__(self): return self
    def __exit__(self, *_): pass


def _apply_trade_update_locked(
    *,
    settings: Settings,
    runtime: RuntimeProtocol,
    update: Any,
    now: datetime | Callable[[], datetime] | None = None,
) -> tuple[
    dict[str, Any],
    list[tuple[str, str]],
    list[ProtectiveStopQuantityReplace],
]:
    normalized = _normalize_trade_update(update)
    timestamp = _resolve_now(now, fallback=normalized.timestamp)

    # Route option fills by client_order_id prefix — must come before equity routing
    client_order_id = normalized.client_order_id or ""
    if client_order_id.startswith("option:"):
        option_store = getattr(runtime, "option_order_store", None)
        if option_store is not None and normalized.filled_avg_price is not None and normalized.filled_qty is not None:
            option_store.update_fill(
                client_order_id=client_order_id,
                broker_order_id=normalized.broker_order_id or "",
                fill_price=normalized.filled_avg_price,
                filled_quantity=int(normalized.filled_qty),
                status=normalized.status,
                updated_at=timestamp,
            )
        return {
            "routed_to": "option_store",
            "client_order_id": client_order_id,
        }, [], []

    matched_order = _find_order(runtime.order_store, normalized)
    pending_notifications: list[tuple[str, str]] = []
    if matched_order is None:
        try:
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
                ),
                commit=False,
            )
            runtime.connection.commit()
        except Exception:
            try:
                runtime.connection.rollback()
            except Exception:
                pass
            raise
        return {
            "matched_order_id": None,
            "status": normalized.status,
            "position_updated": False,
            "order_updated": False,
            "unmatched": True,
        }, [], []

    _is_fill_event = normalized.status in {"filled", "partially_filled"}
    _is_entry_terminal_event = (
        matched_order.intent_type == "entry"
        and normalized.status in _ENTRY_TERMINAL_STATUSES
    )
    saved_order = OrderRecord(
        client_order_id=matched_order.client_order_id,
        symbol=matched_order.symbol,
        side=matched_order.side,
        intent_type=matched_order.intent_type,
        status=normalized.status,
        quantity=normalized.quantity or matched_order.quantity,
        trading_mode=matched_order.trading_mode,
        strategy_version=matched_order.strategy_version,
        strategy_name=matched_order.strategy_name,
        created_at=matched_order.created_at,
        updated_at=timestamp,
        stop_price=matched_order.stop_price,
        limit_price=matched_order.limit_price,
        initial_stop_price=matched_order.initial_stop_price,
        broker_order_id=normalized.broker_order_id or matched_order.broker_order_id,
        signal_timestamp=matched_order.signal_timestamp,
        fill_price=(
            normalized.filled_avg_price
            if (
                (_is_fill_event or _is_entry_terminal_event)
                and normalized.filled_avg_price is not None
            )
            else matched_order.fill_price
        ),
        filled_quantity=(
            normalized.filled_qty
            if _is_fill_event and normalized.filled_qty is not None
            else _entry_cumulative_fill_qty(matched_order, normalized)
            if _is_entry_terminal_event
            else matched_order.filled_quantity
        ),
    )
    position_updated = False
    protective_stop_queued = False
    protective_stop_client_order_id: str | None = None
    pending_stop_replacements: list[ProtectiveStopQuantityReplace] = []
    position_cleared = False

    # Prepare notifications (pure, no DB writes).
    if (
        matched_order.intent_type == "entry"
        and normalized.status in {"filled", "partially_filled"}
        and normalized.filled_avg_price is not None
        and matched_order.status not in {"filled"}
    ):
        position_updated = True
        fill_price = normalized.filled_avg_price
        qty = normalized.filled_qty if normalized.filled_qty is not None else matched_order.quantity
        logger.info(
            "trade_updates: entry fill %s — order_qty=%g filled_qty=%s fill_price=%s",
            matched_order.symbol,
            matched_order.quantity,
            normalized.filled_qty,
            normalized.filled_avg_price,
        )
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
        pending_notifications.append((
            f"Fill: {matched_order.symbol} {qty}@{fill_price}",
            f"{matched_order.symbol}: {qty} shares filled at {fill_price}{slippage_msg}",
        ))
        if matched_order.initial_stop_price is not None:
            protective_stop_client_order_id = _protective_stop_client_order_id(
                matched_order.client_order_id
            )
        else:
            logger.error(
                "Entry fill for %s has no initial_stop_price — protective stop will not be queued. "
                "Check order %s for missing prices (possible enum serialization bug).",
                matched_order.symbol,
                matched_order.client_order_id,
            )
    elif (
        matched_order.intent_type in {"stop", "exit"}
        and normalized.status in {"filled", "partially_filled"}
        and matched_order.status not in {"filled"}
    ):
        position_updated = True
        position_cleared = normalized.status == "filled"
        exit_qty = normalized.filled_qty if normalized.filled_qty is not None else matched_order.quantity
        exit_price = normalized.filled_avg_price or matched_order.fill_price or "?"
        exit_subject = (
            f"Position closed: {matched_order.symbol}"
            if position_cleared
            else f"Position reduced: {matched_order.symbol}"
        )
        pending_notifications.append((
            exit_subject,
            (
                f"{matched_order.intent_type.upper()} fill on {matched_order.symbol}: "
                f"{exit_qty} shares @ {exit_price}"
            ),
        ))
    elif (
        matched_order.intent_type == "entry"
        and normalized.status in _ENTRY_TERMINAL_STATUSES
    ):
        position_updated = True
        terminal_fill_qty = _entry_cumulative_fill_qty(matched_order, normalized)
        terminal_fill_price = _entry_cumulative_fill_price(matched_order, normalized)
        position_cleared = not (terminal_fill_qty is not None and terminal_fill_qty > 0)
        if not position_cleared:
            if matched_order.initial_stop_price is not None:
                protective_stop_client_order_id = _protective_stop_client_order_id(
                    matched_order.client_order_id
                )
            pending_notifications.append((
                f"Position retained: {matched_order.symbol}",
                (
                    f"ENTRY {normalized.status} on {matched_order.symbol}: "
                    f"{terminal_fill_qty} shares remain open"
                ),
            ))
            if terminal_fill_price is None:
                logger.error(
                    "Entry terminal update for %s carried filled_qty=%s but no fill price; "
                    "preserving existing position/stop state.",
                    matched_order.symbol,
                    terminal_fill_qty,
                )

    audit_payload: dict[str, Any] = {
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

    # All DB writes in a single atomic block with rollback guard.
    try:
        runtime.order_store.save(saved_order, commit=False)
        if (
            matched_order.intent_type == "entry"
            and normalized.status in {"filled", "partially_filled"}
            and normalized.filled_avg_price is not None
            and matched_order.status not in {"filled"}
        ):
            fill_qty = (
                normalized.filled_qty
                if normalized.filled_qty is not None
                else matched_order.quantity
            )
            (
                protective_stop_queued,
                protective_stop_client_order_id,
                pending_stop_replacement,
            ) = (
                _save_entry_position_and_stop(
                    runtime,
                    matched_order=matched_order,
                    normalized=normalized,
                    timestamp=timestamp,
                    fill_qty=fill_qty,
                    fill_price=normalized.filled_avg_price,
                )
            )
            if pending_stop_replacement is not None:
                pending_stop_replacements.append(pending_stop_replacement)
        elif (
            matched_order.intent_type in {"stop", "exit"}
            and normalized.status in {"filled", "partially_filled"}
            and matched_order.status not in {"filled"}
        ):
            strategy_name = matched_order.strategy_name or "breakout"
            if normalized.status == "filled":
                runtime.position_store.delete(
                    symbol=matched_order.symbol,
                    trading_mode=matched_order.trading_mode,
                    strategy_version=matched_order.strategy_version,
                    strategy_name=strategy_name,
                    commit=False,
                )
            else:
                remaining_quantity = _reduce_position_for_partial_exit_fill(
                    runtime.position_store,
                    matched_order=matched_order,
                    normalized=normalized,
                    timestamp=timestamp,
                    strategy_name=strategy_name,
                )
                if remaining_quantity is not None:
                    audit_payload["position_remaining_quantity"] = remaining_quantity
        elif (
            matched_order.intent_type == "entry"
            and normalized.status in _ENTRY_TERMINAL_STATUSES
        ):
            terminal_fill_qty = _entry_cumulative_fill_qty(matched_order, normalized)
            terminal_fill_price = _entry_cumulative_fill_price(matched_order, normalized)
            if terminal_fill_qty is not None and terminal_fill_qty > 0:
                audit_payload["position_retained"] = True
                if terminal_fill_price is not None:
                    (
                        protective_stop_queued,
                        protective_stop_client_order_id,
                        pending_stop_replacement,
                    ) = (
                        _save_entry_position_and_stop(
                            runtime,
                            matched_order=matched_order,
                            normalized=normalized,
                            timestamp=timestamp,
                            fill_qty=terminal_fill_qty,
                            fill_price=terminal_fill_price,
                        )
                    )
                    if pending_stop_replacement is not None:
                        pending_stop_replacements.append(pending_stop_replacement)
                else:
                    protective_stop_client_order_id = None
                    runtime.audit_event_store.append(
                        AuditEvent(
                            event_type="entry_terminal_fill_no_price",
                            symbol=matched_order.symbol,
                            payload={
                                "client_order_id": matched_order.client_order_id,
                                "filled_quantity": terminal_fill_qty,
                                "status": normalized.status,
                            },
                            created_at=timestamp,
                        ),
                        commit=False,
                    )
            else:
                runtime.position_store.delete(
                    symbol=matched_order.symbol,
                    trading_mode=matched_order.trading_mode,
                    strategy_version=matched_order.strategy_version,
                    strategy_name=matched_order.strategy_name or "breakout",
                    commit=False,
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
                            strategy_name=pending_stop.strategy_name,
                            created_at=pending_stop.created_at,
                            updated_at=timestamp,
                            stop_price=pending_stop.stop_price,
                            limit_price=pending_stop.limit_price,
                            initial_stop_price=pending_stop.initial_stop_price,
                            broker_order_id=pending_stop.broker_order_id,
                            signal_timestamp=pending_stop.signal_timestamp,
                        ),
                        commit=False,
                    )
        if protective_stop_client_order_id is None:
            audit_payload.pop("protective_stop_client_order_id", None)
        runtime.audit_event_store.append(
            AuditEvent(
                event_type="trade_update_applied",
                symbol=matched_order.symbol,
                payload=audit_payload,
                created_at=timestamp,
            ),
            commit=False,
        )
        runtime.connection.commit()
    except Exception:
        try:
            runtime.connection.rollback()
        except Exception:
            pass
        raise
    return {
        "matched_order_id": matched_order.client_order_id,
        "status": normalized.status,
        "position_updated": position_updated,
        "protective_stop_queued": protective_stop_queued,
        "protective_stop_client_order_id": protective_stop_client_order_id,
        "position_cleared": position_cleared,
        "order_updated": True,
        "unmatched": False,
    }, pending_notifications, pending_stop_replacements


def _normalize_trade_update(update: Any) -> TradeUpdate:
    if isinstance(update, dict):
        payload: dict[str, Any] = dict(update)
    else:
        payload = dict(vars(update)) if hasattr(update, "__dict__") else {}

    # Alpaca SDK wraps all order-level fields (client_order_id, symbol, status,
    # filled_qty, etc.) inside a nested .order attribute.  Merge them into the
    # flat payload so the rest of the function works unchanged for both dict
    # payloads (tests/webhooks) and live SDK objects.
    order_obj = payload.get("order")
    if order_obj is not None:
        order_dict: dict[str, Any] = (
            order_obj if isinstance(order_obj, dict)
            else (dict(vars(order_obj)) if hasattr(order_obj, "__dict__") else {})
        )
        for key, val in order_dict.items():
            if key not in payload or payload[key] is None:
                payload[key] = val

    timestamp_value = payload.get("timestamp") or payload.get("at")
    if timestamp_value is None:
        raise ValueError("trade update timestamp is required")

    # SDK enum fields (TradeEvent, OrderStatus, OrderSide) serialize to
    # "EnumClass.VALUE" — use .value when available to get the raw string.
    broker_id_raw = (
        payload.get("broker_order_id")
        or payload.get("order_id")
        or payload.get("id")  # SDK Order.id is a UUID
    )
    return TradeUpdate(
        event=_enum_str(payload.get("event", "")).lower(),
        client_order_id=_optional_str(payload.get("client_order_id")),
        broker_order_id=_optional_str(broker_id_raw),
        symbol=_enum_str(payload.get("symbol", "")).upper(),
        side=_optional_str(_enum_str(payload.get("side")) or None),
        status=_enum_str(payload.get("status") or payload.get("event") or "").lower(),
        quantity=_optional_float(payload.get("qty")),
        filled_qty=_optional_float(payload.get("filled_qty")),
        filled_avg_price=_optional_float(payload.get("filled_avg_price")),
        timestamp=_as_datetime(timestamp_value),
    )


def _enum_str(value: Any) -> str:
    """Return the string value from an enum or coerce to str, never None."""
    if value is None:
        return ""
    if hasattr(value, "value"):
        return str(value.value)
    return str(value)


def _replace_broker_backed_stop_quantities(
    *,
    runtime: RuntimeProtocol,
    broker: BrokerProtocol | None,
    replacements: list[ProtectiveStopQuantityReplace],
    store_lock: Any,
) -> dict[str, Any]:
    if not replacements:
        return {}

    replaced = False
    failed = False
    lock_ctx = store_lock if store_lock is not None else _nullcontext()

    for replacement in replacements:
        stop_order = replacement.stop_order
        if broker is None:
            failed = True
            with lock_ctx:
                try:
                    runtime.audit_event_store.append(
                        AuditEvent(
                            event_type="protective_stop_quantity_replace_failed",
                            symbol=stop_order.symbol,
                            payload={
                                "client_order_id": stop_order.client_order_id,
                                "broker_order_id": stop_order.broker_order_id,
                                "entry_client_order_id": replacement.entry_client_order_id,
                                "old_quantity": stop_order.quantity,
                                "new_quantity": replacement.quantity,
                                "error": "broker_not_available",
                            },
                            created_at=replacement.timestamp,
                        ),
                        commit=False,
                    )
                    runtime.connection.commit()
                except Exception:
                    try:
                        runtime.connection.rollback()
                    except Exception:
                        pass
                    raise
            continue

        try:
            broker_order = broker.replace_order(
                order_id=stop_order.broker_order_id,
                quantity=replacement.quantity,
            )
        except Exception as exc:
            failed = True
            with lock_ctx:
                try:
                    runtime.audit_event_store.append(
                        AuditEvent(
                            event_type="protective_stop_quantity_replace_failed",
                            symbol=stop_order.symbol,
                            payload={
                                "client_order_id": stop_order.client_order_id,
                                "broker_order_id": stop_order.broker_order_id,
                                "entry_client_order_id": replacement.entry_client_order_id,
                                "old_quantity": stop_order.quantity,
                                "new_quantity": replacement.quantity,
                                "error": str(exc),
                            },
                            created_at=replacement.timestamp,
                        ),
                        commit=False,
                    )
                    runtime.connection.commit()
                except Exception:
                    try:
                        runtime.connection.rollback()
                    except Exception:
                        pass
                    raise
            continue

        new_broker_order_id = (
            getattr(broker_order, "broker_order_id", None)
            or stop_order.broker_order_id
        )
        new_status = str(getattr(broker_order, "status", stop_order.status)).lower()
        new_quantity = float(getattr(broker_order, "quantity", replacement.quantity))
        updated_stop = OrderRecord(
            client_order_id=stop_order.client_order_id,
            symbol=stop_order.symbol,
            side=stop_order.side,
            intent_type=stop_order.intent_type,
            status=new_status,
            quantity=new_quantity,
            trading_mode=stop_order.trading_mode,
            strategy_version=stop_order.strategy_version,
            strategy_name=stop_order.strategy_name,
            created_at=stop_order.created_at,
            updated_at=replacement.timestamp,
            stop_price=stop_order.stop_price,
            limit_price=stop_order.limit_price,
            initial_stop_price=stop_order.initial_stop_price,
            broker_order_id=new_broker_order_id,
            signal_timestamp=stop_order.signal_timestamp,
        )
        with lock_ctx:
            try:
                runtime.order_store.save(updated_stop, commit=False)
                runtime.audit_event_store.append(
                    AuditEvent(
                        event_type="protective_stop_quantity_replaced",
                        symbol=stop_order.symbol,
                        payload={
                            "client_order_id": stop_order.client_order_id,
                            "broker_order_id": stop_order.broker_order_id,
                            "replacement_broker_order_id": new_broker_order_id,
                            "entry_client_order_id": replacement.entry_client_order_id,
                            "old_quantity": stop_order.quantity,
                            "new_quantity": new_quantity,
                        },
                        created_at=replacement.timestamp,
                    ),
                    commit=False,
                )
                runtime.connection.commit()
            except Exception:
                try:
                    runtime.connection.rollback()
                except Exception:
                    pass
                raise
        replaced = True

    return {
        "protective_stop_quantity_replaced": replaced,
        "protective_stop_quantity_replace_failed": failed,
    }


def _find_order(order_store: OrderStoreProtocol, update: TradeUpdate) -> OrderRecord | None:
    order = None
    if update.client_order_id:
        order = order_store.load(update.client_order_id)
    if order is None and update.broker_order_id:
        order = order_store.load_by_broker_order_id(update.broker_order_id)
    return order


def _entry_cumulative_fill_qty(
    matched_order: OrderRecord,
    normalized: TradeUpdate,
) -> float | None:
    if normalized.filled_qty is None:
        return matched_order.filled_quantity
    if matched_order.filled_quantity is None:
        return normalized.filled_qty
    return max(normalized.filled_qty, matched_order.filled_quantity)


def _entry_cumulative_fill_price(
    matched_order: OrderRecord,
    normalized: TradeUpdate,
) -> float | None:
    if normalized.filled_avg_price is not None:
        return normalized.filled_avg_price
    return matched_order.fill_price


def _save_entry_position_and_stop(
    runtime: RuntimeProtocol,
    *,
    matched_order: OrderRecord,
    normalized: TradeUpdate,
    timestamp: datetime,
    fill_qty: float,
    fill_price: float,
) -> tuple[bool, str | None, ProtectiveStopQuantityReplace | None]:
    strategy_name = matched_order.strategy_name or "breakout"
    protective_stop_client_order_id: str | None = None
    protective_stop_queued = False
    pending_stop_replacement: ProtectiveStopQuantityReplace | None = None

    if matched_order.initial_stop_price is None:
        logger.critical(
            "trade_updates: fill received but order has no initial_stop_price — "
            "position for %s will open with stop=0. "
            "client_order_id=%s",
            matched_order.symbol,
            matched_order.client_order_id,
        )
        runtime.audit_event_store.append(
            AuditEvent(
                event_type="entry_fill_no_stop_price",
                symbol=matched_order.symbol,
                payload={
                    "client_order_id": matched_order.client_order_id,
                    "fill_price": fill_price,
                },
                created_at=timestamp,
            ),
            commit=False,
        )

    initial_stop_price = matched_order.initial_stop_price or 0.0
    runtime.position_store.save(
        PositionRecord(
            symbol=matched_order.symbol,
            trading_mode=matched_order.trading_mode,
            strategy_version=matched_order.strategy_version,
            strategy_name=strategy_name,
            quantity=fill_qty,
            entry_price=fill_price,
            stop_price=initial_stop_price,
            initial_stop_price=initial_stop_price,
            opened_at=normalized.timestamp,
            updated_at=timestamp,
        ),
        commit=False,
    )

    if matched_order.initial_stop_price is None:
        return protective_stop_queued, protective_stop_client_order_id, pending_stop_replacement

    protective_stop_client_order_id = _protective_stop_client_order_id(
        matched_order.client_order_id
    )
    existing_stop = runtime.order_store.load(protective_stop_client_order_id)
    if existing_stop is not None and existing_stop.status in _TERMINATED_STOP_STATUSES:
        existing_stop = None
    if existing_stop is None:
        runtime.order_store.save(
            OrderRecord(
                client_order_id=protective_stop_client_order_id,
                symbol=matched_order.symbol,
                side="sell",
                intent_type="stop",
                status="pending_submit",
                quantity=fill_qty,
                trading_mode=matched_order.trading_mode,
                strategy_version=matched_order.strategy_version,
                strategy_name=strategy_name,
                created_at=timestamp,
                updated_at=timestamp,
                stop_price=matched_order.initial_stop_price,
                initial_stop_price=matched_order.initial_stop_price,
                signal_timestamp=matched_order.signal_timestamp,
            ),
            commit=False,
        )
        protective_stop_queued = True
    else:
        if existing_stop.status == "pending_submit" and existing_stop.quantity != fill_qty:
            runtime.order_store.save(
                OrderRecord(
                    client_order_id=existing_stop.client_order_id,
                    symbol=existing_stop.symbol,
                    side=existing_stop.side,
                    intent_type=existing_stop.intent_type,
                    status=existing_stop.status,
                    quantity=fill_qty,
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
                ),
                commit=False,
            )
        elif (
            existing_stop.broker_order_id
            and existing_stop.quantity != fill_qty
            and existing_stop.status not in _TERMINATED_STOP_STATUSES
        ):
            pending_stop_replacement = ProtectiveStopQuantityReplace(
                stop_order=existing_stop,
                entry_client_order_id=matched_order.client_order_id,
                quantity=fill_qty,
                timestamp=timestamp,
            )
        protective_stop_client_order_id = None

    return (
        protective_stop_queued,
        protective_stop_client_order_id,
        pending_stop_replacement,
    )


def _reduce_position_for_partial_exit_fill(
    position_store: PositionStoreProtocol,
    *,
    matched_order: OrderRecord,
    normalized: TradeUpdate,
    timestamp: datetime,
    strategy_name: str,
) -> float | None:
    if normalized.filled_qty is None:
        logger.warning(
            "trade_updates: partial %s fill for %s has no filled_qty; local position unchanged",
            matched_order.intent_type,
            matched_order.symbol,
        )
        return None
    previously_filled = matched_order.filled_quantity or 0.0
    fill_delta = normalized.filled_qty - previously_filled
    if fill_delta <= 0:
        return None

    positions = position_store.list_all(
        trading_mode=matched_order.trading_mode,
        strategy_version=matched_order.strategy_version,
        strategy_name=strategy_name,
    )
    current_position = next(
        (position for position in positions if position.symbol == matched_order.symbol),
        None,
    )
    if current_position is None:
        logger.warning(
            "trade_updates: partial %s fill for %s has no matching local position",
            matched_order.intent_type,
            matched_order.symbol,
        )
        return None

    remaining_quantity = current_position.quantity - fill_delta
    if remaining_quantity <= 0:
        position_store.delete(
            symbol=matched_order.symbol,
            trading_mode=matched_order.trading_mode,
            strategy_version=matched_order.strategy_version,
            strategy_name=strategy_name,
            commit=False,
        )
        return 0.0

    position_store.save(
        replace(
            current_position,
            quantity=remaining_quantity,
            updated_at=timestamp,
        ),
        commit=False,
    )
    return remaining_quantity


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
