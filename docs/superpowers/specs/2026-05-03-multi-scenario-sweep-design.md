# Multi-Scenario Sweep Aggregation — Design Spec

## Problem

The existing `run_sweep()` function evaluates parameter combinations against a **single** replay scenario (one symbol, one time window). Parameters optimized on a single scenario risk overfitting: `BREAKOUT_LOOKBACK_BARS=20` may maximize Sharpe on AAPL's Q1 history while performing poorly on MSFT, TSLA, or a different market regime.

The `BackfillFetcher` already generates per-symbol scenario JSON files. The `TuningResultStore` already persists sweep results. The missing piece is aggregating scores across multiple scenarios so that a parameter set must prove itself on every scenario in the portfolio, not just one.

## Goal

Add `run_multi_scenario_sweep()` to `alpaca_bot/tuning/sweep.py` and wire it into the `alpaca-bot-evolve` CLI so operators can run a robust multi-symbol sweep with a single command.

## Architecture

### Core function: `run_multi_scenario_sweep()`

```
scenarios: list[ReplayScenario]     — two or more scenario objects
base_env: dict[str, str]            — current process env (from os.environ)
grid: ParameterGrid | None          — parameter value lists; None → DEFAULT_GRID
min_trades_per_scenario: int = 2    — per-scenario disqualification threshold
aggregate: str = "min"              — "min" or "mean" across per-scenario scores
signal_evaluator: ...               — injectable strategy (default: breakout)
→ list[TuningCandidate]             — sorted descending by aggregate score
```

For each parameter combination:

1. Build a modified `Settings` via `Settings.from_env(merged_env)`. Skip the combination on `ValueError`.
2. For each scenario, run `ReplayRunner(settings).run(scenario)` and call `score_report(report, min_trades=min_trades_per_scenario)`.
3. **Disqualification rule**: if ANY scenario returns `score=None` (too few trades), the combination's aggregate score is `None`. This prevents parameters from hiding poor performance on hard scenarios behind a great score on an easy one.
4. **Aggregation**: `min(scores)` (default) or `mean(scores)`. Min is the conservative choice for risk-managed trading — the worst case determines the ranking.
5. Store a synthetic `BacktestReport` with aggregated metrics (summed trades, averaged returns and win rate, worst-case max drawdown) so the result persists correctly in `TuningResultStore.save_run()`.
6. Sort: scored candidates first, descending; unscored last.

### Helper: `_aggregate_reports()`

Combines a list of `BacktestReport | None` into a single synthetic `BacktestReport`:
- `total_trades` = sum across valid reports
- `winning_trades`, `losing_trades` = sums
- `win_rate` = `winning_trades / total_trades` if `total_trades > 0` else `None`
- `mean_return_pct` = mean of per-scenario values (excluding None)
- `max_drawdown_pct` = max (worst case) of per-scenario values
- `sharpe_ratio` = mean of per-scenario Sharpe values (informational only — the score field carries the aggregate score)
- `trades = ()` (individual trade records not available at aggregation level)

### CLI update: `alpaca-bot-evolve`

Add two new mutually-exclusive arguments:

```
--scenario FILE       (existing) — single scenario, uses run_sweep()
--scenario-dir DIR    (new)      — all *.json files in DIR, uses run_multi_scenario_sweep()
```

Also add `--strategy STRATEGY` (matching the existing `alpaca-bot-sweep` flag) so operators can evolve any of the 11 registered strategies.

Add `--aggregate {min,mean}` defaulting to `min`.

When `--scenario-dir` is used:
- Load all `*.json` files from DIR using `ReplayRunner.load_scenario()`
- Exit with error if fewer than 2 scenarios are found (single-file case should use `--scenario`)
- Print scenario names in the summary header

## Data flow

```
alpaca-bot-backfill --symbols AAPL MSFT TSLA --days 252
    → writes data/backfill/AAPL_252d.json
    → writes data/backfill/MSFT_252d.json
    → writes data/backfill/TSLA_252d.json

alpaca-bot-evolve --scenario-dir data/backfill --aggregate min
    → loads 3 scenarios
    → runs grid: 4×4×3 = 48 combinations × 3 scenarios = 144 ReplayRunner.run() calls
    → scores each combo by min(AAPL_score, MSFT_score, TSLA_score)
    → prints top 10 candidates ranked by worst-case Sharpe
    → saves to DB (TuningResultStore.save_run)
    → writes --output-env winning.env if specified
```

## Files

| Action | File | What changes |
|--------|------|--------------|
| Modify | `src/alpaca_bot/tuning/sweep.py` | Add `run_multi_scenario_sweep()` and `_aggregate_reports()` |
| Modify | `src/alpaca_bot/tuning/cli.py` | Add `--scenario-dir`, `--strategy`, `--aggregate` args |
| Modify | `tests/unit/test_tuning_sweep.py` | Add 4 tests for multi-scenario behaviour |
| Modify | `tests/unit/test_tuning_sweep_cli.py` | Add 2 tests for new CLI args |

No DB schema changes. No runtime path changes. No migrations.

## Tests

### `test_tuning_sweep.py` additions

1. `test_run_multi_scenario_sweep_disqualifies_when_any_scenario_fails` — two scenarios; first produces trades, second does not (quiet/flat bars). Assert all candidates have `score=None`.

2. `test_run_multi_scenario_sweep_min_aggregate_uses_worst_case` — two scored scenarios with different per-scenario scores. Mock `score_report` to return 2.0 for scenario A and 0.5 for scenario B. Assert aggregate score == 0.5.

3. `test_run_multi_scenario_sweep_mean_aggregate_averages_scores` — same setup with `aggregate="mean"`. Assert aggregate score == 1.25.

4. `test_run_multi_scenario_sweep_aggregated_report_sums_trades` — two scored scenarios each producing 3 trades. Assert `candidate.report.total_trades == 6`.

### `test_tuning_sweep_cli.py` additions

5. `test_evolve_cli_scenario_dir_calls_multi_sweep` — monkeypatch `run_multi_scenario_sweep`; assert it is called (not `run_sweep`) when `--scenario-dir` is passed with two JSON files.

6. `test_evolve_cli_scenario_dir_requires_at_least_two_files` — single JSON in dir, assert `SystemExit`.

## Non-goals (YAGNI)

- No walk-forward validation (time-split train/test within a scenario)
- No parallel execution (multiprocessing / threading) — grid is fast enough in-process
- No new database tables or columns
- No UI changes
- No changes to the runtime supervisor or order dispatch path
