# Strategy-Specific Parameter Grids Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `STRATEGY_GRIDS: dict[str, ParameterGrid]` to `sweep.py` so that `alpaca-bot-evolve --strategy ema_pullback` sweeps EMA-relevant params instead of breakout params.

**Architecture:** `STRATEGY_GRIDS` is a module-level dict in `sweep.py` keyed by strategy name; the CLI uses `STRATEGY_GRIDS.get(args.strategy, DEFAULT_GRID)` to select the grid before any `--params-grid` override is applied. No changes to scoring, Settings, or strategy logic.

**Tech Stack:** Python, pytest, existing `alpaca_bot.tuning` module.

---

### Task 1: Failing tests for STRATEGY_GRIDS completeness and content

**Files:**
- Modify: `tests/unit/test_tuning_sweep.py` (append after existing tests)

- [ ] **Step 1: Write the two failing tests**

Append to `tests/unit/test_tuning_sweep.py`:

```python
# ---------------------------------------------------------------------------
# STRATEGY_GRIDS
# ---------------------------------------------------------------------------

def test_strategy_grids_covers_all_registry_entries() -> None:
    """Every strategy in STRATEGY_REGISTRY must have an entry in STRATEGY_GRIDS."""
    from alpaca_bot.strategy import STRATEGY_REGISTRY
    from alpaca_bot.tuning.sweep import STRATEGY_GRIDS

    missing = [name for name in STRATEGY_REGISTRY if name not in STRATEGY_GRIDS]
    assert not missing, f"Strategies missing from STRATEGY_GRIDS: {missing}"


def test_strategy_grids_keys_match_strategy_params() -> None:
    """Spot-check: each strategy grid contains its unique params, not breakout params."""
    from alpaca_bot.tuning.sweep import STRATEGY_GRIDS

    assert "BREAKOUT_LOOKBACK_BARS" in STRATEGY_GRIDS["breakout"]
    assert "EMA_PERIOD" in STRATEGY_GRIDS["ema_pullback"]
    assert "BREAKOUT_LOOKBACK_BARS" not in STRATEGY_GRIDS["ema_pullback"]
    assert "BB_PERIOD" in STRATEGY_GRIDS["bb_squeeze"]
    assert "BREAKOUT_LOOKBACK_BARS" not in STRATEGY_GRIDS["bb_squeeze"]
```

- [ ] **Step 2: Run to verify tests fail**

```
pytest tests/unit/test_tuning_sweep.py::test_strategy_grids_covers_all_registry_entries tests/unit/test_tuning_sweep.py::test_strategy_grids_keys_match_strategy_params -v
```

Expected: `ImportError` or `KeyError` — `STRATEGY_GRIDS` not yet defined.

---

### Task 2: Failing CLI test for grid selection

**Files:**
- Modify: `tests/unit/test_tuning_sweep_cli.py` (append after existing tests)

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_tuning_sweep_cli.py`:

```python
def test_evolve_cli_uses_strategy_grid_not_default(monkeypatch, tmp_path):
    """--strategy ema_pullback should sweep EMA_PERIOD, not BREAKOUT_LOOKBACK_BARS."""
    import json
    from alpaca_bot.tuning import cli as module

    _patch_env(monkeypatch)

    scenario_file = tmp_path / "SYM_252d.json"
    scenario_file.write_text(json.dumps({
        "name": "test", "symbol": "SYM", "starting_equity": 100000.0,
        "daily_bars": [], "intraday_bars": [],
    }))

    captured: list[dict] = []
    monkeypatch.setattr(module, "run_sweep", lambda **kw: captured.append(kw) or [])
    monkeypatch.setattr(sys, "argv", [
        "evolve", "--scenario", str(scenario_file),
        "--strategy", "ema_pullback", "--no-db",
    ])

    try:
        module.main()
    except SystemExit:
        pass

    assert captured
    grid = captured[0]["grid"]
    assert "EMA_PERIOD" in grid, "EMA_PERIOD should be in the ema_pullback grid"
    assert "BREAKOUT_LOOKBACK_BARS" not in grid, "BREAKOUT_LOOKBACK_BARS should not be in the ema_pullback grid"
