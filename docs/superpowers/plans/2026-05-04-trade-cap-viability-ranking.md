# Trade Cap and Viability Ranking Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `max_trades` ceiling gate to `score_report()` and replace the raw-Sharpe sort in sweep functions with a richer viability composite that uses R-multiple and win-rate as tie-breakers.

**Architecture:** `max_trades` mirrors `max_drawdown_pct` — zero means disabled, positive value disqualifies candidates where any scenario produces more than that many trades. `_viability_key()` returns a 4-tuple `(has_score, score, r_multiple, win_rate)` used as the sort key in sweep results and as the `max()` key in WF candidate selection in both CLIs.

**Tech Stack:** Python 3.13, pytest, existing TDD pattern (fake callables, no mocks of own classes)

---

### Task 1: max_trades gate in score_report() + propagate to all sweep functions

**Files:**
- Modify: `src/alpaca_bot/tuning/sweep.py`
- Modify: `tests/unit/test_tuning_sweep.py` (update 2 fake_score signatures + add 2 tests)

- [ ] **Step 1: Update the two existing fake_score signatures in test_tuning_sweep.py**

Both `fake_score` functions at lines ~211 and ~242 currently have signature `(report, *, min_trades, max_drawdown_pct=0.0)`. Update both to include `max_trades=0`:

```python
def fake_score(report, *, min_trades, max_drawdown_pct=0.0, max_trades=0):
```

Run: `pytest tests/unit/test_tuning_sweep.py -v --tb=short`
Expected: all existing 38 tests pass (no change yet, just safe to proceed)

- [ ] **Step 2: Write failing tests for max_trades gate**

Append to `tests/unit/test_tuning_sweep.py`:

```python
def test_score_report_disqualifies_on_excessive_trades() -> None:
    """max_trades gate: total_trades > max_trades → None; at or below → scored."""
    report = BacktestReport(
        trades=(), total_trades=6, winning_trades=4, losing_trades=2,
        win_rate=0.67, mean_return_pct=0.02, max_drawdown_pct=0.05, sharpe_ratio=0.8,
    )
    assert score_report(report, min_trades=3, max_trades=5) is None
    assert score_report(report, min_trades=3, max_trades=6) is not None  # boundary: equal is OK
    assert score_report(report, min_trades=3, max_trades=0) is not None  # 0 = disabled


def test_score_report_max_trades_zero_disabled() -> None:
    """max_trades=0 (default) never disqualifies on trade count ceiling."""
    report = BacktestReport(
        trades=(), total_trades=500, winning_trades=300, losing_trades=200,
        win_rate=0.6, mean_return_pct=0.01, max_drawdown_pct=0.10, sharpe_ratio=0.5,
    )
    assert score_report(report, min_trades=3, max_trades=0) is not None
```

Run: `pytest tests/unit/test_tuning_sweep.py::test_score_report_disqualifies_on_excessive_trades tests/unit/test_tuning_sweep.py::test_score_report_max_trades_zero_disabled -v`
Expected: FAIL — NameError or AssertionError (max_trades not yet a parameter)

- [ ] **Step 3: Implement max_trades in score_report()**

In `src/alpaca_bot/tuning/sweep.py`, update `score_report()`:

```python
def score_report(
    report: BacktestReport,
    *,
    min_trades: int = 3,
    max_drawdown_pct: float = 0.0,
    max_trades: int = 0,
) -> float | None:
    """Sharpe-first composite score; None if disqualified.

    Disqualified when: fewer than min_trades, more than max_trades (when max_trades > 0),
    profit_factor < 1.0 (net-losing), base score ≤ 0 (non-positive Sharpe/Calmar),
    or drawdown exceeds max_drawdown_pct (when max_drawdown_pct > 0.0 and not None).
    profit_factor=None (no losses at all) is never penalised.
    """
    if report.total_trades < min_trades:
        return None
    if max_trades > 0 and report.total_trades > max_trades:
        return None  # over-trading: exceeds operator-configured ceiling
    if report.sharpe_ratio is not None:
        base = report.sharpe_ratio
    elif report.mean_return_pct is None:
        return None
    else:
        drawdown = report.max_drawdown_pct or 0.0
        base = report.mean_return_pct / (drawdown + 0.001)
    if report.profit_factor is not None and report.profit_factor < 1.0:
        return None  # net-losing strategy: hard disqualify
    if base <= 0.0:
        return None  # non-positive Sharpe/Calmar: no exploitable edge
    if (max_drawdown_pct > 0.0
            and report.max_drawdown_pct is not None
            and report.max_drawdown_pct > max_drawdown_pct):
        return None  # drawdown exceeds operator-configured limit
    return base
```

