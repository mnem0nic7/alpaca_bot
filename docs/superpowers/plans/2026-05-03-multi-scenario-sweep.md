# Multi-Scenario Sweep Aggregation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `run_multi_scenario_sweep()` to the tuning module and wire it into the `alpaca-bot-evolve` CLI so parameter optimization can be validated across multiple symbols simultaneously, ranking combinations by worst-case (or average) performance across all scenarios.

**Architecture:** A new `run_multi_scenario_sweep()` function in `tuning/sweep.py` mirrors the existing `run_sweep()` but iterates each parameter combination across a list of scenarios, computing per-scenario scores and aggregating them (min or mean). A new `_aggregate_reports()` helper synthesizes a single `BacktestReport` from multiple reports for DB persistence. The `alpaca-bot-evolve` CLI gains `--scenario-dir`, `--strategy`, and `--aggregate` args; when `--scenario-dir` is used it calls `run_multi_scenario_sweep()` instead of `run_sweep()`.

**Tech Stack:** Python stdlib only (`itertools`, `dataclasses`). No new dependencies, no DB schema changes, no migrations.

---

## File Map

| Action | File | Responsibility |
|--------|------|----------------|
| Modify | `src/alpaca_bot/tuning/sweep.py` | Add `_aggregate_reports()` and `run_multi_scenario_sweep()` |
| Modify | `src/alpaca_bot/tuning/cli.py` | Add `--scenario-dir`, `--strategy`, `--aggregate`; call multi-sweep when appropriate |
| Modify | `tests/unit/test_tuning_sweep.py` | 4 new tests for the new sweep functions |
| Modify | `tests/unit/test_tuning_sweep_cli.py` | 2 new tests for the new CLI args |

---

## Task 1: Core sweep logic + tests

**Files:**
- Modify: `src/alpaca_bot/tuning/sweep.py`
- Test: `tests/unit/test_tuning_sweep.py`

### Step 1.1: Write the four failing tests

Append the following tests to the end of `tests/unit/test_tuning_sweep.py`:

```python
# ---------------------------------------------------------------------------
# run_multi_scenario_sweep
# ---------------------------------------------------------------------------

def test_run_multi_scenario_sweep_disqualifies_when_any_scenario_fails() -> None:
    """When one scenario produces no trades, all combos are disqualified."""
    golden = _make_golden_scenario()
    quiet = _make_quiet_scenario()
    small_grid: ParameterGrid = {
        "BREAKOUT_LOOKBACK_BARS": ["20"],
        "RELATIVE_VOLUME_THRESHOLD": ["1.5"],
        "DAILY_SMA_PERIOD": ["20"],
    }
    candidates = run_multi_scenario_sweep(
        scenarios=[golden, quiet],
        base_env=_base_env(),
        grid=small_grid,
        min_trades_per_scenario=1,
    )
    assert len(candidates) == 1
    assert candidates[0].score is None


def test_run_multi_scenario_sweep_min_aggregate_uses_worst_case(monkeypatch) -> None:
    """aggregate='min' returns the lowest per-scenario score."""
    import alpaca_bot.tuning.sweep as sweep_module

    quiet = _make_quiet_scenario()
    call_results = [2.0, 0.5]
    call_idx = [0]

    def fake_score(report, *, min_trades):
        result = call_results[call_idx[0] % len(call_results)]
        call_idx[0] += 1
        return result

    monkeypatch.setattr(sweep_module, "score_report", fake_score)

    small_grid: ParameterGrid = {
        "BREAKOUT_LOOKBACK_BARS": ["20"],
        "RELATIVE_VOLUME_THRESHOLD": ["1.5"],
        "DAILY_SMA_PERIOD": ["20"],
    }
    candidates = run_multi_scenario_sweep(
        scenarios=[quiet, quiet],
        base_env=_base_env(),
        grid=small_grid,
        min_trades_per_scenario=1,
        aggregate="min",
    )
    assert len(candidates) == 1
    assert candidates[0].score == pytest.approx(0.5)


def test_run_multi_scenario_sweep_mean_aggregate_averages_scores(monkeypatch) -> None:
    """aggregate='mean' returns the average of per-scenario scores."""
    import alpaca_bot.tuning.sweep as sweep_module

    quiet = _make_quiet_scenario()
    call_results = [2.0, 0.5]
    call_idx = [0]

    def fake_score(report, *, min_trades):
        result = call_results[call_idx[0] % len(call_results)]
        call_idx[0] += 1
        return result

    monkeypatch.setattr(sweep_module, "score_report", fake_score)

    small_grid: ParameterGrid = {
        "BREAKOUT_LOOKBACK_BARS": ["20"],
        "RELATIVE_VOLUME_THRESHOLD": ["1.5"],
        "DAILY_SMA_PERIOD": ["20"],
    }
    candidates = run_multi_scenario_sweep(
        scenarios=[quiet, quiet],
        base_env=_base_env(),
        grid=small_grid,
        min_trades_per_scenario=1,
        aggregate="mean",
    )
    assert len(candidates) == 1
    assert candidates[0].score == pytest.approx(1.25)  # (2.0 + 0.5) / 2


def test_run_multi_scenario_sweep_aggregated_report_sums_trades() -> None:
    """Aggregated report total_trades equals the sum of all per-scenario trades."""
    from alpaca_bot.domain.models import ReplayScenario

    golden = _make_golden_scenario()
    # Two identical scenarios (same data, different name) each produce 1 trade.
    golden2 = ReplayScenario(
        name="golden2",
        symbol=golden.symbol,
        starting_equity=golden.starting_equity,
        daily_bars=golden.daily_bars,
        intraday_bars=golden.intraday_bars,
    )
    small_grid: ParameterGrid = {
        "BREAKOUT_LOOKBACK_BARS": ["20"],
        "RELATIVE_VOLUME_THRESHOLD": ["1.5"],
        "DAILY_SMA_PERIOD": ["20"],
    }
    candidates = run_multi_scenario_sweep(
        scenarios=[golden, golden2],
        base_env=_base_env(),
        grid=small_grid,
        min_trades_per_scenario=1,
    )
    assert len(candidates) == 1
    assert candidates[0].report is not None
    assert candidates[0].report.total_trades == 2
```

