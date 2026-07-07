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

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

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
    should_update_stop,
    simulate_buy_stop_limit_fill,
    stop_exit_price,
)
from alpaca_bot.replay.report import ReplayTradeRecord
from alpaca_bot.risk.sizing import calculate_position_size
from alpaca_bot.strategy import STRATEGY_REGISTRY, StrategySignalEvaluator
from alpaca_bot.strategy.breakout import session_day
from alpaca_bot.strategy.market_context import compute_market_context


@dataclass
class _Lane:
    """Per-symbol replay state in the shared-equity portfolio run."""

    symbol: str
    intraday: list[Bar]
    daily: list[Bar]
    daily_all_closes_positive: bool
    cursor: int = -1
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
        self._daily_slice_cache: dict[tuple[str, date], Sequence[Bar]] = {}
        self._regime_daily: list[Bar] = []
        self._regime_daily_all_closes_positive = True
        self._regime_slice_cache: dict[date, Sequence[Bar]] = {}
        self._vix_daily: list[Bar] = []
        self._sector_daily_by_etf: dict[str, list[Bar]] = {}
        self._market_context_cache: dict[date, object] = {}

    def _index_scenarios(self, scenarios: list[ReplayScenario]) -> None:
        self._lanes = {}
        self._daily_slice_cache = {}
        self._regime_daily = []
        self._regime_daily_all_closes_positive = True
        self._regime_slice_cache = {}
        self._vix_daily = []
        self._sector_daily_by_etf = {}
        self._market_context_cache = {}
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
        regime_symbol = self.settings.regime_symbol.upper()
        if regime_symbol in self._lanes:
            self._regime_daily = self._lanes[regime_symbol].daily
        else:
            for sc in scenarios:
                if sc.regime_daily_bars:
                    self._regime_daily = _sorted_bars(sc.regime_daily_bars)
                    break
        self._regime_daily_all_closes_positive = all(
            b.close > 0 for b in self._regime_daily
        )
        vix_symbol = self.settings.vix_proxy_symbol.upper()
        if vix_symbol in self._lanes:
            self._vix_daily = self._lanes[vix_symbol].daily
        else:
            for sc in scenarios:
                if sc.vix_daily_bars:
                    self._vix_daily = _sorted_bars(sc.vix_daily_bars)
                    break
        for etf in self.settings.sector_etf_symbols:
            etf_name = etf.upper()
            if etf_name in self._lanes:
                self._sector_daily_by_etf[etf_name] = self._lanes[etf_name].daily
        for sc in scenarios:
            for etf, bars in (sc.sector_daily_bars_by_etf or {}).items():
                etf_name = etf.upper()
                self._sector_daily_by_etf.setdefault(etf_name, _sorted_bars(bars))

    def _build_timeline(self, scenarios: list[ReplayScenario]) -> list[datetime]:
        stamps: set[datetime] = set()
        source_bars = (
            (lane.intraday for lane in self._lanes.values())
            if self._lanes
            else (sc.intraday_bars for sc in scenarios)
        )
        for bars in source_bars:
            for bar in bars:
                stamps.add(self._bar_close_time(bar))
        return sorted(stamps)

    def _bar_close_time(self, bar: Bar) -> datetime:
        # Alpaca intraday bars are start-stamped. The live supervisor only
        # evaluates a bar after timestamp + timeframe has elapsed.
        return bar.timestamp + timedelta(minutes=self.settings.entry_timeframe_minutes)

    def _last_entry_order_active_timestamp(
        self,
        lane: _Lane,
        first_active_index: int,
    ) -> datetime | None:
        last_active_index = min(
            len(lane.intraday) - 1,
            first_active_index + self.settings.entry_order_active_bars - 1,
        )
        first_active = lane.intraday[first_active_index]
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
                lane.intraday[index].timestamp.replace(tzinfo=timezone.utc)
                if lane.intraday[index].timestamp.tzinfo is None
                else lane.intraday[index].timestamp.astimezone(timezone.utc)
            )
            if bar_utc.astimezone(self.settings.market_timezone) < flatten_at:
                return lane.intraday[index].timestamp
        return None

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

    def _regime_slice_for(self, now: datetime) -> Sequence[Bar] | None:
        if not self._regime_daily:
            return None
        day = session_day(now, self.settings)
        cached = self._regime_slice_cache.get(day)
        if cached is not None:
            return cached
        tz = self.settings.market_timezone
        prefix_length = 0
        for bar in self._regime_daily:
            if bar.timestamp.astimezone(tz).date() >= day:
                break
            prefix_length += 1
        prefix_type = (
            _KnownCleanBarPrefix
            if self._regime_daily_all_closes_positive
            else _BarPrefix
        )
        regime_slice: Sequence[Bar] = prefix_type(self._regime_daily, prefix_length)
        self._regime_slice_cache[day] = regime_slice
        return regime_slice

    def _daily_context_slice_for(self, bars: list[Bar], now: datetime) -> list[Bar]:
        day = session_day(now, self.settings)
        tz = self.settings.market_timezone
        prefix_length = 0
        for bar in bars:
            if bar.timestamp.astimezone(tz).date() >= day:
                break
            prefix_length += 1
        return bars[:prefix_length]

    def _market_context_for(self, now: datetime):
        if not self._vix_daily and not self._sector_daily_by_etf:
            return None
        day = session_day(now, self.settings)
        cached = self._market_context_cache.get(day)
        if cached is not None:
            return cached
        context = compute_market_context(
            as_of=now,
            vix_bars=self._daily_context_slice_for(self._vix_daily, now),
            sector_bars_by_etf={
                etf: self._daily_context_slice_for(bars, now)
                for etf, bars in self._sector_daily_by_etf.items()
            },
            settings=self.settings,
        )
        self._market_context_cache[day] = context
        return context

    # --- main loop -------------------------------------------------------

    def run(
        self,
        scenarios,
        *,
        on_progress: Callable[[str], None] | None = None,
        progress_label: str | None = None,
    ) -> list[ReplayTradeRecord]:
        return self._run_strategy_sequence(
            scenarios,
            ((self.strategy_name, self.signal_evaluator),),
            on_progress=on_progress,
            progress_label=progress_label,
        )

    def _run_strategy_sequence(
        self,
        scenarios,
        strategy_sequence: Sequence[tuple[str, StrategySignalEvaluator | None]],
        *,
        strategy_equity_scales: Mapping[str, float] | None = None,
        on_progress: Callable[[str], None] | None = None,
        progress_label: str | None = None,
    ) -> list[ReplayTradeRecord]:
        self._index_scenarios(scenarios)
        timeline = self._build_timeline(scenarios)
        equity = float(getattr(scenarios[0], "starting_equity", 100000.0)) if scenarios else 100000.0

        trades: list[ReplayTradeRecord] = []
        traded_symbols_by_strategy: dict[str, set[tuple[str, date]]] = {
            strategy_name: set()
            for strategy_name, _evaluator in strategy_sequence
        }
        progress_every = max(1, len(timeline) // 20) if on_progress else 0
        label = progress_label or "+".join(
            strategy_name for strategy_name, _evaluator in strategy_sequence
        )
        equity_scales = dict(strategy_equity_scales or {})

        for timeline_index, now in enumerate(timeline, start=1):
            fresh: list[str] = []
            for sym, lane in self._lanes.items():
                nxt = lane.cursor + 1
                if (
                    nxt < len(lane.intraday)
                    and self._bar_close_time(lane.intraday[nxt]) == now
                ):
                    lane.cursor = nxt
                    fresh.append(sym)

            # 1) Resolve fills/exits for fresh lanes (shared equity).
            for sym in fresh:
                lane = self._lanes[sym]
                bar = lane.intraday[lane.cursor]
                equity = self._resolve_order(
                    lane,
                    bar,
                    equity,
                    traded_symbols_by_strategy.setdefault(
                        lane.working_order.strategy_name
                        if lane.working_order is not None
                        else self.strategy_name,
                        set(),
                    ),
                )
                trade_set = (
                    traded_symbols_by_strategy.setdefault(
                        lane.position.strategy_name, set()
                    )
                    if lane.position is not None
                    else traded_symbols_by_strategy.setdefault(self.strategy_name, set())
                )
                closed, equity = self._resolve_exits(lane, bar, equity, trade_set)
                trades.extend(closed)

            if not fresh:
                continue

            # 2) One cross-sectional engine call over all symbols that have a
            # completed bar. The live supervisor evaluates the whole active
            # watchlist each cycle using each symbol's most recent completed
            # bar; restricting the replay candidate pool to symbols with a bar
            # at this exact timestamp changes top-K selection on sparse data.
            eligible = [
                s for s, lane in self._lanes.items()
                if lane.cursor >= 0
            ]
            intraday_by_symbol = {
                s: _BarPrefix(self._lanes[s].intraday, self._lanes[s].cursor + 1)
                for s in eligible
            }
            daily_by_symbol = {s: self._daily_slice_for(s, now) for s in eligible}
            fresh_set = set(fresh)
            for strategy_name, evaluator in strategy_sequence:
                open_positions = [
                    l.position
                    for l in self._lanes.values()
                    if l.position is not None
                ]
                strategy_positions = [
                    p for p in open_positions
                    if p.strategy_name == strategy_name
                ]
                working_order_symbols = {
                    s for s, l in self._lanes.items() if l.working_order is not None
                }
                global_position_symbols = {p.symbol for p in open_positions}
                strategy_position_symbols = {p.symbol for p in strategy_positions}
                strategy_working_symbols = set(working_order_symbols)
                strategy_working_symbols |= (
                    global_position_symbols - strategy_position_symbols
                )
                global_occupied_slots = len(
                    global_position_symbols | working_order_symbols
                )

                cycle = evaluate_cycle(
                    settings=self.settings,
                    now=now,
                    equity=equity * equity_scales.get(strategy_name, 1.0),
                    intraday_bars_by_symbol=intraday_by_symbol,
                    daily_bars_by_symbol=daily_by_symbol,
                    open_positions=strategy_positions,
                    working_order_symbols=strategy_working_symbols,
                    traded_symbols_today=traded_symbols_by_strategy.setdefault(
                        strategy_name, set()
                    ),
                    entries_disabled=False,
                    signal_evaluator=evaluator,
                    strategy_name=strategy_name,
                    global_open_count=global_occupied_slots,
                    symbols=tuple(sorted(eligible)),
                    regime_bars=(
                        self._regime_slice_for(now)
                        if self.settings.enable_regime_filter
                        else None
                    ),
                    market_context=(
                        self._market_context_for(now)
                        if (
                            self.settings.enable_vix_filter
                            or self.settings.enable_sector_filter
                        )
                        else None
                    ),
                )

                # Route intents to lanes. The stale-lane guard mirrors the
                # single-strategy path: bars-free EOD flatten intents wait for
                # that symbol's own next fresh bar, where pricing is correct.
                for intent in cycle.intents:
                    lane = self._lanes.get(intent.symbol)
                    if lane is None:
                        continue
                    if intent.intent_type == CycleIntentType.EXIT:
                        if intent.symbol not in fresh_set:
                            continue
                        closed, equity = self._eod_exit(
                            lane,
                            lane.intraday[lane.cursor],
                            equity,
                            traded_symbols_by_strategy.setdefault(
                                strategy_name, set()
                            ),
                            reason=intent.reason or "eod_flatten",
                        )
                        if closed is not None:
                            trades.append(closed)
                    elif intent.intent_type == CycleIntentType.UPDATE_STOP:
                        if intent.symbol not in fresh_set:
                            continue
                        if lane.position is not None and intent.stop_price is not None:
                            if should_update_stop(
                                position=lane.position,
                                candidate_stop=intent.stop_price,
                            ):
                                lane.position.stop_price = intent.stop_price
                                lane.position.trailing_active = True
                    elif intent.intent_type == CycleIntentType.ENTRY:
                        self._place_order(lane, intent)

            if (
                on_progress is not None
                and (timeline_index == len(timeline) or timeline_index % progress_every == 0)
            ):
                pct = timeline_index / len(timeline) * 100 if timeline else 100.0
                on_progress(
                    f"{label}: replay {pct:.0f}% "
                    f"({timeline_index}/{len(timeline)} timestamps, trades={len(trades)})"
                )

        return trades

    # --- lane mechanics (shared equity returned by value) ----------------

    def _place_order(self, lane: _Lane, intent) -> None:
        if lane.position is not None or lane.working_order is not None:
            return
        nxt = lane.cursor + 1
        if nxt >= len(lane.intraday):
            return
        expires_at = self._last_entry_order_active_timestamp(lane, nxt)
        if expires_at is None:
            return
        lane.working_order = WorkingEntryOrder(
            symbol=intent.symbol,
            signal_timestamp=intent.timestamp,
            active_bar_timestamp=lane.intraday[nxt].timestamp,
            expires_at_timestamp=expires_at,
            stop_price=intent.stop_price,
            limit_price=intent.limit_price,
            initial_stop_price=intent.initial_stop_price,
            entry_level=0.0,
            relative_volume=0.0,
            quantity=intent.quantity,
            strategy_name=intent.strategy_name or self.strategy_name,
        )

    def _resolve_order(
        self,
        lane: _Lane,
        bar: Bar,
        equity: float,
        traded_symbols: set[tuple[str, date]],
    ) -> float:
        order = lane.working_order
        if order is None or bar.timestamp < order.active_bar_timestamp:
            return equity
        if bar.timestamp > order.last_active_bar_timestamp:
            traded_symbols.add(
                (order.symbol, session_day(order.signal_timestamp, self.settings))
            )
            lane.working_order = None
            return equity
        raw = simulate_buy_stop_limit_fill(
            bar=bar, stop_price=order.stop_price, limit_price=order.limit_price
        )
        if raw is None and bar.timestamp >= order.last_active_bar_timestamp:
            traded_symbols.add(
                (order.symbol, session_day(order.signal_timestamp, self.settings))
            )
            lane.working_order = None
            return equity
        if raw is None:
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
            strategy_name=order.strategy_name,
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

    def _eod_exit(self, lane, bar, equity, traded_symbols, *, reason="eod_flatten"):
        pos = lane.position
        if pos is None:
            return None, equity
        px = eod_exit_price(bar=bar, bps=self.settings.replay_slippage_bps)
        equity += (px - pos.entry_price) * pos.quantity
        exit_reason = "eod" if reason == "eod_flatten" else reason
        rec = self._record(pos, bar, px, exit_reason)
        traded_symbols.add((pos.symbol, session_day(bar.timestamp, self.settings)))
        lane.position = None
        return rec, equity

    def _record(self, pos, bar, exit_price, reason) -> ReplayTradeRecord:
        # Paper trading can fill fractional quantities. Score replay P&L from
        # the same float quantity used for equity bookkeeping so audits and
        # proof-horizon checks match live paper proof semantics.
        qty = float(pos.quantity)
        pnl = (exit_price - pos.entry_price) * qty
        return ReplayTradeRecord(
            symbol=pos.symbol, entry_price=pos.entry_price, exit_price=exit_price,
            quantity=qty, entry_time=pos.entry_timestamp,
            exit_time=bar.timestamp, exit_reason=reason, pnl=pnl,
            return_pct=(exit_price - pos.entry_price) / pos.entry_price,
        )


class PortfolioBasketReplayRunner(PortfolioReplayRunner):
    def __init__(
        self,
        settings: Settings,
        strategies: Sequence[tuple[str, StrategySignalEvaluator]],
        *,
        strategy_equity_scales: Mapping[str, float] | None = None,
    ):
        if not strategies:
            raise ValueError("PortfolioBasketReplayRunner requires at least one strategy")
        first_name, first_evaluator = strategies[0]
        super().__init__(
            settings,
            signal_evaluator=first_evaluator,
            strategy_name=first_name,
        )
        self.strategies = tuple(strategies)
        self.strategy_equity_scales = dict(strategy_equity_scales or {})

    def run(
        self,
        scenarios,
        *,
        on_progress: Callable[[str], None] | None = None,
        progress_label: str | None = None,
    ) -> list[ReplayTradeRecord]:
        return self._run_strategy_sequence(
            scenarios,
            self.strategies,
            strategy_equity_scales=self.strategy_equity_scales,
            on_progress=on_progress,
            progress_label=progress_label,
        )


def portfolio_pooled_trades(
    scenarios: Sequence[ReplayScenario],
    settings: Settings,
    strategy_name: str,
    *,
    on_progress: Callable[[str], None] | None = None,
) -> list[ReplayTradeRecord]:
    """PooledTradesFn-compatible adapter: ONE shared-equity portfolio sim over all
    scenarios. Drop-in for run_audit / run_break_even_sweep so the bootstrap CI
    objective scores portfolio top-K identically to the single-symbol baseline."""
    evaluator = STRATEGY_REGISTRY[strategy_name]
    runner = PortfolioReplayRunner(
        settings, signal_evaluator=evaluator, strategy_name=strategy_name
    )
    return runner.run(
        list(scenarios),
        on_progress=on_progress,
        progress_label=f"{strategy_name} {settings.replay_slippage_bps:g}bps",
    )


def portfolio_basket_pooled_trades(
    scenarios: Sequence[ReplayScenario],
    settings: Settings,
    strategy_names: Sequence[str],
    *,
    strategy_equity_scales: Mapping[str, float] | None = None,
    on_progress: Callable[[str], None] | None = None,
) -> list[ReplayTradeRecord]:
    """Replay several enabled strategies against one shared-equity portfolio."""
    if not strategy_names:
        raise ValueError("portfolio basket requires at least one strategy")
    strategies: list[tuple[str, StrategySignalEvaluator]] = []
    for name in strategy_names:
        try:
            evaluator = STRATEGY_REGISTRY[name]
        except KeyError as exc:
            raise ValueError(f"unknown strategy for portfolio basket: {name}") from exc
        strategies.append((name, evaluator))
    runner = PortfolioBasketReplayRunner(
        settings,
        strategies,
        strategy_equity_scales=strategy_equity_scales,
    )
    label = "+".join(strategy_names)
    return runner.run(
        list(scenarios),
        on_progress=on_progress,
        progress_label=f"{label} {settings.replay_slippage_bps:g}bps",
    )


def _sorted_bars(bars: list[Bar]) -> list[Bar]:
    """Return bars in timestamp order, reusing an already-sorted list."""

    if all(bars[i - 1].timestamp <= bars[i].timestamp for i in range(1, len(bars))):
        return bars
    return sorted(bars, key=lambda b: b.timestamp)
