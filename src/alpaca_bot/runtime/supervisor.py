from __future__ import annotations

import contextlib
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, timezone
import logging
from pathlib import Path
import signal
import threading
import time
from typing import Any, Callable

logger = logging.getLogger(__name__)

from alpaca_bot.config import Settings
from alpaca_bot.domain import OpenPosition
from alpaca_bot.execution import (
    AlpacaBroker,
    AlpacaMarketDataAdapter,
    AlpacaTradingStreamAdapter,
    BrokerPosition,
)
from alpaca_bot.notifications import Notifier
from alpaca_bot.notifications.factory import build_notifier
from alpaca_bot.runtime.bootstrap import (
    LockAcquisitionError,
    RuntimeContext,
    bootstrap_runtime,
    close_runtime,
    reconnect_runtime_connection,
)
from alpaca_bot.risk.weighting import compute_strategy_weights
from alpaca_bot.storage.db import check_connection
from alpaca_bot.runtime.cli import _list_open_orders, _list_open_positions
from alpaca_bot.core.engine import CycleIntentType
from alpaca_bot.runtime.cycle import run_cycle
from alpaca_bot.runtime.cycle_intent_execution import ACTIVE_STOP_STATUSES, execute_cycle_intents
from alpaca_bot.runtime.order_dispatch import dispatch_pending_orders
from alpaca_bot.runtime.startup_recovery import (
    compose_startup_mismatch_detector,
    recover_startup_state,
    StartupRecoveryReport,
)
from alpaca_bot.runtime.trade_update_stream import attach_trade_update_stream
from alpaca_bot.runtime.trader import TraderStartupReport, start_trader
from alpaca_bot.storage import (
    AuditEvent,
    DailySessionState,
    EQUITY_SESSION_STATE_STRATEGY_NAME,
    GLOBAL_SESSION_STATE_STRATEGY_NAME,
    PositionRecord,
    TradingStatusValue,
)
from alpaca_bot.strategy import OPTION_STRATEGY_NAMES, STRATEGY_REGISTRY, StrategySignalEvaluator
from alpaca_bot.strategy.breakout import evaluate_breakout_signal as _default_evaluator, is_past_flatten_time
from alpaca_bot.strategy.breakout_calls import make_breakout_calls_evaluator
from alpaca_bot.strategy.session import SessionType, detect_session_type
from alpaca_bot.execution.option_chain import AlpacaOptionChainAdapter
from alpaca_bot.runtime.option_dispatch import dispatch_pending_option_orders
from alpaca_bot.storage.models import OptionOrderRecord


STREAM_HEARTBEAT_TIMEOUT_SECONDS = 300


@dataclass(frozen=True)
class SupervisorCycleReport:
    entries_disabled: bool
    cycle_result: object
    dispatch_report: object
    account_equity: float = 0.0


@dataclass(frozen=True)
class SupervisorLoopReport:
    iterations: int
    active_iterations: int
    idle_iterations: int


