# After-Hours Stop Preloading Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Compute breakeven trail stops during after-hours/pre-market cycles and persist the updated price to Postgres (DB only — no broker calls), so `dispatch_pending_orders` at market open submits the correct trail-adjusted price instead of the stale initial price.

**Architecture:** Two targeted changes to existing source files. The engine's breakeven gate is relaxed from `not is_extended` to always-on, with a new safety guard that skips any computed stop ≥ the bar's close (would trigger at open). The executor's extended-hours `continue` is replaced with a `db_only=True` path that updates the pending-submit stop record in Postgres without touching the broker. `evaluate_cycle()` remains a pure function; no I/O is introduced into the engine.

**Tech Stack:** Python, pytest, psycopg2 (via existing fake-callable DI pattern).

---

## File Map

| Action | File |
|--------|------|
| Modify | `src/alpaca_bot/core/engine.py` |
| Modify | `src/alpaca_bot/runtime/cycle_intent_execution.py` |
| Create | `tests/unit/test_engine_after_hours_breakeven.py` |
| Update | `tests/unit/test_engine_extended_hours.py` (one test) |
| Create | `tests/unit/test_executor_after_hours_stop.py` |

---

### Task 1: Engine — relax breakeven gate, add safety guard

**Files:**
- Modify: `src/alpaca_bot/core/engine.py:292`
- Create: `tests/unit/test_engine_after_hours_breakeven.py`
- Update: `tests/unit/test_engine_extended_hours.py` (update `test_update_stop_suppressed_in_after_hours`)

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_engine_after_hours_breakeven.py`:

```python
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from alpaca_bot.config import Settings
from alpaca_bot.core.engine import CycleIntentType, evaluate_cycle
from alpaca_bot.domain import Bar, OpenPosition
from alpaca_bot.strategy.session import SessionType


def _settings(**overrides) -> Settings:
    base = {
        "TRADING_MODE": "paper",
        "ENABLE_LIVE_TRADING": "false",
        "STRATEGY_VERSION": "v1",
        "DATABASE_URL": "postgresql://x:y@localhost/db",
        "MARKET_DATA_FEED": "sip",
        "SYMBOLS": "AAPL",
        "DAILY_SMA_PERIOD": "20",
        "BREAKOUT_LOOKBACK_BARS": "20",
        "RELATIVE_VOLUME_LOOKBACK_BARS": "20",
        "RELATIVE_VOLUME_THRESHOLD": "1.5",
        "ENTRY_TIMEFRAME_MINUTES": "15",
        "RISK_PER_TRADE_PCT": "0.0025",
        "MAX_POSITION_PCT": "0.05",
        "MAX_OPEN_POSITIONS": "3",
        "DAILY_LOSS_LIMIT_PCT": "0.01",
        "STOP_LIMIT_BUFFER_PCT": "0.001",
        "BREAKOUT_STOP_BUFFER_PCT": "0.001",
        "ENTRY_STOP_PRICE_BUFFER": "0.01",
        "ENTRY_WINDOW_START": "10:00",
        "ENTRY_WINDOW_END": "15:30",
        "FLATTEN_TIME": "15:45",
        # REQUIRED: without this, is_flatten_time() returns True for AFTER_HOURS
        # sessions (session.py:53-54), causing the engine to emit EXIT intents
        # instead of breakeven UPDATE_STOP intents.
        "EXTENDED_HOURS_ENABLED": "true",
        # BREAKEVEN_TRIGGER_PCT=0.0025, BREAKEVEN_TRAIL_PCT=0.002
    }
    base.update(overrides)
    return Settings.from_env(base)


def _make_position(
    *,
    entry_price: float = 100.0,
    stop_price: float = 95.0,
    highest_price: float = 105.0,
) -> OpenPosition:
    return OpenPosition(
        symbol="AAPL",
        entry_timestamp=datetime(2026, 5, 9, 14, 0, tzinfo=timezone.utc),
        entry_price=entry_price,
        quantity=100.0,
        entry_level=entry_price - 5.0,
        initial_stop_price=stop_price,
        stop_price=stop_price,
        trailing_active=False,
        highest_price=highest_price,
        strategy_name="breakout",
    )