- [ ] **Step 4: Propagate max_trades through run_sweep()**

In `run_sweep()`, add `max_trades: int = 0` after `max_drawdown_pct`:

```python
def run_sweep(
    *,
    scenario: ReplayScenario,
    base_env: dict[str, str],
    grid: ParameterGrid | None = None,
    min_trades: int = 3,
    max_drawdown_pct: float = 0.0,
    max_trades: int = 0,
    signal_evaluator: "StrategySignalEvaluator | None" = None,
) -> list[TuningCandidate]:
```

Update the `score_report` call inside `run_sweep()`:

```python
        s = (
            score_report(report, min_trades=min_trades, max_drawdown_pct=max_drawdown_pct, max_trades=max_trades)
            if report is not None else None
        )
```

- [ ] **Step 5: Propagate max_trades through run_multi_scenario_sweep()**

Add `max_trades: int = 0` after `max_drawdown_pct` in the signature, and update the `score_report` call inside:

```python
def run_multi_scenario_sweep(
    *,
    scenarios: list[ReplayScenario],
    base_env: dict[str, str],
    grid: ParameterGrid | None = None,
    min_trades_per_scenario: int = 2,
    aggregate: str = "min",
    max_drawdown_pct: float = 0.0,
    max_trades: int = 0,
    signal_evaluator: "StrategySignalEvaluator | None" = None,
    surrogate: "SurrogateModel | None" = None,
) -> list[TuningCandidate]:
```

Update inner `score_report` call:

```python
            s = (
                score_report(report, min_trades=min_trades_per_scenario,
                             max_drawdown_pct=max_drawdown_pct, max_trades=max_trades)
                if report is not None else None
            )
```

- [ ] **Step 6: Propagate max_trades through evaluate_candidates_oos()**

Add `max_trades: int = 0` after `max_drawdown_pct`:

```python
def evaluate_candidates_oos(
    candidates: list[TuningCandidate],
    oos_scenarios: list[ReplayScenario],
    *,
    base_env: dict[str, str],
    min_trades: int,
    aggregate: str = "min",
    max_drawdown_pct: float = 0.0,
    max_trades: int = 0,
    signal_evaluator: "StrategySignalEvaluator | None" = None,
) -> list[float | None]:
```

Update inner `score_report` call:

```python
            s = (
                score_report(report, min_trades=min_trades, max_drawdown_pct=max_drawdown_pct, max_trades=max_trades)
                if report is not None else None
            )
```

- [ ] **Step 7: Run tests to verify Task 1 passes**

Run: `pytest tests/unit/test_tuning_sweep.py -v --tb=short`
Expected: 40 passed (38 existing + 2 new)

- [ ] **Step 8: Commit Task 1**

```bash
git add src/alpaca_bot/tuning/sweep.py tests/unit/test_tuning_sweep.py
git commit -m "feat: add max_trades ceiling gate to score_report() and sweep functions"
```

---

### Task 2: _viability_key composite ranking in sweep.py

**Files:**
- Modify: `src/alpaca_bot/tuning/sweep.py`
- Modify: `tests/unit/test_tuning_sweep.py`

- [ ] **Step 1: Write failing tests for _viability_key**

Append to `tests/unit/test_tuning_sweep.py`:

```python
def test_viability_key_uses_r_multiple_as_tiebreaker() -> None:
    """Candidates with equal IS score: higher R-multiple ranks higher."""
    from alpaca_bot.tuning.sweep import _viability_key

    low_r = TuningCandidate(
        params={},
        score=1.0,
        report=BacktestReport(
            trades=(), total_trades=5, winning_trades=3, losing_trades=2,
            win_rate=0.6, mean_return_pct=0.02, max_drawdown_pct=0.05, sharpe_ratio=1.0,
            avg_win_return_pct=0.02, avg_loss_return_pct=-0.016,  # R ≈ 1.25
        ),
    )
    high_r = TuningCandidate(
        params={},
        score=1.0,
        report=BacktestReport(
            trades=(), total_trades=5, winning_trades=3, losing_trades=2,
            win_rate=0.6, mean_return_pct=0.02, max_drawdown_pct=0.05, sharpe_ratio=1.0,
            avg_win_return_pct=0.05, avg_loss_return_pct=-0.014,  # R ≈ 3.57
        ),
    )
    assert _viability_key(high_r) > _viability_key(low_r)


def test_viability_key_no_losers_gets_high_r() -> None:
    """When avg_loss_return_pct is None (all wins), R defaults to 10.0."""
    from alpaca_bot.tuning.sweep import _viability_key

    all_wins = TuningCandidate(
        params={},
        score=1.0,
        report=BacktestReport(
            trades=(), total_trades=5, winning_trades=5, losing_trades=0,
            win_rate=1.0, mean_return_pct=0.05, max_drawdown_pct=0.0, sharpe_ratio=1.0,
            avg_win_return_pct=0.05, avg_loss_return_pct=None,
        ),
    )
    has_losses = TuningCandidate(
        params={},
        score=1.0,
        report=BacktestReport(
            trades=(), total_trades=5, winning_trades=3, losing_trades=2,
            win_rate=0.6, mean_return_pct=0.02, max_drawdown_pct=0.05, sharpe_ratio=1.0,
            avg_win_return_pct=0.04, avg_loss_return_pct=-0.02,  # R = 2.0
        ),
    )
    # No-loser candidate should beat the R=2 candidate on the tiebreaker
    assert _viability_key(all_wins) > _viability_key(has_losses)


def test_viability_key_none_score_sorts_last() -> None:
    """Candidate with score=None must sort below any scored candidate."""
    from alpaca_bot.tuning.sweep import _viability_key

    unscored = TuningCandidate(params={}, score=None, report=None)
    scored = TuningCandidate(
        params={},
        score=0.001,
        report=BacktestReport(
            trades=(), total_trades=3, winning_trades=2, losing_trades=1,
            win_rate=0.67, mean_return_pct=0.001, max_drawdown_pct=0.01, sharpe_ratio=0.001,
        ),
    )
    assert _viability_key(scored) > _viability_key(unscored)


def test_viability_key_with_oos_score_uses_oos_as_primary() -> None:
    """When oos_score is supplied, it replaces IS score as the primary criterion."""
    from alpaca_bot.tuning.sweep import _viability_key

    low_is_high_oos = TuningCandidate(
        params={},
        score=0.5,
        report=BacktestReport(
            trades=(), total_trades=5, winning_trades=3, losing_trades=2,
            win_rate=0.6, mean_return_pct=0.02, max_drawdown_pct=0.05, sharpe_ratio=0.5,
        ),
    )
    high_is_low_oos = TuningCandidate(
        params={},
        score=1.0,
        report=BacktestReport(
            trades=(), total_trades=5, winning_trades=3, losing_trades=2,
            win_rate=0.6, mean_return_pct=0.02, max_drawdown_pct=0.05, sharpe_ratio=1.0,
        ),
    )
    # OOS scores override: low_is_high_oos wins because its oos_score=0.9 > 0.3
    assert _viability_key(low_is_high_oos, oos_score=0.9) > _viability_key(high_is_low_oos, oos_score=0.3)
```

Run: `pytest tests/unit/test_tuning_sweep.py -k "viability" -v`
Expected: FAIL — ImportError `_viability_key` does not exist yet

- [ ] **Step 2: Implement _viability_key in sweep.py**

Add after `TuningCandidate` dataclass definition (before `_parse_grid`):

```python
def _viability_key(
    candidate: TuningCandidate,
    oos_score: float | None = None,
) -> tuple:
    """Composite sort key: (has_score, score, r_multiple, win_rate).

    Lexicographic comparison — Sharpe/OOS score dominates, R-multiple and win_rate
    are tie-breakers. None-scored candidates always sort last (has_score=False).
    """
    has_score = candidate.score is not None
    score = oos_score if oos_score is not None else (candidate.score or 0.0)
    report = candidate.report
    r_multiple = 0.0
    win_rate = 0.0
    if report is not None:
        if (report.avg_win_return_pct is not None
                and report.avg_loss_return_pct is not None
                and report.avg_loss_return_pct != 0.0):
            r_multiple = report.avg_win_return_pct / abs(report.avg_loss_return_pct)
        elif report.avg_loss_return_pct is None and report.avg_win_return_pct is not None:
            r_multiple = 10.0  # no losers: cap at high sentinel to reward pure-win streaks
        win_rate = report.win_rate or 0.0
    return (has_score, score, r_multiple, win_rate)
```

