# Walk-Forward Validation — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Guard against overfitting by splitting each backfill scenario into in-sample (optimization) and out-of-sample (validation) windows, evaluating top sweep candidates on unseen data.

**Architecture:** Pure splitter in `replay/splitter.py` → OOS evaluation function in `tuning/sweep.py` → `--validate-pct` flag in `tuning/cli.py`.

**Tech Stack:** Python stdlib only. No new dependencies. No migrations.

---

### Task 1: `replay/splitter.py` — scenario date-range splitter

**Files:**
- Create: `src/alpaca_bot/replay/splitter.py`
- Test: `tests/unit/test_replay_splitter.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_replay_splitter.py
from __future__ import annotations

import math
from datetime import date, datetime, timezone, timedelta

import pytest

from alpaca_bot.domain.models import Bar, ReplayScenario
from alpaca_bot.replay.splitter import split_scenario


def _make_bar(symbol: str, ts: datetime, price: float = 100.0) -> Bar:
    return Bar(
        symbol=symbol,
        timestamp=ts,
        open=price,
        high=price + 1,
        low=price - 1,
        close=price,
        volume=10_000.0,
    )


def _make_scenario(n_trading_days: int, intraday_per_day: int = 26) -> ReplayScenario:
    """Build a synthetic scenario with n_trading_days of data.

    Each trading day has `intraday_per_day` 15-min bars (09:30–16:00 ≈ 26 bars).
    Daily bars align one-to-one with trading days.
    """
    intraday: list[Bar] = []
    daily: list[Bar] = []
    # Start on a Monday
    base_date = date(2026, 1, 5)
    for day_idx in range(n_trading_days):
        # Skip weekends (Mondays only in a perfect sequence for simplicity)
        trading_date = base_date + timedelta(days=day_idx)
        daily_ts = datetime(
            trading_date.year, trading_date.month, trading_date.day, 20, 0,
            tzinfo=timezone.utc,
        )
        daily.append(_make_bar("AAPL", daily_ts, 150.0 + day_idx))
        for bar_idx in range(intraday_per_day):
            bar_ts = datetime(
                trading_date.year, trading_date.month, trading_date.day,
                9, 30 + bar_idx * 15 // 60,
                (bar_idx * 15) % 60,
                tzinfo=timezone.utc,
            )
            intraday.append(_make_bar("AAPL", bar_ts, 151.0 + day_idx + bar_idx * 0.01))
    return ReplayScenario(
        name="test_scenario",
        symbol="AAPL",
        starting_equity=100_000.0,
        daily_bars=daily,
        intraday_bars=intraday,
    )


def test_split_respects_ratio():
    scenario = _make_scenario(100)
    is_s, oos_s = split_scenario(scenario, in_sample_ratio=0.8)
    is_dates = {b.timestamp.date() for b in is_s.intraday_bars}
    oos_dates = {b.timestamp.date() for b in oos_s.intraday_bars}
    assert len(is_dates) == 80
    assert len(oos_dates) == 20


def test_split_intraday_bars_no_overlap():
    scenario = _make_scenario(50)
    is_s, oos_s = split_scenario(scenario)
    is_dates = {b.timestamp.date() for b in is_s.intraday_bars}
    oos_dates = {b.timestamp.date() for b in oos_s.intraday_bars}
    assert is_dates.isdisjoint(oos_dates)


def test_split_oos_daily_includes_warmup_prefix():
    scenario = _make_scenario(50, intraday_per_day=4)
    is_s, oos_s = split_scenario(scenario, in_sample_ratio=0.8, daily_warmup=30)
    # OOS daily_bars should start with warmup bars from the IS daily_bars tail
    # IS has 40 daily bars; OOS daily_bars should include the last 30 of those + 10 OOS daily bars
    is_daily_ts = [b.timestamp for b in is_s.daily_bars]
    oos_daily_ts = [b.timestamp for b in oos_s.daily_bars]
    # The warmup bars are from the IS tail — their timestamps must appear in is_daily_ts
    warmup_ts = oos_daily_ts[:30]
    is_set = set(is_daily_ts)
    assert all(ts in is_set for ts in warmup_ts)
    # Total OOS daily_bars length = warmup + OOS daily bars
    assert len(oos_s.daily_bars) == 30 + 10


def test_split_names_suffixed():
    scenario = _make_scenario(20)
    is_s, oos_s = split_scenario(scenario)
    assert is_s.name == "test_scenario_is"
    assert oos_s.name == "test_scenario_oos"


def test_split_raises_on_too_short_scenario():
    scenario = _make_scenario(9)
    with pytest.raises(ValueError, match="too short"):
        split_scenario(scenario)


def test_split_oos_has_at_least_one_date():
    scenario = _make_scenario(10)
    is_s, oos_s = split_scenario(scenario, in_sample_ratio=0.99)
    oos_dates = {b.timestamp.date() for b in oos_s.intraday_bars}
    assert len(oos_dates) >= 1
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/unit/test_replay_splitter.py -v
```
Expected: `ModuleNotFoundError: No module named 'alpaca_bot.replay.splitter'`

