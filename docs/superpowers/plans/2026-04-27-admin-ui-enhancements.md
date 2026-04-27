# Implementation Plan: Admin UI Enhancements & Expanded Operator Controls

**Date:** 2026-04-27  
**Spec:** `docs/superpowers/specs/2026-04-27-admin-ui-enhancements.md`  
**Grill fixes applied:** Jinja2 max-filter bug → `AuditLogPage` computed properties; `list_by_event_types` offset added; `DashboardSnapshot.strategy_entries_disabled` added with `DailySessionStateStore.list_by_session`; three admin endpoints extracted into one `_execute_admin_status_change` helper.

---

## Overview

Seven tasks (one added for the storage helper). No database migrations. All changes are in the web layer and storage layer.

| Task | File(s) | What |
|---|---|---|
| 1 | `dashboard.html`, `app.py` | Auto-refresh (30s) with `?no_refresh=1` escape hatch |
| 2 | `repositories.py` | `AuditEventStore.list_recent` + `list_by_event_types` add `offset` param |
| 3 | `repositories.py` | `DailySessionStateStore.list_by_session` new query |
| 4 | `service.py`, `app.py`, `audit.html` | Audit log page + route |
| 5 | `service.py`, `app.py`, `dashboard.html` | Historical metrics via `?date=` |
| 6 | `app.py`, `dashboard.html` | Halt/Resume/Close-Only web endpoints + dashboard panel |
| 7 | `app.py`, `service.py`, `dashboard.html` | Per-strategy entries_disabled toggle |

---

## Task 1 — Auto-refresh

**Files:** `src/alpaca_bot/web/templates/dashboard.html`, `src/alpaca_bot/web/app.py`

### app.py

```python
@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request, no_refresh: bool = False) -> HTMLResponse:
    operator = current_operator(request, settings=app_settings)
    if auth_enabled(app_settings) and operator is None:
        return _render_login_page(app, request, next_path=request.url.path)
    try:
        snapshot, metrics = _load_dashboard_data(app)
    except Exception:
        return HTMLResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content="<html><body><h1>alpaca_bot dashboard unavailable</h1></body></html>",
        )
    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={
            "request": request,
            "trading_mode": app_settings.trading_mode.value,
            "strategy_version": app_settings.strategy_version,
            "snapshot": snapshot,
            "metrics": metrics,
            "operator_email": operator,
            "no_refresh": no_refresh,
        },
    )
```

### dashboard.html — inside `<head>`, after `<title>`:

```html
{% if not no_refresh %}
<meta http-equiv="refresh" content="30">
{% endif %}
```

**Tests in `tests/unit/test_web_app.py`:**

```python
def test_dashboard_route_has_auto_refresh_meta_tag() -> None:
    app = create_app(settings=make_settings(), connect_postgres_fn=ConnectionFactory([_empty_conn()]))
    client = TestClient(app)
    response = client.get("/")
    assert response.status_code == 200
    assert 'http-equiv="refresh"' in response.text

def test_dashboard_no_refresh_suppresses_meta_tag() -> None:
    app = create_app(settings=make_settings(), connect_postgres_fn=ConnectionFactory([_empty_conn()]))
    client = TestClient(app)
    response = client.get("/?no_refresh=1")
    assert response.status_code == 200
    assert 'http-equiv="refresh"' not in response.text
```

Test command: `pytest tests/unit/test_web_app.py::test_dashboard_route_has_auto_refresh_meta_tag tests/unit/test_web_app.py::test_dashboard_no_refresh_suppresses_meta_tag -v`

---

## Task 2 — AuditEventStore offset support

**File:** `src/alpaca_bot/storage/repositories.py`

### list_recent — add `offset: int = 0`

```python
def list_recent(self, *, limit: int = 20, offset: int = 0) -> list[AuditEvent]:
    rows = fetch_all(
        self._connection,
        """
        SELECT event_type, symbol, payload, created_at
        FROM audit_events
        ORDER BY created_at DESC, event_id DESC
        LIMIT %s
        OFFSET %s
        """,
        (limit, offset),
    )
    return [
        AuditEvent(
            event_type=row[0],
            symbol=row[1],
            payload=_load_json_payload(row[2]),
            created_at=row[3],
        )
        for row in rows
    ]
```

### list_by_event_types — add `offset: int = 0`

```python
def list_by_event_types(
    self,
    *,
    event_types: list[str],
    limit: int = 20,
    offset: int = 0,
) -> list[AuditEvent]:
    if not event_types:
        return []
    placeholders = ", ".join(["%s"] * len(event_types))
    rows = fetch_all(
        self._connection,
        f"""
        SELECT event_type, symbol, payload, created_at
        FROM audit_events
        WHERE event_type IN ({placeholders})
        ORDER BY created_at DESC, event_id DESC
        LIMIT %s
        OFFSET %s
        """,
        (*event_types, limit, offset),
    )
    return [
        AuditEvent(
            event_type=row[0],
            symbol=row[1],
            payload=_load_json_payload(row[2]),
            created_at=row[3],
        )
        for row in rows
    ]
```

Both changes are backward-compatible: existing callers pass no `offset`, so they get `offset=0`.

**Tests in `tests/unit/test_web_service.py`:**

