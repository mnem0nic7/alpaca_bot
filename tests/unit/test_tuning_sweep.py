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
    """profit_factor=0.7 with sharpe=2.0 → score = 2.0 * 0.7 = 1.4."""
    report = BacktestReport(
        trades=(), total_trades=5, winning_trades=3, losing_trades=2,
        win_rate=0.6, mean_return_pct=0.02, max_drawdown_pct=0.05,
        sharpe_ratio=2.0, profit_factor=0.7,
    )
    assert score_report(report, min_trades=3) == pytest.approx(1.4)


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
