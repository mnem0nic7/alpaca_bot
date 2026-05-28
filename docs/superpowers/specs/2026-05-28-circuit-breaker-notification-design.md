# Option Circuit Breaker — Notification on Disable — Design Spec

**Date:** 2026-05-28
**Author:** auto (plan-and-refine pipeline)
**Status:** Ready for implementation

---

## Problem Statement

`_check_option_strategy_circuit_breakers` (supervisor.py:1911) disables a strategy by writing
`StrategyFlag(enabled=False)` and emitting an audit event, but it sends no notification. The
operator learns about the disable only by:
- Checking the dashboard manually
- Noticing that no new option orders appear
- Reading audit event rows in the DB

A professional trading system must notify the operator **immediately** when a safety gate fires.
The notification infrastructure already exists: `self._notifier.send(subject, body)` is used in
6+ other places in supervisor.py (daily loss limit, per-symbol loss limit, flatten failure,
strategy cycle error, stream restart). Adding a notification here closes the final observability
gap in the circuit breaker feature.

---

## Scope

**In scope:**
- Add `self._notifier.send()` call in `_check_option_strategy_circuit_breakers` after the
  StrategyFlag is saved and the audit event is emitted.
- Unit test that captures the notification subject and body.

**Out of scope:**
- Changing the circuit breaker trigger logic.
- Adding a "re-enabled" notification (separate lifecycle event, not in scope here).
- Changing the notification transport (email/Slack already configured by the factory).

---

## Fix Design

### Call site

After `logger.warning(...)` at line 1983–1990, add:

```python
if self._notifier is not None:
    try:
        self._notifier.send(
            subject=f"[alpaca-bot] Option circuit breaker: {strategy_name} disabled",
            body=(
                f"Strategy '{strategy_name}' has been automatically disabled by the "
                f"rolling-loss circuit breaker.\n\n"
                f"  Rolling P&L:  ${pnl:,.2f}\n"
                f"  Threshold:    ${threshold:,.2f}\n"
                f"  Window:       {window_days} days\n\n"
                f"Re-enable via:\n"
                f"  alpaca-bot-admin enable-strategy --strategy {strategy_name}"
            ),
        )
    except Exception:
        logger.exception(
            "Notifier failed to send circuit breaker alert for %s", strategy_name
        )
```

### Why wrap in try/except

All existing `self._notifier.send()` call sites follow the same pattern — the notifier is
fire-and-forget; a transport failure must not crash the supervisor cycle.

### No duplicate notification guard

The outer `if existing is not None and not existing.enabled: continue` check already ensures
this code path is only reached when the strategy transitions from enabled → disabled. The
notification fires exactly once per transition (same as the audit event and flag write).

---

## Data Flow

```
_check_option_strategy_circuit_breakers()
  └─ pnl <= threshold AND strategy was enabled
       ├─ flag_store.save(StrategyFlag(enabled=False))  ← already committed
       ├─ _append_audit(option_strategy_circuit_breaker_triggered)
       ├─ logger.warning(...)
       └─ self._notifier.send(subject, body)            ← NEW
            ├─ success: operator receives email/Slack alert
            └─ failure: logger.exception() only — cycle continues
```

---

## Testing

**Test 1:** `test_circuit_breaker_sends_notification` — when P&L breaches threshold and strategy
was enabled, verify `_notifier.send()` is called with a subject containing the strategy name
and a body containing the rolling P&L, threshold, window, and "enable-strategy" CLI command.

**Test 2:** `test_circuit_breaker_no_notification_when_already_disabled` — when the strategy
is already disabled, `_notifier.send()` is NOT called (idempotency guard is upstream).

**Test 3:** `test_circuit_breaker_no_notification_when_notifier_none` — with `_notifier=None`,
no AttributeError is raised (existing behavior preserved).

**Test 4:** `test_circuit_breaker_notification_failure_does_not_crash_cycle` — when the notifier
`send()` raises, the cycle does not propagate the exception.

All tests use a fake `_notifier` that is a `SimpleNamespace` with a `send` callable that
appends to a list.

---

## Risk Assessment

- **Financial risk:** None — notification is fire-and-forget after the flag is already written.
- **Notification spam:** None — fires at most once per strategy per lifecycle transition
  (enabled→disabled). Subsequent cycles skip the notification because the flag is already disabled.
- **Rollback:** No env vars, no migrations, no schema changes. Reverting is a one-line code delete.
- **Paper vs. live:** Identical — the notifier is mode-agnostic.
