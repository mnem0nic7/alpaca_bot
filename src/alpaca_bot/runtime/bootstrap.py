from __future__ import annotations

from dataclasses import dataclass

from alpaca_bot.config import Settings
from alpaca_bot.storage import (
    AuditEventStore,
    DailySessionStateStore,
    MigrationRunner,
    OrderStore,
    PostgresAdvisoryLock,
    PositionStore,
    TradingStatusStore,
    resolve_migrations_path,
)
from alpaca_bot.storage.db import ConnectionProtocol, connect_postgres


@dataclass
class RuntimeContext:
    settings: Settings
    connection: ConnectionProtocol
    lock: PostgresAdvisoryLock
    trading_status_store: TradingStatusStore
    audit_event_store: AuditEventStore
    order_store: OrderStore
    daily_session_state_store: DailySessionStateStore | None = None
    position_store: PositionStore | None = None


def bootstrap_runtime(
    settings: Settings,
    *,
    connection: ConnectionProtocol | None = None,
    migrations_path: str | Path | None = None,
) -> RuntimeContext:
    runtime_connection = connection or connect_postgres(settings.database_url)
    runner = MigrationRunner(
        connection=runtime_connection,
        migrations_path=resolve_migrations_path(migrations_path),
    )
    runner.apply_all()

    lock = PostgresAdvisoryLock(
        runtime_connection,
        strategy_version=settings.strategy_version,
        trading_mode=settings.trading_mode,
    )
    if not lock.try_acquire():
        raise RuntimeError(
            "Could not acquire singleton trader lock for "
            f"{settings.trading_mode.value}/{settings.strategy_version}"
        )

    return RuntimeContext(
        settings=settings,
        connection=runtime_connection,
        lock=lock,
        trading_status_store=TradingStatusStore(runtime_connection),
        audit_event_store=AuditEventStore(runtime_connection),
        order_store=OrderStore(runtime_connection),
        position_store=PositionStore(runtime_connection),
        daily_session_state_store=DailySessionStateStore(runtime_connection),
    )


def close_runtime(context: RuntimeContext) -> None:
    try:
        context.lock.release()
    finally:
        close = getattr(context.connection, "close", None)
        if callable(close):
            close()
