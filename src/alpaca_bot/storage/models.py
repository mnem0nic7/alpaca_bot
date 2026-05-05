from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from enum import StrEnum
from typing import Any

from alpaca_bot.config import TradingMode


GLOBAL_SESSION_STATE_STRATEGY_NAME = "_global"
EQUITY_SESSION_STATE_STRATEGY_NAME = "_equity"


class TradingStatusValue(StrEnum):
    ENABLED = "enabled"
    HALTED = "halted"
    CLOSE_ONLY = "close_only"


@dataclass(frozen=True)
class TradingStatus:
    trading_mode: TradingMode
    strategy_version: str
    status: TradingStatusValue
    kill_switch_enabled: bool
    status_reason: str | None = None
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(frozen=True)
class AuditEvent:
    event_type: str
    payload: dict[str, Any]
    symbol: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(frozen=True)
class OrderRecord:
    client_order_id: str
    symbol: str
    side: str
    intent_type: str
    status: str
    quantity: int
    trading_mode: TradingMode
    strategy_version: str
    strategy_name: str = "breakout"
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    stop_price: float | None = None
    limit_price: float | None = None
    initial_stop_price: float | None = None
    broker_order_id: str | None = None
    signal_timestamp: datetime | None = None
    fill_price: float | None = None
    filled_quantity: int | None = None
    reconciliation_miss_count: int = 0


@dataclass(frozen=True)
class PositionRecord:
    symbol: str
    trading_mode: TradingMode
    strategy_version: str
    quantity: int
    entry_price: float
    stop_price: float
    initial_stop_price: float
    opened_at: datetime
    strategy_name: str = "breakout"
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(frozen=True)
class DailySessionState:
    session_date: date
    trading_mode: TradingMode
    strategy_version: str
    entries_disabled: bool
    flatten_complete: bool
    strategy_name: str = "breakout"
    last_reconciled_at: datetime | None = None
    notes: str | None = None
    equity_baseline: float | None = None
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(frozen=True)
class OptionOrderRecord:
    client_order_id: str
    occ_symbol: str
    underlying_symbol: str
    option_type: str
    strike: float
    expiry: date
    side: str
    status: str
    quantity: int
    trading_mode: TradingMode
    strategy_version: str
    strategy_name: str
    created_at: datetime
    updated_at: datetime
    limit_price: float | None = None
    broker_order_id: str | None = None
    fill_price: float | None = None
    filled_quantity: int | None = None


@dataclass(frozen=True)
class StrategyFlag:
    strategy_name: str
    trading_mode: TradingMode
    strategy_version: str
    enabled: bool
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(frozen=True)
class StrategyWeight:
    strategy_name: str
    trading_mode: TradingMode
    strategy_version: str
    weight: float
    sharpe: float
    computed_at: datetime
