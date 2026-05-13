# Trading System Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix four issues: skip shorts in `_apply_highest_price_updates`, add two missing engine tests, add observability logging for floor-gated strategies, add ATR fallback debug logging.

**Architecture:** All changes are isolated to existing files. No new files, no schema changes, no audit events. Fixes #1 and #2 are correctness improvements; #3 and #4 are observability additions (debug-level logging only). ATR logging lives in `atr.py`, not `engine.py`, because `evaluate_cycle()` must remain a pure function (CLAUDE.md constraint).

**Tech Stack:** Python 3.12, pytest, logging stdlib

---

## File Map

| File | What changes |
|---|---|
| `src/alpaca_bot/runtime/supervisor.py` | Add short-skip guard to `_apply_highest_price_updates` |
| `src/alpaca_bot/risk/weighting.py` | Add `import logging` + debug log when strategy gets `sharpe=0.0` due to `min_trades` |
| `src/alpaca_bot/risk/atr.py` | Add `import logging` + debug log when ATR returns None (insufficient bars) |
| `tests/unit/test_supervisor_highest_price.py` | Add `test_apply_highest_price_updates_skips_short_positions` |
| `tests/unit/test_cycle_engine.py` | Add `test_short_breakeven_uses_tracked_lowest_price` and `test_short_trailing_stop_atr_unavailable_falls_back_to_bar_high` |

---

## Task 1: Add short guard to `_apply_highest_price_updates` + test

**Files:**
- Modify: `tests/unit/test_supervisor_highest_price.py`
- Modify: `src/alpaca_bot/runtime/supervisor.py:1358`

### Step 1.1: Write the failing test

Append to `tests/unit/test_supervisor_highest_price.py`. The existing `_make_supervisor` factory and `_RecordingPositionStore` are already in that file — add only the new test function at the bottom.

```python
def test_apply_highest_price_updates_skips_short_positions():
    """Short positions (qty < 0) must be passed through unchanged — highest_price is only for longs."""
    settings = _make_settings()
    pstore = _RecordingPositionStore()
    supervisor = _make_supervisor(settings, pstore)

    # Short: qty=-100, bar.high=5.20 > highest_price=5.00 — without the guard this WOULD
    # trigger an update, writing a new highest_price to the DB for a short position.
    position = OpenPosition(
        symbol="AAPL",
        entry_timestamp=datetime(2026, 5, 1, 10, 0, tzinfo=timezone.utc),
        entry_price=5.00,
        quantity=-100.0,
        entry_level=5.03,
        initial_stop_price=5.03,
        stop_price=5.03,
        trailing_active=False,
        highest_price=5.00,
        strategy_name="bear_breakdown",
    )
    bars = {"AAPL": [_make_bar(high=5.20)]}  # bar.high > highest_price — would trigger without guard

    result = supervisor._apply_highest_price_updates([position], bars)

    assert result[0].highest_price == 5.00, "Short position highest_price must not be updated"
    assert pstore.update_calls == [], "No DB write should occur for short positions"
```

### Step 1.2: Run the test to confirm it fails

```bash
cd /workspace/alpaca_bot && pytest tests/unit/test_supervisor_highest_price.py::test_apply_highest_price_updates_skips_short_positions -v
```

Expected: **FAIL** — `assert result[0].highest_price == 5.00` fails because the current code updates it to 5.20.

### Step 1.3: Add the short guard in `supervisor.py`

In `src/alpaca_bot/runtime/supervisor.py`, locate `_apply_highest_price_updates` (line ~1358). Change the loop body to add the guard as the first check:

**Old** (line 1358 onward):
```python
        for position in positions:
            bars = intraday_bars_by_symbol.get(position.symbol, ())
            if not bars:
```

**New:**
```python
        for position in positions:
            if position.quantity < 0:
                result.append(position)
                continue
            bars = intraday_bars_by_symbol.get(position.symbol, ())
            if not bars:
```

