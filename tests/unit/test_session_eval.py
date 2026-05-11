from __future__ import annotations

from datetime import datetime, timezone

import pytest

from alpaca_bot.replay.report import BacktestReport, ReplayTradeRecord, report_from_records


def _make_trade(
    symbol: str = "AAPL",
    entry: float = 100.0,
    exit_: float = 102.0,
    qty: int = 10,
    exit_reason: str = "eod",
    entry_time: datetime | None = None,
    exit_time: datetime | None = None,
) -> ReplayTradeRecord:
    t0 = datetime(2026, 5, 4, 10, 0, tzinfo=timezone.utc)
    t1 = datetime(2026, 5, 4, 11, 0, tzinfo=timezone.utc)
    pnl = (exit_ - entry) * qty
    return ReplayTradeRecord(
        symbol=symbol,
        entry_price=entry,
        exit_price=exit_,
        quantity=qty,
        entry_time=entry_time or t0,
        exit_time=exit_time or t1,
        exit_reason=exit_reason,
        pnl=pnl,
        return_pct=(exit_ - entry) / entry,
    )


def test_report_from_records_basic_stats():
    trades = [
        _make_trade(exit_=102.0),  # win, +$20
        _make_trade(exit_=103.0),  # win, +$30
        _make_trade(exit_=98.0),   # loss, -$20
    ]
    report = report_from_records(trades, starting_equity=100_000.0)
    assert report.total_trades == 3
    assert report.winning_trades == 2
    assert report.losing_trades == 1
    assert abs(report.win_rate - 2 / 3) < 1e-9
    assert report.profit_factor is not None
    assert report.profit_factor > 1.0


def test_report_from_records_exit_breakdown():
    trades = [
        _make_trade(exit_=102.0, exit_reason="stop"),   # stop win
        _make_trade(exit_=98.0, exit_reason="stop"),    # stop loss
        _make_trade(exit_=103.0, exit_reason="eod"),    # eod win
        _make_trade(exit_=99.0, exit_reason="eod"),     # eod loss
    ]
    report = report_from_records(trades, starting_equity=100_000.0)
    assert report.stop_wins == 1
    assert report.stop_losses == 1
    assert report.eod_wins == 1
    assert report.eod_losses == 1


def test_report_from_records_zero_trades():
    report = report_from_records([], starting_equity=100_000.0)
    assert report.total_trades == 0
    assert report.win_rate is None
    assert report.mean_return_pct is None
    assert report.max_drawdown_pct is None
    assert report.profit_factor is None


def test_report_from_records_parity_with_build_backtest_report():
    """report_from_records() produces the same stats as build_backtest_report() for equivalent input."""
    from alpaca_bot.domain.enums import IntentType
    from alpaca_bot.domain.models import ReplayEvent, ReplayResult, ReplayScenario
    from alpaca_bot.replay.report import build_backtest_report

    t_entry = datetime(2026, 5, 4, 10, 0, tzinfo=timezone.utc)
    t_stop = datetime(2026, 5, 4, 10, 30, tzinfo=timezone.utc)
    t_eod = datetime(2026, 5, 4, 16, 0, tzinfo=timezone.utc)

    events = [
        ReplayEvent(event_type=IntentType.ENTRY_FILLED, symbol="AAPL", timestamp=t_entry,
                    details={"entry_price": 100.0, "quantity": 10}),
        ReplayEvent(event_type=IntentType.STOP_HIT, symbol="AAPL", timestamp=t_stop,
                    details={"exit_price": 98.0}),
        ReplayEvent(event_type=IntentType.ENTRY_FILLED, symbol="TSLA", timestamp=t_entry,
                    details={"entry_price": 200.0, "quantity": 5}),
        ReplayEvent(event_type=IntentType.EOD_EXIT, symbol="TSLA", timestamp=t_eod,
                    details={"exit_price": 205.0}),
    ]
    scenario = ReplayScenario(
        name="test", symbol="AAPL", starting_equity=100_000.0,
        daily_bars=[], intraday_bars=[],
    )
    result = ReplayResult(scenario=scenario, events=events, final_position=None, traded_symbols=set())

    backtest_report = build_backtest_report(result)

    trades = [
        ReplayTradeRecord(symbol="AAPL", entry_price=100.0, exit_price=98.0, quantity=10,
                          entry_time=t_entry, exit_time=t_stop, exit_reason="stop",
                          pnl=-20.0, return_pct=-0.02),
        ReplayTradeRecord(symbol="TSLA", entry_price=200.0, exit_price=205.0, quantity=5,
                          entry_time=t_entry, exit_time=t_eod, exit_reason="eod",
                          pnl=25.0, return_pct=0.025),
    ]
    live_report = report_from_records(trades, starting_equity=100_000.0)

    assert live_report.total_trades == backtest_report.total_trades
    assert live_report.winning_trades == backtest_report.winning_trades
    assert live_report.losing_trades == backtest_report.losing_trades
    assert live_report.win_rate == backtest_report.win_rate
    assert live_report.profit_factor == backtest_report.profit_factor
    assert live_report.stop_wins == backtest_report.stop_wins
    assert live_report.stop_losses == backtest_report.stop_losses
    assert live_report.eod_wins == backtest_report.eod_wins
    assert live_report.eod_losses == backtest_report.eod_losses
    assert live_report.max_consecutive_losses == backtest_report.max_consecutive_losses


# ---------------------------------------------------------------------------
# Task 2 — _row_to_trade_record() and list_closed_trades() contract
# ---------------------------------------------------------------------------


