from __future__ import annotations

import itertools
import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING

from alpaca_bot.config import Settings
from alpaca_bot.domain.models import ReplayScenario
from alpaca_bot.replay.report import BacktestReport
from alpaca_bot.replay.runner import ReplayRunner

if TYPE_CHECKING:
    from alpaca_bot.strategy import StrategySignalEvaluator
    from alpaca_bot.tuning.surrogate import SurrogateModel


@dataclass(frozen=True)
class TuningCandidate:
    params: dict[str, str]
    report: BacktestReport | None
    score: float | None


ParameterGrid = dict[str, list[str]]

DEFAULT_GRID: ParameterGrid = {
    "BREAKOUT_LOOKBACK_BARS": ["15", "20", "25", "30"],
    "RELATIVE_VOLUME_THRESHOLD": ["1.3", "1.5", "1.8", "2.0"],
    "DAILY_SMA_PERIOD": ["10", "20", "30"],
}

STRATEGY_GRIDS: dict[str, ParameterGrid] = {
    "breakout": {
        "BREAKOUT_LOOKBACK_BARS": ["15", "20", "25", "30"],
        "RELATIVE_VOLUME_THRESHOLD": ["1.3", "1.5", "1.8", "2.0"],
        "DAILY_SMA_PERIOD": ["10", "20", "30"],
    },
    "momentum": {
        "PRIOR_DAY_HIGH_LOOKBACK_BARS": ["1", "2", "3"],
        "RELATIVE_VOLUME_THRESHOLD": ["1.3", "1.5", "1.8", "2.0"],
        "ATR_STOP_MULTIPLIER": ["1.0", "1.5", "2.0"],
    },
    "orb": {
        "ORB_OPENING_BARS": ["1", "2", "3", "4"],
        "RELATIVE_VOLUME_THRESHOLD": ["1.3", "1.5", "1.8"],
        "ATR_STOP_MULTIPLIER": ["1.0", "1.5", "2.0"],
    },
    "high_watermark": {
        "HIGH_WATERMARK_LOOKBACK_DAYS": ["63", "126", "252"],
        "RELATIVE_VOLUME_THRESHOLD": ["1.3", "1.5", "1.8", "2.0"],
        "ATR_STOP_MULTIPLIER": ["1.0", "1.5", "2.0"],
    },
    "ema_pullback": {
        "EMA_PERIOD": ["7", "9", "12", "20"],
        "RELATIVE_VOLUME_THRESHOLD": ["1.3", "1.5", "1.8"],
        "ATR_STOP_MULTIPLIER": ["1.0", "1.5", "2.0"],
    },
    "vwap_reversion": {
        "VWAP_DIP_THRESHOLD_PCT": ["0.01", "0.015", "0.02", "0.025"],
        "RELATIVE_VOLUME_THRESHOLD": ["1.3", "1.5", "1.8"],
        "ATR_STOP_MULTIPLIER": ["1.0", "1.5", "2.0"],
    },
    "gap_and_go": {
        "GAP_THRESHOLD_PCT": ["0.01", "0.015", "0.02", "0.025"],
        "GAP_VOLUME_THRESHOLD": ["1.5", "2.0", "2.5"],
        "ATR_STOP_MULTIPLIER": ["1.0", "1.5", "2.0"],
    },
    "bull_flag": {
        "BULL_FLAG_MIN_RUN_PCT": ["0.015", "0.02", "0.03"],
        "BULL_FLAG_CONSOLIDATION_RANGE_PCT": ["0.4", "0.5", "0.6"],
        "RELATIVE_VOLUME_THRESHOLD": ["1.3", "1.5", "2.0"],
        "ATR_STOP_MULTIPLIER": ["1.0", "1.5", "2.0"],
    },
    "vwap_cross": {
        "RELATIVE_VOLUME_THRESHOLD": ["1.3", "1.5", "1.8", "2.0"],
        "ATR_STOP_MULTIPLIER": ["1.0", "1.5", "2.0"],
    },
    "bb_squeeze": {
        "BB_PERIOD": ["15", "20", "25"],
        "BB_SQUEEZE_THRESHOLD_PCT": ["0.02", "0.03", "0.04"],
        "RELATIVE_VOLUME_THRESHOLD": ["1.3", "1.5", "2.0"],
    },
    "failed_breakdown": {
        "FAILED_BREAKDOWN_VOLUME_RATIO": ["1.5", "2.0", "2.5", "3.0"],
        "FAILED_BREAKDOWN_RECAPTURE_BUFFER_PCT": ["0.001", "0.002", "0.003"],
        "ATR_STOP_MULTIPLIER": ["1.0", "1.5", "2.0"],
    },
}


