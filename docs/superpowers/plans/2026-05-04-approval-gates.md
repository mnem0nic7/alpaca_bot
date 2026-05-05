# Approval Gates — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Block net-losing and overfit parameter sets from being approved by `alpaca-bot-evolve`.

**Architecture:** Two targeted changes: (1) harden `score_report()` gates in `tuning/sweep.py`; (2) make walk-forward validation affect candidate selection in `tuning/cli.py`.

**Tech Stack:** Pure Python stdlib. No new dependencies, no migrations, no env vars.

---

### Task 1: Harden `score_report()` — profit factor gate and score floor

**Files:**
- Modify: `src/alpaca_bot/tuning/sweep.py:102-119`
- Test: `tests/unit/test_tuning_sweep.py`

- [ ] **Step 1: Run the existing penalty test to confirm current behavior**

```bash
pytest tests/unit/test_tuning_sweep.py::test_score_report_penalizes_subunit_profit_factor -v
```

Expected: PASS (currently returns 1.4)

- [ ] **Step 2: Update the existing penalty test to expect disqualification**

In `tests/unit/test_tuning_sweep.py`, replace the `test_score_report_penalizes_subunit_profit_factor` test body:

```python
def test_score_report_penalizes_subunit_profit_factor() -> None:
    """profit_factor < 1.0 is now a hard disqualifier, not a score penalty."""
    report = BacktestReport(
        trades=(), total_trades=5, winning_trades=3, losing_trades=2,
        win_rate=0.6, mean_return_pct=0.02, max_drawdown_pct=0.05,
        sharpe_ratio=2.0, profit_factor=0.7,
    )
    assert score_report(report, min_trades=3) is None
```

- [ ] **Step 3: Add new score-floor and gate tests at the end of the `# score_report: profit_factor penalty` block**

Append after `test_score_report_no_penalty_when_profit_factor_none` (around line 348):

```python
def test_score_report_profit_factor_below_one_disqualifies() -> None:
    """Any profit_factor strictly below 1.0 → None (hard gate)."""
    for pf in (0.99, 0.5, 0.01):
        report = BacktestReport(
            trades=(), total_trades=5, winning_trades=3, losing_trades=2,
            win_rate=0.6, mean_return_pct=0.02, max_drawdown_pct=0.05,
            sharpe_ratio=2.0, profit_factor=pf,
        )
        assert score_report(report, min_trades=3) is None, f"Expected None for profit_factor={pf}"


def test_score_report_nonpositive_sharpe_disqualifies() -> None:
    """Sharpe ≤ 0 is disqualified by the score floor."""
    for sharpe in (0.0, -0.5, -2.0):
        report = BacktestReport(
            trades=(), total_trades=5, winning_trades=3, losing_trades=2,
            win_rate=0.6, mean_return_pct=0.02, max_drawdown_pct=0.05,
            sharpe_ratio=sharpe, profit_factor=1.5,
        )
        assert score_report(report, min_trades=3) is None, f"Expected None for sharpe={sharpe}"


def test_score_report_positive_sharpe_above_floor_passes() -> None:
    """Any Sharpe > 0 with profit_factor ≥ 1.0 passes the gates."""
    report = BacktestReport(
        trades=(), total_trades=5, winning_trades=3, losing_trades=2,
        win_rate=0.6, mean_return_pct=0.02, max_drawdown_pct=0.05,
        sharpe_ratio=0.001, profit_factor=1.0,
    )
    result = score_report(report, min_trades=3)
    assert result is not None
    assert result > 0.0
```

- [ ] **Step 4: Run the new tests to verify they fail (TDD)**

```bash
pytest tests/unit/test_tuning_sweep.py::test_score_report_penalizes_subunit_profit_factor tests/unit/test_tuning_sweep.py::test_score_report_profit_factor_below_one_disqualifies tests/unit/test_tuning_sweep.py::test_score_report_nonpositive_sharpe_disqualifies tests/unit/test_tuning_sweep.py::test_score_report_positive_sharpe_above_floor_passes -v
```

