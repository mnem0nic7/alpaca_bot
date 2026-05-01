from __future__ import annotations

import math
from collections.abc import Sequence

from alpaca_bot.domain.models import Bar


def calculate_vwap(bars: Sequence[Bar]) -> float | None:
    total_vp = sum((b.high + b.low + b.close) / 3 * b.volume for b in bars)
    total_v = sum(b.volume for b in bars)
    return total_vp / total_v if total_v > 0 else None


def calculate_bollinger_bands(
    bars: Sequence[Bar], period: int, std_dev: float
) -> tuple[float, float, float] | None:
    """Return (lower, midline, upper) using population std dev. None if len(bars) < period."""
    if len(bars) < period:
        return None
    window = [b.close for b in bars[-period:]]
    midline = sum(window) / period
    variance = sum((c - midline) ** 2 for c in window) / period
    sigma = math.sqrt(variance)
    upper = midline + std_dev * sigma
    lower = midline - std_dev * sigma
    return lower, midline, upper
