# Stop-Above-Market Guard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent `UPDATE_STOP` intents from being emitted when the computed stop price ≥ the current bar's close, eliminating the infinite `stop_update_failed` audit-event loop caused by Alpaca rejecting stop-above-market replace_order calls.

**Architecture:** Four targeted guard additions to `core/engine.py` — one per UPDATE_STOP-emitting pass (ATR trailing, profit trail, breakeven, cap-up). The cap-up pass additionally needs a new bar lookup because it currently has no access to the current close. Each test disables all other passes so failures are unambiguous. No new settings, no schema changes.

**Tech Stack:** Python, pytest, existing `make_settings` / `load_engine_api` test helpers in `tests/unit/test_cycle_engine.py`.

---

## Files Affected

| File | Action |
|---|---|
| `src/alpaca_bot/core/engine.py` | Four guard changes — see per-task details |
| `tests/unit/test_cycle_engine.py` | Add four test functions (one per pass) |

---

### Task 1: ATR trailing pass guard

**Files:**
- Modify: `tests/unit/test_cycle_engine.py`
- Modify: `src/alpaca_bot/core/engine.py` (line 262)

**Isolation:** Breakeven disabled (`ENABLE_BREAKEVEN_STOP="false"`). `stop_price=77.0` ensures `effective_stop=77.0 > cap_stop=76.0` so cap-up does not emit. Profit trail is off by default.

- [ ] **Step 1: Fix an existing test that uses a degenerate bar before adding the guard**

`test_breakeven_stop_defers_when_trailing_already_emitted_above_be_stop` uses a bar with
`low=high=close=110.0`. This makes `new_stop=110.0 == close=110.0`, which the strict `< close`
guard would suppress (production bars never have `close < low`; this was an artificial fixture).

In `tests/unit/test_cycle_engine.py` at the bar in that test (line ~1979), change:

```python
        close=110.0,
```

To:

```python
        close=110.1,
```

Run to confirm the test still passes:

```bash
pytest tests/unit/test_cycle_engine.py::test_breakeven_stop_defers_when_trailing_already_emitted_above_be_stop -v
```

Expected: PASS (ATR trailing emits stop=110.0 < close=110.1 → guard does not suppress it).

- [ ] **Step 2: Write the failing test**

Add this function to `tests/unit/test_cycle_engine.py`:

```python
def test_atr_trailing_stop_above_close_not_emitted() -> None:
    """Gap-down: computed ATR trailing stop ≥ close → no UPDATE_STOP emitted."""
    CycleIntentType, evaluate_cycle = load_engine_api()

    # entry=80, initial_stop=75 → risk_per_share=5.0
    # profit_trigger = 80 + 1.0 * 5.0 = 85.0
    # high=90 ≥ 85 → ATR pass activates; atr_multiplier=0 → new_stop = max(75,80,low=87) = 87
    # close=76 → new_stop=87 ≥ 76 → guard must fire → no UPDATE_STOP
    # Breakeven disabled; stop_price=77 > cap_stop=76 → cap-up does not emit
    position = OpenPosition(
        symbol="SYRE",
        entry_timestamp=datetime(2026, 4, 20, 14, 0, tzinfo=timezone.utc),
        entry_price=80.0,
        quantity=10,
        entry_level=80.0,
        initial_stop_price=75.0,
        stop_price=77.0,
    )
    bar = Bar(
        symbol="SYRE",
        timestamp=datetime(2026, 4, 24, 19, 0, tzinfo=timezone.utc),
        open=89.0,
        high=90.0,
        low=87.0,
        close=76.0,
        volume=100_000,
    )
    result = evaluate_cycle(
        settings=make_settings(ENABLE_BREAKEVEN_STOP="false"),
        now=datetime(2026, 4, 24, 19, 0, tzinfo=timezone.utc),
        equity=10_000.0,
        intraday_bars_by_symbol={"SYRE": [bar]},
        daily_bars_by_symbol={},
        open_positions=[position],
        working_order_symbols=set(),
        traded_symbols_today=set(),
        entries_disabled=True,
    )
    update_stops = [i for i in result.intents if i.intent_type == CycleIntentType.UPDATE_STOP]
    assert update_stops == [], f"Expected no UPDATE_STOP, got {update_stops!r}"
```

