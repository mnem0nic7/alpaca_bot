# Cross-sectional / portfolio top-K replay — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a portfolio replay that joins all per-symbol scenarios on a shared date axis, feeds the unchanged pure `evaluate_cycle` the full symbol set against one shared equity pool, and scores the result with the existing audit objective — so cross-sectional top-K selectivity becomes measurable.

**Architecture:** The cross-sectional ranking, K-cap, and exposure cap already live in `evaluate_cycle` ([engine.py:1020-1035](../../src/alpaca_bot/core/engine.py#L1020-L1035)); the single-symbol runner just never exercises them (passes `symbols=(scenario.symbol,)`, per-symbol equity). We add (1) shared slippage/exit-price primitives extracted from `ReplayRunner`, (2) a `PortfolioReplayRunner` driving a union-timeline loop with per-symbol lanes against one shared equity pool, (3) a `_portfolio_pooled_trades` adapter matching `PooledTradesFn` so `run_audit`/`run_break_even_sweep` score it unchanged, (4) a CLI subcommand, (5) an ops run + report. `evaluate_cycle` is not modified.

**Tech Stack:** Python 3, dataclasses, pytest with fake callables / in-memory scenarios (no mocks). Spec: `docs/superpowers/specs/2026-06-16-cross-sectional-topk-replay-design.md`.

**Baseline:** `pytest` is green before starting (record the count). Every commit runs full `pytest` first. Commit trailer on every commit:
`Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.

---

## File Structure

- Create: `src/alpaca_bot/replay/mechanics.py` — stateless fill / slippage / exit-price primitives shared by both runners.
- Modify: `src/alpaca_bot/replay/runner.py` — delegate `_slipped` and the fill simulator to `mechanics.py` (behavior-preserving).
- Create: `src/alpaca_bot/replay/portfolio.py` — `PortfolioReplayRunner` + `_portfolio_pooled_trades`.
- Modify: `src/alpaca_bot/replay/cli.py` — `portfolio-audit` subcommand.
- Create: `tests/unit/test_replay_mechanics.py`
- Create: `tests/unit/test_portfolio_runner.py`
- Create: `tests/unit/test_portfolio_pooled_trades.py`
- Create: `tests/unit/test_portfolio_cli.py`
- Create (ops, Task 6): `docs/strategy-audit/2026-06-16-cross-sectional-topk.md`

---

## Task 0: Establish baseline

- [ ] **Step 1: Run the full suite and record the count**

Run: `pytest -q`
Expected: all pass. Record the number (e.g. "N passed") — every later task must keep this green.

---

## Task 1: Extract shared fill / slippage / exit-price primitives

**Files:**
- Create: `src/alpaca_bot/replay/mechanics.py`
- Modify: `src/alpaca_bot/replay/runner.py:66-76` (`_slipped`), `:210-214` and `:389-398` (fill simulator import)
- Test: `tests/unit/test_replay_mechanics.py`

Rationale: the slippage formula and the stop-limit fill simulator are the numerically-critical pieces. Extracting them to one module guarantees the portfolio runner computes identical fills/exits. Position/equity bookkeeping is NOT extracted (it differs: shared vs per-symbol equity).

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_replay_mechanics.py
from datetime import datetime, timezone

from alpaca_bot.domain.models import Bar
from alpaca_bot.replay.mechanics import (
    apply_slippage,
    simulate_buy_stop_limit_fill,
    entry_fill_price,
    eod_exit_price,
)


def _bar(o, h, l, c):
    return Bar(
        symbol="AAA",
        timestamp=datetime(2026, 1, 2, 14, 30, tzinfo=timezone.utc),
        open=o, high=h, low=l, close=c, volume=1000,
    )


def test_apply_slippage_buy_raises_sell_lowers():
    assert apply_slippage(100.0, side="buy", bps=10.0) == 100.1
    assert apply_slippage(100.0, side="sell", bps=10.0) == 99.9


def test_apply_slippage_zero_is_identity():
    assert apply_slippage(100.0, side="buy", bps=0.0) == 100.0


def test_simulate_buy_stop_limit_fill_matches_existing_rules():
    # Open above limit -> no fill
    assert simulate_buy_stop_limit_fill(bar=_bar(101, 102, 100, 101), stop_price=100.5, limit_price=100.5) is None
    # High below stop -> no fill
    assert simulate_buy_stop_limit_fill(bar=_bar(99, 100, 98, 99.5), stop_price=100.5, limit_price=101.0) is None
    # Normal fill at max(open, stop)
    assert simulate_buy_stop_limit_fill(bar=_bar(99.8, 101, 99, 100.5), stop_price=100.0, limit_price=101.0) == 100.0


def test_entry_fill_price_capped_at_limit():
    # raw fill slipped up, but capped at limit
    assert entry_fill_price(raw_fill=100.0, limit_price=100.05, bps=10.0) == 100.05
    # slipped fill below limit passes through
    assert entry_fill_price(raw_fill=100.0, limit_price=101.0, bps=10.0) == 100.1


def test_eod_exit_price_applies_sell_slippage():
    assert eod_exit_price(bar=_bar(100, 101, 99, 100.0), bps=10.0) == 99.9
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `pytest tests/unit/test_replay_mechanics.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'alpaca_bot.replay.mechanics'`.

- [ ] **Step 3: Create the module**

```python
# src/alpaca_bot/replay/mechanics.py
"""Stateless fill / slippage / exit-price primitives.

Shared by ReplayRunner (single-symbol) and PortfolioReplayRunner so that the
numerically-critical fill and slippage math is provably identical across both.
Position and equity bookkeeping is intentionally NOT here — it differs between
the per-symbol runner and the shared-equity portfolio runner.
"""

from __future__ import annotations

from alpaca_bot.config import Settings
from alpaca_bot.domain.models import Bar, OpenPosition


def apply_slippage(price: float, *, side: str, bps: float) -> float:
    """Adverse slippage: buys fill higher, sells fill lower, by bps per side."""
    if bps <= 0.0:
        return price
    factor = 1.0 + bps / 10_000.0 if side == "buy" else 1.0 - bps / 10_000.0
    return round(price * factor, 4)


def simulate_buy_stop_limit_fill(
    *, bar: Bar, stop_price: float, limit_price: float
) -> float | None:
    """Raw (pre-slippage) stop-limit buy fill price, or None if no fill."""
    if bar.open > limit_price:
        return None
    if bar.high < stop_price:
        return None
    fill_price = max(bar.open, stop_price)
    if fill_price > limit_price:
        return None
    return round(fill_price, 2)


def entry_fill_price(*, raw_fill: float, limit_price: float, bps: float) -> float:
    """Slipped entry fill, capped at the limit (a stop-limit cannot fill above limit)."""
    return min(apply_slippage(raw_fill, side="buy", bps=bps), limit_price)


def stop_exit_price(*, bar: Bar, position: OpenPosition, bps: float) -> float:
    """Slipped stop-hit exit: fills at the worse of stop and the bar open."""
    return apply_slippage(min(position.stop_price, bar.open), side="sell", bps=bps)


def profit_target_price(*, position: OpenPosition, settings: Settings) -> float:
    return round(
        position.entry_price + settings.profit_target_r * position.risk_per_share, 2
    )


def eod_exit_price(*, bar: Bar, bps: float) -> float:
    return apply_slippage(bar.close, side="sell", bps=bps)
```

- [ ] **Step 4: Run the new test to confirm it passes**

Run: `pytest tests/unit/test_replay_mechanics.py -q`
Expected: PASS.

- [ ] **Step 5: Delegate ReplayRunner to the primitives (behavior-preserving)**

In `src/alpaca_bot/replay/runner.py`, add to the imports near line 20:

```python
from alpaca_bot.replay.mechanics import (
    apply_slippage,
    simulate_buy_stop_limit_fill,
)
```

Replace `_slipped` (lines 66-76) body with a delegation:

```python
    def _slipped(self, price: float, *, side: str) -> float:
        """Apply adverse slippage to a simulated fill price.

        Delegates to mechanics.apply_slippage so both runners share one formula.
        """
        return apply_slippage(price, side=side, bps=self.settings.replay_slippage_bps)
```

Replace the call site at line 210-214 (`fill_price = _simulate_buy_stop_limit_fill(...)`) so it calls the imported name:

```python
        fill_price = simulate_buy_stop_limit_fill(
            bar=bar,
            stop_price=order.stop_price,
            limit_price=order.limit_price,
        )
```

Delete the now-duplicated module-level `_simulate_buy_stop_limit_fill` function at the bottom of `runner.py` (lines 389-398).

- [ ] **Step 6: Run the full suite to confirm no regression**

Run: `pytest -q`
Expected: same count as Task 0 baseline, all pass. The single-symbol runner now delegates to the shared primitives with identical results.

- [ ] **Step 7: Commit**

```bash
git add src/alpaca_bot/replay/mechanics.py src/alpaca_bot/replay/runner.py tests/unit/test_replay_mechanics.py
git commit -m "refactor: extract shared replay fill/slippage primitives to mechanics.py

Behavior-preserving. ReplayRunner._slipped and the stop-limit fill simulator now
delegate to alpaca_bot.replay.mechanics so the upcoming PortfolioReplayRunner
computes identical fills/exits.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## Task 2: PortfolioReplayRunner — scenario join, union timeline, point-in-time slicing

**Files:**
- Create: `src/alpaca_bot/replay/portfolio.py`
- Test: `tests/unit/test_portfolio_runner.py`

This task builds the data scaffolding only (no entries yet): load N scenarios, index bars by symbol, build the union timeline, and produce per-symbol point-in-time slices with monotonic cursors. The cycle loop comes in Task 3.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_portfolio_runner.py
from datetime import datetime, timezone

from alpaca_bot.config import Settings
from alpaca_bot.domain.models import Bar, ReplayScenario
from alpaca_bot.replay.portfolio import PortfolioReplayRunner

ENV = {
    "TRADING_MODE": "paper",
    "ENABLE_LIVE_TRADING": "false",
    "STRATEGY_VERSION": "v1-breakout",
    "DATABASE_URL": "postgresql://u:p@h:5432/d",
    "MARKET_DATA_FEED": "sip",
    "SYMBOLS": "AAA,BBB",
    "ENTRY_TIMEFRAME_MINUTES": "15",
}


def _bar(symbol, ts, o=100.0, h=101.0, l=99.0, c=100.5, v=1000):
    return Bar(symbol=symbol, timestamp=ts, open=o, high=h, low=l, close=c, volume=v)


def _utc(y, mo, d, h, mi):
    return datetime(y, mo, d, h, mi, tzinfo=timezone.utc)


def _scenario(symbol, intraday_ts, daily_ts):
    return ReplayScenario(
        name=f"{symbol}_x",
        symbol=symbol,
        starting_equity=100000.0,
        daily_bars=[_bar(symbol, ts) for ts in daily_ts],
        intraday_bars=[_bar(symbol, ts) for ts in intraday_ts],
    )


def test_union_timeline_merges_and_dedupes_across_symbols():
    settings = Settings.from_env(ENV)
    a = _scenario("AAA", [_utc(2026, 1, 2, 14, 30), _utc(2026, 1, 2, 14, 45)], [_utc(2026, 1, 1, 5, 0)])
    # BBB missing the 14:30 bar (a gap) and adds a 15:00 bar
    b = _scenario("BBB", [_utc(2026, 1, 2, 14, 45), _utc(2026, 1, 2, 15, 0)], [_utc(2026, 1, 1, 5, 0)])
    runner = PortfolioReplayRunner(settings, signal_evaluator=lambda **k: None, strategy_name="breakout")
    timeline = runner._build_timeline([a, b])
    assert timeline == [
        _utc(2026, 1, 2, 14, 30),
        _utc(2026, 1, 2, 14, 45),
        _utc(2026, 1, 2, 15, 0),
    ]


def test_point_in_time_daily_slice_excludes_current_and_future_days():
    settings = Settings.from_env(ENV)
    daily = [_utc(2026, 1, 1, 5, 0), _utc(2026, 1, 2, 5, 0)]  # day-1 and day-2 daily bars
    a = _scenario("AAA", [_utc(2026, 1, 2, 14, 30)], daily)
    runner = PortfolioReplayRunner(settings, signal_evaluator=lambda **k: None, strategy_name="breakout")
    runner._index_scenarios([a])
    # On session day 2026-01-02, only the 2026-01-01 daily bar is visible (< day).
    sliced = runner._daily_slice_for("AAA", _utc(2026, 1, 2, 14, 30))
    assert len(sliced) == 1
    assert sliced[0].timestamp == _utc(2026, 1, 1, 5, 0)
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `pytest tests/unit/test_portfolio_runner.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'alpaca_bot.replay.portfolio'`.

- [ ] **Step 3: Implement the scaffolding**

```python
# src/alpaca_bot/replay/portfolio.py
"""Cross-sectional / portfolio replay.

Joins all per-symbol scenarios on their shared session-day axis and replays them
jointly against ONE shared equity pool, calling the unchanged pure evaluate_cycle
once per cycle with the full symbol set. This exercises the engine's existing
cross-sectional ranking + top-K cap (max_open_positions) + portfolio exposure cap,
which the single-symbol ReplayRunner never triggers.

Read-only diagnostic: no Postgres, no broker, no AuditEvent. Emits a pooled
list[ReplayTradeRecord] scored by the existing audit objective.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime

from alpaca_bot.config import Settings
from alpaca_bot.core.engine import CycleIntentType, evaluate_cycle
from alpaca_bot.domain.models import Bar, OpenPosition, ReplayScenario, WorkingEntryOrder
from alpaca_bot.replay.mechanics import (
    entry_fill_price,
    eod_exit_price,
    profit_target_price,
    simulate_buy_stop_limit_fill,
    stop_exit_price,
)
from alpaca_bot.replay.report import ReplayTradeRecord
from alpaca_bot.risk.sizing import calculate_position_size
from alpaca_bot.strategy import StrategySignalEvaluator
from alpaca_bot.strategy.breakout import session_day


@dataclass
class _Lane:
    """Per-symbol mutable state. Equity is shared at the portfolio level."""
    symbol: str
    intraday: list[Bar]
    daily: list[Bar]
    cursor: int = 0  # index of this symbol's bar at the current tick, -1 if none yet
    working_order: WorkingEntryOrder | None = None
    position: OpenPosition | None = None


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

    # --- scaffolding -----------------------------------------------------

    def _index_scenarios(self, scenarios) -> None:
        self._lanes = {}
        for sc in scenarios:
            intraday = sorted(sc.intraday_bars, key=lambda b: b.timestamp)
            daily = sorted(sc.daily_bars, key=lambda b: b.timestamp)
            self._lanes[sc.symbol] = _Lane(symbol=sc.symbol, intraday=intraday, daily=daily)

    def _build_timeline(self, scenarios) -> list[datetime]:
        stamps: set[datetime] = set()
        for sc in scenarios:
            for b in sc.intraday_bars:
                stamps.add(b.timestamp)
        return sorted(stamps)

    def _daily_slice_for(self, symbol: str, now: datetime) -> list[Bar]:
        lane = self._lanes[symbol]
        day = session_day(now, self.settings)
        tz = self.settings.market_timezone
        return [b for b in lane.daily if b.timestamp.astimezone(tz).date() < day]
```

- [ ] **Step 4: Run the new test to confirm it passes**

Run: `pytest tests/unit/test_portfolio_runner.py -q`
Expected: PASS (both scaffolding tests).

- [ ] **Step 5: Commit**

```bash
git add src/alpaca_bot/replay/portfolio.py tests/unit/test_portfolio_runner.py
git commit -m "feat: PortfolioReplayRunner scaffolding (timeline join + point-in-time slicing)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## Task 3: Portfolio cycle loop — shared equity, cross-sectional top-K entries, exits

**Files:**
- Modify: `src/alpaca_bot/replay/portfolio.py`
- Test: `tests/unit/test_portfolio_runner.py` (add cases)

The loop walks the union timeline. At each tick `t`:
1. For every lane whose current bar timestamp == `t` (a *fresh* bar): advance its cursor, then resolve working-order fill, stop-hit, and profit-target against the shared equity pool — recording any closed trade.
2. Build `symbols` = the set of fresh-bar lanes (so stale slices never re-fire). Call `evaluate_cycle` ONCE with all-symbol point-in-time mappings, shared equity, all open positions, all working-order symbols, and portfolio-wide `traded_symbols_today`. `global_open_count` is left None so the engine derives `available_slots = max_open_positions - open - working`.
3. Route returned intents: ENTRY → place a working order on that symbol's *next* bar; UPDATE_STOP → raise that lane's stop; EXIT → EOD-flatten that lane.

Key fidelity points (match single-symbol semantics):
- A symbol participates in entry evaluation only on its own fresh bars.
- Next-bar execution: the working order's `active_bar_timestamp` is the symbol's own next intraday bar after the signal.
- Position sizing at fill uses the shared equity at that tick.
- Stop-hit takes priority over profit-target within a bar (stop checked first).

- [ ] **Step 1: Write the failing test (top-K selectivity + shared equity)**

```python
# add to tests/unit/test_portfolio_runner.py
def _ramp(symbol, day_ts, base, step, n):
    """n intraday bars rising by `step` each — a clean breakout setup."""
    out = []
    price = base
    for i in range(n):
        ts = day_ts.replace(minute=30 + 15 * i) if (30 + 15 * i) < 60 else day_ts.replace(hour=day_ts.hour + 1, minute=(30 + 15 * i) % 60)
        out.append(_bar(symbol, ts, o=price, h=price + step, l=price - step / 2, c=price + step, v=5000))
        price += step
    return out


def test_topk_cap_limits_concurrent_entries_to_max_open_positions(monkeypatch):
    # Two symbols both fire an ENTRY on the same tick; with max_open_positions=1
    # only the higher-ranked one is taken (engine enforces the cap).
    base_env = dict(ENV)
    base_env["MAX_OPEN_POSITIONS"] = "1"
    settings = Settings.from_env(base_env)

    t0 = _utc(2026, 1, 2, 14, 30)
    t1 = _utc(2026, 1, 2, 14, 45)
    t2 = _utc(2026, 1, 2, 15, 0)

    # A fake evaluator that emits a strong signal for both symbols at t0,
    # with AAA stronger (higher close/entry_level). We assert exactly one fill.
    from alpaca_bot.domain.models import EntrySignal

    def fake_eval(*, symbol, intraday_bars, signal_index, daily_bars, settings):
        bar = intraday_bars[signal_index]
        if bar.timestamp != t0:
            return None
        strength = 1.05 if symbol == "AAA" else 1.02
        return EntrySignal(
            symbol=symbol,
            signal_bar=bar,
            entry_level=100.0,
            relative_volume=2.0,
            stop_price=99.0,
            limit_price=round(100.0 * strength, 2),
            initial_stop_price=99.0,
            option_contract=None,
        )

    def mk(symbol):
        intraday = [
            _bar(symbol, t0, o=100, h=106, l=99, c=105, v=5000),
            _bar(symbol, t1, o=105, h=107, l=104, c=106, v=5000),
            _bar(symbol, t2, o=106, h=108, l=99, c=107, v=5000),
        ]
        daily = [_bar(symbol, _utc(2026, 1, 1, 5, 0))]
        return ReplayScenario(name=symbol, symbol=symbol, starting_equity=100000.0,
                              daily_bars=daily, intraday_bars=intraday)

    runner = PortfolioReplayRunner(settings, signal_evaluator=fake_eval, strategy_name="breakout")
    trades = runner.run([mk("AAA"), mk("BBB")])
    # With one capacity slot and AAA ranked higher, only AAA should ever hold a position.
    assert {t.symbol for t in trades} == {"AAA"}
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `pytest tests/unit/test_portfolio_runner.py::test_topk_cap_limits_concurrent_entries_to_max_open_positions -q`
Expected: FAIL — `AttributeError: 'PortfolioReplayRunner' object has no attribute 'run'`.

- [ ] **Step 3: Implement the cycle loop**

Append to `PortfolioReplayRunner` in `src/alpaca_bot/replay/portfolio.py`:

```python
    # --- main loop -------------------------------------------------------

    def run(self, scenarios) -> list[ReplayTradeRecord]:
        self._index_scenarios(scenarios)
        timeline = self._build_timeline(scenarios)
        equity = float(getattr(scenarios[0], "starting_equity", 100000.0)) if scenarios else 100000.0

        trades: list[ReplayTradeRecord] = []
        traded_symbols: set[tuple[str, date]] = set()
        # per-symbol next-index pointer into intraday bars
        next_idx: dict[str, int] = {s: 0 for s in self._lanes}

        for now in timeline:
            fresh: list[str] = []
            for sym, lane in self._lanes.items():
                idx = next_idx[sym]
                if idx < len(lane.intraday) and lane.intraday[idx].timestamp == now:
                    lane.cursor = idx
                    next_idx[sym] = idx + 1
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
                s: self._lanes[s].intraday[: self._lanes[s].cursor + 1] for s in fresh
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
```

> **Implementer note:** `_resolve_exits` enforces the same exit precedence as the single-symbol runner — the stop-hit branch is checked first and `return`s on a hit, so the profit-target branch only runs when the stop did not fire (exactly as in `runner.py:92-100`). The `lane.position is not None` re-check on the profit-target branch is defensive: it is always true here (the stop branch returns when it nulls the position) but documents that the two branches are mutually exclusive.

- [ ] **Step 4: Run the test to confirm it passes**

Run: `pytest tests/unit/test_portfolio_runner.py -q`
Expected: PASS — only AAA ever holds a position under `MAX_OPEN_POSITIONS=1`.

- [ ] **Step 5: Add a shared-equity regression test**

```python
# add to tests/unit/test_portfolio_runner.py
def test_single_shared_equity_pool_not_per_symbol():
    # Two symbols, ample capacity: both can trade, but they draw from ONE pool.
    # We assert the runner runs to completion and produces trades for both,
    # confirming the shared-pool path executes end to end.
    settings = Settings.from_env(ENV)
    t0 = _utc(2026, 1, 2, 14, 30)
    t1 = _utc(2026, 1, 2, 14, 45)
    t2 = _utc(2026, 1, 2, 15, 0)
    from alpaca_bot.domain.models import EntrySignal

    def fake_eval(*, symbol, intraday_bars, signal_index, daily_bars, settings):
        bar = intraday_bars[signal_index]
        if bar.timestamp != t0:
            return None
        return EntrySignal(symbol=symbol, signal_bar=bar, entry_level=100.0,
                           relative_volume=2.0, stop_price=99.0, limit_price=100.5,
                           initial_stop_price=99.0, option_contract=None)

    def mk(symbol):
        intraday = [
            _bar(symbol, t0, o=100, h=106, l=99, c=105, v=5000),
            _bar(symbol, t1, o=100.5, h=107, l=100, c=106, v=5000),   # fills at 100.5
            _bar(symbol, t2, o=106, h=108, l=80, c=107, v=5000),       # stop hit (low 80)
        ]
        daily = [_bar(symbol, _utc(2026, 1, 1, 5, 0))]
        return ReplayScenario(name=symbol, symbol=symbol, starting_equity=100000.0,
                              daily_bars=daily, intraday_bars=intraday)

    runner = PortfolioReplayRunner(settings, signal_evaluator=fake_eval, strategy_name="breakout")
    trades = runner.run([mk("AAA"), mk("BBB")])
    assert {t.symbol for t in trades} == {"AAA", "BBB"}
    assert all(t.exit_reason == "stop" for t in trades)
```

- [ ] **Step 6: Run the full suite**

Run: `pytest -q`
Expected: baseline count + 4 new portfolio tests, all pass.

- [ ] **Step 7: Commit**

```bash
git add src/alpaca_bot/replay/portfolio.py tests/unit/test_portfolio_runner.py
git commit -m "feat: portfolio cycle loop — shared equity, cross-sectional top-K entries

One evaluate_cycle call per tick over fresh-bar symbols; engine ranks/caps/
exposure-limits. Per-lane fills/stops/targets/EOD against a single shared equity
pool. evaluate_cycle unchanged and still pure.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## Task 4: `_portfolio_pooled_trades` adapter (matches PooledTradesFn)

**Files:**
- Modify: `src/alpaca_bot/replay/portfolio.py`
- Test: `tests/unit/test_portfolio_pooled_trades.py`

The adapter runs ONE portfolio simulation over all scenarios and returns the pooled trades — matching `PooledTradesFn = Callable[[Sequence[ReplayScenario], Settings, str], list[ReplayTradeRecord]]`, so it drops into `run_audit` and `run_break_even_sweep`.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_portfolio_pooled_trades.py
from datetime import datetime, timezone

from alpaca_bot.config import Settings
from alpaca_bot.domain.models import Bar, EntrySignal, ReplayScenario
from alpaca_bot.replay.audit import run_audit
from alpaca_bot.replay.break_even import run_break_even_sweep
from alpaca_bot.replay.portfolio import portfolio_pooled_trades

ENV = {
    "TRADING_MODE": "paper", "ENABLE_LIVE_TRADING": "false",
    "STRATEGY_VERSION": "v1-breakout", "DATABASE_URL": "postgresql://u:p@h:5432/d",
    "MARKET_DATA_FEED": "sip", "SYMBOLS": "AAA,BBB", "ENTRY_TIMEFRAME_MINUTES": "15",
}


def _bar(symbol, ts, o=100.0, h=101.0, l=99.0, c=100.5, v=1000):
    return Bar(symbol=symbol, timestamp=ts, open=o, high=h, low=l, close=c, volume=v)


def _utc(h, mi):
    return datetime(2026, 1, 2, h, mi, tzinfo=timezone.utc)


def _scn(symbol):
    intraday = [
        _bar(symbol, _utc(14, 30), o=100, h=106, l=99, c=105, v=5000),
        _bar(symbol, _utc(14, 45), o=100.5, h=107, l=100, c=106, v=5000),
        _bar(symbol, _utc(15, 0), o=106, h=108, l=80, c=107, v=5000),
    ]
    daily = [_bar(symbol, datetime(2026, 1, 1, 5, 0, tzinfo=timezone.utc))]
    return ReplayScenario(name=symbol, symbol=symbol, starting_equity=100000.0,
                          daily_bars=daily, intraday_bars=intraday)


def _fake_registry(monkeypatch):
    def fake_eval(*, symbol, intraday_bars, signal_index, daily_bars, settings):
        bar = intraday_bars[signal_index]
        if bar.timestamp != _utc(14, 30):
            return None
        return EntrySignal(symbol=symbol, signal_bar=bar, entry_level=100.0,
                           relative_volume=2.0, stop_price=99.0, limit_price=100.5,
                           initial_stop_price=99.0, option_contract=None)
    monkeypatch.setattr(
        "alpaca_bot.strategy.STRATEGY_REGISTRY", {"breakout": fake_eval}, raising=False
    )
    monkeypatch.setattr(
        "alpaca_bot.replay.portfolio.STRATEGY_REGISTRY", {"breakout": fake_eval}, raising=False
    )


def test_portfolio_pooled_trades_matches_pooledtradesfn_shape(monkeypatch):
    _fake_registry(monkeypatch)
    settings = Settings.from_env(ENV)
    trades = portfolio_pooled_trades([_scn("AAA"), _scn("BBB")], settings, "breakout")
    assert {t.symbol for t in trades} == {"AAA", "BBB"}
    assert all(hasattr(t, "pnl") for t in trades)


def test_injectable_into_run_audit(monkeypatch):
    _fake_registry(monkeypatch)
    settings = Settings.from_env(ENV)
    rows = run_audit(
        scenarios=[_scn("AAA"), _scn("BBB")], settings=settings,
        strategies=["breakout"], slippage_bps=5.0,
        pooled_trades_fn=portfolio_pooled_trades,
    )
    assert len(rows) == 1
    assert rows[0].strategy == "breakout"


def test_injectable_into_break_even_sweep(monkeypatch):
    _fake_registry(monkeypatch)
    settings = Settings.from_env(ENV)
    res = run_break_even_sweep(
        scenarios=[_scn("AAA"), _scn("BBB")], settings=settings,
        strategy="breakout", slippage_ladder=[0.0, 5.0],
        pooled_trades_fn=portfolio_pooled_trades,
    )
    assert res.strategy == "breakout"
    assert len(res.points) == 2
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `pytest tests/unit/test_portfolio_pooled_trades.py -q`
Expected: FAIL — `ImportError: cannot import name 'portfolio_pooled_trades'`.

- [ ] **Step 3: Implement the adapter**

Add to the top imports of `src/alpaca_bot/replay/portfolio.py`:

```python
from collections.abc import Sequence

from alpaca_bot.strategy import STRATEGY_REGISTRY
```

Append at module level (after the class):

```python
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
```

- [ ] **Step 4: Run the test to confirm it passes**

Run: `pytest tests/unit/test_portfolio_pooled_trades.py -q`
Expected: PASS (all three).

- [ ] **Step 5: Run the full suite**

Run: `pytest -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/alpaca_bot/replay/portfolio.py tests/unit/test_portfolio_pooled_trades.py
git commit -m "feat: portfolio_pooled_trades adapter — drop-in PooledTradesFn for audit/break-even

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## Task 5: CLI subcommand `portfolio-audit`

**Files:**
- Modify: `src/alpaca_bot/replay/cli.py`
- Test: `tests/unit/test_portfolio_cli.py`

Add a `portfolio-audit` subcommand mirroring the existing `audit`/`break-even` wiring, but injecting `portfolio_pooled_trades`. It loads all scenarios from `--scenario-dir`, accepts `--strategy` (repeatable), `--slippage-bps` (default 5.0), `--max-open-positions` (repeatable K sweep; each value runs a separate portfolio audit via `dataclasses.replace`), and `--output` (path or `-` for stdout). Read-only; never writes production config.

> **Implementer:** open `src/alpaca_bot/replay/cli.py` and match the exact argparse/structure of the existing `audit` and `break-even` subcommands (subparser creation, scenario loading via `ReplayRunner.load_scenario` over `sorted(Path(dir).glob("*.json"))`, the `--output -` stdout convention, and the `return 1` on empty dir). Reuse `run_audit` with `pooled_trades_fn=portfolio_pooled_trades`. For each K in `--max-open-positions`, call `run_audit` with `settings=dataclasses.replace(base_settings, max_open_positions=K)` and label the section with K. Emit a markdown table of `StrategyAuditRow` fields (strategy, trades, mean_trade_pnl, ci_low, ci_high, p_positive, verdict, cost_drag).

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_portfolio_cli.py
import json
from pathlib import Path

from alpaca_bot.replay.cli import main

ENVKEYS = {
    "TRADING_MODE": "paper", "ENABLE_LIVE_TRADING": "false",
    "STRATEGY_VERSION": "v1-breakout",
    "DATABASE_URL": "postgresql://u:p@h:5432/d",
    "MARKET_DATA_FEED": "sip", "SYMBOLS": "AAA",
}


def _write_scenario(path: Path, symbol: str) -> None:
    bars = [{
        "symbol": symbol, "timestamp": "2026-01-02T14:30:00+00:00",
        "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5, "volume": 1000,
    }]
    path.write_text(json.dumps({
        "name": f"{symbol}_x", "symbol": symbol, "starting_equity": 100000.0,
        "intraday_bars": bars, "daily_bars": bars,
    }))


def _set_env(monkeypatch):
    for k, v in ENVKEYS.items():
        monkeypatch.setenv(k, v)


def test_portfolio_audit_cli_writes_report(tmp_path, monkeypatch):
    _set_env(monkeypatch)
    scen = tmp_path / "scen"
    scen.mkdir()
    _write_scenario(scen / "AAA.json", "AAA")
    _write_scenario(scen / "BBB.json", "BBB")
    out = tmp_path / "report.md"
    rc = main([
        "portfolio-audit", "--scenario-dir", str(scen),
        "--strategy", "bull_flag", "--slippage-bps", "5",
        "--max-open-positions", "20", "--max-open-positions", "5",
        "--output", str(out),
    ])
    assert rc == 0
    text = out.read_text()
    assert "bull_flag" in text
    assert "K=20" in text and "K=5" in text


def test_portfolio_audit_cli_empty_dir_returns_1(tmp_path, monkeypatch):
    _set_env(monkeypatch)
    empty = tmp_path / "empty"
    empty.mkdir()
    rc = main(["portfolio-audit", "--scenario-dir", str(empty), "--strategy", "bull_flag"])
    assert rc == 1
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `pytest tests/unit/test_portfolio_cli.py -q`
Expected: FAIL — argparse rejects the unknown `portfolio-audit` subcommand (SystemExit) or returns nonzero.

- [ ] **Step 3: Implement the subcommand**

Edit `src/alpaca_bot/replay/cli.py`. Add the import:

```python
from alpaca_bot.replay.portfolio import portfolio_pooled_trades
```

Register a subparser (alongside `audit` / `break-even`):

```python
    p_port = sub.add_parser("portfolio-audit", help="Cross-sectional shared-equity top-K audit")
    p_port.add_argument("--scenario-dir", required=True)
    p_port.add_argument("--strategy", action="append", required=True)
    p_port.add_argument("--slippage-bps", type=float, default=5.0)
    p_port.add_argument("--max-open-positions", action="append", type=int, default=None)
    p_port.add_argument("--output", default="-")
```

Add the handler (follow the exact scenario-loading + empty-dir + output conventions used by the existing `audit` handler in this file):

```python
    if args.command == "portfolio-audit":
        import dataclasses
        from pathlib import Path
        from alpaca_bot.replay.audit import run_audit
        from alpaca_bot.replay.runner import ReplayRunner

        settings = Settings.from_env()
        paths = sorted(Path(args.scenario_dir).glob("*.json"))
        if not paths:
            print(f"No scenarios in {args.scenario_dir}", file=sys.stderr)
            return 1
        scenarios = [ReplayRunner.load_scenario(p) for p in paths]
        ks = args.max_open_positions or [settings.max_open_positions]

        out_lines: list[str] = ["# Portfolio top-K audit", ""]
        for k in ks:
            ksettings = dataclasses.replace(settings, max_open_positions=k)
            rows = run_audit(
                scenarios=scenarios, settings=ksettings,
                strategies=args.strategy, slippage_bps=args.slippage_bps,
                pooled_trades_fn=portfolio_pooled_trades,
                on_progress=lambda m: print(m, file=sys.stderr),
            )
            out_lines.append(f"## K={k} (max_open_positions)")
            out_lines.append("")
            out_lines.append("| strategy | trades | mean | ci_low | ci_high | p_positive | verdict | cost_drag |")
            out_lines.append("|---|---|---|---|---|---|---|---|")
            for r in rows:
                out_lines.append(
                    f"| {r.strategy} | {r.trades} | {r.mean_trade_pnl} | {r.ci_low} | "
                    f"{r.ci_high} | {r.p_positive} | {r.verdict} | {r.cost_drag} |"
                )
            out_lines.append("")
        report = "\n".join(out_lines)
        if args.output == "-":
            print(report)
        else:
            Path(args.output).write_text(report)
        return 0
```

> If `Settings.from_env()` / `sys` are not already imported at the top of `cli.py`, add them. Check the existing `audit` handler for the canonical import location.

- [ ] **Step 4: Run the test to confirm it passes**

Run: `pytest tests/unit/test_portfolio_cli.py -q`
Expected: PASS (both).

- [ ] **Step 5: Run the full suite**

Run: `pytest -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/alpaca_bot/replay/cli.py tests/unit/test_portfolio_cli.py
git commit -m "feat: portfolio-audit CLI subcommand (K sweep, read-only)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## Task 6 (ops, gated on Tasks 1-5 green): run + report

**Files:**
- Create: `docs/strategy-audit/2026-06-16-cross-sectional-topk.md`

This is a read-only ops run over the production scenario store. No production config is touched. Run **one** portfolio process at a time (≈2.4 GB peak RSS; ~6 GB free).

- [ ] **Step 1: Confirm the suite is green and the CLI smoke-tests on a tiny dir**

Run: `pytest -q`
Then a 2-file smoke test:
```bash
set -a && source /etc/alpaca_bot/alpaca-bot.env && set +a
mkdir -p /tmp/topk_smoke
cp $(ls /var/lib/alpaca-bot/nightly/scenarios/*.json | head -2) /tmp/topk_smoke/
alpaca-bot-backtest portfolio-audit --scenario-dir /tmp/topk_smoke \
  --strategy bull_flag --max-open-positions 20 --output -
```
Expected: a `K=20` table prints; rc 0.

- [ ] **Step 2: Establish the single-symbol baseline for bull_flag (default `_replay_pooled_trades`)**

```bash
set -a && source /etc/alpaca_bot/alpaca-bot.env && set +a
alpaca-bot-backtest audit --scenario-dir /var/lib/alpaca-bot/nightly/scenarios \
  --strategy bull_flag --slippage-bps 5 --output /tmp/baseline_bull_flag.md
```
Run in the background (`run_in_background: true`); it is ~audit-scale (~2 h). Record bull_flag's baseline mean/ci_low/p/verdict.

- [ ] **Step 3: Run the portfolio top-K sweep for bull_flag at 5 bps**

```bash
set -a && source /etc/alpaca_bot/alpaca-bot.env && set +a
alpaca-bot-backtest portfolio-audit --scenario-dir /var/lib/alpaca-bot/nightly/scenarios \
  --strategy bull_flag --slippage-bps 5 \
  --max-open-positions 5 --max-open-positions 10 --max-open-positions 20 \
  --output /tmp/portfolio_bull_flag.md
```
Run in the background, **after** Step 2 finishes (one heavy process at a time — do not run baseline and portfolio concurrently). Monitor RSS; if a second job is needed, wait.

- [ ] **Step 4: Write the comparison report**

Create `docs/strategy-audit/2026-06-16-cross-sectional-topk.md` with:
- The header block: read-only diagnostic, candidates only, promotion via nightly OOS gate, `TRADING_MODE=paper`/`ENABLE_LIVE_TRADING=false`/`close_only` untouched, `REPLAY_SLIPPAGE_BPS` semantics unchanged (slippage + K are sweep parameters via `dataclasses.replace`).
- The pivotal-finding framing (engine already cross-sectional; harness was the gap).
- The comparison table: baseline (100k/symbol, all signals) vs portfolio K∈{5,10,20} (100k shared) — trades, mean per-trade P&L, ci_low, ci_high, p_positive, verdict. Same seeded-bootstrap objective for all rows.
- Interpretation: does tightening K raise per-trade edge / ci_low despite fewer trades? Does any K reach `positive-edge` at 5 bps? Relate to the break-even headroom from `2026-06-15-break-even-slippage.md`.
- Gate decision: if portfolio top-K materially lifts ci_low → recommend advancing the portfolio config through the **nightly OOS gate** (never hand-applied); if not → record the null and name the next mechanism. Either way, no production change in this commit.

- [ ] **Step 5: Commit the report**

```bash
git add docs/strategy-audit/2026-06-16-cross-sectional-topk.md
git commit -m "docs: cross-sectional top-K replay results — selectivity vs single-symbol baseline

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## Self-Review

**Spec coverage:** All five spec tensions map to tasks — (1) join/timeline → Task 2; (2) ranking in pure engine → Task 3 (no engine change); (3) reuse momentum/relative_volume ranking → Task 3 (no new field); (4) shared equity → Task 3; (5) scoring reuse → Task 4. CLI → Task 5. Report → Task 6. DRY-extraction → Task 1.

**Placeholder scan:** No placeholders remain. The earlier deliberately-broken profit-target line in Task 3 Step 3 was excised during the Stage-4 refine; `_resolve_exits` now contains only the single explicit stop-then-target flow with `apply_slippage`. All other steps contain complete code or explicit "match the existing handler" instructions tied to named functions in `cli.py`.

**Type consistency:** `portfolio_pooled_trades` signature matches `PooledTradesFn` exactly (`Sequence[ReplayScenario], Settings, str -> list[ReplayTradeRecord]`). `ReplayTradeRecord` fields match `report.py:11-21`. `WorkingEntryOrder` / `OpenPosition` constructor kwargs match `runner.py:163-172` / `:240-249`. `evaluate_cycle` call mirrors `runner.py:124-136` plus full `symbols` and real `open_positions`.

**Grill resolutions (Stage 3 → Stage 4 refine):**

- **(a) Restricting `symbols=` to fresh-bar lanes vs EOD/stop-update emission — RESOLVED, drove Fix 1.** The engine processes the full `open_positions` list directly (not just `symbols`); its EOD-flatten EXIT path emits an EXIT for *any* open position regardless of whether that symbol has a bar this tick (engine.py EOD-flatten loop, which does not `continue` on missing bars). So a stale lane (open position, no fresh bar this tick) would receive an EOD-flatten EXIT that the routing loop would mis-apply at `lane.intraday[lane.cursor]` — a PAST bar — producing a mispriced phantom trade the single-symbol runner never makes. **Fix:** Task 3's routing loop now guards `if intent.symbol not in fresh_set: continue`, deferring every stale lane's intents to its own next fresh bar, where the engine re-emits EOD-flatten EXIT at the correct bar. UPDATE_STOP and trailing/viability EXIT already require bars inside the engine, so only the bars-free EOD-flatten EXIT could leak — the guard closes exactly that one path.

  - **EOD-flatten completeness invariant.** With the guard, every position is closed by an engine EOD-flatten EXIT *on that symbol's own bar within the flatten window*. This matches single-symbol semantics as long as each scenario supplies full-session intraday bars through the close — which the nightly scenario store does (full regular-session bars per day). A leak (position never flattened) is therefore not expected; if it ever occurred it would surface as a **trade-count shortfall** against the single-symbol baseline in Task 6 Step 4's comparison table (baseline vs portfolio K), so the report's own reconciliation is the detector. No silent final sweep is added — that would risk pricing a leaked exit at the wrong bar and mask the data-quality signal.

- **(b) `int(pos.quantity)` truncation vs `report.py:147` — RESOLVED, drove Fix 2.** The single-symbol baseline computes the scored `ReplayTradeRecord.pnl` from the truncated int quantity (`report.py:147-148`). Task 3's `_record` now computes `pnl` from `int(pos.quantity)` (not the float) so the audit objective is byte-identical for whole-share quantities and never float-vs-int divergent for fractionable symbols. Equity bookkeeping keeps the float quantity, matching the single-symbol runner's float equity updates (`runner.py:293`); only the recorded pnl uses the int — the audit scores pnl, not equity.

- **(c) `Settings.from_env()` env source — RESOLVED, no task change.** `Settings.from_env()` reads `os.environ` by default; the production-server run sources `/etc/alpaca_bot/alpaca-bot.env` via `set -a && source … && set +a` before invoking the CLI (Task 6 commands), so the process env is sufficient. No new env var is introduced; `max_open_positions=K` is applied at runtime via `dataclasses.replace` and is never written to env or `candidate.env`.

Both code fixes (a, b) are confined to Task 3. Because the refine touched a single task, a second grill pass over the revised Task 3 confirms no further changes (Stage 5).
