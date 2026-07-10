from __future__ import annotations

from collections.abc import Sequence

from alpaca_bot.config import Settings
from alpaca_bot.domain.models import Bar, EntrySignal
from alpaca_bot.risk.atr import atr_stop_buffer, calculate_atr
from alpaca_bot.strategy.breakout import (
    daily_trend_filter_passes,
    is_entry_session_time,
    session_day,
)
from alpaca_bot.strategy.indicators import calculate_vwap

_calculate_vwap = calculate_vwap  # backward compat — test_vwap_reversion_strategy.py imports _calculate_vwap from here


def evaluate_vwap_reversion_signal(
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

    today = session_day(signal_bar.timestamp, settings)
    first_today_index = signal_index
    while (
        first_today_index > 0
        and session_day(intraday_bars[first_today_index - 1].timestamp, settings) == today
    ):
        first_today_index -= 1
    today_bars = intraday_bars[first_today_index : signal_index + 1]

    vwap = _calculate_vwap(today_bars)
    if vwap is None:
        return None

    if signal_bar.low > vwap * (1 - settings.vwap_dip_threshold_pct):
        return None
    if signal_bar.close < vwap:
        return None

    if signal_index < settings.relative_volume_lookback_bars:
        return None
    prior_bars = intraday_bars[
        signal_index - settings.relative_volume_lookback_bars : signal_index
    ]
    avg_volume = sum(b.volume for b in prior_bars) / len(prior_bars)
    relative_volume = signal_bar.volume / avg_volume if avg_volume > 0 else 0.0
    relative_volume_threshold = (
        settings.vwap_reversion_relative_volume_threshold
        if settings.vwap_reversion_relative_volume_threshold is not None
        else settings.relative_volume_threshold
    )
    if relative_volume < relative_volume_threshold:
        return None

    if calculate_atr(daily_bars, settings.atr_period) is None:
        return None

    atr_stop_multiplier = (
        settings.vwap_reversion_atr_stop_multiplier
        if settings.vwap_reversion_atr_stop_multiplier is not None
        else settings.atr_stop_multiplier
    )
    stop_buffer = atr_stop_buffer(
        daily_bars,
        settings.atr_period,
        atr_stop_multiplier,
        signal_bar.low,
        settings.breakout_stop_buffer_pct,
    )
    initial_stop_price = round(max(0.01, signal_bar.low - stop_buffer), 2)
    stop_price = round(signal_bar.high + settings.entry_stop_price_buffer, 2)
    limit_price = round(stop_price * (1 + settings.stop_limit_buffer_pct), 2)
    entry_level = round(vwap, 2)

    return EntrySignal(
        symbol=symbol,
        signal_bar=signal_bar,
        entry_level=entry_level,
        relative_volume=relative_volume,
        stop_price=stop_price,
        limit_price=limit_price,
        initial_stop_price=initial_stop_price,
    )
