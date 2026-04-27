from __future__ import annotations

from collections.abc import Sequence

from alpaca_bot.config import Settings
from alpaca_bot.domain.models import Bar, EntrySignal
from alpaca_bot.risk.atr import atr_stop_buffer
from alpaca_bot.strategy.breakout import daily_trend_filter_passes, is_entry_session_time, session_day


def evaluate_momentum_signal(
    *,
    symbol: str,
    intraday_bars: Sequence[Bar],
    signal_index: int,
    daily_bars: Sequence[Bar],
    settings: Settings,
) -> EntrySignal | None:
    if symbol not in settings.symbols:
        return None
    if not intraday_bars or signal_index < 0 or signal_index >= len(intraday_bars):
        return None

    signal_bar = intraday_bars[signal_index]
    if signal_bar.symbol != symbol:
        return None
    if not is_entry_session_time(signal_bar.timestamp, settings):
        return None

    # Exclude today's partial daily bar — Alpaca may include an in-progress bar.
    today = session_day(signal_bar.timestamp, settings)
    prior_daily = [b for b in daily_bars if b.timestamp.date() < today]

    if not daily_trend_filter_passes(prior_daily, settings):
        return None

    lookback = settings.prior_day_high_lookback_bars
    if len(prior_daily) < lookback:
        return None
    yesterday_high = prior_daily[-lookback].high

    if signal_bar.high <= yesterday_high:
        return None
    if signal_bar.close <= yesterday_high:
        return None

    if signal_index < settings.relative_volume_lookback_bars:
        return None
    prior_bars = intraday_bars[signal_index - settings.relative_volume_lookback_bars : signal_index]
    average_volume = sum(bar.volume for bar in prior_bars) / len(prior_bars)
    relative_volume = signal_bar.volume / average_volume if average_volume > 0 else 0.0
    if relative_volume < settings.relative_volume_threshold:
        return None

    stop_price = round(signal_bar.high + settings.entry_stop_price_buffer, 2)
    limit_price = round(stop_price * (1 + settings.stop_limit_buffer_pct), 2)
    stop_buffer = atr_stop_buffer(
        prior_daily, settings.atr_period, settings.atr_stop_multiplier,
        yesterday_high, settings.breakout_stop_buffer_pct,
    )
    initial_stop_price = round(max(0.01, yesterday_high - stop_buffer), 2)

    return EntrySignal(
        symbol=symbol,
        signal_bar=signal_bar,
        entry_level=yesterday_high,
        relative_volume=relative_volume,
        stop_price=stop_price,
        limit_price=limit_price,
        initial_stop_price=initial_stop_price,
    )
