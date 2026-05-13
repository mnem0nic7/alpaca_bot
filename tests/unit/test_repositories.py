"""Tests for TuningResultStore, WatchlistStore, and other store coverage."""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from alpaca_bot.storage.repositories import PositionStore, TuningResultStore, WatchlistStore, WatchlistRecord
from alpaca_bot.storage.models import PositionRecord
from alpaca_bot.config import TradingMode


def _make_candidate(*, params: dict, score: float | None = 1.0):
    report = SimpleNamespace(
        total_trades=10,
        win_rate=0.6,
        mean_return_pct=0.5,
        max_drawdown_pct=-0.1,
        sharpe_ratio=1.2,
    )
    return SimpleNamespace(params=params, score=score, report=report)


class _TrackingConnection:
    """Minimal fake Postgres connection that records commit/rollback calls."""

    def __init__(self) -> None:
        self.commit_count = 0
        self.rollback_count = 0
        self.execute_calls: list[tuple] = []

    def commit(self) -> None:
        self.commit_count += 1

    def rollback(self) -> None:
        self.rollback_count += 1

    def cursor(self):
        conn = self

        class _Cursor:
            def execute(self, sql: str, params=None) -> None:
                conn.execute_calls.append((sql, params))

        return _Cursor()


class _FailingOnNthCommitConnection(_TrackingConnection):
    """Raises on the Nth commit() call."""

    def __init__(self, fail_on: int = 1) -> None:
        super().__init__()
        self._fail_on = fail_on

    def commit(self) -> None:
        self.commit_count += 1
        if self.commit_count >= self._fail_on:
            raise RuntimeError("commit failed")


class _FailingCursorConnection(_TrackingConnection):
    """Raises on the Nth execute call (to simulate mid-loop insert failure)."""

    def __init__(self, fail_on_execute: int = 2) -> None:
        super().__init__()
        self._fail_on_execute = fail_on_execute
        self._execute_count = 0

    def cursor(self):
        conn = self

        class _FailCursor:
            def execute(self, sql: str, params=None) -> None:
                conn._execute_count += 1
                conn.execute_calls.append((sql, params))
                if conn._execute_count >= conn._fail_on_execute:
                    raise RuntimeError("execute failed mid-loop")

        return _FailCursor()


# ── save_run happy path ──────────────────────────────────────────────────────


def test_save_run_returns_run_id() -> None:
    conn = _TrackingConnection()
    store = TuningResultStore(conn)
    candidates = [_make_candidate(params={"a": 1})]

    run_id = store.save_run(
        scenario_name="test",
        trading_mode="paper",
        candidates=candidates,
        created_at=datetime(2026, 4, 24, tzinfo=timezone.utc),
    )

    assert isinstance(run_id, str)
    assert len(run_id) == 36  # UUID format


def test_save_run_uses_provided_run_id() -> None:
    conn = _TrackingConnection()
    store = TuningResultStore(conn)
    candidates = [_make_candidate(params={"a": 1})]

    run_id = store.save_run(
        scenario_name="test",
        trading_mode="paper",
        candidates=candidates,
        created_at=datetime(2026, 4, 24, tzinfo=timezone.utc),
        run_id="fixed-run-id",
    )

    assert run_id == "fixed-run-id"


def test_save_run_commits_once_for_multiple_candidates() -> None:
    conn = _TrackingConnection()
    store = TuningResultStore(conn)
    candidates = [
        _make_candidate(params={"a": 1}, score=1.0),
        _make_candidate(params={"a": 2}, score=0.8),
        _make_candidate(params={"a": 3}, score=0.6),
    ]

    store.save_run(
        scenario_name="sweep",
        trading_mode="paper",
        candidates=candidates,
        created_at=datetime(2026, 4, 24, tzinfo=timezone.utc),
    )

    # All three inserts + exactly one commit — atomic for the whole run
    assert len(conn.execute_calls) == 3
    assert conn.commit_count == 1
    assert conn.rollback_count == 0


