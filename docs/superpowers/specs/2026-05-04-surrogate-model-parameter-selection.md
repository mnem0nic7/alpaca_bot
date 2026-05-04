# Surrogate Model for Parameter Selection

## Problem

The nightly parameter sweep runs the full grid blindly on every execution — each night's results are independent. After weeks of nightly runs, the `tuning_results` table accumulates thousands of labeled `(params → score)` pairs, but nothing reads them back. The system cannot distinguish between a parameter region that consistently scores well and one that scored well on a single lucky night.

## Goal

Train a lightweight surrogate model on the `tuning_results` history and use it to pre-sort grid candidates before running backtests. Higher-predicted-score combos run first. Cold start (insufficient history) falls back transparently to the existing random grid order.

## Constraints

- `evaluate_cycle()` in `core/engine.py` must remain pure — no ML inference inside it
- Surrogate lives exclusively in the offline nightly pipeline; supervisor loop is untouched
- No new external services — single Python process
- `numpy` and `pandas` are already in `requirements.txt`; `scikit-learn` is new but small
- Cold-start path must be identical to the current grid search behavior
- Per-`trading_mode` models — paper and live runs have different market contexts

## Approach

Three options were considered:

**A. Historical lookup table** — For each exact param combo, average its scores across past runs. Pure Python, no sklearn. Only works for grid points already seen; can't generalize to new grid values.

**B. Gradient-boosted surrogate (chosen)** — Encode params as floats, fit `GradientBoostingRegressor` on all historical scored rows. Predictions generalize beyond seen combos. Trains in < 100ms on 5000 rows. Requires `scikit-learn`.

**C. Bayesian optimization** — Replace grid search with iterative acquisition function. Too much overhead; the grid is small (12–72 combos depending on strategy) and full sweeps are already fast.

**Option B** is chosen. The key benefits over A:
- Generalizes to untried combos when grids expand in future
- Learns interaction effects (e.g. high BREAKOUT_LOOKBACK_BARS is only good when RELATIVE_VOLUME_THRESHOLD is also high)
- Min-samples gate ensures we never fit on noise

## Architecture

### New file: `src/alpaca_bot/tuning/surrogate.py`

`SurrogateModel` wraps `GradientBoostingRegressor`:

```python
class SurrogateModel:
    min_samples: int = 50     # cold-start threshold
    _model: Any | None        # fitted regressor; None = not fitted
    _keys: list[str]          # sorted param names (feature columns)
    
    def fit(self, records: list[dict]) -> bool
    def predict(self, params: dict) -> float | None
    is_fitted: bool           # property
```

- `fit()` extracts `(params dict, score float)` from records, encodes params as float features (parameter values are already numeric strings), trains GBR. Returns `True` if fitted, `False` if < `min_samples` scored rows.
- `predict()` returns `None` if not fitted (caller treats as unknown → no reordering).
- Feature encoding: `float(params.get(key, "0"))` for each sorted key. Works because all grid values are numeric strings already.

### Modified: `TuningResultStore`

Add `load_all_scored(*, trading_mode: str, limit: int = 5000) -> list[dict]` that returns `[{params, score}, ...]` from `tuning_results` ordered by `created_at DESC`. Used exclusively by the nightly CLI to build training data.

### Modified: `run_multi_scenario_sweep`

Add optional `surrogate: SurrogateModel | None = None` parameter. When fitted, sort all parameter combos by descending predicted score before iteration. The full grid still runs — no pruning. Result ordering reflects model expectations, not random grid traversal.

### Modified: `nightly/cli.py`

In the evolve section, before calling `run_multi_scenario_sweep`:
1. Load history: `tuning_store.load_all_scored(trading_mode=trading_mode.value)`
2. Fit: `surrogate = SurrogateModel(); fitted = surrogate.fit(records)`
3. Print whether surrogate is active or cold start
4. Pass `surrogate=surrogate` to sweep

### Dependency

Add `scikit-learn>=1.4` to `requirements.txt`. Import is guarded in `surrogate.py` with a clear `ImportError` message if not installed.

## Data flow

```
tuning_results table
    → load_all_scored()        # historical (params, score) pairs
    → SurrogateModel.fit()     # GBR trained on numeric-encoded params
    → SurrogateModel.predict() # per-combo predicted score
    → sorted combo list        # highest-expected-score first
    → run_multi_scenario_sweep # full grid, reordered
    → top candidates           # walk-forward validation unchanged
```

## Cold-start behaviour

- `fit()` returns `False` when `< min_samples` scored rows exist
- `predict()` returns `None` when not fitted
- `run_multi_scenario_sweep` skips sort when surrogate is `None` or not fitted
- First N nights: identical to current behaviour
- After ~50 scored rows (≈1 full nightly run): model activates

## Safety analysis

| Concern | Status |
|---|---|
| Order submission affected? | No — surrogate is offline-only |
| `evaluate_cycle()` purity? | Unchanged |
| Paper vs. live separation? | Model is fit per `trading_mode` |
| New env vars? | None |
| Walk-forward gate modified? | No — `evaluate_candidates_oos` unchanged |
| Migration needed? | No — reads existing `tuning_results` table |
| Cold-start risk? | Fallback = current behaviour exactly |

## Testing

- `tests/unit/test_tuning_surrogate.py`: test fit with synthetic data, predict after fit, cold-start (insufficient data), predict before fit returns None, feature ordering stability
- `tests/unit/test_nightly_cli.py`: add one test verifying surrogate-active path (mock `load_all_scored` to return 60 records)
- `tests/unit/test_tuning_sweep.py`: add test verifying `run_multi_scenario_sweep` reorders combos when surrogate predicts in reverse

## Out of scope

- Grid pruning (removing combos predicted below threshold) — defer
- Expanding search beyond grid bounds — defer
- Model persistence to disk between runs (model is re-trained each night from DB, cheap)
- Hyperparameter tuning of the GBR itself
