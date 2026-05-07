from __future__ import annotations

import math
from typing import NamedTuple


class WeightResult(NamedTuple):
    weights: dict[str, float]
    sharpes: dict[str, float]


def compute_strategy_weights(
    trade_rows: list[dict],
    active_strategies: list[str],
    *,
    min_weight: float = 0.01,
    max_weight: float = 0.40,
    min_trades: int = 5,
) -> WeightResult:
    """Compute Sharpe-proportional capital weights for active strategies.

    Returns WeightResult with weights summing to 1.0 and per-strategy Sharpes.
    Each weight is clipped to [min_weight, max_weight] via iterative normalization.
    Falls back to equal weights when all Sharpes are 0 (no history or all losing).
    """
    n_active = len(active_strategies)
    if n_active == 0:
        return WeightResult({}, {})

    active_set = set(active_strategies)
    # Accumulate daily PnL and trade counts per strategy
    daily_pnl: dict[str, dict] = {name: {} for name in active_strategies}
    trade_count: dict[str, int] = {name: 0 for name in active_strategies}
    for row in trade_rows:
        name = row["strategy_name"]
        if name not in active_set:
            continue
        d = row["exit_date"]
        daily_pnl[name][d] = daily_pnl[name].get(d, 0.0) + row["pnl"]
        trade_count[name] += 1

    # Compute annualised Sharpe per strategy
    sharpes: dict[str, float] = {}
    for name in active_strategies:
        if trade_count[name] < min_trades:
            sharpes[name] = 0.0
            continue
        daily_values = list(daily_pnl[name].values())
        n = len(daily_values)
        mean = sum(daily_values) / n
        if n < 2:
            sharpes[name] = 1.0 if mean > 0 else 0.0
            continue
        variance = sum((v - mean) ** 2 for v in daily_values) / (n - 1)
        std = variance ** 0.5
        if std == 0.0:
            sharpes[name] = 1.0 if mean > 0 else 0.0
        else:
            sharpes[name] = max(0.0, mean / std * math.sqrt(252))

    total_sharpe = sum(sharpes.values())
    if total_sharpe == 0.0:
        equal = 1.0 / n_active
        return WeightResult({name: equal for name in active_strategies}, sharpes)

    weights: dict[str, float] = {
        name: sharpes[name] / total_sharpe for name in active_strategies
    }

    # With a single strategy there is nothing to cap or floor against — return directly.
    if n_active == 1:
        return WeightResult(weights, sharpes)

    # Phase 1: Cap overweighted strategies; redistribute excess to others by Sharpe.
    for _ in range(50):
        over_cap = [name for name in active_strategies if weights[name] > max_weight + 1e-12]
        if not over_cap:
            break
        excess = sum(weights[name] - max_weight for name in over_cap)
        new_weights: dict[str, float] = {}
        free_names: list[str] = []
        for name in active_strategies:
            if weights[name] > max_weight + 1e-12:
                new_weights[name] = max_weight
            else:
                new_weights[name] = weights[name]
                free_names.append(name)
        if not free_names:
            break
        free_sharpe = sum(sharpes[name] for name in free_names)
        for name in free_names:
            if free_sharpe > 0.0:
                new_weights[name] += excess * (sharpes[name] / free_sharpe)
            else:
                new_weights[name] += excess / len(free_names)
        weights = new_weights

    # Phase 2: Apply floor; take shortfall proportionally from above-floor strategies.
    for _ in range(50):
        under_floor = [name for name in active_strategies if weights[name] < min_weight - 1e-12]
        if not under_floor:
            break
        deficit = sum(min_weight - weights[name] for name in under_floor)
        above_floor = [name for name in active_strategies if weights[name] > min_weight + 1e-12]
        if not above_floor:
            break
        above_total = sum(weights[name] for name in above_floor)
        new_weights2: dict[str, float] = dict(weights)
        for name in under_floor:
            new_weights2[name] = min_weight
        for name in above_floor:
            new_weights2[name] = weights[name] - deficit * (weights[name] / above_total)
        weights = new_weights2

    # Normalise to guard against accumulated floating-point drift.
    total = sum(weights.values())
    return WeightResult({name: w / total for name, w in weights.items()}, sharpes)
