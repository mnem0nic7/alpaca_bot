from __future__ import annotations

from collections.abc import Sequence

from alpaca_bot.config import Settings
from alpaca_bot.domain.models import Bar, EntrySignal
from alpaca_bot.risk.atr import atr_stop_buffer
from alpaca_bot.strategy.breakout import daily_trend_filter_passes, is_entry_session_time, session_day


def evaluate_orb_signal(
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

    # intraday_bars spans multiple days — filter to the signal bar's session date
    signal_date = session_day(signal_bar.timestamp, settings)
    today_bars = [
        bar for bar in intraday_bars[: signal_index + 1]
        if session_day(bar.timestamp, settings) == signal_date
    ]

    # need at least orb_opening_bars + 1 bars (the range bars plus a signal bar beyond the range)
    if len(today_bars) <= settings.orb_opening_bars:
        return None

    opening_range_bars = today_bars[: settings.orb_opening_bars]
    opening_range_high = max(bar.high for bar in opening_range_bars)
    opening_range_low = min(bar.low for bar in opening_range_bars)

    if signal_bar.high <= opening_range_high:
        return None
    if signal_bar.close <= opening_range_high:
        return None

    # volume baseline uses opening range bars so signals can fire early in the session
    avg_volume = sum(bar.volume for bar in opening_range_bars) / len(opening_range_bars)
    relative_volume = signal_bar.volume / avg_volume if avg_volume > 0 else 0.0
    if relative_volume < settings.relative_volume_threshold:
        return None

    stop_price = round(signal_bar.high + settings.entry_stop_price_buffer, 2)
    limit_price = round(stop_price * (1 + settings.stop_limit_buffer_pct), 2)
    stop_buffer = atr_stop_buffer(
        daily_bars, settings.atr_period, settings.atr_stop_multiplier,
        opening_range_low, settings.breakout_stop_buffer_pct,
    )
    initial_stop_price = round(max(0.01, opening_range_low - stop_buffer), 2)

    return EntrySignal(
        symbol=symbol,
        signal_bar=signal_bar,
        entry_level=opening_range_high,
        relative_volume=relative_volume,
        stop_price=stop_price,
        limit_price=limit_price,
        initial_stop_price=initial_stop_price,
    )
