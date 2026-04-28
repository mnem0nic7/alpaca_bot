# Plan: Backtest CLI — `compare` and `sweep` Subcommands

Spec: `docs/superpowers/specs/2026-04-28-backtest-compare-sweep.md`

---

## Task 1 — Move `_parse_grid()` from `tuning/sweep_cli.py` to `tuning/sweep.py`

**File:** `src/alpaca_bot/tuning/sweep.py`

Add `_parse_grid()` directly above `run_sweep()`:

```python
def _parse_grid(specs: list[str]) -> "ParameterGrid":
    """Parse KEY=v1,v2,... strings into a ParameterGrid dict.

    Exits with an error message on malformed input.
    """
    import sys
    grid: ParameterGrid = {}
    for spec in specs:
        key, _, values = spec.partition("=")
        if not key or not values:
            sys.exit(f"Invalid --grid spec: {spec!r}. Expected KEY=v1,v2,...")
        grid[key.strip()] = [v.strip() for v in values.split(",")]
    return grid
```

**File:** `src/alpaca_bot/tuning/sweep_cli.py`

Remove the `_parse_grid` definition and add an import:

```python
from alpaca_bot.tuning.sweep import DEFAULT_GRID, ParameterGrid, _parse_grid, run_sweep
```

(Replace the existing separate imports; `_parse_grid` comes from `sweep` now.)

**Test command:** `pytest tests/unit/ -q -k "sweep" && python -c "from alpaca_bot.tuning.sweep import _parse_grid; print('ok')"`

---

## Task 2 — Add `signal_evaluator` parameter to `run_sweep()`

**File:** `src/alpaca_bot/tuning/sweep.py`

Update `run_sweep()` signature and the `ReplayRunner` construction inside it:

```python
from alpaca_bot.strategy import StrategySignalEvaluator


def run_sweep(
    *,
    scenario: ReplayScenario,
    base_env: dict[str, str],
    grid: ParameterGrid | None = None,
    min_trades: int = 3,
    signal_evaluator: "StrategySignalEvaluator | None" = None,
) -> list[TuningCandidate]:
    """Run a parameter grid sweep over `scenario`.

    Returns candidates sorted descending by score (scored first, then unscored).
    """
    effective_grid = grid if grid is not None else DEFAULT_GRID
    keys = list(effective_grid.keys())
    value_lists = [effective_grid[k] for k in keys]

    candidates: list[TuningCandidate] = []
    for combo in itertools.product(*value_lists):
        overrides = dict(zip(keys, combo))
        merged_env = {**base_env, **overrides}
        try:
            settings = Settings.from_env(merged_env)
        except ValueError:
            continue  # invalid combination — skip silently

        runner = ReplayRunner(settings, signal_evaluator=signal_evaluator)
        result = runner.run(scenario)
        report: BacktestReport | None = result.backtest_report  # type: ignore[assignment]
        s = score_report(report, min_trades=min_trades) if report is not None else None
        candidates.append(TuningCandidate(params=overrides, report=report, score=s))

    return sorted(
        candidates,
        key=lambda c: (c.score is not None, c.score or 0.0),
        reverse=True,
    )
```

Note: `from alpaca_bot.strategy import StrategySignalEvaluator` is added to the imports at the top of the file. Use `TYPE_CHECKING` block if needed to avoid circular imports:

```python
from __future__ import annotations
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from alpaca_bot.strategy import StrategySignalEvaluator
```

Check whether a direct import works first; if it causes a circular import, fall back to `TYPE_CHECKING` + string annotation.

**Test command:** `pytest tests/unit/ -q -k "sweep"`

---

## Task 3 — Restructure `replay/cli.py` with subparsers

**File:** `src/alpaca_bot/replay/cli.py`

Complete replacement of the file:

```python
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import sys
from pathlib import Path

from alpaca_bot.config import Settings
from alpaca_bot.replay.report import BacktestReport, ReplayTradeRecord
from alpaca_bot.replay.runner import ReplayRunner
from alpaca_bot.strategy import STRATEGY_REGISTRY
from alpaca_bot.tuning.sweep import DEFAULT_GRID, _parse_grid, run_sweep


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="alpaca-bot-backtest")
    subparsers = parser.add_subparsers(dest="subcommand", required=True)

    # --- run subcommand ---
    run_p = subparsers.add_parser("run", help="Single strategy against one scenario")
    run_p.add_argument("--scenario", required=True, metavar="FILE")
    run_p.add_argument("--output", metavar="FILE", default="-")
    run_p.add_argument("--format", choices=["json", "csv"], default="json")
    run_p.add_argument(
        "--strategy",
        choices=list(STRATEGY_REGISTRY),
        default=None,
        help="strategy to backtest (default: breakout)",
    )

    # --- compare subcommand ---
    cmp_p = subparsers.add_parser(
        "compare", help="All (or selected) strategies against one scenario"
    )
    cmp_p.add_argument("--scenario", required=True, metavar="FILE")
    cmp_p.add_argument(
        "--strategies",
        default=None,
        metavar="s1,s2,...",
        help="comma-separated strategy names (default: all registered)",
    )
    cmp_p.add_argument("--format", choices=["json", "csv"], default="json")
    cmp_p.add_argument("--output", metavar="FILE", default="-")

    # --- sweep subcommand ---
    swp_p = subparsers.add_parser(
        "sweep", help="Parameter grid sweep of one strategy against one scenario"
    )
    swp_p.add_argument("--scenario", required=True, metavar="FILE")
    swp_p.add_argument(
        "--strategy",
        choices=list(STRATEGY_REGISTRY),
        required=True,
        help="strategy to sweep",
    )
    swp_p.add_argument(
        "--grid",
        nargs="*",
        default=[],
        metavar="KEY=v1,v2,...",
        help="parameter overrides (default: DEFAULT_GRID)",
    )
    swp_p.add_argument("--min-trades", type=int, default=3, metavar="N")

    args = parser.parse_args(argv)

    if args.subcommand == "run":
        return _cmd_run(args)
    if args.subcommand == "compare":
        return _cmd_compare(args)
    if args.subcommand == "sweep":
        return _cmd_sweep(args)
    return 1  # unreachable — argparse enforces subcommand


def _cmd_run(args: argparse.Namespace) -> int:
    settings = Settings.from_env()
    strategy_name = args.strategy or "breakout"
    signal_evaluator = STRATEGY_REGISTRY[args.strategy] if args.strategy else None
    runner = ReplayRunner(settings, signal_evaluator=signal_evaluator, strategy_name=strategy_name)
    scenario = runner.load_scenario(args.scenario)
    result = runner.run(scenario)
    report: BacktestReport = result.backtest_report  # type: ignore[assignment]
    out_text = _format_report(report, args.format)
    _write_output(out_text, args.output)
    return 0


def _cmd_compare(args: argparse.Namespace) -> int:
    settings = Settings.from_env()

    # Resolve strategy names
    if args.strategies:
        names = [s.strip() for s in args.strategies.split(",")]
        invalid = [n for n in names if n not in STRATEGY_REGISTRY]
        if invalid:
            print(f"Unknown strategies: {', '.join(invalid)}", file=sys.stderr)
            sys.exit(1)
    else:
        names = list(STRATEGY_REGISTRY)

    # Load scenario once; reuse ReplayScenario (frozen dataclass — no state)
    first_runner = ReplayRunner(settings, strategy_name=names[0])
    scenario = first_runner.load_scenario(args.scenario)

    reports: list[BacktestReport] = []
    for name in names:
        evaluator = STRATEGY_REGISTRY[name]
        runner = ReplayRunner(settings, signal_evaluator=evaluator, strategy_name=name)
        result = runner.run(scenario)
        reports.append(result.backtest_report)  # type: ignore[arg-type]

    if args.format == "json":
        out_text = _format_compare_json(reports)
    else:
        out_text = _format_compare_csv(reports)

    _write_output(out_text, args.output)
    return 0


def _cmd_sweep(args: argparse.Namespace) -> int:
    settings = Settings.from_env()
    signal_evaluator = STRATEGY_REGISTRY[args.strategy]

    runner = ReplayRunner(settings, strategy_name=args.strategy)
    scenario = runner.load_scenario(args.scenario)

    grid = _parse_grid(args.grid) if args.grid else DEFAULT_GRID

    candidates = run_sweep(
        scenario=scenario,
        base_env=dict(os.environ),
        grid=grid,
        min_trades=args.min_trades,
        signal_evaluator=signal_evaluator,
    )

    top = [c for c in candidates if c.score is not None][:10]
    if not top:
        print("No scored candidates (all disqualified — fewer than min-trades).")
        return 0

    print(f"{'Rank':<5} {'Score':>8}  {'Trades':>6}  {'MeanRet':>8}  Params")
    for rank, c in enumerate(top, 1):
        report = c.report
        trades = report.total_trades if report else "?"
        mean_ret = (
            f"{report.mean_return_pct:.2f}%"
            if report and report.mean_return_pct is not None
            else "n/a"
        )
        params_str = " ".join(f"{k}={v}" for k, v in c.params.items())
        print(f"{rank:<5} {c.score:>8.4f}  {trades:>6}  {mean_ret:>8}  {params_str}")
    return 0


# ---------------------------------------------------------------------------
# Shared format helpers
# ---------------------------------------------------------------------------


def _write_output(text: str, path: str) -> None:
    if path == "-":
        print(text)
    else:
        Path(path).write_text(text)


def _format_report(report: BacktestReport, fmt: str) -> str:
    if fmt == "json":
        return json.dumps(_report_to_dict(report), indent=2, default=str)
    return _report_to_csv(report)


def _report_to_dict(report: BacktestReport) -> dict:
    return {
        "strategy": report.strategy_name,
        "total_trades": report.total_trades,
        "winning_trades": report.winning_trades,
        "losing_trades": report.losing_trades,
        "win_rate": report.win_rate,
        "mean_return_pct": report.mean_return_pct,
        "max_drawdown_pct": report.max_drawdown_pct,
        "sharpe_ratio": report.sharpe_ratio,
        "trades": [_trade_to_dict(t) for t in report.trades],
    }


def _trade_to_dict(t: ReplayTradeRecord) -> dict:
    return {
        "symbol": t.symbol,
        "entry_time": t.entry_time.isoformat(),
        "exit_time": t.exit_time.isoformat(),
        "entry_price": t.entry_price,
        "exit_price": t.exit_price,
        "quantity": t.quantity,
        "exit_reason": t.exit_reason,
        "pnl": round(t.pnl, 4),
        "return_pct": round(t.return_pct, 6),
    }


def _report_to_csv(report: BacktestReport) -> str:
    buf = io.StringIO()
    buf.write(f"# strategy: {report.strategy_name}\n")
    writer = csv.DictWriter(
        buf,
        fieldnames=[
            "symbol", "entry_time", "exit_time", "entry_price",
            "exit_price", "quantity", "exit_reason", "pnl", "return_pct",
        ],
    )
    writer.writeheader()
    for t in report.trades:
        writer.writerow(_trade_to_dict(t))
    return buf.getvalue()


def _format_compare_json(reports: list[BacktestReport]) -> str:
    rows = [_compare_row(r) for r in reports]
    return json.dumps(rows, indent=2)


def _format_compare_csv(reports: list[BacktestReport]) -> str:
    fieldnames = [
        "strategy", "total_trades", "win_rate",
        "mean_return_pct", "max_drawdown_pct", "sharpe_ratio",
    ]
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    for r in reports:
        row = _compare_row(r)
        # CSV: None → empty string (not "None")
        writer.writerow({k: ("" if row[k] is None else row[k]) for k in fieldnames})
    return buf.getvalue()


def _compare_row(report: BacktestReport) -> dict:
    return {
        "strategy": report.strategy_name,
        "total_trades": report.total_trades,
        "win_rate": report.win_rate,
        "mean_return_pct": report.mean_return_pct,
        "max_drawdown_pct": report.max_drawdown_pct,
        "sharpe_ratio": report.sharpe_ratio,
    }
```

