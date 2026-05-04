from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from alpaca_bot.config import Settings
from alpaca_bot.domain.models import Bar, ReplayScenario
from alpaca_bot.replay.report import BacktestReport
from alpaca_bot.tuning.sweep import (
    DEFAULT_GRID,
    ParameterGrid,
    TuningCandidate,
    run_multi_scenario_sweep,
    run_sweep,
    score_report,
)


def _base_env() -> dict[str, str]:
    return {
        "TRADING_MODE": "paper",
        "ENABLE_LIVE_TRADING": "false",
        "STRATEGY_VERSION": "v1-breakout",
        "DATABASE_URL": "postgresql://x:x@localhost/x",
        "MARKET_DATA_FEED": "sip",
        "SYMBOLS": "AAPL",
        "DAILY_SMA_PERIOD": "20",
        "BREAKOUT_LOOKBACK_BARS": "20",
        "RELATIVE_VOLUME_LOOKBACK_BARS": "20",
        "RELATIVE_VOLUME_THRESHOLD": "1.5",
        "ENTRY_TIMEFRAME_MINUTES": "15",
        "RISK_PER_TRADE_PCT": "0.0025",
        "MAX_POSITION_PCT": "0.05",
        "MAX_OPEN_POSITIONS": "3",
        "DAILY_LOSS_LIMIT_PCT": "0.01",
        "STOP_LIMIT_BUFFER_PCT": "0.001",
        "BREAKOUT_STOP_BUFFER_PCT": "0.001",
        "ENTRY_STOP_PRICE_BUFFER": "0.01",
        "ENTRY_WINDOW_START": "10:00",
        "ENTRY_WINDOW_END": "15:30",
        "FLATTEN_TIME": "15:45",
    }


def _make_quiet_scenario() -> ReplayScenario:
    """A scenario with no breakout signals — every combination produces 0 trades."""
    t0 = datetime(2026, 4, 24, 14, 0, tzinfo=timezone.utc)
    bars = [
        Bar(symbol="AAPL", timestamp=t0 + timedelta(minutes=15 * i),
            open=100.0, high=100.5, low=99.5, close=100.0, volume=500)
        for i in range(30)
    ]
    daily = [
        Bar(symbol="AAPL",
            timestamp=datetime(2026, 3, 26, 20, 0, tzinfo=timezone.utc) + timedelta(days=i),
            open=89.0 + i, high=90.0 + i, low=88.0 + i, close=90.0 + i, volume=1_000_000)
        for i in range(25)
    ]
    return ReplayScenario(name="quiet", symbol="AAPL", starting_equity=100_000.0,
                          daily_bars=daily, intraday_bars=bars)


def _make_golden_scenario() -> ReplayScenario:
    golden = Path(__file__).resolve().parent.parent / "golden" / "breakout_success.json"
    from alpaca_bot.replay.runner import ReplayRunner
    settings = Settings.from_env(_base_env())
    return ReplayRunner(settings).load_scenario(golden)


# ---------------------------------------------------------------------------
# score_report
# ---------------------------------------------------------------------------

def test_score_report_none_below_min_trades() -> None:
    report = BacktestReport(
        trades=(), total_trades=2, winning_trades=2, losing_trades=0,
        win_rate=1.0, mean_return_pct=0.05, max_drawdown_pct=None, sharpe_ratio=1.0,
    )
    assert score_report(report, min_trades=3) is None


def test_score_report_uses_sharpe_when_available() -> None:
    report = BacktestReport(
        trades=(), total_trades=5, winning_trades=4, losing_trades=1,
        win_rate=0.8, mean_return_pct=0.03, max_drawdown_pct=0.1, sharpe_ratio=2.5,
    )
    assert score_report(report, min_trades=3) == pytest.approx(2.5)


def test_score_report_calmar_fallback_when_no_sharpe() -> None:
    report = BacktestReport(
        trades=(), total_trades=5, winning_trades=4, losing_trades=1,
        win_rate=0.8, mean_return_pct=0.03, max_drawdown_pct=0.1, sharpe_ratio=None,
    )
    expected = 0.03 / (0.1 + 0.001)
    assert score_report(report, min_trades=3) == pytest.approx(expected)


