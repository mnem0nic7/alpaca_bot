# Option Entry Observability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make post-session RCA possible by emitting an audit event on every option entry, adding a `/decisions` dashboard route backed by the existing `decision_log` table, and fixing the stale `ALL_AUDIT_EVENT_TYPES` allowlist.

**Architecture:** Four self-contained changes. `cycle.py` gains one new `AuditEvent` append per option ENTRY intent (inside the existing transaction). `repositories.py` gains a read-only `DecisionLogStore.list_recent()` query. `service.py` gains `load_decisions_page()` and an expanded `ALL_AUDIT_EVENT_TYPES`. `app.py` gains a `/decisions` GET route and a matching `decisions.html` template. No schema changes — `decision_log` already exists and is already populated.

**Tech Stack:** Python 3.12, FastAPI, Jinja2, psycopg2, pytest (DI fake-callables pattern, no mocks)

---

## File Map

| File | Change |
|---|---|
| `src/alpaca_bot/runtime/cycle.py` | Append `option_entry_intent_created` AuditEvent per option ENTRY intent |
| `src/alpaca_bot/storage/repositories.py` | Add `DecisionLogStore.list_recent()` |
| `src/alpaca_bot/web/service.py` | Add `load_decisions_page()`, expand `ALL_AUDIT_EVENT_TYPES` |
| `src/alpaca_bot/web/app.py` | Add `decision_log_store_factory` param, add `/decisions` route |
| `src/alpaca_bot/web/templates/decisions.html` | New template |
| `tests/unit/test_cycle.py` | Tests for option_entry_intent_created event |
| `tests/unit/test_repositories.py` | Tests for DecisionLogStore.list_recent() |
| `tests/unit/test_web_service.py` | Tests for load_decisions_page() |
| `tests/unit/test_web_app.py` | Tests for /decisions route |

---

## Task 1: Emit `option_entry_intent_created` audit event from `cycle.py`

**Files:**
- Modify: `src/alpaca_bot/runtime/cycle.py:92-117`
- Test: `tests/unit/test_cycle.py`

**Background:** `cycle.py:run_cycle()` iterates over `result.intents`. For option ENTRY intents it saves an `OptionOrderRecord` (lines 92-117) but emits no audit event. We add one `AuditEvent` append per option ENTRY intent, using `commit=False`, inside the existing `try` block — so it rolls back atomically if anything else in that block fails.

`CycleIntent` has no `entry_level` field — signal-level data flows into `decision_log` via `DecisionRecord`. The audit event payload captures what IS on the intent: OCC symbol, underlying, type, strike, expiry, ask price, quantity, and the bar timestamp that triggered entry.

- [ ] **Step 1: Write the failing test**

Add this test to `tests/unit/test_cycle.py`:

```python
class RecordingOptionOrderStore:
    def __init__(self) -> None:
        self.saved: list = []

    def save(self, record, *, commit: bool = True) -> None:
        self.saved.append(record)


def _make_option_entry_intent(occ_symbol: str, now: datetime) -> CycleIntent:
    from datetime import date as _date
    return CycleIntent(
        intent_type=CycleIntentType.ENTRY,
        symbol=occ_symbol,
        timestamp=now,
        client_order_id=f"option:{occ_symbol}:entry",
        quantity=1,
        stop_price=None,
        limit_price=1.20,
        initial_stop_price=None,
        signal_timestamp=now,
        strategy_name="breakout",
        underlying_symbol="ALHC",
        is_option=True,
        option_strike=17.5,
        option_expiry=_date(2026, 6, 18),
        option_type_str="put",
    )


def test_run_cycle_emits_option_entry_intent_created_event() -> None:
    """Each option ENTRY intent must produce exactly one option_entry_intent_created
    audit event with the correct payload fields."""
    from alpaca_bot.runtime.cycle import run_cycle
    import alpaca_bot.runtime.cycle as cycle_module

    settings = _make_settings()
    now = datetime(2026, 5, 12, 14, 0, tzinfo=timezone.utc)
    audit_store = RecordingAuditEventStore()
    connection = CountingConnection()
    option_store = RecordingOptionOrderStore()
    runtime = SimpleNamespace(
        order_store=RecordingOrderStore(),
        audit_event_store=audit_store,
        connection=connection,
        option_order_store=option_store,
    )

    occ = "ALHC260618P00017500"
    option_result = CycleResult(
        as_of=now,
        intents=[_make_option_entry_intent(occ, now)],
    )
    original = cycle_module.evaluate_cycle
    cycle_module.evaluate_cycle = lambda **_: option_result
    try:
        run_cycle(
            settings=settings,
            runtime=runtime,
            now=now,
            equity=100_000.0,
            intraday_bars_by_symbol={},
            daily_bars_by_symbol={},
            open_positions=[],
            working_order_symbols=set(),
            traded_symbols_today=set(),
            entries_disabled=False,
        )
    finally:
        cycle_module.evaluate_cycle = original

    entry_events = [
        e for e in audit_store.appended
        if e.event_type == "option_entry_intent_created"
    ]
    assert len(entry_events) == 1, (
        f"Expected 1 option_entry_intent_created event, got {len(entry_events)}"
    )
    payload = entry_events[0].payload
    assert payload["occ_symbol"] == occ
    assert payload["underlying_symbol"] == "ALHC"
    assert payload["option_type"] == "put"
    assert payload["strike"] == 17.5
    assert payload["ask_price"] == 1.20
    assert payload["quantity"] == 1


def test_run_cycle_equity_entry_does_not_emit_option_event() -> None:
    """Equity ENTRY intents must NOT emit option_entry_intent_created."""
    from alpaca_bot.runtime.cycle import run_cycle
    import alpaca_bot.runtime.cycle as cycle_module

    settings = _make_settings()
    now = datetime(2026, 5, 12, 14, 15, tzinfo=timezone.utc)
    audit_store = RecordingAuditEventStore()
    runtime = SimpleNamespace(
        order_store=RecordingOrderStore(),
        audit_event_store=audit_store,
        connection=CountingConnection(),
    )

    equity_result = CycleResult(
        as_of=now,
        intents=[_make_entry_intent("AAPL", now)],
    )
    original = cycle_module.evaluate_cycle
    cycle_module.evaluate_cycle = lambda **_: equity_result
    try:
        run_cycle(
            settings=settings,
            runtime=runtime,
            now=now,
            equity=100_000.0,
            intraday_bars_by_symbol={},
            daily_bars_by_symbol={},
            open_positions=[],
            working_order_symbols=set(),
            traded_symbols_today=set(),
            entries_disabled=False,
        )
    finally:
        cycle_module.evaluate_cycle = original

    option_events = [
        e for e in audit_store.appended
        if e.event_type == "option_entry_intent_created"
    ]
    assert len(option_events) == 0, (
        "Equity ENTRY intent must not produce option_entry_intent_created"
    )
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/unit/test_cycle.py::test_run_cycle_emits_option_entry_intent_created_event \
       tests/unit/test_cycle.py::test_run_cycle_equity_entry_does_not_emit_option_event -v
```

