from __future__ import annotations

from collections.abc import Sequence
from datetime import date

from alpaca_bot.config import Settings
from alpaca_bot.domain.models import OptionContract


def select_call_contract(
    contracts: Sequence[OptionContract],
    *,
    current_price: float,
    today: date,
    settings: Settings,
) -> OptionContract | None:
    eligible = [
        c for c in contracts
        if c.option_type == "call"
        and c.ask > 0
        and settings.option_dte_min <= (c.expiry - today).days <= settings.option_dte_max
    ]
    if not eligible:
        return None
    with_delta = [c for c in eligible if c.delta is not None]
    if with_delta:
        return min(with_delta, key=lambda c: abs(c.delta - settings.option_delta_target))  # type: ignore[operator]
    return min(eligible, key=lambda c: abs(c.strike - current_price))


def select_put_contract(
    contracts: Sequence[OptionContract],
    *,
    current_price: float,
    today: date,
    settings: Settings,
) -> OptionContract | None:
    eligible = [
        c for c in contracts
        if c.option_type == "put"
        and c.ask > 0
        and settings.option_dte_min <= (c.expiry - today).days <= settings.option_dte_max
    ]
    if not eligible:
        return None
    with_delta = [c for c in eligible if c.delta is not None]
    if with_delta:
        return min(with_delta, key=lambda c: abs(abs(c.delta) - settings.option_delta_target))  # type: ignore[operator]
    return min(eligible, key=lambda c: abs(c.strike - current_price))
