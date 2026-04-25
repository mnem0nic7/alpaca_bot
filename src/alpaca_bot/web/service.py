from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from alpaca_bot.config import Settings
from alpaca_bot.storage import (
    AuditEvent,
    AuditEventStore,
    DailySessionState,
    DailySessionStateStore,
    OrderRecord,
    OrderStore,
    PositionRecord,
    PositionStore,
    TradingStatus,
    TradingStatusStore,
)
from alpaca_bot.storage.db import ConnectionProtocol

WORKING_ORDER_STATUSES = [
    "pending_submit",
    "submitted",
    "accepted",
    "new",
    "partially_filled",
    "replace_pending",
    "cancel_pending",
]


@dataclass(frozen=True)
class DashboardSnapshot:
    generated_at: datetime
    trading_status: TradingStatus | None
    session_state: DailySessionState | None
    positions: list[PositionRecord]
    working_orders: list[OrderRecord]
    recent_orders: list[OrderRecord]
    recent_events: list[AuditEvent]


def load_dashboard_snapshot(
    *,
    settings: Settings,
    connection: ConnectionProtocol,
    trading_status_store: TradingStatusStore | None = None,
    daily_session_state_store: DailySessionStateStore | None = None,
    position_store: PositionStore | None = None,
    order_store: OrderStore | None = None,
    audit_event_store: AuditEventStore | None = None,
    now: datetime | None = None,
) -> DashboardSnapshot:
    generated_at = now or datetime.now(timezone.utc)
    session_date = generated_at.astimezone(settings.market_timezone).date()
    trading_status_store = trading_status_store or TradingStatusStore(connection)
    daily_session_state_store = daily_session_state_store or DailySessionStateStore(connection)
    position_store = position_store or PositionStore(connection)
    order_store = order_store or OrderStore(connection)
    audit_event_store = audit_event_store or AuditEventStore(connection)

    return DashboardSnapshot(
        generated_at=generated_at,
        trading_status=trading_status_store.load(
            trading_mode=settings.trading_mode,
            strategy_version=settings.strategy_version,
        ),
        session_state=daily_session_state_store.load(
            session_date=session_date,
            trading_mode=settings.trading_mode,
            strategy_version=settings.strategy_version,
        ),
        positions=position_store.list_all(
            trading_mode=settings.trading_mode,
            strategy_version=settings.strategy_version,
        ),
        working_orders=order_store.list_by_status(
            trading_mode=settings.trading_mode,
            strategy_version=settings.strategy_version,
            statuses=WORKING_ORDER_STATUSES,
        ),
        recent_orders=order_store.list_recent(
            trading_mode=settings.trading_mode,
            strategy_version=settings.strategy_version,
            limit=10,
        ),
        recent_events=audit_event_store.list_recent(limit=12),
    )


def load_health_snapshot(
    *,
    settings: Settings,
    connection: ConnectionProtocol,
    trading_status_store: TradingStatusStore | None = None,
) -> TradingStatus | None:
    store = trading_status_store or TradingStatusStore(connection)
    return store.load(
        trading_mode=settings.trading_mode,
        strategy_version=settings.strategy_version,
    )
