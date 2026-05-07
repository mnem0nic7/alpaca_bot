from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence

from alpaca_bot.config import Settings
from alpaca_bot.domain.models import Bar, EntrySignal, OptionContract
from alpaca_bot.strategy.breakout import daily_downtrend_filter_passes
from alpaca_bot.strategy.option_selector import select_put_contract


def evaluate_bear_orb_signal(
    *,
    symbol: str,
    intraday_bars: Sequence[Bar],
    signal_index: int,
    daily_bars: Sequence[Bar],
    settings: Settings,
) -> EntrySignal | None:
    if not intraday_bars or signal_index < 0 or signal_index >= len(intraday_bars):
        return None
    if not daily_downtrend_filter_passes(daily_bars, settings):
        return None
    opening_bars_count = settings.orb_opening_bars
    if signal_index < opening_bars_count:
        return None
    opening_bars = intraday_bars[:opening_bars_count]
    opening_range_low = min(bar.low for bar in opening_bars)
    signal_bar = intraday_bars[signal_index]
    if signal_bar.close >= opening_range_low:
        return None
    avg_vol = sum(b.volume for b in opening_bars) / len(opening_bars)
    if avg_vol <= 0 or signal_bar.volume / avg_vol < settings.relative_volume_threshold:
        return None
    return EntrySignal(
        symbol=symbol,
        signal_bar=signal_bar,
        entry_level=signal_bar.close,
        relative_volume=signal_bar.volume / avg_vol,
        stop_price=opening_range_low + settings.entry_stop_price_buffer,
        limit_price=0.0,       # equity-only field; factory overwrites with contract.ask
        initial_stop_price=0.01,  # no stop order on option contracts; EOD flatten is the exit
        option_contract=None,
    )


def make_bear_orb_evaluator(
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
        equity_signal = evaluate_bear_orb_signal(
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
