# Tighten Gate Defaults and Add Max Drawdown Gate — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a configurable max drawdown gate to IS/OOS scoring and tighten the nightly pipeline's default OOS thresholds to the values the profitability gate spec recommended.

**Architecture:** Four surgical changes — `score_report()` gains `max_drawdown_pct` kwarg; that kwarg propagates through `run_sweep`, `run_multi_scenario_sweep`, and `evaluate_candidates_oos` with default `0.0` (disabled); both CLIs expose `--max-drawdown-pct`; `nightly/cli.py` defaults for `--min-oos-score` and `--oos-gate-ratio` are tightened to `0.2` / `0.6`. No schema changes, no Settings changes, no engine changes.

**Tech Stack:** Python, argparse, pytest, existing test helpers in `test_tuning_sweep.py`, `test_tuning_sweep_cli.py`, `test_nightly_cli.py`.

---

### Task 1: Add `max_drawdown_pct` gate to `score_report()` and propagate through sweep functions

**Files:**
- Modify: `src/alpaca_bot/tuning/sweep.py`
- Modify: `tests/unit/test_tuning_sweep.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_tuning_sweep.py`:

```python
def test_score_report_disqualifies_on_excessive_drawdown() -> None:
    report = BacktestReport(
        trades=(), total_trades=5, winning_trades=3, losing_trades=2,
        win_rate=0.6, mean_return_pct=0.02, max_drawdown_pct=0.25, sharpe_ratio=0.5,
    )
    # 0.25 > 0.20 → disqualified
    assert score_report(report, min_trades=3, max_drawdown_pct=0.20) is None
    # 0.25 <= 0.30 → passes gate
    assert score_report(report, min_trades=3, max_drawdown_pct=0.30) is not None
    # default 0.0 → gate disabled, passes
    assert score_report(report, min_trades=3) is not None


def test_score_report_drawdown_gate_skips_none_drawdown() -> None:
    report = BacktestReport(
        trades=(), total_trades=5, winning_trades=5, losing_trades=0,
        win_rate=1.0, mean_return_pct=0.05, max_drawdown_pct=None, sharpe_ratio=0.5,
    )
    # max_drawdown_pct is None in report → cannot enforce gate → should NOT disqualify
    assert score_report(report, min_trades=3, max_drawdown_pct=0.10) is not None
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/unit/test_tuning_sweep.py::test_score_report_disqualifies_on_excessive_drawdown tests/unit/test_tuning_sweep.py::test_score_report_drawdown_gate_skips_none_drawdown -v
```

Expected: FAIL — `TypeError: score_report() got an unexpected keyword argument 'max_drawdown_pct'`

- [ ] **Step 3: Update `score_report()` signature and add drawdown check**

In `src/alpaca_bot/tuning/sweep.py`, replace lines 103–123:

```python
def score_report(
    report: BacktestReport,
    *,
    min_trades: int = 3,
    max_drawdown_pct: float = 0.0,
) -> float | None:
    """Sharpe-first composite score; None if disqualified.

    Disqualified when: fewer than min_trades, profit_factor < 1.0 (net-losing),
    base score ≤ 0 (non-positive Sharpe/Calmar), or drawdown exceeds max_drawdown_pct
    (when max_drawdown_pct > 0.0 and report.max_drawdown_pct is not None).
    profit_factor=None (no losses at all) is never penalised.
    """
    if report.total_trades < min_trades:
        return None
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

- [ ] **Step 4: Propagate `max_drawdown_pct` through `run_sweep()`**

In `src/alpaca_bot/tuning/sweep.py`, update `run_sweep` signature and body:

```python
def run_sweep(
    *,
    scenario: ReplayScenario,
    base_env: dict[str, str],
    grid: ParameterGrid | None = None,
    min_trades: int = 3,
    max_drawdown_pct: float = 0.0,
    signal_evaluator: "StrategySignalEvaluator | None" = None,
) -> list[TuningCandidate]:
    """Run a parameter grid sweep over `scenario`.

    Returns candidates sorted descending by score (scored first, then unscored).
    """
    effective_grid = grid if grid is not None else DEFAULT_GRID
    keys = list(effective_grid.keys())
    value_lists = [effective_grid[k] for k in keys]

    candidates: list[TuningCandidate] = []
    for combo in itertools.product(*value_lists):
        overrides = dict(zip(keys, combo))
        merged_env = {**base_env, **overrides}
        try:
            settings = Settings.from_env(merged_env)
        except ValueError:
            continue  # invalid combination — skip silently

        runner = ReplayRunner(settings, signal_evaluator=signal_evaluator)
        result = runner.run(scenario)
        report: BacktestReport | None = result.backtest_report  # type: ignore[assignment]
        s = (
            score_report(report, min_trades=min_trades, max_drawdown_pct=max_drawdown_pct)
            if report is not None
            else None
        )
        candidates.append(TuningCandidate(params=overrides, report=report, score=s))

    return sorted(
        candidates,
        key=lambda c: (c.score is not None, c.score or 0.0),
        reverse=True,
    )
