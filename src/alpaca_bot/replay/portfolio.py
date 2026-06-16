# src/alpaca_bot/replay/portfolio.py
"""Cross-sectional / portfolio replay over many symbols sharing one equity pool.

The single-symbol ReplayRunner walks one ReplayScenario's bars and lets the pure
engine decide entries/exits for that lone symbol. Because each scenario carries
exactly one symbol, the engine's cross-sectional machinery — ranking entry
candidates by signal strength, capping at available slots, enforcing a portfolio
exposure cap — is a permanent no-op.

PortfolioReplayRunner feeds the SAME pure ``evaluate_cycle`` the full multi-symbol
mappings on each cycle against ONE shared equity pool, so the ranking/slot/exposure
logic finally exercises. The engine stays pure: all bookkeeping (lanes, fills,
equity) lives here in the harness.

This module builds the data scaffolding only: index bars by symbol, join a union
timeline across symbols, and produce per-symbol point-in-time daily slices. The
cycle loop that drives entries/exits is layered on in a later task.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from alpaca_bot.config import Settings
from alpaca_bot.core.engine import CycleIntentType, evaluate_cycle
from alpaca_bot.domain.models import (
    Bar,
    OpenPosition,
    ReplayScenario,
    WorkingEntryOrder,
)
from alpaca_bot.replay.mechanics import (
    entry_fill_price,
    eod_exit_price,
    profit_target_price,
    simulate_buy_stop_limit_fill,
    stop_exit_price,
)
from alpaca_bot.replay.report import ReplayTradeRecord
from alpaca_bot.risk.sizing import calculate_position_size
from alpaca_bot.strategy import StrategySignalEvaluator
from alpaca_bot.strategy.breakout import session_day


@dataclass
class _Lane:
    """Per-symbol replay state in the shared-equity portfolio run."""

    symbol: str
    intraday: list[Bar]
    daily: list[Bar]
    cursor: int = 0
    working_order: WorkingEntryOrder | None = None
    position: OpenPosition | None = None


class PortfolioReplayRunner:
    def __init__(
        self,
        settings: Settings,
        signal_evaluator: StrategySignalEvaluator | None = None,
        strategy_name: str = "breakout",
    ):
        self.settings = settings
        self.signal_evaluator = signal_evaluator
        self.strategy_name = strategy_name
        self._lanes: dict[str, _Lane] = {}

    def _index_scenarios(self, scenarios: list[ReplayScenario]) -> None:
        self._lanes = {}
        for sc in scenarios:
            intraday = sorted(sc.intraday_bars, key=lambda b: b.timestamp)
            daily = sorted(sc.daily_bars, key=lambda b: b.timestamp)
            self._lanes[sc.symbol] = _Lane(symbol=sc.symbol, intraday=intraday, daily=daily)

    def _build_timeline(self, scenarios: list[ReplayScenario]) -> list[datetime]:
        stamps: set[datetime] = set()
        for sc in scenarios:
            for b in sc.intraday_bars:
                stamps.add(b.timestamp)
        return sorted(stamps)

    def _daily_slice_for(self, symbol: str, now: datetime) -> list[Bar]:
        lane = self._lanes[symbol]
        day = session_day(now, self.settings)
        tz = self.settings.market_timezone
        return [b for b in lane.daily if b.timestamp.astimezone(tz).date() < day]
