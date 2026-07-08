from __future__ import annotations

import csv
import dataclasses
import io
import json
import os
from pathlib import Path
import subprocess
import sys

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
    "REPLAY_SLIPPAGE_BPS": "0",
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


def test_regime_benchmark_attaches_from_unsampled_scenario_file(tmp_path: Path) -> None:
    from datetime import datetime, timezone

    from alpaca_bot.domain.models import Bar, ReplayScenario
    from alpaca_bot.replay.cli import _with_regime_daily_bars_from_dir

    ts = datetime(2026, 1, 2, 20, 0, tzinfo=timezone.utc)
    bar = Bar(
        symbol="AAPL",
        timestamp=ts,
        open=100.0,
        high=101.0,
        low=99.0,
        close=100.5,
        volume=1000,
    )
    spy_payload = {
        "name": "SPY_252d",
        "symbol": "SPY",
        "starting_equity": 100_000.0,
        "daily_bars": [
            {
                "symbol": "SPY",
                "timestamp": ts.isoformat(),
                "open": 500.0,
                "high": 501.0,
                "low": 499.0,
                "close": 500.5,
                "volume": 1000,
            }
        ],
        "intraday_bars": [
            {
                "symbol": "SPY",
                "timestamp": ts.isoformat(),
                "open": 500.0,
                "high": 501.0,
                "low": 499.0,
                "close": 500.5,
                "volume": 1000,
            }
        ],
    }
    (tmp_path / "SPY_252d.json").write_text(json.dumps(spy_payload))
    scenario = ReplayScenario(
        name="AAPL_252d",
        symbol="AAPL",
        starting_equity=100_000.0,
        daily_bars=[bar],
        intraday_bars=[bar],
    )

    [enriched] = _with_regime_daily_bars_from_dir(
        [scenario],
        scenario_dir=tmp_path,
        settings=_FAKE_SETTINGS,
    )

    assert enriched.regime_daily_bars is not None
    assert enriched.regime_daily_bars[0].symbol == "SPY"


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
        "REPLAY_SLIPPAGE_BPS": "0",
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
        "REPLAY_SLIPPAGE_BPS": "0",
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
        "mean_return_pct", "max_drawdown_pct", "sharpe_ratio", "profit_factor",
        "stop_wins", "stop_losses", "eod_wins", "eod_losses",
        "profit_target_wins", "profit_target_losses",
        "avg_hold_minutes", "avg_win_return_pct", "avg_loss_return_pct",
        "expectancy_pct", "max_consecutive_losses", "max_consecutive_wins",
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
        "mean_return_pct", "max_drawdown_pct", "sharpe_ratio", "profit_factor",
        "stop_wins", "stop_losses", "eod_wins", "eod_losses",
        "profit_target_wins", "profit_target_losses",
        "avg_hold_minutes", "avg_win_return_pct", "avg_loss_return_pct",
        "expectancy_pct", "max_consecutive_losses", "max_consecutive_wins",
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


# ---------------------------------------------------------------------------
# audit subcommand
# ---------------------------------------------------------------------------

_GOLDEN_SCENARIO = Path(__file__).resolve().parent.parent / "golden" / "breakout_success.json"

# Same env dict as tests/unit/test_replay_golden.py make_settings — the only
# addition is REPLAY_SLIPPAGE_BPS, pinned so the test is deterministic even if
# the ambient environment sets it.
_AUDIT_ENV = {
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
    "ATR_PERIOD": "14",
    "REPLAY_SLIPPAGE_BPS": "0",
}


def _set_audit_env(monkeypatch) -> None:
    for key, value in _AUDIT_ENV.items():
        monkeypatch.setenv(key, value)


def test_replay_cli_module_executes_as_main() -> None:
    env = os.environ.copy()
    env.update(_AUDIT_ENV)
    root = Path(__file__).resolve().parents[2]
    existing_pythonpath = env.get("PYTHONPATH")
    env["PYTHONPATH"] = (
        str(root / "src")
        if not existing_pythonpath
        else f"{root / 'src'}{os.pathsep}{existing_pythonpath}"
    )

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "alpaca_bot.replay.cli",
            "run",
            "--scenario",
            str(_GOLDEN_SCENARIO),
            "--strategy",
            "breakout",
            "--format",
            "json",
        ],
        check=False,
        env=env,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["strategy"] == "breakout"
    assert payload["total_trades"] == 1


