# Profitability Improvements Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a take-profit exit at N×R, compute expectancy in BacktestReport, and add a debounce to the trend-filter exit so a single bad day no longer triggers premature closure.

**Architecture:** Three independent features land in the same frozen-dataclass config pattern, pure engine, and replay runner. Profit target is detected in the replay runner (pre-engine, same as stop-hit) and in the engine (for live trading intent emission). Trend-filter debounce extends the existing exit guard in `strategy/breakout.py` without touching the entry-side `daily_trend_filter_passes`. Expectancy is a pure computation added to `report_from_records`.

**Tech Stack:** Python 3.12, pytest, existing DI pattern (fake callables, in-memory stores), no database migrations.

---

### Task 1: Settings — add three new env-var fields

**Files:**
- Modify: `src/alpaca_bot/config/__init__.py`
- Test: `tests/unit/test_settings.py` (existing file — add cases)

- [ ] **Step 1: Write the failing tests**

Open `tests/unit/test_settings.py` and add at the end:

```python
def test_profit_target_defaults():
    s = make_settings()
    assert s.enable_profit_target is False
    assert s.profit_target_r == 2.0
    assert s.trend_filter_exit_lookback_days == 1


def test_profit_target_r_invalid():
    with pytest.raises(ValueError, match="PROFIT_TARGET_R"):
        make_settings(PROFIT_TARGET_R="0")


def test_profit_target_r_negative():
    with pytest.raises(ValueError, match="PROFIT_TARGET_R"):
        make_settings(PROFIT_TARGET_R="-1.0")


def test_trend_filter_exit_lookback_days_invalid():
    with pytest.raises(ValueError, match="TREND_FILTER_EXIT_LOOKBACK_DAYS"):
        make_settings(TREND_FILTER_EXIT_LOOKBACK_DAYS="0")


def test_profit_target_enabled_from_env():
    s = make_settings(ENABLE_PROFIT_TARGET="true", PROFIT_TARGET_R="3.5")
    assert s.enable_profit_target is True
    assert s.profit_target_r == 3.5


def test_trend_filter_exit_lookback_days_from_env():
    s = make_settings(TREND_FILTER_EXIT_LOOKBACK_DAYS="3")
    assert s.trend_filter_exit_lookback_days == 3
```

- [ ] **Step 2: Run tests to verify they fail**

```
pytest tests/unit/test_settings.py::test_profit_target_defaults -v
```

Expected: FAIL — `Settings` has no `enable_profit_target` attribute.

- [ ] **Step 3: Add the three dataclass fields**

In `src/alpaca_bot/config/__init__.py`, after line 151 (`profit_trail_pct: float = 0.95`), insert:

```python
    enable_profit_target: bool = False
    profit_target_r: float = 2.0
    trend_filter_exit_lookback_days: int = 1
```

- [ ] **Step 4: Add from_env() parsing**

In the `from_env()` method, after the line `profit_trail_pct=float(values.get("PROFIT_TRAIL_PCT", "0.95")),` (around line 340), add:

```python
            enable_profit_target=_parse_bool(
                "ENABLE_PROFIT_TARGET", values.get("ENABLE_PROFIT_TARGET", "false")
            ),
            profit_target_r=float(values.get("PROFIT_TARGET_R", "2.0")),
            trend_filter_exit_lookback_days=int(
                values.get("TREND_FILTER_EXIT_LOOKBACK_DAYS", "1")
            ),
```

- [ ] **Step 5: Add validate() checks**

In `validate()`, after the check for `max_loss_per_trade_dollars` (after line 549, before confidence_floor check), add:

```python
        if self.profit_target_r <= 0:
            raise ValueError("PROFIT_TARGET_R must be > 0")
        if self.trend_filter_exit_lookback_days < 1:
            raise ValueError("TREND_FILTER_EXIT_LOOKBACK_DAYS must be >= 1")
```

- [ ] **Step 6: Run tests to verify they pass**

```
pytest tests/unit/test_settings.py -v -k "profit_target or trend_filter_exit_lookback"
```

Expected: all 7 new tests PASS.

- [ ] **Step 7: Run full test suite**

```
pytest
```

Expected: all existing tests still PASS.

- [ ] **Step 8: Commit**

```bash
git add src/alpaca_bot/config/__init__.py tests/unit/test_settings.py
git commit -m "feat: add ENABLE_PROFIT_TARGET, PROFIT_TARGET_R, TREND_FILTER_EXIT_LOOKBACK_DAYS settings"
```

---

### Task 2: IntentType — add PROFIT_TARGET_HIT enum value

**Files:**
- Modify: `src/alpaca_bot/domain/enums.py`

- [ ] **Step 1: Add the new value**

In `src/alpaca_bot/domain/enums.py`, after `EOD_EXIT = "eod_exit"`, add:

```python
    PROFIT_TARGET_HIT = "profit_target_hit"
```

The full file becomes:

```python
from enum import StrEnum


class IntentType(StrEnum):
    ENTRY_ORDER_PLACED = "entry_order_placed"
    ENTRY_FILLED = "entry_filled"
    ENTRY_EXPIRED = "entry_expired"
    STOP_UPDATED = "stop_updated"
    STOP_HIT = "stop_hit"
    EOD_EXIT = "eod_exit"
    PROFIT_TARGET_HIT = "profit_target_hit"
```

- [ ] **Step 2: Verify import works**

