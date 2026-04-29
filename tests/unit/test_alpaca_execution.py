from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any

import pytest

from enum import Enum

from alpaca_bot.config import Settings
from alpaca_bot.execution import (
    AlpacaBroker,
    AlpacaExecutionAdapter,
    AlpacaCredentialsError,
)
from alpaca_bot.execution.alpaca import _parse_broker_order, _retry_with_backoff, resolve_alpaca_credentials


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
        if hasattr(filters, "start") and hasattr(filters, "end"):
            self.calendar_filters = {"start": filters.start, "end": filters.end}
        else:
            self.calendar_filters = filters
        return self.calendar

    def get_orders(self, filter: object | None = None) -> list[OrderStub]:
        if hasattr(filter, "status"):
            self.order_filter = {"status": str(filter.status.value if hasattr(filter.status, "value") else filter.status)}
        else:
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


# ---------------------------------------------------------------------------
# _retry_with_backoff tests
# ---------------------------------------------------------------------------


def test_retry_succeeds_on_second_attempt_after_rate_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Function that raises a 429/rate-limit error once should succeed on retry."""
    slept: list[float] = []
    monkeypatch.setattr("alpaca_bot.execution.alpaca.time.sleep", lambda s: slept.append(s))

    calls = 0

    def flaky() -> str:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("HTTP 429 rate limit exceeded")
        return "ok"

    result = _retry_with_backoff(flaky)

    assert result == "ok"
    assert calls == 2
    assert slept == [1]


def test_retry_raises_immediately_on_non_transient_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A non-transient error (e.g. 422 validation) must not be retried."""
    slept: list[float] = []
    monkeypatch.setattr("alpaca_bot.execution.alpaca.time.sleep", lambda s: slept.append(s))

    calls = 0

    def bad_request() -> None:
        nonlocal calls
        calls += 1
        raise ValueError("422 Unprocessable Entity — bad symbol")

    with pytest.raises(ValueError, match="422"):
        _retry_with_backoff(bad_request)

    assert calls == 1
    assert slept == []


def test_retry_raises_after_all_attempts_exhausted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """After 3 consecutive transient failures the original exception propagates."""
    slept: list[float] = []
    monkeypatch.setattr("alpaca_bot.execution.alpaca.time.sleep", lambda s: slept.append(s))

    calls = 0

    def always_fails() -> None:
        nonlocal calls
        calls += 1
        raise ConnectionError("connection reset by peer")

    with pytest.raises(ConnectionError, match="connection reset"):
        _retry_with_backoff(always_fails)

    assert calls == 3
    assert slept == [1, 2]


def test_submit_order_retries_on_transient_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """submit_stop_limit_entry must retry when the underlying client raises a 5xx error."""
    slept: list[float] = []
    monkeypatch.setattr("alpaca_bot.execution.alpaca.time.sleep", lambda s: slept.append(s))

    @dataclass
    class OrderStubLocal:
        client_order_id: str
        id: str
        symbol: str
        side: str
        status: str
        qty: str

    calls = 0

    class FlakyTradingClient:
        def submit_order(self, order_data: Any) -> OrderStubLocal:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError("500 Internal Server Error")
            return OrderStubLocal(
                client_order_id="paper:v1:AAPL:entry",
                id="broker-1",
                symbol="AAPL",
                side="buy",
                status="accepted",
                qty="5",
            )

        # required by TradingClientProtocol but not exercised here
        def get_account(self) -> Any: ...
        def get_clock(self) -> Any: ...
        def get_calendar(self, filters: Any = None) -> list[Any]: return []
        def get_orders(self, filter: Any = None) -> list[Any]: return []
        def get_all_positions(self) -> list[Any]: return []
        def replace_order_by_id(self, order_id: str, order_data: Any) -> Any: ...
        def cancel_order_by_id(self, order_id: str) -> None: ...

    adapter = AlpacaExecutionAdapter(trading_client=FlakyTradingClient())

    order = adapter.submit_stop_limit_entry(
        symbol="AAPL",
        qty=5,
        stop_price=101.0,
        limit_price=101.25,
        client_order_id="paper:v1:AAPL:entry",
    )

    assert order.symbol == "AAPL"
    assert order.status == "accepted"
    assert calls == 2
    assert slept == [1]


