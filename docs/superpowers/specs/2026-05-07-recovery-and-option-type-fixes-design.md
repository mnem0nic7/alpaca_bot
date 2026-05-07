# Recovery Report Completeness and Option Fill Type Fixes — Design Spec

**Goal:** Fix two latent correctness issues discovered during code review of the 2026-05-07 session bug fixes: (1) the zero-qty broker position guard leaves the symbol in `broker_positions_by_symbol`, causing the clearing loop to undercount and omit a diagnostic mismatch string; (2) `option_store.update_fill` receives `float` for `filled_quantity` but its signature declares `int`, violating the option contract model.

**Architecture:** Both fixes are one-line or two-line surgical changes. No new settings, no DB migrations, no new I/O. Each fix is covered with a focused TDD-first test.

**Tech Stack:** Python, pytest, psycopg, fake-store DI pattern.

---

## Bug 1 — Zero-qty guard leaves symbol in broker_positions_by_symbol

### Root cause

`broker_positions_by_symbol` is built at line 102 of `startup_recovery.py` from ALL broker positions including those with `quantity <= 0`. The guard at lines 108–127 fires correctly: it logs a warning, appends a mismatch string, emits an `AuditEvent`, and calls `continue`. But it does NOT remove the skipped symbol from `broker_positions_by_symbol`.

The clearing loop at lines 204–209 checks `if position.symbol not in broker_positions_by_symbol`. Because the zero-qty symbol IS still in the dict, the loop does not:
- Record "local position missing at broker: SYMBOL" as a mismatch
- Increment `cleared_position_count`

**Functional consequence:** The local position IS cleared from the DB by `position_store.replace_all()` (which replaces all records for trading_mode/strategy_version with only `synced_positions`, which does not include the skipped symbol). So no orphan persists. The bug is in the report, not the state:
- `cleared_position_count` undercounts by 1
- The "local position missing at broker" diagnostic is suppressed, making post-incident investigation harder

### Fix

After appending the guard's mismatch and emitting the AuditEvent, pop the symbol from `broker_positions_by_symbol` before `continue`:

```python
broker_positions_by_symbol.pop(broker_position.symbol, None)
continue
```

This allows the clearing loop to correctly count and diagnose local positions that were present but no longer held at the broker at non-positive quantity.

### Test

A new test adds a local `PositionRecord` for SKYT alongside the existing test's zero/negative-qty broker position. After `recover_startup_state()`:
- "local position missing at broker: SKYT" appears in mismatches
- `report.cleared_position_count == 1`
- "SKYT" is NOT in synced positions (already verified by existing test)
- The existing negative-qty test continues to pass unchanged

---

## Bug 2 — OptionOrderRecord.filled_quantity receives float

### Root cause

`trade_updates.py` previously parsed `filled_qty` with `_optional_int()` (int-truncating). After the 2026-05-07 fix it now parses with `_optional_float()`. This is correct for equities, which can have fractional shares.

However, the option routing path at lines 104–111 passes `normalized.filled_qty` (now `float | None`) directly to `option_store.update_fill(... filled_quantity: int ...)`. Option contracts are discrete whole-number quantities — there is no such thing as a fractional option contract. The `OptionOrderRecord.filled_quantity: int | None`, the `option_orders.filled_quantity INTEGER` DB column, and the `_row_to_option_order_record` deserializer (`int(row[15])`) are all semantically correct for options and should not change.

**Functional consequence:** Python doesn't enforce annotations at runtime, and Postgres `INTEGER` silently accepts a whole-number float (e.g. `1.0` → `1`). For paper trading with whole-contract fills, this produces no observable error today. But the type annotation mismatch is a latent bug: if a float with a fractional part were somehow passed (shouldn't happen for options but is now possible via the call site), Postgres would raise a `DataError`, crashing the trade update handler.

### Fix

Cast at the call site in `trade_updates.py`:

```python
filled_quantity=int(normalized.filled_qty),
```

This makes the semantic contract explicit (option fills are whole contracts) and ensures the call site matches the method signature. No changes to `OptionOrderRecord`, the DB schema, or the deserializer.

### Test

A new test in `test_trade_updates.py` uses a fake option store and verifies that when an option fill with `filled_qty="1.0"` arrives via `_apply_trade_update_locked`, `option_store.update_fill` is called with `filled_quantity=1` (an `int`, not a float). The test uses a `RecordingOptionStore` following the project's fake-store pattern.

---

## Files

| File | Change |
|---|---|
| `src/alpaca_bot/runtime/startup_recovery.py` | Add `broker_positions_by_symbol.pop(...)` before `continue` in qty<=0 guard |
| `tests/unit/test_startup_recovery.py` | Add test: local SKYT position + zero-qty broker = correct cleared_position_count and mismatch |
| `src/alpaca_bot/runtime/trade_updates.py` | Cast `normalized.filled_qty` to `int` at `option_store.update_fill` call site |
| `tests/unit/test_trade_updates.py` | Add test: option fill with float filled_qty calls update_fill with int filled_quantity |

---

## Out of scope

- Changing `OptionOrderRecord.quantity: int` to `float` — option contracts are always integers; this would be semantically wrong
- Adding a DB migration for `option_orders.filled_quantity` — column type is correct
- Fixing the `cleared_position_count` undercount for negative-qty (non-zero) broker positions alongside local positions — the existing test already covers qty=-2, and the guard's own mismatch string is present; the "local position missing at broker" string for the qty=-2 case is an extra diagnostic. The pop fix covers both qty=0 and qty<0 uniformly.
