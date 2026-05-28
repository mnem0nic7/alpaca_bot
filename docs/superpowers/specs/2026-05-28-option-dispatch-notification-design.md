# Option Order Dispatch Failure Notification — Design Spec

**Date:** 2026-05-28
**Author:** auto (plan-and-refine pipeline)
**Status:** Ready for implementation

---

## Problem Statement

When `dispatch_pending_option_orders()` fails to submit an option order, it:
1. Marks the order `status="failed"` in Postgres
2. Emits an `option_order_dispatch_failed` audit event

But it sends no notification. The operator learns about the failure only by:
- Checking the dashboard manually
- Reading the weekly review (dispatch failures are shown there)
- Noticing the position was never opened

An order that fails to dispatch means an intended trade was silently skipped. In a short-put
strategy this can be operationally significant — if the order isn't dispatched, the position
was never opened and the subsequent P&L tracking for that entry is meaningless.

The notification infrastructure is fully in place. Circuit breaker, daily loss limit,
per-symbol loss limit, flatten failure, and cycle error all send notifications. Dispatch
failure is the one remaining gap in the option trading notification coverage.

---

## Fix Design

### `OptionDispatchReport` — add `failed_count`

```python
@dataclasses.dataclass
class OptionDispatchReport:
    submitted_count: int
    failed_count: int = 0   # NEW
```

Increment in the `except` block inside `dispatch_pending_option_orders`:

```python
except Exception:
    logger.exception(...)
    # ... mark failed, save, audit event ...
    failed_count += 1   # NEW (was: no tracking)
```

Return `OptionDispatchReport(submitted_count=submitted_count, failed_count=failed_count)`.

### Supervisor call sites — capture report, notify

`run_cycle_once()` calls `dispatch_pending_option_orders()` at two places, both discarding
the return value. Change both to capture the report:

```python
option_dispatch_report = dispatch_pending_option_orders(...)
```

and

```python
option_dispatch_eod_report = dispatch_pending_option_orders(...)
```

After both calls (just before the `return SupervisorCycleReport(...)`), aggregate and notify:

```python
total_opt_failed = (
    (option_dispatch_report.failed_count if option_dispatch_report else 0)
    + (option_dispatch_eod_report.failed_count if option_dispatch_eod_report else 0)
)
if total_opt_failed > 0 and self._notifier is not None:
    try:
        self._notifier.send(
            subject=f"[alpaca-bot] Option dispatch failure: {total_opt_failed} order(s) failed",
            body=(
                f"{total_opt_failed} option order(s) failed to dispatch this cycle.\n\n"
                f"Check the audit log for 'option_order_dispatch_failed' events "
                f"to see which symbols and order IDs were affected."
            ),
        )
    except Exception:
        logger.exception("Notifier failed to send option dispatch failure alert")
```

### No re-notification guard needed

`failed_count` is per-cycle and per-call. The guard against spam is structural: dispatch
failures only happen when orders are in `pending_submit` state, which is cleared on each
cycle (order moves to `submitting` before the broker call, then `submitted` or `failed`).
So the same order cannot fail twice in two cycles.

---

## Scope

**In scope:**
- `option_dispatch.py`: add `failed_count` field to `OptionDispatchReport`, increment on failure
- `supervisor.py`: capture return values at both call sites, aggregate, notify
- Unit tests for both

**Out of scope:**
- Notification body listing individual order IDs (operator can read audit log)
- Retry logic for failed orders (separate concern)
- Notifying on equity dispatch failures (covered by separate audit path)

---

## Data Flow

```
run_cycle_once()
  ├─ option_dispatch_report = dispatch_pending_option_orders(intraday)
  │    └─ order submit raises → failed_count += 1
  │
  ├─ option_dispatch_eod_report = dispatch_pending_option_orders(EOD flatten)
  │    └─ order submit raises → failed_count += 1
  │
  └─ total_opt_failed = sum of failed_counts
       └─ > 0? → self._notifier.send(subject, body)
```

---

## Risk Assessment

- **Financial risk:** None — notification fires after the failure is already recorded. The
  order is already marked failed in Postgres and the audit event is already emitted.
- **Notification spam:** Low. Dispatch failures require an order in `pending_submit` state,
  which transitions away after each cycle. The same order won't fail twice.
- **Rollback:** No migrations. Pure code change: remove 4 lines and discard return values.
- **Paper vs. live:** Identical.

---

## Testing

**Test 1:** `OptionDispatchReport` has `failed_count` field, defaults to 0.
**Test 2:** `dispatch_pending_option_orders` increments `failed_count` when broker raises.
**Test 3:** `dispatch_pending_option_orders` leaves `failed_count=0` on successful dispatch.
**Test 4:** Supervisor sends notification when `option_dispatch_report.failed_count > 0`.
**Test 5:** Supervisor sends no notification when `failed_count == 0`.
**Test 6:** Notification failure (notifier raises) does not crash the supervisor.
