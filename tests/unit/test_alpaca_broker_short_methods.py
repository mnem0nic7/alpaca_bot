from __future__ import annotations

from dataclasses import dataclass, field
from unittest.mock import MagicMock


@dataclass
class FakeTradingClient:
    submitted: list = field(default_factory=list)

    def submit_order(self, request):
        self.submitted.append(request)
        result = MagicMock()
        result.id = "fake-broker-id"
        result.client_order_id = getattr(request, "client_order_id", "coid")
        result.status = MagicMock()
        result.status.__str__ = lambda s: "accepted"
        result.filled_qty = "0"
        result.filled_avg_price = None
        result.limit_price = None
        result.stop_price = getattr(request, "stop_price", None)
        result.qty = getattr(request, "qty", None)
        return result


def _make_broker(fake_client):
    from alpaca_bot.execution.alpaca import AlpacaBroker
    broker = object.__new__(AlpacaBroker)
    broker._trading = fake_client
    broker._data = MagicMock()
    return broker


def test_submit_buy_stop_order_uses_buy_side():
    from alpaca.trading.enums import OrderSide
    fake = FakeTradingClient()
    broker = _make_broker(fake)
    broker.submit_buy_stop_order(
        symbol="QBTS",
        quantity=100,
        stop_price=6.05,
        client_order_id="test-coid-1",
    )
    assert len(fake.submitted) == 1
    req = fake.submitted[0]
    assert req.side == OrderSide.BUY
    assert float(req.stop_price) == 6.05


def test_submit_market_buy_to_cover_uses_buy_side():
    from alpaca.trading.enums import OrderSide
    fake = FakeTradingClient()
    broker = _make_broker(fake)
    broker.submit_market_buy_to_cover(
        symbol="QBTS",
        quantity=100,
        client_order_id="test-coid-2",
    )
    assert len(fake.submitted) == 1
    req = fake.submitted[0]
    assert req.side == OrderSide.BUY


def test_submit_option_market_buy_to_close_uses_buy_side():
    from alpaca.trading.enums import OrderSide
    fake = FakeTradingClient()
    broker = _make_broker(fake)
    broker.submit_option_market_buy_to_close(
        occ_symbol="ALHC250620P00005000",
        quantity=1,
        client_order_id="test-coid-3",
    )
    assert len(fake.submitted) == 1
    req = fake.submitted[0]
    assert req.side == OrderSide.BUY
    assert req.symbol == "ALHC250620P00005000"
