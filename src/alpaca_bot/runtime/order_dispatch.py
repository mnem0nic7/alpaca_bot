from __future__ import annotations

import contextlib
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Protocol

from alpaca_bot.config import Settings
from alpaca_bot.execution import BrokerOrder
from alpaca_bot.notifications import Notifier
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
        if order.intent_type == "stop" and order.created_at is not None:
            created_ts = order.created_at
            if created_ts.tzinfo is None:
                created_ts = created_ts.replace(tzinfo=timezone.utc)
            created_date_et = created_ts.astimezone(settings.market_timezone).date()
            if created_date_et < session_date_et:
                logger.warning(
                    "order_dispatch: expiring stale stop order for %s (created %s, today %s)",
                    order.symbol,
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
            with lock_ctx:
                try:
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
                        ),
                        commit=False,
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
                        quantity=int(broker_order.quantity),
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