def test_score_report_calmar_fallback_zero_drawdown() -> None:
    report = BacktestReport(
        trades=(), total_trades=5, winning_trades=5, losing_trades=0,
        win_rate=1.0, mean_return_pct=0.05, max_drawdown_pct=None, sharpe_ratio=None,
    )
    expected = 0.05 / (0.0 + 0.001)
    assert score_report(report, min_trades=3) == pytest.approx(expected)


# ---------------------------------------------------------------------------
# run_sweep
# ---------------------------------------------------------------------------

def test_run_sweep_quiet_scenario_all_unscored() -> None:
    scenario = _make_quiet_scenario()
    small_grid: ParameterGrid = {
        "BREAKOUT_LOOKBACK_BARS": ["15", "20"],
        "RELATIVE_VOLUME_THRESHOLD": ["1.5"],
        "DAILY_SMA_PERIOD": ["20"],
    }
    candidates = run_sweep(scenario=scenario, base_env=_base_env(), grid=small_grid, min_trades=1)
    assert len(candidates) == 2
    assert all(c.score is None for c in candidates)


def test_run_sweep_golden_scenario_produces_scored_candidates() -> None:
    scenario = _make_golden_scenario()
    small_grid: ParameterGrid = {
        "BREAKOUT_LOOKBACK_BARS": ["20"],
        "RELATIVE_VOLUME_THRESHOLD": ["1.5"],
        "DAILY_SMA_PERIOD": ["20"],
    }
    candidates = run_sweep(scenario=scenario, base_env=_base_env(), grid=small_grid, min_trades=1)
    assert len(candidates) == 1
    assert candidates[0].score is not None


def test_run_sweep_sorted_scored_before_unscored() -> None:
    scenario = _make_golden_scenario()
    small_grid: ParameterGrid = {
        "BREAKOUT_LOOKBACK_BARS": ["20", "25"],
        "RELATIVE_VOLUME_THRESHOLD": ["1.5"],
        "DAILY_SMA_PERIOD": ["20"],
    }
    candidates = run_sweep(scenario=scenario, base_env=_base_env(), grid=small_grid, min_trades=1)
    seen_unscored = False
    for c in candidates:
        if c.score is None:
            seen_unscored = True
        elif seen_unscored:
            pytest.fail("A scored candidate appeared after an unscored one")


def test_run_sweep_skips_invalid_param_combinations() -> None:
    scenario = _make_quiet_scenario()
    grid: ParameterGrid = {
        "BREAKOUT_LOOKBACK_BARS": ["20", "NOT_AN_INT"],
        "RELATIVE_VOLUME_THRESHOLD": ["1.5"],
        "DAILY_SMA_PERIOD": ["20"],
    }
    candidates = run_sweep(scenario=scenario, base_env=_base_env(), grid=grid)
    assert isinstance(candidates, list)
    assert len(candidates) == 1


def test_run_sweep_custom_grid_overrides_default() -> None:
    scenario = _make_quiet_scenario()
    custom_grid: ParameterGrid = {
        "BREAKOUT_LOOKBACK_BARS": ["22"],
        "RELATIVE_VOLUME_THRESHOLD": ["1.6"],
        "DAILY_SMA_PERIOD": ["18"],
    }
    candidates = run_sweep(scenario=scenario, base_env=_base_env(), grid=custom_grid)
    assert len(candidates) == 1
    assert candidates[0].params["BREAKOUT_LOOKBACK_BARS"] == "22"
    assert candidates[0].params["RELATIVE_VOLUME_THRESHOLD"] == "1.6"
    assert candidates[0].params["DAILY_SMA_PERIOD"] == "18"


# ---------------------------------------------------------------------------
# run_multi_scenario_sweep
# ---------------------------------------------------------------------------

def test_run_multi_scenario_sweep_disqualifies_when_any_scenario_fails() -> None:
    """When one scenario produces no trades, all combos are disqualified."""
    golden = _make_golden_scenario()
    quiet = _make_quiet_scenario()
    small_grid: ParameterGrid = {
        "BREAKOUT_LOOKBACK_BARS": ["20"],
        "RELATIVE_VOLUME_THRESHOLD": ["1.5"],
        "DAILY_SMA_PERIOD": ["20"],
    }
    candidates = run_multi_scenario_sweep(
        scenarios=[golden, quiet],
        base_env=_base_env(),
        grid=small_grid,
        min_trades_per_scenario=1,
    )
    assert len(candidates) == 1
    assert candidates[0].score is None


