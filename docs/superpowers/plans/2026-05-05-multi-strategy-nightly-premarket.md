# Multi-Strategy Nightly + Pre-Market CLI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend `alpaca-bot-nightly` to sweep all strategies in `STRATEGY_GRIDS` and build a composite env block; add `alpaca-bot-premarket` CLI that sanity-checks current params against cached scenarios before market open.

**Architecture:** Feature 1 replaces the single `--strategy` arg with `--strategies` (default `all`), wraps the existing evolve block in a per-strategy loop, collects `winners`, and merges them into a composite env block using first-wins by `_viability_key` rank. Feature 2 is a new read-only CLI that builds a single-value constrained grid from current env vars and runs IS+OOS eval to produce a pass/fail report per strategy.

**Tech Stack:** Python stdlib, existing `run_multi_scenario_sweep`, `evaluate_candidates_oos`, `_viability_key`, `STRATEGY_GRIDS` from `alpaca_bot.tuning.sweep`; `Settings.from_env()`; argparse; pytest.

---

## File Map

| Action | Path |
|--------|------|
| Modify | `src/alpaca_bot/nightly/cli.py` |
| Create | `src/alpaca_bot/nightly/premarket_cli.py` |
| Extend | `tests/unit/test_nightly_cli.py` |
| Create | `tests/unit/test_premarket_cli.py` |
| Modify | `pyproject.toml` |
| Modify | `deploy/cron.d/alpaca-bot` |

---

## Task 1: Multi-strategy nightly — four new tests

**Files:**
- Test: `tests/unit/test_nightly_cli.py`

- [ ] **Step 1: Write four failing tests**

Append to `tests/unit/test_nightly_cli.py`:

