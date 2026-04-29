# Plan: Fix flatten_complete One-Way Latch

Spec: docs/superpowers/specs/2026-04-29-flatten-complete-latch.md

## Summary

Remove `if not flatten_complete:` from the position-loop in `evaluate_cycle()`. Positions that exist after flatten_time should always generate EXIT intents, regardless of whether a prior flatten cycle already ran. Duplicate exit submissions are already blocked by the `_execute_exit` idempotency guard in `cycle_intent_execution.py` (lines 362–388).

Two files change. No migration.

---

## Task 1 — engine.py: remove flatten_complete guard

**File**: `src/alpaca_bot/core/engine.py`

Remove the `flatten_complete` local variable (lines 86–88) and collapse the `if not flatten_complete:` guard (lines 100–116) so that EXIT intents are always emitted when `past_flatten=True`.

**Old code** (lines 86–117):
```python
    flatten_complete = (
        session_state is not None and session_state.flatten_complete
    )

    intents: list[CycleIntent] = []
    open_position_symbols = {position.symbol for position in open_positions}
    is_extended = session_type in (SessionType.PRE_MARKET, SessionType.AFTER_HOURS)
    if session_type is not None:
        past_flatten = _session_flatten_time(now, settings, session_type)
    else:
        past_flatten = is_past_flatten_time(now, settings)

    for position in open_positions:
        if past_flatten:
            if not flatten_complete:
                bars = intraday_bars_by_symbol.get(position.symbol, ())
                limit_price_for_exit: float | None = None
                if is_extended and bars:
                    limit_price_for_exit = round(
                        bars[-1].close * (1 - settings.extended_hours_limit_offset_pct), 2
                    )
                intents.append(
                    CycleIntent(
                        intent_type=CycleIntentType.EXIT,
                        symbol=position.symbol,
                        timestamp=now,
                        reason="eod_flatten",
                        limit_price=limit_price_for_exit,
                        strategy_name=strategy_name,
                    )
                )
            continue
```

**New code**:
```python
    intents: list[CycleIntent] = []
    open_position_symbols = {position.symbol for position in open_positions}
    is_extended = session_type in (SessionType.PRE_MARKET, SessionType.AFTER_HOURS)
    if session_type is not None:
        past_flatten = _session_flatten_time(now, settings, session_type)
    else:
        past_flatten = is_past_flatten_time(now, settings)

    for position in open_positions:
        if past_flatten:
            bars = intraday_bars_by_symbol.get(position.symbol, ())
            limit_price_for_exit: float | None = None
            if is_extended and bars:
                limit_price_for_exit = round(
                    bars[-1].close * (1 - settings.extended_hours_limit_offset_pct), 2
                )
            intents.append(
                CycleIntent(
                    intent_type=CycleIntentType.EXIT,
                    symbol=position.symbol,
                    timestamp=now,
                    reason="eod_flatten",
                    limit_price=limit_price_for_exit,
                    strategy_name=strategy_name,
                )
            )
            continue
```

**Why safe**: `_execute_exit` checks `active_exit_orders` under lock before submitting. If an exit is already pending/working for a symbol, it returns `(0, 0, 0)` and skips submission. No duplicate orders are possible.

---

## Task 2 — test_cycle_engine.py: update test_evaluate_cycle_emits_no_exits_when_flatten_already_complete

**File**: `tests/unit/test_cycle_engine.py`

The test at line 334 asserts that `flatten_complete=True` suppresses EXIT intents. This behavior is being removed. Rename the test and flip its assertion.

**Old test**:
```python
# Fix #4: flatten_complete flag suppresses EXIT intents


def test_evaluate_cycle_emits_no_exits_when_flatten_already_complete() -> None:
    """When session_state.flatten_complete is True, evaluate_cycle must not emit
    any EXIT intents — prevents duplicate market orders when the trade stream
    is down and the fill hasn't been recorded yet."""
    ...
    exit_intents = [i for i in result.intents if i.intent_type == CycleIntentType.EXIT]
    assert exit_intents == [], (
        f"Expected no EXIT intents when flatten_complete=True, got: {exit_intents}"
    )
```

**New test**:
```python
def test_evaluate_cycle_emits_exits_for_late_positions_when_flatten_already_complete() -> None:
    """flatten_complete=True must NOT suppress EXIT intents for positions that
    exist after flatten_time. A position here means it arrived after the initial
    flatten (e.g., late fill from a restart cascade). _execute_exit has its own
    idempotency guard to prevent duplicate broker submissions."""
    ...
    exit_intents = [i for i in result.intents if i.intent_type == CycleIntentType.EXIT]
    assert len(exit_intents) == 1, (
        f"Expected EXIT intent for position even when flatten_complete=True, got: {exit_intents}"
    )
```

Also remove the comment line `# Fix #4: flatten_complete flag suppresses EXIT intents` and the blank line below it (lines 329–331), since the behavior being documented no longer applies.

The control test `test_evaluate_cycle_emits_exits_when_flatten_not_complete` (line 393) remains unchanged and still passes.

---

## Task 3 — Run full test suite

```bash
pytest tests/unit/ -v
```

All tests must pass. Verify specifically:
- `test_evaluate_cycle_emits_exits_for_late_positions_when_flatten_already_complete` passes
- `test_evaluate_cycle_emits_exits_when_flatten_not_complete` passes
- All other flatten-related tests pass

---

## Grilling answers

**Does this change affect order submission?** No new order submission paths. The only change is that EXIT intents are now generated even when `flatten_complete=True`. `_execute_exit` suppresses duplicate submissions via its `active_exit_orders` guard — no double-sell risk.

**Concurrent cycle conflict?** Not possible — Postgres advisory lock prevents two supervisor instances. Single-threaded cycle loop within one instance.

**AuditEvent trail?** Unchanged. The `cycle_intent_skipped` audit event (already emitted by `_execute_exit` when skipping a duplicate) provides visibility. No new audit events needed.

**Intent/dispatch separation?** EXIT intents are executed immediately by `execute_cycle_intents` (not via pending_submit queue). This is unchanged.

**Pure engine boundary?** `evaluate_cycle()` remains a pure function. No I/O added.

**Rollback safety?** No migration. No DB schema changes.

**Paper vs live?** Identical behavior in both modes. `flatten_complete` field is mode-scoped, but the engine change is mode-agnostic.

**Market-hours guards?** This change only affects the `past_flatten` branch, which requires `is_past_flatten_time() == True`. That time check is unchanged. No new broker calls outside market hours.

**New env vars?** None.

**Test coverage?** Task 2 directly tests the fixed behavior. The pre-existing `_execute_exit` idempotency is tested in `test_cycle_intent_execution.py` (existing coverage).

**`flatten_complete` field in DailySessionState?** Retained. Used by `web/app.py` (line 450) for dashboard display and by the supervisor to persist `entries_disabled=True`. Removing it from the engine's EXIT generation logic is the only change.