Also add the import at the top of the imports section (after `from alpaca_bot.tuning.sweep import ...`):

The existing import line is:
```python
from alpaca_bot.tuning.sweep import (
    DEFAULT_GRID,
    ParameterGrid,
    TuningCandidate,
    run_sweep,
    score_report,
)
```

Replace it with:
```python
from alpaca_bot.tuning.sweep import (
    DEFAULT_GRID,
    ParameterGrid,
    TuningCandidate,
    run_multi_scenario_sweep,
    run_sweep,
    score_report,
)
```

- [ ] **Step 1.1: Write the failing tests** (append the 4 test functions above to `tests/unit/test_tuning_sweep.py`; update the import line to add `run_multi_scenario_sweep`)

- [ ] **Step 1.2: Run to verify all four fail**

```bash
pytest tests/unit/test_tuning_sweep.py::test_run_multi_scenario_sweep_disqualifies_when_any_scenario_fails \
       tests/unit/test_tuning_sweep.py::test_run_multi_scenario_sweep_min_aggregate_uses_worst_case \
       tests/unit/test_tuning_sweep.py::test_run_multi_scenario_sweep_mean_aggregate_averages_scores \
       tests/unit/test_tuning_sweep.py::test_run_multi_scenario_sweep_aggregated_report_sums_trades \
       -v
```

Expected: 4 × `ImportError: cannot import name 'run_multi_scenario_sweep'`

- [ ] **Step 1.3: Implement `_aggregate_reports()` and `run_multi_scenario_sweep()` in `sweep.py`**

Add the following to `src/alpaca_bot/tuning/sweep.py`. Place `_aggregate_reports` immediately before `run_sweep`, and `run_multi_scenario_sweep` immediately after `run_sweep`.

First, update the imports at the top of `sweep.py`. The current file begins:

```python
from __future__ import annotations

import itertools
import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING

from alpaca_bot.config import Settings
from alpaca_bot.domain.models import ReplayScenario
from alpaca_bot.replay.report import BacktestReport
from alpaca_bot.replay.runner import ReplayRunner

if TYPE_CHECKING:
    from alpaca_bot.strategy import StrategySignalEvaluator
```

No import changes needed — `BacktestReport`, `ReplayScenario`, `ReplayRunner`, `Settings`, and `itertools` are already imported.

Add `_aggregate_reports` between the `score_report` function and `run_sweep`:

```python
def _aggregate_reports(reports: list[BacktestReport | None]) -> BacktestReport | None:
    """Combine per-scenario reports into one synthetic report for DB storage."""
    valid = [r for r in reports if r is not None]
    if not valid:
        return None
    total_trades = sum(r.total_trades for r in valid)
    winning_trades = sum(r.winning_trades for r in valid)
    losing_trades = sum(r.losing_trades for r in valid)
    win_rate: float | None = winning_trades / total_trades if total_trades > 0 else None
    mean_rets = [r.mean_return_pct for r in valid if r.mean_return_pct is not None]
    mean_return_pct: float | None = sum(mean_rets) / len(mean_rets) if mean_rets else None
    drawdowns = [r.max_drawdown_pct for r in valid if r.max_drawdown_pct is not None]
    max_drawdown_pct: float | None = max(drawdowns) if drawdowns else None
    sharpes = [r.sharpe_ratio for r in valid if r.sharpe_ratio is not None]
    sharpe_ratio: float | None = sum(sharpes) / len(sharpes) if sharpes else None
    return BacktestReport(
        trades=(),
        total_trades=total_trades,
        winning_trades=winning_trades,
        losing_trades=losing_trades,
        win_rate=win_rate,
        mean_return_pct=mean_return_pct,
        max_drawdown_pct=max_drawdown_pct,
        sharpe_ratio=sharpe_ratio,
        strategy_name="aggregate",
    )
```

Add `run_multi_scenario_sweep` after the existing `run_sweep` function:

```python
def run_multi_scenario_sweep(
    *,
    scenarios: list[ReplayScenario],
    base_env: dict[str, str],
    grid: ParameterGrid | None = None,
    min_trades_per_scenario: int = 2,
    aggregate: str = "min",
    signal_evaluator: "StrategySignalEvaluator | None" = None,
) -> list[TuningCandidate]:
    """Run a parameter grid sweep across multiple scenarios.

    Each combination is evaluated against every scenario. The final score is
    the aggregate (min or mean) of per-scenario scores. A combination is
    disqualified (score=None) if ANY scenario yields fewer than
    min_trades_per_scenario trades.
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
            continue

        per_scenario_reports: list[BacktestReport | None] = []
        per_scenario_scores: list[float | None] = []
        runner = ReplayRunner(settings, signal_evaluator=signal_evaluator)
        for scenario in scenarios:
            result = runner.run(scenario)
            report: BacktestReport | None = result.backtest_report  # type: ignore[assignment]
            s = score_report(report, min_trades=min_trades_per_scenario) if report is not None else None
            per_scenario_reports.append(report)
            per_scenario_scores.append(s)

        if any(s is None for s in per_scenario_scores):
            agg_score: float | None = None
        elif aggregate == "mean":
            scored = [s for s in per_scenario_scores if s is not None]
            agg_score = sum(scored) / len(scored)
        else:  # "min"
            scored = [s for s in per_scenario_scores if s is not None]
            agg_score = min(scored)

        agg_report = _aggregate_reports(per_scenario_reports)
        candidates.append(TuningCandidate(params=overrides, report=agg_report, score=agg_score))

    return sorted(
        candidates,
        key=lambda c: (c.score is not None, c.score or 0.0),
        reverse=True,
    )
```

- [ ] **Step 1.4: Run all sweep tests to verify they pass**

```bash
pytest tests/unit/test_tuning_sweep.py -v
```

Expected: all tests PASS (previously 9 tests; now 13 total)

- [ ] **Step 1.5: Commit**

```bash
git add src/alpaca_bot/tuning/sweep.py tests/unit/test_tuning_sweep.py
git commit -m "feat: add run_multi_scenario_sweep and _aggregate_reports to tuning"
```

---

## Task 2: CLI wiring + tests

**Files:**
- Modify: `src/alpaca_bot/tuning/cli.py`
- Test: `tests/unit/test_tuning_sweep_cli.py`

### Step 2.1: Write the two failing CLI tests

Append to the end of `tests/unit/test_tuning_sweep_cli.py`:

```python
def test_evolve_cli_scenario_dir_calls_multi_sweep(monkeypatch, tmp_path):
    """--scenario-dir with 2+ files calls run_multi_scenario_sweep, not run_sweep."""
    import json
    from alpaca_bot.tuning import cli as module

    _patch_env(monkeypatch)

    for name in ("SYM_A_252d.json", "SYM_B_252d.json"):
        (tmp_path / name).write_text(json.dumps({
            "name": name.replace(".json", ""), "symbol": "SYM", "starting_equity": 100000.0,
            "daily_bars": [], "intraday_bars": [],
        }))

    captured_multi: list[dict] = []
    captured_single: list[dict] = []

    monkeypatch.setattr(module, "run_multi_scenario_sweep", lambda **kw: captured_multi.append(kw) or [])
    monkeypatch.setattr(module, "run_sweep", lambda **kw: captured_single.append(kw) or [])
    monkeypatch.setattr(sys, "argv", ["evolve", "--scenario-dir", str(tmp_path), "--no-db"])

    try:
        module.main()
    except SystemExit:
        pass

    assert captured_multi, "run_multi_scenario_sweep was not called"
    assert not captured_single, "run_sweep should not be called when --scenario-dir is used"
    assert len(captured_multi[0]["scenarios"]) == 2


def test_evolve_cli_scenario_dir_requires_at_least_two_files(monkeypatch, tmp_path):
    """--scenario-dir with fewer than 2 JSON files exits with an error."""
    import json
    from alpaca_bot.tuning import cli as module

    _patch_env(monkeypatch)

    (tmp_path / "only_one.json").write_text(json.dumps({
        "name": "only_one", "symbol": "SYM", "starting_equity": 100000.0,
        "daily_bars": [], "intraday_bars": [],
    }))

    monkeypatch.setattr(sys, "argv", ["evolve", "--scenario-dir", str(tmp_path), "--no-db"])

    with pytest.raises(SystemExit):
        module.main()
```

