# src/alpaca_bot/replay/mechanics.py
"""Stateless fill / slippage / exit-price primitives.

Shared by ReplayRunner (single-symbol) and PortfolioReplayRunner so that the
numerically-critical fill and slippage math is provably identical across both.
Position and equity bookkeeping is intentionally NOT here — it differs between
the per-symbol runner and the shared-equity portfolio runner.
"""

from __future__ import annotations

from alpaca_bot.config import Settings
from alpaca_bot.domain.models import Bar, OpenPosition


def apply_slippage(price: float, *, side: str, bps: float) -> float:
    """Adverse slippage: buys fill higher, sells fill lower, by bps per side."""
    if bps <= 0.0:
        return price
    factor = 1.0 + bps / 10_000.0 if side == "buy" else 1.0 - bps / 10_000.0
    return round(price * factor, 4)


def simulate_buy_stop_limit_fill(
    *, bar: Bar, stop_price: float, limit_price: float
) -> float | None:
    """Raw (pre-slippage) stop-limit buy fill price, or None if no fill."""
    if bar.open > limit_price:
        return None
    if bar.high < stop_price:
        return None
    fill_price = max(bar.open, stop_price)
    if fill_price > limit_price:
        return None
    return round(fill_price, 2)


def entry_fill_price(*, raw_fill: float, limit_price: float, bps: float) -> float:
    """Slipped entry fill, capped at the limit (a stop-limit cannot fill above limit)."""
    return min(apply_slippage(raw_fill, side="buy", bps=bps), limit_price)


def stop_exit_price(*, bar: Bar, position: OpenPosition, bps: float) -> float:
    """Slipped stop-hit exit: fills at the worse of stop and the bar open."""
    return apply_slippage(min(position.stop_price, bar.open), side="sell", bps=bps)


def should_update_stop(*, position: OpenPosition, candidate_stop: float) -> bool:
    """Return whether candidate_stop tightens risk for the position direction."""
    if position.quantity < 0:
        return candidate_stop < position.stop_price
    return candidate_stop > position.stop_price


def profit_target_price(*, position: OpenPosition, settings: Settings) -> float:
    return round(
        position.entry_price + settings.profit_target_r * position.risk_per_share, 2
    )


def eod_exit_price(*, bar: Bar, bps: float) -> float:
    return apply_slippage(bar.close, side="sell", bps=bps)
