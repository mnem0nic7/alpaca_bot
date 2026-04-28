from __future__ import annotations

from base64 import b64encode
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

from fastapi.testclient import TestClient

from alpaca_bot.config import TradingMode
from alpaca_bot.storage import (
    DailySessionState,
    OrderRecord,
    PositionRecord,
    TradingStatus,
    TradingStatusValue,
)
from alpaca_bot.config import Settings
from alpaca_bot.web.auth import hash_password
from alpaca_bot.web.app import create_app


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
        if isinstance(response, list):
            return response
        return [response]


class FakeConnection:
    def __init__(self, responses) -> None:
        self.responses = list(responses)
        self.executed = []
        self.closed = False

    def cursor(self) -> FakeCursor:
        return FakeCursor(self)

    def commit(self) -> None:
        return None

    def close(self) -> None:
        self.closed = True


class ConnectionFactory:
    def __init__(self, connections: list[FakeConnection]) -> None:
        self.connections = list(connections)

    def __call__(self, _database_url: str) -> FakeConnection:
        return self.connections.pop(0)


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


def test_dashboard_route_renders_runtime_snapshot() -> None:
    now = datetime.now(timezone.utc)
    connection = FakeConnection(responses=[])
    settings = make_settings()
    order = OrderRecord(
        client_order_id="paper:v1:AAPL:entry",
        symbol="AAPL",
        side="buy",
        intent_type="entry",
        status="accepted",
        quantity=10,
        trading_mode=TradingMode.PAPER,
        strategy_version=settings.strategy_version,
        created_at=now,
        updated_at=now,
        stop_price=109.9,
        limit_price=111.1,
        initial_stop_price=109.9,
        broker_order_id="broker-entry",
        signal_timestamp=now,
    )
    app = create_app(
        settings=settings,
        connect_postgres_fn=ConnectionFactory([connection]),
        trading_status_store_factory=lambda _connection: SimpleNamespace(
            load=lambda **_kwargs: TradingStatus(
                trading_mode=TradingMode.PAPER,
                strategy_version=settings.strategy_version,
                status=TradingStatusValue.ENABLED,
                kill_switch_enabled=False,
                updated_at=now,
            )
        ),
        daily_session_state_store_factory=lambda _connection: SimpleNamespace(
            load=lambda **_kwargs: DailySessionState(
                session_date=date(2026, 4, 25),
                trading_mode=TradingMode.PAPER,
                strategy_version=settings.strategy_version,
                entries_disabled=False,
                flatten_complete=False,
                last_reconciled_at=now,
                notes="ready",
                updated_at=now,
            )
        ),
        position_store_factory=lambda _connection: SimpleNamespace(
            list_all=lambda **_kwargs: [
                PositionRecord(
                    symbol="AAPL",
                    trading_mode=TradingMode.PAPER,
                    strategy_version=settings.strategy_version,
                    quantity=10,
                    entry_price=110.5,
                    stop_price=109.9,
                    initial_stop_price=109.9,
                    opened_at=now,
                    updated_at=now,
                )
            ]
        ),
        order_store_factory=lambda _connection: SimpleNamespace(
            list_by_status=lambda **_kwargs: [order],
            list_recent=lambda **_kwargs: [order],
            list_closed_trades=lambda **_kwargs: [],
        ),
        audit_event_store_factory=lambda _connection: SimpleNamespace(
            list_recent=lambda **_kwargs: [
                SimpleNamespace(
                    event_type="supervisor_cycle",
                    symbol=None,
                    payload={"entries_disabled": False},
                    created_at=now,
                )
            ],
            load_latest=lambda **_kwargs: SimpleNamespace(
                event_type="supervisor_cycle",
                symbol=None,
                payload={"entries_disabled": False},
                created_at=now,
            ),
            list_by_event_types=lambda **_kwargs: [],
        ),
    )

    with TestClient(app) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert "Runtime Dashboard" in response.text
    assert "paper" in response.text
    assert "v1-breakout" in response.text
    assert "enabled" in response.text
    assert "fresh" in response.text
    assert "AAPL" in response.text
    assert "supervisor_cycle" in response.text
    assert connection.closed is True


def test_healthz_route_reports_runtime_status() -> None:
    now = datetime.now(timezone.utc)
    connection = FakeConnection(responses=[])
    settings = make_settings()
    app = create_app(
        settings=settings,
        connect_postgres_fn=ConnectionFactory([connection]),
        trading_status_store_factory=lambda _connection: SimpleNamespace(
            load=lambda **_kwargs: TradingStatus(
                trading_mode=TradingMode.PAPER,
                strategy_version=settings.strategy_version,
                status=TradingStatusValue.CLOSE_ONLY,
                kill_switch_enabled=True,
                status_reason="manual halt",
                updated_at=now,
            )
        ),
        audit_event_store_factory=lambda _connection: SimpleNamespace(
            list_recent=lambda **_kwargs: [],
            load_latest=lambda **_kwargs: SimpleNamespace(
                event_type="supervisor_idle",
                symbol=None,
                payload={"reason": "market_closed"},
                created_at=now,
            ),
        ),
    )

    with TestClient(app) as client:
        response = client.get("/healthz")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["db"] == "ok"
    assert payload["database"] == "ok"
    assert payload["trading_mode"] == "paper"
    assert payload["strategy_version"] == "v1-breakout"
    assert payload["trading_status"] == "close_only"
    assert payload["kill_switch_enabled"] is True
    assert payload["worker_status"] == "fresh"
    assert payload["worker_last_event_type"] == "supervisor_idle"
    assert payload["worker_last_event_at"] == now.isoformat()
    assert isinstance(payload["worker_age_seconds"], int)
    assert payload["worker_age_seconds"] >= 0


def test_healthz_route_returns_503_when_database_fails() -> None:
    def broken_connector(_database_url: str):
        raise RuntimeError("db unavailable")

    app = create_app(
        settings=make_settings(),
        connect_postgres_fn=broken_connector,
    )

    with TestClient(app) as client:
        response = client.get("/healthz")

    assert response.status_code == 503
    assert response.json() == {"status": "error", "reason": "db unavailable"}


def test_healthz_route_reports_missing_worker_when_no_heartbeat_exists() -> None:
    app = create_app(
        settings=make_settings(),
        connect_postgres_fn=ConnectionFactory([FakeConnection(responses=[])]),
        trading_status_store_factory=lambda _connection: SimpleNamespace(
            load=lambda **_kwargs: None
        ),
        audit_event_store_factory=lambda _connection: SimpleNamespace(
            list_recent=lambda **_kwargs: [],
            load_latest=lambda **_kwargs: None,
        ),
    )

    with TestClient(app) as client:
        response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json()["worker_status"] == "missing"


