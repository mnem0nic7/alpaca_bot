# Surrogate Model for Parameter Selection — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Train a `GradientBoostingRegressor` on historical `tuning_results` data and use it to pre-sort the parameter grid before each nightly sweep, so historically strong parameter regions are evaluated first.

**Architecture:** New `SurrogateModel` class in `tuning/surrogate.py`; new `TuningResultStore.load_all_scored()` read method; `run_multi_scenario_sweep` gains an optional `surrogate` parameter that sorts grid combos by predicted score before running; nightly CLI fits the model before calling the sweep.

**Tech Stack:** Python 3.11, scikit-learn>=1.4 (new), numpy (already present)

---

## File Structure

| File | Change |
|---|---|
| `requirements.txt` | Add `scikit-learn>=1.4` |
| `src/alpaca_bot/tuning/surrogate.py` | New: `SurrogateModel` class |
| `src/alpaca_bot/storage/repositories.py` | Add `TuningResultStore.load_all_scored()` |
| `src/alpaca_bot/tuning/sweep.py` | Add `surrogate` param to `run_multi_scenario_sweep` |
| `src/alpaca_bot/nightly/cli.py` | Fit surrogate + pass to sweep |
| `tests/unit/test_tuning_surrogate.py` | New: 5 unit tests |
| `tests/unit/test_tuning_sweep.py` | Add 1 ordering test |
| `tests/unit/test_nightly_cli.py` | Add 1 surrogate-active path test |

---

## Task 1: Add scikit-learn dependency

**Files:**
- Modify: `requirements.txt`

- [ ] **Step 1: Add scikit-learn to requirements**

```
alpaca-py>=0.30.0
pandas>=2.0.0
numpy>=1.26.0
scikit-learn>=1.4
psycopg[binary]>=3.2.0
python-dotenv>=1.0.0
fastapi>=0.115.0
jinja2>=3.1.0
uvicorn>=0.30.0
httpx>=0.27.0
```

- [ ] **Step 2: Install**

Run: `pip install scikit-learn>=1.4 --break-system-packages`
Expected: Successfully installed scikit-learn (or already satisfied)

- [ ] **Step 3: Verify import**

Run: `python3 -c "from sklearn.ensemble import GradientBoostingRegressor; print('OK')"`
Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add requirements.txt
git commit -m "chore: add scikit-learn>=1.4 for surrogate model"
```

---

## Task 2: Create SurrogateModel

**Files:**
- Create: `src/alpaca_bot/tuning/surrogate.py`
- Create: `tests/unit/test_tuning_surrogate.py`

- [ ] **Step 1: Write failing tests**

Create `tests/unit/test_tuning_surrogate.py`:

```python
from __future__ import annotations

import pytest

from alpaca_bot.tuning.surrogate import SurrogateModel


def _make_records(n: int, score_fn=None) -> list[dict]:
    """Synthetic training records with 2 params."""
    records = []
    for i in range(n):
        p1 = float(10 + (i % 4) * 5)   # 10, 15, 20, 25, repeating
        p2 = float(1.0 + (i % 3) * 0.2) # 1.0, 1.2, 1.4, repeating
        score = score_fn(p1, p2) if score_fn else float(i % 5) * 0.1 + 0.05
        records.append({
            "params": {"LOOKBACK": str(int(p1)), "THRESHOLD": str(p2)},
            "score": score,
        })
    return records


def test_surrogate_cold_start_returns_false() -> None:
    model = SurrogateModel(min_samples=50)
    records = _make_records(49)
    result = model.fit(records)
    assert result is False
    assert not model.is_fitted


def test_surrogate_fits_when_enough_samples() -> None:
    model = SurrogateModel(min_samples=50)
    records = _make_records(60)
    result = model.fit(records)
    assert result is True
    assert model.is_fitted


def test_surrogate_predict_returns_none_when_not_fitted() -> None:
    model = SurrogateModel()
    pred = model.predict({"LOOKBACK": "20", "THRESHOLD": "1.5"})
    assert pred is None


def test_surrogate_predict_returns_float_after_fit() -> None:
    model = SurrogateModel(min_samples=10)
    records = _make_records(20)
    model.fit(records)
    pred = model.predict({"LOOKBACK": "20", "THRESHOLD": "1.5"})
    assert isinstance(pred, float)


