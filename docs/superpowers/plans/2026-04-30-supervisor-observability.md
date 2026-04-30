# Plan: Supervisor Lifecycle Observability

Spec: `docs/superpowers/specs/2026-04-30-supervisor-observability.md`

---

## Task 1 — Add `_shutdown_requested` to `RuntimeSupervisor.__init__`

**File:** `src/alpaca_bot/runtime/supervisor.py`

After the `self._summary_sent` line (~line 116), add:

```python
self._shutdown_requested: bool = False
```

**Test command:** `pytest tests/unit/test_supervisor.py -q`

---

## Task 2 — SIGTERM/SIGINT handler, `supervisor_started` event, heartbeat file

**File:** `src/alpaca_bot/runtime/supervisor.py`

Add import at the top of the file (with existing stdlib imports):
```python
import signal
from pathlib import Path
```

In `run_forever()`, immediately after `sleeper = sleep_fn if sleep_fn is not None else time.sleep`
(before the `try:` block), add:

```python
def _request_shutdown(signum: int, frame: object) -> None:
    self._shutdown_requested = True

signal.signal(signal.SIGTERM, _request_shutdown)
signal.signal(signal.SIGINT, _request_shutdown)
```

In `run_forever()`, after `self.startup(...)` succeeds (before `while True:`), add:

```python
self._append_audit(AuditEvent(event_type="supervisor_started", payload={}))
```

Inside the `while True:` loop, immediately after `timestamp = _resolve_now(cycle_now)`, add:

```python
try:
    Path("/tmp/supervisor_heartbeat").write_text(timestamp.isoformat())
except OSError:
    pass
```

In the loop's end-of-iteration block, extend the existing `should_stop` checks to also break on
the shutdown flag. Change:

```python
if should_stop is not None and should_stop():
    break
if max_iterations is not None and iterations >= max_iterations:
    break
sleeper(poll_interval_seconds)
```

to:

```python
if (should_stop is not None and should_stop()) or self._shutdown_requested:
    break
if max_iterations is not None and iterations >= max_iterations:
    break
sleeper(poll_interval_seconds)
```

**Test command:** `pytest tests/unit/test_supervisor.py -q`

---

## Task 3 — `supervisor_exited` event in `close()`

**File:** `src/alpaca_bot/runtime/supervisor.py`

In `close()`, before `self._closed = True`, emit the lifecycle event:

```python
def close(self) -> None:
    if self._closed:
        return
    try:
        self._append_audit(AuditEvent(event_type="supervisor_exited", payload={}))
    except Exception:
        pass  # best-effort: DB may be gone on unclean exit
    if self.stream is not None and hasattr(self.stream, "stop"):
        self.stream.stop()
    if self._stream_thread is not None and self._stream_thread.is_alive():
        self._stream_thread.join(timeout=1.0)
    self._close_runtime(self.runtime)
    self._stream_thread = None
    self._closed = True
```

The `try/except` is necessary here — if the DB connection is already gone (crash scenario), the
audit write will fail and we must still release the advisory lock and close cleanly.

**Test command:** `pytest tests/unit/test_supervisor.py -q`

---

## Task 4 — Stream health fields in `HealthSnapshot`

**File:** `src/alpaca_bot/web/service.py`

Add constant near the top (after `WORKER_STALE_AFTER_SECONDS`):

```python
STREAM_STALE_WINDOW_SECONDS = 600  # 10 minutes
```

Add fields to `HealthSnapshot`:

```python
stream_stale: bool = False
stream_last_stale_at: datetime | None = None
```

In `load_health_snapshot()`, add a **dedicated targeted query** for stream stale events using the
existing `list_by_event_types()` method (do NOT rely on the 12-event `list_recent` window —
`stream_heartbeat_stale` can be displaced by cycle events within 12 minutes at 1 cycle/60s):

```python
from datetime import timedelta

now_utc = datetime.now(timezone.utc)
stale_cutoff = now_utc - timedelta(seconds=STREAM_STALE_WINDOW_SECONDS)
recent_stale = audit_event_store.list_by_event_types(
    event_types=["stream_heartbeat_stale"],
    limit=1,
)
stream_last_stale_at = (
    recent_stale[0].created_at
    if recent_stale and recent_stale[0].created_at >= stale_cutoff
    else None
)
stream_stale = stream_last_stale_at is not None
```