```
python -c "from alpaca_bot.domain.enums import IntentType; print(IntentType.PROFIT_TARGET_HIT)"
```

Expected output: `profit_target_hit`

- [ ] **Step 3: Run full test suite**

```
pytest
```

Expected: all tests PASS.

- [ ] **Step 4: Commit**

```bash
git add src/alpaca_bot/domain/enums.py
git commit -m "feat: add IntentType.PROFIT_TARGET_HIT enum value"
```

---

### Task 3: Trend-filter exit debounce — new function in breakout.py

**Files:**
- Modify: `src/alpaca_bot/strategy/breakout.py`
- Test: `tests/unit/test_engine_trend_filter_debounce.py` (new file)

The existing `daily_trend_filter_passes` is used for **entries** (breakout, momentum, ema_pullback, etc.) and must not change. A new `daily_trend_filter_exit_passes` function handles exit debouncing only.

Debounce math for `offset` in `range(n)`:
- `offset=0`: `window = daily_bars[-(sma_period+1):−1]` → 20 bars, `window[-1]` = most-recent completed close
- `offset=1`: `window = daily_bars[-(sma_period+2):−2]` → 20 bars, `window[-1]` = day before most-recent

For this to work, `len(daily_bars) >= sma_period + n` (not `sma_period + n + 1`); the partial current bar occupies index `-1`, so the earliest window endpoint for `offset=n-1` is `daily_bars[-(sma_period+n)]`.

- [ ] **Step 1: Write the failing tests in new file**

Create `tests/unit/test_engine_trend_filter_debounce.py`:

```python
from datetime import datetime, timezone

import pytest

from alpaca_bot.config import Settings
from alpaca_bot.domain.models import Bar
from alpaca_bot.strategy.breakout import daily_trend_filter_exit_passes


def _make_settings(**overrides) -> Settings:
    env = {
        "TRADING_MODE": "paper",
        "STRATEGY_VERSION": "v1",
        "DATABASE_URL": "postgresql://localhost/test",
        "SYMBOLS": "AAPL",
        "DAILY_SMA_PERIOD": "3",
    }
    env.update({k: str(v) for k, v in overrides.items()})
    return Settings.from_env(env)


def _bar(close: float) -> Bar:
    return Bar(
        symbol="AAPL",
        timestamp=datetime(2024, 1, 1, tzinfo=timezone.utc),
        open=close,
        high=close,
        low=close,
        close=close,
        volume=1000,
    )


def _bars_above_then_below(n_above: int, n_below: int, sma_period: int = 3) -> list[Bar]:
    """Build a bar list: sma_period bars at 110 (above sma), then n_above bars
    at 110 (still above), then n_below bars at 90 (below sma of ~110), then
    one partial bar (current, excluded from window)."""
    base = [_bar(110.0)] * (sma_period + n_above)
    below = [_bar(90.0)] * n_below
    partial = [_bar(95.0)]  # current incomplete bar, excluded by slice [-1]
    return base + below + partial


def test_lookback_1_single_day_below_exits():
    """With lookback=1, a single day below SMA triggers exit (current behaviour)."""
    settings = _make_settings(TREND_FILTER_EXIT_LOOKBACK_DAYS="1")
    bars = _bars_above_then_below(n_above=0, n_below=1)
    assert daily_trend_filter_exit_passes(bars, settings) is False


def test_lookback_1_above_sma_holds():
    settings = _make_settings(TREND_FILTER_EXIT_LOOKBACK_DAYS="1")
    bars = _bars_above_then_below(n_above=1, n_below=0)
    assert daily_trend_filter_exit_passes(bars, settings) is True


def test_lookback_2_one_day_below_holds():
    """With lookback=2, exactly one day below SMA should NOT exit."""
    settings = _make_settings(TREND_FILTER_EXIT_LOOKBACK_DAYS="2")
    bars = _bars_above_then_below(n_above=1, n_below=1)
    assert daily_trend_filter_exit_passes(bars, settings) is True


def test_lookback_2_two_consecutive_days_below_exits():
    """With lookback=2, two consecutive days below SMA should exit."""
    settings = _make_settings(TREND_FILTER_EXIT_LOOKBACK_DAYS="2")
    bars = _bars_above_then_below(n_above=0, n_below=2)
    assert daily_trend_filter_exit_passes(bars, settings) is False


def test_lookback_2_gap_above_holds():
    """With lookback=2: day1 below, day2 above, day3 below → one above in window → hold."""
    settings = _make_settings(TREND_FILTER_EXIT_LOOKBACK_DAYS="2")
    # sma_period=3, three bars at 110, then above/below/above pattern, then partial
    base = [_bar(110.0)] * 3
    mixed = [_bar(90.0), _bar(110.0)]  # two most-recent completed bars: day2=90, day1=110
    partial = [_bar(95.0)]
    bars = base + mixed + partial
    assert daily_trend_filter_exit_passes(bars, settings) is True


def test_insufficient_history_holds():
    """Too few bars → hold (don't exit)."""
    settings = _make_settings(TREND_FILTER_EXIT_LOOKBACK_DAYS="2")
    # Need sma_period + n = 3 + 2 = 5 bars (plus partial = 6 total)
    bars = [_bar(90.0)] * 4  # only 4 — not enough
    assert daily_trend_filter_exit_passes(bars, settings) is True
```

