from __future__ import annotations

import json
import csv
import io
from datetime import datetime, timezone
from pathlib import Path

import pytest

from alpaca_bot.domain.enums import IntentType
from alpaca_bot.domain.models import ReplayEvent, ReplayResult, ReplayScenario, Bar
from alpaca_bot.replay.report import BacktestReport, build_backtest_report


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_T0 = datetime(2026, 4, 24, 14, 0, tzinfo=timezone.utc)
_T1 = datetime(2026, 4, 24, 14, 15, tzinfo=timezone.utc)
_T2 = datetime(2026, 4, 24, 14, 30, tzinfo=timezone.utc)


def _make_result(events: list[ReplayEvent]) -> ReplayResult:
    bar = Bar(symbol="AAPL", timestamp=_T0, open=100.0, high=101.0, low=99.0, close=100.5, volume=1000)
    scenario = ReplayScenario(
        name="test",
        symbol="AAPL",
        starting_equity=100_000.0,
        daily_bars=[bar],
        intraday_bars=[bar],
    )
    return ReplayResult(
        scenario=scenario,
        events=events,
        final_position=None,
        traded_symbols=set(),
    )


def _fill(entry_price: float = 150.0, quantity: int = 10, t: datetime = _T0) -> ReplayEvent:
    return ReplayEvent(
        event_type=IntentType.ENTRY_FILLED,
        symbol="AAPL",
        timestamp=t,
        details={"entry_price": entry_price, "quantity": quantity, "initial_stop_price": 148.0},
    )


def _stop_exit(exit_price: float = 148.0, t: datetime = _T1) -> ReplayEvent:
    return ReplayEvent(event_type=IntentType.STOP_HIT, symbol="AAPL", timestamp=t, details={"exit_price": exit_price})


def _eod_exit(exit_price: float = 155.0, t: datetime = _T2) -> ReplayEvent:
    return ReplayEvent(event_type=IntentType.EOD_EXIT, symbol="AAPL", timestamp=t, details={"exit_price": exit_price})


# ---------------------------------------------------------------------------
# build_backtest_report: zero trades
# ---------------------------------------------------------------------------


def test_no_trades_returns_nulls() -> None:
    result = _make_result([])
    report = build_backtest_report(result)
    assert report.total_trades == 0
    assert report.win_rate is None
    assert report.mean_return_pct is None
    assert report.max_drawdown_pct is None
    assert report.sharpe_ratio is None


def test_fill_without_exit_produces_no_trade() -> None:
    result = _make_result([_fill()])
    report = build_backtest_report(result)
    assert report.total_trades == 0


# ---------------------------------------------------------------------------
# build_backtest_report: single trades
# ---------------------------------------------------------------------------


def test_stop_fill_trade_pnl_correct() -> None:
    result = _make_result([_fill(entry_price=150.0, quantity=10), _stop_exit(exit_price=148.0)])
    report = build_backtest_report(result)
    assert report.total_trades == 1
    trade = report.trades[0]
    assert trade.pnl == pytest.approx((148.0 - 150.0) * 10)
    assert trade.exit_reason == "stop"
    assert trade.return_pct == pytest.approx((148.0 - 150.0) / 150.0)


def test_eod_fill_trade_pnl_correct() -> None:
    result = _make_result([_fill(entry_price=150.0, quantity=10), _eod_exit(exit_price=155.0)])
    report = build_backtest_report(result)
    assert report.total_trades == 1
    trade = report.trades[0]
    assert trade.pnl == pytest.approx((155.0 - 150.0) * 10)
    assert trade.exit_reason == "eod"


def test_single_winner_win_rate_is_one() -> None:
    result = _make_result([_fill(), _eod_exit(exit_price=155.0)])
    report = build_backtest_report(result)
    assert report.win_rate == pytest.approx(1.0)
    assert report.winning_trades == 1
    assert report.losing_trades == 0


def test_single_loser_win_rate_is_zero() -> None:
    result = _make_result([_fill(), _stop_exit(exit_price=148.0)])
    report = build_backtest_report(result)
    assert report.win_rate == pytest.approx(0.0)
    assert report.winning_trades == 0
    assert report.losing_trades == 1


# ---------------------------------------------------------------------------
# build_backtest_report: max_drawdown_pct
# ---------------------------------------------------------------------------


def test_max_drawdown_loss_from_starting_equity() -> None:
    """A single loss on a 100k account registers as a small but non-None drawdown."""
    events = [
        _fill(entry_price=150.0, quantity=10, t=_T0),
        _stop_exit(exit_price=148.0, t=_T1),  # pnl = -20; equity 100000→99980
    ]
    result = _make_result(events)
    report = build_backtest_report(result)
    # drawdown = 20 / 100_000 = 0.0002
    assert report.max_drawdown_pct == pytest.approx(20 / 100_000)


