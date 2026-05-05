from __future__ import annotations

from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest

from alpaca_bot.config import TradingMode
from alpaca_bot.storage.db import (
    check_connection,
    connect_postgres_with_retry,
)
from alpaca_bot.storage import StrategyWeight, StrategyWeightStore
from alpaca_bot.storage.repositories import AuditEventStore, OrderStore


# ---------------------------------------------------------------------------
# Helpers / stubs
# ---------------------------------------------------------------------------

def _make_healthy_connection():
    """Return a SimpleNamespace stub that acts as a live psycopg connection."""
    cursor_stub = SimpleNamespace(
        execute=lambda sql, params=None: None,
        fetchone=lambda: (1,),
        fetchall=lambda: [(1,)],
    )
    return SimpleNamespace(
        cursor=lambda: cursor_stub,
        commit=lambda: None,
    )


def _make_dead_connection(exc_type=OSError):
    """Return a SimpleNamespace stub whose cursor.execute raises exc_type."""
    def _raise(sql, params=None):
        raise exc_type("connection dead")

    cursor_stub = SimpleNamespace(
        execute=_raise,
        fetchone=lambda: None,
        fetchall=lambda: [],
    )
    return SimpleNamespace(
        cursor=lambda: cursor_stub,
        commit=lambda: None,
    )


# ---------------------------------------------------------------------------
# connect_postgres_with_retry
# ---------------------------------------------------------------------------

class TestConnectPostgresWithRetry:
    def test_connect_postgres_with_retry_succeeds_on_first_attempt(self):
        """When connect_postgres succeeds immediately, it is called exactly once."""
        healthy_conn = _make_healthy_connection()
        calls: list[str] = []

        def fake_connect(url: str):
            calls.append(url)
            return healthy_conn

        result = connect_postgres_with_retry(
            "postgresql://test/db",
            _connect_fn=fake_connect,
            _sleep_fn=lambda _: None,
        )

        assert result is healthy_conn
        assert len(calls) == 1

    def test_connect_postgres_with_retry_succeeds_on_third_attempt_after_two_failures(self):
        """
        When the first two attempts raise an exception and the third succeeds,
        the function returns the successful connection and called connect 3 times.
        """
        healthy_conn = _make_healthy_connection()
        attempt_counter = {"n": 0}

        def fake_connect(url: str):
            attempt_counter["n"] += 1
            if attempt_counter["n"] < 3:
                raise OSError(f"transient failure #{attempt_counter['n']}")
            return healthy_conn

        sleep_calls: list[float] = []

        result = connect_postgres_with_retry(
            "postgresql://test/db",
            _connect_fn=fake_connect,
            _sleep_fn=sleep_calls.append,
        )

        assert result is healthy_conn
        assert attempt_counter["n"] == 3
        # Two sleeps between three attempts
        assert len(sleep_calls) == 2
        assert all(s == 2 for s in sleep_calls)

    def test_connect_postgres_with_retry_raises_after_all_attempts_exhausted(self):
        """
        When all three attempts fail the last exception is re-raised.
        """
        last_exc = OSError("final failure")
        attempt_counter = {"n": 0}

        def fake_connect(url: str):
            attempt_counter["n"] += 1
            raise OSError(f"failure #{attempt_counter['n']}")

        sleep_calls: list[float] = []

        with pytest.raises(OSError, match="failure #3"):
            connect_postgres_with_retry(
                "postgresql://test/db",
                _connect_fn=fake_connect,
                _sleep_fn=sleep_calls.append,
            )

        assert attempt_counter["n"] == 3
        # Two sleeps: between attempt 1→2 and 2→3
        assert len(sleep_calls) == 2

    def test_connect_postgres_with_retry_uses_two_second_sleep(self, monkeypatch):
        """
        Without an injected sleep function the real time.sleep(2) is called.
        We patch it and verify it receives 2 seconds.
        """
        import alpaca_bot.storage.db as db_module

        healthy_conn = _make_healthy_connection()
        attempt_counter = {"n": 0}

        def fake_connect(url: str):
            attempt_counter["n"] += 1
            if attempt_counter["n"] < 2:
                raise OSError("transient")
            return healthy_conn

        sleep_calls: list[float] = []
        monkeypatch.setattr(db_module.time, "sleep", sleep_calls.append)

        result = connect_postgres_with_retry("postgresql://test/db", _connect_fn=fake_connect)
        assert result is healthy_conn
        assert sleep_calls == [2]


