from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field as dc_field
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

from alpaca_bot.config import Settings
from alpaca_bot.replay.report import BacktestReport, ReplayTradeRecord, report_from_records
from alpaca_bot.storage import (
    AuditEvent,
    AuditEventStore,
    ConfidenceFloorStore,
    DailySessionState,
    DailySessionStateStore,
    EQUITY_SESSION_STATE_STRATEGY_NAME,
    GLOBAL_SESSION_STATE_STRATEGY_NAME,
    OrderRecord,
    OrderStore,
    PositionRecord,
    PositionStore,
    StrategyFlag,
    StrategyFlagStore,
    StrategyWeight,
    StrategyWeightStore,
    TradingStatus,
    TradingStatusStore,
)
from alpaca_bot.storage.repositories import TuningResultStore
from alpaca_bot.storage.db import ConnectionProtocol
from alpaca_bot.strategy import ALL_STRATEGY_NAMES, OPTION_STRATEGY_FACTORIES, STRATEGY_REGISTRY

ADMIN_EVENT_TYPES = ["trading_status_changed", "strategy_flag_changed"]

ALL_AUDIT_EVENT_TYPES = [
    "daily_loss_limit_breached",
    "daily_summary_sent",
    "decision_cycle_completed",
    "extended_hours_cycle",
    "nightly_sweep_completed",
    "option_chains_fetched",
    "option_entry_intent_created",
    "option_order_submitted",
    "option_stop_skipped_no_price",
    "order_dispatch_failed",
    "order_dispatch_stop_price_rejected",
    "postgres_reconnected",
    "runtime_reconciliation_detected",
    "stale_exit_cancel_failed",
    "stale_exit_canceled_for_resubmission",
    "startup_recovery_completed",
    "startup_recovery_skipped",
    "stop_update_skipped_extended_hours",
    "stream_heartbeat_stale",
    "stream_restart_failed",
    "stream_started",
    "stream_stopped",
    "strategy_cycle_error",
    "strategy_entries_changed",
    "strategy_flag_changed",
    "strategy_weights_updated",
    "supervisor_cycle",
    "supervisor_cycle_error",
    "supervisor_idle",
    "trade_update_stream_failed",
    "trade_update_stream_restarted",
    "trade_update_stream_started",
    "trade_update_stream_stopped",
    "trader_startup_completed",
    "trading_status_changed",
    "WATCHLIST_ADD",
    "WATCHLIST_IGNORE",
    "WATCHLIST_REMOVE",
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
STREAM_STALE_WINDOW_SECONDS = 600


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
    exit_reason: str = "eod"          # "stop" or "eod"
    hold_minutes: float | None = None  # (exit_time - entry_time).total_seconds() / 60


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
    strategy_flags: list[tuple[str, bool]] = dc_field(default_factory=list)
    stream_stale: bool = False
    stream_last_stale_at: datetime | None = None


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
    session_report: BacktestReport | None = None


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
    latest_prices: dict[str, float] = dc_field(default_factory=dict)
    realized_pnl: float | None = None
    loss_limit_amount: float | None = None
    strategy_win_loss: dict[str, tuple[int, int]] = dc_field(default_factory=dict)
    strategy_capital_pct: dict[str, float] = dc_field(default_factory=dict)
    strategy_lifetime_pnl: dict[str, float] = dc_field(default_factory=dict)
    account_equity: float | None = None
    total_deployed_notional: float = 0.0


def _option_multiplier(pos) -> int:
    return 100 if getattr(pos, "strategy_name", None) in ("option", "short_option") else 1


def _compute_capital_pct(
    positions: list,
    latest_prices: dict[str, float],
) -> dict[str, float]:
    strategy_value: dict[str, float] = {}
    for pos in positions:
        price = latest_prices.get(pos.symbol, pos.entry_price)
        val = price * pos.quantity * _option_multiplier(pos)
        strategy_value[pos.strategy_name] = strategy_value.get(pos.strategy_name, 0.0) + val
    total = sum(strategy_value.values())
    if total <= 0:
        return {}
    return {name: round(val / total * 100, 1) for name, val in strategy_value.items()}


def total_deployed_notional(positions: list) -> float:
    return sum(pos.quantity * pos.entry_price * _option_multiplier(pos) for pos in positions)


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
    latest_prices: dict[str, float] | None = None,
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
    strategy_flags = (
        [(name, flags_by_name.get(name)) for name in STRATEGY_REGISTRY]
        + [(name, flags_by_name.get(name)) for name in sorted(OPTION_STRATEGY_FACTORIES)]
    )

    if hasattr(daily_session_state_store, "list_by_session"):
        session_states = daily_session_state_store.list_by_session(
            session_date=session_date,
            trading_mode=settings.trading_mode,
            strategy_version=settings.strategy_version,
        )
        strategy_entries_disabled = {s.strategy_name: s.entries_disabled for s in session_states}
    else:
        strategy_entries_disabled = {}

    session_state = daily_session_state_store.load(
        session_date=session_date,
        trading_mode=settings.trading_mode,
        strategy_version=settings.strategy_version,
        strategy_name=GLOBAL_SESSION_STATE_STRATEGY_NAME,
    )
    equity_baseline = session_state.equity_baseline if session_state is not None else None
    if equity_baseline is not None:
        realized_pnl: float | None = order_store.daily_realized_pnl(
            trading_mode=settings.trading_mode,
            strategy_version=settings.strategy_version,
            session_date=session_date,
            market_timezone=str(settings.market_timezone),
        )
        loss_limit_amount: float | None = equity_baseline * settings.daily_loss_limit_pct
    else:
        realized_pnl = None
        loss_limit_amount = None

    positions = position_store.list_all(
        trading_mode=settings.trading_mode,
        strategy_version=settings.strategy_version,
    )
    _managed = {
        pos.strategy_name
        for pos in positions
        if pos.strategy_name in ("short_option", "short_equity")
    }
    if _managed:
        strategy_flags = strategy_flags + [(name, None) for name in sorted(_managed)]

    if hasattr(order_store, "win_loss_counts_by_strategy"):
        strategy_win_loss: dict[str, tuple[int, int]] = order_store.win_loss_counts_by_strategy(
            trading_mode=settings.trading_mode,
            strategy_version=settings.strategy_version,
        )
    else:
        strategy_win_loss = {}
    if hasattr(order_store, "lifetime_pnl_by_strategy"):
        strategy_lifetime_pnl: dict[str, float] = order_store.lifetime_pnl_by_strategy(
            trading_mode=settings.trading_mode,
            strategy_version=settings.strategy_version,
        )
    else:
        strategy_lifetime_pnl = {}
    strategy_capital_pct = _compute_capital_pct(positions, latest_prices or {})

    latest_cycle_loader = getattr(audit_event_store, "load_latest", None)
    latest_cycle_event = (
        latest_cycle_loader(event_types=["supervisor_cycle"])
        if callable(latest_cycle_loader)
        else None
    )
    account_equity: float | None = (
        latest_cycle_event.payload.get("account_equity")
        if latest_cycle_event is not None
        else None
    )
    total_deployed_notional_value: float = total_deployed_notional(positions)

    return DashboardSnapshot(
        generated_at=generated_at,
        trading_status=trading_status_store.load(
            trading_mode=settings.trading_mode,
            strategy_version=settings.strategy_version,
        ),
        session_state=session_state,
        positions=positions,
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
        latest_prices=latest_prices or {},
        realized_pnl=realized_pnl,
        loss_limit_amount=loss_limit_amount,
        strategy_win_loss=strategy_win_loss,
        strategy_capital_pct=strategy_capital_pct,
        strategy_lifetime_pnl=strategy_lifetime_pnl,
        account_equity=account_equity,
        total_deployed_notional=total_deployed_notional_value,
    )


def load_health_snapshot(
    *,
    settings: Settings,
    connection: ConnectionProtocol,
    trading_status_store: TradingStatusStore | None = None,
    audit_event_store: AuditEventStore | None = None,
    strategy_flag_store: StrategyFlagStore | None = None,
) -> HealthSnapshot:
    store = trading_status_store or TradingStatusStore(connection)
    audit_event_store = audit_event_store or AuditEventStore(connection)
    strategy_flag_store = strategy_flag_store or StrategyFlagStore(connection)
    now = datetime.now(timezone.utc)
    stale_cutoff = now - timedelta(seconds=STREAM_STALE_WINDOW_SECONDS)
    _recent_stale = audit_event_store.list_by_event_types(
        event_types=["stream_heartbeat_stale"],
        limit=1,
    )
    _stream_last_stale_at = (
        _recent_stale[0].created_at
        if _recent_stale and _recent_stale[0].created_at >= stale_cutoff
        else None
    )
    _stream_stale = _stream_last_stale_at is not None
    recent_events = audit_event_store.list_recent(limit=12)
    flags_by_name = {
        f.strategy_name: f.enabled
        for f in strategy_flag_store.list_all(
            trading_mode=settings.trading_mode,
            strategy_version=settings.strategy_version,
        )
    }
    strategy_flags = (
        [(name, flags_by_name.get(name, True)) for name in STRATEGY_REGISTRY]
        + [(name, flags_by_name.get(name, True)) for name in sorted(OPTION_STRATEGY_FACTORIES)]
    )
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
        strategy_flags=strategy_flags,
        stream_stale=_stream_stale,
        stream_last_stale_at=_stream_last_stale_at,
    )