def _parse_grid(specs: list[str]) -> ParameterGrid:
    """Parse KEY=v1,v2,... strings into a ParameterGrid dict."""
    grid: ParameterGrid = {}
    for spec in specs:
        key, _, values = spec.partition("=")
        if not key or not values:
            sys.exit(f"Invalid --grid spec: {spec!r}. Expected KEY=v1,v2,...")
        grid[key.strip()] = [v.strip() for v in values.split(",")]
    return grid


def score_report(report: BacktestReport, *, min_trades: int = 3) -> float | None:
    """Sharpe-first composite score; None if disqualified.

    Disqualified when: fewer than min_trades, profit_factor < 1.0 (net-losing),
    or base score ≤ 0 (non-positive Sharpe/Calmar — no exploitable edge).
    profit_factor=None (no losses at all) is never penalised.
    """
    if report.total_trades < min_trades:
        return None
    if report.sharpe_ratio is not None:
        base = report.sharpe_ratio
    elif report.mean_return_pct is None:
        return None
    else:
        drawdown = report.max_drawdown_pct or 0.0
        base = report.mean_return_pct / (drawdown + 0.001)
    if report.profit_factor is not None and report.profit_factor < 1.0:
        return None  # net-losing strategy: hard disqualify
    if base <= 0.0:
        return None  # non-positive Sharpe/Calmar: no exploitable edge
    return base


def _aggregate_reports(reports: list[BacktestReport | None]) -> BacktestReport | None:
    """Combine per-scenario reports into one synthetic report for DB storage."""
    valid = [r for r in reports if r is not None]
    if not valid:
        return None
    total_trades = sum(r.total_trades for r in valid)
    winning_trades = sum(r.winning_trades for r in valid)
    losing_trades = sum(r.losing_trades for r in valid)
    win_rate: float | None = winning_trades / total_trades if total_trades > 0 else None
    mean_rets = [r.mean_return_pct for r in valid if r.mean_return_pct is not None]
    mean_return_pct: float | None = sum(mean_rets) / len(mean_rets) if mean_rets else None
    drawdowns = [r.max_drawdown_pct for r in valid if r.max_drawdown_pct is not None]
    max_drawdown_pct: float | None = max(drawdowns) if drawdowns else None
    sharpes = [r.sharpe_ratio for r in valid if r.sharpe_ratio is not None]
    sharpe_ratio: float | None = sum(sharpes) / len(sharpes) if sharpes else None
    profit_factors = [r.profit_factor for r in valid if r.profit_factor is not None]
    profit_factor: float | None = sum(profit_factors) / len(profit_factors) if profit_factors else None
    stop_wins = sum(r.stop_wins for r in valid)
    stop_losses = sum(r.stop_losses for r in valid)
    eod_wins = sum(r.eod_wins for r in valid)
    eod_losses = sum(r.eod_losses for r in valid)
    hold_mins = [r.avg_hold_minutes for r in valid if r.avg_hold_minutes is not None]
    avg_hold_minutes: float | None = sum(hold_mins) / len(hold_mins) if hold_mins else None
    max_consecutive_losses = max(r.max_consecutive_losses for r in valid)
    max_consecutive_wins = max(r.max_consecutive_wins for r in valid)
    win_avgs = [r.avg_win_return_pct for r in valid if r.avg_win_return_pct is not None]
    avg_win_return_pct: float | None = sum(win_avgs) / len(win_avgs) if win_avgs else None
    loss_avgs = [r.avg_loss_return_pct for r in valid if r.avg_loss_return_pct is not None]
    avg_loss_return_pct: float | None = sum(loss_avgs) / len(loss_avgs) if loss_avgs else None
    return BacktestReport(
        trades=(),
        total_trades=total_trades,
        winning_trades=winning_trades,
        losing_trades=losing_trades,
        win_rate=win_rate,
        mean_return_pct=mean_return_pct,
        max_drawdown_pct=max_drawdown_pct,
        sharpe_ratio=sharpe_ratio,
        profit_factor=profit_factor,
        stop_wins=stop_wins,
        stop_losses=stop_losses,
        eod_wins=eod_wins,
        eod_losses=eod_losses,
        avg_hold_minutes=avg_hold_minutes,
        avg_win_return_pct=avg_win_return_pct,
        avg_loss_return_pct=avg_loss_return_pct,
        max_consecutive_losses=max_consecutive_losses,
        max_consecutive_wins=max_consecutive_wins,
        strategy_name="aggregate",
    )