```

- [ ] **Step 5: Propagate `max_drawdown_pct` through `run_multi_scenario_sweep()`**

In `src/alpaca_bot/tuning/sweep.py`, update `run_multi_scenario_sweep` signature (add `max_drawdown_pct: float = 0.0` after `aggregate`) and body:

Replace the `score_report` call at line 258:
```python
            s = (
                score_report(report, min_trades=min_trades_per_scenario, max_drawdown_pct=max_drawdown_pct)
                if report is not None
                else None
            )
```

- [ ] **Step 6: Propagate `max_drawdown_pct` through `evaluate_candidates_oos()`**

In `src/alpaca_bot/tuning/sweep.py`, update `evaluate_candidates_oos` signature (add `max_drawdown_pct: float = 0.0` after `signal_evaluator`) and body:

Replace the `score_report` call at line 308:
```python
            s = (
                score_report(report, min_trades=min_trades, max_drawdown_pct=max_drawdown_pct)
                if report is not None
                else None
            )
```

- [ ] **Step 7: Run tests to verify they pass**

```bash
pytest tests/unit/test_tuning_sweep.py::test_score_report_disqualifies_on_excessive_drawdown tests/unit/test_tuning_sweep.py::test_score_report_drawdown_gate_skips_none_drawdown -v
```

Expected: PASS

- [ ] **Step 8: Run full sweep test suite**

```bash
pytest tests/unit/test_tuning_sweep.py -v
```

Expected: all tests pass

- [ ] **Step 9: Commit**

```bash
git add src/alpaca_bot/tuning/sweep.py tests/unit/test_tuning_sweep.py
git commit -m "feat: add max_drawdown_pct gate to score_report() and propagate through sweep functions"
```

---

### Task 2: Add `--max-drawdown-pct` to both CLIs and tighten nightly defaults

**Files:**
- Modify: `src/alpaca_bot/tuning/cli.py`
- Modify: `src/alpaca_bot/nightly/cli.py`
- Modify: `tests/unit/test_tuning_sweep_cli.py`
- Modify: `tests/unit/test_nightly_cli.py`

- [ ] **Step 1: Fix existing `fake_oos` signatures in `test_tuning_sweep_cli.py`**

Two existing tests define `fake_oos` with explicit keyword-only args that will break once
`max_drawdown_pct` is added to the `evaluate_candidates_oos` call in `cli.py`.
Update both to accept the new kwarg:

In `test_walk_forward_gate_selects_best_oos_held_candidate`, change:
```python
    def fake_oos(candidates, oos_scenarios, *, base_env, min_trades, aggregate, signal_evaluator=None):
```
To:
```python
    def fake_oos(candidates, oos_scenarios, *, base_env, min_trades, aggregate, max_drawdown_pct=0.0, signal_evaluator=None):