def test_healthz_route_reports_stale_worker_when_last_event_is_old() -> None:
    stale_now = datetime.now(timezone.utc) - timedelta(minutes=10)
    app = create_app(
        settings=make_settings(),
        connect_postgres_fn=ConnectionFactory([FakeConnection(responses=[])]),
        trading_status_store_factory=lambda _connection: SimpleNamespace(
            load=lambda **_kwargs: None
        ),
        audit_event_store_factory=lambda _connection: SimpleNamespace(
            list_recent=lambda **_kwargs: [],
            load_latest=lambda **_kwargs: SimpleNamespace(
                event_type="supervisor_idle",
                symbol=None,
                payload={"reason": "market_closed"},
                created_at=stale_now,
            ),
        ),
    )

    with TestClient(app) as client:
        response = client.get("/healthz")

    assert response.status_code == 503
    assert response.json()["worker_status"] == "stale"
    assert response.json()["status"] == "stale"


def test_dashboard_renders_login_page_when_auth_enabled() -> None:
    app = create_app(
        settings=make_settings(
            DASHBOARD_AUTH_ENABLED="true",
            DASHBOARD_AUTH_USERNAME="operator@example.com",
            DASHBOARD_AUTH_PASSWORD_HASH=hash_password(
                "secret-password",
                salt=bytes.fromhex("000102030405060708090a0b0c0d0e0f"),
            ),
        ),
        connect_postgres_fn=ConnectionFactory([FakeConnection(responses=[])]),
    )

    with TestClient(app) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert "Sign in" in response.text
    assert 'action="/login"' in response.text


def test_dashboard_login_sets_session_cookie_and_redirects() -> None:
    now = datetime.now(timezone.utc)
    connection = FakeConnection(responses=[])
    settings = make_settings(
        DASHBOARD_AUTH_ENABLED="true",
        DASHBOARD_AUTH_USERNAME="operator@example.com",
        DASHBOARD_AUTH_PASSWORD_HASH=hash_password(
            "secret-password",
            salt=bytes.fromhex("000102030405060708090a0b0c0d0e0f"),
        ),
    )
    order = OrderRecord(
        client_order_id="paper:v1:AAPL:entry",
        symbol="AAPL",
        side="buy",
        intent_type="entry",
        status="accepted",
        quantity=10,
        trading_mode=TradingMode.PAPER,
        strategy_version=settings.strategy_version,
        created_at=now,
        updated_at=now,
        stop_price=109.9,
        limit_price=111.1,
        initial_stop_price=109.9,
        broker_order_id="broker-entry",
        signal_timestamp=now,
    )
    app = create_app(
        settings=settings,
        connect_postgres_fn=ConnectionFactory([connection]),
        trading_status_store_factory=lambda _connection: SimpleNamespace(
            load=lambda **_kwargs: TradingStatus(
                trading_mode=TradingMode.PAPER,
                strategy_version=settings.strategy_version,
                status=TradingStatusValue.ENABLED,
                kill_switch_enabled=False,
                updated_at=now,
            )
        ),
        daily_session_state_store_factory=lambda _connection: SimpleNamespace(
            load=lambda **_kwargs: DailySessionState(
                session_date=date(2026, 4, 25),
                trading_mode=TradingMode.PAPER,
                strategy_version=settings.strategy_version,
                entries_disabled=False,
                flatten_complete=False,
                last_reconciled_at=now,
                notes="ready",
                updated_at=now,
            )
        ),
        position_store_factory=lambda _connection: SimpleNamespace(
            list_all=lambda **_kwargs: [
                PositionRecord(
                    symbol="AAPL",
                    trading_mode=TradingMode.PAPER,
                    strategy_version=settings.strategy_version,
                    quantity=10,
                    entry_price=110.5,
                    stop_price=109.9,
                    initial_stop_price=109.9,
                    opened_at=now,
                    updated_at=now,
                )
            ]
        ),
        order_store_factory=lambda _connection: SimpleNamespace(
            list_by_status=lambda **_kwargs: [order],
            list_recent=lambda **_kwargs: [order],
            list_closed_trades=lambda **_kwargs: [],
        ),
        audit_event_store_factory=lambda _connection: SimpleNamespace(
            list_recent=lambda **_kwargs: [],
            load_latest=lambda **_kwargs: None,
            list_by_event_types=lambda **_kwargs: [],
        ),
    )

    with TestClient(app, base_url="https://testserver") as client:
        login_response = client.post(
            "/login",
            data={
                "username": "operator@example.com",
                "password": "secret-password",
                "next": "/",
            },
            follow_redirects=False,
        )
        dashboard_response = client.get("/")

    assert login_response.status_code == 303
    assert login_response.headers["location"] == "/"
    assert "set-cookie" in {key.lower() for key in login_response.headers.keys()}
    assert dashboard_response.status_code == 200
    assert "operator@example.com" in dashboard_response.text


def test_dashboard_login_shows_error_for_invalid_credentials() -> None:
    app = create_app(
        settings=make_settings(
            DASHBOARD_AUTH_ENABLED="true",
            DASHBOARD_AUTH_USERNAME="operator@example.com",
            DASHBOARD_AUTH_PASSWORD_HASH=hash_password(
                "secret-password",
                salt=bytes.fromhex("000102030405060708090a0b0c0d0e0f"),
            ),
        ),
        connect_postgres_fn=ConnectionFactory([FakeConnection(responses=[])]),
    )

    with TestClient(app) as client:
        response = client.post(
            "/login",
            data={
                "username": "operator@example.com",
                "password": "wrong-password",
                "next": "/",
            },
        )

    assert response.status_code == 401
    assert "Invalid username or password" in response.text


def test_dashboard_allows_access_with_valid_basic_auth() -> None:
    now = datetime.now(timezone.utc)
    connection = FakeConnection(responses=[])
    settings = make_settings(
        DASHBOARD_AUTH_ENABLED="true",
        DASHBOARD_AUTH_USERNAME="operator@example.com",
        DASHBOARD_AUTH_PASSWORD_HASH=hash_password(
            "secret-password",
            salt=bytes.fromhex("000102030405060708090a0b0c0d0e0f"),
        ),
    )
    order = OrderRecord(
        client_order_id="paper:v1:AAPL:entry",
        symbol="AAPL",
        side="buy",
        intent_type="entry",
        status="accepted",
        quantity=10,
        trading_mode=TradingMode.PAPER,
        strategy_version=settings.strategy_version,
        created_at=now,
        updated_at=now,
        stop_price=109.9,
        limit_price=111.1,
        initial_stop_price=109.9,
        broker_order_id="broker-entry",
        signal_timestamp=now,
    )
    app = create_app(
        settings=settings,
        connect_postgres_fn=ConnectionFactory([connection]),
        trading_status_store_factory=lambda _connection: SimpleNamespace(
            load=lambda **_kwargs: TradingStatus(
                trading_mode=TradingMode.PAPER,
                strategy_version=settings.strategy_version,
                status=TradingStatusValue.ENABLED,
                kill_switch_enabled=False,
                updated_at=now,
            )
        ),
        daily_session_state_store_factory=lambda _connection: SimpleNamespace(
            load=lambda **_kwargs: DailySessionState(
                session_date=date(2026, 4, 25),
                trading_mode=TradingMode.PAPER,
                strategy_version=settings.strategy_version,
                entries_disabled=False,
                flatten_complete=False,
                last_reconciled_at=now,
                notes="ready",
                updated_at=now,
            )
        ),
        position_store_factory=lambda _connection: SimpleNamespace(
            list_all=lambda **_kwargs: [
                PositionRecord(
                    symbol="AAPL",
                    trading_mode=TradingMode.PAPER,
                    strategy_version=settings.strategy_version,
                    quantity=10,
                    entry_price=110.5,
                    stop_price=109.9,
                    initial_stop_price=109.9,
                    opened_at=now,
                    updated_at=now,
                )
            ]
        ),
        order_store_factory=lambda _connection: SimpleNamespace(
            list_by_status=lambda **_kwargs: [order],
            list_recent=lambda **_kwargs: [order],
            list_closed_trades=lambda **_kwargs: [],
        ),
        audit_event_store_factory=lambda _connection: SimpleNamespace(
            list_recent=lambda **_kwargs: [],
            load_latest=lambda **_kwargs: None,
            list_by_event_types=lambda **_kwargs: [],
        ),
    )

    with TestClient(app) as client:
        dashboard_response = client.get(
            "/",
            headers={
                "Authorization": "Basic "
                + b64encode(b"operator@example.com:secret-password").decode("ascii")
            },
        )

    assert dashboard_response.status_code == 200
    assert "operator@example.com" in dashboard_response.text


