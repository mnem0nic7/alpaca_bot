from __future__ import annotations

from datetime import date, datetime, timezone
from types import SimpleNamespace

from alpaca_bot.config import TradingMode
from alpaca_bot.storage.models import DailySessionState, OrderRecord, PositionRecord
from alpaca_bot.storage.repositories import DailySessionStateStore, OrderStore, PositionStore


class FakeCursor:
    def __init__(self):
        self.rows = []
        self.executed = []

    def execute(self, sql, params=()):
        self.executed.append((sql, params))

    def fetchall(self):
        return self.rows

    def fetchone(self):
        return self.rows[0] if self.rows else None


class FakeConnection:
    def __init__(self):
        self._cursor = FakeCursor()

    def cursor(self):
        return self._cursor

    def commit(self):
        pass


def _make_position(strategy_name: str = "breakout") -> PositionRecord:
    return PositionRecord(
        symbol="AAPL",
        trading_mode=TradingMode.PAPER,
        strategy_version="v1",
        strategy_name=strategy_name,
        quantity=10,
        entry_price=150.0,
        stop_price=148.0,
        initial_stop_price=147.0,
        opened_at=datetime(2026, 1, 2, 14, tzinfo=timezone.utc),
    )


def test_position_record_has_strategy_name():
    pos = _make_position("momentum")
    assert pos.strategy_name == "momentum"


def test_position_record_default_strategy_name():
    pos = PositionRecord(
        symbol="AAPL",
        trading_mode=TradingMode.PAPER,
        strategy_version="v1",
        quantity=10,
        entry_price=150.0,
        stop_price=148.0,
        initial_stop_price=147.0,
        opened_at=datetime(2026, 1, 2, 14, tzinfo=timezone.utc),
    )
    assert pos.strategy_name == "breakout"


def test_order_record_has_strategy_name():
    order = OrderRecord(
        client_order_id="breakout:v1:2026-01-02:AAPL:entry:ts",
        symbol="AAPL",
        side="buy",
        intent_type="entry",
        status="pending_submit",
        quantity=10,
        trading_mode=TradingMode.PAPER,
        strategy_version="v1",
        strategy_name="momentum",
    )
    assert order.strategy_name == "momentum"


def test_order_record_default_strategy_name():
    order = OrderRecord(
        client_order_id="v1:2026-01-02:AAPL:entry:ts",
        symbol="AAPL",
        side="buy",
        intent_type="entry",
        status="pending_submit",
        quantity=10,
        trading_mode=TradingMode.PAPER,
        strategy_version="v1",
    )
    assert order.strategy_name == "breakout"


def test_daily_session_state_has_strategy_name():
    state = DailySessionState(
        session_date=date(2026, 1, 2),
        trading_mode=TradingMode.PAPER,
        strategy_version="v1",
        strategy_name="momentum",
        entries_disabled=False,
        flatten_complete=False,
    )
    assert state.strategy_name == "momentum"


def test_position_store_save_includes_strategy_name():
    conn = FakeConnection()
    store = PositionStore(conn)
    store.save(_make_position("momentum"))
    sql, params = conn._cursor.executed[0]
    assert "strategy_name" in sql
    assert "momentum" in params


def test_position_store_list_all_filters_by_strategy_name():
    conn = FakeConnection()
    conn._cursor.rows = []
    store = PositionStore(conn)
    result = store.list_all(
        trading_mode=TradingMode.PAPER,
        strategy_version="v1",
        strategy_name="momentum",
    )
    sql, params = conn._cursor.executed[0]
    assert "strategy_name" in sql
    assert "momentum" in params
    assert result == []


def test_daily_session_state_store_load_filters_by_strategy_name():
    conn = FakeConnection()
    conn._cursor.rows = []
    store = DailySessionStateStore(conn)
    result = store.load(
        session_date=date(2026, 1, 2),
        trading_mode=TradingMode.PAPER,
        strategy_version="v1",
        strategy_name="momentum",
    )
    sql, params = conn._cursor.executed[0]
    assert "strategy_name" in sql
    assert "momentum" in params
    assert result is None


def test_daily_session_state_store_save_includes_strategy_name():
    conn = FakeConnection()
    store = DailySessionStateStore(conn)
    state = DailySessionState(
        session_date=date(2026, 1, 2),
        trading_mode=TradingMode.PAPER,
        strategy_version="v1",
        strategy_name="momentum",
        entries_disabled=False,
        flatten_complete=False,
    )
    store.save(state)
    sql, params = conn._cursor.executed[0]
    assert "strategy_name" in sql
    assert "momentum" in params
