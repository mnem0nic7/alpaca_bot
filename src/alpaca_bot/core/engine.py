from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Mapping, Sequence

from alpaca_bot.config import Settings
from alpaca_bot.domain import Bar, OpenPosition
from alpaca_bot.risk import calculate_position_size
from alpaca_bot.strategy import StrategySignalEvaluator
from alpaca_bot.strategy.breakout import (
    evaluate_breakout_signal,
    is_past_flatten_time,
    session_day,
)

if TYPE_CHECKING:
    from alpaca_bot.storage import DailySessionState


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
    strategy_name: str = "breakout"


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
    flatten_all: bool = False,
    signal_evaluator: StrategySignalEvaluator | None = None,
    session_state: "DailySessionState | None" = None,
    strategy_name: str = "breakout",
    global_open_count: int | None = None,
) -> CycleResult:
    if signal_evaluator is None:
        signal_evaluator = evaluate_breakout_signal

    if flatten_all:
        intents = [
            CycleIntent(
                intent_type=CycleIntentType.EXIT,
                symbol=position.symbol,
                timestamp=now,
                reason="loss_limit_flatten",
                strategy_name=strategy_name,
            )
            for position in open_positions
        ]
        intents.sort(key=lambda intent: (intent.timestamp, intent.symbol, intent.intent_type.value))
        return CycleResult(as_of=now, intents=intents)

    flatten_complete = (
        session_state is not None and session_state.flatten_complete
    )

    intents: list[CycleIntent] = []
    open_position_symbols = {position.symbol for position in open_positions}
    past_flatten = is_past_flatten_time(now, settings)

    for position in open_positions:
        if past_flatten:
            if not flatten_complete:
                intents.append(
                    CycleIntent(
                        intent_type=CycleIntentType.EXIT,
                        symbol=position.symbol,
                        timestamp=now,
                        reason="eod_flatten",
                        strategy_name=strategy_name,
                    )
                )
            continue

        bars = intraday_bars_by_symbol.get(position.symbol, ())
        if not bars:
            continue
        latest_bar = bars[-1]

        if latest_bar.high >= position.entry_price + position.risk_per_share:
            new_stop = round(max(position.stop_price, position.entry_price, latest_bar.low), 2)
            if new_stop > position.stop_price:
                intents.append(
                    CycleIntent(
                        intent_type=CycleIntentType.UPDATE_STOP,
                        symbol=position.symbol,
                        timestamp=latest_bar.timestamp,
                        stop_price=new_stop,
                        strategy_name=strategy_name,
                    )
                )

    if not entries_disabled:
        if global_open_count is not None:
            # Caller has pre-computed the total occupied slots across ALL strategies.
            available_slots = max(settings.max_open_positions - global_open_count, 0)
        else:
            available_slots = max(
                settings.max_open_positions - len(open_positions) - len(working_order_symbols), 0
            )
        if available_slots > 0:
            current_exposure = (
                sum(p.entry_price * p.quantity for p in open_positions) / equity
                if equity > 0
                else 0.0
            )
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

                signal = signal_evaluator(
                    symbol=symbol,
                    intraday_bars=bars,
                    signal_index=len(bars) - 1,
                    daily_bars=daily_bars,
                    settings=settings,
                )
                if signal is None:
                    continue

                if signal.initial_stop_price >= signal.limit_price:
                    continue
                quantity = calculate_position_size(
                    equity=equity,
                    entry_price=signal.limit_price,
                    stop_price=signal.initial_stop_price,
                    settings=settings,
                )
                if quantity < 1:
                    continue

                entry_candidates.append(
                    (
                        round((signal.signal_bar.close / signal.entry_level) - 1, 6),
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
                                strategy_name=strategy_name,
                            ),
                            signal_timestamp=signal.signal_bar.timestamp,
                            strategy_name=strategy_name,
                        ),
                    )
                )

            entry_candidates.sort(
                key=lambda item: (-item[0], -item[1], item[2].symbol),
            )
            selected: list[CycleIntent] = []
            for *_rank, candidate in entry_candidates:
                if len(selected) >= available_slots:
                    break
                candidate_exposure = (
                    (candidate.limit_price or 0.0) * (candidate.quantity or 0) / equity
                    if equity > 0
                    else 0.0
                )
                if current_exposure + candidate_exposure > settings.max_portfolio_exposure_pct:
                    continue
                selected.append(candidate)
                current_exposure += candidate_exposure
            intents.extend(selected)

    intents.sort(key=lambda intent: (intent.timestamp, intent.symbol, intent.intent_type.value))
    return CycleResult(as_of=now, intents=intents)


def _client_order_id(
    *,
    settings: Settings,
    symbol: str,
    signal_timestamp: datetime,
    strategy_name: str = "breakout",
) -> str:
    return (
        f"{strategy_name}:"
        f"{settings.strategy_version}:"
        f"{signal_timestamp.date().isoformat()}:"
        f"{symbol}:entry:{signal_timestamp.isoformat()}"
    )