```python
def test_nightly_multi_strategy_sweeps_all_grids(monkeypatch, tmp_path):
    """--strategies all: run_multi_scenario_sweep called once per STRATEGY_GRIDS key."""
    from alpaca_bot.nightly import cli as module
    from alpaca_bot.tuning.sweep import STRATEGY_GRIDS, TuningCandidate

    _patch_env(monkeypatch)
    _make_scenario_files(tmp_path)
    _patch_common_db(monkeypatch, module)
    monkeypatch.setattr(module, "split_scenario", _fake_split)

    sweep_calls: list[str] = []

    def fake_sweep(**kw):
        strat = kw["signal_evaluator"].__name__ if hasattr(kw["signal_evaluator"], "__name__") else str(kw["signal_evaluator"])
        sweep_calls.append(strat)
        return [TuningCandidate(params={"RELATIVE_VOLUME_THRESHOLD": "1.5"}, report=None, score=0.3)]

    monkeypatch.setattr(module, "run_multi_scenario_sweep", fake_sweep)
    monkeypatch.setattr(module, "evaluate_candidates_oos",
                        lambda candidates, oos_scenarios, **kw: [0.25])

    monkeypatch.setattr(sys, "argv", [
        "nightly", "--dry-run", "--no-db", "--output-dir", str(tmp_path),
        "--strategies", "all",
    ])

    result = module.main()

    assert result == 0
    assert len(sweep_calls) == len(STRATEGY_GRIDS), (
        f"Expected {len(STRATEGY_GRIDS)} sweep calls, got {len(sweep_calls)}"
    )


def test_nightly_composite_env_shared_params_from_highest_scorer(monkeypatch, tmp_path):
    """Shared keys come from the highest-_viability_key winner."""
    from alpaca_bot.nightly import cli as module
    from alpaca_bot.tuning.sweep import TuningCandidate

    _patch_env(monkeypatch)
    _make_scenario_files(tmp_path)
    _patch_common_db(monkeypatch, module)
    monkeypatch.setattr(module, "split_scenario", _fake_split)

    # breakout wins with score=0.5, momentum wins with score=0.2
    # RELATIVE_VOLUME_THRESHOLD is shared — should come from breakout (higher rank)
    call_count = [0]

    def fake_sweep(**kw):
        call_count[0] += 1
        if call_count[0] == 1:  # first strategy (breakout, alphabetical or grid order)
            return [TuningCandidate(
                params={"BREAKOUT_LOOKBACK_BARS": "25", "RELATIVE_VOLUME_THRESHOLD": "1.8",
                        "DAILY_SMA_PERIOD": "20"},
                report=None, score=0.5,
            )]
        else:
            return [TuningCandidate(
                params={"PRIOR_DAY_HIGH_LOOKBACK_BARS": "2", "RELATIVE_VOLUME_THRESHOLD": "1.3",
                        "ATR_STOP_MULTIPLIER": "1.5"},
                report=None, score=0.2,
            )]

    monkeypatch.setattr(module, "run_multi_scenario_sweep", fake_sweep)
    # Both pass OOS gate
    monkeypatch.setattr(module, "evaluate_candidates_oos",
                        lambda candidates, oos_scenarios, **kw: [0.4] if call_count[0] <= 1 else [0.15])

    output_env = tmp_path / "candidate.env"
    monkeypatch.setattr(sys, "argv", [
        "nightly", "--dry-run", "--no-db",
        "--output-dir", str(tmp_path),
        "--output-env", str(output_env),
        "--strategies", "breakout,momentum",
    ])

    result = module.main()

    assert result == 0
    assert output_env.exists()
    content = output_env.read_text()
    # Shared RELATIVE_VOLUME_THRESHOLD must come from breakout (score=0.5 > 0.2)
    assert "RELATIVE_VOLUME_THRESHOLD=1.8" in content, (
        "Shared param must come from highest-scoring winner (breakout, 1.8 not 1.3)"
    )
    # Strategy-specific param from second winner must also be present
    assert "PRIOR_DAY_HIGH_LOOKBACK_BARS=2" in content


def test_nightly_omits_strategy_with_no_held_candidates(monkeypatch, tmp_path):
    """Strategy with OOS=None is excluded from composite env (no held candidates)."""
    from alpaca_bot.nightly import cli as module
    from alpaca_bot.tuning.sweep import TuningCandidate

    _patch_env(monkeypatch)
    _make_scenario_files(tmp_path)
    _patch_common_db(monkeypatch, module)
    monkeypatch.setattr(module, "split_scenario", _fake_split)

    call_count = [0]

    def fake_sweep(**kw):
        call_count[0] += 1
        return [TuningCandidate(
            params={"BREAKOUT_LOOKBACK_BARS": "20", "RELATIVE_VOLUME_THRESHOLD": "1.5",
                    "DAILY_SMA_PERIOD": "20"},
            report=None, score=0.4,
        )]

    monkeypatch.setattr(module, "run_multi_scenario_sweep", fake_sweep)

    oos_call = [0]

    def fake_oos(candidates, oos_scenarios, **kw):
        oos_call[0] += 1
        # First strategy fails OOS gate; second passes
        return [None] if oos_call[0] == 1 else [0.3]

    monkeypatch.setattr(module, "evaluate_candidates_oos", fake_oos)

    output_env = tmp_path / "candidate.env"
    monkeypatch.setattr(sys, "argv", [
        "nightly", "--dry-run", "--no-db",
        "--output-dir", str(tmp_path),
        "--output-env", str(output_env),
        "--strategies", "breakout,momentum",
    ])

    result = module.main()

    assert result == 0
    assert output_env.exists()
    content = output_env.read_text()
    # breakout (first, OOS=None) must NOT contribute params — only momentum winner
    assert "BREAKOUT_LOOKBACK_BARS" not in content, (
        "Strategy with no held candidates must not appear in composite env"
    )


def test_nightly_no_winners_writes_no_candidate_env(monkeypatch, tmp_path):
    """All strategies fail OOS gate → no candidate.env written."""
    from alpaca_bot.nightly import cli as module
    from alpaca_bot.tuning.sweep import TuningCandidate

    _patch_env(monkeypatch)
    _make_scenario_files(tmp_path)
    _patch_common_db(monkeypatch, module)
    monkeypatch.setattr(module, "split_scenario", _fake_split)

    monkeypatch.setattr(module, "run_multi_scenario_sweep",
                        lambda **kw: [TuningCandidate(
                            params={"RELATIVE_VOLUME_THRESHOLD": "1.5"}, report=None, score=0.3
                        )])
    monkeypatch.setattr(module, "evaluate_candidates_oos",
                        lambda candidates, oos_scenarios, **kw: [None])

    output_env = tmp_path / "candidate.env"
    monkeypatch.setattr(sys, "argv", [
        "nightly", "--dry-run", "--no-db",
        "--output-dir", str(tmp_path),
        "--output-env", str(output_env),
        "--strategies", "breakout,momentum",
    ])

    result = module.main()

    assert result == 0
    assert not output_env.exists(), "No candidate.env must be written when all strategies fail OOS gate"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/unit/test_nightly_cli.py::test_nightly_multi_strategy_sweeps_all_grids \
       tests/unit/test_nightly_cli.py::test_nightly_composite_env_shared_params_from_highest_scorer \
       tests/unit/test_nightly_cli.py::test_nightly_omits_strategy_with_no_held_candidates \
       tests/unit/test_nightly_cli.py::test_nightly_no_winners_writes_no_candidate_env -v
```

Expected: 4 FAILED (error: unrecognized argument `--strategies`)

- [ ] **Step 3: Commit failing tests**

```bash
git add tests/unit/test_nightly_cli.py
git commit -m "test: failing tests for multi-strategy nightly sweep"
```

---

## Task 2: Multi-strategy nightly — implementation

**Files:**
- Modify: `src/alpaca_bot/nightly/cli.py`

- [ ] **Step 1: Replace `--strategy` with `--strategies` and rewrite evolve block**

Replace lines 59–61 of `src/alpaca_bot/nightly/cli.py`:

```python
    parser.add_argument("--strategy", default="breakout",
                        choices=list(STRATEGY_REGISTRY),
                        help="Strategy grid to sweep (default: breakout)")
```

with:

```python
    parser.add_argument("--strategies", default="all",
                        help="Comma-separated strategy names or 'all' (default: all)")
```

