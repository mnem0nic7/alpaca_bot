from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from types import SimpleNamespace


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
    """Patch DB-backed stores used by nightly tests."""
    monkeypatch.setattr(module, "connect_postgres", lambda url: object())

    class FakeWatchlistStore:
        def __init__(self, conn): pass
        def list_enabled(self, trading_mode): return list(symbols)
        def list_ignored(self, trading_mode): return []

    monkeypatch.setattr(module, "WatchlistStore", FakeWatchlistStore)

    class FakeStrategyFlagStore:
        def __init__(self, conn): pass
        def list_all(self, *, trading_mode, strategy_version):
            return [SimpleNamespace(strategy_name="breakout", enabled=True)]

    monkeypatch.setattr(module, "StrategyFlagStore", FakeStrategyFlagStore)

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
    sweep_calls: list[dict] = []

    def fake_sweep(**kw):
        sweep_calls.append(kw)
        return [cand]

    monkeypatch.setattr(module, "run_multi_scenario_sweep", fake_sweep)
    # OOS=0.4 >= IS=0.5 * 0.5=0.25 → held
    oos_calls: list[dict] = []

    def fake_oos(candidates, oos_scenarios, **kw):
        oos_calls.append(kw)
        return [0.4]

    monkeypatch.setattr(module, "evaluate_candidates_oos", fake_oos)

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
    assert sweep_calls[0]["aggregate"] == "pooled"
    assert sweep_calls[0]["min_trades_per_scenario"] == 3
    assert sweep_calls[0]["max_combos"] == 0
    assert oos_calls[0]["aggregate"] == "pooled"


def test_nightly_cli_forwards_max_combos(monkeypatch, tmp_path):
    """--max-combos is passed to the pooled sweep so long runs can be capped."""
    from alpaca_bot.nightly import cli as module
    from alpaca_bot.tuning.sweep import TuningCandidate

    _patch_env(monkeypatch)
    _make_scenario_files(tmp_path)
    _patch_common_db(monkeypatch, module)
    monkeypatch.setattr(module, "split_scenario", _fake_split)

    cand = TuningCandidate(params={"BREAKOUT_LOOKBACK_BARS": "20"}, report=None, score=0.5)
    sweep_calls: list[dict] = []

    def fake_sweep(**kw):
        sweep_calls.append(kw)
        return [cand]

    monkeypatch.setattr(module, "run_multi_scenario_sweep", fake_sweep)
    monkeypatch.setattr(module, "evaluate_candidates_oos",
                        lambda candidates, oos_scenarios, **kw: [0.4])

    monkeypatch.setattr(sys, "argv", [
        "nightly", "--dry-run", "--no-db",
        "--output-dir", str(tmp_path),
        "--strategies", "breakout",
        "--max-combos", "5",
    ])

    result = module.main()

    assert result == 0
    assert sweep_calls[0]["max_combos"] == 5
    assert callable(sweep_calls[0]["on_progress"])


def test_nightly_cli_resolves_fractionable_symbols_for_live_sweep(monkeypatch, tmp_path):
    """Scheduled nightly sweeps should model Alpaca paper fractionability."""
    from alpaca_bot.nightly import cli as module
    from alpaca_bot.tuning.sweep import TuningCandidate

    _patch_env(monkeypatch)
    _make_scenario_files(tmp_path)
    _patch_common_db(monkeypatch, module)
    monkeypatch.setattr(module, "split_scenario", _fake_split)

    class FakeMarketDataAdapter:
        @staticmethod
        def from_settings(settings):
            return object()

    class FakeFetcher:
        def __init__(self, adapter, settings):
            pass

        def fetch_and_save(self, *, symbols, days, output_dir):
            return [
                (module.Path(output_dir) / f"{symbol}_252d.json", 0, 0)
                for symbol in symbols
            ]

    class FakeExecutionAdapter:
        @staticmethod
        def from_settings(settings):
            return FakeExecutionAdapter()

        def get_fractionable_symbols(self, symbols):
            assert tuple(symbols) == ("AAPL", "MSFT")
            return frozenset({"AAPL"})

    monkeypatch.setattr(module, "AlpacaMarketDataAdapter", FakeMarketDataAdapter)
    monkeypatch.setattr(module, "BackfillFetcher", FakeFetcher)
    monkeypatch.setattr(module, "AlpacaExecutionAdapter", FakeExecutionAdapter)

    cand = TuningCandidate(params={"BREAKOUT_LOOKBACK_BARS": "20"}, report=None, score=0.5)
    sweep_calls: list[dict] = []
    oos_calls: list[dict] = []

    def fake_sweep(**kw):
        sweep_calls.append(kw)
        return [cand]

    def fake_oos(candidates, oos_scenarios, **kw):
        oos_calls.append(kw)
        return [None]

    monkeypatch.setattr(module, "run_multi_scenario_sweep", fake_sweep)
    monkeypatch.setattr(module, "evaluate_candidates_oos", fake_oos)
    monkeypatch.setattr(sys, "argv", [
        "nightly", "--no-db",
        "--output-dir", str(tmp_path),
        "--strategies", "breakout",
    ])

    result = module.main()

    assert result == 0
    assert sweep_calls[0]["fractionable_symbols"] == frozenset({"AAPL"})
    assert oos_calls[0]["fractionable_symbols"] == frozenset({"AAPL"})


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
        "--strategies", "breakout",
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