- [ ] **Step 3: Run to verify the test FAILS**

```bash
pytest tests/unit/test_cycle_engine.py::test_atr_trailing_stop_above_close_not_emitted -v
```

Expected: FAIL — an UPDATE_STOP intent with stop_price=87.0 is emitted.

- [ ] **Step 4: Add the guard to `engine.py`**

In `src/alpaca_bot/core/engine.py`, locate line 262. Replace:

```python
            if new_stop > position.stop_price:
```

With:

```python
            if new_stop > position.stop_price and new_stop < latest_bar.close:
```

- [ ] **Step 5: Run to verify the test PASSES**

```bash
pytest tests/unit/test_cycle_engine.py::test_atr_trailing_stop_above_close_not_emitted -v
```

Expected: PASS.

- [ ] **Step 6: Run the full engine test suite to check for regressions**

```bash
pytest tests/unit/test_cycle_engine.py -q
```

Expected: all existing tests pass. Key test to watch: `test_evaluate_cycle_emits_stop_update_after_plus_one_r_without_loosening` uses `close=112.10`, `new_stop=111.70` — `111.70 < 112.10` so the guard does not suppress that emit.

- [ ] **Step 7: Commit**

```bash
git add src/alpaca_bot/core/engine.py tests/unit/test_cycle_engine.py
git commit -m "fix: guard ATR trailing stop against stop-above-market emission

When a gap-down puts the ATR trailing candidate above the current close,
the emitted UPDATE_STOP is rejected by Alpaca every cycle. Guard checks
new_stop < latest_bar.close before emitting."
```

---

### Task 2: Profit trail pass guard

**Files:**
- Modify: `tests/unit/test_cycle_engine.py`
- Modify: `src/alpaca_bot/core/engine.py` (line 298)

**Isolation:** ATR trailing disabled (`TRAILING_STOP_PROFIT_TRIGGER_R="1000"`). Breakeven disabled (`ENABLE_BREAKEVEN_STOP="false"`). `stop_price=77.0` prevents cap-up.

- [ ] **Step 1: Write the failing test**

Add this function to `tests/unit/test_cycle_engine.py`:

```python
def test_profit_trail_candidate_above_close_not_emitted() -> None:
    """Gap-down: profit trail candidate ≥ close → no UPDATE_STOP emitted."""
    CycleIntentType, evaluate_cycle = load_engine_api()

    # profit_trail_pct=0.95, today_high=90 → trail_candidate = round(90*0.95, 2) = 85.5
    # stop_price=77.0 → 85.5 > 77.0 → would emit without guard
    # close=76.0 → 85.5 ≥ 76.0 → guard must fire → no UPDATE_STOP
    # Bar must be stamped "today": now=2026-04-24T19:00Z = 15:00 ET → session_date=2026-04-24
    position = OpenPosition(
        symbol="SYRE",
        entry_timestamp=datetime(2026, 4, 20, 14, 0, tzinfo=timezone.utc),
        entry_price=80.0,
        quantity=10,
        entry_level=80.0,
        initial_stop_price=75.0,
        stop_price=77.0,
    )
    bar = Bar(
        symbol="SYRE",
        timestamp=datetime(2026, 4, 24, 14, 0, tzinfo=timezone.utc),  # 10:00 ET = today
        open=89.0,
        high=90.0,
        low=75.5,
        close=76.0,
        volume=100_000,
    )
    result = evaluate_cycle(
        settings=make_settings(
            ENABLE_PROFIT_TRAIL="true",
            PROFIT_TRAIL_PCT="0.95",
            TRAILING_STOP_PROFIT_TRIGGER_R="1000",  # disable ATR trailing
            ENABLE_BREAKEVEN_STOP="false",
        ),
        now=datetime(2026, 4, 24, 19, 0, tzinfo=timezone.utc),
        equity=10_000.0,
        intraday_bars_by_symbol={"SYRE": [bar]},
        daily_bars_by_symbol={},
        open_positions=[position],
        working_order_symbols=set(),
        traded_symbols_today=set(),
        entries_disabled=True,
    )
    update_stops = [i for i in result.intents if i.intent_type == CycleIntentType.UPDATE_STOP]
    assert update_stops == [], f"Expected no UPDATE_STOP, got {update_stops!r}"
```