Then replace the entire `# ── Evolve ───` block (lines 120–231) with:

```python
        # ── Evolve ───────────────────────────────────────────────────────────
        if symbols:
            print("\n── Evolve ───────────────────────────────────────────────────────────")
            files = sorted(output_dir.glob("*.json"))
            if len(files) < 2:
                print(
                    f"Error: need at least 2 scenario files in {output_dir}; "
                    f"found {len(files)}. Run without --dry-run or add scenario files.",
                    file=sys.stderr,
                )
                return 1

            strategy_names = _resolve_strategies(args.strategies)
            all_scenarios = [ReplayRunner.load_scenario(f) for f in files]
            is_scenarios = []
            oos_scenarios = []
            for s in all_scenarios:
                is_s, oos_s = split_scenario(s, in_sample_ratio=1.0 - args.validate_pct)
                is_scenarios.append(is_s)
                oos_scenarios.append(oos_s)

            scenario_name_base = "+".join(s.name for s in all_scenarios)
            oos_pct_int = round(args.validate_pct * 100)
            print(
                f"Scenarios: {len(all_scenarios)} × IS/OOS split "
                f"({100 - oos_pct_int}% / {oos_pct_int}%)"
            )
            print(f"Strategies: {', '.join(strategy_names)}")

            tuning_store = TuningResultStore(conn)

            # winners: (strategy_name, best_candidate, oos_score) — only held candidates
            winners: list[tuple[str, object, float]] = []

            for strat_name in strategy_names:
                grid = STRATEGY_GRIDS.get(strat_name, DEFAULT_GRID)
                signal_evaluator = STRATEGY_REGISTRY[strat_name]
                total_combos = 1
                for vals in grid.values():
                    total_combos *= len(vals)

                # Load surrogate per strategy (grid keys differ)
                try:
                    historical = tuning_store.load_all_scored(trading_mode=trading_mode.value)
                except Exception as exc:
                    print(f"Warning: could not load tuning history for surrogate ({strat_name}): {exc}",
                          file=sys.stderr)
                    historical = []
                grid_keys = set(grid.keys())
                historical = [r for r in historical if set(r["params"].keys()) == grid_keys]
                surrogate = SurrogateModel()
                surrogate_fitted = surrogate.fit(historical)
                if surrogate_fitted:
                    print(f"  [{strat_name}] surrogate: fitted on {len(historical)} records")

                candidates = run_multi_scenario_sweep(
                    scenarios=is_scenarios,
                    base_env=base_env,
                    grid=grid,
                    max_drawdown_pct=args.max_drawdown_pct,
                    max_trades=args.max_trades,
                    signal_evaluator=signal_evaluator,
                    surrogate=surrogate,
                )
                scored = [c for c in candidates if c.score is not None]

                top10 = scored[:10]
                if not top10:
                    print(f"  [{strat_name}] no scored candidates — skipped")
                    continue

                oos_scores = evaluate_candidates_oos(
                    candidates=top10,
                    oos_scenarios=oos_scenarios,
                    base_env=base_env,
                    min_trades=3,
                    max_drawdown_pct=args.max_drawdown_pct,
                    max_trades=args.max_trades,
                    signal_evaluator=signal_evaluator,
                )

                held_pairs = [
                    (c, s) for c, s in zip(top10, oos_scores)
                    if s is not None
                    and c.score is not None
                    and s >= c.score * args.oos_gate_ratio
                    and s >= args.min_oos_score
                ]

                if not args.no_db and candidates:
                    try:
                        run_id = tuning_store.save_run(
                            scenario_name=f"{scenario_name_base} [{strat_name}]",
                            trading_mode=trading_mode.value,
                            candidates=candidates,
                            created_at=now,
                        )
                        print(f"  [{strat_name}] DB run_id={run_id}")
                    except Exception as exc:
                        print(f"Warning: could not save tuning results ({strat_name}): {exc}",
                              file=sys.stderr)

                if held_pairs:
                    best, best_oos = max(held_pairs, key=lambda p: _viability_key(p[0], p[1]))
                    winners.append((strat_name, best, best_oos))

            _print_strategy_results(winners, strategy_names, all_scenarios)

            if winners:
                composite_params = _build_composite_env(winners)
                env_block = _format_composite_env_block(composite_params, winners[0][0], now)
                print(f"\n{env_block}")
                if args.output_env:
                    Path(args.output_env).write_text(env_block + "\n")
                    print(f"Candidate env written to {args.output_env}")
            else:
                print("\nNo walk-forward held candidates across all strategies — current parameters remain active.")
```

Also add these three helper functions at the bottom of `src/alpaca_bot/nightly/cli.py` (before or after `_weekdays_back`):

