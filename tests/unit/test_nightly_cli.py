from __future__ import annotations

import json
import sys


def _patch_env(monkeypatch):
    monkeypatch.setenv("TRADING_MODE", "paper")
    monkeypatch.setenv("ENABLE_LIVE_TRADING", "false")
    monkeypatch.setenv("STRATEGY_VERSION", "v1")
    monkeypatch.setenv("DATABASE_URL", "postgresql://dummy:dummy@localhost/dummy")
    monkeypatch.setenv("SYMBOLS", "AAPL")
    monkeypatch.setenv("MARKET_DATA_FEED", "sip")


def _make_scenario_files(tmp_path):
    for sym in ("AAPL", "MSFT"):
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


def _patch_common_db(monkeypatch, module, symbols=("AAPL", "MSFT")):
    """Patch connect_postgres, WatchlistStore, OrderStore, DailySessionStateStore."""
    monkeypatch.setattr(module, "connect_postgres", lambda url: object())

    class FakeWatchlistStore:
        def __init__(self, conn): pass
        def list_enabled(self, trading_mode): return list(symbols)

    monkeypatch.setattr(module, "WatchlistStore", FakeWatchlistStore)

    class FakeOrderStore:
        def __init__(self, conn): pass
        def list_closed_trades(self, **kw): return []

    monkeypatch.setattr(module, "OrderStore", FakeOrderStore)

    class FakeDailySessionStateStore:
        def __init__(self, conn): pass
        def load(self, **kw): return None

    monkeypatch.setattr(module, "DailySessionStateStore", FakeDailySessionStateStore)


def test_nightly_cli_runs_evolve_and_writes_output_env(monkeypatch, tmp_path):
    """Full happy-path: evolve finds a held candidate, writes output-env, returns 0."""
    from alpaca_bot.nightly import cli as module
    from alpaca_bot.tuning.sweep import TuningCandidate

    _patch_env(monkeypatch)
    _make_scenario_files(tmp_path)
    _patch_common_db(monkeypatch, module)

    monkeypatch.setattr(module, "split_scenario", _fake_split)

    cand = TuningCandidate(params={"BREAKOUT_LOOKBACK_BARS": "20"}, report=None, score=0.5)
    monkeypatch.setattr(module, "run_multi_scenario_sweep", lambda **kw: [cand])
    # OOS=0.4 >= IS=0.5 * 0.5=0.25 → held
    monkeypatch.setattr(module, "evaluate_candidates_oos",
                        lambda candidates, oos_scenarios, **kw: [0.4])

    output_env = tmp_path / "candidate.env"
    monkeypatch.setattr(sys, "argv", [
        "nightly", "--dry-run", "--no-db",
        "--output-dir", str(tmp_path),
        "--output-env", str(output_env),
    ])

    result = module.main()

    assert result == 0
    assert output_env.exists()
    assert "BREAKOUT_LOOKBACK_BARS=20" in output_env.read_text()


def test_nightly_cli_dry_run_skips_backfill(monkeypatch, tmp_path):
    """--dry-run must not instantiate AlpacaMarketDataAdapter or call fetch_and_save."""
    from alpaca_bot.nightly import cli as module
    from alpaca_bot.tuning.sweep import TuningCandidate

    _patch_env(monkeypatch)
    _make_scenario_files(tmp_path)
    _patch_common_db(monkeypatch, module)

    class FailingAdapter:
        @staticmethod
        def from_settings(settings):
            raise AssertionError("AlpacaMarketDataAdapter must not be called with --dry-run")

    monkeypatch.setattr(module, "AlpacaMarketDataAdapter", FailingAdapter)

    monkeypatch.setattr(module, "split_scenario", _fake_split)

    cand = TuningCandidate(params={"BREAKOUT_LOOKBACK_BARS": "20"}, report=None, score=0.5)
    monkeypatch.setattr(module, "run_multi_scenario_sweep", lambda **kw: [cand])
    monkeypatch.setattr(module, "evaluate_candidates_oos",
                        lambda candidates, oos_scenarios, **kw: [0.4])

    monkeypatch.setattr(sys, "argv", [
        "nightly", "--dry-run", "--no-db", "--output-dir", str(tmp_path),
    ])

    result = module.main()

    assert result == 0  # no exception → adapter was not called


