# Session Diagnostics Design

**Date:** 2026-05-11
**Status:** Approved

---

## Problem

`alpaca-bot-session-eval` reports performance metrics (P&L, Sharpe, win rate, exit breakdown) for completed trades, but gives no signal about operational problems during the session. A profitable-looking report could still mask dispatch failures, stream interruptions, unfilled entries, or open carryover positions — all of which require operator attention.

The desired behaviour: every session eval run also prints a **"Diagnostics"** section that surfaces operational issues from the same session window, and confirms "no issues found" when the day ran cleanly.

---

## Scope

Six diagnostic categories, in order of severity:

| Category | Data source |
|---|---|
| Cycle errors | `supervisor_cycle_error`, `strategy_cycle_error` audit events |
| Dispatch failures | `order_dispatch_failed` audit events |
| Unfilled entries | `orders` rows with `intent_type='entry'` and `status IN ('canceled','rejected')` for the session date |
| Stream interruptions | `stream_heartbeat_stale`, `stream_restart_failed`, `trade_update_stream_failed` audit events |
| Open positions at EOD | All rows in `positions` table for the mode/version |
| Reconciliation issues | `reconciliation_miss_count_incremented`, `runtime_reconciliation_detected` audit events |

---

## Architecture

### Session window

The diagnostic window is midnight-to-midnight Eastern time for the eval date:

```python
tz = ZoneInfo(settings.market_timezone)
session_start = datetime.combine(eval_date, time(0, 0), tzinfo=tz).astimezone(timezone.utc)
session_end   = datetime.combine(eval_date + timedelta(days=1), time(0, 0), tzinfo=tz).astimezone(timezone.utc)
```

All audit event queries use this window via the new `since`/`until` params on `list_by_event_types`.

### Repository changes

**`AuditEventStore.list_by_event_types`** (modify signature):

```python
def list_by_event_types(
    self,
    *,
    event_types: list[str],
    limit: int = 20,
    offset: int = 0,
    since: datetime | None = None,
    until: datetime | None = None,
) -> list[AuditEvent]:
```

Adds optional `since`/`until` params that inject `AND created_at >= %s` / `AND created_at < %s` clauses. The composite index `(event_type, created_at DESC)` on `audit_events` makes this efficient.

**`OrderStore.list_failed_entries`** (new method):

```python
def list_failed_entries(
    self,
    *,
    trading_mode: TradingMode,
    strategy_version: str,
    session_date: date,
    market_timezone: str = "America/New_York",
) -> list[OrderRecord]:
    """Entry orders canceled or rejected on the given session date."""
```

Uses `DATE(updated_at AT TIME ZONE %s) = %s` — same pattern as `list_closed_trades`.

`PositionStore.list_all` is already sufficient for open positions; no changes needed.

### CLI changes

**New `SessionDiagnostics` dataclass** in `session_eval_cli.py`:

```python
@dataclass
class SessionDiagnostics:
    cycle_errors: list[AuditEvent]
    dispatch_failures: list[AuditEvent]
    failed_entries: list[OrderRecord]
    stream_issues: list[AuditEvent]
    open_positions: list[PositionRecord]
    reconciliation_issues: list[AuditEvent]

    @property
    def has_issues(self) -> bool:
        return any([
            self.cycle_errors, self.dispatch_failures, self.failed_entries,
            self.stream_issues, self.open_positions, self.reconciliation_issues,
        ])
```

**New `_build_session_diagnostics`** function (pure construction, testable):

```python
def _build_session_diagnostics(
    conn: ConnectionProtocol,
    *,
    trading_mode: TradingMode,
    strategy_version: str,
    eval_date: date,
    market_timezone: str,
) -> SessionDiagnostics:
```

**New `_print_session_diagnostics`** function: prints the Diagnostics section.

**`main`**: calls `_build_session_diagnostics` and `_print_session_diagnostics` after the existing performance report. Always runs (no flag).

### Output example

Clean day:

```
 Diagnostics
 ────────────────────────────────────────────────
 ✓ No operational issues found
```

Day with issues:

```
 Diagnostics
 ────────────────────────────────────────────────
 ⚠ Cycle errors: 2
     14:32:01 — ZeroDivisionError in strategy_cycle
     14:58:17 — ZeroDivisionError in strategy_cycle
 ⚠ Dispatch failures: 1
     NVDA: connection timeout
 ⚠ Unfilled entries: AAPL (canceled), MSFT (rejected)
 ⚠ Stream interruptions: 3
 ⚠ Open positions at EOD: TSLA (opened 2026-05-11)
 ⚠ Reconciliation issues: 1
```

### Strategy filter behaviour

The `--strategy` flag narrows the **performance** section only. Diagnostic categories (cycle errors, stream issues, reconciliation) are session-wide — they reflect the whole system, not a single strategy. Unfilled entries and open positions filter by `strategy_version` but not `strategy_name`, consistent with how those tables are scoped.

---

## Tests

New file: `tests/unit/test_session_eval_diagnostics.py`

| Test | Scenario |
|---|---|
| `test_no_issues_build` | All stores return empty → `has_issues` is False |
| `test_cycle_errors_surfaced` | Two `supervisor_cycle_error` events → counted |
| `test_dispatch_failures_surfaced` | One `order_dispatch_failed` event → listed |
| `test_unfilled_entries_listed` | Canceled and rejected entry orders → in `failed_entries` |
| `test_stream_interruptions_counted` | `stream_heartbeat_stale` events → counted |
| `test_open_positions_flagged` | One open position → `open_positions` non-empty |
| `test_reconciliation_issues_counted` | `runtime_reconciliation_detected` event → counted |
| `test_audit_event_store_since_until_filter` | `list_by_event_types` with `since`/`until` → filters by time window |
| `test_print_no_issues` | `_print_session_diagnostics` with clean diagnostics → "No operational issues found" |
| `test_print_with_issues` | `_print_session_diagnostics` with all categories populated → all ⚠ lines printed |

---

## What Is Not Changing

- Performance metrics section of `session_eval_cli.py` — no changes.
- Exit reason attribution (`profit_target_wins/losses` always 0 in live reports) — this is a pre-existing schema gap (no `reason` column on `orders`) that is out of scope here.
- `BacktestReport` / `report_from_records` — no changes.
- No new CLI entry point.
- No database migrations.