### Step 1.4: Run the test to confirm it passes

```bash
cd /workspace/alpaca_bot && pytest tests/unit/test_supervisor_highest_price.py -v
```

Expected: **all PASS** — new test passes, existing tests still green.

### Step 1.5: Commit

```bash
cd /workspace/alpaca_bot && git add tests/unit/test_supervisor_highest_price.py src/alpaca_bot/runtime/supervisor.py
git commit -m "fix: skip short positions in _apply_highest_price_updates

highest_price is only consumed by the long-side breakeven pass in
evaluate_cycle. Tracking it for short positions caused gratuitous DB
writes every cycle when a short's bar.high exceeded the stored value.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

---

## Task 2: Test — short breakeven with pre-tracked `lowest_price`

**Files:**
- Modify: `tests/unit/test_cycle_engine.py`

The test goes at the bottom of the file, after `test_short_breakeven_stop_emits_when_low_hits_trigger` (line ~2590).

### Step 2.1: Write the failing test

Append to `tests/unit/test_cycle_engine.py`:

```python
def test_short_breakeven_uses_tracked_lowest_price():
    """Short breakeven uses position.lowest_price when it is below current bar.low.

    Setup: entry=6.00, lowest_price=5.85 (tracked from a prior cycle), bar.low=5.90.
    trigger = 6.00 * (1 - 0.0025) = 5.985. bar.low=5.90 <= 5.985 → trigger fires.
    min_price = min(5.85, 5.90) = 5.85  (tracked historical low wins).
    trail_stop = round(5.85 * 1.002, 2) = 5.87.
    be_stop = min(6.00, 5.87) = 5.87.
    be_stop=5.87 > close=5.83 → accept. effective_stop=6.25 > 5.87 → emit.

    Compare: same setup with lowest_price=6.0 (entry) gives be_stop=5.99 — the
    pre-tracked lowest_price produces a tighter (lower) stop.
    """
    CycleIntentType, evaluate_cycle = load_engine_api()
    position = _make_short_position(
        entry_price=6.0, stop_price=6.25, initial_stop_price=6.25, lowest_price=5.85
    )
    bar = Bar(
        symbol="QBTS",
        timestamp=datetime(2026, 5, 13, 17, 0, tzinfo=timezone.utc),
        open=5.92, high=5.93, low=5.90, close=5.83, volume=100_000,
    )
    result = evaluate_cycle(
        settings=make_settings(
            ENABLE_BREAKEVEN_STOP="true",
            BREAKEVEN_TRIGGER_PCT="0.0025",
            BREAKEVEN_TRAIL_PCT="0.002",
            ENABLE_PROFIT_TARGET="false",
            ENABLE_PROFIT_TRAIL="false",
            TRAILING_STOP_ATR_MULTIPLIER="0",
            TRAILING_STOP_PROFIT_TRIGGER_R="999",
        ),
        now=datetime(2026, 5, 13, 17, 0, tzinfo=timezone.utc),
        equity=10_000.0,
        intraday_bars_by_symbol={"QBTS": [bar]},
        daily_bars_by_symbol={},
        open_positions=[position],
        working_order_symbols=set(),
        traded_symbols_today=set(),
        entries_disabled=True,
    )
    be_updates = [
        i for i in result.intents
        if i.intent_type == CycleIntentType.UPDATE_STOP
        and i.symbol == "QBTS"
        and i.reason == "breakeven"
    ]
    assert be_updates, "Short breakeven must emit UPDATE_STOP"
    # be_stop derived from tracked lowest_price=5.85: round(5.85*1.002,2)=5.87
    # NOT from bar.low=5.90: round(5.90*1.002,2)=5.92
    assert be_updates[0].stop_price == pytest.approx(5.87), (
        f"Expected 5.87 (from tracked lowest_price=5.85), got {be_updates[0].stop_price}"
    )
