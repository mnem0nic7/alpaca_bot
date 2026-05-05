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
    monkeypatch.setattr(sys, "argv", [
        "nightly", "--dry-run", "--no-db",
        "--output-dir", str(tmp_path),
        "--output-env", str(output_env),
        "--strategies", "breakout,momentum",
    ])

    result = module.main()

    assert result == 0
    assert not output_env.exists(), "No candidate.env must be written when all strategies fail OOS gate"
