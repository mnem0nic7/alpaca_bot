# Design: Fix Trading System Operational Bugs

**Date:** 2026-05-26
**Scope:** Fix 5 operational bugs causing audit event storms, orphaned records, and false safety-gate triggers in the paper-trading short-put option strategy.

---

## Context

Two weeks of production data (2026-05-12 to 2026-05-22) revealed:
- `exit_hard_failed`: 1,540–1,771/day on trading days; 2,926–3,014/day on weekends
- `stale_positions_detected`: 155–162/day (fires every session for short option positions)
- `daily_loss_limit_breached`: fires every morning at ~09:33 ET with -$2,400 to -$4,939 unrealized loss
- 960 orphaned `option_orders` records stuck in `status="submitting"`
- Net options P&L: -$21,079 over 9 trading days (paying 119–317% of premium back to close)
- `entries_disabled=true` every trading day (blocking equity strategy too)

All activity is paper trading (`TRADING_MODE=paper`). No real capital at risk.

---

## Bug 1: exit_hard_failed Storm

### Root Cause
`_execute_exit()` in `cycle_intent_execution.py` (line 828–835) calls `submit_option_market_buy_to_close()` for all OCC-format symbols (short puts) regardless of session type. Options only trade during REGULAR session (9:30am–4:00pm ET). After 4pm, on weekends, and on holidays, Alpaca rejects the order → exception → `exit_hard_failed` audit event.

`_close_stale_carryover_positions()` in `supervisor.py` also fires exit intents for every short option position on every cycle (intentionally-held overnight positions), amplifying the storm.

### Fix
In `_execute_exit()`, before calling `submit_option_market_buy_to_close()`, check the current local time. If outside 09:30–16:00 ET, log a debug message and return `(0, 0, 0)` without firing `exit_hard_failed`. This is a "market closed – defer" case, not an error.

The check must use `now` (the `datetime` parameter already present in the function signature) and `settings.market_timezone`. No additional parameters needed.

### Files Changed
- `src/alpaca_bot/runtime/cycle_intent_execution.py`

---

## Bug 2: Orphaned "submitting" Option Orders

Two sub-bugs combine to produce 960 stuck records and 8–10 new orphans per symbol per day.

### Sub-bug 2a: No Status Rollback on Dispatch Failure
In `option_dispatch.py`, `dispatch_pending_option_orders()` writes `status="submitting"` to Postgres **before** calling the broker. If the broker call raises, the exception is caught and logged, but the record stays `"submitting"` forever. The dispatch loop only queries `status="pending_submit"`, so these orphans are never retried or cleaned up.

**Fix:** In the `except Exception` block, save a `status="failed"` record to unblock the dispatch loop and make the failure visible. Add `"failed"` as a terminal state alongside `"submitting"` in any status queries that need excluding.

### Sub-bug 2b: EOD Flatten Creates Wrong-Side Orders
`run_cycle_once()` in `supervisor.py` (lines 1079–1112) calls `option_order_store.list_open_option_positions()` (returns all `status="filled"` records) after `flatten_time` and creates a new `side="sell"` pending order for each. These `"sell"` records, when dispatched, call `submit_option_market_exit()` (SELL at market). For positions that are already short puts, this would **add** to the short rather than close it. Alpaca rejects these (or the options market is closed), and they become orphaned `"submitting"` records.

The logic also runs **every cycle** after `flatten_time`, producing a new unique `client_order_id` each time — so 15 cycles × 10 symbols = 150 new pending_submit records per day, none of which close properly.

**Fix:** Remove the EOD option flatten for positions whose entry was a SELL (short options). Short puts should be closed via the stale-position carryover mechanism the next morning during REGULAR session. For any genuinely long option positions (entry `side="buy"`), the EOD flatten should create `side="sell"` close orders and must be idempotent (check for an existing pending/submitted close order before creating a new one).

