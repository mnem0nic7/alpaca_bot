from __future__ import annotations

from datetime import date, datetime, timezone
from importlib import import_module
from types import SimpleNamespace

import pytest

from alpaca_bot.config import Settings, TradingMode
from alpaca_bot.execution import BrokerAccount
from alpaca_bot.storage import AuditEvent

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


class _FakeConn:
    def commit(self): pass
    def rollback(self): pass


class _RecordingAuditStore:
    def __init__(self):
        self.appended: list[AuditEvent] = []
    def append(self, event: AuditEvent, *, commit: bool = True):
        self.appended.append(event)
    def load_latest(self, **kwargs): return None
    def list_recent(self, **kwargs): return []
    def list_by_event_types(self, **kwargs): return []


class _RecordingOrderStore:
    def __init__(self, *, pnl_rows: list[dict] | None = None):
        self._pnl_rows = pnl_rows or []
    def save(self, order, *, commit=True): pass
    def list_by_status(self, **kwargs): return []
    def list_pending_submit(self, **kwargs): return []
    def daily_realized_pnl(self, **kwargs): return 0.0
    def daily_realized_pnl_by_symbol(self, **kwargs): return {}
    def list_trade_pnl_by_strategy(self, **kwargs): return self._pnl_rows


class _RecordingOptionOrderStore:
    def __init__(self, *, pnl_rows: list[dict] | None = None):
        self._pnl_rows = pnl_rows or []

    def list_trade_pnl_by_strategy(self, **kwargs) -> list[dict]:
        return list(self._pnl_rows)


class _FakeWeightStore:
    def __init__(self, *, preloaded: list | None = None):
        self._preloaded = preloaded or []
        self.upserted: list[dict] = []
    def load_all(self, **kwargs): return list(self._preloaded)
    def upsert_many(self, *, weights, sharpes, trading_mode, strategy_version, computed_at):
        self.upserted.append({"weights": dict(weights), "sharpes": dict(sharpes)})


class _CapturingCycleRunner:
    def __init__(self):
        self.captured_equities: list[float] = []
        self.captured_strategy_names: list[str] = []
    def __call__(self, *, equity, strategy_name, **kwargs):
        self.captured_equities.append(equity)
        self.captured_strategy_names.append(strategy_name)
        return SimpleNamespace(intents=[])


def _make_supervisor(
    *,
    settings: Settings,
    broker_equity: float = 10_000.0,
    weight_store: _FakeWeightStore | None = None,
    order_store: _RecordingOrderStore | None = None,
    option_order_store: _RecordingOptionOrderStore | None = None,
    cycle_runner=None,
    only_breakout: bool = True,
    order_dispatcher=None,
):
    module = import_module("alpaca_bot.runtime.supervisor")
    RuntimeSupervisor = module.RuntimeSupervisor

    class _FakeBroker:
        def get_account(self):
            return BrokerAccount(
                equity=broker_equity,
                buying_power=broker_equity * 2,
                trading_blocked=False,
            )
        def list_open_orders(self): return []

    class _FakeMarketData:
        def get_stock_bars(self, **kwargs): return {}
        def get_daily_bars(self, **kwargs): return {}

    class _FakeTradingStatusStore:
        def load(self, **kwargs): return None

    class _FakePositionStore:
        def list_all(self, **kwargs): return []
        def replace_all(self, **kwargs): pass

    class _FakeStrategyFlagStore:
        def list_all(self, **kwargs): return []
        def load(self, *, strategy_name, **kwargs):
            if only_breakout and strategy_name != "breakout":
                from alpaca_bot.storage import StrategyFlag
                return StrategyFlag(
                    strategy_name=strategy_name,
                    trading_mode=settings.trading_mode,
                    strategy_version=settings.strategy_version,
                    enabled=False,
                    updated_at=_NOW,
                )
            return None

    class _FakeSessionStateStore:
        def load(self, **kwargs): return None
        def save(self, state=None, **kwargs): pass
        def list_by_session(self, **kwargs): return []

    class _FakeWatchlistStore:
        def list_enabled(self, *args): return ["AAPL", "MSFT"]
        def list_ignored(self, *args): return []

    _order_store = order_store or _RecordingOrderStore()
    _weight_store = weight_store
    _option_order_store = option_order_store

    class _FakeRuntimeContext:
        connection = _FakeConn()
        store_lock = None
        order_store = _order_store
        strategy_weight_store = _weight_store
        option_order_store = _option_order_store
        trading_status_store = _FakeTradingStatusStore()
        position_store = _FakePositionStore()
        daily_session_state_store = _FakeSessionStateStore()
        audit_event_store = _RecordingAuditStore()
        strategy_flag_store = _FakeStrategyFlagStore()
        watchlist_store = _FakeWatchlistStore()
        def commit(self): pass

    _runner = cycle_runner or (lambda **kwargs: SimpleNamespace(intents=[]))

    supervisor = RuntimeSupervisor(
        settings=settings,
        runtime=_FakeRuntimeContext(),
        broker=_FakeBroker(),
        market_data=_FakeMarketData(),
        stream=None,
        close_runtime_fn=lambda _: None,
        connection_checker=lambda _: True,
        cycle_runner=_runner,
        cycle_intent_executor=lambda **kwargs: SimpleNamespace(
            submitted_exit_count=0, failed_exit_count=0
        ),
        order_dispatcher=order_dispatcher or (lambda **kwargs: {"submitted_count": 0}),
    )
    return supervisor, _FakeRuntimeContext