def _make_bar(*, high: float, close: float, ts: datetime | None = None) -> Bar:
    ts = ts or datetime(2026, 5, 9, 21, 0, tzinfo=timezone.utc)  # 5pm ET = after hours
    return Bar(
        symbol="AAPL",
        timestamp=ts,
        open=close - 0.10,
        high=high,
        low=close - 0.20,
        close=close,
        volume=300_000,
    )


def _run_after_hours(position: OpenPosition, bar: Bar, settings: Settings) -> list:
    now = datetime(2026, 5, 9, 21, 0, tzinfo=timezone.utc)
    daily_bar = Bar(
        symbol="AAPL",
        timestamp=datetime(2026, 5, 8, tzinfo=timezone.utc),
        open=99.0, high=106.0, low=98.0, close=100.0,
        volume=1_000_000,
    )
    result = evaluate_cycle(
        settings=settings,
        now=now,
        equity=10_000.0,
        open_positions=[position],
        intraday_bars_by_symbol={"AAPL": [bar]},
        daily_bars_by_symbol={"AAPL": [daily_bar] * 60},
        working_order_symbols=set(),
        traded_symbols_today=set(),
        entries_disabled=False,
        session_type=SessionType.AFTER_HOURS,
    )
    return [i for i in result.intents if i.intent_type == CycleIntentType.UPDATE_STOP]


def test_after_hours_breakeven_emits_intent_when_stop_below_close():
    """
    Extended hours: when the computed breakeven stop is below the bar's close,
    the engine must emit UPDATE_STOP (safety guard does not fire).

    Setup:
      entry_price=100, highest_price=105, bar.high=101, bar.close=106
      trigger = 100 * 1.0025 = 100.25  → bar.high 101 >= trigger ✓
      max_price = max(105, 101) = 105
      trail_stop = round(105 * 0.998, 2) = 104.79
      be_stop = max(100, 104.79) = 104.79
      safety guard: 104.79 >= 106.0 → False → intent emitted ✓
    """
    settings = _settings()
    position = _make_position(entry_price=100.0, stop_price=95.0, highest_price=105.0)
    bar = _make_bar(high=101.0, close=106.0)

    intents = _run_after_hours(position, bar, settings)

    assert len(intents) == 1, f"Expected 1 UPDATE_STOP intent, got {len(intents)}"
    assert intents[0].stop_price == pytest.approx(104.79)
    assert intents[0].reason == "breakeven"


def test_after_hours_safety_guard_suppresses_stop_at_or_above_close():
    """
    Extended hours: when the computed breakeven stop >= bar.close,
    submitting it would trigger immediately at open — engine must NOT emit.

    Setup:
      entry_price=100, highest_price=105, bar.high=106, bar.close=104
      trigger = 100.25  → bar.high 106 >= trigger ✓
      max_price = max(105, 106) = 106
      trail_stop = round(106 * 0.998, 2) = 105.79
      be_stop = max(100, 105.79) = 105.79
      safety guard: 105.79 >= 104.0 → True → no intent emitted ✓
    """
    settings = _settings()
    position = _make_position(entry_price=100.0, stop_price=95.0, highest_price=105.0)
    bar = _make_bar(high=106.0, close=104.0)

    intents = _run_after_hours(position, bar, settings)

    assert intents == [], (
        f"Safety guard should have suppressed intent when stop >= close; got {intents}"
    )


def test_after_hours_price_below_trigger_no_intent():
    """
    Extended hours: when bar.high < trigger, the breakeven condition is not met
    and no intent is emitted.

    Setup:
      entry_price=100, trigger=100.25, bar.high=100.0 < trigger → no intent
    """
    settings = _settings()
    position = _make_position(entry_price=100.0, stop_price=95.0, highest_price=100.0)
    bar = _make_bar(high=100.0, close=100.50)

    intents = _run_after_hours(position, bar, settings)

    assert intents == [], f"No intent expected below trigger; got {intents}"