- [ ] **Step 2: Run to verify the test FAILS**

```bash
pytest tests/unit/test_cycle_engine.py::test_profit_trail_candidate_above_close_not_emitted -v
```

Expected: FAIL — an UPDATE_STOP intent with stop_price=85.5 and reason="profit_trail" is emitted.

- [ ] **Step 3: Add the guard to `engine.py`**

In `src/alpaca_bot/core/engine.py`, locate line 298. Replace:

```python
            if trail_candidate > prior_stop:
```

With:

```python
            if trail_candidate > prior_stop and trail_candidate < bars[-1].close:
```

- [ ] **Step 4: Run to verify the test PASSES**

```bash
pytest tests/unit/test_cycle_engine.py::test_profit_trail_candidate_above_close_not_emitted -v
```

Expected: PASS.

- [ ] **Step 5: Run the full engine test suite**

```bash
pytest tests/unit/test_cycle_engine.py -q
```

Expected: all existing tests pass. Key test to watch: `test_profit_trail_emits_when_trail_exceeds_current_stop` uses `close=11.90`, `trail=10.80` — `10.80 < 11.90` so the guard does not suppress that emit.

- [ ] **Step 6: Commit**

```bash
git add src/alpaca_bot/core/engine.py tests/unit/test_cycle_engine.py
git commit -m "fix: guard profit trail stop against stop-above-market emission

When a gap-down pulls the current close below today_high * profit_trail_pct,
the emitted UPDATE_STOP is rejected by Alpaca. Guard checks
trail_candidate < bars[-1].close before emitting."
```

---

### Task 3: Breakeven pass — remove extended-hours gate

**Files:**
- Modify: `tests/unit/test_cycle_engine.py`
- Modify: `src/alpaca_bot/core/engine.py` (line 336)

**Context:** The breakeven pass already has a close guard, but it is gated on `is_extended`. During a regular session (`session_type=None` → `is_extended=False`), a gap-down can put `be_stop` above the current close — but the guard never fires because `is_extended=False`. Removing `is_extended and` makes the guard unconditional.

**Isolation:** ATR trailing disabled (`TRAILING_STOP_PROFIT_TRIGGER_R="1000"`). `bar.high=102.0 < profit_trigger=105.0` so ATR pass does not activate. `stop_price=95.0 >= cap_stop=95.0` so cap-up does not emit.

- [ ] **Step 1: Write the failing test**

Add this function to `tests/unit/test_cycle_engine.py`:

```python
def test_breakeven_stop_above_close_regular_session_not_emitted() -> None:
    """Gap-down during regular session: breakeven stop ≥ close → no UPDATE_STOP.

    Before the fix the guard was gated on is_extended; with is_extended=False
    (regular session) the guard never fired and an above-market UPDATE_STOP was
    emitted every cycle.
    """
    CycleIntentType, evaluate_cycle = load_engine_api()

    # entry=100, initial_stop=95, stop=95 → risk=5; profit_trigger=105
    # bar.high=102 < 105 → ATR pass does NOT activate
    # breakeven_trigger_pct=0.0025 → trigger=100.25; high=102 ≥ 100.25 → breakeven activates
    # highest_price=102 → max_price=102; trail_stop=round(102*0.998,2)=101.8
    # be_stop = max(100.0, 101.8) = 101.8
    # close=76 → be_stop=101.8 ≥ 76 → guard must fire → no UPDATE_STOP
    # stop_price=95 == cap_stop=95 → 95 < 95 False → cap-up does not emit
    position = OpenPosition(
        symbol="SYRE",
        entry_timestamp=datetime(2026, 4, 20, 14, 0, tzinfo=timezone.utc),
        entry_price=100.0,
        quantity=10,
        entry_level=100.0,
        initial_stop_price=95.0,
        stop_price=95.0,
        highest_price=102.0,
    )
    bar = Bar(
        symbol="SYRE",
        timestamp=datetime(2026, 4, 24, 19, 0, tzinfo=timezone.utc),
        open=78.0,
        high=102.0,
        low=75.5,
        close=76.0,
        volume=100_000,
    )
    result = evaluate_cycle(
        settings=make_settings(
            TRAILING_STOP_PROFIT_TRIGGER_R="1000",  # disable ATR trailing
            ENABLE_BREAKEVEN_STOP="true",
        ),
        now=datetime(2026, 4, 24, 19, 0, tzinfo=timezone.utc),
        equity=10_000.0,
        intraday_bars_by_symbol={"SYRE": [bar]},
        daily_bars_by_symbol={},
        open_positions=[position],
        working_order_symbols=set(),
        traded_symbols_today=set(),
        entries_disabled=True,
        session_type=None,  # regular session → is_extended=False
    )
    update_stops = [i for i in result.intents if i.intent_type == CycleIntentType.UPDATE_STOP]
    assert update_stops == [], f"Expected no UPDATE_STOP, got {update_stops!r}"
```