```python
def test_audit_event_store_list_recent_offset_appended_to_query() -> None:
    from alpaca_bot.storage.repositories import AuditEventStore

    class TrackingConn:
        def __init__(self):
            self.params_seen = []
            self.closed = False
        def cursor(self):
            conn = self
            class C:
                def execute(self, sql, params=None):
                    conn.params_seen.append(params)
                def fetchall(self):
                    return []
            return C()
        def commit(self): pass
        def close(self): self.closed = True

    conn = TrackingConn()
    store = AuditEventStore(conn)
    store.list_recent(limit=10, offset=50)
    assert any(50 in (p or ()) for p in conn.params_seen)
```

Test command: `pytest tests/unit/test_web_service.py::test_audit_event_store_list_recent_offset_appended_to_query -v`

---

## Task 3 — DailySessionStateStore.list_by_session

**File:** `src/alpaca_bot/storage/repositories.py`

Add a new method to `DailySessionStateStore`:

```python
def list_by_session(
    self,
    *,
    session_date: Any,
    trading_mode: TradingMode,
    strategy_version: str,
) -> list[DailySessionState]:
    rows = fetch_all(
        self._connection,
        """
        SELECT
            session_date, trading_mode, strategy_version, strategy_name,
            entries_disabled, flatten_complete, last_reconciled_at,
            notes, equity_baseline, updated_at
        FROM daily_session_state
        WHERE session_date = %s
          AND trading_mode = %s
          AND strategy_version = %s
        """,
        (session_date, trading_mode.value, strategy_version),
    )
    return [
        DailySessionState(
            session_date=row[0],
            trading_mode=TradingMode(row[1]),
            strategy_version=row[2],
            strategy_name=row[3],
            entries_disabled=bool(row[4]),
            flatten_complete=bool(row[5]),
            last_reconciled_at=row[6],
            notes=row[7],
            equity_baseline=float(row[8]) if row[8] is not None else None,
            updated_at=row[9],
        )
        for row in rows
    ]
```

**Tests in `tests/unit/test_web_service.py`:**

```python
def test_daily_session_state_store_list_by_session_returns_all_rows() -> None:
    from alpaca_bot.storage.repositories import DailySessionStateStore
    from datetime import date

    rows_returned = [
        ("2026-01-15", "paper", "v1", "breakout", True, False, None, None, None, datetime.now(timezone.utc)),
        ("2026-01-15", "paper", "v1", "momentum", False, False, None, None, None, datetime.now(timezone.utc)),
    ]

    class FakeConn:
        def cursor(self):
            class C:
                def execute(self, sql, params=None): pass
                def fetchall(self): return rows_returned
            return C()
        def commit(self): pass
        def close(self): pass

    store = DailySessionStateStore(FakeConn())
    results = store.list_by_session(
        session_date=date(2026, 1, 15),
        trading_mode=TradingMode.PAPER,
        strategy_version="v1",
    )
    assert len(results) == 2
    assert results[0].strategy_name == "breakout"
    assert results[0].entries_disabled is True
    assert results[1].strategy_name == "momentum"
```

Test command: `pytest tests/unit/test_web_service.py::test_daily_session_state_store_list_by_session_returns_all_rows -v`

---

## Task 4 — Audit log page: service, route, template

**Files:** `src/alpaca_bot/web/service.py`, `src/alpaca_bot/web/app.py`, `src/alpaca_bot/web/templates/audit.html`

### service.py — AuditLogPage dataclass and load_audit_page

Add to `service.py` (after `MetricsSnapshot`):

```python
ALL_AUDIT_EVENT_TYPES = [
    "trading_status_changed",
    "strategy_flag_changed",
    "strategy_entries_changed",
    "supervisor_cycle",
    "supervisor_idle",
    "supervisor_cycle_error",
    "strategy_cycle_error",
    "trader_startup_completed",
    "daily_loss_limit_breached",
    "postgres_reconnected",
    "runtime_reconciliation_detected",
    "trade_update_stream_started",
    "trade_update_stream_stopped",
    "trade_update_stream_failed",
    "trade_update_stream_restarted",
    "stream_restart_failed",
]


@dataclass(frozen=True)
class AuditLogPage:
    events: list[AuditEvent]
    limit: int
    offset: int
    has_more: bool
    event_type_filter: str | None

    @property
    def prev_offset(self) -> int | None:
        if self.offset <= 0:
            return None
        return max(0, self.offset - self.limit)

    @property
    def next_offset(self) -> int | None:
        return self.offset + self.limit if self.has_more else None


def load_audit_page(
    *,
    connection: ConnectionProtocol,
    audit_event_store: AuditEventStore | None = None,
    limit: int = 50,
    offset: int = 0,
    event_type_filter: str | None = None,
) -> AuditLogPage:
    store = audit_event_store or AuditEventStore(connection)
    limit = max(1, min(limit, 200))
    offset = max(0, offset)
    if event_type_filter:
        events = store.list_by_event_types(
            event_types=[event_type_filter],
            limit=limit + 1,
            offset=offset,
        )
    else:
        events = store.list_recent(limit=limit + 1, offset=offset)
    has_more = len(events) > limit
    return AuditLogPage(
        events=events[:limit],
        limit=limit,
        offset=offset,
        has_more=has_more,
        event_type_filter=event_type_filter,
    )
```

### app.py — new GET /audit route