def test_max_drawdown_none_when_all_wins() -> None:
    """Equity only rises → peak never exceeded → no drawdown recorded."""
    events = [
        _fill(entry_price=150.0, quantity=10, t=_T0),
        _eod_exit(exit_price=155.0, t=_T1),  # pnl = +50
        ReplayEvent(event_type=IntentType.ENTRY_FILLED, symbol="AAPL", timestamp=_T1,
                    details={"entry_price": 155.0, "quantity": 10, "initial_stop_price": 153.0}),
        _eod_exit(exit_price=160.0, t=_T2),   # pnl = +50
    ]
    result = _make_result(events)
    report = build_backtest_report(result)
    assert report.max_drawdown_pct is None


def test_max_drawdown_correct_after_loss_following_gain() -> None:
    """Gain then loss: drawdown is relative to absolute equity peak, not cumulative-PnL peak."""
    events = [
        _fill(entry_price=150.0, quantity=10, t=_T0),
        _eod_exit(exit_price=155.0, t=_T1),  # pnl = +50 → equity=100050, peak=100050
        ReplayEvent(event_type=IntentType.ENTRY_FILLED, symbol="AAPL", timestamp=_T1,
                    details={"entry_price": 155.0, "quantity": 10, "initial_stop_price": 152.0}),
        _stop_exit(exit_price=153.0, t=_T2),  # pnl = -20 → equity=100030, drawdown=20/100050
    ]
    result = _make_result(events)
    report = build_backtest_report(result)
    assert report.max_drawdown_pct == pytest.approx(20 / 100_050)


def test_sharpe_ratio_none_for_single_trade() -> None:
    result = _make_result([_fill(), _eod_exit()])
    assert build_backtest_report(result).sharpe_ratio is None  # need n >= 2


def test_sharpe_ratio_two_wins() -> None:
    result = _make_result([
        _fill(entry_price=150.0, quantity=10, t=_T0),
        _eod_exit(exit_price=155.0, t=_T1),
        ReplayEvent(event_type=IntentType.ENTRY_FILLED, symbol="AAPL", timestamp=_T1,
                    details={"entry_price": 155.0, "quantity": 10, "initial_stop_price": 153.0}),
        _eod_exit(exit_price=160.0, t=_T2),
    ])
    report = build_backtest_report(result)
    assert report.sharpe_ratio is not None
    assert report.sharpe_ratio > 0


def test_sharpe_ratio_none_for_identical_returns() -> None:
    result = _make_result([
        _fill(entry_price=100.0, quantity=10, t=_T0),
        _eod_exit(exit_price=110.0, t=_T1),
        ReplayEvent(event_type=IntentType.ENTRY_FILLED, symbol="AAPL", timestamp=_T1,
                    details={"entry_price": 100.0, "quantity": 10, "initial_stop_price": 98.0}),
        _eod_exit(exit_price=110.0, t=_T2),
    ])
    report = build_backtest_report(result)
    assert report.sharpe_ratio is None


# ---------------------------------------------------------------------------
# CLI: JSON and CSV export
# ---------------------------------------------------------------------------


def test_cli_json_output(tmp_path: Path) -> None:
    from alpaca_bot.replay.cli import _format_report, _report_to_dict
    from alpaca_bot.replay.report import ReplayTradeRecord

    trade = ReplayTradeRecord(
        symbol="AAPL",
        entry_price=150.0,
        exit_price=155.0,
        quantity=10,
        entry_time=_T0,
        exit_time=_T1,
        exit_reason="eod",
        pnl=50.0,
        return_pct=0.0333,
    )
    report = BacktestReport(
        trades=(trade,),
        total_trades=1,
        winning_trades=1,
        losing_trades=0,
        win_rate=1.0,
        mean_return_pct=0.0333,
        max_drawdown_pct=0.0,
    )
    output = _format_report(report, "json")
    parsed = json.loads(output)
    assert parsed["total_trades"] == 1
    assert parsed["win_rate"] == 1.0
    assert len(parsed["trades"]) == 1
    assert parsed["trades"][0]["symbol"] == "AAPL"
    assert parsed["trades"][0]["exit_reason"] == "eod"


