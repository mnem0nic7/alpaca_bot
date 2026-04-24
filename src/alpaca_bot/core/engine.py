from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum
from typing import Mapping, Sequence

from alpaca_bot.config import Settings
from alpaca_bot.domain import Bar, OpenPosition
from alpaca_bot.risk import calculate_position_size
from alpaca_bot.strategy.breakout import (
    evaluate_breakout_signal,
    is_past_flatten_time,
    session_day,
)


class CycleIntentType(StrEnum):
    ENTRY = "entry"
    UPDATE_STOP = "update_stop"
    EXIT = "exit"


@dataclass(frozen=True)
class CycleIntent:
    intent_type: CycleIntentType
    symbol: str
    timestamp: datetime
    quantity: int | None = None
    stop_price: float | None = None
    limit_price: float | None = None
    initial_stop_price: float | None = None
    client_order_id: str | None = None
    reason: str | None = None
    signal_timestamp: datetime | None = None


@dataclass(frozen=True)
class CycleResult:
    as_of: datetime
    intents: list[CycleIntent] = field(default_factory=list)


def evaluate_cycle(
    *,
    settings: Settings,
    now: datetime,
    equity: float,
    intraday_bars_by_symbol: Mapping[str, Sequence[Bar]],
    daily_bars_by_symbol: Mapping[str, Sequence[Bar]],
    open_positions: Sequence[OpenPosition],
    working_order_symbols: set[str],
    traded_symbols_today: set[tuple[str, date]],
    entries_disabled: bool,
) -> CycleResult:
    intents: list[CycleIntent] = []
    open_position_symbols = {position.symbol for position in open_positions}

    for position in open_positions:
        bars = intraday_bars_by_symbol.get(position.symbol, ())
        if not bars:
            continue
        latest_bar = bars[-1]

        if is_past_flatten_time(latest_bar.timestamp, settings):
            intents.append(
                CycleIntent(
                    intent_type=CycleIntentType.EXIT,
                    symbol=position.symbol,
                    timestamp=latest_bar.timestamp,
                    reason="eod_flatten",
                )
            )
            continue

        if latest_bar.high >= position.entry_price + position.risk_per_share:
            new_stop = round(max(position.stop_price, latest_bar.low), 2)
            if new_stop > position.stop_price:
                intents.append(
                    CycleIntent(
                        intent_type=CycleIntentType.UPDATE_STOP,
                        symbol=position.symbol,
                        timestamp=latest_bar.timestamp,
                        stop_price=new_stop,
                    )
                )

    if not entries_disabled:
        available_slots = max(settings.max_open_positions - len(open_positions), 0)
        if available_slots > 0:
            entry_candidates: list[tuple[float, float, CycleIntent]] = []
            for symbol in settings.symbols:
                if symbol in open_position_symbols or symbol in working_order_symbols:
                    continue
                bars = intraday_bars_by_symbol.get(symbol, ())
                daily_bars = daily_bars_by_symbol.get(symbol, ())
                if not bars or not daily_bars:
                    continue
                latest_bar = bars[-1]
                if (symbol, session_day(latest_bar.timestamp, settings)) in traded_symbols_today:
                    continue

                signal = evaluate_breakout_signal(
                    symbol=symbol,
                    intraday_bars=bars,
                    signal_index=len(bars) - 1,
                    daily_bars=daily_bars,
                    settings=settings,
                )
                if signal is None:
                    continue

                quantity = calculate_position_size(
                    equity=equity,
                    entry_price=signal.stop_price,
                    stop_price=signal.initial_stop_price,
                    settings=settings,
                )
                if quantity < 1:
                    continue

                entry_candidates.append(
                    (
                        round((signal.signal_bar.close / signal.breakout_level) - 1, 6),
                        round(signal.relative_volume, 6),
                        CycleIntent(
                            intent_type=CycleIntentType.ENTRY,
                            symbol=symbol,
                            timestamp=signal.signal_bar.timestamp,
                            quantity=quantity,
                            stop_price=signal.stop_price,
                            limit_price=signal.limit_price,
                            initial_stop_price=signal.initial_stop_price,
                            client_order_id=_client_order_id(
                                settings=settings,
                                symbol=symbol,
                                signal_timestamp=signal.signal_bar.timestamp,
                            ),
                            signal_timestamp=signal.signal_bar.timestamp,
                        ),
                    )
                )

            entry_candidates.sort(
                key=lambda item: (-item[0], -item[1], item[2].symbol),
            )
            intents.extend(candidate for *_rank, candidate in entry_candidates[:available_slots])

    intents.sort(key=lambda intent: (intent.timestamp, intent.symbol, intent.intent_type.value))
    return CycleResult(as_of=now, intents=intents)


def _client_order_id(
    *,
    settings: Settings,
    symbol: str,
    signal_timestamp: datetime,
) -> str:
    return (
        f"{settings.strategy_version}:"
        f"{signal_timestamp.date().isoformat()}:"
        f"{symbol}:entry:{signal_timestamp.isoformat()}"
    )