def test_effective_equity_uses_confidence_score() -> None:
    """Supervisor passes account.equity * confidence_score to cycle_runner, not weight-shrunk equity."""
    settings = _make_settings(CONFIDENCE_FLOOR="0.0")
    runner = _CapturingCycleRunner()
    supervisor, _ = _make_supervisor(
        settings=settings,
        broker_equity=10_000.0,
        cycle_runner=runner,
        only_breakout=True,
    )
    # Pre-populate session state to bypass session-open DB writes.
    # Breakout is the only strategy with sharpe=2.0 → confidence score = 1.0 (sole positive Sharpe).
    supervisor._session_equity_baseline[_SESSION_DATE] = 10_000.0
    supervisor._session_capital_weights[_SESSION_DATE] = {"breakout": 0.6}
    supervisor._session_sharpes[_SESSION_DATE] = {"breakout": 2.0}

    supervisor.run_cycle_once(now=lambda: _NOW)

    assert runner.captured_strategy_names == ["breakout"]
    # confidence_score = 1.0 (sole positive-Sharpe strategy) → full equity, not weight-shrunk
    assert abs(runner.captured_equities[0] - 10_000.0) < 1e-6


def test_effective_equity_uses_floor_when_sharpe_is_zero() -> None:
    """When strategy Sharpe is zero (no trade history), equity is scaled by the confidence floor."""
    settings = _make_settings(CONFIDENCE_FLOOR="0.25")
    runner = _CapturingCycleRunner()
    supervisor, _ = _make_supervisor(
        settings=settings,
        broker_equity=10_000.0,
        cycle_runner=runner,
        only_breakout=True,
    )
    # Zero Sharpe → no positive history → confidence score = floor = 0.25
    supervisor._session_equity_baseline[_SESSION_DATE] = 10_000.0
    supervisor._session_capital_weights[_SESSION_DATE] = {"breakout": 1.0}
    supervisor._session_sharpes[_SESSION_DATE] = {"breakout": 0.0}

    supervisor.run_cycle_once(now=lambda: _NOW)

    # confidence_score = 0.25 (floor) → 10000 * 0.25 = 2500
    assert runner.captured_strategy_names == ["breakout"]
    assert abs(runner.captured_equities[0] - 2_500.0) < 1e-6


def test_update_session_weights_uses_cached_db_weights_on_crash_recovery() -> None:
    """If today's weights already exist in DB, return them without recomputing."""
    from alpaca_bot.storage import StrategyWeight

    cached_weight = StrategyWeight(
        strategy_name="breakout",
        trading_mode=TradingMode.PAPER,
        strategy_version="v1",
        weight=0.7,
        sharpe=2.1,
        computed_at=datetime(_SESSION_DATE.year, _SESSION_DATE.month, _SESSION_DATE.day,
                             10, 0, 0, tzinfo=timezone.utc),
    )
    order_store = _RecordingOrderStore()
    weight_store = _FakeWeightStore(preloaded=[cached_weight])

    settings = _make_settings()
    supervisor, _ = _make_supervisor(
        settings=settings,
        weight_store=weight_store,
        order_store=order_store,
    )

    result = supervisor._update_session_weights(_SESSION_DATE)

    assert result.weights == {"breakout": 0.7}
    assert result.sharpes == {"breakout": 2.1}
    # order_store.list_trade_pnl_by_strategy was never called (returned cached weights)
    assert weight_store.upserted == []


