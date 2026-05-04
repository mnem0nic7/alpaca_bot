# Profitability Gate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add configurable `--min-oos-score` (absolute floor) and `--oos-gate-ratio` (relative gate multiplier) to both `alpaca-bot-nightly` and `alpaca-bot-evolve`, replacing the hardcoded `0.5` in the walk-forward candidate filter. Defaults preserve existing behavior.

**Architecture:** Four surgical changes — `_print_walk_forward_block` gains two optional kwargs; the `held_pairs` filter in both CLIs uses those values; two new CLI args are wired up. No schema changes, no Settings changes, no live engine changes.

**Tech Stack:** Python, argparse, pytest, existing test helpers in `test_nightly_cli.py` and `test_tuning_sweep_cli.py`

---

### Task 1: Update `_print_walk_forward_block` to accept gate parameters

**Files:**
- Modify: `src/alpaca_bot/tuning/cli.py:214-231` (`_print_walk_forward_block`)

The function currently hardcodes `0.5` for both the displayed label and the `held` check. Make these configurable via keyword-only args.

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/test_tuning_sweep_cli.py`:

```python
def test_print_walk_forward_block_uses_custom_gate_params(capsys):
    """_print_walk_forward_block must use oos_gate_ratio and min_oos_score for 'held' display."""
    from alpaca_bot.tuning.cli import _print_walk_forward_block
    from alpaca_bot.tuning.sweep import TuningCandidate

    cand = TuningCandidate(params={"BREAKOUT_LOOKBACK_BARS": "20"}, report=None, score=0.5)
    # OOS=0.2 passes ratio gate (0.2 >= 0.5*0.3=0.15) but fails min_oos_score (0.2 < 0.4)
    _print_walk_forward_block(
        [cand], [0.2],
        validate_pct=0.2,
        aggregate="min",
        oos_gate_ratio=0.3,
        min_oos_score=0.4,
    )
    out = capsys.readouterr().out
    # Must show "✗" — failed absolute floor
    assert "✗" in out
    # Must display the actual gate values, not hardcoded 0.5
    assert "30%" in out or "0.30" in out  # ratio shown
    assert "0.40" in out or "0.4" in out   # floor shown


def test_print_walk_forward_block_held_when_both_gates_pass(capsys):
    """Candidate held when OOS passes both relative ratio AND absolute floor."""
    from alpaca_bot.tuning.cli import _print_walk_forward_block
    from alpaca_bot.tuning.sweep import TuningCandidate

    cand = TuningCandidate(params={"BREAKOUT_LOOKBACK_BARS": "20"}, report=None, score=0.5)
    # OOS=0.4 >= IS*0.5=0.25 AND 0.4 >= min_oos_score=0.3 → held
    _print_walk_forward_block(
        [cand], [0.4],
        validate_pct=0.2,
        aggregate="min",
        oos_gate_ratio=0.5,
        min_oos_score=0.3,
    )
    out = capsys.readouterr().out
    assert "✓" in out
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/unit/test_tuning_sweep_cli.py::test_print_walk_forward_block_uses_custom_gate_params tests/unit/test_tuning_sweep_cli.py::test_print_walk_forward_block_held_when_both_gates_pass -v
```

Expected: FAIL — `TypeError: _print_walk_forward_block() got unexpected keyword argument 'oos_gate_ratio'`

- [ ] **Step 3: Update `_print_walk_forward_block` signature and body**

In `src/alpaca_bot/tuning/cli.py`, replace the existing `_print_walk_forward_block` function (lines 214-231):

```python
def _print_walk_forward_block(
    candidates: list[TuningCandidate],
    oos_scores: list[float | None],
    *,
    validate_pct: float,
    aggregate: str,
    oos_gate_ratio: float = 0.5,
    min_oos_score: float = 0.0,
) -> None:
    oos_pct_int = round(validate_pct * 100)
    ratio_pct = round(oos_gate_ratio * 100)
    floor_str = f"{min_oos_score:.2f}" if min_oos_score > 0.0 else "none"
    print(f"\nWalk-forward validation (OOS: {oos_pct_int}% of each scenario, aggregate={aggregate})")
    print(f"  IS score threshold for \"held\": OOS ≥ IS × {ratio_pct}%  AND  OOS ≥ {floor_str}")
    print()
    print(f"  {'[Rank]':>6}  {'IS-score':>8}  {'OOS-score':>9}  {'OOS-trades':>10}  {'held?':>5}  Params")
    for i, (c, oos_score) in enumerate(zip(candidates, oos_scores), 1):
        is_score_str = f"{c.score:.4f}" if c.score is not None else "    None"
        oos_score_str = f"{oos_score:.4f}" if oos_score is not None else "    None"
        held = (
            "✓"
            if (
                oos_score is not None
                and c.score is not None
                and oos_score >= c.score * oos_gate_ratio
                and oos_score >= min_oos_score
            )
            else "✗"
        )
        params_str = " ".join(f"{k}={v}" for k, v in c.params.items())
        print(f"  [{i:3d}]  {is_score_str:>8}  {oos_score_str:>9}  {'—':>10}  {held:>5}  {params_str}")
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/unit/test_tuning_sweep_cli.py::test_print_walk_forward_block_uses_custom_gate_params tests/unit/test_tuning_sweep_cli.py::test_print_walk_forward_block_held_when_both_gates_pass -v
```

Expected: PASS

- [ ] **Step 5: Run full test suite to confirm no regressions**

```bash
pytest tests/unit/test_tuning_sweep_cli.py tests/unit/test_nightly_cli.py -v
```

Expected: all existing tests pass

- [ ] **Step 6: Commit**

```bash
git add src/alpaca_bot/tuning/cli.py tests/unit/test_tuning_sweep_cli.py
git commit -m "feat: _print_walk_forward_block accepts oos_gate_ratio and min_oos_score params"
```

---

### Task 2: Add `--min-oos-score` and `--oos-gate-ratio` to `alpaca-bot-evolve`

**Files:**
- Modify: `src/alpaca_bot/tuning/cli.py` (argparse + `held_pairs` filter + `_print_walk_forward_block` call)
- Modify: `tests/unit/test_tuning_sweep_cli.py` (new test)

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/test_tuning_sweep_cli.py`:

```python
def test_evolve_min_oos_score_rejects_below_floor(monkeypatch, tmp_path):
    """--min-oos-score 0.5: candidate passes relative gate but fails absolute floor → return 1."""
    import json
    from alpaca_bot.tuning import cli as module
    from alpaca_bot.tuning.sweep import TuningCandidate
    from alpaca_bot.replay.runner import ReplayScenario

    _patch_env(monkeypatch)

    for name in ("SYM_A_252d.json", "SYM_B_252d.json"):
        (tmp_path / name).write_text(json.dumps({
            "name": name.replace(".json", ""), "symbol": "SYM",
            "starting_equity": 100000.0, "daily_bars": [], "intraday_bars": [],
        }))

    def fake_split(scenario, *, in_sample_ratio):
        is_s = ReplayScenario(name=scenario.name + "_is", symbol=scenario.symbol,
                              starting_equity=scenario.starting_equity,
                              daily_bars=[], intraday_bars=[])
        oos_s = ReplayScenario(name=scenario.name + "_oos", symbol=scenario.symbol,
                               starting_equity=scenario.starting_equity,
                               daily_bars=[], intraday_bars=[])
        return is_s, oos_s

    monkeypatch.setattr(module, "split_scenario", fake_split)

    # IS=0.6, OOS=0.35: passes relative gate (0.35 >= 0.6*0.5=0.3) but fails floor (0.35 < 0.5)
    cand = TuningCandidate(params={"BREAKOUT_LOOKBACK_BARS": "20"}, report=None, score=0.6)
    monkeypatch.setattr(module, "run_multi_scenario_sweep", lambda **kw: [cand])
    monkeypatch.setattr(module, "evaluate_candidates_oos",
                        lambda candidates, oos_scenarios, **kw: [0.35])

    monkeypatch.setattr(sys, "argv", [
        "evolve", "--scenario-dir", str(tmp_path), "--no-db",
        "--validate-pct", "0.2",
        "--min-oos-score", "0.5",
    ])

    result = module.main()

    assert result == 1, "below min_oos_score must return 1 (no held candidates)"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/unit/test_tuning_sweep_cli.py::test_evolve_min_oos_score_rejects_below_floor -v
```

Expected: FAIL — `error: unrecognized arguments: --min-oos-score`

- [ ] **Step 3: Add argparse args to `alpaca-bot-evolve` and update filter**

In `src/alpaca_bot/tuning/cli.py`, in the `main()` function:

After the existing `--validate-pct` argument (around line 46), add:

```python
    parser.add_argument("--min-oos-score", type=float, default=0.0,
                        help="Minimum absolute OOS score to accept a candidate (default: 0.0)")
    parser.add_argument("--oos-gate-ratio", type=float, default=0.5,
                        help="Required OOS/IS score ratio to hold a candidate (default: 0.5)")
```

Update the `_print_walk_forward_block` call (line 146) to:

```python
        _print_walk_forward_block(
            top10, oos_scores,
            validate_pct=validate_pct,
            aggregate=args.aggregate,
            oos_gate_ratio=args.oos_gate_ratio,
            min_oos_score=args.min_oos_score,
        )
```

Replace the `held_pairs` filter (lines 147-150) with:

```python
        held_pairs = [
            (c, s) for c, s in zip(top10, oos_scores)
            if s is not None
            and c.score is not None
            and s >= c.score * args.oos_gate_ratio
            and s >= args.min_oos_score
        ]
```

- [ ] **Step 4: Run test to verify it passes**