```

- [ ] **Step 2: Run to verify test fails**

```
pytest tests/unit/test_tuning_sweep_cli.py::test_evolve_cli_uses_strategy_grid_not_default -v
```

Expected: FAIL — `BREAKOUT_LOOKBACK_BARS` will be in the grid (DEFAULT_GRID is used currently).

---

### Task 3: Implement STRATEGY_GRIDS in sweep.py

**Files:**
- Modify: `src/alpaca_bot/tuning/sweep.py` (add after DEFAULT_GRID, around line 30)

- [ ] **Step 1: Add STRATEGY_GRIDS after DEFAULT_GRID**

In `src/alpaca_bot/tuning/sweep.py`, replace the section after DEFAULT_GRID (currently just a blank line before `_parse_grid`) with:

```python
DEFAULT_GRID: ParameterGrid = {
    "BREAKOUT_LOOKBACK_BARS": ["15", "20", "25", "30"],
    "RELATIVE_VOLUME_THRESHOLD": ["1.3", "1.5", "1.8", "2.0"],
    "DAILY_SMA_PERIOD": ["10", "20", "30"],
}

STRATEGY_GRIDS: dict[str, ParameterGrid] = {
    "breakout": {
        "BREAKOUT_LOOKBACK_BARS": ["15", "20", "25", "30"],
        "RELATIVE_VOLUME_THRESHOLD": ["1.3", "1.5", "1.8", "2.0"],
        "DAILY_SMA_PERIOD": ["10", "20", "30"],
    },
    "momentum": {
        "PRIOR_DAY_HIGH_LOOKBACK_BARS": ["1", "2", "3"],
        "RELATIVE_VOLUME_THRESHOLD": ["1.3", "1.5", "1.8", "2.0"],
        "ATR_STOP_MULTIPLIER": ["1.0", "1.5", "2.0"],
    },
    "orb": {
        "ORB_OPENING_BARS": ["1", "2", "3", "4"],
        "RELATIVE_VOLUME_THRESHOLD": ["1.3", "1.5", "1.8"],
        "ATR_STOP_MULTIPLIER": ["1.0", "1.5", "2.0"],
    },
    "high_watermark": {
        "HIGH_WATERMARK_LOOKBACK_DAYS": ["63", "126", "252"],
        "RELATIVE_VOLUME_THRESHOLD": ["1.3", "1.5", "1.8", "2.0"],
        "ATR_STOP_MULTIPLIER": ["1.0", "1.5", "2.0"],
    },
    "ema_pullback": {
        "EMA_PERIOD": ["7", "9", "12", "20"],
        "RELATIVE_VOLUME_THRESHOLD": ["1.3", "1.5", "1.8"],
        "ATR_STOP_MULTIPLIER": ["1.0", "1.5", "2.0"],
    },
    "vwap_reversion": {
        "VWAP_DIP_THRESHOLD_PCT": ["0.01", "0.015", "0.02", "0.025"],
        "RELATIVE_VOLUME_THRESHOLD": ["1.3", "1.5", "1.8"],
        "ATR_STOP_MULTIPLIER": ["1.0", "1.5", "2.0"],
    },
    "gap_and_go": {
        "GAP_THRESHOLD_PCT": ["0.01", "0.015", "0.02", "0.025"],
        "GAP_VOLUME_THRESHOLD": ["1.5", "2.0", "2.5"],
        "ATR_STOP_MULTIPLIER": ["1.0", "1.5", "2.0"],
    },
    "bull_flag": {
        "BULL_FLAG_MIN_RUN_PCT": ["0.015", "0.02", "0.03"],
        "BULL_FLAG_CONSOLIDATION_RANGE_PCT": ["0.4", "0.5", "0.6"],
        "RELATIVE_VOLUME_THRESHOLD": ["1.3", "1.5", "2.0"],
        "ATR_STOP_MULTIPLIER": ["1.0", "1.5", "2.0"],
    },
    "vwap_cross": {
        "RELATIVE_VOLUME_THRESHOLD": ["1.3", "1.5", "1.8", "2.0"],
        "ATR_STOP_MULTIPLIER": ["1.0", "1.5", "2.0"],
    },
    "bb_squeeze": {
        "BB_PERIOD": ["15", "20", "25"],
        "BB_SQUEEZE_THRESHOLD_PCT": ["0.02", "0.03", "0.04"],
        "RELATIVE_VOLUME_THRESHOLD": ["1.3", "1.5", "2.0"],
    },
    "failed_breakdown": {
        "FAILED_BREAKDOWN_VOLUME_RATIO": ["1.5", "2.0", "2.5", "3.0"],
        "FAILED_BREAKDOWN_RECAPTURE_BUFFER_PCT": ["0.001", "0.002", "0.003"],
        "ATR_STOP_MULTIPLIER": ["1.0", "1.5", "2.0"],
    },
}
```

- [ ] **Step 2: Run the sweep tests to verify they pass**

```
pytest tests/unit/test_tuning_sweep.py::test_strategy_grids_covers_all_registry_entries tests/unit/test_tuning_sweep.py::test_strategy_grids_keys_match_strategy_params -v
```

Expected: PASS.

---

### Task 4: Update CLI to use STRATEGY_GRIDS

**Files:**
- Modify: `src/alpaca_bot/tuning/cli.py`

- [ ] **Step 1: Add STRATEGY_GRIDS to the import and update grid selection**

In `src/alpaca_bot/tuning/cli.py`, change the import from:
```python
from alpaca_bot.tuning.sweep import (
    DEFAULT_GRID,
    ParameterGrid,
    TuningCandidate,
    run_multi_scenario_sweep,
    run_sweep,
)
```
to:
```python
from alpaca_bot.tuning.sweep import (
    DEFAULT_GRID,
    STRATEGY_GRIDS,
    ParameterGrid,
    TuningCandidate,
    run_multi_scenario_sweep,
    run_sweep,
)
```

Then in `main()`, change:
```python
    grid: ParameterGrid = DEFAULT_GRID
    if args.params_grid:
        grid = _load_grid(args.params_grid)
