# Plan: Dynamic Symbol Watchlist Management

**Date:** 2026-04-27  
**Spec:** docs/superpowers/specs/2026-04-27-symbol-watchlist.md  
**Revision:** r3 — post-grilling-2 fixes: bootstrap variable name, _get_connection DNE, validate_csrf_token kwargs

---

## Task 1 — Migration: `009_symbol_watchlist.sql`

**File:** `migrations/009_symbol_watchlist.sql`

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

CREATE INDEX IF NOT EXISTS idx_symbol_watchlist_trading_mode
    ON symbol_watchlist (trading_mode)
    WHERE enabled = TRUE;
```

**Test command:** `alpaca-bot-migrate && psql $DATABASE_URL -c "\d symbol_watchlist"`

---

## Task 2 — `WatchlistRecord` model and `WatchlistStore`

**File:** `src/alpaca_bot/storage/repositories.py`

Add after the `StrategyFlagStore` class (end of file).

### `WatchlistRecord` dataclass

```python
@dataclass(frozen=True)
class WatchlistRecord:
    symbol: str
    trading_mode: str
    enabled: bool
    added_at: datetime
    added_by: str
```

### `WatchlistStore` class

```python
class WatchlistStore:
    def __init__(self, connection) -> None:
        self._conn = connection

    def list_enabled(self, trading_mode: str) -> list[str]:
        with self._conn.cursor() as cur:
            cur.execute(
                "SELECT symbol FROM symbol_watchlist "
                "WHERE trading_mode = %s AND enabled = TRUE "
                "ORDER BY symbol",
                (trading_mode,),
            )
            return [row[0] for row in cur.fetchall()]

    def list_all(self, trading_mode: str) -> list[WatchlistRecord]:
        with self._conn.cursor() as cur:
            cur.execute(
                "SELECT symbol, trading_mode, enabled, added_at, added_by "
                "FROM symbol_watchlist "
                "WHERE trading_mode = %s "
                "ORDER BY symbol",
                (trading_mode,),
            )
            rows = cur.fetchall()
        return [
            WatchlistRecord(
                symbol=r[0],
                trading_mode=r[1],
                enabled=r[2],
                added_at=r[3],
                added_by=r[4],
            )
            for r in rows
        ]

    def add(self, symbol: str, trading_mode: str, added_by: str = "system") -> None:
        """Insert or re-enable a symbol. Idempotent."""
        with self._conn.cursor() as cur:
            cur.execute(
                "INSERT INTO symbol_watchlist (symbol, trading_mode, enabled, added_at, added_by) "
                "VALUES (%s, %s, TRUE, NOW(), %s) "
                "ON CONFLICT (symbol, trading_mode) DO UPDATE "
                "SET enabled = TRUE, added_at = NOW(), added_by = EXCLUDED.added_by",
                (symbol, trading_mode, added_by),
            )

    def remove(self, symbol: str, trading_mode: str) -> None:
        """Soft delete — sets enabled=FALSE, preserves history."""
        with self._conn.cursor() as cur:
            cur.execute(
                "UPDATE symbol_watchlist SET enabled = FALSE "
                "WHERE symbol = %s AND trading_mode = %s",
                (symbol, trading_mode),
            )

    def seed(self, symbols: tuple[str, ...], trading_mode: str) -> None:
        """Insert symbols that don't yet exist. Does not re-enable disabled ones."""
        with self._conn.cursor() as cur:
            for symbol in symbols:
                cur.execute(
                    "INSERT INTO symbol_watchlist (symbol, trading_mode, enabled, added_at, added_by) "
                    "VALUES (%s, %s, TRUE, NOW(), 'system') "
                    "ON CONFLICT (symbol, trading_mode) DO NOTHING",
                    (symbol, trading_mode),
                )
```

**Test command:** `pytest tests/unit/test_repositories.py -v -k watchlist`

---

## Task 3 — `evaluate_cycle()` symbols parameter

**File:** `src/alpaca_bot/core/engine.py`

Add `symbols: tuple[str, ...] | None = None` as the last parameter of `evaluate_cycle()`. The full signature currently ends at `global_open_count: int | None = None` (line ~64). Append after it:

```python
    symbols: tuple[str, ...] | None = None,
```

In the entry-signal loop (line 142):
```python
# Before:
for symbol in settings.symbols:

# After:
for symbol in (symbols or settings.symbols):
```

**Test command:** `pytest tests/unit/test_cycle_engine.py -v`

---

## Task 3b — `run_cycle()` symbols parameter

**File:** `src/alpaca_bot/runtime/cycle.py`

`run_cycle()` is the default `cycle_runner` callable that the supervisor uses. It must accept and forward the new `symbols` kwarg.

Read the file first to find the exact signature. Then add `symbols: tuple[str, ...] | None = None` to its parameter list, and pass it through to the `evaluate_cycle()` call:

```python
result = evaluate_cycle(
    ...,
    symbols=symbols,
)
```

**Test command:** `pytest tests/unit/test_supervisor.py -v`

---

## Task 4 — Remove strategy `settings.symbols` guards

**Files:**
- `src/alpaca_bot/strategy/breakout.py` line 44
- `src/alpaca_bot/strategy/momentum.py` line 19
- `src/alpaca_bot/strategy/ema_pullback.py` line 62
- `src/alpaca_bot/strategy/orb.py` line 19
- `src/alpaca_bot/strategy/high_watermark.py` line 19

In each file, delete the two-line block:
```python
if symbol not in settings.symbols:
    return None
```

Rationale: these guards block watchlist symbols not present in the `SYMBOLS` env var. The engine already controls which symbols are evaluated — the guards are redundant and incorrect after this change.

**Test command:** `pytest tests/unit/ -v -k strategy`

---

## Task 5 — Supervisor: seed watchlist + query on each cycle

### 5a — Seeding at startup

**File:** `src/alpaca_bot/runtime/bootstrap.py`

After migrations are applied and the advisory lock is acquired (but before returning `RuntimeContext`), add. **Use `runtime_connection`** — the actual variable name in `bootstrap_runtime()`:

```python
from alpaca_bot.storage.repositories import WatchlistStore

watchlist_store = WatchlistStore(runtime_connection)
enabled = watchlist_store.list_enabled(settings.trading_mode)
if not enabled:
    watchlist_store.seed(settings.symbols, settings.trading_mode)
    runtime_connection.commit()
    logger.info("Seeded symbol watchlist from SYMBOLS env: %s", list(settings.symbols))
else:
    logger.info("Symbol watchlist loaded (%d symbols): %s", len(enabled), enabled)
```

Also store `WatchlistStore` in `RuntimeContext` so routes and the supervisor can reuse it:

```python
# Add to RuntimeContext dataclass:
watchlist_store: WatchlistStore
```

### 5b — Cycle-time query

**File:** `src/alpaca_bot/runtime/supervisor.py`

In `RuntimeSupervisor.run_cycle_once()`, before the bar-fetch block (before line 310), read the live watchlist:

```python
watchlist_symbols = tuple(
    self.runtime.watchlist_store.list_enabled(self.settings.trading_mode)
)
if not watchlist_symbols:
    logger.warning("Symbol watchlist is empty — skipping cycle")
    return
```

Replace the two bar-fetch `symbols=` arguments:
```python
# Before (line 310):
symbols=list(self.settings.symbols),
# After:
symbols=list(watchlist_symbols),

# Before (line 319):
symbols=list(self.settings.symbols),
# After:
symbols=list(watchlist_symbols),
```

Pass `symbols=watchlist_symbols` to `self._cycle_runner()`:
```python
self._cycle_runner(
    ...,
    symbols=watchlist_symbols,
)
```

**Note on test fakes:** Existing test fakes for `_cycle_runner` (lambdas / `SimpleNamespace`) will receive the new `symbols` kwarg. Any fake that doesn't accept `**kwargs` must be updated to accept `symbols=None`. Audit all usages in `test_supervisor.py`.

**Test command:** `pytest tests/unit/test_supervisor.py -v`

---

## Task 6 — Add audit event types

**File:** `src/alpaca_bot/web/service.py`

In `ALL_AUDIT_EVENT_TYPES` list, append two entries:
```python
"WATCHLIST_ADD",
"WATCHLIST_REMOVE",
```

---

## Task 7 — Web app: inject `WatchlistStore` + add routes

**File:** `src/alpaca_bot/web/app.py`

### 7a — App state (factory pattern — matches existing injection style)

Import `WatchlistStore, WatchlistRecord` from `alpaca_bot.storage.repositories`.

Add to `create_app()` signature:
```python
watchlist_store_factory: Callable | None = None,
```

Store the factory (never an instance) on `app.state`:
```python
app.state.watchlist_store_factory = watchlist_store_factory or WatchlistStore
```

Routes instantiate the store per-request via `_build_store()` (the same helper used by all other routes):
```python
store = _build_store(request.app.state.watchlist_store_factory, conn)
```

Where `conn` is the per-request connection from `_get_connection(request)` (follow the exact pattern used in `toggle_strategy` or similar route).

### 7b — Symbol validation helper

Add module-level (before route definitions):
```python
import re
_SYMBOL_RE = re.compile(r'^[A-Z]{1,5}$')

def _validate_symbol(raw: str) -> str | None:
    cleaned = raw.strip().upper()
    return cleaned if _SYMBOL_RE.match(cleaned) else None