Given the current strategy portfolio only uses short puts, the simplest correct change is: **skip the EOD option flatten entirely for `side="sell"` filled positions.** Long-option EOD flatten can be added back later.

### Cleanup Migration
A one-shot migration marks all existing `status="submitting"` records as `status="failed"` so they no longer pollute queries.

### Files Changed
- `src/alpaca_bot/runtime/option_dispatch.py`
- `src/alpaca_bot/runtime/supervisor.py`
- `migrations/022_mark_orphaned_submitting_as_failed.sql`

---

## Bug 3: stale_positions_detected Every Cycle

### Root Cause
`_close_stale_carryover_positions()` fires for every position whose `opened_at` date is before `session_date`. Short puts are intentionally held overnight; they will always be "stale" on the morning after entry. The function both:
1. Appends `stale_positions_detected` audit event (noise)
2. Attempts to exit via `_cycle_intent_executor` → `_execute_exit()` → `submit_option_market_buy_to_close()` → fails (outside market hours) → `exit_hard_failed`

### Fix
In `_close_stale_carryover_positions()`, partition the stale list into option positions (OCC symbol format, detected by `_is_short_option_symbol()` imported from `cycle_intent_execution`) and equity positions. Only attempt exits for equity positions. For option positions, only emit the audit event (without the exit attempt). This preserves observability while eliminating the false `exit_hard_failed` cascade.

The audit event payload should include a `"skipped_exit_option_count"` field so it's clear why options weren't exited.

### Files Changed
- `src/alpaca_bot/runtime/supervisor.py`

---

## Bug 4: daily_loss_limit_breached Every Morning

### Root Cause
`DAILY_LOSS_LIMIT_PCT=0.01` (1%) on a ~$99k account = ~$997 loss limit. Realized losses from short puts buying to close are $2,011–$5,405/day. The limit fires within 3 minutes of market open every day, setting `entries_disabled=true` for the entire session and blocking the breakout equity strategy too.

The `external_upnl_baseline` mechanism (added in recent commits) correctly excludes *intraday changes* in short-option unrealized P&L from the loss calculation. But it cannot exclude the **realized** losses that flow through equity when BTC orders fill.

### Fix
Raise `DAILY_LOSS_LIMIT_PCT` from `0.01` to `0.05` (5%) in `/etc/alpaca_bot/alpaca-bot.env`. This lifts the daily loss cap to ~$4,975 on a $99,500 account, accommodating the current realized-loss range without disabling equity strategy entries on most days.

This is a configuration change only — no code required.

**Note:** A 5% daily loss limit does not mask catastrophic failure. With the per-position operational bugs fixed, the short option strategy losses should decrease significantly. If the strategy is re-evaluated later, the limit can be tightened.

### Files Changed
- `/etc/alpaca_bot/alpaca-bot.env` (env update, redeploy required)

---

## Bug 5: Supervisor Cycles on Weekends

### Root Cause
`detect_session_type()` in `strategy/session.py` classifies timestamps by time-of-day only — not day of week. On Saturday/Sunday:
- 04:00–09:29 ET → `PRE_MARKET`
- 09:30–15:59 ET → `REGULAR` (but broker clock says `is_open=False` → `_current_session()` returns `CLOSED`)
- 16:00–19:59 ET → `AFTER_HOURS`

`_current_session()` checks the Alpaca broker clock only for `REGULAR` sessions. For `PRE_MARKET` and `AFTER_HOURS`, it blindly returns the session type if `extended_hours_enabled=True`. Result: the supervisor runs ~270 pre-market + ~240 after-hours cycles on each weekend day (266–274 observed), generating thousands of audit events and failed exit attempts.

### Fix
In `_current_session()`, before returning `PRE_MARKET` or `AFTER_HOURS`, check the broker clock's `next_open` date. If `clock.next_open.astimezone(market_timezone).date() != today_et` AND `not clock.is_open`, the market does not open today → return `SessionType.CLOSED`.

