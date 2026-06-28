# src/alpaca_bot/replay/portfolio.py
"""Cross-sectional / portfolio replay over many symbols sharing one equity pool.

The single-symbol ReplayRunner walks one ReplayScenario's bars and lets the pure
engine decide entries/exits for that lone symbol. Because each scenario carries
exactly one symbol, the engine's cross-sectional machinery — ranking entry
candidates by signal strength, capping at available slots, enforcing a portfolio
exposure cap — is a permanent no-op.

PortfolioReplayRunner feeds the SAME pure ``evaluate_cycle`` the full multi-symbol
mappings on each cycle against ONE shared equity pool, so the ranking/slot/exposure
logic finally exercises. The engine stays pure: all bookkeeping (lanes, fills,
equity) lives here in the harness.

This module builds the data scaffolding only: index bars by symbol, join a union
timeline across symbols, and produce per-symbol point-in-time daily slices. The
cycle loop that drives entries/exits is layered on in a later task.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime

from alpaca_bot.config import Settings
from alpaca_bot.core.engine import CycleIntentType, evaluate_cycle
from alpaca_bot.domain.models import (
    Bar,
    OpenPosition,
    ReplayScenario,
    WorkingEntryOrder,
)
from alpaca_bot.replay.mechanics import (
    entry_fill_price,
    eod_exit_price,
    profit_target_price,
    simulate_buy_stop_limit_fill,
    stop_exit_price,
)
from alpaca_bot.replay.report import ReplayTradeRecord
from alpaca_bot.risk.sizing import calculate_position_size
from alpaca_bot.strategy import STRATEGY_REGISTRY, StrategySignalEvaluator
from alpaca_bot.strategy.breakout import session_day


@dataclass
class _Lane:
    """Per-symbol replay state in the shared-equity portfolio run."""

    symbol: str
    intraday: list[Bar]
    daily: list[Bar]
    daily_all_closes_positive: bool
    cursor: int = 0
    working_order: WorkingEntryOrder | None = None
    position: OpenPosition | None = None


class _BarPrefix(Sequence[Bar]):
    """Read-only prefix view over a bar list.

    Portfolio replay calls the engine once per timestamp across many symbols.
    Copying each symbol's full intraday history before every call dominates the
    top-K audit. A Sequence view preserves the engine/evaluator contract while
    only materializing slices the evaluator explicitly asks for.
    """

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
    """Prefix view over bars whose closes are all positive."""

    all_closes_positive = True


class PortfolioReplayRunner:
    def __init__(
        self,
        settings: Settings,
        signal_evaluator: StrategySignalEvaluator | None = None,
        strategy_name: str = "breakout",
    ):
        self.settings = settings
        self.signal_evaluator = signal_evaluator
        self.strategy_name = strategy_name
        self._lanes: dict[str, _Lane] = {}
        self._bars_by_timestamp: dict[datetime, list[tuple[str, int]]] = {}
        self._daily_slice_cache: dict[tuple[str, date], Sequence[Bar]] = {}

    def _index_scenarios(self, scenarios: list[ReplayScenario]) -> None:
        self._lanes = {}
        self._bars_by_timestamp = {}
        self._daily_slice_cache = {}
        for sc in scenarios:
            if sc.symbol in self._lanes:
                raise ValueError(
                    "PortfolioReplayRunner requires one scenario per symbol; "
                    f"duplicate symbol: {sc.symbol}"
                )
            intraday = _sorted_bars(sc.intraday_bars)
            daily = _sorted_bars(sc.daily_bars)
            self._lanes[sc.symbol] = _Lane(
                symbol=sc.symbol,
                intraday=intraday,
                daily=daily,
                daily_all_closes_positive=all(b.close > 0 for b in daily),
            )
            for idx, bar in enumerate(intraday):
                self._bars_by_timestamp.setdefault(bar.timestamp, []).append((sc.symbol, idx))

    def _build_timeline(self, scenarios: list[ReplayScenario]) -> list[datetime]:
        if self._bars_by_timestamp:
            return sorted(self._bars_by_timestamp)
        stamps: set[datetime] = set()
        for sc in scenarios:
            for bar in sc.intraday_bars:
                stamps.add(bar.timestamp)
        return sorted(stamps)

    def _daily_slice_for(self, symbol: str, now: datetime) -> Sequence[Bar]:
        lane = self._lanes[symbol]
        day = session_day(now, self.settings)
        cache_key = (symbol, day)
        cached = self._daily_slice_cache.get(cache_key)
        if cached is not None:
            return cached
        tz = self.settings.market_timezone
        prefix_length = 0
        for bar in lane.daily:
            if bar.timestamp.astimezone(tz).date() >= day:
                break
            prefix_length += 1
        prefix_type = _KnownCleanBarPrefix if lane.daily_all_closes_positive else _BarPrefix
        daily_slice: Sequence[Bar] = prefix_type(lane.daily, prefix_length)
        self._daily_slice_cache[cache_key] = daily_slice
        return daily_slice

    # --- main loop -------------------------------------------------------

    def run(self, scenarios) -> list[ReplayTradeRecord]:
        self._index_scenarios(scenarios)
        timeline = self._build_timeline(scenarios)
        equity = float(getattr(scenarios[0], "starting_equity", 100000.0)) if scenarios else 100000.0

        trades: list[ReplayTradeRecord] = []
        traded_symbols: set[tuple[str, date]] = set()

        for now in timeline:
            fresh: list[str] = []
            for sym, idx in self._bars_by_timestamp.get(now, ()):
                lane = self._lanes[sym]
                lane.cursor = idx
                fresh.append(sym)

            # 1) Resolve fills/exits for fresh lanes (shared equity).
            for sym in fresh:
                lane = self._lanes[sym]
                bar = lane.intraday[lane.cursor]
                equity = self._resolve_order(lane, bar, equity)
                closed, equity = self._resolve_exits(lane, bar, equity, traded_symbols)
                trades.extend(closed)

            if not fresh:
                continue

            # 2) One cross-sectional engine call over fresh symbols.
            intraday_by_symbol = {
                s: _BarPrefix(self._lanes[s].intraday, self._lanes[s].cursor + 1)
                for s in fresh
            }
            daily_by_symbol = {s: self._daily_slice_for(s, now) for s in fresh}
            open_positions = [l.position for l in self._lanes.values() if l.position is not None]
            working_order_symbols = {
                s for s, l in self._lanes.items() if l.working_order is not None
            }

            cycle = evaluate_cycle(
                settings=self.settings,
                now=now,
                equity=equity,
                intraday_bars_by_symbol=intraday_by_symbol,
                daily_bars_by_symbol=daily_by_symbol,
                open_positions=open_positions,
                working_order_symbols=working_order_symbols,
                traded_symbols_today=traded_symbols,
                entries_disabled=False,
                signal_evaluator=self.signal_evaluator,
                symbols=tuple(sorted(fresh)),
            )

            # 3) Route intents to lanes.
            #
            # The engine sees *all* open positions (stale lanes included, line
            # building `open_positions` above) and its EOD-flatten path emits an
            # EXIT for any open position regardless of whether that symbol has a
            # bar this tick (engine.py EOD-flatten loop emits EXIT with no bars).
            # A stale lane (open position, no fresh bar this tick) must NOT be
            # acted on here: routing its EXIT to `lane.intraday[lane.cursor]`
            # would flatten it at a PAST bar's close — a mispriced phantom trade
            # that the single-symbol runner never produces (it manages a position
            # only on that symbol's own bars). Defer every stale lane's intents to
            # its own next fresh bar, where the engine re-emits EOD-flatten EXIT at
            # the correct bar. UPDATE_STOP/trailing/viability EXIT already require
            # bars inside the engine, so only the bars-free EOD-flatten EXIT can
            # leak to a stale lane — this guard closes that single path.
            fresh_set = set(fresh)
            for intent in cycle.intents:
                lane = self._lanes.get(intent.symbol)
                if lane is None:
                    continue
                if intent.symbol not in fresh_set:
                    continue
                if intent.intent_type == CycleIntentType.EXIT:
                    closed, equity = self._eod_exit(lane, lane.intraday[lane.cursor], equity, traded_symbols)
                    if closed is not None:
                        trades.append(closed)
                elif intent.intent_type == CycleIntentType.UPDATE_STOP:
                    if lane.position is not None and intent.stop_price is not None:
                        if intent.stop_price > lane.position.stop_price:
                            lane.position.stop_price = intent.stop_price
                            lane.position.trailing_active = True
                elif intent.intent_type == CycleIntentType.ENTRY:
                    self._place_order(lane, intent)

        return trades

    # --- lane mechanics (shared equity returned by value) ----------------

    def _place_order(self, lane: _Lane, intent) -> None:
        if lane.position is not None or lane.working_order is not None:
            return
        nxt = lane.cursor + 1
        if nxt >= len(lane.intraday):
            return
        lane.working_order = WorkingEntryOrder(
            symbol=intent.symbol,
            signal_timestamp=intent.timestamp,
            active_bar_timestamp=lane.intraday[nxt].timestamp,
            stop_price=intent.stop_price,
            limit_price=intent.limit_price,
            initial_stop_price=intent.initial_stop_price,
            entry_level=0.0,
            relative_volume=0.0,
            quantity=intent.quantity,
        )

    def _resolve_order(self, lane: _Lane, bar: Bar, equity: float) -> float:
        order = lane.working_order
        if order is None or bar.timestamp != order.active_bar_timestamp:
            return equity
        raw = simulate_buy_stop_limit_fill(
            bar=bar, stop_price=order.stop_price, limit_price=order.limit_price
        )
        if raw is None:
            lane.working_order = None
            return equity
        fill = entry_fill_price(
            raw_fill=raw, limit_price=order.limit_price,
            bps=self.settings.replay_slippage_bps,
        )
        qty = order.quantity
        if qty is None:
            qty = calculate_position_size(
                equity=equity, entry_price=fill,
                stop_price=order.initial_stop_price, settings=self.settings,
            )
        lane.position = OpenPosition(
            symbol=order.symbol, entry_timestamp=bar.timestamp, entry_price=fill,
            quantity=qty, entry_level=order.entry_level,
            initial_stop_price=order.initial_stop_price,
            stop_price=order.initial_stop_price, highest_price=fill,
        )
        lane.working_order = None
        return equity

    def _resolve_exits(self, lane, bar, equity, traded_symbols):
        """Stop-hit (priority) then profit-target. Returns (closed_trades, equity)."""
        closed: list[ReplayTradeRecord] = []
        pos = lane.position
        if pos is None:
            return closed, equity
        pos.highest_price = max(pos.highest_price, bar.high)

        if bar.low <= pos.stop_price:
            px = stop_exit_price(bar=bar, position=pos, bps=self.settings.replay_slippage_bps)
            equity += (px - pos.entry_price) * pos.quantity
            closed.append(self._record(pos, bar, px, "stop"))
            traded_symbols.add((pos.symbol, session_day(bar.timestamp, self.settings)))
            lane.position = None
            return closed, equity

        # Profit-target is checked ONLY after the stop (which returned above on a
        # hit), matching the stop-before-target ordering in runner.py.
        if self.settings.enable_profit_target and lane.position is not None:
            target = profit_target_price(position=pos, settings=self.settings)
            if bar.high >= target:
                from alpaca_bot.replay.mechanics import apply_slippage
                exit_px = apply_slippage(target, side="sell", bps=self.settings.replay_slippage_bps)
                equity += (exit_px - pos.entry_price) * pos.quantity
                closed.append(self._record(pos, bar, exit_px, "profit_target"))
                traded_symbols.add((pos.symbol, session_day(bar.timestamp, self.settings)))
                lane.position = None
        return closed, equity

    def _eod_exit(self, lane, bar, equity, traded_symbols):
        pos = lane.position
        if pos is None:
            return None, equity
        px = eod_exit_price(bar=bar, bps=self.settings.replay_slippage_bps)
        equity += (px - pos.entry_price) * pos.quantity
        rec = self._record(pos, bar, px, "eod")
        traded_symbols.add((pos.symbol, session_day(bar.timestamp, self.settings)))
        lane.position = None
        return rec, equity

    def _record(self, pos, bar, exit_price, reason) -> ReplayTradeRecord:
        # The audit objective scores ReplayTradeRecord.pnl. The single-symbol
        # baseline computes that pnl from the TRUNCATED int quantity
        # (report.py: `quantity = int(fill.details["quantity"]); pnl =
        # (exit_price - entry_price) * quantity`). Compute pnl from
        # int(pos.quantity) here so the portfolio runner's recorded pnl is
        # byte-identical to the baseline for whole-share quantities and
        # consistent (never float-vs-int divergent) for fractionable symbols —
        # the audit must be apples-to-apples. Equity bookkeeping above keeps the
        # float quantity, matching the single-symbol runner's float equity
        # updates; only the recorded pnl uses the int.
        qty = int(pos.quantity)
        pnl = (exit_price - pos.entry_price) * qty
        return ReplayTradeRecord(
            symbol=pos.symbol, entry_price=pos.entry_price, exit_price=exit_price,
            quantity=qty, entry_time=pos.entry_timestamp,
            exit_time=bar.timestamp, exit_reason=reason, pnl=pnl,
            return_pct=(exit_price - pos.entry_price) / pos.entry_price,
        )


def portfolio_pooled_trades(
    scenarios: Sequence[ReplayScenario], settings: Settings, strategy_name: str
) -> list[ReplayTradeRecord]:
    """PooledTradesFn-compatible adapter: ONE shared-equity portfolio sim over all
    scenarios. Drop-in for run_audit / run_break_even_sweep so the bootstrap CI
    objective scores portfolio top-K identically to the single-symbol baseline."""
    evaluator = STRATEGY_REGISTRY[strategy_name]
    runner = PortfolioReplayRunner(
        settings, signal_evaluator=evaluator, strategy_name=strategy_name
    )
    return runner.run(list(scenarios))


def _sorted_bars(bars: list[Bar]) -> list[Bar]:
    """Return bars in timestamp order, reusing an already-sorted list."""

    if all(bars[i - 1].timestamp <= bars[i].timestamp for i in range(1, len(bars))):
        return bars
    return sorted(bars, key=lambda b: b.timestamp)