```

### Connection pattern note

`_get_connection(request)` does NOT exist. All routes open a connection inline via `app.state.connect_postgres(app_settings.database_url)` and close it in a `try/finally`. Follow the pattern from `toggle_strategy` (app.py lines 462–498) exactly.

`validate_csrf_token` signature: `validate_csrf_token(request, token, *, settings, action, csrf_secret)` — all three extras are keyword-only. Always call as:
```python
validate_csrf_token(request, token, settings=app_settings, action="watchlist", csrf_secret=csrf_secret)
```

Use `_read_form_fields(request)` (the existing helper) instead of `await request.form()` to match codebase conventions.

### 7c — GET /watchlist

```python
@app.get("/watchlist", response_class=HTMLResponse)
async def watchlist_page(request: Request):
    connection = app.state.connect_postgres(app_settings.database_url)
    try:
        store = _build_store(app.state.watchlist_store_factory, connection)
        records = store.list_all(app_settings.trading_mode)
        enabled_count = sum(1 for r in records if r.enabled)
    finally:
        connection.close()
    return templates.TemplateResponse(
        "watchlist.html",
        {
            "request": request,
            "records": records,
            "enabled_count": enabled_count,
            "trading_mode": app_settings.trading_mode,
            "strategy_version": app_settings.strategy_version,
            "error": request.query_params.get("error", ""),
        },
    )
```

### 7d — POST /admin/watchlist/add

CSRF token travels as a hidden form field `_csrf_token` (same pattern as halt/resume/close-only).

```python
@app.post("/admin/watchlist/add")
async def watchlist_add(request: Request):
    fields = await _read_form_fields(request)
    token = fields.get("_csrf_token", "")
    if not validate_csrf_token(request, token, settings=app_settings, action="watchlist", csrf_secret=csrf_secret):
        raise HTTPException(status_code=403, detail="Invalid CSRF token")
    symbol_raw = fields.get("symbol", "")
    validated = _validate_symbol(symbol_raw)
    if not validated:
        return RedirectResponse("/watchlist?error=invalid_symbol", status_code=303)
    connection = app.state.connect_postgres(app_settings.database_url)
    try:
        store = _build_store(app.state.watchlist_store_factory, connection)
        store.add(validated, app_settings.trading_mode, added_by="admin")
        audit = _build_store(app.state.audit_event_store_factory, connection)
        audit.append(AuditEvent(
            event_type="WATCHLIST_ADD",
            symbol=validated,
            payload={"added_by": "admin", "trading_mode": app_settings.trading_mode},
            created_at=datetime.now(UTC),
        ), commit=False)
        connection.commit()
    finally:
        connection.close()
    return RedirectResponse("/watchlist", status_code=303)
```

### 7e — POST /admin/watchlist/remove

```python
@app.post("/admin/watchlist/remove")
async def watchlist_remove(request: Request):
    fields = await _read_form_fields(request)
    token = fields.get("_csrf_token", "")
    if not validate_csrf_token(request, token, settings=app_settings, action="watchlist", csrf_secret=csrf_secret):
        raise HTTPException(status_code=403, detail="Invalid CSRF token")
    symbol = fields.get("symbol", "").strip().upper()
    connection = app.state.connect_postgres(app_settings.database_url)
    try:
        store = _build_store(app.state.watchlist_store_factory, connection)
        enabled = store.list_enabled(app_settings.trading_mode)
        if len(enabled) <= 1 and symbol in enabled:
            return RedirectResponse("/watchlist?error=last_symbol", status_code=303)
        store.remove(symbol, app_settings.trading_mode)
        audit = _build_store(app.state.audit_event_store_factory, connection)
        audit.append(AuditEvent(
            event_type="WATCHLIST_REMOVE",
            symbol=symbol,
            payload={"removed_by": "admin", "trading_mode": app_settings.trading_mode},
            created_at=datetime.now(UTC),
        ), commit=False)
        connection.commit()
    finally:
        connection.close()
    return RedirectResponse("/watchlist", status_code=303)