# ---------------------------------------------------------------------------
# Format helpers
# ---------------------------------------------------------------------------


def test_format_timestamp_returns_na_for_none() -> None:
    from alpaca_bot.web.app import _format_timestamp

    result = _format_timestamp(None, settings=make_settings())

    assert result == "n/a"


def test_format_timestamp_converts_utc_to_market_timezone() -> None:
    from alpaca_bot.web.app import _format_timestamp

    utc_dt = datetime(2026, 4, 25, 14, 30, tzinfo=timezone.utc)
    result = _format_timestamp(utc_dt, settings=make_settings())

    # America/New_York in April is EDT (UTC-4) → 10:30 ET
    assert "10:30:00" in result
    assert "EDT" in result


def test_format_price_returns_na_for_none() -> None:
    from alpaca_bot.web.app import _format_price

    assert _format_price(None) == "n/a"


def test_format_price_formats_with_dollar_sign_and_commas() -> None:
    from alpaca_bot.web.app import _format_price

    assert _format_price(1234.5) == "$1,234.50"
    assert _format_price(0.99) == "$0.99"
    assert _format_price(10_000.0) == "$10,000.00"


# ---------------------------------------------------------------------------
# _build_store
# ---------------------------------------------------------------------------


def test_build_store_calls_factory_with_connection() -> None:
    from alpaca_bot.web.app import _build_store

    received: list = []

    def factory(conn: object) -> object:
        received.append(conn)
        return object()

    sentinel = object()
    _build_store(factory, sentinel)

    assert received == [sentinel]


def test_build_store_retries_without_args_on_type_error() -> None:
    from alpaca_bot.web.app import _build_store

    fallback = object()

    def factory_no_args() -> object:
        return fallback

    result = _build_store(factory_no_args, object())

    assert result is fallback


def test_build_store_returns_non_callable_as_is() -> None:
    from alpaca_bot.web.app import _build_store

    sentinel = object()
    result = _build_store(sentinel, object())

    assert result is sentinel


# ---------------------------------------------------------------------------
# /metrics route (Phase 2)
# ---------------------------------------------------------------------------


def _make_metrics_app(settings=None, connect_fn=None):
    """Create an app whose stores return minimal data for metrics tests."""
    app_settings = settings or make_settings()
    now = datetime.now(timezone.utc)

    def _audit_store_factory(_conn):
        return SimpleNamespace(
            list_recent=lambda **_: [],
            load_latest=lambda **_: None,
            list_by_event_types=lambda **_: [],
        )

    def _order_store_factory(_conn):
        return SimpleNamespace(
            list_by_status=lambda **_: [],
            list_recent=lambda **_: [],
            list_closed_trades=lambda **_: [],
        )

    return create_app(
        settings=app_settings,
        connect_postgres_fn=connect_fn or (lambda _: FakeConnection(responses=[])),
        trading_status_store_factory=lambda _: SimpleNamespace(load=lambda **_: None),
        daily_session_state_store_factory=lambda _: SimpleNamespace(load=lambda **_: None),
        position_store_factory=lambda _: SimpleNamespace(list_all=lambda **_: []),
        order_store_factory=_order_store_factory,
        audit_event_store_factory=_audit_store_factory,
    )


def test_metrics_route_returns_200_without_auth() -> None:
    app = _make_metrics_app()
    with TestClient(app) as client:
        response = client.get("/metrics")
    assert response.status_code == 200
    assert "Session P" in response.text  # "Session P&L Summary"


def test_metrics_route_renders_login_page_when_auth_enabled_and_no_credentials() -> None:
    settings = make_settings(
        DASHBOARD_AUTH_ENABLED="true",
        DASHBOARD_AUTH_USERNAME="operator@example.com",
        DASHBOARD_AUTH_PASSWORD_HASH=hash_password(
            "secret",
            salt=bytes.fromhex("000102030405060708090a0b0c0d0e0f"),
        ),
    )
    app = _make_metrics_app(settings=settings)
    with TestClient(app) as client:
        response = client.get("/metrics")
    assert response.status_code == 200
    assert "Sign in" in response.text
    assert 'value="/metrics"' in response.text


def test_metrics_route_returns_503_when_database_fails() -> None:
    def broken(_url: str):
        raise RuntimeError("metrics db down")

    app = _make_metrics_app(connect_fn=broken)
    with TestClient(app) as client:
        response = client.get("/metrics")
    assert response.status_code == 503
    assert "metrics unavailable" in response.text
    assert "metrics db down" not in response.text


# ---------------------------------------------------------------------------
# Dashboard route — edge cases
# ---------------------------------------------------------------------------


def test_dashboard_returns_503_when_snapshot_load_fails() -> None:
    def exploding_store(_connection: object) -> object:
        raise RuntimeError("store unavailable")

    app = create_app(
        settings=make_settings(),
        connect_postgres_fn=ConnectionFactory([FakeConnection(responses=[])]),
        trading_status_store_factory=exploding_store,
    )

    with TestClient(app) as client:
        response = client.get("/")

    assert response.status_code == 503
    assert "alpaca_bot dashboard unavailable" in response.text
    assert "store unavailable" not in response.text
    assert "Service temporarily unavailable" in response.text