# ---------------------------------------------------------------------------
# check_connection
# ---------------------------------------------------------------------------

class TestCheckConnection:
    def test_check_connection_returns_true_for_healthy_connection(self):
        """check_connection executes SELECT 1 and returns True when it succeeds."""
        executed: list[str] = []

        def _execute(sql, params=None):
            executed.append(sql)

        cursor_stub = SimpleNamespace(
            execute=_execute,
            fetchone=lambda: (1,),
            fetchall=lambda: [],
        )
        conn = SimpleNamespace(
            cursor=lambda: cursor_stub,
            commit=lambda: None,
        )

        assert check_connection(conn) is True
        assert executed == ["SELECT 1"]

    def test_check_connection_returns_false_when_query_raises(self):
        """check_connection returns False when the probe query raises any exception."""
        conn = _make_dead_connection(exc_type=OSError)
        assert check_connection(conn) is False

    def test_check_connection_returns_false_on_operational_error(self):
        """check_connection returns False for database-level errors (OperationalError-like)."""
        conn = _make_dead_connection(exc_type=RuntimeError)
        assert check_connection(conn) is False

    def test_check_connection_returns_false_when_cursor_itself_raises(self):
        """check_connection returns False when cursor() raises before execute() is called."""
        def _raising_cursor():
            raise OSError("cursor unavailable")

        conn = SimpleNamespace(
            cursor=_raising_cursor,
            commit=lambda: None,
        )

        assert check_connection(conn) is False


# ---------------------------------------------------------------------------
# Phase 1 — OrderStore.daily_realized_pnl()
# ---------------------------------------------------------------------------

def _make_fake_connection(rows: list[tuple]) -> SimpleNamespace:
    """Return a fake connection whose fetch_all returns *rows*."""
    executed: list[tuple] = []

    def _execute(sql, params=None):
        executed.append((sql, params))

    cursor_stub = SimpleNamespace(
        execute=_execute,
        fetchone=lambda: None,
        fetchall=lambda: list(rows),
    )
    conn = SimpleNamespace(
        cursor=lambda: cursor_stub,
        commit=lambda: None,
        _executed=executed,
    )
    return conn


