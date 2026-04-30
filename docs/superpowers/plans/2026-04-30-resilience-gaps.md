# Resilience Gaps — Implementation Plan

**Date:** 2026-04-30  
**Spec:** `docs/superpowers/specs/2026-04-30-resilience-gaps.md`

---

## Task 1 — Gap 1: Order double-submission (`submitting` status)

**Files:**
- `src/alpaca_bot/runtime/order_dispatch.py`
- `src/alpaca_bot/runtime/startup_recovery.py`
- `tests/unit/test_order_dispatch.py`
- `tests/unit/test_startup_recovery.py`

### 1a. Add `submitting` pre-flight stamp in `order_dispatch.py`

In `dispatch_pending_orders`, before the `broker_order = _submit_order(...)` call, save the order as `submitting`:

```python
# Stamp the order as submitting before the broker call. If the process crashes
# between here and the DB commit after broker confirmation, startup recovery
# will find "submitting" orders and reconcile them against broker open orders.
with lock_ctx:
    try:
        runtime.order_store.save(
            OrderRecord(
                client_order_id=order.client_order_id,
                symbol=order.symbol,
                side=order.side,
                intent_type=order.intent_type,
                status="submitting",
                quantity=order.quantity,
                trading_mode=order.trading_mode,
                strategy_version=order.strategy_version,
                strategy_name=order.strategy_name,
                created_at=order.created_at,
                updated_at=timestamp,
                stop_price=order.stop_price,
                limit_price=order.limit_price,
                initial_stop_price=order.initial_stop_price,
                broker_order_id=order.broker_order_id,
                signal_timestamp=order.signal_timestamp,
            ),
            commit=False,
        )
        runtime.audit_event_store.append(
            AuditEvent(
                event_type="order_dispatch_submitting",
                symbol=order.symbol,
                payload={
                    "client_order_id": order.client_order_id,
                    "intent_type": order.intent_type,
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
        raise
try:
    broker_order = _submit_order(...)
```

### 1b. Add `submitting` to `ACTIVE_ORDER_STATUSES` in `startup_recovery.py`

```python
ACTIVE_ORDER_STATUSES = ["pending_submit", "submitting", "new", "accepted", "submitted", "partially_filled"]
```

### 1c. Extend `_is_never_submitted` to cover `submitting` orders

**Why this matters first:** The existing reconciliation loop (startup_recovery.py:338-365) marks any
unmatched local active order as `reconciled_missing` unless `_is_never_submitted` returns True.
Without this change, a `submitting`-no-broker-id order would be written as `reconciled_missing` by
that loop *and then* overwritten as `pending_submit` by the new reset loop — two conflicting DB writes
before the commit. Extending `_is_never_submitted` prevents the first write entirely.

```python
def _is_never_submitted(order: "OrderRecord") -> bool:
    """Return True when an order was queued locally but never sent to the broker."""
    return order.status in ("pending_submit", "submitting") and not order.broker_order_id
```

### 1d. Add recovery for `submitting` orders with no broker_order_id in `startup_recovery.py`

Before the `position_store.replace_all` call (in the mismatch reporting section), add to `mismatches`:

```python
# Recover orders that were stamped "submitting" but never confirmed — the process
# crashed between the pre-flight stamp and the post-submission DB update.
# If the order appears in broker_open_orders, the reconciliation above already
# matched and synced it. If it did NOT appear, it was never actually received by
# the broker — reset to pending_submit so dispatch retries it.
for order in local_active_orders:
    if order.status == "submitting" and not order.broker_order_id:
        if order.client_order_id not in matched_local_client_ids:
            # Not found at broker: reset to pending_submit for re-dispatch
            mismatches.append(
                f"submitting order not found at broker (will retry): {order.client_order_id}"
            )
```

Then in the DB write section, handle `submitting`-with-no-broker-id orders by resetting them:

```python
# After position_store.replace_all and stop-queuing, reset orphaned submitting orders
for order in local_active_orders:
    if order.status == "submitting" and not order.broker_order_id:
        if order.client_order_id not in matched_local_client_ids:
            runtime.order_store.save(
                OrderRecord(
                    client_order_id=order.client_order_id,
                    symbol=order.symbol,
                    side=order.side,
                    intent_type=order.intent_type,
                    status="pending_submit",
                    quantity=order.quantity,
                    trading_mode=order.trading_mode,
                    strategy_version=order.strategy_version,
                    strategy_name=order.strategy_name,
                    created_at=order.created_at,
                    updated_at=timestamp,
                    stop_price=order.stop_price,
                    limit_price=order.limit_price,
                    initial_stop_price=order.initial_stop_price,
                    broker_order_id=None,
                    signal_timestamp=order.signal_timestamp,
                ),
                commit=False,
            )
            runtime.audit_event_store.append(
                AuditEvent(
                    event_type="order_submitting_reset",
                    symbol=order.symbol,
                    payload={
                        "client_order_id": order.client_order_id,
                        "reason": "not_found_at_broker_on_recovery",
                    },
                    created_at=timestamp,
                ),
                commit=False,
            )
```