```

### Step 2.2: Run the test to confirm it passes immediately

This test exercises existing engine code — it should pass without any code change, because the engine already uses `lowest_price` correctly. This is a **coverage test**, not a fix test.

```bash
cd /workspace/alpaca_bot && pytest tests/unit/test_cycle_engine.py::test_short_breakeven_uses_tracked_lowest_price -v
```

Expected: **PASS**. If it fails, the engine's `min_price` logic has a bug that needs investigation.

### Step 2.3: Commit

```bash
cd /workspace/alpaca_bot && git add tests/unit/test_cycle_engine.py
git commit -m "test: verify short breakeven uses pre-tracked lowest_price

The existing test used lowest_price == entry_price, so the historical-low
path (engine.py:391 min(position.lowest_price, bar.low)) was never exercised.
New test sets lowest_price=5.85 < bar.low=5.90 and asserts the tighter stop.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

---

## Task 3: Test — short ATR-unavailable trailing stop fallback

**Files:**
- Modify: `tests/unit/test_cycle_engine.py`

### Step 3.1: Write the failing test

Append to `tests/unit/test_cycle_engine.py`, after the test from Task 2:

```python
def test_short_trailing_stop_atr_unavailable_falls_back_to_bar_high():
    """Short trailing stop: when ATR is unavailable (< period+1 daily bars), falls back to bar.high.

    Mirrors test_trailing_stop_atr_unavailable_falls_back_to_bar_low for longs (line ~1041).

    Setup:
      entry_price=10.00, initial_stop_price=10.50, stop_price=10.50, quantity=-50
      risk_per_share = entry_price - initial_stop_price = 10.00 - 10.50 = -0.50  (computed property)
      TRAILING_STOP_PROFIT_TRIGGER_R=0.001
      profit_trigger = 10.00 + 0.001 * (-0.50) = 9.9995
      bar.low=9.80 <= 9.9995 → profit trigger fires

    ATR: only 3 daily bars provided; calculate_atr(period=14) requires 15 → returns None.
    Short fallback (engine.py:289-291):
      new_stop = round(min(stop_price=10.50, entry_price=10.00, bar.high=9.85), 2) = 9.85
    Accept conditions: 9.85 < 10.50 (tightening) AND 9.85 > close=9.82 → emit UPDATE_STOP at 9.85.
    """
    CycleIntentType, evaluate_cycle = load_engine_api()
    position = OpenPosition(
        symbol="TSLA",
        entry_timestamp=datetime(2026, 5, 1, 10, 0, tzinfo=timezone.utc),
        entry_price=10.00,
        quantity=-50,
        entry_level=10.50,
        initial_stop_price=10.50,  # risk_per_share = 10.00 - 10.50 = -0.50
        stop_price=10.50,
        strategy_name="bear_breakdown",
    )
    bar = Bar(
        symbol="TSLA",
        timestamp=datetime(2026, 5, 13, 17, 0, tzinfo=timezone.utc),
        open=9.90, high=9.85, low=9.80, close=9.82, volume=200_000,
    )
    # 3 daily bars → ATR(14) requires 15, so calculate_atr returns None
    short_daily_bars = [
        Bar(
            symbol="TSLA",
            timestamp=datetime(2026, 5, 1 + i, 20, 0, tzinfo=timezone.utc),
            open=10.0, high=10.1, low=9.9, close=10.0, volume=100_000,
        )
        for i in range(3)
    ]
    result = evaluate_cycle(
        settings=make_settings(
            SYMBOLS="TSLA",
            TRAILING_STOP_ATR_MULTIPLIER="1.5",
            TRAILING_STOP_PROFIT_TRIGGER_R="0.001",
            ENABLE_BREAKEVEN_STOP="false",
            ENABLE_PROFIT_TARGET="false",
            ENABLE_PROFIT_TRAIL="false",
        ),
        now=datetime(2026, 5, 13, 17, 0, tzinfo=timezone.utc),
        equity=10_000.0,
        intraday_bars_by_symbol={"TSLA": [bar]},
        daily_bars_by_symbol={"TSLA": short_daily_bars},
        open_positions=[position],
        working_order_symbols=set(),
        traded_symbols_today=set(),
        entries_disabled=True,
    )
    updates = [i for i in result.intents if i.intent_type == CycleIntentType.UPDATE_STOP]
    tsla_updates = [i for i in updates if i.symbol == "TSLA"]
    assert tsla_updates, "Short trailing stop must emit UPDATE_STOP when ATR unavailable"
    # new_stop = round(min(10.50, 10.00, bar.high=9.85), 2) = 9.85
    assert tsla_updates[0].stop_price == pytest.approx(9.85)
```