class TestDailyRealizedPnl:
    SESSION_DATE = date(2026, 4, 25)
    MODE = TradingMode.PAPER
    STRATEGY = "v1-breakout"

    def _store(self, rows: list[tuple]) -> OrderStore:
        return OrderStore(_make_fake_connection(rows))

    def test_single_profitable_trade_returns_correct_pnl(self):
        """(exit_fill - entry_fill) × qty for a single winner."""
        # symbol, entry_fill, exit_fill, qty
        rows = [("AAPL", 150.00, 155.00, 10)]
        store = self._store(rows)
        pnl = store.daily_realized_pnl(
            trading_mode=self.MODE,
            strategy_version=self.STRATEGY,
            session_date=self.SESSION_DATE,
        )
        assert pnl == pytest.approx(50.00)

    def test_single_losing_trade_returns_negative_pnl(self):
        """Negative PnL when exit is below entry."""
        rows = [("AAPL", 155.00, 150.00, 10)]
        store = self._store(rows)
        pnl = store.daily_realized_pnl(
            trading_mode=self.MODE,
            strategy_version=self.STRATEGY,
            session_date=self.SESSION_DATE,
        )
        assert pnl == pytest.approx(-50.00)

    def test_two_symbols_sums_both_trades(self):
        """PnL from two different symbols is summed."""
        rows = [
            ("AAPL", 150.00, 155.00, 10),   # +50
            ("MSFT", 400.00, 395.00, 5),     # -25
        ]
        store = self._store(rows)
        pnl = store.daily_realized_pnl(
            trading_mode=self.MODE,
            strategy_version=self.STRATEGY,
            session_date=self.SESSION_DATE,
        )
        assert pnl == pytest.approx(25.00)

    def test_partial_fill_uses_filled_quantity_not_order_quantity(self):
        """qty column is COALESCE(filled_quantity, quantity) — here the DB row provides it directly."""
        rows = [("AAPL", 150.00, 156.00, 7)]  # partial fill of 7 shares
        store = self._store(rows)
        pnl = store.daily_realized_pnl(
            trading_mode=self.MODE,
            strategy_version=self.STRATEGY,
            session_date=self.SESSION_DATE,
        )
        assert pnl == pytest.approx(42.00)

    def test_no_trades_returns_zero(self):
        """When there are no completed trades, PnL must be 0.0."""
        store = self._store([])
        pnl = store.daily_realized_pnl(
            trading_mode=self.MODE,
            strategy_version=self.STRATEGY,
            session_date=self.SESSION_DATE,
        )
        assert pnl == 0.0

    def test_exit_with_null_entry_fill_treated_as_full_loss(self):
        """Rows where entry_fill (row[1]) is None must be counted as -(exit_fill × qty)
        to fail safe on the loss-limit check rather than silently understate losses."""
        rows = [
            ("AAPL", None, 155.00, 10),   # no entry fill → -(155 × 10) = -1550
            ("MSFT", 400.00, 405.00, 5),  # +25
        ]
        store = self._store(rows)
        pnl = store.daily_realized_pnl(
            trading_mode=self.MODE,
            strategy_version=self.STRATEGY,
            session_date=self.SESSION_DATE,
        )
        assert pnl == pytest.approx(-1525.00)

    def test_all_exits_null_entry_fill_returns_total_full_loss(self):
        """When every row lacks an entry fill the entire session P&L is negative."""
        rows = [
            ("AAPL", None, 100.00, 5),   # -(100 × 5) = -500
            ("MSFT", None, 200.00, 3),   # -(200 × 3) = -600
        ]
        store = self._store(rows)
        pnl = store.daily_realized_pnl(
            trading_mode=self.MODE,
            strategy_version=self.STRATEGY,
            session_date=self.SESSION_DATE,
        )
        assert pnl == pytest.approx(-1100.00)


# ---------------------------------------------------------------------------
# Phase 2 — OrderStore.list_closed_trades()
# ---------------------------------------------------------------------------


