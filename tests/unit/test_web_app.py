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
    assert "Read-Only Runtime Dashboard" in response.text
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
