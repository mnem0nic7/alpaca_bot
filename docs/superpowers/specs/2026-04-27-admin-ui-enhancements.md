# Spec: Admin UI Enhancements & Expanded Operator Controls

**Date:** 2026-04-27  
**Status:** Draft

---

## Problem

The current web dashboard is read-only beyond strategy enable/disable toggling. All halt/resume/close-only operations require SSH access to the production server to run `alpaca-bot-admin`. The dashboard also has no auto-refresh and shows only the last 12 audit events. An operator monitoring positions intraday must either refresh manually or SSH in to take action.

---

## Goals

1. **Admin controls in the web UI** — halt, resume, close-only without requiring SSH access.
2. **Auto-refresh** — dashboard automatically reloads so live data stays current.
3. **Historical metrics** — operators can review any past session day's P&L from the UI.
4. **Full audit log viewer** — paginated view of all audit events with event-type filtering.
5. **Richer position display** — stop distance %, trailing stop activation indicator.
6. **Per-strategy entries disabled toggle** — fine-grained entry control beyond the global halt.

---

## Non-Goals

- Real-time unrealized P&L (requires broker price streaming into the web layer — out of scope).
- Manual stop price override via UI (requires broker API call — out of scope).
- Force-flatten via UI (requires new supervisor signal path and schema migration — deferred).
- Multi-user auth / role-based access (single operator model is sufficient).
- JavaScript framework / AJAX (all interactions remain server-rendered, no JS dependencies).

---

## Design

### 1. Auto-Refresh

Add `<meta http-equiv="refresh" content="30">` to `dashboard.html`. Operators can disable it by adding `?no_refresh=1` (the route strips the meta tag when that param is present). No server changes required.

### 2. Admin Controls in Web UI

Three new `POST` endpoints added to `app.py`:

```
POST /admin/halt        — body: reason (required)
POST /admin/resume      — body: reason (optional)
POST /admin/close-only  — body: reason (optional)
```

All three:
- Require auth (redirect to `/login` if unauthenticated).
- Require a valid CSRF token (action=`"admin"`).
- Call `_write_status_change` from `admin/cli.py` directly — the same atomic DB write (TradingStatus + AuditEvent in one commit) used by the CLI.
- Set `operator` field in the audit event payload to the authenticated operator email.
- Redirect to `/` on success.
- Return 400 on missing required fields.

The dashboard hero panel gains an **Operator Controls** section with three buttons and a reason text input. Confirmation is via `onclick="return confirm(...)"` — no JS framework needed.

**Safety**: `halt` is the only action that sets `kill_switch_enabled=True`. The web endpoint matches the CLI exactly. `ENABLE_LIVE_TRADING` and `TRADING_MODE` remain the sole real-order gates — these endpoints write to Postgres only, not to the broker.

### 3. Historical Metrics

`GET /metrics?date=YYYY-MM-DD` — the `date` query param overrides today's session date for the metrics load. `load_metrics_snapshot` already accepts any `session_date`; this just threads the user-supplied date through. Invalid or future dates return today's data with a warning banner. The metrics template gains previous/next day navigation links.

### 4. Full Audit Log Viewer

New route: `GET /audit?limit=50&offset=0&event_type=<type>`

`AuditEventStore.list_recent` gains an `offset: int = 0` parameter (appends `OFFSET %s` to the existing query). A new `load_audit_page` function in `service.py` assembles the data. New template `audit.html` shows the event table with pagination controls and an event-type filter dropdown.

### 5. Position Risk Display

Add two derived columns to the Open Positions table:
- **Stop distance %** — `(entry_price - stop_price) / entry_price * 100` — computed in the template via a Jinja2 filter or inline expression.
- **Trailing** — "yes" if `stop_price > initial_stop_price`, "no" otherwise.

No code changes needed beyond the template. The `PositionRecord` already has all four fields.

### 6. Per-Strategy Entries Disabled Toggle

New endpoint: `POST /strategies/{name}/toggle-entries`

Loads today's `DailySessionState` for the strategy, flips `entries_disabled`, calls `DailySessionStateStore.save` with `commit=False`, appends `AuditEvent(event_type="strategy_entries_changed", payload={"strategy_name": name, "entries_disabled": new_value, "operator": ...})`, commits. Auth + CSRF required (action=`"toggle"`). Redirects to `/`.

The Strategies panel in the dashboard shows a second button per strategy: **"Disable Entries" / "Enable Entries"**.

---

## Audit Trail

Every new web admin action appends an `AuditEvent`:

| Endpoint | event_type | payload fields |
|---|---|---|
| `POST /admin/halt` | `trading_status_changed` | command, trading_mode, strategy_version, status, reason, operator |
| `POST /admin/resume` | `trading_status_changed` | same |
| `POST /admin/close-only` | `trading_status_changed` | same |
| `POST /strategies/{name}/toggle-entries` | `strategy_entries_changed` | strategy_name, entries_disabled, operator |

All writes are atomic (status/state + event in one `connection.commit()`).

---

## Safety Analysis

**Financial safety:** Admin control endpoints write to Postgres only. The supervisor picks up changes on the next poll cycle (≤60 seconds). No broker API calls in the web layer. `ENABLE_LIVE_TRADING` gate is unchanged.

**Audit trail:** Every state change produces an `AuditEvent`. Crash between the write and the redirect leaves a committed audit row — that is better than silence.

**Intent/dispatch separation:** Not affected. These endpoints write `TradingStatus`, not `orders`.

**Advisory lock:** Not affected — the web container never acquires the advisory lock.

**Pure engine boundary:** `evaluate_cycle()` is not touched.

**Rollback safety:** No migrations required. All tables already exist.

**CSRF/auth:** All mutating endpoints require auth + CSRF token. Read-only endpoints (`/audit`, `/metrics?date=...`) require auth but no CSRF.

**Paper vs live:** Behavior is identical in both modes — the web UI reads `settings.trading_mode` from env, and all writes are scoped to that mode. A misconfigured `TRADING_MODE=live` env would only affect what `TradingStatus` row is written — it cannot cause real orders.

---

## Files Affected

| File | Change type |
|---|---|
| `src/alpaca_bot/web/app.py` | Add 5 new routes |
| `src/alpaca_bot/web/service.py` | Add `load_audit_page`; `load_metrics_snapshot` session_date param is already there via `now` override — thread explicit `session_date` instead |
| `src/alpaca_bot/web/templates/dashboard.html` | Auto-refresh, admin controls panel, position risk columns, entries-disabled toggle buttons |
| `src/alpaca_bot/web/templates/audit.html` | New template |
| `src/alpaca_bot/storage/repositories.py` | `AuditEventStore.list_recent` gains `offset` param |
| `tests/unit/test_web_app.py` | Tests for new routes |
| `tests/unit/test_web_service.py` | Tests for `load_audit_page` |

No migrations. No changes to `admin/cli.py`, `storage/models.py`, `core/engine.py`, or any runtime files.
