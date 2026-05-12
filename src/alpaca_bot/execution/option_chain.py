from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from datetime import date
from typing import Any, Protocol, runtime_checkable

from alpaca_bot.config import Settings
from alpaca_bot.domain.models import OptionContract

logger = logging.getLogger(__name__)


@runtime_checkable
class OptionChainAdapterProtocol(Protocol):
    def get_option_chain(self, symbol: str, settings: Settings) -> list[OptionContract]: ...


class AlpacaOptionChainAdapter:
    def __init__(self, option_data_client: Any) -> None:
        self._client = option_data_client

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        *,
        _client_factory: Any | None = None,
    ) -> "AlpacaOptionChainAdapter":
        from alpaca.data.historical import OptionHistoricalDataClient  # type: ignore[import]
        from alpaca_bot.execution.alpaca import resolve_alpaca_credentials

        api_key, secret_key, _paper = resolve_alpaca_credentials(settings)
        factory = _client_factory if _client_factory is not None else OptionHistoricalDataClient
        return cls(factory(api_key=api_key, secret_key=secret_key))

    def get_option_chain(self, symbol: str, settings: Settings) -> list[OptionContract]:
        try:
            from alpaca.data.requests import OptionChainRequest  # type: ignore[import]
            request = OptionChainRequest(underlying_symbol=symbol, feed="indicative")
        except ImportError:
            return []

        try:
            snapshots: dict[str, Any] = self._client.get_option_chain(request)
        except Exception:
            logger.warning("option chain fetch failed for %s", symbol, exc_info=True)
            return []

        contracts = []
        for occ_symbol, snapshot in snapshots.items():
            try:
                contracts.append(_snapshot_to_contract(occ_symbol, symbol, snapshot))
            except Exception:
                continue
        return contracts


_OCC_RE = re.compile(r'^([A-Z]{1,6})(\d{2})(\d{2})(\d{2})([CP])(\d{8})$')


def _parse_occ(occ_symbol: str) -> tuple[date, str, float]:
    """Parse expiry, option_type ('call'/'put'), and strike from OCC symbol.

    OCC format: UNDERLYING YYMMDD C/P STRIKE(8 digits = price × 1000)
    Example: AAPL240701C00150000 → 2024-07-01, 'call', 150.0
    """
    m = _OCC_RE.match(occ_symbol)
    if m is None:
        raise ValueError(f"Cannot parse OCC symbol: {occ_symbol!r}")
    _, yy, mm, dd, cp, strike_str = m.groups()
    expiry = date(int(yy) + 2000, int(mm), int(dd))
    option_type = "call" if cp == "C" else "put"
    strike = int(strike_str) / 1000.0
    return expiry, option_type, strike


def _snapshot_to_contract(occ_symbol: str, underlying: str, snapshot: Any) -> OptionContract:
    expiry, option_type, strike = _parse_occ(occ_symbol)

    quote = snapshot.latest_quote
    ask = float(quote.ask_price) if quote is not None else 0.0
    bid = float(quote.bid_price) if quote is not None else 0.0

    delta: float | None = None
    if snapshot.greeks is not None:
        try:
            delta = float(snapshot.greeks.delta)
        except (TypeError, AttributeError):
            delta = None

    open_interest: int | None = None
    try:
        raw_oi = getattr(snapshot, "open_interest", None)
        if raw_oi is not None:
            open_interest = int(raw_oi)
    except (TypeError, ValueError):
        open_interest = None

    return OptionContract(
        occ_symbol=occ_symbol,
        underlying=underlying,
        option_type=option_type,
        strike=strike,
        expiry=expiry,
        bid=bid,
        ask=ask,
        delta=delta,
        open_interest=open_interest,
    )