- [ ] **Step 2: Run tests to verify they fail**

```
pytest tests/unit/test_engine_trend_filter_debounce.py -v
```

Expected: FAIL — `daily_trend_filter_exit_passes` does not exist.

- [ ] **Step 3: Implement the function in breakout.py**

Add after `daily_downtrend_filter_passes` (after line 47 in `strategy/breakout.py`):

```python
def daily_trend_filter_exit_passes(daily_bars: Sequence[Bar], settings: Settings) -> bool:
    """Returns False when the last TREND_FILTER_EXIT_LOOKBACK_DAYS closes are ALL
    below the SMA — meaning an exit is warranted. Returns True (hold) otherwise.

    Uses offset slicing to check N completed bars counting backward from the most
    recent completed bar (daily_bars[-2]), skipping the partial current bar at [-1].
    """
    n = settings.trend_filter_exit_lookback_days
    if len(daily_bars) < settings.daily_sma_period + n:
        return True  # insufficient history → don't exit
    for offset in range(n):
        window = daily_bars[-(settings.daily_sma_period + 1 + offset) : -(1 + offset)]
        sma = sum(b.close for b in window) / len(window)
        if window[-1].close > sma:
            return True  # at least one completed bar above SMA → hold
    return False  # all N consecutive completed bars below SMA → exit warranted
```

- [ ] **Step 4: Run tests to verify they pass**

```
pytest tests/unit/test_engine_trend_filter_debounce.py -v
```

Expected: all 6 tests PASS.

- [ ] **Step 5: Run full suite**

```
pytest
```

Expected: all existing tests still PASS.

- [ ] **Step 6: Commit**

```bash
git add src/alpaca_bot/strategy/breakout.py tests/unit/test_engine_trend_filter_debounce.py
git commit -m "feat: add daily_trend_filter_exit_passes with N-day debounce"
```

---

### Task 4: Engine — profit target EXIT intent + fix trend-filter call

**Files:**
- Modify: `src/alpaca_bot/core/engine.py`
- Test: `tests/unit/test_engine_profit_target.py` (new file)

The engine's per-position loop structure (simplified):
1. EOD flatten guard → `continue`
2. Stale bar guard → `continue`
3. Extended hours exit guard → `continue`
4. **[INSERT HERE] Profit target check → `continue`**
5. Trend filter exit check (currently calls wrong function — fix to `daily_trend_filter_exit_passes`)
6. VWAP breakdown exit check
7. Trailing stop / breakeven logic

- [ ] **Step 1: Write failing tests in new file**

Create `tests/unit/test_engine_profit_target.py`:

```python
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from alpaca_bot.config import Settings
from alpaca_bot.core.engine import evaluate_cycle
from alpaca_bot.domain.enums import CycleIntentType
from alpaca_bot.domain.models import Bar, OpenPosition


def _make_settings(**overrides) -> Settings:
    env = {
        "TRADING_MODE": "paper",
        "STRATEGY_VERSION": "v1",
        "DATABASE_URL": "postgresql://localhost/test",
        "SYMBOLS": "AAPL",
        "ENABLE_PROFIT_TARGET": "false",
        "PROFIT_TARGET_R": "2.0",
    }
    env.update({k: str(v) for k, v in overrides.items()})
    return Settings.from_env(env)


def _make_bar(symbol: str, high: float, low: float = 100.0, close: float = 101.0) -> Bar:
    ts = datetime(2024, 3, 15, 14, 0, tzinfo=timezone.utc)
    return Bar(
        symbol=symbol, timestamp=ts,
        open=100.0, high=high, low=low, close=close, volume=10000
    )


def _make_daily_bars(n: int = 22) -> list[Bar]:
    ts = datetime(2024, 3, 14, 20, 0, tzinfo=timezone.utc)
    return [
        Bar(symbol="AAPL", timestamp=ts, open=100.0, high=110.0, low=90.0, close=100.0, volume=1000)
        for _ in range(n)
    ]


def _make_position(entry_price: float, initial_stop_price: float) -> OpenPosition:
    return OpenPosition(
        symbol="AAPL",
        entry_timestamp=datetime(2024, 3, 15, 10, 30, tzinfo=timezone.utc),
        entry_price=entry_price,
        quantity=10,
        entry_level=initial_stop_price + 0.01,
        initial_stop_price=initial_stop_price,
        stop_price=initial_stop_price,
        highest_price=entry_price,
        strategy_name="breakout",
    )


def _run_cycle(settings, bar, position):
    return evaluate_cycle(
        settings=settings,
        now=bar.timestamp,
        equity=100_000.0,
        intraday_bars_by_symbol={"AAPL": [bar]},
        daily_bars_by_symbol={"AAPL": _make_daily_bars()},
        open_positions=[position],
        working_order_symbols=set(),
        traded_symbols_today=set(),
        entries_disabled=False,
        signal_evaluator=MagicMock(return_value=None),
    )


def test_profit_target_hit_emits_exit():
    """Bar.high >= target_price → EXIT intent with reason='profit_target'."""
    # entry=100, initial_stop=95 → risk_per_share=5, target_price=100+2*5=110
    settings = _make_settings(ENABLE_PROFIT_TARGET="true", PROFIT_TARGET_R="2.0")
    position = _make_position(entry_price=100.0, initial_stop_price=95.0)
    bar = _make_bar("AAPL", high=110.0)
    result = _run_cycle(settings, bar, position)
    exits = [i for i in result.intents if i.intent_type == CycleIntentType.EXIT]
    assert len(exits) == 1
    assert exits[0].reason == "profit_target"
    assert exits[0].symbol == "AAPL"


def test_profit_target_not_hit_no_exit():
    """Bar.high < target_price → no EXIT intent."""
    settings = _make_settings(ENABLE_PROFIT_TARGET="true", PROFIT_TARGET_R="2.0")
    position = _make_position(entry_price=100.0, initial_stop_price=95.0)
    bar = _make_bar("AAPL", high=109.99)
    result = _run_cycle(settings, bar, position)
    exits = [i for i in result.intents if i.intent_type == CycleIntentType.EXIT]
    assert len(exits) == 0


def test_profit_target_disabled_no_exit():
    """ENABLE_PROFIT_TARGET=false → no EXIT even when bar.high >= target."""
    settings = _make_settings(ENABLE_PROFIT_TARGET="false")
    position = _make_position(entry_price=100.0, initial_stop_price=95.0)
    bar = _make_bar("AAPL", high=115.0)
    result = _run_cycle(settings, bar, position)
    exits = [i for i in result.intents if i.intent_type == CycleIntentType.EXIT]
    assert len(exits) == 0


def test_profit_target_hit_no_stop_update_emitted():
    """When profit target fires, no UPDATE_STOP intent for same symbol."""
    settings = _make_settings(ENABLE_PROFIT_TARGET="true", PROFIT_TARGET_R="2.0")
    position = _make_position(entry_price=100.0, initial_stop_price=95.0)
    bar = _make_bar("AAPL", high=110.0)
    result = _run_cycle(settings, bar, position)
    stop_updates = [i for i in result.intents if i.intent_type == CycleIntentType.UPDATE_STOP]
    assert len(stop_updates) == 0
```

