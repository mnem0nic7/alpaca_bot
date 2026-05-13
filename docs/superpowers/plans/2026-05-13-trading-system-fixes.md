# Trading System Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix four issues: skip shorts in `_apply_highest_price_updates`, add two missing engine tests, add observability logging for floor-gated strategies, add ATR fallback debug logging.

**Architecture:** All changes are isolated to existing files. No new files, no schema changes, no audit events. Fixes #1 and #2 are correctness improvements; #3 and #4 are observability additions (debug-level logging only).

**Tech Stack:** Python 3.12, pytest, logging stdlib

---

## File Map

| File | What changes |
|---|---|
| `src/alpaca_bot/runtime/supervisor.py` | Add short-skip guard to `_apply_highest_price_updates`; add confidence floor debug log |
| `src/alpaca_bot/risk/weighting.py` | Add `import logging` + debug log when strategy gets `sharpe=0.0` due to `min_trades` |
| `src/alpaca_bot/core/engine.py` | Add `import logging` + debug log in both ATR-fallback branches |
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

    # Short: qty=-100, stock dropped to 4.70 (bar.high=4.70 > current highest_price=5.00 is false
    # for a short that rallied from 5.00 — actually simulating a short where bar.high=5.20 > 5.00
    # so without the guard this WOULD trigger an update)
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

    Mirrors test_trailing_stop_atr_unavailable_falls_back_to_bar_low for longs (line 1041).

    Setup: short entry=10.00, stop=10.50, risk_per_share=0.30.
    profit_trigger = 10.00 + 1.0 * (-0.30) = 9.70.  (is_short: trigger = entry + R*risk)

    Wait — for shorts, profit_trigger is computed the same way:
      profit_trigger = entry_price + trailing_stop_profit_trigger_r * risk_per_share
    risk_per_share for a short is stored as negative (stop is above entry).
    But _make_short_position doesn't set risk_per_share. Check engine default...

    Actually looking at engine.py:264-266:
      profit_trigger = position.entry_price + settings.trailing_stop_profit_trigger_r * position.risk_per_share

    For short: entry=10.00, risk_per_share defaults to 0.0 in OpenPosition.
    trigger = 10.00 + R * 0.0 = 10.00, and bar.low <= 10.00 always when stock is below entry.
    So we need risk_per_share != 0 to control the trigger reliably.

    Use TRAILING_STOP_PROFIT_TRIGGER_R very small (0.001) and risk_per_share=0.30 so
    trigger = 10.00 + 0.001 * 0.30 ≈ 10.0003, and bar.low=9.80 easily beats it.

    ATR: provide only 3 daily bars (< 14+1=15 required) → calculate_atr returns None.
    Fallback (engine.py:290-291): new_stop = round(min(10.50, 10.00, bar.high=9.85), 2) = 9.85.
    accept: 9.85 < 10.50 AND 9.85 > close=9.82 → emit UPDATE_STOP at 9.85.
    """
    CycleIntentType, evaluate_cycle = load_engine_api()
    from alpaca_bot.domain import OpenPosition as _OP
    from datetime import datetime, timezone as _tz

    position = _OP(
        symbol="TSLA",
        entry_timestamp=datetime(2026, 5, 1, 10, 0, tzinfo=_tz.utc),
        entry_price=10.00,
        quantity=-50,
        entry_level=10.50,
        initial_stop_price=10.50,
        stop_price=10.50,
        risk_per_share=0.30,
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

Expected: **PASS**. If it fails, trace the trigger calculation — `risk_per_share` may need adjustment.

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
- Modify: `src/alpaca_bot/runtime/supervisor.py:369` (add post-scoring debug log)

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

### Step 4.3: Add debug log in `supervisor.py` after confidence scores are computed

In `supervisor.py`, after line 369 (`session_confidence_scores = compute_confidence_scores(...)`), add:

**Old** (line 369):
```python
        session_confidence_scores = compute_confidence_scores(session_sharpes, confidence_floor)
        # Per-strategy losing streak exclusion.
```

**New:**
```python
        session_confidence_scores = compute_confidence_scores(session_sharpes, confidence_floor)
        for _sn, _sc in session_confidence_scores.items():
            if _sc <= confidence_floor:
                logger.debug("strategy %s confidence at floor %.2f", _sn, _sc)
        # Per-strategy losing streak exclusion.
```

### Step 4.4: Run all supervisor and weighting tests

```bash
cd /workspace/alpaca_bot && pytest tests/unit/test_supervisor_weights.py tests/unit/test_weighting.py tests/unit/test_confidence_scoring.py -v
```

Expected: **all PASS** — no logic changed, only logging added.

### Step 4.5: Commit

```bash
cd /workspace/alpaca_bot && git add src/alpaca_bot/risk/weighting.py src/alpaca_bot/runtime/supervisor.py
git commit -m "feat: add debug logging for floor-gated strategies

weighting.py logs when a strategy gets sharpe=0.0 due to insufficient trades.
supervisor.py logs each strategy whose confidence score equals the floor.
Helps operators identify warming-up strategies without querying the DB.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

---

## Task 5: Add ATR fallback debug logging in `engine.py`

**Files:**
- Modify: `src/alpaca_bot/core/engine.py:1-7` (add import) and `:289-292`, `:302-305` (add logs)

### Step 5.1: Add logger to `engine.py`

`engine.py` has no `logging` import. Add it at the top:

**Old** (lines 1-7):
```python
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from enum import StrEnum
from typing import TYPE_CHECKING, Mapping, Sequence
```

**New:**
```python
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from enum import StrEnum
from typing import TYPE_CHECKING, Mapping, Sequence

logger = logging.getLogger(__name__)
```

### Step 5.2: Add debug log in short ATR-fallback branch

**Old** (engine.py lines ~289-292):
```python
                    else:
                        new_stop = round(
                            min(position.stop_price, position.entry_price, latest_bar.high), 2
                        )
```

**New:**
```python
                    else:
                        logger.debug(
                            "trailing stop ATR unavailable for %s (short): using bar.high fallback",
                            position.symbol,
                        )
                        new_stop = round(
                            min(position.stop_price, position.entry_price, latest_bar.high), 2
                        )
```

### Step 5.3: Add debug log in long ATR-fallback branch

**Old** (engine.py lines ~302-305):
```python
                    else:
                        new_stop = round(
                            max(position.stop_price, position.entry_price, latest_bar.low), 2
                        )
```

**New:**
```python
                    else:
                        logger.debug(
                            "trailing stop ATR unavailable for %s (long): using bar.low fallback",
                            position.symbol,
                        )
                        new_stop = round(
                            max(position.stop_price, position.entry_price, latest_bar.low), 2
                        )
```

### Step 5.4: Run all engine tests

```bash
cd /workspace/alpaca_bot && pytest tests/unit/test_cycle_engine.py -v
```

Expected: **all PASS** — no logic changed, only logging added.

### Step 5.5: Commit

```bash
cd /workspace/alpaca_bot && git add src/alpaca_bot/core/engine.py
git commit -m "feat: log ATR fallback in trailing stop pass (both directions)

When calculate_atr returns None (insufficient daily bars), the trailing
stop silently used bar.high (short) or bar.low (long). Now emits a debug
log so operators can see which symbols trigger the fallback.

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
- ✅ Fix 3: Task 4 adds logging in weighting.py and supervisor.py
- ✅ Fix 4: Task 5 adds ATR fallback debug logs in engine.py

**Placeholder scan:** No TBDs, no "similar to Task N" references, all code blocks complete.

**Type consistency:** `_make_short_position`, `load_engine_api`, `make_settings`, `Bar`, `CycleIntentType`, `evaluate_cycle` all match existing definitions in test_cycle_engine.py. `_make_bar`, `_make_supervisor`, `_RecordingPositionStore` in test_supervisor_highest_price.py match existing definitions.

**Note on Task 3:** The test imports `OpenPosition` directly instead of using `_make_short_position` because it needs `risk_per_share=0.30` which the helper doesn't expose. This is intentional.