def test_save_run_empty_candidates_commits_once() -> None:
    conn = _TrackingConnection()
    store = TuningResultStore(conn)

    store.save_run(
        scenario_name="empty",
        trading_mode="paper",
        candidates=[],
        created_at=datetime(2026, 4, 24, tzinfo=timezone.utc),
    )

    assert conn.commit_count == 1
    assert conn.rollback_count == 0


# ── save_run atomicity / rollback ────────────────────────────────────────────


def test_save_run_rollback_on_mid_loop_insert_failure() -> None:
    # Second insert fails — the first should be rolled back
    conn = _FailingCursorConnection(fail_on_execute=2)
    store = TuningResultStore(conn)
    candidates = [
        _make_candidate(params={"a": 1}),
        _make_candidate(params={"a": 2}),
    ]

    with pytest.raises(RuntimeError, match="execute failed mid-loop"):
        store.save_run(
            scenario_name="test",
            trading_mode="paper",
            candidates=candidates,
            created_at=datetime(2026, 4, 24, tzinfo=timezone.utc),
        )

    assert conn.rollback_count == 1, "rollback() must be called on insert failure"
    assert conn.commit_count == 0, "commit() must NOT be called after failure"


def test_save_run_rollback_on_commit_failure() -> None:
    conn = _FailingOnNthCommitConnection(fail_on=1)
    store = TuningResultStore(conn)
    candidates = [_make_candidate(params={"a": 1})]

    with pytest.raises(RuntimeError, match="commit failed"):
        store.save_run(
            scenario_name="test",
            trading_mode="paper",
            candidates=candidates,
            created_at=datetime(2026, 4, 24, tzinfo=timezone.utc),
        )

    assert conn.rollback_count == 1, "rollback() must be called when commit raises"


def test_save_run_reraises_after_rollback() -> None:
    conn = _FailingCursorConnection(fail_on_execute=1)
    store = TuningResultStore(conn)
    candidates = [_make_candidate(params={"a": 1})]

    exc_raised = None
    try:
        store.save_run(
            scenario_name="test",
            trading_mode="paper",
            candidates=candidates,
            created_at=datetime(2026, 4, 24, tzinfo=timezone.utc),
        )
    except RuntimeError as exc:
        exc_raised = exc

    assert exc_raised is not None, "Original exception must be re-raised"
    assert "execute failed" in str(exc_raised)


# ── PositionStore.replace_all rollback guard ─────────────────────────────────


def _make_position() -> PositionRecord:
    return PositionRecord(
        symbol="AAPL",
        trading_mode=TradingMode.PAPER,
        strategy_version="v1",
        quantity=10,
        entry_price=150.0,
        stop_price=145.0,
        initial_stop_price=145.0,
        opened_at=datetime(2026, 4, 24, tzinfo=timezone.utc),
    )


def test_position_store_replace_all_commits_on_success() -> None:
    conn = _TrackingConnection()
    store = PositionStore(conn)

    store.replace_all(
        positions=[_make_position()],
        trading_mode=TradingMode.PAPER,
        strategy_version="v1",
    )

    assert conn.commit_count == 1
    assert conn.rollback_count == 0
    # DELETE + INSERT = 2 execute calls
    assert len(conn.execute_calls) == 2


def test_position_store_replace_all_rollback_on_insert_failure() -> None:
    # First execute = DELETE (succeeds), second = INSERT (fails)
    conn = _FailingCursorConnection(fail_on_execute=2)
    store = PositionStore(conn)

    with pytest.raises(RuntimeError, match="execute failed mid-loop"):
        store.replace_all(
            positions=[_make_position()],
            trading_mode=TradingMode.PAPER,
            strategy_version="v1",
        )

    assert conn.rollback_count == 1, "rollback() must be called on insert failure"
    assert conn.commit_count == 0, "commit() must NOT be called after failure"


