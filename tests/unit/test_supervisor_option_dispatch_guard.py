from __future__ import annotations

from datetime import datetime, timezone
from importlib import import_module
from types import SimpleNamespace

from alpaca_bot.strategy.session import SessionType
from tests.unit.helpers import _base_env
from alpaca_bot.config import Settings


def _make_settings() -> Settings:
    return Settings.from_env(_base_env())


def _make_supervisor_with_option_broker():
    """Build a supervisor wired with a fake _option_broker and option_order_store.
    Monkeypatching of dispatch_pending_option_orders must be done by the caller
    after construction using monkeypatch."""
    module = import_module("alpaca_bot.runtime.supervisor")
    RuntimeSupervisor = module.RuntimeSupervisor
    settings = _make_settings()

    class _FakeConn:
        def commit(self): pass
        def rollback(self): pass

    class _FakeOptionOrderStore:
        def list_open_option_positions(self, **kw): return []
        def list_pending_submit(self, **kw): return []
        def list_trade_pnl_by_strategy(self, **kw): return []

    class _FakeRuntime:
        connection = _FakeConn()
        store_lock = None
        audit_event_store = SimpleNamespace(
            append=lambda *a, **k: None,
            load_latest=lambda **_: None,
            list_recent=lambda **_: [],
            list_by_event_types=lambda **_: [],
        )
        position_store = SimpleNamespace(list_all=lambda **kw: [])
        order_store = SimpleNamespace(
            list_by_status=lambda **kw: [],
            list_pending_submit=lambda **kw: [],
            daily_realized_pnl=lambda **kw: 0.0,
            daily_realized_pnl_by_symbol=lambda **kw: {},
            list_trade_pnl_by_strategy=lambda **kw: [],
        )
        trading_status_store = SimpleNamespace(load=lambda **kw: None)
        daily_session_state_store = SimpleNamespace(
            load=lambda **kw: None, save=lambda *a, **kw: None, list_by_session=lambda **kw: []
        )
        strategy_flag_store = SimpleNamespace(list_all=lambda **kw: [], load=lambda **kw: None)
        watchlist_store = SimpleNamespace(
            list_enabled=lambda *a: ["AAPL"], list_ignored=lambda *a: []
        )
        option_order_store = _FakeOptionOrderStore()

    fake_option_broker = SimpleNamespace()

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
        cycle_intent_executor=lambda **kw: SimpleNamespace(
            submitted_exit_count=0, failed_exit_count=0,
            replaced_stop_count=0, submitted_stop_count=0, canceled_stop_count=0,
        ),
        order_dispatcher=lambda **kw: {"submitted_count": 0},
        option_broker=fake_option_broker,
    )
    return supervisor, module


def test_option_dispatch_skipped_when_not_regular_session(monkeypatch):
    """dispatch_pending_option_orders must NOT be called when session_type is
    AFTER_HOURS (or any non-REGULAR session). Regression test for the missing
    session guard at supervisor.py line ~1082."""
    dispatch_calls: list = []
    supervisor, module = _make_supervisor_with_option_broker()

    monkeypatch.setattr(
        module, "recover_startup_state",
        lambda **kw: module.StartupRecoveryReport(
            mismatches=(), synced_position_count=0, synced_order_count=0,
            cleared_position_count=0, cleared_order_count=0,
        ),
    )
    monkeypatch.setattr(
        module, "dispatch_pending_option_orders",
        lambda **kw: dispatch_calls.append(kw),
    )

    # 20:00 UTC = 16:00 ET — AFTER_HOURS
    ts = datetime(2026, 5, 27, 20, 0, tzinfo=timezone.utc)
    supervisor.run_cycle_once(now=lambda: ts, session_type=SessionType.AFTER_HOURS)

    assert dispatch_calls == [], (
        "dispatch_pending_option_orders must not fire outside REGULAR market hours"
    )


def test_option_dispatch_called_during_regular_session(monkeypatch):
    """dispatch_pending_option_orders IS called when session_type is REGULAR."""
    dispatch_calls: list = []
    supervisor, module = _make_supervisor_with_option_broker()

    monkeypatch.setattr(
        module, "recover_startup_state",
        lambda **kw: module.StartupRecoveryReport(
            mismatches=(), synced_position_count=0, synced_order_count=0,
            cleared_position_count=0, cleared_order_count=0,
        ),
    )
    monkeypatch.setattr(
        module, "dispatch_pending_option_orders",
        lambda **kw: dispatch_calls.append(kw),
    )

    # 14:00 UTC = 10:00 ET — REGULAR market hours
    ts = datetime(2026, 5, 27, 14, 0, tzinfo=timezone.utc)
    supervisor.run_cycle_once(now=lambda: ts, session_type=SessionType.REGULAR)

    assert len(dispatch_calls) >= 1, (
        "dispatch_pending_option_orders must be called once per cycle during REGULAR session"
    )