def test_surrogate_predict_is_deterministic() -> None:
    model = SurrogateModel(min_samples=10)
    records = _make_records(20)
    model.fit(records)
    params = {"LOOKBACK": "20", "THRESHOLD": "1.5"}
    assert model.predict(params) == model.predict(params)
```

- [ ] **Step 2: Run to verify they fail**

Run: `pytest tests/unit/test_tuning_surrogate.py -v`
Expected: `ERROR` (ModuleNotFoundError: No module named 'alpaca_bot.tuning.surrogate')

- [ ] **Step 3: Implement SurrogateModel**

Create `src/alpaca_bot/tuning/surrogate.py`:

```python
from __future__ import annotations

from typing import Any


class SurrogateModel:
    """Gradient-boosted surrogate trained on historical (params, score) pairs.

    Cold-start: fit() returns False when fewer than min_samples scored rows exist.
    predict() returns None when not fitted — callers skip reordering.
    """

    def __init__(self, min_samples: int = 50) -> None:
        self._min_samples = min_samples
        self._model: Any = None
        self._keys: list[str] = []

    @property
    def is_fitted(self) -> bool:
        return self._model is not None

    def fit(self, records: list[dict]) -> bool:
        """Train on records [{params: dict[str,str], score: float}, ...].

        Returns True if the model was fitted, False if below min_samples.
        """
        scored = [r for r in records if r.get("score") is not None]
        if len(scored) < self._min_samples:
            return False

        try:
            from sklearn.ensemble import GradientBoostingRegressor
        except ImportError as exc:
            raise ImportError(
                "scikit-learn is required for SurrogateModel. "
                "Install it with: pip install scikit-learn>=1.4"
            ) from exc

        all_keys = sorted({k for r in scored for k in r["params"]})
        X = [
            [float(r["params"].get(k, "0")) for k in all_keys]
            for r in scored
        ]
        y = [float(r["score"]) for r in scored]

        model = GradientBoostingRegressor(
            n_estimators=100, max_depth=3, random_state=42
        )
        model.fit(X, y)
        self._model = model
        self._keys = all_keys
        return True

    def predict(self, params: dict[str, str]) -> float | None:
        """Return predicted score for params, or None if not fitted."""
        if self._model is None:
            return None
        features = [float(params.get(k, "0")) for k in self._keys]
        return float(self._model.predict([features])[0])
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_tuning_surrogate.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add src/alpaca_bot/tuning/surrogate.py tests/unit/test_tuning_surrogate.py
git commit -m "feat: add SurrogateModel for historical parameter score prediction"
```

---

## Task 3: Add TuningResultStore.load_all_scored()

**Files:**
- Modify: `src/alpaca_bot/storage/repositories.py` (after `load_latest_best`, before `class StrategyFlagStore`)

- [ ] **Step 1: Write failing test**

Add to `tests/unit/test_tuning_surrogate.py` (at the bottom of the file, after existing tests):

```python
# --- TuningResultStore.load_all_scored integration ---

import json


class _FakeConn:
    """Minimal ConnectionProtocol stub for TuningResultStore tests."""

    def __init__(self, rows):
        self._rows = rows

    def cursor(self):
        return self

    def execute(self, sql, params=None):
        self._last_params = params

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def commit(self): pass
    def rollback(self): pass


