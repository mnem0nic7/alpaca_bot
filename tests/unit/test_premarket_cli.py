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
    monkeypatch.setattr(module, "run_multi_scenario_sweep", lambda **kw: [failing_cand])
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
    from alpaca_bot.tuning.sweep import TuningCandidate

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