```python
def _resolve_strategies(strategies_arg: str) -> list[str]:
    """Resolve '--strategies all' or comma-separated names to a list."""
    if strategies_arg.strip().lower() == "all":
        return list(STRATEGY_GRIDS.keys())
    names = [s.strip() for s in strategies_arg.split(",") if s.strip()]
    unknown = [n for n in names if n not in STRATEGY_GRIDS]
    if unknown:
        print(f"Warning: unknown strategies ignored: {unknown}", file=sys.stderr)
    return [n for n in names if n in STRATEGY_GRIDS]


def _build_composite_env(
    winners: list[tuple[str, object, float]],
) -> dict[str, str]:
    """First-wins merge: sort by _viability_key descending, apply params in rank order."""
    # Sort highest first
    sorted_winners = sorted(winners, key=lambda t: _viability_key(t[1], t[2]), reverse=True)
    composite: dict[str, str] = {}
    for _strat, candidate, _oos in sorted_winners:
        for k, v in candidate.params.items():
            if k not in composite:
                composite[k] = v
    return composite


def _format_composite_env_block(
    params: dict[str, str],
    top_strategy: str,
    now: datetime,
) -> str:
    lines = [
        f"# Composite params from nightly run {now.strftime('%Y-%m-%dT%H:%M:%SZ')}",
        f"# Shared params from: {top_strategy}",
    ]
    lines += [f"{k}={v}" for k, v in params.items()]
    return "\n".join(lines)


def _print_strategy_results(
    winners: list[tuple[str, object, float]],
    strategy_names: list[str],
    all_scenarios: list,
) -> None:
    print("\n── Strategy Results ─────────────────────────────────────────────────")
    winner_map = {strat: (cand, oos) for strat, cand, oos in winners}
    for strat in strategy_names:
        if strat in winner_map:
            cand, oos = winner_map[strat]
            report = cand.report
            trades = report.total_trades if report else 0
            pf = f"{report.profit_factor:.2f}" if (report and report.profit_factor is not None) else "—"
            print(f"  {strat:<20s} score={oos:.4f}  trades={trades:<3d}  pf={pf}  held? ✓")
        else:
            print(f"  {strat:<20s} held? ✗  (no held candidates)")
    if winners:
        top = sorted(winners, key=lambda t: _viability_key(t[1], t[2]), reverse=True)[0][0]
        print(f"Composite winner (shared params from: {top})")
```

Also add `TuningCandidate` to the imports at top of `cli.py` (needed for type in winners list), and ensure `DEFAULT_GRID` is imported from `alpaca_bot.tuning.sweep`. Check the existing import block:

```python
from alpaca_bot.tuning.sweep import (
    DEFAULT_GRID,
    STRATEGY_GRIDS,
    _viability_key,
    evaluate_candidates_oos,
    run_multi_scenario_sweep,
)
```

This is already present. No changes needed to imports.

- [ ] **Step 2: Run the four new tests**

```bash
pytest tests/unit/test_nightly_cli.py::test_nightly_multi_strategy_sweeps_all_grids \
       tests/unit/test_nightly_cli.py::test_nightly_composite_env_shared_params_from_highest_scorer \
       tests/unit/test_nightly_cli.py::test_nightly_omits_strategy_with_no_held_candidates \
       tests/unit/test_nightly_cli.py::test_nightly_no_winners_writes_no_candidate_env -v
```

Expected: 4 PASSED

- [ ] **Step 3: Run full test suite to check for regressions**

The existing test `test_nightly_cli_runs_evolve_and_writes_output_env` uses `--dry-run` with no `--strategies` arg — it should still pass because `default="all"` causes a full sweep. However, it stubs `run_multi_scenario_sweep` to return one candidate with key `BREAKOUT_LOOKBACK_BARS`. The new loop calls sweep once per strategy (11 calls) but only one call returns a candidate with a non-None OOS score. Since `fake_oos` returns `[0.4]` for all calls, all 11 strategies will produce a winner. The composite env will contain `BREAKOUT_LOOKBACK_BARS=20` from whichever strategy has that key (breakout, first alphabetically). The existing assertion `"BREAKOUT_LOOKBACK_BARS=20" in output_env.read_text()` will still pass.

But: the existing test stubs `run_multi_scenario_sweep` with a simple lambda that ignores `signal_evaluator`, so all 11 strategies return the same candidate. The OOS stub `lambda candidates, oos_scenarios, **kw: [0.4]` also works for all calls. Run:

```bash
pytest tests/unit/test_nightly_cli.py -v
```

Expected: all tests PASS (13 total including 4 new)

- [ ] **Step 4: Commit**

```bash
git add src/alpaca_bot/nightly/cli.py
git commit -m "feat: multi-strategy nightly sweep with composite env block"
```

---

## Task 3: Pre-market CLI — five failing tests

**Files:**
- Create: `tests/unit/test_premarket_cli.py`

- [ ] **Step 1: Write five failing tests**

Create `tests/unit/test_premarket_cli.py`:

```python
from __future__ import annotations

import json
import sys


def _patch_premarket_env(monkeypatch):
    monkeypatch.setenv("TRADING_MODE", "paper")
    monkeypatch.setenv("ENABLE_LIVE_TRADING", "false")
    monkeypatch.setenv("STRATEGY_VERSION", "v1")
    monkeypatch.setenv("DATABASE_URL", "postgresql://dummy:dummy@localhost/dummy")
    monkeypatch.setenv("SYMBOLS", "AAPL")
    monkeypatch.setenv("MARKET_DATA_FEED", "sip")
    # Set a known value for a shared param key to verify it is picked up
    monkeypatch.setenv("RELATIVE_VOLUME_THRESHOLD", "1.5")


def _make_scenario_files(tmp_path, n=3):
    for i in range(n):
        sym = f"SYM{i}"
        (tmp_path / f"{sym}_252d.json").write_text(json.dumps({
            "name": f"{sym}_252d", "symbol": sym,
            "starting_equity": 100000.0, "daily_bars": [], "intraday_bars": [],
        }))


def _fake_split(scenario, *, in_sample_ratio):
    from alpaca_bot.replay.runner import ReplayScenario
    is_s = ReplayScenario(name=scenario.name + "_is", symbol=scenario.symbol,
                          starting_equity=scenario.starting_equity,
                          daily_bars=[], intraday_bars=[])
    oos_s = ReplayScenario(name=scenario.name + "_oos", symbol=scenario.symbol,
                           starting_equity=scenario.starting_equity,
                           daily_bars=[], intraday_bars=[])
    return is_s, oos_s


def test_premarket_pass_returns_exit_0(monkeypatch, tmp_path):
    """All strategies pass gates → exit 0."""
    from alpaca_bot.nightly import premarket_cli as module
    from alpaca_bot.replay.report import BacktestReport
    from alpaca_bot.tuning.sweep import TuningCandidate

    _patch_premarket_env(monkeypatch)
    _make_scenario_files(tmp_path)
    monkeypatch.setattr(module, "split_scenario", _fake_split)

    passing_report = BacktestReport(
        trades=(), total_trades=5, winning_trades=3, losing_trades=2,
        win_rate=0.6, mean_return_pct=0.02, max_drawdown_pct=None, sharpe_ratio=0.5,
        avg_win_return_pct=None, avg_loss_return_pct=None,
        profit_factor=1.3,
    )
    passing_cand = TuningCandidate(
        params={"RELATIVE_VOLUME_THRESHOLD": "1.5"}, report=passing_report, score=0.4
    )
    monkeypatch.setattr(module, "run_multi_scenario_sweep", lambda **kw: [passing_cand])
    monkeypatch.setattr(module, "evaluate_candidates_oos",
                        lambda candidates, oos_scenarios, **kw: [0.35])

    monkeypatch.setattr(sys, "argv", [
        "premarket", "--scenario-dir", str(tmp_path),
    ])

    result = module.main()

    assert result == 0


def test_premarket_fail_returns_exit_1(monkeypatch, tmp_path):
    """One strategy fails profit_factor < 1.0 gate → exit 1."""
    from alpaca_bot.nightly import premarket_cli as module
    from alpaca_bot.replay.report import BacktestReport
    from alpaca_bot.tuning.sweep import TuningCandidate

    _patch_premarket_env(monkeypatch)
    _make_scenario_files(tmp_path)
    monkeypatch.setattr(module, "split_scenario", _fake_split)

    failing_report = BacktestReport(
        trades=(), total_trades=5, winning_trades=2, losing_trades=3,
        win_rate=0.4, mean_return_pct=-0.01, max_drawdown_pct=None, sharpe_ratio=0.2,
        avg_win_return_pct=None, avg_loss_return_pct=None,
        profit_factor=0.85,  # < 1.0 → FAIL
    )
    failing_cand = TuningCandidate(
        params={"RELATIVE_VOLUME_THRESHOLD": "1.5"}, report=failing_report, score=0.3
    )

    call_count = [0]

    def fake_sweep(**kw):
        call_count[0] += 1
        return [failing_cand]

    monkeypatch.setattr(module, "run_multi_scenario_sweep", fake_sweep)
    monkeypatch.setattr(module, "evaluate_candidates_oos",
                        lambda candidates, oos_scenarios, **kw: [0.25])

    monkeypatch.setattr(sys, "argv", [
        "premarket", "--scenario-dir", str(tmp_path),
    ])

    result = module.main()

    assert result == 1


def test_premarket_missing_scenario_dir_exits_0(monkeypatch, tmp_path):
    """Missing --scenario-dir → warning, exit 0 (advisory — nightly may not have run yet)."""
    from alpaca_bot.nightly import premarket_cli as module

    _patch_premarket_env(monkeypatch)
    missing = tmp_path / "nonexistent"

    monkeypatch.setattr(sys, "argv", [
        "premarket", "--scenario-dir", str(missing),
    ])

    result = module.main()

    assert result == 0


def test_premarket_reads_settings_not_candidate_env(monkeypatch, tmp_path):
    """Params come from os.environ (via base_env), not a candidate.env file."""
    from alpaca_bot.nightly import premarket_cli as module
    from alpaca_bot.tuning.sweep import STRATEGY_GRIDS, TuningCandidate

    _patch_premarket_env(monkeypatch)
    # Set a distinctive value so we can verify it was used
    monkeypatch.setenv("RELATIVE_VOLUME_THRESHOLD", "2.0")
    _make_scenario_files(tmp_path)
    monkeypatch.setattr(module, "split_scenario", _fake_split)

    captured_grids: list[dict] = []

    def fake_sweep(**kw):
        captured_grids.append(dict(kw["grid"]))
        return [TuningCandidate(params=kw["grid"], report=None, score=0.3)]

    monkeypatch.setattr(module, "run_multi_scenario_sweep", fake_sweep)
    monkeypatch.setattr(module, "evaluate_candidates_oos",
                        lambda candidates, oos_scenarios, **kw: [0.25])

    monkeypatch.setattr(sys, "argv", [
        "premarket", "--scenario-dir", str(tmp_path),
    ])

    module.main()

    # At least one strategy has RELATIVE_VOLUME_THRESHOLD in its grid
    rvt_grids = [g for g in captured_grids if "RELATIVE_VOLUME_THRESHOLD" in g]
    assert rvt_grids, "At least one strategy grid must include RELATIVE_VOLUME_THRESHOLD"
    for g in rvt_grids:
        assert g["RELATIVE_VOLUME_THRESHOLD"] == ["2.0"], (
            "Constrained grid must use env var value '2.0', not default '1.5'"
        )


def test_premarket_oos_gate_ratio_respected(monkeypatch, tmp_path):
    """OOS < IS × oos_gate_ratio → FAIL even if OOS > min_oos_score."""
    from alpaca_bot.nightly import premarket_cli as module
    from alpaca_bot.replay.report import BacktestReport
    from alpaca_bot.tuning.sweep import TuningCandidate

    _patch_premarket_env(monkeypatch)
    _make_scenario_files(tmp_path)
    monkeypatch.setattr(module, "split_scenario", _fake_split)

    # IS=0.5, OOS=0.25 — ratio=0.25/0.5=0.50 < gate_ratio=0.6 → FAIL
    report = BacktestReport(
        trades=(), total_trades=5, winning_trades=3, losing_trades=2,
        win_rate=0.6, mean_return_pct=0.02, max_drawdown_pct=None, sharpe_ratio=0.5,
        avg_win_return_pct=None, avg_loss_return_pct=None,
        profit_factor=1.2,
    )
    cand = TuningCandidate(
        params={"RELATIVE_VOLUME_THRESHOLD": "1.5"}, report=report, score=0.5
    )
    monkeypatch.setattr(module, "run_multi_scenario_sweep", lambda **kw: [cand])
    # OOS=0.25 passes min_oos_score=0.2 but fails ratio gate (0.25/0.5=0.5 < 0.6)
    monkeypatch.setattr(module, "evaluate_candidates_oos",
                        lambda candidates, oos_scenarios, **kw: [0.25])

    monkeypatch.setattr(sys, "argv", [
        "premarket", "--scenario-dir", str(tmp_path),
        "--oos-gate-ratio", "0.6",
        "--min-oos-score", "0.2",
    ])

    result = module.main()

    assert result == 1
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/unit/test_premarket_cli.py -v
```