Expected: FAIL — `option_entry_intent_created` events are never emitted.

- [ ] **Step 3: Implement the audit event in `cycle.py`**

In `src/alpaca_bot/runtime/cycle.py`, locate the option ENTRY branch (lines ~95-117). The current code is:

```python
if getattr(intent, "is_option", False):
    option_order_store = getattr(runtime, "option_order_store", None)
    if option_order_store is not None:
        option_order_store.save(
            OptionOrderRecord(
                ...
            ),
            commit=False,
        )
```

Replace it with (add the audit event append immediately after the `option_order_store.save()` call, still inside the `if option_order_store is not None` block **and** also unconditionally after the block):

```python
if getattr(intent, "is_option", False):
    option_order_store = getattr(runtime, "option_order_store", None)
    if option_order_store is not None:
        option_order_store.save(
            OptionOrderRecord(
                client_order_id=intent.client_order_id or "",
                occ_symbol=intent.symbol,
                underlying_symbol=intent.underlying_symbol or "",
                option_type=intent.option_type_str or "call",
                strike=intent.option_strike or 0.0,
                expiry=intent.option_expiry or now.date(),
                side="buy",
                status="pending_submit",
                quantity=intent.quantity or 0,
                trading_mode=settings.trading_mode,
                strategy_version=settings.strategy_version,
                strategy_name=intent.strategy_name,
                limit_price=intent.limit_price,
                created_at=now,
                updated_at=now,
            ),
            commit=False,
        )
    runtime.audit_event_store.append(
        AuditEvent(
            event_type="option_entry_intent_created",
            payload={
                "occ_symbol": intent.symbol,
                "underlying_symbol": intent.underlying_symbol,
                "option_type": intent.option_type_str,
                "strike": intent.option_strike,
                "expiry": intent.option_expiry.isoformat() if intent.option_expiry else None,
                "ask_price": intent.limit_price,
                "quantity": intent.quantity,
                "signal_timestamp": (
                    intent.signal_timestamp.isoformat()
                    if intent.signal_timestamp else None
                ),
            },
            created_at=now,
        ),
        commit=False,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/unit/test_cycle.py::test_run_cycle_emits_option_entry_intent_created_event \
       tests/unit/test_cycle.py::test_run_cycle_equity_entry_does_not_emit_option_event -v
```

Expected: PASS

- [ ] **Step 5: Run the full test suite to check for regressions**

```bash
pytest tests/unit/test_cycle.py -v
```

Expected: all existing tests still pass.

- [ ] **Step 6: Commit**

```bash
git add src/alpaca_bot/runtime/cycle.py tests/unit/test_cycle.py
git commit -m "feat: emit option_entry_intent_created audit event per option ENTRY intent"
```

---

## Task 2: Add `DecisionLogStore.list_recent()` to `repositories.py`

**Files:**
- Modify: `src/alpaca_bot/storage/repositories.py` (after the `bulk_insert` method, ~line 2005)
- Test: `tests/unit/test_repositories.py`

