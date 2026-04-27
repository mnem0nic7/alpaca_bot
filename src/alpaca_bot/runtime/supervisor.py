from __future__ import annotations

import contextlib
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import logging
import threading
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
    RuntimeContext,
    bootstrap_runtime,
    close_runtime,
    reconnect_runtime_connection,
)
from alpaca_bot.storage.db import check_connection
from alpaca_bot.runtime.cli import _list_open_orders, _list_open_positions
from alpaca_bot.core.engine import CycleIntentType
from alpaca_bot.runtime.cycle import run_cycle
from alpaca_bot.runtime.cycle_intent_execution import execute_cycle_intents
from alpaca_bot.runtime.order_dispatch import dispatch_pending_orders
from alpaca_bot.runtime.startup_recovery import (
    compose_startup_mismatch_detector,
    recover_startup_state,
)
from alpaca_bot.runtime.trade_update_stream import attach_trade_update_stream
from alpaca_bot.runtime.trader import TraderStartupReport, start_trader
from alpaca_bot.storage import AuditEvent, DailySessionState, PositionRecord, TradingStatusValue
from alpaca_bot.strategy import STRATEGY_REGISTRY, StrategySignalEvaluator
from alpaca_bot.strategy.breakout import evaluate_breakout_signal as _default_evaluator


