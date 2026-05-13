from __future__ import annotations

import logging
from collections.abc import Sequence

from alpaca_bot.domain.models import Bar

logger = logging.getLogger(__name__)

_MIN_BUFFER = 0.01


def calculate_atr(bars: Sequence[Bar], period: int) -> float | None:
    """Return Wilder's ATR for the last bar in `bars`, or None if insufficient data.

    Requires at least period + 1 bars (period bars to compute TRs, plus a
    prev_close for the first TR).
    """
    if len(bars) < period + 1:
        logger.debug(
            "ATR unavailable for %s: %d bars available, %d required",
            bars[0].symbol if bars else "unknown",
            len(bars),
            period + 1,
        )
        return None

    def _tr(i: int) -> float:
        bar, prev_close = bars[i], bars[i - 1].close
        return max(bar.high - bar.low, abs(bar.high - prev_close), abs(bar.low - prev_close))

    atr = sum(_tr(i) for i in range(1, period + 1)) / period
    for i in range(period + 1, len(bars)):
        atr = (atr * (period - 1) + _tr(i)) / period
    return atr


def atr_stop_buffer(
    daily_bars: Sequence[Bar],
    atr_period: int,
    atr_stop_multiplier: float,
    fallback_anchor: float,
    fallback_pct: float,
) -> float:
    """Return the stop buffer distance using ATR when data permits, else a pct fallback.

    Always returns at least _MIN_BUFFER (0.01) to prevent flush-against-anchor stops.
    """
    atr = calculate_atr(daily_bars, atr_period)
    if atr is not None:
        return max(_MIN_BUFFER, atr_stop_multiplier * atr)
    return max(_MIN_BUFFER, fallback_anchor * fallback_pct)
