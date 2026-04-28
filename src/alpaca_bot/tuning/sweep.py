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
    """Sharpe-first composite score; None if disqualified (< min_trades)."""
    if report.total_trades < min_trades:
        return None
    if report.sharpe_ratio is not None:
        return report.sharpe_ratio
    if report.mean_return_pct is None:
        return None
    drawdown = report.max_drawdown_pct or 0.0
    return report.mean_return_pct / (drawdown + 0.001)


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