def test_dashboard_renders_kill_switch_engaged_warning() -> None:
    now = datetime.now(timezone.utc)
    settings = make_settings()
    app = create_app(
        settings=settings,
        connect_postgres_fn=ConnectionFactory([FakeConnection(responses=[])]),
        trading_status_store_factory=lambda _c: SimpleNamespace(
            load=lambda **_: TradingStatus(
                trading_mode=TradingMode.PAPER,
                strategy_version=settings.strategy_version,
                status=TradingStatusValue.CLOSE_ONLY,
                kill_switch_enabled=True,
                updated_at=now,
            )
        ),
        daily_session_state_store_factory=lambda _c: SimpleNamespace(load=lambda **_: None),
        position_store_factory=lambda _c: SimpleNamespace(list_all=lambda **_: []),
        order_store_factory=lambda _c: SimpleNamespace(
            list_by_status=lambda **_: [], list_recent=lambda **_: [],
            list_closed_trades=lambda **_: [],
        ),
        audit_event_store_factory=lambda _c: SimpleNamespace(
            list_recent=lambda **_: [], load_latest=lambda **_: None,
            list_by_event_types=lambda **_: [],
        ),
    )

    with TestClient(app) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert "engaged" in response.text


def test_dashboard_renders_entries_disabled_warning() -> None:
    now = datetime.now(timezone.utc)
    settings = make_settings()
    app = create_app(
        settings=settings,
        connect_postgres_fn=ConnectionFactory([FakeConnection(responses=[])]),
        trading_status_store_factory=lambda _c: SimpleNamespace(load=lambda **_: None),
        daily_session_state_store_factory=lambda _c: SimpleNamespace(
            load=lambda **_: DailySessionState(
                session_date=date(2026, 4, 25),
                trading_mode=TradingMode.PAPER,
                strategy_version=settings.strategy_version,
                entries_disabled=True,
                flatten_complete=False,
                last_reconciled_at=now,
                notes="loss limit hit",
                updated_at=now,
            )
        ),
        position_store_factory=lambda _c: SimpleNamespace(list_all=lambda **_: []),
        order_store_factory=lambda _c: SimpleNamespace(
            list_by_status=lambda **_: [], list_recent=lambda **_: [],
            list_closed_trades=lambda **_: [],
        ),
        audit_event_store_factory=lambda _c: SimpleNamespace(
            list_recent=lambda **_: [], load_latest=lambda **_: None,
            list_by_event_types=lambda **_: [],
        ),
    )

    with TestClient(app) as client:
        response = client.get("/")

    assert response.status_code == 200
    # entries_disabled=True should render a warning "yes" on the dashboard
    assert "loss limit hit" in response.text


def test_dashboard_renders_empty_states_for_no_positions_or_orders() -> None:
    now = datetime.now(timezone.utc)
    settings = make_settings()
    app = create_app(
        settings=settings,
        connect_postgres_fn=ConnectionFactory([FakeConnection(responses=[])]),
        trading_status_store_factory=lambda _c: SimpleNamespace(load=lambda **_: None),
        daily_session_state_store_factory=lambda _c: SimpleNamespace(load=lambda **_: None),
        position_store_factory=lambda _c: SimpleNamespace(list_all=lambda **_: []),
        order_store_factory=lambda _c: SimpleNamespace(
            list_by_status=lambda **_: [], list_recent=lambda **_: [],
            list_closed_trades=lambda **_: [],
        ),
        audit_event_store_factory=lambda _c: SimpleNamespace(
            list_recent=lambda **_: [], load_latest=lambda **_: None,
            list_by_event_types=lambda **_: [],
        ),
    )

    with TestClient(app) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert "No open positions." in response.text
    assert "No working orders." in response.text
    assert "No recent orders." in response.text
    assert "No recent events." in response.text


def test_dashboard_renders_no_session_row_when_state_is_none() -> None:
    settings = make_settings()
    app = create_app(
        settings=settings,
        connect_postgres_fn=ConnectionFactory([FakeConnection(responses=[])]),
        trading_status_store_factory=lambda _c: SimpleNamespace(load=lambda **_: None),
        daily_session_state_store_factory=lambda _c: SimpleNamespace(load=lambda **_: None),
        position_store_factory=lambda _c: SimpleNamespace(list_all=lambda **_: []),
        order_store_factory=lambda _c: SimpleNamespace(
            list_by_status=lambda **_: [], list_recent=lambda **_: [],
            list_closed_trades=lambda **_: [],
        ),
        audit_event_store_factory=lambda _c: SimpleNamespace(
            list_recent=lambda **_: [], load_latest=lambda **_: None,
            list_by_event_types=lambda **_: [],
        ),
    )

    with TestClient(app) as client:
        response = client.get("/")

    assert "no session row" in response.text


# ---------------------------------------------------------------------------
# Healthz route — null trading status
# ---------------------------------------------------------------------------


def test_healthz_returns_null_trading_status_and_false_kill_switch_when_none() -> None:
    app = create_app(
        settings=make_settings(),
        connect_postgres_fn=ConnectionFactory([FakeConnection(responses=[])]),
        trading_status_store_factory=lambda _c: SimpleNamespace(load=lambda **_: None),
        audit_event_store_factory=lambda _c: SimpleNamespace(
            list_recent=lambda **_: [],
            load_latest=lambda **_: None,
        ),
    )

    with TestClient(app) as client:
        response = client.get("/healthz")

    payload = response.json()
    assert response.status_code == 200
    assert payload["trading_status"] is None
    assert payload["kill_switch_enabled"] is False


def test_healthz_returns_null_worker_event_fields_when_no_heartbeat() -> None:
    app = create_app(
        settings=make_settings(),
        connect_postgres_fn=ConnectionFactory([FakeConnection(responses=[])]),
        trading_status_store_factory=lambda _c: SimpleNamespace(load=lambda **_: None),
        audit_event_store_factory=lambda _c: SimpleNamespace(
            list_recent=lambda **_: [],
            load_latest=lambda **_: None,
        ),
    )

    with TestClient(app) as client:
        response = client.get("/healthz")

    payload = response.json()
    assert payload["worker_last_event_type"] is None
    assert payload["worker_last_event_at"] is None
    assert payload["worker_age_seconds"] is None


def test_healthz_closes_connection_after_health_load() -> None:
    connection = FakeConnection(responses=[])
    app = create_app(
        settings=make_settings(),
        connect_postgres_fn=ConnectionFactory([connection]),
        trading_status_store_factory=lambda _c: SimpleNamespace(load=lambda **_: None),
        audit_event_store_factory=lambda _c: SimpleNamespace(
            list_recent=lambda **_: [],
            load_latest=lambda **_: None,
        ),
    )

    with TestClient(app) as client:
        client.get("/healthz")

    assert connection.closed is True


# ---------------------------------------------------------------------------
# CSRF protection
# ---------------------------------------------------------------------------


import hashlib
import hmac as _hmac_module


def _csrf_token(client: TestClient, action: str) -> str:
    secret: bytes = client.app.state.csrf_secret
    return _hmac_module.HMAC(secret, f"\n{action}".encode(), hashlib.sha256).hexdigest()


