from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import json
from pathlib import Path

from alpaca_bot.config import Settings
from alpaca_bot.domain.enums import IntentType
from alpaca_bot.domain.models import (
    Bar,
    OpenPosition,
    ReplayEvent,
    ReplayResult,
    ReplayScenario,
    WorkingEntryOrder,
)
from alpaca_bot.risk.sizing import calculate_position_size
from alpaca_bot.strategy.breakout import (
    evaluate_breakout_signal,
    is_past_flatten_time,
    session_day,
)


@dataclass
class ReplayState:
    equity: float
    working_order: WorkingEntryOrder | None = None
    position: OpenPosition | None = None
    traded_symbols: set[tuple[str, date]] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.traded_symbols is None:
            self.traded_symbols = set()


class ReplayRunner:
    def __init__(self, settings: Settings):
        self.settings = settings

    @staticmethod
    def load_scenario(path: str | Path) -> ReplayScenario:
        payload = json.loads(Path(path).read_text())
        return ReplayScenario(
            name=payload["name"],
            symbol=payload["symbol"].upper(),
            starting_equity=float(payload.get("starting_equity", 100000.0)),
            daily_bars=[Bar.from_dict(item) for item in payload["daily_bars"]],
            intraday_bars=[Bar.from_dict(item) for item in payload["intraday_bars"]],
        )

    def run(self, scenario: ReplayScenario) -> ReplayResult:
        bars = sorted(scenario.intraday_bars, key=lambda bar: bar.timestamp)
        state = ReplayState(equity=scenario.starting_equity)
        events: list[ReplayEvent] = []

        for index, bar in enumerate(bars):
            self._process_existing_order(bar=bar, state=state, events=events)
            self._process_open_position(bar=bar, state=state, events=events)

            traded_key = (bar.symbol, session_day(bar.timestamp, self.settings))
            if traded_key in state.traded_symbols:
                continue
            if state.position is not None or state.working_order is not None:
                continue

            signal = evaluate_breakout_signal(
                symbol=bar.symbol,
                intraday_bars=bars,
                signal_index=index,
                daily_bars=scenario.daily_bars,
                settings=self.settings,
            )
            if signal is None:
                continue

            quantity = calculate_position_size(
                equity=state.equity,
                entry_price=signal.stop_price,
                stop_price=signal.initial_stop_price,
                settings=self.settings,
            )
            if quantity < 1:
                continue

            next_index = index + 1
            if next_index >= len(bars):
                continue

            active_bar = bars[next_index]
            state.working_order = WorkingEntryOrder(
                symbol=signal.symbol,
                signal_timestamp=bar.timestamp,
                active_bar_timestamp=active_bar.timestamp,
                stop_price=signal.stop_price,
                limit_price=signal.limit_price,
                initial_stop_price=signal.initial_stop_price,
                breakout_level=signal.breakout_level,
                relative_volume=signal.relative_volume,
            )
            events.append(
                ReplayEvent(
                    event_type=IntentType.ENTRY_ORDER_PLACED,
                    symbol=signal.symbol,
                    timestamp=bar.timestamp,
                    details={
                        "stop_price": signal.stop_price,
                        "limit_price": signal.limit_price,
                        "initial_stop_price": signal.initial_stop_price,
                        "relative_volume": round(signal.relative_volume, 4),
                    },
                )
            )

        return ReplayResult(
            scenario=scenario,
            events=events,
            final_position=state.position,
            traded_symbols=state.traded_symbols,
        )

    def _process_existing_order(
        self,
        *,
        bar: Bar,
        state: ReplayState,
        events: list[ReplayEvent],
    ) -> None:
        order = state.working_order
        if order is None:
            return

        if bar.timestamp != order.active_bar_timestamp:
            return

        fill_price = _simulate_buy_stop_limit_fill(
            bar=bar,
            stop_price=order.stop_price,
            limit_price=order.limit_price,
        )
        if fill_price is None:
            events.append(
                ReplayEvent(
                    event_type=IntentType.ENTRY_EXPIRED,
                    symbol=order.symbol,
                    timestamp=bar.timestamp,
                    details={
                        "stop_price": order.stop_price,
                        "limit_price": order.limit_price,
                    },
                )
            )
            state.working_order = None
            return

        quantity = calculate_position_size(
            equity=state.equity,
            entry_price=fill_price,
            stop_price=order.initial_stop_price,
            settings=self.settings,
        )
        state.position = OpenPosition(
            symbol=order.symbol,
            entry_timestamp=bar.timestamp,
            entry_price=fill_price,
            quantity=quantity,
            breakout_level=order.breakout_level,
            initial_stop_price=order.initial_stop_price,
            stop_price=order.initial_stop_price,
            highest_price=fill_price,
        )
        events.append(
            ReplayEvent(
                event_type=IntentType.ENTRY_FILLED,
                symbol=order.symbol,
                timestamp=bar.timestamp,
                details={
                    "entry_price": fill_price,
                    "initial_stop_price": order.initial_stop_price,
                    "quantity": quantity,
                },
            )
        )
        state.working_order = None

    def _process_open_position(
        self,
        *,
        bar: Bar,
        state: ReplayState,
        events: list[ReplayEvent],
    ) -> None:
        position = state.position
        if position is None:
            return

        position.highest_price = max(position.highest_price, bar.high)

        if bar.low <= position.stop_price:
            exit_price = min(position.stop_price, bar.open)
            events.append(
                ReplayEvent(
                    event_type=IntentType.STOP_HIT,
                    symbol=position.symbol,
                    timestamp=bar.timestamp,
                    details={"exit_price": round(exit_price, 2)},
                )
            )
            state.traded_symbols.add((position.symbol, session_day(bar.timestamp, self.settings)))
            state.position = None
            return

        if is_past_flatten_time(bar.timestamp, self.settings):
            events.append(
                ReplayEvent(
                    event_type=IntentType.EOD_EXIT,
                    symbol=position.symbol,
                    timestamp=bar.timestamp,
                    details={"exit_price": round(bar.close, 2)},
                )
            )
            state.traded_symbols.add((position.symbol, session_day(bar.timestamp, self.settings)))
            state.position = None
            return

        if not position.trailing_active and bar.high >= position.entry_price + position.risk_per_share:
            position.trailing_active = True

        if position.trailing_active:
            new_stop = round(max(position.stop_price, bar.low), 2)
            if new_stop > position.stop_price:
                position.stop_price = new_stop
                events.append(
                    ReplayEvent(
                        event_type=IntentType.STOP_UPDATED,
                        symbol=position.symbol,
                        timestamp=bar.timestamp,
                        details={"stop_price": new_stop},
                    )
                )

def _simulate_buy_stop_limit_fill(*, bar: Bar, stop_price: float, limit_price: float) -> float | None:
    if bar.open > limit_price:
        return None
    if bar.high < stop_price:
        return None

    fill_price = max(bar.open, stop_price)
    if fill_price > limit_price:
        return None
    return round(fill_price, 2)