def test_position_store_replace_all_rollback_on_delete_failure() -> None:
    conn = _FailingCursorConnection(fail_on_execute=1)
    store = PositionStore(conn)

    with pytest.raises(RuntimeError, match="execute failed mid-loop"):
        store.replace_all(
            positions=[_make_position()],
            trading_mode=TradingMode.PAPER,
            strategy_version="v1",
        )

    assert conn.rollback_count == 1, "rollback() must be called on delete failure"


def test_position_store_replace_all_empty_positions_commits_once() -> None:
    conn = _TrackingConnection()
    store = PositionStore(conn)

    store.replace_all(
        positions=[],
        trading_mode=TradingMode.PAPER,
        strategy_version="v1",
    )

    # Only DELETE, no inserts
    assert len(conn.execute_calls) == 1
    assert conn.commit_count == 1
    assert conn.rollback_count == 0


# ── WatchlistStore ────────────────────────────────────────────────────────────


class _FetchingConnection(_TrackingConnection):
    """Extends _TrackingConnection with configurable fetchall results."""

    def __init__(self, fetchall_result: list | None = None) -> None:
        super().__init__()
        self._fetchall_result: list = fetchall_result or []

    def cursor(self):
        conn = self

        class _FetchCursor:
            def execute(self, sql: str, params=None) -> None:
                conn.execute_calls.append((sql, params))

            def fetchall(self):
                return list(conn._fetchall_result)

        return _FetchCursor()


def test_watchlist_store_list_enabled_returns_symbols() -> None:
    rows = [("AAPL",), ("MSFT",)]
    conn = _FetchingConnection(fetchall_result=rows)
    store = WatchlistStore(conn)

    result = store.list_enabled("paper")

    assert result == ["AAPL", "MSFT"]
    assert len(conn.execute_calls) == 1
    assert "enabled = TRUE" in conn.execute_calls[0][0]
    assert conn.execute_calls[0][1] == ("paper",)


def test_watchlist_store_list_all_returns_records() -> None:
    now = datetime(2026, 4, 27, tzinfo=timezone.utc)
    rows = [
        ("AAPL", "paper", True, False, now, "operator@example.com"),
        ("TSLA", "paper", False, False, now, "system"),
    ]
    conn = _FetchingConnection(fetchall_result=rows)
    store = WatchlistStore(conn)

    result = store.list_all("paper")

    assert len(result) == 2
    assert isinstance(result[0], WatchlistRecord)
    assert result[0].symbol == "AAPL"
    assert result[0].enabled is True
    assert result[0].ignored is False
    assert result[1].symbol == "TSLA"
    assert result[1].enabled is False


def test_watchlist_store_add_inserts_with_on_conflict_update() -> None:
    conn = _TrackingConnection()
    store = WatchlistStore(conn)

    store.add("NVDA", "paper", added_by="admin", commit=True)

    assert len(conn.execute_calls) == 1
    sql = conn.execute_calls[0][0]
    assert "ON CONFLICT" in sql
    assert "enabled = TRUE" in sql
    assert conn.execute_calls[0][1] == ("NVDA", "paper", "admin")
    assert conn.commit_count == 1


def test_watchlist_store_add_with_commit_false_does_not_commit() -> None:
    conn = _TrackingConnection()
    store = WatchlistStore(conn)

    store.add("NVDA", "paper", commit=False)

    assert conn.commit_count == 0


def test_watchlist_store_remove_soft_deletes() -> None:
    conn = _TrackingConnection()
    store = WatchlistStore(conn)

    store.remove("AAPL", "paper", commit=True)

    assert len(conn.execute_calls) == 1
    sql = conn.execute_calls[0][0]
    assert "enabled = FALSE" in sql
    assert conn.execute_calls[0][1] == ("AAPL", "paper")
    assert conn.commit_count == 1


