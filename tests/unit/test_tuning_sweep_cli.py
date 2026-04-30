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