- [ ] **Step 3: Replace sort lambdas in run_sweep() and run_multi_scenario_sweep()**

In `run_sweep()`, replace:
```python
    return sorted(
        candidates,
        key=lambda c: (c.score is not None, c.score or 0.0),
        reverse=True,
    )
```
with:
```python
    return sorted(candidates, key=_viability_key, reverse=True)
```

In `run_multi_scenario_sweep()`, replace the same lambda with:
```python
    return sorted(candidates, key=_viability_key, reverse=True)
```

- [ ] **Step 4: Run all sweep tests**

Run: `pytest tests/unit/test_tuning_sweep.py -v --tb=short`
Expected: 44 passed (40 from Task 1 + 4 new viability tests)

- [ ] **Step 5: Commit Task 2**

```bash
git add src/alpaca_bot/tuning/sweep.py tests/unit/test_tuning_sweep.py
git commit -m "feat: add _viability_key composite ranking to sweep sort (R-multiple tiebreaker)"
```

---

### Task 3: Wire max_trades and _viability_key into tuning/cli.py

**Files:**
- Modify: `src/alpaca_bot/tuning/cli.py`
- Modify: `tests/unit/test_tuning_sweep_cli.py`

- [ ] **Step 1: Update existing fake_oos signatures in test_tuning_sweep_cli.py**

Two existing functions (in `test_walk_forward_gate_selects_best_oos_held_candidate` and `test_walk_forward_gate_exits_nonzero_when_no_held_candidates`) need `max_trades=0` added:

```python
def fake_oos(candidates, oos_scenarios, *, base_env, min_trades, aggregate,
             max_drawdown_pct=0.0, max_trades=0, signal_evaluator=None):
```

Run: `pytest tests/unit/test_tuning_sweep_cli.py -v --tb=short`
Expected: all existing tests pass (no logic change, just future-safe signatures)

- [ ] **Step 2: Write failing test for --max-trades forwarding**

Append to `tests/unit/test_tuning_sweep_cli.py`:

```python
def test_evolve_max_trades_passed_to_sweep(monkeypatch, tmp_path):
    """--max-trades 5 must be forwarded to run_multi_scenario_sweep as max_trades=5."""
    import json
    import pytest
    from alpaca_bot.tuning import cli as module
    from alpaca_bot.tuning.sweep import TuningCandidate

    _patch_env(monkeypatch)
    for name in ("SYM_A_252d.json", "SYM_B_252d.json"):
        (tmp_path / name).write_text(json.dumps({
            "name": name.replace(".json", ""), "symbol": "SYM",
            "starting_equity": 100000.0, "daily_bars": [], "intraday_bars": [],
        }))

    received_kw: dict = {}

    def fake_sweep(**kw):
        received_kw.update(kw)
        return [TuningCandidate(params={"BREAKOUT_LOOKBACK_BARS": "20"}, report=None, score=0.5)]

    monkeypatch.setattr(module, "run_multi_scenario_sweep", fake_sweep)
    monkeypatch.setattr(sys, "argv", [
        "evolve", "--scenario-dir", str(tmp_path), "--no-db",
        "--max-trades", "5",
    ])
    module.main()
    assert received_kw.get("max_trades") == 5
```

Run: `pytest tests/unit/test_tuning_sweep_cli.py::test_evolve_max_trades_passed_to_sweep -v`
Expected: FAIL — AttributeError or assertion error (max_trades not in received_kw)

- [ ] **Step 3: Add --max-trades arg to tuning/cli.py**

After the `--max-drawdown-pct` argument:

```python
    parser.add_argument("--max-trades", type=int, default=0,
                        help="Maximum trades per scenario to accept a candidate (0 = disabled)")
```

- [ ] **Step 4: Wire max_trades into all three call sites in tuning/cli.py**

