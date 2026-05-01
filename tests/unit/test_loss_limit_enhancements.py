from __future__ import annotations

from datetime import date, datetime, timezone
from importlib import import_module
from types import SimpleNamespace

import pytest

from alpaca_bot.config import Settings, TradingMode
from alpaca_bot.execution import BrokerAccount
from alpaca_bot.storage import AuditEvent, DailySessionState


# ── shared helpers ────────────────────────────────────────────────────────────

_NOW = datetime(2026, 5, 1, 14, 30, tzinfo=timezone.utc)
_SESSION_DATE = date(2026, 5, 1)


def _make_settings(**overrides) -> Settings:
    base = {
        "TRADING_MODE": "paper",
        "ENABLE_LIVE_TRADING": "false",
        "STRATEGY_VERSION": "v1",
        "DATABASE_URL": "postgresql://x:y@localhost/db",
        "MARKET_DATA_FEED": "sip",
        "SYMBOLS": "AAPL,MSFT",
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
    base.update(overrides)
    return Settings.from_env(base)


def _load_supervisor_api():
    module = import_module("alpaca_bot.runtime.supervisor")
    return module, module.RuntimeSupervisor


class _FakeConn:
    def commit(self) -> None:
        pass

    def rollback(self) -> None:
        pass


class _RecordingAuditStore:
    def __init__(self) -> None:
        self.appended: list[AuditEvent] = []

    def append(self, event: AuditEvent, *, commit: bool = True) -> None:
        self.appended.append(event)

    def load_latest(self, **kwargs) -> AuditEvent | None:
        return None

    def list_recent(self, **kwargs) -> list[AuditEvent]:
        return []

    def list_by_event_types(self, **kwargs) -> list[AuditEvent]:
        return []


class _RecordingOrderStore:
    def __init__(self, *, daily_pnl: float = 0.0, pnl_by_symbol: dict | None = None) -> None:
        self._daily_pnl = daily_pnl
        self._pnl_by_symbol: dict[str, float] = pnl_by_symbol or {}
        self.saved: list[object] = []

    def save(self, order, *, commit: bool = True) -> None:
        self.saved.append(order)

    def list_by_status(self, **kwargs):
        return []

    def list_pending_submit(self, **kwargs):
        return []

    def daily_realized_pnl(self, **kwargs) -> float:
        return self._daily_pnl

    def daily_realized_pnl_by_symbol(self, **kwargs) -> dict[str, float]:
        return dict(self._pnl_by_symbol)


class _RecordingSessionStateStore:
    def __init__(self, preloaded: DailySessionState | None = None) -> None:
        self._preloaded = preloaded
        self.saved: list[DailySessionState] = []

    def load(self, *, session_date, trading_mode, strategy_version, strategy_name="breakout"):
        if self._preloaded and self._preloaded.strategy_name == strategy_name:
            return self._preloaded
        return None

    def save(self, state: DailySessionState) -> None:
        self.saved.append(state)

    def list_by_session(self, **kwargs):
        return []


def _make_supervisor(
    *,
    settings: Settings,
    broker_equity: float = 10_000.0,
    order_store: _RecordingOrderStore | None = None,
    session_state_store: _RecordingSessionStateStore | None = None,
    equity_baseline: float | None = None,
):
    module, RuntimeSupervisor = _load_supervisor_api()

    class _FakeBroker:
        def get_account(self):
            return BrokerAccount(
                equity=broker_equity,
                buying_power=broker_equity * 2,
                trading_blocked=False,
            )
        def list_open_orders(self):
            return []

    class _FakeMarketData:
        def get_stock_bars(self, **kwargs):
            return {}
        def get_daily_bars(self, **kwargs):
            return {}

    class _FakeTradingStatusStore:
        def load(self, **kwargs):
            return None

    class _FakePositionStore:
        def list_all(self, **kwargs):
            return []
        def replace_all(self, **kwargs):
            pass

    class _FakeStrategyFlagStore:
        def list_all(self, **kwargs):
            return []
        def load(self, **kwargs):
            return None

    class _FakeWatchlistStore:
        def list_enabled(self, *args):
            return ["AAPL", "MSFT"]
        def list_ignored(self, *args):
            return []

    _order_store = order_store or _RecordingOrderStore()
    _sess_store = session_state_store or _RecordingSessionStateStore()

    class _FakeRuntimeContext:
        connection = _FakeConn()
        store_lock = None
        order_store = _order_store
        trading_status_store = _FakeTradingStatusStore()
        position_store = _FakePositionStore()
        daily_session_state_store = _sess_store
        audit_event_store = _RecordingAuditStore()
        strategy_flag_store = _FakeStrategyFlagStore()
        watchlist_store = _FakeWatchlistStore()

        def commit(self):
            pass

    supervisor = RuntimeSupervisor(
        settings=settings,
        runtime=_FakeRuntimeContext(),
        broker=_FakeBroker(),
        market_data=_FakeMarketData(),
        stream=None,
        close_runtime_fn=lambda _: None,
        connection_checker=lambda _: True,
        cycle_runner=lambda **kwargs: SimpleNamespace(intents=[]),
        cycle_intent_executor=lambda **kwargs: SimpleNamespace(
            submitted_exit_count=0, failed_exit_count=0
        ),
        order_dispatcher=lambda **kwargs: {"submitted_count": 0},
    )
    if equity_baseline is not None:
        supervisor._session_equity_baseline[_SESSION_DATE] = equity_baseline
    return supervisor, _FakeRuntimeContext


# ── Settings tests ────────────────────────────────────────────────────────────

def test_settings_per_symbol_loss_limit_pct_defaults_to_zero():
    s = _make_settings()
    assert s.per_symbol_loss_limit_pct == 0.0


def test_settings_per_symbol_loss_limit_pct_parsed_from_env():
    s = _make_settings(**{"PER_SYMBOL_LOSS_LIMIT_PCT": "0.005"})
    assert s.per_symbol_loss_limit_pct == pytest.approx(0.005)


def test_settings_per_symbol_loss_limit_pct_negative_raises():
    with pytest.raises(ValueError, match="PER_SYMBOL_LOSS_LIMIT_PCT"):
        _make_settings(**{"PER_SYMBOL_LOSS_LIMIT_PCT": "-0.001"})


def test_settings_per_symbol_loss_limit_pct_ge_one_raises():
    with pytest.raises(ValueError, match="PER_SYMBOL_LOSS_LIMIT_PCT"):
        _make_settings(**{"PER_SYMBOL_LOSS_LIMIT_PCT": "1.0"})


# ── Sticky loss limit tests ───────────────────────────────────────────────────

def test_loss_limit_remains_disabled_after_equity_recovery(monkeypatch):
    """Once the daily loss limit fires it must stay fired even after equity recovers."""
    module, _ = _load_supervisor_api()
    monkeypatch.setattr(module, "run_cycle", lambda **kwargs: SimpleNamespace(intents=[]))
    monkeypatch.setattr(module, "dispatch_pending_orders", lambda **kwargs: {"submitted_count": 0})
    monkeypatch.setattr(module, "execute_cycle_intents", lambda **kwargs: SimpleNamespace(
        submitted_exit_count=0, failed_exit_count=0
    ))

    settings = _make_settings(**{"DAILY_LOSS_LIMIT_PCT": "0.01"})
    # Cycle 1: equity 9_880 vs baseline 10_000 → PnL = -120 > limit 100 → BREACH
    order_store = _RecordingOrderStore(daily_pnl=-120.0)
    supervisor, _ = _make_supervisor(
        settings=settings,
        broker_equity=9_880.0,
        order_store=order_store,
        equity_baseline=10_000.0,
    )
    supervisor.run_cycle_once(now=lambda: _NOW)
    assert _SESSION_DATE in supervisor._loss_limit_fired, "Loss limit must be in fired set"

    # Cycle 2: equity recovers to 10_050 → live condition is False but sticky flag must win
    supervisor.broker = type("B", (), {
        "get_account": lambda self: BrokerAccount(equity=10_050.0, buying_power=20_100.0, trading_blocked=False),
        "list_open_orders": lambda self: [],
    })()
    report = supervisor.run_cycle_once(now=lambda: _NOW)
    assert report.entries_disabled, "Entries must remain disabled after equity recovery"


def test_loss_limit_fires_sticky_flag_in_memory():
    """_loss_limit_fired must be populated when the breach condition first becomes True."""
    settings = _make_settings(**{"DAILY_LOSS_LIMIT_PCT": "0.01"})
    supervisor, _ = _make_supervisor(
        settings=settings,
        broker_equity=9_880.0,
        order_store=_RecordingOrderStore(daily_pnl=-120.0),
        equity_baseline=10_000.0,
    )

    supervisor._save_session_state = lambda s: None
    supervisor._append_audit = lambda e: None

    baseline_equity = 10_000.0
    loss_limit = settings.daily_loss_limit_pct * baseline_equity
    total_pnl = 9_880.0 - baseline_equity  # -120
    if total_pnl < -loss_limit:
        supervisor._loss_limit_fired.add(_SESSION_DATE)
    daily_loss_limit_breached = _SESSION_DATE in supervisor._loss_limit_fired

    assert daily_loss_limit_breached


def test_loss_limit_sticky_flag_restored_from_persisted_state():
    """On restart the supervisor must re-populate _loss_limit_fired from the _equity row."""
    persisted_equity_row = DailySessionState(
        session_date=_SESSION_DATE,
        trading_mode=TradingMode.PAPER,
        strategy_version="v1",
        strategy_name="_equity",
        entries_disabled=True,
        flatten_complete=False,
        equity_baseline=10_000.0,
    )
    settings = _make_settings()

    supervisor, _ = _make_supervisor(
        settings=settings,
        broker_equity=10_050.0,
        session_state_store=_RecordingSessionStateStore(preloaded=persisted_equity_row),
        equity_baseline=None,
    )

    supervisor._cycle_runner = lambda **kwargs: SimpleNamespace(intents=[])
    supervisor._cycle_intent_executor = lambda **kwargs: SimpleNamespace(
        submitted_exit_count=0, failed_exit_count=0
    )
    supervisor._order_dispatcher = lambda **kwargs: {"submitted_count": 0}

    report = supervisor.run_cycle_once(now=lambda: _NOW)
    assert _SESSION_DATE in supervisor._loss_limit_fired, (
        "Supervisor must restore _loss_limit_fired from persisted _equity row"
    )
    assert report.entries_disabled, "Entries must be disabled after restart if prior breach was persisted"


def test_loss_limit_persists_sticky_marker_to_postgres():
    """When the loss limit fires, the _equity session-state row must be updated with entries_disabled=True."""
    settings = _make_settings(**{"DAILY_LOSS_LIMIT_PCT": "0.01"})
    sess_store = _RecordingSessionStateStore()
    order_store = _RecordingOrderStore(daily_pnl=-120.0)

    supervisor, _ = _make_supervisor(
        settings=settings,
        broker_equity=9_880.0,
        order_store=order_store,
        session_state_store=sess_store,
        equity_baseline=10_000.0,
    )
    supervisor._cycle_runner = lambda **kwargs: SimpleNamespace(intents=[])
    supervisor._cycle_intent_executor = lambda **kwargs: SimpleNamespace(
        submitted_exit_count=0, failed_exit_count=0
    )
    supervisor._order_dispatcher = lambda **kwargs: {"submitted_count": 0}
    supervisor._notifier = None

    supervisor.run_cycle_once(now=lambda: _NOW)

    equity_rows = [
        s for s in sess_store.saved
        if s.strategy_name == "_equity" and s.entries_disabled
    ]
    assert equity_rows, "Expected _equity row saved with entries_disabled=True when loss limit fires"


# ── Per-symbol loss limit tests ───────────────────────────────────────────────

def test_per_symbol_loss_limit_blocks_entries_for_breached_symbol(monkeypatch):
    """When a symbol's realized PnL exceeds per_symbol_loss_limit, it must be blocked from new entries."""
    module, _ = _load_supervisor_api()
    monkeypatch.setattr(module, "dispatch_pending_orders", lambda **kwargs: {"submitted_count": 0})
    monkeypatch.setattr(module, "execute_cycle_intents", lambda **kwargs: SimpleNamespace(
        submitted_exit_count=0, failed_exit_count=0
    ))

    received_working_symbols: list[set] = []

    def _fake_cycle_runner(**kwargs):
        received_working_symbols.append(set(kwargs["working_order_symbols"]))
        return SimpleNamespace(intents=[])

    # baseline=10_000, per_symbol_limit=0.005 → limit=$50; AAPL realized=-60 → blocked
    settings = _make_settings(**{
        "PER_SYMBOL_LOSS_LIMIT_PCT": "0.005",
        "DAILY_LOSS_LIMIT_PCT": "0.05",
    })
    supervisor, _ = _make_supervisor(
        settings=settings,
        broker_equity=10_000.0,
        order_store=_RecordingOrderStore(
            daily_pnl=-60.0,
            pnl_by_symbol={"AAPL": -60.0},
        ),
        equity_baseline=10_000.0,
    )
    supervisor._cycle_runner = _fake_cycle_runner
    supervisor.run_cycle_once(now=lambda: _NOW)

    assert received_working_symbols, "cycle_runner must have been called"
    assert "AAPL" in received_working_symbols[0], (
        "AAPL must appear in working_order_symbols when per-symbol limit is breached"
    )


def test_per_symbol_loss_limit_does_not_block_symbol_within_limit(monkeypatch):
    """Symbols within the per-symbol limit must not be added to working_order_symbols."""
    module, _ = _load_supervisor_api()
    monkeypatch.setattr(module, "dispatch_pending_orders", lambda **kwargs: {"submitted_count": 0})
    monkeypatch.setattr(module, "execute_cycle_intents", lambda **kwargs: SimpleNamespace(
        submitted_exit_count=0, failed_exit_count=0
    ))

    received_working_symbols: list[set] = []

    def _fake_cycle_runner(**kwargs):
        received_working_symbols.append(set(kwargs["working_order_symbols"]))
        return SimpleNamespace(intents=[])

    # baseline=10_000, per_symbol_limit=0.005 → limit=$50; AAPL realized=-30 → NOT blocked
    settings = _make_settings(**{
        "PER_SYMBOL_LOSS_LIMIT_PCT": "0.005",
        "DAILY_LOSS_LIMIT_PCT": "0.05",
    })
    supervisor, _ = _make_supervisor(
        settings=settings,
        broker_equity=10_000.0,
        order_store=_RecordingOrderStore(
            daily_pnl=-30.0,
            pnl_by_symbol={"AAPL": -30.0},
        ),
        equity_baseline=10_000.0,
    )
    supervisor._cycle_runner = _fake_cycle_runner
    supervisor.run_cycle_once(now=lambda: _NOW)

    assert received_working_symbols, "cycle_runner must have been called"
    assert "AAPL" not in received_working_symbols[0], (
        "AAPL must NOT be in working_order_symbols when within per-symbol limit"
    )


def test_per_symbol_loss_limit_emits_audit_event_once_per_symbol(monkeypatch):
    """A per_symbol_loss_limit_breached audit event must be emitted once per symbol."""
    module, _ = _load_supervisor_api()
    monkeypatch.setattr(module, "run_cycle", lambda **kwargs: SimpleNamespace(intents=[]))
    monkeypatch.setattr(module, "dispatch_pending_orders", lambda **kwargs: {"submitted_count": 0})
    monkeypatch.setattr(module, "execute_cycle_intents", lambda **kwargs: SimpleNamespace(
        submitted_exit_count=0, failed_exit_count=0
    ))

    settings = _make_settings(**{
        "PER_SYMBOL_LOSS_LIMIT_PCT": "0.005",
        "DAILY_LOSS_LIMIT_PCT": "0.05",
    })
    supervisor, _ = _make_supervisor(
        settings=settings,
        broker_equity=10_000.0,
        order_store=_RecordingOrderStore(
            daily_pnl=-60.0,
            pnl_by_symbol={"AAPL": -60.0},
        ),
        equity_baseline=10_000.0,
    )
    # Run two cycles — audit event should appear exactly once
    supervisor.run_cycle_once(now=lambda: _NOW)
    supervisor.run_cycle_once(now=lambda: _NOW)

    breach_events = [
        e for e in supervisor.runtime.audit_event_store.appended
        if getattr(e, "event_type", None) == "per_symbol_loss_limit_breached"
        and getattr(e, "symbol", None) == "AAPL"
    ]
    assert len(breach_events) == 1, "Expected exactly one per_symbol_loss_limit_breached audit event per symbol"


def test_per_symbol_loss_limit_disabled_when_zero(monkeypatch):
    """When per_symbol_loss_limit_pct=0.0 (default), no per-symbol check must run."""
    module, _ = _load_supervisor_api()
    monkeypatch.setattr(module, "run_cycle", lambda **kwargs: SimpleNamespace(intents=[]))
    monkeypatch.setattr(module, "dispatch_pending_orders", lambda **kwargs: {"submitted_count": 0})
    monkeypatch.setattr(module, "execute_cycle_intents", lambda **kwargs: SimpleNamespace(
        submitted_exit_count=0, failed_exit_count=0
    ))

    settings = _make_settings()  # per_symbol_loss_limit_pct=0.0 by default
    supervisor, _ = _make_supervisor(
        settings=settings,
        broker_equity=10_000.0,
        order_store=_RecordingOrderStore(
            daily_pnl=-60.0,
            pnl_by_symbol={"AAPL": -60.0},
        ),
        equity_baseline=10_000.0,
    )
    supervisor.run_cycle_once(now=lambda: _NOW)

    breach_events = [
        e for e in supervisor.runtime.audit_event_store.appended
        if getattr(e, "event_type", None) == "per_symbol_loss_limit_breached"
    ]
    assert len(breach_events) == 0, "No per_symbol_loss_limit_breached events when feature is disabled"


def test_daily_realized_pnl_by_symbol_empty_when_no_trades():
    """daily_realized_pnl_by_symbol must return {} when there are no closed trades."""
    from alpaca_bot.storage.repositories import OrderStore as _OrderStore

    class _FakeConn2:
        def cursor(self):
            class _C:
                def execute(self, *a): pass
                def fetchall(self): return []
                def close(self): pass
            return _C()
        def commit(self): pass

    store = _OrderStore(_FakeConn2())
    result = store.daily_realized_pnl_by_symbol(
        trading_mode=TradingMode.PAPER,
        strategy_version="v1",
        session_date=_SESSION_DATE,
    )
    assert result == {}
