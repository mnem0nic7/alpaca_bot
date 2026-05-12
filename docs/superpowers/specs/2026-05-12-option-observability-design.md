# Option Entry Observability — Design Spec

**Date:** 2026-05-12
**Status:** Approved

## Problem

After today's session (7 option put positions, all with heavy losses), there is no way to answer:
- Why did the signal generator select these names?
- What did the intraday bars look like at entry time?
- What filter parameters (volume, IV, chain criteria) were applied?

The `decision_log` table already captures all signal data for every evaluated candidate (entries and rejects), but it is unqueryable from the dashboard. Option ENTRY intents do not emit an audit event at signal time. The audit page filter dropdown is missing 15+ event types.

## Scope

**In scope:**
1. Emit `option_entry_intent_created` audit event from `cycle.py` when an option ENTRY intent is saved.
2. Add `DecisionLogStore.list_recent()` query method.
3. Add `/decisions` dashboard route + HTML template.
4. Fix `ALL_AUDIT_EVENT_TYPES` in `web/service.py`.

**Out of scope:** Option strategy parameter changes, position sizing changes, liquidity filters (Spec B).

## Architecture

Four components, no schema changes:

| Component | File | Change |
|---|---|---|
| Audit event | `src/alpaca_bot/runtime/cycle.py` | Emit `option_entry_intent_created` per option ENTRY intent |
| Query method | `src/alpaca_bot/storage/repositories.py` | Add `DecisionLogStore.list_recent()` |
| Service + allowlist | `src/alpaca_bot/web/service.py` | Add `load_decisions_page()`, fix `ALL_AUDIT_EVENT_TYPES` |
| Route + template | `src/alpaca_bot/web/app.py` + `templates/decisions.html` | GET `/decisions` |

## Component Design

### 1. `option_entry_intent_created` Audit Event

**File:** `src/alpaca_bot/runtime/cycle.py`

Emitted once per option ENTRY intent, appended inside the existing transaction (`commit=False`) immediately after `option_order_store.save()`. If the append fails, the transaction rolls back (identical behavior to equity ENTRY intents).

**Payload:**
```python
{
    "occ_symbol": intent.symbol,
    "underlying_symbol": intent.underlying_symbol,
    "option_type": intent.option_type_str,
    "strike": intent.option_strike,
    "expiry": intent.option_expiry.isoformat(),
    "ask_price": intent.limit_price,
    "quantity": intent.quantity,
    "entry_level": intent.entry_level,
    "signal_timestamp": intent.signal_timestamp.isoformat() if intent.signal_timestamp else None,
}
```

`entry_level` is already set on `CycleIntent` (the breakout level the signal fired at). `signal_timestamp` is the bar timestamp that triggered entry.

### 2. `DecisionLogStore.list_recent()`

**File:** `src/alpaca_bot/storage/repositories.py`

```python
def list_recent(
    self,
    session_date: date,
    symbol: str | None = None,
    limit: int = 200,
) -> list[dict]:
```

**Query:** `SELECT * FROM decision_log WHERE DATE(cycle_at AT TIME ZONE 'America/New_York') = :session_date [AND symbol = :symbol] ORDER BY cycle_at DESC LIMIT :limit`

Returns a list of plain dicts (one per row). Read-only; no write path.

### 3. `web/service.py` changes

**`load_decisions_page(session_date, symbol)`:** Calls `DecisionLogStore.list_recent()`. Returns empty list on exception (logs the error). Passed to the template.

**`ALL_AUDIT_EVENT_TYPES` fix:** Add all missing event types:
- `option_entry_intent_created` (new)
- `option_order_submitted`
- `option_stop_skipped_no_price`
- `option_chains_fetched`
- `decision_cycle_completed`
- `nightly_sweep_completed`
- `order_dispatch_failed`
- `order_dispatch_stop_price_rejected`
- `startup_recovery_completed`
- `startup_recovery_skipped`
- `stream_started`
- `stream_stopped`
- `stream_heartbeat_stale`
- `postgres_reconnected`
- `stale_exit_canceled_for_resubmission` (from stale-exit-deadlock spec)
- `stale_exit_cancel_failed`

The full list must be sorted alphabetically to match the existing style.

### 4. `/decisions` Route + Template

**File:** `src/alpaca_bot/web/app.py`

```
GET /decisions?date=YYYY-MM-DD&symbol=AAPL
```

- `date` param: defaults to today in ET (`settings.market_timezone`). Parsed as `date`.
- `symbol` param: optional, case-insensitive substring match passed to `list_recent()`.
- Returns Jinja2-rendered `decisions.html`.

**Template columns (decisions.html):**
| Column | Source |
|---|---|
| Time (ET) | `cycle_at` formatted in ET |
| Symbol | `symbol` |
| Strategy | `strategy_name` |
| Decision | `decision` (ENTRY / REJECTED, styled green/red) |
| Reject stage | `reject_stage` |
| Reject reason | `reject_reason` |
| Entry level | `entry_level` |
| Bar close | `signal_bar_close` |
| Rel. volume | `relative_volume` (2 decimal places) |
| Filters | `filter_results` JSON (collapsed `<details>` tag) |

**Template structure:**
- Date selector (`<input type="date">`) and symbol text filter, both submit via GET.
- Table below. If empty: "No decisions recorded for this session."
- Follows existing template style (same CSS classes as `positions.html`, `audit.html`).

## Data Flow

```
Engine evaluates candidates
  → DecisionRecords created (already, no change)
  → run_cycle() bulk-inserts into decision_log (already, no change)
  → run_cycle() NOW ALSO appends option_entry_intent_created AuditEvent
     per option ENTRY (inside existing transaction, commit=False)

Dashboard /decisions
  → GET /decisions?date=YYYY-MM-DD&symbol=ALHC
  → service.load_decisions_page(date, symbol)
  → DecisionLogStore.list_recent(session_date, symbol)
  → renders decisions.html table

Dashboard /audit
  → filter dropdown now includes all previously-missing event types
```

## Error Handling

| Scenario | Behavior |
|---|---|
| `option_entry_intent_created` append fails | Transaction rolls back (same as OptionOrderRecord save failure); no partial write |
| `list_recent()` raises | `load_decisions_page()` catches, logs, returns `[]`; template shows empty state |
| Invalid `date` param on `/decisions` | Defaults to today ET |
| No rows for requested date | Template shows "No decisions recorded for this session." |

## Testing

All tests use the fake-callables DI pattern (no mocks).

| Test file | Tests |
|---|---|
| `tests/unit/test_cycle.py` | Option ENTRY intent → exactly one `option_entry_intent_created` event emitted; payload contains expected fields; equity ENTRY → no such event |
| `tests/unit/test_repositories.py` | `list_recent()` returns rows for requested date; `symbol` filter narrows results; empty date returns `[]` |
| `tests/unit/test_web_service.py` | `load_decisions_page()` calls `list_recent()` with correct args; returns `[]` when store raises |
| `tests/unit/test_web_app.py` | GET `/decisions` returns 200; `?symbol=ALHC` filters; missing date defaults to today |