def test_run_multi_scenario_sweep_min_aggregate_uses_worst_case(monkeypatch) -> None:
    """aggregate='min' returns the lowest per-scenario score."""
    import alpaca_bot.tuning.sweep as sweep_module

    quiet = _make_quiet_scenario()
    call_results = [2.0, 0.5]
    call_idx = [0]

    def fake_score(report, *, min_trades):
        result = call_results[call_idx[0] % len(call_results)]
        call_idx[0] += 1
        return result

    monkeypatch.setattr(sweep_module, "score_report", fake_score)

    small_grid: ParameterGrid = {
        "BREAKOUT_LOOKBACK_BARS": ["20"],
        "RELATIVE_VOLUME_THRESHOLD": ["1.5"],
        "DAILY_SMA_PERIOD": ["20"],
    }
    candidates = run_multi_scenario_sweep(
        scenarios=[quiet, quiet],
        base_env=_base_env(),
        grid=small_grid,
        min_trades_per_scenario=1,
        aggregate="min",
    )
    assert len(candidates) == 1
    assert candidates[0].score == pytest.approx(0.5)


def test_run_multi_scenario_sweep_mean_aggregate_averages_scores(monkeypatch) -> None:
    """aggregate='mean' returns the average of per-scenario scores."""
    import alpaca_bot.tuning.sweep as sweep_module

    quiet = _make_quiet_scenario()
    call_results = [2.0, 0.5]
    call_idx = [0]

    def fake_score(report, *, min_trades):
        result = call_results[call_idx[0] % len(call_results)]
        call_idx[0] += 1
        return result

    monkeypatch.setattr(sweep_module, "score_report", fake_score)

    small_grid: ParameterGrid = {
        "BREAKOUT_LOOKBACK_BARS": ["20"],
        "RELATIVE_VOLUME_THRESHOLD": ["1.5"],
        "DAILY_SMA_PERIOD": ["20"],
    }
    candidates = run_multi_scenario_sweep(
        scenarios=[quiet, quiet],
        base_env=_base_env(),
        grid=small_grid,
        min_trades_per_scenario=1,
        aggregate="mean",
    )
    assert len(candidates) == 1
    assert candidates[0].score == pytest.approx(1.25)  # (2.0 + 0.5) / 2


def test_run_multi_scenario_sweep_aggregated_report_sums_trades() -> None:
    """Aggregated report total_trades equals the sum of all per-scenario trades."""
    from alpaca_bot.domain.models import ReplayScenario

    golden = _make_golden_scenario()
    golden2 = ReplayScenario(
        name="golden2",
        symbol=golden.symbol,
        starting_equity=golden.starting_equity,
        daily_bars=golden.daily_bars,
        intraday_bars=golden.intraday_bars,
    )
    small_grid: ParameterGrid = {
        "BREAKOUT_LOOKBACK_BARS": ["20"],
        "RELATIVE_VOLUME_THRESHOLD": ["1.5"],
        "DAILY_SMA_PERIOD": ["20"],
    }
    candidates = run_multi_scenario_sweep(
        scenarios=[golden, golden2],
        base_env=_base_env(),
        grid=small_grid,
        min_trades_per_scenario=1,
    )
    assert len(candidates) == 1
    assert candidates[0].report is not None
    assert candidates[0].report.total_trades == 2


# ---------------------------------------------------------------------------
# STRATEGY_GRIDS
# ---------------------------------------------------------------------------

def test_strategy_grids_covers_all_registry_entries() -> None:
    """Every strategy in STRATEGY_REGISTRY must have an entry in STRATEGY_GRIDS."""
    from alpaca_bot.strategy import STRATEGY_REGISTRY
    from alpaca_bot.tuning.sweep import STRATEGY_GRIDS

    missing = [name for name in STRATEGY_REGISTRY if name not in STRATEGY_GRIDS]
    assert not missing, f"Strategies missing from STRATEGY_GRIDS: {missing}"


