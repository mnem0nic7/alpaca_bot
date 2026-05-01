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


def evaluate_bull_flag_signal(
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

    pole_bars = today_bars[:-1]
    if not pole_bars:
        return None

    pole_open = pole_bars[0].open
    pole_high = max(b.high for b in pole_bars)
    pole_run_pct = (pole_high - pole_open) / pole_open if pole_open > 0 else 0.0
    if pole_run_pct < settings.bull_flag_min_run_pct:
        return None

    pole_low = min(b.low for b in pole_bars)
    pole_range = pole_high - pole_low
    if pole_range <= 0:
        return None

    signal_range = signal_bar.high - signal_bar.low
    if signal_range > pole_range * settings.bull_flag_consolidation_range_pct:
        return None

    pole_avg_volume = sum(b.volume for b in pole_bars) / len(pole_bars)
    if signal_bar.volume > pole_avg_volume * settings.bull_flag_consolidation_volume_ratio:
        return None

    first_today_index = signal_index - len(today_bars) + 1
    if first_today_index < settings.relative_volume_lookback_bars:
        return None
    baseline_bars = intraday_bars[
        first_today_index - settings.relative_volume_lookback_bars : first_today_index
    ]
    baseline_avg_volume = sum(b.volume for b in baseline_bars) / len(baseline_bars) if baseline_bars else 0.0
    if baseline_avg_volume <= 0:
        return None

    relative_volume = pole_avg_volume / baseline_avg_volume
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
    stop_price = round(pole_high + settings.entry_stop_price_buffer, 2)
    limit_price = round(stop_price * (1 + settings.stop_limit_buffer_pct), 2)
    entry_level = pole_high

    return EntrySignal(
        symbol=symbol,
        signal_bar=signal_bar,
        entry_level=entry_level,
        relative_volume=relative_volume,
        stop_price=stop_price,
        limit_price=limit_price,
        initial_stop_price=initial_stop_price,
    )
