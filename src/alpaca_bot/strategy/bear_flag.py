from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence

from alpaca_bot.config import Settings
from alpaca_bot.domain.models import Bar, EntrySignal, OptionContract
from alpaca_bot.strategy.breakout import daily_downtrend_filter_passes
from alpaca_bot.strategy.option_selector import select_put_contract

_MIN_POLE_BARS = 2
_MIN_CONSOLIDATION_BARS = 2


def evaluate_bear_flag_signal(
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
    if signal_index < _MIN_POLE_BARS + _MIN_CONSOLIDATION_BARS:
        return None
    signal_bar = intraday_bars[signal_index]
    pole_end = None
    pole_bars_found = None
    for pole_len in range(_MIN_POLE_BARS, min(5, signal_index)):
        pole_start_idx = signal_index - pole_len - _MIN_CONSOLIDATION_BARS
        if pole_start_idx < 0:
            break
        pole_bars = intraday_bars[pole_start_idx : pole_start_idx + pole_len]
        pole_open = pole_bars[0].open
        pole_low = min(b.low for b in pole_bars)
        if pole_open <= 0:
            continue
        drop_pct = (pole_open - pole_low) / pole_open
        if drop_pct >= settings.bull_flag_min_run_pct:
            pole_end = pole_start_idx + pole_len
            pole_bars_found = pole_bars
            break
    if pole_end is None or pole_bars_found is None:
        return None
    consol_bars = intraday_bars[pole_end : signal_index]
    if len(consol_bars) < _MIN_CONSOLIDATION_BARS:
        return None
    consol_high = max(b.high for b in consol_bars)
    consol_low = min(b.low for b in consol_bars)
    consol_range = consol_high - consol_low
    pole_ref_price = intraday_bars[pole_end - 1].close if pole_end > 0 else intraday_bars[0].close
    if pole_ref_price <= 0:
        return None
    if consol_range / pole_ref_price > settings.bull_flag_consolidation_range_pct:
        return None
    pole_avg_vol = sum(b.volume for b in pole_bars_found) / len(pole_bars_found)
    consol_avg_vol = sum(b.volume for b in consol_bars) / len(consol_bars)
    if pole_avg_vol > 0 and consol_avg_vol / pole_avg_vol > settings.bull_flag_consolidation_volume_ratio:
        return None
    if signal_bar.close >= consol_low:
        return None
    entry_level = min(b.low for b in pole_bars_found)
    rel_vol = signal_bar.volume / pole_avg_vol if pole_avg_vol > 0 else 1.0
    return EntrySignal(
        symbol=symbol,
        signal_bar=signal_bar,
        entry_level=entry_level,
        relative_volume=rel_vol,
        stop_price=consol_high + settings.entry_stop_price_buffer,
        limit_price=0.0,
        initial_stop_price=0.01,
        option_contract=None,
    )


def make_bear_flag_evaluator(
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
        equity_signal = evaluate_bear_flag_signal(
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