```

- [ ] **Step 2: Update the existing test that will break**

In `tests/unit/test_engine_extended_hours.py`, the test `test_update_stop_suppressed_in_after_hours` currently expects NO UPDATE_STOP when `bar.high=106, bar.close=106`. After the engine change the computed stop (105.79) is below close (106.0), so the safety guard does NOT fire and an intent IS emitted. Update the test to cover the safety-guard case instead (stop above close):

Find the function `test_update_stop_suppressed_in_after_hours` in `tests/unit/test_engine_extended_hours.py` and replace it entirely with:

```python
def test_update_stop_suppressed_in_after_hours_safety_guard():
    """
    After removing the extended-hours gate, the safety guard now prevents
    UPDATE_STOP when be_stop >= close (would trigger at open).
    bar.close=103 < be_stop≈105.79 → safety guard fires → no intent.
    """
    settings = _settings()
    now = datetime(2026, 4, 28, 21, 0, tzinfo=timezone.utc)
    position = _position(stop_price=95.0)
    # high=106 → be_stop ≈ 105.79; close=103 < 105.79 → safety guard fires
    bar = _bar("AAPL", close=103.0, high=106.0)

    result = evaluate_cycle(
        settings=settings,
        now=now,
        equity=100_000.0,
        intraday_bars_by_symbol={"AAPL": [bar]},
        daily_bars_by_symbol={"AAPL": [bar]},
        open_positions=[position],
        working_order_symbols=set(),
        traded_symbols_today=set(),
        entries_disabled=False,
        session_type=SessionType.AFTER_HOURS,
    )
    update_stops = [i for i in result.intents if i.intent_type is CycleIntentType.UPDATE_STOP]
    assert update_stops == [], "Safety guard must suppress UPDATE_STOP when stop >= close"
```

Note: the `_position()` helper in `test_engine_extended_hours.py` does not set `highest_price`, so it defaults to `0.0`. This means `max_price = max(0.0, 106.0) = 106.0`, `trail_stop = round(106.0 * 0.998, 2) = 105.79`, `be_stop = 105.79`. With `close=103.0`, safety guard `105.79 >= 103.0` → True → suppressed ✓.

- [ ] **Step 3: Run to confirm the new tests fail and the updated old test fails**

```bash
pytest tests/unit/test_engine_after_hours_breakeven.py tests/unit/test_engine_extended_hours.py::test_update_stop_suppressed_in_after_hours -v
```

Expected: all three new tests FAIL (`test_after_hours_breakeven_emits_intent_when_stop_below_close`, `test_after_hours_safety_guard_suppresses_stop_at_or_above_close`, `test_after_hours_price_below_trigger_no_intent`). The old test either fails or is renamed to `test_update_stop_suppressed_in_after_hours_safety_guard` (which will also fail before the engine change).

- [ ] **Step 4: Implement the engine change**

In `src/alpaca_bot/core/engine.py`, line 292, make two changes:

**Change 1** — remove `and not is_extended` from the gate:
```
# Before:
if settings.enable_breakeven_stop and not is_extended:

# After:
if settings.enable_breakeven_stop:
```

**Change 2** — inside the per-position loop, add the safety guard immediately after computing `be_stop` (after line 313, before the `if effective_stop < be_stop:` check):
```python
            if latest_bar.high >= trigger:
                max_price = max(position.highest_price, latest_bar.high)
                trail_stop = round(max_price * (1 - settings.breakeven_trail_pct), 2)
                be_stop = max(position.entry_price, trail_stop)
                if is_extended and be_stop >= latest_bar.close:
                    continue  # stop above current price would trigger immediately at open
                if effective_stop < be_stop:
                    intents.append(
                        CycleIntent(
                            intent_type=CycleIntentType.UPDATE_STOP,
                            symbol=position.symbol,
                            timestamp=now,
                            stop_price=be_stop,
                            strategy_name=strategy_name,
                            reason="breakeven",
                        )
                    )
