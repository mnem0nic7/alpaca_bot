from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence

from alpaca_bot.config import Settings
from alpaca_bot.domain.models import Bar, EntrySignal, OptionContract
from alpaca_bot.strategy.breakout import daily_downtrend_filter_passes
from alpaca_bot.strategy.option_selector import select_put_contract


def evaluate_bear_momentum_signal(
    *,
    symbol: str,
    intraday_bars: Sequence[Bar],
    signal_index: int,
    daily_bars: Sequence[Bar],
    settings: Settings,
) -> EntrySignal | None:
    if not intraday_bars or signal_index < 3 or signal_index >= len(intraday_bars):
        return None
    if not daily_downtrend_filter_passes(daily_bars, settings):
        return None
    signal_bar = intraday_bars[signal_index]
    if not (
        intraday_bars[signal_index - 2].close
        > intraday_bars[signal_index - 1].close
        > intraday_bars[signal_index].close
    ):
        return None
    atr_window = intraday_bars[max(0, signal_index - settings.atr_period) : signal_index + 1]
    if len(atr_window) < 2:
        return None
    trs = [
        max(b.high - b.low, abs(b.high - atr_window[i - 1].close), abs(b.low - atr_window[i - 1].close))
        for i, b in enumerate(atr_window[1:], 1)
    ]
    atr = sum(trs) / len(trs)
    stop_price = signal_bar.high + atr * settings.atr_stop_multiplier
    return EntrySignal(
        symbol=symbol,
        signal_bar=signal_bar,
        entry_level=signal_bar.close,
        relative_volume=1.0,
        stop_price=stop_price,
        limit_price=0.0,       # equity-only field; factory overwrites with contract.ask
        initial_stop_price=0.01,  # no stop order on option contracts; EOD flatten is the exit
        option_contract=None,
    )


def make_bear_momentum_evaluator(
    option_chains_by_symbol: Mapping[str, Sequence[OptionContract]],
) -> Callable[..., EntrySignal | None]:
    def evaluate(
        *,
        symbol: str,
        intraday_bars: Sequence[Bar],
        signal_index: int,
        daily_bars: Sequence[Bar],
        settings: Settings,
    ) -> EntrySignal | None:
        chains = option_chains_by_symbol.get(symbol, ())
        if not chains:
            return None
        equity_signal = evaluate_bear_momentum_signal(
            symbol=symbol,
            intraday_bars=intraday_bars,
            signal_index=signal_index,
            daily_bars=daily_bars,
            settings=settings,
        )
        if equity_signal is None:
            return None
        today = intraday_bars[-1].timestamp.astimezone(settings.market_timezone).date()
        contract = select_put_contract(
            chains,
            current_price=intraday_bars[-1].close,
            today=today,
            settings=settings,
        )
        if contract is None:
            return None
        return EntrySignal(
            symbol=symbol,
            signal_bar=equity_signal.signal_bar,
            entry_level=equity_signal.entry_level,
            relative_volume=equity_signal.relative_volume,
            stop_price=0.0,           # no stop order on option contracts
            limit_price=contract.ask,
            initial_stop_price=0.01,  # EOD flatten is the exit; no intraday stop
            option_contract=contract,
        )
    return evaluate