```bash
pytest tests/unit/test_tuning_sweep_cli.py::test_evolve_min_oos_score_rejects_below_floor -v
```

Expected: PASS

- [ ] **Step 5: Run full test suite**

```bash
pytest tests/unit/test_tuning_sweep_cli.py -v
```

Expected: all tests pass

- [ ] **Step 6: Commit**

```bash
git add src/alpaca_bot/tuning/cli.py tests/unit/test_tuning_sweep_cli.py
git commit -m "feat: add --min-oos-score and --oos-gate-ratio to alpaca-bot-evolve"
```

---

### Task 3: Add `--min-oos-score` and `--oos-gate-ratio` to `alpaca-bot-nightly`

**Files:**
- Modify: `src/alpaca_bot/nightly/cli.py` (argparse + `held_pairs` filter + `_print_walk_forward_block` call)
- Modify: `tests/unit/test_nightly_cli.py` (new test)

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/test_nightly_cli.py`:

```python
def test_nightly_cli_min_oos_score_rejects_below_floor(monkeypatch, tmp_path):
    """--min-oos-score 0.5: OOS=0.35 passes relative gate but fails floor → no held → return 0."""
    from alpaca_bot.nightly import cli as module
    from alpaca_bot.tuning.sweep import TuningCandidate

    _patch_env(monkeypatch)
    _make_scenario_files(tmp_path)
    _patch_common_db(monkeypatch, module)

    monkeypatch.setattr(module, "split_scenario", _fake_split)

    # IS=0.6, OOS=0.35: passes ratio gate (0.35 >= 0.6*0.5=0.3) but fails floor (0.35 < 0.5)
    cand = TuningCandidate(params={"BREAKOUT_LOOKBACK_BARS": "20"}, report=None, score=0.6)
    monkeypatch.setattr(module, "run_multi_scenario_sweep", lambda **kw: [cand])
    monkeypatch.setattr(module, "evaluate_candidates_oos",
                        lambda candidates, oos_scenarios, **kw: [0.35])

    monkeypatch.setattr(sys, "argv", [
        "nightly", "--dry-run", "--no-db",
        "--output-dir", str(tmp_path),
        "--min-oos-score", "0.5",
    ])

    result = module.main()

    # Nightly returns 0 (not 1) when no held candidates — live report still runs
    assert result == 0
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/unit/test_nightly_cli.py::test_nightly_cli_min_oos_score_rejects_below_floor -v
```

Expected: FAIL — `error: unrecognized arguments: --min-oos-score`

- [ ] **Step 3: Add argparse args to `alpaca-bot-nightly` and update filter**

In `src/alpaca_bot/nightly/cli.py`, after the existing `--validate-pct` argument (around line 48), add:

```python
    parser.add_argument("--min-oos-score", type=float, default=0.0,
                        help="Minimum absolute OOS score to accept a candidate (default: 0.0)")
    parser.add_argument("--oos-gate-ratio", type=float, default=0.5,
                        help="Required OOS/IS score ratio to hold a candidate (default: 0.5)")
```

Update the `_print_walk_forward_block` call (lines 179-183) to:

```python
            _print_walk_forward_block(
                top10, oos_scores,
                validate_pct=args.validate_pct,
                aggregate="min",
                oos_gate_ratio=args.oos_gate_ratio,
                min_oos_score=args.min_oos_score,
            )
```

Replace the `held_pairs` filter (lines 185-188) with:

```python
            held_pairs = [
                (c, s) for c, s in zip(top10, oos_scores)
                if s is not None
                and c.score is not None
                and s >= c.score * args.oos_gate_ratio
                and s >= args.min_oos_score
            ]
```

- [ ] **Step 4: Run test to verify it passes**

```bash
pytest tests/unit/test_nightly_cli.py::test_nightly_cli_min_oos_score_rejects_below_floor -v
```

Expected: PASS

- [ ] **Step 5: Run full test suite**

```bash
pytest tests/unit/test_nightly_cli.py -v
```

Expected: all 7 tests pass

- [ ] **Step 6: Commit**

```bash
git add src/alpaca_bot/nightly/cli.py tests/unit/test_nightly_cli.py
git commit -m "feat: add --min-oos-score and --oos-gate-ratio to alpaca-bot-nightly"
```

---

### Task 4: Final regression check

- [ ] **Step 1: Run full test suite**

```bash
pytest -q --tb=short
```

Expected: all tests pass (≥ 1103)

- [ ] **Step 2: Verify CLI help shows new flags**

```bash
alpaca-bot-nightly --help | grep -E "min-oos|oos-gate"
alpaca-bot-evolve --help | grep -E "min-oos|oos-gate"
```

Expected: both flags appear with descriptions

- [ ] **Step 3: Commit (if any cleanup needed)**

Only if there are outstanding uncommitted changes:

```bash
git add -p
git commit -m "chore: final cleanup for profitability gate feature"
```