### 1d. Tests

In `tests/unit/test_order_dispatch.py`:
- `test_dispatch_stamps_submitting_status_before_broker_call`: broker call succeeds; assert `submitting` was saved before `new` (inspect all `order_store.saved` calls in order)
- `test_dispatch_submitting_status_preserved_on_broker_failure`: broker raises → assert order ends as `error`, not `submitting`

In `tests/unit/test_startup_recovery.py`:
- `test_submitting_order_not_at_broker_resets_to_pending_submit`: local order status=`submitting`, no broker_order_id, not in broker_open_orders → after recovery, order saved with `pending_submit`
- `test_submitting_order_found_at_broker_matched_and_synced`: local order status=`submitting`, no broker_order_id, IS in broker_open_orders → normal reconciliation path (no reset)

**Test command:** `pytest tests/unit/test_order_dispatch.py tests/unit/test_startup_recovery.py -v`

---

## Task 2 — Gap 2: UPDATE_STOP silent swallow

**Files:**
- `src/alpaca_bot/runtime/cycle_intent_execution.py`
- `tests/unit/test_cycle_intent_execution.py`

### 2a. Add `notifier` parameter to `execute_cycle_intents` and `_execute_update_stop`

Add `notifier: "Notifier | None" = None` to `execute_cycle_intents` signature. Pass it through to `_execute_update_stop`:

```python
def execute_cycle_intents(
    *,
    settings: Settings,
    runtime: RuntimeProtocol,
    broker: BrokerProtocol,
    cycle_result: object,
    now: datetime | Callable[[], datetime] | None = None,
    session_type: "SessionType | None" = None,
    notifier: "Notifier | None" = None,
) -> CycleIntentExecutionReport:
```

Add `notifier: "Notifier | None" = None` to `_execute_update_stop` signature.

### 2b. Fire audit event + notifier on unrecognized exception in `_execute_update_stop`

Replace the current `else:` branch of the exception handler:

```python
    except Exception as exc:
        exc_msg = str(exc).lower()
        if any(phrase in exc_msg for phrase in ("not found", "already filled", "already canceled",
                                                 "does not exist", "has been filled", "is filled",
                                                 "order is", "order was")):
            logger.debug("update_stop skipped for %s — order already gone: %s", symbol, exc)
        else:
            logger.exception("Broker call failed for update_stop on %s; skipping", symbol)
            with lock_ctx:
                try:
                    runtime.audit_event_store.append(
                        AuditEvent(
                            event_type="stop_update_failed",
                            symbol=symbol,
                            payload={
                                "error": str(exc),
                                "symbol": symbol,
                                "timestamp": now.isoformat(),
                            },
                            created_at=now,
                        ),
                        commit=False,
                    )
                    runtime.connection.commit()
                except Exception:
                    try:
                        runtime.connection.rollback()
                    except Exception:
                        pass
            if notifier is not None:
                try:
                    notifier.send(
                        subject=f"Stop update failed: {symbol}",
                        body=(
                            f"replace_order raised an unrecognized error for {symbol}. "
                            f"The position may have lost stop protection.\n"
                            f"Error: {exc}"
                        ),
                    )
                except Exception:
                    logger.exception("Notifier failed to send stop_update_failed alert for %s", symbol)
        return None
```

### 2c. Wire notifier from supervisor

In `supervisor.py`, the `_cycle_intent_executor` is called with `**dispatch_kwargs`. Find the call site and add `notifier=self._notifier`. Specifically in `run_cycle_once`, the call to `self._cycle_intent_executor` should pass `notifier=self._notifier`.

Check the call at approximately line 460-480 in supervisor.py and add the kwarg.

### 2d. Tests

In `tests/unit/test_cycle_intent_execution.py`:
- `test_execute_update_stop_unknown_exception_fires_audit_and_notifier`: `replace_order` raises `RuntimeError("broker exploded")` (not matching any known phrase); assert `audit_event_store.appended` contains a `stop_update_failed` event AND notifier was called with subject containing "stop" and "failed"