```

The complete modified block (lines 289–324) should read:
```python
    # Breakeven pass: once a position is up BREAKEVEN_TRIGGER_PCT from entry, raise
    # stop to entry price so the trade cannot become a loss.
    # Also runs during extended hours — executor uses db_only path to persist without
    # broker call. Safety guard skips if computed stop >= close (would trigger at open).
    if settings.enable_breakeven_stop:
        _be_exit_syms = {i.symbol for i in intents if i.intent_type == CycleIntentType.EXIT}
        _be_emitted: dict[str, float] = {
            i.symbol: (i.stop_price or 0.0)
            for i in intents
            if i.intent_type == CycleIntentType.UPDATE_STOP
        }
        for position in open_positions:
            if position.symbol in _be_exit_syms:
                continue
            if position.entry_price <= 0:
                continue
            bars = intraday_bars_by_symbol.get(position.symbol, ())
            if not bars:
                continue
            latest_bar = bars[-1]
            trigger = position.entry_price * (1 + settings.breakeven_trigger_pct)
            effective_stop = _be_emitted.get(position.symbol, position.stop_price)
            if latest_bar.high >= trigger:
                max_price = max(position.highest_price, latest_bar.high)
                trail_stop = round(max_price * (1 - settings.breakeven_trail_pct), 2)
                be_stop = max(position.entry_price, trail_stop)
                if is_extended and be_stop >= latest_bar.close:
                    continue  # stop above current price would trigger immediately at open
                if effective_stop < be_stop:
                    intents.append(
                        CycleIntent(
                            intent_type=CycleIntentType.UPDATE_STOP,
                            symbol=position.symbol,
                            timestamp=now,
                            stop_price=be_stop,
                            strategy_name=strategy_name,
                            reason="breakeven",
                        )
                    )
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
pytest tests/unit/test_engine_after_hours_breakeven.py tests/unit/test_engine_extended_hours.py -v
```

Expected: all tests PASS.

Also run the full test suite to catch regressions:
```bash
pytest tests/unit/ -v
```

Expected: all tests PASS.

- [ ] **Step 6: Commit**

```bash
git add tests/unit/test_engine_after_hours_breakeven.py tests/unit/test_engine_extended_hours.py src/alpaca_bot/core/engine.py
git commit -m "feat: relax engine breakeven gate to run after hours with safety guard

Removes 'not is_extended' from the breakeven pass gate so UPDATE_STOP
intents are emitted during extended-hours cycles. Adds a safety guard
that skips any computed stop >= latest_bar.close (would trigger at open).

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

---

### Task 2: Executor — DB-only path for after-hours UPDATE_STOP

**Files:**
- Modify: `src/alpaca_bot/runtime/cycle_intent_execution.py`
- Create: `tests/unit/test_executor_after_hours_stop.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_executor_after_hours_stop.py`:

```python
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from alpaca_bot.config import Settings
from alpaca_bot.core.engine import CycleIntent, CycleIntentType, CycleResult
from alpaca_bot.runtime.cycle_intent_execution import execute_cycle_intents
from alpaca_bot.storage import OrderRecord, PositionRecord
from alpaca_bot.storage.models import TradingMode
from alpaca_bot.strategy.session import SessionType


def _settings() -> Settings:
    return Settings.from_env({
        "TRADING_MODE": "paper",
        "ENABLE_LIVE_TRADING": "false",
        "STRATEGY_VERSION": "v1",
        "DATABASE_URL": "postgresql://test/db",
        "MARKET_DATA_FEED": "iex",
        "SYMBOLS": "AAPL",
        "DAILY_SMA_PERIOD": "20",
        "BREAKOUT_LOOKBACK_BARS": "20",
        "RELATIVE_VOLUME_LOOKBACK_BARS": "20",
        "RELATIVE_VOLUME_THRESHOLD": "1.5",
        "ENTRY_TIMEFRAME_MINUTES": "15",
        "RISK_PER_TRADE_PCT": "0.0025",
        "MAX_POSITION_PCT": "0.05",
        "MAX_OPEN_POSITIONS": "3",
        "DAILY_LOSS_LIMIT_PCT": "0.01",
        "STOP_LIMIT_BUFFER_PCT": "0.001",
        "BREAKOUT_STOP_BUFFER_PCT": "0.001",
        "ENTRY_STOP_PRICE_BUFFER": "0.01",
        "ENTRY_WINDOW_START": "10:00",
        "ENTRY_WINDOW_END": "15:30",
        "FLATTEN_TIME": "15:45",
        "EXTENDED_HOURS_ENABLED": "true",
    })


_NOW = datetime(2026, 5, 9, 21, 0, tzinfo=timezone.utc)


def _make_position(stop_price: float = 95.0) -> PositionRecord:
    return PositionRecord(
        symbol="AAPL",
        trading_mode=TradingMode.PAPER,
        strategy_version="v1",
        strategy_name="breakout",
        quantity=10.0,
        entry_price=100.0,
        stop_price=stop_price,
        initial_stop_price=95.0,
        opened_at=_NOW,
        updated_at=_NOW,
    )


def _make_pending_stop(stop_price: float = 95.0) -> OrderRecord:
    """A pending_submit stop: no broker_order_id — safe to update DB-only."""
    return OrderRecord(
        client_order_id="order-pending-1",
        symbol="AAPL",
        side="sell",
        intent_type="stop",
        status="pending_submit",
        quantity=10.0,
        trading_mode=TradingMode.PAPER,
        strategy_version="v1",
        strategy_name="breakout",
        stop_price=stop_price,
        initial_stop_price=95.0,
        broker_order_id=None,
    )


def _make_submitted_stop(stop_price: float = 95.0) -> OrderRecord:
    """A submitted stop: has broker_order_id — must NOT be modified after hours."""
    return OrderRecord(
        client_order_id="order-submitted-1",
        symbol="AAPL",
        side="sell",
        intent_type="stop",
        status="new",
        quantity=10.0,
        trading_mode=TradingMode.PAPER,
        strategy_version="v1",
        strategy_name="breakout",
        stop_price=stop_price,
        initial_stop_price=95.0,
        broker_order_id="broker-abc-123",
    )


def _make_update_stop_intent(stop_price: float = 104.79) -> CycleIntent:
    return CycleIntent(
        intent_type=CycleIntentType.UPDATE_STOP,
        symbol="AAPL",
        timestamp=_NOW,
        stop_price=stop_price,
        strategy_name="breakout",
        reason="breakeven",
    )


class _BrokerThatMustNotBeCalled:
    """Raises AssertionError if any method is invoked — proves no broker calls."""
    def replace_order(self, **kwargs):
        raise AssertionError(f"replace_order must not be called after hours: {kwargs}")

    def submit_stop_order(self, **kwargs):
        raise AssertionError(f"submit_stop_order must not be called after hours: {kwargs}")

    def submit_market_exit(self, **kwargs):
        raise AssertionError(f"submit_market_exit must not be called after hours: {kwargs}")

    def submit_limit_exit(self, **kwargs):
        raise AssertionError(f"submit_limit_exit must not be called after hours: {kwargs}")

    def cancel_order(self, order_id):
        raise AssertionError(f"cancel_order must not be called after hours: {order_id}")


def _fake_runtime(stop_order: OrderRecord | None, position: PositionRecord):
    saved_orders: list[OrderRecord] = []
    saved_positions: list[PositionRecord] = []
    audits: list = []

    _stop_order = stop_order  # capture for closure

    class FakeOrderStore:
        def save(self, order, *, commit=True):
            saved_orders.append(order)

        def list_by_status(self, **kwargs):
            if _stop_order is None:
                return []
            return [_stop_order]

    class FakePositionStore:
        def save(self, pos, *, commit=True):
            saved_positions.append(pos)

        def list_all(self, **kwargs):
            return [position]

    class FakeAuditStore:
        def append(self, event, *, commit=True):
            audits.append(event)

    class FakeConn:
        def commit(self): pass
        def rollback(self): pass

    class FakeRuntime:
        order_store = FakeOrderStore()
        position_store = FakePositionStore()
        audit_event_store = FakeAuditStore()
        connection = FakeConn()

    return FakeRuntime(), saved_orders, saved_positions, audits


def test_after_hours_pending_stop_updated_db_only_no_broker_call():
    """
    After hours with a pending_submit stop: the executor must update
    order.stop_price and position.stop_price in DB without calling the broker.

    This is the primary contract: pending_submit orders are safe to update
    because dispatch_pending_orders hasn't run yet.
    """
    settings = _settings()
    position = _make_position(stop_price=95.0)
    pending_stop = _make_pending_stop(stop_price=95.0)
    runtime, saved_orders, saved_positions, audits = _fake_runtime(pending_stop, position)

    intent = _make_update_stop_intent(stop_price=104.79)
    cycle_result = CycleResult(as_of=_NOW, intents=[intent])

    report = execute_cycle_intents(
        settings=settings,
        runtime=runtime,
        broker=_BrokerThatMustNotBeCalled(),
        cycle_result=cycle_result,
        now=_NOW,
        session_type=SessionType.AFTER_HOURS,
    )

    assert report.updated_pending_stop_count == 1, (
        f"Expected 1 updated_pending, got {report.updated_pending_stop_count}"
    )
    assert len(saved_orders) == 1
    assert saved_orders[0].stop_price == pytest.approx(104.79)
    assert saved_orders[0].status == "pending_submit"
    assert saved_orders[0].broker_order_id is None

    assert len(saved_positions) == 1
    assert saved_positions[0].stop_price == pytest.approx(104.79)

    assert len(audits) == 1
    assert audits[0].event_type == "cycle_intent_executed"


def test_after_hours_submitted_stop_skipped_no_db_write():
    """
    After hours with a stop already at the broker (broker_order_id set):
    the executor must not update DB or call the broker. Alpaca does not
    allow stop order replacement after hours.
    """
    settings = _settings()
    position = _make_position(stop_price=95.0)
    submitted_stop = _make_submitted_stop(stop_price=95.0)
    runtime, saved_orders, saved_positions, audits = _fake_runtime(submitted_stop, position)

    intent = _make_update_stop_intent(stop_price=104.79)
    cycle_result = CycleResult(as_of=_NOW, intents=[intent])

    report = execute_cycle_intents(
        settings=settings,
        runtime=runtime,
        broker=_BrokerThatMustNotBeCalled(),
        cycle_result=cycle_result,
        now=_NOW,
        session_type=SessionType.AFTER_HOURS,
    )

    assert report.updated_pending_stop_count == 0
    assert report.replaced_stop_count == 0
    assert report.submitted_stop_count == 0
    assert saved_orders == []
    assert saved_positions == []
    assert audits == []


def test_after_hours_no_stop_order_skipped():
    """
    After hours with no active stop order: no DB write and no broker call.
    The morning cycle will submit a fresh stop via submit_stop_order.
    """
    settings = _settings()
    position = _make_position(stop_price=95.0)
    runtime, saved_orders, saved_positions, audits = _fake_runtime(None, position)

    intent = _make_update_stop_intent(stop_price=104.79)
    cycle_result = CycleResult(as_of=_NOW, intents=[intent])

    report = execute_cycle_intents(
        settings=settings,
        runtime=runtime,
        broker=_BrokerThatMustNotBeCalled(),
        cycle_result=cycle_result,
        now=_NOW,
        session_type=SessionType.AFTER_HOURS,
    )

    assert report.updated_pending_stop_count == 0
    assert saved_orders == []
    assert saved_positions == []
    assert audits == []


def test_pre_market_pending_stop_also_updated_db_only():
    """
    The db_only path applies equally to PRE_MARKET sessions — same gate.
    """
    settings = _settings()
    position = _make_position(stop_price=95.0)
    pending_stop = _make_pending_stop(stop_price=95.0)
    runtime, saved_orders, saved_positions, audits = _fake_runtime(pending_stop, position)

    intent = _make_update_stop_intent(stop_price=104.79)
    cycle_result = CycleResult(as_of=_NOW, intents=[intent])

    report = execute_cycle_intents(
        settings=settings,
        runtime=runtime,
        broker=_BrokerThatMustNotBeCalled(),
        cycle_result=cycle_result,
        now=_NOW,
        session_type=SessionType.PRE_MARKET,
    )

    assert report.updated_pending_stop_count == 1
    assert saved_orders[0].stop_price == pytest.approx(104.79)
```