```python
from alpaca_bot.web.service import (
    load_audit_page,
    load_dashboard_snapshot,
    load_health_snapshot,
    load_metrics_snapshot,
    ALL_AUDIT_EVENT_TYPES,
)

@app.get("/audit", response_class=HTMLResponse)
def audit_log(
    request: Request,
    limit: int = 50,
    offset: int = 0,
    event_type: str | None = None,
) -> HTMLResponse:
    operator = current_operator(request, settings=app_settings)
    if auth_enabled(app_settings) and operator is None:
        return RedirectResponse(url="/login?next=/audit", status_code=status.HTTP_303_SEE_OTHER)
    connection = None
    try:
        connection = app.state.connect_postgres(app_settings.database_url)
        audit_store = _build_store(app.state.audit_event_store_factory, connection)
        page = load_audit_page(
            connection=connection,
            audit_event_store=audit_store,
            limit=limit,
            offset=offset,
            event_type_filter=event_type or None,
        )
    except Exception:
        return HTMLResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content="<html><body><h1>Audit log unavailable</h1></body></html>",
        )
    finally:
        if connection is not None:
            close = getattr(connection, "close", None)
            if callable(close):
                close()
    return templates.TemplateResponse(
        request=request,
        name="audit.html",
        context={
            "request": request,
            "trading_mode": app_settings.trading_mode.value,
            "strategy_version": app_settings.strategy_version,
            "page": page,
            "all_event_types": ALL_AUDIT_EVENT_TYPES,
            "operator_email": operator,
        },
    )
```

### audit.html — new file

```html
<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Audit Log — alpaca_bot</title>
    <style>
      :root { color-scheme: light; --bg: #f3f0e8; --panel: #fffdf8; --line: #d8d2c5; --ink: #1d2b2a; --muted: #6a736e; --accent: #1f6f78; --warn: #8f3b2e; }
      * { box-sizing: border-box; }
      body { margin: 0; font-family: "Iowan Old Style","Palatino Linotype",serif; background: var(--bg); color: var(--ink); }
      main { max-width: 1200px; margin: 0 auto; padding: 2rem 1.25rem 3rem; }
      h1 { margin: 0; font-weight: 700; }
      p { margin: 0; }
      .panel { background: rgba(255,253,248,0.92); border: 1px solid var(--line); border-radius: 18px; padding: 1.1rem 1.2rem; box-shadow: 0 10px 30px rgba(29,43,42,0.06); }
      .eyebrow { font-size: 0.78rem; text-transform: uppercase; color: var(--muted); letter-spacing: 0.08em; }
      table { width: 100%; border-collapse: collapse; margin-top: 0.9rem; }
      th, td { text-align: left; padding: 0.55rem 0; border-bottom: 1px solid rgba(216,210,197,0.8); font-size: 0.92rem; vertical-align: top; }
      th { color: var(--muted); font-size: 0.78rem; text-transform: uppercase; letter-spacing: 0.08em; }
      .mono { font-family: "SFMono-Regular","Menlo",monospace; font-size: 0.85rem; }
      .muted { color: var(--muted); }
      .nav { display: flex; gap: 1rem; margin-top: 1.2rem; align-items: center; }
      a { color: var(--accent); }
      a.btn { border: 1px solid var(--line); border-radius: 999px; background: #f7f2e7; color: var(--ink); padding: 0.4rem 0.9rem; text-decoration: none; font-size: 0.9rem; }
      select, button { font: inherit; border: 1px solid var(--line); border-radius: 999px; background: #f7f2e7; color: var(--ink); padding: 0.35rem 0.8rem; cursor: pointer; }
    </style>
  </head>
  <body>
    <main>
      <p style="margin-bottom:1rem;"><a href="/">&larr; Dashboard</a></p>
      <h1>Audit Log</h1>
      <p class="muted" style="margin-top:0.4rem;">
        <span class="mono">{{ trading_mode }}</span> / <span class="mono">{{ strategy_version }}</span>
      </p>

      <div class="panel" style="margin-top:1.5rem;">
        <form method="get" action="/audit" style="display:flex; gap:1rem; align-items:center; flex-wrap:wrap; margin-bottom:1rem;">
          <label class="eyebrow" style="margin:0;">Event type</label>
          <select name="event_type">
            <option value="">All</option>
            {% for et in all_event_types %}
              <option value="{{ et }}" {% if page.event_type_filter == et %}selected{% endif %}>{{ et }}</option>
            {% endfor %}
          </select>
          <input type="hidden" name="limit" value="{{ page.limit }}">
          <button type="submit">Filter</button>
        </form>

        <table>
          <thead>
            <tr>
              <th>Time</th>
              <th>Event Type</th>
              <th>Symbol</th>
              <th>Payload</th>
            </tr>
          </thead>
          <tbody>
            {% for event in page.events %}
              <tr>
                <td class="mono muted">{{ format_timestamp(event.created_at) }}</td>
                <td><strong>{{ event.event_type }}</strong></td>
                <td class="mono">{{ event.symbol or "—" }}</td>
                <td class="mono muted" style="word-break:break-all;">{{ event.payload }}</td>
              </tr>
            {% else %}
              <tr><td colspan="4" class="muted">No events found.</td></tr>
            {% endfor %}
          </tbody>
        </table>

        <div class="nav">
          {% if page.prev_offset is not none %}
            <a class="btn" href="/audit?limit={{ page.limit }}&offset={{ page.prev_offset }}{% if page.event_type_filter %}&event_type={{ page.event_type_filter }}{% endif %}">&larr; Newer</a>
          {% endif %}
          {% if page.next_offset is not none %}
            <a class="btn" href="/audit?limit={{ page.limit }}&offset={{ page.next_offset }}{% if page.event_type_filter %}&event_type={{ page.event_type_filter }}{% endif %}">Older &rarr;</a>
          {% endif %}
          <span class="muted" style="font-size:0.85rem;">
            Showing {{ page.offset + 1 }}–{{ page.offset + page.events|length }}
          </span>
        </div>
      </div>
    </main>
  </body>
</html>
```

