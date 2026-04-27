from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Protocol, Sequence

from alpaca_bot.config import Settings
from alpaca_bot.execution import BrokerOrder, BrokerPosition
from alpaca_bot.notifications import Notifier
from alpaca_bot.storage import AuditEvent, OrderRecord, PositionRecord


ACTIVE_ORDER_STATUSES = ["pending_submit", "new", "accepted", "submitted", "partially_filled"]


class OrderStoreProtocol(Protocol):
    def save(self, order: OrderRecord, *, commit: bool = True) -> None: ...

    def list_by_status(
        self,
        *,
        trading_mode,
        strategy_version: str,
        statuses: list[str],
    ) -> list[OrderRecord]: ...


class PositionStoreProtocol(Protocol):
    def replace_all(
        self,
        *,
        positions: list[PositionRecord],
        trading_mode,
        strategy_version: str,
        commit: bool = True,
    ) -> None: ...

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


@dataclass(frozen=True)
class StartupRecoveryReport:
    mismatches: tuple[str, ...]
    synced_position_count: int
    synced_order_count: int
    cleared_position_count: int
    cleared_order_count: int


def recover_startup_state(
    *,
    settings: Settings,
    runtime: RuntimeProtocol,
    broker_open_positions: Sequence[BrokerPosition],
    broker_open_orders: Sequence[BrokerOrder],
    now: datetime | Callable[[], datetime] | None = None,
    audit_event_type: str | None = "startup_recovery_completed",
    notifier: Notifier | None = None,
    default_strategy_name: str = "breakout",
) -> StartupRecoveryReport:
    timestamp = _resolve_now(now)
    mismatches: list[str] = []

    local_positions = runtime.position_store.list_all(
        trading_mode=settings.trading_mode,
        strategy_version=settings.strategy_version,
    )
    local_positions_by_symbol: dict[str, list[PositionRecord]] = {}
    for position in local_positions:
        local_positions_by_symbol.setdefault(position.symbol, []).append(position)
    broker_positions_by_symbol = {position.symbol: position for position in broker_open_positions}

    synced_positions: list[PositionRecord] = []
    for broker_position in broker_open_positions:
        local_for_symbol = local_positions_by_symbol.get(broker_position.symbol, [])

        if not local_for_symbol:
            mismatches.append(f"broker position missing locally: {broker_position.symbol}")
            resolved_entry_price = broker_position.entry_price
            if resolved_entry_price is not None and resolved_entry_price != 0.0:
                stop_price = resolved_entry_price * (1 - settings.breakout_stop_buffer_pct)
                initial_stop_price = stop_price
            else:
                stop_price = 0.0
                initial_stop_price = 0.0
                runtime.audit_event_store.append(
                    AuditEvent(
                        event_type="startup_recovery_missing_entry_price",
                        payload={"symbol": broker_position.symbol},
                        created_at=timestamp,
                    ),
                    commit=False,
                )
            synced_positions.append(
                PositionRecord(
                    symbol=broker_position.symbol,
                    trading_mode=settings.trading_mode,
                    strategy_version=settings.strategy_version,
                    strategy_name=default_strategy_name,
                    quantity=broker_position.quantity,
                    entry_price=broker_position.entry_price if broker_position.entry_price is not None else 0.0,
                    stop_price=stop_price,
                    initial_stop_price=initial_stop_price,
                    opened_at=timestamp,
                    updated_at=timestamp,
                )
            )
        elif len(local_for_symbol) == 1:
            existing = local_for_symbol[0]
            if existing.quantity != broker_position.quantity or (
                broker_position.entry_price is not None
                and round(existing.entry_price, 4) != round(broker_position.entry_price, 4)
            ):
                mismatches.append(f"broker position differs locally: {broker_position.symbol}")
            synced_positions.append(
                PositionRecord(
                    symbol=broker_position.symbol,
                    trading_mode=settings.trading_mode,
                    strategy_version=settings.strategy_version,
                    strategy_name=existing.strategy_name,
                    quantity=broker_position.quantity,
                    entry_price=broker_position.entry_price if broker_position.entry_price is not None else existing.entry_price,
                    stop_price=existing.stop_price,
                    initial_stop_price=existing.initial_stop_price,
                    opened_at=existing.opened_at,
                    updated_at=timestamp,
                )
            )
        else:
            # Multiple strategies hold this symbol simultaneously.
            # Broker reports a single position; each local record preserves its per-strategy qty.
            total_local_qty = sum(p.quantity for p in local_for_symbol)
            if total_local_qty != broker_position.quantity:
                mismatches.append(f"broker position differs locally: {broker_position.symbol}")
            for existing in local_for_symbol:
                synced_positions.append(
                    PositionRecord(
                        symbol=broker_position.symbol,
                        trading_mode=settings.trading_mode,
                        strategy_version=settings.strategy_version,
                        strategy_name=existing.strategy_name,
                        quantity=existing.quantity,
                        entry_price=broker_position.entry_price if broker_position.entry_price is not None else existing.entry_price,
                        stop_price=existing.stop_price,
                        initial_stop_price=existing.initial_stop_price,
                        opened_at=existing.opened_at,
                        updated_at=timestamp,
                    )
                )

    cleared_position_count = 0
    seen_symbols_with_mismatch: set[str] = set()
    for position in local_positions:
        if position.symbol not in broker_positions_by_symbol:
            if position.symbol not in seen_symbols_with_mismatch:
                mismatches.append(f"local position missing at broker: {position.symbol}")
                seen_symbols_with_mismatch.add(position.symbol)
            cleared_position_count += 1

    runtime.position_store.replace_all(
        positions=synced_positions,
        trading_mode=settings.trading_mode,
        strategy_version=settings.strategy_version,
        commit=False,
    )

    local_active_orders = runtime.order_store.list_by_status(
        trading_mode=settings.trading_mode,
        strategy_version=settings.strategy_version,
        statuses=ACTIVE_ORDER_STATUSES,
    )
    local_orders_by_broker_id = {
        order.broker_order_id: order for order in local_active_orders if order.broker_order_id
    }
    local_orders_by_client_id = {order.client_order_id: order for order in local_active_orders}

    synced_order_count = 0
    matched_local_client_ids: set[str] = set()
    for broker_order in broker_open_orders:
        existing = None
        if broker_order.broker_order_id is not None:
            existing = local_orders_by_broker_id.get(broker_order.broker_order_id)
        if existing is None:
            existing = local_orders_by_client_id.get(broker_order.client_order_id)
        if existing is not None:
            matched_local_client_ids.add(existing.client_order_id)

        normalized_status = str(broker_order.status).lower()
        if existing is None:
            mismatches.append(f"broker order missing locally: {broker_order.client_order_id}")
        elif (
            existing.status != normalized_status
            or existing.quantity != broker_order.quantity
            or existing.side != broker_order.side
        ):
            mismatches.append(f"broker order differs locally: {broker_order.client_order_id}")

        runtime.order_store.save(
            OrderRecord(
                client_order_id=broker_order.client_order_id,
                symbol=broker_order.symbol,
                side=broker_order.side,
                intent_type=(
                    existing.intent_type
                    if existing is not None
                    else _infer_intent_type(
                        client_order_id=broker_order.client_order_id,
                        side=broker_order.side,
                    )
                ),
                status=normalized_status,
                quantity=broker_order.quantity,
                trading_mode=settings.trading_mode,
                strategy_version=settings.strategy_version,
                strategy_name=(
                    existing.strategy_name
                    if existing is not None
                    else _infer_strategy_name_from_client_order_id(broker_order.client_order_id)
                ),
                created_at=existing.created_at if existing is not None else timestamp,
                updated_at=timestamp,
                stop_price=existing.stop_price if existing is not None else None,
                limit_price=existing.limit_price if existing is not None else None,
                initial_stop_price=existing.initial_stop_price if existing is not None else None,
                broker_order_id=broker_order.broker_order_id,
                signal_timestamp=existing.signal_timestamp if existing is not None else None,
            ),
            commit=False,
        )
        synced_order_count += 1

    cleared_order_count = 0
    for order in local_active_orders:
        if order.client_order_id in matched_local_client_ids:
            continue
        mismatches.append(f"local order missing at broker: {order.client_order_id}")
        runtime.order_store.save(
            OrderRecord(
                client_order_id=order.client_order_id,
                symbol=order.symbol,
                side=order.side,
                intent_type=order.intent_type,
                status="reconciled_missing",
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
        cleared_order_count += 1

    report = StartupRecoveryReport(
        mismatches=tuple(mismatches),
        synced_position_count=len(synced_positions),
        synced_order_count=synced_order_count,
        cleared_position_count=cleared_position_count,
        cleared_order_count=cleared_order_count,
    )
    if report.mismatches and notifier is not None:
        notifier.send(
            subject="Startup mismatch detected",
            body="\n".join(report.mismatches),
        )
    if audit_event_type is not None:
        runtime.audit_event_store.append(
            AuditEvent(
                event_type=audit_event_type,
                payload={
                    "mismatch_count": len(report.mismatches),
                    "mismatches": list(report.mismatches),
                    "synced_position_count": report.synced_position_count,
                    "synced_order_count": report.synced_order_count,
                    "cleared_position_count": report.cleared_position_count,
                    "cleared_order_count": report.cleared_order_count,
                },
                created_at=timestamp,
            ),
            commit=False,
        )
    runtime.connection.commit()
    return report


def compose_startup_mismatch_detector(
    *,
    recovery_report: StartupRecoveryReport,
    extra_detector: Callable[[RuntimeProtocol, object], Sequence[str]] | None = None,
) -> Callable[[RuntimeProtocol, object], tuple[str, ...]] | None:
    if not recovery_report.mismatches and extra_detector is None:
        return None

    def detector(runtime: RuntimeProtocol, session: object) -> tuple[str, ...]:
        combined = list(recovery_report.mismatches)
        if extra_detector is not None:
            combined.extend(str(item) for item in extra_detector(runtime, session))
        return tuple(dict.fromkeys(combined))

    return detector


def _infer_strategy_name_from_client_order_id(client_order_id: str) -> str:
    """Parse strategy_name from new-format client_order_id: {strategy}:{version}:..."""
    from alpaca_bot.strategy import STRATEGY_REGISTRY
    if not client_order_id:
        return "breakout"
    first_segment = client_order_id.split(":")[0]
    return first_segment if first_segment in STRATEGY_REGISTRY else "breakout"


def _infer_intent_type(*, client_order_id: str, side: str) -> str:
    lowered = client_order_id.lower()
    if ":entry:" in lowered:
        return "entry"
    if ":stop:" in lowered:
        return "stop"
    if ":exit:" in lowered:
        return "exit"
    return "stop" if side.lower() == "sell" else "entry"


def _resolve_now(now: datetime | Callable[[], datetime] | None) -> datetime:
    if isinstance(now, datetime):
        return now
    if callable(now):
        return now()
    return datetime.now(timezone.utc)
