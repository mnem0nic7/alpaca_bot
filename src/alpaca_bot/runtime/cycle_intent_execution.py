from __future__ import annotations

import contextlib
import json
import logging
from dataclasses import dataclass, replace as dataclass_replace
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Callable, Protocol, Sequence

if TYPE_CHECKING:
    from alpaca_bot.strategy.session import SessionType

from alpaca_bot.config import Settings
from alpaca_bot.core.engine import CycleIntentType
from alpaca_bot.notifications import Notifier
from alpaca_bot.storage import AuditEvent, OrderRecord, PositionRecord

logger = logging.getLogger(__name__)


ACTIVE_STOP_STATUSES = (
    "pending_submit", "new", "accepted", "submitted", "partially_filled", "held",
)


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

    def submit_limit_exit(self, **kwargs): ...

    def cancel_order(self, order_id: str) -> None: ...

    def get_open_orders_for_symbol(self, symbol: str) -> list: ...


@dataclass(frozen=True)
class CycleIntentExecutionReport:
    replaced_stop_count: int
    submitted_stop_count: int
    submitted_exit_count: int
    canceled_stop_count: int
    updated_pending_stop_count: int = 0
    # Incremented when an EXIT intent had a position but a broker call failed (cancel
    # hard-failed or submit_market_exit raised). Does NOT count exits where the position
    # was already gone — those are treated as success for flatten_complete purposes.
    failed_exit_count: int = 0


