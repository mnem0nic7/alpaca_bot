from __future__ import annotations

from dataclasses import dataclass, field as dc_field
from datetime import date, datetime, timezone

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
    StrategyFlag,
    StrategyFlagStore,
    TradingStatus,
    TradingStatusStore,
)
from alpaca_bot.storage.repositories import TuningResultStore
from alpaca_bot.storage.db import ConnectionProtocol
from alpaca_bot.strategy import STRATEGY_REGISTRY

ADMIN_EVENT_TYPES = ["trading_status_changed", "strategy_flag_changed"]

ALL_AUDIT_EVENT_TYPES = [
    "trading_status_changed",
    "strategy_flag_changed",
    "strategy_entries_changed",
    "supervisor_cycle",
    "supervisor_idle",
    "supervisor_cycle_error",
    "strategy_cycle_error",
    "trader_startup_completed",
    "daily_loss_limit_breached",
    "postgres_reconnected",
    "runtime_reconciliation_detected",
    "trade_update_stream_started",
    "trade_update_stream_stopped",
    "trade_update_stream_failed",
    "trade_update_stream_restarted",
    "stream_restart_failed",
    "WATCHLIST_ADD",
    "WATCHLIST_REMOVE",
    "WATCHLIST_IGNORE",
    "WATCHLIST_UNIGNORE",
]

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
class TradeRecord:
    symbol: str
    strategy_name: str
    entry_time: datetime | None
    exit_time: datetime | None
    entry_price: float
    exit_price: float
    quantity: int
    pnl: float
    slippage: float | None  # limit_price - fill_price; positive=favorable, negative=adverse


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
class AuditLogPage:
    events: list[AuditEvent]
    limit: int
    offset: int
    has_more: bool
    event_type_filter: str | None

    @property
    def prev_offset(self) -> int | None:
        if self.offset <= 0:
            return None
        return max(0, self.offset - self.limit)

    @property
    def next_offset(self) -> int | None:
        return self.offset + self.limit if self.has_more else None


@dataclass(frozen=True)
class MetricsSnapshot:
    generated_at: datetime
    session_date: date
    trades: list[TradeRecord]
    trades_by_strategy: dict[str, list[TradeRecord]]
    total_pnl: float
    win_rate: float | None
    mean_return_pct: float | None
    max_drawdown_pct: float | None
    sharpe_ratio: float | None
    admin_history: list[AuditEvent]
    last_backtest: object | None = None  # BacktestReport; None until Phase 5 persists reports


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
    strategy_flags: list[tuple[str, StrategyFlag | None]]
    strategy_entries_disabled: dict[str, bool] = dc_field(default_factory=dict)


def load_dashboard_snapshot(
    *,
    settings: Settings,
    connection: ConnectionProtocol,
    trading_status_store: TradingStatusStore | None = None,
    daily_session_state_store: DailySessionStateStore | None = None,
    position_store: PositionStore | None = None,
    order_store: OrderStore | None = None,
    audit_event_store: AuditEventStore | None = None,
    strategy_flag_store: StrategyFlagStore | None = None,
    now: datetime | None = None,
) -> DashboardSnapshot:
    generated_at = now or datetime.now(timezone.utc)
    session_date = generated_at.astimezone(settings.market_timezone).date()
    trading_status_store = trading_status_store or TradingStatusStore(connection)
    daily_session_state_store = daily_session_state_store or DailySessionStateStore(connection)
    position_store = position_store or PositionStore(connection)
    order_store = order_store or OrderStore(connection)
    audit_event_store = audit_event_store or AuditEventStore(connection)
    strategy_flag_store = strategy_flag_store or StrategyFlagStore(connection)
    recent_events = audit_event_store.list_recent(limit=12)

    flags_by_name = {
        f.strategy_name: f
        for f in strategy_flag_store.list_all(
            trading_mode=settings.trading_mode,
            strategy_version=settings.strategy_version,
        )
    }
    strategy_flags = [(name, flags_by_name.get(name)) for name in STRATEGY_REGISTRY]

    if hasattr(daily_session_state_store, "list_by_session"):
        session_states = daily_session_state_store.list_by_session(
            session_date=session_date,
            trading_mode=settings.trading_mode,
            strategy_version=settings.strategy_version,
        )
        strategy_entries_disabled = {s.strategy_name: s.entries_disabled for s in session_states}
    else:
        strategy_entries_disabled = {}

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
        strategy_flags=strategy_flags,
        strategy_entries_disabled=strategy_entries_disabled,
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