**Tests:**

```python
def test_load_audit_page_paginates_with_has_more() -> None:
    from alpaca_bot.web.service import load_audit_page
    from datetime import datetime, timezone

    def make_event(n):
        return AuditEvent(event_type="supervisor_cycle", payload={"n": n},
                          created_at=datetime.now(timezone.utc))

    class FakeAuditStore:
        def list_recent(self, *, limit, offset=0):
            # returns limit+1 events to signal has_more
            return [make_event(i) for i in range(limit)]
        def list_by_event_types(self, *, event_types, limit, offset=0):
            return [make_event(i) for i in range(limit)]

    page = load_audit_page(connection=None, audit_event_store=FakeAuditStore(), limit=3)
    assert len(page.events) == 3
    assert page.has_more is True
    assert page.next_offset == 3
    assert page.prev_offset is None

def test_load_audit_page_prev_offset_correct() -> None:
    from alpaca_bot.web.service import load_audit_page, AuditLogPage
    # Construct directly to test the property
    page = AuditLogPage(events=[], limit=50, offset=100, has_more=False, event_type_filter=None)
    assert page.prev_offset == 50
    assert page.next_offset is None

def test_audit_route_returns_200() -> None:
    app = create_app(settings=make_settings(), connect_postgres_fn=ConnectionFactory([_empty_conn()]))
    client = TestClient(app)
    response = client.get("/audit")
    assert response.status_code == 200
    assert "Audit Log" in response.text

def test_audit_route_with_event_type_filter() -> None:
    app = create_app(settings=make_settings(), connect_postgres_fn=ConnectionFactory([_empty_conn()]))
    client = TestClient(app)
    response = client.get("/audit?event_type=supervisor_cycle")
    assert response.status_code == 200
```

Test command: `pytest tests/unit/test_web_app.py::test_audit_route_returns_200 tests/unit/test_web_service.py::test_load_audit_page_paginates_with_has_more -v`

---

## Task 5 — Historical metrics via `?date=` param

**Files:** `src/alpaca_bot/web/service.py`, `src/alpaca_bot/web/app.py`, `src/alpaca_bot/web/templates/dashboard.html`

### service.py — add explicit `session_date` param

```python
def load_metrics_snapshot(
    *,
    settings: Settings,
    connection: ConnectionProtocol,
    order_store: OrderStore | None = None,
    audit_event_store: AuditEventStore | None = None,
    tuning_result_store: TuningResultStore | None = None,
    now: datetime | None = None,
    session_date: date | None = None,          # new
) -> MetricsSnapshot:
    generated_at = now or datetime.now(timezone.utc)
    resolved_date = session_date or generated_at.astimezone(settings.market_timezone).date()
    order_store = order_store or OrderStore(connection)
    audit_event_store = audit_event_store or AuditEventStore(connection)
    tuning_store = tuning_result_store or TuningResultStore(connection)

    raw_trades = order_store.list_closed_trades(
        trading_mode=settings.trading_mode,
        strategy_version=settings.strategy_version,
        session_date=resolved_date,                   # use resolved_date
        market_timezone=str(settings.market_timezone),
    )
    ...
    return MetricsSnapshot(
        generated_at=generated_at,
        session_date=resolved_date,                   # use resolved_date
        ...
    )
```

### app.py — modify /metrics route and _load_dashboard_data

```python
@app.get("/metrics", response_class=HTMLResponse)
def metrics_page(request: Request, date: str | None = None) -> HTMLResponse:
    operator = current_operator(request, settings=app_settings)
    if auth_enabled(app_settings) and operator is None:
        return _render_login_page(app, request, next_path=request.url.path)
    session_date_override = _parse_date_param(date)
    try:
        _, metrics = _load_dashboard_data(app, session_date_override=session_date_override)
    except Exception:
        return HTMLResponse(status_code=503, content="<html><body><h1>Metrics unavailable</h1></body></html>")
    from datetime import timedelta
    today = datetime.now(app_settings.market_timezone).date()
    prev_date = (metrics.session_date - timedelta(days=1)).isoformat()
    next_date = (metrics.session_date + timedelta(days=1)).isoformat()
    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={
            "request": request,
            "settings": app_settings,
            "snapshot": None,
            "metrics": metrics,
            "operator_email": operator,
            "no_refresh": True,            # suppress auto-refresh on historical view
            "prev_date": prev_date,
            "next_date": next_date,
            "is_today": metrics.session_date == today,
        },
    )
```

Add helper and update `_load_dashboard_data`:

```python
def _parse_date_param(date_str: str | None):
    if not date_str:
        return None
    try:
        from datetime import date as date_type
        return date_type.fromisoformat(date_str)
    except ValueError:
        return None


def _load_dashboard_data(app: FastAPI, *, session_date_override=None) -> tuple:
    connection = app.state.connect_postgres(app.state.settings.database_url)
    try:
        order_store = _build_store(app.state.order_store_factory, connection)
        audit_event_store = _build_store(app.state.audit_event_store_factory, connection)
        snapshot = load_dashboard_snapshot(
            settings=app.state.settings,
            connection=connection,
            trading_status_store=_build_store(app.state.trading_status_store_factory, connection),
            daily_session_state_store=_build_store(app.state.daily_session_state_store_factory, connection),
            position_store=_build_store(app.state.position_store_factory, connection),
            order_store=order_store,
            audit_event_store=audit_event_store,
            strategy_flag_store=_build_store(app.state.strategy_flag_store_factory, connection),
        )
        metrics = load_metrics_snapshot(
            settings=app.state.settings,
            connection=connection,
            order_store=order_store,
            audit_event_store=audit_event_store,
            session_date=session_date_override,    # thread through
        )
        return snapshot, metrics
    finally:
        close = getattr(connection, "close", None)
        if callable(close):
            close()
```

### dashboard.html — add date nav to Session P&L panel

Replace the existing date display line:
```html
<p class="value mono">{{ metrics.session_date.isoformat() }}</p>
```
with:
```html
<div style="display:flex; gap:1rem; align-items:center; margin-top:0.3rem;">
  <a href="/metrics?date={{ prev_date }}" style="color:var(--accent);">&larr;</a>
  <span class="mono">{{ metrics.session_date.isoformat() }}</span>
  {% if not is_today %}
    <a href="/metrics?date={{ next_date }}" style="color:var(--accent);">&rarr;</a>
  {% endif %}
</div>
```

These template variables default to `None` on the main dashboard (which also renders metrics inline). Guard with `{% if prev_date %}` to avoid errors on the main dashboard where these vars are absent.

**Tests:**

```python
def test_metrics_route_accepts_date_param() -> None:
    app = create_app(settings=make_settings(), connect_postgres_fn=ConnectionFactory([_empty_conn(), _empty_conn()]))
    client = TestClient(app)
    response = client.get("/metrics?date=2026-01-15")
    assert response.status_code == 200

def test_metrics_route_ignores_invalid_date_param() -> None:
    app = create_app(settings=make_settings(), connect_postgres_fn=ConnectionFactory([_empty_conn(), _empty_conn()]))
    client = TestClient(app)
    response = client.get("/metrics?date=not-a-date")
    assert response.status_code == 200

def test_load_metrics_snapshot_respects_explicit_session_date() -> None:
    from alpaca_bot.web.service import load_metrics_snapshot
    from datetime import date
    # ... fake stores returning empty results
    metrics = load_metrics_snapshot(
        settings=make_settings(),
        connection=...,
        order_store=FakeOrderStore(),
        audit_event_store=FakeAuditStore(),
        session_date=date(2026, 1, 15),
    )
    assert metrics.session_date == date(2026, 1, 15)
```

Test command: `pytest tests/unit/test_web_app.py -k "metrics" -v`

---

## Task 6 — Admin controls (halt / resume / close-only) via web UI

**Files:** `src/alpaca_bot/web/app.py`, `src/alpaca_bot/web/templates/dashboard.html`

### app.py — shared helper + three endpoints

Add imports at top of `create_app` closure (or at module level):
```python
from alpaca_bot.storage import TradingStatusValue  # add to existing storage imports
```

Add private helper inside `create_app` (or as module-level function):

```python
def _execute_admin_status_change(
    *,
    app: FastAPI,
    operator: str | None,
    new_status: TradingStatusValue,
    kill_switch_enabled: bool,
    reason: str | None,
    command_name: str,
) -> None:
    now = datetime.now(timezone.utc)
    app_s = app.state.settings
    connection = app.state.connect_postgres(app_s.database_url)
    try:
        status_store = _build_store(app.state.trading_status_store_factory, connection)
        audit_store = _build_store(app.state.audit_event_store_factory, connection)
        status_store.save(
            TradingStatus(
                trading_mode=app_s.trading_mode,
                strategy_version=app_s.strategy_version,
                status=new_status,
                kill_switch_enabled=kill_switch_enabled,
                status_reason=reason,
                updated_at=now,
            ),
            commit=False,
        )
        audit_store.append(
            AuditEvent(
                event_type="trading_status_changed",
                payload={
                    "command": command_name,
                    "trading_mode": app_s.trading_mode.value,
                    "strategy_version": app_s.strategy_version,
                    "status": new_status.value,
                    "operator": operator or "web",
                    **({"reason": reason} if reason else {}),
                },
                created_at=now,
            ),
            commit=False,
        )
        connection.commit()
    finally:
        close = getattr(connection, "close", None)
        if callable(close):
            close()
```

Three endpoints (all inside `create_app`, referencing `app_settings` and `csrf_secret` from closure):