def test_update_session_weights_computes_and_stores_when_no_cache() -> None:
    """When DB has no today's weights, compute from trade rows and store."""
    from datetime import timedelta
    order_store = _RecordingOrderStore(
        pnl_rows=[
            {"strategy_name": "breakout", "exit_date": _SESSION_DATE - timedelta(days=1), "pnl": 100.0}
            for _ in range(5)
        ]
    )
    weight_store = _FakeWeightStore(preloaded=[])

    settings = _make_settings()
    supervisor, _ = _make_supervisor(
        settings=settings,
        weight_store=weight_store,
        order_store=order_store,
        only_breakout=True,
    )

    result = supervisor._update_session_weights(_SESSION_DATE)

    assert "breakout" in result.weights
    assert len(weight_store.upserted) == 1


def test_update_session_weights_writes_audit_event() -> None:
    """_update_session_weights always writes an AuditEvent with the computed weights."""
    order_store = _RecordingOrderStore(pnl_rows=[])
    weight_store = _FakeWeightStore(preloaded=[])

    settings = _make_settings()
    supervisor, FakeRuntime = _make_supervisor(
        settings=settings,
        weight_store=weight_store,
        order_store=order_store,
        only_breakout=True,
    )

    supervisor._update_session_weights(_SESSION_DATE)

    audit_store = supervisor.runtime.audit_event_store
    events = [e for e in audit_store.appended if e.event_type == "strategy_weights_updated"]
    assert len(events) == 1
    assert "breakout" in events[0].payload


def test_update_session_weights_uses_all_time_start_date() -> None:
    """Weight computation must use start_date=date(2000,1,1) for all-time Sharpe.

    Before the fix, start_date = end_date - timedelta(days=28) — only 28
    calendar days of trades feed into the Sharpe computation, so long-term
    strategy performance has no influence on capital allocation.
    """
    captured_kwargs: list[dict] = []

    class _CapturingOrderStore(_RecordingOrderStore):
        def list_trade_pnl_by_strategy(self, **kwargs):
            captured_kwargs.append(dict(kwargs))
            return []

    order_store = _CapturingOrderStore()
    weight_store = _FakeWeightStore(preloaded=[])

    settings = _make_settings()
    supervisor, _ = _make_supervisor(
        settings=settings,
        weight_store=weight_store,
        order_store=order_store,
        only_breakout=True,
    )

    supervisor._update_session_weights(_SESSION_DATE)

    assert len(captured_kwargs) == 1, "list_trade_pnl_by_strategy must be called exactly once"
    assert captured_kwargs[0]["start_date"] == date(2000, 1, 1), (
        f"Expected all-time start_date=date(2000,1,1), got {captured_kwargs[0]['start_date']}. "
        "The 28-day rolling window has not been changed to all-time."
    )


def test_run_cycle_once_report_includes_account_equity() -> None:
    """run_cycle_once() must populate account_equity on the returned report."""
    settings = _make_settings(CONFIDENCE_FLOOR="0.0")
    supervisor, _ = _make_supervisor(
        settings=settings,
        broker_equity=12_345.67,
        only_breakout=True,
    )
    supervisor._session_equity_baseline[_SESSION_DATE] = 12_345.67
    supervisor._session_capital_weights[_SESSION_DATE] = {"breakout": 1.0}
    supervisor._session_sharpes[_SESSION_DATE] = {"breakout": 1.5}

    report = supervisor.run_cycle_once(now=lambda: _NOW)

    assert abs(report.account_equity - 12_345.67) < 1e-6


