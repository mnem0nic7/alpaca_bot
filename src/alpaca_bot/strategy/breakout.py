from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime

from alpaca_bot.config import Settings
from alpaca_bot.domain.models import Bar, EntrySignal


def is_entry_session_time(timestamp: datetime, settings: Settings) -> bool:
    local_time = timestamp.astimezone(settings.market_timezone).time()
    return settings.entry_window_start <= local_time <= settings.entry_window_end


def is_past_flatten_time(timestamp: datetime, settings: Settings) -> bool:
    local_time = timestamp.astimezone(settings.market_timezone).time()
    return local_time >= settings.flatten_time


def session_day(timestamp: datetime, settings: Settings) -> date:
    return timestamp.astimezone(settings.market_timezone).date()


def daily_trend_filter_passes(daily_bars: Sequence[Bar], settings: Settings) -> bool:
    if len(daily_bars) < settings.daily_sma_period:
        return False

    window = daily_bars[-settings.daily_sma_period :]
    sma = sum(bar.close for bar in window) / len(window)
    latest_close = window[-1].close
    return latest_close > sma


def evaluate_breakout_signal(
    *,
    symbol: str,
    intraday_bars: Sequence[Bar],
    signal_index: int,
    daily_bars: Sequence[Bar],
    settings: Settings,
) -> EntrySignal | None:
    if symbol not in settings.symbols:
        return None
    if signal_index < settings.breakout_lookback_bars:
        return None

    signal_bar = intraday_bars[signal_index]
    if signal_bar.symbol != symbol:
        return None
    if not is_entry_session_time(signal_bar.timestamp, settings):
        return None
    if not daily_trend_filter_passes(daily_bars, settings):
        return None

    lookback = intraday_bars[
        signal_index - settings.breakout_lookback_bars : signal_index
    ]
    breakout_level = max(bar.high for bar in lookback)
    average_volume = sum(bar.volume for bar in lookback) / len(lookback)
    relative_volume = signal_bar.volume / average_volume

    if signal_bar.high <= breakout_level:
        return None
    if signal_bar.close <= breakout_level:
        return None
    if relative_volume < settings.relative_volume_threshold:
        return None

    stop_price = round(signal_bar.high + settings.entry_stop_price_buffer, 2)
    limit_price = round(stop_price * (1 + settings.stop_limit_buffer_pct), 2)
    breakout_stop_buffer = max(0.01, breakout_level * settings.breakout_stop_buffer_pct)
    initial_stop_price = round(breakout_level - breakout_stop_buffer, 2)
    return EntrySignal(
        symbol=symbol,
        signal_bar=signal_bar,
        entry_level=breakout_level,
        relative_volume=relative_volume,
        stop_price=stop_price,
        limit_price=limit_price,
        initial_stop_price=initial_stop_price,
    )