class RuntimeSupervisor:
    def __init__(
        self,
        *,
        settings: Settings,
        runtime: RuntimeContext,
        broker: object,
        market_data: object,
        stream: object | None,
        start_trader_fn: Callable[..., TraderStartupReport] | None = None,
        cycle_runner: Callable[..., object] | None = None,
        order_dispatcher: Callable[..., object] | None = None,
        cycle_intent_executor: Callable[..., object] | None = None,
        stream_attacher: Callable[..., object] | None = None,
        close_runtime_fn: Callable[[RuntimeContext], None] | None = None,
        connection_checker: Callable[..., bool] | None = None,
        reconnect_fn: Callable[[RuntimeContext], None] | None = None,
        notifier: Notifier | None = None,
        option_chain_adapter=None,
        option_broker=None,
    ) -> None:
        self.settings = settings
        self.runtime = runtime
        self.broker = broker
        self.market_data = market_data
        self.stream = stream
        self._start_trader = start_trader_fn or start_trader
        self._cycle_runner = cycle_runner or run_cycle
        self._order_dispatcher = order_dispatcher or dispatch_pending_orders
        self._cycle_intent_executor = cycle_intent_executor or execute_cycle_intents
        self._stream_attacher = stream_attacher or attach_trade_update_stream
        self._close_runtime = close_runtime_fn or close_runtime
        self._check_connection = connection_checker or check_connection
        self._reconnect = reconnect_fn or reconnect_runtime_connection
        self._notifier = notifier
        self._option_chain_adapter = option_chain_adapter
        self._option_broker = option_broker
        self._stream_attached = False
        self._stream_thread: threading.Thread | None = None
        self._closed = False
        self._stream_restart_attempts: int = 0
        self._next_stream_restart_at: datetime | None = None
        self._last_stream_event_at: datetime | None = None
        self._stream_heartbeat_alerted: bool = False
        self._consecutive_cycle_failures: int = 0
        # Keyed by session_date (ET); populated on the first cycle of each day.
        self._session_equity_baseline: dict[date, float] = {}
        # Per-session capital weights: keyed by session_date, populated once per day.
        self._session_capital_weights: dict[date, dict[str, float]] = {}
        # Dates for which the daily loss limit alert has already been sent (once per day).
        self._loss_limit_alerted: set[date] = set()
        # Dates for which the daily loss limit has ever fired; sticky — never cleared on recovery.
        self._loss_limit_fired: set[date] = set()
        # Per-symbol loss limit: dict[session_date, set[symbol]] — prevents duplicate alerts.
        self._per_symbol_limit_alerted: dict[date, set[str]] = {}
        # Dates for which the daily session summary has already been sent.
        self._summary_sent: set[date] = set()
        # Dates on which at least one active cycle ran this process lifetime.
        self._session_had_active_cycle: set[date] = set()
        self._shutdown_requested: bool = False
        # Intra-day review: keyed by session_date; reset to new dict each process lifetime.
        self._session_cycle_count: dict[date, int] = {}
        self._digest_sent_count: dict[date, int] = {}
        self._consecutive_loss_gate_fired: set[date] = set()

    @classmethod
    def from_settings(cls, settings: Settings) -> "RuntimeSupervisor":
        broker = AlpacaBroker.from_settings(settings)
        fractionable = broker.get_fractionable_symbols(settings.symbols)
        settings = replace(settings, fractionable_symbols=fractionable)
        option_chain_adapter = None
        option_broker = None
        if settings.enable_options_trading:
            option_chain_adapter = AlpacaOptionChainAdapter.from_settings(settings)
            option_broker = broker
        return cls(
            settings=settings,
            runtime=bootstrap_runtime(settings),
            broker=broker,
            market_data=AlpacaMarketDataAdapter.from_settings(settings),
            stream=AlpacaTradingStreamAdapter.from_settings(settings),
            notifier=build_notifier(settings),
            option_chain_adapter=option_chain_adapter,
            option_broker=option_broker,
        )

    def startup(
        self,
        *,
        now: Callable[[], datetime] | None = None,
        mismatch_detector=None,
    ) -> TraderStartupReport:
        timestamp = _resolve_now(now)
        open_orders = list(_list_open_orders(self.broker))
        open_positions = list(_list_open_positions(self.broker))
        _startup_lock = getattr(self.runtime, "store_lock", None)
        _startup_lock_ctx = _startup_lock if _startup_lock is not None else contextlib.nullcontext()
        with _startup_lock_ctx:
            recovery_report = recover_startup_state(
                settings=self.settings,
                runtime=self.runtime,
                broker_open_positions=open_positions,
                broker_open_orders=open_orders,
                now=timestamp,
                notifier=self._notifier,
            )
        report = self._start_trader(
            self.settings,
            broker_client=self.broker,
            bootstrap=lambda _: self.runtime,
            mismatch_detector=compose_startup_mismatch_detector(
                recovery_report=recovery_report,
                extra_detector=mismatch_detector,
            ),
            now=lambda: timestamp,
        )
        if self.stream is not None and not self._stream_attached:
            self._stream_attacher(
                settings=self.settings,
                runtime=self.runtime,
                stream=self.stream,
                now=lambda: timestamp,
                notifier=self._notifier,
                on_event=self._record_stream_event,
            )
            self._stream_attached = True
            self._start_stream_thread(now=lambda: timestamp)
        return report

    def run_cycle_once(
        self,
        *,
        now: Callable[[], datetime] | None = None,
        session_type: SessionType | None = None,
    ) -> SupervisorCycleReport:
        if self._closed:
            raise RuntimeError("Supervisor is closed")

        # Probe the Postgres connection before doing any DB work.  If the
        # connection is dead (TCP timeout, idle culling, server restart) we
        # reconnect with retry rather than letting the cycle fail.
        if not self._check_connection(self.runtime.connection):
            logger.warning(
                "Postgres connection appears dead at cycle start; reconnecting..."
            )
            # Acquire store_lock before rewiring _connection on all store objects so
            # the stream thread is not mid-query on the old connection when we swap it.
            _reconnect_lock = getattr(self.runtime, "store_lock", None)
            with _reconnect_lock if _reconnect_lock is not None else contextlib.nullcontext():
                self._reconnect(self.runtime)
                logger.info("Postgres connection re-established.")
            reconnect_ts = _resolve_now(now)
            self._append_audit(
                AuditEvent(
                    event_type="postgres_reconnected",
                    payload={"timestamp": reconnect_ts.isoformat()},
                    created_at=reconnect_ts,
                )
            )

        timestamp = _resolve_now(now)
        broker_open_orders = list(_list_open_orders(self.broker))
        broker_open_positions = list(_list_open_positions(self.broker))
        _rec_lock = getattr(self.runtime, "store_lock", None)
        _rec_lock_ctx = _rec_lock if _rec_lock is not None else contextlib.nullcontext()
        _recovery_exception_occurred = False
        with _rec_lock_ctx:
            try:
                recovery_report = recover_startup_state(
                    settings=self.settings,
                    runtime=self.runtime,
                    broker_open_positions=broker_open_positions,
                    broker_open_orders=broker_open_orders,
                    now=timestamp,
                    audit_event_type=None,
                )
            except Exception:
                logger.exception("run_cycle_once: startup recovery raised — treating as empty report")
                try:
                    self.runtime.connection.rollback()
                except Exception:
                    pass
                recovery_report = StartupRecoveryReport(
                    mismatches=(),
                    synced_position_count=0,
                    synced_order_count=0,
                    cleared_position_count=0,
                    cleared_order_count=0,
                )
                _recovery_exception_occurred = True
            if recovery_report.mismatches:
                try:
                    self.runtime.audit_event_store.append(
                        AuditEvent(
                            event_type="runtime_reconciliation_detected",
                            payload={
                                "mismatch_count": len(recovery_report.mismatches),
                                "mismatches": list(recovery_report.mismatches),
                                "synced_position_count": recovery_report.synced_position_count,
                                "synced_order_count": recovery_report.synced_order_count,
                                "cleared_position_count": recovery_report.cleared_position_count,
                                "cleared_order_count": recovery_report.cleared_order_count,
                                "timestamp": timestamp.isoformat(),
                            },
                            created_at=timestamp,
                        )
                    )
                except Exception:
                    # Recovery already succeeded — log and continue rather than aborting
                    # the cycle just because we couldn't persist the mismatch audit event.
                    logger.exception(
                        "Failed to append runtime_reconciliation_detected audit event; continuing"
                    )
                    try:
                        self.runtime.connection.rollback()
                    except Exception:
                        pass
        if _recovery_exception_occurred:
            self._append_audit(
                AuditEvent(
                    event_type="recovery_exception",
                    payload={"timestamp": timestamp.isoformat()},
                    created_at=timestamp,
                )
            )
        account = self.broker.get_account()
        session_date = _session_date(timestamp, self.settings)

        # Load session state once and apply staleness check (Task 7).
        session_state = self._load_session_state(
            session_date=session_date,
            strategy_name=GLOBAL_SESSION_STATE_STRATEGY_NAME,
        )
        if session_state is not None and session_state.session_date != session_date:
            session_state = None

        _pnl_lock = getattr(self.runtime, "store_lock", None)
        with _pnl_lock if _pnl_lock is not None else contextlib.nullcontext():
            realized_pnl = self.runtime.order_store.daily_realized_pnl(
                trading_mode=self.settings.trading_mode,
                strategy_version=self.settings.strategy_version,
                session_date=session_date,
                market_timezone=str(self.settings.market_timezone),
            )
        # Snapshot equity once per session day so the daily loss limit is always
        # computed against start-of-day capital, not the current (post-loss) value.
        # On mid-day restart the in-memory dict is empty, so we recover the
        # baseline from Postgres (written on the first cycle of the day).
        if session_date not in self._session_equity_baseline:
            persisted = self._load_session_state(
                session_date=session_date,
                strategy_name=EQUITY_SESSION_STATE_STRATEGY_NAME,
            )
            if persisted is not None and persisted.equity_baseline is not None:
                self._session_equity_baseline[session_date] = persisted.equity_baseline
                if persisted.entries_disabled:
                    self._loss_limit_fired.add(session_date)
            else:
                self._session_equity_baseline[session_date] = account.equity
                self._save_session_state(
                    DailySessionState(
                        session_date=session_date,
                        trading_mode=self.settings.trading_mode,
                        strategy_version=self.settings.strategy_version,
                        strategy_name=EQUITY_SESSION_STATE_STRATEGY_NAME,
                        entries_disabled=False,
                        flatten_complete=False,
                        equity_baseline=account.equity,
                        updated_at=timestamp,
                    )
                )
        baseline_equity = self._session_equity_baseline[session_date]
        if session_date not in self._session_capital_weights:
            weights = self._update_session_weights(session_date)
            self._session_capital_weights[session_date] = weights
        loss_limit = self.settings.daily_loss_limit_pct * baseline_equity
        # Include unrealized losses via broker-reported equity delta so open
        # positions with large drawdowns trigger the limit before stops fill.
        total_pnl = account.equity - baseline_equity
        if total_pnl < -loss_limit:
            self._loss_limit_fired.add(session_date)
        daily_loss_limit_breached = session_date in self._loss_limit_fired
        if daily_loss_limit_breached and session_date not in self._loss_limit_alerted:
            self._loss_limit_alerted.add(session_date)
            self._save_session_state(
                DailySessionState(
                    session_date=session_date,
                    trading_mode=self.settings.trading_mode,
                    strategy_version=self.settings.strategy_version,
                    strategy_name=EQUITY_SESSION_STATE_STRATEGY_NAME,
                    entries_disabled=True,
                    flatten_complete=False,
                    equity_baseline=baseline_equity,
                    updated_at=timestamp,
                )
            )
            self._append_audit(
                AuditEvent(
                    event_type="daily_loss_limit_breached",
                    payload={
                        "realized_pnl": realized_pnl,
                        "total_pnl": total_pnl,
                        "limit": loss_limit,
                        "timestamp": timestamp.isoformat(),
                    },
                    created_at=timestamp,
                )
            )
            if self._notifier is not None:
                try:
                    self._notifier.send(
                        subject="Daily loss limit breached",
                        body=(
                            f"Total PnL {total_pnl:.2f} (realized {realized_pnl:.2f}) "
                            f"exceeded limit {-loss_limit:.2f}. Entries disabled for the session."
                        ),
                    )
                except Exception:
                    logger.exception("Notifier failed to send daily loss limit alert")

        # Intra-day consecutive-loss gate and digest (REGULAR session only, either feature enabled)
        _trades_for_review: list[dict] | None = None
        _intraday_enabled = (
            self.settings.intraday_consecutive_loss_gate > 0
            or self.settings.intraday_digest_interval_cycles > 0
        )
        if _intraday_enabled and session_type is SessionType.REGULAR:
            _intraday_lock = getattr(self.runtime, "store_lock", None)
            try:
                with _intraday_lock if _intraday_lock is not None else contextlib.nullcontext():
                    _trades_for_review = self.runtime.order_store.list_closed_trades(
                        trading_mode=self.settings.trading_mode,
                        strategy_version=self.settings.strategy_version,
                        session_date=session_date,
                        market_timezone=str(self.settings.market_timezone),
                    )
            except Exception:
                logger.exception(
                    "run_cycle_once: list_closed_trades raised — skipping gate and digest"
                )
        if _trades_for_review is not None:
            from alpaca_bot.runtime.daily_summary import trailing_consecutive_losses
            self._session_cycle_count.setdefault(session_date, 0)
            self._session_cycle_count[session_date] += 1
            cl_streak = trailing_consecutive_losses(_trades_for_review)
            if not daily_loss_limit_breached:
                self._maybe_fire_consecutive_loss_gate(
                    session_date=session_date,
                    consecutive_losses=cl_streak,
                    timestamp=timestamp,
                )
                self._maybe_send_intraday_digest(
                    session_date=session_date,
                    closed_trades=_trades_for_review,
                    baseline_equity=baseline_equity,
                    current_equity=account.equity,
                    timestamp=timestamp,
                )

        status = self._effective_trading_status(
            session_date=session_date, session_state=session_state, session_type=session_type
        )
        entries_disabled = (
            status in {TradingStatusValue.CLOSE_ONLY, TradingStatusValue.HALTED}
            or bool(recovery_report.mismatches)
            or daily_loss_limit_breached
            or session_date in self._consecutive_loss_gate_fired
        )
        open_positions = self._load_open_positions()
        working_order_symbols = {order.symbol for order in broker_open_orders}
        working_order_symbols.update(order.symbol for order in self._list_pending_submit_orders())
        # Include symbols with active local stop-sell orders so evaluate_cycle()
        # never emits an entry for a symbol already covered by a stop.  Without this,
        # a symbol whose local stop was cleared by reconciliation (RC-2) could get a
        # new entry submitted, triggering Alpaca wash-trade rejection (RC-5).
        _stop_lock = getattr(self.runtime, "store_lock", None)
        with _stop_lock if _stop_lock is not None else contextlib.nullcontext():
            _active_stop_sell_orders = self.runtime.order_store.list_by_status(
                trading_mode=self.settings.trading_mode,
                strategy_version=self.settings.strategy_version,
                statuses=list(ACTIVE_STOP_STATUSES),
            )
        working_order_symbols.update(
            o.symbol
            for o in _active_stop_sell_orders
            if o.intent_type == "stop" and o.side == "sell"
        )
        # Per-symbol loss limit: compute blocked symbols from today's realized PnL.
        # Applied per-strategy below via strategy_working_symbols — NOT added to
        # working_order_symbols to avoid inflating global_occupied_slots with
        # symbols that hold no open position.
        per_symbol_blocked_symbols: set[str] = set()
        if self.settings.per_symbol_loss_limit_pct > 0:
            _sym_pnl_lock = getattr(self.runtime, "store_lock", None)
            with _sym_pnl_lock if _sym_pnl_lock is not None else contextlib.nullcontext():
                sym_pnl_map = self.runtime.order_store.daily_realized_pnl_by_symbol(
                    trading_mode=self.settings.trading_mode,
                    strategy_version=self.settings.strategy_version,
                    session_date=session_date,
                    market_timezone=str(self.settings.market_timezone),
                )
            per_sym_limit = self.settings.per_symbol_loss_limit_pct * baseline_equity
            day_alerted = self._per_symbol_limit_alerted.setdefault(session_date, set())
            for sym, sym_pnl in sym_pnl_map.items():
                if sym_pnl < -per_sym_limit:
                    per_symbol_blocked_symbols.add(sym)
                    if sym not in day_alerted:
                        day_alerted.add(sym)
                        self._append_audit(
                            AuditEvent(
                                event_type="per_symbol_loss_limit_breached",
                                payload={
                                    "symbol": sym,
                                    "realized_pnl": sym_pnl,
                                    "limit": per_sym_limit,
                                    "timestamp": timestamp.isoformat(),
                                },
                                symbol=sym,
                                created_at=timestamp,
                            )
                        )
                        if self._notifier is not None:
                            try:
                                self._notifier.send(
                                    subject=f"Per-symbol loss limit breached: {sym}",
                                    body=(
                                        f"{sym} realized PnL {sym_pnl:.2f} exceeded "
                                        f"per-symbol limit {-per_sym_limit:.2f}. "
                                        f"New entries for {sym} disabled for the session."
                                    ),
                                )
                            except Exception:
                                logger.exception(
                                    "Notifier failed to send per-symbol loss limit alert for %s",
                                    sym,
                                )
        watchlist_store = getattr(self.runtime, "watchlist_store", None)
        if watchlist_store is not None:
            watchlist_symbols = tuple(watchlist_store.list_enabled(self.settings.trading_mode.value))
            if not watchlist_symbols:
                logger.warning("Symbol watchlist is empty — skipping cycle")
                from types import SimpleNamespace as _SN
                return SupervisorCycleReport(
                    entries_disabled=entries_disabled,
                    cycle_result=_SN(intents=[]),
                    dispatch_report={"submitted_count": 0},
                    account_equity=account.equity,
                )
            ignored_set = set(watchlist_store.list_ignored(self.settings.trading_mode.value))
            entry_symbols = tuple(s for s in watchlist_symbols if s not in ignored_set)
        else:
            watchlist_symbols = self.settings.symbols
            entry_symbols = watchlist_symbols

        intraday_bars_by_symbol = self.market_data.get_stock_bars(
            symbols=list(watchlist_symbols),
            start=timestamp - timedelta(days=5),
            end=timestamp,
            timeframe_minutes=self.settings.entry_timeframe_minutes,
        )
        daily_bars_end = datetime.combine(session_date, datetime.min.time()).replace(
            tzinfo=self.settings.market_timezone
        )
        daily_bars_by_symbol = self.market_data.get_daily_bars(
            symbols=list(watchlist_symbols),
            start=timestamp - timedelta(days=max(
                self.settings.daily_sma_period * 3,
                60,
                self.settings.high_watermark_lookback_days + 10,
            )),
            end=daily_bars_end,
        )
        # Regime filter: reuse already-fetched daily bars if regime_symbol is on the
        # watchlist, otherwise fetch separately to avoid a duplicate API call.
        regime_bars: list[Bar] | None = None
        if self.settings.enable_regime_filter:
            if self.settings.regime_symbol in watchlist_symbols:
                regime_bars = list(daily_bars_by_symbol.get(self.settings.regime_symbol) or []) or None
            else:
                try:
                    regime_daily = self.market_data.get_daily_bars(
                        symbols=[self.settings.regime_symbol],
                        start=timestamp - timedelta(days=max(self.settings.regime_sma_period * 3, 60)),
                        end=daily_bars_end,
                    )
                    regime_bars = regime_daily.get(self.settings.regime_symbol)
                except Exception:
                    logger.warning(
                        "Failed to fetch regime bars for %s; regime filter disabled this cycle",
                        self.settings.regime_symbol,
                        exc_info=True,
                    )

        # News filter data — fetched once per cycle, shared across all strategies.
        news_by_symbol: dict[str, list] | None = None
        if self.settings.enable_news_filter:
            try:
                news_by_symbol = self.market_data.get_news(
                    symbols=list(watchlist_symbols),
                    lookback_hours=self.settings.news_filter_lookback_hours,
                    now=timestamp,
                )
            except Exception:
                logger.warning("Failed to fetch news; news filter disabled this cycle", exc_info=True)

        # Spread filter data — fetched once per cycle, shared across all strategies.
        quotes_by_symbol: dict[str, object] | None = None
        if self.settings.enable_spread_filter:
            try:
                quotes_by_symbol = self.market_data.get_latest_quotes(list(watchlist_symbols))
            except Exception:
                logger.warning("Failed to fetch quotes; spread filter disabled this cycle", exc_info=True)

        # Resolve registered strategies (breakout, etc.)
        active_strategies = list(self._resolve_active_strategies())

        # Fetch option chains and append option strategies when adapter is configured.
        # breakout_calls is NOT in STRATEGY_REGISTRY — it is a factory that closes over chains.
        option_chains_by_symbol: dict = {}
        option_order_store = getattr(self.runtime, "option_order_store", None)
        if self._option_chain_adapter is not None:
            for symbol in self.settings.symbols:
                try:
                    chains = self._option_chain_adapter.get_option_chain(symbol, self.settings)
                    if chains:
                        option_chains_by_symbol[symbol] = chains
                except Exception:
                    logger.exception("option chain fetch failed for %s", symbol)
            for opt_name in OPTION_STRATEGY_NAMES:
                active_strategies.append(
                    (opt_name, make_breakout_calls_evaluator(option_chains_by_symbol))
                )

        # Add open option position underlying symbols to prevent double-entry.
        if option_order_store is not None:
            open_options = option_order_store.list_open_option_positions(
                trading_mode=self.settings.trading_mode,
                strategy_version=self.settings.strategy_version,
            )
            for opt_pos in open_options:
                working_order_symbols.add(opt_pos.underlying_symbol)

        all_cycle_results: list[tuple[str, object]] = []
        entries_disabled_strategies: set[str] = set()
        # Track occupied slots globally across all strategies so no single
        # strategy can exceed the portfolio-wide max_open_positions cap.
        global_occupied_slots = len(
            {p.symbol for p in open_positions} | working_order_symbols
        )
        # All symbols currently held across ALL strategies — used to prevent
        # Strategy B from entering a symbol already held by Strategy A.
        global_position_symbols = {p.symbol for p in open_positions}

        for strategy_name, evaluator in active_strategies:
            try:
                strategy_positions = [
                    p for p in open_positions
                    if getattr(p, "strategy_name", "breakout") == strategy_name
                ]
                # Build from all-strategy working symbols so prior-cycle pending_submit
                # orders from other strategies also block duplicate entries this cycle.
                strategy_working_symbols = set(working_order_symbols)
                # Also block symbols held by other strategies as positions.
                strategy_working_symbols |= (
                    global_position_symbols - {p.symbol for p in strategy_positions}
                )
                # Block symbols that have breached their per-symbol daily loss limit.
                strategy_working_symbols |= per_symbol_blocked_symbols
                strategy_traded_symbols = self._load_traded_symbols(
                    session_date=session_date,
                    strategy_name=strategy_name,
                )
                strategy_session_state = self._load_session_state(
                    session_date=session_date,
                    strategy_name=strategy_name,
                )
                if strategy_session_state is not None and strategy_session_state.session_date != session_date:
                    strategy_session_state = None

                _is_extended = session_type in {SessionType.PRE_MARKET, SessionType.AFTER_HOURS}
                strategy_entries_disabled = (
                    entries_disabled
                    or (
                        strategy_session_state is not None
                        and strategy_session_state.entries_disabled
                        and not _is_extended
                    )
                )
                if strategy_entries_disabled:
                    entries_disabled_strategies.add(strategy_name)

                strategy_weight = self._session_capital_weights[session_date].get(
                    strategy_name, 1.0 / max(len(active_strategies), 1)
                )
                effective_equity = account.equity * strategy_weight
                cycle_result = self._cycle_runner(
                    settings=self.settings,
                    runtime=self.runtime,
                    now=timestamp,
                    equity=effective_equity,
                    intraday_bars_by_symbol=intraday_bars_by_symbol,
                    daily_bars_by_symbol=daily_bars_by_symbol,
                    open_positions=strategy_positions,
                    working_order_symbols=strategy_working_symbols,
                    traded_symbols_today=strategy_traded_symbols,
                    entries_disabled=strategy_entries_disabled,
                    flatten_all=daily_loss_limit_breached,
                    session_state=strategy_session_state,
                    signal_evaluator=evaluator,
                    strategy_name=strategy_name,
                    global_open_count=global_occupied_slots,
                    symbols=entry_symbols,
                    session_type=session_type,
                    regime_bars=regime_bars,
                    news_by_symbol=news_by_symbol,
                    quotes_by_symbol=quotes_by_symbol,
                )
                all_cycle_results.append((strategy_name, cycle_result))
                # Consume slots and symbols taken by entries this strategy emitted so
                # subsequent strategies see the updated global state.
                new_entry_intents = [
                    i for i in getattr(cycle_result, "intents", [])
                    if getattr(i, "intent_type", None) == CycleIntentType.ENTRY
                ]
                global_occupied_slots += len(new_entry_intents)
                global_position_symbols.update(i.symbol for i in new_entry_intents)

                has_flatten_intents = any(
                    getattr(intent, "reason", None) in {"eod_flatten", "loss_limit_flatten"}
                    for intent in getattr(cycle_result, "intents", [])
                )

                exec_report = None
                if status is not TradingStatusValue.HALTED:
                    try:
                        exec_report = self._cycle_intent_executor(
                            settings=self.settings,
                            runtime=self.runtime,
                            broker=self.broker,
                            cycle_result=cycle_result,
                            now=timestamp,
                            session_type=session_type,
                            notifier=self._notifier,
                        )
                    except Exception:
                        logger.exception(
                            "execute_cycle_intents failed for strategy %s; continuing to dispatch",
                            strategy_name,
                        )
                        if has_flatten_intents and self._notifier is not None:
                            try:
                                self._notifier.send(
                                    subject=f"EOD/loss-limit flatten failed: {strategy_name}",
                                    body=(
                                        f"execute_cycle_intents raised during flatten for "
                                        f"strategy {strategy_name}. Open positions may remain."
                                    ),
                                )
                            except Exception:
                                logger.exception(
                                    "Notifier failed to send flatten failure alert for %s",
                                    strategy_name,
                                )

                # Only mark flatten_complete when no exit hard-failed (broker cancel
                # error or submit_market_exit failure). Exits that resolve as
                # position_already_gone are not failures — the position is flat.
                if (
                    has_flatten_intents
                    and exec_report is not None
                    and exec_report.failed_exit_count == 0
                ):
                    self._save_session_state(
                        DailySessionState(
                            session_date=session_date,
                            trading_mode=self.settings.trading_mode,
                            strategy_version=self.settings.strategy_version,
                            strategy_name=strategy_name,
                            entries_disabled=True,
                            flatten_complete=True,
                            updated_at=timestamp,
                        )
                    )

                # Only free slots for exits that were actually submitted to the broker.
                # Using intent count would optimistically free slots for exits that may
                # have failed, allowing over-allocation above max_open_positions.
                confirmed_exits = (
                    exec_report.submitted_exit_count
                    if exec_report is not None
                    else 0
                )
                global_occupied_slots = max(global_occupied_slots - confirmed_exits, 0)
            except Exception:
                logger.exception(
                    "Strategy cycle failed for %s; skipping to next strategy",
                    strategy_name,
                )
                self._append_audit(
                    AuditEvent(
                        event_type="strategy_cycle_error",
                        payload={
                            "strategy_name": strategy_name,
                            "timestamp": timestamp.isoformat(),
                        },
                        created_at=timestamp,
                    )
                )
                if self._notifier is not None:
                    try:
                        self._notifier.send(
                            subject=f"Strategy cycle error: {strategy_name}",
                            body=(
                                f"_cycle_runner raised for strategy {strategy_name}. "
                                "Open positions may be unprotected — check positions and stops immediately."
                            ),
                        )
                    except Exception:
                        logger.exception(
                            "Notifier failed to send strategy cycle error alert for %s", strategy_name
                        )

        from types import SimpleNamespace as _SN
        cycle_result = all_cycle_results[-1][1] if all_cycle_results else _SN(intents=[])

        dispatch_kwargs = {
            "settings": self.settings,
            "runtime": self.runtime,
            "broker": self.broker,
            "now": timestamp,
            "blocked_strategy_names": entries_disabled_strategies,
            "notifier": self._notifier,
            "session_type": session_type,
        }
        if status is TradingStatusValue.HALTED:
            return SupervisorCycleReport(
                entries_disabled=True,
                cycle_result=cycle_result,
                dispatch_report={"submitted_count": 0},
                account_equity=account.equity,
            )
        if entries_disabled:
            dispatch_kwargs["allowed_intent_types"] = {"stop", "exit"}
        dispatch_report = self._order_dispatcher(**dispatch_kwargs)

        option_broker = getattr(self, "_option_broker", None)
        if option_broker is not None and option_order_store is not None:
            dispatch_pending_option_orders(
                settings=self.settings,
                runtime=self.runtime,
                broker=option_broker,
            )

        # EOD option flatten: create sell records for all open option positions and dispatch.
        if is_past_flatten_time(timestamp, self.settings) and option_order_store is not None:
            open_option_positions = option_order_store.list_open_option_positions(
                trading_mode=self.settings.trading_mode,
                strategy_version=self.settings.strategy_version,
            )
            for pos in open_option_positions:
                sell_id = (
                    f"option:{self.settings.strategy_version}:{timestamp.date().isoformat()}"
                    f":{pos.occ_symbol}:sell:{timestamp.isoformat()}"
                )
                sell_record = OptionOrderRecord(
                    client_order_id=sell_id,
                    occ_symbol=pos.occ_symbol,
                    underlying_symbol=pos.underlying_symbol,
                    option_type=pos.option_type,
                    strike=pos.strike,
                    expiry=pos.expiry,
                    side="sell",
                    status="pending_submit",
                    quantity=pos.filled_quantity or pos.quantity,
                    trading_mode=self.settings.trading_mode,
                    strategy_version=self.settings.strategy_version,
                    strategy_name=pos.strategy_name,
                    created_at=timestamp,
                    updated_at=timestamp,
                )
                option_order_store.save(sell_record, commit=True)
            if option_broker is not None and open_option_positions:
                dispatch_pending_option_orders(
                    settings=self.settings,
                    runtime=self.runtime,
                    broker=option_broker,
                )

        return SupervisorCycleReport(
            entries_disabled=entries_disabled,
            cycle_result=cycle_result,
            dispatch_report=dispatch_report,
            account_equity=account.equity,
        )

    def close(self) -> None:
        if self._closed:
            return
        try:
            self._append_audit(AuditEvent(event_type="supervisor_exited", payload={}))
        except Exception:
            pass  # best-effort: DB may be gone on unclean exit
        if self.stream is not None and hasattr(self.stream, "stop"):
            self.stream.stop()
        if self._stream_thread is not None and self._stream_thread.is_alive():
            self._stream_thread.join(timeout=1.0)
        self._close_runtime(self.runtime)
        self._stream_thread = None
        self._closed = True

    def run_forever(
        self,
        *,
        should_stop: Callable[[], bool] | None = None,
        sleep_fn: Callable[[float], None] | None = None,
        poll_interval_seconds: float = 60.0,
        max_iterations: int | None = None,
        startup_now: Callable[[], datetime] | None = None,
        cycle_now: Callable[[], datetime] | None = None,
    ) -> SupervisorLoopReport:
        iterations = 0
        active_iterations = 0
        idle_iterations = 0
        sleeper = sleep_fn if sleep_fn is not None else time.sleep

        def _request_shutdown(signum: int, frame: object) -> None:
            self._shutdown_requested = True

        signal.signal(signal.SIGTERM, _request_shutdown)
        signal.signal(signal.SIGINT, _request_shutdown)

        try:
            if startup_now is None:
                self.startup()
            else:
                self.startup(now=startup_now)
            self._append_audit(AuditEvent(event_type="supervisor_started", payload={}))
            while True:
                if (should_stop is not None and should_stop()) or self._shutdown_requested:
                    break
                if max_iterations is not None and iterations >= max_iterations:
                    break

                timestamp = _resolve_now(cycle_now)
                try:
                    Path("/tmp/supervisor_heartbeat").write_text(timestamp.isoformat())
                except OSError:
                    pass
                session_date = _session_date(timestamp, self.settings)
                session_type = self._current_session(timestamp)
                if session_type is not SessionType.CLOSED:
                    self._session_had_active_cycle.add(session_date)
                    try:
                        cycle_report = self.run_cycle_once(now=lambda: timestamp, session_type=session_type)
                        self._consecutive_cycle_failures = 0
                    except LockAcquisitionError:
                        logger.critical(
                            "Advisory lock lost after reconnect — halting supervisor to prevent "
                            "concurrent instance from running without exclusive DB access"
                        )
                        raise SystemExit(1)
                    except Exception as exc:
                        self._consecutive_cycle_failures += 1
                        log_fn = logger.error if self._consecutive_cycle_failures >= 5 else logger.warning
                        log_fn("Supervisor cycle error: %s", exc, exc_info=True)
                        self._append_audit(
                            AuditEvent(
                                event_type="supervisor_cycle_error",
                                payload={
                                    "error": str(exc),
                                    "timestamp": timestamp.isoformat(),
                                    "consecutive_failures": self._consecutive_cycle_failures,
                                },
                                created_at=timestamp,
                            )
                        )
                        if self._consecutive_cycle_failures >= 5 and self._notifier is not None:
                            try:
                                self._notifier.send(
                                    subject="Supervisor cycle failing repeatedly",
                                    body=(
                                        f"{self._consecutive_cycle_failures} consecutive cycle failures. "
                                        f"Last error: {exc}"
                                    ),
                                )
                            except Exception:
                                logger.exception("Notifier failed to send cycle failure alert")
                        if self._consecutive_cycle_failures >= 10:
                            logger.critical(
                                "Supervisor: %d consecutive cycle failures — exiting for Docker restart",
                                self._consecutive_cycle_failures,
                            )
                            raise SystemExit(1)
                        iterations += 1
                        sleeper(poll_interval_seconds)
                        continue
                    # Stream thread watchdog — restart dead stream thread with
                    # exponential backoff (cap 5 min) and alert after 5 failures.
                    _stream_thread = self._stream_thread
                    if _stream_thread is not None and _stream_thread.is_alive():
                        # Stream recovered — reset failure counter so future turbulence
                        # doesn't permanently fire at the >=5 alert threshold.
                        self._stream_restart_attempts = 0
                        self._next_stream_restart_at = None
                    elif (
                        _stream_thread is not None
                        and not _stream_thread.is_alive()
                    ):
                        self._stream_thread = None
                        # Respect backoff window before attempting restart
                        if (
                            self._next_stream_restart_at is None
                            or timestamp >= self._next_stream_restart_at
                        ):
                            self._stream_restart_attempts += 1
                            backoff_seconds = min(
                                60 * (2 ** (self._stream_restart_attempts - 1)),
                                300,  # cap at 5 minutes
                            )
                            self._next_stream_restart_at = timestamp + timedelta(
                                seconds=backoff_seconds
                            )
                            self._start_stream_thread(now=lambda: timestamp)
                            self._append_audit(
                                AuditEvent(
                                    event_type="trade_update_stream_restarted",
                                    payload={
                                        "timestamp": timestamp.isoformat(),
                                        "attempt": self._stream_restart_attempts,
                                    },
                                    created_at=timestamp,
                                )
                            )
                            if self._stream_restart_attempts >= 5:
                                self._append_audit(
                                    AuditEvent(
                                        event_type="stream_restart_failed",
                                        payload={
                                            "attempt_count": self._stream_restart_attempts,
                                            "timestamp": timestamp.isoformat(),
                                        },
                                        created_at=timestamp,
                                    )
                                )
                                if self._notifier is not None:
                                    try:
                                        self._notifier.send(
                                            subject="Trade stream restart failed",
                                            body=(
                                                f"Stream has failed to restart after "
                                                f"{self._stream_restart_attempts} attempts. "
                                                f"Fill events may be missed."
                                            ),
                                        )
                                    except Exception:
                                        logger.exception("Notifier failed to send stream restart alert")
                    # Heartbeat staleness guard — catches silent clean-close disconnects
                    # that leave the thread alive but no longer receiving events.
                    _stream_thread_alive = (
                        self._stream_thread is not None and self._stream_thread.is_alive()
                    )
                    if (
                        _stream_thread_alive
                        and self._last_stream_event_at is not None
                        and (timestamp - self._last_stream_event_at)
                        > timedelta(seconds=STREAM_HEARTBEAT_TIMEOUT_SECONDS)
                    ):
                        if not self._stream_heartbeat_alerted:
                            self._stream_heartbeat_alerted = True
                            logger.critical(
                                "Trade update stream heartbeat stale: no event in %ds",
                                STREAM_HEARTBEAT_TIMEOUT_SECONDS,
                            )
                            self._append_audit(
                                AuditEvent(
                                    event_type="stream_heartbeat_stale",
                                    payload={
                                        "last_event_at": self._last_stream_event_at.isoformat(),
                                        "timestamp": timestamp.isoformat(),
                                        "timeout_seconds": STREAM_HEARTBEAT_TIMEOUT_SECONDS,
                                    },
                                    created_at=timestamp,
                                )
                            )
                            if self._notifier is not None:
                                try:
                                    self._notifier.send(
                                        subject="Trade stream heartbeat stale",
                                        body=(
                                            f"No trade update event received in "
                                            f"{STREAM_HEARTBEAT_TIMEOUT_SECONDS}s. "
                                            f"Fill events may be missed."
                                        ),
                                    )
                                except Exception:
                                    logger.exception("Notifier failed to send heartbeat stale alert")
                    else:
                        self._stream_heartbeat_alerted = False

                    active_iterations += 1
                    self._append_audit(
                        AuditEvent(
                            event_type="supervisor_cycle",
                            payload={
                                "entries_disabled": cycle_report.entries_disabled,
                                "timestamp": timestamp.isoformat(),
                                "account_equity": cycle_report.account_equity,
                            },
                            created_at=timestamp,
                        )
                    )
                else:
                    idle_iterations += 1
                    if (
                        session_date not in self._summary_sent
                        and session_date in self._session_had_active_cycle
                    ):
                        self._send_daily_summary(
                            session_date=session_date, timestamp=timestamp
                        )
                        self._summary_sent.add(session_date)
                    self._append_audit(
                        AuditEvent(
                            event_type="supervisor_idle",
                            payload={
                                "reason": "market_closed",
                                "timestamp": timestamp.isoformat(),
                            },
                            created_at=timestamp,
                        )
                    )
                iterations += 1

                if (should_stop is not None and should_stop()) or self._shutdown_requested:
                    break
                if max_iterations is not None and iterations >= max_iterations:
                    break
                sleeper(poll_interval_seconds)
        finally:
            self.close()

        return SupervisorLoopReport(
            iterations=iterations,
            active_iterations=active_iterations,
            idle_iterations=idle_iterations,
        )

    def _load_open_positions(self) -> list[OpenPosition]:
        return [
            OpenPosition(
                symbol=position.symbol,
                entry_timestamp=position.opened_at,
                entry_price=position.entry_price,
                quantity=position.quantity,
                entry_level=position.initial_stop_price,
                initial_stop_price=position.initial_stop_price,
                stop_price=position.stop_price,
                trailing_active=position.stop_price > position.initial_stop_price,
                highest_price=position.entry_price,
                strategy_name=getattr(position, "strategy_name", "breakout"),
            )
            for position in self._load_position_records()
        ]

    def _load_position_records(self) -> list[PositionRecord]:
        if self.runtime.position_store is None or not hasattr(self.runtime.position_store, "list_all"):
            return []
        store_lock = getattr(self.runtime, "store_lock", None)
        if store_lock is not None:
            with store_lock:
                return self.runtime.position_store.list_all(
                    trading_mode=self.settings.trading_mode,
                    strategy_version=self.settings.strategy_version,
                )
        return self.runtime.position_store.list_all(
            trading_mode=self.settings.trading_mode,
            strategy_version=self.settings.strategy_version,
        )

    def _resolve_active_strategies(self) -> list[tuple[str, StrategySignalEvaluator]]:
        """Return (strategy_name, evaluator) for every enabled strategy."""
        store = getattr(self.runtime, "strategy_flag_store", None)
        store_lock = getattr(self.runtime, "store_lock", None)
        active = []
        with store_lock if store_lock is not None else contextlib.nullcontext():
            for name, evaluator in STRATEGY_REGISTRY.items():
                if store is not None:
                    flag = store.load(
                        strategy_name=name,
                        trading_mode=self.settings.trading_mode,
                        strategy_version=self.settings.strategy_version,
                    )
                    if flag is not None and not flag.enabled:
                        continue
                active.append((name, evaluator))
        return active

    def _working_symbols_for_strategy(
        self,
        *,
        strategy_name: str,
        broker_open_orders: list,
    ) -> set[str]:
        symbols: set[str] = set()
        if hasattr(self.runtime.order_store, "list_by_status"):
            store_lock = getattr(self.runtime, "store_lock", None)
            with store_lock if store_lock is not None else contextlib.nullcontext():
                pending = self.runtime.order_store.list_by_status(
                    trading_mode=self.settings.trading_mode,
                    strategy_version=self.settings.strategy_version,
                    statuses=["pending_submit"],
                    strategy_name=strategy_name,
                )
            for order in pending:
                symbols.add(order.symbol)
        for order in broker_open_orders:
            cid = getattr(order, "client_order_id", "") or ""
            first_segment = cid.split(":")[0] if cid else ""
            # Orders whose prefix isn't a known strategy are attributed to "breakout"
            inferred = first_segment if first_segment in STRATEGY_REGISTRY else "breakout"
            if inferred == strategy_name:
                symbols.add(getattr(order, "symbol", ""))
        return symbols

    def _load_session_state(
        self,
        *,
        session_date: date,
        strategy_name: str = GLOBAL_SESSION_STATE_STRATEGY_NAME,
    ) -> DailySessionState | None:
        if self.runtime.daily_session_state_store is None or not hasattr(
            self.runtime.daily_session_state_store, "load"
        ):
            return None
        store_lock = getattr(self.runtime, "store_lock", None)
        with store_lock if store_lock is not None else contextlib.nullcontext():
            return self.runtime.daily_session_state_store.load(
                session_date=session_date,
                trading_mode=self.settings.trading_mode,
                strategy_version=self.settings.strategy_version,
                strategy_name=strategy_name,
            )

    def _save_session_state(self, state: DailySessionState) -> None:
        if self.runtime.daily_session_state_store is None or not hasattr(
            self.runtime.daily_session_state_store, "save"
        ):
            return
        store_lock = getattr(self.runtime, "store_lock", None)
        with store_lock if store_lock is not None else contextlib.nullcontext():
            try:
                self.runtime.daily_session_state_store.save(state)
            except Exception:
                try:
                    self.runtime.connection.rollback()
                except Exception:
                    pass
                raise

    def _update_session_weights(self, session_date: date) -> dict[str, float]:
        weight_store = getattr(self.runtime, "strategy_weight_store", None)
        if weight_store is None:
            active_names = [name for name, _ in self._resolve_active_strategies()]
            n = max(len(active_names), 1)
            return {name: 1.0 / n for name in active_names}

        store_lock = getattr(self.runtime, "store_lock", None)
        lock_ctx = store_lock if store_lock is not None else contextlib.nullcontext()

        with lock_ctx:
            existing = weight_store.load_all(
                trading_mode=self.settings.trading_mode,
                strategy_version=self.settings.strategy_version,
            )
        if existing and all(w.computed_at.date() == session_date for w in existing):
            return {w.strategy_name: w.weight for w in existing}

        end_date = session_date - timedelta(days=1)
        start_date = date(2000, 1, 1)
        active_names = [name for name, _ in self._resolve_active_strategies()]

        with lock_ctx:
            trade_rows = self.runtime.order_store.list_trade_pnl_by_strategy(
                trading_mode=self.settings.trading_mode,
                strategy_version=self.settings.strategy_version,
                start_date=start_date,
                end_date=end_date,
            )

        result = compute_strategy_weights(trade_rows, active_names)
        now = datetime.now(timezone.utc)

        with lock_ctx:
            weight_store.upsert_many(
                weights=result.weights,
                sharpes=result.sharpes,
                trading_mode=self.settings.trading_mode,
                strategy_version=self.settings.strategy_version,
                computed_at=now,
            )

        self._append_audit(
            AuditEvent(
                event_type="strategy_weights_updated",
                payload={name: round(w, 6) for name, w in result.weights.items()},
                created_at=now,
            )
        )
        return result.weights

    def _append_audit(self, event: AuditEvent) -> None:
        """Append an AuditEvent while holding store_lock to prevent races with the stream thread."""
        store_lock = getattr(self.runtime, "store_lock", None)
        with store_lock if store_lock is not None else contextlib.nullcontext():
            try:
                self.runtime.audit_event_store.append(event)
            except Exception:
                logger.exception(
                    "Failed to append audit event %s; rolling back and continuing",
                    event.event_type,
                )
                try:
                    self.runtime.connection.rollback()
                except Exception:
                    pass

    def _effective_trading_status(
        self,
        *,
        session_date: date,
        session_state: DailySessionState | None = None,
        session_type: SessionType | None = None,
    ) -> TradingStatusValue | None:
        status = self._load_trading_status()
        if status in {TradingStatusValue.CLOSE_ONLY, TradingStatusValue.HALTED}:
            return status
        # The session-state entries_disabled flag is set at end-of-regular-session flatten
        # time. During extended-hours sessions (pre-market, after-hours) we start fresh —
        # the flatten gate must not carry over and block the new session's entries.
        is_extended = session_type in {SessionType.PRE_MARKET, SessionType.AFTER_HOURS}
        if session_state is not None and session_state.entries_disabled and not is_extended:
            return TradingStatusValue.CLOSE_ONLY
        return status

    def _load_trading_status(self) -> TradingStatusValue | None:
        if not hasattr(self.runtime.trading_status_store, "load"):
            return None
        store_lock = getattr(self.runtime, "store_lock", None)
        with store_lock if store_lock is not None else contextlib.nullcontext():
            status = self.runtime.trading_status_store.load(
                trading_mode=self.settings.trading_mode,
                strategy_version=self.settings.strategy_version,
            )
        return None if status is None else status.status

    def _list_pending_submit_orders(self) -> list[object]:
        if not hasattr(self.runtime.order_store, "list_pending_submit"):
            return []
        store_lock = getattr(self.runtime, "store_lock", None)
        with store_lock if store_lock is not None else contextlib.nullcontext():
            return self.runtime.order_store.list_pending_submit(
                trading_mode=self.settings.trading_mode,
                strategy_version=self.settings.strategy_version,
            )

    def _maybe_fire_consecutive_loss_gate(
        self,
        *,
        session_date: date,
        consecutive_losses: int,
        timestamp: datetime,
    ) -> None:
        """Fire the consecutive-loss entry gate if threshold is met and not yet fired today."""
        gate = self.settings.intraday_consecutive_loss_gate
        if gate == 0:
            return
        if session_date in self._consecutive_loss_gate_fired:
            return
        if consecutive_losses < gate:
            return
        self._consecutive_loss_gate_fired.add(session_date)
        baseline_equity = self._session_equity_baseline.get(session_date, 0.0)
        try:
            self._save_session_state(
                DailySessionState(
                    session_date=session_date,
                    trading_mode=self.settings.trading_mode,
                    strategy_version=self.settings.strategy_version,
                    strategy_name=EQUITY_SESSION_STATE_STRATEGY_NAME,
                    entries_disabled=True,
                    flatten_complete=False,
                    equity_baseline=baseline_equity,
                    updated_at=timestamp,
                )
            )
        except Exception:
            # Deliberate: in-process _consecutive_loss_gate_fired set already holds the guard
            # for this process lifetime. On restart the gate re-arms from the trade list —
            # acceptable because the operator was notified and must manually resume.
            logger.exception("Failed to save session state after consecutive-loss gate fired")
        self._append_audit(
            AuditEvent(
                event_type="intraday_consecutive_loss_gate",
                payload={
                    "consecutive_losses": consecutive_losses,
                    "threshold": gate,
                    "timestamp": timestamp.isoformat(),
                },
                created_at=timestamp,
            )
        )
        if self._notifier is not None:
            try:
                self._notifier.send(
                    subject="Entries disabled — consecutive losses",
                    body=(
                        f"Entries disabled — {consecutive_losses} consecutive losses this session. "
                        "Resume manually to re-enable."
                    ),
                )
            except Exception:
                logger.exception("Notifier failed to send consecutive-loss gate alert")

    def _maybe_send_intraday_digest(
        self,
        *,
        session_date: date,
        closed_trades: list[dict],
        baseline_equity: float,
        current_equity: float,
        timestamp: datetime,
    ) -> None:
        """Send intra-day performance digest at configured cycle intervals."""
        if self._notifier is None:
            return
        interval = self.settings.intraday_digest_interval_cycles
        if interval == 0:
            return
        cycle_num = self._session_cycle_count.get(session_date, 0)
        if cycle_num == 0 or cycle_num % interval != 0:
            return
        if not closed_trades:
            return
        from alpaca_bot.runtime.daily_summary import build_intraday_digest

        store_lock = getattr(self.runtime, "store_lock", None)
        with store_lock if store_lock is not None else contextlib.nullcontext():
            open_positions = self.runtime.position_store.list_all(
                trading_mode=self.settings.trading_mode,
                strategy_version=self.settings.strategy_version,
            )
        try:
            subject, body = build_intraday_digest(
                settings=self.settings,
                trades=closed_trades,
                open_positions=open_positions,
                baseline_equity=baseline_equity,
                current_equity=current_equity,
                cycle_num=cycle_num,
                timestamp=timestamp,
                session_date=session_date,
            )
            self._notifier.send(subject, body)
        except Exception:
            logger.exception("Notifier failed to send intraday digest for %s", session_date)
            return
        digest_num = cycle_num // interval
        self._digest_sent_count[session_date] = digest_num
        self._append_audit(
            AuditEvent(
                event_type="intraday_digest_sent",
                payload={
                    "cycle": cycle_num,
                    "digest_num": digest_num,
                    "timestamp": timestamp.isoformat(),
                },
                created_at=timestamp,
            )
        )

    def _send_daily_summary(self, *, session_date: date, timestamp: datetime) -> None:
        """Send end-of-session summary notification. Failures are logged, never raised."""
        if self._notifier is None:
            return
        from alpaca_bot.runtime.daily_summary import build_daily_summary

        store_lock = getattr(self.runtime, "store_lock", None)
        with store_lock if store_lock is not None else contextlib.nullcontext():
            subject, body = build_daily_summary(
                settings=self.settings,
                order_store=self.runtime.order_store,
                position_store=self.runtime.position_store,
                session_date=session_date,
                daily_loss_limit_breached=session_date in self._loss_limit_alerted,
            )
        try:
            self._notifier.send(subject, body)
        except Exception:
            logger.exception("Notifier failed to send daily summary for %s", session_date)
            return
        self._append_audit(
            AuditEvent(
                event_type="daily_summary_sent",
                payload={
                    "session_date": session_date.isoformat(),
                    "timestamp": timestamp.isoformat(),
                },
                created_at=timestamp,
            )
        )

    def _load_traded_symbols(
        self,
        *,
        session_date: date,
        strategy_name: str = "breakout",
    ) -> set[tuple[str, date]]:
        if not hasattr(self.runtime.order_store, "list_by_status"):
            return set()
        store_lock = getattr(self.runtime, "store_lock", None)
        with store_lock if store_lock is not None else contextlib.nullcontext():
            orders = self.runtime.order_store.list_by_status(
                trading_mode=self.settings.trading_mode,
                strategy_version=self.settings.strategy_version,
                statuses=["filled", "partially_filled"],
                strategy_name=strategy_name,
            )
        traded_symbols: set[tuple[str, date]] = set()
        for order in orders:
            if getattr(order, "intent_type", None) != "entry":
                continue
            signal_timestamp = getattr(order, "signal_timestamp", None)
            if signal_timestamp is None:
                continue
            if _session_date(signal_timestamp, self.settings) == session_date:
                traded_symbols.add((order.symbol, session_date))
        return traded_symbols

    def _market_is_open(self) -> bool:
        if hasattr(self.broker, "get_clock"):
            return bool(self.broker.get_clock().is_open)
        if hasattr(self.broker, "get_market_clock"):
            return bool(self.broker.get_market_clock().is_open)
        raise RuntimeError(
            "Broker has no clock method (get_clock or get_market_clock); "
            "cannot determine market hours — refusing to proceed"
        )

    def _current_session(self, timestamp: datetime) -> SessionType:
        session = detect_session_type(timestamp, self.settings)
        if session is SessionType.REGULAR:
            try:
                clock = (
                    self.broker.get_clock()
                    if hasattr(self.broker, "get_clock")
                    else self.broker.get_market_clock()
                )
                return SessionType.REGULAR if clock.is_open else SessionType.CLOSED
            except Exception:
                return SessionType.REGULAR
        if session in (SessionType.PRE_MARKET, SessionType.AFTER_HOURS):
            return session if self.settings.extended_hours_enabled else SessionType.CLOSED
        return SessionType.CLOSED

    def _start_stream_thread(self, *, now: Callable[[], datetime]) -> None:
        if self.stream is None or self._stream_thread is not None:
            return

        def runner() -> None:
            _stream_lock = getattr(self.runtime, "store_lock", None)
            _stream_lock_ctx = _stream_lock if _stream_lock is not None else contextlib.nullcontext()
            timestamp = _resolve_now(now)
            with _stream_lock_ctx:
                try:
                    self.runtime.audit_event_store.append(
                        AuditEvent(
                            event_type="trade_update_stream_started",
                            payload={"timestamp": timestamp.isoformat()},
                            created_at=timestamp,
                        )
                    )
                except Exception:
                    logger.exception("Failed to append trade_update_stream_started; continuing")
                    try:
                        self.runtime.connection.rollback()
                    except Exception:
                        pass
            try:
                if not hasattr(self.stream, "run"):
                    raise RuntimeError("Configured trade update stream does not expose run()")
                self.stream.run()
            except Exception as exc:
                failure_at = _resolve_now(now)
                with _stream_lock_ctx:
                    try:
                        self.runtime.audit_event_store.append(
                            AuditEvent(
                                event_type="trade_update_stream_failed",
                                payload={"error": str(exc)},
                                created_at=failure_at,
                            )
                        )
                    except Exception:
                        # Log the original stream exception here since the audit append failed
                        # and it would otherwise be silently discarded.
                        logger.exception(
                            "Trade update stream exited with error: %s; "
                            "also failed to append audit event",
                            exc,
                        )
                        try:
                            self.runtime.connection.rollback()
                        except Exception:
                            pass
            else:
                stopped_at = _resolve_now(now)
                with _stream_lock_ctx:
                    try:
                        self.runtime.audit_event_store.append(
                            AuditEvent(
                                event_type="trade_update_stream_stopped",
                                payload={"reason": "stream_exited"},
                                created_at=stopped_at,
                            )
                        )
                    except Exception:
                        logger.exception("Failed to append trade_update_stream_stopped; continuing")
                        try:
                            self.runtime.connection.rollback()
                        except Exception:
                            pass
            finally:
                self._stream_thread = None

        self._stream_thread = threading.Thread(
            target=runner,
            name="alpaca-bot-trade-updates",
            daemon=True,
        )
        self._stream_thread.start()

    def _record_stream_event(self) -> None:
        self._last_stream_event_at = datetime.now(timezone.utc)


TraderSupervisor = RuntimeSupervisor


def _resolve_now(now: Callable[[], datetime] | None) -> datetime:
    if callable(now):
        return now()
    return datetime.now(timezone.utc)


def _session_date(timestamp: datetime, settings: Settings) -> date:
    return timestamp.astimezone(settings.market_timezone).date()