```python
@app.post("/admin/halt")
async def admin_halt(request: Request) -> Response:
    operator = current_operator(request, settings=app_settings)
    if auth_enabled(app_settings) and operator is None:
        return RedirectResponse(url="/login?next=/", status_code=status.HTTP_303_SEE_OTHER)
    fields = await _read_form_fields(request)
    if not validate_csrf_token(request, fields.get("_csrf_token", ""),
                               settings=app_settings, action="admin", csrf_secret=csrf_secret):
        return HTMLResponse(status_code=status.HTTP_403_FORBIDDEN, content="Forbidden")
    reason = fields.get("reason", "").strip()
    if not reason:
        return HTMLResponse(status_code=status.HTTP_400_BAD_REQUEST, content="Reason required for halt")
    _execute_admin_status_change(
        app=app, operator=operator,
        new_status=TradingStatusValue.HALTED, kill_switch_enabled=True,
        reason=reason, command_name="halt",
    )
    return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)


@app.post("/admin/resume")
async def admin_resume(request: Request) -> Response:
    operator = current_operator(request, settings=app_settings)
    if auth_enabled(app_settings) and operator is None:
        return RedirectResponse(url="/login?next=/", status_code=status.HTTP_303_SEE_OTHER)
    fields = await _read_form_fields(request)
    if not validate_csrf_token(request, fields.get("_csrf_token", ""),
                               settings=app_settings, action="admin", csrf_secret=csrf_secret):
        return HTMLResponse(status_code=status.HTTP_403_FORBIDDEN, content="Forbidden")
    _execute_admin_status_change(
        app=app, operator=operator,
        new_status=TradingStatusValue.ENABLED, kill_switch_enabled=False,
        reason=fields.get("reason", "").strip() or None, command_name="resume",
    )
    return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)


@app.post("/admin/close-only")
async def admin_close_only(request: Request) -> Response:
    operator = current_operator(request, settings=app_settings)
    if auth_enabled(app_settings) and operator is None:
        return RedirectResponse(url="/login?next=/", status_code=status.HTTP_303_SEE_OTHER)
    fields = await _read_form_fields(request)
    if not validate_csrf_token(request, fields.get("_csrf_token", ""),
                               settings=app_settings, action="admin", csrf_secret=csrf_secret):
        return HTMLResponse(status_code=status.HTTP_403_FORBIDDEN, content="Forbidden")
    _execute_admin_status_change(
        app=app, operator=operator,
        new_status=TradingStatusValue.CLOSE_ONLY, kill_switch_enabled=False,
        reason=fields.get("reason", "").strip() or None, command_name="close-only",
    )
    return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)
```

Note: `_execute_admin_status_change` must be defined at module level (not inside `create_app`) since it takes `app` as a parameter and can't reference the closure. Import `TradingStatus`, `TradingStatusValue`, and `AuditEvent` at the top of `app.py` (update existing storage imports).

### dashboard.html — Operator Controls panel

Add after the Strategies panel and before the section-grid:

```html
{% if snapshot %}
<div class="panel" style="margin-bottom: 1rem;">
  <h2>Operator Controls</h2>
  <p class="muted" style="margin-top:0.4rem; margin-bottom:1rem; font-size:0.9rem;">
    Changes take effect on the next supervisor cycle (&le;60&nbsp;s).
    &nbsp;<a href="/audit" style="color:var(--accent);">View audit log &rarr;</a>
  </p>

  <form method="post" action="/admin/halt"
        style="display:flex; gap:0.75rem; align-items:center; flex-wrap:wrap; margin-bottom:0.75rem;"
        onsubmit="return confirm('Halt trading? This engages the kill switch.')">
    <input type="hidden" name="_csrf_token" value="{{ csrf_token_for(request, 'admin') }}">
    <input type="text" name="reason" placeholder="Reason (required)"
           style="border:1px solid var(--line); border-radius:8px; padding:0.35rem 0.7rem; font:inherit; background:#fffdf8; flex:1; min-width:12rem;" required>
    <button type="submit"
            style="border:1px solid var(--warn); border-radius:999px; background:#fdf0ee; color:var(--warn); padding:0.4rem 1rem; font:inherit; cursor:pointer;">
      Halt
    </button>
  </form>

  <form method="post" action="/admin/close-only"
        style="display:flex; gap:0.75rem; align-items:center; flex-wrap:wrap; margin-bottom:0.75rem;"
        onsubmit="return confirm('Set to close-only mode?')">
    <input type="hidden" name="_csrf_token" value="{{ csrf_token_for(request, 'admin') }}">
    <input type="text" name="reason" placeholder="Reason (optional)"
           style="border:1px solid var(--line); border-radius:8px; padding:0.35rem 0.7rem; font:inherit; background:#fffdf8; flex:1; min-width:12rem;">
    <button type="submit"
            style="border:1px solid var(--line); border-radius:999px; background:#f7f2e7; color:var(--ink); padding:0.4rem 1rem; font:inherit; cursor:pointer;">
      Close-Only
    </button>
  </form>

  <form method="post" action="/admin/resume"
        style="display:flex; gap:0.75rem; align-items:center; flex-wrap:wrap;"
        onsubmit="return confirm('Resume normal trading?')">
    <input type="hidden" name="_csrf_token" value="{{ csrf_token_for(request, 'admin') }}">
    <input type="text" name="reason" placeholder="Reason (optional)"
           style="border:1px solid var(--line); border-radius:8px; padding:0.35rem 0.7rem; font:inherit; background:#fffdf8; flex:1; min-width:12rem;">
    <button type="submit"
            style="border:1px solid var(--accent); border-radius:999px; background:#eef6f7; color:var(--accent); padding:0.4rem 1rem; font:inherit; cursor:pointer;">
      Resume
    </button>
  </form>
</div>
{% endif %}
```

