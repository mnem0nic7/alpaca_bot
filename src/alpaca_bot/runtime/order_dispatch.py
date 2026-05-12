from __future__ import annotations

import contextlib
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Protocol

from alpaca_bot.config import Settings
from alpaca_bot.execution import BrokerOrder
from alpaca_bot.notifications import Notifier
from alpaca_bot.storage import AuditEvent, OrderRecord

logger = logging.getLogger(__name__)

_UNRECOVERABLE_STOP_CODES = frozenset({"42210000"})


def _is_unrecoverable_stop_error(exc: Exception) -> bool:
    msg = str(exc)
    return any(code in msg for code in _UNRECOVERABLE_STOP_CODES)


class OrderStoreProtocol(Protocol):
    def list_by_status(
        self,
        *,
        trading_mode,
        strategy_version: str,
        statuses: list[str],
    ) -> list[OrderRecord]: ...

    def save(self, order: OrderRecord, *, commit: bool = True) -> None: ...


class AuditEventStoreProtocol(Protocol):
    def append(self, event: AuditEvent, *, commit: bool = True) -> None: ...


class ConnectionProtocol(Protocol):
    def commit(self) -> None: ...


class RuntimeProtocol(Protocol):
    order_store: OrderStoreProtocol
    audit_event_store: AuditEventStoreProtocol
    connection: ConnectionProtocol