- [ ] **Step 2: Run — tests 1 and 4 must fail; tests 2 and 3 are regression guards**

```bash
pytest tests/unit/test_executor_after_hours_stop.py -v
```

Expected outcome by test:
- `test_after_hours_pending_stop_updated_db_only_no_broker_call` → **FAIL** (asserts `updated_pending_stop_count == 1`; current `continue` gives 0)
- `test_pre_market_pending_stop_also_updated_db_only` → **FAIL** (same reason)
- `test_after_hours_submitted_stop_skipped_no_db_write` → PASS by coincidence (current `continue` already produces `saved_orders == []`, which is what the test asserts)
- `test_after_hours_no_stop_order_skipped` → PASS by coincidence (same reason)

Tests 2 and 3 are regression guards, not TDD tests. They verify via `_BrokerThatMustNotBeCalled` that the implementation never calls the broker during extended hours. They pass before and after the change — but would fail immediately if the `db_only=True` guards were accidentally removed.

- [ ] **Step 3: Implement the executor change**

**Change A:** In `src/alpaca_bot/runtime/cycle_intent_execution.py`, replace the extended-hours `continue` block (lines 123–132) with a `_db_only` path:

```
# Before (lines 123–132):
        if intent_type is CycleIntentType.UPDATE_STOP:
            if session_type is not None:
                from alpaca_bot.strategy.session import SessionType as _SessionType
                if session_type in (_SessionType.AFTER_HOURS, _SessionType.PRE_MARKET):
                    logger.debug(
                        "execute_cycle_intents: skipping UPDATE_STOP for %s during %s session",
                        symbol,
                        session_type,
                    )
                    continue
            if positions_by_symbol is None:
                with lock_ctx:
                    positions_by_symbol = _positions_by_symbol(runtime, settings)
            action = _execute_update_stop(
                settings=settings,
                runtime=runtime,
                broker=broker,
                symbol=symbol,
                stop_price=getattr(intent, "stop_price", None),
                intent_timestamp=getattr(intent, "timestamp", timestamp),
                position=positions_by_symbol.get((symbol, strategy_name)),
                now=timestamp,
                strategy_name=strategy_name,
                lock_ctx=lock_ctx,
                notifier=notifier,
            )

# After:
        if intent_type is CycleIntentType.UPDATE_STOP:
            _db_only = False
            if session_type is not None:
                from alpaca_bot.strategy.session import SessionType as _SessionType
                if session_type in (_SessionType.AFTER_HOURS, _SessionType.PRE_MARKET):
                    _db_only = True
            if positions_by_symbol is None:
                with lock_ctx:
                    positions_by_symbol = _positions_by_symbol(runtime, settings)
            action = _execute_update_stop(
                settings=settings,
                runtime=runtime,
                broker=broker,
                symbol=symbol,
                stop_price=getattr(intent, "stop_price", None),
                intent_timestamp=getattr(intent, "timestamp", timestamp),
                position=positions_by_symbol.get((symbol, strategy_name)),
                now=timestamp,
                strategy_name=strategy_name,
                lock_ctx=lock_ctx,
                notifier=notifier,
                db_only=_db_only,
            )
```

