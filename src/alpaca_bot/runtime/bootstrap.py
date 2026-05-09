from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import threading

from alpaca_bot.config import Settings
from alpaca_bot.storage import (
    AuditEventStore,
    ConfidenceFloorStore,
    DailySessionStateStore,
    DecisionLogStore,
    MarketContextStore,
    MigrationRunner,
    OptionOrderRepository,
    OrderStore,
    PostgresAdvisoryLock,
    PositionStore,
    StrategyFlagStore,
    StrategyWeightStore,
    TradingStatusStore,
    WatchlistStore,
    resolve_migrations_path,
)
from alpaca_bot.storage.db import ConnectionProtocol, connect_postgres, connect_postgres_with_retry


class LockAcquisitionError(Exception):
    """Raised when the advisory lock cannot be acquired or re-acquired after reconnect."""


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
    strategy_flag_store: StrategyFlagStore | None = None
    watchlist_store: WatchlistStore | None = None
    option_order_store: OptionOrderRepository | None = None
    strategy_weight_store: StrategyWeightStore | None = None
    confidence_floor_store: ConfidenceFloorStore | None = None
    decision_log_store: DecisionLogStore | None = None
    market_context_store: MarketContextStore | None = None
    # Protects all store operations against concurrent access from the trade update stream thread
    store_lock: threading.Lock = field(default_factory=threading.Lock)


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

    watchlist_store = WatchlistStore(runtime_connection)
    enabled_symbols = watchlist_store.list_enabled(settings.trading_mode.value)
    if not enabled_symbols:
        watchlist_store.seed(settings.symbols, settings.trading_mode.value, commit=True)

    return RuntimeContext(
        settings=settings,
        connection=runtime_connection,
        lock=lock,
        trading_status_store=TradingStatusStore(runtime_connection),
        audit_event_store=AuditEventStore(runtime_connection),
        order_store=OrderStore(runtime_connection),
        position_store=PositionStore(runtime_connection),
        daily_session_state_store=DailySessionStateStore(runtime_connection),
        strategy_flag_store=StrategyFlagStore(runtime_connection),
        watchlist_store=watchlist_store,
        option_order_store=OptionOrderRepository(runtime_connection),
        strategy_weight_store=StrategyWeightStore(runtime_connection),
        confidence_floor_store=ConfidenceFloorStore(runtime_connection),
        decision_log_store=DecisionLogStore(runtime_connection),
        market_context_store=MarketContextStore(runtime_connection),
    )


def reconnect_runtime_connection(
    context: RuntimeContext,
    *,
    _new_conn: ConnectionProtocol | None = None,
) -> None:
    """Replace the underlying Postgres connection in *context* and all stores.

    Opens a new connection using :func:`connect_postgres_with_retry` (up to 3
    attempts, 2 s apart) and splices it into every object that holds a
    ``_connection`` attribute, as well as ``context.connection`` itself.

    This is called by the supervisor when :func:`~alpaca_bot.storage.db.check_connection`
    detects that the current connection is dead.

    *_new_conn* is an injection seam for tests — production code never passes it.
    """
    new_conn = _new_conn if _new_conn is not None else connect_postgres_with_retry(context.settings.database_url)
    # RuntimeContext is a plain (non-frozen) dataclass so direct assignment works.
    context.connection = new_conn
    # Rewire all stores that cache the connection.
    for attr in (
        "trading_status_store",
        "audit_event_store",
        "order_store",
        "daily_session_state_store",
        "position_store",
        "strategy_flag_store",
        "watchlist_store",
        "option_order_store",
        "strategy_weight_store",
        "confidence_floor_store",
        "decision_log_store",
        "market_context_store",
    ):
        store = getattr(context, attr, None)
        if store is not None and hasattr(store, "_connection"):
            store._connection = new_conn
    # Rewire the advisory lock and re-acquire it on the fresh connection.
    if hasattr(context.lock, "_connection"):
        context.lock._connection = new_conn
    if not context.lock.try_acquire():
        try:
            new_conn.close()
        except Exception:
            pass
        raise LockAcquisitionError(
            "Could not re-acquire singleton trader lock after reconnect for "
            f"{context.settings.trading_mode.value}/{context.settings.strategy_version}"
        )


def close_runtime(context: RuntimeContext) -> None:
    try:
        context.lock.release()
    finally:
        close = getattr(context.connection, "close", None)
        if callable(close):
            close()