def load_metrics_snapshot(
    *,
    settings: Settings,
    connection: ConnectionProtocol,
    order_store: OrderStore | None = None,
    audit_event_store: AuditEventStore | None = None,
    tuning_result_store: TuningResultStore | None = None,
    now: datetime | None = None,
    session_date: date | None = None,
) -> MetricsSnapshot:
    generated_at = now or datetime.now(timezone.utc)
    session_date = session_date or generated_at.astimezone(settings.market_timezone).date()
    order_store = order_store or OrderStore(connection)
    audit_event_store = audit_event_store or AuditEventStore(connection)
    tuning_store = tuning_result_store or TuningResultStore(connection)

    raw_trades = order_store.list_closed_trades(
        trading_mode=settings.trading_mode,
        strategy_version=settings.strategy_version,
        session_date=session_date,
        market_timezone=str(settings.market_timezone),
    )
    trades = [_to_trade_record(t) for t in raw_trades]
    trades_by_strategy: dict[str, list[TradeRecord]] = {}
    for trade in trades:
        trades_by_strategy.setdefault(trade.strategy_name, []).append(trade)
    admin_history = audit_event_store.list_by_event_types(
        event_types=ADMIN_EVENT_TYPES,
        limit=20,
    )
    last_tuning = tuning_store.load_latest_best(trading_mode=settings.trading_mode.value)
    return MetricsSnapshot(
        generated_at=generated_at,
        session_date=session_date,
        trades=trades,
        trades_by_strategy=trades_by_strategy,
        total_pnl=sum(t.pnl for t in trades),
        win_rate=_win_rate(trades),
        mean_return_pct=_mean_return_pct(trades),
        max_drawdown_pct=_max_drawdown_pct(trades),
        sharpe_ratio=_compute_sharpe_from_trade_records(trades),
        admin_history=admin_history,
        last_backtest=last_tuning,
    )


def load_audit_page(
    *,
    connection: ConnectionProtocol,
    audit_event_store: AuditEventStore | None = None,
    limit: int = 50,
    offset: int = 0,
    event_type_filter: str | None = None,
) -> AuditLogPage:
    store = audit_event_store or AuditEventStore(connection)
    fetch_limit = limit + 1
    if event_type_filter:
        events = store.list_by_event_types(
            event_types=[event_type_filter],
            limit=fetch_limit,
            offset=offset,
        )
    else:
        events = store.list_recent(limit=fetch_limit, offset=offset)
    has_more = len(events) > limit
    return AuditLogPage(
        events=events[:limit],
        limit=limit,
        offset=offset,
        has_more=has_more,
        event_type_filter=event_type_filter,
    )


def _to_trade_record(row: dict) -> TradeRecord:
    entry_fill = row["entry_fill"]
    exit_fill = row["exit_fill"]
    qty = row["qty"]
    pnl = (exit_fill - entry_fill) * qty
    slippage = (
        row["entry_limit"] - entry_fill
        if row.get("entry_limit") is not None
        else None
    )
    return TradeRecord(
        symbol=row["symbol"],
        strategy_name=row.get("strategy_name", "breakout"),
        entry_time=row.get("entry_time"),
        exit_time=row.get("exit_time"),
        entry_price=entry_fill,
        exit_price=exit_fill,
        quantity=qty,
        pnl=pnl,
        slippage=slippage,
    )


def _compute_sharpe_from_trade_records(trades: list[TradeRecord]) -> float | None:
    returns = [
        t.pnl / (t.entry_price * t.quantity)
        for t in trades
        if t.entry_price > 0 and t.quantity > 0
    ]
    if len(returns) < 2:
        return None
    mean_r = sum(returns) / len(returns)
    variance = sum((r - mean_r) ** 2 for r in returns) / (len(returns) - 1)
    std_r = variance ** 0.5
    if std_r == 0.0:
        return None
    return mean_r / std_r


def _win_rate(trades: list[TradeRecord]) -> float | None:
    if not trades:
        return None
    return sum(1 for t in trades if t.pnl > 0) / len(trades)


def _mean_return_pct(trades: list[TradeRecord]) -> float | None:
    if not trades:
        return None
    returns = [t.pnl / (t.entry_price * t.quantity) for t in trades if t.entry_price > 0 and t.quantity > 0]
    return sum(returns) / len(returns) if returns else None


def _max_drawdown_pct(trades: list[TradeRecord]) -> float | None:
    if not trades:
        return None
    cumulative = 0.0
    peak = 0.0
    max_dd = 0.0
    for t in trades:
        cumulative += t.pnl
        if cumulative > peak:
            peak = cumulative
        if peak > 0:
            dd = (peak - cumulative) / peak
            if dd > max_dd:
                max_dd = dd
    return max_dd if peak > 0 else None


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
