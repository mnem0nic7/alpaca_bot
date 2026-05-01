from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from importlib import import_module

import pytest

from alpaca_bot.config import Settings, TradingMode
from alpaca_bot.runtime import RuntimeContext
from alpaca_bot.storage import (
    DailySessionState,
    GLOBAL_SESSION_STATE_STRATEGY_NAME,
    TradingStatus,
    TradingStatusValue,
)


def load_reconcile_api() -> tuple[type[object], type[object], object, object]:
    module = import_module("alpaca_bot.runtime.reconcile")
    return (
        module.SessionSnapshot,
        module.ReconciliationOutcome,
        module.resolve_current_session,
        module.reconcile_startup,
    )


def load_trader_api() -> tuple[type[object], type[object], object]:
    module = import_module("alpaca_bot.runtime.trader")
    return (
        module.TraderStartupStatus,
        module.TraderStartupReport,
        module.start_trader,
    )


@dataclass(frozen=True)
class FakeClock:
    timestamp: datetime
    is_open: bool
    next_open: datetime
    next_close: datetime


@dataclass(frozen=True)
class FakeCalendarSession:
    date: date
    open: datetime
    close: datetime


class FakeBrokerClient:
    def __init__(
        self,
        *,
        clock: FakeClock,
        calendar: list[FakeCalendarSession],
    ) -> None:
        self._clock = clock
        self._calendar = list(calendar)
        self.clock_calls = 0
        self.calendar_calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def get_clock(self) -> FakeClock:
        self.clock_calls += 1
        return self._clock

    def get_calendar(self, *args: object, **kwargs: object) -> list[FakeCalendarSession]:
        self.calendar_calls.append((args, kwargs))
        return list(self._calendar)


class RecordingTradingStatusStore:
    def __init__(self, loaded_status: TradingStatus | None = None) -> None:
        self.loaded_status = loaded_status
        self.load_calls: list[tuple[TradingMode, str]] = []

    def load(
        self,
        *,
        trading_mode: TradingMode,
        strategy_version: str,
    ) -> TradingStatus | None:
        self.load_calls.append((trading_mode, strategy_version))
        return self.loaded_status


class RecordingDailySessionStateStore:
    def __init__(self) -> None:
        self.saved: list[DailySessionState] = []

    def save(self, state: DailySessionState) -> None:
        self.saved.append(state)


class BootstrapStub:
    def __init__(self, result: RuntimeContext | Exception) -> None:
        self._result = result
        self.calls = 0

    def __call__(self, *args: object, **kwargs: object) -> RuntimeContext:
        self.calls += 1
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


def make_settings() -> Settings:
    return Settings.from_env(
        {
            "TRADING_MODE": "paper",
            "ENABLE_LIVE_TRADING": "false",
            "STRATEGY_VERSION": "v1-breakout",
            "DATABASE_URL": "postgresql://alpaca_bot:secret@db.example.com:5432/alpaca_bot",
            "MARKET_DATA_FEED": "sip",
            "SYMBOLS": "AAPL,MSFT,SPY",
            "DAILY_SMA_PERIOD": "20",
            "BREAKOUT_LOOKBACK_BARS": "20",
            "RELATIVE_VOLUME_LOOKBACK_BARS": "20",
            "RELATIVE_VOLUME_THRESHOLD": "1.5",
            "ENTRY_TIMEFRAME_MINUTES": "15",
            "RISK_PER_TRADE_PCT": "0.0025",
            "MAX_POSITION_PCT": "0.05",
            "MAX_OPEN_POSITIONS": "3",
            "DAILY_LOSS_LIMIT_PCT": "0.01",
            "STOP_LIMIT_BUFFER_PCT": "0.001",
            "BREAKOUT_STOP_BUFFER_PCT": "0.001",
            "ENTRY_STOP_PRICE_BUFFER": "0.01",
            "ENTRY_WINDOW_START": "10:00",
            "ENTRY_WINDOW_END": "15:30",
            "FLATTEN_TIME": "15:45",
        }
    )