def test_audit_subcommand_writes_markdown_and_json(tmp_path, monkeypatch):
    import shutil

    from alpaca_bot.replay.cli import main

    _set_audit_env(monkeypatch)
    scenario_dir = tmp_path / "scenarios"
    scenario_dir.mkdir()
    shutil.copy(_GOLDEN_SCENARIO, scenario_dir / "a.json")
    shutil.copy(_GOLDEN_SCENARIO, scenario_dir / "b.json")

    out_md = tmp_path / "audit.md"
    out_json = tmp_path / "audit.json"
    out_jsonl = tmp_path / "audit.jsonl"
    rc = main([
        "audit",
        "--scenario-dir", str(scenario_dir),
        "--strategies", "breakout",
        "--slippage-bps", "5",
        "--output", str(out_md),
        "--json", str(out_json),
        "--jsonl", str(out_jsonl),
    ])
    assert rc == 0

    md = out_md.read_text()
    assert "| strategy |" in md
    assert "breakout" in md

    rows = json.loads(out_json.read_text())
    assert len(rows) == 1
    row = rows[0]
    assert row["strategy"] == "breakout"
    assert row["scenarios"] == 2
    assert row["trades"] >= 2  # golden scenario trades once, copied twice
    assert row["verdict"] in (
        "negative-edge", "no-evidence", "positive-edge", "insufficient-data"
    )
    assert row["cost_drag"] >= 0

    jsonl_rows = [
        json.loads(line) for line in out_jsonl.read_text().splitlines() if line
    ]
    assert len(jsonl_rows) == 1
    assert jsonl_rows[0]["strategy"] == "breakout"
    assert jsonl_rows[0]["scenarios"] == 2
    assert jsonl_rows[0]["slippage_bps"] == 5.0


def test_portfolio_basket_audit_subcommand_scores_combined_basket(
    tmp_path,
    monkeypatch,
) -> None:
    from datetime import datetime, timezone

    from alpaca_bot.replay import cli as replay_cli
    from alpaca_bot.replay.audit import StrategyAuditRow
    from alpaca_bot.replay.cli import main
    from alpaca_bot.replay.report import ReplayTradeRecord

    _set_audit_env(monkeypatch)
    scenario_dir = tmp_path / "scenarios"
    scenario_dir.mkdir()
    payload = json.loads(_GOLDEN_SCENARIO.read_text())
    for symbol in ("AAA", "BBB"):
        scenario = dict(payload)
        scenario["name"] = symbol
        scenario["symbol"] = symbol
        for key in ("daily_bars", "intraday_bars"):
            scenario[key] = [dict(bar, symbol=symbol) for bar in payload[key]]
        (scenario_dir / f"{symbol}.json").write_text(json.dumps(scenario))

    captured: dict[str, object] = {}

    def fake_basket_pooled_trades(
        scenarios,
        settings,
        strategy_names,
        *,
        strategy_equity_scales=None,
        on_progress=None,
    ):
        captured["basket"] = tuple(strategy_names)
        captured["strategy_equity_scales"] = dict(strategy_equity_scales or {})
        captured["max_open_positions"] = settings.max_open_positions
        captured["starting_equity"] = [scenario.starting_equity for scenario in scenarios]
        if on_progress is not None:
            on_progress("basket replay complete")
        now = datetime(2026, 1, 2, 15, 0, tzinfo=timezone.utc)
        return [
            ReplayTradeRecord(
                symbol="AAA",
                entry_price=10.0,
                exit_price=11.0,
                quantity=1.0,
                entry_time=now,
                exit_time=now,
                exit_reason="eod",
                pnl=1.0,
                return_pct=0.1,
                strategy_name="bull_flag",
            ),
            ReplayTradeRecord(
                symbol="BBB",
                entry_price=20.0,
                exit_price=19.0,
                quantity=1.0,
                entry_time=now,
                exit_time=now,
                exit_reason="eod",
                pnl=-1.0,
                return_pct=-0.05,
                strategy_name="orb",
            ),
        ]

    def fake_run_audit(*, scenarios, settings, strategies, pooled_trades_fn, **kwargs):
        captured["audit_strategies"] = list(strategies)
        costed = dataclasses.replace(settings, replay_slippage_bps=2.0)
        pooled_trades_fn(scenarios, costed, strategies[0])
        return [
            StrategyAuditRow(
                strategy=strategies[0],
                scenarios=len(scenarios),
                trades=42,
                win_rate=0.5,
                profit_factor=1.2,
                total_pnl=10.0,
                mean_trade_pnl=0.2381,
                annualized_sharpe=1.0,
                ci_low=0.01,
                ci_high=0.5,
                p_positive=0.04,
                zero_cost_total_pnl=12.0,
                cost_drag=2.0,
                verdict="positive-edge",
            )
        ]

    monkeypatch.setattr(
        replay_cli,
        "portfolio_basket_pooled_trades",
        fake_basket_pooled_trades,
    )
    monkeypatch.setattr(replay_cli, "run_audit", fake_run_audit)
    out_md = tmp_path / "basket.md"
    out_jsonl = tmp_path / "basket.jsonl"

    rc = main([
        "portfolio-basket-audit",
        "--scenario-dir", str(scenario_dir),
        "--strategy", "bull_flag",
        "--strategy", "orb",
        "--slippage-bps", "2",
        "--max-open-positions", "4",
        "--starting-equity", "12345",
        "--confidence-scale", "orb=0.25",
        "--output", str(out_md),
        "--jsonl", str(out_jsonl),
    ])

    assert rc == 0
    assert captured["basket"] == ("bull_flag", "orb")
    assert captured["strategy_equity_scales"] == {"orb": 0.25}
    assert captured["audit_strategies"] == ["bull_flag+orb"]
    assert captured["max_open_positions"] == 4
    assert captured["starting_equity"] == [12345.0, 12345.0]
    text = out_md.read_text()
    assert "Basket: `bull_flag+orb`." in text
    assert "Confidence sizing scales: `orb=0.25`." in text
    assert "## K=4" in text
    assert "| bull_flag | 1 | 1 | 0 | 1.00 | 1.0000 |" in text
    assert "| orb | 1 | 0 | 1 | -1.00 | -1.0000 |" in text
    assert "positive-edge" in text
    [payload] = [json.loads(line) for line in out_jsonl.read_text().splitlines()]
    assert payload["max_open_positions"] == 4
    assert payload["rows"][0]["strategy"] == "bull_flag+orb"
    assert payload["trade_diagnostics"]["strategies"] == [
        {
            "strategy": "bull_flag",
            "trades": 1,
            "winning_trades": 1,
            "losing_trades": 0,
            "total_pnl": 1.0,
            "mean_trade_pnl": 1.0,
            "ci_low": None,
            "ci_high": None,
            "p_mean_le_zero": None,
            "verdict": "insufficient-data",
        },
        {
            "strategy": "orb",
            "trades": 1,
            "winning_trades": 0,
            "losing_trades": 1,
            "total_pnl": -1.0,
            "mean_trade_pnl": -1.0,
            "ci_low": None,
            "ci_high": None,
            "p_mean_le_zero": None,
            "verdict": "insufficient-data",
        },
    ]


