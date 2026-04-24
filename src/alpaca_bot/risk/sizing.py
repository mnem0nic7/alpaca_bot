from __future__ import annotations

import math

from alpaca_bot.config import Settings


def calculate_position_size(
    *,
    equity: float,
    entry_price: float,
    stop_price: float,
    settings: Settings,
) -> int:
    if stop_price >= entry_price:
        raise ValueError("stop_price must be below entry_price for a long position")

    risk_per_share = entry_price - stop_price
    risk_budget = equity * settings.risk_per_trade_pct
    quantity = math.floor(risk_budget / risk_per_share)
    if quantity < 1:
        return 0

    max_notional = equity * settings.max_position_pct
    if quantity * entry_price > max_notional:
        quantity = math.floor(max_notional / entry_price)

    return max(quantity, 0)