**Change B:** Add `db_only: bool = False` to `_execute_update_stop` signature and guard the broker-call branches. The function starts at line 196. Change the signature and the two broker branches:

```
# Before (line 209, end of signature):
    notifier: Notifier | None = None,
) -> str | None:

# After:
    notifier: Notifier | None = None,
    db_only: bool = False,
) -> str | None:
```

Inside the `try` block, at the two broker-calling branches (lines 225–247 "replaced" and 271–312 "submitted"), add `db_only` short-circuit returns:

```python
        if active_stop is not None and active_stop.broker_order_id:
            if db_only:
                return None  # can't replace broker stops after hours
            broker_order = broker.replace_order(
                ...
            )
            ...
            action = "replaced"
        elif active_stop is not None and not active_stop.broker_order_id:
            # The stop exists as a pending_submit but hasn't been dispatched yet.
            # Update its price in-place; dispatch_pending_orders will submit with the
            # correct price.
            updated_order = OrderRecord(...)
            action = "updated_pending"
        else:
            if db_only:
                return None  # no pending_submit stop exists; nothing to update
            client_order_id = _stop_client_order_id(...)
            ...
            action = "submitted"
```

The "updated_pending" branch (line 248–270) is unchanged — it runs for both `db_only=True` and `db_only=False`.

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/unit/test_executor_after_hours_stop.py -v
```

Expected: all four tests PASS.

Run the full suite to confirm no regressions:

```bash
pytest tests/unit/ -v
```

Expected: all tests PASS. Pay particular attention to `test_cycle_intent_execution_extended_hours.py` — those existing tests must still pass.

- [ ] **Step 5: Commit**

```bash
git add tests/unit/test_executor_after_hours_stop.py src/alpaca_bot/runtime/cycle_intent_execution.py
git commit -m "feat: DB-only UPDATE_STOP path for after-hours breakeven stop preloading

During extended-hours cycles, _execute_update_stop now accepts db_only=True.
When True: only the pending_submit branch executes (DB update without broker
call). Stops already at the broker are skipped; the first morning cycle will
replace them. Pending-submit stops carry the correct trail-adjusted price into
dispatch_pending_orders at market open.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```