- [ ] **Step 2: Run tests to verify they fail**

```
pytest tests/unit/test_engine_profit_target.py -v
```

Expected: FAIL — profit target logic not yet in engine.

- [ ] **Step 3: Add import in engine.py**

In `src/alpaca_bot/core/engine.py`, find the import of `daily_trend_filter_passes` from `strategy.breakout` and add `daily_trend_filter_exit_passes` to it:

```python
from alpaca_bot.strategy.breakout import (
    daily_trend_filter_exit_passes,
    daily_trend_filter_passes,
)
```

- [ ] **Step 4: Insert profit target check in the position loop**

In `src/alpaca_bot/core/engine.py`, find the block that ends the extended-hours guard (the `continue` after appending the extended-hours stop breach exit). After that block's `continue` statement (line 171), insert before the `position_age_s` line:

```python
        if settings.enable_profit_target:
            target_price = round(
                position.entry_price + settings.profit_target_r * position.risk_per_share, 2
            )
            if latest_bar.high >= target_price:
                intents.append(
                    CycleIntent(
                        intent_type=CycleIntentType.EXIT,
                        symbol=position.symbol,
                        timestamp=latest_bar.timestamp,
                        reason="profit_target",
                        strategy_name=strategy_name,
                    )
                )
                emitted_exit_symbols.add(position.symbol)
                continue
```

- [ ] **Step 5: Fix the trend-filter exit call**

In the same file, find the line (currently ~line 185):

```python
                    if not daily_trend_filter_passes(daily_bars_pos, settings):
```

Change it to:

```python
                    if not daily_trend_filter_exit_passes(daily_bars_pos, settings):
```

Also update the length guard on the line above it (currently `settings.daily_sma_period + 1`) to:

```python
            if len(daily_bars_pos) >= settings.daily_sma_period + settings.trend_filter_exit_lookback_days:
```

- [ ] **Step 6: Run profit target tests**

```
pytest tests/unit/test_engine_profit_target.py -v
```

Expected: all 4 tests PASS.

- [ ] **Step 7: Run full suite**

```
pytest
```

Expected: all tests PASS.

- [ ] **Step 8: Commit**

```bash
git add src/alpaca_bot/core/engine.py tests/unit/test_engine_profit_target.py
git commit -m "feat: profit target EXIT intent in engine; fix trend-filter exit to use debounce function"
```

---

### Task 5: Replay runner — profit target fill simulation

**Files:**
- Modify: `src/alpaca_bot/replay/runner.py`
- Test: `tests/unit/test_replay_profit_target.py` (new file)

Stop hits are detected *before* `evaluate_cycle()` in the runner because the broker handles stops as working orders — the replay mirrors that. Profit targets need the same treatment: if the bar's high crosses the target price, fill at `target_price` and skip the rest of bar processing.

Priority rule: if both stop and target are hit in the same bar (`bar.low <= stop_price` AND `bar.high >= target_price`), stop takes priority (conservative). This is guaranteed by calling `_process_stop_hit()` before `_process_profit_target_hit()`.

- [ ] **Step 1: Write failing tests**

Create `tests/unit/test_replay_profit_target.py`:

```python
from datetime import datetime, timezone

import pytest

from alpaca_bot.config import Settings
from alpaca_bot.domain.enums import IntentType
from alpaca_bot.domain.models import Bar
from alpaca_bot.domain.models import ReplayScenario
from alpaca_bot.replay.runner import ReplayRunner


def _make_settings(**overrides) -> Settings:
    env = {
        "TRADING_MODE": "paper",
        "STRATEGY_VERSION": "v1",
        "DATABASE_URL": "postgresql://localhost/test",
        "SYMBOLS": "AAPL",
        "ENABLE_PROFIT_TARGET": "true",
        "PROFIT_TARGET_R": "2.0",
        "ENTRY_WINDOW_START": "10:00",
        "ENTRY_WINDOW_END": "15:30",
        "FLATTEN_TIME": "15:45",
    }
    env.update({k: str(v) for k, v in overrides.items()})
    return Settings.from_env(env)


def _bar(high: float, low: float, close: float, minute: int = 30) -> Bar:
    ts = datetime(2024, 3, 15, 10, minute, tzinfo=timezone.utc)
    return Bar(
        symbol="AAPL", timestamp=ts,
        open=100.0, high=high, low=low, close=close, volume=10000,
    )


def _daily_bar() -> Bar:
    ts = datetime(2024, 3, 14, 20, 0, tzinfo=timezone.utc)
    return Bar(
        symbol="AAPL", timestamp=ts,
        open=100.0, high=110.0, low=90.0, close=100.0, volume=1000,
    )


def _run_with_prefilled_position(
    settings: Settings, bars: list[Bar]
) -> list:
    """Build a scenario where AAPL is already held (entry pre-seeded into state).

    We inject the position by pre-seeding state via a minimal signal approach:
    use ReplayRunner internal _process_stop_hit / _process_profit_target_hit by
    constructing the runner and calling run() with a scenario that has an entry
    filled on bar 0.
    """
    # Build a scenario with a fake fill event. The easiest way is to run the
    # full runner with a scenario that fires an ENTRY on bar 0 and then
    # experiences the target on bar 1.
    runner = ReplayRunner(settings=settings)
    scenario = ReplayScenario(
        symbol="AAPL",
        starting_equity=100_000.0,
        daily_bars=[_daily_bar()] * 25,
        intraday_bars=bars,
    )
    result = runner.run(scenario)
    return result.events


def test_profit_target_hit_generates_event():
    """bar.high >= target → PROFIT_TARGET_HIT event at target_price."""
    # entry=100, stop=95 → risk=5, target=100+2*5=110
    # We need an ENTRY_FILLED then a bar that hits 110+
    # Bar 0: triggers breakout entry (high breaks above lookback) — runner fills at open
    # Bar 1: high=111.0 → should hit profit target
    settings = _make_settings(ENABLE_PROFIT_TARGET="true", PROFIT_TARGET_R="2.0")
    # This integration approach verifies the event type is emitted.
    # See full integration test for end-to-end; here we test the method directly.
    from alpaca_bot.replay.runner import ReplayRunner, ReplayState
    from alpaca_bot.domain.models import ReplayEvent, OpenPosition

    runner = ReplayRunner(settings=settings)
    position = OpenPosition(
        symbol="AAPL",
        entry_timestamp=datetime(2024, 3, 15, 10, 0, tzinfo=timezone.utc),
        entry_price=100.0,
        quantity=10,
        entry_level=95.01,
        initial_stop_price=95.0,
        stop_price=95.0,
        highest_price=100.0,
        strategy_name="breakout",
    )
    state = ReplayState(equity=100_000.0)
    state.position = position
    events: list[ReplayEvent] = []
    bar = _bar(high=111.0, low=101.0, close=110.0)
    hit = runner._process_profit_target_hit(bar=bar, state=state, events=events)
    assert hit is True
    assert len(events) == 1
    assert events[0].event_type == IntentType.PROFIT_TARGET_HIT
    assert events[0].details["exit_price"] == 110.0  # target_price=100+2*5=110
    assert state.position is None


def test_profit_target_not_hit():
    settings = _make_settings(ENABLE_PROFIT_TARGET="true", PROFIT_TARGET_R="2.0")
    from alpaca_bot.replay.runner import ReplayRunner, ReplayState
    from alpaca_bot.domain.models import ReplayEvent, OpenPosition

    runner = ReplayRunner(settings=settings)
    position = OpenPosition(
        symbol="AAPL",
        entry_timestamp=datetime(2024, 3, 15, 10, 0, tzinfo=timezone.utc),
        entry_price=100.0,
        quantity=10,
        entry_level=95.01,
        initial_stop_price=95.0,
        stop_price=95.0,
        highest_price=100.0,
        strategy_name="breakout",
    )
    state = ReplayState(equity=100_000.0)
    state.position = position
    events: list[ReplayEvent] = []
    bar = _bar(high=109.99, low=101.0, close=105.0)
    hit = runner._process_profit_target_hit(bar=bar, state=state, events=events)
    assert hit is False
    assert len(events) == 0
    assert state.position is not None


def test_profit_target_disabled_no_event():
    settings = _make_settings(ENABLE_PROFIT_TARGET="false")
    from alpaca_bot.replay.runner import ReplayRunner, ReplayState
    from alpaca_bot.domain.models import ReplayEvent, OpenPosition

    runner = ReplayRunner(settings=settings)
    position = OpenPosition(
        symbol="AAPL",
        entry_timestamp=datetime(2024, 3, 15, 10, 0, tzinfo=timezone.utc),
        entry_price=100.0,
        quantity=10,
        entry_level=95.01,
        initial_stop_price=95.0,
        stop_price=95.0,
        highest_price=100.0,
        strategy_name="breakout",
    )
    state = ReplayState(equity=100_000.0)
    state.position = position
    events: list[ReplayEvent] = []
    bar = _bar(high=115.0, low=101.0, close=114.0)
    hit = runner._process_profit_target_hit(bar=bar, state=state, events=events)
    assert hit is False


def test_stop_takes_priority_over_profit_target():
    """When both stop and target are hit in same bar, stop fires first."""
    settings = _make_settings(ENABLE_PROFIT_TARGET="true", PROFIT_TARGET_R="2.0")
    from alpaca_bot.replay.runner import ReplayRunner, ReplayState
    from alpaca_bot.domain.models import ReplayEvent, OpenPosition

    runner = ReplayRunner(settings=settings)
    position = OpenPosition(
        symbol="AAPL",
        entry_timestamp=datetime(2024, 3, 15, 10, 0, tzinfo=timezone.utc),
        entry_price=100.0,
        quantity=10,
        entry_level=95.01,
        initial_stop_price=95.0,
        stop_price=95.0,
        highest_price=100.0,
        strategy_name="breakout",
    )
    state = ReplayState(equity=100_000.0)
    state.position = position
    events: list[ReplayEvent] = []
    # Bar spans from 94 (below stop=95) to 115 (above target=110)
    bar = _bar(high=115.0, low=94.0, close=110.0)
    stop_hit = runner._process_stop_hit(bar=bar, state=state, events=events)
    assert stop_hit is True
    assert events[0].event_type == IntentType.STOP_HIT
    # Now position is gone — profit target check should return False
    profit_hit = runner._process_profit_target_hit(bar=bar, state=state, events=events)
    assert profit_hit is False
    assert len(events) == 1  # only the stop event
```