def _make_minimal_app(settings=None):
    """Minimal create_app suitable for testing CSRF / logout flows."""
    s = settings or make_settings()
    app = create_app(
        settings=s,
        connect_postgres_fn=lambda _url: FakeConnection(responses=[]),
        trading_status_store_factory=lambda _c: SimpleNamespace(load=lambda **_: None),
        position_store_factory=lambda _c: SimpleNamespace(list_all=lambda **_: []),
        order_store_factory=lambda _c: SimpleNamespace(
            list_by_status=lambda **_: [],
            list_recent=lambda **_: [],
            list_closed_trades=lambda **_: [],
        ),
        daily_session_state_store_factory=lambda _c: SimpleNamespace(load=lambda **_: None),
        audit_event_store_factory=lambda _c: SimpleNamespace(
            list_recent=lambda **_: [],
            load_latest=lambda **_: None,
            list_by_event_types=lambda **_: [],
        ),
        strategy_flag_store_factory=lambda _c: SimpleNamespace(
            load=lambda **_: None,
            list_all=lambda **_: [],
        ),
    )
    return app


def test_logout_returns_403_for_missing_csrf_token() -> None:
    app = _make_minimal_app()
    client = TestClient(app, follow_redirects=False)
    response = client.post("/logout")
    assert response.status_code == 403


def test_logout_returns_403_for_wrong_csrf_token() -> None:
    app = _make_minimal_app()
    client = TestClient(app, follow_redirects=False)
    response = client.post("/logout", data={"_csrf_token": "wrong"})
    assert response.status_code == 403


def test_logout_redirects_with_valid_csrf_token() -> None:
    app = _make_minimal_app()
    client = TestClient(app, follow_redirects=False)
    token = _csrf_token(client, "logout")
    response = client.post("/logout", data={"_csrf_token": token})
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


# ---------------------------------------------------------------------------
# Admin status-change routes
# ---------------------------------------------------------------------------


def _make_admin_app(*, saved_statuses: list | None = None, saved_events: list | None = None):
    """App with write-capable stores for testing admin endpoints."""
    statuses = saved_statuses if saved_statuses is not None else []
    events = saved_events if saved_events is not None else []

    def status_store_factory(_conn):
        return SimpleNamespace(
            load=lambda **_: None,
            save=lambda status, *, commit=True: statuses.append(status),
        )

    def audit_store_factory(_conn):
        return SimpleNamespace(
            list_recent=lambda **_: [],
            load_latest=lambda **_: None,
            list_by_event_types=lambda **_: [],
            append=lambda event, *, commit=True: events.append(event),
        )

    return create_app(
        settings=make_settings(),
        connect_postgres_fn=lambda _url: FakeConnection(responses=[]),
        trading_status_store_factory=status_store_factory,
        daily_session_state_store_factory=lambda _c: SimpleNamespace(load=lambda **_: None),
        position_store_factory=lambda _c: SimpleNamespace(list_all=lambda **_: []),
        order_store_factory=lambda _c: SimpleNamespace(
            list_by_status=lambda **_: [],
            list_recent=lambda **_: [],
            list_closed_trades=lambda **_: [],
        ),
        audit_event_store_factory=audit_store_factory,
        strategy_flag_store_factory=lambda _c: SimpleNamespace(load=lambda **_: None, list_all=lambda **_: []),
    )


