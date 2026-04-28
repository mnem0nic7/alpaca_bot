# Symbol Ignore — Implementation Plan

**Date:** 2026-04-27
**Spec:** `docs/superpowers/specs/2026-04-27-symbol-ignore.md`
**Test command:** `pytest tests/unit/test_repositories.py tests/unit/test_runtime_supervisor.py tests/unit/test_web_app.py -q`

---

## Task 1: Migration — add `ignored` column to `symbol_watchlist`

**File:** `migrations/010_symbol_watchlist_ignored.sql` (new file)

```sql
ALTER TABLE symbol_watchlist
    ADD COLUMN IF NOT EXISTS ignored BOOLEAN NOT NULL DEFAULT FALSE;
```

This is backward-compatible: all existing rows default to `ignored=FALSE` (no entries skipped), so the system behaves identically until the UI writes the first `ignored=TRUE` row.

Rollback: `ALTER TABLE symbol_watchlist DROP COLUMN IF EXISTS ignored;`

---

## Task 2: Extend `WatchlistRecord` and `WatchlistStore`

**File:** `src/alpaca_bot/storage/repositories.py`

### 2a. WatchlistRecord — add `ignored` field

```python
@dataclass(frozen=True)
class WatchlistRecord:
    symbol: str
    trading_mode: str
    enabled: bool
    ignored: bool          # ← new field (after enabled, before added_at for logical grouping)
    added_at: datetime
    added_by: str
```

### 2b. WatchlistStore — update `list_all`, add 3 new methods

Update the `list_all` SELECT to include `ignored`:

```python
def list_all(self, trading_mode: str) -> list[WatchlistRecord]:
    rows = fetch_all(
        self._connection,
        "SELECT symbol, trading_mode, enabled, ignored, added_at, added_by "
        "FROM symbol_watchlist "
        "WHERE trading_mode = %s "
        "ORDER BY symbol",
        (trading_mode,),
    )
    return [
        WatchlistRecord(
            symbol=row[0],
            trading_mode=row[1],
            enabled=bool(row[2]),
            ignored=bool(row[3]),
            added_at=row[4],
            added_by=row[5],
        )
        for row in rows
    ]
```

Add three new methods after `remove()`:

```python
def list_ignored(self, trading_mode: str) -> list[str]:
    """Returns symbols that are enabled but marked as ignored for new entries."""
    rows = fetch_all(
        self._connection,
        "SELECT symbol FROM symbol_watchlist "
        "WHERE trading_mode = %s AND enabled = TRUE AND ignored = TRUE "
        "ORDER BY symbol",
        (trading_mode,),
    )
    return [row[0] for row in rows]

def ignore(self, symbol: str, trading_mode: str, *, commit: bool = True) -> None:
    """Mark an enabled symbol as ignored for new entries. Idempotent."""
    execute(
        self._connection,
        "UPDATE symbol_watchlist SET ignored = TRUE "
        "WHERE symbol = %s AND trading_mode = %s",
        (symbol, trading_mode),
        commit=commit,
    )

def unignore(self, symbol: str, trading_mode: str, *, commit: bool = True) -> None:
    """Clear the ignore flag; the symbol resumes normal entry evaluation."""
    execute(
        self._connection,
        "UPDATE symbol_watchlist SET ignored = FALSE "
        "WHERE symbol = %s AND trading_mode = %s",
        (symbol, trading_mode),
        commit=commit,
    )
```

`list_enabled()` and `seed()` are unchanged — they do not filter on `ignored`.

---

## Task 3: Supervisor — compute `entry_symbols` from ignored list

**File:** `src/alpaca_bot/runtime/supervisor.py`

Change the watchlist block (currently lines 309–316) to also fetch ignored symbols and compute `entry_symbols`:

**Before:**
```python
watchlist_store = getattr(self.runtime, "watchlist_store", None)
if watchlist_store is not None:
    watchlist_symbols = tuple(watchlist_store.list_enabled(self.settings.trading_mode.value))
    if not watchlist_symbols:
        logger.warning("Symbol watchlist is empty — skipping cycle")
        return
else:
    watchlist_symbols = self.settings.symbols
```

