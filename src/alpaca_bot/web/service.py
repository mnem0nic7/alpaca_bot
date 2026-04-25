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
WORKER_ACTIVITY_EVENT_TYPES = (
    "supervisor_cycle",
    "supervisor_idle",
    "trader_startup_completed",
)
WORKER_STALE_AFTER_SECONDS = 180


@dataclass(frozen=True)
class WorkerHealth:
    status: str
    last_event_type: str | None
    last_event_at: datetime | None
    age_seconds: int | None
    stale_after_seconds: int = WORKER_STALE_AFTER_SECONDS


@dataclass(frozen=True)
class HealthSnapshot:
    trading_status: TradingStatus | None
    worker_health: WorkerHealth


@dataclass(frozen=True)
class DashboardSnapshot:
    generated_at: datetime
    trading_status: TradingStatus | None
    session_state: DailySessionState | None
    positions: list[PositionRecord]
    working_orders: list[OrderRecord]
    recent_orders: list[OrderRecord]
    recent_events: list[AuditEvent]
    worker_health: WorkerHealth


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
    recent_events = audit_event_store.list_recent(limit=12)

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
        recent_events=recent_events,
        worker_health=_load_worker_health(
            audit_event_store=audit_event_store,
            recent_events=recent_events,
            now=generated_at,
        ),
    )


def load_health_snapshot(
    *,
    settings: Settings,
    connection: ConnectionProtocol,
    trading_status_store: TradingStatusStore | None = None,
    audit_event_store: AuditEventStore | None = None,
) -> HealthSnapshot:
    store = trading_status_store or TradingStatusStore(connection)
    audit_event_store = audit_event_store or AuditEventStore(connection)
    now = datetime.now(timezone.utc)
    recent_events = audit_event_store.list_recent(limit=12)
    return HealthSnapshot(
        trading_status=store.load(
            trading_mode=settings.trading_mode,
            strategy_version=settings.strategy_version,
        ),
        worker_health=_load_worker_health(
            audit_event_store=audit_event_store,
            recent_events=recent_events,
            now=now,
        ),
    )


def _load_worker_health(
    *,
    audit_event_store: AuditEventStore,
    recent_events: list[AuditEvent],
    now: datetime,
) -> WorkerHealth:
    latest_loader = getattr(audit_event_store, "load_latest", None)
    latest_event = (
        latest_loader(event_types=list(WORKER_ACTIVITY_EVENT_TYPES))
        if callable(latest_loader)
        else None
    )
    if latest_event is None:
        latest_event = next(
            (
                event
                for event in recent_events
                if event.event_type in WORKER_ACTIVITY_EVENT_TYPES
            ),
            None,
        )
    if latest_event is None:
        return WorkerHealth(
            status="missing",
            last_event_type=None,
            last_event_at=None,
            age_seconds=None,
        )

    age_seconds = max(int((now - latest_event.created_at).total_seconds()), 0)
    return WorkerHealth(
        status="fresh" if age_seconds <= WORKER_STALE_AFTER_SECONDS else "stale",
        last_event_type=latest_event.event_type,
        last_event_at=latest_event.created_at,
        age_seconds=age_seconds,
    )
