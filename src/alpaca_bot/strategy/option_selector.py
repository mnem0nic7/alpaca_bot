from __future__ import annotations

from collections.abc import Sequence
from datetime import date

from alpaca_bot.config import Settings
from alpaca_bot.domain.models import OptionContract


def _eligible_contracts(
    contracts: Sequence[OptionContract],
    option_type: str,
    today: date,
    settings: Settings,
) -> list[OptionContract]:
    # NOTE: When option_min_open_interest > 0, contracts with open_interest=None
    # still pass the OI filter (fail-open). Indicative feeds may not report OI
    # for all strikes; rejecting them would silently eliminate all contracts.
    return [
        c for c in contracts
        if c.option_type == option_type
        and c.ask > 0
        and settings.option_dte_min <= (c.expiry - today).days <= settings.option_dte_max
        and c.spread_pct <= settings.option_max_spread_pct
        and (
            settings.option_min_open_interest == 0
            or c.open_interest is None
            or c.open_interest >= settings.option_min_open_interest
        )
    ]


def select_call_contract(
    contracts: Sequence[OptionContract],
    *,
    current_price: float,
    today: date,
    settings: Settings,
) -> OptionContract | None:
    eligible = _eligible_contracts(contracts, "call", today, settings)
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
    eligible = _eligible_contracts(contracts, "put", today, settings)
    if not eligible:
        return None
    with_delta = [c for c in eligible if c.delta is not None]
    if with_delta:
        return min(with_delta, key=lambda c: abs(abs(c.delta) - settings.option_delta_target))  # type: ignore[operator]
    return min(eligible, key=lambda c: abs(c.strike - current_price))