- [ ] **Step 3: Implement `replay/splitter.py`**

```python
# src/alpaca_bot/replay/splitter.py
from __future__ import annotations

import math
from datetime import date

from alpaca_bot.domain.models import Bar, ReplayScenario


def split_scenario(
    scenario: ReplayScenario,
    *,
    in_sample_ratio: float = 0.8,
    daily_warmup: int = 30,
) -> tuple[ReplayScenario, ReplayScenario]:
    """Split a scenario chronologically into in-sample and out-of-sample halves.

    in_sample_ratio — fraction of unique trading dates allocated to IS (default 0.8)
    daily_warmup    — number of IS daily bars prepended to OOS daily_bars so that
                      SMA/ATR lookbacks have enough history at the start of OOS
    """
    all_dates: list[date] = sorted(
        {b.timestamp.date() for b in scenario.intraday_bars}
    )
    n = len(all_dates)
    if n < 10:
        raise ValueError(
            f"scenario '{scenario.name}' too short to split: "
            f"need at least 10 trading dates, got {n}"
        )

    split_idx = max(1, math.ceil(n * in_sample_ratio))
    # Ensure at least 1 OOS date
    split_idx = min(split_idx, n - 1)

    is_dates = set(all_dates[:split_idx])
    oos_dates = set(all_dates[split_idx:])

    # --- Intraday bars ---
    is_intraday = [b for b in scenario.intraday_bars if b.timestamp.date() in is_dates]
    oos_intraday = [b for b in scenario.intraday_bars if b.timestamp.date() in oos_dates]

    # --- Daily bars ---
    # IS daily bars: all bars whose date is <= last IS intraday date
    last_is_date = all_dates[split_idx - 1]
    is_daily = [b for b in scenario.daily_bars if b.timestamp.date() <= last_is_date]

    # OOS daily bars: warmup prefix (tail of IS daily bars) + bars after last IS date
    warmup = is_daily[-daily_warmup:] if len(is_daily) >= daily_warmup else is_daily[:]
    oos_daily_tail = [b for b in scenario.daily_bars if b.timestamp.date() > last_is_date]
    oos_daily = warmup + oos_daily_tail

    return (
        ReplayScenario(
            name=f"{scenario.name}_is",
            symbol=scenario.symbol,
            starting_equity=scenario.starting_equity,
            daily_bars=is_daily,
            intraday_bars=is_intraday,
        ),
        ReplayScenario(
            name=f"{scenario.name}_oos",
            symbol=scenario.symbol,
            starting_equity=scenario.starting_equity,
            daily_bars=oos_daily,
            intraday_bars=oos_intraday,
        ),
    )
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/unit/test_replay_splitter.py -v
```
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add src/alpaca_bot/replay/splitter.py tests/unit/test_replay_splitter.py
git commit -m "feat: add split_scenario() for walk-forward IS/OOS partitioning"
```

---

### Task 2: `evaluate_candidates_oos()` in `tuning/sweep.py`

**Files:**
- Modify: `src/alpaca_bot/tuning/sweep.py`
- Test: `tests/unit/test_tuning_sweep.py`

- [ ] **Step 1: Write the failing test**

Read the existing test file first. Append after the last test:

```python
def test_evaluate_candidates_oos_returns_parallel_scores():
    """OOS evaluation produces a score list parallel to the input candidates list."""
    from alpaca_bot.tuning.sweep import evaluate_candidates_oos

    golden = _make_golden_scenario()
    quiet = _make_quiet_scenario()

    # Build two hand-crafted candidates with known params
    from alpaca_bot.tuning.sweep import TuningCandidate
    import os

    base_env = dict(os.environ)

    # Use params from the default grid that produce trades on the golden scenario
    params = {
        "BREAKOUT_LOOKBACK_BARS": "5",
        "RELATIVE_VOLUME_THRESHOLD": "1.0",
        "DAILY_SMA_PERIOD": "5",
    }
    c1 = TuningCandidate(params=params, report=None, score=0.5)
    c2 = TuningCandidate(params=params, report=None, score=0.3)

    scores = evaluate_candidates_oos(
        candidates=[c1, c2],
        oos_scenarios=[golden],
        base_env=base_env,
        min_trades=1,
        aggregate="min",
    )
    assert len(scores) == 2
    # Each score is either float or None
    for s in scores:
        assert s is None or isinstance(s, float)
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/unit/test_tuning_sweep.py::test_evaluate_candidates_oos_returns_parallel_scores -v
```
Expected: `ImportError` or `AttributeError` — function not yet defined

- [ ] **Step 3: Add `evaluate_candidates_oos()` to `tuning/sweep.py`**

Open `src/alpaca_bot/tuning/sweep.py`. After the `run_multi_scenario_sweep` function (end of file), add:

```python
def evaluate_candidates_oos(
    candidates: list[TuningCandidate],
    oos_scenarios: list[ReplayScenario],
    *,
    base_env: dict[str, str],
    min_trades: int,
    aggregate: str = "min",
    signal_evaluator: "StrategySignalEvaluator | None" = None,
) -> list[float | None]:
    """Evaluate IS sweep winners on OOS scenarios.

    Returns a list of OOS scores parallel to `candidates`.
    A candidate is disqualified (score=None) if any OOS scenario yields fewer
    than min_trades trades, matching the IS sweep disqualification logic.
    """
    scores: list[float | None] = []
    for candidate in candidates:
        merged_env = {**base_env, **candidate.params}
        try:
            settings = Settings.from_env(merged_env)
        except ValueError:
            scores.append(None)
            continue

        runner = ReplayRunner(settings, signal_evaluator=signal_evaluator)
        per_scenario_scores: list[float | None] = []
        for scenario in oos_scenarios:
            result = runner.run(scenario)
            report: BacktestReport | None = result.backtest_report  # type: ignore[assignment]
            s = score_report(report, min_trades=min_trades) if report is not None else None
            per_scenario_scores.append(s)

        if any(s is None for s in per_scenario_scores):
            scores.append(None)
        elif aggregate == "mean":
            valid = [s for s in per_scenario_scores if s is not None]
            scores.append(sum(valid) / len(valid))
        else:  # "min"
            valid = [s for s in per_scenario_scores if s is not None]
            scores.append(min(valid))

    return scores