@dataclass(frozen=True)
class SupervisorCycleReport:
    entries_disabled: bool
    cycle_result: object
    dispatch_report: object


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
        self._stream_attached = False
        self._stream_thread: threading.Thread | None = None
        self._closed = False
        self._stream_restart_attempts: int = 0
        self._next_stream_restart_at: datetime | None = None
        self._consecutive_cycle_failures: int = 0
        # Keyed by session_date (ET); populated on the first cycle of each day.
        self._session_equity_baseline: dict[date, float] = {}
        # Dates for which the daily loss limit alert has already been sent (once per day).
        self._loss_limit_alerted: set[date] = set()

    @classmethod
    def from_settings(cls, settings: Settings) -> "RuntimeSupervisor":
        return cls(
            settings=settings,
            runtime=bootstrap_runtime(settings),
            broker=AlpacaBroker.from_settings(settings),
            market_data=AlpacaMarketDataAdapter.from_settings(settings),
            stream=AlpacaTradingStreamAdapter.from_settings(settings),
            notifier=build_notifier(settings),
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
            )
            self._stream_attached = True
            self._start_stream_thread(now=lambda: timestamp)
        return report

    def run_cycle_once(
        self,
        *,
        now: Callable[[], datetime] | None = None,
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
        with _rec_lock_ctx:
            recovery_report = recover_startup_state(
                settings=self.settings,
                runtime=self.runtime,
                broker_open_positions=broker_open_positions,
                broker_open_orders=broker_open_orders,
                now=timestamp,
                audit_event_type=None,
            )
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
        account = self.broker.get_account()
        session_date = _session_date(timestamp, self.settings)

        # Load session state once and apply staleness check (Task 7).
        session_state = self._load_session_state(session_date=session_date)
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
            persisted = self._load_session_state(session_date=session_date, strategy_name="_equity")
            if persisted is not None and persisted.equity_baseline is not None:
                self._session_equity_baseline[session_date] = persisted.equity_baseline
            else:
                self._session_equity_baseline[session_date] = account.equity
                self._save_session_state(
                    DailySessionState(
                        session_date=session_date,
                        trading_mode=self.settings.trading_mode,
                        strategy_version=self.settings.strategy_version,
                        strategy_name="_equity",
                        entries_disabled=False,
                        flatten_complete=False,
                        equity_baseline=account.equity,
                        updated_at=timestamp,
                    )
                )
        baseline_equity = self._session_equity_baseline[session_date]
        loss_limit = self.settings.daily_loss_limit_pct * baseline_equity
        daily_loss_limit_breached = realized_pnl < -loss_limit
        if daily_loss_limit_breached and session_date not in self._loss_limit_alerted:
            self._loss_limit_alerted.add(session_date)
            self._append_audit(
                AuditEvent(
                    event_type="daily_loss_limit_breached",
                    payload={
                        "realized_pnl": realized_pnl,
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
                            f"Realized PnL {realized_pnl:.2f} exceeded limit "
                            f"{-loss_limit:.2f}. Entries disabled for the session."
                        ),
                    )
                except Exception:
                    logger.exception("Notifier failed to send daily loss limit alert")

        status = self._effective_trading_status(
            session_date=session_date, session_state=session_state
        )
        entries_disabled = (
            status in {TradingStatusValue.CLOSE_ONLY, TradingStatusValue.HALTED}
            or bool(recovery_report.mismatches)
            or daily_loss_limit_breached
        )
        open_positions = self._load_open_positions()
        working_order_symbols = {order.symbol for order in broker_open_orders}
        working_order_symbols.update(order.symbol for order in self._list_pending_submit_orders())
        intraday_bars_by_symbol = self.market_data.get_stock_bars(
            symbols=list(self.settings.symbols),
            start=timestamp - timedelta(days=5),
            end=timestamp,
            timeframe_minutes=self.settings.entry_timeframe_minutes,
        )
        daily_bars_end = datetime.combine(session_date, datetime.min.time()).replace(
            tzinfo=self.settings.market_timezone
        )
        daily_bars_by_symbol = self.market_data.get_daily_bars(
            symbols=list(self.settings.symbols),
            start=timestamp - timedelta(days=max(
                self.settings.daily_sma_period * 3,
                60,
                self.settings.high_watermark_lookback_days + 10,
            )),
            end=daily_bars_end,
        )
        active_strategies = self._resolve_active_strategies()
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

                strategy_entries_disabled = (
                    entries_disabled
                    or (strategy_session_state is not None and strategy_session_state.entries_disabled)
                )
                if strategy_entries_disabled:
                    entries_disabled_strategies.add(strategy_name)

                cycle_result = self._cycle_runner(
                    settings=self.settings,
                    runtime=self.runtime,
                    now=timestamp,
                    equity=account.equity,
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
                if has_flatten_intents:
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

                exec_report = None
                if status is not TradingStatusValue.HALTED:
                    try:
                        exec_report = self._cycle_intent_executor(
                            settings=self.settings,
                            runtime=self.runtime,
                            broker=self.broker,
                            cycle_result=cycle_result,
                            now=timestamp,
                        )
                    except Exception:
                        logger.exception(
                            "execute_cycle_intents failed for strategy %s; continuing to dispatch",
                            strategy_name,
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

        from types import SimpleNamespace as _SN
        cycle_result = all_cycle_results[-1][1] if all_cycle_results else _SN(intents=[])

        dispatch_kwargs = {
            "settings": self.settings,
            "runtime": self.runtime,
            "broker": self.broker,
            "now": timestamp,
            "blocked_strategy_names": entries_disabled_strategies,
            "notifier": self._notifier,
        }
        if status is TradingStatusValue.HALTED:
            return SupervisorCycleReport(
                entries_disabled=True,
                cycle_result=cycle_result,
                dispatch_report={"submitted_count": 0},
            )
        if entries_disabled:
            dispatch_kwargs["allowed_intent_types"] = {"stop", "exit"}
        dispatch_report = self._order_dispatcher(**dispatch_kwargs)
        return SupervisorCycleReport(
            entries_disabled=entries_disabled,
            cycle_result=cycle_result,
            dispatch_report=dispatch_report,
        )

    def close(self) -> None:
        if self._closed:
            return
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
        sleeper = sleep_fn or (lambda _seconds: None)

        try:
            if startup_now is None:
                self.startup()
            else:
                self.startup(now=startup_now)
            while True:
                if should_stop is not None and should_stop():
                    break
                if max_iterations is not None and iterations >= max_iterations:
                    break

                timestamp = _resolve_now(cycle_now)
                if self._market_is_open():
                    try:
                        cycle_report = self.run_cycle_once(now=lambda: timestamp)
                        self._consecutive_cycle_failures = 0
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
                        iterations += 1
                        sleeper(poll_interval_seconds)
                        continue
                    # Stream thread watchdog — restart dead stream thread with
                    # exponential backoff (cap 5 min) and alert after 5 failures.
                    _stream_thread = self._stream_thread
                    if (
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
                    active_iterations += 1
                    self._append_audit(
                        AuditEvent(
                            event_type="supervisor_cycle",
                            payload={
                                "entries_disabled": cycle_report.entries_disabled,
                                "timestamp": timestamp.isoformat(),
                            },
                            created_at=timestamp,
                        )
                    )
                else:
                    idle_iterations += 1
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

                if should_stop is not None and should_stop():
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
        strategy_name: str = "breakout",
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
    ) -> TradingStatusValue | None:
        status = self._load_trading_status()
        if status in {TradingStatusValue.CLOSE_ONLY, TradingStatusValue.HALTED}:
            return status
        if session_state is not None and session_state.entries_disabled:
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
                        logger.exception("Failed to append trade_update_stream_failed; continuing")
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


TraderSupervisor = RuntimeSupervisor


def _resolve_now(now: Callable[[], datetime] | None) -> datetime:
    if callable(now):
        return now()
    return datetime.now(timezone.utc)


def _session_date(timestamp: datetime, settings: Settings) -> date:
    return timestamp.astimezone(settings.market_timezone).date()