- [ ] **Step 2: Run tests to verify they fail**

```
pytest tests/unit/test_replay_profit_target.py -v
```

Expected: FAIL — `_process_profit_target_hit` does not exist.

- [ ] **Step 3: Add the method to ReplayRunner**

In `src/alpaca_bot/replay/runner.py`, add after the `_process_stop_hit` method (after line 262):

```python
    def _process_profit_target_hit(
        self,
        *,
        bar: Bar,
        state: ReplayState,
        events: list[ReplayEvent],
    ) -> bool:
        """Check if the current bar's high crosses the take-profit price.

        Returns True if target was hit (caller should skip remaining processing
        for this bar), False otherwise. Stop takes priority — call _process_stop_hit
        first so position is already None if stop also fired this bar.
        """
        position = state.position
        if position is None or not self.settings.enable_profit_target:
            return False

        target_price = round(
            position.entry_price + self.settings.profit_target_r * position.risk_per_share, 2
        )
        if bar.high >= target_price:
            events.append(
                ReplayEvent(
                    event_type=IntentType.PROFIT_TARGET_HIT,
                    symbol=position.symbol,
                    timestamp=bar.timestamp,
                    details={"exit_price": target_price},
                )
            )
            state.equity += (target_price - position.entry_price) * position.quantity
            state.traded_symbols.add(
                (position.symbol, session_day(bar.timestamp, self.settings))
            )
            state.position = None
            return True

        return False
```

- [ ] **Step 4: Wire the call into the run() loop**

In the `run()` method of `ReplayRunner`, after the stop-hit block:

```python
            if self._process_stop_hit(bar=bar, state=state, events=events):
                continue
```

Add immediately after:

```python
            if self._process_profit_target_hit(bar=bar, state=state, events=events):
                continue
```

- [ ] **Step 5: Add IntentType import if not already present**

Verify that `IntentType` is imported in `runner.py`. If the file imports from `alpaca_bot.domain.enums`, add `PROFIT_TARGET_HIT` usage will resolve via `IntentType.PROFIT_TARGET_HIT` from the existing import.

- [ ] **Step 6: Run tests**

```
pytest tests/unit/test_replay_profit_target.py -v
```

Expected: all 4 tests PASS.

- [ ] **Step 7: Run full suite**

```
pytest
```

Expected: all tests PASS.

- [ ] **Step 8: Commit**

```bash
git add src/alpaca_bot/replay/runner.py tests/unit/test_replay_profit_target.py
git commit -m "feat: profit target fill simulation in replay runner"
```

---

### Task 6: BacktestReport — expectancy_pct and profit_target counters

**Files:**
- Modify: `src/alpaca_bot/replay/report.py`
- Test: `tests/unit/test_report_expectancy.py` (new file)

- [ ] **Step 1: Write failing tests**

Create `tests/unit/test_report_expectancy.py`:

```python
from datetime import datetime, timezone

import pytest

from alpaca_bot.replay.report import BacktestReport, ReplayTradeRecord, report_from_records


def _trade(exit_reason: str, pnl: float, entry: float = 100.0) -> ReplayTradeRecord:
    qty = 10
    exit_price = entry + pnl / qty
    return_pct = (exit_price - entry) / entry
    return ReplayTradeRecord(
        symbol="AAPL",
        entry_price=entry,
        exit_price=round(exit_price, 4),
        quantity=qty,
        entry_time=datetime(2024, 3, 15, 10, 30, tzinfo=timezone.utc),
        exit_time=datetime(2024, 3, 15, 14, 0, tzinfo=timezone.utc),
        exit_reason=exit_reason,
        pnl=pnl,
        return_pct=round(return_pct, 6),
    )


def test_expectancy_zero_trades():
    report = report_from_records([], starting_equity=100_000.0)
    assert report.expectancy_pct is None


def test_expectancy_100_percent_win_rate():
    """Expectancy = avg_win_return_pct when win_rate=1.0."""
    trades = [_trade("stop", pnl=50.0), _trade("stop", pnl=100.0)]
    report = report_from_records(trades, starting_equity=100_000.0)
    assert report.win_rate == 1.0
    assert report.expectancy_pct is not None
    assert abs(report.expectancy_pct - report.avg_win_return_pct) < 1e-9


def test_expectancy_zero_percent_win_rate():
    """Expectancy = avg_loss_return_pct when win_rate=0.0."""
    trades = [_trade("stop", pnl=-50.0), _trade("eod", pnl=-30.0)]
    report = report_from_records(trades, starting_equity=100_000.0)
    assert report.win_rate == 0.0
    assert report.expectancy_pct is not None
    assert abs(report.expectancy_pct - report.avg_loss_return_pct) < 1e-9


def test_expectancy_50_50():
    """Expectancy = 0.5 * avg_win + 0.5 * avg_loss."""
    win = _trade("stop", pnl=100.0)    # return_pct = 0.10
    loss = _trade("stop", pnl=-50.0)   # return_pct = -0.05
    report = report_from_records([win, loss], starting_equity=100_000.0)
    expected = 0.5 * report.avg_win_return_pct + 0.5 * report.avg_loss_return_pct
    assert abs(report.expectancy_pct - expected) < 1e-9


def test_profit_target_wins_and_losses_counted():
    trades = [
        _trade("profit_target", pnl=200.0),
        _trade("profit_target", pnl=150.0),
        _trade("profit_target", pnl=-10.0),
        _trade("stop", pnl=-50.0),
    ]
    report = report_from_records(trades, starting_equity=100_000.0)
    assert report.profit_target_wins == 2
    assert report.profit_target_losses == 1


def test_profit_target_counters_zero_when_none():
    trades = [_trade("stop", pnl=100.0), _trade("eod", pnl=-20.0)]
    report = report_from_records(trades, starting_equity=100_000.0)
    assert report.profit_target_wins == 0
    assert report.profit_target_losses == 0
```

- [ ] **Step 2: Run tests to verify they fail**

```
pytest tests/unit/test_report_expectancy.py -v
```

Expected: FAIL — `expectancy_pct`, `profit_target_wins`, `profit_target_losses` not in `BacktestReport`.

- [ ] **Step 3: Add fields to BacktestReport dataclass**

In `src/alpaca_bot/replay/report.py`, after the `strategy_name: str = "breakout"` field (line 43), add:

```python
    profit_target_wins: int = 0
    profit_target_losses: int = 0
    expectancy_pct: float | None = None
```

- [ ] **Step 4: Handle PROFIT_TARGET_HIT in _extract_trades()**

In `_extract_trades()`, change line 123:

```python
        elif event.event_type in (IntentType.STOP_HIT, IntentType.EOD_EXIT):
```

To:

```python
        elif event.event_type in (IntentType.STOP_HIT, IntentType.EOD_EXIT, IntentType.PROFIT_TARGET_HIT):
```

And change the `exit_reason` assignment line 132:

```python
            exit_reason = "stop" if event.event_type == IntentType.STOP_HIT else "eod"
```

To:

```python
            if event.event_type == IntentType.STOP_HIT:
                exit_reason = "stop"
            elif event.event_type == IntentType.PROFIT_TARGET_HIT:
                exit_reason = "profit_target"
            else:
                exit_reason = "eod"
```

- [ ] **Step 5: Compute new stats in report_from_records()**

In `report_from_records()`, after the existing `stop_wins`, `stop_losses`, `eod_wins`, `eod_losses` lines (after line 86), add:

```python
    profit_target_wins = sum(
        1 for t in trades if t.exit_reason == "profit_target" and t.pnl > 0
    )
    profit_target_losses = sum(
        1 for t in trades if t.exit_reason == "profit_target" and t.pnl <= 0
    )
    expectancy_pct: float | None = None
    if (
        win_rate is not None
        and avg_win_return_pct is not None
        and avg_loss_return_pct is not None
    ):
        expectancy_pct = (
            win_rate * avg_win_return_pct + (1 - win_rate) * avg_loss_return_pct
        )
```

Note: `avg_win_return_pct` and `avg_loss_return_pct` are computed by `_compute_avg_win_loss_return(trades)` on line 89. To reference them at this point, the block must be inserted *after* line 89. Move the `profit_target_wins/losses/expectancy_pct` computation to just before the `return BacktestReport(...)` call, after `max_consecutive_losses, max_consecutive_wins = ...`.

- [ ] **Step 6: Pass the new fields to BacktestReport constructor**

In the `return BacktestReport(...)` call (starting around line 92), add the three new keyword arguments:

```python
        profit_target_wins=profit_target_wins,
        profit_target_losses=profit_target_losses,
        expectancy_pct=expectancy_pct,
```

- [ ] **Step 7: Run tests**

```
pytest tests/unit/test_report_expectancy.py -v
```

Expected: all 6 tests PASS.

- [ ] **Step 8: Run full suite**

```
pytest
```

Expected: all tests PASS.

- [ ] **Step 9: Commit**