def execute_cycle_intents(
    *,
    settings: Settings,
    runtime: RuntimeProtocol,
    broker: BrokerProtocol,
    cycle_result: object,
    now: datetime | Callable[[], datetime] | None = None,
    session_type: "SessionType | None" = None,
    notifier: Notifier | None = None,
) -> CycleIntentExecutionReport:
    timestamp = _resolve_now(now)
    positions_by_symbol: dict[str, PositionRecord] | None = None

    replaced_stop_count = 0
    submitted_stop_count = 0
    submitted_exit_count = 0
    canceled_stop_count = 0
    updated_pending_stop_count = 0
    failed_exit_count = 0

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
            if session_type is not None:
                from alpaca_bot.strategy.session import SessionType as _SessionType
                if session_type in (_SessionType.AFTER_HOURS, _SessionType.PRE_MARKET):
                    logger.debug(
                        "execute_cycle_intents: skipping UPDATE_STOP for %s during %s session",
                        symbol,
                        session_type,
                    )
                    continue
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
                notifier=notifier,
            )
            if action == "replaced":
                replaced_stop_count += 1
            elif action == "submitted":
                submitted_stop_count += 1
            elif action == "updated_pending":
                updated_pending_stop_count += 1
            # Refresh the cached position so subsequent intents for the same symbol
            # see the updated stop_price and don't bypass the regression guard.
            new_stop = getattr(intent, "stop_price", None)
            if action in ("replaced", "submitted", "updated_pending") and new_stop is not None:
                cached = positions_by_symbol.get((symbol, strategy_name))
                if cached is not None:
                    positions_by_symbol[(symbol, strategy_name)] = dataclass_replace(
                        cached, stop_price=new_stop
                    )
        elif intent_type is CycleIntentType.EXIT:
            if positions_by_symbol is None:
                with lock_ctx:
                    positions_by_symbol = _positions_by_symbol(runtime, settings)
            canceled, submitted, hard_failed = _execute_exit(
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
                limit_price=getattr(intent, "limit_price", None),
                notifier=notifier,
            )
            canceled_stop_count += canceled
            submitted_exit_count += submitted
            failed_exit_count += hard_failed

    return CycleIntentExecutionReport(
        replaced_stop_count=replaced_stop_count,
        submitted_stop_count=submitted_stop_count,
        submitted_exit_count=submitted_exit_count,
        canceled_stop_count=canceled_stop_count,
        updated_pending_stop_count=updated_pending_stop_count,
        failed_exit_count=failed_exit_count,
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
    notifier: Notifier | None = None,
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
    _path_c_client_order_id: str | None = None
    try:
        if active_stop is not None and active_stop.broker_order_id:
            broker_order = broker.replace_order(
                order_id=active_stop.broker_order_id,
                stop_price=stop_price,
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
        elif active_stop is not None and not active_stop.broker_order_id:
            # The stop exists as a pending_submit but hasn't been dispatched yet.
            # Update its price in-place; dispatch_pending_orders will submit with the
            # correct price. Submitting to the broker here would create a duplicate
            # stop alongside the one dispatch will submit from the same record.
            updated_order = OrderRecord(
                client_order_id=active_stop.client_order_id,
                symbol=symbol,
                side=active_stop.side,
                intent_type="stop",
                status="pending_submit",
                quantity=active_stop.quantity,
                trading_mode=active_stop.trading_mode,
                strategy_version=active_stop.strategy_version,
                created_at=active_stop.created_at,
                updated_at=now,
                stop_price=stop_price,
                initial_stop_price=active_stop.initial_stop_price,
                broker_order_id=None,
                signal_timestamp=active_stop.signal_timestamp,
                strategy_name=strategy_name,
            )
            action = "updated_pending"
        else:
            client_order_id = _stop_client_order_id(
                settings=settings,
                symbol=symbol,
                timestamp=intent_timestamp,
                strategy_name=strategy_name,
            )
            _path_c_client_order_id = client_order_id
            _cancel_partial_fill_entry(
                symbol=symbol,
                strategy_name=strategy_name,
                runtime=runtime,
                broker=broker,
                settings=settings,
                now=now,
                lock_ctx=lock_ctx,
                context="update_stop",
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

        if "client_order_id must be unique" in exc_msg and _path_c_client_order_id is not None:
            # 40010001: broker has our stop under this client_order_id but DB doesn't track it.
            # The prior Path C submission succeeded at broker but the DB write failed.
            success = _resync_duplicate_stop_order(
                settings=settings,
                runtime=runtime,
                broker=broker,
                symbol=symbol,
                client_order_id=_path_c_client_order_id,
                stop_price=stop_price,
                position=position,
                now=now,
                strategy_name=strategy_name,
                lock_ctx=lock_ctx,
            )
            if not success:
                _emit_stop_update_failed(
                    runtime=runtime,
                    symbol=symbol,
                    exc=exc,
                    now=now,
                    lock_ctx=lock_ctx,
                    notifier=notifier,
                )
        elif "insufficient qty available" in exc_msg:
            # 40310000: a phantom stop at the broker holds the full qty.
            # Cancel blocking orders; the next cycle re-emits the intent from a clean state.
            _cancel_blocking_orders(
                exc=exc,
                runtime=runtime,
                broker=broker,
                symbol=symbol,
                event_type="blocking_stop_canceled",
                now=now,
                lock_ctx=lock_ctx,
            )
        elif any(phrase in exc_msg for phrase in (
            "not found", "already filled", "already canceled", "does not exist",
            "has been filled", "is filled", "order is", "order was",
        )):
            logger.debug("update_stop skipped for %s — order already gone: %s", symbol, exc)
        else:
            logger.exception("Broker call failed for update_stop on %s; skipping", symbol)
            _emit_stop_update_failed(
                runtime=runtime,
                symbol=symbol,
                exc=exc,
                now=now,
                lock_ctx=lock_ctx,
                notifier=notifier,
            )
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
        try:
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
        except Exception:
            try:
                runtime.connection.rollback()
            except Exception:
                pass
            raise
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
    limit_price: float | None = None,
    notifier: Notifier | None = None,
) -> tuple[int, int, int]:
    # Returns (canceled_stop_count, submitted_exit_count, hard_failed).
    # hard_failed=1 when a broker call failed with an unrecognized error (stop cancel
    # or market exit submission), meaning the position is NOT confirmed closed.
    # hard_failed=0 for all other outcomes including position-already-gone paths.
    if position is None:
        return 0, 0, 0

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
            try:
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
            except Exception:
                try:
                    runtime.connection.rollback()
                except Exception:
                    pass
                raise
            return 0, 0, 0

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
                if "already filled" in exc_msg:
                    logger.warning(
                        "cycle_intent_execution: stop filled (position gone) for %s "
                        "broker_order_id=%s: %s",
                        symbol,
                        stop_order.broker_order_id,
                        exc,
                    )
                    position_already_gone = True
                    # Still record the cancellation in DB.
                elif any(
                    phrase in exc_msg
                    for phrase in ("not found", "already canceled", "does not exist")
                ):
                    # Stop is gone at broker but position may still be open (e.g., stop was
                    # canceled by a prior replace_order). Proceed with market exit; the
                    # pre-exit reverify step will catch the case where position is actually gone.
                    logger.warning(
                        "cycle_intent_execution: stop already gone for %s broker_order_id=%s "
                        "(not position-closing): %s",
                        symbol,
                        stop_order.broker_order_id,
                        exc,
                    )
                    # Still record the cancellation in DB.
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
        # Persist any successfully-canceled stops and an audit event unconditionally so the
        # next cycle doesn't re-cancel them, hit "already_canceled", and permanently abandon
        # the exit. Always writing the audit event ensures operator visibility in the DB.
        with lock_ctx:
            try:
                for record in canceled_order_records:
                    runtime.order_store.save(record, commit=False)
                runtime.audit_event_store.append(
                    AuditEvent(
                        event_type="exit_hard_failed",
                        symbol=symbol,
                        payload={
                            "intent_type": "exit",
                            "action": "cancel_hard_failed",
                            "reason": reason,
                            "canceled_stop_count": canceled_stop_count,
                        },
                        created_at=now,
                    ),
                    commit=False,
                )
                runtime.connection.commit()
            except Exception:
                logger.exception(
                    "cycle_intent_execution: failed to persist state after hard-failed cancel "
                    "for %s; position may be unprotected",
                    symbol,
                )
                try:
                    runtime.connection.rollback()
                except Exception:
                    pass
                # Do not re-raise: a DB write failure here is secondary. Re-raising
                # would leave the successfully-canceled stops looking "active" in the
                # DB, causing every subsequent cycle to re-cancel them, hit
                # "already_canceled", set position_already_gone, and permanently
                # abandon the exit for an unprotected position.
        if notifier is not None:
            try:
                notifier.send(
                    subject=f"Exit HARD FAILED: {symbol}/{strategy_name} — stop state UNKNOWN, exit aborted",
                    body=(
                        f"cancel_order raised an unrecognized error for {symbol} ({strategy_name}).\n"
                        f"The stop order status at the broker is unknown. Exit was aborted to prevent double-sell.\n"
                        f"Position may still be protected (stop may be live). Manual verification required.\n"
                        f"Reason: {reason}"
                    ),
                )
            except Exception:
                logger.exception(
                    "cycle_intent_execution: notifier failed for cancel_hard_failed on %s", symbol
                )
        return canceled_stop_count, 0, 1  # hard_failed: stop cancel had unrecognized error

    if position_already_gone:
        with lock_ctx:
            try:
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
            except Exception:
                try:
                    runtime.connection.rollback()
                except Exception:
                    pass
                raise
        return canceled_stop_count, 0, 0  # position_already_gone: not a hard failure

    # Re-verify position still exists before submitting exit — prevents naked-short
    # if the fill stream closed the position between cancel_order and here.
    # Also refresh position quantity in case a partial fill arrived while stops were canceling.
    with lock_ctx:
        _reverify_positions = _positions_by_symbol(runtime, settings)
        if (symbol, strategy_name) not in _reverify_positions:
            try:
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
            except Exception:
                try:
                    runtime.connection.rollback()
                except Exception:
                    pass
                raise
            return canceled_stop_count, 0, 0  # position gone at re-verify: not a hard failure
        position = _reverify_positions[(symbol, strategy_name)]

    client_order_id = _exit_client_order_id(
        settings=settings,
        symbol=symbol,
        timestamp=intent_timestamp,
        strategy_name=strategy_name,
    )
    _cancel_partial_fill_entry(
        symbol=symbol,
        strategy_name=strategy_name,
        runtime=runtime,
        broker=broker,
        settings=settings,
        now=now,
        lock_ctx=lock_ctx,
        context="exit",
    )
    # Submit exit outside the lock.
    try:
        if limit_price is not None:
            broker_order = broker.submit_limit_exit(
                symbol=symbol,
                quantity=position.quantity,
                limit_price=limit_price,
                client_order_id=client_order_id,
            )
        else:
            broker_order = broker.submit_market_exit(
                symbol=symbol,
                quantity=position.quantity,
                client_order_id=client_order_id,
            )
    except Exception:
        # Stops are already canceled at the broker (position is unprotected). Persist
        # canceled stop records and an audit event unconditionally so the next cycle
        # doesn't re-cancel them, hit "already canceled", and permanently abandon the exit.
        exit_method = "submit_limit_exit" if limit_price is not None else "submit_market_exit"
        logger.exception(
            "cycle_intent_execution: %s failed for %s/%s; "
            "position is unprotected — manual intervention required",
            exit_method,
            symbol,
            strategy_name,
        )
        with lock_ctx:
            try:
                for record in canceled_order_records:
                    runtime.order_store.save(record, commit=False)
                runtime.audit_event_store.append(
                    AuditEvent(
                        event_type="exit_hard_failed",
                        symbol=symbol,
                        payload={
                            "intent_type": "exit",
                            "action": f"{exit_method}_failed",
                            "reason": reason,
                            "canceled_stop_count": canceled_stop_count,
                        },
                        created_at=now,
                    ),
                    commit=False,
                )
                # Re-queue a protective stop: we cancelled the broker stop but failed to
                # submit the exit, leaving the position unprotected. Queue a recovery stop
                # so the next dispatch cycle restores protection immediately.
                if canceled_stop_count > 0 and position.stop_price is not None and position.stop_price > 0:
                    _recovery_stop_id = (
                        f"exit_failed_recovery:{settings.strategy_version}:"
                        f"{now.date().isoformat()}:{symbol}:stop"
                    )
                    runtime.order_store.save(
                        OrderRecord(
                            client_order_id=_recovery_stop_id,
                            symbol=symbol,
                            side="sell",
                            intent_type="stop",
                            status="pending_submit",
                            quantity=position.quantity,
                            trading_mode=settings.trading_mode,
                            strategy_version=settings.strategy_version,
                            strategy_name=strategy_name,
                            created_at=now,
                            updated_at=now,
                            stop_price=position.stop_price,
                            initial_stop_price=position.initial_stop_price,
                        ),
                        commit=False,
                    )
                    runtime.audit_event_store.append(
                        AuditEvent(
                            event_type="recovery_stop_queued_after_exit_failure",
                            symbol=symbol,
                            payload={
                                "client_order_id": _recovery_stop_id,
                                "stop_price": position.stop_price,
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
        if notifier is not None:
            try:
                notifier.send(
                    subject=f"Exit HARD FAILED: {symbol}/{strategy_name} — position UNPROTECTED",
                    body=(
                        f"Stop cancel succeeded but {exit_method} raised for {symbol} ({strategy_name}).\n"
                        f"Position is live and unprotected. A recovery stop has been queued.\n"
                        f"Manual verification required.\n"
                        f"Reason: {reason}"
                    ),
                )
            except Exception:
                logger.exception(
                    "cycle_intent_execution: notifier failed for exit submission failure on %s", symbol
                )
        return canceled_stop_count, 0, 1  # hard_failed: exit submission raised

    # Write all results under lock.
    with lock_ctx:
        # Re-check: position may have been filled/closed while broker calls were in-flight.
        current_positions = _positions_by_symbol(runtime, settings)
        try:
            if (symbol, strategy_name) not in current_positions:
                # Position was closed by the fill stream while the broker call was in-flight.
                # Still save the exit order record so the fill event can be matched and PnL
                # can be computed. Without this, the fill arrives as trade_update_unmatched
                # and daily_realized_pnl misses the trade.
                logger.warning(
                    "Position for %s/%s disappeared during broker exit; "
                    "saving exit order record for fill tracking",
                    symbol,
                    strategy_name,
                )
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
                            "action": "submitted_position_already_gone",
                            "reason": reason,
                            "canceled_stop_count": canceled_stop_count,
                            "client_order_id": client_order_id,
                        },
                        created_at=now,
                    ),
                    commit=False,
                )
                runtime.connection.commit()
                return canceled_stop_count, 1, 0  # exit submitted; position already cleared by stream
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
        except Exception:
            try:
                runtime.connection.rollback()
            except Exception:
                pass
            raise
    return canceled_stop_count, 1, 0  # success


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


def _cancel_partial_fill_entry(
    *,
    symbol: str,
    strategy_name: str,
    runtime: RuntimeProtocol,
    broker: BrokerProtocol,
    settings: "Settings",
    now: datetime,
    lock_ctx: Any,
    context: str,
) -> None:
    with lock_ctx:
        all_partial = runtime.order_store.list_by_status(
            trading_mode=settings.trading_mode,
            strategy_version=settings.strategy_version,
            statuses=["partially_filled"],
            strategy_name=strategy_name,
        )
    partial_entries = [
        o for o in all_partial if o.intent_type == "entry" and o.symbol == symbol
    ]
    if not partial_entries:
        return
    for entry in partial_entries:
        if not entry.broker_order_id:
            continue
        try:
            broker.cancel_order(entry.broker_order_id)
        except Exception as exc:
            exc_msg = str(exc).lower()
            if any(
                phrase in exc_msg
                for phrase in ("already canceled", "not found", "does not exist")
            ):
                logger.warning(
                    "cycle_intent_execution: partial-fill entry %s already gone at broker: %s",
                    entry.client_order_id,
                    exc,
                )
            else:
                logger.exception(
                    "cycle_intent_execution: failed to cancel partial-fill entry %s before exit — proceeding anyway",
                    entry.client_order_id,
                )
                with lock_ctx:
                    try:
                        runtime.audit_event_store.append(
                            AuditEvent(
                                event_type="partial_fill_cancel_failed",
                                symbol=symbol,
                                payload={
                                    "entry_client_order_id": entry.client_order_id,
                                    "entry_broker_order_id": entry.broker_order_id,
                                    "error": str(exc),
                                    "context": context,
                                },
                                created_at=now,
                            ),
                            commit=True,
                        )
                    except Exception:
                        pass
                continue
        canceled_entry = OrderRecord(
            client_order_id=entry.client_order_id,
            symbol=entry.symbol,
            side=entry.side,
            intent_type=entry.intent_type,
            status="canceled",
            quantity=entry.quantity,
            trading_mode=entry.trading_mode,
            strategy_version=entry.strategy_version,
            created_at=entry.created_at,
            updated_at=now,
            stop_price=entry.stop_price,
            limit_price=entry.limit_price,
            broker_order_id=entry.broker_order_id,
            signal_timestamp=entry.signal_timestamp,
        )
        with lock_ctx:
            try:
                runtime.order_store.save(canceled_entry, commit=False)
                runtime.audit_event_store.append(
                    AuditEvent(
                        event_type="partial_fill_entry_canceled",
                        symbol=symbol,
                        payload={
                            "entry_client_order_id": entry.client_order_id,
                            "entry_broker_order_id": entry.broker_order_id,
                            "context": context,
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


def _parse_related_orders_from_error(exc: Exception) -> list[str]:
    """Extract blocking broker order IDs from a 40310000 'insufficient qty' error body."""
    raw = str(exc)
    try:
        body = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        # SDK may wrap the body in a string like: "... {'related_orders': [...]}"
        start = raw.find("{")
        if start == -1:
            return []
        try:
            body = json.loads(raw[start:])
        except (json.JSONDecodeError, ValueError):
            return []
    related = body.get("related_orders", [])
    return [str(oid) for oid in related if oid]


def _resync_duplicate_stop_order(
    *,
    settings: Settings,
    runtime: RuntimeProtocol,
    broker: BrokerProtocol,
    symbol: str,
    client_order_id: str,
    stop_price: float,
    position: "PositionRecord",
    now: datetime,
    strategy_name: str,
    lock_ctx: Any,
) -> bool:
    """Handle 40010001: broker already has a stop under this client_order_id.

    Fetch open orders for the symbol, find the conflicting order, UPSERT to DB,
    replace_order() to the correct stop_price, write audit events.

    Returns True on success, False on any failure (caller should emit stop_update_failed).
    """
    try:
        broker_orders = broker.get_open_orders_for_symbol(symbol)
    except Exception as fetch_exc:
        logger.exception(
            "cycle_intent_execution: get_open_orders_for_symbol failed during 40010001 resync for %s: %s",
            symbol, fetch_exc,
        )
        return False

    found = next((o for o in broker_orders if o.client_order_id == client_order_id), None)
    if found is None:
        logger.warning(
            "cycle_intent_execution: 40010001 resync for %s — no order matching client_order_id=%s found; "
            "order may have been filled or expired",
            symbol, client_order_id,
        )
        return False

    if not found.broker_order_id:
        logger.warning(
            "cycle_intent_execution: 40010001 resync for %s — found order has no broker_order_id; skipping replace",
            symbol,
        )
        return False

    try:
        replaced = broker.replace_order(order_id=found.broker_order_id, stop_price=stop_price)
    except Exception as replace_exc:
        logger.exception(
            "cycle_intent_execution: replace_order failed during 40010001 resync for %s: %s",
            symbol, replace_exc,
        )
        return False

    with lock_ctx:
        try:
            runtime.order_store.save(
                OrderRecord(
                    client_order_id=found.client_order_id,
                    symbol=symbol,
                    side=found.side if hasattr(found, "side") else "sell",
                    intent_type="stop",
                    status=str(replaced.status).lower(),
                    quantity=found.quantity if hasattr(found, "quantity") else position.quantity,
                    trading_mode=settings.trading_mode,
                    strategy_version=settings.strategy_version,
                    strategy_name=strategy_name,
                    created_at=now,
                    updated_at=now,
                    stop_price=stop_price,
                    initial_stop_price=position.initial_stop_price,
                    broker_order_id=replaced.broker_order_id,
                    signal_timestamp=None,
                ),
                commit=False,
            )
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
                    event_type="stop_order_resynced",
                    symbol=symbol,
                    payload={
                        "client_order_id": found.client_order_id,
                        "broker_order_id": found.broker_order_id,
                        "stop_price": stop_price,
                        "strategy_name": strategy_name,
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
            logger.exception(
                "cycle_intent_execution: DB write failed during 40010001 resync for %s", symbol
            )
            return False
    return True


def _cancel_blocking_orders(
    *,
    exc: Exception,
    runtime: RuntimeProtocol,
    broker: BrokerProtocol,
    symbol: str,
    event_type: str,
    now: datetime,
    lock_ctx: Any,
) -> None:
    """Handle 40310000: cancel phantom stops blocking a new order submission.

    Parses related_orders from the error body, cancels each via the broker,
    and emits an audit event per blocking order.
    """
    related = _parse_related_orders_from_error(exc)
    if not related:
        logger.warning(
            "cycle_intent_execution: 40310000 for %s but no related_orders in error body: %s",
            symbol, exc,
        )
        return
    for broker_order_id in related:
        try:
            broker.cancel_order(broker_order_id)
        except Exception as cancel_exc:
            logger.warning(
                "cycle_intent_execution: cancel_order failed for blocking order %s on %s: %s",
                broker_order_id, symbol, cancel_exc,
            )
        with lock_ctx:
            try:
                runtime.audit_event_store.append(
                    AuditEvent(
                        event_type=event_type,
                        symbol=symbol,
                        payload={
                            "broker_order_id": broker_order_id,
                            "error": str(exc),
                        },
                        created_at=now,
                    ),
                    commit=True,
                )
            except Exception:
                logger.exception(
                    "cycle_intent_execution: failed to append %s audit event for %s",
                    event_type, symbol,
                )


def _emit_stop_update_failed(
    *,
    runtime: RuntimeProtocol,
    symbol: str,
    exc: Exception,
    now: datetime,
    lock_ctx: Any,
    notifier: Any,
) -> None:
    """Log stop_update_failed audit event and optionally send notification."""
    with lock_ctx:
        try:
            runtime.audit_event_store.append(
                AuditEvent(
                    event_type="stop_update_failed",
                    symbol=symbol,
                    payload={
                        "error": str(exc),
                        "symbol": symbol,
                        "timestamp": now.isoformat(),
                    },
                    created_at=now,
                ),
                commit=True,
            )
        except Exception:
            logger.exception("Failed to write stop_update_failed audit event for %s", symbol)
    if notifier is not None:
        try:
            notifier.send(
                subject=f"Stop update failed: {symbol}",
                body=(
                    f"Broker call failed for UPDATE_STOP on {symbol}.\n"
                    f"Position may be losing stop protection.\n"
                    f"Error: {exc}"
                ),
            )
        except Exception:
            logger.exception("Notifier failed to send stop_update_failed alert for %s", symbol)