def test_watchlist_store_seed_uses_on_conflict_do_nothing() -> None:
    conn = _TrackingConnection()
    store = WatchlistStore(conn)

    store.seed(("AAPL", "MSFT", "SPY"), "paper", commit=True)

    assert len(conn.execute_calls) == 3
    for sql, _params in conn.execute_calls:
        assert "ON CONFLICT" in sql
        assert "DO NOTHING" in sql
    assert conn.commit_count == 1


def test_watchlist_store_seed_commits_once_for_multiple_symbols() -> None:
    conn = _TrackingConnection()
    store = WatchlistStore(conn)

    store.seed(("AAPL", "MSFT"), "paper", commit=True)

    assert conn.commit_count == 1


def test_watchlist_store_seed_commit_false_does_not_commit() -> None:
    conn = _TrackingConnection()
    store = WatchlistStore(conn)

    store.seed(("AAPL",), "paper", commit=False)

    assert conn.commit_count == 0


def test_watchlist_store_list_ignored_returns_only_enabled_and_ignored() -> None:
    rows = [("TSLA",)]
    conn = _FetchingConnection(fetchall_result=rows)
    store = WatchlistStore(conn)

    result = store.list_ignored("paper")

    assert result == ["TSLA"]
    sql = conn.execute_calls[0][0]
    assert "enabled = TRUE" in sql
    assert "ignored = TRUE" in sql


def test_watchlist_store_ignore_sets_ignored_true() -> None:
    conn = _TrackingConnection()
    store = WatchlistStore(conn)

    store.ignore("TSLA", "paper", commit=True)

    assert len(conn.execute_calls) == 1
    sql = conn.execute_calls[0][0]
    assert "ignored = TRUE" in sql
    assert conn.execute_calls[0][1] == ("TSLA", "paper")
    assert conn.commit_count == 1


def test_watchlist_store_ignore_commit_false_does_not_commit() -> None:
    conn = _TrackingConnection()
    store = WatchlistStore(conn)

    store.ignore("TSLA", "paper", commit=False)

    assert conn.commit_count == 0


def test_watchlist_store_unignore_sets_ignored_false() -> None:
    conn = _TrackingConnection()
    store = WatchlistStore(conn)

    store.unignore("TSLA", "paper", commit=True)

    assert len(conn.execute_calls) == 1
    sql = conn.execute_calls[0][0]
    assert "ignored = FALSE" in sql
    assert conn.execute_calls[0][1] == ("TSLA", "paper")
    assert conn.commit_count == 1


def test_watchlist_store_list_all_includes_ignored_field() -> None:
    now = datetime(2026, 4, 27, tzinfo=timezone.utc)
    rows = [("AAPL", "paper", True, False, now, "system")]
    conn = _FetchingConnection(fetchall_result=rows)
    store = WatchlistStore(conn)

    result = store.list_all("paper")

    assert len(result) == 1
    assert result[0].ignored is False


# ── MarketContextStore ────────────────────────────────────────────────────────

from alpaca_bot.domain.models import MarketContext
from alpaca_bot.storage.repositories import MarketContextStore


def test_market_context_store_save_inserts_row() -> None:
    conn = _TrackingConnection()
    store = MarketContextStore(conn)
    ctx = MarketContext(
        as_of=datetime(2026, 5, 9, 14, 30, tzinfo=timezone.utc),
        vix_close=18.5,
        vix_sma=16.2,
        vix_above_sma=True,
        sector_etf_states={"XLK": True, "XLF": False},
        sector_passing_pct=0.45,
    )
    store.save(ctx, trading_mode="paper")
    assert len(conn.execute_calls) == 1
    sql, params = conn.execute_calls[0]
    assert "market_context" in sql.lower()
    assert params[1] == "paper"
    assert params[2] == 18.5
    assert params[4] is True
    assert params[6] == 0.45


