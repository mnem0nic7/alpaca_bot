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


def evaluate_vwap_cross_signal(
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
    today_bars = [
        b for b in intraday_bars[: signal_index + 1]
        if session_day(b.timestamp, settings) == today
    ]

    if len(today_bars) < 3:
        return None

    prior_today_bars = today_bars[:-1]
    prior_vwap = calculate_vwap(prior_today_bars[:-1])
    if prior_vwap is None:
        return None
    if prior_today_bars[-1].close >= prior_vwap:
        return None

    current_vwap = calculate_vwap(today_bars)
    if current_vwap is None:
        return None
    if signal_bar.close < current_vwap:
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
    entry_level = round(current_vwap, 2)

    return EntrySignal(
        symbol=symbol,
        signal_bar=signal_bar,
        entry_level=entry_level,
        relative_volume=relative_volume,
        stop_price=stop_price,
        limit_price=limit_price,
        initial_stop_price=initial_stop_price,
    )
