from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from types import SimpleNamespace

import alpaca_bot.admin.weekly_review_cli as weekly_review_cli
from alpaca_bot.admin.weekly_review_cli import (
    _group_by_date,
    _group_by_symbol,
    _trade_quality,
    _render_daily_table,
    _render_operational_health,
    _render_symbol_attribution,
    _render_trade_quality,
)


def _rec(
    symbol: str = "AAPL",
    strategy_name: str = "breakout",
    pnl: float = 50.0,
    intent_type: str = "exit",
    exit_time: datetime | None = None,
    entry_time: datetime | None = None,
) -> dict:
    """Build a minimal equity trade record dict."""
    if exit_time is None:
        exit_time = datetime(2026, 5, 21, 20, 0, 0, tzinfo=timezone.utc)
    if entry_time is None:
        entry_time = datetime(2026, 5, 21, 18, 0, 0, tzinfo=timezone.utc)
    return {
        "symbol": symbol,
        "strategy_name": strategy_name,
        "qty": 10.0,
        "entry_price": 100.0,
        "exit_price": 100.0 + pnl / 10.0,
        "pnl": pnl,
        "intent_type": intent_type,
        "exit_time": exit_time,
        "entry_time": entry_time,
        "hold_seconds": (exit_time - entry_time).total_seconds(),
    }


def test_group_by_date_groups_trades_by_exit_date():
    day1 = datetime(2026, 5, 21, 20, 0, 0, tzinfo=timezone.utc)
    day2 = datetime(2026, 5, 22, 20, 0, 0, tzinfo=timezone.utc)
    records = [
        _rec(pnl=100.0, exit_time=day1),
        _rec(pnl=-20.0, exit_time=day1),
        _rec(pnl=50.0, exit_time=day2),
        _rec(pnl=30.0, exit_time=day2),
    ]
    rows = _group_by_date(records, [], "America/New_York")
    assert len(rows) == 2
    assert rows[0]["trade_count"] == 2
    assert rows[0]["total_pnl"] == pytest.approx(80.0)
    assert rows[0]["win_count"] == 1
    assert rows[1]["trade_count"] == 2
    # cumulative after day2 = 80 + 80 = 160
    assert rows[1]["cumul_pnl"] == pytest.approx(160.0)


def test_group_by_symbol_sorts_by_pnl_descending():
    records = [
        _rec(symbol="TSLA", pnl=-50.0),
        _rec(symbol="NVDA", pnl=200.0),
        _rec(symbol="AAPL", pnl=80.0),
    ]
    rows = _group_by_symbol(records)
    assert rows[0]["symbol"] == "NVDA"
    assert rows[1]["symbol"] == "AAPL"
    assert rows[2]["symbol"] == "TSLA"
    assert rows[2]["total_pnl"] == pytest.approx(-50.0)


def test_trade_quality_win_loss_ratio():
    records = [
        _rec(pnl=60.0),
        _rec(pnl=40.0),
        _rec(pnl=-20.0),
        _rec(pnl=-30.0),
    ]
    q = _trade_quality(records)
    assert q["avg_winner"] == pytest.approx(50.0)
    assert q["avg_loser"] == pytest.approx(-25.0)
    assert q["win_loss_ratio"] == pytest.approx(2.0)
    assert q["max_winner"] == pytest.approx(60.0)
    assert q["max_loser"] == pytest.approx(-30.0)


def test_symbol_attribution_top_bottom_5():
    """_group_by_symbol returns correctly sorted data for top/bottom slicing."""
    records = [_rec(symbol=f"SYM{i}", pnl=float(100 - i * 20)) for i in range(12)]
    rows = _group_by_symbol(records)
    assert rows[0]["symbol"] == "SYM0"
    assert rows[0]["total_pnl"] == pytest.approx(100.0)
    assert rows[-1]["symbol"] == "SYM11"
    assert rows[-1]["total_pnl"] == pytest.approx(100.0 - 11 * 20.0)
    assert len(rows) == 12
    top5 = rows[:5]
    bottom5 = rows[-5:]
    assert all(t["total_pnl"] > b["total_pnl"] for t in top5 for b in bottom5)


