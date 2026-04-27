"""Tests for TuningResultStore (and future store coverage)."""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from alpaca_bot.storage.repositories import PositionStore, TuningResultStore
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
