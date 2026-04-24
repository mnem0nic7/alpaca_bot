from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from enum import StrEnum
from typing import Any

from alpaca_bot.config import TradingMode


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
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    stop_price: float | None = None
    limit_price: float | None = None
    initial_stop_price: float | None = None
    broker_order_id: str | None = None
    signal_timestamp: datetime | None = None


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
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(frozen=True)
class DailySessionState:
    session_date: date
    trading_mode: TradingMode
    strategy_version: str
    entries_disabled: bool
    flatten_complete: bool
    last_reconciled_at: datetime | None = None
    notes: str | None = None
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