def test_nightly_cli_excludes_ignored_watchlist_symbols(monkeypatch, tmp_path):
    """Ignored watchlist rows should not be backfilled or evolved."""
    from alpaca_bot.nightly import cli as module

    _patch_env(monkeypatch)
    _make_scenario_files(tmp_path)
    monkeypatch.setattr(module, "connect_postgres", lambda url: object())

    class FakeWatchlistStore:
        def __init__(self, conn): pass
        def list_enabled(self, trading_mode): return ["AAPL", "MSFT", "HEIA"]
        def list_ignored(self, trading_mode): return ["HEIA"]

    monkeypatch.setattr(module, "WatchlistStore", FakeWatchlistStore)
    monkeypatch.setattr(module, "split_scenario", _fake_split)

    seen_symbols = []

    class FakeAdapter:
        @staticmethod
        def from_settings(settings): return object()

    class FakeFetcher:
        def __init__(self, adapter, settings): pass
        def fetch_and_save(self, *, symbols, days, output_dir, starting_equity=100_000.0):
            seen_symbols.extend(symbols)
            return []

    monkeypatch.setattr(module, "AlpacaMarketDataAdapter", FakeAdapter)
    monkeypatch.setattr(module, "BackfillFetcher", FakeFetcher)
    monkeypatch.setattr(sys, "argv", [
        "nightly", "--no-db", "--output-dir", str(tmp_path),
    ])

    result = module.main()

    assert result == 1
    assert seen_symbols == ["AAPL", "MSFT"]


def test_nightly_cli_evolves_only_active_watchlist_scenarios(monkeypatch, tmp_path):
    """Leftover ignored-symbol scenario files must not influence nightly sweeps."""
    from alpaca_bot.nightly import cli as module
    from alpaca_bot.tuning.sweep import TuningCandidate

    _patch_env(monkeypatch)
    _make_scenario_files(tmp_path)
    (tmp_path / "HEIA_252d.json").write_text(json.dumps({
        "name": "HEIA_252d", "symbol": "HEIA",
        "starting_equity": 100000.0, "daily_bars": [], "intraday_bars": [],
    }))
    monkeypatch.setattr(module, "connect_postgres", lambda url: object())

    class FakeWatchlistStore:
        def __init__(self, conn): pass
        def list_enabled(self, trading_mode): return ["AAPL", "MSFT", "HEIA"]
        def list_ignored(self, trading_mode): return ["HEIA"]

    monkeypatch.setattr(module, "WatchlistStore", FakeWatchlistStore)

    class FakeOrderStore:
        def __init__(self, conn): pass
        def list_closed_trades(self, **kw): return []

    monkeypatch.setattr(module, "OrderStore", FakeOrderStore)

    class FakeDailySessionStateStore:
        def __init__(self, conn): pass
        def load(self, **kw): return None

    monkeypatch.setattr(module, "DailySessionStateStore", FakeDailySessionStateStore)
    monkeypatch.setattr(module, "split_scenario", _fake_split)

    swept_symbols: list[str] = []

    def fake_sweep(**kw):
        swept_symbols.extend(s.symbol for s in kw["scenarios"])
        return [TuningCandidate(params={"BREAKOUT_LOOKBACK_BARS": "20"}, report=None, score=0.5)]

    monkeypatch.setattr(module, "run_multi_scenario_sweep", fake_sweep)
    monkeypatch.setattr(module, "evaluate_candidates_oos",
                        lambda candidates, oos_scenarios, **kw: [None])
    monkeypatch.setattr(sys, "argv", [
        "nightly", "--dry-run", "--no-db", "--output-dir", str(tmp_path),
        "--strategies", "breakout",
    ])

    result = module.main()

    assert result == 0
    assert swept_symbols == ["AAPL", "MSFT"]


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

    class FakeStrategyFlagStore:
        def __init__(self, conn): pass
        def list_all(self, *, trading_mode, strategy_version):
            return [SimpleNamespace(strategy_name="breakout", enabled=True)]

    monkeypatch.setattr(module, "StrategyFlagStore", FakeStrategyFlagStore)

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
        "--strategies", "breakout",
    ])

    result = module.main()

    assert result == 0
    surrogate = surrogate_kwargs.get("surrogate")
    assert surrogate is not None, "surrogate must be passed to run_multi_scenario_sweep"
    assert surrogate.is_fitted, "surrogate must be fitted when 60 records are available"


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


