from __future__ import annotations

from datetime import datetime, timezone
import json
import sys
from typing import Callable, Sequence, TextIO

from alpaca_bot.config import Settings
from alpaca_bot.execution import AlpacaExecutionAdapter, BrokerPosition
from alpaca_bot.runtime.bootstrap import RuntimeContext, bootstrap_runtime
from alpaca_bot.runtime.startup_recovery import (
    compose_startup_mismatch_detector,
    recover_startup_state,
)
from alpaca_bot.runtime.trader import TraderStartupStatus, start_trader
from alpaca_bot.storage import AuditEvent


def main(
    argv: Sequence[str] | None = None,
    *,
    settings: Settings | None = None,
    bootstrap: Callable[[Settings], RuntimeContext] = bootstrap_runtime,
    broker_factory: Callable[[Settings], object] | None = None,
    now: Callable[[], datetime] | None = None,
    stdout: TextIO | None = None,
) -> int:
    del argv
    resolved_settings = settings or Settings.from_env()
    runtime = bootstrap(resolved_settings)
    broker = (
        broker_factory(resolved_settings)
        if broker_factory is not None
        else AlpacaExecutionAdapter.from_settings(resolved_settings)
    )
    timestamp = (now or (lambda: datetime.now(timezone.utc)))()
    open_orders = list(_list_open_orders(broker))
    open_positions = list(_list_open_positions(broker))
    recovery_report = recover_startup_state(
        settings=resolved_settings,
        runtime=runtime,
        broker_open_positions=open_positions,
        broker_open_orders=open_orders,
        now=timestamp,
    )
    report = start_trader(
        resolved_settings,
        broker_client=broker,
        bootstrap=lambda _: runtime,
        mismatch_detector=compose_startup_mismatch_detector(
            recovery_report=recovery_report,
            extra_detector=lambda runtime_context, session: _detect_startup_mismatches(
                runtime_context,
                session_date=session.session_date,
                open_positions=open_positions,
            ),
        ),
        now=lambda: timestamp,
    )

    effective_status = _effective_status(report.status, report.reconciliation.mismatch_detected)
    summary = {
        "effective_status": effective_status,
        "open_order_count": len(open_orders),
        "open_position_count": len(open_positions),
        "mismatch_detected": report.reconciliation.mismatch_detected,
        "session_date": report.session.session_date.isoformat(),
    }
    runtime.audit_event_store.append(
        AuditEvent(
            event_type="trader_startup_completed",
            payload={
                "trading_mode": resolved_settings.trading_mode.value,
                "strategy_version": resolved_settings.strategy_version,
                **summary,
            },
            created_at=timestamp,
        )
    )

    output = stdout or sys.stdout
    output.write(json.dumps(summary))
    return 0


def _list_open_orders(broker: object) -> Sequence[object]:
    if hasattr(broker, "list_open_orders"):
        return broker.list_open_orders()
    return ()


def _list_open_positions(broker: object) -> Sequence[BrokerPosition]:
    if hasattr(broker, "list_open_positions"):
        return broker.list_open_positions()
    if hasattr(broker, "list_positions"):
        return broker.list_positions()
    return ()


def _detect_startup_mismatches(
    runtime: RuntimeContext,
    *,
    session_date,
    open_positions: Sequence[BrokerPosition],
) -> list[str]:
    if runtime.daily_session_state_store is None:
        return []
    previous_state = runtime.daily_session_state_store.load(
        session_date=session_date,
        trading_mode=runtime.settings.trading_mode,
        strategy_version=runtime.settings.strategy_version,
    )
    if previous_state is not None and previous_state.flatten_complete and open_positions:
        return ["broker position mismatch"]
    return []


def _effective_status(
    startup_status: TraderStartupStatus,
    mismatch_detected: bool,
) -> str:
    if mismatch_detected:
        return "halted"
    if startup_status is TraderStartupStatus.HALTED:
        return "halted"
    if startup_status is TraderStartupStatus.CLOSE_ONLY:
        return "close_only"
    return "enabled"