`list_by_event_types()` already exists on `AuditEventStore` at
`src/alpaca_bot/storage/repositories.py:153` — no new method needed.

Pass new fields when constructing `HealthSnapshot`:

```python
return HealthSnapshot(
    ...,
    stream_stale=stream_stale,
    stream_last_stale_at=stream_last_stale_at,
)
```

**Test command:** `pytest tests/unit/test_web_service.py -q`

---

## Task 5 — Expose stream health in `/healthz` and update 503 condition

**File:** `src/alpaca_bot/web/app.py`

`stream_stale` is **informational only — it does NOT affect HTTP 503**. The reason: after market
close, the stream naturally goes quiet. The supervisor fires exactly one `stream_heartbeat_stale`
event ~5 min after the last fill. If stream_stale affected 503, /healthz would return 503 for
10 min after every market close, causing false alarms on external uptime monitors. The web service
has no market-hours knowledge to condition the 503 on.

The 503 condition remains: `worker_stale` only (unchanged from current code).

Add to the JSON response dict:

```python
"stream_stale": health_snapshot.stream_stale,
"stream_last_stale_at": (
    None
    if health_snapshot.stream_last_stale_at is None
    else health_snapshot.stream_last_stale_at.isoformat()
),
```

HTTP status: no change from current logic (503 when `worker_stale`, 200 otherwise).

**Test command:** `pytest tests/unit/test_web_service.py -q`

---

## Task 6 — Docker healthcheck on supervisor service

**File:** `deploy/compose.yaml`

Add `healthcheck` block to the `supervisor` service (alongside existing `restart: unless-stopped`
and `init: true`):

```yaml
    healthcheck:
      test:
        - "CMD"
        - "python"
        - "-c"
        - |
          import os, time
          f = '/tmp/supervisor_heartbeat'
          exit(0 if os.path.exists(f) and time.time() - os.path.getmtime(f) < 180 else 1)
      interval: 60s
      timeout: 5s
      retries: 3
      start_period: 120s
```

`start_period: 120s` accounts for the 60s startup sequence plus one full cycle.

**Test command:** `docker compose -f deploy/compose.yaml config --quiet` (validates YAML syntax)

---

## Task 7 — Tests

**File:** `tests/unit/test_supervisor.py` — add:

```python
def test_shutdown_flag_stops_loop():
    # Set _shutdown_requested = True after first iteration; verify loop exits cleanly
    # and supervisor_exited audit event was appended.
    ...

def test_sigterm_sets_shutdown_flag():
    # Simulate SIGTERM signal (send_signal(SIGTERM) to the process or call the handler
    # directly by invoking signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)).
    # Verify _shutdown_requested becomes True.
    ...

def test_supervisor_started_event_emitted():
    # Run supervisor for 1 iteration; verify first appended audit event is
    # event_type="supervisor_started".
    ...

def test_supervisor_exited_event_emitted_on_close():
    # Call supervisor.close() directly; verify supervisor_exited is in appended events.
    ...

def test_heartbeat_file_written_each_cycle(tmp_path):
    # Monkeypatch /tmp/supervisor_heartbeat path to tmp_path/heartbeat.
    # Run 2 iterations; verify file exists and contains a timestamp.
    ...
```

**File:** `tests/unit/test_web_service.py` — add:

```python
def test_health_snapshot_stream_stale_when_recent_stale_event():
    # Inject a stream_heartbeat_stale audit event within last 600s;
    # verify HealthSnapshot.stream_stale=True and stream_last_stale_at is set.
    ...

def test_health_snapshot_stream_fresh_when_no_recent_stale_event():
    # No stream_heartbeat_stale within 600s; verify stream_stale=False.
    ...

def test_healthz_returns_503_when_stream_stale():
    # Inject stream_heartbeat_stale event; call GET /healthz; verify 503 and
    # stream_stale=True in response JSON.
    ...

def test_healthz_stream_stale_fields_in_response():
    # Verify stream_stale and stream_last_stale_at keys present in /healthz response.
    ...
```

**Test command:** `pytest tests/unit/test_supervisor.py tests/unit/test_web_service.py -q`

---

## Task 8 — Full test suite

```bash
pytest -q
```

All 813+ tests must pass.