def test_update_session_weights_includes_option_names_when_options_enabled() -> None:
    """When enable_options_trading=True, option strategy names join the weight pool."""
    from alpaca_bot.strategy import OPTION_STRATEGY_NAMES

    captured_names: list[list[str]] = []

    import alpaca_bot.runtime.supervisor as _sup_mod
    from alpaca_bot.risk.weighting import compute_strategy_weights as _orig

    def capturing_compute(trade_rows, active_names):
        captured_names.append(list(active_names))
        return _orig(trade_rows, active_names)

    original = _sup_mod.compute_strategy_weights
    _sup_mod.compute_strategy_weights = capturing_compute
    try:
        settings = _make_settings(ENABLE_OPTIONS_TRADING="true")
        supervisor, _ = _make_supervisor(settings=settings, weight_store=_FakeWeightStore(preloaded=[]), only_breakout=False)
        supervisor._update_session_weights(_SESSION_DATE)
    finally:
        _sup_mod.compute_strategy_weights = original

    assert len(captured_names) == 1
    pool = set(captured_names[0])
    for opt_name in OPTION_STRATEGY_NAMES:
        assert opt_name in pool, f"Option strategy {opt_name!r} missing from weight pool"


def test_update_session_weights_excludes_option_names_when_options_disabled() -> None:
    """When enable_options_trading=False, option strategy names must NOT join the weight pool."""
    from alpaca_bot.strategy import OPTION_STRATEGY_NAMES

    captured_names: list[list[str]] = []

    import alpaca_bot.runtime.supervisor as _sup_mod
    from alpaca_bot.risk.weighting import compute_strategy_weights as _orig

    def capturing_compute(trade_rows, active_names):
        captured_names.append(list(active_names))
        return _orig(trade_rows, active_names)

    original = _sup_mod.compute_strategy_weights
    _sup_mod.compute_strategy_weights = capturing_compute
    try:
        settings = _make_settings(ENABLE_OPTIONS_TRADING="false")
        supervisor, _ = _make_supervisor(settings=settings, weight_store=_FakeWeightStore(preloaded=[]), only_breakout=False)
        supervisor._update_session_weights(_SESSION_DATE)
    finally:
        _sup_mod.compute_strategy_weights = original

    assert len(captured_names) == 1
    pool = set(captured_names[0])
    for opt_name in OPTION_STRATEGY_NAMES:
        assert opt_name not in pool, f"Option strategy {opt_name!r} must not be in weight pool when options disabled"


def test_update_session_weights_bypasses_cache_when_option_names_added() -> None:
    """Stale cache (equity-only) is bypassed when options are enabled — the set-equality check forces recompute."""
    from alpaca_bot.storage import StrategyWeight
    from alpaca_bot.strategy import OPTION_STRATEGY_NAMES, STRATEGY_REGISTRY

    # Fresh weights for today, but only for equity strategies (12 option names are missing)
    equity_weight = 1.0 / len(STRATEGY_REGISTRY)
    equity_cache = [
        StrategyWeight(
            strategy_name=name,
            trading_mode=TradingMode.PAPER,
            strategy_version="v1",
            weight=equity_weight,
            sharpe=0.0,
            computed_at=datetime(_SESSION_DATE.year, _SESSION_DATE.month, _SESSION_DATE.day, 9, 30, tzinfo=timezone.utc),
        )
        for name in STRATEGY_REGISTRY
    ]

    captured_names: list[list[str]] = []

    import alpaca_bot.runtime.supervisor as _sup_mod
    from alpaca_bot.risk.weighting import compute_strategy_weights as _orig

    def capturing_compute(trade_rows, active_names):
        captured_names.append(list(active_names))
        return _orig(trade_rows, active_names)

    original = _sup_mod.compute_strategy_weights
    _sup_mod.compute_strategy_weights = capturing_compute
    try:
        settings = _make_settings(ENABLE_OPTIONS_TRADING="true")
        supervisor, _ = _make_supervisor(
            settings=settings,
            weight_store=_FakeWeightStore(preloaded=equity_cache),
            only_breakout=False,
        )
        supervisor._update_session_weights(_SESSION_DATE)
    finally:
        _sup_mod.compute_strategy_weights = original

    assert len(captured_names) == 1, (
        "compute_strategy_weights must be called — equity-only cache must not satisfy the 23-strategy active set"
    )
    pool = set(captured_names[0])
    for opt_name in OPTION_STRATEGY_NAMES:
        assert opt_name in pool, f"Option strategy {opt_name!r} missing from recomputed weight pool"