```bash
git add src/alpaca_bot/replay/report.py tests/unit/test_report_expectancy.py
git commit -m "feat: add expectancy_pct and profit_target_wins/losses to BacktestReport"
```

---

### Task 7: session_eval_cli — display expectancy and profit_target stats

**Files:**
- Modify: `src/alpaca_bot/admin/session_eval_cli.py`

Note: Live `OrderRecord` has no `reason` field. `_row_to_trade_record()` maps `intent_type=="stop"` → `"stop"`, everything else → `"eod"`. Profit-target tracking is only reliable in replay. In the live session eval, `profit_target_wins` and `profit_target_losses` will always be 0 — this is expected and acceptable.

- [ ] **Step 1: Update _print_session_report()**

In `src/alpaca_bot/admin/session_eval_cli.py`, find `_print_session_report()`. After the exit breakdown block (after line 131 `print(f"   EOD wins: ...")`), add:

```python
    exp_str = (
        f"{report.expectancy_pct:+.3%}" if report.expectancy_pct is not None else "—"
    )
    print(f" Expectancy: {exp_str}")
    if report.profit_target_wins or report.profit_target_losses:
        print(
            f"   TP wins:   {report.profit_target_wins:3d}   "
            f"TP losses:   {report.profit_target_losses:3d}"
        )
```

The `profit_target_wins/losses` block is shown only when non-zero — in live session eval they will always be 0, so the line won't appear and won't confuse operators.

- [ ] **Step 2: Verify the print output**

```python
python -c "
from alpaca_bot.replay.report import BacktestReport
from alpaca_bot.admin.session_eval_cli import _print_session_report
from datetime import date
r = BacktestReport(
    trades=(), total_trades=0, winning_trades=0, losing_trades=0,
    win_rate=None, mean_return_pct=None, max_drawdown_pct=None,
    expectancy_pct=0.0034,
)
_print_session_report(r, eval_date=date.today(), trading_mode='paper', strategy_version='v1')
"
```

Expected: output includes `Expectancy: +0.340%`.

- [ ] **Step 3: Run full suite**

```
pytest
```

Expected: all tests PASS.

- [ ] **Step 4: Commit**

```bash
git add src/alpaca_bot/admin/session_eval_cli.py
git commit -m "feat: display expectancy and profit_target counts in session eval CLI"
```

---

### Task 8: replay/cli.py — add new fields to JSON and compare output

**Files:**
- Modify: `src/alpaca_bot/replay/cli.py`

- [ ] **Step 1: Update _report_to_dict()**

In `src/alpaca_bot/replay/cli.py`, in `_report_to_dict()`, add after `"max_consecutive_wins": report.max_consecutive_wins,`:

```python
        "expectancy_pct": report.expectancy_pct,
        "profit_target_wins": report.profit_target_wins,
        "profit_target_losses": report.profit_target_losses,
```

- [ ] **Step 2: Update _compare_row()**

In `_compare_row()`, add after `"max_consecutive_wins": report.max_consecutive_wins,`:

```python
        "expectancy_pct": report.expectancy_pct,
        "profit_target_wins": report.profit_target_wins,
        "profit_target_losses": report.profit_target_losses,
```

- [ ] **Step 3: Update _format_compare_csv() fieldnames**

In `_format_compare_csv()`, add to the `fieldnames` list after `"max_consecutive_wins"`:

```python
        "expectancy_pct", "profit_target_wins", "profit_target_losses",
```

- [ ] **Step 4: Run full suite**

```
pytest
```

Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/alpaca_bot/replay/cli.py
git commit -m "feat: add expectancy_pct and profit_target fields to replay CLI JSON/CSV output"
```

---

## Self-Review Checklist

### Spec coverage

| Spec requirement | Task covering it |
|---|---|
| `ENABLE_PROFIT_TARGET` + `PROFIT_TARGET_R` settings | Task 1 |
| `TREND_FILTER_EXIT_LOOKBACK_DAYS` setting | Task 1 |
| `IntentType.PROFIT_TARGET_HIT` | Task 2 |
| `daily_trend_filter_exit_passes()` debounce function | Task 3 |
| Engine: profit target EXIT intent after extended-hours guard | Task 4 |
| Engine: fix trend filter exit to use new debounce function | Task 4 |
| Replay runner: `_process_profit_target_hit()` before evaluate_cycle | Task 5 |
| `BacktestReport.expectancy_pct` | Task 6 |
| `BacktestReport.profit_target_wins/losses` | Task 6 |
| `_extract_trades()` handles `PROFIT_TARGET_HIT` → `exit_reason="profit_target"` | Task 6 |
| session_eval_cli displays expectancy | Task 7 |
| replay/cli JSON + CSV includes new fields | Task 8 |

All spec requirements covered. No gaps.

### Placeholder scan

No TBD, TODO, or incomplete sections. All code blocks are complete.

### Type consistency

- `daily_trend_filter_exit_passes` used identically in Task 3 (definition) and Task 4 (engine call).
- `IntentType.PROFIT_TARGET_HIT` referenced identically in Tasks 2, 5, and 6.
- `profit_target_wins`, `profit_target_losses`, `expectancy_pct` field names used identically in Task 6 (definition), Task 7 (display), Task 8 (CLI output).
- `exit_reason="profit_target"` string used identically in Task 4 (engine), Task 6 (report counters and _extract_trades).
