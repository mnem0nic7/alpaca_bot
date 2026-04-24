from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from typing import Callable, Protocol

from alpaca_bot.config import Settings
from alpaca_bot.runtime.bootstrap import RuntimeContext, bootstrap_runtime
from alpaca_bot.runtime.reconcile import (
    ReconciliationOutcome,
    SessionSnapshot,
    reconcile_startup,
    resolve_current_session,
)
from alpaca_bot.storage import TradingStatusValue


class BrokerProtocol(Protocol):
    def get_clock(self): ...

    def get_calendar(self, *, start, end): ...


class TraderStartupStatus(StrEnum):
    READY = "ready"
    HALTED = "halted"
    CLOSE_ONLY = "close_only"


@dataclass(frozen=True)
class TraderStartupReport:
    status: TraderStartupStatus
    session: SessionSnapshot
    reconciliation: ReconciliationOutcome


def start_trader(
    settings: Settings,
    *,
    broker_client: BrokerProtocol,
    bootstrap: Callable[..., RuntimeContext] = bootstrap_runtime,
    mismatch_detector: Callable[[RuntimeContext, SessionSnapshot], tuple[str, ...] | list[str]]
    | None = None,
    now: Callable[[], datetime] | None = None,
) -> TraderStartupReport:
    runtime = bootstrap(settings)
    clock = _get_clock(broker_client)
    calendar = broker_client.get_calendar(
        start=clock.timestamp.date(),
        end=clock.next_close.date(),
    )
    session = resolve_current_session(clock=clock, calendar=calendar)
    current_time = now or (lambda: datetime.now(timezone.utc))
    stored_status = runtime.trading_status_store.load(
        trading_mode=settings.trading_mode,
        strategy_version=settings.strategy_version,
    )
    entries_disabled = stored_status is not None and (
        stored_status.status is not TradingStatusValue.ENABLED
    )
    reconciliation = reconcile_startup(
        context=runtime,
        session=session,
        entries_disabled=entries_disabled,
        mismatch_detector=mismatch_detector,
        now=current_time,
    )
    return TraderStartupReport(
        status=_resolve_startup_status(stored_status, reconciliation),
        session=session,
        reconciliation=reconciliation,
    )


def _resolve_startup_status(
    stored_status,
    reconciliation: ReconciliationOutcome,
) -> TraderStartupStatus:
    if stored_status is not None and stored_status.status is TradingStatusValue.HALTED:
        return TraderStartupStatus.HALTED
    if stored_status is not None and stored_status.status is TradingStatusValue.CLOSE_ONLY:
        return TraderStartupStatus.CLOSE_ONLY
    if reconciliation.mismatch_detected:
        return TraderStartupStatus.CLOSE_ONLY
    return TraderStartupStatus.READY


def _get_clock(broker_client):
    if hasattr(broker_client, "get_clock"):
        return broker_client.get_clock()
    if hasattr(broker_client, "get_market_clock"):
        return broker_client.get_market_clock()
    raise AttributeError("broker client must expose get_clock() or get_market_clock()")


class TraderService:
    def __init__(
        self,
        *,
        settings: Settings,
        runtime: RuntimeContext,
        broker: BrokerProtocol,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._settings = settings
        self._runtime = runtime
        self._broker = broker
        self._now = now or (lambda: datetime.now(timezone.utc))

    def startup(self) -> TraderStartupReport:
        return start_trader(
            self._settings,
            broker_client=self._broker,
            bootstrap=lambda settings: self._runtime,
            now=self._now,
        )
