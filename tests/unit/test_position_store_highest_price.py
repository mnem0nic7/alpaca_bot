from __future__ import annotations

from datetime import datetime, timezone

from alpaca_bot.config import TradingMode
from alpaca_bot.storage.models import PositionRecord
from alpaca_bot.storage.repositories import PositionStore


def test_position_record_has_highest_price_field():
    """PositionRecord must carry highest_price through the storage layer."""
    rec = PositionRecord(
        symbol="AAPL",
        trading_mode=TradingMode.PAPER,
        strategy_version="v1",
        quantity=10.0,
        entry_price=3.00,
        stop_price=2.97,
        initial_stop_price=2.97,
        opened_at=datetime(2026, 5, 1, tzinfo=timezone.utc),
        highest_price=3.20,
    )
    assert rec.highest_price == 3.20


def test_position_record_highest_price_defaults_to_none():
    rec = PositionRecord(
        symbol="AAPL",
        trading_mode=TradingMode.PAPER,
        strategy_version="v1",
        quantity=10.0,
        entry_price=3.00,
        stop_price=2.97,
        initial_stop_price=2.97,
        opened_at=datetime(2026, 5, 1, tzinfo=timezone.utc),
    )
    assert rec.highest_price is None


# ---------------------------------------------------------------------------
# Repository-layer helpers
# ---------------------------------------------------------------------------

def _make_record(**overrides) -> PositionRecord:
    base = dict(
        symbol="AAPL",
        trading_mode=TradingMode.PAPER,
        strategy_version="v1",
        quantity=10.0,
        entry_price=3.00,
        stop_price=2.97,
        initial_stop_price=2.97,
        opened_at=datetime(2026, 5, 1, tzinfo=timezone.utc),
    )
    base.update(overrides)
    return PositionRecord(**base)


class _FakeCursor:
    def __init__(self, rows=None):
        self._rows = rows or []
        self.executed: list[tuple] = []

    def execute(self, sql, params=None):
        self.executed.append((sql, params))

    def fetchall(self):
        return list(self._rows)

    def __enter__(self): return self
    def __exit__(self, *a): pass


class _FakeConn:
    def __init__(self, rows=None):
        self._cursor = _FakeCursor(rows)
        self.committed = False
        self.rolled_back = False

    def cursor(self): return self._cursor
    def commit(self): self.committed = True
    def rollback(self): self.rolled_back = True


# ---------------------------------------------------------------------------
# PositionStore tests
# ---------------------------------------------------------------------------

def test_save_includes_highest_price_in_insert():
    conn = _FakeConn()
    store = PositionStore(conn)
    rec = _make_record(highest_price=3.20)
    store.save(rec)
    sql, params = conn._cursor.executed[0]
    assert "highest_price" in sql
    assert 3.20 in params


def test_save_with_none_highest_price_passes_none():
    conn = _FakeConn()
    store = PositionStore(conn)
    rec = _make_record(highest_price=None)
    store.save(rec)
    _, params = conn._cursor.executed[0]
    assert None in params


def test_list_all_populates_highest_price():
    row = (
        "AAPL", "paper", "v1", "breakout",
        10.0, 3.00, 2.97, 2.97,
        datetime(2026, 5, 1, tzinfo=timezone.utc),
        datetime(2026, 5, 1, tzinfo=timezone.utc),
        3.20,  # highest_price
        None,  # lowest_price
    )
    conn = _FakeConn(rows=[row])
    store = PositionStore(conn)
    records = store.list_all(trading_mode=TradingMode.PAPER, strategy_version="v1")
    assert len(records) == 1
    assert records[0].highest_price == 3.20


def test_list_all_handles_null_highest_price():
    row = (
        "AAPL", "paper", "v1", "breakout",
        10.0, 3.00, 2.97, 2.97,
        datetime(2026, 5, 1, tzinfo=timezone.utc),
        datetime(2026, 5, 1, tzinfo=timezone.utc),
        None,  # highest_price is NULL in DB
        None,  # lowest_price
    )
    conn = _FakeConn(rows=[row])
    store = PositionStore(conn)
    records = store.list_all(trading_mode=TradingMode.PAPER, strategy_version="v1")
    assert records[0].highest_price is None


def test_update_highest_price_issues_targeted_update():
    conn = _FakeConn()
    store = PositionStore(conn)
    store.update_highest_price(
        symbol="AAPL",
        trading_mode=TradingMode.PAPER,
        strategy_version="v1",
        strategy_name="breakout",
        highest_price=3.50,
    )
    assert conn.committed
    sql, params = conn._cursor.executed[0]
    assert "UPDATE positions" in sql
    assert "highest_price" in sql
    assert 3.50 in params
    assert "AAPL" in params


def test_save_on_conflict_coalesce_preserves_existing_highest_price():
    """ON CONFLICT clause must use COALESCE so a stop update never overwrites an accumulated high."""
    conn = _FakeConn()
    store = PositionStore(conn)
    rec = _make_record(highest_price=None)
    store.save(rec)
    sql, _ = conn._cursor.executed[0]
    assert "COALESCE" in sql
