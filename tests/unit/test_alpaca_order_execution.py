from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from alpaca_bot.execution.alpaca import AlpacaExecutionAdapter, BrokerOrder


@dataclass
class OrderStub:
    client_order_id: str
    id: str
    symbol: str
    side: str
    status: str
    qty: str


class TradingClientWriteStub:
    def __init__(self) -> None:
        self.submitted_order: Any | None = None
        self.replaced_order_id: str | None = None
        self.replace_request: Any | None = None
        self.cancelled_order_id: str | None = None

    def submit_order(self, order_data: Any) -> OrderStub:
        self.submitted_order = order_data
        payload = _request_payload(order_data)
        return OrderStub(
            client_order_id=str(payload["client_order_id"]),
            id="broker-entry-1",
            symbol=str(payload["symbol"]),
            side=str(payload["side"]),
            status="accepted",
            qty=str(payload["qty"]),
        )

    def replace_order_by_id(self, order_id: str, order_data: Any) -> OrderStub:
        self.replaced_order_id = order_id
        self.replace_request = order_data
        return OrderStub(
            client_order_id="paper:v1:AAPL:stop:2",
            id=order_id,
            symbol="AAPL",
            side="sell",
            status="accepted",
            qty="5",
        )

    def cancel_order_by_id(self, order_id: str) -> None:
        self.cancelled_order_id = order_id


def _request_payload(request: Any) -> dict[str, Any]:
    if isinstance(request, dict):
        raw = dict(request)
    else:
        raw = {
            name: getattr(request, name)
            for name in dir(request)
            if not name.startswith("_") and not callable(getattr(request, name))
        }

    payload: dict[str, Any] = {}
    for key, value in raw.items():
        payload[key] = value.value if hasattr(value, "value") else value
    return payload


def _assert_contains(actual: dict[str, Any], expected: dict[str, Any]) -> None:
    for key, value in expected.items():
        assert actual.get(key) == value


def test_submit_stop_limit_entry_builds_stop_limit_buy_request_and_normalizes_order() -> None:
    trading_client = TradingClientWriteStub()
    adapter = AlpacaExecutionAdapter(trading_client=trading_client)

    order = adapter.submit_stop_limit_entry(
        symbol="AAPL",
        qty=5,
        stop_price=101.25,
        limit_price=101.5,
        client_order_id="paper:v1:AAPL:entry",
    )

    _assert_contains(_request_payload(trading_client.submitted_order), {
        "symbol": "AAPL",
        "qty": 5,
        "side": "buy",
        "type": "stop_limit",
        "stop_price": 101.25,
        "limit_price": 101.5,
        "client_order_id": "paper:v1:AAPL:entry",
        "time_in_force": "day",
    })
    assert order == BrokerOrder(
        client_order_id="paper:v1:AAPL:entry",
        broker_order_id="broker-entry-1",
        symbol="AAPL",
        side="buy",
        status="accepted",
        quantity=5,
    )


def test_submit_stop_order_builds_stop_sell_request_and_normalizes_order() -> None:
    trading_client = TradingClientWriteStub()
    adapter = AlpacaExecutionAdapter(trading_client=trading_client)

    order = adapter.submit_stop_order(
        symbol="AAPL",
        qty=5,
        stop_price=97.4,
        client_order_id="paper:v1:AAPL:stop:1",
    )

    _assert_contains(_request_payload(trading_client.submitted_order), {
        "symbol": "AAPL",
        "qty": 5,
        "side": "sell",
        "type": "stop",
        "stop_price": 97.4,
        "client_order_id": "paper:v1:AAPL:stop:1",
        "time_in_force": "day",
    })
    assert order == BrokerOrder(
        client_order_id="paper:v1:AAPL:stop:1",
        broker_order_id="broker-entry-1",
        symbol="AAPL",
        side="sell",
        status="accepted",
        quantity=5,
    )


def test_replace_order_builds_replace_request_with_stop_limit_and_client_order_id() -> None:
    trading_client = TradingClientWriteStub()
    adapter = AlpacaExecutionAdapter(trading_client=trading_client)

    order = adapter.replace_order(
        order_id="broker-stop-1",
        stop_price=98.1,
        limit_price=97.95,
        client_order_id="paper:v1:AAPL:stop:2",
    )

    assert trading_client.replaced_order_id == "broker-stop-1"
    _assert_contains(_request_payload(trading_client.replace_request), {
        "stop_price": 98.1,
        "limit_price": 97.95,
        "client_order_id": "paper:v1:AAPL:stop:2",
    })
    assert order == BrokerOrder(
        client_order_id="paper:v1:AAPL:stop:2",
        broker_order_id="broker-stop-1",
        symbol="AAPL",
        side="sell",
        status="accepted",
        quantity=5,
    )


def test_cancel_order_delegates_to_cancel_order_by_id() -> None:
    trading_client = TradingClientWriteStub()
    adapter = AlpacaExecutionAdapter(trading_client=trading_client)

    adapter.cancel_order("broker-stop-1")

    assert trading_client.cancelled_order_id == "broker-stop-1"


def test_submit_market_exit_builds_market_sell_request_and_normalizes_order() -> None:
    trading_client = TradingClientWriteStub()
    adapter = AlpacaExecutionAdapter(trading_client=trading_client)

    order = adapter.submit_market_exit(
        symbol="AAPL",
        qty=5,
        client_order_id="paper:v1:AAPL:exit:1",
    )

    _assert_contains(_request_payload(trading_client.submitted_order), {
        "symbol": "AAPL",
        "qty": 5,
        "side": "sell",
        "type": "market",
        "client_order_id": "paper:v1:AAPL:exit:1",
        "time_in_force": "day",
    })
    assert order == BrokerOrder(
        client_order_id="paper:v1:AAPL:exit:1",
        broker_order_id="broker-entry-1",
        symbol="AAPL",
        side="sell",
        status="accepted",
        quantity=5,
    )


class TradingClientReadStub:
    def __init__(self, orders: list[Any]) -> None:
        self._orders = orders
        self.last_filter: Any = None

    def get_orders(self, filter: Any = None) -> list[Any]:
        self.last_filter = filter
        return self._orders


def test_get_open_orders_for_symbol_returns_orders_matching_symbol() -> None:
    stub_order = OrderStub(
        client_order_id="v1:breakout:2026-01-02:AAPL:stop:2026-01-02T10:00:00",
        id="broker-stop-1",
        symbol="AAPL",
        side="sell",
        status="accepted",
        qty="10",
    )
    trading_client = TradingClientReadStub(orders=[stub_order])
    adapter = AlpacaExecutionAdapter(trading_client=trading_client)

    orders = adapter.get_open_orders_for_symbol("AAPL")

    assert orders == [
        BrokerOrder(
            client_order_id="v1:breakout:2026-01-02:AAPL:stop:2026-01-02T10:00:00",
            broker_order_id="broker-stop-1",
            symbol="AAPL",
            side="sell",
            status="accepted",
            quantity=10,
        )
    ]
