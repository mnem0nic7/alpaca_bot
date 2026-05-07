# After-Hours Bug Fixes: Stale Signal Guard + Extended-Hours Stop Protection

## Background

The first live after-hours trade (SOUN momentum, 2026-05-07) exposed two bugs that together
produced an unprotected position with an upside-down stop:

1. A momentum signal detected at ~3:30 PM ET was queued but blocked by the regular-session
   flatten gate. When after-hours entries reopened at 5:42 PM ET (~132 minutes later), the
   stale signal fired with its original geometry — SOUN had dropped from ~$9.14 to $8.49,
   producing a `stop_price=$9.14` that was above the fill price.

2. `trade_updates.py` immediately created a `pending_submit` stop order on fill.
   `dispatch_pending_orders` → `_submit_order` unconditionally called
   `broker.submit_stop_order()`, which Alpaca rejects during extended hours. Both attempts
   returned `status=error`. Position is currently unprotected.

---

## Bug 1 — Stale Cross-Session Signal

### Root Cause

`engine.py` (lines 504–516) walks back through bars to find the last bar in the regular
entry window when `session_type is AFTER_HOURS`. There is no check on the age of that bar.
A signal detected at 3:05 PM ET can fire at 8:00 PM ET using the same stale geometry.

### Fix

After the AFTER_HOURS signal_index walk-back, reject the signal if the bar is older than
`settings.extended_hours_signal_max_age_minutes` minutes:

```python
if session_type is SessionType.AFTER_HOURS:
    signal_index = next(
        (
            i
            for i in range(len(bars) - 1, -1, -1)
            if _is_entry_window(bars[i].timestamp, settings, SessionType.REGULAR)
        ),
        -1,
    )
    if signal_index < 0:
        continue
    # Reject signals from an earlier session
    signal_bar_age_s = (
        now - bars[signal_index].timestamp.astimezone(timezone.utc)
    ).total_seconds()
    if signal_bar_age_s > settings.extended_hours_signal_max_age_minutes * 60:
        continue
else:
    signal_index = len(bars) - 1
```

**Default threshold: 60 minutes.** This allows signals from the last hour of the regular
session (3:05–4:00 PM ET) to carry into the 4:05 PM AH open, matching normal AH trading
intent. SOUN's 132-minute-old signal would have been rejected.

### New Config Field

```python
# Settings dataclass
extended_hours_signal_max_age_minutes: int = 60

# from_env()
extended_hours_signal_max_age_minutes=int(
    values.get("EXTENDED_HOURS_SIGNAL_MAX_AGE_MINUTES", "60")
),
```

---

## Bug 2 — Extended-Hours Stop Protection

### Root Cause (two parts)

**Part A — Stop dispatch during AH:**
`dispatch_pending_orders` → `_submit_order` calls `broker.submit_stop_order()` for `intent_type="stop"` regardless of `session_type`. Alpaca rejects stop orders during extended hours.

**Part B — No per-cycle stop check in position loop:**
`engine.py`'s position loop does `if is_extended: continue` — skipping ALL stop management,
including checking whether `close <= stop_price`. Positions entered in extended hours receive
zero downside protection until the regular session opens.

### Fix Part A — Skip stop dispatch during AH

In `dispatch_pending_orders` (order_dispatch.py), after stale-order checks but before the
"submitting" mark, skip stop orders during extended hours:

```python
if order.intent_type == "stop" and session_type is not None:
    from alpaca_bot.strategy.session import SessionType as _ST
    if session_type in (_ST.PRE_MARKET, _ST.AFTER_HOURS):
        continue  # Leave as pending_submit; submit at regular session open
```

This means the stop record remains in Postgres and will be dispatched when the regular
session opens and `session_type=REGULAR`.

### Fix Part B — Soft stop in engine position loop

Replace `if is_extended: continue` with a soft-stop check:

```python
if is_extended:
    if position.stop_price > 0 and latest_bar.close <= position.stop_price:
        intents.append(
            CycleIntent(
                intent_type=CycleIntentType.EXIT,
                symbol=position.symbol,
                timestamp=now,
                reason="stop_breach_extended_hours",
                limit_price=round(
                    latest_bar.close * (1 - settings.extended_hours_limit_offset_pct), 2
                ),
                strategy_name=strategy_name,
            )
        )
    continue
```

**Why limit sell, not stop order:** A limit sell executes at or ABOVE the limit price —
it caps upside, not downside. The correct approach is an engine-driven EXIT intent when
`close <= stop_price`, dispatched as a limit sell at a slight discount to current close.
The existing `extended_hours_limit_offset_pct` (default 0.1%) provides the execution
buffer.

**SOUN immediate recovery:** On the first cycle after deployment, `$8.49 close <= $9.14 stop`
fires immediately, emitting EXIT for SOUN at `limit_price ≈ 8.49 * 0.999 ≈ $8.48`.

---

## Pre-Market Symmetry

Pre-market positions face the same risk: standing stop orders queued before the pre-market
session would also fail. Part A applies to `PRE_MARKET` as well (already in the code above).
Part B (soft-stop check) applies to `is_extended` which covers both `PRE_MARKET` and
`AFTER_HOURS`.

---

## No-Op Interactions

- **UPDATE_STOP blanket skip in `execute_cycle_intents`** (lines 123–132): Currently dead
  code — the engine already skips all stop management during extended hours. After this fix
  the soft-stop EXIT path is the only position management during AH; UPDATE_STOP remains
  suppressed, which is correct.
- **Pending stop record in Postgres**: After Part A, the stop order stays `pending_submit`.
  On the regular session open, `dispatch_pending_orders` will submit it. If the position was
  already closed by the soft-stop EXIT, the stop record will be cancelled by the reconciliation
  loop (already handles this case for closed positions).
- **`trade_updates.py` still creates the pending stop**: No change needed. The pending stop
  is correct infrastructure — it just won't be dispatched to Alpaca until regular hours.

---

## Testing

**`tests/unit/test_engine_extended_hours.py`** (extend existing):
- Stale signal rejected when `signal_bar_age > EXTENDED_HOURS_SIGNAL_MAX_AGE_MINUTES * 60`
- Fresh signal (within threshold) fires normally
- Soft-stop EXIT emitted when `close <= stop_price` during AH
- No EXIT when `close > stop_price` during AH
- Position with `stop_price=0` does not emit EXIT during AH

**`tests/unit/test_order_dispatch_extended_hours.py`** (extend existing):
- Stop order skipped (left `pending_submit`) when `session_type=AFTER_HOURS`
- Stop order skipped when `session_type=PRE_MARKET`
- Stop order submitted normally when `session_type=REGULAR`

**`tests/unit/test_settings_extended_hours.py`** (extend existing):
- `EXTENDED_HOURS_SIGNAL_MAX_AGE_MINUTES` parses correctly; defaults to 60

---

## Files Modified

| File | Change |
|---|---|
| `src/alpaca_bot/config/__init__.py` | Add `extended_hours_signal_max_age_minutes: int = 60` |
| `src/alpaca_bot/core/engine.py` | Bug 1: staleness check after walk-back; Bug 2: soft-stop in position loop |
| `src/alpaca_bot/runtime/order_dispatch.py` | Skip stop dispatch during AH/PM |
| `tests/unit/test_engine_extended_hours.py` | Staleness + soft-stop tests |
| `tests/unit/test_order_dispatch_extended_hours.py` | AH stop-skip tests |
| `tests/unit/test_settings_extended_hours.py` | New config field test |

---

## Deployment Notes

SOUN position (1 share, $8.49, `stop_price=$9.14`) will be closed automatically by the
soft-stop engine check on the first cycle after deployment: `8.49 <= 9.14` → EXIT emitted
at limit_price ≈ $8.48. No manual intervention required if deployed before AH session ends;
otherwise closed at regular session open.