def test_retry_succeeds_on_second_attempt_after_server_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 503 transient error should trigger a retry and eventually succeed."""
    slept: list[float] = []
    monkeypatch.setattr("alpaca_bot.execution.alpaca.time.sleep", lambda s: slept.append(s))

    calls = 0

    def flaky() -> str:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("503 Service Unavailable")
        return "recovered"

    result = _retry_with_backoff(flaky)

    assert result == "recovered"
    assert calls == 2
    assert slept == [1]


def test_retry_treats_too_many_requests_keyword_as_transient(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """'too many requests' in the exception message must be treated as rate-limit."""
    slept: list[float] = []
    monkeypatch.setattr("alpaca_bot.execution.alpaca.time.sleep", lambda s: slept.append(s))

    calls = 0

    def flaky() -> str:
        nonlocal calls
        calls += 1
        if calls < 3:
            raise Exception("too many requests, slow down")
        return "finally"

    result = _retry_with_backoff(flaky)

    assert result == "finally"
    assert calls == 3
    assert slept == [1, 2]


def test_keyboard_interrupt_propagates_immediately_without_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """KeyboardInterrupt must NOT be caught by _retry_with_backoff.

    The retry loop uses ``except Exception`` which does not catch
    KeyboardInterrupt (a BaseException subclass).  The interrupt must
    propagate on the very first call and time.sleep must never be invoked.
    """
    slept: list[float] = []
    monkeypatch.setattr("alpaca_bot.execution.alpaca.time.sleep", lambda s: slept.append(s))

    calls = 0

    def raises_keyboard_interrupt() -> None:
        nonlocal calls
        calls += 1
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        _retry_with_backoff(raises_keyboard_interrupt)

    assert calls == 1, "fn should have been called exactly once before propagating"
    assert slept == [], "time.sleep must never be called when KeyboardInterrupt fires"


# ---------------------------------------------------------------------------
# _parse_broker_order tests
# ---------------------------------------------------------------------------


class _OrderSide(str, Enum):
    BUY = "buy"


class _OrderStatus(str, Enum):
    NEW = "new"
    ACCEPTED = "accepted"


@dataclass
class EnumOrderStub:
    client_order_id: str
    id: str
    symbol: str
    side: _OrderSide
    status: _OrderStatus
    qty: str


@dataclass
class StringOrderStub:
    client_order_id: str
    id: str
    symbol: str
    side: str
    status: str
    qty: str


def test_parse_broker_order_enum_side_and_status_uses_value() -> None:
    """Enum fields must produce their raw string values, not 'OrderSide.BUY'."""
    raw = EnumOrderStub(
        client_order_id="paper:v1:AAPL:entry",
        id="broker-1",
        symbol="aapl",
        side=_OrderSide.BUY,
        status=_OrderStatus.NEW,
        qty="10",
    )
    order = _parse_broker_order(raw)
    assert order.side == "buy"
    assert order.status == "new"
    assert order.symbol == "AAPL"
    assert order.quantity == 10


def test_parse_broker_order_str_side_and_status_passes_through() -> None:
    """Plain string fields must pass through unchanged."""
    raw = StringOrderStub(
        client_order_id="paper:v1:AAPL:entry",
        id="broker-2",
        symbol="AAPL",
        side="buy",
        status="accepted",
        qty="5",
    )
    order = _parse_broker_order(raw)
    assert order.side == "buy"
    assert order.status == "accepted"


def test_parse_broker_order_enum_status_not_string_enum_prefix() -> None:
    """str(Enum) returns 'ClassName.VALUE'; _parse_broker_order must not use str()."""
    raw = EnumOrderStub(
        client_order_id="paper:v1:MSFT:entry",
        id="broker-3",
        symbol="MSFT",
        side=_OrderSide.BUY,
        status=_OrderStatus.ACCEPTED,
        qty="3",
    )
    order = _parse_broker_order(raw)
    assert order.status == "accepted"
    assert "OrderStatus" not in order.status
    assert "_OrderStatus" not in order.status