def test_nightly_proof_guard_blocks_held_candidate(monkeypatch, tmp_path):
    """--proof-guard rejects an OOS-held candidate when proof metrics regress."""
    from alpaca_bot.nightly import cli as module
    from alpaca_bot.tuning.sweep import TuningCandidate

    _patch_env(monkeypatch)
    _make_scenario_files(tmp_path)
    _patch_common_db(monkeypatch, module)
    monkeypatch.setattr(module, "split_scenario", _fake_split)

    cand = TuningCandidate(params={"BREAKOUT_LOOKBACK_BARS": "20"}, report=None, score=0.5)
    monkeypatch.setattr(module, "run_multi_scenario_sweep", lambda **kw: [cand])
    monkeypatch.setattr(module, "evaluate_candidates_oos",
                        lambda candidates, oos_scenarios, **kw: [0.4])
    monkeypatch.setattr(module, "_select_proof_guarded_candidate", lambda **kw: None)

    output_env = tmp_path / "candidate.env"
    output_env.write_text("BREAKOUT_LOOKBACK_BARS=10\n")
    monkeypatch.setattr(sys, "argv", [
        "nightly", "--dry-run", "--no-db",
        "--output-dir", str(tmp_path),
        "--output-env", str(output_env),
        "--proof-guard",
    ])

    result = module.main()

    assert result == 0
    assert not output_env.exists()


def test_proof_guard_regressions_rejects_weaker_metrics():
    from alpaca_bot.nightly import cli as module

    baseline = module._ProofGuardMetrics(
        trades=100,
        total_pnl=100.0,
        eventual_pass_rate=0.99,
        first_threshold_pass_rate=0.62,
        p95_sessions_to_pass=21,
        slowest_sessions_to_pass=30,
    )
    candidate = module._ProofGuardMetrics(
        trades=130,
        total_pnl=75.0,
        eventual_pass_rate=0.99,
        first_threshold_pass_rate=0.58,
        p95_sessions_to_pass=23,
        slowest_sessions_to_pass=38,
    )

    regressions = module._proof_guard_regressions(
        baseline=baseline,
        candidate=candidate,
    )

    assert regressions == [
        "total_pnl 75.00 < baseline 100.00",
        "first_threshold_pass_rate 58.00% < baseline 62.00%",
        "p95_sessions_to_pass 23 > baseline 21",
        "slowest_sessions_to_pass 38 > baseline 30",
    ]


def test_proof_guard_forwards_fractionable_symbols(monkeypatch):
    from alpaca_bot.nightly import cli as module
    from alpaca_bot.replay.report import BacktestReport
    from alpaca_bot.tuning.sweep import TuningCandidate

    _patch_env(monkeypatch)
    report = BacktestReport(
        trades=(),
        total_trades=0,
        winning_trades=0,
        losing_trades=0,
        win_rate=None,
        mean_return_pct=None,
        max_drawdown_pct=None,
        sharpe_ratio=None,
    )
    calls: list[dict] = []

    def fake_pooled_report(**kw):
        calls.append(kw)
        return report

    monkeypatch.setattr(module, "_pooled_report", fake_pooled_report)
    scenario = SimpleNamespace(
        intraday_bars=[
            SimpleNamespace(timestamp=datetime(2026, 6, 29, 14, 0, tzinfo=timezone.utc))
        ]
    )
    candidate = TuningCandidate(
        params={"BREAKOUT_LOOKBACK_BARS": "20"},
        report=report,
        score=0.5,
    )

    selected = module._select_proof_guarded_candidate(
        held_pairs=[(candidate, 0.4)],
        scenarios=[scenario],
        base_env=dict(os.environ),
        signal_evaluator=None,
        strategy_name="breakout",
        fractionable_symbols=frozenset({"AAPL"}),
    )

    assert selected == (candidate, 0.4)
    assert [call["settings"].fractionable_symbols for call in calls] == [
        frozenset({"AAPL"}),
        frozenset({"AAPL"}),
    ]


