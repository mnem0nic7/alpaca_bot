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

    assert response.status_code == 200
    assert response.json()["worker_status"] == "stale"


def test_dashboard_requires_basic_auth_when_auth_enabled() -> None:
    app = create_app(
        settings=make_settings(
            DASHBOARD_AUTH_ENABLED="true",
            DASHBOARD_AUTH_USERNAME="m7ga.77@gmail.com",
            DASHBOARD_AUTH_PASSWORD_HASH=hash_password(
                "secret-password",
                salt=bytes.fromhex("000102030405060708090a0b0c0d0e0f"),
            ),
        ),
        connect_postgres_fn=ConnectionFactory([FakeConnection(responses=[])]),
    )

    with TestClient(app) as client:
        response = client.get("/")

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == 'Basic realm="alpaca_bot"'


def test_dashboard_allows_access_with_valid_basic_auth() -> None:
    now = datetime.now(timezone.utc)
    connection = FakeConnection(responses=[])
    settings = make_settings(
        DASHBOARD_AUTH_ENABLED="true",
        DASHBOARD_AUTH_USERNAME="m7ga.77@gmail.com",
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
        connect_postgres_fn=ConnectionFactory([connection, connection]),
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
        ),
        audit_event_store_factory=lambda _connection: SimpleNamespace(
            list_recent=lambda **_kwargs: []
        ),
    )

    with TestClient(app) as client:
        dashboard_response = client.get(
            "/",
            headers={
                "Authorization": "Basic "
                + b64encode(b"m7ga.77@gmail.com:secret-password").decode("ascii")
            },
        )

    assert dashboard_response.status_code == 200
    assert "m7ga.77@gmail.com" in dashboard_response.text