def test_confidence_settings_defaults() -> None:
    base = {
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
        "MAX_POSITION_PCT": "0.015",
        "MAX_OPEN_POSITIONS": "20",
        "DAILY_LOSS_LIMIT_PCT": "0.01",
        "STOP_LIMIT_BUFFER_PCT": "0.001",
        "BREAKOUT_STOP_BUFFER_PCT": "0.001",
        "ENTRY_STOP_PRICE_BUFFER": "0.01",
        "ENTRY_WINDOW_START": "10:00",
        "ENTRY_WINDOW_END": "15:30",
        "FLATTEN_TIME": "15:45",
        "PER_SYMBOL_LOSS_LIMIT_PCT": "0.0",
    }
    from alpaca_bot.config import Settings
    s = Settings.from_env(base)
    assert s.confidence_floor == 0.25
    assert s.floor_raise_step == 0.10
    assert s.drawdown_raise_pct == 0.05
    assert s.losing_streak_n == 3
    assert s.vol_raise_threshold == 0.025


def test_confidence_floor_validation_rejects_out_of_range() -> None:
    from alpaca_bot.config import Settings
    import pytest
    base = {
        "TRADING_MODE": "paper", "ENABLE_LIVE_TRADING": "false",
        "STRATEGY_VERSION": "v1", "DATABASE_URL": "x",
        "MARKET_DATA_FEED": "sip", "SYMBOLS": "AAPL",
        "DAILY_SMA_PERIOD": "20", "BREAKOUT_LOOKBACK_BARS": "20",
        "RELATIVE_VOLUME_LOOKBACK_BARS": "20", "RELATIVE_VOLUME_THRESHOLD": "1.5",
        "ENTRY_TIMEFRAME_MINUTES": "15", "RISK_PER_TRADE_PCT": "0.0025",
        "MAX_POSITION_PCT": "0.015", "MAX_OPEN_POSITIONS": "20",
        "DAILY_LOSS_LIMIT_PCT": "0.01", "STOP_LIMIT_BUFFER_PCT": "0.001",
        "BREAKOUT_STOP_BUFFER_PCT": "0.001", "ENTRY_STOP_PRICE_BUFFER": "0.01",
        "ENTRY_WINDOW_START": "10:00", "ENTRY_WINDOW_END": "15:30",
        "FLATTEN_TIME": "15:45", "PER_SYMBOL_LOSS_LIMIT_PCT": "0.0",
        "CONFIDENCE_FLOOR": "1.5",  # invalid — > 1.0
    }
    with pytest.raises(ValueError, match="CONFIDENCE_FLOOR"):
        Settings.from_env(base)


def test_effective_equity_uses_full_account_equity_scaled_by_confidence() -> None:
    """With confidence score, sizing should use account.equity * confidence_score,
    not account.equity * strategy_weight (the old weight-shrunk approach)."""
    from alpaca_bot.storage import StrategyWeight

    recorded_equities: list[float] = []

    def fake_cycle_runner(*, equity, **kwargs):
        recorded_equities.append(equity)
        return SimpleNamespace(intents=[])

    settings = _make_settings(
        MAX_POSITION_PCT="0.015",
        MAX_OPEN_POSITIONS="3",
        CONFIDENCE_FLOOR="0.0",
    )
    # Only breakout is active. Sharpe=2.0 → sole strategy
    # gets confidence score 1.0 (single-strategy → raw=1.0 when only positive Sharpe).
    # Old behavior: equity = 10000 * weight (e.g. 0.80 → 8000).
    # New behavior: equity = 10000 * 1.0 = 10000 (full equity).
    preloaded_weights = [
        StrategyWeight(
            strategy_name="breakout",
            trading_mode=TradingMode.PAPER,
            strategy_version="v1",
            weight=0.80,
            sharpe=2.0,
            computed_at=datetime(_SESSION_DATE.year, _SESSION_DATE.month, _SESSION_DATE.day, 0, 0, tzinfo=timezone.utc),
        ),
    ]
    weight_store = _FakeWeightStore(preloaded=preloaded_weights)
    supervisor, _ = _make_supervisor(
        settings=settings,
        weight_store=weight_store,
        cycle_runner=fake_cycle_runner,
    )
    supervisor.run_cycle_once(now=lambda: _NOW)

    assert len(recorded_equities) >= 1
    # Key assertion: full equity (10000), not weight-shrunk (8000)
    assert max(recorded_equities) == pytest.approx(10000.0)


