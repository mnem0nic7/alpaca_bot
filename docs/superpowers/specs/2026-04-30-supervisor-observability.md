# Spec: Supervisor Lifecycle Observability

## Problem

After three phases of resilience hardening, four observability gaps remain:

1. **No lifecycle audit trail.** The audit log records `trader_startup_completed` but not
   `supervisor_started` (when the main loop begins) or `supervisor_exited` (when the process
   terminates cleanly). A crash leaves the same audit footprint as a clean shutdown — an operator
   cannot distinguish between "supervisor stopped as requested" and "supervisor died."

2. **SIGTERM is unhandled.** Docker Compose sends SIGTERM before SIGKILL. Python's default SIGTERM
   handler terminates the process immediately, bypassing the `finally: self.close()` block that
   releases the advisory lock, stops the stream thread, and closes the DB connection. Under load
   the OS reclaims resources anyway, but the advisory lock is held until the DB connection is fully
   closed — a forced reconnect may take seconds.

3. **Stream health is invisible to /healthz.** `stream_heartbeat_stale` events are written to the
   audit log and trigger a notifier alert, but `/healthz` returns 200 OK even when the stream has
   been dead for 5+ minutes. External uptime monitors that poll `/healthz` have no signal.

4. **No Docker healthcheck on the supervisor container.** The `web` service has a curl healthcheck;
   the `supervisor` service has none. Docker cannot mark the supervisor as unhealthy, so
   `docker compose ps` shows a green status even if the supervisor is stuck in a deadlock.

## Goals

1. Emit `supervisor_started` at the top of `run_forever()` and `supervisor_exited` in the finally
   block so the audit log captures the full supervisor lifetime.
2. Install a SIGTERM/SIGINT handler that sets a shutdown flag; the main loop checks the flag and
   exits cleanly, allowing `finally: self.close()` to run and the `supervisor_exited` event to be
   appended before process termination.
3. Expose `stream_stale` (bool) and `stream_last_stale_at` (ISO timestamp or null) in the
   `/healthz` JSON response, derived from recent `stream_heartbeat_stale` audit events. Return 503
   during market hours when stream is stale.
4. Add a Docker HEALTHCHECK to the supervisor service that verifies a per-cycle heartbeat file
   (`/tmp/supervisor_heartbeat`) is fresh (mtime < 180s). The supervisor writes the file on every
   cycle iteration.

## Non-Goals

- Advisory lock heartbeat / lease renewal (complex, deferred).
- Push-alerting on supervisor death (the notifier fires on cycle failures; death is handled by
  Docker `restart: unless-stopped`).
- Crash dump or core file collection.

## Design

### 1. `supervisor_started` and `supervisor_exited` events

In `RuntimeSupervisor.run_forever()`:

```python
# At top of run_forever(), before main loop:
self._append_audit(AuditEvent(event_type="supervisor_started", payload={}))

# In finally block, before self.close():
self._append_audit(AuditEvent(event_type="supervisor_exited", payload={"iterations": iterations}))
```

`supervisor_exited` is emitted in the `finally` block so it fires on both clean and exception
exits. It does NOT fire on SIGKILL (no handler can catch that), which is the expected behaviour.

### 2. SIGTERM/SIGINT handler

In `run_forever()`, before the main loop, register handlers:

```python
import signal

def _request_shutdown(signum, frame):
    self._shutdown_requested = True

signal.signal(signal.SIGTERM, _request_shutdown)
signal.signal(signal.SIGINT, _request_shutdown)
```

`_shutdown_requested` is an instance attribute initialised to `False` in `__init__`. The main loop
already checks `should_stop()` after each sleep; the check is extended to also break when
`self._shutdown_requested` is True.

The existing `should_stop` callable passed by tests continues to work unchanged.

### 3. Stream health in /healthz

`load_health_snapshot()` loads recent audit events that include `stream_heartbeat_stale`. Two new
fields are added to `HealthSnapshot`:

```python
stream_stale: bool = False
stream_last_stale_at: datetime | None = None
```

Logic: scan the last 12 audit events for any `stream_heartbeat_stale` event within the last
`STREAM_STALE_WINDOW_SECONDS = 600` seconds (10 minutes). If found, `stream_stale = True` and
`stream_last_stale_at` is that event's `created_at`.

`/healthz` includes:
```json
{
  "stream_stale": false,
  "stream_last_stale_at": null
}
```

HTTP 503 is returned when `worker_stale OR (stream_stale AND market_is_open)`. Market-hours check
uses `settings.entry_window_start` / `settings.flatten_time` in the server's local timezone (same
logic as the supervisor's session guard).

### 4. Docker healthcheck via heartbeat file

In `run_forever()`, write `/tmp/supervisor_heartbeat` at the top of each cycle iteration (after
the market-open check, so it fires on both active and idle cycles):

```python
Path("/tmp/supervisor_heartbeat").write_text(timestamp.isoformat())
```

In `deploy/compose.yaml`, add to the `supervisor` service:

```yaml
healthcheck:
  test: >
    CMD python -c "
    import os, time;
    f='/tmp/supervisor_heartbeat';
    exit(0 if os.path.exists(f) and time.time()-os.path.getmtime(f)<180 else 1)"
  interval: 60s
  timeout: 5s
  retries: 3
  start_period: 120s
```

`start_period: 120s` avoids false failures during the 60s initial startup + first cycle.

## Files Changed

| File | Change |
|------|--------|
| `src/alpaca_bot/runtime/supervisor.py` | Add `_shutdown_requested` attr; SIGTERM/SIGINT handlers; `supervisor_started`/`supervisor_exited` events; heartbeat file write |
| `src/alpaca_bot/web/service.py` | Add `stream_stale`, `stream_last_stale_at` to `HealthSnapshot`; update `load_health_snapshot()` |
| `src/alpaca_bot/web/app.py` | Add `stream_stale`, `stream_last_stale_at` to `/healthz` response; update HTTP 503 condition |
| `deploy/compose.yaml` | Add healthcheck to supervisor service |
| `tests/unit/test_supervisor.py` | Tests for SIGTERM flag, lifecycle events, heartbeat file |
| `tests/unit/test_web_service.py` | Tests for stream_stale field in health snapshot and /healthz |

## Safety Analysis

- `supervisor_started`/`supervisor_exited` are informational events — no order, broker, or position
  side effects.
- SIGTERM handler sets a flag only; the actual shutdown path is identical to the existing clean
  exit path (finally → close()). No new code paths for order dispatch or position mutation.
- The `finally` block already calls `self.close()` which releases the advisory lock. SIGTERM
  handling makes this path MORE reliable, not less.
- `stream_stale` returning 503 during market hours: the web service is read-only. External
  monitors re-querying on 503 is expected behaviour (alerts operators). No trading action is
  taken.
- Heartbeat file: writes one small text file per 60s cycle. No security exposure — /tmp is
  container-scoped.
- `ENABLE_LIVE_TRADING=false` gate: unaffected by all changes.
- No new env vars; no DB migration.