Expected: 5 FAILED (ModuleNotFoundError: `alpaca_bot.nightly.premarket_cli`)

- [ ] **Step 3: Commit failing tests**

```bash
git add tests/unit/test_premarket_cli.py
git commit -m "test: failing tests for alpaca-bot-premarket CLI"
```

---

## Task 4: Pre-market CLI — implementation

**Files:**
- Create: `src/alpaca_bot/nightly/premarket_cli.py`

- [ ] **Step 1: Create `premarket_cli.py`**

```python
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from alpaca_bot.config import Settings
from alpaca_bot.replay.runner import ReplayRunner
from alpaca_bot.replay.splitter import split_scenario
from alpaca_bot.strategy import STRATEGY_REGISTRY
from alpaca_bot.tuning.sweep import (
    STRATEGY_GRIDS,
    evaluate_candidates_oos,
    run_multi_scenario_sweep,
)

# Hardcoded defaults matching Settings.from_env() field defaults.
# Used when a strategy grid key is absent from os.environ.
_PARAM_DEFAULTS: dict[str, str] = {
    "BREAKOUT_LOOKBACK_BARS": "20",
    "RELATIVE_VOLUME_THRESHOLD": "1.5",
    "DAILY_SMA_PERIOD": "20",
    "RELATIVE_VOLUME_LOOKBACK_BARS": "20",
    "ATR_STOP_MULTIPLIER": "1.0",
    "PRIOR_DAY_HIGH_LOOKBACK_BARS": "1",
    "ORB_OPENING_BARS": "2",
    "HIGH_WATERMARK_LOOKBACK_DAYS": "252",
    "EMA_PERIOD": "9",
    "VWAP_DIP_THRESHOLD_PCT": "0.015",
    "GAP_THRESHOLD_PCT": "0.02",
    "GAP_VOLUME_THRESHOLD": "2.0",
    "BULL_FLAG_MIN_RUN_PCT": "0.02",
    "BULL_FLAG_CONSOLIDATION_RANGE_PCT": "0.5",
    "BB_PERIOD": "20",
    "BB_SQUEEZE_THRESHOLD_PCT": "0.03",
    "FAILED_BREAKDOWN_VOLUME_RATIO": "2.0",
    "FAILED_BREAKDOWN_RECAPTURE_BUFFER_PCT": "0.001",
}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="alpaca-bot-premarket")
    parser.add_argument("--scenario-dir", default="/data/scenarios",
                        help="Directory of *.json scenario files (default: /data/scenarios)")
    parser.add_argument("--validate-pct", type=float, default=0.2,
                        help="OOS fraction for IS/OOS split (default: 0.2)")
    parser.add_argument("--oos-gate-ratio", type=float, default=0.6,
                        help="Required OOS/IS score ratio to pass (default: 0.6)")
    parser.add_argument("--min-oos-score", type=float, default=0.2,
                        help="Minimum absolute OOS score to pass (default: 0.2)")
    parser.add_argument("--trading-mode", choices=["paper", "live"],
                        help="Override TRADING_MODE env var")
    args = parser.parse_args(list(argv) if argv is not None else sys.argv[1:])

    base_env = dict(os.environ)
    if args.trading_mode:
        base_env["TRADING_MODE"] = args.trading_mode

    try:
        Settings.from_env(base_env)
    except ValueError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 1

    scenario_dir = Path(args.scenario_dir)
    if not scenario_dir.exists():
        print(f"Warning: --scenario-dir {scenario_dir} does not exist — "
              "nightly may not have run yet. Skipping pre-market check.")
        return 0

    files = sorted(scenario_dir.glob("*.json"))
    if len(files) < 2:
        print(f"Warning: fewer than 2 scenario files in {scenario_dir} — "
              "nightly may not have run yet. Skipping pre-market check.")
        return 0

    all_scenarios = [ReplayRunner.load_scenario(f) for f in files]
    is_scenarios = []
    oos_scenarios = []
    for s in all_scenarios:
        is_s, oos_s = split_scenario(s, in_sample_ratio=1.0 - args.validate_pct)
        is_scenarios.append(is_s)
        oos_scenarios.append(oos_s)

    now = datetime.now(timezone.utc)
    print(f"\n── Pre-market check ({now.strftime('%Y-%m-%d %H:%M UTC')}) "
          "────────────────────────────")

    results: list[tuple[str, float | None, float | None, object | None, bool, str]] = []
    # (strat_name, is_score, oos_score, report, passed, fail_reason)

    for strat_name, strat_grid in STRATEGY_GRIDS.items():
        signal_evaluator = STRATEGY_REGISTRY[strat_name]
        constrained_grid = {
            k: [base_env.get(k, _PARAM_DEFAULTS.get(k, ""))]
            for k in strat_grid
        }

        candidates = run_multi_scenario_sweep(
            scenarios=is_scenarios,
            base_env=base_env,
            grid=constrained_grid,
            signal_evaluator=signal_evaluator,
        )
        if not candidates or candidates[0].score is None:
            results.append((strat_name, None, None, None, False, "no IS score"))
            continue

        cand = candidates[0]
        is_score = cand.score

        oos_scores_list = evaluate_candidates_oos(
            candidates=[cand],
            oos_scenarios=oos_scenarios,
            base_env=base_env,
            min_trades=3,
            signal_evaluator=signal_evaluator,
        )
        oos_score = oos_scores_list[0] if oos_scores_list else None
        report = cand.report

        passed, fail_reason = _check_gates(
            is_score=is_score,
            oos_score=oos_score,
            report=report,
            oos_gate_ratio=args.oos_gate_ratio,
            min_oos_score=args.min_oos_score,
        )
        results.append((strat_name, is_score, oos_score, report, passed, fail_reason))

    _print_results(results)

    n_pass = sum(1 for *_, passed, _ in results if passed)
    n_total = len(results)
    print(f"Overall: {n_pass}/{n_total} strategies pass pre-market gates.")

    if n_pass == n_total:
        print("✓ All clear.")
        return 0
    else:
        n_fail = n_total - n_pass
        print(f"⚠ WARNING: {n_fail} {'strategy' if n_fail == 1 else 'strategies'} "
              "failed pre-market check — review before market open.")
        return 1


def _check_gates(
    *,
    is_score: float | None,
    oos_score: float | None,
    report: object | None,
    oos_gate_ratio: float,
    min_oos_score: float,
) -> tuple[bool, str]:
    if oos_score is None:
        return False, "no OOS score"
    if is_score is not None and oos_score < is_score * oos_gate_ratio:
        return False, f"OOS/IS ratio {oos_score / is_score:.2f} < {oos_gate_ratio}"
    if oos_score < min_oos_score:
        return False, f"OOS {oos_score:.4f} < min {min_oos_score}"
    if report is not None:
        pf = getattr(report, "profit_factor", None)
        sharpe = getattr(report, "sharpe_ratio", None)
        if pf is not None and pf < 1.0:
            return False, f"profit_factor {pf:.2f} < 1.0"
        if sharpe is not None and sharpe <= 0:
            return False, f"sharpe {sharpe:.2f} ≤ 0"
    return True, ""


def _print_results(
    results: list[tuple[str, float | None, float | None, object | None, bool, str]],
) -> None:
    for strat_name, is_score, oos_score, report, passed, fail_reason in results:
        is_str = f"{is_score:.4f}" if is_score is not None else "—"
        oos_str = f"{oos_score:.4f}" if oos_score is not None else "—"
        pf = getattr(report, "profit_factor", None) if report else None
        sharpe = getattr(report, "sharpe_ratio", None) if report else None
        pf_str = f"{pf:.2f}" if pf is not None else "—"
        sharpe_str = f"{sharpe:.2f}" if sharpe is not None else "—"
        status = "✓ PASS" if passed else f"✗ FAIL  {fail_reason}"
        print(f"  {strat_name:<20s} IS={is_str}  OOS={oos_str}  "
              f"pf={pf_str}  sharpe={sharpe_str}  {status}")
```