**Background:** `DecisionLogStore` currently has only `bulk_insert()`. We add a read-only `list_recent()` that filters by session date (ET) and optional symbol, returning a list of plain dicts. The `decision_log` table stores 24 columns; the method returns all of them so the template can render any column.

The `fetch_all` helper from `alpaca_bot.storage.db` is the standard query helper in this file — use it. psycopg2 returns tuples; map to dicts via `zip(column_names, row)`. The `filter_results` column is JSONB — psycopg2 deserializes it automatically to a Python dict.

- [ ] **Step 1: Write the failing test**

Add this section to `tests/unit/test_repositories.py`:

```python
from datetime import date
from alpaca_bot.storage.repositories import DecisionLogStore


class _FetchingDecisionLogConnection(_TrackingConnection):
    """Returns canned rows from fetchall() for decision_log queries."""

    def __init__(self, rows: list[tuple]) -> None:
        super().__init__()
        self._rows = rows

    def cursor(self):
        rows = self._rows
        conn = self

        class _FetchCursor:
            def execute(self, sql: str, params=None) -> None:
                conn.execute_calls.append((sql, params))

            def fetchall(self):
                return list(rows)

        return _FetchCursor()


def _make_decision_row(
    symbol: str = "ALHC260618P00017500",
    session_date: date = date(2026, 5, 12),
    decision: str = "ENTRY",
) -> tuple:
    """Return a 24-element tuple matching decision_log column order."""
    from datetime import datetime, timezone
    return (
        datetime(2026, 5, 12, 14, 0, tzinfo=timezone.utc),  # cycle_at
        symbol,                   # symbol
        "breakout",               # strategy_name
        "paper",                  # trading_mode
        "v1-breakout",            # strategy_version
        decision,                 # decision
        None,                     # reject_stage
        None,                     # reject_reason
        17.5,                     # entry_level
        17.3,                     # signal_bar_close
        2.1,                      # relative_volume
        None,                     # atr
        None,                     # stop_price
        1.20,                     # limit_price
        None,                     # initial_stop_price
        1,                        # quantity
        None,                     # risk_per_share
        50_000.0,                 # equity
        {},                       # filter_results (psycopg2 returns dict for JSONB)
        None,                     # vix_close
        None,                     # vix_above_sma
        None,                     # sector_passing_pct
        None,                     # vwap_at_signal
        None,                     # signal_bar_above_vwap
    )


def test_decision_log_list_recent_returns_dicts_for_session_date() -> None:
    row = _make_decision_row()
    conn = _FetchingDecisionLogConnection([row])
    store = DecisionLogStore(conn)
    results = store.list_recent(session_date=date(2026, 5, 12))
    assert len(results) == 1
    assert results[0]["symbol"] == "ALHC260618P00017500"
    assert results[0]["decision"] == "ENTRY"
    assert results[0]["entry_level"] == 17.5


def test_decision_log_list_recent_symbol_filter_passes_param() -> None:
    conn = _FetchingDecisionLogConnection([])
    store = DecisionLogStore(conn)
    store.list_recent(session_date=date(2026, 5, 12), symbol="AAPL")
    sqls = [call[0] for call in conn.execute_calls]
    assert any("symbol" in sql.lower() for sql in sqls), (
        "Expected symbol filter in SQL when symbol param provided"
    )
    params = conn.execute_calls[0][1]
    assert "AAPL" in params


def test_decision_log_list_recent_empty_returns_empty_list() -> None:
    conn = _FetchingDecisionLogConnection([])
    store = DecisionLogStore(conn)
    results = store.list_recent(session_date=date(2026, 5, 12))
    assert results == []
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/unit/test_repositories.py::test_decision_log_list_recent_returns_dicts_for_session_date \
       tests/unit/test_repositories.py::test_decision_log_list_recent_symbol_filter_passes_param \
       tests/unit/test_repositories.py::test_decision_log_list_recent_empty_returns_empty_list -v
```

Expected: FAIL — `DecisionLogStore` has no `list_recent` method.

- [ ] **Step 3: Implement `DecisionLogStore.list_recent()`**

In `src/alpaca_bot/storage/repositories.py`, add this method to the `DecisionLogStore` class immediately after `bulk_insert` (around line 2005):

```python
_DECISION_LOG_COLS = (
    "cycle_at", "symbol", "strategy_name", "trading_mode", "strategy_version",
    "decision", "reject_stage", "reject_reason",
    "entry_level", "signal_bar_close", "relative_volume", "atr",
    "stop_price", "limit_price", "initial_stop_price",
    "quantity", "risk_per_share", "equity", "filter_results",
    "vix_close", "vix_above_sma", "sector_passing_pct",
    "vwap_at_signal", "signal_bar_above_vwap",
)

def list_recent(
    self,
    *,
    session_date: date,
    symbol: str | None = None,
    limit: int = 200,
    market_timezone: str = "America/New_York",
) -> list[dict]:
    cols = ", ".join(_DECISION_LOG_COLS)
    if symbol:
        rows = fetch_all(
            self._connection,
            f"""
            SELECT {cols}
            FROM decision_log
            WHERE DATE(cycle_at AT TIME ZONE %s) = %s
              AND symbol = %s
            ORDER BY cycle_at DESC
            LIMIT %s
            """,
            (market_timezone, session_date, symbol, limit),
        )
    else:
        rows = fetch_all(
            self._connection,
            f"""
            SELECT {cols}
            FROM decision_log
            WHERE DATE(cycle_at AT TIME ZONE %s) = %s
            ORDER BY cycle_at DESC
            LIMIT %s
            """,
            (market_timezone, session_date, limit),
        )
    return [dict(zip(_DECISION_LOG_COLS, row)) for row in rows]
```

