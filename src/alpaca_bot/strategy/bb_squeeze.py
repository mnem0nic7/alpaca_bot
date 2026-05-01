from __future__ import annotations

from collections.abc import Sequence

from alpaca_bot.config import Settings
from alpaca_bot.domain.models import Bar, EntrySignal
from alpaca_bot.risk.atr import atr_stop_buffer, calculate_atr
from alpaca_bot.strategy.breakout import (
    daily_trend_filter_passes,
    is_entry_session_time,
)
from alpaca_bot.strategy.indicators import calculate_bollinger_bands


def evaluate_bb_squeeze_signal(
    *,
    symbol: str,
    intraday_bars: Sequence[Bar],
    signal_index: int,
    daily_bars: Sequence[Bar],
    settings: Settings,
) -> EntrySignal | None:
    if not intraday_bars or signal_index < 0 or signal_index >= len(intraday_bars):
        return None

    signal_bar = intraday_bars[signal_index]
    if signal_bar.symbol != symbol:
        return None
    if not is_entry_session_time(signal_bar.timestamp, settings):
        return None
    if not daily_trend_filter_passes(daily_bars, settings):
        return None

    min_required = settings.bb_period + settings.bb_squeeze_min_bars - 1
    if signal_index < min_required:
        return None

    for i in range(signal_index - settings.bb_squeeze_min_bars, signal_index):
        bands = calculate_bollinger_bands(
            intraday_bars[: i + 1], settings.bb_period, settings.bb_std_dev
        )
        if bands is None:
            return None
        lower, midline, upper = bands
        if midline <= 0:
            return None
        band_width_pct = (upper - lower) / midline
        if band_width_pct >= settings.bb_squeeze_threshold_pct:
            return None

    prior_bands = calculate_bollinger_bands(
        intraday_bars[:signal_index], settings.bb_period, settings.bb_std_dev
    )
    if prior_bands is None:
        return None
    _lower_prior, _midline_prior, upper_prior = prior_bands
    if signal_bar.close <= upper_prior:
        return None

    if signal_index < settings.relative_volume_lookback_bars:
        return None
    lookback_bars = intraday_bars[
        signal_index - settings.relative_volume_lookback_bars : signal_index
    ]
    avg_volume = sum(b.volume for b in lookback_bars) / len(lookback_bars)
    relative_volume = signal_bar.volume / avg_volume if avg_volume > 0 else 0.0
    if relative_volume < settings.relative_volume_threshold:
        return None

    if calculate_atr(daily_bars, settings.atr_period) is None:
        return None

    stop_buffer = atr_stop_buffer(
        daily_bars,
        settings.atr_period,
        settings.atr_stop_multiplier,
        signal_bar.low,
        settings.breakout_stop_buffer_pct,
    )
    initial_stop_price = round(max(0.01, signal_bar.low - stop_buffer), 2)
    stop_price = round(signal_bar.high + settings.entry_stop_price_buffer, 2)
    limit_price = round(stop_price * (1 + settings.stop_limit_buffer_pct), 2)
    entry_level = round(upper_prior, 2)

    return EntrySignal(
        symbol=symbol,
        signal_bar=signal_bar,
        entry_level=entry_level,
        relative_volume=relative_volume,
        stop_price=stop_price,
        limit_price=limit_price,
        initial_stop_price=initial_stop_price,
    )
