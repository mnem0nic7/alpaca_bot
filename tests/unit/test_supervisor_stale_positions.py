from __future__ import annotations

from datetime import date, datetime, timezone
from importlib import import_module
from types import SimpleNamespace

from alpaca_bot.config import Settings
from alpaca_bot.core.engine import CycleIntent, CycleIntentType, CycleResult
from alpaca_bot.domain import OpenPosition
from alpaca_bot.storage.models import AuditEvent


def _make_settings() -> Settings:
    return Settings.from_env(
        {
            "TRADING_MODE": "paper",
            "ENABLE_LIVE_TRADING": "false",
            "STRATEGY_VERSION": "v1",
            "DATABASE_URL": "postgresql://x:y@localhost/db",
            "MARKET_DATA_FEED": "sip",
            "SYMBOLS": "AAPL",
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
            "PER_SYMBOL_LOSS_LIMIT_PCT": "0.0",
        }
    )


class _RecordingExecutor:
    """Records calls to cycle_intent_executor and returns a fake execution report."""
    def __init__(self):
        self.calls: list[dict] = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            submitted_exit_count=1,
            failed_exit_count=0,
            replaced_stop_count=0,
            submitted_stop_count=0,
            canceled_stop_count=0,
        )


class _RecordingAuditStore:
    def __init__(self):
        self.appended: list[AuditEvent] = []

    def append(self, event, *, commit=True):
        self.appended.append(event)


class _RecordingNotifier:
    def __init__(self):
        self.calls: list[tuple[str, str]] = []

    def send(self, subject: str, body: str) -> None:
        self.calls.append((subject, body))


def _make_supervisor(*, executor=None, notifier=None):
    module = import_module("alpaca_bot.runtime.supervisor")
    RuntimeSupervisor = module.RuntimeSupervisor
    settings = _make_settings()
    audit_store = _RecordingAuditStore()

    class _FakeConn:
        def commit(self): pass
        def rollback(self): pass

    class _FakeRuntime:
        connection = _FakeConn()
        store_lock = None
        audit_event_store = audit_store
        position_store = SimpleNamespace(list_all=lambda **kw: [])
        order_store = SimpleNamespace(
            list_by_status=lambda **kw: [],
            list_pending_submit=lambda **kw: [],
            daily_realized_pnl=lambda **kw: 0.0,
            daily_realized_pnl_by_symbol=lambda **kw: {},
        )
        trading_status_store = SimpleNamespace(load=lambda **kw: None)
        daily_session_state_store = SimpleNamespace(
            load=lambda **kw: None, save=lambda **kw: None, list_by_session=lambda **kw: []
        )
        strategy_flag_store = SimpleNamespace(list_all=lambda **kw: [], load=lambda **kw: None)
        watchlist_store = SimpleNamespace(
            list_enabled=lambda *a: ["AAPL"], list_ignored=lambda *a: []
        )

    _exec = executor or _RecordingExecutor()
    supervisor = RuntimeSupervisor(
        settings=settings,
        runtime=_FakeRuntime(),
        broker=SimpleNamespace(
            get_account=lambda: SimpleNamespace(
                equity=100_000.0, buying_power=200_000.0, trading_blocked=False
            ),
            list_open_orders=lambda: [],
            list_open_positions=lambda: [],
        ),
        market_data=SimpleNamespace(get_stock_bars=lambda **kw: {}, get_daily_bars=lambda **kw: {}),
        stream=None,
        close_runtime_fn=lambda _: None,
        connection_checker=lambda _: True,
        cycle_runner=lambda **kw: SimpleNamespace(intents=[]),
        cycle_intent_executor=_exec,
        order_dispatcher=lambda **kw: {"submitted_count": 0},
        notifier=notifier,
    )
    # Expose audit store so tests can inspect it
    supervisor._test_audit_store = audit_store
    return supervisor, _exec, settings