### Step 3.2: Run the test to confirm it passes immediately

Again a coverage test over existing engine code:

```bash
cd /workspace/alpaca_bot && pytest tests/unit/test_cycle_engine.py::test_short_trailing_stop_atr_unavailable_falls_back_to_bar_high -v
```

Expected: **PASS**. If it fails, check the profit trigger calculation — `risk_per_share` is `entry_price - initial_stop_price = -0.50`, which is negative (as expected for shorts), making `profit_trigger = 9.9995`, well above `bar.low = 9.80`.

### Step 3.3: Commit

```bash
cd /workspace/alpaca_bot && git add tests/unit/test_cycle_engine.py
git commit -m "test: cover short ATR-unavailable trailing stop fallback to bar.high

Mirrors the existing long-side test (test_trailing_stop_atr_unavailable_falls_back_to_bar_low).
Engine line 290-291: new_stop = round(min(stop, entry, bar.high), 2) when ATR is None.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

---

## Task 4: Add observability logging for floor-gated strategies

**Files:**
- Modify: `src/alpaca_bot/risk/weighting.py:1-4` (add import) and `:45-46` (add debug log)

### Step 4.1: Add logger to `weighting.py`

`weighting.py` has no `logging` import. Add it and the logger at the top:

**Old** (lines 1-4):
```python
from __future__ import annotations

import math
from typing import NamedTuple
```

**New:**
```python
from __future__ import annotations

import logging
import math
from typing import NamedTuple

logger = logging.getLogger(__name__)
```

### Step 4.2: Add debug log in `compute_strategy_weights` when `min_trades` gates a strategy

**Old** (lines 44-47):
```python
    for name in active_strategies:
        if trade_count[name] < min_trades:
            sharpes[name] = 0.0
            continue
```

**New:**
```python
    for name in active_strategies:
        if trade_count[name] < min_trades:
            logger.debug(
                "strategy %s has %d trade(s) (< min %d): assigning sharpe=0.0",
                name, trade_count[name], min_trades,
            )
            sharpes[name] = 0.0
            continue
```

### Step 4.3: Run all weighting tests

```bash
cd /workspace/alpaca_bot && pytest tests/unit/test_supervisor_weights.py tests/unit/test_weighting.py tests/unit/test_confidence_scoring.py -v
```

Expected: **all PASS** — no logic changed, only logging added.

### Step 4.4: Commit

```bash
cd /workspace/alpaca_bot && git add src/alpaca_bot/risk/weighting.py
git commit -m "feat: log when a strategy is floor-gated by min_trades in weighting.py

Strategies with fewer than min_trades (default 5) receive sharpe=0.0, which
maps to floor_score in the confidence scorer. Previously silent; now emits a
debug log once per session day (weights are cached after first computation).

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

---

## Task 5: Add ATR fallback debug logging in `atr.py`

`evaluate_cycle()` must remain a pure function (CLAUDE.md: "no I/O, no side effects"). Logging therefore cannot live in `engine.py`. Instead, add it to `calculate_atr` in `atr.py`: when the function determines it cannot compute ATR (insufficient bars), it logs before returning `None`. This fires at the same moment the engine would fall back, and `bars[0].symbol` carries the symbol identifier.

