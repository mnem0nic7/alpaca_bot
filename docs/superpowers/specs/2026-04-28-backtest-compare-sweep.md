# Spec: Backtest CLI — `compare` and `sweep` Subcommands

**File:** `docs/superpowers/specs/2026-04-28-backtest-compare-sweep.md`
**Date:** 2026-04-28
**Status:** Draft

---

## Motivation

`alpaca-bot-backtest` currently runs a single strategy against a single scenario file and prints a detailed trade-level report. Two operator workflows are missing:

1. **Strategy comparison.** Choosing which strategy to run on a given symbol requires running the CLI five times, capturing output, and diffing by hand. There is no way to produce a side-by-side summary row per strategy in one command.

2. **Single-scenario parameter sweep.** `alpaca-bot-sweep` sweeps a *directory* of scenario files across all combos in `DEFAULT_GRID`. `alpaca-bot-evolve` does the same with DB persistence. Neither accepts a single scenario file plus an ad-hoc parameter grid on the CLI, which is the natural workflow when iterating on a new scenario. The operator should not need to create a directory of one file just to use the sweep machinery.

Both workflows use only offline replay machinery — no broker calls, no Postgres writes in the base case.

---

## Non-Goals

- No new strategy implementation.
- No changes to `BacktestReport`, `ReplayRunner`, `run_sweep()`, `score_report()`, or any existing logic in `replay/` or `tuning/`. Those are consumed as-is.
- No DB persistence for `compare` output. Results are always stdout or a file.
- The `sweep` subcommand does **not** replace `alpaca-bot-sweep` or `alpaca-bot-evolve`. It is a focused single-scenario, no-DB slice of what those tools already do.
- No HTML or chart output.
- No parallel execution of strategies or sweep candidates (sequential is fine for an offline tool).
- No changes to scenario file format.
- `compare` does not attempt to identify a "winner" or make recommendations; it only presents data.

---

## Design Decisions

### D1: Subcommands in `replay/cli.py`, not new entry points

The existing `alpaca-bot-backtest` is a single-purpose offline tool. Both new modes are variations on the same replay-and-report loop. Adding `argparse` subparsers keeps all replay-oriented CLI logic in one file and one entry point, avoiding proliferation of nearly-identical scripts.

The existing flat-argument interface becomes the `run` subcommand. Backward compatibility for the no-subcommand invocation is **not** preserved. The old interface (`alpaca-bot-backtest --scenario FILE`) was added recently and has no downstream consumers outside of tests. The tests in `test_backtest_cli.py` all call `main(argv)` directly and will be updated to use `run` subcommand syntax.

Rationale for dropping compat: Argparse subparsers and flat positional dispatch do not compose cleanly. The clean break is cheap — only one test file calls `main()`.

### D2: `compare` output is a summary table, not a trade list

`run` outputs per-trade detail. `compare` outputs one row per strategy with summary metrics only: `strategy`, `total_trades`, `win_rate`, `mean_return_pct`, `max_drawdown_pct`, `sharpe_ratio`. The `trades` list is omitted from `compare` JSON output to keep it readable.

JSON output is an array of objects (one per strategy), naturally machine-parseable. CSV output is a flat table with one data row per strategy plus a header. Both formats use the same field set.

There is no new `CompareReport` datatype. The comparison is simply `list[BacktestReport]`. The formatting helpers `_compare_to_json()` and `_compare_to_csv()` are new private functions in `replay/cli.py`.

### D3: `sweep` subcommand wraps `run_sweep()` — single scenario, no DB

`run_sweep()` in `tuning/sweep.py` already accepts a single `ReplayScenario` plus a `ParameterGrid`. The `sweep` subcommand is a thin CLI wrapper: load the scenario file, parse the grid from `--grid` flags, call `run_sweep()`, and print ranked results.

`_parse_grid()` is moved from `tuning/sweep_cli.py` to `tuning/sweep.py` so both files can share it without a circular dependency. This is a pure refactor with no behaviour change.

`--grid` is optional. When omitted, `DEFAULT_GRID` is used.

No DB write. `--no-db` is not needed because there is no DB path.

### D4: `--strategies` filter uses comma-separated string

`--strategies breakout,momentum` is more readable in shell one-liners than `--strategy breakout --strategy momentum`. The value is split on commas and validated against `STRATEGY_REGISTRY` keys. An absent flag means "all registered strategies".

### D5: Settings for multi-strategy compare runs

Each strategy in `compare` uses the same `Settings.from_env()` instance. Strategy-specific parameters are in env and apply where relevant. The operator is responsible for env being correctly configured before running `compare`.

### D6: `run_sweep()` gains optional `signal_evaluator` parameter

To support sweeping non-breakout strategies, `run_sweep()` in `tuning/sweep.py` accepts an optional `signal_evaluator: StrategySignalEvaluator | None = None` and passes it to `ReplayRunner`. The existing call in `tuning/cli.py` passes no evaluator (default `None`) — no breakage.

---

## Interface Specification

### Restructured `alpaca-bot-backtest`

```
alpaca-bot-backtest <subcommand> [options]

Subcommands:
  run      Single strategy against one scenario (previous default behaviour)
  compare  All (or selected) strategies against one scenario, summary table
  sweep    Parameter grid sweep of one strategy against one scenario
```

### `run` subcommand (unchanged behaviour, new syntax)