Expected: FAIL for all four (the first because of the changed assertion, others because the gates don't exist yet)

- [ ] **Step 5: Implement the gates in `score_report()`**

Replace `score_report` in `src/alpaca_bot/tuning/sweep.py`:

```python
def score_report(report: BacktestReport, *, min_trades: int = 3) -> float | None:
    """Sharpe-first composite score; None if disqualified.

    Disqualified when: fewer than min_trades, profit_factor < 1.0 (net-losing),
    or base score ≤ 0 (non-positive Sharpe/Calmar).
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
    return base
```

- [ ] **Step 6: Run the four tests to verify they pass**

```bash
pytest tests/unit/test_tuning_sweep.py::test_score_report_penalizes_subunit_profit_factor tests/unit/test_tuning_sweep.py::test_score_report_profit_factor_below_one_disqualifies tests/unit/test_tuning_sweep.py::test_score_report_nonpositive_sharpe_disqualifies tests/unit/test_tuning_sweep.py::test_score_report_positive_sharpe_above_floor_passes -v
```

Expected: all PASS

- [ ] **Step 7: Run the full sweep test suite to catch regressions**

```bash
pytest tests/unit/test_tuning_sweep.py -v
```

Expected: all PASS

- [ ] **Step 8: Commit**

```bash
git add src/alpaca_bot/tuning/sweep.py tests/unit/test_tuning_sweep.py
git commit -m "feat: harden score_report() — profit_factor < 1.0 and Sharpe ≤ 0 now disqualify"
```

---

### Task 2: Walk-forward gate — select from OOS-held candidates

**Files:**
- Modify: `src/alpaca_bot/tuning/cli.py:125-168`
- Test: `tests/unit/test_tuning_sweep_cli.py`

- [ ] **Step 1: Write the two new failing CLI tests**

Append to the end of `tests/unit/test_tuning_sweep_cli.py`:

```python
def test_walk_forward_gate_selects_best_oos_held_candidate(monkeypatch, tmp_path):
    """When --validate-pct is used, best candidate is the highest-OOS-scoring held one."""
    import json
    from alpaca_bot.tuning import cli as module
    from alpaca_bot.replay.runner import ReplayScenario
    from alpaca_bot.replay.report import BacktestReport
    from alpaca_bot.tuning.sweep import TuningCandidate

    _patch_env(monkeypatch)

    for name in ("SYM_A_252d.json", "SYM_B_252d.json"):
        (tmp_path / name).write_text(json.dumps({
            "name": name.replace(".json", ""), "symbol": "SYM", "starting_equity": 100000.0,
            "daily_bars": [], "intraday_bars": [],
        }))

    cand_0 = TuningCandidate(params={"BREAKOUT_LOOKBACK_BARS": "20"}, report=None, score=0.5)
    cand_1 = TuningCandidate(params={"BREAKOUT_LOOKBACK_BARS": "25"}, report=None, score=0.4)
    cand_2 = TuningCandidate(params={"BREAKOUT_LOOKBACK_BARS": "30"}, report=None, score=0.3)

    def fake_split(scenario, *, in_sample_ratio):
        is_s = ReplayScenario(name=scenario.name + "_is", symbol=scenario.symbol,
                              starting_equity=scenario.starting_equity,
                              daily_bars=[], intraday_bars=[])
        oos_s = ReplayScenario(name=scenario.name + "_oos", symbol=scenario.symbol,
                               starting_equity=scenario.starting_equity,
                               daily_bars=[], intraday_bars=[])
        return is_s, oos_s

    def fake_run_multi(**kwargs):
        return [cand_0, cand_1, cand_2]

    def fake_oos(candidates, oos_scenarios, *, base_env, min_trades, aggregate, signal_evaluator=None):
        # cand_0: OOS=0.4 → held (0.4 >= 0.5*0.5=0.25) ✓
        # cand_1: OOS=0.1 → not held (0.1 < 0.4*0.5=0.2) ✗
        # cand_2: OOS=None → not held ✗
        return [0.4, 0.1, None]

    output_env = tmp_path / "out.env"
    monkeypatch.setattr(module, "split_scenario", fake_split)
    monkeypatch.setattr(module, "run_multi_scenario_sweep", fake_run_multi)
    monkeypatch.setattr(module, "evaluate_candidates_oos", fake_oos)
    monkeypatch.setattr(sys, "argv", [
        "evolve", "--scenario-dir", str(tmp_path),
        "--validate-pct", "0.2", "--no-db",
        "--output-env", str(output_env),
    ])

    result = module.main()

    assert result == 0
    env_content = output_env.read_text()
    assert "BREAKOUT_LOOKBACK_BARS=20" in env_content  # cand_0, highest OOS score


def test_walk_forward_gate_exits_nonzero_when_no_held_candidates(monkeypatch, tmp_path):
    """When --validate-pct is used and no candidate holds in OOS, main() returns 1."""
    import json
    from alpaca_bot.tuning import cli as module
    from alpaca_bot.replay.runner import ReplayScenario
    from alpaca_bot.tuning.sweep import TuningCandidate

    _patch_env(monkeypatch)

    for name in ("SYM_A_252d.json", "SYM_B_252d.json"):
        (tmp_path / name).write_text(json.dumps({
            "name": name.replace(".json", ""), "symbol": "SYM", "starting_equity": 100000.0,
            "daily_bars": [], "intraday_bars": [],
        }))

    cand_0 = TuningCandidate(params={"BREAKOUT_LOOKBACK_BARS": "20"}, report=None, score=0.5)
    cand_1 = TuningCandidate(params={"BREAKOUT_LOOKBACK_BARS": "25"}, report=None, score=0.4)

    def fake_split(scenario, *, in_sample_ratio):
        is_s = ReplayScenario(name=scenario.name + "_is", symbol=scenario.symbol,
                              starting_equity=scenario.starting_equity,
                              daily_bars=[], intraday_bars=[])
        oos_s = ReplayScenario(name=scenario.name + "_oos", symbol=scenario.symbol,
                               starting_equity=scenario.starting_equity,
                               daily_bars=[], intraday_bars=[])
        return is_s, oos_s

    def fake_run_multi(**kwargs):
        return [cand_0, cand_1]

    def fake_oos(candidates, oos_scenarios, *, base_env, min_trades, aggregate, signal_evaluator=None):
        # cand_0: OOS=0.2 → not held (0.2 < 0.5*0.5=0.25) ✗
        # cand_1: OOS=None → not held ✗
        return [0.2, None]

    monkeypatch.setattr(module, "split_scenario", fake_split)
    monkeypatch.setattr(module, "run_multi_scenario_sweep", fake_run_multi)
    monkeypatch.setattr(module, "evaluate_candidates_oos", fake_oos)
    monkeypatch.setattr(sys, "argv", [
        "evolve", "--scenario-dir", str(tmp_path),
        "--validate-pct", "0.2", "--no-db",
    ])

    result = module.main()

    assert result == 1
```

- [ ] **Step 2: Run the new tests to verify they fail**

```bash
pytest tests/unit/test_tuning_sweep_cli.py::test_walk_forward_gate_selects_best_oos_held_candidate tests/unit/test_tuning_sweep_cli.py::test_walk_forward_gate_exits_nonzero_when_no_held_candidates -v
```

Expected: FAIL (walk-forward gate not yet implemented)

- [ ] **Step 3: Implement the walk-forward gate in `cli.py`**

In `src/alpaca_bot/tuning/cli.py`, replace the walk-forward block in `main()`. The current block is:

```python
    if validate_pct > 0.0 and oos_scenarios:
        top10 = scored[:10]
        oos_scores = evaluate_candidates_oos(
            candidates=top10,
            oos_scenarios=oos_scenarios,
            base_env=base_env,
            min_trades=args.min_trades,
            aggregate=args.aggregate,
            signal_evaluator=signal_evaluator,
        )
        _print_walk_forward_block(top10, oos_scores, validate_pct=validate_pct, aggregate=args.aggregate)
```

Replace with:

```python
    if validate_pct > 0.0 and oos_scenarios:
        top10 = scored[:10]
        oos_scores = evaluate_candidates_oos(
            candidates=top10,
            oos_scenarios=oos_scenarios,
            base_env=base_env,
            min_trades=args.min_trades,
            aggregate=args.aggregate,
            signal_evaluator=signal_evaluator,
        )
        _print_walk_forward_block(top10, oos_scores, validate_pct=validate_pct, aggregate=args.aggregate)
        held_pairs = [
            (c, s) for c, s in zip(top10, oos_scores)
            if s is not None and c.score is not None and s >= c.score * 0.5
        ]
        if not held_pairs:
            print("\nNo walk-forward held candidates — approval gate blocked all.")
            return 1
        best = max(held_pairs, key=lambda pair: pair[1])[0]
```

- [ ] **Step 4: Run the new tests to verify they pass**

```bash
pytest tests/unit/test_tuning_sweep_cli.py::test_walk_forward_gate_selects_best_oos_held_candidate tests/unit/test_tuning_sweep_cli.py::test_walk_forward_gate_exits_nonzero_when_no_held_candidates -v
```

Expected: PASS

- [ ] **Step 5: Run the full CLI test suite to catch regressions**

```bash
pytest tests/unit/test_tuning_sweep_cli.py -v
```

Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add src/alpaca_bot/tuning/cli.py tests/unit/test_tuning_sweep_cli.py
git commit -m "feat: walk-forward gate — select from OOS-held candidates only when --validate-pct used"
```

---

### Task 3: Full regression

- [ ] **Step 1: Run all tests**

```bash
pytest
```

Expected: all PASS (previous count was 1085)

- [ ] **Step 2: Commit spec**

```bash
git add docs/superpowers/specs/2026-05-04-approval-gates.md docs/superpowers/plans/2026-05-04-approval-gates.md
git commit -m "docs: spec and plan for tighter approval gates in alpaca-bot-evolve"
```