def _make_open_position(
    symbol: str,
    entry_timestamp: datetime,
    strategy_name: str = "breakout",
) -> OpenPosition:
    return OpenPosition(
        symbol=symbol,
        entry_timestamp=entry_timestamp,
        entry_price=100.0,
        quantity=10.0,
        entry_level=98.0,
        initial_stop_price=98.0,
        stop_price=98.0,
        trailing_active=False,
        highest_price=100.0,
        strategy_name=strategy_name,
    )


def test_stale_positions_submitted_via_execute_cycle_intents():
    """Prior-session position → executor called with one EXIT intent.
    Current-session position → not included in the executor call."""
    supervisor, executor, settings = _make_supervisor()
    # Cycle timestamp: 2026-05-11 14:00 UTC = 2026-05-11 10:00 ET
    now = datetime(2026, 5, 11, 14, 0, tzinfo=timezone.utc)
    session_date = now.astimezone(settings.market_timezone).date()  # 2026-05-11

    stale_ts = datetime(2026, 5, 9, 20, 0, tzinfo=timezone.utc)    # 2026-05-09 ET — prior session
    current_ts = datetime(2026, 5, 11, 13, 45, tzinfo=timezone.utc)  # 2026-05-11 ET — today

    positions = [
        _make_open_position("AAPL", stale_ts),
        _make_open_position("MSFT", current_ts),
    ]

    supervisor._close_stale_carryover_positions(
        session_date=session_date,
        open_positions=positions,
        timestamp=now,
    )

    assert len(executor.calls) == 1
    cycle_result = executor.calls[0]["cycle_result"]
    assert len(cycle_result.intents) == 1
    intent = cycle_result.intents[0]
    assert intent.symbol == "AAPL"
    assert intent.intent_type == CycleIntentType.EXIT
    assert intent.reason == "stale_position_carryover"
    assert intent.strategy_name == "breakout"


def test_no_stale_positions_no_executor_call():
    """All positions opened today → executor never called, no audit event."""
    supervisor, executor, settings = _make_supervisor()
    now = datetime(2026, 5, 11, 14, 0, tzinfo=timezone.utc)
    session_date = now.astimezone(settings.market_timezone).date()

    positions = [
        _make_open_position("AAPL", datetime(2026, 5, 11, 13, 45, tzinfo=timezone.utc)),
    ]

    supervisor._close_stale_carryover_positions(
        session_date=session_date,
        open_positions=positions,
        timestamp=now,
    )

    assert executor.calls == []
    stale_events = [
        e for e in supervisor._test_audit_store.appended
        if getattr(e, "event_type", None) == "stale_positions_detected"
    ]
    assert stale_events == []


def test_stale_positions_detected_audit_event_written():
    """Stale position found → stale_positions_detected AuditEvent written with symbol list."""
    supervisor, _, settings = _make_supervisor()
    now = datetime(2026, 5, 11, 14, 0, tzinfo=timezone.utc)
    session_date = now.astimezone(settings.market_timezone).date()

    positions = [
        _make_open_position("AAPL", datetime(2026, 5, 9, 20, 0, tzinfo=timezone.utc)),
    ]

    supervisor._close_stale_carryover_positions(
        session_date=session_date,
        open_positions=positions,
        timestamp=now,
    )

    stale_events = [
        e for e in supervisor._test_audit_store.appended
        if e.event_type == "stale_positions_detected"
    ]
    assert len(stale_events) == 1
    assert "AAPL" in stale_events[0].payload["symbols"]
    assert stale_events[0].payload["session_date"] == "2026-05-11"


def test_stale_audit_event_written_every_detection_cycle():
    """stale_positions_detected is written on every call where stale positions exist,
    not gated by session date — the AuditEvent serves as a per-cycle signal."""
    supervisor, _, settings = _make_supervisor()
    now = datetime(2026, 5, 11, 14, 0, tzinfo=timezone.utc)
    session_date = now.astimezone(settings.market_timezone).date()
    positions = [_make_open_position("AAPL", datetime(2026, 5, 9, 20, 0, tzinfo=timezone.utc))]

    supervisor._close_stale_carryover_positions(
        session_date=session_date, open_positions=positions, timestamp=now
    )
    supervisor._close_stale_carryover_positions(
        session_date=session_date, open_positions=positions, timestamp=now
    )

    stale_events = [
        e for e in supervisor._test_audit_store.appended
        if e.event_type == "stale_positions_detected"
    ]
    assert len(stale_events) == 2


