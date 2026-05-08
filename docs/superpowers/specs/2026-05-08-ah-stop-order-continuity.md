# After-Hours Stop Order Continuity and Dispatch Observability

## Background

The May 7 AH bug fix (commit 3858c9a) addressed three issues:

1. Stale cross-session signal guard (engine.py — signal age check)
2. Stop dispatch skip during AH/PM (order_dispatch.py — `continue` for session_type in AH/PM)
3. Soft-stop EXIT during extended hours (engine.py — `close <= stop_price` check)

Post-fix code review exposes two remaining gaps.

---

## Bug 3 — AH Stop Orders Expire at Regular-Session Open

### Root Cause

`order_dispatch.py` has a stale-stop expiration guard (lines 158–218):

```python
if order.intent_type == "stop":
    ref_ts = order.signal_timestamp if order.signal_timestamp is not None else order.created_at
    created_date_et = ref_ts.astimezone(settings.market_timezone).date()
    if ref_ts is not None and created_date_et < session_date_et:
        # EXPIRE
        ...
        continue
```

When 13 AH positions were opened on May 7 evening, `trade_updates.py` created a
`pending_submit` stop for each fill with `signal_timestamp` = AH fill time (e.g., 17:42 ET on
May 7). The dispatch skip (fix 2A) kept these stops in `pending_submit` state overnight.

When regular session opens on May 8:
- `ref_ts = signal_timestamp` = May 7 17:42 ET
- `created_date_et = May 7`
- `session_date_et = May 8`
- `May 7 < May 8` → **EXPIRED**

### Impact

All 13 AH stops are silently expired in cycle 1 of the May 8 regular session — exactly when
price gaps and volatility are highest. The `startup_recovery` second pass (runs every cycle)
detects the positions now have no active stop and queues recovery stops in cycle 2. This
creates a ~60-second window at 9:30 AM ET where positions have no broker stop protection.

The pre-market soft-stop (4:00–9:30 AM ET) provides partial coverage for any price
breach that occurs before regular open, but gap-down opens can move price before the first
PM cycle fires.

### Fix

The original purpose of the stale-stop expiration is to prevent a **submitted** stop from a
prior session — one that disappeared from the broker — from being re-submitted against a
position that may no longer exist (creating a naked short).

Never-submitted stops (`broker_order_id is None`, `status = "pending_submit"`) are a
completely different case: they are local intentions that were queued but not yet sent to the
broker. These are always safe to dispatch; normal exit paths (`_execute_exit`) always
cancel the pending stop record (sets it to "canceled") before submitting an exit order. The
stop record left by `_execute_exit` would not be in `pending_submit` state.

**Fix**: in the stale-stop expiration check, skip expiration when `order.broker_order_id is None`.

```python
if order.intent_type == "stop":
    ref_ts = order.signal_timestamp if order.signal_timestamp is not None else order.created_at
    if ref_ts is None:
        ref_ts = order.created_at
    if ref_ts is None:
        pass  # no timestamp — fall through to dispatch
    else:
        if ref_ts.tzinfo is None:
            ref_ts = ref_ts.replace(tzinfo=timezone.utc)
        created_date_et = ref_ts.astimezone(settings.market_timezone).date()
    if ref_ts is not None and created_date_et < session_date_et:
        if order.broker_order_id is not None:
            # Previously submitted stop that disappeared from broker — genuinely stale.
            logger.warning(
                "order_dispatch: expiring stale stop order for %s "
                "(broker_order_id=%s, created %s, today %s)",
                order.symbol,
                order.broker_order_id,
                created_date_et,
                session_date_et,
            )
            with lock_ctx:
                try:
                    runtime.order_store.save(
                        OrderRecord(..., status="expired", ...),
                        commit=False,
                    )
                    runtime.audit_event_store.append(
                        AuditEvent(event_type="order_expired_stale_stop", ...),
                        commit=False,
                    )
                    runtime.connection.commit()
                except Exception:
                    ...
            continue
        # else: broker_order_id is None — never submitted; fall through to
        # the AH session-type skip check and then dispatch.
```

### No New Risk

