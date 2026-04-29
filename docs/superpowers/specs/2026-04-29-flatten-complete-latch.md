# Spec: Fix flatten_complete One-Way Latch

## Problem

`DailySessionState.flatten_complete` is stored in Postgres and read by `evaluate_cycle()` to suppress EXIT intent generation after `flatten_time`. The intent was: "once we've flattened all positions for the day, don't re-generate exit orders on subsequent cycles."

The flaw: `flatten_complete=True` suppresses exit generation for **any** position that exists when the next cycle runs — including positions that **appeared after the initial flatten** due to late fill processing from supervisor restart cascades.

### Observed failure (production, 2026-04-29)

1. AIN filled at broker at ~15:35 ET.
2. Supervisor restart cascade: 4 reconciliation events between 15:48–15:53 ET delayed trade_updates fill processing.
3. 15:45 ET: Flatten cycle runs. Some other momentum position (if any existed) was flattened → `flatten_complete=True` saved.
4. 15:53 ET: startup_recovery finally processes AIN as a broker position. AIN record created in local DB with `stop_price=0` (separate bug, now fixed with COALESCE).
5. 15:59 ET: Next cycle. `open_positions=[AIN]`. Engine reads `flatten_complete=True`. Skips EXIT generation. AIN carries over to next trading day with no stop protection.

## Root Cause

In `engine.py` lines 99–116:
```python
for position in open_positions:
    if past_flatten:
        if not flatten_complete:   # ← this guard is too broad
            ...generate EXIT intent...
        continue
```

`flatten_complete=True` was set when previous positions were successfully flattened. But a **new** position appeared after that flatten. The latch has no way to know about it and blocks exit generation.

## Fix

Remove the `if not flatten_complete:` guard. Always generate EXIT intents for any position when `past_flatten=True`.

**Idempotency is already handled**: `_execute_exit` in `cycle_intent_execution.py` (lines 362–388) checks `active_exit_orders` under a lock before submitting. If an exit order is already pending/working for this symbol, it returns `(0, 0, 0)` and writes a `cycle_intent_skipped` audit event. Duplicate submissions are blocked at the execution layer — the engine doesn't need to suppress them.

**`flatten_complete` field is retained** in `DailySessionState` for:
- Dashboard display (`web/app.py` line 450 reads it)
- Persisted historical record of when flattening occurred
- `entries_disabled=True` (always saved alongside `flatten_complete=True`) remains the correct post-flatten entry gate

## What does NOT change

- `DailySessionState.flatten_complete` field: stays in model, stays persisted, stays set by supervisor
- `entries_disabled=True`: still set alongside `flatten_complete=True` — new entries remain blocked after flatten
- `_execute_exit` idempotency guard: unchanged — still prevents duplicate exit orders
- Supervisor's `flatten_complete` save logic: unchanged — still only set when `has_flatten_intents=True` and `failed_exit_count=0`

## Scope

Two-file change:
1. `src/alpaca_bot/core/engine.py` — remove `flatten_complete` local variable and `if not flatten_complete:` guard
2. `tests/unit/test_cycle_engine.py` — update one test that asserted "no EXIT when flatten_complete=True"

No migration, no new env vars, no schema changes.