def test_notification_sent_once_per_session():
    """Two calls for the same session_date → notifier.send called exactly once."""
    notifier = _RecordingNotifier()
    supervisor, _, settings = _make_supervisor(notifier=notifier)
    now = datetime(2026, 5, 11, 14, 0, tzinfo=timezone.utc)
    session_date = now.astimezone(settings.market_timezone).date()
    positions = [_make_open_position("AAPL", datetime(2026, 5, 9, 20, 0, tzinfo=timezone.utc))]

    supervisor._close_stale_carryover_positions(
        session_date=session_date, open_positions=positions, timestamp=now
    )
    supervisor._close_stale_carryover_positions(
        session_date=session_date, open_positions=positions, timestamp=now
    )

    assert len(notifier.calls) == 1
    subject, body = notifier.calls[0]
    assert "stale" in subject.lower() or "carryover" in subject.lower()
    assert "AAPL" in body


def test_notification_not_sent_when_no_notifier():
    """No notifier configured → method completes without raising."""
    supervisor, _, settings = _make_supervisor(notifier=None)
    now = datetime(2026, 5, 11, 14, 0, tzinfo=timezone.utc)
    session_date = now.astimezone(settings.market_timezone).date()
    positions = [_make_open_position("AAPL", datetime(2026, 5, 9, 20, 0, tzinfo=timezone.utc))]

    # Must not raise even though notifier is None
    supervisor._close_stale_carryover_positions(
        session_date=session_date, open_positions=positions, timestamp=now
    )


def test_multiple_stale_positions_all_included_in_intent_list():
    """Two stale positions → CycleResult contains two EXIT intents, one per symbol."""
    supervisor, executor, settings = _make_supervisor()
    now = datetime(2026, 5, 11, 14, 0, tzinfo=timezone.utc)
    session_date = now.astimezone(settings.market_timezone).date()
    stale_ts = datetime(2026, 5, 9, 20, 0, tzinfo=timezone.utc)

    positions = [
        _make_open_position("AAPL", stale_ts),
        _make_open_position("MSFT", stale_ts, strategy_name="breakout"),
    ]

    supervisor._close_stale_carryover_positions(
        session_date=session_date, open_positions=positions, timestamp=now
    )

    assert len(executor.calls) == 1
    intents = executor.calls[0]["cycle_result"].intents
    symbols = {i.symbol for i in intents}
    assert symbols == {"AAPL", "MSFT"}
    assert all(i.intent_type == CycleIntentType.EXIT for i in intents)
    assert all(i.reason == "stale_position_carryover" for i in intents)


def test_executor_exception_does_not_propagate():
    """If the executor raises, the exception is caught and does not propagate."""
    class _RaisingExecutor:
        def __call__(self, **kwargs):
            raise RuntimeError("broker offline")

    supervisor, _, settings = _make_supervisor(executor=_RaisingExecutor())
    now = datetime(2026, 5, 11, 14, 0, tzinfo=timezone.utc)
    session_date = now.astimezone(settings.market_timezone).date()
    positions = [_make_open_position("AAPL", datetime(2026, 5, 9, 20, 0, tzinfo=timezone.utc))]

    # Must not raise
    supervisor._close_stale_carryover_positions(
        session_date=session_date, open_positions=positions, timestamp=now
    )