```
to:
```python
    grid: ParameterGrid = STRATEGY_GRIDS.get(args.strategy, DEFAULT_GRID)
    if args.params_grid:
        grid = _load_grid(args.params_grid)
```

- [ ] **Step 2: Run the CLI test to verify it passes**

```
pytest tests/unit/test_tuning_sweep_cli.py::test_evolve_cli_uses_strategy_grid_not_default -v
```

Expected: PASS.

---

### Task 5: Full regression and commit

- [ ] **Step 1: Run full test suite**

```
pytest tests/unit/test_tuning_sweep.py tests/unit/test_tuning_sweep_cli.py -v
```

Expected: all pass (was 11 tests; now 14 tests).

- [ ] **Step 2: Run full regression**

```
pytest
```

Expected: all tests pass (no regressions).

- [ ] **Step 3: Commit**

```bash
git add src/alpaca_bot/tuning/sweep.py src/alpaca_bot/tuning/cli.py \
        tests/unit/test_tuning_sweep.py tests/unit/test_tuning_sweep_cli.py
git commit -m "feat: add STRATEGY_GRIDS for per-strategy parameter tuning

Each of the 11 strategies now has a ParameterGrid keyed by its name in
STRATEGY_GRIDS. The CLI selects the correct grid automatically based on
--strategy, so --strategy ema_pullback sweeps EMA_PERIOD instead of
BREAKOUT_LOOKBACK_BARS.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```
