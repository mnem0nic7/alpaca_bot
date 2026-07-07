from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
import json
from pathlib import Path
from typing import Callable, Sequence

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
from alpaca_bot.replay.mechanics import (
    apply_slippage,
    simulate_buy_stop_limit_fill,
)
from alpaca_bot.replay.report import build_backtest_report
from alpaca_bot.risk.sizing import calculate_position_size
from alpaca_bot.strategy import StrategySignalEvaluator
from alpaca_bot.strategy.breakout import session_day
from alpaca_bot.strategy.market_context import compute_market_context


@dataclass
class ReplayState:
    equity: float
    working_order: WorkingEntryOrder | None = None
    position: OpenPosition | None = None
    traded_symbols: set[tuple[str, date]] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.traded_symbols is None:
            self.traded_symbols = set()


class _BarPrefix(Sequence[Bar]):
    def __init__(self, bars: list[Bar], length: int) -> None:
        self._bars = bars
        self._length = length

    def __len__(self) -> int:
        return self._length

    def __getitem__(self, index: int | slice) -> Bar | list[Bar]:
        if isinstance(index, slice):
            start, stop, step = index.indices(self._length)
            return [self._bars[i] for i in range(start, stop, step)]
        if index < 0:
            index += self._length
        if index < 0 or index >= self._length:
            raise IndexError(index)
        return self._bars[index]


class _KnownCleanBarPrefix(_BarPrefix):
    all_closes_positive = True