def test_nightly_viability_tiebreak_picks_higher_r(monkeypatch, tmp_path):
    """When two held candidates have equal OOS score, the one with higher R-multiple wins."""
    from alpaca_bot.nightly import cli as module
    from alpaca_bot.tuning.sweep import TuningCandidate
    from alpaca_bot.replay.report import BacktestReport

    _patch_env(monkeypatch)
    _make_scenario_files(tmp_path)
    _patch_common_db(monkeypatch, module)

    monkeypatch.setattr(module, "split_scenario", _fake_split)

    low_r_report = BacktestReport(
        trades=(), total_trades=5, winning_trades=3, losing_trades=2,
        win_rate=0.6, mean_return_pct=0.02, max_drawdown_pct=None, sharpe_ratio=0.5,
        avg_win_return_pct=0.025, avg_loss_return_pct=-0.02,
    )
    high_r_report = BacktestReport(
        trades=(), total_trades=5, winning_trades=3, losing_trades=2,
        win_rate=0.6, mean_return_pct=0.02, max_drawdown_pct=None, sharpe_ratio=0.5,
        avg_win_return_pct=0.05, avg_loss_return_pct=-0.014,
    )
    # cand_low_r listed first so old max(pair[1]) would pick it on tie
    cand_low_r = TuningCandidate(
        params={"BREAKOUT_LOOKBACK_BARS": "15"}, report=low_r_report, score=0.5
    )
    cand_high_r = TuningCandidate(
        params={"BREAKOUT_LOOKBACK_BARS": "30"}, report=high_r_report, score=0.5
    )

    monkeypatch.setattr(module, "run_multi_scenario_sweep", lambda **kw: [cand_low_r, cand_high_r])
    # Both pass OOS gate: OOS=0.4 >= IS=0.5 * ratio=0.6 → 0.4 >= 0.3 ✓, and 0.4 >= min_oos=0.2 ✓
    monkeypatch.setattr(module, "evaluate_candidates_oos",
                        lambda candidates, oos_scenarios, **kw: [0.4, 0.4])

    output_env = tmp_path / "candidate.env"
    monkeypatch.setattr(sys, "argv", [
        "nightly", "--dry-run", "--no-db",
        "--output-dir", str(tmp_path),
        "--output-env", str(output_env),
    ])

    result = module.main()

    assert result == 0
    assert output_env.exists()
    assert "BREAKOUT_LOOKBACK_BARS=30" in output_env.read_text(), \
        "higher R-multiple candidate (LOOKBACK=30) must be selected over lower R (LOOKBACK=15)"


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


def test_nightly_default_sweeps_enabled_strategy_flags(monkeypatch, tmp_path):
    """Default --strategies enabled sweeps only enabled strategy flags."""
    from alpaca_bot.nightly import cli as module
    from alpaca_bot.tuning.sweep import STRATEGY_GRIDS, TuningCandidate

    _patch_env(monkeypatch)
    _make_scenario_files(tmp_path)
    _patch_common_db(monkeypatch, module)
    monkeypatch.setattr(module, "split_scenario", _fake_split)

    sweep_count = 0

    def fake_sweep(**kw):
        nonlocal sweep_count
        sweep_count += 1
        assert kw["grid"] == STRATEGY_GRIDS["breakout"]
        return [TuningCandidate(params={"RELATIVE_VOLUME_THRESHOLD": "1.5"}, report=None, score=0.3)]

    monkeypatch.setattr(module, "run_multi_scenario_sweep", fake_sweep)
    monkeypatch.setattr(module, "evaluate_candidates_oos",
                        lambda candidates, oos_scenarios, **kw: [0.25])

    monkeypatch.setattr(sys, "argv", [
        "nightly", "--dry-run", "--no-db", "--output-dir", str(tmp_path),
    ])

    result = module.main()

    assert result == 0
    assert sweep_count == 1


def test_nightly_enabled_strategy_falls_back_to_all_without_flags() -> None:
    """Fresh installs with no strategy flags preserve the old all-strategy default."""
    from alpaca_bot.nightly.cli import _resolve_strategies
    from alpaca_bot.tuning.sweep import STRATEGY_GRIDS

    assert _resolve_strategies("enabled", enabled_strategy_names=()) == list(STRATEGY_GRIDS)