Exit paths that close a position always cancel the pending stop via `_execute_exit`:
- Soft-stop EXIT during AH: `_execute_exit` sets stop to "canceled" (line 521–540 even when
  `broker_order_id is None`, because the loop appends to `canceled_order_records` regardless).
- EOD flatten: same path.
- Manual broker close (no system involvement): `startup_recovery` detects "local position
  missing at broker" → mismatch logged → position cleared from DB. The pending stop is
  NOT cleared by startup_recovery, but when dispatch submits it, the broker rejects it
  (no position to protect). The stop is then marked "error" and never retried.

---

## Gap 4 — Silent Stop Dispatch Deferral

### Root Cause

When `dispatch_pending_orders` defers a stop during AH/PM (lines 230–233):

```python
if order.intent_type == "stop" and session_type is not None:
    from alpaca_bot.strategy.session import SessionType as _ST
    if session_type in (_ST.PRE_MARKET, _ST.AFTER_HOURS):
        continue  # silent
```

There is no `AuditEvent`, no log at INFO level. The operator cannot confirm from the audit
trail that stops are being properly deferred (as opposed to silently dropped).

### Fix

Emit an `AuditEvent` and a `logger.debug` when a stop is deferred:

```python
if order.intent_type == "stop" and session_type is not None:
    from alpaca_bot.strategy.session import SessionType as _ST
    if session_type in (_ST.PRE_MARKET, _ST.AFTER_HOURS):
        logger.debug(
            "order_dispatch: deferring stop for %s during %s — will submit at regular open",
            order.symbol,
            session_type,
        )
        with lock_ctx:
            try:
                runtime.audit_event_store.append(
                    AuditEvent(
                        event_type="stop_dispatch_deferred_extended_hours",
                        symbol=order.symbol,
                        payload={
                            "client_order_id": order.client_order_id,
                            "session_type": str(session_type),
                            "stop_price": order.stop_price,
                        },
                        created_at=timestamp,
                    ),
                    commit=False,
                )
                runtime.connection.commit()
            except Exception:
                try:
                    runtime.connection.rollback()
                except Exception:
                    pass
        continue
```

---

## What's Already Correct (No Change Needed)

- **`_execute_exit` with `broker_order_id is None`**: correctly records the cancellation
  without calling `broker.cancel_order`, preventing a spurious API call.
- **`submit_limit_exit` with `extended_hours=True`**: the soft-stop EXIT is submitted as an
  extended-hours limit sell — correct for AH.
- **UPDATE_STOP skipped during AH** (cycle_intent_execution.py): has a `logger.debug` log;
  no audit event needed since stop updates during AH are an expected no-op.
- **Startup_recovery second pass**: correctly queues recovery stops for positions that have
  no active stop. After this fix, it will only be triggered for genuinely missing stops (not
  for AH-deferred ones that expired).

---

## Files Modified

| File | Change |
|---|---|
| `src/alpaca_bot/runtime/order_dispatch.py` | Bug 3: guard `broker_order_id is not None` before stale-stop expiration; Gap 4: audit event on deferral |
| `tests/unit/test_order_dispatch_extended_hours.py` | Tests for both fixes |

---

## Testing

**Bug 3 tests** (add to `test_order_dispatch_extended_hours.py`):

- `test_ah_stop_not_expired_at_regular_session_open`: pending_submit stop with
  `signal_timestamp` = yesterday evening, `broker_order_id=None`, session_type=REGULAR →
  stop is SUBMITTED (not expired)
- `test_submitted_stop_still_expires_at_next_session`: stop with `broker_order_id="brk123"`,
  `signal_timestamp` = yesterday, session_type=REGULAR → stop is EXPIRED

**Gap 4 tests** (add to `test_order_dispatch_extended_hours.py`):

- `test_deferred_stop_emits_audit_event`: session_type=AFTER_HOURS, pending stop →
  audit_event_store receives `stop_dispatch_deferred_extended_hours` event

---

## Deployment Notes

The 13 AH positions from May 7 have already been handled by startup_recovery's second pass
queuing recovery stops (these were submitted at regular session open on May 8 with a ~60s
gap). This fix ensures the gap does not recur for future overnight AH positions.

No migration needed — only `order_dispatch.py` changes.