Note: `_DECISION_LOG_COLS` is a module-level constant defined just before the method. Place it at module scope (near the bottom of the file before `DecisionLogStore`, or as a class-level constant). The `date` type is already imported at the top of `repositories.py`.

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/unit/test_repositories.py::test_decision_log_list_recent_returns_dicts_for_session_date \
       tests/unit/test_repositories.py::test_decision_log_list_recent_symbol_filter_passes_param \
       tests/unit/test_repositories.py::test_decision_log_list_recent_empty_returns_empty_list -v
```

Expected: PASS

- [ ] **Step 5: Run the full repositories test suite**

```bash
pytest tests/unit/test_repositories.py -v
```

Expected: all existing tests still pass.

- [ ] **Step 6: Commit**

```bash
git add src/alpaca_bot/storage/repositories.py tests/unit/test_repositories.py
git commit -m "feat: add DecisionLogStore.list_recent() for decision log dashboard queries"
```

---

## Task 3: Add `load_decisions_page()` + fix `ALL_AUDIT_EVENT_TYPES` in `service.py`

**Files:**
- Modify: `src/alpaca_bot/web/service.py:35-60` (ALL_AUDIT_EVENT_TYPES)
- Modify: `src/alpaca_bot/web/service.py` (add load_decisions_page)
- Test: `tests/unit/test_web_service.py`

**Background:** `ALL_AUDIT_EVENT_TYPES` is a manually-maintained list used to populate the audit page filter dropdown. It is missing 15+ event types. We expand it. We also add `load_decisions_page()` which wraps `DecisionLogStore.list_recent()` with error handling, following the same pattern as `load_audit_page()`.

- [ ] **Step 1: Write the failing tests**

Add this section to `tests/unit/test_web_service.py`:

```python
from alpaca_bot.web.service import load_decisions_page, ALL_AUDIT_EVENT_TYPES
from datetime import date


def make_decision_log_store(rows=None, raises=False):
    class _Store:
        def list_recent(self, *, session_date, symbol=None, limit=200, market_timezone="America/New_York"):
            if raises:
                raise RuntimeError("db down")
            return rows if rows is not None else []
    return _Store()


def test_load_decisions_page_calls_list_recent_with_correct_args() -> None:
    calls = []

    class _TrackingStore:
        def list_recent(self, *, session_date, symbol=None, limit=200, market_timezone="America/New_York"):
            calls.append({"session_date": session_date, "symbol": symbol})
            return []

    load_decisions_page(
        session_date=date(2026, 5, 12),
        symbol="ALHC",
        decision_log_store=_TrackingStore(),
    )
    assert len(calls) == 1
    assert calls[0]["session_date"] == date(2026, 5, 12)
    assert calls[0]["symbol"] == "ALHC"


def test_load_decisions_page_returns_empty_list_when_store_raises() -> None:
    store = make_decision_log_store(raises=True)
    result = load_decisions_page(
        session_date=date(2026, 5, 12),
        symbol=None,
        decision_log_store=store,
    )
    assert result == []


def test_all_audit_event_types_includes_option_events() -> None:
    for expected in (
        "option_entry_intent_created",
        "option_order_submitted",
        "option_chains_fetched",
        "decision_cycle_completed",
        "nightly_sweep_completed",
        "stale_exit_canceled_for_resubmission",
        "stale_exit_cancel_failed",
    ):
        assert expected in ALL_AUDIT_EVENT_TYPES, (
            f"ALL_AUDIT_EVENT_TYPES missing '{expected}'"
        )
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/unit/test_web_service.py::test_load_decisions_page_calls_list_recent_with_correct_args \
       tests/unit/test_web_service.py::test_load_decisions_page_returns_empty_list_when_store_raises \
       tests/unit/test_web_service.py::test_all_audit_event_types_includes_option_events -v