**After:**
```python
watchlist_store = getattr(self.runtime, "watchlist_store", None)
if watchlist_store is not None:
    watchlist_symbols = tuple(watchlist_store.list_enabled(self.settings.trading_mode.value))
    if not watchlist_symbols:
        logger.warning("Symbol watchlist is empty — skipping cycle")
        return
    ignored_set = set(watchlist_store.list_ignored(self.settings.trading_mode.value))
    entry_symbols = tuple(s for s in watchlist_symbols if s not in ignored_set)
else:
    watchlist_symbols = self.settings.symbols
    entry_symbols = watchlist_symbols
```

Then change the `symbols=watchlist_symbols` argument in `self._cycle_runner(...)` (line 395) to `symbols=entry_symbols`:

```python
symbols=entry_symbols,
```

Market data fetches (`get_stock_bars`, `get_daily_bars`) continue to use `list(watchlist_symbols)` — unchanged. Bars are still fetched for ignored symbols so existing positions (stops, exits) continue to work.

---

## Task 4: Audit event types

**File:** `src/alpaca_bot/web/service.py`

Add two entries to `ALL_AUDIT_EVENT_TYPES`:

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
    "WATCHLIST_ADD",
    "WATCHLIST_REMOVE",
    "WATCHLIST_IGNORE",    # ← new
    "WATCHLIST_UNIGNORE",  # ← new
]
```

---

## Task 5: Web routes — ignore and unignore

**File:** `src/alpaca_bot/web/app.py`

### 5a. Update GET /watchlist — no context change needed

`WatchlistRecord.ignored` is now a field on each record. The template can read `rec.ignored` directly. No context key change required; `enabled_count` already counts enabled records, and the template will derive ignore state from `rec.ignored`.

### 5b. Add POST /admin/watchlist/ignore

Insert after `watchlist_remove` and before `return app`:

```python
@app.post("/admin/watchlist/ignore")
async def watchlist_ignore(request: Request) -> Response:
    operator = current_operator(request, settings=app_settings)
    if auth_enabled(app_settings) and operator is None:
        return RedirectResponse(url="/login?next=/watchlist", status_code=status.HTTP_303_SEE_OTHER)
    fields = await _read_form_fields(request)
    token = fields.get("_csrf_token", "")
    if not validate_csrf_token(
        request, token, settings=app_settings, action="watchlist", csrf_secret=csrf_secret
    ):
        return HTMLResponse(status_code=status.HTTP_403_FORBIDDEN, content="Forbidden")
    symbol = fields.get("symbol", "").strip().upper()
    connection = None
    try:
        connection = app.state.connect_postgres(app_settings.database_url)
        store = _build_store(app.state.watchlist_store_factory, connection)
        audit_store = _build_store(app.state.audit_event_store_factory, connection)
        now = datetime.now(timezone.utc)
        store.ignore(symbol, app_settings.trading_mode.value, commit=False)
        audit_store.append(
            AuditEvent(
                event_type="WATCHLIST_IGNORE",
                symbol=symbol,
                payload={"ignored_by": operator or "admin", "trading_mode": app_settings.trading_mode.value},
                created_at=now,
            ),
            commit=False,
        )
        connection.commit()
    finally:
        close = getattr(connection, "close", None)
        if callable(close):
            close()
    return RedirectResponse(url="/watchlist", status_code=status.HTTP_303_SEE_OTHER)
```

### 5c. Add POST /admin/watchlist/unignore

```python
@app.post("/admin/watchlist/unignore")
async def watchlist_unignore(request: Request) -> Response:
    operator = current_operator(request, settings=app_settings)
    if auth_enabled(app_settings) and operator is None:
        return RedirectResponse(url="/login?next=/watchlist", status_code=status.HTTP_303_SEE_OTHER)
    fields = await _read_form_fields(request)
    token = fields.get("_csrf_token", "")
    if not validate_csrf_token(
        request, token, settings=app_settings, action="watchlist", csrf_secret=csrf_secret
    ):
        return HTMLResponse(status_code=status.HTTP_403_FORBIDDEN, content="Forbidden")
    symbol = fields.get("symbol", "").strip().upper()
    connection = None
    try:
        connection = app.state.connect_postgres(app_settings.database_url)
        store = _build_store(app.state.watchlist_store_factory, connection)
        audit_store = _build_store(app.state.audit_event_store_factory, connection)
        now = datetime.now(timezone.utc)
        store.unignore(symbol, app_settings.trading_mode.value, commit=False)
        audit_store.append(
            AuditEvent(
                event_type="WATCHLIST_UNIGNORE",
                symbol=symbol,
                payload={"unignored_by": operator or "admin", "trading_mode": app_settings.trading_mode.value},
                created_at=now,
            ),
            commit=False,
        )
        connection.commit()
    finally:
        close = getattr(connection, "close", None)
        if callable(close):
            close()
    return RedirectResponse(url="/watchlist", status_code=status.HTTP_303_SEE_OTHER)