- [ ] **Step 2: Run the five new tests**

```bash
pytest tests/unit/test_premarket_cli.py -v
```

Expected: 5 PASSED

- [ ] **Step 3: Run full suite**

```bash
pytest -x
```

Expected: all tests PASS

- [ ] **Step 4: Commit**

```bash
git add src/alpaca_bot/nightly/premarket_cli.py
git commit -m "feat: add alpaca-bot-premarket pre-market sanity check CLI"
```

---

## Task 5: Deployment wiring

**Files:**
- Modify: `pyproject.toml`
- Modify: `deploy/cron.d/alpaca-bot`

- [ ] **Step 1: Add entry point to `pyproject.toml`**

In `pyproject.toml`, add after the `alpaca-bot-nightly` line:

```toml
alpaca-bot-premarket = "alpaca_bot.nightly.premarket_cli:main"
```

So the `[project.scripts]` block ends with:
```toml
alpaca-bot-nightly = "alpaca_bot.nightly.cli:main"
alpaca-bot-premarket = "alpaca_bot.nightly.premarket_cli:main"
```

- [ ] **Step 2: Add cron line to `deploy/cron.d/alpaca-bot`**

Append to `deploy/cron.d/alpaca-bot`:

```
# Pre-market sanity check — runs at 13:30 UTC (8:30 AM ET) on weekdays
30 13 * * 1-5 root cd /workspace/alpaca_bot && docker compose -f deploy/compose.yaml run --rm nightly alpaca-bot-premarket >> /var/log/alpaca-bot-premarket.log 2>&1
```