def test_cli_csv_output() -> None:
    from alpaca_bot.replay.cli import _format_report
    from alpaca_bot.replay.report import ReplayTradeRecord

    trade = ReplayTradeRecord(
        symbol="AAPL",
        entry_price=150.0,
        exit_price=148.0,
        quantity=10,
        entry_time=_T0,
        exit_time=_T1,
        exit_reason="stop",
        pnl=-20.0,
        return_pct=-0.01333,
    )
    report = BacktestReport(
        trades=(trade,),
        total_trades=1,
        winning_trades=0,
        losing_trades=1,
        win_rate=0.0,
        mean_return_pct=-0.01333,
        max_drawdown_pct=None,
    )
    output = _format_report(report, "csv")
    data_lines = "\n".join(line for line in output.splitlines() if not line.startswith("#"))
    reader = csv.DictReader(io.StringIO(data_lines))
    rows = list(reader)
    assert len(rows) == 1
    assert rows[0]["symbol"] == "AAPL"
    assert rows[0]["exit_reason"] == "stop"
    assert float(rows[0]["pnl"]) == pytest.approx(-20.0)


def test_cli_csv_empty_report() -> None:
    from alpaca_bot.replay.cli import _format_report

    report = BacktestReport(
        trades=(),
        total_trades=0,
        winning_trades=0,
        losing_trades=0,
        win_rate=None,
        mean_return_pct=None,
        max_drawdown_pct=None,
    )
    output = _format_report(report, "csv")
    data_lines = "\n".join(line for line in output.splitlines() if not line.startswith("#"))
    reader = csv.DictReader(io.StringIO(data_lines))
    rows = list(reader)
    assert rows == []


# ---------------------------------------------------------------------------
# ReplayRunner integration: backtest_report attached to result
# ---------------------------------------------------------------------------


def test_replay_runner_attaches_backtest_report_to_result() -> None:
    from pathlib import Path
    from alpaca_bot.config import Settings
    from alpaca_bot.replay.runner import ReplayRunner
    from alpaca_bot.replay.report import BacktestReport

    def make_settings() -> Settings:
        return Settings.from_env({
            "TRADING_MODE": "paper",
            "ENABLE_LIVE_TRADING": "false",
            "STRATEGY_VERSION": "v1-breakout",
            "DATABASE_URL": "postgresql://alpaca_bot:secret@db.example.com:5432/alpaca_bot",
            "MARKET_DATA_FEED": "sip",
            "SYMBOLS": "AAPL,MSFT,SPY",
            "DAILY_SMA_PERIOD": "20",
            "BREAKOUT_LOOKBACK_BARS": "20",
            "RELATIVE_VOLUME_LOOKBACK_BARS": "20",
            "RELATIVE_VOLUME_THRESHOLD": "1.5",
            "ENTRY_TIMEFRAME_MINUTES": "15",
            "RISK_PER_TRADE_PCT": "0.0025",
            "MAX_POSITION_PCT": "0.05",
            "MAX_OPEN_POSITIONS": "3",
            "DAILY_LOSS_LIMIT_PCT": "0.01",
            "STOP_LIMIT_BUFFER_PCT": "0.001",
            "BREAKOUT_STOP_BUFFER_PCT": "0.001",
            "ENTRY_STOP_PRICE_BUFFER": "0.01",
            "ENTRY_WINDOW_START": "10:00",
            "ENTRY_WINDOW_END": "15:30",
            "FLATTEN_TIME": "15:45",
        })

    golden_dir = Path(__file__).resolve().parent.parent / "golden"
    runner = ReplayRunner(make_settings())
    scenario = runner.load_scenario(golden_dir / "breakout_success.json")
    result = runner.run(scenario)

    assert result.backtest_report is not None
    assert isinstance(result.backtest_report, BacktestReport)
    # Golden scenario ends with EOD_EXIT — should have 1 trade
    assert result.backtest_report.total_trades == 1
    assert result.backtest_report.trades[0].exit_reason == "eod"


# ---------------------------------------------------------------------------
# profit_factor
# ---------------------------------------------------------------------------


def test_profit_factor_none_for_zero_trades() -> None:
    result = _make_result([])
    assert build_backtest_report(result).profit_factor is None


def test_profit_factor_none_when_no_losses() -> None:
    """All winners → no losses to divide by → None (not penalized)."""
    result = _make_result([
        _fill(entry_price=150.0, quantity=10, t=_T0),
        _eod_exit(exit_price=155.0, t=_T1),
    ])
    report = build_backtest_report(result)
    assert report.profit_factor is None


def test_profit_factor_zero_when_all_losers() -> None:
    """All losers → gross_wins = 0 → profit_factor = 0.0."""
    result = _make_result([
        _fill(entry_price=150.0, quantity=10, t=_T0),
        _stop_exit(exit_price=148.0, t=_T1),  # pnl = -20
    ])
    report = build_backtest_report(result)
    assert report.profit_factor == pytest.approx(0.0)