```

Also add `evaluate_candidates_oos` to the imports in `tuning/cli.py` (Task 3 will handle that).

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/unit/test_tuning_sweep.py -v 2>&1 | tail -15
```
Expected: all existing tests pass + 1 new test passes

- [ ] **Step 5: Commit**

```bash
git add src/alpaca_bot/tuning/sweep.py tests/unit/test_tuning_sweep.py
git commit -m "feat: add evaluate_candidates_oos() for walk-forward OOS scoring"
```

---

### Task 3: `--validate-pct` flag in `tuning/cli.py`

**Files:**
- Modify: `src/alpaca_bot/tuning/cli.py`
- Test: `tests/unit/test_tuning_sweep_cli.py`

- [ ] **Step 1: Write the failing tests**

Read `tests/unit/test_tuning_sweep_cli.py`. Append two new tests after the last existing test:

```python
def test_validate_pct_errors_with_single_scenario(tmp_path, monkeypatch):
    """--validate-pct is not supported with --scenario (single-scenario mode)."""
    import sys
    from alpaca_bot.tuning.cli import main

    scenario_path = tmp_path / "s.json"
    scenario_path.write_text('{"name":"s","symbol":"AAPL","starting_equity":100000,'
                              '"daily_bars":[],"intraday_bars":[]}')
    monkeypatch.setattr(sys, "argv", [
        "alpaca-bot-evolve",
        "--scenario", str(scenario_path),
        "--validate-pct", "0.2",
        "--no-db",
    ])
    rc = main()
    assert rc != 0


def test_validate_pct_out_of_range_exits(monkeypatch, tmp_path):
    """--validate-pct must be in (0, 1) — 1.5 should exit with error."""
    import sys
    from alpaca_bot.tuning.cli import main

    scenario_dir = tmp_path / "scenarios"
    scenario_dir.mkdir()
    # Create two minimal scenario files (needed to pass --scenario-dir check)
    for name in ["a.json", "b.json"]:
        (scenario_dir / name).write_text(
            '{"name":"' + name[:-5] + '","symbol":"AAPL","starting_equity":100000,'
            '"daily_bars":[],"intraday_bars":[]}'
        )
    monkeypatch.setattr(sys, "argv", [
        "alpaca-bot-evolve",
        "--scenario-dir", str(scenario_dir),
        "--validate-pct", "1.5",
        "--no-db",
    ])
    rc = main()
    assert rc != 0
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/unit/test_tuning_sweep_cli.py::test_validate_pct_errors_with_single_scenario tests/unit/test_tuning_sweep_cli.py::test_validate_pct_out_of_range_exits -v
```
Expected: both fail (argument not yet defined)

