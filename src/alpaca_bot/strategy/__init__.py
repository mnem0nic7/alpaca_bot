from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from alpaca_bot.config import Settings
from alpaca_bot.domain.models import Bar, BreakoutSignal


@runtime_checkable
class StrategySignalEvaluator(Protocol):
    def __call__(
        self,
        *,
        symbol: str,
        intraday_bars: Sequence[Bar],
        signal_index: int,
        daily_bars: Sequence[Bar],
        settings: Settings,
    ) -> BreakoutSignal | None: ...