**Test command:** `pytest tests/unit/test_cycle_intent_execution.py -v`

---

## Task 3 — Gap 3: Stream heartbeat

**Files:**
- `src/alpaca_bot/runtime/trade_update_stream.py`
- `src/alpaca_bot/runtime/supervisor.py`
- `tests/unit/test_runtime_supervisor.py`

### 3a. Add `on_event` callback to `attach_trade_update_stream`

```python
def attach_trade_update_stream(
    *,
    settings: Settings,
    runtime: RuntimeProtocol,
    stream: TradeUpdateStreamProtocol,
    now: Callable[[], datetime] | None = None,
    notifier: Notifier | None = None,
    on_event: Callable[[], None] | None = None,
):
    async def handler(update: Any) -> None:
        if on_event is not None:
            on_event()
        timestamp = (now or (lambda: datetime.now(timezone.utc)))()
        try:
            apply_trade_update(...)
        ...
```

### 3b. Add heartbeat tracking to `RuntimeSupervisor`

In `__init__`, add:
```python
self._last_stream_event_at: datetime | None = None
self._stream_stale_alerted: bool = False
```

Add method:
```python
def _record_stream_event(self) -> None:
    self._last_stream_event_at = datetime.now(timezone.utc)
    self._stream_stale_alerted = False  # Reset so next stale window re-alerts
```

### 3c. Wire `on_event` in `startup()`

In the `_stream_attacher` call:
```python
self._stream_attacher(
    settings=self.settings,
    runtime=self.runtime,
    stream=self.stream,
    now=lambda: timestamp,
    notifier=self._notifier,
    on_event=self._record_stream_event,
)
```

### 3d. Add staleness check in `run_forever`

After the existing dead-thread watchdog block (around line 680), add:

```python
_STREAM_HEARTBEAT_TIMEOUT_SECONDS = 300

# Heartbeat staleness check — fires only when thread is alive but no event
# has arrived in the last 5 minutes during a non-closed session.
if (
    self._stream_thread is not None
    and self._stream_thread.is_alive()
    and self._last_stream_event_at is not None
    and session_type is not SessionType.CLOSED
    and not self._stream_stale_alerted
    and (timestamp - self._last_stream_event_at).total_seconds()
        > _STREAM_HEARTBEAT_TIMEOUT_SECONDS
):
    logger.critical(
        "Trade update stream appears stale — no event received in %ds during market hours",
        _STREAM_HEARTBEAT_TIMEOUT_SECONDS,
    )
    self._append_audit(
        AuditEvent(
            event_type="stream_heartbeat_stale",
            payload={
                "last_event_at": self._last_stream_event_at.isoformat(),
                "timeout_seconds": _STREAM_HEARTBEAT_TIMEOUT_SECONDS,
            },
            created_at=timestamp,
        )
    )
    if self._notifier is not None:
        try:
            self._notifier.send(
                subject="Trade stream heartbeat stale",
                body=(
                    f"No trade update events received in {_STREAM_HEARTBEAT_TIMEOUT_SECONDS}s "
                    f"during market hours. Stream may be silently disconnected. "
                    f"Last event: {self._last_stream_event_at.isoformat()}"
                ),
            )
        except Exception:
            logger.exception("Notifier failed to send stream heartbeat stale alert")
    self._stream_stale_alerted = True
```

### 3e. Tests

In `tests/unit/test_runtime_supervisor.py`:
- `test_stream_heartbeat_stale_fires_audit_and_notifier`: run supervisor with stream thread alive, `_last_stream_event_at` set to 6 minutes ago, session not CLOSED → assert `stream_heartbeat_stale` audit event appended and notifier called
- `test_stream_heartbeat_alert_not_repeated_in_same_window`: second cycle after stale alert already fired → assert notifier NOT called again (stale_alerted=True)
- `test_stream_heartbeat_resets_after_event`: after stale alert fires, call `_record_stream_event()` → `_stream_stale_alerted` is False again

**Test command:** `pytest tests/unit/test_runtime_supervisor.py -v`

---

## Task 4 — Gap 4: daily_realized_pnl conservative treatment of missing entries

**Files:**
- `src/alpaca_bot/storage/repositories.py`
- `tests/unit/test_repositories.py` (or create if needed)

### 4a. Change sum to include missing-entry rows as conservative losses