def test_nightly_publishes_enabled_winner_weights(monkeypatch, tmp_path):
    """Normal nightly persists enabled held winners as runtime strategy weights."""
    from alpaca_bot.nightly import cli as module
    from alpaca_bot.tuning.sweep import TuningCandidate

    _patch_env(monkeypatch)
    _make_scenario_files(tmp_path)
    monkeypatch.setattr(module, "split_scenario", _fake_split)

    class FakeConn:
        def __init__(self):
            self.commits = 0
            self.rollbacks = 0

        def commit(self):
            self.commits += 1

        def rollback(self):
            self.rollbacks += 1

    conn = FakeConn()
    monkeypatch.setattr(module, "connect_postgres", lambda url: conn)

    class FakeWatchlistStore:
        def __init__(self, conn): pass
        def list_enabled(self, trading_mode): return ["AAPL", "MSFT"]
        def list_ignored(self, trading_mode): return []

    class FakeStrategyFlagStore:
        def __init__(self, conn): pass
        def list_all(self, *, trading_mode, strategy_version):
            return [
                SimpleNamespace(strategy_name="bull_flag", enabled=True),
                SimpleNamespace(strategy_name="breakout", enabled=False),
            ]

    class FakeTuningResultStore:
        def __init__(self, conn): pass
        def load_all_scored(self, *, trading_mode, limit=5000): return []
        def save_run(self, **kw): return "run-id"

    class FakeOrderStore:
        def __init__(self, conn): pass
        def list_closed_trades(self, **kw): return []

    class FakeDailySessionStateStore:
        def __init__(self, conn): pass
        def load(self, **kw): return None

    upsert_calls: list[dict] = []

    class FakeStrategyWeightStore:
        def __init__(self, conn): pass
        def upsert_many(self, **kw):
            upsert_calls.append(kw)

    events: list = []

    class FakeAuditEventStore:
        def __init__(self, conn): pass
        def append(self, event, *, commit=True):
            events.append(event)

    monkeypatch.setattr(module, "WatchlistStore", FakeWatchlistStore)
    monkeypatch.setattr(module, "StrategyFlagStore", FakeStrategyFlagStore)
    monkeypatch.setattr(module, "TuningResultStore", FakeTuningResultStore)
    monkeypatch.setattr(module, "OrderStore", FakeOrderStore)
    monkeypatch.setattr(module, "DailySessionStateStore", FakeDailySessionStateStore)
    monkeypatch.setattr(module, "StrategyWeightStore", FakeStrategyWeightStore)
    monkeypatch.setattr(module, "AuditEventStore", FakeAuditEventStore)

    cand = TuningCandidate(
        params={
            "BULL_FLAG_MIN_RUN_PCT": "0.02",
            "BULL_FLAG_CONSOLIDATION_RANGE_PCT": "0.5",
        },
        report=None,
        score=1.2,
    )
    monkeypatch.setattr(module, "run_multi_scenario_sweep", lambda **kw: [cand])
    monkeypatch.setattr(
        module,
        "evaluate_candidates_oos",
        lambda candidates, oos_scenarios, **kw: [1.75],
    )

    monkeypatch.setattr(sys, "argv", [
        "nightly", "--dry-run",
        "--output-dir", str(tmp_path),
        "--strategies", "bull_flag",
        "--prune-keep-days", "0",
    ])

    result = module.main()

    assert result == 0
    assert len(upsert_calls) == 1
    call = upsert_calls[0]
    assert call["weights"] == {"bull_flag": 1.0}
    assert call["sharpes"] == {"bull_flag": 1.75}
    assert call["trading_mode"].value == "paper"
    assert call["strategy_version"] == "v1"
    assert call["commit"] is False
    assert conn.commits == 1
    updated = [e for e in events if e.event_type == "strategy_weights_updated"]
    assert len(updated) == 1
    assert updated[0].payload["source"] == "nightly_pooled_oos"
    assert updated[0].payload["sharpes"] == {"bull_flag": 1.75}


def test_publish_winner_weights_skips_when_enabled_strategy_lacks_winner(monkeypatch):
    """Weight publishing must not delete active rows unless all enabled strategies won."""
    from alpaca_bot.nightly import cli as module
    from alpaca_bot.tuning.sweep import TuningCandidate

    _patch_env(monkeypatch)
    settings = module.Settings.from_env(dict(os.environ))

    class FakeStrategyFlagStore:
        def __init__(self, conn): pass
        def list_all(self, *, trading_mode, strategy_version):
            return [
                SimpleNamespace(strategy_name="breakout", enabled=True),
                SimpleNamespace(strategy_name="momentum", enabled=True),
            ]

    class FailingStrategyWeightStore:
        def __init__(self, conn):
            raise AssertionError("StrategyWeightStore must not be used when a winner is missing")

    events: list = []

    class FakeAuditEventStore:
        def __init__(self, conn): pass
        def append(self, event, *, commit=True):
            events.append(event)

    monkeypatch.setattr(module, "StrategyFlagStore", FakeStrategyFlagStore)
    monkeypatch.setattr(module, "StrategyWeightStore", FailingStrategyWeightStore)
    monkeypatch.setattr(module, "AuditEventStore", FakeAuditEventStore)

    cand = TuningCandidate(params={"BREAKOUT_LOOKBACK_BARS": "20"}, report=None, score=0.5)
    result = module._publish_winner_strategy_weights(
        conn=object(),
        settings=settings,
        strategy_names=["breakout", "momentum"],
        winners=[("breakout", cand, 0.4)],
        computed_at=datetime(2026, 6, 28, tzinfo=timezone.utc),
    )

    assert result is False
    assert len(events) == 1
    assert events[0].event_type == "nightly_strategy_weights_skipped"
    assert events[0].payload["reason"] == "active_strategy_without_held_winner"
    assert events[0].payload["missing_strategies"] == ["momentum"]