```

**Test command:** `pytest tests/unit/test_web_app.py -v -k watchlist`

---

## Task 8 — `watchlist.html` template

**File:** `src/alpaca_bot/web/templates/watchlist.html`

Mirror the existing style exactly (same CSS vars, `.panel`, `.mono`, `.eyebrow`, serif font, etc.) from `audit.html` as a template base.

CSRF: Use hidden form field `<input type="hidden" name="_csrf_token" value="{{ csrf_token_for(request, 'watchlist') }}">` — **not** JS fetch. This matches the actual pattern used in `dashboard.html` for halt/resume/close-only.

Sections:
1. `<nav class="nav-links">` — Dashboard / Metrics / Audit Log / Watchlist (current page, no link)
2. Live-trading warning banner (conditional on `trading_mode == "live"`)
3. Header panel: "Symbol Watchlist" title, trading_mode / strategy_version subtitle
4. Error banner panel (conditional `{% if error %}`):
   - `error == "invalid_symbol"` → "Symbol must be 1–5 uppercase letters (e.g. AAPL)"
   - `error == "last_symbol"` → "Cannot remove the last tracked symbol"
5. Add symbol panel:
   ```html
   <form method="post" action="/admin/watchlist/add">
     <input type="hidden" name="_csrf_token" value="{{ csrf_token_for(request, 'watchlist') }}">
     <input type="text" name="symbol" placeholder="e.g. AAPL" maxlength="5"
            style="text-transform: uppercase;">
     <input type="submit" value="Add Symbol">
   </form>
   ```
6. Current watchlist table panel:
   - Columns: Symbol | Status | Added At | Added By | Remove
   - `{% for r in records %}` — each row shows symbol (bold), enabled/disabled badge, `format_timestamp(r.added_at)`, `r.added_by`
   - Remove cell: a `<form method="post" action="/admin/watchlist/remove">` with hidden `_csrf_token` and `<input type="hidden" name="symbol" value="{{ r.symbol }}">` and a Remove `<input type="submit">` button; if `enabled_count <= 1 and r.enabled`, add `disabled` attribute to the submit button
   - `{% else %}` row: "No symbols tracked."

**Test command:** Run `pytest tests/unit/test_web_app.py -v` then visually confirm with dev server.

---

## Task 9 — Update nav in all templates

**Files:**
- `src/alpaca_bot/web/templates/dashboard.html`
- `src/alpaca_bot/web/templates/audit.html`

Add `<a href="/watchlist">Watchlist</a>` to `<nav class="nav-links">` in each file.

(Check if `metrics.html` exists — if so, update it too.)

**Test command:** `pytest tests/unit/test_web_app.py -v`

---

## Task 10 — Tests

### `tests/unit/test_repositories.py` additions

Use the project's DI pattern with a real in-memory or per-test Postgres connection (match how other repo tests are structured). Tests for `WatchlistStore`:

- `test_watchlist_list_enabled_empty` — fresh table → empty list
- `test_watchlist_add_and_list_enabled` — add two symbols; list_enabled returns both sorted
- `test_watchlist_remove_soft_delete` — add then remove; list_enabled empty; list_all shows disabled row
- `test_watchlist_add_idempotent` — add same symbol twice; list_enabled has exactly one entry
- `test_watchlist_seed_skips_existing` — seed [A,B]; seed [B,C]; list_enabled has [A,B,C]
- `test_watchlist_remove_then_readd` — remove then add re-enables (enabled=TRUE again)

### `tests/unit/test_cycle_engine.py` additions

- `test_evaluate_cycle_uses_symbols_param` — pass `symbols=("AAPL",)` with settings having two symbols; verify signal_evaluator called only for AAPL
- `test_evaluate_cycle_falls_back_to_settings_symbols` — omit `symbols`; engine iterates settings.symbols

### `tests/unit/test_web_app.py` additions

Helper: extend or copy `_make_admin_app` to accept a `watchlist_records` list and inject a fake `watchlist_store_factory` whose `list_all` returns those records and `list_enabled` returns enabled symbols from that list.

Tests:
- `test_watchlist_page_get` — GET /watchlist returns 200 containing symbol names
- `test_watchlist_add_valid_symbol` — POST /admin/watchlist/add with `_csrf_token` adds symbol, redirects to /watchlist
- `test_watchlist_add_invalid_symbol` — symbol="invalid!!" → redirect to /watchlist?error=invalid_symbol
- `test_watchlist_remove_symbol` — POST /admin/watchlist/remove removes symbol, redirects
- `test_watchlist_remove_last_symbol_rejected` — 1 enabled symbol → redirect to /watchlist?error=last_symbol
- `test_watchlist_add_appends_audit_event` — WATCHLIST_ADD event in captured events list
- `test_watchlist_remove_appends_audit_event` — WATCHLIST_REMOVE event in captured events list

**Test command:** `pytest tests/unit/ -v`

---

## Execution order

1 → 2 → 3 → 3b → 4 → 5 → 6 → 7 → 8 → 9 → 10

Tasks 3 and 3b are sequential (3b requires the new `symbols` param in `evaluate_cycle`).  
Tasks 3 and 4 touch different files — can be done in parallel once 3 is done.  
Tasks 7, 8, 9 can be parallelised.

---

## Rollback

```sql
DROP TABLE IF EXISTS symbol_watchlist;
```

Safe: the supervisor seeds from `settings.symbols` on next startup if the table is empty. Code falls back to `settings.symbols` when `symbols=None` is passed to `evaluate_cycle`.
