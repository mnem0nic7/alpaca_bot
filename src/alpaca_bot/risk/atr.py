from __future__ import annotations

from collections.abc import Sequence

from alpaca_bot.domain.models import Bar


def calculate_atr(bars: Sequence[Bar], period: int) -> float | None:
    """Return Wilder's ATR for the last bar in `bars`, or None if insufficient data.

    Requires at least period + 1 bars (period bars to compute TRs, plus a
    prev_close for the first TR).
    """
    if len(bars) < period + 1:
        return None

    true_ranges: list[float] = []
    for i in range(1, len(bars)):
        bar = bars[i]
        prev_close = bars[i - 1].close
        tr = max(
            bar.high - bar.low,
            abs(bar.high - prev_close),
            abs(bar.low - prev_close),
        )
        true_ranges.append(tr)

    # Seed with simple mean of first `period` TRs, then apply Wilder's smoothing.
    atr = sum(true_ranges[:period]) / period
    for tr in true_ranges[period:]:
        atr = (atr * (period - 1) + tr) / period

    return atr
