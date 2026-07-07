"""Cost-aware, significance-aware audit of every strategy across scenarios.

Runs each strategy twice (frictionless and with slippage), pools per-trade
P&L across all scenarios, and classifies the edge with bootstrap statistics.
"""

from __future__ import annotations

import dataclasses
import gc
from dataclasses import dataclass
from typing import Callable, Sequence

from alpaca_bot.config import Settings
from alpaca_bot.domain.models import ReplayScenario
from alpaca_bot.replay.report import ReplayTradeRecord, report_from_records
from alpaca_bot.replay.runner import ReplayRunner
from alpaca_bot.replay.stats import MIN_SAMPLES, bootstrap_mean_ci, bootstrap_p_positive
from alpaca_bot.strategy import STRATEGY_REGISTRY

AUDIT_STARTING_EQUITY = 100_000.0

PooledTradesFn = Callable[
    [Sequence["ReplayScenario"], Settings, str], list[ReplayTradeRecord]
]


@dataclass(frozen=True)
class StrategyAuditRow:
    strategy: str
    scenarios: int
    trades: int
    win_rate: float | None
    profit_factor: float | None
    total_pnl: float
    mean_trade_pnl: float | None
    annualized_sharpe: float | None
    ci_low: float | None
    ci_high: float | None
    p_positive: float | None
    zero_cost_total_pnl: float
    cost_drag: float  # zero_cost_total_pnl - total_pnl (always >= 0)
    verdict: str  # negative-edge | no-evidence | positive-edge | insufficient-data


def classify_verdict(
    *, trades: int, ci: tuple[float, float] | None, p_positive: float | None
) -> str:
    if trades < MIN_SAMPLES or ci is None or p_positive is None:
        return "insufficient-data"
    lo, hi = ci
    if hi < 0.0:
        return "negative-edge"
    if lo > 0.0 and p_positive < 0.05:
        return "positive-edge"
    return "no-evidence"


def _replay_pooled_trades(
    scenarios: Sequence[ReplayScenario], settings: Settings, strategy_name: str
) -> list[ReplayTradeRecord]:
    evaluator = STRATEGY_REGISTRY[strategy_name]
    regime_daily_bars = _resolve_regime_daily_bars(scenarios, settings)
    runner = ReplayRunner(
        settings,
        signal_evaluator=evaluator,
        strategy_name=strategy_name,
        regime_daily_bars=regime_daily_bars,
    )
    trades: list[ReplayTradeRecord] = []
    for scenario in scenarios:
        result = runner.run(scenario)
        trades.extend(result.backtest_report.trades)
    return trades


def _resolve_regime_daily_bars(
    scenarios: Sequence[ReplayScenario],
    settings: Settings,
) -> Sequence | None:
    if not settings.enable_regime_filter:
        return None
    regime_symbol = settings.regime_symbol.upper()
    for scenario in scenarios:
        if scenario.symbol.upper() == regime_symbol and scenario.daily_bars:
            return scenario.daily_bars
    for scenario in scenarios:
        if scenario.regime_daily_bars:
            return scenario.regime_daily_bars
    return None


def run_audit(
    *,
    scenarios: Sequence[ReplayScenario],
    settings: Settings,
    strategies: Sequence[str],
    slippage_bps: float,
    pooled_trades_fn: PooledTradesFn = _replay_pooled_trades,
    on_progress: Callable[[str], None] | None = None,
    on_row: Callable[[StrategyAuditRow], None] | None = None,
) -> list[StrategyAuditRow]:
    costed = dataclasses.replace(settings, replay_slippage_bps=slippage_bps)
    frictionless = dataclasses.replace(settings, replay_slippage_bps=0.0)

    rows: list[StrategyAuditRow] = []
    for name in strategies:
        cost_trades = pooled_trades_fn(scenarios, costed, name)
        if on_progress is not None:
            on_progress(f"{name}: costed replay complete ({len(cost_trades)} trades)")
        gc.collect()
        if cost_trades:
            free_trades = pooled_trades_fn(scenarios, frictionless, name)
            if on_progress is not None:
                on_progress(
                    f"{name}: frictionless replay complete ({len(free_trades)} trades)"
                )
        else:
            free_trades = []
            if on_progress is not None:
                on_progress(
                    f"{name}: frictionless replay skipped (0 costed trades)"
                )

        report = report_from_records(
            list(cost_trades), AUDIT_STARTING_EQUITY, name
        )
        pnls = [t.pnl for t in cost_trades]
        ci = bootstrap_mean_ci(pnls)
        p = bootstrap_p_positive(pnls)
        total = sum(pnls)
        zero_total = sum(t.pnl for t in free_trades)

        row = StrategyAuditRow(
            strategy=name,
            scenarios=len(scenarios),
            trades=len(cost_trades),
            win_rate=report.win_rate,
            profit_factor=report.profit_factor,
            total_pnl=round(total, 2),
            mean_trade_pnl=(
                round(total / len(cost_trades), 4) if cost_trades else None
            ),
            annualized_sharpe=report.annualized_sharpe,
            ci_low=round(ci[0], 4) if ci is not None else None,
            ci_high=round(ci[1], 4) if ci is not None else None,
            p_positive=p,
            zero_cost_total_pnl=round(zero_total, 2),
            cost_drag=round(zero_total - total, 2),
            verdict=classify_verdict(
                trades=len(cost_trades), ci=ci, p_positive=p
            ),
        )
        rows.append(row)
        if on_row is not None:
            on_row(row)
        if on_progress is not None:
            on_progress(
                f"{name}: {len(cost_trades)} trades, verdict={rows[-1].verdict}"
            )
    return rows