def test_low_confidence_strategy_receives_floor_equity() -> None:
    """A sole strategy with no Sharpe history should receive floor-scaled equity."""
    entries_disabled_flags: list[bool] = []
    recorded_equities: list[float] = []

    def fake_cycle_runner(*, entries_disabled, equity, **kwargs):
        entries_disabled_flags.append(entries_disabled)
        recorded_equities.append(equity)
        return SimpleNamespace(intents=[])

    settings = _make_settings(
        CONFIDENCE_FLOOR="0.60",
        MAX_OPEN_POSITIONS="3",
    )
    # No preloaded weights → _update_session_weights computes equal weights,
    # all sharpes default to 0.0 → all-zero case → all strategies get floor (0.60).
    # Single breakout strategy → not disabled (0.60 >= floor 0.60 → in score dict).
    supervisor, _ = _make_supervisor(settings=settings, cycle_runner=fake_cycle_runner)
    supervisor.run_cycle_once(now=lambda: _NOW)

    assert all(not d for d in entries_disabled_flags), (
        f"Unexpected entries disabled: {entries_disabled_flags}"
    )
    # Floor is 0.60 with all-zero Sharpes (no history) → confidence_score = 0.60
    # effective_equity = 10000 * 0.60 = 6000
    assert recorded_equities, "cycle_runner was never called"
    assert max(recorded_equities) == pytest.approx(6000.0)


def test_strategy_below_confidence_floor_has_entries_disabled() -> None:
    """When a strategy's score is None (below floor), entries must be disabled."""
    entries_disabled_flags: list[bool] = []
    recorded_equities: list[float] = []

    def fake_cycle_runner(*, entries_disabled, equity, **kwargs):
        entries_disabled_flags.append(entries_disabled)
        recorded_equities.append(equity)
        return SimpleNamespace(intents=[])

    settings = _make_settings(
        CONFIDENCE_FLOOR="0.50",
        MAX_OPEN_POSITIONS="3",
    )
    supervisor, _ = _make_supervisor(
        settings=settings,
        cycle_runner=fake_cycle_runner,
    )

    # Pre-populate _session_capital_weights so the weight update block is skipped.
    # Also pre-populate _session_sharpes with an empty dict — breakout is absent,
    # so compute_confidence_scores({}, 0.50) returns {} (early-exit for empty sharpes).
    # Then session_confidence_scores.get("breakout") returns None, triggering the None branch.
    supervisor._session_equity_baseline[_SESSION_DATE] = 10_000.0
    supervisor._session_capital_weights[_SESSION_DATE] = {"breakout": 1.0}
    supervisor._session_sharpes[_SESSION_DATE] = {}  # breakout absent → None from get()

    supervisor.run_cycle_once(now=lambda: _NOW)

    assert len(entries_disabled_flags) >= 1, "cycle_runner was never called"
    assert entries_disabled_flags[0] is True, (
        "Strategy absent from confidence scores should have entries disabled"
    )
    # equity = account.equity * confidence_floor (fallback for None case)
    assert recorded_equities[0] == pytest.approx(10_000.0 * 0.50)


def test_losing_streak_exclusion_emits_excluded_audit_event() -> None:
    """When a strategy has >= LOSING_STREAK_N consecutive losing days, run_cycle_once emits
    strategy_confidence_excluded and adds the strategy to _strategy_streak_excluded."""
    settings = _make_settings(LOSING_STREAK_N="2")
    order_store = _RecordingOrderStore(pnl_rows=[
        {"strategy_name": "breakout", "exit_date": date(2026, 4, 30), "pnl": -50.0},
        {"strategy_name": "breakout", "exit_date": date(2026, 4, 29), "pnl": -30.0},
    ])
    supervisor, _ = _make_supervisor(
        settings=settings,
        order_store=order_store,
        only_breakout=True,
    )
    supervisor._session_equity_baseline[_SESSION_DATE] = 10_000.0
    supervisor._session_capital_weights[_SESSION_DATE] = {"breakout": 1.0}
    supervisor._session_sharpes[_SESSION_DATE] = {"breakout": 2.0}

    supervisor.run_cycle_once(now=lambda: _NOW)

    audit_store = supervisor.runtime.audit_event_store
    excluded_events = [
        e for e in audit_store.appended if e.event_type == "strategy_confidence_excluded"
    ]
    assert len(excluded_events) == 1, (
        f"Expected 1 strategy_confidence_excluded event, got {len(excluded_events)}"
    )
    assert excluded_events[0].payload["strategy_name"] == "breakout"
    assert supervisor._strategy_streak_excluded == {"breakout"}


