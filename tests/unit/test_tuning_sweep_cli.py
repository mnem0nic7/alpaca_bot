from __future__ import annotations
import sys
import pytest


def _make_fake_scenario():
    from alpaca_bot.replay.runner import ReplayScenario
    return ReplayScenario(
        name="test",
        symbol="X",
        starting_equity=100_000.0,
        daily_bars=[],
        intraday_bars=[],
    )


def _patch_env(monkeypatch):
    monkeypatch.setenv("TRADING_MODE", "paper")
    monkeypatch.setenv("ENABLE_LIVE_TRADING", "false")
    monkeypatch.setenv("STRATEGY_VERSION", "v1")
    monkeypatch.setenv("DATABASE_URL", "postgresql://dummy:dummy@localhost/dummy")
    monkeypatch.setenv("SYMBOLS", "X")
    monkeypatch.setenv("MARKET_DATA_FEED", "sip")


def test_sweep_cli_default_strategy_is_breakout(monkeypatch, tmp_path):
    """With no --strategy flag, run_sweep receives breakout evaluator."""
    import json
    from alpaca_bot.tuning import sweep_cli as module
    from alpaca_bot.strategy import STRATEGY_REGISTRY

    _patch_env(monkeypatch)

    scenario_file = tmp_path / "SYM_252d.json"
    scenario_file.write_text(json.dumps({
        "name": "test", "symbol": "SYM", "starting_equity": 100000.0,
        "daily_bars": [], "intraday_bars": [],
    }))

    captured: list[dict] = []

    def fake_run_sweep(**kwargs):
        captured.append(kwargs)
        return []

    monkeypatch.setattr(module, "run_sweep", fake_run_sweep)
    monkeypatch.setattr(sys, "argv", ["sweep", "--scenario-dir", str(tmp_path)])

    try:
        module.main()
    except SystemExit:
        pass

    assert captured
    assert captured[0].get("signal_evaluator") is STRATEGY_REGISTRY["breakout"]


def test_sweep_cli_strategy_flag_passes_evaluator(monkeypatch, tmp_path):
    """--strategy momentum passes momentum evaluator to run_sweep."""
    import json
    from alpaca_bot.tuning import sweep_cli as module
    from alpaca_bot.strategy import STRATEGY_REGISTRY

    _patch_env(monkeypatch)

    scenario_file = tmp_path / "SYM_252d.json"
    scenario_file.write_text(json.dumps({
        "name": "test", "symbol": "SYM", "starting_equity": 100000.0,
        "daily_bars": [], "intraday_bars": [],
    }))

    captured: list[dict] = []

    def fake_run_sweep(**kwargs):
        captured.append(kwargs)
        return []

    monkeypatch.setattr(module, "run_sweep", fake_run_sweep)
    monkeypatch.setattr(sys, "argv", [
        "sweep", "--scenario-dir", str(tmp_path), "--strategy", "momentum"
    ])

    try:
        module.main()
    except SystemExit:
        pass

    assert captured
    assert captured[0].get("signal_evaluator") is STRATEGY_REGISTRY["momentum"]


def test_evolve_cli_scenario_dir_calls_multi_sweep(monkeypatch, tmp_path):
    """--scenario-dir with 2+ files calls run_multi_scenario_sweep, not run_sweep."""
    import json
    from alpaca_bot.tuning import cli as module

    _patch_env(monkeypatch)

    for name in ("SYM_A_252d.json", "SYM_B_252d.json"):
        (tmp_path / name).write_text(json.dumps({
            "name": name.replace(".json", ""), "symbol": "SYM", "starting_equity": 100000.0,
            "daily_bars": [], "intraday_bars": [],
        }))

    captured_multi: list[dict] = []
    captured_single: list[dict] = []

    monkeypatch.setattr(module, "run_multi_scenario_sweep", lambda **kw: captured_multi.append(kw) or [])
    monkeypatch.setattr(module, "run_sweep", lambda **kw: captured_single.append(kw) or [])
    monkeypatch.setattr(sys, "argv", ["evolve", "--scenario-dir", str(tmp_path), "--no-db"])

    try:
        module.main()
    except SystemExit:
        pass

    assert captured_multi, "run_multi_scenario_sweep was not called"
    assert not captured_single, "run_sweep should not be called when --scenario-dir is used"
    assert len(captured_multi[0]["scenarios"]) == 2


def test_evolve_cli_scenario_dir_requires_at_least_two_files(monkeypatch, tmp_path):
    """--scenario-dir with fewer than 2 JSON files exits with an error."""
    import json
    from alpaca_bot.tuning import cli as module

    _patch_env(monkeypatch)

    (tmp_path / "only_one.json").write_text(json.dumps({
        "name": "only_one", "symbol": "SYM", "starting_equity": 100000.0,
        "daily_bars": [], "intraday_bars": [],
    }))

    monkeypatch.setattr(sys, "argv", ["evolve", "--scenario-dir", str(tmp_path), "--no-db"])

    with pytest.raises(SystemExit):
        module.main()


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


def test_validate_pct_errors_with_single_scenario(monkeypatch, tmp_path):
    """--validate-pct combined with --scenario (single file) must exit with error."""
    import json
    from alpaca_bot.tuning import cli as module

    _patch_env(monkeypatch)

    scenario_file = tmp_path / "SYM_252d.json"
    scenario_file.write_text(json.dumps({
        "name": "test", "symbol": "SYM", "starting_equity": 100000.0,
        "daily_bars": [], "intraday_bars": [],
    }))

    monkeypatch.setattr(sys, "argv", [
        "evolve", "--scenario", str(scenario_file), "--validate-pct", "0.2", "--no-db",
    ])

    with pytest.raises(SystemExit):
        module.main()


def test_validate_pct_out_of_range(monkeypatch, tmp_path):
    """--validate-pct values outside (0.0, 1.0) must exit with error."""
    import json
    from alpaca_bot.tuning import cli as module

    _patch_env(monkeypatch)

    for name in ("SYM_A_252d.json", "SYM_B_252d.json"):
        (tmp_path / name).write_text(json.dumps({
            "name": name.replace(".json", ""), "symbol": "SYM", "starting_equity": 100000.0,
            "daily_bars": [], "intraday_bars": [],
        }))

    monkeypatch.setattr(sys, "argv", [
        "evolve", "--scenario-dir", str(tmp_path), "--validate-pct", "1.5", "--no-db",
    ])

    with pytest.raises(SystemExit):
        module.main()


def test_walk_forward_gate_selects_best_oos_held_candidate(monkeypatch, tmp_path):
    """When --validate-pct is used, best candidate is the highest-OOS-scoring held one."""
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