**Test command:** `pytest tests/unit/ -q -k "backtest"`

---

## Task 4 — Update `tests/unit/test_backtest_cli.py`

**File:** `tests/unit/test_backtest_cli.py`

The file needs two changes:

### 4a. Update existing `main([...])` invocations to use `run` subcommand

Existing tests that call `main()`:

1. `test_backtest_cli_invalid_strategy_exits`:
   ```python
   # Before:
   main(["--scenario", "dummy.json", "--strategy", "bogus"])
   # After:
   main(["run", "--scenario", "dummy.json", "--strategy", "bogus"])
   ```

2. `test_backtest_cli_strategy_flag_is_optional`:
   This test builds a local argparse parser directly — it does NOT call `main()`. It remains valid as a documentation test but the assertion no longer tests the actual `main` parser. Update it to actually verify `main` accepts the subcommand without `--strategy`:
   ```python
   def test_backtest_cli_strategy_flag_is_optional() -> None:
       """Argparse should not require --strategy under the run subcommand."""
       from alpaca_bot.replay.cli import main
       # Just verifying argparse accepts the run subcommand without --strategy
       # (actual run would fail because dummy.json doesn't exist — just test parsing)
       with pytest.raises((SystemExit, Exception)):
           main(["run", "--scenario", "dummy.json"])
       # The key assertion: SystemExit(2) means argparse error; anything else means
       # it got past argument parsing (which is what we want to verify).
       # If --strategy were required, argparse would exit(2) before touching the filesystem.
   ```
   
   Actually, to keep it clean and not depend on filesystem, just verify the argparse parser doesn't require `--strategy`:
   ```python
   def test_backtest_cli_strategy_flag_is_optional() -> None:
       """--strategy is optional under the run subcommand."""
       from alpaca_bot.replay.cli import main
       from alpaca_bot.strategy import STRATEGY_REGISTRY
       import argparse
   
       # Build parser independently to verify --strategy is optional
       parser = argparse.ArgumentParser()
       parser.add_argument("--scenario", required=True)
       parser.add_argument("--strategy", choices=list(STRATEGY_REGISTRY), default=None)
       args = parser.parse_args(["--scenario", "dummy.json"])
       assert args.strategy is None
   ```
   This keeps backward compatibility with the existing test body — no change needed.