def load_metrics_snapshot(
    *,
    settings: Settings,
    connection: ConnectionProtocol,
    order_store: OrderStore | None = None,
    audit_event_store: AuditEventStore | None = None,
    tuning_result_store: TuningResultStore | None = None,
    daily_session_state_store: DailySessionStateStore | None = None,
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

    session_report: BacktestReport | None = None
    if raw_trades:
        state_store = daily_session_state_store or DailySessionStateStore(connection)
        state = state_store.load(
            session_date=session_date,
            trading_mode=settings.trading_mode,
            strategy_version=settings.strategy_version,
            strategy_name=EQUITY_SESSION_STATE_STRATEGY_NAME,
        )
        starting_equity = (
            state.equity_baseline
            if state is not None and state.equity_baseline is not None
            else 100_000.0
        )
        replay_records = [_row_to_replay_record(r) for r in raw_trades]
        session_report = report_from_records(
            replay_records, starting_equity=starting_equity, strategy_name="all"
        )

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
        session_report=session_report,
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


def load_decisions_page(
    *,
    session_date: date,
    symbol: str | None,
    decision_log_store: object,
) -> list[dict]:
    try:
        return decision_log_store.list_recent(  # type: ignore[union-attr]
            session_date=session_date,
            symbol=symbol or None,
        )
    except Exception:
        logger.exception("decision log query failed for date=%s symbol=%s", session_date, symbol)
        return []


def _to_trade_record(row: dict) -> TradeRecord:
    entry_fill = row["entry_fill"]
    exit_fill = row["exit_fill"]
    qty = row["qty"]
    multiplier = 100 if row.get("strategy_name") in ("option", "short_option") else 1
    pnl = (exit_fill - entry_fill) * qty * multiplier
    slippage = (
        row["entry_limit"] - entry_fill
        if row.get("entry_limit") is not None
        else None
    )
    entry_time = row.get("entry_time")
    exit_time = row.get("exit_time")
    hold_minutes = (
        (exit_time - entry_time).total_seconds() / 60
        if entry_time is not None and exit_time is not None
        else None
    )
    exit_reason = "stop" if row.get("intent_type") == "stop" else "eod"
    return TradeRecord(
        symbol=row["symbol"],
        strategy_name=row.get("strategy_name", "breakout"),
        entry_time=entry_time,
        exit_time=exit_time,
        entry_price=entry_fill,
        exit_price=exit_fill,
        quantity=qty,
        pnl=pnl,
        slippage=slippage,
        exit_reason=exit_reason,
        hold_minutes=hold_minutes,
    )


def _row_to_replay_record(row: dict) -> ReplayTradeRecord:
    entry = row["entry_fill"]
    exit_ = row["exit_fill"]
    qty = row["qty"]
    pnl = (exit_ - entry) * qty
    return_pct = (exit_ - entry) / entry
    exit_reason = "stop" if row.get("intent_type") == "stop" else "eod"
    return ReplayTradeRecord(
        symbol=row["symbol"],
        entry_price=entry,
        exit_price=exit_,
        quantity=qty,
        entry_time=row["entry_time"],
        exit_time=row["exit_time"],
        exit_reason=exit_reason,
        pnl=pnl,
        return_pct=return_pct,
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


@dataclass(frozen=True)
class EquityChartPoint:
    t: datetime
    v: float


@dataclass(frozen=True)
class EquityChartData:
    range_code: str
    points: list[EquityChartPoint]
    current: float
    pct_change: float  # percentage, e.g. 1.5 means +1.5%
    label: str


@dataclass(frozen=True)
class StrategyWeightRow:
    strategy_name: str
    weight: float
    sharpe: float


def load_strategy_weights(
    *,
    settings: Settings,
    connection,
    strategy_weight_store=None,
) -> list[StrategyWeightRow]:
    store = strategy_weight_store or StrategyWeightStore(connection)
    weights = store.load_all(
        trading_mode=settings.trading_mode,
        strategy_version=settings.strategy_version,
    )
    rows = [
        StrategyWeightRow(
            strategy_name=w.strategy_name,
            weight=w.weight,
            sharpe=w.sharpe,
        )
        for w in weights
    ]
    return sorted(rows, key=lambda r: r.weight, reverse=True)


def _equity_label(range_code: str) -> str:
    return {"1d": "Today", "1m": "1 Month", "1y": "1 Year", "all": "All Time"}.get(range_code, range_code)


def load_equity_chart_data(
    *,
    settings,
    connection,
    range_code: str,
    anchor_date: date,
    now: datetime,
    order_store=None,
    daily_session_state_store=None,
) -> EquityChartData:
    tz = ZoneInfo(str(settings.market_timezone))

    if range_code == "1d":
        start_date = anchor_date
        end_date = anchor_date
    elif range_code == "1m":
        start_date = anchor_date - timedelta(days=31)
        end_date = anchor_date
    elif range_code == "1y":
        start_date = anchor_date - timedelta(days=366)
        end_date = anchor_date
    else:  # all
        start_date = date(2000, 1, 1)
        end_date = anchor_date

    o_store = order_store or OrderStore(connection)
    d_store = daily_session_state_store or DailySessionStateStore(connection)

    baselines: dict[date, float] = d_store.list_equity_baselines(
        trading_mode=settings.trading_mode,
        strategy_version=settings.strategy_version,
        start_date=start_date,
        end_date=end_date,
    )
    exits = o_store.list_trade_exits_in_range(
        trading_mode=settings.trading_mode,
        strategy_version=settings.strategy_version,
        start_date=start_date,
        end_date=end_date,
    )

    if range_code == "1d":
        return _build_1d_series(anchor_date, baselines, exits, tz)
    else:
        return _build_multi_session_series(range_code, baselines, exits, tz)


def _build_1d_series(
    session_date: date,
    baselines: dict[date, float],
    exits: list[dict],
    tz,
) -> EquityChartData:
    baseline = baselines.get(session_date, 0.0)
    session_start = datetime.combine(session_date, time(9, 30), tzinfo=tz)

    points: list[EquityChartPoint] = [EquityChartPoint(t=session_start, v=baseline)]
    cumulative = baseline
    for exit_record in exits:
        exit_time = exit_record["exit_time"]
        if exit_time.tzinfo is None:
            exit_time = exit_time.replace(tzinfo=timezone.utc)
        if exit_time < session_start:
            continue
        cumulative += exit_record["pnl"]
        points.append(EquityChartPoint(t=exit_time, v=cumulative))

    current = cumulative
    pct_change = ((current - baseline) / baseline * 100) if baseline else 0.0
    return EquityChartData(
        range_code="1d",
        points=points,
        current=current,
        pct_change=round(pct_change, 4),
        label=_equity_label("1d"),
    )


def _build_multi_session_series(
    range_code: str,
    baselines: dict[date, float],
    exits: list[dict],
    tz,
) -> EquityChartData:
    exits_by_date: dict[date, list[dict]] = defaultdict(list)
    for exit_record in exits:
        exit_time = exit_record["exit_time"]
        if exit_time.tzinfo is None:
            exit_time = exit_time.replace(tzinfo=timezone.utc)
        et_date = exit_time.astimezone(tz).date()
        exits_by_date[et_date].append(exit_record)

    points: list[EquityChartPoint] = []
    for session_date in sorted(baselines):
        baseline = baselines[session_date]
        session_pnl = sum(e["pnl"] for e in exits_by_date.get(session_date, []))
        close_time = datetime.combine(session_date, time(16, 0), tzinfo=tz)
        points.append(EquityChartPoint(t=close_time, v=baseline + session_pnl))

    if points:
        sorted_dates = sorted(baselines)
        first_v = baselines[sorted_dates[0]]
        current = points[-1].v
        pct_change = ((current - first_v) / first_v * 100) if first_v else 0.0
    else:
        current = 0.0
        pct_change = 0.0

    return EquityChartData(
        range_code=range_code,
        points=points,
        current=current,
        pct_change=round(pct_change, 4),
        label=_equity_label(range_code),
    )


def _parse_confidence_trigger(reason: str | None) -> str | None:
    """Parse the trigger type from a confidence floor reason string."""
    if reason is None:
        return None
    lower = reason.lower()
    if "drawdown" in lower:
        return "drawdown"
    if "volatility" in lower or "vol " in lower:
        return "volatility"
    return None


def load_confidence_floor_info(
    *,
    settings: Settings,
    confidence_floor_store: ConfidenceFloorStore,
) -> dict:
    """Load confidence floor data for dashboard display.

    Returns a dict with:
      floor_value       – current floor (from store or settings fallback)
      manual_baseline   – last operator-set baseline
      set_by            – "operator" | "system"
      reason            – reason string from the store, or None
      auto_raised       – True if floor_value > manual_floor_baseline
      trigger           – "drawdown" | "volatility" | None (parsed from reason)
      no_record         – True if no DB record exists
    """
    record = confidence_floor_store.load(
        trading_mode=settings.trading_mode,
        strategy_version=settings.strategy_version,
    )

    if record is None:
        default_floor = settings.confidence_floor
        return {
            "floor_value": default_floor,
            "manual_baseline": default_floor,
            "set_by": "operator",
            "reason": None,
            "auto_raised": False,
            "trigger": None,
            "no_record": True,
        }

    auto_raised = record.floor_value > record.manual_floor_baseline
    return {
        "floor_value": record.floor_value,
        "manual_baseline": record.manual_floor_baseline,
        "set_by": record.set_by,
        "reason": record.reason,
        "auto_raised": auto_raised,
        "trigger": _parse_confidence_trigger(record.reason),
        "no_record": False,
    }


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