```

In `test_walk_forward_gate_exits_nonzero_when_no_held_candidates`, make the identical change to its `fake_oos`.

Verify: `pytest tests/unit/test_tuning_sweep_cli.py::test_walk_forward_gate_selects_best_oos_held_candidate tests/unit/test_tuning_sweep_cli.py::test_walk_forward_gate_exits_nonzero_when_no_held_candidates -v` → both still PASS (behavior unchanged, just signature extended).

- [ ] **Step 2: Write the new failing tests**

Append to `tests/unit/test_tuning_sweep_cli.py`:

```python
def test_evolve_max_drawdown_pct_passed_to_sweep(monkeypatch, tmp_path):
    """--max-drawdown-pct 0.15 must be forwarded to run_multi_scenario_sweep."""
    import json
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
        "--max-drawdown-pct", "0.15",
    ])

    module.main()

    assert received_kw.get("max_drawdown_pct") == pytest.approx(0.15)
```

Append to `tests/unit/test_nightly_cli.py`:

```python
def test_nightly_cli_tighter_defaults_reject_marginal_oos_candidate(monkeypatch, tmp_path):
    """Default oos_gate_ratio=0.6 rejects OOS=0.28/IS=0.5 (ratio 0.56 < 0.6) without explicit flags."""
    from alpaca_bot.nightly import cli as module
    from alpaca_bot.tuning.sweep import TuningCandidate

    _patch_env(monkeypatch)
    _make_scenario_files(tmp_path)
    _patch_common_db(monkeypatch, module)

    monkeypatch.setattr(module, "split_scenario", _fake_split)

    # OOS=0.28, IS=0.5 → ratio 0.28/0.5=0.56 < 0.6 (new default) → not held
    # (with old default 0.5: 0.28 >= 0.25 would pass → candidate would be held)
    cand = TuningCandidate(params={"BREAKOUT_LOOKBACK_BARS": "20"}, report=None, score=0.5)
    monkeypatch.setattr(module, "run_multi_scenario_sweep", lambda **kw: [cand])
    monkeypatch.setattr(module, "evaluate_candidates_oos",
                        lambda candidates, oos_scenarios, **kw: [0.28])

    output_env = tmp_path / "candidate.env"
    monkeypatch.setattr(sys, "argv", [
        "nightly", "--dry-run", "--no-db",
        "--output-dir", str(tmp_path),
        "--output-env", str(output_env),
    ])

    result = module.main()

    assert result == 0  # nightly always returns 0 even with no held candidates
    assert not output_env.exists(), "no candidate env written when OOS/IS ratio < new default 0.6"
```

- [ ] **Step 3: Run new tests to verify they fail**

```bash
pytest tests/unit/test_tuning_sweep_cli.py::test_evolve_max_drawdown_pct_passed_to_sweep tests/unit/test_nightly_cli.py::test_nightly_cli_tighter_defaults_reject_marginal_oos_candidate -v
```

Expected:
- `test_evolve_max_drawdown_pct_passed_to_sweep` FAIL — `error: unrecognized arguments: --max-drawdown-pct`
- `test_nightly_cli_tighter_defaults_reject_marginal_oos_candidate` FAIL — `output_env.exists()` is True (old default 0.5 lets candidate through)

- [ ] **Step 4: Add `--max-drawdown-pct` to `tuning/cli.py` and wire through**

In `src/alpaca_bot/tuning/cli.py`, after the `--oos-gate-ratio` argument (around line 52):

```python
    parser.add_argument("--max-drawdown-pct", type=float, default=0.0,
                        help="Maximum allowed IS/OOS drawdown to accept a candidate (0.0 = disabled)")
```

Update the `run_sweep` call (single-scenario path, around line 72):

```python
        candidates = run_sweep(
            scenario=scenario,
            base_env=base_env,
            grid=grid,
            min_trades=args.min_trades,
            max_drawdown_pct=args.max_drawdown_pct,
            signal_evaluator=signal_evaluator,
        )
```

Update the `run_multi_scenario_sweep` call (multi-scenario path, around line 115):

```python
        candidates = run_multi_scenario_sweep(
            scenarios=sweep_scenarios,
            base_env=base_env,
            grid=grid,
            min_trades_per_scenario=args.min_trades,
            aggregate=args.aggregate,
            max_drawdown_pct=args.max_drawdown_pct,
            signal_evaluator=signal_evaluator,
        )