def test_portfolio_basket_audit_supports_option_snapshot_ledger(
    tmp_path,
    monkeypatch,
) -> None:
    from datetime import date, datetime, timezone

    from alpaca_bot.domain.models import OptionContract
    from alpaca_bot.replay import cli as replay_cli
    from alpaca_bot.replay.audit import StrategyAuditRow
    from alpaca_bot.replay.cli import main
    from alpaca_bot.replay.option_snapshots import append_option_chain_snapshot

    _set_audit_env(monkeypatch)
    scenario_dir = tmp_path / "scenarios"
    scenario_dir.mkdir()
    payload = json.loads(_GOLDEN_SCENARIO.read_text())
    for symbol in ("AAA", "BBB"):
        scenario = dict(payload)
        scenario["name"] = symbol
        scenario["symbol"] = symbol
        for key in ("daily_bars", "intraday_bars"):
            scenario[key] = [dict(bar, symbol=symbol) for bar in payload[key]]
        (scenario_dir / f"{symbol}.json").write_text(json.dumps(scenario))
    snapshot_dir = tmp_path / "snapshots"
    append_option_chain_snapshot(
        snapshot_dir=snapshot_dir,
        cycle_at=datetime(2026, 1, 2, 15, 0, tzinfo=timezone.utc),
        chains_by_symbol={
            "AAA": [
                OptionContract(
                    occ_symbol="AAA260717P00100000",
                    underlying="AAA",
                    option_type="put",
                    strike=100.0,
                    expiry=date(2026, 7, 17),
                    bid=1.0,
                    ask=1.2,
                )
            ]
        },
    )
    captured: dict[str, object] = {}

    def fake_basket_pooled_trades(
        scenarios,
        settings,
        strategy_names,
        *,
        strategy_equity_scales=None,
        option_chain_ledger=None,
        on_progress=None,
    ):
        del scenarios, settings, strategy_equity_scales, on_progress
        captured["basket"] = tuple(strategy_names)
        captured["ledger_snapshots"] = len(option_chain_ledger.snapshots)
        return []

    def fake_run_audit(*, scenarios, settings, strategies, pooled_trades_fn, **kwargs):
        pooled_trades_fn(scenarios, settings, strategies[0])
        return [
            StrategyAuditRow(
                strategy=strategies[0],
                scenarios=len(scenarios),
                trades=1,
                win_rate=1.0,
                profit_factor=None,
                total_pnl=1.0,
                mean_trade_pnl=1.0,
                annualized_sharpe=None,
                ci_low=None,
                ci_high=None,
                p_positive=None,
                zero_cost_total_pnl=1.0,
                cost_drag=0.0,
                verdict="insufficient-data",
            )
        ]

    monkeypatch.setattr(
        replay_cli,
        "portfolio_basket_pooled_trades",
        fake_basket_pooled_trades,
    )
    monkeypatch.setattr(replay_cli, "run_audit", fake_run_audit)
    out_md = tmp_path / "basket.md"

    rc = main([
        "portfolio-basket-audit",
        "--scenario-dir", str(scenario_dir),
        "--strategy", "bull_flag",
        "--strategy", "bear_orb",
        "--option-chain-snapshots", str(snapshot_dir),
        "--output", str(out_md),
    ])

    assert rc == 0
    assert captured == {
        "basket": ("bull_flag", "bear_orb"),
        "ledger_snapshots": 1,
    }
    assert "Option replay marks:" in out_md.read_text()