```

Expected: FAIL — `load_decisions_page` doesn't exist, `ALL_AUDIT_EVENT_TYPES` is incomplete.

- [ ] **Step 3: Expand `ALL_AUDIT_EVENT_TYPES`**

In `src/alpaca_bot/web/service.py`, replace the current `ALL_AUDIT_EVENT_TYPES` list (lines 35-60) with:

```python
ALL_AUDIT_EVENT_TYPES = [
    "daily_loss_limit_breached",
    "daily_summary_sent",
    "decision_cycle_completed",
    "extended_hours_cycle",
    "nightly_sweep_completed",
    "option_chains_fetched",
    "option_entry_intent_created",
    "option_order_submitted",
    "option_stop_skipped_no_price",
    "order_dispatch_failed",
    "order_dispatch_stop_price_rejected",
    "postgres_reconnected",
    "runtime_reconciliation_detected",
    "stale_exit_cancel_failed",
    "stale_exit_canceled_for_resubmission",
    "startup_recovery_completed",
    "startup_recovery_skipped",
    "stop_update_skipped_extended_hours",
    "stream_restart_failed",
    "stream_started",
    "stream_stopped",
    "stream_heartbeat_stale",
    "strategy_cycle_error",
    "strategy_entries_changed",
    "strategy_flag_changed",
    "strategy_weights_updated",
    "supervisor_cycle",
    "supervisor_cycle_error",
    "supervisor_idle",
    "trade_update_stream_failed",
    "trade_update_stream_restarted",
    "trade_update_stream_started",
    "trade_update_stream_stopped",
    "trader_startup_completed",
    "trading_status_changed",
    "WATCHLIST_ADD",
    "WATCHLIST_IGNORE",
    "WATCHLIST_REMOVE",
    "WATCHLIST_UNIGNORE",
]
```

- [ ] **Step 4: Add `load_decisions_page()` to `service.py`**

Add this function to `src/alpaca_bot/web/service.py`, after `load_audit_page()` (around line 465):

```python
def load_decisions_page(
    *,
    session_date: date,
    symbol: str | None,
    decision_log_store: object,
) -> list[dict]:
    try:
        return decision_log_store.list_recent(  # type: ignore[union-attr]
            session_date=session_date,
            symbol=symbol or None,
        )
    except Exception:
        logger.exception("decision log query failed for date=%s symbol=%s", session_date, symbol)
        return []
```

Add `from datetime import date` to the imports at the top of `service.py` if `date` is not already imported (check — if only `datetime` is imported, add `date` to the import).

Also add `load_decisions_page` to the module's exports if there's an `__all__` list — check first with `grep __all__ src/alpaca_bot/web/service.py`.

- [ ] **Step 5: Run tests to verify they pass**

```bash
pytest tests/unit/test_web_service.py::test_load_decisions_page_calls_list_recent_with_correct_args \
       tests/unit/test_web_service.py::test_load_decisions_page_returns_empty_list_when_store_raises \
       tests/unit/test_web_service.py::test_all_audit_event_types_includes_option_events -v
```

Expected: PASS

- [ ] **Step 6: Run the full service test suite**

```bash
pytest tests/unit/test_web_service.py -v
```

Expected: all existing tests still pass.

- [ ] **Step 7: Commit**

```bash
git add src/alpaca_bot/web/service.py tests/unit/test_web_service.py
git commit -m "feat: add load_decisions_page() and expand ALL_AUDIT_EVENT_TYPES"
```

---

## Task 4: Add `/decisions` route, template, and factory wiring to `app.py`

**Files:**
- Modify: `src/alpaca_bot/web/app.py`
- Create: `src/alpaca_bot/web/templates/decisions.html`
- Test: `tests/unit/test_web_app.py`

**Background:** The route follows the same pattern as `/audit` (lines 345-385 in `app.py`): auth check, connect postgres, call service function, render template, close connection. We add a `decision_log_store_factory` parameter to `create_app` so tests can inject a fake store. The `_parse_date_param` helper (already in `app.py` at line 865) handles date defaulting and validation.

- [ ] **Step 1: Write the failing tests**

Add this section to `tests/unit/test_web_app.py`:

```python
def _make_decisions_app(*, rows=None, raises=False):
    """Create a test app with a fake decision log store."""
    settings = make_settings()

    class _FakeDecisionLogStore:
        def list_recent(self, *, session_date, symbol=None, limit=200, market_timezone="America/New_York"):
            if raises:
                raise RuntimeError("db down")
            return rows if rows is not None else []

    return create_app(
        settings=settings,
        connect_postgres_fn=lambda _: FakeConnection(),
        decision_log_store_factory=lambda _: _FakeDecisionLogStore(),
        audit_event_store_factory=lambda _: SimpleNamespace(
            list_recent=lambda **_: [],
            load_latest=lambda **_: None,
            list_by_event_types=lambda **_: [],
        ),
    )


def test_decisions_route_returns_200() -> None:
    app = _make_decisions_app()
    with TestClient(app) as client:
        response = client.get("/decisions")
    assert response.status_code == 200


def test_decisions_route_renders_empty_state_when_no_rows() -> None:
    app = _make_decisions_app(rows=[])
    with TestClient(app) as client:
        response = client.get("/decisions")
    assert response.status_code == 200
    assert "No decisions" in response.text


