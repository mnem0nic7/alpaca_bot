# Stale Carryover Position Cleanup

**Date:** 2026-05-11  
**Status:** Approved

---

## Problem

EOD flatten occasionally fails (broker error, supervisor down at market close). When the supervisor restarts on the next trading day, positions opened during a prior session are still held at the broker. `recover_startup_state` (called every cycle) faithfully reflects broker reality: because the broker still holds the positions, `replace_all` keeps them in the local `positions` table. The positions appear in `open_positions` and are treated as current-session trades. Their stops may be stale relative to overnight price movement.

The desired behaviour: at the start of each new session, any position whose `opened_at` date is prior to the current session date must be force-closed via market exit before normal trading resumes.

---

## Scope

This covers **Scenario A** only: broker still holds the position overnight. Scenario B (phantom local rows whose broker counterpart is already gone) is already handled by `recover_startup_state → replace_all`.

---

## Architecture

### Detection

A position is "stale" when:

```
position.entry_timestamp.astimezone(settings.market_timezone).date() < session_date
```

`entry_timestamp` on `OpenPosition` maps to `opened_at` on `PositionRecord`. The timezone conversion uses `settings.market_timezone` (Eastern), matching the rest of the session-date logic in the supervisor.

### Action

For each stale position:

1. Emit a `CycleIntent(intent_type=EXIT, reason="stale_position_carryover")`.
2. Pass all stale EXIT intents as a `CycleResult` to `execute_cycle_intents`.
3. `execute_cycle_intents → _execute_exit` handles: cancel active stops, submit market exit, idempotency (skips if an active exit order already exists for the symbol).

No new broker-call logic is introduced. The entire exit path reuses the proven `_execute_exit` implementation.

### Idempotency

`_execute_exit` already guards against duplicate dispatch (line 448, `cycle_intent_execution.py`):

```python
if any(o.symbol == symbol and o.intent_type == "exit" for o in active_exit_orders):
    return 0, 0, 0
```

The stale cleanup therefore runs on every cycle without risk of double-submission. Positions disappear from `open_positions` once fills arrive (via the trade update stream → `recover_startup_state → replace_all`), so subsequent cycles see no stale positions to process.

### Integration point

In `run_cycle_once()`, immediately after:

```python
open_positions = self._load_open_positions()   # line 529
```

and before the `working_order_symbols` computation. This ensures:

- Stale positions' symbols are still in `open_positions` and therefore in `working_order_symbols`, which blocks new entries for those symbols automatically.
- Cleanup exits are submitted before the strategy loop runs.

### HALTED behaviour

Stale cleanup runs regardless of `TradingStatusValue`. An overnight carryover is unintended risk that predates the HALT; it should be resolved irrespective of the operator's operational pause intent. The `execute_cycle_intents` call for cleanup exits is a targeted exception to the HALTED no-dispatch rule.

---

## Changes

### `runtime/supervisor.py`

**New instance variable** (in `__init__`):

```python
self._stale_cleanup_notified: set[date] = set()
```

**New private method** `_close_stale_carryover_positions`:

```python
def _close_stale_carryover_positions(
    self,
    *,
    session_date: date,
    open_positions: list[OpenPosition],
    timestamp: datetime,
) -> None:
```

Logic:
1. Filter `open_positions` for entries where `entry_timestamp.astimezone(settings.market_timezone).date() < session_date`.
2. Return early if no stale positions found.
3. Write a single `stale_positions_detected` AuditEvent listing all stale symbols and their `opened_at` dates.
4. Build a `CycleResult` with one EXIT `CycleIntent` per stale position (`reason="stale_position_carryover"`, `strategy_name` from the position).
5. Call `execute_cycle_intents(settings, runtime, broker, cycle_result, now=timestamp)`.
6. If `session_date not in self._stale_cleanup_notified` and `self._notifier is not None`, send one notification listing all stale symbols; add `session_date` to `_stale_cleanup_notified`.

**Call site** in `run_cycle_once()`, after `open_positions = self._load_open_positions()`:

```python
self._close_stale_carryover_positions(
    session_date=session_date,
    open_positions=open_positions,
    timestamp=timestamp,
)
```

Note: `open_positions` must be reloaded after calling this method if the exits complete synchronously. In practice, positions are removed from the local store by the trade update stream and `recover_startup_state`, not by `_execute_exit` directly — so `open_positions` remains unchanged within this cycle and the strategy loop sees the stale positions (but their symbols are in `working_order_symbols`, blocking new entries).

---

## Audit Events

| Event type | When | Payload |
|---|---|---|
| `stale_positions_detected` | Once per detection (every cycle where stale positions exist) | `symbols`, `session_date`, `timestamp` |
| `cycle_intent_executed` (action=submitted) | Per stale exit submitted | existing schema — `intent_type`, `action`, `symbol` |
| `cycle_intent_skipped` (reason=active_exit_order_exists) | Per stale exit that already has an active order | existing schema |

---

## Notification

One message per session (gated on `_stale_cleanup_notified`):

- **Subject**: `Stale carryover positions found`
- **Body**: Lists each stale symbol with its `opened_at` date.

Notification failures are caught and logged; they do not abort the cleanup.

---

## Tests

New file: `tests/unit/test_supervisor_stale_positions.py`

| Test | Scenario |
|---|---|
| `test_stale_positions_submitted_via_execute_cycle_intents` | Two positions: one from prior session date, one from current session → only the prior one gets an EXIT intent submitted |
| `test_no_stale_positions_no_audit_event` | All positions opened today → no `stale_positions_detected` event written |
| `test_stale_cleanup_runs_regardless_of_halted_status` | HALTED status → stale exits still submitted |
| `test_stale_cleanup_audit_event_written_each_detection_cycle` | Two cycles with same stale position in-flight → `stale_positions_detected` written both cycles (exit idempotent via existing guard) |
| `test_notification_sent_once_per_session` | Two cycles with stale position → notifier called exactly once |
| `test_stale_cleanup_skips_already_active_exit_order` | Existing active exit order for stale symbol → `execute_cycle_intents` skips re-submission (verifies idempotency guard fires) |

Tests use the fake-callables / in-memory stores pattern consistent with `test_supervisor_highest_price.py`.

---

## Error Handling

- If `execute_cycle_intents` raises, log the exception and continue. Stale positions will be retried next cycle.
- If AuditEvent write fails, log and continue (same pattern as `runtime_reconciliation_detected` event in `run_cycle_once`).
- If notifier fails, log and continue (same pattern as loss-limit alert).

---

## What Is Not Changing

- `recover_startup_state` — no changes.
- `PositionRecord` / `OpenPosition` / DB schema — no migrations needed.
- `execute_cycle_intents` — no changes. All behaviour reused as-is.
- `evaluate_cycle()` — remains pure; no changes.
- EOD flatten path — no changes.