def test_loser_analysis_counts_stop_vs_eod():
    records = [
        _rec(pnl=-30.0, intent_type="stop"),
        _rec(pnl=-20.0, intent_type="stop"),
        _rec(pnl=-10.0, intent_type="exit"),
        _rec(pnl=50.0, intent_type="exit"),
    ]
    q = _trade_quality(records)
    assert q["stop_losses"] == 2
    assert q["eod_losses"] == 1
    assert q["eod_wins"] == 1
    assert q["stop_wins"] == 0


def test_weekly_review_no_trades_prints_no_data(capsys):
    """Zero-trade path: render functions produce 'no closed' messages without crashing."""
    _render_daily_table([])
    _render_symbol_attribution([])
    _render_trade_quality({
        "avg_winner": None,
        "avg_loser": None,
        "max_winner": None,
        "max_loser": None,
        "win_loss_ratio": None,
        "stop_wins": 0,
        "stop_losses": 0,
        "eod_wins": 0,
        "eod_losses": 0,
    })
    captured = capsys.readouterr()
    assert "no closed" in captured.out.lower()


def test_render_operational_health_shows_circuit_breaker_count(capsys):
    """Circuit breaker fires are counted and displayed in the operational health section."""
    _since = datetime(2026, 5, 21, 0, 0, tzinfo=timezone.utc)
    _until = datetime(2026, 5, 28, 0, 0, tzinfo=timezone.utc)

    cb_event = SimpleNamespace(
        event_type="option_strategy_circuit_breaker_triggered",
        payload={"strategy_name": "bear_orb"},
    )

    def _fake_list_by_event_types(*, event_types, since, until, limit):
        if "option_strategy_circuit_breaker_triggered" in event_types:
            return [cb_event]
        return []

    fake_audit_store = SimpleNamespace(list_by_event_types=_fake_list_by_event_types)

    _render_operational_health(fake_audit_store, _since, _until)
    captured = capsys.readouterr()
    assert "Circuit breaker" in captured.out or "circuit breaker" in captured.out.lower()
    assert "1" in captured.out


def test_weekly_review_main_loads_operational_health_before_closing_connection(
    monkeypatch,
    capsys,
) -> None:
    class FakeConnection:
        closed = False

        def close(self):
            self.closed = True

    class FakeOrderStore:
        def __init__(self, conn):
            self.conn = conn

        def list_closed_trade_records(self, **kwargs):
            assert not self.conn.closed
            return []

    class FakeOptionRepo:
        def __init__(self, conn):
            self.conn = conn

        def list_closed_option_trade_records(self, **kwargs):
            assert not self.conn.closed
            return []

    class FakeDecisionLogStore:
        def __init__(self, conn):
            self.conn = conn

        def funnel_by_strategy(self, **kwargs):
            assert not self.conn.closed
            return []

    class FakeAuditStore:
        def __init__(self, conn):
            self.conn = conn

        def list_by_event_types(self, **kwargs):
            assert not self.conn.closed
            return []

    fake_connection = FakeConnection()
    fake_settings = SimpleNamespace(
        database_url="postgresql://example",
        market_timezone=ZoneInfo("America/New_York"),
        strategy_version="v1-breakout",
    )

    monkeypatch.setattr(
        weekly_review_cli.Settings,
        "from_env",
        staticmethod(lambda: fake_settings),
    )
    monkeypatch.setattr(weekly_review_cli, "connect_postgres", lambda url: fake_connection)
    monkeypatch.setattr(weekly_review_cli, "OrderStore", FakeOrderStore)
    monkeypatch.setattr(weekly_review_cli, "OptionOrderRepository", FakeOptionRepo)
    monkeypatch.setattr(weekly_review_cli, "DecisionLogStore", FakeDecisionLogStore)
    monkeypatch.setattr(weekly_review_cli, "AuditEventStore", FakeAuditStore)

    rc = weekly_review_cli.main([
        "--since", "2026-06-21",
        "--until", "2026-06-27",
        "--mode", "paper",
        "--strategy-version", "v1-breakout",
    ])

    assert rc == 0
    assert fake_connection.closed is True
    assert "Operational Health" in capsys.readouterr().out
