from __future__ import annotations

from datetime import date, datetime, timezone
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


def make_settings() -> Settings:
    return Settings.from_env(
        {
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
    )


def test_dashboard_route_renders_runtime_snapshot() -> None:
    now = datetime(2026, 4, 25, 14, 0, tzinfo=timezone.utc)
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
            ]
        ),
    )

    with TestClient(app) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert "Read-Only Runtime Dashboard" in response.text
    assert "paper" in response.text
    assert "v1-breakout" in response.text
    assert "enabled" in response.text
    assert "AAPL" in response.text
    assert "supervisor_cycle" in response.text
    assert connection.closed is True


def test_healthz_route_reports_runtime_status() -> None:
    now = datetime(2026, 4, 25, 14, 0, tzinfo=timezone.utc)
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
    )

    with TestClient(app) as client:
        response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "db": "ok",
        "database": "ok",
        "trading_mode": "paper",
        "strategy_version": "v1-breakout",
        "trading_status": "close_only",
        "kill_switch_enabled": True,
    }


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