class TestListClosedTrades:
    SESSION_DATE = date(2026, 4, 25)
    MODE = TradingMode.PAPER
    STRATEGY = "v1-breakout"

    def _store(self, rows: list[tuple]) -> OrderStore:
        return OrderStore(_make_fake_connection(rows))

    def test_returns_one_dict_per_closed_trade(self):
        """Each DB row becomes a dict with symbol, fills, limit, times, qty, intent_type."""
        now = datetime(2026, 4, 25, 15, 0, tzinfo=timezone.utc)
        rows = [("AAPL", "breakout", "stop", 110.00, 111.00, now, 112.00, now, 10)]
        store = self._store(rows)
        trades = store.list_closed_trades(
            trading_mode=self.MODE,
            strategy_version=self.STRATEGY,
            session_date=self.SESSION_DATE,
        )
        assert len(trades) == 1
        trade = trades[0]
        assert trade["symbol"] == "AAPL"
        assert trade["strategy_name"] == "breakout"
        assert trade["intent_type"] == "stop"
        assert trade["entry_fill"] == pytest.approx(110.00)
        assert trade["entry_limit"] == pytest.approx(111.00)
        assert trade["exit_fill"] == pytest.approx(112.00)
        assert trade["qty"] == 10

    def test_excludes_rows_with_null_entry_fill(self):
        """Rows where entry_fill (col 3) is None are filtered out."""
        now = datetime(2026, 4, 25, 15, 0, tzinfo=timezone.utc)
        rows = [
            ("AAPL", "breakout", "stop", None, None, now, 112.00, now, 10),  # no entry fill → skip
            ("MSFT", "breakout", "exit", 400.00, 401.00, now, 405.00, now, 5),
        ]
        store = self._store(rows)
        trades = store.list_closed_trades(
            trading_mode=self.MODE,
            strategy_version=self.STRATEGY,
            session_date=self.SESSION_DATE,
        )
        assert len(trades) == 1
        assert trades[0]["symbol"] == "MSFT"

    def test_returns_empty_list_when_no_closed_trades(self):
        store = self._store([])
        trades = store.list_closed_trades(
            trading_mode=self.MODE,
            strategy_version=self.STRATEGY,
            session_date=self.SESSION_DATE,
        )
        assert trades == []

    def test_entry_limit_none_is_preserved(self):
        """entry_limit may be None for stop-only entries (no limit price)."""
        now = datetime(2026, 4, 25, 15, 0, tzinfo=timezone.utc)
        rows = [("AAPL", "breakout", "exit", 110.00, None, now, 112.00, now, 10)]
        store = self._store(rows)
        trades = store.list_closed_trades(
            trading_mode=self.MODE,
            strategy_version=self.STRATEGY,
            session_date=self.SESSION_DATE,
        )
        assert trades[0]["entry_limit"] is None


# ---------------------------------------------------------------------------
# Phase 2 — AuditEventStore.list_by_event_types()
# ---------------------------------------------------------------------------


class TestListTradeExitsInRange:
    def _store(self, rows: list[tuple]) -> OrderStore:
        return OrderStore(_make_fake_connection(rows))

    def test_list_trade_exits_in_range_empty(self):
        """Empty result when no rows returned."""
        store = self._store([])
        result = store.list_trade_exits_in_range(
            trading_mode=TradingMode.PAPER,
            strategy_version="v1",
            start_date=date(2026, 1, 1),
            end_date=date(2026, 1, 31),
        )
        assert result == []

    def test_list_trade_exits_in_range_filters_null_entry(self):
        """Rows where entry_fill is None are filtered out."""
        now_1 = datetime(2026, 1, 2, 14, 0, tzinfo=timezone.utc)
        now_2 = datetime(2026, 1, 2, 15, 0, tzinfo=timezone.utc)
        rows = [
            (now_1, 10, 105.0, None),   # entry_fill is None → skip
            (now_2, 5, 110.0, 100.0),  # valid
        ]
        store = self._store(rows)
        result = store.list_trade_exits_in_range(
            trading_mode=TradingMode.PAPER,
            strategy_version="v1",
            start_date=date(2026, 1, 1),
            end_date=date(2026, 1, 31),
        )
        assert len(result) == 1
        assert abs(result[0]["pnl"] - 50.0) < 0.01

    def test_list_trade_exits_in_range_calculates_pnl(self):
        """PnL is calculated as (exit_fill - entry_fill) * qty."""
        now = datetime(2026, 1, 2, 15, 0, tzinfo=timezone.utc)
        rows = [
            (now, 10, 115.0, 100.0),  # (115 - 100) * 10 = 150
        ]
        store = self._store(rows)
        result = store.list_trade_exits_in_range(
            trading_mode=TradingMode.PAPER,
            strategy_version="v1",
            start_date=date(2026, 1, 1),
            end_date=date(2026, 1, 31),
        )
        assert len(result) == 1
        assert abs(result[0]["pnl"] - 150.0) < 0.01


