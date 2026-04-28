from __future__ import annotations

import csv
import io
import json
from pathlib import Path

import pytest

from alpaca_bot.config import Settings as _Settings
from alpaca_bot.replay.cli import _format_report, _report_to_dict
from alpaca_bot.replay.report import BacktestReport


_FAKE_SETTINGS = _Settings.from_env({
    "TRADING_MODE": "paper",
    "ENABLE_LIVE_TRADING": "false",
    "STRATEGY_VERSION": "v1-breakout",
    "DATABASE_URL": "postgresql://test/db",
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
        main(["run", "--scenario", "dummy.json", "--strategy", "bogus"])


def test_backtest_cli_strategy_flag_is_optional() -> None:
    """Argparse should not require --strategy under the run subcommand."""
    import argparse
    from alpaca_bot.strategy import STRATEGY_REGISTRY

    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", required=True)
    parser.add_argument("--strategy", choices=list(STRATEGY_REGISTRY), default=None)

    args = parser.parse_args(["--scenario", "dummy.json"])
    assert args.strategy is None


# ---------------------------------------------------------------------------
# compare subcommand
# ---------------------------------------------------------------------------


def _patch_settings(monkeypatch) -> None:
    from alpaca_bot.replay import cli as _cli
    _fake_cls = type("S", (), {"from_env": staticmethod(lambda: _FAKE_SETTINGS)})
    monkeypatch.setattr(_cli, "Settings", _fake_cls)


class FakeReplayResult:
    def __init__(self, report: BacktestReport) -> None:
        self.backtest_report = report


class FakeReplayRunner:
    """Minimal ReplayRunner replacement — returns a fixed BacktestReport."""

    def __init__(self, report: BacktestReport) -> None:
        self._report = report

    def load_scenario(self, path):
        return path  # return path as sentinel

    def run(self, scenario) -> FakeReplayResult:
        return FakeReplayResult(self._report)


def _make_report(strategy_name: str, total_trades: int = 5) -> BacktestReport:
    return BacktestReport(
        trades=(),
        total_trades=total_trades,
        winning_trades=3,
        losing_trades=2,
        win_rate=0.6,
        mean_return_pct=0.0025,
        max_drawdown_pct=0.005,
        sharpe_ratio=1.2,
        strategy_name=strategy_name,
    )


def test_compare_default_runs_all_strategies(monkeypatch) -> None:
    from alpaca_bot.replay import cli as cli_module
    from alpaca_bot.strategy import STRATEGY_REGISTRY

    _patch_settings(monkeypatch)

    def fake_runner_factory(settings, *, signal_evaluator=None, strategy_name="breakout"):
        return FakeReplayRunner(_make_report(strategy_name))

    monkeypatch.setattr(cli_module, "ReplayRunner", fake_runner_factory)

    out_lines: list[str] = []
    monkeypatch.setattr(cli_module, "_write_output", lambda text, path: out_lines.append(text))

    cli_module._cmd_compare(
        type("Args", (), {
            "scenario": "dummy.json",
            "strategies": None,
            "format": "json",
            "output": "-",
        })()
    )

    parsed = json.loads(out_lines[0])
    assert len(parsed) == len(STRATEGY_REGISTRY)
    assert {r["strategy"] for r in parsed} == set(STRATEGY_REGISTRY)


def test_compare_subset_strategies(monkeypatch) -> None:
    from alpaca_bot.replay import cli as cli_module

    _patch_settings(monkeypatch)

    def fake_runner_factory(settings, *, signal_evaluator=None, strategy_name="breakout"):
        return FakeReplayRunner(_make_report(strategy_name))

    monkeypatch.setattr(cli_module, "ReplayRunner", fake_runner_factory)

    out_lines: list[str] = []
    monkeypatch.setattr(cli_module, "_write_output", lambda text, path: out_lines.append(text))

    cli_module._cmd_compare(
        type("Args", (), {
            "scenario": "dummy.json",
            "strategies": "breakout,momentum",
            "format": "json",
            "output": "-",
        })()
    )

    parsed = json.loads(out_lines[0])
    assert len(parsed) == 2
    assert {r["strategy"] for r in parsed} == {"breakout", "momentum"}


def test_compare_invalid_strategy_exits(monkeypatch) -> None:
    from alpaca_bot.replay import cli as cli_module

    _patch_settings(monkeypatch)

    with pytest.raises(SystemExit):
        cli_module.main(["compare", "--scenario", "dummy.json", "--strategies", "breakout,bogus"])


def test_compare_json_output_shape(monkeypatch) -> None:
    from alpaca_bot.replay import cli as cli_module

    _patch_settings(monkeypatch)

    def fake_runner_factory(settings, *, signal_evaluator=None, strategy_name="breakout"):
        return FakeReplayRunner(_make_report(strategy_name))

    monkeypatch.setattr(cli_module, "ReplayRunner", fake_runner_factory)

    out_lines: list[str] = []
    monkeypatch.setattr(cli_module, "_write_output", lambda text, path: out_lines.append(text))

    cli_module._cmd_compare(
        type("Args", (), {
            "scenario": "dummy.json",
            "strategies": "breakout",
            "format": "json",
            "output": "-",
        })()
    )

    parsed = json.loads(out_lines[0])
    assert len(parsed) == 1
    row = parsed[0]
    assert set(row.keys()) == {
        "strategy", "total_trades", "win_rate",
        "mean_return_pct", "max_drawdown_pct", "sharpe_ratio",
    }
    assert "trades" not in row


def test_compare_csv_output_has_header_and_rows(monkeypatch) -> None:
    from alpaca_bot.replay import cli as cli_module

    _patch_settings(monkeypatch)

    def fake_runner_factory(settings, *, signal_evaluator=None, strategy_name="breakout"):
        return FakeReplayRunner(_make_report(strategy_name))

    monkeypatch.setattr(cli_module, "ReplayRunner", fake_runner_factory)

    out_lines: list[str] = []
    monkeypatch.setattr(cli_module, "_write_output", lambda text, path: out_lines.append(text))

    cli_module._cmd_compare(
        type("Args", (), {
            "scenario": "dummy.json",
            "strategies": "breakout,momentum",
            "format": "csv",
            "output": "-",
        })()
    )

    reader = csv.DictReader(io.StringIO(out_lines[0]))
    rows = list(reader)
    assert len(rows) == 2
    assert set(reader.fieldnames) == {
        "strategy", "total_trades", "win_rate",
        "mean_return_pct", "max_drawdown_pct", "sharpe_ratio",
    }


def test_compare_null_fields_json(monkeypatch) -> None:
    from alpaca_bot.replay import cli as cli_module

    _patch_settings(monkeypatch)

    null_report = BacktestReport(
        trades=(),
        total_trades=0,
        winning_trades=0,
        losing_trades=0,
        win_rate=None,
        mean_return_pct=None,
        max_drawdown_pct=None,
        sharpe_ratio=None,
        strategy_name="breakout",
    )

    def fake_runner_factory(settings, *, signal_evaluator=None, strategy_name="breakout"):
        return FakeReplayRunner(null_report)

    monkeypatch.setattr(cli_module, "ReplayRunner", fake_runner_factory)
    out_lines: list[str] = []
    monkeypatch.setattr(cli_module, "_write_output", lambda text, path: out_lines.append(text))

    cli_module._cmd_compare(
        type("Args", (), {
            "scenario": "dummy.json",
            "strategies": "breakout",
            "format": "json",
            "output": "-",
        })()
    )

    parsed = json.loads(out_lines[0])
    assert parsed[0]["win_rate"] is None
    assert parsed[0]["sharpe_ratio"] is None


def test_compare_null_fields_csv(monkeypatch) -> None:
    from alpaca_bot.replay import cli as cli_module

    _patch_settings(monkeypatch)

    null_report = BacktestReport(
        trades=(),
        total_trades=0,
        winning_trades=0,
        losing_trades=0,
        win_rate=None,
        mean_return_pct=None,
        max_drawdown_pct=None,
        sharpe_ratio=None,
        strategy_name="breakout",
    )

    def fake_runner_factory(settings, *, signal_evaluator=None, strategy_name="breakout"):
        return FakeReplayRunner(null_report)

    monkeypatch.setattr(cli_module, "ReplayRunner", fake_runner_factory)
    out_lines: list[str] = []
    monkeypatch.setattr(cli_module, "_write_output", lambda text, path: out_lines.append(text))

    cli_module._cmd_compare(
        type("Args", (), {
            "scenario": "dummy.json",
            "strategies": "breakout",
            "format": "csv",
            "output": "-",
        })()
    )

    reader = csv.DictReader(io.StringIO(out_lines[0]))
    rows = list(reader)
    assert rows[0]["win_rate"] == ""
    assert rows[0]["sharpe_ratio"] == ""


def test_compare_output_to_file(tmp_path, monkeypatch) -> None:
    from alpaca_bot.replay import cli as cli_module

    _patch_settings(monkeypatch)

    def fake_runner_factory(settings, *, signal_evaluator=None, strategy_name="breakout"):
        return FakeReplayRunner(_make_report(strategy_name))

    monkeypatch.setattr(cli_module, "ReplayRunner", fake_runner_factory)

    out_file = tmp_path / "out.json"
    cli_module._cmd_compare(
        type("Args", (), {
            "scenario": "dummy.json",
            "strategies": "breakout",
            "format": "json",
            "output": str(out_file),
        })()
    )

    assert out_file.exists()
    parsed = json.loads(out_file.read_text())
    assert len(parsed) == 1


# ---------------------------------------------------------------------------
# sweep subcommand
# ---------------------------------------------------------------------------


def test_sweep_default_grid_runs(monkeypatch) -> None:
    from alpaca_bot.replay import cli as cli_module
    from alpaca_bot.tuning.sweep import TuningCandidate

    _patch_settings(monkeypatch)

    def fake_runner_factory(settings, *, signal_evaluator=None, strategy_name="breakout"):
        r = FakeReplayRunner(_make_report(strategy_name))
        r.load_scenario = lambda path: path
        return r

    monkeypatch.setattr(cli_module, "ReplayRunner", fake_runner_factory)

    def fake_run_sweep(**kwargs):
        report = _make_report("breakout")
        return [TuningCandidate(params={"BREAKOUT_LOOKBACK_BARS": "20"}, report=report, score=1.0)]

    monkeypatch.setattr(cli_module, "run_sweep", fake_run_sweep)
    monkeypatch.setattr("builtins.print", lambda *a: None)

    args = type("Args", (), {
        "scenario": "dummy.json",
        "strategy": "breakout",
        "grid": [],
        "min_trades": 3,
    })()
    ret = cli_module._cmd_sweep(args)
    assert ret == 0


def test_sweep_min_trades_filter(monkeypatch) -> None:
    """Zero-trade scenario yields no ranked output without crashing."""
    from alpaca_bot.replay import cli as cli_module
    from alpaca_bot.tuning.sweep import TuningCandidate

    _patch_settings(monkeypatch)

    def fake_runner_factory(settings, *, signal_evaluator=None, strategy_name="breakout"):
        r = FakeReplayRunner(_make_report(strategy_name, total_trades=0))
        r.load_scenario = lambda path: path
        return r

    monkeypatch.setattr(cli_module, "ReplayRunner", fake_runner_factory)

    output_lines: list[str] = []
    monkeypatch.setattr("builtins.print", lambda *a: output_lines.append(" ".join(str(x) for x in a)))

    def fake_run_sweep(**kwargs):
        return [TuningCandidate(params={}, report=_make_report("breakout", 0), score=None)]

    monkeypatch.setattr(cli_module, "run_sweep", fake_run_sweep)

    args = type("Args", (), {
        "scenario": "dummy.json",
        "strategy": "breakout",
        "grid": [],
        "min_trades": 3,
    })()
    ret = cli_module._cmd_sweep(args)
    assert ret == 0
    assert any("No scored" in line for line in output_lines)


def test_sweep_non_breakout_strategy(monkeypatch) -> None:
    """--strategy momentum passes signal_evaluator to run_sweep."""
    from alpaca_bot.replay import cli as cli_module
    from alpaca_bot.strategy import STRATEGY_REGISTRY
    from alpaca_bot.tuning.sweep import TuningCandidate

    _patch_settings(monkeypatch)

    sweep_kwargs_captured: list[dict] = []

    def fake_run_sweep(**kwargs):
        sweep_kwargs_captured.append(kwargs)
        return [TuningCandidate(params={}, report=_make_report("momentum"), score=1.0)]

    def fake_runner_factory(settings, *, signal_evaluator=None, strategy_name="breakout"):
        r = FakeReplayRunner(_make_report(strategy_name))
        r.load_scenario = lambda path: path
        return r

    monkeypatch.setattr(cli_module, "ReplayRunner", fake_runner_factory)
    monkeypatch.setattr(cli_module, "run_sweep", fake_run_sweep)
    monkeypatch.setattr("builtins.print", lambda *a: None)

    args = type("Args", (), {
        "scenario": "dummy.json",
        "strategy": "momentum",
        "grid": [],
        "min_trades": 3,
    })()
    cli_module._cmd_sweep(args)

    assert sweep_kwargs_captured
    assert sweep_kwargs_captured[0]["signal_evaluator"] is STRATEGY_REGISTRY["momentum"]


def test_parse_grid_importable_from_sweep_module() -> None:
    from alpaca_bot.tuning.sweep import _parse_grid
    result = _parse_grid(["BREAKOUT_LOOKBACK_BARS=15,20,25"])
    assert result == {"BREAKOUT_LOOKBACK_BARS": ["15", "20", "25"]}