```
alpaca-bot-backtest run --scenario FILE [--strategy NAME] [--format json|csv] [--output FILE]
```

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--scenario` | path | required | Scenario JSON or YAML file |
| `--strategy` | choice | `breakout` | Strategy name from `STRATEGY_REGISTRY` |
| `--format` | `json`\|`csv` | `json` | Output format |
| `--output` | path | `-` (stdout) | Output file; `-` for stdout |

### `compare` subcommand

```
alpaca-bot-backtest compare --scenario FILE [--strategies s1,s2,...] [--format json|csv] [--output FILE]
```

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--scenario` | path | required | Scenario file to run all strategies against |
| `--strategies` | comma list | all registered | Restrict to named subset, e.g. `breakout,momentum` |
| `--format` | `json`\|`csv` | `json` | Output format |
| `--output` | path | `-` (stdout) | Output file; `-` for stdout |

JSON output shape:

```json
[
  {
    "strategy": "breakout",
    "total_trades": 12,
    "win_rate": 0.583,
    "mean_return_pct": 0.0041,
    "max_drawdown_pct": 0.0087,
    "sharpe_ratio": 0.74
  }
]
```

`win_rate`, `mean_return_pct`, `max_drawdown_pct`, `sharpe_ratio` are `null` when not computable. The array is ordered by the `--strategies` argument if provided, or by `STRATEGY_REGISTRY` insertion order otherwise — no automatic ranking.

CSV output:

```
strategy,total_trades,win_rate,mean_return_pct,max_drawdown_pct,sharpe_ratio
breakout,12,0.583,0.0041,0.0087,0.74
momentum,7,0.571,0.0033,,
```

Empty cells for `None` values (not the string `"None"`).

### `sweep` subcommand

```
alpaca-bot-backtest sweep --scenario FILE --strategy NAME [--grid KEY=v1,v2,...] [--min-trades N]
```

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--scenario` | path | required | Scenario file |
| `--strategy` | choice | required | Strategy name (must be in `STRATEGY_REGISTRY`) |
| `--grid` | repeated | `DEFAULT_GRID` | Parameter overrides, e.g. `BREAKOUT_LOOKBACK_BARS=15,20,25` |
| `--min-trades` | int | `3` | Disqualify candidates with fewer trades |

Output is the same ranked-table format as `alpaca-bot-sweep` (human-readable text). No `--format` flag for `sweep` — use `alpaca-bot-evolve --no-db` for machine-parseable output.

---

## Data Flow

### `compare`

```
argv → argparse (subcommand=compare)
  → Settings.from_env()                        # one instance, shared
  → ReplayRunner.load_scenario(FILE)           # parsed once, reused
  → for each strategy_name in resolved_strategies:
      runner = ReplayRunner(settings, STRATEGY_REGISTRY[name], name)
      result = runner.run(scenario)
      report = result.backtest_report
  → reports: list[BacktestReport]
  → _format_compare(reports, fmt) → str
  → write to output or stdout
```

The `ReplayScenario` object is reused across all strategy runs. Each `ReplayRunner.run()` creates a fresh `ReplayState`, so there is no cross-contamination between strategies.

### `sweep`

```
argv → argparse (subcommand=sweep)
  → Settings.from_env()                        # base settings
  → ReplayRunner.load_scenario(FILE)           # ReplayScenario
  → _parse_grid(args.grid) or DEFAULT_GRID     # ParameterGrid
  → signal_evaluator = STRATEGY_REGISTRY[args.strategy]
  → run_sweep(scenario, base_env=os.environ, grid, min_trades,
               signal_evaluator=signal_evaluator)
  → top candidates (scored, sorted by score desc)
  → print ranked table
```

---

## Files Changed

| File | Change |
|------|--------|
| `src/alpaca_bot/replay/cli.py` | Restructure to argparse subparsers; `run` = current logic; add `compare` and `sweep` handlers; add `_format_compare_json()`, `_format_compare_csv()` |
| `src/alpaca_bot/tuning/sweep.py` | Move `_parse_grid()` here from `sweep_cli.py`; add `signal_evaluator: StrategySignalEvaluator | None = None` to `run_sweep()` |
| `src/alpaca_bot/tuning/sweep_cli.py` | Remove `_parse_grid()` definition; import from `tuning.sweep` |
| `tests/unit/test_backtest_cli.py` | Update all `main([...])` calls to `run` subcommand prefix; add tests for `compare` and `sweep` |

No changes to: `replay/runner.py`, `replay/report.py`, `tuning/cli.py`, `strategy/`, `config/`, `storage/`, `runtime/`, `web/`, `admin/`.

---

## Test Plan

### `run` subcommand (migration of existing tests)

- Rename all existing `main([...])` calls to `main(["run", ...])`.
- All existing assertions remain valid — only the argv prefix changes.

### `compare` subcommand

- `test_compare_default_runs_all_strategies` — output list has one entry per registered strategy.
- `test_compare_subset_strategies` — `--strategies breakout,momentum` produces exactly two rows.
- `test_compare_invalid_strategy_exits` — `--strategies breakout,bogus` raises `SystemExit`.
- `test_compare_json_output_shape` — each element has the 6 summary keys; no `trades` key.
- `test_compare_csv_output_has_header_and_rows` — CSV header matches expected fieldnames; row count equals strategy count.
- `test_compare_null_fields_json` — `None` fields serialize as JSON `null`.
- `test_compare_null_fields_csv` — `None` fields are empty string in CSV.
- `test_compare_output_to_file` — `--output /tmp/out.json` writes file.

### `sweep` subcommand

- `test_sweep_default_grid_runs` — exits 0, prints rank table.
- `test_sweep_custom_grid_single_param` — `--grid BREAKOUT_LOOKBACK_BARS=20,25` produces 2 candidates before min-trades filtering.
- `test_sweep_min_trades_filter` — zero-trade scenario yields no ranked output without crash.
- `test_sweep_non_breakout_strategy` — `--strategy momentum` does not crash; strategy flows to `ReplayRunner`.
- `test_parse_grid_importable_from_sweep_module` — `from alpaca_bot.tuning.sweep import _parse_grid` resolves.