def test_strategy_grids_keys_match_strategy_params() -> None:
    """Spot-check: each strategy grid contains its unique params, not breakout params."""
    from alpaca_bot.tuning.sweep import STRATEGY_GRIDS

    assert "BREAKOUT_LOOKBACK_BARS" in STRATEGY_GRIDS["breakout"]
    assert "EMA_PERIOD" in STRATEGY_GRIDS["ema_pullback"]
    assert "BREAKOUT_LOOKBACK_BARS" not in STRATEGY_GRIDS["ema_pullback"]
    assert "BB_PERIOD" in STRATEGY_GRIDS["bb_squeeze"]
    assert "BREAKOUT_LOOKBACK_BARS" not in STRATEGY_GRIDS["bb_squeeze"]


# ---------------------------------------------------------------------------
# score_report: profit_factor penalty
# ---------------------------------------------------------------------------

def test_score_report_penalizes_subunit_profit_factor() -> None:
    """profit_factor < 1.0 is now a hard disqualifier, not a score penalty."""
    report = BacktestReport(
        trades=(), total_trades=5, winning_trades=3, losing_trades=2,
        win_rate=0.6, mean_return_pct=0.02, max_drawdown_pct=0.05,
        sharpe_ratio=2.0, profit_factor=0.7,
    )
    assert score_report(report, min_trades=3) is None


def test_score_report_no_penalty_when_profit_factor_at_or_above_one() -> None:
    """profit_factor=1.5 with sharpe=2.0 → score = 2.0 (no upward scaling)."""
    report = BacktestReport(
        trades=(), total_trades=5, winning_trades=3, losing_trades=2,
        win_rate=0.6, mean_return_pct=0.02, max_drawdown_pct=0.05,
        sharpe_ratio=2.0, profit_factor=1.5,
    )
    assert score_report(report, min_trades=3) == pytest.approx(2.0)


def test_score_report_no_penalty_when_profit_factor_none() -> None:
    """profit_factor=None (no losses) → score is unchanged from Sharpe."""
    report = BacktestReport(
        trades=(), total_trades=5, winning_trades=5, losing_trades=0,
        win_rate=1.0, mean_return_pct=0.05, max_drawdown_pct=None,
        sharpe_ratio=3.0, profit_factor=None,
    )
    assert score_report(report, min_trades=3) == pytest.approx(3.0)


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


# ---------------------------------------------------------------------------
# _aggregate_reports: exit type fields
# ---------------------------------------------------------------------------

def test_aggregate_reports_sums_exit_type_fields() -> None:
    """Aggregated report sums stop_wins/losses and eod_wins/losses across scenarios."""
    from alpaca_bot.tuning.sweep import _aggregate_reports

    r1 = BacktestReport(
        trades=(), total_trades=3, winning_trades=2, losing_trades=1,
        win_rate=0.67, mean_return_pct=0.02, max_drawdown_pct=None,
        stop_wins=1, stop_losses=1, eod_wins=1, eod_losses=0, avg_hold_minutes=20.0,
    )
    r2 = BacktestReport(
        trades=(), total_trades=2, winning_trades=1, losing_trades=1,
        win_rate=0.5, mean_return_pct=0.01, max_drawdown_pct=None,
        stop_wins=0, stop_losses=1, eod_wins=1, eod_losses=0, avg_hold_minutes=30.0,
    )
    agg = _aggregate_reports([r1, r2])
    assert agg is not None
    assert agg.stop_wins == 1
    assert agg.stop_losses == 2
    assert agg.eod_wins == 2
    assert agg.eod_losses == 0
    assert agg.avg_hold_minutes == pytest.approx(25.0)


def test_aggregate_reports_max_consecutive_losses_uses_worst_case() -> None:
    """Aggregated max_consecutive_losses is max across scenarios (worst case)."""
    from alpaca_bot.tuning.sweep import _aggregate_reports

    r1 = BacktestReport(
        trades=(), total_trades=3, winning_trades=2, losing_trades=1,
        win_rate=0.67, mean_return_pct=0.02, max_drawdown_pct=None,
        max_consecutive_losses=2, max_consecutive_wins=3,
    )
    r2 = BacktestReport(
        trades=(), total_trades=4, winning_trades=2, losing_trades=2,
        win_rate=0.5, mean_return_pct=0.01, max_drawdown_pct=None,
        max_consecutive_losses=4, max_consecutive_wins=1,
    )
    agg = _aggregate_reports([r1, r2])
    assert agg is not None
    assert agg.max_consecutive_losses == 4
    assert agg.max_consecutive_wins == 3


