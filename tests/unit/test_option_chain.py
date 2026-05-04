from __future__ import annotations

import pytest
from datetime import date, datetime, timezone

from alpaca_bot.domain.models import OptionContract
from alpaca_bot.execution.option_chain import (
    OptionChainAdapterProtocol,
    AlpacaOptionChainAdapter,
)
from tests.unit.helpers import _base_env
from alpaca_bot.config import Settings


def _settings(**overrides) -> Settings:
    env = _base_env()
    env.update(overrides)
    return Settings.from_env(env)


class _FakeSnapshotClient:
    """Fake option data client — returns minimal snapshot data."""
    def __init__(self, snapshots: dict):
        self._snapshots = snapshots

    def get_option_chain(self, request):
        return self._snapshots


def _make_snapshot(ask: float, delta: float | None = None):
    """Build a minimal fake Alpaca OptionSnapshot-like object.

    Note: strike, expiry, and option_type are parsed from the OCC symbol key
    by AlpacaOptionChainAdapter — the real OptionsSnapshot has no 'details'
    field. Only quote and greeks are needed on the fake.
    """
    class FakeGreeks:
        def __init__(self, delta):
            self.delta = delta

    class FakeQuote:
        def __init__(self, ask):
            self.ask_price = ask
            self.bid_price = ask - 0.10

    class FakeSnapshot:
        def __init__(self, ask, delta):
            self.greeks = FakeGreeks(delta) if delta is not None else None
            self.latest_quote = FakeQuote(ask)

    return FakeSnapshot(ask, delta)


class TestAlpacaOptionChainAdapter:
    def test_returns_empty_list_when_no_snapshots(self):
        client = _FakeSnapshotClient({})
        adapter = AlpacaOptionChainAdapter(client)
        s = _settings()
        result = adapter.get_option_chain("AAPL", s)
        assert result == []

    def test_converts_snapshot_to_option_contract(self):
        expiry = date(2024, 7, 1)
        occ = "AAPL240701C00150000"
        snapshots = {occ: _make_snapshot(ask=3.00, delta=0.50)}
        client = _FakeSnapshotClient(snapshots)
        adapter = AlpacaOptionChainAdapter(client)
        s = _settings()
        result = adapter.get_option_chain("AAPL", s)
        assert len(result) == 1
        c = result[0]
        assert isinstance(c, OptionContract)
        assert c.occ_symbol == occ
        assert c.underlying == "AAPL"
        assert c.strike == 150.0
        assert c.expiry == expiry
        assert c.ask == 3.00
        assert c.delta == 0.50

    def test_delta_is_none_when_greeks_unavailable(self):
        occ = "AAPL240701C00150000"
        snapshots = {occ: _make_snapshot(ask=3.00, delta=None)}
        client = _FakeSnapshotClient(snapshots)
        adapter = AlpacaOptionChainAdapter(client)
        s = _settings()
        result = adapter.get_option_chain("AAPL", s)
        assert result[0].delta is None

    def test_satisfies_protocol(self):
        client = _FakeSnapshotClient({})
        adapter = AlpacaOptionChainAdapter(client)
        assert isinstance(adapter, OptionChainAdapterProtocol)


class TestAlpacaExecutionAdapterOptionMethods:
    def test_submit_option_limit_entry_calls_submit_order(self):
        from alpaca_bot.execution.alpaca import AlpacaExecutionAdapter
        from tests.unit.helpers import _base_env
        from alpaca_bot.config import Settings

        submitted = []

        class FakeTradingClient:
            def submit_order(self, order_data):
                submitted.append(order_data)

                class FakeOrder:
                    id = "broker-456"
                    client_order_id = "option:v1:2024-06-01:AAPL240701C00100000:entry:2024-06-01T14:00:00+00:00"
                    symbol = "AAPL240701C00100000"
                    side = "buy"
                    status = "accepted"
                    qty = 2
                return FakeOrder()

        adapter = AlpacaExecutionAdapter(FakeTradingClient(), settings=Settings.from_env(_base_env()))
        result = adapter.submit_option_limit_entry(
            occ_symbol="AAPL240701C00100000",
            quantity=2,
            limit_price=3.00,
            client_order_id="option:v1:2024-06-01:AAPL240701C00100000:entry:2024-06-01T14:00:00+00:00",
        )
        assert len(submitted) == 1
        assert result.broker_order_id == "broker-456"

    def test_submit_option_market_exit_calls_submit_order(self):
        from alpaca_bot.execution.alpaca import AlpacaExecutionAdapter
        from tests.unit.helpers import _base_env
        from alpaca_bot.config import Settings

        submitted = []

        class FakeTradingClient:
            def submit_order(self, order_data):
                submitted.append(order_data)

                class FakeOrder:
                    id = "broker-789"
                    client_order_id = "option:v1:2024-06-01:AAPL240701C00100000:sell:2024-06-01T15:50:00+00:00"
                    symbol = "AAPL240701C00100000"
                    side = "sell"
                    status = "accepted"
                    qty = 2
                return FakeOrder()

        adapter = AlpacaExecutionAdapter(FakeTradingClient(), settings=Settings.from_env(_base_env()))
        result = adapter.submit_option_market_exit(
            occ_symbol="AAPL240701C00100000",
            quantity=2,
            client_order_id="option:v1:2024-06-01:AAPL240701C00100000:sell:2024-06-01T15:50:00+00:00",
        )
        assert len(submitted) == 1
        assert result.broker_order_id == "broker-789"