In `run_sweep()` call (single-scenario path):
```python
        candidates = run_sweep(
            scenario=scenario,
            base_env=base_env,
            grid=grid,
            min_trades=args.min_trades,
            max_drawdown_pct=args.max_drawdown_pct,
            max_trades=args.max_trades,
            signal_evaluator=signal_evaluator,
        )
```

In `run_multi_scenario_sweep()` call:
```python
        candidates = run_multi_scenario_sweep(
            scenarios=sweep_scenarios,
            base_env=base_env,
            grid=grid,
            min_trades_per_scenario=args.min_trades,
            aggregate=args.aggregate,
            max_drawdown_pct=args.max_drawdown_pct,
            max_trades=args.max_trades,
            signal_evaluator=signal_evaluator,
        )
```

In `evaluate_candidates_oos()` call:
```python
        oos_scores = evaluate_candidates_oos(
            candidates=top10,
            oos_scenarios=oos_scenarios,
            base_env=base_env,
            min_trades=args.min_trades,
            aggregate=args.aggregate,
            max_drawdown_pct=args.max_drawdown_pct,
            max_trades=args.max_trades,
            signal_evaluator=signal_evaluator,
        )
```

- [ ] **Step 5: Import _viability_key and update WF max() selection**

In the imports at the top of `tuning/cli.py`, add `_viability_key` to the sweep import:

```python
from alpaca_bot.tuning.sweep import (
    DEFAULT_GRID,
    STRATEGY_GRIDS,
    ParameterGrid,
    TuningCandidate,
    _viability_key,
    evaluate_candidates_oos,
    run_multi_scenario_sweep,
    run_sweep,
)
```

Update the WF best-selection line (currently `best = max(held_pairs, key=lambda pair: pair[1])[0]`):

```python
        best = max(held_pairs, key=lambda pair: _viability_key(pair[0], pair[1]))[0]
```

- [ ] **Step 6: Run tests**

Run: `pytest tests/unit/test_tuning_sweep_cli.py -v --tb=short`
Expected: all tests pass including new `test_evolve_max_trades_passed_to_sweep`

- [ ] **Step 7: Commit Task 3**

```bash
git add src/alpaca_bot/tuning/cli.py tests/unit/test_tuning_sweep_cli.py
git commit -m "feat: add --max-trades to evolve CLI; use _viability_key for WF candidate selection"
```

---

### Task 4: Wire max_trades and _viability_key into nightly/cli.py

**Files:**
- Modify: `src/alpaca_bot/nightly/cli.py`
- Modify: `tests/unit/test_nightly_cli.py`

- [ ] **Step 1: Write failing test for nightly viability tiebreak**

Append to `tests/unit/test_nightly_cli.py`:

```python
def test_nightly_viability_tiebreak_picks_higher_r(monkeypatch, tmp_path):
    """When two held candidates have equal OOS score, nightly CLI picks the one with higher R-multiple."""
    from alpaca_bot.nightly import cli as module
    from alpaca_bot.tuning.sweep import TuningCandidate
    from alpaca_bot.replay.report import BacktestReport

    _patch_env(monkeypatch)
    _make_scenario_files(tmp_path)
    _patch_common_db(monkeypatch, module)
    monkeypatch.setattr(module, "split_scenario", _fake_split)

    # Both candidates have IS score=0.5; cand_high_r has R ≈ 3.57, cand_low_r has R ≈ 1.25
    low_r_report = BacktestReport(
        trades=(), total_trades=5, winning_trades=3, losing_trades=2,
        win_rate=0.6, mean_return_pct=0.02, max_drawdown_pct=0.05, sharpe_ratio=0.5,
        avg_win_return_pct=0.02, avg_loss_return_pct=-0.016,
    )
    high_r_report = BacktestReport(
        trades=(), total_trades=5, winning_trades=3, losing_trades=2,
        win_rate=0.6, mean_return_pct=0.02, max_drawdown_pct=0.05, sharpe_ratio=0.5,
        avg_win_return_pct=0.05, avg_loss_return_pct=-0.014,
    )
    cand_low_r = TuningCandidate(
        params={"BREAKOUT_LOOKBACK_BARS": "15"}, report=low_r_report, score=0.5
    )
    cand_high_r = TuningCandidate(
        params={"BREAKOUT_LOOKBACK_BARS": "30"}, report=high_r_report, score=0.5
    )

    # low_r is ranked first by IS (same score, but listed first — old code would pick it)
    monkeypatch.setattr(module, "run_multi_scenario_sweep", lambda **kw: [cand_low_r, cand_high_r])
    # Both held with equal OOS score=0.4
    monkeypatch.setattr(module, "evaluate_candidates_oos",
                        lambda candidates, oos_scenarios, **kw: [0.4, 0.4])

    output_env = tmp_path / "candidate.env"
    monkeypatch.setattr(sys, "argv", [
        "nightly", "--dry-run", "--no-db",
        "--output-dir", str(tmp_path),
        "--output-env", str(output_env),
        "--min-oos-score", "0.0",  # ensure both pass the floor
    ])

    result = module.main()

    assert result == 0
    assert output_env.exists()
    text = output_env.read_text()
    assert "BREAKOUT_LOOKBACK_BARS=30" in text, (
        "high-R candidate (lookback=30) must win over low-R candidate (lookback=15)"
    )
```