def test_profit_factor_correct_with_mixed_trades() -> None:
    """2 wins (+50 + +50) against 1 loss (-20) → profit_factor = 100/20 = 5.0."""
    _T3 = datetime(2026, 4, 24, 14, 45, tzinfo=timezone.utc)
    _T4 = datetime(2026, 4, 24, 15, 0, tzinfo=timezone.utc)
    result = _make_result([
        _fill(entry_price=150.0, quantity=10, t=_T0),
        _eod_exit(exit_price=155.0, t=_T1),  # pnl = +50
        ReplayEvent(
            event_type=IntentType.ENTRY_FILLED, symbol="AAPL", timestamp=_T1,
            details={"entry_price": 155.0, "quantity": 10, "initial_stop_price": 153.0},
        ),
        _eod_exit(exit_price=160.0, t=_T2),  # pnl = +50
        ReplayEvent(
            event_type=IntentType.ENTRY_FILLED, symbol="AAPL", timestamp=_T3,
            details={"entry_price": 160.0, "quantity": 10, "initial_stop_price": 158.0},
        ),
        ReplayEvent(
            event_type=IntentType.STOP_HIT, symbol="AAPL", timestamp=_T4,
            details={"exit_price": 158.0},  # pnl = -20
        ),
    ])
    report = build_backtest_report(result)
    assert report.total_trades == 3
    assert report.profit_factor == pytest.approx(100.0 / 20.0)


# ---------------------------------------------------------------------------
# exit type segmentation
# ---------------------------------------------------------------------------


def test_exit_type_fields_zero_for_no_trades() -> None:
    result = _make_result([])
    report = build_backtest_report(result)
    assert report.stop_wins == 0
    assert report.stop_losses == 0
    assert report.eod_wins == 0
    assert report.eod_losses == 0
    assert report.avg_hold_minutes is None


def test_exit_type_fields_eod_win() -> None:
    result = _make_result([_fill(entry_price=150.0, quantity=10, t=_T0),
                           _eod_exit(exit_price=155.0, t=_T1)])
    report = build_backtest_report(result)
    assert report.stop_wins == 0
    assert report.eod_wins == 1
    assert report.stop_losses == 0
    assert report.eod_losses == 0


def test_exit_type_fields_stop_loss() -> None:
    result = _make_result([_fill(entry_price=150.0, quantity=10, t=_T0),
                           _stop_exit(exit_price=148.0, t=_T1)])
    report = build_backtest_report(result)
    assert report.stop_wins == 0
    assert report.stop_losses == 1
    assert report.eod_wins == 0
    assert report.eod_losses == 0


def test_exit_type_fields_mixed() -> None:
    """2 eod wins + 1 stop loss."""
    _T3 = datetime(2026, 4, 24, 14, 45, tzinfo=timezone.utc)
    _T4 = datetime(2026, 4, 24, 15, 0, tzinfo=timezone.utc)
    result = _make_result([
        _fill(entry_price=150.0, quantity=10, t=_T0),
        _eod_exit(exit_price=155.0, t=_T1),        # eod win
        ReplayEvent(event_type=IntentType.ENTRY_FILLED, symbol="AAPL", timestamp=_T1,
                    details={"entry_price": 155.0, "quantity": 10, "initial_stop_price": 153.0}),
        _eod_exit(exit_price=160.0, t=_T2),        # eod win
        ReplayEvent(event_type=IntentType.ENTRY_FILLED, symbol="AAPL", timestamp=_T3,
                    details={"entry_price": 160.0, "quantity": 10, "initial_stop_price": 158.0}),
        ReplayEvent(event_type=IntentType.STOP_HIT, symbol="AAPL", timestamp=_T4,
                    details={"exit_price": 158.0}),  # stop loss
    ])
    report = build_backtest_report(result)
    assert report.eod_wins == 2
    assert report.eod_losses == 0
    assert report.stop_wins == 0
    assert report.stop_losses == 1


def test_avg_hold_minutes_correct() -> None:
    """_T0 to _T1 = 15 min; _T1 to _T2 = 15 min → avg = 15.0."""
    result = _make_result([
        _fill(entry_price=150.0, quantity=10, t=_T0),
        _eod_exit(exit_price=155.0, t=_T1),
        ReplayEvent(event_type=IntentType.ENTRY_FILLED, symbol="AAPL", timestamp=_T1,
                    details={"entry_price": 155.0, "quantity": 10, "initial_stop_price": 153.0}),
        _eod_exit(exit_price=160.0, t=_T2),
    ])
    report = build_backtest_report(result)
    assert report.avg_hold_minutes == pytest.approx(15.0)
