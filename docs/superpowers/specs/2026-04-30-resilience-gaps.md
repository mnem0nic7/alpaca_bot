# Resilience Gaps — Spec

**Date:** 2026-04-30  
**Status:** Approved for implementation

## Problem

Seven resilience gaps were identified in a codebase audit. Three are critical (direct financial risk on a live account), two are high (silent data loss), and two are medium (correctness / defensiveness).

---

## Gap 1 — Order double-submission (Critical)

**Root cause:** `dispatch_pending_orders` fetches `pending_submit` orders under lock, releases the lock, calls the broker, then updates the DB. A crash between the broker call and the DB commit leaves the order as `pending_submit` forever. On the next startup the order is re-submitted.

**Desired behaviour:** Orders that have been handed to the broker but not yet confirmed are in `submitting` status. On restart, startup recovery reconciles `submitting` orders against broker open orders and either confirms or resets them.

**Design:**
- Before calling the broker, stamp the order as `submitting` (under lock, committed).
- `startup_recovery.py` already lists active orders (via `ACTIVE_ORDER_STATUSES`). Add `"submitting"` to that list.
- `_is_never_submitted()` test: `status == "pending_submit"` with no `broker_order_id` — keep as-is.
- For `submitting` orders not matched in `broker_open_orders` at recovery time: reset to `pending_submit`. These were in-flight when the process died and need to be re-dispatched. Alpaca's client_order_id idempotency means a re-submission that Alpaca already received will return the same order without duplication.
- Audit event: `"order_dispatch_submitting"` logged when status is stamped.
- No new env vars, no schema migration (status is a free-text column).

---

## Gap 2 — UPDATE_STOP unrecognized exception swallowed (Critical)

**Root cause:** `_execute_update_stop` in `cycle_intent_execution.py` catches all exceptions on `replace_order()`. If the phrase list doesn't match, it logs and returns `None` silently. No audit event. No notifier alert.

**Desired behaviour:** Unrecognized exceptions fire a `stop_update_failed` audit event and send a notifier alert. This gives the operator visibility that a position may be losing stop protection.

**Design:**
- Add `notifier: Notifier | None = None` parameter to `execute_cycle_intents` and `_execute_update_stop`.
- Wire through from `RuntimeSupervisor._cycle_intent_executor` call (already passes other kwargs through `**dispatch_kwargs` pattern — check exact wiring).
- On unrecognized exception: append `stop_update_failed` audit event with symbol, error, timestamp; fire notifier if available; return `None` as before (don't raise — other symbols' intents must still process).

---

## Gap 3 — Trade update stream has no heartbeat (Critical)

**Root cause:** Stream thread is considered "healthy" iff `is_alive()` returns True. A clean close (WebSocket code 1000, no exception) makes `stream.run()` return normally, thread exits, `_stream_thread = None`, and the next `run_forever` iteration detects the dead thread and restarts with backoff. But there is no detection of a live thread with a stale connection that stopped delivering events.

**Desired behaviour:** The supervisor tracks when the last trade update event was received. During market hours, if the stream thread is alive but no event has arrived in `STREAM_HEARTBEAT_TIMEOUT_SECONDS` seconds, log a CRITICAL warning and fire a notifier alert. Do not auto-restart on stale heartbeat alone (auto-restart only for dead threads) — this avoids false positives during genuinely quiet markets.

**Design:**
- Add `_last_stream_event_at: datetime | None = None` to `RuntimeSupervisor.__init__`.
- Add `on_event: Callable[[], None] | None = None` to `attach_trade_update_stream` (backward-compatible, default None).
- In the stream handler, call `on_event()` before `apply_trade_update()`.
- In `startup()`, pass `on_event=self._record_stream_event` to `_stream_attacher`.
- `_record_stream_event` sets `_last_stream_event_at = datetime.now(timezone.utc)`.
- In `run_forever`, add a staleness check after the dead-thread watchdog: if thread is alive AND `_last_stream_event_at` is not None AND session is not CLOSED AND `(now - _last_stream_event_at) > timedelta(seconds=STREAM_HEARTBEAT_TIMEOUT_SECONDS)`: log CRITICAL and fire notifier (once per staleness window, reset flag when event arrives).
- `STREAM_HEARTBEAT_TIMEOUT_SECONDS = 300` (5 minutes). No new env var — hard constant since it's an internal monitoring threshold.
- Audit event: `"stream_heartbeat_stale"` appended when alert fires.