def test_losing_streak_excluded_strategy_blocked_from_dispatch() -> None:
    """A losing-streak-excluded strategy must appear in blocked_strategy_names passed to
    the order dispatcher, preventing pending entry orders from being submitted."""
    captured_dispatch_kwargs: list[dict] = []

    def capturing_dispatcher(**kwargs):
        captured_dispatch_kwargs.append(dict(kwargs))
        return {"submitted_count": 0}

    settings = _make_settings(LOSING_STREAK_N="2")
    order_store = _RecordingOrderStore(pnl_rows=[
        {"strategy_name": "breakout", "exit_date": date(2026, 4, 30), "pnl": -50.0},
        {"strategy_name": "breakout", "exit_date": date(2026, 4, 29), "pnl": -30.0},
    ])
    supervisor, _ = _make_supervisor(
        settings=settings,
        order_store=order_store,
        only_breakout=True,
        order_dispatcher=capturing_dispatcher,
    )
    supervisor._session_equity_baseline[_SESSION_DATE] = 10_000.0
    supervisor._session_capital_weights[_SESSION_DATE] = {"breakout": 1.0}
    supervisor._session_sharpes[_SESSION_DATE] = {"breakout": 2.0}

    supervisor.run_cycle_once(now=lambda: _NOW)

    assert len(captured_dispatch_kwargs) == 1, "order_dispatcher must be called exactly once"
    blocked = captured_dispatch_kwargs[0]["blocked_strategy_names"]
    assert "breakout" in blocked, (
        f"Expected 'breakout' in blocked_strategy_names, got: {blocked}"
    )


def test_losing_streak_restoration_emits_restored_audit_event() -> None:
    """When a previously-excluded strategy logs a winning day (streak broken), run_cycle_once
    emits strategy_confidence_restored and removes the strategy from _strategy_streak_excluded.
    No strategy_confidence_excluded event must fire in the same cycle."""
    settings = _make_settings(LOSING_STREAK_N="2")
    order_store = _RecordingOrderStore(pnl_rows=[
        {"strategy_name": "breakout", "exit_date": date(2026, 4, 30), "pnl": 25.0},
        {"strategy_name": "breakout", "exit_date": date(2026, 4, 29), "pnl": -30.0},
    ])
    supervisor, _ = _make_supervisor(
        settings=settings,
        order_store=order_store,
        only_breakout=True,
    )
    supervisor._session_equity_baseline[_SESSION_DATE] = 10_000.0
    supervisor._session_capital_weights[_SESSION_DATE] = {"breakout": 1.0}
    supervisor._session_sharpes[_SESSION_DATE] = {"breakout": 2.0}
    supervisor._strategy_streak_excluded = {"breakout"}

    supervisor.run_cycle_once(now=lambda: _NOW)

    audit_store = supervisor.runtime.audit_event_store
    restored_events = [
        e for e in audit_store.appended if e.event_type == "strategy_confidence_restored"
    ]
    excluded_events = [
        e for e in audit_store.appended if e.event_type == "strategy_confidence_excluded"
    ]
    assert len(restored_events) == 1, (
        f"Expected 1 strategy_confidence_restored event, got {len(restored_events)}"
    )
    assert restored_events[0].payload["strategy_name"] == "breakout"
    assert len(excluded_events) == 0, "No excluded event must fire in the same restoration cycle"
    assert supervisor._strategy_streak_excluded == set()


# ── option_chains_fetched audit event tests ───────────────────────────────────


class _FakeOptionChainAdapter:
    """Returns the provided chains dict on every call; empty list if symbol not found."""
    def __init__(self, chains_by_symbol: dict | None = None):
        self._chains = chains_by_symbol or {}

    def get_option_chain(self, symbol: str, settings):
        return self._chains.get(symbol, [])