Run: `pytest tests/unit/test_nightly_cli.py::test_nightly_viability_tiebreak_picks_higher_r -v`
Expected: FAIL — assertion error (old code picks first held by OOS only; equal OOS → first wins, which is cand_low_r)

- [ ] **Step 2: Add --max-trades arg to nightly/cli.py**

After `--max-drawdown-pct`:

```python
    parser.add_argument("--max-trades", type=int, default=0,
                        help="Maximum trades per scenario to accept a candidate (0 = disabled)")
```

- [ ] **Step 3: Wire max_trades into run_multi_scenario_sweep and evaluate_candidates_oos in nightly/cli.py**

In `run_multi_scenario_sweep()` call:
```python
            candidates = run_multi_scenario_sweep(
                scenarios=is_scenarios,
                base_env=base_env,
                grid=grid,
                max_drawdown_pct=args.max_drawdown_pct,
                max_trades=args.max_trades,
                signal_evaluator=signal_evaluator,
                surrogate=surrogate,
            )
```

In `evaluate_candidates_oos()` call:
```python
            oos_scores = evaluate_candidates_oos(
                candidates=top10,
                oos_scenarios=oos_scenarios,
                base_env=base_env,
                min_trades=3,
                max_drawdown_pct=args.max_drawdown_pct,
                max_trades=args.max_trades,
                signal_evaluator=signal_evaluator,
            )
```

- [ ] **Step 4: Import _viability_key and update WF max() selection in nightly/cli.py**

Add `_viability_key` to the sweep import:

```python
from alpaca_bot.tuning.sweep import (
    DEFAULT_GRID,
    STRATEGY_GRIDS,
    _viability_key,
    evaluate_candidates_oos,
    run_multi_scenario_sweep,
)
```

Update the WF best-selection line (currently `best = max(held_pairs, key=lambda pair: pair[1])[0]`):

```python
                best = max(held_pairs, key=lambda pair: _viability_key(pair[0], pair[1]))[0]
```

- [ ] **Step 5: Run tests**

Run: `pytest tests/unit/test_nightly_cli.py -v --tb=short`
Expected: all 9 tests pass including new viability tiebreak test

- [ ] **Step 6: Run full affected test suites**

Run: `pytest tests/unit/test_tuning_sweep.py tests/unit/test_tuning_sweep_cli.py tests/unit/test_nightly_cli.py -v --tb=short`
Expected: all tests pass

- [ ] **Step 7: Commit Task 4**

```bash
git add src/alpaca_bot/nightly/cli.py tests/unit/test_nightly_cli.py
git commit -m "feat: add --max-trades to nightly CLI; use _viability_key for WF candidate selection"
```

---

### Task 5: Final regression check

**Files:** none (read-only verification)

- [ ] **Step 1: Full test suite**

Run: `pytest -q --tb=short`
Expected: ≥1111 tests pass, 0 failures

- [ ] **Step 2: Verify --help for both CLIs**

Run:
```bash
alpaca-bot-evolve --help | grep max-trades
alpaca-bot-nightly --help | grep max-trades
```
Expected: both show `--max-trades MAX_TRADES`
