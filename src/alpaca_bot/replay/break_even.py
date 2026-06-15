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