class BrokerProtocol(Protocol):
    def submit_stop_limit_entry(self, **kwargs) -> BrokerOrder: ...

    def submit_limit_entry(self, **kwargs) -> BrokerOrder: ...

    def submit_stop_order(self, **kwargs) -> BrokerOrder: ...

    def submit_market_exit(self, **kwargs) -> BrokerOrder: ...

    def cancel_order(self, order_id: str) -> None: ...


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
    notifier: Notifier | None = None,
    session_type: "SessionType | None" = None,
) -> OrderDispatchReport:
    timestamp = _resolve_now(now)

    # Serialize all store access with the stream thread — they share one psycopg2 connection.
    store_lock = getattr(runtime, "store_lock", None)
    lock_ctx = store_lock if store_lock is not None else contextlib.nullcontext()

    with lock_ctx:
        pending_orders = _list_pending_submit_orders(runtime, settings)

    session_date_et = timestamp.astimezone(settings.market_timezone).date()
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
        # Skip entry orders whose signal is from a prior trading day — they are
        # stale and would enter at today's price against yesterday's signal.
        if order.intent_type == "entry" and order.signal_timestamp is not None:
            sig_ts = order.signal_timestamp
            if sig_ts.tzinfo is None:
                sig_ts = sig_ts.replace(tzinfo=timezone.utc)
            signal_date_et = sig_ts.astimezone(settings.market_timezone).date()
            if signal_date_et < session_date_et:
                logger.warning(
                    "order_dispatch: skipping stale entry order for %s (signal date %s, today %s)",
                    order.symbol,
                    signal_date_et,
                    session_date_et,
                )
                with lock_ctx:
                    try:
                        runtime.order_store.save(
                            OrderRecord(
                                client_order_id=order.client_order_id,
                                symbol=order.symbol,
                                side=order.side,
                                intent_type=order.intent_type,
                                status="expired",
                                quantity=order.quantity,
                                trading_mode=order.trading_mode,
                                strategy_version=order.strategy_version,
                                strategy_name=order.strategy_name,
                                created_at=order.created_at,
                                updated_at=timestamp,
                                stop_price=order.stop_price,
                                limit_price=order.limit_price,
                                initial_stop_price=order.initial_stop_price,
                                broker_order_id=order.broker_order_id,
                                signal_timestamp=order.signal_timestamp,
                            ),
                            commit=False,
                        )
                        runtime.audit_event_store.append(
                            AuditEvent(
                                event_type="order_expired_stale_signal",
                                symbol=order.symbol,
                                payload={
                                    "client_order_id": order.client_order_id,
                                    "signal_date": signal_date_et.isoformat(),
                                    "session_date": session_date_et.isoformat(),
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
                continue
        # Expire pending stop orders from a prior trading day — they correspond to
        # positions that should have been flattened at EOD.  Submitting them now
        # would create a naked short against a position that no longer exists.
        # Use signal_timestamp (the session the order was generated for) when
        # available — created_at reflects when the record was written to the DB,
        # which may be the evening before for pre-computed ORB intents.
        if order.intent_type == "stop":
            ref_ts = order.signal_timestamp if order.signal_timestamp is not None else order.created_at
            if ref_ts is None:
                ref_ts = order.created_at
            if ref_ts is None:
                pass  # no timestamp to compare — fall through to dispatch
            else:
                if ref_ts.tzinfo is None:
                    ref_ts = ref_ts.replace(tzinfo=timezone.utc)
                created_date_et = ref_ts.astimezone(settings.market_timezone).date()
            if ref_ts is not None and created_date_et < session_date_et:
                if order.broker_order_id is None:
                    # Never submitted to broker — this is an AH/PM-deferred stop that is
                    # safe to dispatch now. Fall through to the session-type guard below.
                    pass
                else:
                    # Either: (1) previously submitted stop that disappeared from the broker — genuinely
                    # stale, OR (2) stop with no signal context and stale creation date.
                    # Expire it to prevent re-submitting against a position that may no longer exist.
                    logger.warning(
                        "order_dispatch: expiring stale stop order for %s "
                        "(broker_order_id=%s, created %s, today %s)",
                        order.symbol,
                        order.broker_order_id,
                        created_date_et,
                        session_date_et,
                    )
                    with lock_ctx:
                        try:
                            runtime.order_store.save(
                                OrderRecord(
                                    client_order_id=order.client_order_id,
                                    symbol=order.symbol,
                                    side=order.side,
                                    intent_type=order.intent_type,
                                    status="expired",
                                    quantity=order.quantity,
                                    trading_mode=order.trading_mode,
                                    strategy_version=order.strategy_version,
                                    strategy_name=order.strategy_name,
                                    created_at=order.created_at,
                                    updated_at=timestamp,
                                    stop_price=order.stop_price,
                                    limit_price=order.limit_price,
                                    initial_stop_price=order.initial_stop_price,
                                    broker_order_id=order.broker_order_id,
                                    signal_timestamp=order.signal_timestamp,
                                ),
                                commit=False,
                            )
                            runtime.audit_event_store.append(
                                AuditEvent(
                                    event_type="order_expired_stale_stop",
                                    symbol=order.symbol,
                                    payload={
                                        "client_order_id": order.client_order_id,
                                        "created_date": created_date_et.isoformat(),
                                        "session_date": session_date_et.isoformat(),
                                        "broker_order_id": order.broker_order_id,
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
                    continue
        if order.intent_type == "stop":
            if not _cancel_partial_fill_entry(
                order=order,
                runtime=runtime,
                broker=broker,
                settings=settings,
                now=timestamp,
                lock_ctx=lock_ctx,
            ):
                # Cancel failed — leave stop as pending_submit for next cycle retry.
                continue
        if order.intent_type == "stop" and session_type is not None:
            from alpaca_bot.strategy.session import SessionType as _ST
            if session_type in (_ST.PRE_MARKET, _ST.AFTER_HOURS):
                logger.debug(
                    "order_dispatch: deferring stop for %s during %s — will submit at regular open",
                    order.symbol,
                    session_type,
                )
                with lock_ctx:
                    try:
                        runtime.audit_event_store.append(
                            AuditEvent(
                                event_type="stop_dispatch_deferred_extended_hours",
                                symbol=order.symbol,
                                payload={
                                    "client_order_id": order.client_order_id,
                                    "session_type": str(session_type),
                                    "stop_price": order.stop_price,
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
                continue  # Alpaca rejects stops during extended hours; submit at regular-session open
        with lock_ctx:
            try:
                runtime.order_store.save(
                    OrderRecord(
                        client_order_id=order.client_order_id,
                        symbol=order.symbol,
                        side=order.side,
                        intent_type=order.intent_type,
                        status="submitting",
                        quantity=order.quantity,
                        trading_mode=order.trading_mode,
                        strategy_version=order.strategy_version,
                        strategy_name=order.strategy_name,
                        created_at=order.created_at,
                        updated_at=timestamp,
                        stop_price=order.stop_price,
                        limit_price=order.limit_price,
                        initial_stop_price=order.initial_stop_price,
                        broker_order_id=None,
                        signal_timestamp=order.signal_timestamp,
                    ),
                    commit=False,
                )
                runtime.audit_event_store.append(
                    AuditEvent(
                        event_type="order_dispatch_submitting",
                        symbol=order.symbol,
                        payload={
                            "client_order_id": order.client_order_id,
                            "intent_type": order.intent_type,
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
        try:
            # Broker submission stays outside the lock — it is slow network I/O
            # and must not block the trade-update stream thread.
            broker_order = _submit_order(order=order, broker=broker, session_type=session_type, settings=settings)
        except Exception as exc:
            logger.warning(
                "order_dispatch: broker submission failed for %s %s: %s",
                order.symbol,
                order.intent_type,
                exc,
            )
            is_unrecoverable_stop = (
                order.intent_type == "stop" and _is_unrecoverable_stop_error(exc)
            )
            final_status = "canceled" if is_unrecoverable_stop else "error"
            audit_event_type = (
                "order_dispatch_stop_price_rejected"
                if is_unrecoverable_stop
                else "order_dispatch_failed"
            )
            with lock_ctx:
                try:
                    runtime.audit_event_store.append(
                        AuditEvent(
                            event_type=audit_event_type,
                            symbol=order.symbol,
                            payload={
                                "error": str(exc),
                                "symbol": order.symbol,
                                "intent_type": order.intent_type,
                                "client_order_id": order.client_order_id,
                                "stop_price": order.stop_price,
                                "timestamp": timestamp.isoformat(),
                            },
                            created_at=timestamp,
                        ),
                        commit=False,
                    )
                    runtime.order_store.save(
                        OrderRecord(
                            client_order_id=order.client_order_id,
                            symbol=order.symbol,
                            side=order.side,
                            intent_type=order.intent_type,
                            status=final_status,
                            quantity=order.quantity,
                            trading_mode=order.trading_mode,
                            strategy_version=order.strategy_version,
                            strategy_name=order.strategy_name,
                            created_at=order.created_at,
                            updated_at=timestamp,
                            stop_price=order.stop_price,
                            limit_price=order.limit_price,
                            initial_stop_price=order.initial_stop_price,
                            broker_order_id=order.broker_order_id,
                            signal_timestamp=order.signal_timestamp,
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
            if notifier is not None:
                try:
                    notifier.send(
                        subject=f"Order dispatch failed: {order.symbol} {order.intent_type}",
                        body=(
                            f"Failed to submit {order.intent_type} order for {order.symbol}.\n"
                            f"client_order_id: {order.client_order_id}\n"
                            f"Error: {exc}"
                        ),
                    )
                except Exception:
                    logger.exception("Notifier failed to send order dispatch failure alert")
            continue
        if order.intent_type == "entry":
            logger.info(
                "order_dispatch: entry submitted for %s — submitted_qty=%g broker_confirmed_qty=%s",
                order.symbol,
                order.quantity,
                broker_order.quantity,
            )
        normalized_status = str(broker_order.status).lower()
        with lock_ctx:
            try:
                runtime.order_store.save(
                    OrderRecord(
                        client_order_id=order.client_order_id,
                        symbol=order.symbol,
                        side=order.side,
                        intent_type=order.intent_type,
                        status=normalized_status,
                        quantity=float(broker_order.quantity),
                        trading_mode=order.trading_mode,
                        strategy_version=order.strategy_version,
                        strategy_name=order.strategy_name,
                        created_at=order.created_at,
                        updated_at=timestamp,
                        stop_price=order.stop_price,
                        limit_price=order.limit_price,
                        initial_stop_price=order.initial_stop_price,
                        broker_order_id=broker_order.broker_order_id,
                        signal_timestamp=order.signal_timestamp,
                    ),
                    commit=False,
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
        submitted_count += 1

    return OrderDispatchReport(submitted_count=submitted_count)


def _submit_order(
    *,
    order: OrderRecord,
    broker: BrokerProtocol,
    session_type: "SessionType | None" = None,
    settings: Settings | None = None,
) -> BrokerOrder:
    from alpaca_bot.execution.alpaca import extended_hours_limit_price
    from alpaca_bot.strategy.session import SessionType

    is_extended = session_type in (SessionType.PRE_MARKET, SessionType.AFTER_HOURS)
    offset_pct = settings.extended_hours_limit_offset_pct if settings is not None else 0.001

    if order.intent_type == "entry":
        if is_extended:
            lp = extended_hours_limit_price("buy", ref_price=order.stop_price or 0.0, offset_pct=offset_pct)
            return broker.submit_limit_entry(
                symbol=order.symbol,
                quantity=order.quantity,
                limit_price=lp,
                client_order_id=order.client_order_id,
            )
        if order.stop_price is None or order.limit_price is None:
            raise ValueError(
                f"entry order {order.client_order_id} for {order.symbol} has "
                f"stop_price={order.stop_price!r}, limit_price={order.limit_price!r} — cannot submit"
            )
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
    if order.intent_type == "exit":
        return broker.submit_market_exit(
            symbol=order.symbol,
            quantity=order.quantity,
            client_order_id=order.client_order_id,
        )
    raise ValueError(f"Unsupported pending order intent_type: {order.intent_type}")


def _cancel_partial_fill_entry(
    *,
    order: OrderRecord,
    runtime: RuntimeProtocol,
    broker: BrokerProtocol,
    settings: "Settings",
    now: datetime,
    lock_ctx: Any,
) -> bool:
    """Cancel the open buy-limit for a partially-filled entry of the same symbol.

    Returns True if safe to proceed (no partial entry found, or cancel succeeded/already gone).
    Returns False if cancel raised an unrecognized error — caller should skip the sell-side
    order to avoid wash trade rejection, leaving it pending_submit for next cycle retry.
    """
    if lock_ctx is None:
        lock_ctx = contextlib.nullcontext()

    with lock_ctx:
        all_partial = runtime.order_store.list_by_status(
            trading_mode=settings.trading_mode,
            strategy_version=settings.strategy_version,
            statuses=["partially_filled"],
        )
    partial_entries = [
        o for o in all_partial
        if o.intent_type == "entry" and o.symbol == order.symbol
    ]
    if not partial_entries:
        return True

    for entry in partial_entries:
        if not entry.broker_order_id:
            continue
        try:
            broker.cancel_order(entry.broker_order_id)
        except Exception as exc:
            exc_msg = str(exc).lower()
            if any(phrase in exc_msg for phrase in ("already canceled", "not found", "does not exist")):
                logger.warning(
                    "order_dispatch: partial-fill entry for %s already gone at broker (%s) — proceeding",
                    order.symbol,
                    exc,
                )
            else:
                logger.exception(
                    "order_dispatch: failed to cancel partial-fill entry %s for %s before stop dispatch",
                    entry.broker_order_id,
                    order.symbol,
                )
                with lock_ctx:
                    try:
                        runtime.audit_event_store.append(
                            AuditEvent(
                                event_type="partial_fill_cancel_failed",
                                symbol=order.symbol,
                                payload={
                                    "entry_client_order_id": entry.client_order_id,
                                    "entry_broker_order_id": entry.broker_order_id,
                                    "error": str(exc),
                                    "context": "stop_dispatch",
                                },
                                created_at=now,
                            ),
                            commit=True,
                        )
                    except Exception:
                        pass
                return False
        canceled_record = OrderRecord(
            client_order_id=entry.client_order_id,
            symbol=entry.symbol,
            side=entry.side,
            intent_type=entry.intent_type,
            status="canceled",
            quantity=entry.quantity,
            trading_mode=entry.trading_mode,
            strategy_version=entry.strategy_version,
            strategy_name=entry.strategy_name,
            created_at=entry.created_at,
            updated_at=now,
            stop_price=entry.stop_price,
            limit_price=entry.limit_price,
            initial_stop_price=entry.initial_stop_price,
            broker_order_id=entry.broker_order_id,
            signal_timestamp=entry.signal_timestamp,
        )
        with lock_ctx:
            try:
                runtime.order_store.save(canceled_record, commit=False)
                runtime.audit_event_store.append(
                    AuditEvent(
                        event_type="partial_fill_entry_canceled",
                        symbol=order.symbol,
                        payload={
                            "entry_client_order_id": entry.client_order_id,
                            "entry_broker_order_id": entry.broker_order_id,
                            "context": "stop_dispatch",
                        },
                        created_at=now,
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

    return True


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
