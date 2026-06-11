"""Bootstrap statistics for per-trade P&L samples.

Pure functions, deterministic via seeded RNG. Used by the strategy audit
to put confidence intervals on backtest edges instead of point estimates.
"""

from __future__ import annotations

import random
from collections.abc import Sequence

MIN_SAMPLES = 5


def _bootstrap_means(
    values: Sequence[float], n_resamples: int, seed: int
) -> list[float]:
    rng = random.Random(seed)
    n = len(values)
    return sorted(
        sum(rng.choices(values, k=n)) / n for _ in range(n_resamples)
    )


def bootstrap_mean_ci(
    values: Sequence[float],
    *,
    n_resamples: int = 2000,
    confidence: float = 0.95,
    seed: int = 42,
) -> tuple[float, float] | None:
    """Percentile-bootstrap confidence interval for the mean.

    Returns None when fewer than MIN_SAMPLES values — an interval from a
    handful of trades is more misleading than no interval.
    """
    if len(values) < MIN_SAMPLES:
        return None
    means = _bootstrap_means(values, n_resamples, seed)
    alpha = (1.0 - confidence) / 2.0
    lower_idx = int(alpha * n_resamples)
    upper_idx = min(int((1.0 - alpha) * n_resamples), n_resamples - 1)
    return means[lower_idx], means[upper_idx]


def bootstrap_p_positive(
    values: Sequence[float],
    *,
    n_resamples: int = 2000,
    seed: int = 42,
) -> float | None:
    """One-sided bootstrap p-value for 'mean > 0': fraction of resample
    means that are <= 0. Returns None below MIN_SAMPLES."""
    if len(values) < MIN_SAMPLES:
        return None
    means = _bootstrap_means(values, n_resamples, seed)
    return sum(1 for m in means if m <= 0.0) / n_resamples