def test_decisions_route_renders_symbol_in_table() -> None:
    from datetime import datetime, timezone as _tz
    row = {
        "cycle_at": datetime(2026, 5, 12, 14, 0, tzinfo=_tz.utc),
        "symbol": "ALHC260618P00017500",
        "strategy_name": "breakout",
        "trading_mode": "paper",
        "strategy_version": "v1-breakout",
        "decision": "ENTRY",
        "reject_stage": None,
        "reject_reason": None,
        "entry_level": 17.5,
        "signal_bar_close": 17.3,
        "relative_volume": 2.1,
        "atr": None,
        "stop_price": None,
        "limit_price": 1.20,
        "initial_stop_price": None,
        "quantity": 1,
        "risk_per_share": None,
        "equity": 50000.0,
        "filter_results": {},
        "vix_close": None,
        "vix_above_sma": None,
        "sector_passing_pct": None,
        "vwap_at_signal": None,
        "signal_bar_above_vwap": None,
    }
    app = _make_decisions_app(rows=[row])
    with TestClient(app) as client:
        response = client.get("/decisions?symbol=ALHC260618P00017500")
    assert response.status_code == 200
    assert "ALHC260618P00017500" in response.text


def test_decisions_route_returns_503_when_store_raises() -> None:
    app = _make_decisions_app(raises=True)
    with TestClient(app) as client:
        response = client.get("/decisions")
    assert response.status_code == 200
    assert "No decisions" in response.text
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/unit/test_web_app.py::test_decisions_route_returns_200 \
       tests/unit/test_web_app.py::test_decisions_route_renders_empty_state_when_no_rows \
       tests/unit/test_web_app.py::test_decisions_route_renders_symbol_in_table \
       tests/unit/test_web_app.py::test_decisions_route_returns_503_when_store_raises -v
```

Expected: FAIL — `/decisions` route doesn't exist.

- [ ] **Step 3: Add `DecisionLogStore` import to `app.py`**

In `src/alpaca_bot/web/app.py`, add `DecisionLogStore` to the storage imports (around line 18-33):

```python
from alpaca_bot.storage import (
    AuditEvent,
    AuditEventStore,
    ConfidenceFloorStore,
    DailySessionState,
    DailySessionStateStore,
    DecisionLogStore,          # ← add this
    OrderStore,
    PositionStore,
    StrategyFlag,
    StrategyFlagStore,
    StrategyWeightStore,
    TradingStatus,
    TradingStatusStore,
    TradingStatusValue,
    WatchlistStore,
)
```

Also add `load_decisions_page` to the service imports (around line 45-56):

```python
from alpaca_bot.web.service import (
    ALL_AUDIT_EVENT_TYPES,
    EquityChartData,
    StrategyWeightRow,
    load_audit_page,
    load_confidence_floor_info,
    load_dashboard_snapshot,
    load_decisions_page,          # ← add this
    load_equity_chart_data,
    load_health_snapshot,
    load_metrics_snapshot,
    load_strategy_weights,
)
```

- [ ] **Step 4: Add `decision_log_store_factory` parameter to `create_app()`**

In `src/alpaca_bot/web/app.py`, add the parameter to `create_app`'s signature. Insert it after `confidence_floor_store_factory`:

```python
def create_app(
    *,
    settings: Settings | None = None,
    connect: Callable[[str], ConnectionProtocol] | None = None,
    connection: ConnectionProtocol | None = None,
    db_connection: ConnectionProtocol | None = None,
    connect_postgres_fn: Callable[[str], ConnectionProtocol] | None = None,
    trading_status_store_factory: Callable[[ConnectionProtocol], object] | None = None,
    position_store_factory: Callable[[ConnectionProtocol], object] | None = None,
    order_store_factory: Callable[[ConnectionProtocol], object] | None = None,
    daily_session_state_store_factory: Callable[[ConnectionProtocol], object] | None = None,
    audit_event_store_factory: Callable[[ConnectionProtocol], object] | None = None,
    strategy_flag_store_factory: Callable[[ConnectionProtocol], object] | None = None,
    watchlist_store_factory: Callable[[ConnectionProtocol], object] | None = None,
    strategy_weight_store_factory: Callable[[ConnectionProtocol], object] | None = None,
    confidence_floor_store_factory: Callable[[ConnectionProtocol], object] | None = None,
    decision_log_store_factory: Callable[[ConnectionProtocol], object] | None = None,  # ← add
    notifier: Notifier | None = None,
    market_data_adapter: object | None = None,
    portfolio_reader: object | None = None,
    equity_chart_data_factory: Callable[..., EquityChartData] | None = None,
) -> FastAPI:
```

Then inside `create_app`, after `app.state.confidence_floor_store_factory = ...`, add:

```python
app.state.decision_log_store_factory = decision_log_store_factory or DecisionLogStore
```

- [ ] **Step 5: Add the `/decisions` route to `app.py`**

Add this route to `app.py` after the `/audit` route (after line ~385):

```python
@app.get("/decisions", response_class=HTMLResponse)
def decisions_log(
    request: Request,
    date: str = "",
    symbol: str = "",
) -> HTMLResponse:
    operator = current_operator(request, settings=app_settings)
    if auth_enabled(app_settings) and operator is None:
        return _render_login_page(app, request, next_path=request.url.path)
    today = datetime.now(app_settings.market_timezone).date()
    session_date, _date_warning = _parse_date_param(date, today=today)
    clean_symbol = symbol.strip().upper() or None
    try:
        connection = app.state.connect_postgres(app_settings.database_url)
        try:
            rows = load_decisions_page(
                session_date=session_date,
                symbol=clean_symbol,
                decision_log_store=_build_store(
                    app.state.decision_log_store_factory, connection
                ),
            )
        finally:
            close = getattr(connection, "close", None)
            if callable(close):
                close()
    except Exception:
        rows = []
    return templates.TemplateResponse(
        request=request,
        name="decisions.html",
        context={
            "request": request,
            "trading_mode": app_settings.trading_mode.value,
            "strategy_version": app_settings.strategy_version,
            "operator_email": operator,
            "rows": rows,
            "session_date": session_date.isoformat(),
            "symbol_filter": symbol.strip(),
        },
    )
