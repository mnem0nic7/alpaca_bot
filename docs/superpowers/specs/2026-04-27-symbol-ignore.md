# Symbol Ignore Feature — Design Spec

**Date:** 2026-04-27
**Status:** Draft

## Problem

The admin can add/remove symbols from the watchlist, but removal is all-or-nothing: a symbol is either tracked (enabled) or gone. There is no way to keep a symbol visible in the dashboard while temporarily preventing the bot from opening new positions in it. This is useful for earnings events, news blackouts, or liquidity concerns where the admin knows a symbol should be watched but not traded until further notice.

## Goal

Add a per-symbol **ignore flag** that:
- Persists across restarts (stored in Postgres)
- Is reversible from the web UI at any time
- Does NOT prevent stop updates or exits on existing open positions in that symbol
- Only suppresses new **entry** orders for ignored symbols
- Shows clearly in the watchlist UI alongside the enabled/disabled state

## Non-goals

- Per-day ignore (that resets at midnight) — use case calls for persistent until manually reversed
- Ignoring an entire strategy — already handled by `StrategyFlag.enabled` and `DailySessionState.entries_disabled`
- Preventing exits — ignored symbols with open positions still get stop updates and exits

## Design

### Storage: extend `symbol_watchlist` table

Add `ignored BOOLEAN NOT NULL DEFAULT FALSE` to the existing `symbol_watchlist` table via a backward-compatible migration. The composite PK `(symbol, trading_mode)` already covers uniqueness.

New column semantics:
- `enabled=TRUE, ignored=FALSE` — tracked and traded (default)
- `enabled=TRUE, ignored=TRUE` — tracked and visible, but no new entries
- `enabled=FALSE, *` — fully removed from watchlist (hidden from active trading)

The `ignore`/`unignore` toggle only operates on enabled symbols (the UI should not show Ignore for disabled rows since they're already effectively ignored).

### WatchlistRecord

Add `ignored: bool` field. The `list_all()` SELECT gains the column; `list_enabled()` stays unchanged (returns all enabled, ignored or not — so the supervisor still fetches bars for ignored symbols, needed for stop/exit management on existing positions).

New store methods:
- `list_ignored(trading_mode) -> list[str]` — returns symbols where `enabled=TRUE AND ignored=TRUE`, ordered alphabetically
- `ignore(symbol, trading_mode, *, commit=True)` — `UPDATE SET ignored = TRUE`
- `unignore(symbol, trading_mode, *, commit=True)` — `UPDATE SET ignored = FALSE`

### Supervisor: entry_symbols vs watchlist_symbols

After reading `watchlist_symbols` from `list_enabled()`, also read `ignored_symbols` from `list_ignored()`. Compute:

```python
ignored_set = set(ignored_symbols)
entry_symbols = tuple(s for s in watchlist_symbols if s not in ignored_set)
```

Pass `entry_symbols` (not `watchlist_symbols`) as `symbols=` to `run_cycle()`. Market data (`get_stock_bars`, `get_daily_bars`) continues to use `list(watchlist_symbols)` so bars are available for stop/exit logic on existing ignored-symbol positions.

`evaluate_cycle()` requires no changes — it already uses the `symbols` param only for entry candidate iteration. Stop updates and exits operate through `open_positions` which is populated independently.

### Web routes (action="watchlist")

Two new POST routes reuse the existing `"watchlist"` CSRF action:

- `POST /admin/watchlist/ignore` — form field `symbol`; sets ignored=TRUE, appends `WATCHLIST_IGNORE` audit event; redirects to `/watchlist`
- `POST /admin/watchlist/unignore` — form field `symbol`; sets ignored=FALSE, appends `WATCHLIST_UNIGNORE` audit event; redirects to `/watchlist`

Both follow the existing atomic commit pattern: `store.ignore(commit=False)` + `audit_store.append(commit=False)` + `connection.commit()`.

### UI changes (watchlist.html)

- Add "ignored" amber badge alongside "enabled"/"disabled"
- Per-symbol buttons: Ignore (shown when enabled+not ignored), Unignore (shown when enabled+ignored)
- Remove button remains; no change to its behavior
- GET /watchlist route passes `ignored_set` as a set of ignored symbols to the template (computed from `list_ignored()` or derived from records directly)

### Audit events

Two new event types: `WATCHLIST_IGNORE`, `WATCHLIST_UNIGNORE`.
Both added to `ALL_AUDIT_EVENT_TYPES` in `web/service.py`.
Payload: `{"symbol": symbol, "operator": operator, "trading_mode": trading_mode}`.
`AuditEvent.symbol` field set to the symbol for audit log filtering.

## Safety Analysis

**Financial safety**: The ignore flag only affects entry evaluation. All stop updates and exits continue normally for ignored symbols' existing positions. The `entry_symbols` computation is deterministic and happens before `run_cycle()` is called — no stale data risk.

**Concurrent cycles**: The advisory lock prevents two supervisors; single-threaded cycle execution within the supervisor means no race between ignore reads and entry writes.

**Audit trail**: Both ignore and unignore write an `AuditEvent` atomically with the DB update. A crash between the two writes rolls back both (standard `commit=False` pattern).

**Intent/dispatch separation**: Not affected — ignore acts at the symbol-selection layer above the intent queue.

**Pure engine boundary**: `evaluate_cycle()` is unchanged.

**Paper vs live safety**: Ignore applies equally to both modes. No env var changes.

**Market hours**: Ignore/unignore is an admin DB update with no broker API calls. Market hours guards are unaffected.

## Migration rollback

The migration is `ALTER TABLE symbol_watchlist ADD COLUMN ignored BOOLEAN NOT NULL DEFAULT FALSE`. Rollback is `ALTER TABLE symbol_watchlist DROP COLUMN ignored`. No data is lost; all rows get `ignored=FALSE` by default so the system behaves identically before the column is used.

## Files changed

| File | Change |
|------|--------|
| `migrations/010_symbol_watchlist_ignored.sql` | New migration: ALTER TABLE |
| `src/alpaca_bot/storage/repositories.py` | WatchlistRecord + WatchlistStore: new field + 3 new methods, update list_all SELECT |
| `src/alpaca_bot/runtime/supervisor.py` | Compute entry_symbols from list_ignored(); pass entry_symbols to run_cycle() |
| `src/alpaca_bot/web/service.py` | Add WATCHLIST_IGNORE, WATCHLIST_UNIGNORE to ALL_AUDIT_EVENT_TYPES |
| `src/alpaca_bot/web/app.py` | 2 new routes; update GET /watchlist to pass ignored info |
| `src/alpaca_bot/web/templates/watchlist.html` | Ignore/Unignore buttons, ignored badge |
| `tests/unit/test_repositories.py` | WatchlistStore ignore/unignore tests |
| `tests/unit/test_runtime_supervisor.py` | entry_symbols computation test |
| `tests/unit/test_web_app.py` | web route tests for ignore/unignore |