**Tests:**

```python
def _make_tracking_stores():
    """Returns (app, committed_statuses, committed_events)."""
    committed_statuses = []
    committed_events = []
    class TrackingStatusStore:
        def save(self, s, *, commit=True): committed_statuses.append(s)
    class TrackingAuditStore:
        def append(self, e, *, commit=True): committed_events.append(e)
        def list_recent(self, **_): return []
        def load_latest(self, **_): return None
    ...

def test_admin_halt_writes_halted_status_and_audit_event() -> None:
    app, committed_statuses, committed_events = _make_tracking_stores()
    client = TestClient(app)
    # get a valid CSRF token first
    response = client.get("/")
    token = _extract_csrf_token(response.text, action="admin")
    response = client.post("/admin/halt",
        data={"reason": "test halt", "_csrf_token": token},
        follow_redirects=False)
    assert response.status_code == 303
    assert any(s.status.value == "halted" and s.kill_switch_enabled for s in committed_statuses)
    assert any(e.event_type == "trading_status_changed" for e in committed_events)

def test_admin_halt_requires_reason() -> None:
    ...
    response = client.post("/admin/halt", data={"reason": "", "_csrf_token": token})
    assert response.status_code == 400

def test_admin_halt_rejected_without_csrf() -> None:
    ...
    response = client.post("/admin/halt", data={"reason": "test", "_csrf_token": "bad"})
    assert response.status_code == 403

def test_admin_resume_writes_enabled_status() -> None: ...
def test_admin_close_only_writes_close_only_status() -> None: ...
```

Test command: `pytest tests/unit/test_web_app.py -k "admin_halt or admin_resume or admin_close_only" -v`

---

## Task 7 — Per-strategy entries_disabled toggle

**Files:** `src/alpaca_bot/web/service.py`, `src/alpaca_bot/web/app.py`, `src/alpaca_bot/web/templates/dashboard.html`

### service.py — extend DashboardSnapshot

Add `strategy_entries_disabled: dict[str, bool]` field with a default:

```python
from dataclasses import dataclass, field as dc_field

@dataclass(frozen=True)
class DashboardSnapshot:
    generated_at: datetime
    trading_status: TradingStatus | None
    session_state: DailySessionState | None
    positions: list[PositionRecord]
    working_orders: list[OrderRecord]
    recent_orders: list[OrderRecord]
    recent_events: list[AuditEvent]
    worker_health: WorkerHealth
    strategy_flags: list[tuple[str, StrategyFlag | None]]
    strategy_entries_disabled: dict[str, bool] = dc_field(default_factory=dict)  # new
```

Update `load_dashboard_snapshot` to populate it using `DailySessionStateStore.list_by_session`:

```python
def load_dashboard_snapshot(...) -> DashboardSnapshot:
    ...
    # existing code
    all_session_states = daily_session_state_store.list_by_session(
        session_date=session_date,
        trading_mode=settings.trading_mode,
        strategy_version=settings.strategy_version,
    ) if hasattr(daily_session_state_store, "list_by_session") else []
    entries_disabled_by_strategy = {s.strategy_name: s.entries_disabled for s in all_session_states}

    return DashboardSnapshot(
        ...
        strategy_entries_disabled=entries_disabled_by_strategy,
    )
```

The `hasattr` guard is a DI-safety check: tests that inject fake stores without `list_by_session` get an empty dict (no breakage), while production uses the real store.

### app.py — new POST /strategies/{name}/toggle-entries

Add to imports in `app.py`:
```python
from alpaca_bot.storage import DailySessionState  # add to storage imports
```

```python
@app.post("/strategies/{strategy_name}/toggle-entries")
async def toggle_strategy_entries(strategy_name: str, request: Request) -> Response:
    operator = current_operator(request, settings=app_settings)
    if auth_enabled(app_settings) and operator is None:
        return RedirectResponse(url="/login?next=/", status_code=status.HTTP_303_SEE_OTHER)
    fields = await _read_form_fields(request)
    if not validate_csrf_token(request, fields.get("_csrf_token", ""),
                               settings=app_settings, action="toggle", csrf_secret=csrf_secret):
        return HTMLResponse(status_code=status.HTTP_403_FORBIDDEN, content="Forbidden")
    if strategy_name not in STRATEGY_REGISTRY:
        return HTMLResponse(status_code=status.HTTP_404_NOT_FOUND, content="Unknown strategy")
    now = datetime.now(timezone.utc)
    session_date = now.astimezone(app_settings.market_timezone).date()
    connection = None
    try:
        connection = app.state.connect_postgres(app_settings.database_url)
        session_store = _build_store(app.state.daily_session_state_store_factory, connection)
        audit_store = _build_store(app.state.audit_event_store_factory, connection)
        current_state = session_store.load(
            session_date=session_date,
            trading_mode=app_settings.trading_mode,
            strategy_version=app_settings.strategy_version,
            strategy_name=strategy_name,
        )
        current_disabled = current_state.entries_disabled if current_state is not None else False
        new_disabled = not current_disabled
        session_store.save(
            DailySessionState(
                session_date=session_date,
                trading_mode=app_settings.trading_mode,
                strategy_version=app_settings.strategy_version,
                strategy_name=strategy_name,
                entries_disabled=new_disabled,
                flatten_complete=current_state.flatten_complete if current_state else False,
                equity_baseline=current_state.equity_baseline if current_state else None,
                notes=current_state.notes if current_state else None,
                updated_at=now,
            ),
            commit=False,
        )
        audit_store.append(
            AuditEvent(
                event_type="strategy_entries_changed",
                payload={
                    "strategy_name": strategy_name,
                    "entries_disabled": new_disabled,
                    "operator": operator or "web",
                },
                created_at=now,
            ),
            commit=False,
        )
        connection.commit()
    finally:
        if connection is not None:
            close = getattr(connection, "close", None)
            if callable(close):
                close()
    return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)
```

