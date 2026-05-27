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


def test_funnel_cli_prints_header_and_rows(monkeypatch, capsys) -> None:
    """main() prints strategy funnel table to stdout."""
    from types import SimpleNamespace
    import alpaca_bot.admin.funnel_report_cli as cli_module

    fake_rows = [
        {
            "strategy_name": "breakout",
            "evaluated": 100,
            "not_skipped": 90,
            "not_prefiltered": 60,
            "signal_fired": 30,
            "passed_entry_filter": 28,
            "sized": 27,
            "accepted": 15,
        },
    ]

    class _FakeSettings:
        database_url = "postgresql://x:x@localhost/x"
        market_timezone = SimpleNamespace(key="America/New_York")

    class _FakeStore:
        def __init__(self, conn):
            pass

        def funnel_by_strategy(self, **kwargs):
            return fake_rows

    # Patch on cli_module (the bound names), not on the source modules.
    # Project pattern: monkeypatch.setattr(cli_module, "Settings", ...) so the
    # reference already imported into the CLI module namespace is replaced.
    monkeypatch.setattr(cli_module, "Settings", SimpleNamespace(from_env=lambda: _FakeSettings()))
    monkeypatch.setattr(cli_module, "connect_postgres", lambda url: None)
    monkeypatch.setattr(cli_module, "DecisionLogStore", _FakeStore)

    exit_code = cli_module.main(["--days", "7"])

    output = capsys.readouterr().out
    assert "breakout" in output
    assert "Strategy" in output  # header
    assert "100" in output        # evaluated count
    assert "15" in output         # accepted count
    assert exit_code == 0