def test_admin_halt_writes_status_and_audit_event() -> None:
    saved_statuses: list = []
    saved_events: list = []
    app = _make_admin_app(saved_statuses=saved_statuses, saved_events=saved_events)
    client = TestClient(app, follow_redirects=False)
    token = _csrf_token(client, "admin")

    response = client.post(
        "/admin/halt",
        data={"_csrf_token": token, "reason": "end of day"},
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/"
    assert len(saved_statuses) == 1
    assert saved_statuses[0].kill_switch_enabled is True
    assert saved_statuses[0].status.value == "halted"
    assert len(saved_events) == 1
    assert saved_events[0].event_type == "trading_status_changed"
    assert saved_events[0].payload["command"] == "halt"
    assert saved_events[0].payload["reason"] == "end of day"


def test_admin_halt_returns_400_when_reason_missing() -> None:
    app = _make_admin_app()
    client = TestClient(app, follow_redirects=False)
    token = _csrf_token(client, "admin")

    response = client.post("/admin/halt", data={"_csrf_token": token, "reason": ""})

    assert response.status_code == 400


def test_admin_halt_returns_403_for_bad_csrf() -> None:
    app = _make_admin_app()
    client = TestClient(app, follow_redirects=False)
    response = client.post("/admin/halt", data={"_csrf_token": "bad", "reason": "test"})
    assert response.status_code == 403


def test_admin_resume_writes_enabled_status() -> None:
    saved_statuses: list = []
    app = _make_admin_app(saved_statuses=saved_statuses)
    client = TestClient(app, follow_redirects=False)
    token = _csrf_token(client, "admin")

    response = client.post(
        "/admin/resume",
        data={"_csrf_token": token, "reason": ""},
    )

    assert response.status_code == 303
    assert saved_statuses[0].kill_switch_enabled is False
    assert saved_statuses[0].status.value == "enabled"


def test_admin_close_only_writes_close_only_status() -> None:
    saved_statuses: list = []
    app = _make_admin_app(saved_statuses=saved_statuses)
    client = TestClient(app, follow_redirects=False)
    token = _csrf_token(client, "admin")

    response = client.post(
        "/admin/close-only",
        data={"_csrf_token": token, "reason": ""},
    )

    assert response.status_code == 303
    assert saved_statuses[0].kill_switch_enabled is False
    assert saved_statuses[0].status.value == "close_only"


# ---------------------------------------------------------------------------
# /strategies/{name}/toggle-entries route
# ---------------------------------------------------------------------------


def test_toggle_entries_flips_entries_disabled() -> None:
    saved_states: list = []
    saved_events: list = []

    def state_store_factory(_conn):
        return SimpleNamespace(
            load=lambda **_: DailySessionState(
                session_date=date(2026, 4, 25),
                trading_mode=TradingMode.PAPER,
                strategy_version="v1-breakout",
                entries_disabled=False,
                flatten_complete=False,
                last_reconciled_at=None,
                notes=None,
                updated_at=datetime.now(timezone.utc),
            ),
            save=lambda state, *, commit=True: saved_states.append(state),
        )

    app = create_app(
        settings=make_settings(),
        connect_postgres_fn=lambda _url: FakeConnection(responses=[]),
        trading_status_store_factory=lambda _c: SimpleNamespace(load=lambda **_: None),
        daily_session_state_store_factory=state_store_factory,
        position_store_factory=lambda _c: SimpleNamespace(list_all=lambda **_: []),
        order_store_factory=lambda _c: SimpleNamespace(
            list_by_status=lambda **_: [],
            list_recent=lambda **_: [],
            list_closed_trades=lambda **_: [],
        ),
        audit_event_store_factory=lambda _c: SimpleNamespace(
            list_recent=lambda **_: [],
            load_latest=lambda **_: None,
            list_by_event_types=lambda **_: [],
            append=lambda event, *, commit=True: saved_events.append(event),
        ),
        strategy_flag_store_factory=lambda _c: SimpleNamespace(load=lambda **_: None, list_all=lambda **_: []),
    )

    client = TestClient(app, follow_redirects=False)
    token = _csrf_token(client, "toggle")

    response = client.post(
        "/strategies/breakout/toggle-entries",
        data={"_csrf_token": token},
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/"
    assert len(saved_states) == 1
    assert saved_states[0].entries_disabled is True
    assert len(saved_events) == 1
    assert saved_events[0].event_type == "strategy_entries_changed"
    assert saved_events[0].payload["strategy_name"] == "breakout"
    assert saved_events[0].payload["entries_disabled"] is True


def test_toggle_entries_returns_404_for_unknown_strategy() -> None:
    app = _make_minimal_app()
    client = TestClient(app, follow_redirects=False)
    token = _csrf_token(client, "toggle")
    response = client.post(
        "/strategies/nonexistent_strategy/toggle-entries",
        data={"_csrf_token": token},
    )
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# GET /audit route
# ---------------------------------------------------------------------------


def test_audit_route_returns_200_with_empty_events() -> None:
    app = create_app(
        settings=make_settings(),
        connect_postgres_fn=lambda _url: FakeConnection(responses=[]),
        trading_status_store_factory=lambda _c: SimpleNamespace(load=lambda **_: None),
        daily_session_state_store_factory=lambda _c: SimpleNamespace(load=lambda **_: None),
        position_store_factory=lambda _c: SimpleNamespace(list_all=lambda **_: []),
        order_store_factory=lambda _c: SimpleNamespace(
            list_by_status=lambda **_: [], list_recent=lambda **_: [], list_closed_trades=lambda **_: [],
        ),
        audit_event_store_factory=lambda _c: SimpleNamespace(
            list_recent=lambda **_: [],
            load_latest=lambda **_: None,
            list_by_event_types=lambda **_: [],
            append=lambda *a, **kw: None,
        ),
    )

    with TestClient(app) as client:
        response = client.get("/audit")

    assert response.status_code == 200
    assert "Audit Log" in response.text
    assert "No events found." in response.text


def test_audit_route_renders_login_page_when_auth_enabled() -> None:
    settings = make_settings(
        DASHBOARD_AUTH_ENABLED="true",
        DASHBOARD_AUTH_USERNAME="operator@example.com",
        DASHBOARD_AUTH_PASSWORD_HASH=hash_password(
            "secret",
            salt=bytes.fromhex("000102030405060708090a0b0c0d0e0f"),
        ),
    )
    app = _make_minimal_app(settings=settings)
    with TestClient(app) as client:
        response = client.get("/audit")
    assert response.status_code == 200
    assert "Sign in" in response.text


def test_audit_route_shows_events() -> None:
    now = datetime.now(timezone.utc)
    event = SimpleNamespace(
        event_type="supervisor_cycle",
        symbol=None,
        payload={"reason": "normal"},
        created_at=now,
    )
    app = create_app(
        settings=make_settings(),
        connect_postgres_fn=lambda _url: FakeConnection(responses=[]),
        trading_status_store_factory=lambda _c: SimpleNamespace(load=lambda **_: None),
        daily_session_state_store_factory=lambda _c: SimpleNamespace(load=lambda **_: None),
        position_store_factory=lambda _c: SimpleNamespace(list_all=lambda **_: []),
        order_store_factory=lambda _c: SimpleNamespace(
            list_by_status=lambda **_: [], list_recent=lambda **_: [], list_closed_trades=lambda **_: [],
        ),
        audit_event_store_factory=lambda _c: SimpleNamespace(
            list_recent=lambda **_: [event],
            load_latest=lambda **_: None,
            list_by_event_types=lambda **_: [],
            append=lambda *a, **kw: None,
        ),
    )

    with TestClient(app) as client:
        response = client.get("/audit")

    assert response.status_code == 200
    assert "supervisor_cycle" in response.text


# ---------------------------------------------------------------------------
# Auto-refresh meta tag
# ---------------------------------------------------------------------------


def test_dashboard_includes_auto_refresh_by_default() -> None:
    app = _make_minimal_app()
    with TestClient(app) as client:
        response = client.get("/")
    assert 'http-equiv="refresh"' in response.text


def test_dashboard_omits_auto_refresh_when_no_refresh_param_set() -> None:
    app = _make_minimal_app()
    with TestClient(app) as client:
        response = client.get("/?no_refresh=1")
    assert 'http-equiv="refresh"' not in response.text


# ---------------------------------------------------------------------------
# /metrics date navigation
# ---------------------------------------------------------------------------


def test_metrics_route_accepts_date_param() -> None:
    order_dates: list = []

    def tracking_order_store(_conn):
        def list_closed_trades(**kwargs):
            order_dates.append(kwargs.get("session_date"))
            return []
        return SimpleNamespace(
            list_by_status=lambda **_: [],
            list_recent=lambda **_: [],
            list_closed_trades=list_closed_trades,
        )

    app = create_app(
        settings=make_settings(),
        connect_postgres_fn=lambda _url: FakeConnection(responses=[]),
        trading_status_store_factory=lambda _c: SimpleNamespace(load=lambda **_: None),
        daily_session_state_store_factory=lambda _c: SimpleNamespace(load=lambda **_: None),
        position_store_factory=lambda _c: SimpleNamespace(list_all=lambda **_: []),
        order_store_factory=tracking_order_store,
        audit_event_store_factory=lambda _c: SimpleNamespace(
            list_recent=lambda **_: [],
            load_latest=lambda **_: None,
            list_by_event_types=lambda **_: [],
        ),
    )

    with TestClient(app) as client:
        response = client.get("/metrics?date_param=2026-04-20")

    assert response.status_code == 200
    assert order_dates == [date(2026, 4, 20)]


def test_metrics_route_shows_warning_for_invalid_date() -> None:
    app = _make_minimal_app()
    with TestClient(app) as client:
        response = client.get("/metrics?date_param=not-a-date")
    assert response.status_code == 200
    assert "Invalid date" in response.text


def test_metrics_route_shows_warning_for_future_date() -> None:
    app = _make_minimal_app()
    with TestClient(app) as client:
        response = client.get("/metrics?date_param=2099-01-01")
    assert response.status_code == 200
    assert "future" in response.text


# ---------------------------------------------------------------------------
# Watchlist routes
# ---------------------------------------------------------------------------


def _make_watchlist_app(
    *,
    watchlist_records: list | None = None,
    enabled_symbols: list[str] | None = None,
    ignored_symbols: list[str] | None = None,
    saved_adds: list | None = None,
    saved_removes: list | None = None,
    saved_ignores: list | None = None,
    saved_unignores: list | None = None,
    saved_events: list | None = None,
):
    """App with injectable watchlist store for testing watchlist endpoints."""
    records = watchlist_records if watchlist_records is not None else []
    enabled = enabled_symbols if enabled_symbols is not None else ["AAPL", "MSFT"]
    ignored = ignored_symbols if ignored_symbols is not None else []
    adds = saved_adds if saved_adds is not None else []
    removes = saved_removes if saved_removes is not None else []
    ignores = saved_ignores if saved_ignores is not None else []
    unignores = saved_unignores if saved_unignores is not None else []
    events = saved_events if saved_events is not None else []

    def watchlist_store_factory(_conn):
        return SimpleNamespace(
            list_all=lambda trading_mode: records,
            list_enabled=lambda trading_mode: list(enabled),
            list_ignored=lambda trading_mode: list(ignored),
            add=lambda symbol, trading_mode, *, added_by="system", commit=True: adds.append(symbol),
            remove=lambda symbol, trading_mode, *, commit=False: removes.append(symbol),
            ignore=lambda symbol, trading_mode, *, commit=True: ignores.append(symbol),
            unignore=lambda symbol, trading_mode, *, commit=True: unignores.append(symbol),
        )

    def audit_store_factory(_conn):
        return SimpleNamespace(
            list_recent=lambda **_: [],
            load_latest=lambda **_: None,
            list_by_event_types=lambda **_: [],
            append=lambda event, *, commit=True: events.append(event),
        )

    return create_app(
        settings=make_settings(),
        connect_postgres_fn=lambda _url: FakeConnection(responses=[]),
        trading_status_store_factory=lambda _c: SimpleNamespace(load=lambda **_: None),
        daily_session_state_store_factory=lambda _c: SimpleNamespace(load=lambda **_: None),
        position_store_factory=lambda _c: SimpleNamespace(list_all=lambda **_: []),
        order_store_factory=lambda _c: SimpleNamespace(
            list_by_status=lambda **_: [],
            list_recent=lambda **_: [],
            list_closed_trades=lambda **_: [],
        ),
        audit_event_store_factory=audit_store_factory,
        strategy_flag_store_factory=lambda _c: SimpleNamespace(load=lambda **_: None, list_all=lambda **_: []),
        watchlist_store_factory=watchlist_store_factory,
    )


def test_watchlist_page_renders_symbols() -> None:
    from datetime import timezone

    now = datetime.now(timezone.utc)
    records = [
        SimpleNamespace(symbol="AAPL", enabled=True, ignored=False, added_at=now, added_by="system"),
        SimpleNamespace(symbol="MSFT", enabled=True, ignored=False, added_at=now, added_by="operator"),
    ]
    app = _make_watchlist_app(watchlist_records=records, enabled_symbols=["AAPL", "MSFT"])
    with TestClient(app) as client:
        response = client.get("/watchlist")

    assert response.status_code == 200
    assert "AAPL" in response.text
    assert "MSFT" in response.text
    assert "Symbol Watchlist" in response.text


def test_watchlist_page_shows_error_for_invalid_symbol_query_param() -> None:
    app = _make_watchlist_app()
    with TestClient(app) as client:
        response = client.get("/watchlist?error=invalid_symbol")

    assert response.status_code == 200
    assert "Invalid symbol" in response.text


def test_watchlist_page_shows_error_for_last_symbol_query_param() -> None:
    app = _make_watchlist_app()
    with TestClient(app) as client:
        response = client.get("/watchlist?error=last_symbol")

    assert response.status_code == 200
    assert "Cannot remove the last" in response.text


def test_watchlist_add_valid_symbol_calls_store() -> None:
    saved_adds: list = []
    saved_events: list = []
    app = _make_watchlist_app(saved_adds=saved_adds, saved_events=saved_events)
    client = TestClient(app, follow_redirects=False)
    token = _csrf_token(client, "watchlist")

    response = client.post(
        "/admin/watchlist/add",
        data={"_csrf_token": token, "symbol": "NVDA"},
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/watchlist"
    assert "NVDA" in saved_adds
    assert any(e.event_type == "WATCHLIST_ADD" for e in saved_events)
    assert saved_events[0].symbol == "NVDA"


def test_watchlist_add_invalid_symbol_redirects_to_error() -> None:
    app = _make_watchlist_app()
    client = TestClient(app, follow_redirects=False)
    token = _csrf_token(client, "watchlist")

    response = client.post(
        "/admin/watchlist/add",
        data={"_csrf_token": token, "symbol": "invalid symbol!"},
    )

    assert response.status_code == 303
    assert "invalid_symbol" in response.headers["location"]


def test_watchlist_add_lowercase_symbol_is_accepted() -> None:
    """Input is uppercased server-side; lowercase should normalize correctly."""
    saved_adds: list = []
    app = _make_watchlist_app(saved_adds=saved_adds)
    client = TestClient(app, follow_redirects=False)
    token = _csrf_token(client, "watchlist")

    response = client.post(
        "/admin/watchlist/add",
        data={"_csrf_token": token, "symbol": "nvda"},
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/watchlist"
    assert "NVDA" in saved_adds


def test_watchlist_add_returns_403_for_bad_csrf() -> None:
    app = _make_watchlist_app()
    client = TestClient(app, follow_redirects=False)

    response = client.post(
        "/admin/watchlist/add",
        data={"_csrf_token": "bad-token", "symbol": "AAPL"},
    )

    assert response.status_code == 403


def test_watchlist_remove_valid_symbol_calls_store() -> None:
    saved_removes: list = []
    saved_events: list = []
    app = _make_watchlist_app(
        enabled_symbols=["AAPL", "MSFT"],
        saved_removes=saved_removes,
        saved_events=saved_events,
    )
    client = TestClient(app, follow_redirects=False)
    token = _csrf_token(client, "watchlist")

    response = client.post(
        "/admin/watchlist/remove",
        data={"_csrf_token": token, "symbol": "AAPL"},
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/watchlist"
    assert "AAPL" in saved_removes
    assert any(e.event_type == "WATCHLIST_REMOVE" for e in saved_events)


def test_watchlist_remove_last_symbol_redirects_to_error() -> None:
    app = _make_watchlist_app(enabled_symbols=["AAPL"])
    client = TestClient(app, follow_redirects=False)
    token = _csrf_token(client, "watchlist")

    response = client.post(
        "/admin/watchlist/remove",
        data={"_csrf_token": token, "symbol": "AAPL"},
    )

    assert response.status_code == 303
    assert "last_symbol" in response.headers["location"]


def test_watchlist_remove_returns_403_for_bad_csrf() -> None:
    app = _make_watchlist_app()
    client = TestClient(app, follow_redirects=False)

    response = client.post(
        "/admin/watchlist/remove",
        data={"_csrf_token": "wrong", "symbol": "AAPL"},
    )

    assert response.status_code == 403


def test_watchlist_nav_link_present_on_dashboard() -> None:
    app = _make_minimal_app()
    with TestClient(app) as client:
        response = client.get("/")
    assert 'href="/watchlist"' in response.text


def test_watchlist_nav_link_present_on_audit_page() -> None:
    app = _make_minimal_app()
    with TestClient(app) as client:
        response = client.get("/audit")
    assert 'href="/watchlist"' in response.text


def test_watchlist_ignore_valid_symbol_calls_store() -> None:
    saved_ignores: list = []
    saved_events: list = []
    app = _make_watchlist_app(
        enabled_symbols=["TSLA", "AAPL"],
        saved_ignores=saved_ignores,
        saved_events=saved_events,
    )
    client = TestClient(app, follow_redirects=False)
    token = _csrf_token(client, "watchlist")

    response = client.post(
        "/admin/watchlist/ignore",
        data={"_csrf_token": token, "symbol": "TSLA"},
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/watchlist"
    assert "TSLA" in saved_ignores
    assert any(e.event_type == "WATCHLIST_IGNORE" for e in saved_events)
    assert saved_events[0].symbol == "TSLA"


def test_watchlist_ignore_returns_403_for_bad_csrf() -> None:
    app = _make_watchlist_app()
    client = TestClient(app, follow_redirects=False)

    response = client.post(
        "/admin/watchlist/ignore",
        data={"_csrf_token": "bad-token", "symbol": "TSLA"},
    )

    assert response.status_code == 403


def test_watchlist_unignore_valid_symbol_calls_store() -> None:
    saved_unignores: list = []
    saved_events: list = []
    app = _make_watchlist_app(
        enabled_symbols=["TSLA", "AAPL"],
        ignored_symbols=["TSLA"],
        saved_unignores=saved_unignores,
        saved_events=saved_events,
    )
    client = TestClient(app, follow_redirects=False)
    token = _csrf_token(client, "watchlist")

    response = client.post(
        "/admin/watchlist/unignore",
        data={"_csrf_token": token, "symbol": "TSLA"},
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/watchlist"
    assert "TSLA" in saved_unignores
    assert any(e.event_type == "WATCHLIST_UNIGNORE" for e in saved_events)
    assert saved_events[0].symbol == "TSLA"


def test_watchlist_unignore_returns_403_for_bad_csrf() -> None:
    app = _make_watchlist_app()
    client = TestClient(app, follow_redirects=False)

    response = client.post(
        "/admin/watchlist/unignore",
        data={"_csrf_token": "bad-token", "symbol": "TSLA"},
    )

    assert response.status_code == 403


def test_watchlist_page_renders_ignored_badge() -> None:
    from datetime import timezone

    now = datetime.now(timezone.utc)
    records = [
        SimpleNamespace(symbol="TSLA", enabled=True, ignored=True, added_at=now, added_by="system"),
        SimpleNamespace(symbol="AAPL", enabled=True, ignored=False, added_at=now, added_by="system"),
    ]
    app = _make_watchlist_app(watchlist_records=records, enabled_symbols=["TSLA", "AAPL"])
    with TestClient(app) as client:
        response = client.get("/watchlist")

    assert response.status_code == 200
    assert "ignored" in response.text
    assert "badge-ignored" in response.text


# ---------------------------------------------------------------------------
# /healthz — strategy_flags in response
# ---------------------------------------------------------------------------


def test_healthz_includes_strategy_flags() -> None:
    from alpaca_bot.strategy import STRATEGY_REGISTRY

    now = datetime.now(timezone.utc)
    enabled_flag = SimpleNamespace(strategy_name="breakout", enabled=True)
    app = create_app(
        settings=make_settings(),
        connect_postgres_fn=ConnectionFactory([FakeConnection(responses=[])]),
        trading_status_store_factory=lambda _c: SimpleNamespace(load=lambda **_: None),
        audit_event_store_factory=lambda _c: SimpleNamespace(
            list_recent=lambda **_: [],
            load_latest=lambda **_: SimpleNamespace(
                event_type="supervisor_idle",
                symbol=None,
                payload={},
                created_at=now,
            ),
        ),
        strategy_flag_store_factory=lambda _c: SimpleNamespace(
            list_all=lambda **_: [enabled_flag]
        ),
    )

    with TestClient(app) as client:
        response = client.get("/healthz")

    payload = response.json()
    assert "strategy_flags" in payload
    flags = {f["name"]: f["enabled"] for f in payload["strategy_flags"]}
    assert set(flags.keys()) == set(STRATEGY_REGISTRY.keys())
    assert flags["breakout"] is True


# ---------------------------------------------------------------------------
# Notifier is called on admin status changes
# ---------------------------------------------------------------------------


class _RecordingNotifier:
    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.raise_on_send: bool = False

    def send(self, *, subject: str, body: str) -> None:
        if self.raise_on_send:
            raise RuntimeError("send failed")
        self.calls.append({"subject": subject, "body": body})


def _make_admin_app_with_notifier(notifier):
    """Admin app wired with an explicit notifier for testing."""
    return create_app(
        settings=make_settings(),
        connect_postgres_fn=lambda _url: FakeConnection(responses=[]),
        trading_status_store_factory=lambda _c: SimpleNamespace(
            load=lambda **_: None,
            save=lambda *_a, **_k: None,
        ),
        daily_session_state_store_factory=lambda _c: SimpleNamespace(load=lambda **_: None),
        position_store_factory=lambda _c: SimpleNamespace(list_all=lambda **_: []),
        order_store_factory=lambda _c: SimpleNamespace(
            list_by_status=lambda **_: [],
            list_recent=lambda **_: [],
            list_closed_trades=lambda **_: [],
        ),
        audit_event_store_factory=lambda _c: SimpleNamespace(
            list_recent=lambda **_: [],
            load_latest=lambda **_: None,
            list_by_event_types=lambda **_: [],
            append=lambda *_a, **_k: None,
        ),
        strategy_flag_store_factory=lambda _c: SimpleNamespace(list_all=lambda **_: []),
        notifier=notifier,
    )


def test_admin_halt_calls_notifier() -> None:
    notifier = _RecordingNotifier()
    app = _make_admin_app_with_notifier(notifier)
    client = TestClient(app, follow_redirects=False)
    token = _csrf_token(client, "admin")

    client.post("/admin/halt", data={"_csrf_token": token, "reason": "eod"})

    assert len(notifier.calls) == 1
    assert notifier.calls[0]["subject"] == "Trading halted"
    assert "eod" in notifier.calls[0]["body"]


def test_admin_resume_calls_notifier() -> None:
    notifier = _RecordingNotifier()
    app = _make_admin_app_with_notifier(notifier)
    client = TestClient(app, follow_redirects=False)
    token = _csrf_token(client, "admin")

    client.post("/admin/resume", data={"_csrf_token": token, "reason": ""})

    assert len(notifier.calls) == 1
    assert notifier.calls[0]["subject"] == "Trading resumed"


def test_notifier_failure_does_not_abort_redirect() -> None:
    notifier = _RecordingNotifier()
    notifier.raise_on_send = True
    app = _make_admin_app_with_notifier(notifier)
    client = TestClient(app, follow_redirects=False)
    token = _csrf_token(client, "admin")

    response = client.post("/admin/halt", data={"_csrf_token": token, "reason": "test"})

    assert response.status_code == 303