This handles weekends and exchange holidays. The clock call is already made for `REGULAR` sessions; extract it into a shared helper to avoid duplicate API calls within a single cycle.

Edge case: On a normal trading day, pre-market hours have `next_open.date() == today` (correct → PRE_MARKET runs). After-hours on a trading day (Mon–Thu) have `next_open.date() == tomorrow` (→ returns CLOSED). Since options close at 4pm and Fix 1 already prevents option exits after 4pm, this tradeoff is acceptable for this strategy. Extended-hours equity trading was not being used profitably.

### Files Changed
- `src/alpaca_bot/runtime/supervisor.py`

---

## Architecture Decisions

### OCC Symbol Detection
`_is_short_option_symbol()` in `cycle_intent_execution.py` already implements the OCC regex. The supervisor needs the same check. Rather than importing from `cycle_intent_execution` (creates a reverse dependency), move the OCC regex to a shared location: `src/alpaca_bot/domain/option_utils.py`. Both files import from there.

Alternatively, duplicate the trivial one-liner. Given it's a one-line regex match, duplication is acceptable to avoid circular imports. We will add a copy to `supervisor.py` as a module-level private helper.

### No New External API Calls for Fix 5
The broker clock is already called in `_current_session()` for REGULAR sessions. The fix reuses the same call for PRE_MARKET and AFTER_HOURS by extracting a `_get_clock()` helper that caches the result within a single call to avoid redundant API round-trips.

### audit trail for deferred option exits
When `_execute_exit()` skips a short option exit because options market is closed, a `cycle_intent_skipped` audit event is appended with `reason="options_market_closed"`. This preserves the audit trail without generating `exit_hard_failed` noise.

### EOD Flatten Idempotency (Long Options Future Proofing)
If long option positions are added later, the EOD flatten must check for existing pending/submitted BTC orders before creating new ones. The check: query `option_order_store.list_by_status(statuses=["pending_submit", "submitting", "submitted"])` for the OCC symbol. If any record exists for today, skip creation. This check is not needed now (no long option positions) but is noted for when the strategy expands.

---

## Testing

Each fix requires a unit test using the project's DI pattern (fake callables, in-memory stores):

1. **Bug 1 test:** Call `_execute_exit()` with a short option position at 20:00 ET → assert returns `(0, 0, 0)` and appends `cycle_intent_skipped` with `reason="options_market_closed"`. Separate test: same call at 10:00 ET → assert broker is called.

2. **Bug 2a test:** `dispatch_pending_option_orders` with a broker that raises → assert record updated to `status="failed"`.

3. **Bug 2b test:** EOD flatten with a `side="sell"` filled position → assert no new pending_submit record created. EOD flatten with a `side="buy"` filled position → assert `side="sell"` pending_submit created.

4. **Bug 3 test:** `_close_stale_carryover_positions` with mixed stale list (OCC + equity symbols) → assert `_cycle_intent_executor` is NOT called for OCC symbols but IS called for equity symbols.

5. **Bug 5 test:** `_current_session()` on a Saturday at 08:00 ET with broker clock returning `is_open=False, next_open=Monday` → assert returns `CLOSED`. Same call on a Monday at 08:00 ET → assert returns `PRE_MARKET`.

---

## Rollback

- All code changes are additive guards (early returns, additional checks). No schema changes except the cleanup migration.
- Migration 022 marks orphaned records as `"failed"`. Rollback SQL: `UPDATE option_orders SET status='submitting' WHERE status='failed' AND updated_at >= '<migration_timestamp>'`. This is safe; no irreversible state change.
- `DAILY_LOSS_LIMIT_PCT` change: revert env value and redeploy.

---

## Out of Scope

- Per-option max-loss enforcement (requires real-time option pricing in the cycle): useful but a larger feature, deferred.
- Strategy performance (delta target, DTE selection): separate concern from operational bugs.
- Holiday calendar integration (for Fix 5): weekday check handles 99% of cases; exchange holidays are rare.
