from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime

from alpaca_bot.config import Settings
from alpaca_bot.domain.models import Bar, EntrySignal
from alpaca_bot.risk.atr import atr_stop_buffer, calculate_atr
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


def daily_trend_filter_exit_passes(daily_bars: Sequence[Bar], settings: Settings) -> bool:
    """Return False when the last TREND_FILTER_EXIT_LOOKBACK_DAYS closes are all below the
    daily SMA — meaning an exit is warranted.  Returns True (hold) otherwise.

    The final element of daily_bars is treated as a partial (intraday) bar and excluded.
    Requires sma_period + lookback_days + 1 bars total (sma window + lookback completed days
    + 1 partial).
    """
    n = settings.trend_filter_exit_lookback_days
    # Need: sma_period bars for the SMA window + n completed days to check + 1 partial bar.
    # The partial bar sits at bars[-1]; the n-th completed day sits at bars[-(n+1)].
    # Accessing bars[-(n + sma_period)] requires len >= n + sma_period.
    required = settings.daily_sma_period + n
    if len(daily_bars) < required:
        return True  # insufficient history → hold

    for offset in range(n):
        # offset=0: check latest completed bar (index -2, excluding partial at -1)
        # offset=1: check day before that, etc.
        window_end = -(1 + offset)          # excludes partial and any already-checked days
        window_start = window_end - settings.daily_sma_period
        window = daily_bars[window_start:window_end]
        sma = sum(b.close for b in window) / len(window)
        close = daily_bars[window_end - 1].close
        if close > sma:
            return True  # at least one day above SMA → hold
    return False  # all N days below SMA → exit warranted


def daily_trend_filter_short_exit_passes(daily_bars: Sequence[Bar], settings: Settings) -> bool:
    """Return False when the last TREND_FILTER_EXIT_LOOKBACK_DAYS closes are all ABOVE the
    daily SMA — uptrend confirmed, warranting an exit of the short. Returns True (hold) otherwise."""
    n = settings.trend_filter_exit_lookback_days
    required = settings.daily_sma_period + n
    if len(daily_bars) < required:
        return True  # insufficient history → hold
    for offset in range(n):
        window_end = -(1 + offset)
        window_start = window_end - settings.daily_sma_period
        window = daily_bars[window_start:window_end]
        sma = sum(b.close for b in window) / len(window)
        close = daily_bars[window_end - 1].close
        if close <= sma:
            return True  # at least one day at or below SMA → hold
    return False  # all N days above SMA → uptrend confirmed → exit short


def daily_downtrend_filter_passes(daily_bars: Sequence[Bar], settings: Settings) -> bool:
    """Returns True when the prior close is BELOW the SMA — stock is in a downtrend."""
    if len(daily_bars) < settings.daily_sma_period + 1:
        return False
    # Exclude the last bar which may be a partial (intraday) session bar.
    window = daily_bars[-settings.daily_sma_period - 1 : -1]
    sma = sum(bar.close for bar in window) / len(window)
    latest_close = window[-1].close
    return latest_close < sma


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
    if calculate_atr(daily_bars, settings.atr_period) is None:
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
