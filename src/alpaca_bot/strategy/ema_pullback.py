from __future__ import annotations

from collections.abc import Sequence

from alpaca_bot.config import Settings
from alpaca_bot.domain.models import Bar, EntrySignal
from alpaca_bot.risk.atr import atr_stop_buffer
from alpaca_bot.strategy.breakout import daily_trend_filter_passes, is_entry_session_time


def _calculate_ema(bars: Sequence[Bar], signal_index: int, period: int) -> tuple[float, float]:
    """Return (prior_ema, current_ema) at signal_index using the standard EMA formula."""
    alpha = 2.0 / (period + 1)
    ema = bars[0].close
    prev_ema = ema
    for i in range(1, signal_index + 1):
        prev_ema = ema
        ema = alpha * bars[i].close + (1 - alpha) * ema
    return prev_ema, ema


def _detect_ema_pullback(
    bars: Sequence[Bar],
    signal_index: int,
    ema_period: int,
) -> bool:
    """
    Return True if a pullback to the EMA occurred before the signal bar.

    The prior bar (signal_index - 1) is the candidate pullback bar. You decide
    how strictly to define "touched the EMA":

    Strict  — prior_bar.close <= prior_ema  (close-based; fewer false positives)
    Loose   — prior_bar.low   <= prior_ema  (shadow-based; catches more pullbacks)
    Multi-bar — any of the last N bars touched the EMA before recovering

    Consider the trade-off: strict reduces noise but may miss valid pullbacks where
    price briefly dips below EMA intrabar. Loose captures those but can fire on bars
    that merely wick through the EMA without closing below it.

    Parameters
    ----------
    bars : intraday bars up to and including signal_index
    signal_index : index of the potential signal bar in `bars`
    ema_period : the EMA period (same as settings.ema_period)
    """
    if signal_index < 1:
        return False
    _, prior_ema = _calculate_ema(bars, signal_index - 1, ema_period)
    prior_bar = bars[signal_index - 1]
    return prior_bar.close <= prior_ema


def evaluate_ema_pullback_signal(
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

    # EMA warmup guard — need at least ema_period bars before signal_index
    if signal_index < settings.ema_period:
        return None

    prior_ema, current_ema = _calculate_ema(intraday_bars, signal_index, settings.ema_period)

    # Signal condition: bar closes above EMA after prior bar was at/below it
    if signal_bar.close <= current_ema:
        return None

    if not _detect_ema_pullback(intraday_bars, signal_index, settings.ema_period):
        return None

    if signal_index < settings.relative_volume_lookback_bars:
        return None
    prior_bars = intraday_bars[signal_index - settings.relative_volume_lookback_bars : signal_index]
    avg_volume = sum(bar.volume for bar in prior_bars) / len(prior_bars)
    relative_volume = signal_bar.volume / avg_volume if avg_volume > 0 else 0.0
    if relative_volume < settings.relative_volume_threshold:
        return None

    prior_bar = intraday_bars[signal_index - 1]
    stop_buffer = atr_stop_buffer(
        daily_bars, settings.atr_period, settings.atr_stop_multiplier,
        prior_bar.low, settings.breakout_stop_buffer_pct,
    )
    initial_stop_price = round(max(0.01, prior_bar.low - stop_buffer), 2)
    stop_price = round(signal_bar.high + settings.entry_stop_price_buffer, 2)
    limit_price = round(stop_price * (1 + settings.stop_limit_buffer_pct), 2)
    entry_level = round(current_ema, 2)

    return EntrySignal(
        symbol=symbol,
        signal_bar=signal_bar,
        entry_level=entry_level,
        relative_volume=relative_volume,
        stop_price=stop_price,
        limit_price=limit_price,
        initial_stop_price=initial_stop_price,
    )