def test_market_context_store_save_with_none_fields() -> None:
    conn = _TrackingConnection()
    store = MarketContextStore(conn)
    ctx = MarketContext(
        as_of=datetime(2026, 5, 9, 14, 30, tzinfo=timezone.utc),
    )
    store.save(ctx, trading_mode="paper")
    assert len(conn.execute_calls) == 1
    _, params = conn.execute_calls[0]
    assert params[2] is None
    assert params[4] is None
    assert params[5] is None


def test_market_context_store_exported_from_storage() -> None:
    from alpaca_bot.storage import MarketContextStore as MCS
    assert MCS is MarketContextStore


# ── DecisionLogStore.list_recent ─────────────────────────────────────────────

from datetime import date
from alpaca_bot.storage.repositories import DecisionLogStore


class _FetchingDecisionLogConnection(_TrackingConnection):
    """Returns canned rows from fetchall() for decision_log queries."""

    def __init__(self, rows: list[tuple]) -> None:
        super().__init__()
        self._rows = rows

    def cursor(self):
        rows = self._rows
        conn = self

        class _FetchCursor:
            def execute(self, sql: str, params=None) -> None:
                conn.execute_calls.append((sql, params))

            def fetchall(self):
                return list(rows)

        return _FetchCursor()


def _make_decision_row(
    symbol: str = "ALHC260618P00017500",
    session_date: date = date(2026, 5, 12),
    decision: str = "ENTRY",
) -> tuple:
    """Return a 24-element tuple matching decision_log column order."""
    from datetime import datetime, timezone
    return (
        datetime(2026, 5, 12, 14, 0, tzinfo=timezone.utc),  # cycle_at
        symbol,                   # symbol
        "breakout",               # strategy_name
        "paper",                  # trading_mode
        "v1-breakout",            # strategy_version
        decision,                 # decision
        None,                     # reject_stage
        None,                     # reject_reason
        17.5,                     # entry_level
        17.3,                     # signal_bar_close
        2.1,                      # relative_volume
        None,                     # atr
        None,                     # stop_price
        1.20,                     # limit_price
        None,                     # initial_stop_price
        1,                        # quantity
        None,                     # risk_per_share
        50_000.0,                 # equity
        {},                       # filter_results (psycopg2 returns dict for JSONB)
        None,                     # vix_close
        None,                     # vix_above_sma
        None,                     # sector_passing_pct
        None,                     # vwap_at_signal
        None,                     # signal_bar_above_vwap
    )


def test_decision_log_list_recent_returns_dicts_for_session_date() -> None:
    row = _make_decision_row()
    conn = _FetchingDecisionLogConnection([row])
    store = DecisionLogStore(conn)
    results = store.list_recent(session_date=date(2026, 5, 12))
    assert len(results) == 1
    assert results[0]["symbol"] == "ALHC260618P00017500"
    assert results[0]["decision"] == "ENTRY"
    assert results[0]["entry_level"] == 17.5


def test_decision_log_list_recent_symbol_filter_passes_param() -> None:
    conn = _FetchingDecisionLogConnection([])
    store = DecisionLogStore(conn)
    store.list_recent(session_date=date(2026, 5, 12), symbol="AAPL")
    sqls = [call[0] for call in conn.execute_calls]
    assert any("symbol" in sql.lower() for sql in sqls), (
        "Expected symbol filter in SQL when symbol param provided"
    )
    params = conn.execute_calls[0][1]
    assert "AAPL" in params


def test_decision_log_list_recent_empty_returns_empty_list() -> None:
    conn = _FetchingDecisionLogConnection([])
    store = DecisionLogStore(conn)
    results = store.list_recent(session_date=date(2026, 5, 12))
    assert results == []


# ── PositionStore lowest_price column ────────────────────────────────────────

class _CapturingCursor:
    def __init__(self, rows=None):
        self._rows = rows or []
        self.executed: list[tuple] = []

    def execute(self, sql, params=None):
        self.executed.append((sql, params))

    def fetchall(self):
        return list(self._rows)


