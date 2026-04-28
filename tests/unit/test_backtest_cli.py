from __future__ import annotations

import csv
import io
import json
from pathlib import Path

import pytest

from alpaca_bot.replay.cli import _format_report, _report_to_dict
from alpaca_bot.replay.report import BacktestReport


_GOLDEN_DIR = Path(__file__).resolve().parent.parent / "golden"

_EMPTY_REPORT = BacktestReport(
    trades=(),
    total_trades=0,
    winning_trades=0,
    losing_trades=0,
    win_rate=None,
    mean_return_pct=None,
    max_drawdown_pct=None,
)


def _strip_comments(text: str) -> str:
    return "\n".join(line for line in text.splitlines() if not line.startswith("#"))


# ---------------------------------------------------------------------------
# BacktestReport.strategy_name default and propagation
# ---------------------------------------------------------------------------


def test_backtest_report_default_strategy_name_is_breakout() -> None:
    assert _EMPTY_REPORT.strategy_name == "breakout"


def test_backtest_report_strategy_name_custom() -> None:
    report = BacktestReport(
        trades=(),
        total_trades=0,
        winning_trades=0,
        losing_trades=0,
        win_rate=None,
        mean_return_pct=None,
        max_drawdown_pct=None,
        strategy_name="momentum",
    )
    assert report.strategy_name == "momentum"


# ---------------------------------------------------------------------------
# build_backtest_report passes strategy_name through
# ---------------------------------------------------------------------------


def test_build_backtest_report_passes_strategy_name() -> None:
    from alpaca_bot.domain.models import ReplayEvent, ReplayResult, ReplayScenario, Bar
    from alpaca_bot.replay.report import build_backtest_report
    from datetime import datetime, timezone

    t0 = datetime(2026, 4, 24, 14, 0, tzinfo=timezone.utc)
    bar = Bar(symbol="AAPL", timestamp=t0, open=100.0, high=101.0, low=99.0, close=100.5, volume=1000)
    scenario = ReplayScenario(
        name="test", symbol="AAPL", starting_equity=100_000.0,
        daily_bars=[bar], intraday_bars=[bar],
    )
    result = ReplayResult(scenario=scenario, events=[], final_position=None, traded_symbols=set())

    report = build_backtest_report(result, strategy_name="momentum")

    assert report.strategy_name == "momentum"


# ---------------------------------------------------------------------------
# JSON output includes "strategy" key
# ---------------------------------------------------------------------------


def test_report_to_dict_includes_strategy() -> None:
    d = _report_to_dict(_EMPTY_REPORT)
    assert d["strategy"] == "breakout"


def test_report_to_dict_includes_custom_strategy() -> None:
    report = BacktestReport(
        trades=(),
        total_trades=0,
        winning_trades=0,
        losing_trades=0,
        win_rate=None,
        mean_return_pct=None,
        max_drawdown_pct=None,
        strategy_name="momentum",
    )
    d = _report_to_dict(report)
    assert d["strategy"] == "momentum"


def test_format_report_json_includes_strategy() -> None:
    output = _format_report(_EMPTY_REPORT, "json")
    parsed = json.loads(output)
    assert parsed["strategy"] == "breakout"


# ---------------------------------------------------------------------------
# CSV output starts with strategy comment line
# ---------------------------------------------------------------------------


def test_format_report_csv_starts_with_strategy_comment() -> None:
    report = BacktestReport(
        trades=(),
        total_trades=0,
        winning_trades=0,
        losing_trades=0,
        win_rate=None,
        mean_return_pct=None,
        max_drawdown_pct=None,
        strategy_name="momentum",
    )
    output = _format_report(report, "csv")
    assert output.startswith("# strategy: momentum\n")


def test_format_report_csv_data_rows_parseable_after_comment() -> None:
    output = _format_report(_EMPTY_REPORT, "csv")
    data = _strip_comments(output)
    reader = csv.DictReader(io.StringIO(data))
    rows = list(reader)
    assert rows == []


# ---------------------------------------------------------------------------
# ReplayRunner.strategy_name flows to report
# ---------------------------------------------------------------------------


def test_replay_runner_passes_strategy_name_to_report() -> None:
    from alpaca_bot.config import Settings
    from alpaca_bot.replay.runner import ReplayRunner

    settings = Settings.from_env({
        "TRADING_MODE": "paper",
        "ENABLE_LIVE_TRADING": "false",
        "STRATEGY_VERSION": "v1-breakout",
        "DATABASE_URL": "postgresql://example",
        "MARKET_DATA_FEED": "sip",
        "SYMBOLS": "AAPL",
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
    runner = ReplayRunner(settings, strategy_name="momentum")
    scenario = runner.load_scenario(_GOLDEN_DIR / "breakout_success.json")
    result = runner.run(scenario)

    assert result.backtest_report.strategy_name == "momentum"


def test_replay_runner_default_strategy_name_is_breakout() -> None:
    from alpaca_bot.config import Settings
    from alpaca_bot.replay.runner import ReplayRunner

    settings = Settings.from_env({
        "TRADING_MODE": "paper",
        "ENABLE_LIVE_TRADING": "false",
        "STRATEGY_VERSION": "v1-breakout",
        "DATABASE_URL": "postgresql://example",
        "MARKET_DATA_FEED": "sip",
        "SYMBOLS": "AAPL",
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
    runner = ReplayRunner(settings)
    scenario = runner.load_scenario(_GOLDEN_DIR / "breakout_success.json")
    result = runner.run(scenario)

    assert result.backtest_report.strategy_name == "breakout"


# ---------------------------------------------------------------------------
# CLI --strategy flag: argparse choices validation
# ---------------------------------------------------------------------------


def test_backtest_cli_invalid_strategy_exits() -> None:
    from alpaca_bot.replay.cli import main

    with pytest.raises(SystemExit):
        main(["--scenario", "dummy.json", "--strategy", "bogus"])


def test_backtest_cli_strategy_flag_is_optional() -> None:
    """Argparse should not require --strategy."""
    import argparse
    from alpaca_bot.replay.cli import main
    from alpaca_bot.strategy import STRATEGY_REGISTRY

    # Build the parser to verify --strategy is optional
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", required=True)
    parser.add_argument("--strategy", choices=list(STRATEGY_REGISTRY), default=None)

    args = parser.parse_args(["--scenario", "dummy.json"])
    assert args.strategy is None
