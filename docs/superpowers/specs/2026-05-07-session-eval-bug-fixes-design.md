# Session Evaluation Bug Fixes Design — 2026-05-07

## Context

Live evaluation of the paper trading session on 2026-05-07 exposed three active production bugs.
This spec covers the two bugs causing immediate financial risk and system instability. A third
(gap-down stop handling) is noted for a future plan.

---

## Bug 1: Recovery crash loop from negative broker quantity

**Symptom:** 236 `recovery_exception` audit events today. Every supervisor cycle (~90s), the
`recover_startup_state()` function in `startup_recovery.py` crashes with:

```
psycopg.errors.CheckViolation: new row for relation "orders" violates check constraint
"orders_quantity_check"
DETAIL: Failing row contains (startup_recovery:v1-breakout:2026-05-07:SKYT:stop, SKYT, sell,
stop, pending_submit, -2.0000, ...)
```

**Root cause:** SKYT was exited at EOD yesterday but a short position of -2 shares remained at
Alpaca (likely from a double-sell: the flatten exit and the stop order both executed). The recovery
code at `startup_recovery.py:107` iterates over `broker_open_positions` and blindly copies
`broker_position.quantity` into a new `OrderRecord`. When that quantity is negative (short
position), the DB constraint `CHECK (quantity >= 0)` rejects it. The exception is caught at the
supervisor level but causes recovery to be skipped entirely — position reconciliation is broken
every cycle.

**Effect:** No mismatch detection. If any open positions get out of sync with Alpaca state, the
supervisor will not notice. Additionally, 236 DB rollbacks per day add noise to logs.

**Fix:** In the `broker_open_positions` loop in `startup_recovery.py`, add a guard before
processing any position:

```python
if broker_position.quantity <= 0:
    _log.warning(
        "startup_recovery: skipping broker position %s with non-positive qty=%s "
        "(possible short or stale position — manual review required)",
        broker_position.symbol,
        broker_position.quantity,
    )
    mismatches.append(
        f"broker position non-positive quantity skipped: {broker_position.symbol} qty={broker_position.quantity}"
    )
    continue
```

This skips the position, records it as a mismatch (visible in the audit log), and allows the rest
of recovery to proceed normally.

**Test:** Provide a `broker_open_positions` list containing one position with `quantity=-2` and one
normal long. Verify `recover_startup_state()` completes without raising, the negative-qty symbol
appears in `report.mismatches`, and the normal position is processed correctly.

---

## Bug 2: Fractional fill quantities truncated to zero

**Symptom:** MSFT and QQQ have `status=filled` entries with `filled_quantity=0.0000` in the
orders table. Their stop orders were created with `quantity=0`, causing Alpaca to reject them
("qty must be > 0"). Both positions are currently open with **no stop protection**.

**Root cause:** `trade_updates.py` defines `TradeUpdate.filled_qty` as `int | None` and parses it
with `_optional_int()` (line 482). `_optional_int()` calls `int(float(value))`, which truncates
fractional quantities: `int(0.7737) == 0`. When Alpaca fills an entry for 0.7737 shares of MSFT,
the trade update arrives with `filled_qty=0.7737`, but the handler stores it as `0`. The stop
order then uses `quantity=0` and is rejected at dispatch.

**Fix:** Change `TradeUpdate.filled_qty` and `TradeUpdate.quantity` from `int | None` to
`float | None`. Change the parsing at lines 481-482 from `_optional_int()` to `_optional_float()`.
Fix the `%d` log format at line 189 to `%g`. Three-line change, no DB schema change needed (the
`NUMERIC(18,4)` migration for `filled_quantity` is already applied as migration 014).

```python
# TradeUpdate dataclass (lines 50-51):
quantity: float | None
filled_qty: float | None

# Parse (lines 481-482):
quantity=_optional_float(payload.get("qty")),
filled_qty=_optional_float(payload.get("filled_qty")),

# Log format (line 189):
"trade_updates: entry fill %s — order_qty=%g filled_qty=%s fill_price=%s",
```

**Test:** Call `_normalize_payload()` with a payload containing `filled_qty=0.7737` and verify the
resulting `TradeUpdate.filled_qty == pytest.approx(0.7737)` (not 0). Separately, verify that a
full `apply_trade_update()` call for a fractional entry fill results in the position quantity being
set to `0.7737` and the protective stop quantity being `0.7737`.

---

## Out of Scope (future plan)

**Gap-down stop handling (ATEX):** ATEX entered after-hours at $51.96, stop=$49.36. Today's
open was $46.25 (gap down). Alpaca rejected the stop with error 42210000 ("stop price must be
less than current price"). The position is open with no stop protection. Correct handling: when
`order_dispatch` receives error 42210000 for a stop, it should immediately submit a market exit
for the position quantity. This is a separate, more complex design involving dispatch→position
coupling and will be addressed in a dedicated plan.

---

## File Map

| File | Change |
|---|---|
| `src/alpaca_bot/runtime/startup_recovery.py` | Add negative-qty guard in broker positions loop |
| `tests/unit/test_startup_recovery.py` | Add test: negative broker qty is skipped, logged, and reported as mismatch |
| `src/alpaca_bot/runtime/trade_updates.py` | `quantity`/`filled_qty` type `int|None` → `float|None`; parsing `_optional_int` → `_optional_float`; log format `%d` → `%g` |
| `tests/unit/test_trade_updates.py` | Add test: fractional `filled_qty` in payload is preserved as float; add test: apply_trade_update with fractional fill produces correct position qty and stop qty |

---

## Architecture

No new settings. No DB migrations. No I/O introduced into the engine. Both fixes are in runtime
and storage layers only. `evaluate_cycle()` remains pure.

**Safety:** Both fixes are fail-safe. The negative-qty guard skips unusual positions and records
them as mismatches — the same path the code already uses for other anomalies. The
`_optional_float` change is strictly more permissive — any value that was previously handled (int)
is still handled correctly; fractional values that were previously truncated are now preserved.

**Paper vs. live:** Behaviour is identical in both modes. The bugs are mode-agnostic (they depend
only on what Alpaca reports, not on paper/live flag).
