from __future__ import annotations

from datetime import date

from alpaca_bot.storage.repositories import DecisionLogStore


class _FakeCursor:
    """Cursor that returns predefined rows from fetchall()."""

    def __init__(self, rows: list[tuple]) -> None:
        self._rows = rows

    def execute(self, sql: str, params) -> None:
        pass  # no-op; rows are predefined

    def fetchall(self) -> list[tuple]:
        return self._rows


class _FakeConn:
    def __init__(self, rows: list[tuple]) -> None:
        self._rows = rows

    def cursor(self) -> _FakeCursor:
        return _FakeCursor(self._rows)


def _make_rows() -> list[tuple]:
    # Columns: strategy_name, evaluated, not_skipped, not_prefiltered,
    #          signal_fired, passed_entry_filter, sized, accepted
    return [
        ("breakout", 10, 8, 5, 4, 3, 3, 2),
        ("orb",       5, 5, 5, 3, 2, 1, 1),
    ]


def test_funnel_by_strategy_returns_dicts_with_correct_counts() -> None:
    conn = _FakeConn(_make_rows())
    store = DecisionLogStore(conn)
    result = store.funnel_by_strategy(
        start_date=date(2026, 5, 1),
        end_date=date(2026, 5, 7),
        trading_mode="paper",
    )
    assert len(result) == 2

    brk = next(r for r in result if r["strategy_name"] == "breakout")
    assert brk["evaluated"] == 10
    assert brk["not_skipped"] == 8
    assert brk["not_prefiltered"] == 5
    assert brk["signal_fired"] == 4
    assert brk["passed_entry_filter"] == 3
    assert brk["sized"] == 3
    assert brk["accepted"] == 2

    orb = next(r for r in result if r["strategy_name"] == "orb")
    assert orb["accepted"] == 1


def test_funnel_by_strategy_empty_result() -> None:
    conn = _FakeConn([])
    store = DecisionLogStore(conn)
    result = store.funnel_by_strategy(
        start_date=date(2026, 5, 1),
        end_date=date(2026, 5, 7),
        trading_mode="paper",
    )
    assert result == []