def test_load_all_scored_returns_list_of_dicts() -> None:
    from alpaca_bot.storage.repositories import TuningResultStore

    raw_params = {"BREAKOUT_LOOKBACK_BARS": "20", "RELATIVE_VOLUME_THRESHOLD": "1.5"}
    rows = [(json.dumps(raw_params), 0.75)]
    conn = _FakeConn(rows)
    store = TuningResultStore(conn)
    results = store.load_all_scored(trading_mode="paper")
    assert len(results) == 1
    assert results[0]["params"] == raw_params
    assert results[0]["score"] == 0.75
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/unit/test_tuning_surrogate.py::test_load_all_scored_returns_list_of_dicts -v`
Expected: FAIL (AttributeError: 'TuningResultStore' has no attribute 'load_all_scored')

- [ ] **Step 3: Implement load_all_scored**

In `src/alpaca_bot/storage/repositories.py`, add after `load_latest_best` (around line 1039), before `class StrategyFlagStore`:

```python
    def load_all_scored(
        self,
        *,
        trading_mode: str,
        limit: int = 5000,
    ) -> list[dict]:
        """Return all scored rows as [{params, score}, ...] for surrogate training.

        Ordered most-recent first; capped at limit to bound memory.
        """
        rows = fetch_all(
            self._connection,
            """
            SELECT params, score
            FROM tuning_results
            WHERE trading_mode = %s AND score IS NOT NULL
            ORDER BY created_at DESC
            LIMIT %s
            """,
            (trading_mode, limit),
        )
        return [
            {
                "params": row[0] if isinstance(row[0], dict) else json.loads(row[0]),
                "score": float(row[1]),
            }
            for row in rows
        ]
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/unit/test_tuning_surrogate.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add src/alpaca_bot/storage/repositories.py tests/unit/test_tuning_surrogate.py
git commit -m "feat: add TuningResultStore.load_all_scored() for surrogate training data"
```

---

## Task 4: Add surrogate param to run_multi_scenario_sweep

**Files:**
- Modify: `src/alpaca_bot/tuning/sweep.py`
- Modify: `tests/unit/test_tuning_sweep.py`

- [ ] **Step 1: Write failing test**

Add to the bottom of `tests/unit/test_tuning_sweep.py`:

```python
def test_run_multi_scenario_sweep_respects_surrogate_ordering() -> None:
    """Surrogate pre-sorts grid: high-predicted-score combo runs first → appears first in results."""
    from alpaca_bot.tuning.surrogate import SurrogateModel

    class _FixedSurrogate(SurrogateModel):
        """Predicts 1.0 for BREAKOUT_LOOKBACK_BARS=15 and 0.0 for everything else."""
        @property
        def is_fitted(self) -> bool:
            return True
        def predict(self, params: dict) -> float | None:
            return 1.0 if params.get("BREAKOUT_LOOKBACK_BARS") == "15" else 0.0

    quiet_1 = _make_quiet_scenario()
    quiet_2 = ReplayScenario(
        name="quiet2", symbol="AAPL", starting_equity=100_000.0,
        daily_bars=quiet_1.daily_bars, intraday_bars=quiet_1.intraday_bars,
    )
    small_grid: ParameterGrid = {
        "BREAKOUT_LOOKBACK_BARS": ["15", "30"],
        "RELATIVE_VOLUME_THRESHOLD": ["1.5"],
        "DAILY_SMA_PERIOD": ["20"],
    }

    results = run_multi_scenario_sweep(
        scenarios=[quiet_1, quiet_2],
        base_env=_base_env(),
        grid=small_grid,
        surrogate=_FixedSurrogate(),
    )

    # Both combos produce score=None (quiet scenario). Python's sort is stable,
    # so insertion order is preserved for equal keys. The surrogate pre-sort
    # determines which combo runs first → gets appended first → stays first after
    # the stable final sort. Assert that the surrogate-preferred combo (LOOKBACK=15)
    # is first in results.
    lookbacks = [c.params["BREAKOUT_LOOKBACK_BARS"] for c in results]
    assert set(lookbacks) == {"15", "30"}, "both combos must run (no pruning)"
    assert results[0].params["BREAKOUT_LOOKBACK_BARS"] == "15", \
        "surrogate-preferred combo (predicted 1.0) must appear first"
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/unit/test_tuning_sweep.py::test_run_multi_scenario_sweep_respects_surrogate_ordering -v`
Expected: FAIL (TypeError: run_multi_scenario_sweep() got an unexpected keyword argument 'surrogate')

- [ ] **Step 3: Add surrogate param to run_multi_scenario_sweep**

In `src/alpaca_bot/tuning/sweep.py`, modify the `run_multi_scenario_sweep` function signature and add sort logic. Change the function definition from:

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
```

To:

```python
def run_multi_scenario_sweep(
    *,
    scenarios: list[ReplayScenario],
    base_env: dict[str, str],
    grid: ParameterGrid | None = None,
    min_trades_per_scenario: int = 2,
    aggregate: str = "min",
    signal_evaluator: "StrategySignalEvaluator | None" = None,
    surrogate: "SurrogateModel | None" = None,
) -> list[TuningCandidate]:
```

Add the import at the top of the function body (inside TYPE_CHECKING block is cleaner — add to TYPE_CHECKING block at the top of the file):

In the `if TYPE_CHECKING:` block at the top of `sweep.py`, add:
```python
    from alpaca_bot.tuning.surrogate import SurrogateModel
```

Then in the function body, replace the line:
```python
    candidates: list[TuningCandidate] = []
    for combo in itertools.product(*value_lists):
```