```

---

## Task 6: Template — watchlist.html

**File:** `src/alpaca_bot/web/templates/watchlist.html`

### 6a. Add "ignored" badge style

In the `<style>` block, add after `.badge-disabled`:

```css
.badge-ignored {
    display: inline-block; padding: 0.15rem 0.55rem;
    background: #fdf3e3; color: #8f6000; border-radius: 999px;
    font-size: 0.8rem; font-weight: 600;
}
```

### 6b. Update table Status cell

Replace the current `{% if rec.enabled %}` block in the Status column with:

```html
<td>
  {% if rec.enabled and rec.ignored %}
    <span class="badge-ignored">ignored</span>
  {% elif rec.enabled %}
    <span class="badge-enabled">enabled</span>
  {% else %}
    <span class="badge-disabled">disabled</span>
  {% endif %}
</td>
```

### 6c. Add Ignore/Unignore column (after Remove column)

Add `<th>Ignore</th>` to the `<thead>` row.

In `<tbody>`, after the Remove `<td>`, add:

```html
<td>
  {% if rec.enabled and not rec.ignored %}
    <form method="post" action="/admin/watchlist/ignore" style="display: inline;">
      <input type="hidden" name="_csrf_token" value="{{ csrf_token_for(request, 'watchlist') }}">
      <input type="hidden" name="symbol" value="{{ rec.symbol }}">
      <button type="submit" class="btn">Ignore</button>
    </form>
  {% elif rec.enabled and rec.ignored %}
    <form method="post" action="/admin/watchlist/unignore" style="display: inline;">
      <input type="hidden" name="_csrf_token" value="{{ csrf_token_for(request, 'watchlist') }}">
      <input type="hidden" name="symbol" value="{{ rec.symbol }}">
      <button type="submit" class="btn">Unignore</button>
    </form>
  {% else %}
    <span class="muted" style="font-size: 0.85rem;">—</span>
  {% endif %}
</td>
```

Also update `colspan="5"` in the empty row to `colspan="6"`.

---

## Task 7: Tests

**Test command:** `pytest tests/unit/test_repositories.py tests/unit/test_runtime_supervisor.py tests/unit/test_web_app.py -q`

### 7a. test_repositories.py — WatchlistStore ignore/unignore

**First, update the existing `test_watchlist_store_list_all_returns_records` test** (around line 329). Its row tuples currently have 5 elements; after adding `ignored` to the SELECT they must have 6. Change:

```python
# Before
rows = [("AAPL", "paper", True, now, "system")]
# After
rows = [("AAPL", "paper", True, False, now, "system")]
```

Also update the assertion to confirm no `ignored` regression: add `assert result[0].ignored is False`.

Append after the existing WatchlistStore tests:

```python
def test_watchlist_store_list_ignored_returns_only_enabled_and_ignored() -> None:
    rows = [("TSLA",)]
    conn = _FetchingConnection(fetchall_result=rows)
    store = WatchlistStore(conn)

    result = store.list_ignored("paper")

    assert result == ["TSLA"]
    sql = conn.execute_calls[0][0]
    assert "enabled = TRUE" in sql
    assert "ignored = TRUE" in sql


def test_watchlist_store_ignore_sets_ignored_true() -> None:
    conn = _TrackingConnection()
    store = WatchlistStore(conn)

    store.ignore("TSLA", "paper", commit=True)

    assert len(conn.execute_calls) == 1
    sql = conn.execute_calls[0][0]
    assert "ignored = TRUE" in sql
    assert conn.execute_calls[0][1] == ("TSLA", "paper")
    assert conn.commit_count == 1


def test_watchlist_store_ignore_commit_false_does_not_commit() -> None:
    conn = _TrackingConnection()
    store = WatchlistStore(conn)

    store.ignore("TSLA", "paper", commit=False)

    assert conn.commit_count == 0


