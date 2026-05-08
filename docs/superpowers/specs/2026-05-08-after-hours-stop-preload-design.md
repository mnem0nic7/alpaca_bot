# After-Hours Stop Price Preloading — Design Spec

## Problem

Open positions held overnight have their breakeven trail stops stuck at the initial stop price for the duration of the after-hours/pre-market session. Two gates enforce this:

1. **Engine gate** (`engine.py:292`): `if settings.enable_breakeven_stop and not is_extended:` — the breakeven pass is skipped entirely during extended-hours cycles, so no `UPDATE_STOP` intents are ever emitted.
2. **Executor gate** (`cycle_intent_execution.py:126-132`): even if `UPDATE_STOP` intents were emitted, `execute_cycle_intents` skips them with `continue` during extended hours.

**Consequence:** `pending_submit` stop orders in Postgres carry the initial stop price overnight. When `dispatch_pending_orders` runs at market open, it submits those stops at the initial (wrong) price. The first 15-minute bar doesn't arrive until 9:45 AM, leaving a 15-minute window with under-protected positions.

**Context:** The `highest_price` persistence feature (shipped 2026-05-08) ensures the breakeven trail computation uses the true lifetime high — but that computation never runs after hours, so the improvement only takes effect at the first morning cycle bar, not at dispatch.

---

## Goal

Compute breakeven trail stop updates during extended-hours cycles and persist them to Postgres (DB-only — no broker calls). When `dispatch_pending_orders` runs at market open, it submits the already-correct trail-adjusted prices.

---

## Non-Goals

- Profit trail stops (`enable_profit_trail`): stays gated on regular hours. The profit trail uses today's session high, which doesn't apply across session boundaries.
- Stop cap (`MAX_STOP_PCT`): stays gated on regular hours. Cap is a correction pass, not a protective mechanism.
- Already-submitted broker stops (those with a `broker_order_id`): Alpaca does not allow stop order replacement after hours. These are left unchanged; the first morning cycle issues a `replace_order`.

---

## Design

### Architecture

Two targeted changes, no new files:

| File | Change |
|------|--------|
| `src/alpaca_bot/core/engine.py` | Relax breakeven gate; add extended-hours safety guard |
| `src/alpaca_bot/runtime/cycle_intent_execution.py` | DB-only path for `UPDATE_STOP` during extended hours |

`evaluate_cycle()` remains a pure function. No I/O is introduced into the engine.

### Engine Change (`engine.py`)

**Before:**
```python
if settings.enable_breakeven_stop and not is_extended:
    ...breakeven pass...
```

**After:**
```python
if settings.enable_breakeven_stop:
    ...breakeven pass...
    # Inside the per-position loop, after computing be_stop:
    if is_extended and be_stop >= latest_bar.close:
        continue  # stop would trigger immediately at open; skip
```

The safety guard `be_stop >= latest_bar.close` prevents emitting a stop that would trigger the moment the market opens (i.e., stop price above current/after-hours price). During regular hours this check is unnecessary — the broker rejects such stops with 42210000 and the cycle retries next bar.