def test_stale_cleanup_called_during_run_cycle_once(monkeypatch) -> None:
    """run_cycle_once must call _close_stale_carryover_positions so stale exits
    fire automatically each cycle without operator intervention."""
    from importlib import import_module as _imp
    module = _imp("alpaca_bot.runtime.supervisor")
    settings = _make_settings()
    executor = _RecordingExecutor()

    from alpaca_bot.storage.models import PositionRecord
    from alpaca_bot.config import TradingMode

    # A position opened on a prior session date (2026-05-09 ET)
    stale_opened_at = datetime(2026, 5, 9, 20, 0, tzinfo=timezone.utc)
    stale_record = PositionRecord(
        symbol="AAPL",
        trading_mode=TradingMode.PAPER,
        strategy_version="v1",
        quantity=10.0,
        entry_price=100.0,
        stop_price=98.0,
        initial_stop_price=98.0,
        opened_at=stale_opened_at,
    )

    class _FakeConn:
        def commit(self): pass
        def rollback(self): pass

    audit_store = _RecordingAuditStore()

    class _FakeRuntime:
        connection = _FakeConn()
        store_lock = None
        audit_event_store = audit_store
        position_store = type("PS", (), {
            "list_all": lambda self, **kw: [stale_record],
        })()
        order_store = type("OS", (), {
            "list_by_status": lambda self, **kw: [],
            "list_pending_submit": lambda self, **kw: [],
            "daily_realized_pnl": lambda self, **kw: 0.0,
            "daily_realized_pnl_by_symbol": lambda self, **kw: {},
            "list_trade_pnl_by_strategy": lambda self, **kw: [],
        })()
        trading_status_store = SimpleNamespace(load=lambda **kw: None)
        daily_session_state_store = SimpleNamespace(
            load=lambda **kw: None,
            save=lambda *a, **kw: None,
            list_by_session=lambda **kw: [],
        )
        strategy_flag_store = SimpleNamespace(list_all=lambda **kw: [], load=lambda **kw: None)
        watchlist_store = SimpleNamespace(
            list_enabled=lambda *a: ["AAPL"], list_ignored=lambda *a: []
        )

    supervisor = module.RuntimeSupervisor(
        settings=settings,
        runtime=_FakeRuntime(),
        broker=SimpleNamespace(
            get_account=lambda: SimpleNamespace(
                equity=100_000.0, buying_power=200_000.0, trading_blocked=False
            ),
            list_open_orders=lambda: [],
            list_open_positions=lambda: [],
        ),
        market_data=SimpleNamespace(get_stock_bars=lambda **kw: {}, get_daily_bars=lambda **kw: {}),
        stream=None,
        close_runtime_fn=lambda _: None,
        connection_checker=lambda _: True,
        cycle_runner=lambda **kw: SimpleNamespace(intents=[]),
        cycle_intent_executor=executor,
        order_dispatcher=lambda **kw: {"submitted_count": 0},
    )
    monkeypatch.setattr(module, "recover_startup_state", lambda **kw: module.StartupRecoveryReport(
        mismatches=(), synced_position_count=0, synced_order_count=0,
        cleared_position_count=0, cleared_order_count=0,
    ))

    # Cycle timestamp: 2026-05-11 14:00 UTC = 2026-05-11 ET — next session after stale position
    now = datetime(2026, 5, 11, 14, 0, tzinfo=timezone.utc)
    supervisor.run_cycle_once(now=lambda: now)

    exit_calls = [
        call for call in executor.calls
        if any(
            getattr(i, "reason", None) == "stale_position_carryover"
            for i in getattr(call.get("cycle_result"), "intents", [])
        )
    ]
    assert len(exit_calls) >= 1, "Expected at least one stale-carryover exit submitted"
    aapl_exits = [
        i for call in exit_calls
        for i in call["cycle_result"].intents
        if i.symbol == "AAPL" and i.reason == "stale_position_carryover"
    ]
    assert len(aapl_exits) == 1

    stale_events = [
        e for e in audit_store.appended
        if getattr(e, "event_type", None) == "stale_positions_detected"
    ]
    assert len(stale_events) == 1
    assert "AAPL" in stale_events[0].payload["symbols"]
