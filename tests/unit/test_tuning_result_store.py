from __future__ import annotations

import json
from datetime import datetime, timezone


class _FakeCursor:
    def __init__(self, connection: "_FakeConn") -> None:
        self._conn = connection

    def execute(self, sql: str, params=None) -> None:
        self._conn.executed.append((sql, params))

    def fetchone(self):
        return self._conn.responses.pop(0) if self._conn.responses else None

    def fetchall(self):
        if not self._conn.responses:
            return []
        r = self._conn.responses.pop(0)
        return r if isinstance(r, list) else [r]


class _FakeConn:
    def __init__(self, responses=()) -> None:
        self.responses = list(responses)
        self.executed: list = []

    def cursor(self) -> _FakeCursor:
        return _FakeCursor(self)

    def commit(self) -> None:
        pass

    def rollback(self) -> None:
        pass


def test_load_latest_best_returns_all_fields() -> None:
    """load_latest_best must return all 7 fields including mean_return_pct and max_drawdown_pct."""
    from alpaca_bot.storage.repositories import TuningResultStore

    created = datetime(2026, 5, 9, 22, 35, tzinfo=timezone.utc)
    params = {"BREAKOUT_LOOKBACK_BARS": "20", "RELATIVE_VOLUME_THRESHOLD": "1.5"}
    row = (
        json.dumps(params),  # params (JSONB returned as str)
        0.52,               # score
        15,                 # total_trades
        0.60,               # win_rate
        0.018,              # mean_return_pct
        0.045,              # max_drawdown_pct
        1.3,                # sharpe_ratio
        created,            # created_at
    )
    conn = _FakeConn(responses=[row])
    store = TuningResultStore(conn)

    result = store.load_latest_best(trading_mode="paper")

    assert result is not None
    assert result["params"] == params
    assert result["score"] == 0.52
    assert result["total_trades"] == 15
    assert result["win_rate"] == 0.60
    assert result["mean_return_pct"] == 0.018
    assert result["max_drawdown_pct"] == 0.045
    assert result["sharpe_ratio"] == 1.3
    assert result["created_at"] == created


def test_load_latest_best_returns_none_when_no_rows() -> None:
    """load_latest_best returns None when no is_best rows exist."""
    from alpaca_bot.storage.repositories import TuningResultStore

    conn = _FakeConn(responses=[None])
    store = TuningResultStore(conn)

    result = store.load_latest_best(trading_mode="paper")

    assert result is None


def test_load_latest_best_parses_dict_params_directly() -> None:
    """load_latest_best handles params returned as a Python dict (psycopg2 JSONB auto-decode)."""
    from alpaca_bot.storage.repositories import TuningResultStore

    created = datetime(2026, 5, 9, 22, 35, tzinfo=timezone.utc)
    params = {"DAILY_SMA_PERIOD": "20"}
    row = (
        params,    # params already a dict (psycopg2 JSONB → dict)
        0.3,       # score
        5,         # total_trades
        0.4,       # win_rate
        None,      # mean_return_pct (null in DB)
        None,      # max_drawdown_pct (null in DB)
        None,      # sharpe_ratio (null in DB)
        created,   # created_at
    )
    conn = _FakeConn(responses=[row])
    store = TuningResultStore(conn)

    result = store.load_latest_best(trading_mode="paper")

    assert result is not None
    assert result["params"] == params
    assert result["mean_return_pct"] is None
    assert result["max_drawdown_pct"] is None
    assert result["sharpe_ratio"] is None
