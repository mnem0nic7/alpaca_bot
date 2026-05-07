from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence

from alpaca_bot.config import Settings
from alpaca_bot.domain.models import Bar, EntrySignal, OptionContract
from alpaca_bot.strategy.breakout import daily_downtrend_filter_passes
from alpaca_bot.strategy.indicators import calculate_vwap
from alpaca_bot.strategy.option_selector import select_put_contract


def evaluate_bear_vwap_breakdown_signal(
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
    today_bars = intraday_bars[: signal_index + 1]
    vwap = calculate_vwap(today_bars)
    if vwap is None:
        return None
    signal_bar = intraday_bars[signal_index]
    threshold = settings.vwap_dip_threshold_pct
    if signal_bar.high <= vwap * (1 + threshold) or signal_bar.close >= vwap:
        return None
    vol_lookback = min(settings.relative_volume_lookback_bars, signal_index)
    if vol_lookback == 0:
        rel_vol = 1.0
    else:
        avg_vol = sum(b.volume for b in intraday_bars[signal_index - vol_lookback : signal_index]) / vol_lookback
        rel_vol = signal_bar.volume / avg_vol if avg_vol > 0 else 1.0
    return EntrySignal(
        symbol=symbol,
        signal_bar=signal_bar,
        entry_level=signal_bar.close,
        relative_volume=rel_vol,
        stop_price=vwap + settings.entry_stop_price_buffer,
        limit_price=0.0,
        initial_stop_price=0.01,
        option_contract=None,
    )


def make_bear_vwap_breakdown_evaluator(
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
        equity_signal = evaluate_bear_vwap_breakdown_signal(
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