- [ ] **Step 3: Verify entry point resolves**

```bash
pip install -e ".[dev]" -q && alpaca-bot-premarket --help
```

Expected: prints usage without error

- [ ] **Step 4: Run full suite one more time**

```bash
pytest
```

Expected: all tests PASS

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml deploy/cron.d/alpaca-bot
git commit -m "feat: register alpaca-bot-premarket entry point and cron schedule"
```

---

## Self-Review

**Spec coverage:**

| Spec requirement | Task |
|-----------------|------|
| `--strategies` replaces `--strategy` | Task 2 |
| `--strategies all` sweeps all `STRATEGY_GRIDS` keys | Task 1 test + Task 2 |
| `winners` list with OOS gate | Task 2 |
| Composite env: shared params from highest `_viability_key` | Task 1 test + Task 2 |
| Per-strategy summary table | Task 2 (`_print_strategy_results`) |
| DB `save_run()` per strategy with tagged `scenario_name` | Task 2 |
| No `candidate.env` when all strategies fail | Task 1 test + Task 2 |
| `alpaca-bot-premarket` entry point | Task 4 + Task 5 |
| Pre-market constrained grid from env vars | Task 3 test + Task 4 |
| Pre-market gates: OOS ratio, min OOS, profit_factor, sharpe | Task 3 test + Task 4 |
| Missing scenario dir → exit 0 | Task 3 test + Task 4 |
| exit 0 all pass, exit 1 any fail | Task 3 test + Task 4 |
| Cron `30 13 * * 1-5` | Task 5 |
| `pyproject.toml` entry point | Task 5 |

**No placeholders found.**

**Type consistency:** `winners` typed as `list[tuple[str, object, float]]` in cli.py (TuningCandidate is imported via sweep imports but not directly re-imported as a type annotation — `object` avoids a circular import concern). `_build_composite_env` accesses `candidate.params` which is valid for `TuningCandidate`. `_viability_key` accepts `TuningCandidate` — passing `object` at call sites will produce a runtime AttributeError if a non-TuningCandidate is passed, but the code only ever appends real `TuningCandidate` instances. To be safe, import `TuningCandidate` explicitly and annotate correctly:

In `src/alpaca_bot/nightly/cli.py`, add `TuningCandidate` to the sweep import:

```python
from alpaca_bot.tuning.sweep import (
    DEFAULT_GRID,
    STRATEGY_GRIDS,
    TuningCandidate,
    _viability_key,
    evaluate_candidates_oos,
    run_multi_scenario_sweep,
)
```

And type the winners list as:
```python
winners: list[tuple[str, TuningCandidate, float]] = []
```

This is already in sweep.py's public API (`TuningCandidate` is defined there and importable).
