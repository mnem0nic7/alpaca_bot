from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from alpaca_bot.domain.enums import IntentType


@dataclass(frozen=True)
class Bar:
    symbol: str
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "Bar":
        return cls(
            symbol=str(payload["symbol"]).upper(),
            timestamp=datetime.fromisoformat(str(payload["timestamp"])),
            open=float(payload["open"]),
            high=float(payload["high"]),
            low=float(payload["low"]),
            close=float(payload["close"]),
            volume=float(payload["volume"]),
        )


@dataclass(frozen=True)
class EntrySignal:
    symbol: str
    signal_bar: Bar
    entry_level: float
    relative_volume: float
    stop_price: float
    limit_price: float
    initial_stop_price: float


@dataclass(frozen=True)
class ReplayEvent:
    event_type: IntentType
    symbol: str
    timestamp: datetime
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class WorkingEntryOrder:
    symbol: str
    signal_timestamp: datetime
    active_bar_timestamp: datetime
    stop_price: float
    limit_price: float
    initial_stop_price: float
    entry_level: float
    relative_volume: float


@dataclass
class OpenPosition:
    symbol: str
    entry_timestamp: datetime
    entry_price: float
    quantity: int
    entry_level: float
    initial_stop_price: float
    stop_price: float
    trailing_active: bool = False
    highest_price: float = 0.0
    strategy_name: str = "breakout"

    @property
    def risk_per_share(self) -> float:
        return self.entry_price - self.initial_stop_price


@dataclass
class ReplayScenario:
    name: str
    symbol: str
    starting_equity: float
    daily_bars: list[Bar]
    intraday_bars: list[Bar]

    @property
    def session_date(self) -> date:
        return self.intraday_bars[0].timestamp.date()


@dataclass
class ReplayResult:
    scenario: ReplayScenario
    events: list[ReplayEvent]
    final_position: OpenPosition | None
    traded_symbols: set[tuple[str, date]]
    backtest_report: object | None = None  # BacktestReport; typed as object to avoid circular import