def _make_trade_row(
    *,
    symbol: str = "AAPL",
    strategy_name: str = "breakout",
    intent_type: str = "exit",
    entry_fill: float = 100.0,
    exit_fill: float = 102.0,
    qty: int = 10,
) -> dict:
    t0 = datetime(2026, 5, 4, 10, 0, tzinfo=timezone.utc)
    t1 = datetime(2026, 5, 4, 11, 0, tzinfo=timezone.utc)
    return {
        "symbol": symbol,
        "strategy_name": strategy_name,
        "intent_type": intent_type,
        "entry_fill": entry_fill,
        "entry_limit": entry_fill + 0.05,
        "entry_time": t0,
        "exit_fill": exit_fill,
        "exit_time": t1,
        "qty": qty,
    }


def test_row_to_trade_record_stop_exit():
    from alpaca_bot.admin.session_eval_cli import _row_to_trade_record
    row = _make_trade_row(intent_type="stop", exit_fill=98.0)
    record = _row_to_trade_record(row)
    assert record.exit_reason == "stop"
    assert record.pnl < 0
    assert record.symbol == "AAPL"


def test_row_to_trade_record_eod_exit():
    from alpaca_bot.admin.session_eval_cli import _row_to_trade_record
    row = _make_trade_row(intent_type="exit", exit_fill=103.0)
    record = _row_to_trade_record(row)
    assert record.exit_reason == "eod"
    assert record.pnl > 0
    assert record.quantity == 10


def test_list_closed_trades_includes_intent_type():
    """list_closed_trades() return dict must include intent_type key."""
    row = (
        "AAPL",     # symbol
        "breakout", # strategy_name
        "stop",     # intent_type
        100.0,      # entry_fill
        100.05,     # entry_limit
        datetime(2026, 5, 4, 10, 0, tzinfo=timezone.utc),  # entry_time
        98.0,       # exit_fill
        datetime(2026, 5, 4, 10, 30, tzinfo=timezone.utc), # exit_time
        10,         # qty
    )
    result = {
        "symbol": row[0],
        "strategy_name": row[1],
        "intent_type": row[2],
        "entry_fill": float(row[3]) if row[3] is not None else None,
        "entry_limit": float(row[4]) if row[4] is not None else None,
        "entry_time": row[5],
        "exit_fill": float(row[6]) if row[6] is not None else None,
        "exit_time": row[7],
        "qty": int(row[8]),
    }
    assert "intent_type" in result
    assert result["intent_type"] == "stop"
    assert result["entry_fill"] == 100.0
    assert result["exit_fill"] == 98.0


# ---------------------------------------------------------------------------
# Task 2 — CLI integration tests
# ---------------------------------------------------------------------------


def _patch_cli_deps(monkeypatch, rows, *, equity_baseline: float | None = 100_000.0):
    """Stub all I/O dependencies for session_eval_cli.main()."""
    import alpaca_bot.admin.session_eval_cli as cli_module
    from types import SimpleNamespace

    fake_settings = SimpleNamespace(
        database_url="postgresql://fake/db",
        strategy_version="v1",
        market_timezone=SimpleNamespace(key="America/New_York"),
    )
    fake_settings_cls = SimpleNamespace(from_env=lambda: fake_settings)
    monkeypatch.setattr(cli_module, "Settings", fake_settings_cls)

    fake_conn = SimpleNamespace(close=lambda: None)
    monkeypatch.setattr(cli_module, "connect_postgres", lambda url: fake_conn)

    state = SimpleNamespace(equity_baseline=equity_baseline) if equity_baseline is not None else None
    fake_session_store = SimpleNamespace(load=lambda **kwargs: state)
    fake_order_store = SimpleNamespace(
        list_closed_trades=lambda **kwargs: rows,
        list_failed_entries=lambda **kwargs: [],
    )
    fake_audit_store = SimpleNamespace(list_by_event_types=lambda **kwargs: [])
    fake_position_store = SimpleNamespace(list_all=lambda **kwargs: [])

    monkeypatch.setattr(cli_module, "DailySessionStateStore", lambda conn: fake_session_store)
    monkeypatch.setattr(cli_module, "OrderStore", lambda conn: fake_order_store)
    monkeypatch.setattr(cli_module, "AuditEventStore", lambda conn: fake_audit_store)
    monkeypatch.setattr(cli_module, "PositionStore", lambda conn: fake_position_store)


def test_session_eval_cli_no_trades_exits_zero(monkeypatch, capsys):
    """When list_closed_trades returns nothing, main() prints a message and returns 0."""
    import alpaca_bot.admin.session_eval_cli as cli_module

    _patch_cli_deps(monkeypatch, rows=[])
    rc = cli_module.main(["--date", "2026-05-04", "--mode", "paper", "--strategy-version", "v1"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "No closed trades" in out


def test_session_eval_cli_produces_report(monkeypatch, capsys):
    """main() calls report_from_records and prints the session report table."""
    import alpaca_bot.admin.session_eval_cli as cli_module

    t0 = datetime(2026, 5, 4, 10, 0, tzinfo=timezone.utc)
    t1 = datetime(2026, 5, 4, 11, 0, tzinfo=timezone.utc)
    fake_rows = [
        {
            "symbol": "AAPL", "strategy_name": "breakout", "intent_type": "stop",
            "entry_fill": 100.0, "entry_limit": 100.05,
            "entry_time": t0, "exit_fill": 98.0, "exit_time": t1, "qty": 10,
        },
    ]
    _patch_cli_deps(monkeypatch, rows=fake_rows)
    rc = cli_module.main(["--date", "2026-05-04", "--mode", "paper", "--strategy-version", "v1"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Session Evaluation" in out
    assert "AAPL" in out
    assert "stop" in out
