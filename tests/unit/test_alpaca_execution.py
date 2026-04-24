from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any

import pytest

from alpaca_bot.config import Settings
from alpaca_bot.execution import (
    AlpacaBroker,
    AlpacaExecutionAdapter,
    AlpacaCredentialsError,
)
from alpaca_bot.execution.alpaca import resolve_alpaca_credentials


@dataclass
class ClockStub:
    timestamp: datetime
    is_open: bool
    next_open: datetime
    next_close: datetime


@dataclass
class CalendarStub:
    date: date
    open: datetime
    close: datetime


@dataclass
class OrderStub:
    client_order_id: str
    id: str
    symbol: str
    side: str
    status: str
    qty: str


@dataclass
class PositionStub:
    symbol: str
    qty: str
    avg_entry_price: str
    market_value: str


@dataclass
class AccountStub:
    equity: str
    buying_power: str
    trading_blocked: bool


class TradingClientStub:
    def __init__(self) -> None:
        self.init_args: tuple[str, str, bool] | None = None
        self.calendar_filters: object | None = None
        self.order_filter: object | None = None
        self.clock = ClockStub(
            timestamp=datetime(2026, 4, 24, 19, 0, tzinfo=timezone.utc),
            is_open=True,
            next_open=datetime(2026, 4, 27, 13, 30, tzinfo=timezone.utc),
            next_close=datetime(2026, 4, 24, 20, 0, tzinfo=timezone.utc),
        )
        self.calendar = [
            CalendarStub(
                date=date(2026, 4, 24),
                open=datetime(2026, 4, 24, 13, 30, tzinfo=timezone.utc),
                close=datetime(2026, 4, 24, 20, 0, tzinfo=timezone.utc),
            )
        ]
        self.orders = [
            OrderStub(
                client_order_id="paper:v1:AAPL:entry",
                id="broker-order-1",
                symbol="AAPL",
                side="buy",
                status="accepted",
                qty="45",
            )
        ]
        self.positions = [
            PositionStub(
                symbol="AAPL",
                qty="45",
                avg_entry_price="111.02",
                market_value="5000.00",
            )
        ]
        self.account = AccountStub(
            equity="100000.00",
            buying_power="97500.50",
            trading_blocked=False,
        )

    def get_clock(self) -> ClockStub:
        return self.clock

    def get_calendar(self, filters: object | None = None) -> list[CalendarStub]:
        self.calendar_filters = filters
        return self.calendar

    def get_orders(self, filter: object | None = None) -> list[OrderStub]:
        self.order_filter = filter
        return self.orders

    def get_all_positions(self) -> list[PositionStub]:
        return self.positions

    def get_account(self) -> AccountStub:
        return self.account


def make_settings(**overrides: str) -> Settings:
    values = {
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
        "ALPACA_PAPER_API_KEY": "paper-key",
        "ALPACA_PAPER_SECRET_KEY": "paper-secret",
        "ALPACA_LIVE_API_KEY": "live-key",
        "ALPACA_LIVE_SECRET_KEY": "live-secret",
    }
    values.update(overrides)
    return Settings.from_env(values)


def test_resolve_credentials_selects_paper_keys() -> None:
    api_key, secret_key, paper = resolve_alpaca_credentials(make_settings())

    assert (api_key, secret_key, paper) == ("paper-key", "paper-secret", True)


def test_resolve_credentials_selects_live_keys() -> None:
    settings = make_settings(TRADING_MODE="live", ENABLE_LIVE_TRADING="true")

    api_key, secret_key, paper = resolve_alpaca_credentials(settings)

    assert (api_key, secret_key, paper) == ("live-key", "live-secret", False)


def test_resolve_credentials_rejects_missing_mode_keys() -> None:
    settings = make_settings(ALPACA_PAPER_API_KEY="", ALPACA_PAPER_SECRET_KEY="")

    with pytest.raises(
        AlpacaCredentialsError,
        match="ALPACA_PAPER_API_KEY, ALPACA_PAPER_SECRET_KEY",
    ):
        resolve_alpaca_credentials(settings)


def test_from_settings_selects_paper_credentials() -> None:
    seen: dict[str, Any] = {}

    def factory(api_key: str, secret_key: str, *, paper: bool) -> TradingClientStub:
        seen.update(api_key=api_key, secret_key=secret_key, paper=paper)
        return TradingClientStub()

    broker = AlpacaExecutionAdapter.from_settings(
        make_settings(),
        trading_client_factory=factory,
    )

    assert isinstance(broker, AlpacaExecutionAdapter)
    assert seen == {
        "api_key": "paper-key",
        "secret_key": "paper-secret",
        "paper": True,
    }


def test_from_settings_selects_live_credentials() -> None:
    seen: dict[str, Any] = {}

    def factory(api_key: str, secret_key: str, *, paper: bool) -> TradingClientStub:
        seen.update(api_key=api_key, secret_key=secret_key, paper=paper)
        return TradingClientStub()

    AlpacaExecutionAdapter.from_settings(
        make_settings(TRADING_MODE="live", ENABLE_LIVE_TRADING="true"),
        trading_client_factory=factory,
    )

    assert seen == {
        "api_key": "live-key",
        "secret_key": "live-secret",
        "paper": False,
    }


def test_from_settings_rejects_missing_mode_keys() -> None:
    settings = make_settings(ALPACA_PAPER_API_KEY="", ALPACA_PAPER_SECRET_KEY="")

    with pytest.raises(ValueError, match="ALPACA_PAPER_API_KEY"):
        AlpacaExecutionAdapter.from_settings(
            settings,
            trading_client_factory=lambda *args, **kwargs: TradingClientStub(),
        )


def test_execution_adapter_exposes_clock_calendar_orders_and_positions() -> None:
    trading_client = TradingClientStub()
    broker = AlpacaExecutionAdapter(trading_client)

    clock = broker.get_market_clock()
    calendar = broker.get_market_calendar(start=date(2026, 4, 24), end=date(2026, 4, 24))
    orders = broker.list_open_orders()
    positions = broker.list_positions()

    assert clock.is_open is True
    assert calendar[0].session_date == date(2026, 4, 24)
    assert orders[0].client_order_id == "paper:v1:AAPL:entry"
    assert positions[0].quantity == 45
    assert trading_client.calendar_filters == {
        "start": date(2026, 4, 24),
        "end": date(2026, 4, 24),
    }
    assert trading_client.order_filter == {"status": "open"}


def test_alpaca_broker_is_backwards_compatible_alias() -> None:
    broker = AlpacaBroker(trading_client=TradingClientStub(), settings=make_settings())

    assert isinstance(broker, AlpacaExecutionAdapter)


def test_execution_adapter_exposes_account_snapshot() -> None:
    broker = AlpacaExecutionAdapter(TradingClientStub())

    account = broker.get_account()

    assert account.equity == 100000.0
    assert account.buying_power == 97500.5
    assert account.trading_blocked is False
