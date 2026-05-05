from __future__ import annotations

import math

from alpaca_bot.config import Settings


def calculate_option_position_size(
    *,
    equity: float,
    ask: float,
    settings: Settings,
) -> int:
    if ask <= 0:
        return 0
    contract_cost = ask * 100
    risk_budget = equity * settings.risk_per_trade_pct
    contracts = math.floor(risk_budget / contract_cost)
    max_notional = equity * settings.max_position_pct
    max_contracts = math.floor(max_notional / contract_cost)
    return max(0, min(contracts, max_contracts))