def make_runtime_context(
    settings: Settings,
    *,
    loaded_status: TradingStatus | None = None,
) -> tuple[RuntimeContext, RecordingTradingStatusStore, RecordingDailySessionStateStore]:
    trading_status_store = RecordingTradingStatusStore(loaded_status=loaded_status)
    daily_session_state_store = RecordingDailySessionStateStore()
    context = RuntimeContext(
        settings=settings,
        connection=object(),
        lock=object(),  # type: ignore[arg-type]
        trading_status_store=trading_status_store,  # type: ignore[arg-type]
        audit_event_store=object(),  # type: ignore[arg-type]
        order_store=object(),  # type: ignore[arg-type]
        daily_session_state_store=daily_session_state_store,  # type: ignore[arg-type]
    )
    return context, trading_status_store, daily_session_state_store


def make_pre_open_market() -> tuple[FakeClock, list[FakeCalendarSession]]:
    session_open = datetime(2026, 4, 24, 13, 30, tzinfo=timezone.utc)
    session_close = datetime(2026, 4, 24, 20, 0, tzinfo=timezone.utc)
    return (
        FakeClock(
            timestamp=datetime(2026, 4, 24, 12, 45, tzinfo=timezone.utc),
            is_open=False,
            next_open=session_open,
            next_close=session_close,
        ),
        [
            FakeCalendarSession(
                date=date(2026, 4, 24),
                open=session_open,
                close=session_close,
            )
        ],
    )


def make_trading_status(
    settings: Settings,
    *,
    status: TradingStatusValue,
    updated_at: datetime,
) -> TradingStatus:
    return TradingStatus(
        trading_mode=settings.trading_mode,
        strategy_version=settings.strategy_version,
        status=status,
        kill_switch_enabled=status is TradingStatusValue.HALTED,
        status_reason="manual intervention" if status is TradingStatusValue.HALTED else None,
        updated_at=updated_at,
    )


def test_resolve_current_session_uses_same_day_calendar_when_market_is_pre_open() -> None:
    SessionSnapshot, _ReconciliationOutcome, resolve_current_session, _reconcile_startup = (
        load_reconcile_api()
    )
    clock, calendar = make_pre_open_market()

    snapshot = resolve_current_session(clock=clock, calendar=calendar)

    assert snapshot == SessionSnapshot(
        session_date=date(2026, 4, 24),
        as_of=clock.timestamp,
        is_open=False,
        opens_at=calendar[0].open,
        closes_at=calendar[0].close,
    )


def test_reconcile_startup_saves_daily_session_state_and_marks_mismatch_close_only() -> None:
    SessionSnapshot, ReconciliationOutcome, _resolve_current_session, reconcile_startup = (
        load_reconcile_api()
    )
    settings = make_settings()
    context, _status_store, daily_session_state_store = make_runtime_context(settings)
    now = datetime(2026, 4, 24, 12, 50, tzinfo=timezone.utc)
    session = SessionSnapshot(
        session_date=date(2026, 4, 24),
        as_of=datetime(2026, 4, 24, 12, 45, tzinfo=timezone.utc),
        is_open=False,
        opens_at=datetime(2026, 4, 24, 13, 30, tzinfo=timezone.utc),
        closes_at=datetime(2026, 4, 24, 20, 0, tzinfo=timezone.utc),
    )

    def mismatch_detector(runtime_context: RuntimeContext, snapshot: object) -> list[str]:
        assert runtime_context is context
        assert snapshot == session
        return ["broker order mismatch"]

    outcome = reconcile_startup(
        context=context,
        session=session,
        entries_disabled=False,
        mismatch_detector=mismatch_detector,
        now=lambda: now,
    )

    expected_state = DailySessionState(
        session_date=date(2026, 4, 24),
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        strategy_name=GLOBAL_SESSION_STATE_STRATEGY_NAME,
        entries_disabled=True,
        flatten_complete=False,
        last_reconciled_at=now,
        notes="broker order mismatch",
        updated_at=now,
    )
    assert outcome == ReconciliationOutcome(
        session=session,
        mismatch_detected=True,
        mismatches=("broker order mismatch",),
        session_state=expected_state,
    )
    assert daily_session_state_store.saved == [expected_state]


