from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence

from alpaca_bot.config import Settings
from alpaca_bot.domain.models import Bar, EntrySignal, OptionContract
from alpaca_bot.strategy.breakout import daily_downtrend_filter_passes
from alpaca_bot.strategy.option_selector import select_put_contract


def evaluate_bear_breakdown_signal(
    *,
    symbol: str,
    intraday_bars: Sequence[Bar],
    signal_index: int,
    daily_bars: Sequence[Bar],
    settings: Settings,
) -> EntrySignal | None:
    if not daily_downtrend_filter_passes(daily_bars, settings):
        return None
    lookback = settings.breakout_lookback_bars
    if signal_index < lookback:
        return None
    signal_bar = intraday_bars[signal_index]
    window = intraday_bars[signal_index - lookback : signal_index]
    prior_low = min(bar.low for bar in window)
    if signal_bar.low >= prior_low:
        return None
    vol_lookback = settings.relative_volume_lookback_bars
    if signal_index < vol_lookback:
        return None
    avg_vol = sum(b.volume for b in intraday_bars[signal_index - vol_lookback : signal_index]) / vol_lookback
    if avg_vol <= 0 or signal_bar.volume / avg_vol < settings.relative_volume_threshold:
        return None
    # ATR-based stop above breakdown level
    atr_window = intraday_bars[max(0, signal_index - settings.atr_period) : signal_index + 1]
    if len(atr_window) < 2:
        return None
    trs = [
        max(b.high - b.low, abs(b.high - atr_window[i - 1].close), abs(b.low - atr_window[i - 1].close))
        for i, b in enumerate(atr_window[1:], 1)
    ]
    atr = sum(trs) / len(trs)
    stop_price = signal_bar.low + atr * settings.atr_stop_multiplier
    return EntrySignal(
        symbol=symbol,
        signal_bar=signal_bar,
        entry_level=signal_bar.low,
        relative_volume=signal_bar.volume / avg_vol,
        stop_price=stop_price,
        limit_price=0.0,
        initial_stop_price=0.01,
        option_contract=None,
    )


def make_bear_breakdown_evaluator(
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
        equity_signal = evaluate_bear_breakdown_signal(
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
            stop_price=0.0,
            limit_price=contract.ask,
            initial_stop_price=0.01,
            option_contract=contract,
        )
    return evaluate