- [ ] **Step 2.1: Append the two test functions above to `tests/unit/test_tuning_sweep_cli.py`**

- [ ] **Step 2.2: Run to verify both fail**

```bash
pytest tests/unit/test_tuning_sweep_cli.py::test_evolve_cli_scenario_dir_calls_multi_sweep \
       tests/unit/test_tuning_sweep_cli.py::test_evolve_cli_scenario_dir_requires_at_least_two_files \
       -v
```

Expected: 2 × FAIL (AttributeError: module has no attribute 'run_multi_scenario_sweep', or SystemExit not raised)

- [ ] **Step 2.3: Replace `src/alpaca_bot/tuning/cli.py` with the updated version**

```python
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from alpaca_bot.config import Settings
from alpaca_bot.replay.runner import ReplayRunner
from alpaca_bot.strategy import STRATEGY_REGISTRY
from alpaca_bot.tuning.sweep import (
    DEFAULT_GRID,
    ParameterGrid,
    TuningCandidate,
    run_multi_scenario_sweep,
    run_sweep,
)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="alpaca-bot-evolve")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--scenario", metavar="FILE",
                       help="Replay scenario (JSON or YAML)")
    group.add_argument("--scenario-dir", metavar="DIR",
                       help="Directory of *.json scenario files (multi-scenario sweep)")
    parser.add_argument("--params-grid", metavar="FILE",
                        help="Parameter grid (JSON/YAML); defaults to built-in grid")
    parser.add_argument("--output-env", metavar="FILE",
                        help="Write winning env block to FILE")
    parser.add_argument("--min-trades", type=int, default=3,
                        help="Minimum trades required to score a candidate (default: 3)")
    parser.add_argument("--strategy", default="breakout",
                        choices=list(STRATEGY_REGISTRY),
                        help="Strategy to sweep (default: breakout)")
    parser.add_argument("--aggregate", default="min", choices=["min", "mean"],
                        help="Score aggregation across scenarios: min (default) or mean")
    parser.add_argument("--no-db", action="store_true",
                        help="Skip DB persistence (just print results)")
    args = parser.parse_args(list(argv) if argv is not None else sys.argv[1:])

    grid: ParameterGrid = DEFAULT_GRID
    if args.params_grid:
        grid = _load_grid(args.params_grid)

    signal_evaluator = STRATEGY_REGISTRY[args.strategy]
    base_env = dict(os.environ)
    now = datetime.now(timezone.utc)

    if args.scenario:
        scenario = ReplayRunner.load_scenario(args.scenario)
        total_combos = 1
        for vals in grid.values():
            total_combos *= len(vals)
        print(f"Running sweep: {total_combos} combinations over scenario '{scenario.name}'...")
        candidates = run_sweep(
            scenario=scenario,
            base_env=base_env,
            grid=grid,
            min_trades=args.min_trades,
            signal_evaluator=signal_evaluator,
        )
        scenario_name = scenario.name
    else:
        scenario_dir = Path(args.scenario_dir)
        files = sorted(scenario_dir.glob("*.json"))
        if len(files) < 2:
            sys.exit(
                f"--scenario-dir requires at least 2 *.json files; "
                f"found {len(files)} in {scenario_dir}"
            )
        scenarios = [ReplayRunner.load_scenario(f) for f in files]
        total_combos = 1
        for vals in grid.values():
            total_combos *= len(vals)
        names = ", ".join(s.name for s in scenarios)
        print(
            f"Running multi-scenario sweep: {total_combos} combinations "
            f"× {len(scenarios)} scenarios"
        )
        print(f"Scenarios: {names}")
        candidates = run_multi_scenario_sweep(
            scenarios=scenarios,
            base_env=base_env,
            grid=grid,
            min_trades_per_scenario=args.min_trades,
            aggregate=args.aggregate,
            signal_evaluator=signal_evaluator,
        )
        scenario_name = "+".join(s.name for s in scenarios)

    scored = [c for c in candidates if c.score is not None]
    unscored = [c for c in candidates if c.score is None]
    print(
        f"Scored: {len(scored)} / {len(candidates)} candidates "
        f"({len(unscored)} disqualified, min_trades={args.min_trades})"
    )

    best = scored[0] if scored else None

    _print_top_candidates(scored[:10])

    if best is None:
        print("\nNo scored candidates — increase --min-trades or provide longer scenarios.")
        return 1

    env_block = _format_env_block(best, now)
    print(f"\n{env_block}")

    if args.output_env:
        Path(args.output_env).write_text(env_block + "\n")
        print(f"Winning env block written to {args.output_env}")

    if not args.no_db:
        settings = Settings.from_env()
        _save_to_db(
            settings=settings,
            candidates=candidates,
            scenario_name=scenario_name,
            now=now,
        )

    return 0


def _load_grid(path: str) -> ParameterGrid:
    p = Path(path)
    text = p.read_text()
    if p.suffix in (".yaml", ".yml"):
        import yaml
        raw = yaml.safe_load(text)
    else:
        raw = json.loads(text)
    return {k: [str(v) for v in vals] for k, vals in raw.items()}


def _print_top_candidates(scored: list[TuningCandidate]) -> None:
    if not scored:
        return
    print("\nTop candidates:")
    for i, c in enumerate(scored, 1):
        report = c.report
        trades = report.total_trades if report else 0
        win = f"{report.win_rate:.0%}" if (report and report.win_rate is not None) else "—"
        sharpe = f"{c.score:.4f}" if c.score is not None else "—"
        params_str = " ".join(f"{k}={v}" for k, v in c.params.items())
        print(f"  [{i:2d}] score={sharpe:>8s}  trades={trades:2d}  win={win:>5s}  {params_str}")


def _format_env_block(best: TuningCandidate, now: datetime) -> str:
    report = best.report
    trades = report.total_trades if report else 0
    win = f"{report.win_rate:.0%}" if (report and report.win_rate is not None) else "—"
    score_str = f"{best.score:.4f}" if best.score is not None else "—"
    lines = [
        f"# Best params from tuning run {now.strftime('%Y-%m-%dT%H:%M:%SZ')}",
        f"# Score={score_str}  Trades={trades}  WinRate={win}",
    ]
    lines += [f"{k}={v}" for k, v in best.params.items()]
    return "\n".join(lines)


def _save_to_db(
    *,
    settings: Settings,
    candidates: list[TuningCandidate],
    scenario_name: str,
    now: datetime,
) -> None:
    from alpaca_bot.storage.db import connect_postgres
    from alpaca_bot.storage.repositories import TuningResultStore
    conn = connect_postgres(settings.database_url)
    try:
        store = TuningResultStore(conn)
        run_id = store.save_run(
            scenario_name=scenario_name,
            trading_mode=settings.trading_mode.value,
            candidates=candidates,
            created_at=now,
        )
        print(f"Results saved to DB (run_id={run_id})")
    finally:
        close = getattr(conn, "close", None)
        if callable(close):
            close()
```

- [ ] **Step 2.4: Run all CLI tests to verify they pass**

```bash
pytest tests/unit/test_tuning_sweep_cli.py -v
```

Expected: all 4 tests PASS

- [ ] **Step 2.5: Commit**

```bash
git add src/alpaca_bot/tuning/cli.py tests/unit/test_tuning_sweep_cli.py
git commit -m "feat: add --scenario-dir, --strategy, --aggregate to alpaca-bot-evolve CLI"
```

---

## Task 3: Full regression run

**Files:** none

- [ ] **Step 3.1: Run the full test suite**

```bash
pytest --tb=short -q
```

Expected: all previously passing tests still pass; total count increases by 6 (4 sweep tests + 2 CLI tests)

- [ ] **Step 3.2: Verify the new entry points are importable**

```bash
python -c "from alpaca_bot.tuning.sweep import run_multi_scenario_sweep; print('ok')"
python -c "from alpaca_bot.tuning.cli import main; print('ok')"
```

Expected: `ok` for both

- [ ] **Step 3.3: Commit if any fixes needed**

Only needed if Step 3.1 revealed regressions. Otherwise this task is complete with no commit.