@pytest.mark.parametrize(
    ("stored_status", "mismatches", "expected_status", "expected_entries_disabled"),
    [
        (None, (), "ready", False),
        (TradingStatusValue.HALTED, ("broker position mismatch",), "halted", True),
        (TradingStatusValue.CLOSE_ONLY, (), "close_only", True),
        (TradingStatusValue.ENABLED, ("broker order mismatch",), "close_only", True),
    ],
)
def test_start_trader_reports_status_from_stored_status_and_mismatches(
    stored_status: TradingStatusValue | None,
    mismatches: tuple[str, ...],
    expected_status: str,
    expected_entries_disabled: bool,
) -> None:
    TraderStartupStatus, _TraderStartupReport, start_trader = load_trader_api()
    settings = make_settings()
    now = datetime(2026, 4, 24, 12, 55, tzinfo=timezone.utc)
    clock, calendar = make_pre_open_market()
    loaded_status = (
        None
        if stored_status is None
        else make_trading_status(settings, status=stored_status, updated_at=now)
    )
    context, trading_status_store, daily_session_state_store = make_runtime_context(
        settings,
        loaded_status=loaded_status,
    )
    bootstrap = BootstrapStub(context)
    broker_client = FakeBrokerClient(clock=clock, calendar=calendar)

    def mismatch_detector(runtime_context: RuntimeContext, snapshot: object) -> list[str]:
        assert runtime_context is context
        assert getattr(snapshot, "session_date") == date(2026, 4, 24)
        return list(mismatches)

    report = start_trader(
        settings,
        broker_client=broker_client,
        bootstrap=bootstrap,
        mismatch_detector=mismatch_detector,
        now=lambda: now,
    )

    expected_state = DailySessionState(
        session_date=date(2026, 4, 24),
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        strategy_name=GLOBAL_SESSION_STATE_STRATEGY_NAME,
        entries_disabled=expected_entries_disabled,
        flatten_complete=False,
        last_reconciled_at=now,
        notes=mismatches[0] if mismatches else None,
        updated_at=now,
    )
    assert bootstrap.calls == 1
    assert broker_client.clock_calls == 1
    assert len(broker_client.calendar_calls) == 1
    assert trading_status_store.load_calls == [(TradingMode.PAPER, "v1-breakout")]
    assert report.status == TraderStartupStatus(expected_status)
    assert report.session.session_date == date(2026, 4, 24)
    assert report.reconciliation.mismatch_detected is bool(mismatches)
    assert report.reconciliation.session_state == expected_state
    assert daily_session_state_store.saved == [expected_state]


def test_start_trader_raises_when_market_clock_cannot_be_resolved() -> None:
    _TraderStartupStatus, _TraderStartupReport, start_trader = load_trader_api()
    settings = make_settings()
    now = datetime(2026, 4, 24, 13, 0, tzinfo=timezone.utc)
    clock, _calendar = make_pre_open_market()
    broker_client = FakeBrokerClient(
        clock=clock,
        calendar=[
            FakeCalendarSession(
                date=date(2026, 4, 25),
                open=datetime(2026, 4, 25, 13, 30, tzinfo=timezone.utc),
                close=datetime(2026, 4, 25, 20, 0, tzinfo=timezone.utc),
            )
        ],
    )
    context, _status_store, daily_session_state_store = make_runtime_context(settings)
    bootstrap = BootstrapStub(context)

    with pytest.raises(RuntimeError, match="market clock"):
        start_trader(
            settings,
            broker_client=broker_client,
            bootstrap=bootstrap,
            now=lambda: now,
        )

    assert daily_session_state_store.saved == []


def test_start_trader_propagates_singleton_lock_failures_before_market_queries() -> None:
    _TraderStartupStatus, _TraderStartupReport, start_trader = load_trader_api()
    settings = make_settings()
    clock, calendar = make_pre_open_market()
    broker_client = FakeBrokerClient(clock=clock, calendar=calendar)
    bootstrap = BootstrapStub(
        RuntimeError("Could not acquire singleton trader lock for paper/v1-breakout")
    )

    with pytest.raises(RuntimeError, match="singleton trader lock"):
        start_trader(
            settings,
            broker_client=broker_client,
            bootstrap=bootstrap,
        )

    assert broker_client.clock_calls == 0
    assert broker_client.calendar_calls == []