def _make_supervisor_with_option_adapter(adapter):
    """Build a supervisor with an injected option chain adapter for audit event tests."""
    s = _make_settings()
    module = import_module("alpaca_bot.runtime.supervisor")
    RuntimeSupervisor = module.RuntimeSupervisor

    class _FakeBroker:
        def get_account(self):
            return BrokerAccount(equity=10_000.0, buying_power=20_000.0, trading_blocked=False)
        def list_open_orders(self): return []

    class _FakeMarketData:
        def get_stock_bars(self, **kwargs): return {}
        def get_daily_bars(self, **kwargs): return {}

    class _FakeConn2:
        def commit(self): pass
        def rollback(self): pass

    class _FakeTradingStatusStore:
        def load(self, **kwargs): return None

    class _FakePositionStore:
        def list_all(self, **kwargs): return []
        def replace_all(self, **kwargs): pass

    class _FakeStrategyFlagStore:
        def list_all(self, **kwargs): return []
        def load(self, *, strategy_name, **kwargs):
            from alpaca_bot.storage import StrategyFlag
            return StrategyFlag(
                strategy_name=strategy_name,
                trading_mode=s.trading_mode,
                strategy_version=s.strategy_version,
                enabled=False,
                updated_at=_NOW,
            )

    class _FakeSessionStateStore:
        def load(self, **kwargs): return None
        def save(self, state=None, **kwargs): pass
        def list_by_session(self, **kwargs): return []

    class _FakeWatchlistStore:
        def list_enabled(self, *args): return list(s.symbols)
        def list_ignored(self, *args): return []

    audit_store = _RecordingAuditStore()

    class _FakeRuntimeCtx:
        connection = _FakeConn2()
        store_lock = None
        order_store = _RecordingOrderStore()
        strategy_weight_store = None
        trading_status_store = _FakeTradingStatusStore()
        position_store = _FakePositionStore()
        daily_session_state_store = _FakeSessionStateStore()
        audit_event_store = audit_store
        strategy_flag_store = _FakeStrategyFlagStore()
        watchlist_store = _FakeWatchlistStore()
        def commit(self): pass

    runtime_ctx = _FakeRuntimeCtx()

    supervisor = RuntimeSupervisor(
        settings=s,
        runtime=runtime_ctx,
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
        option_chain_adapter=adapter,
    )
    # Pre-seed session state to bypass session-open DB writes
    supervisor._session_equity_baseline[_SESSION_DATE] = 10_000.0
    supervisor._session_capital_weights[_SESSION_DATE] = {}
    supervisor._session_sharpes[_SESSION_DATE] = {}
    return supervisor, runtime_ctx


def test_option_chains_fetched_event_emitted_when_adapter_set() -> None:
    """option_chains_fetched audit event is emitted once per cycle when adapter is configured."""
    adapter = _FakeOptionChainAdapter()  # returns [] for every symbol
    supervisor, ctx = _make_supervisor_with_option_adapter(adapter)

    supervisor.run_cycle_once(now=lambda: _NOW)

    fetched_events = [
        e for e in ctx.audit_event_store.appended
        if e.event_type == "option_chains_fetched"
    ]
    assert len(fetched_events) == 1


def test_option_chains_fetched_payload_shows_zero_when_chains_empty() -> None:
    """When all chain fetches return [], the payload contains 0 for every symbol."""
    adapter = _FakeOptionChainAdapter()  # returns [] for all symbols
    supervisor, ctx = _make_supervisor_with_option_adapter(adapter)

    supervisor.run_cycle_once(now=lambda: _NOW)

    fetched_events = [
        e for e in ctx.audit_event_store.appended
        if e.event_type == "option_chains_fetched"
    ]
    assert len(fetched_events) == 1
    payload = fetched_events[0].payload
    # _make_settings uses SYMBOLS="AAPL,MSFT"
    assert payload == {"AAPL": 0, "MSFT": 0}


def test_option_pnl_feeds_into_sharpe() -> None:
    """Option strategies with closed profitable trades produce non-zero Sharpe via _update_session_weights."""
    # 6 profitable trades for breakout_calls on distinct dates (min_trades=5 threshold requires >=5)
    option_rows = [
        {"strategy_name": "breakout_calls", "exit_date": date(2026, 1, d), "pnl": 150.0}
        for d in range(1, 7)
    ]
    settings = _make_settings(ENABLE_OPTIONS_TRADING="true")
    supervisor, _ = _make_supervisor(
        settings=settings,
        weight_store=_FakeWeightStore(preloaded=[]),
        order_store=_RecordingOrderStore(pnl_rows=[]),
        option_order_store=_RecordingOptionOrderStore(pnl_rows=option_rows),
        only_breakout=False,
    )
    result = supervisor._update_session_weights(_SESSION_DATE)
    assert result.sharpes.get("breakout_calls", 0.0) > 0.0, (
        "breakout_calls must earn a positive Sharpe when it has 6 profitable closed option trades"
    )