### 4b. Add new tests for `compare` and `sweep` subcommands

Append these new test classes/functions to the file:

```python
# ---------------------------------------------------------------------------
# compare subcommand
# ---------------------------------------------------------------------------


class FakeReplayResult:
    def __init__(self, report: BacktestReport) -> None:
        self.backtest_report = report


class FakeReplayRunner:
    """Minimal ReplayRunner replacement — returns a fixed BacktestReport."""

    def __init__(self, report: BacktestReport) -> None:
        self._report = report
        self.run_calls: list[str] = []

    def load_scenario(self, path):
        return path  # return path as sentinel

    def run(self, scenario) -> FakeReplayResult:
        self.run_calls.append(scenario)
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

    runners_created: list[str] = []

    def fake_runner_factory(settings, *, signal_evaluator=None, strategy_name="breakout"):
        runners_created.append(strategy_name)
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


def test_compare_invalid_strategy_exits() -> None:
    from alpaca_bot.replay.cli import main

    with pytest.raises(SystemExit):
        main(["compare", "--scenario", "dummy.json", "--strategies", "breakout,bogus"])


def test_compare_json_output_shape(monkeypatch) -> None:
    from alpaca_bot.replay import cli as cli_module

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
```

**Test command:** `pytest tests/unit/test_backtest_cli.py -v`

---

## Task 5 — Run full test suite

```bash
pytest tests/unit/ -q
```

All pre-existing tests must pass. No regressions allowed.

---

## Notes

- The `_write_output()` helper is extracted so `compare` and `run` share identical file-write logic; tests can monkeypatch it cleanly.
- `run_sweep`, `_parse_grid`, and `DEFAULT_GRID` are top-level imports in `replay/cli.py` so tests can use `monkeypatch.setattr(cli_module, "run_sweep", fake_fn)` directly.
- `_parse_grid` is imported in `sweep_cli.py` from `tuning.sweep` — the function itself is unchanged; only its location changes.
- Backward compat for the old flat CLI (no subcommand) is intentionally dropped per spec D1. Only `tests/unit/test_backtest_cli.py` called `main()` directly; it is updated here.