### dashboard.html — add entries toggle to Strategies panel

Replace the existing strategies loop with:

```html
<div class="panel" style="margin-bottom: 1rem;">
  <h2>Strategies</h2>
  {% for name, flag in snapshot.strategy_flags %}
    <div style="display:flex; align-items:center; gap:1rem; margin-bottom:0.6rem; flex-wrap:wrap;">
      <span class="mono" style="min-width:8rem;">{{ name }}</span>

      {% set is_enabled = (flag is none or flag.enabled) %}
      <span class="{{ '' if is_enabled else 'warn' }}">{{ "Enabled" if is_enabled else "Disabled" }}</span>
      <form method="post" action="/strategies/{{ name }}/toggle" style="display:inline;">
        <input type="hidden" name="_csrf_token" value="{{ csrf_token_for(request, 'toggle') }}">
        <button type="submit"
                style="border:1px solid var(--line); border-radius:999px; background:#f7f2e7; color:var(--ink); padding:0.3rem 0.7rem; font:inherit; cursor:pointer; font-size:0.85rem;">
          {{ "Disable" if is_enabled else "Enable" }}
        </button>
      </form>

      {% set entries_off = snapshot.strategy_entries_disabled.get(name, False) %}
      <span class="muted" style="font-size:0.85rem;">Entries: <span class="{{ 'warn' if entries_off else '' }}">{{ "off" if entries_off else "on" }}</span></span>
      <form method="post" action="/strategies/{{ name }}/toggle-entries" style="display:inline;">
        <input type="hidden" name="_csrf_token" value="{{ csrf_token_for(request, 'toggle') }}">
        <button type="submit"
                style="border:1px solid var(--line); border-radius:999px; background:#f7f2e7; color:var(--ink); padding:0.3rem 0.7rem; font:inherit; cursor:pointer; font-size:0.85rem;">
          {{ "Enable entries" if entries_off else "Disable entries" }}
        </button>
      </form>
    </div>
  {% endfor %}
</div>
```

### dashboard.html — Position risk columns

Modify the Open Positions table header and rows:

```html
<thead>
  <tr>
    <th>Symbol</th><th>Strategy</th><th>Qty</th>
    <th>Entry</th><th>Stop</th><th>Stop%</th><th>Trailing</th><th>Opened</th>
  </tr>
</thead>
<tbody>
  {% for position in snapshot.positions %}
    {% set stop_pct = ((position.entry_price - position.stop_price) / position.entry_price * 100) if position.entry_price > 0 else 0 %}
    {% set is_trailing = position.stop_price > position.initial_stop_price %}
    <tr>
      <td class="mono">{{ position.symbol }}</td>
      <td>{{ position.strategy_name }}</td>
      <td>{{ position.quantity }}</td>
      <td>{{ format_price(position.entry_price) }}</td>
      <td>{{ format_price(position.stop_price) }}</td>
      <td class="muted">{{ "%.1f"|format(stop_pct) }}%</td>
      <td>{{ "yes" if is_trailing else "—" }}</td>
      <td>{{ format_timestamp(position.opened_at) }}</td>
    </tr>
  {% else %}
    <tr><td colspan="8" class="muted">No open positions.</td></tr>
  {% endfor %}
</tbody>
```

**Tests:**

```python
def test_toggle_entries_disables_entries_for_known_strategy() -> None:
    ...
    response = client.post("/strategies/breakout/toggle-entries",
        data={"_csrf_token": token}, follow_redirects=False)
    assert response.status_code == 303
    # verify DailySessionState was saved with entries_disabled=True

def test_toggle_entries_rejected_without_csrf() -> None:
    ...
    response = client.post("/strategies/breakout/toggle-entries",
        data={"_csrf_token": "bad"})
    assert response.status_code == 403

def test_toggle_entries_returns_404_for_unknown_strategy() -> None:
    ...
    response = client.post("/strategies/unknown_strategy/toggle-entries",
        data={"_csrf_token": token})
    assert response.status_code == 404
```

Test command: `pytest tests/unit/test_web_app.py -k "toggle_entries" -v`

---

## Final test run

```bash
pytest tests/unit/test_web_app.py tests/unit/test_web_service.py -v
pytest  # full suite — must pass 575+ existing tests plus ~18 new tests
```

---

## Commit message

```
Admin UI enhancements: halt/resume/close-only via web, audit log, historical metrics, auto-refresh, per-strategy entries toggle
```
