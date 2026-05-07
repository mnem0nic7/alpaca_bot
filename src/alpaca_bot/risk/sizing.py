from __future__ import annotations

import math

from alpaca_bot.config import Settings


def calculate_position_size(
    *,
    equity: float,
    entry_price: float,
    stop_price: float,
    settings,
    fractionable: bool = False,
) -> float:
    if stop_price >= entry_price:
        raise ValueError("stop_price must be below entry_price for a long position")

    risk_per_share = entry_price - stop_price
    if risk_per_share <= 0:
        return 0.0
    risk_budget = equity * settings.risk_per_trade_pct
    quantity = risk_budget / risk_per_share
    if not fractionable:
        quantity = math.floor(quantity)
    if not fractionable and quantity < 1:
        return 0.0
    if quantity <= 0.0:
        return 0.0
    if settings.max_loss_per_trade_dollars is not None:
        dollar_cap_qty = settings.max_loss_per_trade_dollars / risk_per_share
        quantity = min(quantity, dollar_cap_qty)
        if not fractionable:
            quantity = math.floor(quantity)
        if not fractionable and quantity < 1:
            return 0.0
        if quantity <= 0.0:
            return 0.0
    max_notional = equity * settings.max_position_pct
    if quantity * entry_price > max_notional:
        quantity = max_notional / entry_price
        if not fractionable:
            quantity = math.floor(quantity)
    return max(float(quantity), 0.0)