class ReplayRunner:
    def __init__(
        self,
        settings: Settings,
        signal_evaluator: StrategySignalEvaluator | None = None,
        strategy_name: str = "breakout",
        regime_daily_bars: Sequence[Bar] | None = None,
    ):
        self.settings = settings
        self.signal_evaluator = signal_evaluator
        self.strategy_name = strategy_name
        self.regime_daily_bars = (
            list(regime_daily_bars) if regime_daily_bars is not None else None
        )

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
            regime_daily_bars=(
                [Bar.from_dict(item) for item in payload["regime_daily_bars"]]
                if payload.get("regime_daily_bars")
                else None
            ),
            vix_daily_bars=(
                [Bar.from_dict(item) for item in payload["vix_daily_bars"]]
                if payload.get("vix_daily_bars")
                else None
            ),
            sector_daily_bars_by_etf=(
                {
                    str(etf).upper(): [Bar.from_dict(item) for item in bars]
                    for etf, bars in payload[
                        "sector_daily_bars_by_etf"
                    ].items()
                }
                if payload.get("sector_daily_bars_by_etf")
                else None
            ),
        )

    def _slipped(self, price: float, *, side: str) -> float:
        """Apply adverse slippage to a simulated fill price.

        Delegates to mechanics.apply_slippage so both runners share one formula.
        """
        return apply_slippage(price, side=side, bps=self.settings.replay_slippage_bps)

    def _last_entry_order_active_timestamp(
        self,
        bars: Sequence[Bar],
        first_active_index: int,
    ) -> datetime | None:
        last_active_index = min(
            len(bars) - 1,
            first_active_index + self.settings.entry_order_active_bars - 1,
        )
        first_active = bars[first_active_index]
        first_active_utc = (
            first_active.timestamp.replace(tzinfo=timezone.utc)
            if first_active.timestamp.tzinfo is None
            else first_active.timestamp.astimezone(timezone.utc)
        )
        first_active_local = first_active_utc.astimezone(self.settings.market_timezone)
        flatten_at = datetime.combine(
            first_active_local.date(),
            self.settings.flatten_time,
            tzinfo=self.settings.market_timezone,
        )
        for index in range(last_active_index, first_active_index - 1, -1):
            bar_utc = (
                bars[index].timestamp.replace(tzinfo=timezone.utc)
                if bars[index].timestamp.tzinfo is None
                else bars[index].timestamp.astimezone(timezone.utc)
            )
            if bar_utc.astimezone(self.settings.market_timezone) < flatten_at:
                return bars[index].timestamp
        return None

    def run(self, scenario: ReplayScenario) -> ReplayResult:
        bars = sorted(scenario.intraday_bars, key=lambda bar: bar.timestamp)
        sorted_daily = sorted(scenario.daily_bars, key=lambda bar: bar.timestamp)
        regime_source = (
            self.regime_daily_bars
            if self.regime_daily_bars is not None
            else scenario.regime_daily_bars
        )
        sorted_regime_daily = sorted(regime_source or (), key=lambda bar: bar.timestamp)
        sorted_vix_daily = sorted(
            scenario.vix_daily_bars or (), key=lambda bar: bar.timestamp
        )
        sorted_sector_daily_by_etf = {
            etf: sorted(bars, key=lambda bar: bar.timestamp)
            for etf, bars in (scenario.sector_daily_bars_by_etf or {}).items()
        }
        intraday_prefix_type = (
            _KnownCleanBarPrefix if all(bar.close > 0 for bar in bars) else _BarPrefix
        )
        daily_prefix_type = (
            _KnownCleanBarPrefix
            if all(bar.close > 0 for bar in sorted_daily)
            else _BarPrefix
        )
        regime_daily_prefix_type = (
            _KnownCleanBarPrefix
            if all(bar.close > 0 for bar in sorted_regime_daily)
            else _BarPrefix
        )
        state = ReplayState(equity=scenario.starting_equity)
        events: list[ReplayEvent] = []
        current_day: date | None = None
        daily_slice: Sequence[Bar] = []
        regime_slice: Sequence[Bar] | None = None
        market_context = None

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
            bars_slice = intraday_prefix_type(bars, index + 1)
            intraday_by_symbol = {bar.symbol: bars_slice}
            # Mirror live data shape: the supervisor fetches daily bars with
            # end = midnight ET of the session date, so the series the engine
            # sees on day D contains only bars from completed days (< D).
            day = session_day(bar.timestamp, self.settings)
            if day != current_day:
                current_day = day
                daily_prefix_length = 0
                for daily_bar in sorted_daily:
                    if (
                        daily_bar.timestamp.astimezone(self.settings.market_timezone).date()
                        >= day
                    ):
                        break
                    daily_prefix_length += 1
                daily_slice = daily_prefix_type(sorted_daily, daily_prefix_length)
                if sorted_regime_daily:
                    regime_prefix_length = 0
                    for daily_bar in sorted_regime_daily:
                        if (
                            daily_bar.timestamp.astimezone(
                                self.settings.market_timezone
                            ).date()
                            >= day
                        ):
                            break
                        regime_prefix_length += 1
                    regime_slice = regime_daily_prefix_type(
                        sorted_regime_daily, regime_prefix_length
                    )
                else:
                    regime_slice = None
                vix_slice = [
                    daily_bar
                    for daily_bar in sorted_vix_daily
                    if daily_bar.timestamp.astimezone(
                        self.settings.market_timezone
                    ).date()
                    < day
                ]
                sector_slices = {
                    etf: [
                        daily_bar
                        for daily_bar in sector_daily
                        if daily_bar.timestamp.astimezone(
                            self.settings.market_timezone
                        ).date()
                        < day
                    ]
                    for etf, sector_daily in sorted_sector_daily_by_etf.items()
                }
                if vix_slice or sector_slices:
                    market_context = compute_market_context(
                        as_of=bar.timestamp,
                        vix_bars=vix_slice,
                        sector_bars_by_etf=sector_slices,
                        settings=self.settings,
                    )
                else:
                    market_context = None
            daily_by_symbol = {bar.symbol: daily_slice}
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
                symbols=(scenario.symbol,),
                regime_bars=(
                    regime_slice if self.settings.enable_regime_filter else None
                ),
                market_context=market_context,
            )

            for intent in cycle_result.intents:
                if intent.intent_type == CycleIntentType.EXIT:
                    self._handle_eod_exit(
                        bar=bar,
                        state=state,
                        events=events,
                        reason=intent.reason or "eod_flatten",
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
                    expires_at = self._last_entry_order_active_timestamp(
                        bars,
                        next_index,
                    )
                    if expires_at is None:
                        continue
                    state.working_order = WorkingEntryOrder(
                        symbol=intent.symbol,
                        signal_timestamp=intent.timestamp,
                        active_bar_timestamp=active_bar.timestamp,
                        expires_at_timestamp=expires_at,
                        stop_price=intent.stop_price,  # type: ignore[arg-type]
                        limit_price=intent.limit_price,  # type: ignore[arg-type]
                        initial_stop_price=intent.initial_stop_price,  # type: ignore[arg-type]
                        entry_level=0.0,  # entry_level not carried in CycleIntent
                        relative_volume=0.0,  # relative_volume not carried in CycleIntent
                        quantity=intent.quantity,
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

        if bar.timestamp < order.active_bar_timestamp:
            return

        if bar.timestamp > order.last_active_bar_timestamp:
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

        fill_price = simulate_buy_stop_limit_fill(
            bar=bar,
            stop_price=order.stop_price,
            limit_price=order.limit_price,
        )
        if fill_price is None and bar.timestamp >= order.last_active_bar_timestamp:
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
        if fill_price is None:
            return

        # Adverse slippage on entry, capped at the limit (a stop-limit order
        # cannot legally fill above its limit price).
        fill_price = min(self._slipped(fill_price, side="buy"), order.limit_price)

        quantity = order.quantity
        if quantity is None:
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
            exit_price = self._slipped(min(position.stop_price, bar.open), side="sell")
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

        exit_price = self._slipped(target_price, side="sell")
        events.append(
            ReplayEvent(
                event_type=IntentType.PROFIT_TARGET_HIT,
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

    def _handle_eod_exit(
        self,
        *,
        bar: Bar,
        state: ReplayState,
        events: list[ReplayEvent],
        reason: str = "eod_flatten",
    ) -> None:
        position = state.position
        if position is None:
            return
        exit_price = self._slipped(bar.close, side="sell")
        events.append(
            ReplayEvent(
                event_type=IntentType.EOD_EXIT,
                symbol=position.symbol,
                timestamp=bar.timestamp,
                details={"exit_price": round(exit_price, 2), "reason": reason},
            )
        )
        state.equity += (exit_price - position.entry_price) * position.quantity
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
