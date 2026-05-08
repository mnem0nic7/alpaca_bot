from __future__ import annotations

from datetime import date

from alpaca_bot.config import TradingMode
from alpaca_bot.storage.repositories import OptionOrderRepository


class _FakeCursor:
    def __init__(self, rows):
        self._rows = rows
        self.last_params: tuple | None = None

    def execute(self, query, params=None):
        self.last_params = params

    def fetchall(self):
        return list(self._rows)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


def _repo(rows=None) -> tuple[OptionOrderRepository, _FakeCursor]:
    cursor = _FakeCursor(rows or [])

    class _Conn:
        def commit(self): pass
        def rollback(self): pass
        def cursor(self): return cursor

    return OptionOrderRepository(_Conn()), cursor


def test_returns_empty_when_no_closed_sells():
    repo, _ = _repo(rows=[])
    result = repo.list_trade_pnl_by_strategy(
        trading_mode=TradingMode.PAPER,
        strategy_version="v1",
        start_date=date(2026, 1, 1),
        end_date=date(2026, 5, 7),
    )
    assert result == []


def test_returns_correct_pnl_for_matched_buy_sell():
    # row: (strategy_name, exit_date, qty, exit_fill, entry_fill)
    rows = [("breakout_calls", date(2026, 4, 1), 3, 3.50, 2.00)]
    repo, _ = _repo(rows=rows)
    result = repo.list_trade_pnl_by_strategy(
        trading_mode=TradingMode.PAPER,
        strategy_version="v1",
        start_date=date(2026, 1, 1),
        end_date=date(2026, 5, 7),
    )
    assert len(result) == 1
    r = result[0]
    assert r["strategy_name"] == "breakout_calls"
    assert r["exit_date"] == date(2026, 4, 1)
    # (3.50 - 2.00) * 3 * 100 = 450.0
    assert abs(r["pnl"] - 450.0) < 1e-6


def test_excludes_unmatched_sells():
    # entry_fill is None → no correlated buy fill → must be excluded
    rows = [("breakout_calls", date(2026, 4, 1), 3, 3.50, None)]
    repo, _ = _repo(rows=rows)
    result = repo.list_trade_pnl_by_strategy(
        trading_mode=TradingMode.PAPER,
        strategy_version="v1",
        start_date=date(2026, 1, 1),
        end_date=date(2026, 5, 7),
    )
    assert result == []


def test_respects_date_range():
    repo, cursor = _repo(rows=[])
    repo.list_trade_pnl_by_strategy(
        trading_mode=TradingMode.PAPER,
        strategy_version="v1",
        start_date=date(2026, 3, 1),
        end_date=date(2026, 4, 30),
    )
    params = cursor.last_params
    assert date(2026, 3, 1) in params, "start_date must be passed as SQL param"
    assert date(2026, 4, 30) in params, "end_date must be passed as SQL param"


def test_respects_trading_mode_and_strategy_version():
    repo, cursor = _repo(rows=[])
    repo.list_trade_pnl_by_strategy(
        trading_mode=TradingMode.LIVE,
        strategy_version="v2",
        start_date=date(2026, 1, 1),
        end_date=date(2026, 5, 7),
    )
    params = cursor.last_params
    assert "live" in params, "trading_mode value must be passed as SQL param"
    assert "v2" in params, "strategy_version must be passed as SQL param"
