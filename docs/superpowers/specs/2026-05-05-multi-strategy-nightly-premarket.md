# Multi-Strategy Nightly Evaluation + Pre-Market Sanity Check

## Goal

Extend `alpaca-bot-nightly` to sweep all strategies with defined grids (not just breakout),
producing a composite candidate env block covering the full strategy set. Add a new
`alpaca-bot-premarket` CLI that runs at 8:30 AM ET on weekdays, evaluates current active
params against cached scenario files, and logs a pass/fail report before market open.

---

## Context

The supervisor already runs all strategies in `STRATEGY_REGISTRY` simultaneously.
The nightly pipeline currently sweeps only one strategy per run (default: breakout).
Six strategies have defined parameter grids in `STRATEGY_GRIDS`:
`breakout`, `momentum`, `orb`, `high_watermark`, `ema_pullback`, `vwap_reversion`.
Several params are shared across strategies (`RELATIVE_VOLUME_THRESHOLD`,
`ATR_STOP_MULTIPLIER`); the rest are strategy-specific.

---

## Feature 1: Multi-Strategy Nightly

### Changes to `src/alpaca_bot/nightly/cli.py`

**`--strategy` → `--strategies`**: replace the single `--strategy` arg with `--strategies`,
accepting a comma-separated list of strategy names **or** the special value `all`
(default: `all`). This allows operators to scope a run to one or more specific strategies
for debugging while keeping full-sweep as the default.

**Outer sweep loop**: after backfill (unchanged), iterate over the resolved strategy list.
For each strategy, run the existing IS sweep + OOS walk-forward using the same scenario
files already loaded. Collect results into:

```python
winners: list[tuple[str, TuningCandidate, float]]  # (strategy_name, candidate, oos_score)
```

A strategy is added to `winners` only if it produced at least one walk-forward-held
candidate (OOS ≥ IS × `--oos-gate-ratio` AND OOS ≥ `--min-oos-score`). Strategies with
no held candidates are skipped and logged.

**Composite env block**: after all strategies are swept, build the candidate env file as
follows:

1. Sort `winners` by `_viability_key(candidate, oos_score)` descending.
2. Assign shared param keys (`RELATIVE_VOLUME_THRESHOLD`, `ATR_STOP_MULTIPLIER`,
   `RELATIVE_VOLUME_LOOKBACK_BARS`) from the highest-ranked winner.
3. Assign all remaining (strategy-specific) param keys from each respective winner.
4. If two strategies share a non-common key (unexpected), the higher-ranked winner wins.

If `winners` is empty after sweeping all strategies, no `candidate.env` is written —
current params remain unchanged (same fail-closed behaviour as before).

**Per-strategy summary** printed before the composite env block:

```
── Strategy Results ─────────────────────────────────────────────────────
  breakout      score=0.1832  trades=47  pf=1.43  held? ✓
  momentum      score=0.0941  trades=31  pf=1.12  held? ✓
  orb           score=None                         held? ✗  (no held candidates)
  ema_pullback  score=0.1204  trades=38  pf=1.09  held? ✓
Composite winner (shared params from: breakout)
```

**DB persistence**: `tuning_store.save_run()` is called once per strategy that produced
scored candidates (not just held ones), using each strategy's name as part of
`scenario_name` (e.g. `"AAPL+TSLA+MSFT [breakout]"`).

### Shared-param conflict definition

Shared params are the intersection of keys appearing in two or more strategy grids. At
the time of writing: `RELATIVE_VOLUME_THRESHOLD` (all strategies), `ATR_STOP_MULTIPLIER`
(momentum, orb, high_watermark, ema_pullback, vwap_reversion). The shared-param set is
computed at runtime as the intersection of grid keys across all strategies being swept,
so it automatically handles future grid changes.

---

## Feature 2: Pre-Market Sanity Check CLI

### New file: `src/alpaca_bot/nightly/premarket_cli.py`

New CLI entry point `alpaca-bot-premarket`. Read-only — no DB writes, no env file changes.

**Arguments:**

| Arg | Default | Description |
|-----|---------|-------------|
| `--scenario-dir` | `/data/scenarios` | Directory of *.json scenario files (same as nightly) |
| `--validate-pct` | `0.2` | OOS fraction for IS/OOS split |
| `--oos-gate-ratio` | `0.6` | Required OOS/IS ratio to pass |
| `--min-oos-score` | `0.2` | Minimum absolute OOS score to pass |
| `--trading-mode` | (from env) | Override TRADING_MODE |

**Logic:**

1. `Settings.from_env()` — validates the environment is sane (credentials, required vars).
2. Load scenario files from `--scenario-dir`; if dir missing or < 2 files, log warning and
   exit 0 (not a hard failure — nightly may not have run yet today).