def test_aggregate_reports_averages_win_loss_return_pct() -> None:
    """Aggregated avg_win/avg_loss are means of non-None per-scenario values."""
    from alpaca_bot.tuning.sweep import _aggregate_reports

    r1 = BacktestReport(
        trades=(), total_trades=3, winning_trades=2, losing_trades=1,
        win_rate=0.67, mean_return_pct=0.02, max_drawdown_pct=None,
        avg_win_return_pct=0.04, avg_loss_return_pct=-0.02,
    )
    r2 = BacktestReport(
        trades=(), total_trades=2, winning_trades=1, losing_trades=1,
        win_rate=0.5, mean_return_pct=0.01, max_drawdown_pct=None,
        avg_win_return_pct=0.02, avg_loss_return_pct=-0.01,
    )
    agg = _aggregate_reports([r1, r2])
    assert agg is not None
    assert agg.avg_win_return_pct == pytest.approx((0.04 + 0.02) / 2)
    assert agg.avg_loss_return_pct == pytest.approx((-0.02 + -0.01) / 2)


# ---------------------------------------------------------------------------
# evaluate_candidates_oos
# ---------------------------------------------------------------------------

def test_run_multi_scenario_sweep_respects_surrogate_ordering() -> None:
    """Surrogate pre-sorts grid: high-predicted-score combo runs first → appears first in results."""
    from alpaca_bot.domain.models import ReplayScenario
    from alpaca_bot.tuning.surrogate import SurrogateModel

    class _FixedSurrogate(SurrogateModel):
        """Predicts 1.0 for BREAKOUT_LOOKBACK_BARS=15 and 0.0 for everything else."""
        @property
        def is_fitted(self) -> bool:
            return True
        def predict(self, params: dict) -> float | None:
            return 1.0 if params.get("BREAKOUT_LOOKBACK_BARS") == "15" else 0.0

    quiet_1 = _make_quiet_scenario()
    quiet_2 = ReplayScenario(
        name="quiet2", symbol="AAPL", starting_equity=100_000.0,
        daily_bars=quiet_1.daily_bars, intraday_bars=quiet_1.intraday_bars,
    )
    small_grid: ParameterGrid = {
        "BREAKOUT_LOOKBACK_BARS": ["15", "30"],
        "RELATIVE_VOLUME_THRESHOLD": ["1.5"],
        "DAILY_SMA_PERIOD": ["20"],
    }

    results = run_multi_scenario_sweep(
        scenarios=[quiet_1, quiet_2],
        base_env=_base_env(),
        grid=small_grid,
        surrogate=_FixedSurrogate(),
    )

    # Both combos produce score=None (quiet scenario). Python's sort is stable,
    # so insertion order is preserved for equal keys. The surrogate pre-sort
    # determines which combo runs first → gets appended first → stays first after
    # the stable final sort. Assert that the surrogate-preferred combo (LOOKBACK=15)
    # is first in results.
    lookbacks = [c.params["BREAKOUT_LOOKBACK_BARS"] for c in results]
    assert set(lookbacks) == {"15", "30"}, "both combos must run (no pruning)"
    assert results[0].params["BREAKOUT_LOOKBACK_BARS"] == "15", \
        "surrogate-preferred combo (predicted 1.0) must appear first"


def test_evaluate_candidates_oos_returns_parallel_scores() -> None:
    """OOS evaluation produces a score list parallel to the input candidates list."""
    from alpaca_bot.tuning.sweep import evaluate_candidates_oos

    golden = _make_golden_scenario()

    params = {
        "BREAKOUT_LOOKBACK_BARS": "20",
        "RELATIVE_VOLUME_THRESHOLD": "1.5",
        "DAILY_SMA_PERIOD": "20",
    }
    c1 = TuningCandidate(params=params, report=None, score=0.5)
    c2 = TuningCandidate(params=params, report=None, score=0.3)

    scores = evaluate_candidates_oos(
        candidates=[c1, c2],
        oos_scenarios=[golden],
        base_env=_base_env(),
        min_trades=1,
        aggregate="min",
    )
    assert len(scores) == 2
    for s in scores:
        assert s is None or isinstance(s, float)
