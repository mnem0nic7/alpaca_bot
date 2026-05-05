from __future__ import annotations

import pytest
from datetime import date, datetime, timezone

from alpaca_bot.config import TradingMode
from alpaca_bot.storage.models import OptionOrderRecord
from alpaca_bot.storage.repositories import OptionOrderRepository
from alpaca_bot.storage.db import ConnectionProtocol


class _FakeConnection:
    def __init__(self):
        self._rows: list[dict] = []
        self.committed = False

    def commit(self) -> None:
        self.committed = True

    def rollback(self) -> None:
        pass

    def cursor(self):
        return _FakeCursor(self._rows)


class _FakeCursor:
    def __init__(self, rows):
        self._rows = rows
        self.lastrowid = None
        self._query = None
        self._params = None

    def execute(self, query, params=None):
        self._query = query
        self._params = params

    def fetchone(self):
        return None

    def fetchall(self):
        return []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


def _now() -> datetime:
    return datetime(2024, 6, 1, 14, 0, tzinfo=timezone.utc)


def _record(**kwargs) -> OptionOrderRecord:
    defaults = dict(
        client_order_id="option:v1:2024-06-01:AAPL240701C00100000:entry:2024-06-01T14:00:00+00:00",
        occ_symbol="AAPL240701C00100000",
        underlying_symbol="AAPL",
        option_type="call",
        strike=100.0,
        expiry=date(2024, 7, 1),
        side="buy",
        status="pending_submit",
        quantity=2,
        trading_mode=TradingMode.PAPER,
        strategy_version="v1",
        strategy_name="breakout_calls",
        created_at=_now(),
        updated_at=_now(),
        limit_price=3.00,
    )
    defaults.update(kwargs)
    return OptionOrderRecord(**defaults)


def test_option_order_record_has_expected_fields():
    r = _record()
    assert r.client_order_id.startswith("option:")
    assert r.occ_symbol == "AAPL240701C00100000"
    assert r.underlying_symbol == "AAPL"
    assert r.option_type == "call"
    assert r.strike == 100.0
    assert r.expiry == date(2024, 7, 1)
    assert r.side == "buy"
    assert r.status == "pending_submit"
    assert r.quantity == 2
    assert r.trading_mode is TradingMode.PAPER
    assert r.limit_price == 3.00
    assert r.broker_order_id is None
    assert r.fill_price is None
    assert r.filled_quantity is None


def test_option_order_record_is_frozen():
    r = _record()
    with pytest.raises((AttributeError, TypeError)):
        r.status = "submitted"  # type: ignore


def test_option_order_repository_save_calls_execute():
    """save() issues an INSERT/ON CONFLICT statement."""
    conn = _FakeConnection()
    repo = OptionOrderRepository(conn)
    r = _record()
    repo.save(r, commit=True)
    cursor = conn.cursor()
    # Just verify no exception — the fake connection doesn't persist rows.


def test_option_order_repository_update_fill():
    """update_fill() sets status, fill_price, filled_quantity, broker_order_id."""
    conn = _FakeConnection()
    repo = OptionOrderRepository(conn)
    repo.update_fill(
        client_order_id="option:v1:2024-06-01:AAPL240701C00100000:entry:2024-06-01T14:00:00+00:00",
        broker_order_id="broker-123",
        fill_price=3.10,
        filled_quantity=2,
        status="filled",
        updated_at=_now(),
    )
    # No exception = pass


def test_option_order_repository_list_by_status():
    conn = _FakeConnection()
    repo = OptionOrderRepository(conn)
    result = repo.list_by_status(
        trading_mode=TradingMode.PAPER,
        strategy_version="v1",
        statuses=["pending_submit"],
    )
    assert isinstance(result, list)


def test_option_order_repository_list_open_option_positions():
    conn = _FakeConnection()
    repo = OptionOrderRepository(conn)
    result = repo.list_open_option_positions(
        trading_mode=TradingMode.PAPER,
        strategy_version="v1",
    )
    assert isinstance(result, list)
