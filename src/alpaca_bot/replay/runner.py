from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import json
from pathlib import Path
from typing import Callable

from alpaca_bot.config import Settings
from alpaca_bot.core.engine import CycleIntentType, evaluate_cycle
from alpaca_bot.domain.enums import IntentType
from alpaca_bot.domain.models import (
    Bar,
    OpenPosition,
    ReplayEvent,
    ReplayResult,
    ReplayScenario,
    WorkingEntryOrder,
)
from alpaca_bot.replay.report import build_backtest_report
from alpaca_bot.risk.sizing import calculate_position_size
from alpaca_bot.strategy import StrategySignalEvaluator
from alpaca_bot.strategy.breakout import session_day


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
    def __init__(
        self,
        settings: Settings,
        signal_evaluator: StrategySignalEvaluator | None = None,
        strategy_name: str = "breakout",
    ):
        self.settings = settings
        self.signal_evaluator = signal_evaluator
        self.strategy_name = strategy_name

    @staticmethod
    def load_scenario(path: str | Path) -> ReplayScenario:
        p = Path(path)
        text = p.read_text()
        if p.suffix in (".yaml", ".yml"):
            import yaml
            payload = yaml.safe_load(text)
        else:
            payload = json.loads(text)
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
            # --- Simulation mechanics: fill or expire working entry order ---
            self._process_existing_order(bar=bar, state=state, events=events)

            # --- Simulation mechanics: stop-hit detection ---
            # This is purely a fill-simulation concern, not a strategy decision.
            if self._process_stop_hit(bar=bar, state=state, events=events):
                continue

            # --- Simulation mechanics: profit-target hit ---
            # Stop check runs before profit-target: if both trigger in the same bar,
            # stop takes priority (conservative).
            if self.settings.enable_profit_target:
                if self._process_profit_target_hit(bar=bar, state=state, events=events):
                    continue

            # --- Strategy decisions via evaluate_cycle() ---
            # Pass bars up to and including the current bar so that
            # signal_index == len(bars_slice) - 1 matches the engine contract.
            bars_slice = bars[: index + 1]
            intraday_by_symbol = {bar.symbol: bars_slice}
            daily_by_symbol = {bar.symbol: scenario.daily_bars}
            working_order_symbols: set[str] = (
                {state.working_order.symbol} if state.working_order is not None else set()
            )
            open_positions = [state.position] if state.position is not None else []

            cycle_result = evaluate_cycle(
                settings=self.settings,
                now=bar.timestamp,
                equity=state.equity,
                intraday_bars_by_symbol=intraday_by_symbol,
                daily_bars_by_symbol=daily_by_symbol,
                open_positions=open_positions,
                working_order_symbols=working_order_symbols,
                traded_symbols_today=state.traded_symbols,
                entries_disabled=False,
                signal_evaluator=self.signal_evaluator,
            )

            for intent in cycle_result.intents:
                if intent.intent_type == CycleIntentType.EXIT:
                    # EOD flatten decision from engine
                    self._handle_eod_exit(
                        bar=bar, state=state, events=events
                    )

                elif intent.intent_type == CycleIntentType.UPDATE_STOP:
                    # Trailing stop update decision from engine
                    self._handle_stop_update(
                        intent_stop=intent.stop_price,
                        bar=bar,
                        state=state,
                        events=events,
                    )

                elif intent.intent_type == CycleIntentType.ENTRY:
                    # Entry signal decision from engine — place working order
                    # for the NEXT bar (the execution bar).
                    next_index = index + 1
                    if next_index >= len(bars):
                        continue
                    if state.position is not None or state.working_order is not None:
                        continue
                    active_bar = bars[next_index]
                    state.working_order = WorkingEntryOrder(
                        symbol=intent.symbol,
                        signal_timestamp=intent.timestamp,
                        active_bar_timestamp=active_bar.timestamp,
                        stop_price=intent.stop_price,  # type: ignore[arg-type]
                        limit_price=intent.limit_price,  # type: ignore[arg-type]
                        initial_stop_price=intent.initial_stop_price,  # type: ignore[arg-type]
                        entry_level=0.0,  # entry_level not carried in CycleIntent
                        relative_volume=0.0,  # relative_volume not carried in CycleIntent
                    )
                    events.append(
                        ReplayEvent(
                            event_type=IntentType.ENTRY_ORDER_PLACED,
                            symbol=intent.symbol,
                            timestamp=bar.timestamp,
                            details={
                                "stop_price": intent.stop_price,
                                "limit_price": intent.limit_price,
                                "initial_stop_price": intent.initial_stop_price,
                                "relative_volume": 0.0,
                            },
                        )
                    )

        result = ReplayResult(
            scenario=scenario,
            events=events,
            final_position=state.position,
            traded_symbols=state.traded_symbols,
        )
        result.backtest_report = build_backtest_report(result, strategy_name=self.strategy_name)
        return result

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
            entry_level=order.entry_level,
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

    def _process_stop_hit(
        self,
        *,
        bar: Bar,
        state: ReplayState,
        events: list[ReplayEvent],
    ) -> bool:
        """Check if the current bar's low crosses the stop price.

        Returns True if a stop was hit (caller should skip remaining processing
        for this bar), False otherwise.
        """
        position = state.position
        if position is None:
            return False

        # Update highest_price for informational tracking
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
            state.equity += (exit_price - position.entry_price) * position.quantity
            state.traded_symbols.add(
                (position.symbol, session_day(bar.timestamp, self.settings))
            )
            state.position = None
            return True

        return False

    def _process_profit_target_hit(
        self,
        *,
        bar: Bar,
        state: ReplayState,
        events: list[ReplayEvent],
    ) -> bool:
        """Check if the current bar's high reaches the profit target price.

        Returns True if target was hit (caller should skip remaining processing
        for this bar), False otherwise.  Fill simulated at exact target_price.
        """
        position = state.position
        if position is None:
            return False

        target_price = round(
            position.entry_price + self.settings.profit_target_r * position.risk_per_share, 2
        )
        if bar.high < target_price:
            return False

        events.append(
            ReplayEvent(
                event_type=IntentType.PROFIT_TARGET_HIT,
                symbol=position.symbol,
                timestamp=bar.timestamp,
                details={"exit_price": round(target_price, 2)},
            )
        )
        state.equity += (target_price - position.entry_price) * position.quantity
        state.traded_symbols.add(
            (position.symbol, session_day(bar.timestamp, self.settings))
        )
        state.position = None
        return True

    def _handle_eod_exit(
        self,
        *,
        bar: Bar,
        state: ReplayState,
        events: list[ReplayEvent],
    ) -> None:
        position = state.position
        if position is None:
            return
        events.append(
            ReplayEvent(
                event_type=IntentType.EOD_EXIT,
                symbol=position.symbol,
                timestamp=bar.timestamp,
                details={"exit_price": round(bar.close, 2)},
            )
        )
        state.equity += (bar.close - position.entry_price) * position.quantity
        state.traded_symbols.add(
            (position.symbol, session_day(bar.timestamp, self.settings))
        )
        state.position = None

    def _handle_stop_update(
        self,
        *,
        intent_stop: float | None,
        bar: Bar,
        state: ReplayState,
        events: list[ReplayEvent],
    ) -> None:
        position = state.position
        if position is None or intent_stop is None:
            return
        if intent_stop > position.stop_price:
            position.stop_price = intent_stop
            position.trailing_active = True
            events.append(
                ReplayEvent(
                    event_type=IntentType.STOP_UPDATED,
                    symbol=position.symbol,
                    timestamp=bar.timestamp,
                    details={"stop_price": intent_stop},
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