def test_nightly_composite_env_shared_params_from_highest_scorer(monkeypatch, tmp_path):
    """Shared keys come from the highest-_viability_key winner."""
    from alpaca_bot.nightly import cli as module
    from alpaca_bot.tuning.sweep import TuningCandidate

    _patch_env(monkeypatch)
    _make_scenario_files(tmp_path)
    _patch_common_db(monkeypatch, module)
    monkeypatch.setattr(module, "split_scenario", _fake_split)

    # breakout wins with score=0.5/OOS=0.4, momentum wins with score=0.2/OOS=0.15
    # RELATIVE_VOLUME_THRESHOLD is shared — should come from breakout (higher rank)
    call_count = [0]

    def fake_sweep(**kw):
        call_count[0] += 1
        if call_count[0] == 1:  # first strategy (breakout)
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
    # breakout OOS=0.4, momentum OOS=0.15
    # With --min-oos-score 0.1: both pass (0.4>=0.1, 0.15>=0.1)
    # Without it: momentum 0.15 < default 0.2 → not held → only breakout in composite
    monkeypatch.setattr(module, "evaluate_candidates_oos",
                        lambda candidates, oos_scenarios, **kw: [0.4] if call_count[0] <= 1 else [0.15])

    output_env = tmp_path / "candidate.env"
    monkeypatch.setattr(sys, "argv", [
        "nightly", "--dry-run", "--no-db",
        "--output-dir", str(tmp_path),
        "--output-env", str(output_env),
        "--strategies", "breakout,momentum",
        "--min-oos-score", "0.1",  # lower floor so momentum (OOS=0.15) is also held
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

    sweep_call = [0]

    def fake_sweep(**kw):
        sweep_call[0] += 1
        if sweep_call[0] == 1:
            # breakout-specific params — must NOT appear in composite (this strategy fails OOS)
            return [TuningCandidate(
                params={"BREAKOUT_LOOKBACK_BARS": "20", "RELATIVE_VOLUME_THRESHOLD": "1.5",
                        "DAILY_SMA_PERIOD": "20"},
                report=None, score=0.4,
            )]
        else:
            # momentum-specific params — must appear in composite (this strategy passes OOS)
            return [TuningCandidate(
                params={"PRIOR_DAY_HIGH_LOOKBACK_BARS": "2", "RELATIVE_VOLUME_THRESHOLD": "1.5",
                        "ATR_STOP_MULTIPLIER": "1.0"},
                report=None, score=0.4,
            )]

    monkeypatch.setattr(module, "run_multi_scenario_sweep", fake_sweep)

    oos_call = [0]

    def fake_oos(candidates, oos_scenarios, **kw):
        oos_call[0] += 1
        # First strategy (breakout) fails OOS gate; second (momentum) passes
        return [None] if oos_call[0] == 1 else [0.3]

    monkeypatch.setattr(module, "evaluate_candidates_oos", fake_oos)

    output_env = tmp_path / "candidate.env"
    monkeypatch.setattr(sys, "argv", [
        "nightly", "--dry-run", "--no-db",
        "--output-dir", str(tmp_path),
        "--output-env", str(output_env),
        "--strategies", "breakout,momentum",
        "--min-oos-score", "0.1",
    ])

    result = module.main()

    assert result == 0
    assert output_env.exists()
    content = output_env.read_text()
    # breakout (first, OOS=None) must NOT contribute its unique param
    assert "BREAKOUT_LOOKBACK_BARS" not in content, (
        "Strategy with no held candidates must not appear in composite env"
    )
    # momentum's unique param must be present
    assert "PRIOR_DAY_HIGH_LOOKBACK_BARS=2" in content


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
    output_env.write_text("RELATIVE_VOLUME_THRESHOLD=1.5\n")
    monkeypatch.setattr(sys, "argv", [
        "nightly", "--dry-run", "--no-db",
        "--output-dir", str(tmp_path),
        "--output-env", str(output_env),
        "--strategies", "breakout,momentum",
    ])

    result = module.main()

    assert result == 0
    assert not output_env.exists(), "No candidate.env must be written when all strategies fail OOS gate"


def test_nightly_cli_writes_audit_event_after_sweep(monkeypatch, tmp_path):
    """nightly_sweep_completed AuditEvent is written after the strategy sweep, win or lose."""
    from alpaca_bot.nightly import cli as module
    from alpaca_bot.tuning.sweep import TuningCandidate

    _patch_env(monkeypatch)
    _make_scenario_files(tmp_path)
    _patch_common_db(monkeypatch, module)
    monkeypatch.setattr(module, "split_scenario", _fake_split)

    cand = TuningCandidate(params={"BREAKOUT_LOOKBACK_BARS": "20"}, report=None, score=0.5)
    monkeypatch.setattr(module, "run_multi_scenario_sweep", lambda **kw: [cand])
    monkeypatch.setattr(module, "evaluate_candidates_oos",
                        lambda candidates, oos_scenarios, **kw: [0.4])

    appended_events: list = []

    class FakeAuditEventStore:
        def __init__(self, conn): pass
        def append(self, event, *, commit=True):
            appended_events.append(event)

    monkeypatch.setattr(module, "AuditEventStore", FakeAuditEventStore)

    output_env = tmp_path / "candidate.env"
    monkeypatch.setattr(sys, "argv", [
        "nightly", "--dry-run", "--no-db",
        "--output-dir", str(tmp_path),
        "--output-env", str(output_env),
        "--strategies", "breakout",
    ])

    result = module.main()

    assert result == 0
    sweep_events = [e for e in appended_events if e.event_type == "nightly_sweep_completed"]
    assert len(sweep_events) == 1, f"expected 1 nightly_sweep_completed event, got {len(sweep_events)}"
    payload = sweep_events[0].payload
    assert payload["strategy_count"] == 1
    assert payload["candidates_accepted"] == 1
    assert payload["best_strategy"] == "breakout"
    assert payload["candidate_env_written"] is True
    assert "best_score" in payload


def test_nightly_publish_winner_strategy_weights_uses_enabled_scores(monkeypatch):
    """Nightly weights are proportional to held pooled OOS scores for enabled strategies."""
    from alpaca_bot.config import TradingMode
    from alpaca_bot.nightly import cli as module
    from alpaca_bot.tuning.sweep import TuningCandidate

    class FakeConn:
        def __init__(self):
            self.commits = 0

        def commit(self):
            self.commits += 1

    class FakeStrategyFlagStore:
        def __init__(self, conn): pass
        def list_all(self, *, trading_mode, strategy_version):
            return [
                SimpleNamespace(strategy_name="breakout", enabled=True),
                SimpleNamespace(strategy_name="momentum", enabled=True),
                SimpleNamespace(strategy_name="orb", enabled=False),
            ]

    weight_calls = []

    class FakeStrategyWeightStore:
        def __init__(self, conn): pass
        def upsert_many(self, **kw):
            weight_calls.append(kw)

    audit_events = []

    class FakeAuditEventStore:
        def __init__(self, conn): pass
        def append(self, event, *, commit=True):
            audit_events.append((event, commit))

    monkeypatch.setattr(module, "StrategyFlagStore", FakeStrategyFlagStore)
    monkeypatch.setattr(module, "StrategyWeightStore", FakeStrategyWeightStore)
    monkeypatch.setattr(module, "AuditEventStore", FakeAuditEventStore)

    conn = FakeConn()
    now = datetime(2026, 6, 28, 12, 0, tzinfo=timezone.utc)
    settings = SimpleNamespace(trading_mode=TradingMode.PAPER, strategy_version="v1")
    winners = [
        ("breakout", TuningCandidate(params={}, report=None, score=0.5), 0.6),
        ("momentum", TuningCandidate(params={}, report=None, score=0.3), 0.3),
        ("orb", TuningCandidate(params={}, report=None, score=0.8), 0.9),
    ]

    published = module._publish_winner_strategy_weights(
        conn=conn,
        settings=settings,
        strategy_names=["breakout", "momentum", "orb"],
        winners=winners,
        computed_at=now,
    )

    assert published is True
    assert conn.commits == 1
    assert len(weight_calls) == 1
    assert weight_calls[0]["commit"] is False
    assert set(weight_calls[0]["weights"]) == {"breakout", "momentum"}
    assert abs(weight_calls[0]["weights"]["breakout"] - (2 / 3)) < 1e-9
    assert abs(weight_calls[0]["weights"]["momentum"] - (1 / 3)) < 1e-9
    assert weight_calls[0]["sharpes"] == {"breakout": 0.6, "momentum": 0.3}
    assert audit_events[0][0].event_type == "strategy_weights_updated"
    assert audit_events[0][1] is False
    assert audit_events[0][0].payload["active_strategies"] == ["breakout", "momentum"]


def test_nightly_publish_winner_strategy_weights_skips_missing_active(monkeypatch):
    """Do not overwrite stored weights unless every active strategy has a held winner."""
    from alpaca_bot.config import TradingMode
    from alpaca_bot.nightly import cli as module
    from alpaca_bot.tuning.sweep import TuningCandidate

    class FakeStrategyFlagStore:
        def __init__(self, conn): pass
        def list_all(self, *, trading_mode, strategy_version):
            return [
                SimpleNamespace(strategy_name="breakout", enabled=True),
                SimpleNamespace(strategy_name="momentum", enabled=True),
            ]

    weight_calls = []

    class FakeStrategyWeightStore:
        def __init__(self, conn): pass
        def upsert_many(self, **kw):
            weight_calls.append(kw)

    audit_events = []

    class FakeAuditEventStore:
        def __init__(self, conn): pass
        def append(self, event, *, commit=True):
            audit_events.append((event, commit))

    monkeypatch.setattr(module, "StrategyFlagStore", FakeStrategyFlagStore)
    monkeypatch.setattr(module, "StrategyWeightStore", FakeStrategyWeightStore)
    monkeypatch.setattr(module, "AuditEventStore", FakeAuditEventStore)

    now = datetime(2026, 6, 28, 12, 0, tzinfo=timezone.utc)
    settings = SimpleNamespace(trading_mode=TradingMode.PAPER, strategy_version="v1")

    published = module._publish_winner_strategy_weights(
        conn=object(),
        settings=settings,
        strategy_names=["breakout", "momentum"],
        winners=[("breakout", TuningCandidate(params={}, report=None, score=0.5), 0.6)],
        computed_at=now,
    )

    assert published is False
    assert weight_calls == []
    assert audit_events[0][0].event_type == "nightly_strategy_weights_skipped"
    assert audit_events[0][0].payload["reason"] == "active_strategy_without_held_winner"
    assert audit_events[0][0].payload["missing_strategies"] == ["momentum"]
    assert audit_events[0][1] is True


def test_nightly_cli_prunes_decision_log_by_default(monkeypatch, tmp_path):
    """Without --no-db, the pipeline prunes decision_log and audits the count."""
    from alpaca_bot.nightly import cli as module

    _patch_env(monkeypatch)
    _patch_common_db(monkeypatch, module, symbols=[])  # skip backfill/evolve

    prune_calls = []

    class FakeDecisionLogStore:
        def __init__(self, conn): pass

        def prune(self, *, older_than_days, now):
            prune_calls.append({"older_than_days": older_than_days})
            return 7

    events = []

    class FakeAuditEventStore:
        def __init__(self, conn): pass

        def append(self, event, *, commit=True):
            events.append(event)

    monkeypatch.setattr(module, "DecisionLogStore", FakeDecisionLogStore, raising=False)
    monkeypatch.setattr(module, "AuditEventStore", FakeAuditEventStore)

    monkeypatch.setattr(sys, "argv", [
        "nightly", "--dry-run", "--output-dir", str(tmp_path),
    ])

    result = module.main()

    assert result == 0
    assert prune_calls == [{"older_than_days": 30}]
    pruned = [e for e in events if e.event_type == "decision_log_pruned"]
    assert len(pruned) == 1
    assert pruned[0].payload["deleted_count"] == 7
    assert pruned[0].payload["source"] == "nightly"


def test_nightly_cli_no_db_skips_prune(monkeypatch, tmp_path):
    """--no-db must skip the destructive decision_log prune entirely."""
    from alpaca_bot.nightly import cli as module

    _patch_env(monkeypatch)
    _patch_common_db(monkeypatch, module, symbols=[])

    prune_calls = []

    class FakeDecisionLogStore:
        def __init__(self, conn): pass

        def prune(self, *, older_than_days, now):
            prune_calls.append({"older_than_days": older_than_days})
            return 7

    monkeypatch.setattr(module, "DecisionLogStore", FakeDecisionLogStore, raising=False)

    monkeypatch.setattr(sys, "argv", [
        "nightly", "--dry-run", "--no-db", "--output-dir", str(tmp_path),
    ])

    result = module.main()

    assert result == 0
    assert prune_calls == []
