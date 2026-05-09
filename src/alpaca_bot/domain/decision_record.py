from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class DecisionRecord:
    cycle_at: datetime
    symbol: str
    strategy_name: str
    trading_mode: str
    strategy_version: str
    decision: str
    reject_stage: str | None
    reject_reason: str | None
    entry_level: float | None
    signal_bar_close: float | None
    relative_volume: float | None
    atr: float | None
    stop_price: float | None
    limit_price: float | None
    initial_stop_price: float | None
    quantity: float | None
    risk_per_share: float | None
    equity: float | None
    filter_results: dict
    vix_close: float | None = None
    vix_above_sma: bool | None = None
    sector_passing_pct: float | None = None
    vwap_at_signal: float | None = None
    signal_bar_above_vwap: bool | None = None
