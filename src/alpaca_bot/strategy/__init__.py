from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Callable, Protocol, runtime_checkable

from alpaca_bot.config import Settings
from alpaca_bot.domain.models import Bar, EntrySignal, OptionContract
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
from alpaca_bot.strategy.bear_bb_squeeze_down import make_bear_bb_squeeze_down_evaluator
from alpaca_bot.strategy.bear_breakdown import make_bear_breakdown_evaluator
from alpaca_bot.strategy.bear_ema_rejection import make_bear_ema_rejection_evaluator
from alpaca_bot.strategy.bear_failed_breakout import make_bear_failed_breakout_evaluator
from alpaca_bot.strategy.bear_flag import make_bear_flag_evaluator
from alpaca_bot.strategy.bear_gap_and_drop import make_bear_gap_and_drop_evaluator
from alpaca_bot.strategy.bear_low_watermark import make_bear_low_watermark_evaluator
from alpaca_bot.strategy.bear_momentum import make_bear_momentum_evaluator
from alpaca_bot.strategy.bear_orb import make_bear_orb_evaluator
from alpaca_bot.strategy.bear_vwap_breakdown import make_bear_vwap_breakdown_evaluator
from alpaca_bot.strategy.bear_vwap_cross_down import make_bear_vwap_cross_down_evaluator
from alpaca_bot.strategy.breakout_calls import make_breakout_calls_evaluator


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

OptionEvaluatorFactory = Callable[[Mapping[str, Sequence[OptionContract]]], StrategySignalEvaluator]

OPTION_STRATEGY_FACTORIES: dict[str, OptionEvaluatorFactory] = {
    "breakout_calls": make_breakout_calls_evaluator,
    "bear_breakdown": make_bear_breakdown_evaluator,
    "bear_momentum": make_bear_momentum_evaluator,
    "bear_orb": make_bear_orb_evaluator,
    "bear_low_watermark": make_bear_low_watermark_evaluator,
    "bear_ema_rejection": make_bear_ema_rejection_evaluator,
    "bear_vwap_breakdown": make_bear_vwap_breakdown_evaluator,
    "bear_gap_and_drop": make_bear_gap_and_drop_evaluator,
    "bear_flag": make_bear_flag_evaluator,
    "bear_vwap_cross_down": make_bear_vwap_cross_down_evaluator,
    "bear_bb_squeeze_down": make_bear_bb_squeeze_down_evaluator,
    "bear_failed_breakout": make_bear_failed_breakout_evaluator,
}

OPTION_STRATEGY_NAMES: frozenset[str] = frozenset(OPTION_STRATEGY_FACTORIES)