---

## Gap 4 — daily_realized_pnl silently understates losses (High)

**Root cause:** `OrderStore.daily_realized_pnl` uses a LEFT JOIN-style query. Rows where the entry fill is missing contribute `row[1] = None`. These are silently skipped. The loss-limit check therefore underestimates the session's loss.

**Desired behaviour:** Missing-entry rows contribute a conservative (negative) P&L estimate. Log at ERROR level (not WARNING). This ensures the loss-limit check fails safe when bookkeeping is incomplete.

**Design:**
- Change the sum to include missing-entry rows: `pnl = -(float(row[2]) * int(row[3]))` for rows where `row[1] is None`. This is equivalent to assuming the position was acquired at twice the exit price — the most conservative assumption, guaranteeing the contribution is negative.
- Change log from `logger.warning` to `logger.error`.
- No interface change — `daily_realized_pnl` still returns a float.

---

## Gap 5 — sizing.py has no direct unit tests (High)

**Root cause:** `calculate_position_size` is exercised only indirectly via engine tests.

**Desired behaviour:** Direct unit tests covering all edge cases with known inputs and expected outputs.

**Tests to add in `tests/unit/test_position_sizing.py`:**
1. Normal case: equity=10000, entry=100, stop=95, risk_pct=0.01, max_pct=0.1 → qty=20
2. Stop >= entry: raises ValueError
3. Tiny equity (returns 0 when floor is < 1): equity=100, entry=200, stop=195, risk_pct=0.01 → qty=0
4. Max-notional cap applied: large equity, small stop distance → qty capped by max_position_pct
5. Equity = 0: risk_budget=0, quantity=0 → returns 0
6. Equity < 0: risk_budget negative, floor of negative = -1, < 1 → returns 0

---

## Gap 6 — Startup recovery stop-queuing ignores pending_submit entries (Medium)

**Root cause:** When `recover_startup_state` queues a stop for a brand-new broker position, it only checks `active_stop_symbols`. It does not check for `pending_submit` entry orders for the same symbol with no `broker_order_id`. If such an order exists, queuing a stop for a not-yet-confirmed position is premature; when the entry eventually dispatches (re-submission), the trade-update stream will queue a stop upon fill.

**Desired behaviour:** Don't queue a recovery stop for a symbol if there is already an unsubmitted (`pending_submit`, no `broker_order_id`) entry order for that symbol.

**Design:**
- Build `pending_entry_symbols = {o.symbol for o in local_active_orders if o.intent_type == "entry" and o.status in ("pending_submit", "submitting") and not o.broker_order_id}`.
- In the stop-queuing loop: `if sym not in active_stop_symbols and sym not in pending_entry_symbols: queue stop`.
- Log a WARNING when a symbol is skipped due to pending entry.

---

## Gap 7 — Advisory lock not verified after reconnect (Medium)

**Root cause:** `reconnect_runtime_connection` raises `RuntimeError` if lock re-acquisition fails. This propagates through `run_cycle_once` into the `except Exception` handler in `run_forever`, which increments `_consecutive_cycle_failures` and retries after 60 s. For up to 9 minutes (9 failures before the 10-failure exit), the supervisor runs cycles without holding the advisory lock.

**Desired behaviour:** If lock re-acquisition fails after a reconnect, the supervisor exits immediately with `SystemExit(1)`. Docker will restart it; re-acquiring the lock on a fresh connection is the correct recovery path.

**Design:**
- Add `LockAcquisitionError(RuntimeError)` to `bootstrap.py`.
- Raise `LockAcquisitionError` instead of bare `RuntimeError` in `reconnect_runtime_connection`.
- In `run_forever`, add a `except LockAcquisitionError` clause **before** `except Exception` that logs CRITICAL and raises `SystemExit(1)`.
- `bootstrap_runtime` already raises a bare `RuntimeError` for startup lock failure — this is fine (process exits before `run_forever` is reached).
- No change to `run_cycle_once`: it lets the `LockAcquisitionError` propagate naturally.

---

## Constraints

- No new env vars except if forced by a gap above (none required).
- No DB schema migration. The `status` column in `orders` is already a free-text `VARCHAR`; `submitting` is a new value, not a new column.
- `evaluate_cycle()` remains pure (none of these changes touch it).
- Paper vs live behaviour: identical — all changes are in dispatch/runtime layers, not in the order-submission path logic itself.
- Each gap is independently testable and deployable.
