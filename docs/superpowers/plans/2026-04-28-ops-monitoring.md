# Plan: Ops/Monitoring Improvements

Spec: `docs/superpowers/specs/2026-04-28-ops-monitoring.md`

---

## Task 1 — Add `strategy_flags` to `HealthSnapshot` and `load_health_snapshot()`

**File:** `src/alpaca_bot/web/service.py`

Add `strategy_flags: list[tuple[str, bool]]` to `HealthSnapshot` and load flags in
`load_health_snapshot()`.

```python
# In HealthSnapshot dataclass (after worker_health):
strategy_flags: list[tuple[str, bool]] = dc_field(default_factory=list)
```

Update `load_health_snapshot()` signature and body:

```python
def load_health_snapshot(
    *,
    settings: Settings,
    connection: ConnectionProtocol,
    trading_status_store: TradingStatusStore | None = None,
    audit_event_store: AuditEventStore | None = None,
    strategy_flag_store: StrategyFlagStore | None = None,
) -> HealthSnapshot:
    store = trading_status_store or TradingStatusStore(connection)
    audit_event_store = audit_event_store or AuditEventStore(connection)
    strategy_flag_store = strategy_flag_store or StrategyFlagStore(connection)
    now = datetime.now(timezone.utc)
    recent_events = audit_event_store.list_recent(limit=12)
    flags_by_name = {
        f.strategy_name: f.enabled
        for f in strategy_flag_store.list_all(
            trading_mode=settings.trading_mode,
            strategy_version=settings.strategy_version,
        )
    }
    strategy_flags = [(name, flags_by_name.get(name, False)) for name in STRATEGY_REGISTRY]
    return HealthSnapshot(
        trading_status=store.load(
            trading_mode=settings.trading_mode,
            strategy_version=settings.strategy_version,
        ),
        worker_health=_load_worker_health(
            audit_event_store=audit_event_store,
            recent_events=recent_events,
            now=now,
        ),
        strategy_flags=strategy_flags,
    )
```

Also add to imports: `StrategyFlagStore` (already imported), `STRATEGY_REGISTRY` from
`alpaca_bot.strategy`, and `dc_field` from dataclasses (alias for `field`).

**Test command:** `pytest tests/unit/test_web_service.py -q`

---

## Task 2 — Enhance `/healthz` to include `strategy_flags`

**File:** `src/alpaca_bot/web/app.py`

Update `healthz()` to call `load_health_snapshot()` with a `strategy_flag_store` and include the
flags in the JSON response.

In `_load_health()` (or inline in `healthz()`), pass `strategy_flag_store` when calling
`load_health_snapshot()`. Then in the JSONResponse body add:

```python
"strategy_flags": [
    {"name": name, "enabled": enabled}
    for name, enabled in health_snapshot.strategy_flags
],
```

Update `_load_health()` at line 801 of `app.py` to pass `strategy_flag_store`:

```python
def _load_health(app: FastAPI):
    connection = app.state.connect_postgres(app.state.settings.database_url)
    try:
        return load_health_snapshot(
            settings=app.state.settings,
            connection=connection,
            trading_status_store=_build_store(app.state.trading_status_store_factory, connection),
            audit_event_store=_build_store(app.state.audit_event_store_factory, connection),
            strategy_flag_store=_build_store(app.state.strategy_flag_store_factory, connection),
        )
    finally:
        close = getattr(connection, "close", None)
        if callable(close):
            close()
```

**Test command:** `pytest tests/unit/test_web_service.py -q`

---

## Task 3 — Wire `notifier` into `create_app()` and `_execute_admin_status_change()`

**File:** `src/alpaca_bot/web/app.py`

1. Add imports at top of file:
```python
from alpaca_bot.notifications import Notifier
from alpaca_bot.notifications.factory import build_notifier
```

2. Add `notifier: Notifier | None = None` parameter to `create_app()`.

3. After `app_settings = settings or Settings.from_env()`, add:
```python
_notifier = notifier or build_notifier(app_settings)
app.state.notifier = _notifier
```

4. Update `_execute_admin_status_change()` signature to accept `notifier: Notifier`:
```python
def _execute_admin_status_change(
    app: FastAPI,
    *,
    new_status: TradingStatusValue,
    command_name: str,
    kill_switch_enabled: bool,
    reason: str | None,
    operator: str | None,
    notifier: Notifier,
) -> Response:
```

5. After `connection.commit()` in `_execute_admin_status_change()`, add:
```python
    subjects = {
        "halt": "Trading halted",
        "close-only": "Trading set to close-only",
        "resume": "Trading resumed",
    }
    try:
        notifier.send(
            subject=subjects.get(command_name, f"Trading status changed: {command_name}"),
            body=(
                f"mode={app_settings.trading_mode.value} "
                f"strategy={app_settings.strategy_version} "
                f"reason={reason or '-'} "
                f"operator={operator or 'web'}"
            ),
        )
    except Exception:
        import logging
        logging.getLogger(__name__).exception("Notifier send failed after status change")
```

6. Update the three call sites (admin_halt, admin_resume, admin_close_only) to pass
`notifier=app.state.notifier`.

**Test command:** `pytest tests/unit/test_web_service.py -q`

---

## Task 4 — Add close-only and resume notifications to admin CLI

**File:** `src/alpaca_bot/admin/cli.py`

In `run_admin_command()`, add notifier calls for close-only and resume after their
`_write_status_change()` returns:

```python
    if args.command == "close-only":
        result = _write_status_change(...)
        if notifier is not None:
            notifier.send(
                subject="Trading set to close-only",
                body=f"mode={trading_mode.value} strategy={strategy_version} reason={args.reason or '-'}",
            )
        return result

    if args.command == "resume":
        result = _write_status_change(...)
        if notifier is not None:
            notifier.send(
                subject="Trading resumed",
                body=f"mode={trading_mode.value} strategy={strategy_version} reason={args.reason or '-'}",
            )
        return result
```

In `main()`, add equivalent calls after the `_write_status_change()` call for close-only and
resume (the `else` branch currently handles all three in one block — split the resume/close-only
paths or add conditional notifier calls after the shared `_write_status_change()` call).

**Test command:** `pytest tests/unit/test_admin_cli.py -q`

---

## Task 5 — Tests

**File:** `tests/unit/test_web_service.py` — add:

```python
def test_load_health_snapshot_includes_strategy_flags():
    # HealthSnapshot.strategy_flags contains (name, enabled) tuples from STRATEGY_REGISTRY
    ...

def test_healthz_response_includes_strategy_flags():
    # /healthz JSON has "strategy_flags" key with list of {name, enabled} dicts
    ...

def test_execute_admin_status_change_calls_notifier():
    # POST /admin/halt triggers notifier.send with subject "Trading halted"
    ...

def test_execute_admin_resume_calls_notifier():
    # POST /admin/resume triggers notifier.send with subject "Trading resumed"
    ...

def test_notifier_failure_does_not_abort_redirect():
    # If notifier.send() raises, the redirect still returns 303 (not 500)
    ...
```

**File:** `tests/unit/test_admin_cli.py` — add:

```python
def test_close_only_notifies():
    # run_admin_command close-only calls notifier.send

def test_resume_notifies():
    # run_admin_command resume calls notifier.send
```

**Test command:** `pytest tests/unit/test_web_service.py tests/unit/test_admin_cli.py -q`

---

## Task 6 — Full test suite

```bash
pytest -q
```

All 633+ tests must pass.