- [ ] **Step 2: Run to verify the test FAILS**

```bash
pytest tests/unit/test_cycle_engine.py::test_breakeven_stop_above_close_regular_session_not_emitted -v
```

Expected: FAIL — an UPDATE_STOP with stop_price=101.8 and reason="breakeven" is emitted because `is_extended=False` means the guard condition `is_extended and be_stop >= close` evaluates to `False`.

- [ ] **Step 3: Remove the `is_extended and` gate**

In `src/alpaca_bot/core/engine.py`, locate line 336. Replace:

```python
                if is_extended and be_stop >= latest_bar.close:
                    continue  # stop above current price would trigger immediately at open
```

With:

```python
                if be_stop >= latest_bar.close:
                    continue
```

- [ ] **Step 4: Run to verify the test PASSES**

```bash
pytest tests/unit/test_cycle_engine.py::test_breakeven_stop_above_close_regular_session_not_emitted -v
```

Expected: PASS.

- [ ] **Step 5: Run the full engine test suite**

```bash
pytest tests/unit/test_cycle_engine.py -q
```

Expected: all existing tests pass. Key test to watch: `test_breakeven_stop_emits_when_up_trigger_pct` uses `close=100.20`, `be_stop=100.10` — `100.10 < 100.20` so the guard does not suppress that emit.

- [ ] **Step 6: Commit**

```bash
git add src/alpaca_bot/core/engine.py tests/unit/test_cycle_engine.py
git commit -m "fix: make breakeven stop-above-close guard unconditional

The guard was gated on is_extended, so it never fired during regular
sessions. A gap-down can put be_stop above close in any session type;
removing the is_extended gate makes the guard always active."
```

---

### Task 4: Cap-up pass — add bar lookup and close guard

**Files:**
- Modify: `tests/unit/test_cycle_engine.py`
- Modify: `src/alpaca_bot/core/engine.py` (lines 362–379)

**Context:** The cap-up pass is the only UPDATE_STOP-emitting pass with no access to the current bar. It must look up `intraday_bars_by_symbol` before computing `cap_stop`, then skip if `cap_stop >= bars[-1].close`. Positions with no bar entry are also skipped (consistent with the breakeven pass pattern).

**Isolation:** ATR trailing disabled (`TRAILING_STOP_PROFIT_TRIGGER_R="1000"`). `bar.high=80.0 < breakeven_trigger=100.25` so breakeven does not activate.

- [ ] **Step 1: Write the failing test**

Add this function to `tests/unit/test_cycle_engine.py`:

```python
def test_cap_up_stop_above_close_not_emitted() -> None:
    """Gap-down: cap stop (entry * (1−max_stop_pct)) ≥ close → no UPDATE_STOP emitted."""
    CycleIntentType, evaluate_cycle = load_engine_api()

    # entry=100, max_stop_pct=0.05 → cap_stop = round(100*0.95, 2) = 95.0
    # stop_price=90 → effective_stop=90 < cap_stop=95 → would emit without guard
    # close=76 → cap_stop=95 ≥ 76 → guard must fire → no UPDATE_STOP
    # bar.high=80 < profit_trigger=105 → ATR not activated
    # bar.high=80 < breakeven_trigger=100.25 → breakeven not activated
    position = OpenPosition(
        symbol="SYRE",
        entry_timestamp=datetime(2026, 4, 20, 14, 0, tzinfo=timezone.utc),
        entry_price=100.0,
        quantity=10,
        entry_level=100.0,
        initial_stop_price=95.0,
        stop_price=90.0,
    )
    bar = Bar(
        symbol="SYRE",
        timestamp=datetime(2026, 4, 24, 19, 0, tzinfo=timezone.utc),
        open=78.0,
        high=80.0,
        low=75.5,
        close=76.0,
        volume=100_000,
    )
    result = evaluate_cycle(
        settings=make_settings(
            TRAILING_STOP_PROFIT_TRIGGER_R="1000",  # disable ATR trailing
            MAX_STOP_PCT="0.05",
        ),
        now=datetime(2026, 4, 24, 19, 0, tzinfo=timezone.utc),
        equity=10_000.0,
        intraday_bars_by_symbol={"SYRE": [bar]},
        daily_bars_by_symbol={},
        open_positions=[position],
        working_order_symbols=set(),
        traded_symbols_today=set(),
        entries_disabled=True,
        session_type=None,
    )
    update_stops = [i for i in result.intents if i.intent_type == CycleIntentType.UPDATE_STOP]
    assert update_stops == [], f"Expected no UPDATE_STOP, got {update_stops!r}"
```

- [ ] **Step 2: Run to verify the test FAILS**

```bash
pytest tests/unit/test_cycle_engine.py::test_cap_up_stop_above_close_not_emitted -v
```

Expected: FAIL — an UPDATE_STOP with stop_price=95.0 and reason="stop_cap_applied" is emitted because no close guard exists in the cap-up pass.

- [ ] **Step 3: Add bar lookup and close guard to cap-up pass**

In `src/alpaca_bot/core/engine.py`, locate the inner loop of the cap-up pass (lines 362–379). Replace:

```python
        for position in open_positions:
            if position.symbol in emitted_exit_syms:
                continue
            if position.stop_price <= 0 or position.entry_price <= 0:
                continue
            cap_stop = round(position.entry_price * (1 - settings.max_stop_pct), 2)
            effective_stop = emitted_update_stops.get(position.symbol, position.stop_price)
            if effective_stop < cap_stop:
                intents.append(
                    CycleIntent(
                        intent_type=CycleIntentType.UPDATE_STOP,
                        symbol=position.symbol,
                        timestamp=now,
                        stop_price=cap_stop,
                        strategy_name=strategy_name,
                        reason="stop_cap_applied",
                    )
                )
```

With:

```python
        for position in open_positions:
            if position.symbol in emitted_exit_syms:
                continue
            if position.stop_price <= 0 or position.entry_price <= 0:
                continue
            bars = intraday_bars_by_symbol.get(position.symbol, ())
            if not bars:
                continue
            cap_stop = round(position.entry_price * (1 - settings.max_stop_pct), 2)
            effective_stop = emitted_update_stops.get(position.symbol, position.stop_price)
            if effective_stop < cap_stop and cap_stop < bars[-1].close:
                intents.append(
                    CycleIntent(
                        intent_type=CycleIntentType.UPDATE_STOP,
                        symbol=position.symbol,
                        timestamp=now,
                        stop_price=cap_stop,
                        strategy_name=strategy_name,
                        reason="stop_cap_applied",
                    )
                )
```

- [ ] **Step 4: Run to verify the test PASSES**

```bash
pytest tests/unit/test_cycle_engine.py::test_cap_up_stop_above_close_not_emitted -v
```

Expected: PASS.

- [ ] **Step 5: Run the full test suite — expect no regressions**

```bash
pytest --tb=short -q
```

Expected: all tests pass. Note: the cap-up pass now skips positions with no bar entry in `intraday_bars_by_symbol`. This is consistent with the breakeven pass behavior and safe — positions with no bar data cannot have a valid current close to compare against.

- [ ] **Step 6: Commit**

```bash
git add src/alpaca_bot/core/engine.py tests/unit/test_cycle_engine.py
git commit -m "fix: guard cap-up stop against stop-above-market emission

The cap-up pass had no bar access. Add intraday_bars_by_symbol lookup and
skip if cap_stop >= bars[-1].close. An extreme gap-down can put the
cap stop above the current price; emitting in that case causes Alpaca to
reject replace_order every cycle."
```
