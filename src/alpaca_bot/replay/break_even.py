"""Break-even slippage diagnostic.

Scores a strategy across a slippage ladder and finds the bps/side at which the
after-cost bootstrap ci_low crosses zero. Read-only: re-runs the replay at each
rung (slippage is not a linear per-trade deduction — entry is capped at the
limit price and quantity/target levels derive from the slipped fill, so the
trade set is not slippage-invariant). Reuses the audit scoring primitives.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from typing import Callable, Sequence

from alpaca_bot.config import Settings
from alpaca_bot.domain.models import ReplayScenario
from alpaca_bot.replay.audit import (
    PooledTradesFn,
    _replay_pooled_trades,
    classify_verdict,
)
from alpaca_bot.replay.stats import bootstrap_mean_ci, bootstrap_p_positive

DEFAULT_SLIPPAGE_LADDER: tuple[float, ...] = (0.0, 1.0, 2.0, 3.0, 4.0, 5.0)


@dataclass(frozen=True)
class BreakEvenPoint:
    slippage_bps: float
    trades: int
    mean_trade_pnl: float | None
    total_pnl: float
    ci_low: float | None
    ci_high: float | None
    p_positive: float | None
    verdict: str


@dataclass(frozen=True)
class BreakEvenResult:
    strategy: str
    scenarios: int
    points: tuple[BreakEvenPoint, ...]
    break_even_bps: float | None


def _interpolate_break_even(points: Sequence[BreakEvenPoint]) -> float | None:
    """First (lowest-bps) zero-crossing of ci_low, linearly interpolated.

    Points are ascending in slippage_bps. ci_low is approximately (not strictly)
    monotone-decreasing in cost, so the first crossing from >0 to <=0 is the
    conservative break-even. Returns 0.0 if the lowest rung is already <=0 (no
    edge even frictionless), None if no valid positive->non-positive bracket
    exists (all positive, leading None, or no valid pair).
    """
    if not points:
        return None
    # The lowest-bps rung is the frictionless reference. If it is itself
    # unmeasurable (insufficient trades even at 0 bps), there is no positive
    # anchor to cross from — the whole ladder is uninterpretable -> None. This
    # is distinct from a *measured* non-positive frictionless rung below.
    if points[0].ci_low is None:
        return None
    valid = [p for p in points if p.ci_low is not None]
    # Lowest-bps valid rung already non-positive: no edge even frictionless.
    if valid[0].ci_low <= 0.0:
        return 0.0
    prev = valid[0]
    for cur in valid[1:]:
        if cur.ci_low <= 0.0 < prev.ci_low:
            span = prev.ci_low - cur.ci_low
            return prev.slippage_bps + (cur.slippage_bps - prev.slippage_bps) * (
                prev.ci_low / span
            )
        prev = cur
    return None


def run_break_even_sweep(
    *,
    scenarios: Sequence[ReplayScenario],
    settings: Settings,
    strategy: str,
    slippage_ladder: Sequence[float] = DEFAULT_SLIPPAGE_LADDER,
    pooled_trades_fn: PooledTradesFn = _replay_pooled_trades,
    on_progress: Callable[[str], None] | None = None,
) -> BreakEvenResult:
    points: list[BreakEvenPoint] = []
    for bps in sorted(slippage_ladder):
        costed = dataclasses.replace(settings, replay_slippage_bps=bps)
        trades = pooled_trades_fn(scenarios, costed, strategy)
        pnls = [t.pnl for t in trades]
        ci = bootstrap_mean_ci(pnls)
        p = bootstrap_p_positive(pnls)  # already None below MIN_SAMPLES
        verdict = classify_verdict(trades=len(pnls), ci=ci, p_positive=p)
        points.append(
            BreakEvenPoint(
                slippage_bps=bps,
                trades=len(pnls),
                mean_trade_pnl=(
                    round(sum(pnls) / len(pnls), 4) if pnls else None
                ),
                total_pnl=round(sum(pnls), 2),
                ci_low=round(ci[0], 4) if ci is not None else None,
                ci_high=round(ci[1], 4) if ci is not None else None,
                p_positive=p,
                verdict=verdict,
            )
        )
        if on_progress is not None:
            be = points[-1]
            on_progress(
                f"{strategy} @ {bps:g}bps: ci_low="
                f"{'n/a' if be.ci_low is None else be.ci_low} "
                f"trades={be.trades} verdict={be.verdict}"
            )
    return BreakEvenResult(
        strategy=strategy,
        scenarios=len(scenarios),
        points=tuple(points),
        break_even_bps=_interpolate_break_even(points),
    )


def _fmt(v: float | None, spec: str = ".4f") -> str:
    return "n/a" if v is None else format(v, spec)


def format_break_even_markdown(results: Sequence[BreakEvenResult]) -> str:
    lines: list[str] = ["# Break-even slippage — after-cost ci_low zero-crossing", ""]
    for res in results:
        lines.append(f"## {res.strategy} ({res.scenarios} scenarios)")
        lines.append("")
        if res.break_even_bps is None:
            all_pos = all(
                p.ci_low is not None and p.ci_low > 0.0 for p in res.points
            )
            note = (
                "break-even > max rung (extend ladder)"
                if all_pos
                else "break-even: none (insufficient data)"
            )
        else:
            note = f"break-even ≈ {res.break_even_bps:.2f} bps/side"
        lines.append(f"**{note}**")
        lines.append("")
        lines.append(
            "| bps/side | trades | mean | ci_low | ci_high | p_positive | verdict |"
        )
        lines.append("|---|---|---|---|---|---|---|")
        for p in res.points:
            lines.append(
                f"| {p.slippage_bps:g} | {p.trades} | {_fmt(p.mean_trade_pnl)} | "
                f"{_fmt(p.ci_low)} | {_fmt(p.ci_high)} | "
                f"{_fmt(p.p_positive, '.4f')} | {p.verdict} |"
            )
        lines.append("")
    return "\n".join(lines)