With:
```python
    all_combos = list(itertools.product(*value_lists))
    if surrogate is not None and surrogate.is_fitted:
        all_combos.sort(
            key=lambda combo: surrogate.predict(dict(zip(keys, combo))) or 0.0,
            reverse=True,
        )
    candidates: list[TuningCandidate] = []
    for combo in all_combos:
```

- [ ] **Step 4: Run test**

Run: `pytest tests/unit/test_tuning_sweep.py -v -k "surrogate_ordering"`
Expected: PASS

- [ ] **Step 5: Run full sweep test suite to check no regressions**

Run: `pytest tests/unit/test_tuning_sweep.py -v`
Expected: All existing tests + new test pass

- [ ] **Step 6: Commit**

```bash
git add src/alpaca_bot/tuning/sweep.py tests/unit/test_tuning_sweep.py
git commit -m "feat: run_multi_scenario_sweep respects surrogate ordering when fitted"
```

---

## Task 5: Integrate surrogate into nightly CLI

**Files:**
- Modify: `src/alpaca_bot/nightly/cli.py`
- Modify: `tests/unit/test_nightly_cli.py`

- [ ] **Step 1: Write failing test**

Add to `tests/unit/test_nightly_cli.py` (after the existing imports and helpers):

```python
def test_nightly_cli_surrogate_active_path(monkeypatch, tmp_path):
    """When load_all_scored returns 60 records, surrogate fits and is passed to sweep."""
    from alpaca_bot.nightly import cli as module
    from alpaca_bot.tuning.sweep import TuningCandidate

    _patch_env(monkeypatch)
    _make_scenario_files(tmp_path)

    monkeypatch.setattr(module, "connect_postgres", lambda url: object())

    class FakeWatchlistStore:
        def __init__(self, conn): pass
        def list_enabled(self, trading_mode): return ["AAPL", "MSFT"]

    monkeypatch.setattr(module, "WatchlistStore", FakeWatchlistStore)

    class FakeTuningResultStore:
        def __init__(self, conn): pass
        def load_all_scored(self, *, trading_mode, limit=5000):
            return [
                {"params": {"BREAKOUT_LOOKBACK_BARS": str(15 + (i % 4) * 5),
                             "RELATIVE_VOLUME_THRESHOLD": str(round(1.3 + (i % 4) * 0.2, 1)),
                             "DAILY_SMA_PERIOD": str(10 + (i % 3) * 10)},
                 "score": float(i % 5) * 0.15 + 0.1}
                for i in range(60)
            ]
        def save_run(self, **kw): return "fake-run-id"

    monkeypatch.setattr(module, "TuningResultStore", FakeTuningResultStore)

    class FakeOrderStore:
        def __init__(self, conn): pass
        def list_closed_trades(self, **kw): return []

    monkeypatch.setattr(module, "OrderStore", FakeOrderStore)

    class FakeDailySessionStateStore:
        def __init__(self, conn): pass
        def load(self, **kw): return None

    monkeypatch.setattr(module, "DailySessionStateStore", FakeDailySessionStateStore)

    surrogate_kwargs = {}

    def fake_sweep(**kw):
        surrogate_kwargs.update({"surrogate": kw.get("surrogate")})
        cand = TuningCandidate(
            params={"BREAKOUT_LOOKBACK_BARS": "20"}, report=None, score=0.5
        )
        return [cand]

    monkeypatch.setattr(module, "run_multi_scenario_sweep", fake_sweep)
    monkeypatch.setattr(module, "evaluate_candidates_oos",
                        lambda candidates, oos_scenarios, **kw: [None])
    monkeypatch.setattr(module, "split_scenario", _fake_split)

    monkeypatch.setattr(sys, "argv", [
        "nightly", "--dry-run", "--no-db", "--output-dir", str(tmp_path),
    ])

    result = module.main()

    assert result == 0
    surrogate = surrogate_kwargs.get("surrogate")
    assert surrogate is not None, "surrogate must be passed to run_multi_scenario_sweep"
    assert surrogate.is_fitted, "surrogate must be fitted when 60 records are available"
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/unit/test_nightly_cli.py::test_nightly_cli_surrogate_active_path -v`
Expected: FAIL (assertion error or AttributeError — surrogate not passed yet)

- [ ] **Step 3: Update nightly CLI**

In `src/alpaca_bot/nightly/cli.py`, add `SurrogateModel` to the imports from `alpaca_bot.tuning.sweep` block. Also import `load_all_scored` via `TuningResultStore`. The `TuningResultStore` is already imported.