class TestListEquityBaselines:
    def _store(self, rows: list[tuple]):
        from alpaca_bot.storage.repositories import DailySessionStateStore
        return DailySessionStateStore(_make_fake_connection(rows))

    def test_list_equity_baselines_empty(self):
        """Empty result returns empty dict."""
        store = self._store([])
        result = store.list_equity_baselines(
            trading_mode=TradingMode.PAPER,
            strategy_version="v1",
            start_date=date(2026, 1, 1),
            end_date=date(2026, 1, 31),
        )
        assert result == {}

    def test_list_equity_baselines_returns_dict(self):
        """Returns dict mapping date to equity_baseline."""
        rows = [
            (date(2026, 1, 2), 100000.0),
            (date(2026, 1, 3), 100500.0),
        ]
        store = self._store(rows)
        result = store.list_equity_baselines(
            trading_mode=TradingMode.PAPER,
            strategy_version="v1",
            start_date=date(2026, 1, 1),
            end_date=date(2026, 1, 31),
        )
        assert result == {date(2026, 1, 2): 100000.0, date(2026, 1, 3): 100500.0}


class TestListByEventTypes:
    def _store(self, rows: list[tuple]) -> AuditEventStore:
        return AuditEventStore(_make_fake_connection(rows))

    def test_returns_matching_events(self):
        """list_by_event_types returns AuditEvent objects for matching rows."""
        import json
        now = datetime(2026, 4, 25, 14, 0, tzinfo=timezone.utc)
        rows = [
            ("trading_status_changed", None, json.dumps({"status": "halted"}), now),
        ]
        store = self._store(rows)
        events = store.list_by_event_types(
            event_types=["trading_status_changed"],
            limit=10,
        )
        assert len(events) == 1
        assert events[0].event_type == "trading_status_changed"
        assert events[0].payload == {"status": "halted"}
        assert events[0].created_at == now

    def test_returns_empty_list_for_empty_event_types(self):
        store = self._store([])
        events = store.list_by_event_types(event_types=[], limit=10)
        assert events == []

    def test_returns_empty_list_when_no_rows_match(self):
        store = self._store([])
        events = store.list_by_event_types(
            event_types=["trading_status_changed"],
            limit=10,
        )
        assert events == []

    def test_multiple_events_returned_in_order(self):
        """Returns all rows provided by the DB in the order given."""
        import json
        t1 = datetime(2026, 4, 25, 14, 0, tzinfo=timezone.utc)
        t2 = datetime(2026, 4, 25, 13, 0, tzinfo=timezone.utc)
        rows = [
            ("trading_status_changed", None, json.dumps({"status": "halted"}), t1),
            ("trading_status_changed", None, json.dumps({"status": "enabled"}), t2),
        ]
        store = self._store(rows)
        events = store.list_by_event_types(
            event_types=["trading_status_changed"],
            limit=20,
        )
        assert [e.payload["status"] for e in events] == ["halted", "enabled"]


# ── test_list_trade_pnl_by_strategy ──────────────────────────────────────────