def test_nightly_cli_no_watchlist_symbols_skips_evolve(monkeypatch, tmp_path):
    """Empty watchlist → skip backfill and evolve entirely, still run live report, return 0."""
    from alpaca_bot.nightly import cli as module

    _patch_env(monkeypatch)
    _patch_common_db(monkeypatch, module, symbols=[])  # no symbols

    evolve_called = []
    monkeypatch.setattr(module, "run_multi_scenario_sweep",
                        lambda **kw: evolve_called.append(True) or [])

    monkeypatch.setattr(sys, "argv", [
        "nightly", "--dry-run", "--no-db", "--output-dir", str(tmp_path),
    ])

    result = module.main()

    assert result == 0
    assert not evolve_called, "run_multi_scenario_sweep must not be called with empty watchlist"


def test_nightly_cli_no_held_candidates_continues_to_live_report(monkeypatch, tmp_path):
    """No OOS-held candidates → exit 0 (not 1), live report still runs."""
    from alpaca_bot.nightly import cli as module
    from alpaca_bot.tuning.sweep import TuningCandidate

    _patch_env(monkeypatch)
    _make_scenario_files(tmp_path)

    monkeypatch.setattr(module, "connect_postgres", lambda url: object())

    class FakeWatchlistStore:
        def __init__(self, conn): pass
        def list_enabled(self, trading_mode): return ["AAPL", "MSFT"]

    monkeypatch.setattr(module, "WatchlistStore", FakeWatchlistStore)

    live_report_called = []

    class FakeOrderStore:
        def __init__(self, conn): pass
        def list_closed_trades(self, **kw):
            live_report_called.append(True)
            return []

    monkeypatch.setattr(module, "OrderStore", FakeOrderStore)

    class FakeDailySessionStateStore:
        def __init__(self, conn): pass
        def load(self, **kw): return None

    monkeypatch.setattr(module, "DailySessionStateStore", FakeDailySessionStateStore)

    monkeypatch.setattr(module, "split_scenario", _fake_split)

    cand = TuningCandidate(params={"BREAKOUT_LOOKBACK_BARS": "20"}, report=None, score=0.5)
    monkeypatch.setattr(module, "run_multi_scenario_sweep", lambda **kw: [cand])
    # OOS=None → no held candidates
    monkeypatch.setattr(module, "evaluate_candidates_oos",
                        lambda candidates, oos_scenarios, **kw: [None])

    monkeypatch.setattr(sys, "argv", [
        "nightly", "--dry-run", "--no-db", "--output-dir", str(tmp_path),
    ])

    result = module.main()

    assert result == 0, "no held candidates must return 0 (unlike evolve CLI which returns 1)"
    assert live_report_called, "live report must still run after no-held-candidates"


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


def test_nightly_cli_too_few_scenario_files_returns_error(monkeypatch, tmp_path):
    """< 2 scenario files in output-dir with --dry-run must return 1 (hard error)."""
    from alpaca_bot.nightly import cli as module

    _patch_env(monkeypatch)
    # Only one file — below the 2-file minimum required for multi-scenario sweep
    (tmp_path / "AAPL_252d.json").write_text(json.dumps({
        "name": "AAPL_252d", "symbol": "AAPL",
        "starting_equity": 100000.0, "daily_bars": [], "intraday_bars": [],
    }))
    _patch_common_db(monkeypatch, module)

    monkeypatch.setattr(sys, "argv", [
        "nightly", "--dry-run", "--no-db", "--output-dir", str(tmp_path),
    ])

    result = module.main()

    assert result == 1