def run_sweep(
    *,
    scenario: ReplayScenario,
    base_env: dict[str, str],
    grid: ParameterGrid | None = None,
    min_trades: int = 3,
    signal_evaluator: "StrategySignalEvaluator | None" = None,
) -> list[TuningCandidate]:
    """Run a parameter grid sweep over `scenario`.

    Returns candidates sorted descending by score (scored first, then unscored).
    """
    effective_grid = grid if grid is not None else DEFAULT_GRID
    keys = list(effective_grid.keys())
    value_lists = [effective_grid[k] for k in keys]

    candidates: list[TuningCandidate] = []
    for combo in itertools.product(*value_lists):
        overrides = dict(zip(keys, combo))
        merged_env = {**base_env, **overrides}
        try:
            settings = Settings.from_env(merged_env)
        except ValueError:
            continue  # invalid combination — skip silently

        runner = ReplayRunner(settings, signal_evaluator=signal_evaluator)
        result = runner.run(scenario)
        report: BacktestReport | None = result.backtest_report  # type: ignore[assignment]
        s = score_report(report, min_trades=min_trades) if report is not None else None
        candidates.append(TuningCandidate(params=overrides, report=report, score=s))

    return sorted(
        candidates,
        key=lambda c: (c.score is not None, c.score or 0.0),
        reverse=True,
    )


def run_multi_scenario_sweep(
    *,
    scenarios: list[ReplayScenario],
    base_env: dict[str, str],
    grid: ParameterGrid | None = None,
    min_trades_per_scenario: int = 2,
    aggregate: str = "min",
    signal_evaluator: "StrategySignalEvaluator | None" = None,
    surrogate: "SurrogateModel | None" = None,
) -> list[TuningCandidate]:
    """Run a parameter grid sweep across multiple scenarios.

    Each combination is evaluated against every scenario. The final score is
    the aggregate (min or mean) of per-scenario scores. A combination is
    disqualified (score=None) if ANY scenario yields fewer than
    min_trades_per_scenario trades.
    """
    effective_grid = grid if grid is not None else DEFAULT_GRID
    keys = list(effective_grid.keys())
    value_lists = [effective_grid[k] for k in keys]

    all_combos = list(itertools.product(*value_lists))
    if surrogate is not None and surrogate.is_fitted:
        all_combos.sort(
            key=lambda combo: surrogate.predict(dict(zip(keys, combo))) or 0.0,
            reverse=True,
        )
    candidates: list[TuningCandidate] = []
    for combo in all_combos:
        overrides = dict(zip(keys, combo))
        merged_env = {**base_env, **overrides}
        try:
            settings = Settings.from_env(merged_env)
        except ValueError:
            continue

        per_scenario_reports: list[BacktestReport | None] = []
        per_scenario_scores: list[float | None] = []
        runner = ReplayRunner(settings, signal_evaluator=signal_evaluator)
        for scenario in scenarios:
            result = runner.run(scenario)
            report: BacktestReport | None = result.backtest_report  # type: ignore[assignment]
            s = score_report(report, min_trades=min_trades_per_scenario) if report is not None else None
            per_scenario_reports.append(report)
            per_scenario_scores.append(s)

        if any(s is None for s in per_scenario_scores):
            agg_score: float | None = None
        elif aggregate == "mean":
            scored = [s for s in per_scenario_scores if s is not None]
            agg_score = sum(scored) / len(scored)
        else:  # "min"
            scored = [s for s in per_scenario_scores if s is not None]
            agg_score = min(scored)

        agg_report = _aggregate_reports(per_scenario_reports)
        candidates.append(TuningCandidate(params=overrides, report=agg_report, score=agg_score))

    return sorted(
        candidates,
        key=lambda c: (c.score is not None, c.score or 0.0),
        reverse=True,
    )


def evaluate_candidates_oos(
    candidates: list[TuningCandidate],
    oos_scenarios: list[ReplayScenario],
    *,
    base_env: dict[str, str],
    min_trades: int,
    aggregate: str = "min",
    signal_evaluator: "StrategySignalEvaluator | None" = None,
) -> list[float | None]:
    """Score each candidate against OOS scenarios; returns a parallel list of scores.

    None means disqualified (< min_trades in at least one OOS scenario).
    Does not produce new TuningCandidate objects — read-only scoring pass.
    """
    scores: list[float | None] = []
    for candidate in candidates:
        merged_env = {**base_env, **candidate.params}
        try:
            settings = Settings.from_env(merged_env)
        except ValueError:
            scores.append(None)
            continue
        runner = ReplayRunner(settings, signal_evaluator=signal_evaluator)
        per_scenario_scores: list[float | None] = []
        for scenario in oos_scenarios:
            result = runner.run(scenario)
            report: BacktestReport | None = result.backtest_report  # type: ignore[assignment]
            s = score_report(report, min_trades=min_trades) if report is not None else None
            per_scenario_scores.append(s)
        if any(s is None for s in per_scenario_scores):
            scores.append(None)
        elif aggregate == "mean":
            valid = [s for s in per_scenario_scores if s is not None]
            scores.append(sum(valid) / len(valid))
        else:  # "min"
            valid = [s for s in per_scenario_scores if s is not None]
            scores.append(min(valid))
    return scores
