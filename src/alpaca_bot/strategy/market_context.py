from __future__ import annotations

from datetime import datetime

from alpaca_bot.config import Settings
from alpaca_bot.domain.models import Bar, MarketContext


def compute_market_context(
    *,
    as_of: datetime,
    vix_bars: list[Bar],
    sector_bars_by_etf: dict[str, list[Bar]],
    settings: Settings,
) -> MarketContext:
    """Compute per-cycle market context from pre-fetched daily bar data.

    Pure function — no I/O. Fail-open: insufficient history yields None fields,
    which the engine treats as "filter not running."
    """
    vix_close, vix_sma, vix_above_sma = _compute_vix(vix_bars, settings)
    sector_etf_states, sector_passing_pct = _compute_sector(sector_bars_by_etf, settings)
    return MarketContext(
        as_of=as_of,
        vix_close=vix_close,
        vix_sma=vix_sma,
        vix_above_sma=vix_above_sma,
        sector_etf_states=sector_etf_states,
        sector_passing_pct=sector_passing_pct,
    )


def _compute_vix(
    bars: list[Bar],
    settings: Settings,
) -> tuple[float | None, float | None, bool | None]:
    """Return (vix_close, vix_sma, vix_above_sma).

    Needs at least vix_lookback_bars bars. Returns (None, None, None) on
    insufficient history so the engine fails open.
    """
    n = settings.vix_lookback_bars
    if len(bars) < n:
        return None, None, None
    window = bars[-n:]
    sma = sum(b.close for b in window) / n
    last_close = bars[-1].close
    return last_close, sma, last_close > sma


def _compute_sector(
    bars_by_etf: dict[str, list[Bar]],
    settings: Settings,
) -> tuple[dict[str, bool], float | None]:
    """Return (per-ETF above-SMA map, passing_pct).

    An ETF is True when its last close is above its N-bar SMA. ETFs with
    insufficient history are excluded from the denominator (fail-open).
    Returns ({}, None) when no ETFs have enough history.
    """
    n = settings.sector_etf_sma_period
    states: dict[str, bool] = {}
    for etf, bars in bars_by_etf.items():
        if len(bars) < n:
            continue
        window = bars[-n:]
        sma = sum(b.close for b in window) / n
        states[etf] = bars[-1].close > sma

    if not states:
        return {}, None

    passing_pct = sum(1 for v in states.values() if v) / len(states)
    return states, passing_pct