class TestListTradePnlByStrategy:
    """Unit tests for OrderStore.list_trade_pnl_by_strategy()."""

    def _make_store(self, rows: list[tuple]) -> "OrderStore":
        return OrderStore(_make_fake_connection(rows))

    def test_returns_empty_when_no_rows(self) -> None:
        store = self._make_store([])
        result = store.list_trade_pnl_by_strategy(
            trading_mode=TradingMode.PAPER,
            strategy_version="v1",
            start_date=date(2026, 1, 1),
            end_date=date(2026, 1, 28),
        )
        assert result == []

    def test_filters_rows_without_entry_fill(self) -> None:
        # entry_fill is None → should be excluded
        rows = [("breakout", date(2026, 1, 2), 10, 105.0, None)]
        store = self._make_store(rows)
        result = store.list_trade_pnl_by_strategy(
            trading_mode=TradingMode.PAPER,
            strategy_version="v1",
            start_date=date(2026, 1, 1),
            end_date=date(2026, 1, 28),
        )
        assert result == []

    def test_computes_pnl_correctly(self) -> None:
        # pnl = (exit_fill - entry_fill) * qty
        # row: (strategy_name, exit_date, qty, exit_fill, entry_fill)
        rows = [("breakout", date(2026, 1, 2), 5, 110.0, 100.0)]
        store = self._make_store(rows)
        result = store.list_trade_pnl_by_strategy(
            trading_mode=TradingMode.PAPER,
            strategy_version="v1",
            start_date=date(2026, 1, 1),
            end_date=date(2026, 1, 28),
        )
        assert len(result) == 1
        assert result[0]["strategy_name"] == "breakout"
        assert result[0]["exit_date"] == date(2026, 1, 2)
        assert abs(result[0]["pnl"] - 50.0) < 1e-9  # (110 - 100) * 5

    def test_multiple_strategies_returned(self) -> None:
        rows = [
            ("breakout", date(2026, 1, 2), 5, 110.0, 100.0),
            ("momentum", date(2026, 1, 3), 10, 52.0, 50.0),
        ]
        store = self._make_store(rows)
        result = store.list_trade_pnl_by_strategy(
            trading_mode=TradingMode.PAPER,
            strategy_version="v1",
            start_date=date(2026, 1, 1),
            end_date=date(2026, 1, 28),
        )
        assert len(result) == 2
        names = {r["strategy_name"] for r in result}
        assert names == {"breakout", "momentum"}

    def test_negative_pnl_for_losing_trade(self) -> None:
        rows = [("breakout", date(2026, 1, 2), 5, 90.0, 100.0)]
        store = self._make_store(rows)
        result = store.list_trade_pnl_by_strategy(
            trading_mode=TradingMode.PAPER,
            strategy_version="v1",
            start_date=date(2026, 1, 1),
            end_date=date(2026, 1, 28),
        )
        assert len(result) == 1
        assert result[0]["pnl"] < 0.0  # losing trade


# ── test_StrategyWeightStore ──────────────────────────────────────────────────

class TestStrategyWeightStore:
    """Unit tests for StrategyWeightStore upsert_many + load_all."""

    def _make_store_with_rows(self, rows: list[tuple]) -> "StrategyWeightStore":
        return StrategyWeightStore(_make_fake_connection(rows))

    def test_load_all_returns_empty_when_no_rows(self) -> None:
        store = self._make_store_with_rows([])
        result = store.load_all(
            trading_mode=TradingMode.PAPER,
            strategy_version="v1",
        )
        assert result == []

    def test_load_all_returns_strategy_weight_objects(self) -> None:
        now = datetime(2026, 1, 2, 15, 0, tzinfo=timezone.utc)
        rows = [("breakout", "paper", "v1", 0.6, 1.8, now)]
        store = self._make_store_with_rows(rows)
        result = store.load_all(
            trading_mode=TradingMode.PAPER,
            strategy_version="v1",
        )
        assert len(result) == 1
        w = result[0]
        assert w.strategy_name == "breakout"
        assert abs(w.weight - 0.6) < 1e-9
        assert abs(w.sharpe - 1.8) < 1e-9
        assert w.trading_mode == TradingMode.PAPER
        assert w.computed_at == now

    def test_upsert_many_calls_execute_for_each_strategy(self) -> None:
        executed: list[tuple] = []

        class _TrackingConn:
            def cursor(self):
                return self

            def execute(self, sql, params=None):
                if params:
                    executed.append(params)

            def fetchone(self):
                return None

            def fetchall(self):
                return []

            def commit(self):
                pass

            def rollback(self):
                pass

            def close(self):
                pass

        store = StrategyWeightStore(_TrackingConn())
        now = datetime(2026, 1, 2, 15, 0, tzinfo=timezone.utc)
        store.upsert_many(
            weights={"breakout": 0.6, "momentum": 0.4},
            sharpes={"breakout": 1.8, "momentum": 0.9},
            trading_mode=TradingMode.PAPER,
            strategy_version="v1",
            computed_at=now,
        )
        assert len(executed) == 2
        names_stored = {p[0] for p in executed}
        assert names_stored == {"breakout", "momentum"}