class _CapturingConn:
    def __init__(self, rows=None):
        self._cursor = _CapturingCursor(rows)
        self.committed = False

    def cursor(self): return self._cursor
    def commit(self): self.committed = True
    def rollback(self): pass


def _make_short_position_rec(
    lowest_price: float | None = None,
    highest_price: float | None = None,
) -> PositionRecord:
    return PositionRecord(
        symbol="QBTS",
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        strategy_name="short_equity",
        quantity=-100,
        entry_price=5.50,
        stop_price=6.00,
        initial_stop_price=6.00,
        opened_at=datetime(2026, 5, 1, 14, 0, tzinfo=timezone.utc),
        highest_price=highest_price,
        lowest_price=lowest_price,
    )


def test_save_includes_lowest_price_in_insert():
    conn = _CapturingConn()
    store = PositionStore(conn)
    store.save(_make_short_position_rec(lowest_price=4.80))
    sql, params = conn._cursor.executed[0]
    assert "lowest_price" in sql
    assert 4.80 in params


def test_save_with_none_lowest_price_passes_none():
    conn = _CapturingConn()
    store = PositionStore(conn)
    store.save(_make_short_position_rec(lowest_price=None))
    _, params = conn._cursor.executed[0]
    assert params[-1] is None  # lowest_price is last param


def test_save_coalesce_preserves_existing_lowest_price():
    conn = _CapturingConn()
    store = PositionStore(conn)
    store.save(_make_short_position_rec(lowest_price=None))
    sql, _ = conn._cursor.executed[0]
    assert "COALESCE" in sql


def test_replace_all_includes_lowest_price_in_insert():
    conn = _CapturingConn()
    store = PositionStore(conn)
    store.replace_all(
        positions=[_make_short_position_rec(lowest_price=4.70)],
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
    )
    insert_sqls = [s for s, _ in conn._cursor.executed if "INSERT" in s]
    assert insert_sqls, "replace_all must issue an INSERT"
    assert "lowest_price" in insert_sqls[0]


def test_list_all_populates_lowest_price():
    row = (
        "QBTS", "paper", "v1-breakout", "short_equity",
        -100.0, 5.50, 6.00, 6.00,
        datetime(2026, 5, 1, 14, 0, tzinfo=timezone.utc),
        datetime(2026, 5, 1, 14, 0, tzinfo=timezone.utc),
        None,   # highest_price
        4.80,   # lowest_price at index 11
    )
    conn = _CapturingConn(rows=[row])
    store = PositionStore(conn)
    records = store.list_all(trading_mode=TradingMode.PAPER, strategy_version="v1-breakout")
    assert len(records) == 1
    assert records[0].lowest_price == 4.80


def test_list_all_handles_null_lowest_price():
    row = (
        "QBTS", "paper", "v1-breakout", "short_equity",
        -100.0, 5.50, 6.00, 6.00,
        datetime(2026, 5, 1, 14, 0, tzinfo=timezone.utc),
        datetime(2026, 5, 1, 14, 0, tzinfo=timezone.utc),
        None,   # highest_price
        None,   # lowest_price is NULL
    )
    conn = _CapturingConn(rows=[row])
    store = PositionStore(conn)
    records = store.list_all(trading_mode=TradingMode.PAPER, strategy_version="v1-breakout")
    assert records[0].lowest_price is None


def test_update_lowest_price_issues_targeted_update():
    conn = _CapturingConn()
    store = PositionStore(conn)
    store.update_lowest_price(
        symbol="QBTS",
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        strategy_name="short_equity",
        lowest_price=4.50,
    )
    assert conn.committed
    sql, params = conn._cursor.executed[0]
    assert "UPDATE positions" in sql
    assert "lowest_price" in sql
    assert 4.50 in params
    assert "QBTS" in params
