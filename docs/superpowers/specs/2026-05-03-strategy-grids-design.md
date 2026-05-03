# Strategy-Specific Parameter Grids Design

## Problem

`DEFAULT_GRID` in `tuning/sweep.py` sweeps only three breakout-specific parameters
(`BREAKOUT_LOOKBACK_BARS`, `RELATIVE_VOLUME_THRESHOLD`, `DAILY_SMA_PERIOD`). When
`alpaca-bot-evolve --strategy ema_pullback` is run, it sweeps breakout parameters against
an EMA strategy — the results are meaningless because the swept params have no effect on
`ema_pullback`'s signal logic.

Ten of the eleven strategies have zero effective tuning.

## Goal

Add `STRATEGY_GRIDS: dict[str, ParameterGrid]` to `sweep.py`. Each entry defines the
parameters that actually drive that strategy's decisions, at sensible value ranges. The CLI
selects the right grid automatically when `--strategy` is provided.

## Scope

Two files change:
- `src/alpaca_bot/tuning/sweep.py` — add `STRATEGY_GRIDS`
- `src/alpaca_bot/tuning/cli.py` — use `STRATEGY_GRIDS.get(args.strategy, DEFAULT_GRID)`

Three test files change:
- `tests/unit/test_tuning_sweep.py` — two unit tests for STRATEGY_GRIDS completeness and content
- `tests/unit/test_tuning_sweep_cli.py` — one CLI test verifying grid selection

No changes to `Settings`, `config/__init__.py`, strategy logic, or any runtime path.
`DEFAULT_GRID` is retained unchanged as the breakout-specific grid and as the fallback.

## Strategy Grid Definitions

Each grid contains only the params that the strategy's `evaluate_signal` function reads.
Shared params (especially `RELATIVE_VOLUME_THRESHOLD` and `ATR_STOP_MULTIPLIER`) appear
in every grid where the strategy uses them.

| Strategy | Grid params | Combinations |
|---|---|---|
| `breakout` | BREAKOUT_LOOKBACK_BARS, RELATIVE_VOLUME_THRESHOLD, DAILY_SMA_PERIOD | 48 |
| `momentum` | PRIOR_DAY_HIGH_LOOKBACK_BARS, RELATIVE_VOLUME_THRESHOLD, ATR_STOP_MULTIPLIER | 36 |
| `orb` | ORB_OPENING_BARS, RELATIVE_VOLUME_THRESHOLD, ATR_STOP_MULTIPLIER | 36 |
| `high_watermark` | HIGH_WATERMARK_LOOKBACK_DAYS, RELATIVE_VOLUME_THRESHOLD, ATR_STOP_MULTIPLIER | 36 |
| `ema_pullback` | EMA_PERIOD, RELATIVE_VOLUME_THRESHOLD, ATR_STOP_MULTIPLIER | 36 |
| `vwap_reversion` | VWAP_DIP_THRESHOLD_PCT, RELATIVE_VOLUME_THRESHOLD, ATR_STOP_MULTIPLIER | 36 |
| `gap_and_go` | GAP_THRESHOLD_PCT, GAP_VOLUME_THRESHOLD, ATR_STOP_MULTIPLIER | 36 |
| `bull_flag` | BULL_FLAG_MIN_RUN_PCT, BULL_FLAG_CONSOLIDATION_RANGE_PCT, RELATIVE_VOLUME_THRESHOLD, ATR_STOP_MULTIPLIER | 36 |
| `vwap_cross` | RELATIVE_VOLUME_THRESHOLD, ATR_STOP_MULTIPLIER | 12 |
| `bb_squeeze` | BB_PERIOD, BB_SQUEEZE_THRESHOLD_PCT, RELATIVE_VOLUME_THRESHOLD | 27 |
| `failed_breakdown` | FAILED_BREAKDOWN_VOLUME_RATIO, FAILED_BREAKDOWN_RECAPTURE_BUFFER_PCT, ATR_STOP_MULTIPLIER | 36 |

### Value ranges (exact values to put in the dict)

