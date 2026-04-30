# Spec: Multi-Strategy Validation via Replay/Sweep

## Problem

The ATR guard (entry signal filter) and extended-hours session routing were both added after the
last formal strategy validation run. The existing strategies (breakout, momentum, ORB,
ema_pullback, high_watermark) have not been tested against current code to confirm they remain
profitable under the new constraints.

The replay and tuning tools (`alpaca-bot-backtest compare`, `alpaca-bot-sweep`) already exist and
scenario files for 8 symbols × 252d are checked in at `data/backfill/`. The gap is:

1. No script orchestrates a full multi-symbol, multi-strategy comparison in one command.
2. No consolidated report exists capturing win rate, mean return, drawdown, and Sharpe per
   strategy/symbol combination.
3. The `alpaca-bot-sweep` CLI does not support selecting a strategy — it always uses the default
   breakout evaluator, so per-strategy parameter sweeps require calling `alpaca-bot-backtest sweep`
   individually.

## Goals

1. Add `--strategy` flag to `alpaca-bot-sweep` so multi-file parameter sweeps work for any
   registered strategy.
2. Add a `scripts/validate_strategies.sh` script that runs full compare + sweep across all 252d
   scenario files and writes a markdown report to `docs/`.
3. Produce `docs/validation-report-2026-04-30.md` as the first run of the validation, containing
   the actual findings.
4. Identify whether any strategy/parameter combination warrants a Settings change.

## Non-Goals

- Live trading param changes — this spec only produces a report and recommendations. Applying
  parameter changes is a separate decision.
- Fetching new bar data — the 252d scenario files in `data/backfill/` are the data source.
- Adding new strategies.

## Design

### 1. `--strategy` flag for `alpaca-bot-sweep`

`tuning/sweep_cli.py` gains `--strategy NAME` (default: `breakout`). It passes
`STRATEGY_REGISTRY[name]` as `signal_evaluator` to `run_sweep()`. Invalid strategy names print
an error and exit 1.

### 2. Validation script `scripts/validate_strategies.sh`

Runs in sequence:
1. For each `data/backfill/*_252d.json`: run `alpaca-bot-backtest compare` (all strategies),
   capture output.
2. For the top-2 strategies by mean Sharpe (determined from step 1): run
   `alpaca-bot-sweep --scenario-dir data/backfill` with per-strategy grid.
3. Write all output to `docs/validation-report-YYYYMMDD.md`.

Requires minimal env: `TRADING_MODE=paper ENABLE_LIVE_TRADING=false STRATEGY_VERSION=v1-test
DATABASE_URL=postgresql://dummy:dummy@localhost/db` (all other settings use defaults; DATABASE_URL
is required by Settings but not used during replay).

### 3. Env setup for replay

Settings.from_env() requires `DATABASE_URL`, `SYMBOLS`, and `STRATEGY_VERSION`. For replay these
can be dummy values (no DB connection is made during replay). The script exports a minimal env
block before invoking CLI commands.

## Files Changed

| File | Change |
|------|--------|
| `src/alpaca_bot/tuning/sweep_cli.py` | Add `--strategy` arg, pass `signal_evaluator` to `run_sweep()` |
| `scripts/validate_strategies.sh` | New: orchestrates full compare + sweep, writes report |
| `docs/validation-report-2026-04-30.md` | New: first-run output (generated, not hand-written) |

## Safety Analysis

- No order submission, broker calls, or DB writes — replay only.
- `ENABLE_LIVE_TRADING=false` is set in the validation env block; even a misconfigured run cannot
  submit orders.
- No new Settings fields; DATABASE_URL is required by Settings validation but is unused by the
  replay engine.
- No migration needed.
