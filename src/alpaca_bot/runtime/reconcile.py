from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Callable, Protocol, Sequence

from alpaca_bot.runtime.bootstrap import RuntimeContext
from alpaca_bot.storage import (
    DailySessionState,
    GLOBAL_SESSION_STATE_STRATEGY_NAME,
)


class ClockProtocol(Protocol):
    timestamp: datetime
    is_open: bool
    next_open: datetime
    next_close: datetime


class CalendarSessionProtocol(Protocol):
    date: date
    open: datetime
    close: datetime


@dataclass(frozen=True)
class SessionSnapshot:
    session_date: date
    as_of: datetime
    is_open: bool
    opens_at: datetime
    closes_at: datetime


@dataclass(frozen=True)
class ReconciliationOutcome:
    session: SessionSnapshot
    mismatch_detected: bool
    mismatches: tuple[str, ...]
    session_state: DailySessionState


def resolve_current_session(
    *,
    clock: ClockProtocol,
    calendar: Sequence[CalendarSessionProtocol],
) -> SessionSnapshot:
    target_dates = (
        clock.timestamp.date(),
        clock.next_open.date(),
        clock.next_close.date(),
    )
    selected = next(
        (item for item in calendar if _session_date(item) in target_dates),
        None,
    )
    if selected is None:
        raise RuntimeError("Could not resolve market clock to a current Alpaca session")

    return SessionSnapshot(
        session_date=_session_date(selected),
        as_of=clock.timestamp,
        is_open=bool(clock.is_open),
        opens_at=_session_open(selected),
        closes_at=_session_close(selected),
    )


def reconcile_startup(
    *,
    context: RuntimeContext,
    session: SessionSnapshot,
    entries_disabled: bool,
    mismatch_detector: Callable[[RuntimeContext, SessionSnapshot], Sequence[str]] | None = None,
    now: Callable[[], datetime] | None = None,
) -> ReconciliationOutcome:
    timestamp = (now or (lambda: datetime.now(timezone.utc)))()
    detector = mismatch_detector or (lambda runtime_context, snapshot: ())
    mismatches = tuple(detector(context, session))
    final_entries_disabled = entries_disabled or bool(mismatches)
    session_state = DailySessionState(
        session_date=session.session_date,
        trading_mode=context.settings.trading_mode,
        strategy_version=context.settings.strategy_version,
        strategy_name=GLOBAL_SESSION_STATE_STRATEGY_NAME,
        entries_disabled=final_entries_disabled,
        flatten_complete=False,
        last_reconciled_at=timestamp,
        notes=mismatches[0] if mismatches else None,
        updated_at=timestamp,
    )

    if context.daily_session_state_store is None:
        raise RuntimeError("Runtime context is missing a daily session state store")
    try:
        context.daily_session_state_store.save(session_state)
    except Exception:
        try:
            context.connection.rollback()
        except Exception:
            pass
        raise

    return ReconciliationOutcome(
        session=session,
        mismatch_detected=bool(mismatches),
        mismatches=mismatches,
        session_state=session_state,
    )


def _session_date(session: object) -> date:
    if hasattr(session, "date"):
        return session.date
    return session.session_date


def _session_open(session: object) -> datetime:
    if hasattr(session, "open"):
        return session.open
    return session.open_at


def _session_close(session: object) -> datetime:
    if hasattr(session, "close"):
        return session.close
    return session.close_at