```
breakout:
  BREAKOUT_LOOKBACK_BARS:       ["15", "20", "25", "30"]
  RELATIVE_VOLUME_THRESHOLD:    ["1.3", "1.5", "1.8", "2.0"]
  DAILY_SMA_PERIOD:             ["10", "20", "30"]

momentum:
  PRIOR_DAY_HIGH_LOOKBACK_BARS: ["1", "2", "3"]
  RELATIVE_VOLUME_THRESHOLD:    ["1.3", "1.5", "1.8", "2.0"]
  ATR_STOP_MULTIPLIER:          ["1.0", "1.5", "2.0"]

orb:
  ORB_OPENING_BARS:             ["1", "2", "3", "4"]
  RELATIVE_VOLUME_THRESHOLD:    ["1.3", "1.5", "1.8"]
  ATR_STOP_MULTIPLIER:          ["1.0", "1.5", "2.0"]

high_watermark:
  HIGH_WATERMARK_LOOKBACK_DAYS: ["63", "126", "252"]
  RELATIVE_VOLUME_THRESHOLD:    ["1.3", "1.5", "1.8", "2.0"]
  ATR_STOP_MULTIPLIER:          ["1.0", "1.5", "2.0"]

ema_pullback:
  EMA_PERIOD:                   ["7", "9", "12", "20"]
  RELATIVE_VOLUME_THRESHOLD:    ["1.3", "1.5", "1.8"]
  ATR_STOP_MULTIPLIER:          ["1.0", "1.5", "2.0"]

vwap_reversion:
  VWAP_DIP_THRESHOLD_PCT:       ["0.01", "0.015", "0.02", "0.025"]
  RELATIVE_VOLUME_THRESHOLD:    ["1.3", "1.5", "1.8"]
  ATR_STOP_MULTIPLIER:          ["1.0", "1.5", "2.0"]

gap_and_go:
  GAP_THRESHOLD_PCT:            ["0.01", "0.015", "0.02", "0.025"]
  GAP_VOLUME_THRESHOLD:         ["1.5", "2.0", "2.5"]
  ATR_STOP_MULTIPLIER:          ["1.0", "1.5", "2.0"]

bull_flag:
  BULL_FLAG_MIN_RUN_PCT:             ["0.015", "0.02", "0.03"]
  BULL_FLAG_CONSOLIDATION_RANGE_PCT: ["0.4", "0.5", "0.6"]
  RELATIVE_VOLUME_THRESHOLD:         ["1.3", "1.5", "2.0"]
  ATR_STOP_MULTIPLIER:               ["1.0", "1.5", "2.0"]

vwap_cross:
  RELATIVE_VOLUME_THRESHOLD:    ["1.3", "1.5", "1.8", "2.0"]
  ATR_STOP_MULTIPLIER:          ["1.0", "1.5", "2.0"]

bb_squeeze:
  BB_PERIOD:                    ["15", "20", "25"]
  BB_SQUEEZE_THRESHOLD_PCT:     ["0.02", "0.03", "0.04"]
  RELATIVE_VOLUME_THRESHOLD:    ["1.3", "1.5", "2.0"]

failed_breakdown:
  FAILED_BREAKDOWN_VOLUME_RATIO:         ["1.5", "2.0", "2.5", "3.0"]
  FAILED_BREAKDOWN_RECAPTURE_BUFFER_PCT: ["0.001", "0.002", "0.003"]
  ATR_STOP_MULTIPLIER:                   ["1.0", "1.5", "2.0"]
```

## CLI Behaviour

Current:
```python
grid: ParameterGrid = DEFAULT_GRID
if args.params_grid:
    grid = _load_grid(args.params_grid)
```

After:
```python
from alpaca_bot.tuning.sweep import DEFAULT_GRID, STRATEGY_GRIDS, ...

grid: ParameterGrid = STRATEGY_GRIDS.get(args.strategy, DEFAULT_GRID)
if args.params_grid:
    grid = _load_grid(args.params_grid)  # explicit --params-grid always wins
```

`--params-grid` still overrides everything — unchanged semantics.

## Validation / Safety

- `Settings.from_env()` already validates all params. Invalid combo → `ValueError` → silently
  skipped by the sweep loop. No new validation needed.
- All grid values already have env var counterparts in `Settings.from_env()` — no missing keys.
- `RELATIVE_VOLUME_THRESHOLD` must be > 1.0 per `Settings.validate()`. All grid values ≥ 1.3.
- `ATR_STOP_MULTIPLIER` must be in (0, 10.0]. All grid values ≤ 2.0.
- `VWAP_DIP_THRESHOLD_PCT` must be in (0, 1.0). All values ≤ 0.025.
- `GAP_THRESHOLD_PCT` must be in (0, 1.0). All values ≤ 0.025.

## Tests

**`tests/unit/test_tuning_sweep.py`** — two new tests:

1. `test_strategy_grids_covers_all_registry_entries` — assert that every key in
   `STRATEGY_REGISTRY` also appears in `STRATEGY_GRIDS`. This is the completeness guard;
   it will fail if a new strategy is added without a grid.

2. `test_strategy_grids_keys_match_strategy_params` — spot-check 3 grids:
   - `STRATEGY_GRIDS["breakout"]` contains `BREAKOUT_LOOKBACK_BARS`
   - `STRATEGY_GRIDS["ema_pullback"]` contains `EMA_PERIOD` (not `BREAKOUT_LOOKBACK_BARS`)
   - `STRATEGY_GRIDS["bb_squeeze"]` contains `BB_PERIOD` (not `BREAKOUT_LOOKBACK_BARS`)

**`tests/unit/test_tuning_sweep_cli.py`** — one new test:

3. `test_evolve_cli_uses_strategy_grid_not_default` — run CLI with `--strategy ema_pullback`,
   monkeypatch `run_sweep`, assert that `kwargs["grid"]` contains `EMA_PERIOD` and does not
   contain `BREAKOUT_LOOKBACK_BARS`.

## Non-goals

- No changes to scoring (`score_report`) or aggregation logic.
- No changes to `Settings` or `config/__init__.py`.
- No changes to strategy signal logic.
- No new env vars.
- `DEFAULT_GRID` is not removed — it remains as the breakout grid and the fallback.