**Files:**
- Modify: `src/alpaca_bot/risk/atr.py`

### Step 5.1: Add logger to `atr.py`

`atr.py` currently has no `logging` import. Add it:

**Old** (lines 1-5):
```python
from __future__ import annotations

from collections.abc import Sequence

from alpaca_bot.domain.models import Bar
```

**New:**
```python
from __future__ import annotations

import logging
from collections.abc import Sequence

from alpaca_bot.domain.models import Bar

logger = logging.getLogger(__name__)
```

### Step 5.2: Add debug log before the early `return None` in `calculate_atr`

**Old** (lines 16-17):
```python
    if len(bars) < period + 1:
        return None
```

**New:**
```python
    if len(bars) < period + 1:
        logger.debug(
            "ATR unavailable for %s: %d bars available, %d required",
            bars[0].symbol if bars else "unknown",
            len(bars),
            period + 1,
        )
        return None
```

### Step 5.3: Run all engine and ATR tests

```bash
cd /workspace/alpaca_bot && pytest tests/unit/test_cycle_engine.py tests/unit/test_atr.py -v
```

Expected: **all PASS** — no logic changed, only logging added. The new Task 3 test (`test_short_trailing_stop_atr_unavailable_falls_back_to_bar_high`) should also pass here since it uses 3 daily bars → ATR unavailable → new log fires, fallback applied.

### Step 5.4: Commit

```bash
cd /workspace/alpaca_bot && git add src/alpaca_bot/risk/atr.py
git commit -m "feat: log ATR unavailable in calculate_atr (both trailing stop directions)

When daily bars < period+1, calculate_atr returns None and the trailing stop
falls back to bar.high (short) or bar.low (long). Previously silent; now emits
a debug log so operators can see which symbols trigger the fallback.

Logging lives in atr.py (not engine.py) to preserve evaluate_cycle() purity.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

---

## Task 6: Full test suite green

### Step 6.1: Run the complete test suite

```bash
cd /workspace/alpaca_bot && pytest -x -q
```

Expected: **all PASS**, no regressions.

### Step 6.2: If any tests fail

Read the failure output. All changes in this plan are additive (guards and logging only) — failures would indicate a pre-existing issue or a typo in the inserted code. Fix before proceeding.

---

## Self-Review

**Spec coverage:**
- ✅ Fix 1: Task 1 adds short guard + test
- ✅ Fix 2a: Task 2 adds `test_short_breakeven_uses_tracked_lowest_price`
- ✅ Fix 2b: Task 3 adds `test_short_trailing_stop_atr_unavailable_falls_back_to_bar_high`
- ✅ Fix 3: Task 4 adds logging in weighting.py only (supervisor.py per-cycle loop dropped — fires every 60s, too noisy; the weighting.py log fires once per session day due to weight caching)
- ✅ Fix 4: Task 5 adds ATR-unavailable debug log in atr.py (not engine.py — engine must stay pure)

**Placeholder scan:** No TBDs, no "similar to Task N" references, all code blocks complete.

**Type consistency:** `_make_short_position`, `load_engine_api`, `make_settings`, `Bar`, `CycleIntentType`, `evaluate_cycle`, `OpenPosition`, `datetime`, `timezone`, `pytest` all at module scope in test_cycle_engine.py — Task 3 uses them directly with no internal imports. `_make_bar`, `_make_supervisor`, `_RecordingPositionStore`, `OpenPosition`, `datetime`, `timezone` in test_supervisor_highest_price.py match existing module-scope definitions.

**Note on Task 3:** `OpenPosition.risk_per_share` is a computed `@property` (`entry_price - initial_stop_price`), not a stored dataclass field. The test uses `initial_stop_price=10.50` so that `risk_per_share = 10.00 - 10.50 = -0.50`. Passing `risk_per_share=0.30` to the constructor would raise `TypeError`.
