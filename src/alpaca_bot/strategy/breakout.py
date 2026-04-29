from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime

from alpaca_bot.config import Settings
from alpaca_bot.domain.models import Bar, EntrySignal
from alpaca_bot.risk.atr import atr_stop_buffer
from alpaca_bot.strategy.session import SessionType, detect_session_type
from alpaca_bot.strategy.session import is_entry_window as _is_entry_window
from alpaca_bot.strategy.session import is_flatten_time as _is_flatten_time


def is_entry_session_time(timestamp: datetime, settings: Settings) -> bool:
    session = detect_session_type(timestamp, settings)
    return _is_entry_window(timestamp, settings, session)


def is_past_flatten_time(timestamp: datetime, settings: Settings) -> bool:
    session = detect_session_type(timestamp, settings)
    return _is_flatten_time(timestamp, settings, session)


def session_day(timestamp: datetime, settings: Settings) -> date:
    return timestamp.astimezone(settings.market_timezone).date()


def daily_trend_filter_passes(daily_bars: Sequence[Bar], settings: Settings) -> bool:
    if len(daily_bars) < settings.daily_sma_period + 1:
        return False

    # Exclude the last bar which may be a partial (intraday) session bar.
    window = daily_bars[-settings.daily_sma_period - 1 : -1]
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
    if not intraday_bars or signal_index < 0 or signal_index >= len(intraday_bars):
        return None
    min_lookback = max(settings.breakout_lookback_bars, settings.relative_volume_lookback_bars)
    if signal_index < min_lookback:
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
    vol_lookback = intraday_bars[
        signal_index - settings.relative_volume_lookback_bars : signal_index
    ]
    average_volume = sum(bar.volume for bar in vol_lookback) / len(vol_lookback) if vol_lookback else 0.0
    relative_volume = signal_bar.volume / average_volume if average_volume > 0 else 0.0

    if signal_bar.high <= breakout_level:
        return None
    if signal_bar.close <= breakout_level:
        return None
    if relative_volume < settings.relative_volume_threshold:
        return None

    stop_price = round(breakout_level + settings.entry_stop_price_buffer, 2)
    limit_price = round(stop_price * (1 + settings.stop_limit_buffer_pct), 2)
    stop_buffer = atr_stop_buffer(
        daily_bars, settings.atr_period, settings.atr_stop_multiplier,
        breakout_level, settings.breakout_stop_buffer_pct,
    )
    initial_stop_price = round(max(0.01, breakout_level - stop_buffer), 2)
    return EntrySignal(
        symbol=symbol,
        signal_bar=signal_bar,
        entry_level=breakout_level,
        relative_volume=relative_volume,
        stop_price=stop_price,
        limit_price=limit_price,
        initial_stop_price=initial_stop_price,
    )
