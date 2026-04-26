from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import hmac
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from alpaca_bot.config import Settings, TradingMode
from alpaca_bot.storage import AuditEvent, StrategyFlag, StrategyFlagStore
from alpaca_bot.storage.db import ConnectionProtocol
from alpaca_bot.strategy import STRATEGY_REGISTRY
from alpaca_bot.web.app import create_app
from alpaca_bot.web.service import load_dashboard_snapshot


def _csrf_token(client: TestClient, action: str) -> str:
    """Compute a valid CSRF token for the given TestClient's app and action."""
    secret: bytes = client.app.state.csrf_secret
    return hmac.HMAC(secret, f"\n{action}".encode(), hashlib.sha256).hexdigest()


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def make_settings(**overrides: str) -> Settings:
    values = {
        "TRADING_MODE": "paper",
        "ENABLE_LIVE_TRADING": "false",
        "STRATEGY_VERSION": "v1-breakout",
        "DATABASE_URL": "postgresql://example",
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
    values.update(overrides)
    return Settings.from_env(values)


class FakeCursor:
    def __init__(self, connection: "FakeConnection") -> None:
        self._connection = connection

    def execute(self, sql: str, params=None) -> None:
        self._connection.executed.append((sql, params))

    def fetchone(self):
        if not self._connection.responses:
            return None
        return self._connection.responses.pop(0)

    def fetchall(self):
        if not self._connection.responses:
            return []
        response = self._connection.responses.pop(0)
        return response if isinstance(response, list) else [response]


class FakeConnection:
    def __init__(self, responses=()) -> None:
        self.responses = list(responses)
        self.executed: list[tuple] = []
        self.closed = False

    def cursor(self) -> FakeCursor:
        return FakeCursor(self)

    def commit(self) -> None:
        pass

    def close(self) -> None:
        self.closed = True


def make_audit_store_factory() -> tuple[SimpleNamespace, list]:
    appended: list[AuditEvent] = []
    store = SimpleNamespace(
        append=lambda event: appended.append(event),
        list_recent=lambda **_: [],
        load_latest=lambda **_: None,
        list_by_event_types=lambda **_: [],
    )
    return store, appended


def make_empty_stores() -> dict:
    audit_store, _ = make_audit_store_factory()
    return dict(
        trading_status_store=SimpleNamespace(load=lambda **_: None),
        daily_session_state_store=SimpleNamespace(load=lambda **_: None),
        position_store=SimpleNamespace(list_all=lambda **_: []),
        order_store=SimpleNamespace(
            list_by_status=lambda **_: [],
            list_recent=lambda **_: [],
            list_closed_trades=lambda **_: [],
        ),
        audit_event_store=audit_store,
    )


# ---------------------------------------------------------------------------
# StrategyFlagStore — save / load / list_all
# ---------------------------------------------------------------------------


def test_strategy_flag_store_save_records_sql() -> None:
    now = datetime(2026, 4, 25, 14, 0, tzinfo=timezone.utc)
    conn = FakeConnection()
    store = StrategyFlagStore(conn)  # type: ignore[arg-type]
    flag = StrategyFlag(
        strategy_name="breakout",
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        enabled=True,
        updated_at=now,
    )
    store.save(flag)
    assert len(conn.executed) == 1
    sql, params = conn.executed[0]
    assert "strategy_flags" in sql
    assert params[0] == "breakout"
    assert params[1] == "paper"
    assert params[3] is True


def test_strategy_flag_store_load_returns_none_when_missing() -> None:
    conn = FakeConnection(responses=[None])
    store = StrategyFlagStore(conn)  # type: ignore[arg-type]
    result = store.load(
        strategy_name="breakout",
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
    )
    assert result is None


def test_strategy_flag_store_load_returns_flag_from_row() -> None:
    now = datetime(2026, 4, 25, 14, 0, tzinfo=timezone.utc)
    conn = FakeConnection(responses=[("breakout", "paper", "v1-breakout", True, now)])
    store = StrategyFlagStore(conn)  # type: ignore[arg-type]
    result = store.load(
        strategy_name="breakout",
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
    )
    assert result is not None
    assert result.strategy_name == "breakout"
    assert result.trading_mode is TradingMode.PAPER
    assert result.strategy_version == "v1-breakout"
    assert result.enabled is True
    assert result.updated_at == now


def test_strategy_flag_store_list_all_filters_by_mode_and_version() -> None:
    now = datetime(2026, 4, 25, 14, 0, tzinfo=timezone.utc)
    conn = FakeConnection(
        responses=[[("breakout", "paper", "v1-breakout", False, now)]]
    )
    store = StrategyFlagStore(conn)  # type: ignore[arg-type]
    result = store.list_all(
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
    )
    assert len(result) == 1
    assert result[0].strategy_name == "breakout"
    assert result[0].enabled is False


def test_strategy_flag_store_list_all_returns_empty_list_when_no_rows() -> None:
    conn = FakeConnection(responses=[[]])
    store = StrategyFlagStore(conn)  # type: ignore[arg-type]
    result = store.list_all(
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
    )
    assert result == []


# ---------------------------------------------------------------------------
# RuntimeSupervisor._resolve_signal_evaluator
# ---------------------------------------------------------------------------


def _make_supervisor(settings, *, strategy_flag_store=None):
    from importlib import import_module

    module = import_module("alpaca_bot.runtime.supervisor")
    RuntimeSupervisor = module.RuntimeSupervisor

    runtime = SimpleNamespace(
        position_store=SimpleNamespace(
            list_all=lambda **_: [],
            replace_all=lambda **_: None,
        ),
        order_store=SimpleNamespace(
            save=lambda _: None,
            list_by_status=lambda **_: [],
            list_pending_submit=lambda **_: [],
            daily_realized_pnl=lambda **_: 0.0,
        ),
        trading_status_store=SimpleNamespace(load=lambda **_: None),
        audit_event_store=SimpleNamespace(append=lambda _: None),
        daily_session_state_store=SimpleNamespace(load=lambda **_: None, save=lambda _: None),
        strategy_flag_store=strategy_flag_store,
    )
    stream = SimpleNamespace(
        subscribe_trade_updates=lambda _handler: None,
        run=lambda: None,
        stop=lambda: None,
    )
    return RuntimeSupervisor(
        settings=settings,
        runtime=runtime,  # type: ignore[arg-type]
        broker=SimpleNamespace(),  # type: ignore[arg-type]
        market_data=SimpleNamespace(),  # type: ignore[arg-type]
        stream=stream,  # type: ignore[arg-type]
        close_runtime_fn=lambda _runtime: None,
        connection_checker=lambda _conn: True,
    )


def test_supervisor_returns_default_evaluator_when_no_store() -> None:
    from alpaca_bot.strategy.breakout import evaluate_breakout_signal

    settings = make_settings()
    supervisor = _make_supervisor(settings, strategy_flag_store=None)
    active = supervisor._resolve_active_strategies()
    assert len(active) >= 1
    assert any(evaluator is evaluate_breakout_signal for _, evaluator in active)


def test_supervisor_returns_evaluator_when_flag_row_missing() -> None:
    """load() returns None (no row) → treat as enabled → include in active list."""
    from alpaca_bot.strategy.breakout import evaluate_breakout_signal

    settings = make_settings()
    store = SimpleNamespace(load=lambda **_: None)
    supervisor = _make_supervisor(settings, strategy_flag_store=store)
    active = supervisor._resolve_active_strategies()
    assert any(evaluator is evaluate_breakout_signal for _, evaluator in active)


def test_supervisor_returns_evaluator_when_flag_enabled() -> None:
    from alpaca_bot.strategy.breakout import evaluate_breakout_signal

    settings = make_settings()
    now = datetime.now(timezone.utc)
    flag = StrategyFlag(
        strategy_name="breakout",
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        enabled=True,
        updated_at=now,
    )
    store = SimpleNamespace(load=lambda **_: flag)
    supervisor = _make_supervisor(settings, strategy_flag_store=store)
    active = supervisor._resolve_active_strategies()
    assert any(evaluator is evaluate_breakout_signal for _, evaluator in active)


def test_supervisor_returns_noop_when_all_strategies_disabled() -> None:
    settings = make_settings()
    now = datetime.now(timezone.utc)
    flag = StrategyFlag(
        strategy_name="breakout",
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        enabled=False,
        updated_at=now,
    )
    store = SimpleNamespace(load=lambda **_: flag)
    supervisor = _make_supervisor(settings, strategy_flag_store=store)
    active = supervisor._resolve_active_strategies()
    # All strategies explicitly disabled → no active strategies
    assert len(active) == 0


# ---------------------------------------------------------------------------
# run_cycle — signal_evaluator forwarded to engine
# ---------------------------------------------------------------------------


def test_run_cycle_passes_signal_evaluator_to_engine() -> None:
    from alpaca_bot.runtime.cycle import run_cycle
    from alpaca_bot.config import TradingMode

    captured: list = []

    def fake_evaluate_cycle(**kwargs):
        captured.append(kwargs.get("signal_evaluator"))
        return SimpleNamespace(intents=[])

    settings = make_settings()
    runtime = SimpleNamespace(
        order_store=SimpleNamespace(save=lambda _: None),
        audit_event_store=SimpleNamespace(append=lambda _: None),
    )
    sentinel_evaluator = lambda **_: None  # noqa: E731

    import alpaca_bot.runtime.cycle as cycle_module
    original = cycle_module.evaluate_cycle
    cycle_module.evaluate_cycle = fake_evaluate_cycle
    try:
        run_cycle(
            settings=settings,
            runtime=runtime,  # type: ignore[arg-type]
            now=datetime.now(timezone.utc),
            equity=100_000.0,
            intraday_bars_by_symbol={},
            daily_bars_by_symbol={},
            open_positions=[],
            working_order_symbols=set(),
            traded_symbols_today=set(),
            entries_disabled=False,
            signal_evaluator=sentinel_evaluator,  # type: ignore[arg-type]
        )
    finally:
        cycle_module.evaluate_cycle = original

    assert len(captured) == 1
    assert captured[0] is sentinel_evaluator


# ---------------------------------------------------------------------------
# load_dashboard_snapshot — strategy_flags field
# ---------------------------------------------------------------------------


def test_load_dashboard_snapshot_includes_strategy_flags_from_store() -> None:
    settings = make_settings()
    now = datetime(2026, 4, 25, 14, 0, tzinfo=timezone.utc)
    flag = StrategyFlag(
        strategy_name="breakout",
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        enabled=False,
        updated_at=now,
    )
    flag_store = SimpleNamespace(list_all=lambda **_: [flag])
    stores = make_empty_stores()
    stores["strategy_flag_store"] = flag_store

    snapshot = load_dashboard_snapshot(
        settings=settings,
        connection=SimpleNamespace(),
        now=now,
        **stores,
    )

    assert len(snapshot.strategy_flags) == len(STRATEGY_REGISTRY)
    name, returned_flag = snapshot.strategy_flags[0]
    assert name == "breakout"
    assert returned_flag is flag
    assert returned_flag.enabled is False


def test_load_dashboard_snapshot_sets_none_for_missing_flag_row() -> None:
    settings = make_settings()
    now = datetime(2026, 4, 25, 14, 0, tzinfo=timezone.utc)
    flag_store = SimpleNamespace(list_all=lambda **_: [])
    stores = make_empty_stores()
    stores["strategy_flag_store"] = flag_store

    snapshot = load_dashboard_snapshot(
        settings=settings,
        connection=SimpleNamespace(),
        now=now,
        **stores,
    )

    assert snapshot.strategy_flags == [
        ("breakout", None),
        ("momentum", None),
        ("orb", None),
        ("high_watermark", None),
        ("ema_pullback", None),
    ]


# ---------------------------------------------------------------------------
# Toggle endpoint — /strategies/{name}/toggle
# ---------------------------------------------------------------------------


def _make_toggle_app(
    *,
    saved_flags: list,
    appended_events: list,
    initial_flag: StrategyFlag | None = None,
    settings_overrides: dict | None = None,
) -> TestClient:
    settings = make_settings(**(settings_overrides or {}))

    def flag_store_factory(_conn):
        return SimpleNamespace(
            load=lambda **_: initial_flag,
            save=lambda flag, **_: saved_flags.append(flag),
        )

    def audit_store_factory(_conn):
        return SimpleNamespace(
            append=lambda event, **_: appended_events.append(event),
            list_recent=lambda **_: [],
            load_latest=lambda **_: None,
            list_by_event_types=lambda **_: [],
        )

    # Dashboard data stores (needed for the redirect-follow GET /)
    def ts_factory(_conn):
        return SimpleNamespace(load=lambda **_: None)

    def pos_factory(_conn):
        return SimpleNamespace(list_all=lambda **_: [])

    def order_factory(_conn):
        return SimpleNamespace(
            list_by_status=lambda **_: [],
            list_recent=lambda **_: [],
            list_closed_trades=lambda **_: [],
        )

    def session_factory(_conn):
        return SimpleNamespace(load=lambda **_: None)

    app = create_app(
        settings=settings,
        connect_postgres_fn=lambda _url: FakeConnection(),
        strategy_flag_store_factory=flag_store_factory,
        audit_event_store_factory=audit_store_factory,
        trading_status_store_factory=ts_factory,
        position_store_factory=pos_factory,
        order_store_factory=order_factory,
        daily_session_state_store_factory=session_factory,
    )
    return TestClient(app, follow_redirects=False)


def test_toggle_endpoint_disables_enabled_strategy() -> None:
    saved_flags: list[StrategyFlag] = []
    appended_events: list[AuditEvent] = []
    client = _make_toggle_app(
        saved_flags=saved_flags,
        appended_events=appended_events,
        initial_flag=None,  # None = currently enabled (default)
    )

    response = client.post(
        "/strategies/breakout/toggle",
        data={"_csrf_token": _csrf_token(client, "toggle")},
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/"
    assert len(saved_flags) == 1
    assert saved_flags[0].strategy_name == "breakout"
    assert saved_flags[0].enabled is False
    assert len(appended_events) == 1
    assert appended_events[0].event_type == "strategy_flag_changed"
    assert appended_events[0].payload["strategy_name"] == "breakout"
    assert appended_events[0].payload["enabled"] is False


def test_toggle_endpoint_enables_disabled_strategy() -> None:
    now = datetime.now(timezone.utc)
    saved_flags: list[StrategyFlag] = []
    appended_events: list[AuditEvent] = []
    disabled_flag = StrategyFlag(
        strategy_name="breakout",
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        enabled=False,
        updated_at=now,
    )
    client = _make_toggle_app(
        saved_flags=saved_flags,
        appended_events=appended_events,
        initial_flag=disabled_flag,
    )

    response = client.post(
        "/strategies/breakout/toggle",
        data={"_csrf_token": _csrf_token(client, "toggle")},
    )

    assert response.status_code == 303
    assert saved_flags[0].enabled is True


def test_toggle_endpoint_returns_404_for_unknown_strategy() -> None:
    saved_flags: list = []
    appended_events: list = []
    client = _make_toggle_app(saved_flags=saved_flags, appended_events=appended_events)

    response = client.post(
        "/strategies/nonexistent_xyz/toggle",
        data={"_csrf_token": _csrf_token(client, "toggle")},
    )

    assert response.status_code == 404
    assert saved_flags == []
    assert appended_events == []


def test_toggle_endpoint_redirects_to_login_when_auth_required_and_no_session() -> None:
    saved_flags: list = []
    appended_events: list = []
    from alpaca_bot.web.auth import hash_password

    client = _make_toggle_app(
        saved_flags=saved_flags,
        appended_events=appended_events,
        settings_overrides={
            "DASHBOARD_AUTH_ENABLED": "true",
            "DASHBOARD_AUTH_USERNAME": "operator@example.com",
            "DASHBOARD_AUTH_PASSWORD_HASH": hash_password(
                "secret",
                salt=bytes.fromhex("000102030405060708090a0b0c0d0e0f"),
            ),
        },
    )

    response = client.post("/strategies/breakout/toggle")

    assert response.status_code == 303
    assert "/login" in response.headers["location"]
    assert saved_flags == []
    assert appended_events == []


def test_toggle_endpoint_returns_403_for_bad_csrf_token() -> None:
    """A POST with a wrong CSRF token must be rejected with 403 regardless of auth mode."""
    saved_flags: list = []
    appended_events: list = []
    client = _make_toggle_app(saved_flags=saved_flags, appended_events=appended_events)

    response = client.post(
        "/strategies/breakout/toggle",
        data={"_csrf_token": "bad-token"},
    )

    assert response.status_code == 403
    assert saved_flags == []


def test_toggle_endpoint_returns_403_when_csrf_token_missing() -> None:
    """A POST with no CSRF token must be rejected with 403."""
    saved_flags: list = []
    appended_events: list = []
    client = _make_toggle_app(saved_flags=saved_flags, appended_events=appended_events)

    response = client.post("/strategies/breakout/toggle")

    assert response.status_code == 403
    assert saved_flags == []
