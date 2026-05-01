from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from alpaca_bot.config import Settings
from alpaca_bot.domain.models import Bar, EntrySignal
from alpaca_bot.strategy.bb_squeeze import evaluate_bb_squeeze_signal
from alpaca_bot.strategy.breakout import evaluate_breakout_signal
from alpaca_bot.strategy.bull_flag import evaluate_bull_flag_signal
from alpaca_bot.strategy.ema_pullback import evaluate_ema_pullback_signal
from alpaca_bot.strategy.failed_breakdown import evaluate_failed_breakdown_signal
from alpaca_bot.strategy.gap_and_go import evaluate_gap_and_go_signal
from alpaca_bot.strategy.high_watermark import evaluate_high_watermark_signal
from alpaca_bot.strategy.momentum import evaluate_momentum_signal
from alpaca_bot.strategy.orb import evaluate_orb_signal
from alpaca_bot.strategy.vwap_cross import evaluate_vwap_cross_signal
from alpaca_bot.strategy.vwap_reversion import evaluate_vwap_reversion_signal


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
    ) -> EntrySignal | None: ...


STRATEGY_REGISTRY: dict[str, StrategySignalEvaluator] = {
    "breakout": evaluate_breakout_signal,
    "momentum": evaluate_momentum_signal,
    "orb": evaluate_orb_signal,
    "high_watermark": evaluate_high_watermark_signal,
    "ema_pullback": evaluate_ema_pullback_signal,
    "vwap_reversion": evaluate_vwap_reversion_signal,
    "gap_and_go": evaluate_gap_and_go_signal,
    "bull_flag": evaluate_bull_flag_signal,
    "vwap_cross": evaluate_vwap_cross_signal,
    "bb_squeeze": evaluate_bb_squeeze_signal,
    "failed_breakdown": evaluate_failed_breakdown_signal,
}
