from __future__ import annotations

from datetime import datetime, timezone

import pytest

from alpaca_bot.config import TradingMode
from alpaca_bot.storage.models import ConfidenceFloor
from alpaca_bot.storage.repositories import ConfidenceFloorStore


class _FakeConn:
    def __init__(self):
        self._rows: list[tuple] = []
        self._last_sql: str = ""
        self._last_params: tuple = ()

    def cursor(self):
        return _FakeCursor(self)

    def commit(self): pass
    def rollback(self): pass


class _FakeCursor:
    def __init__(self, conn: _FakeConn):
        self._conn = conn
        self.description = None
        self._result: list[tuple] = []

    def execute(self, sql: str, params: tuple = ()):
        self._conn._last_sql = sql
        self._conn._last_params = params
        if "SELECT" in sql.upper():
            self._result = list(self._conn._rows)
        else:
            if "INSERT" in sql.upper():
                self._conn._rows = [params[:9]]

    def fetchone(self):
        return self._result[0] if self._result else None

    def fetchall(self):
        return list(self._result)

    def __enter__(self): return self
    def __exit__(self, *a): pass


def _make_floor(**kwargs) -> ConfidenceFloor:
    defaults = dict(
        trading_mode=TradingMode.PAPER,
        strategy_version="v1",
        floor_value=0.25,
        manual_floor_baseline=0.25,
        equity_high_watermark=10000.0,
        set_by="operator",
        reason="test",
        updated_at=datetime(2026, 5, 8, 12, 0, tzinfo=timezone.utc),
    )
    defaults.update(kwargs)
    return ConfidenceFloor(**defaults)


def test_confidence_floor_load_returns_none_when_empty() -> None:
    conn = _FakeConn()
    store = ConfidenceFloorStore(conn)
    result = store.load(trading_mode=TradingMode.PAPER, strategy_version="v1")
    assert result is None


def test_confidence_floor_upsert_then_load() -> None:
    conn = _FakeConn()
    store = ConfidenceFloorStore(conn)
    rec = _make_floor(floor_value=0.40)
    store.upsert(rec)
    # Simulate that the row is now in the fake DB
    conn._rows = [(
        "paper", "v1", 0.40, 0.25, 10000.0, "operator", "test",
        datetime(2026, 5, 8, 12, 0, tzinfo=timezone.utc),
        None,
    )]
    loaded = store.load(trading_mode=TradingMode.PAPER, strategy_version="v1")
    assert loaded is not None
    assert loaded.floor_value == pytest.approx(0.40)
    assert loaded.manual_floor_baseline == pytest.approx(0.25)
    assert loaded.set_by == "operator"
    assert loaded.equity_high_watermark == pytest.approx(10000.0)


def test_confidence_floor_round_trips_floor_raised_at() -> None:
    raised_at = datetime(2026, 6, 1, 14, 30, tzinfo=timezone.utc)
    rec = ConfidenceFloor(
        trading_mode=TradingMode.PAPER,
        strategy_version="v1",
        floor_value=0.45,
        manual_floor_baseline=0.25,
        equity_high_watermark=100_000.0,
        set_by="system",
        reason="test",
        updated_at=datetime(2026, 6, 1, 14, 31, tzinfo=timezone.utc),
        floor_raised_at=raised_at,
    )
    assert rec.floor_raised_at == raised_at


def test_confidence_floor_raised_at_defaults_to_none() -> None:
    rec = ConfidenceFloor(
        trading_mode=TradingMode.PAPER,
        strategy_version="v1",
        floor_value=0.25,
        manual_floor_baseline=0.25,
        equity_high_watermark=100_000.0,
        set_by="operator",
        reason="manual",
    )
    assert rec.floor_raised_at is None


def test_confidence_floor_store_round_trips_floor_raised_at() -> None:
    conn = _FakeConn()
    store = ConfidenceFloorStore(conn)
    raised_at = datetime(2026, 6, 1, 14, 30, tzinfo=timezone.utc)
    store.upsert(_make_floor(set_by="system", floor_raised_at=raised_at))
    assert "floor_raised_at" in conn._last_sql  # INSERT mentions the column
    loaded = store.load(trading_mode=TradingMode.PAPER, strategy_version="v1")
    assert "floor_raised_at" in conn._last_sql  # SELECT mentions the column
    assert loaded is not None
    assert loaded.floor_raised_at == raised_at


def test_migration_023_adds_floor_raised_at() -> None:
    from pathlib import Path

    path = Path(__file__).resolve().parents[2] / "migrations" / "023_add_floor_raised_at.sql"
    sql = path.read_text()
    assert "ALTER TABLE confidence_floor_store" in sql
    assert "floor_raised_at" in sql
    assert "TIMESTAMPTZ" in sql


def test_confidence_floor_raises_stored_correctly() -> None:
    conn = _FakeConn()
    store = ConfidenceFloorStore(conn)
    rec = _make_floor(floor_value=0.50, set_by="system", reason="drawdown trigger")
    store.upsert(rec)
    assert "INSERT" in conn._last_sql.upper()
    # params: (trading_mode, strategy_version, floor_value, manual_floor_baseline, equity_high_watermark, set_by, reason, updated_at)
    params = conn._last_params
    assert params[2] == pytest.approx(0.50)
    assert params[5] == "system"