At the top of `cli.py`, add to the existing imports:
```python
from alpaca_bot.tuning.surrogate import SurrogateModel
```

Then in `main()`, in the evolve section, before the `run_multi_scenario_sweep` call (around line 142), add surrogate construction:

Find this block:
```python
            candidates = run_multi_scenario_sweep(
                scenarios=is_scenarios,
                base_env=base_env,
                grid=grid,
                signal_evaluator=signal_evaluator,
            )
```

Replace with:
```python
            tuning_store = TuningResultStore(conn)
            try:
                historical = tuning_store.load_all_scored(trading_mode=trading_mode.value)
            except Exception as exc:
                print(f"Warning: could not load tuning history for surrogate: {exc}",
                      file=sys.stderr)
                historical = []
            # Filter to records matching the current strategy's grid keys exactly;
            # avoids cross-strategy contamination and stale-grid noise.
            grid_keys = set(grid.keys())
            historical = [r for r in historical if set(r["params"].keys()) == grid_keys]
            surrogate = SurrogateModel()
            surrogate_fitted = surrogate.fit(historical)
            if surrogate_fitted:
                print(f"Surrogate: fitted on {len(historical)} historical records")
            else:
                print(f"Surrogate: cold start ({len(historical)} records < 50 — full grid)")

            candidates = run_multi_scenario_sweep(
                scenarios=is_scenarios,
                base_env=base_env,
                grid=grid,
                signal_evaluator=signal_evaluator,
                surrogate=surrogate,
            )
```

Note: `TuningResultStore` is already instantiated later in the `if held_pairs` block as `tuning_store`. Move that variable up or rename this one to avoid double instantiation. Change the existing `tuning_store` instantiation in `if held_pairs:` block from:
```python
                    tuning_store = TuningResultStore(conn)
                    run_id = tuning_store.save_run(
```
to just:
```python
                    run_id = tuning_store.save_run(
```
(reuse the `tuning_store` already created above)

- [ ] **Step 4: Run test**

Run: `pytest tests/unit/test_nightly_cli.py::test_nightly_cli_surrogate_active_path -v`
Expected: PASS

- [ ] **Step 5: Run full nightly test suite**

Run: `pytest tests/unit/test_nightly_cli.py -v`
Expected: All 6 tests pass

- [ ] **Step 6: Commit**

```bash
git add src/alpaca_bot/nightly/cli.py tests/unit/test_nightly_cli.py
git commit -m "feat: fit surrogate model in nightly pipeline and pass to sweep"
```

---

## Task 6: Final regression check

- [ ] **Step 1: Run full test suite**

Run: `pytest -q --tb=short`
Expected: All tests pass (at least 1095 existing + 7 new = 1102)

- [ ] **Step 2: Verify CLI help still works**

Run: `alpaca-bot-nightly --help`
Expected: prints usage with all flags including `--validate-pct`

- [ ] **Step 3: Commit**

```bash
git add .
git commit -m "test: full regression green — surrogate model integration complete"
```

---

## Self-Review Checklist

**Spec coverage:**
- [x] `SurrogateModel.fit()` returns bool — Task 2
- [x] Cold start (< 50 samples) falls back silently — Task 2
- [x] `load_all_scored()` reads from `tuning_results` — Task 3
- [x] `run_multi_scenario_sweep` sorts by predicted score when surrogate is fitted — Task 4
- [x] Nightly CLI loads history, fits surrogate, passes it to sweep — Task 5
- [x] `scikit-learn` added to requirements — Task 1
- [x] No changes to `evaluate_cycle()` — not in scope
- [x] No new env vars — confirmed
- [x] No new migration — `load_all_scored` reads existing `tuning_results` table

**Grilling fixes applied:**
- [x] Q4+Q6: Grid-key filter before `surrogate.fit()` — filters cross-strategy and stale-grid records — Task 5
- [x] Q8: Ordering test asserts `results[0]` position, not just set membership — Task 4
- [x] Q10: `load_all_scored` wrapped in try/except; falls back to `[]` with warning — Task 5

**Type consistency:**
- `SurrogateModel.predict()` → `float | None` ✓
- `run_multi_scenario_sweep(surrogate=...)` uses `SurrogateModel | None` ✓
- `load_all_scored()` returns `list[dict]` matching what `SurrogateModel.fit()` expects ✓
- `trading_mode.value` (str) passed to `load_all_scored` — not the enum object ✓