def test_watchlist_store_unignore_sets_ignored_false() -> None:
    conn = _TrackingConnection()
    store = WatchlistStore(conn)

    store.unignore("TSLA", "paper", commit=True)

    assert len(conn.execute_calls) == 1
    sql = conn.execute_calls[0][0]
    assert "ignored = FALSE" in sql
    assert conn.execute_calls[0][1] == ("TSLA", "paper")
    assert conn.commit_count == 1


def test_watchlist_store_list_all_includes_ignored_field() -> None:
    now = datetime(2026, 4, 27, tzinfo=timezone.utc)
    rows = [("AAPL", "paper", True, False, now, "system")]
    conn = _FetchingConnection(fetchall_result=rows)
    store = WatchlistStore(conn)

    result = store.list_all("paper")

    assert len(result) == 1
    assert result[0].ignored is False
```

### 7b. test_runtime_supervisor.py — entry_symbols excludes ignored

Find the file and add a test verifying that when `list_ignored()` returns a symbol, it is excluded from the `symbols=` argument passed to the cycle runner.

Pattern: use the existing `_make_runtime` / fake supervisor patterns in that file. The test should:
1. Create a supervisor with a fake watchlist_store where `list_enabled()` returns `["AAPL", "TSLA"]` and `list_ignored()` returns `["TSLA"]`
2. Capture both the `symbols=` argument passed to the cycle runner AND the symbols passed to the market data fetch (`get_stock_bars` or equivalent)
3. Assert `symbols == ("AAPL",)` (TSLA excluded from entry evaluation)
4. Assert TSLA IS present in the market data fetch symbols (bars still fetched for existing position stop/exit management)

Key lookup: inject via `runtime.watchlist_store = SimpleNamespace(list_enabled=..., list_ignored=...)` after calling `make_runtime_context()`, since `make_runtime_context()` doesn't accept `watchlist_store` as a parameter.

### 7c. test_web_app.py — ignore/unignore web routes

**First, update all existing `SimpleNamespace` watchlist record instances** in `_make_watchlist_app` and any test that constructs records inline to include `ignored=False`. The updated `watchlist.html` template accesses `rec.ignored`, so any `SimpleNamespace` without this attribute will raise `AttributeError` at render time. Specifically:
- In `_make_watchlist_app`, add `ignored=False` to the default record construction
- Any test that passes `watchlist_records=` with explicit `SimpleNamespace(...)` must also include `ignored=False` (or `ignored=True` for the badge test)
- The existing `test_watchlist_page_renders_symbols` test's records need `ignored=False`

Extend `_make_watchlist_app` to capture `ignore`/`unignore` calls:

```python
def _make_watchlist_app(
    *,
    watchlist_records=None,
    enabled_symbols=None,
    ignored_symbols=None,
    saved_adds=None,
    saved_removes=None,
    saved_ignores=None,
    saved_unignores=None,
    saved_events=None,
):
    ...
    ignored = ignored_symbols if ignored_symbols is not None else []
    ignores = saved_ignores if saved_ignores is not None else []
    unignores = saved_unignores if saved_unignores is not None else []

    def watchlist_store_factory(_conn):
        return SimpleNamespace(
            list_all=lambda trading_mode: records,
            list_enabled=lambda trading_mode: list(enabled),
            list_ignored=lambda trading_mode: list(ignored),
            add=...,
            remove=...,
            ignore=lambda symbol, trading_mode, *, commit=True: ignores.append(symbol),
            unignore=lambda symbol, trading_mode, *, commit=True: unignores.append(symbol),
        )
    ...
```

Tests to add:
- `test_watchlist_ignore_valid_symbol_calls_store` — POST to `/admin/watchlist/ignore`, assert 303, store called, audit event appended with `event_type="WATCHLIST_IGNORE"` and `event.symbol == "TSLA"`
- `test_watchlist_ignore_returns_403_for_bad_csrf`
- `test_watchlist_unignore_valid_symbol_calls_store` — same pattern for unignore
- `test_watchlist_unignore_returns_403_for_bad_csrf`
- `test_watchlist_page_renders_ignored_badge` — GET /watchlist with a record where `ignored=True`, assert "ignored" in response text

---

## Execution order

1. Task 1 (migration)
2. Task 2 (WatchlistRecord + WatchlistStore)
3. Task 3 (supervisor entry_symbols)
4. Task 4 (audit event types)
5. Task 5 (web routes)
6. Task 6 (template)
7. Task 7 (tests)

Run `pytest -q` after Task 7 to verify all tests pass.