Find the `daily_realized_pnl` method in `OrderStore`. Change:

```python
# Before:
return sum(
    (float(row[2]) - float(row[1])) * int(row[3])
    for row in rows
    if row[1] is not None and row[2] is not None
)

# After:
total = 0.0
for row in rows:
    if row[1] is not None and row[2] is not None:
        total += (float(row[2]) - float(row[1])) * int(row[3])
    elif row[1] is None and row[2] is not None:
        # Conservative: treat missing entry as full loss of exit proceeds
        total += -(float(row[2]) * int(row[3]))
return total
```

### 4b. Change log level from WARNING to ERROR

```python
logger.error(  # was logger.warning
    "daily_realized_pnl: %d exit row(s) with missing entry fill "
    "(symbols: %s); treating as full losses for fail-safe loss-limit check",
    len(missing_entry),
    [row[0] for row in missing_entry],
)
```

### 4c. Tests

In `tests/unit/test_repositories.py` (or the relevant storage test file):
- `test_daily_realized_pnl_missing_entry_treated_as_full_loss`: mock the SQL result to return a row with `row[1]=None, row[2]=50.0, row[3]=10`; assert result is `-500.0`
- `test_daily_realized_pnl_normal_trade_calculated_correctly`: row[1]=100.0, row[2]=95.0, row[3]=10 → result=-50.0

Find the existing test file for repositories: `tests/unit/test_repositories.py` or search for the existing test.

**Test command:** `pytest tests/unit/ -k "pnl or repositories" -v`

---

## Task 5 — Gap 5: sizing.py unit tests

**Files:**
- `tests/unit/test_position_sizing.py` (new file)

```python
from __future__ import annotations

import pytest
from types import SimpleNamespace
from alpaca_bot.risk.sizing import calculate_position_size


def _settings(risk_pct: float = 0.01, max_pct: float = 0.10) -> object:
    # SimpleNamespace per project conventions (fakes, not mocks)
    return SimpleNamespace(risk_per_trade_pct=risk_pct, max_position_pct=max_pct)


def test_normal_case_calculates_quantity():
    # equity=10000, entry=100, stop=95, risk_budget=100, risk_per_share=5 → qty=20
    # notional=2000 < max_notional=1000? No: 20*100=2000 > max_notional=10000*0.1=1000 → cap
    # With max_pct=0.5: 20*100=2000 <= 5000 → qty=20
    qty = calculate_position_size(
        equity=10000, entry_price=100.0, stop_price=95.0, settings=_settings(0.01, 0.5)
    )
    assert qty == 20


def test_max_notional_cap_applied():
    # equity=100000, entry=100, stop=99, risk_budget=1000, risk_per_share=1 → qty=1000
    # max_notional=100000*0.05=5000 → capped to floor(5000/100)=50
    qty = calculate_position_size(
        equity=100000, entry_price=100.0, stop_price=99.0, settings=_settings(0.01, 0.05)
    )
    assert qty == 50


def test_stop_at_or_above_entry_raises():
    with pytest.raises(ValueError, match="stop_price must be below entry_price"):
        calculate_position_size(
            equity=10000, entry_price=100.0, stop_price=100.0, settings=_settings()
        )
    with pytest.raises(ValueError, match="stop_price must be below entry_price"):
        calculate_position_size(
            equity=10000, entry_price=100.0, stop_price=101.0, settings=_settings()
        )


def test_tiny_equity_returns_zero():
    # equity=100, entry=200, stop=195, risk_budget=1, risk_per_share=5 → qty=0
    qty = calculate_position_size(
        equity=100, entry_price=200.0, stop_price=195.0, settings=_settings(0.01, 0.5)
    )
    assert qty == 0


def test_zero_equity_returns_zero():
    qty = calculate_position_size(
        equity=0, entry_price=100.0, stop_price=95.0, settings=_settings()
    )
    assert qty == 0


def test_negative_equity_returns_zero():
    qty = calculate_position_size(
        equity=-5000, entry_price=100.0, stop_price=95.0, settings=_settings()
    )
    assert qty == 0
```

**Test command:** `pytest tests/unit/test_position_sizing.py -v`

---

## Task 6 — Gap 6: Startup recovery stop-queuing ignores pending entry orders

**Files:**
- `src/alpaca_bot/runtime/startup_recovery.py`
- `tests/unit/test_startup_recovery.py`

### 6a. Build `pending_entry_symbols` set

In `recover_startup_state`, after building `active_stop_symbols` (line ~242), add:

```python
# Symbols with unsubmitted (pending_submit/submitting, no broker_order_id) entry orders.
# Do not queue a recovery stop for these — the stop will be created by the trade update
# stream when/if the entry fills.
pending_entry_symbols = {
    o.symbol
    for o in local_active_orders
    if o.intent_type == "entry"
    and o.status in ("pending_submit", "submitting")
    and not o.broker_order_id
}
```

### 6b. Update stop-queuing guard

Change:
```python
if sym not in active_stop_symbols:
```
to:
```python
if sym not in active_stop_symbols and sym not in pending_entry_symbols:
```

Add a warning when skipped:
```python
elif sym in pending_entry_symbols:
    _log.warning(
        "startup_recovery: skipping recovery stop for %s — unsubmitted entry order exists; "
        "stop will be queued by trade update stream on fill",
        sym,
    )
```

### 6c. Tests

In `tests/unit/test_startup_recovery.py`:
- `test_brand_new_position_with_pending_entry_does_not_queue_recovery_stop`: broker has AAPL position, local DB has a `pending_submit` entry order for AAPL with no `broker_order_id` → recovery does NOT queue a stop for AAPL

**Test command:** `pytest tests/unit/test_startup_recovery.py -v`

---

## Task 7 — Gap 7: Advisory lock exit on reconnect failure

**Files:**
- `src/alpaca_bot/runtime/bootstrap.py`
- `src/alpaca_bot/runtime/supervisor.py`
- `tests/unit/test_runtime_supervisor.py`

### 7a. Add `LockAcquisitionError` to `bootstrap.py`

```python
class LockAcquisitionError(RuntimeError):
    """Raised when the Postgres advisory lock cannot be acquired or re-acquired."""
```

### 7b. Raise `LockAcquisitionError` in `reconnect_runtime_connection`

```python
if not context.lock.try_acquire():
    try:
        new_conn.close()
    except Exception:
        pass
    raise LockAcquisitionError(
        "Could not re-acquire singleton trader lock after reconnect for "
        f"{context.settings.trading_mode.value}/{context.settings.strategy_version}"
    )
```

### 7c. Export `LockAcquisitionError` from `bootstrap.py` (add to module-level, not hidden)

No `__all__` in bootstrap.py currently — just define at module level, it's importable.

### 7d. Import and catch in `supervisor.py`

At the top of `supervisor.py`, add import:
```python
from alpaca_bot.runtime.bootstrap import (
    LockAcquisitionError,
    RuntimeContext,
    bootstrap_runtime,
    close_runtime,
    reconnect_runtime_connection,
)
```

In `run_forever`, add before the `except Exception as exc:` clause:

```python
except LockAcquisitionError:
    logger.critical(
        "Supervisor: advisory lock could not be re-acquired after reconnect — "
        "exiting immediately to prevent split-brain with another instance"
    )
    raise SystemExit(1)
except Exception as exc:
    ...  # existing handler
```

### 7e. Tests

In `tests/unit/test_runtime_supervisor.py`:
- `test_lock_acquisition_error_on_reconnect_causes_immediate_exit`: supervisor's `_reconnect` raises `LockAcquisitionError`; run one cycle; assert `SystemExit(1)` is raised immediately (not after 10 failures, not swallowed)

**Test command:** `pytest tests/unit/test_runtime_supervisor.py -v`

---

## Task 8 — Full test suite

**Command:** `pytest --tb=short -q`

Expected: all existing tests pass + new tests added in Tasks 1–7.

---

## Task 9 — Commit and deploy

```bash
git add \
  src/alpaca_bot/runtime/order_dispatch.py \
  src/alpaca_bot/runtime/startup_recovery.py \
  src/alpaca_bot/runtime/cycle_intent_execution.py \
  src/alpaca_bot/runtime/trade_update_stream.py \
  src/alpaca_bot/runtime/supervisor.py \
  src/alpaca_bot/runtime/bootstrap.py \
  src/alpaca_bot/storage/repositories.py \
  tests/unit/test_order_dispatch.py \
  tests/unit/test_startup_recovery.py \
  tests/unit/test_cycle_intent_execution.py \
  tests/unit/test_runtime_supervisor.py \
  tests/unit/test_position_sizing.py

git commit -m "fix: resilience gaps — submitting status, stop_update alert, stream heartbeat, pnl fail-safe, sizing tests, lock exit"

./scripts/deploy.sh /etc/alpaca_bot/alpaca-bot.env
```