3. `base_env = dict(os.environ)` — current active params are already present as env vars.
4. For each strategy in `STRATEGY_GRIDS`:
   a. Build a single-value grid by constraining each key to the current env var value:
      `constrained_grid = {k: [base_env.get(k, defaults[k])] for k in strategy_grid}`.
      Defaults are the same values used by `Settings.from_env()` (e.g. `RELATIVE_VOLUME_THRESHOLD` → `"1.5"`).
   b. Run `run_multi_scenario_sweep` with `constrained_grid` against IS scenarios → produces
      one `TuningCandidate` with an IS score.
   c. Run `evaluate_candidates_oos` with that candidate against OOS scenarios → OOS score.
   d. Determine pass/fail: OOS score is not None AND OOS ≥ IS × `--oos-gate-ratio` AND
      OOS ≥ `--min-oos-score` AND profit_factor ≥ 1.0 AND sharpe > 0.
5. Print per-strategy results and overall summary.
6. Exit 0 if all strategies pass; exit 1 if any fail.

**Output format:**

```
── Pre-market check (2026-05-06 13:30 UTC) ────────────────────────────
  breakout      IS=0.1832  OOS=0.1701  pf=1.43  sharpe=0.31  ✓ PASS
  momentum      IS=0.0941  OOS=0.0812  pf=0.97  sharpe=0.18  ✗ FAIL  profit_factor < 1.0
  ema_pullback  IS=0.1204  OOS=0.1190  pf=1.21  sharpe=0.27  ✓ PASS
Overall: 2/3 strategies pass pre-market gates.
⚠ WARNING: 1 strategy failed pre-market check — review before market open.
```

If all pass:
```
Overall: 3/3 strategies pass pre-market gates.  ✓ All clear.
```

**Extracting current params**: `base_env = dict(os.environ)` — the active env vars
already contain the params applied by the previous nightly run. For each strategy grid
key, the constrained grid uses the current env var value, falling back to the same
hardcoded default that `Settings.from_env()` uses if the var is absent.

---

## Deployment Changes

### `pyproject.toml`

Add entry point:
```
alpaca-bot-premarket = "alpaca_bot.nightly.premarket_cli:main"
```

### `deploy/cron.d/alpaca-bot`

Add premarket line (13:30 UTC = 8:30 AM ET):
```
30 13 * * 1-5 root cd /workspace/alpaca_bot && docker compose -f deploy/compose.yaml run --rm nightly alpaca-bot-premarket >> /var/log/alpaca-bot-premarket.log 2>&1
```

The premarket check runs inside the same `nightly` Docker service (reuses the image with
credentials and scenario volume mount), so no new service is needed.

---

## Testing

### `tests/unit/test_nightly_cli.py` (extend)

- `test_nightly_multi_strategy_sweeps_all_grids`: verify that when `--strategies all`
  is set, the sweep runs for every key in `STRATEGY_GRIDS`.
- `test_nightly_composite_env_shared_params_from_highest_scorer`: verify shared params
  come from the highest-scoring winner when two strategies conflict.
- `test_nightly_omits_strategy_with_no_held_candidates`: verify a strategy whose
  walk-forward fails is absent from the composite env block.
- `test_nightly_no_winners_writes_no_candidate_env`: verify that when all strategies
  fail the walk-forward gate, no `candidate.env` is written.

### `tests/unit/test_premarket_cli.py` (new)

- `test_premarket_pass_returns_exit_0`: all strategies pass gates → exit 0.
- `test_premarket_fail_returns_exit_1`: one strategy fails profit_factor gate → exit 1.
- `test_premarket_missing_scenario_dir_exits_0`: missing dir → warning, exit 0.
- `test_premarket_reads_settings_not_candidate_env`: params come from `Settings.from_env()`
  (verified by injecting a fake Settings with known params).
- `test_premarket_oos_gate_ratio_respected`: candidate with OOS < IS × ratio → FAIL.

---

## Safety Properties

- **No order submission path touched**: both changes are offline evaluation pipelines.
  `evaluate_cycle()` is not called; no broker calls are made.
- **No new env vars**: `--strategies all` is a CLI arg, not an env var.
  `Settings.from_env()` is unchanged.
- **Fail-closed**: if nightly finds no held candidates across all strategies, no
  `candidate.env` is written and `apply_candidate.sh` exits early (existing behaviour).
- **Premarket is advisory**: exit code 1 is logged to cron output; it does not block
  `apply_candidate.sh` or the supervisor.
- **Paper/live identical**: both CLIs use `Settings.from_env()` with `TRADING_MODE`
  from the env. No live-trading-specific paths exist in the evaluation pipeline.