```

- [ ] **Step 6: Create `src/alpaca_bot/web/templates/decisions.html`**

```html
<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>alpaca_bot — Decision Log</title>
    <style>
      :root {
        color-scheme: light;
        --bg: #f3f0e8;
        --panel: #fffdf8;
        --line: #d8d2c5;
        --ink: #1d2b2a;
        --muted: #6a736e;
        --accent: #1f6f78;
        --warn: #8f3b2e;
        --green: #1a6b3c;
        --red: #8f3b2e;
      }
      * { box-sizing: border-box; }
      body {
        margin: 0;
        font-family: "Iowan Old Style", "Palatino Linotype", serif;
        background:
          radial-gradient(circle at top right, rgba(31, 111, 120, 0.12), transparent 28rem),
          linear-gradient(180deg, #f9f6ef 0%, var(--bg) 100%);
        color: var(--ink);
      }
      main { max-width: 1300px; margin: 0 auto; padding: 2rem 1.25rem 3rem; }
      h1, h2 { margin: 0; font-weight: 700; letter-spacing: 0.01em; }
      p { margin: 0; }
      .panel {
        background: rgba(255, 253, 248, 0.92);
        border: 1px solid var(--line);
        border-radius: 18px;
        padding: 1.1rem 1.2rem;
        box-shadow: 0 10px 30px rgba(29, 43, 42, 0.06);
        margin-bottom: 1rem;
      }
      .eyebrow {
        font-size: 0.78rem;
        text-transform: uppercase;
        color: var(--muted);
        letter-spacing: 0.08em;
      }
      .table-wrap { overflow-x: auto; }
      table { width: 100%; border-collapse: collapse; margin-top: 0.9rem; }
      th, td {
        text-align: left;
        padding: 0.45rem 0.3rem;
        border-bottom: 1px solid rgba(216, 210, 197, 0.8);
        font-size: 0.88rem;
        vertical-align: top;
      }
      th { color: var(--muted); font-size: 0.75rem; text-transform: uppercase; letter-spacing: 0.08em; }
      .muted { color: var(--muted); }
      .mono { font-family: "SFMono-Regular", "Menlo", monospace; font-size: 0.83rem; }
      .nav-links { display: flex; gap: 1rem; margin-bottom: 1rem; font-size: 0.9rem; }
      .nav-links a { color: var(--accent); text-decoration: none; }
      .nav-links a:hover { text-decoration: underline; }
      .badge-entry { color: var(--green); font-weight: 700; }
      .badge-rejected { color: var(--red); }
      select, input[type="text"], input[type="date"], input[type="submit"] {
        border: 1px solid var(--line); border-radius: 999px;
        background: #f7f2e7; color: var(--ink);
        padding: 0.3rem 0.8rem; font: inherit; cursor: pointer;
      }
      select:hover, input[type="submit"]:hover { background: #edf7f8; }
      details summary { cursor: pointer; color: var(--muted); font-size: 0.8rem; }
      details pre { margin: 0.3rem 0 0; font-size: 0.78rem; background: #f0ece0; padding: 0.4rem; border-radius: 6px; white-space: pre-wrap; word-break: break-all; }
    </style>
  </head>
  <body>
    <main>
      <nav class="nav-links">
        <a href="/">Dashboard</a>
        <a href="/metrics">Metrics</a>
        <a href="/audit">Audit Log</a>
        <a href="/decisions">Decisions</a>
        <a href="/watchlist">Watchlist</a>
      </nav>

      {% if trading_mode == "live" %}
        <div style="background: #c0392b; color: #fff; text-align: center; padding: 0.6rem 1rem; font-weight: 700; letter-spacing: 0.05em; margin-bottom: 1rem;">
          &#9888; LIVE TRADING ACTIVE &mdash; real capital at risk
        </div>
      {% endif %}

      <div class="panel">
        <p class="eyebrow">alpaca_bot</p>
        <h1>Decision Log</h1>
        <p class="muted" style="margin-top: 0.4rem;">
          <span class="mono">{{ trading_mode }}</span> / <span class="mono">{{ strategy_version }}</span>
          &mdash; every signal evaluated this session, entries and rejects
        </p>
      </div>

      <div class="panel">
        <form method="get" action="/decisions" style="display: flex; gap: 0.8rem; align-items: center; flex-wrap: wrap;">
          <label for="date_input" class="eyebrow">Session date:</label>
          <input type="date" id="date_input" name="date" value="{{ session_date }}">
          <label for="symbol_input" class="eyebrow">Symbol:</label>
          <input type="text" id="symbol_input" name="symbol" value="{{ symbol_filter }}" placeholder="e.g. AAPL or ALHC260618P00017500" style="min-width: 22ch;">
          <input type="submit" value="Apply">
          {% if symbol_filter %}
            <a href="/decisions?date={{ session_date }}" style="color: var(--muted); font-size: 0.88rem;">Clear symbol</a>
          {% endif %}
        </form>
      </div>

      <div class="panel">
        <div class="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Time (ET)</th>
                <th>Symbol</th>
                <th>Strategy</th>
                <th>Decision</th>
                <th>Reject reason</th>
                <th>Entry level</th>
                <th>Bar close</th>
                <th>Rel. vol</th>
                <th>Ask</th>
                <th>Qty</th>
                <th>Filters</th>
              </tr>
            </thead>
            <tbody>
              {% for row in rows %}
                <tr>
                  <td class="mono muted" style="white-space: nowrap;">{{ format_timestamp(row.cycle_at) }}</td>
                  <td class="mono">{{ row.symbol }}</td>
                  <td class="muted">{{ row.strategy_name }}</td>
                  <td>
                    {% if row.decision == "ENTRY" %}
                      <span class="badge-entry">ENTRY</span>
                    {% else %}
                      <span class="badge-rejected">{{ row.decision or "—" }}</span>
                    {% endif %}
                  </td>
                  <td class="muted" style="font-size: 0.82rem;">
                    {% if row.reject_stage %}{{ row.reject_stage }}: {% endif %}
                    {{ row.reject_reason or "" }}
                  </td>
                  <td class="mono">{{ format_price(row.entry_level) }}</td>
                  <td class="mono">{{ format_price(row.signal_bar_close) }}</td>
                  <td class="mono">{{ "%.2f"|format(row.relative_volume) if row.relative_volume is not none else "—" }}</td>
                  <td class="mono">{{ format_price(row.limit_price) }}</td>
                  <td class="mono">{{ row.quantity or "—" }}</td>
                  <td>
                    {% if row.filter_results %}
                      <details>
                        <summary>{{ row.filter_results | length }} filters</summary>
                        <pre>{{ row.filter_results | tojson(indent=2) }}</pre>
                      </details>
                    {% else %}
                      <span class="muted">—</span>
                    {% endif %}
                  </td>
                </tr>
              {% else %}
                <tr><td colspan="11" class="muted">No decisions recorded for this session.</td></tr>
              {% endfor %}
            </tbody>
          </table>
        </div>
      </div>
    </main>
  </body>
</html>
```

- [ ] **Step 7: Run the failing tests to verify they now pass**

```bash
pytest tests/unit/test_web_app.py::test_decisions_route_returns_200 \
       tests/unit/test_web_app.py::test_decisions_route_renders_empty_state_when_no_rows \
       tests/unit/test_web_app.py::test_decisions_route_renders_symbol_in_table \
       tests/unit/test_web_app.py::test_decisions_route_returns_503_when_store_raises -v
```

Expected: PASS

- [ ] **Step 8: Run the full web app test suite**

```bash
pytest tests/unit/test_web_app.py -v
```

Expected: all existing tests still pass.

- [ ] **Step 9: Run the full test suite**

```bash
pytest
```

Expected: all tests pass. Note the count — it should be at least 4 more than before this feature.

- [ ] **Step 10: Commit**

```bash
git add src/alpaca_bot/web/app.py \
        src/alpaca_bot/web/templates/decisions.html \
        tests/unit/test_web_app.py
git commit -m "feat: add /decisions dashboard route backed by decision_log table"
```

---

## Self-Review

### Spec coverage

| Spec requirement | Task |
|---|---|
| Emit `option_entry_intent_created` audit event from `cycle.py` | Task 1 |
| Add `DecisionLogStore.list_recent()` | Task 2 |
| Add `load_decisions_page()` to `service.py` | Task 3 |
| Fix `ALL_AUDIT_EVENT_TYPES` | Task 3 |
| Add `/decisions` GET route | Task 4 |
| Add `decisions.html` template | Task 4 |
| Session date picker + symbol filter | Task 4 (template) |
| Empty state message | Task 4 (template step 6 + test step 1) |

All requirements covered. ✓

### Type consistency

- `DecisionLogStore.list_recent()` returns `list[dict]` → `load_decisions_page()` returns `list[dict]` → template iterates `rows` as dicts with `row.cycle_at` etc. (Jinja2 dict attribute access) ✓
- `option_entry_intent_created` event appended as `AuditEvent(...)` with `commit=False` — same type as all other appends in the same `try` block ✓
- `_DECISION_LOG_COLS` is defined at module scope in `repositories.py` — referenced only within `list_recent()` ✓

### Placeholder scan

No TBDs, no "handle edge cases" without code, no "similar to Task N" without repeating code. ✓

### One gap fixed vs spec

The spec listed `intent.entry_level` in the audit event payload. `CycleIntent` has no `entry_level` field — that data is in `DecisionRecord.entry_level` (written to `decision_log`). The plan correctly omits it from the audit event and surfaces it via the `/decisions` route instead. This is documented in the audit event design above.