```

Update the `evaluate_candidates_oos` call (around line 138):

```python
        oos_scores = evaluate_candidates_oos(
            candidates=top10,
            oos_scenarios=oos_scenarios,
            base_env=base_env,
            min_trades=args.min_trades,
            aggregate=args.aggregate,
            max_drawdown_pct=args.max_drawdown_pct,
            signal_evaluator=signal_evaluator,
        )
```

- [ ] **Step 5: Add `--max-drawdown-pct` to `nightly/cli.py`, tighten defaults, and wire through**

In `src/alpaca_bot/nightly/cli.py`, change existing defaults and add the new arg:

Change:
```python
    parser.add_argument("--min-oos-score", type=float, default=0.0,
                        help="Minimum absolute OOS score to accept a candidate (default: 0.0)")
    parser.add_argument("--oos-gate-ratio", type=float, default=0.5,
                        help="Required OOS/IS score ratio to hold a candidate (default: 0.5)")
```

To:
```python
    parser.add_argument("--min-oos-score", type=float, default=0.2,
                        help="Minimum absolute OOS score to accept a candidate (default: 0.2)")
    parser.add_argument("--oos-gate-ratio", type=float, default=0.6,
                        help="Required OOS/IS score ratio to hold a candidate (default: 0.6)")
    parser.add_argument("--max-drawdown-pct", type=float, default=0.0,
                        help="Maximum allowed IS/OOS drawdown to accept a candidate (0.0 = disabled)")
```

Update the `run_multi_scenario_sweep` call (around line 159):

```python
            candidates = run_multi_scenario_sweep(
                scenarios=is_scenarios,
                base_env=base_env,
                grid=grid,
                signal_evaluator=signal_evaluator,
                max_drawdown_pct=args.max_drawdown_pct,
                surrogate=surrogate,
            )
```

Update the `evaluate_candidates_oos` call (around line 172):

```python
            oos_scores = evaluate_candidates_oos(
                candidates=top10,
                oos_scenarios=oos_scenarios,
                base_env=base_env,
                min_trades=3,
                max_drawdown_pct=args.max_drawdown_pct,
                signal_evaluator=signal_evaluator,
            )
```

- [ ] **Step 6: Run tests to verify they pass**

```bash
pytest tests/unit/test_tuning_sweep_cli.py::test_evolve_max_drawdown_pct_passed_to_sweep tests/unit/test_nightly_cli.py::test_nightly_cli_tighter_defaults_reject_marginal_oos_candidate -v
```

Expected: both PASS

- [ ] **Step 7: Run full test suites for all affected files**

```bash
pytest tests/unit/test_tuning_sweep_cli.py tests/unit/test_nightly_cli.py tests/unit/test_tuning_sweep.py -v
```

Expected: all tests pass

- [ ] **Step 8: Commit**

```bash
git add src/alpaca_bot/tuning/cli.py src/alpaca_bot/nightly/cli.py tests/unit/test_tuning_sweep_cli.py tests/unit/test_nightly_cli.py
git commit -m "feat: add --max-drawdown-pct to both CLIs; tighten nightly OOS defaults to 0.2/0.6"
```

---

### Task 3: Final regression check

- [ ] **Step 1: Run full test suite**

```bash
pytest -q --tb=short
```

Expected: all tests pass (≥ 1107)

- [ ] **Step 2: Verify CLI help shows new flag**

```bash
alpaca-bot-nightly --help | grep -E "max-drawdown|min-oos|oos-gate"
alpaca-bot-evolve --help | grep -E "max-drawdown|min-oos|oos-gate"
```

Expected: all three flags appear in both outputs

- [ ] **Step 3: Commit (if any cleanup needed)**

Only if there are outstanding uncommitted changes:

```bash
git add -p
git commit -m "chore: final cleanup for tighten-gates feature"
```