def test_portfolio_basket_audit_rejects_option_strategy_without_snapshots(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    from alpaca_bot.replay.cli import main

    _set_audit_env(monkeypatch)
    scenario_dir = tmp_path / "scenarios"
    scenario_dir.mkdir()

    rc = main([
        "portfolio-basket-audit",
        "--scenario-dir", str(scenario_dir),
        "--strategy", "bull_flag",
        "--strategy", "bear_orb",
    ])

    assert rc == 1
    assert "--option-chain-snapshots" in capsys.readouterr().err


def test_portfolio_basket_audit_rejects_single_strategy(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    from alpaca_bot.replay.cli import main

    _set_audit_env(monkeypatch)
    scenario_dir = tmp_path / "scenarios"
    scenario_dir.mkdir()

    rc = main([
        "portfolio-basket-audit",
        "--scenario-dir", str(scenario_dir),
        "--strategy", "bull_flag",
    ])

    assert rc == 1
    assert "at least two --strategy" in capsys.readouterr().err


def test_portfolio_basket_audit_rejects_unknown_confidence_scale_strategy(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    from alpaca_bot.replay.cli import main

    _set_audit_env(monkeypatch)
    scenario_dir = tmp_path / "scenarios"
    scenario_dir.mkdir()

    rc = main([
        "portfolio-basket-audit",
        "--scenario-dir", str(scenario_dir),
        "--strategy", "bull_flag",
        "--strategy", "orb",
        "--confidence-scale", "momentum=0.25",
    ])

    assert rc == 1
    assert "not in the basket" in capsys.readouterr().err


def test_audit_subcommand_resume_jsonl_skips_completed_strategy(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    import shutil

    from alpaca_bot.replay import cli as replay_cli
    from alpaca_bot.replay.audit import StrategyAuditRow
    from alpaca_bot.replay.cli import main

    _set_audit_env(monkeypatch)
    scenario_dir = tmp_path / "scenarios"
    scenario_dir.mkdir()
    shutil.copy(_GOLDEN_SCENARIO, scenario_dir / "a.json")
    shutil.copy(_GOLDEN_SCENARIO, scenario_dir / "b.json")

    completed = StrategyAuditRow(
        strategy="breakout",
        scenarios=2,
        trades=10,
        win_rate=0.5,
        profit_factor=1.0,
        total_pnl=1.0,
        mean_trade_pnl=0.1,
        annualized_sharpe=1.0,
        ci_low=-0.1,
        ci_high=0.3,
        p_positive=0.2,
        zero_cost_total_pnl=2.0,
        cost_drag=1.0,
        verdict="no-evidence",
    )
    jsonl_path = tmp_path / "audit.jsonl"
    checkpoint_payload = dataclasses.asdict(completed)
    checkpoint_payload["slippage_bps"] = 5.0
    jsonl_path.write_text(json.dumps(checkpoint_payload) + "\n")

    captured_strategies: list[list[str]] = []

    def fake_run_audit(*, strategies, on_row, **kwargs):
        captured_strategies.append(list(strategies))
        row = StrategyAuditRow(
            strategy="momentum",
            scenarios=2,
            trades=12,
            win_rate=0.6,
            profit_factor=1.2,
            total_pnl=3.0,
            mean_trade_pnl=0.25,
            annualized_sharpe=1.5,
            ci_low=0.01,
            ci_high=0.5,
            p_positive=0.04,
            zero_cost_total_pnl=4.0,
            cost_drag=1.0,
            verdict="positive-edge",
        )
        on_row(row)
        return [row]

    monkeypatch.setattr(replay_cli, "run_audit", fake_run_audit)
    out_json = tmp_path / "audit.json"
    out_md = tmp_path / "audit.md"

    rc = main([
        "audit",
        "--scenario-dir", str(scenario_dir),
        "--strategies", "breakout,momentum",
        "--slippage-bps", "5",
        "--jsonl", str(jsonl_path),
        "--resume-jsonl",
        "--json", str(out_json),
        "--output", str(out_md),
    ])

    assert rc == 0
    assert captured_strategies == [["momentum"]]
    assert "skipping breakout" in capsys.readouterr().err
    rows = json.loads(out_json.read_text())
    assert [row["strategy"] for row in rows] == ["breakout", "momentum"]
    jsonl_rows = [
        json.loads(line) for line in jsonl_path.read_text().splitlines() if line
    ]
    assert [row["strategy"] for row in jsonl_rows] == ["breakout", "momentum"]


def test_audit_subcommand_samples_scenarios_deterministically(
    tmp_path,
    monkeypatch,
) -> None:
    from alpaca_bot.replay import cli as replay_cli
    from alpaca_bot.replay.cli import main

    _set_audit_env(monkeypatch)
    scenario_dir = tmp_path / "scenarios"
    scenario_dir.mkdir()
    payload = json.loads(_GOLDEN_SCENARIO.read_text())
    for symbol in ("AAA", "BBB", "CCC", "DDD", "EEE"):
        scenario = dict(payload)
        scenario["name"] = symbol
        scenario["symbol"] = symbol
        for key in ("daily_bars", "intraday_bars"):
            scenario[key] = [dict(bar, symbol=symbol) for bar in payload[key]]
        (scenario_dir / f"{symbol}.json").write_text(json.dumps(scenario))

    captured: list[list[str]] = []

    def fake_run_audit(*, scenarios, **kwargs):
        captured.append([scenario.name for scenario in scenarios])
        return []

    monkeypatch.setattr(replay_cli, "run_audit", fake_run_audit)

    for _ in range(2):
        rc = main([
            "audit",
            "--scenario-dir", str(scenario_dir),
            "--strategies", "breakout",
            "--sample-size", "3",
            "--sample-seed", "proof",
        ])
        assert rc == 0

    assert len(captured) == 2
    assert captured[0] == captured[1]
    assert len(captured[0]) == 3
    assert captured[0] == sorted(captured[0])


def test_audit_subcommand_rejects_limit_with_sample(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    import shutil

    from alpaca_bot.replay.cli import main

    _set_audit_env(monkeypatch)
    scenario_dir = tmp_path / "scenarios"
    scenario_dir.mkdir()
    shutil.copy(_GOLDEN_SCENARIO, scenario_dir / "a.json")

    rc = main([
        "audit",
        "--scenario-dir", str(scenario_dir),
        "--limit", "1",
        "--sample-size", "1",
    ])

    assert rc == 1
    assert "--limit and --sample-size cannot be combined" in capsys.readouterr().err


def test_audit_subcommand_unknown_strategy_fails(tmp_path, monkeypatch):
    import shutil

    from alpaca_bot.replay.cli import main

    _set_audit_env(monkeypatch)
    scenario_dir = tmp_path / "scenarios"
    scenario_dir.mkdir()
    shutil.copy(_GOLDEN_SCENARIO, scenario_dir / "a.json")
    rc = main(["audit", "--scenario-dir", str(scenario_dir), "--strategies", "bogus"])
    assert rc == 1


def test_audit_subcommand_empty_dir_fails(tmp_path, monkeypatch):
    from alpaca_bot.replay.cli import main

    _set_audit_env(monkeypatch)
    empty = tmp_path / "none"
    empty.mkdir()
    rc = main(["audit", "--scenario-dir", str(empty)])
    assert rc == 1