**What changes:**
- The breakeven trigger check (`latest_bar.high >= trigger`) uses the after-hours bar's high, which may differ from the regular-session high.
- `max_price = max(position.highest_price, latest_bar.high)` uses the persisted `highest_price` — correct since we persisted the true session high.
- The safety guard is: `be_stop >= latest_bar.close` → skip (would trigger at open if price hasn't recovered).

### Executor Change (`cycle_intent_execution.py`)

**Before:**
```python
if session_type in (_SessionType.AFTER_HOURS, _SessionType.PRE_MARKET):
    logger.debug("execute_cycle_intents: skipping UPDATE_STOP for %s during %s session", ...)
    continue
```

**After:**
```python
if session_type in (_SessionType.AFTER_HOURS, _SessionType.PRE_MARKET):
    action = _execute_update_stop(
        ...,
        db_only=True,
    )
    if action == "updated_pending":
        updated_pending_stop_count += 1
    if action in ("replaced", "submitted", "updated_pending") and new_stop is not None:
        # refresh cached position (same pattern as regular-hours path)
        ...
    continue  # never call broker after-hours
```

**`_execute_update_stop` gets `db_only: bool = False`:**
- When `db_only=True`: only the `updated_pending` branch executes (existing logic that updates a `pending_submit` stop order in DB without a broker call). The `replaced` and `submitted` branches are skipped.
- If no `pending_submit` stop exists (stop already at broker), returns `None` — no action.
- The existing DB writes inside `_execute_update_stop` (order + position saves + audit event) run as normal under `store_lock`.

### Data Flow

```
After-hours cycle (e.g. 20:00 ET):
  1. _apply_highest_price_updates() — persists new highest_price if bar.high > current
  2. evaluate_cycle() — breakeven pass runs, emits UPDATE_STOP(reason="breakeven") if:
       latest_bar.high >= trigger AND be_stop > position.stop_price AND be_stop < latest_bar.close
  3. execute_cycle_intents(session_type=AFTER_HOURS) — for each UPDATE_STOP intent:
       _execute_update_stop(db_only=True):
         → finds pending_submit stop order for symbol
         → updates OrderRecord.stop_price = be_stop
         → updates PositionRecord.stop_price = be_stop
         → appends AuditEvent(event_type="cycle_intent_executed", ...)
         → commits

Market open (e.g. 09:30 ET):
  4. dispatch_pending_orders() — submits pending_submit stop at be_stop ✓
     (was: submits at initial_stop_price ✗)

First 15-minute bar (09:45 ET):
  5. evaluate_cycle() — breakeven pass runs again
     be_stop == position.stop_price → regression guard prevents duplicate UPDATE_STOP
```

### Error Handling

- If `_execute_update_stop(db_only=True)` can't find a `pending_submit` stop: returns `None`, logs at DEBUG level. The morning cycle will handle it via `replace_order` as before.
- If the DB write fails: exception propagates to `execute_cycle_intents`, which already wraps the call in a try/except that logs and continues.
- If `highest_price` is not set (NULL): `max(None, bar.high)` would fail — but `_apply_highest_price_updates` runs before the cycle, so `highest_price` will always be set by the time the engine runs. Additionally, `OpenPosition.highest_price` is initialized to `entry_price` as fallback in `_load_open_positions`.

### Audit Trail

The existing `cycle_intent_executed` audit event inside `_execute_update_stop` fires for the `updated_pending` path. No new event type needed. The `db_only=True` update is distinguishable from a regular update by the absence of a `broker_order_id` on the saved order.

### `PositionStore.save()` COALESCE Safety

`PositionStore.save()` uses `COALESCE(EXCLUDED.highest_price, positions.highest_price)` on upsert. Saving a `PositionRecord` with `highest_price=None` (as happens in `_execute_update_stop`, which doesn't carry `highest_price`) will not clobber the persisted value. Verified in `repositories.py:1052`.

---

## Settings

No new settings. The feature is gated on the existing `ENABLE_BREAKEVEN_STOP` setting (`settings.enable_breakeven_stop`). If breakeven stop is disabled, no after-hours stop preloading occurs.

---

## Testing

Three test files, all in `tests/unit/`:

| Test file | What it covers |
|-----------|----------------|
| `test_engine_after_hours_breakeven.py` | Engine emits UPDATE_STOP during extended hours; safety guard prevents emit when stop >= close |
| `test_executor_after_hours_stop.py` | DB-only update writes order + position; no broker call; skips when no pending_submit exists |
| Existing `test_cycle_engine_highest_price.py` | Regression: highest_price used correctly — already passes |

### Key test scenarios

**Engine:**
- Extended hours, stop below close → UPDATE_STOP emitted ✓
- Extended hours, stop >= close → no intent emitted (safety guard) ✓  
- Extended hours, price below trigger → no intent emitted ✓
- Regular hours (existing tests unchanged) ✓

**Executor:**
- Extended hours, pending_submit stop exists → order.stop_price updated, position.stop_price updated, no broker call ✓
- Extended hours, stop already at broker (has broker_order_id) → no action ✓
- Extended hours, no stop order at all → no action ✓
- Regular hours path unchanged ✓

---

## Files Modified

- `src/alpaca_bot/core/engine.py` — 1 gate removed, 2 lines added (safety guard)
- `src/alpaca_bot/runtime/cycle_intent_execution.py` — extended-hours `continue` replaced with `db_only=True` call; `_execute_update_stop` gets `db_only: bool = False` parameter and a guard on broker-call branches
- `tests/unit/test_engine_after_hours_breakeven.py` — new
- `tests/unit/test_executor_after_hours_stop.py` — new
