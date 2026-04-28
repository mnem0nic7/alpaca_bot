# Spec: Dynamic Symbol Watchlist Management

**Date:** 2026-04-27  
**Status:** Draft  

## Problem

`SYMBOLS` is a static comma-separated environment variable read once at startup. Adding or removing a tracked stock requires restarting all Docker containers, which interrupts the supervisor loop and causes a gap in monitoring.

## Goal

Let the admin add and remove symbols to track from the web UI without restarting any service. Changes take effect on the next 60-second supervisor cycle.

## Design

### Storage: `symbol_watchlist` table

New Postgres table keyed by `(symbol, trading_mode)`. Strategy version is intentionally excluded — the watchlist is a market-universe decision, not a strategy-version decision.

```sql
CREATE TABLE IF NOT EXISTS symbol_watchlist (
    symbol TEXT NOT NULL,
    trading_mode TEXT NOT NULL,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    added_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    added_by TEXT NOT NULL DEFAULT 'system',
    PRIMARY KEY (symbol, trading_mode),
    CHECK (trading_mode IN ('paper', 'live'))
);
```

### Seeding

On supervisor startup, if the `symbol_watchlist` table is empty for the current `trading_mode`, seed it from `settings.symbols`. This means the first deploy after migration has zero downtime — the table is seeded automatically.

### Supervisor loop change

At the start of each `run_cycle_once()`, query `WatchlistStore.list_enabled(trading_mode)` to get the current symbol list. Use this dynamic list for:
- Market data bar fetching
- `evaluate_cycle()` iteration

### `evaluate_cycle()` change

Add `symbols: tuple[str, ...] | None = None` parameter. If `None`, falls back to `settings.symbols`. The engine iterates over this list for entry signal evaluation. All existing tests that don't pass `symbols` continue to work unchanged.

### Strategy guard removal

Five strategy files contain `if symbol not in settings.symbols: return None`. These guards are redundant (the engine controls which symbols it evaluates) and would incorrectly reject watchlist symbols that aren't in the `SYMBOLS` env var after this change. Remove them.

### Open positions for removed symbols

Removing a symbol from the watchlist does NOT close or orphan its open position. The engine still receives `open_positions` from Postgres and generates `update_stop` / `exit` intents for them regardless of watchlist membership. Only new *entries* for that symbol are prevented.

### Web UI: `/watchlist` page

A new admin-only server-rendered page showing:
- Current watchlist (symbol, enabled, date added, who added)
- Remove button per symbol (POST /admin/watchlist/remove)
- Add form: text input + submit (POST /admin/watchlist/add)

Uses the existing CSRF token pattern (`X-CSRF-Action: watchlist`).

### Audit events

Two new event types appended to `audit_events`:
- `WATCHLIST_ADD` — payload: `{"symbol": "AAPL", "added_by": "admin", "trading_mode": "paper"}`
- `WATCHLIST_REMOVE` — payload: `{"symbol": "AAPL", "removed_by": "admin", "trading_mode": "paper"}`

### Symbol validation

On add, validate the symbol matches `^[A-Z]{1,5}$` (1-5 uppercase letters). Reject obvious garbage input. Do not validate against a live broker lookup — that would add I/O to the web layer.

### Nav integration

Add `/watchlist` link to the nav bar in all three templates (dashboard, metrics, audit).

## Out of scope

- Per-symbol position sizing overrides
- Symbol-level enable/disable toggle (separate from watchlist membership)
- Historical performance per symbol
- Auto-discovery of symbols from broker account

## Safety analysis

| Concern | Mitigation |
|---|---|
| Concurrent add+remove during a cycle | Supervisor reads watchlist once at cycle start; atomic snapshot |
| Empty watchlist after removes | UI prevents removing last symbol; backend rejects if would result in 0 enabled |
| Wrong trading_mode in UI | Watchlist page shows and modifies only the current `trading_mode` from settings |
| Open positions orphaned | Engine always processes `open_positions` from DB, regardless of watchlist |
| Strategy guard blocks new symbol | All five strategy `settings.symbols` guards removed |