- [ ] **Step 3: Update `tuning/cli.py`**

**3a. Add imports at the top of the file** (after existing imports):

```python
from alpaca_bot.replay.splitter import split_scenario
from alpaca_bot.tuning.sweep import (
    DEFAULT_GRID,
    STRATEGY_GRIDS,
    ParameterGrid,
    TuningCandidate,
    evaluate_candidates_oos,
    run_multi_scenario_sweep,
    run_sweep,
)
```

(Replace the existing import block that imports from `alpaca_bot.tuning.sweep`.)

**3b. Add `--validate-pct` argument** to `parser` (after the `--no-db` argument):

```python
parser.add_argument(
    "--validate-pct", type=float, default=0.0,
    metavar="FLOAT",
    help="Fraction of each scenario held out for OOS validation, e.g. 0.2 (default: 0 = disabled)",
)
```

**3c. Add validation in `main()`** immediately after `args = parser.parse_args(...)`:

```python
if args.validate_pct != 0.0:
    if args.scenario:
        print("--validate-pct requires --scenario-dir, not --scenario", file=sys.stderr)
        return 1
    if not (0.0 < args.validate_pct < 1.0):
        print("--validate-pct must be in range (0.0, 1.0)", file=sys.stderr)
        return 1
```

**3d. In the `else` branch (scenario-dir path)**, replace the block that builds `scenarios` and calls `run_multi_scenario_sweep` with:

```python
    scenario_dir = Path(args.scenario_dir)
    files = sorted(scenario_dir.glob("*.json"))
    if len(files) < 2:
        sys.exit(
            f"--scenario-dir requires at least 2 *.json files; "
            f"found {len(files)} in {scenario_dir}"
        )
    all_scenarios = [ReplayRunner.load_scenario(f) for f in files]

    if args.validate_pct > 0.0:
        # Walk-forward: split each scenario into IS and OOS
        is_scenarios: list = []
        oos_scenarios: list = []
        for s in all_scenarios:
            try:
                is_s, oos_s = split_scenario(s, in_sample_ratio=1.0 - args.validate_pct)
            except ValueError as exc:
                sys.exit(f"Cannot split scenario '{s.name}': {exc}")
            is_scenarios.append(is_s)
            oos_scenarios.append(oos_s)
        scenarios = is_scenarios
    else:
        scenarios = all_scenarios
        oos_scenarios = []

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
```

**3e. After `_print_top_candidates(scored[:10])`, add the OOS block:**

```python
    if args.validate_pct > 0.0 and scored:
        top10 = scored[:10]
        oos_scores = evaluate_candidates_oos(
            candidates=top10,
            oos_scenarios=oos_scenarios,
            base_env=base_env,
            min_trades=args.min_trades,
            aggregate=args.aggregate,
            signal_evaluator=signal_evaluator,
        )
        _print_walk_forward_table(top10, oos_scores)
```

**3f. Add `_print_walk_forward_table()` helper** after `_print_top_candidates()`:

```python
def _print_walk_forward_table(
    candidates: list[TuningCandidate],
    oos_scores: list[float | None],
) -> None:
    print(f"\nWalk-forward OOS evaluation (held = OOS ≥ IS × 50%):")
    print(f"  {'Rank':>4}  {'IS-score':>9}  {'OOS-score':>10}  {'held':>5}  Params")
    for i, (c, oos) in enumerate(zip(candidates, oos_scores), 1):
        is_str = f"{c.score:.4f}" if c.score is not None else "—"
        oos_str = f"{oos:.4f}" if oos is not None else "None"
        if oos is not None and c.score is not None and oos >= c.score * 0.5:
            held = "yes"
        else:
            held = "no"
        params_str = " ".join(f"{k}={v}" for k, v in c.params.items())
        print(f"  [{i:2d}]  {is_str:>9}  {oos_str:>10}  {held:>5}  {params_str}")
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/unit/test_tuning_sweep_cli.py -v 2>&1 | tail -20
```
Expected: all existing tests pass + 2 new tests pass

- [ ] **Step 5: Commit**

```bash
git add src/alpaca_bot/tuning/cli.py tests/unit/test_tuning_sweep_cli.py
git commit -m "feat: add --validate-pct walk-forward validation to alpaca-bot-evolve"
```

---

### Task 4: Full regression

- [ ] **Step 1: Run full test suite**

```bash
pytest -q
```
Expected: all existing tests pass + 9 new tests (6 splitter + 1 sweep + 2 CLI)

- [ ] **Step 2: Smoke-test the CLI help**

```bash
alpaca-bot-evolve --help | grep validate
```
Expected output contains: `--validate-pct FLOAT`

- [ ] **Step 3: Commit if any lint fixes needed; otherwise done**

No commit needed if step 1 passes clean.

---

## Implementation Notes

- `split_scenario` uses date arithmetic only — no time zones or edge cases from DST because it groups by `date()` (local date from `.timestamp.date()`). Since all bar timestamps are UTC, this is consistent.
- The `daily_warmup=30` constant covers `DAILY_SMA_PERIOD` up to 30 (max in `STRATEGY_GRIDS`) and `ATR_PERIOD=14` (default). Any future grid entry that exceeds 30 would need to increase this constant — add a comment in `splitter.py` explaining this.
- `evaluate_candidates_oos` does NOT use a new `ReplayRunner` per candidate — it constructs one runner per candidate with `Settings.from_env(merged_env)`. This is intentional: each candidate has different `Settings` due to different parameter values.
- The `--validate-pct` default of `0.0` means no behavioral change when the flag is omitted — existing callers are unaffected.
- `oos_scenarios` is an empty list when `validate_pct == 0.0`, so the `evaluate_candidates_oos` call is gated behind the `if args.validate_pct > 0.0` check.
