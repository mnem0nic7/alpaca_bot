# Strategy Evaluation Integrity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement S1–S4 from `docs/superpowers/specs/2026-06-10-strategy-results-critical-review-design.md`: stop the capacity-reject decision_log flood, make P&L attribution trustworthy, add a max-age escape to the confidence floor, and add decision_log retention.

**Architecture:** Four independent changes. S1 touches the pure engine (`evaluate_cycle`) plus the funnel SQL that consumes its records. S2 is SQL-only (correlated entry subqueries gain a same-session-date predicate). S3 adds a nullable timestamp column + Settings field + supervisor clock logic. S4 adds a store method, an admin subcommand, and a nightly hook. No change alters order submission, sizing, or stop placement.

**Tech Stack:** Python 3.11, psycopg (raw SQL repositories), argparse CLIs, pytest with DI fakes (no mocks).

**Scope decision recorded during planning:** Spec S2 names `list_trade_pnl_by_strategy`, but its stated beneficiaries (weights, losing-streak, weekly review, session eval) read through three methods. This plan fixes all three: `list_trade_pnl_by_strategy` (weights + streaks), `list_closed_trade_records` (weekly review, strategy report), `list_closed_trades` (session eval, nightly rolling report). The remaining contaminated methods (`list_trade_exits_in_range`, `win_loss_counts_by_strategy`, `lifetime_pnl_by_strategy`, `daily_realized_pnl`) are follow-ups, noted but not in scope.

---

### Task 1: S1 — One aggregate capacity DecisionRecord per strategy per cycle

**Files:**
- Modify: `src/alpaca_bot/domain/decision_record.py` (add sentinel constant)
- Modify: `src/alpaca_bot/core/engine.py:635-662` (replace per-symbol loop)
- Test: `tests/unit/test_decision_log.py`

- [ ] **Step 1: Write the failing test**

In `tests/unit/test_decision_log.py`, replace the body of `test_evaluate_cycle_capacity_full_emits_capacity_rejected` assertions (keep the setup identical) — change the final two lines:

```python
    capacity_recs = [r for r in result.decision_records if r.reject_stage == "capacity"]
    assert len(capacity_recs) >= 1
```

to:

```python
    capacity_recs = [r for r in result.decision_records if r.reject_stage == "capacity"]
    assert len(capacity_recs) == 1
    rec = capacity_recs[0]
    assert rec.symbol == "_capacity_"
    assert rec.reject_reason == "capacity_full"
    assert rec.filter_results == {"blocked_symbol_count": 2}
```

And add a new test directly below it:

```python
def test_capacity_aggregate_excludes_held_and_working_symbols() -> None:
    """Symbols already held or working are not counted as capacity-blocked."""
    settings = make_settings(SYMBOLS="AAPL,MSFT,GOOGL", MAX_OPEN_POSITIONS="1")
    now = datetime(2026, 5, 7, 14, 30, tzinfo=timezone.utc)
    pos = OpenPosition(
        symbol="AAPL",
        quantity=5,
        entry_price=200.0,
        entry_level=198.0,
        initial_stop_price=195.0,
        stop_price=195.0,
        entry_timestamp=now,
    )
    result = evaluate_cycle(
        settings=settings,
        now=now,
        equity=100_000.0,
        intraday_bars_by_symbol={"MSFT": [make_intraday_bar("MSFT")]},
        daily_bars_by_symbol={"MSFT": make_daily_bars("MSFT")},
        open_positions=[pos],
        working_order_symbols={"GOOGL"},
        traded_symbols_today=set(),
        entries_disabled=False,
        signal_evaluator=lambda **kw: None,
        global_open_count=2,
    )
    capacity_recs = [r for r in result.decision_records if r.reject_stage == "capacity"]
    assert len(capacity_recs) == 1
    assert capacity_recs[0].filter_results == {"blocked_symbol_count": 1}
```

(`make_settings`, `make_intraday_bar`, `make_daily_bars`, `OpenPosition`, `evaluate_cycle` are already imported in this file.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_decision_log.py -v -k capacity`
Expected: both FAIL — the current code emits one record per symbol with the real symbol names.

- [ ] **Step 3: Add the sentinel constant**

In `src/alpaca_bot/domain/decision_record.py`, add above the `DecisionRecord` class (after imports):

```python
# Sentinel symbol for the per-strategy aggregate capacity record. When
# available_slots == 0, the engine emits ONE record with this symbol and
# filter_results={"blocked_symbol_count": N} instead of N per-symbol rows.
CAPACITY_SENTINEL_SYMBOL = "_capacity_"
```

Export it from the domain package: in `src/alpaca_bot/domain/__init__.py`, add `CAPACITY_SENTINEL_SYMBOL` to the import from `.decision_record` and to `__all__` (match the file's existing export style).

- [ ] **Step 4: Replace the per-symbol loop in the engine**

In `src/alpaca_bot/core/engine.py`, the current block (inside `if available_slots == 0:`, around line 635):

```python
        if available_slots == 0:
            for _csym in (symbols or settings.symbols):
                if _csym in open_position_symbols or _csym in working_order_symbols:
                    continue
                _decision_records.append(DecisionRecord(
                    cycle_at=now,
                    symbol=_csym,
                    strategy_name=strategy_name,
                    trading_mode=_tm,
                    strategy_version=_sv,
                    decision="rejected",
                    reject_stage="capacity",
                    reject_reason="capacity_full",
                    entry_level=None,
                    signal_bar_close=None,
                    relative_volume=None,
                    atr=None,
                    stop_price=None,
                    limit_price=None,
                    initial_stop_price=None,
                    quantity=None,
                    risk_per_share=None,
                    equity=None,
                    filter_results={},
                    vix_close=_ctx_vix_close,
                    vix_above_sma=_ctx_vix_above_sma,
                    sector_passing_pct=_ctx_sector_passing_pct,
                ))
```

becomes:

```python
        if available_slots == 0:
            # One aggregate record per strategy per cycle — a per-symbol record
            # at watchlist scale floods decision_log (~11M rows/day observed).
            _blocked_count = sum(
                1
                for _csym in (symbols or settings.symbols)
                if _csym not in open_position_symbols
                and _csym not in working_order_symbols
            )
            if _blocked_count > 0:
                _decision_records.append(DecisionRecord(
                    cycle_at=now,
                    symbol=CAPACITY_SENTINEL_SYMBOL,
                    strategy_name=strategy_name,
                    trading_mode=_tm,
                    strategy_version=_sv,
                    decision="rejected",
                    reject_stage="capacity",
                    reject_reason="capacity_full",
                    entry_level=None,
                    signal_bar_close=None,
                    relative_volume=None,
                    atr=None,
                    stop_price=None,
                    limit_price=None,
                    initial_stop_price=None,
                    quantity=None,
                    risk_per_share=None,
                    equity=None,
                    filter_results={"blocked_symbol_count": _blocked_count},
                    vix_close=_ctx_vix_close,
                    vix_above_sma=_ctx_vix_above_sma,
                    sector_passing_pct=_ctx_sector_passing_pct,
                ))
```

Add `CAPACITY_SENTINEL_SYMBOL` to the existing `from alpaca_bot.domain ...` import in `engine.py` (the file already imports `DecisionRecord` — extend that import line).

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/unit/test_decision_log.py -v`
Expected: PASS (all tests in the file — the exposure-stage and other decision-record tests must be untouched).

- [ ] **Step 6: Commit**

```bash
git add src/alpaca_bot/domain/decision_record.py src/alpaca_bot/domain/__init__.py src/alpaca_bot/core/engine.py tests/unit/test_decision_log.py
git commit -m "fix: emit one aggregate capacity DecisionRecord per strategy per cycle"
```

---

### Task 2: S1 — Funnel query weights aggregate capacity rows

**Files:**
- Modify: `src/alpaca_bot/storage/repositories.py` (`DecisionLogStore.funnel_by_strategy`, line ~2318)
- Test: `tests/unit/test_funnel_report.py`

- [ ] **Step 1: Write the failing test**

In `tests/unit/test_funnel_report.py`, extend `_FakeCursor` to record SQL (modify the existing class):

```python
class _FakeCursor:
    """Cursor that returns predefined rows from fetchall() and records SQL."""

    def __init__(self, rows: list[tuple], log: list[str]) -> None:
        self._rows = rows
        self._log = log

    def execute(self, sql: str, params) -> None:
        self._log.append(sql)

    def fetchall(self) -> list[tuple]:
        return self._rows


class _FakeConn:
    def __init__(self, rows: list[tuple]) -> None:
        self._rows = rows
        self.executed_sql: list[str] = []

    def cursor(self) -> _FakeCursor:
        return _FakeCursor(self._rows, self.executed_sql)
```

Add a new test:

```python
def test_funnel_by_strategy_weights_aggregate_capacity_rows() -> None:
    """The SQL must weight rows by blocked_symbol_count so one aggregate
    '_capacity_' record counts as N blocked symbols (and plain rows as 1)."""
    conn = _FakeConn(_make_rows())
    store = DecisionLogStore(conn)
    store.funnel_by_strategy(
        start_date=date(2026, 5, 1),
        end_date=date(2026, 5, 7),
        trading_mode="paper",
    )
    sql = conn.executed_sql[0]
    assert "blocked_symbol_count" in sql
    assert "SUM(w)" in sql
    assert "COUNT(*)" not in sql
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_funnel_report.py -v`
Expected: new test FAILS (`blocked_symbol_count` not in SQL); existing tests still pass (they only consume predefined rows).

- [ ] **Step 3: Rewrite the funnel SQL**

In `funnel_by_strategy`, replace the SQL string (keep the method signature, `_cols`, params, and return statement identical) with:

```python
        rows = fetch_all(
            self._connection,
            """
            SELECT
                strategy_name,
                COALESCE(SUM(w), 0) AS evaluated,
                COALESCE(SUM(w) FILTER (
                    WHERE decision NOT IN (
                        'skipped_existing_position', 'skipped_already_traded'
                    )
                ), 0) AS not_skipped,
                COALESCE(SUM(w) FILTER (
                    WHERE decision NOT IN (
                        'skipped_existing_position', 'skipped_already_traded'
                    )
                      AND reject_stage IS DISTINCT FROM 'pre_filter'
                      AND reject_stage IS DISTINCT FROM 'stale_data'
                ), 0) AS not_prefiltered,
                COALESCE(SUM(w) FILTER (
                    WHERE decision NOT IN (
                        'skipped_existing_position', 'skipped_already_traded',
                        'skipped_no_signal'
                    )
                      AND reject_stage IS DISTINCT FROM 'pre_filter'
                      AND reject_stage IS DISTINCT FROM 'stale_data'
                ), 0) AS signal_fired,
                COALESCE(SUM(w) FILTER (
                    WHERE decision NOT IN (
                        'skipped_existing_position', 'skipped_already_traded',
                        'skipped_no_signal'
                    )
                      AND reject_stage IS DISTINCT FROM 'pre_filter'
                      AND reject_stage IS DISTINCT FROM 'stale_data'
                      AND reject_stage IS DISTINCT FROM 'vwap_filter'
                ), 0) AS passed_entry_filter,
                COALESCE(SUM(w) FILTER (
                    WHERE decision NOT IN (
                        'skipped_existing_position', 'skipped_already_traded',
                        'skipped_no_signal'
                    )
                      AND reject_stage IS DISTINCT FROM 'pre_filter'
                      AND reject_stage IS DISTINCT FROM 'stale_data'
                      AND reject_stage IS DISTINCT FROM 'vwap_filter'
                      AND reject_stage IS DISTINCT FROM 'sizing'
                ), 0) AS sized,
                COALESCE(SUM(w) FILTER (WHERE decision = 'accepted'), 0) AS accepted
            FROM (
                SELECT strategy_name, decision, reject_stage,
                       COALESCE((filter_results->>'blocked_symbol_count')::int, 1) AS w
                FROM decision_log
                WHERE DATE(cycle_at AT TIME ZONE %s) BETWEEN %s AND %s
                  AND trading_mode = %s
            ) weighted
            GROUP BY strategy_name
            ORDER BY strategy_name
            """,
            (market_timezone, start_date, end_date, trading_mode),
        )
```

Historical per-symbol capacity rows have `filter_results = '{}'`, so `->>'blocked_symbol_count'` is NULL and they weight as 1 — backward compatible.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_funnel_report.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/alpaca_bot/storage/repositories.py tests/unit/test_funnel_report.py
git commit -m "feat: weight funnel counts by blocked_symbol_count for aggregate capacity rows"
```

---

### Task 3: S2 — Same-session entry matching in P&L attribution queries

**Files:**
- Modify: `src/alpaca_bot/storage/repositories.py` — `list_trade_pnl_by_strategy` (line ~751), `list_closed_trade_records` (line ~815), `list_closed_trades` (line ~593)
- Test: `tests/unit/test_repositories.py`

The predicate added to **every correlated entry subquery** in these three methods, placed immediately after the existing `AND e.updated_at <= x.updated_at` line:

```sql
AND DATE(e.updated_at AT TIME ZONE %s)
    = DATE(x.updated_at AT TIME ZONE %s)
```

Each occurrence adds two `market_timezone` params. Because the subqueries appear in the SELECT list, their params come **before** the WHERE-clause params in the tuple.

- [ ] **Step 1: Write the failing tests**

Add to `tests/unit/test_repositories.py` (a SQL/param-capture fake matching the file's existing style; if the file already defines a recording connection, reuse it — otherwise add):

```python
class _SqlCaptureCursor:
    def __init__(self, log: list[tuple[str, tuple]]) -> None:
        self._log = log

    def execute(self, sql: str, params=None) -> None:
        self._log.append((sql, tuple(params or ())))

    def fetchall(self) -> list[tuple]:
        return []

    def fetchone(self):
        return None


class _SqlCaptureConn:
    def __init__(self) -> None:
        self.captured: list[tuple[str, tuple]] = []

    def cursor(self) -> _SqlCaptureCursor:
        return _SqlCaptureCursor(self.captured)

    def commit(self) -> None:
        pass

    def rollback(self) -> None:
        pass


def test_trade_pnl_by_strategy_requires_same_session_entry() -> None:
    """Entry fills must be matched on the same session date as the exit —
    recovery liquidations with no same-day entry must drop out."""
    conn = _SqlCaptureConn()
    repo = OrderStore(conn)
    repo.list_trade_pnl_by_strategy(
        trading_mode=TradingMode.PAPER,
        strategy_version="v1",
        start_date=date(2026, 6, 1),
        end_date=date(2026, 6, 9),
    )
    sql, params = conn.captured[0]
    assert sql.count("DATE(e.updated_at AT TIME ZONE %s)") == 1
    assert params.count("America/New_York") == sql.count("AT TIME ZONE %s")


def test_closed_trade_records_requires_same_session_entry() -> None:
    conn = _SqlCaptureConn()
    repo = OrderStore(conn)
    repo.list_closed_trade_records(
        trading_mode=TradingMode.PAPER,
        strategy_version="v1",
        since_date=date(2026, 6, 1),
        until_date=date(2026, 6, 9),
    )
    sql, params = conn.captured[0]
    assert sql.count("DATE(e.updated_at AT TIME ZONE %s)") == 2
    assert params.count("America/New_York") == sql.count("AT TIME ZONE %s")


def test_closed_trades_requires_same_session_entry() -> None:
    conn = _SqlCaptureConn()
    repo = OrderStore(conn)
    repo.list_closed_trades(
        trading_mode=TradingMode.PAPER,
        strategy_version="v1",
        session_date=date(2026, 6, 9),
    )
    sql, params = conn.captured[0]
    assert sql.count("DATE(e.updated_at AT TIME ZONE %s)") == 3
    assert params.count("America/New_York") == sql.count("AT TIME ZONE %s")
```

(Use the repository class name as imported in this test file — `OrderStore` is exported from `alpaca_bot.storage.repositories`; check the file's existing imports and match them.) The `params.count(...) == sql.count("AT TIME ZONE %s")` assertion is the param-ordering guard: every timezone placeholder must be fed a timezone value, which fails if the tuple wasn't extended in the right positions.

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_repositories.py -v -k same_session`
Expected: FAIL — `DATE(e.updated_at AT TIME ZONE %s)` count is 0 in all three.

- [ ] **Step 3: Edit `list_trade_pnl_by_strategy`**

In the correlated subquery, after `AND e.updated_at <= x.updated_at` add:

```sql
                       AND DATE(e.updated_at AT TIME ZONE %s)
                           = DATE(x.updated_at AT TIME ZONE %s)
```

New params tuple (subquery placeholders come after the first SELECT-list `%s`):

```python
            (
                market_timezone,
                market_timezone,
                market_timezone,
                trading_mode.value,
                strategy_version,
                market_timezone,
                start_date,
                market_timezone,
                end_date,
            ),
```

Update the docstring to add: `Entries are matched on the same session date as the exit; carryover/recovery liquidations without a same-day entry are excluded.`

- [ ] **Step 4: Edit `list_closed_trade_records`**

Add the same two-line predicate after `AND e.updated_at <= x.updated_at` in **both** entry subqueries (`entry_fill` and `entry_time`). New params tuple:

```python
            (
                market_timezone,
                market_timezone,
                market_timezone,
                market_timezone,
                trading_mode.value,
                strategy_version,
                market_timezone,
                since_date,
                market_timezone,
                until_date,
            ),
```

- [ ] **Step 5: Edit `list_closed_trades`**

Add the predicate to **all three** entry subqueries (`entry_fill`, `entry_limit`, `entry_time`). New params tuple:

```python
            (
                market_timezone,
                market_timezone,
                market_timezone,
                market_timezone,
                market_timezone,
                market_timezone,
                trading_mode.value,
                strategy_version,
                market_timezone,
                session_date,
                *strategy_params,
            ),
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/unit/test_repositories.py tests/unit/test_supervisor_weights.py tests/unit/test_nightly_cli.py -v`
Expected: PASS (supervisor-weights and nightly tests use fake stores, so they confirm no signature drift).

- [ ] **Step 7: Commit**

```bash
git add src/alpaca_bot/storage/repositories.py tests/unit/test_repositories.py
git commit -m "fix: match entry fills on same session date in P&L attribution queries"
```

---

### Task 4: S3 — `floor_raised_at` column, model field, store round-trip

**Files:**
- Create: `migrations/023_add_floor_raised_at.sql`
- Modify: `src/alpaca_bot/storage/models.py:135` (`ConfidenceFloor`)
- Modify: `src/alpaca_bot/storage/repositories.py:1688` (`ConfidenceFloorStore`)
- Test: `tests/unit/test_confidence_floor_store.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/test_confidence_floor_store.py` (mirror the file's existing fake-connection pattern for upsert/load; adapt names to what the file already defines):

```python
def test_confidence_floor_round_trips_floor_raised_at() -> None:
    raised_at = datetime(2026, 6, 1, 14, 30, tzinfo=timezone.utc)
    rec = ConfidenceFloor(
        trading_mode=TradingMode.PAPER,
        strategy_version="v1",
        floor_value=0.45,
        manual_floor_baseline=0.25,
        equity_high_watermark=100_000.0,
        set_by="system",
        reason="test",
        updated_at=datetime(2026, 6, 1, 14, 31, tzinfo=timezone.utc),
        floor_raised_at=raised_at,
    )
    assert rec.floor_raised_at == raised_at


def test_confidence_floor_raised_at_defaults_to_none() -> None:
    rec = ConfidenceFloor(
        trading_mode=TradingMode.PAPER,
        strategy_version="v1",
        floor_value=0.25,
        manual_floor_baseline=0.25,
        equity_high_watermark=100_000.0,
        set_by="operator",
        reason="manual",
    )
    assert rec.floor_raised_at is None
```

Also extend the file's existing upsert/load round-trip test (if it uses a recording fake connection) to assert `floor_raised_at` appears in both the INSERT column list and the SELECT column list. If the existing tests only check the dataclass, add:

```python
def test_confidence_floor_store_sql_includes_floor_raised_at() -> None:
    conn = _SqlCaptureConn()  # same capture fake as in test_repositories.py; define locally
    store = ConfidenceFloorStore(conn)
    store.upsert(ConfidenceFloor(
        trading_mode=TradingMode.PAPER,
        strategy_version="v1",
        floor_value=0.45,
        manual_floor_baseline=0.25,
        equity_high_watermark=100_000.0,
        set_by="system",
        reason="test",
        updated_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
        floor_raised_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
    ))
    store.load(trading_mode=TradingMode.PAPER, strategy_version="v1")
    upsert_sql = conn.captured[0][0]
    load_sql = conn.captured[1][0]
    assert "floor_raised_at" in upsert_sql
    assert "floor_raised_at" in load_sql
```

And a migration-exists test (mirror `test_migration_015_exists_and_contains_decision_log` in `tests/unit/test_decision_log.py`):

```python
def test_migration_023_adds_floor_raised_at() -> None:
    path = Path(__file__).resolve().parents[2] / "migrations" / "023_add_floor_raised_at.sql"
    sql = path.read_text()
    assert "ALTER TABLE confidence_floor_store" in sql
    assert "floor_raised_at" in sql
    assert "TIMESTAMPTZ" in sql
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_confidence_floor_store.py -v`
Expected: FAIL — `ConfidenceFloor` has no `floor_raised_at`, migration file missing.

- [ ] **Step 3: Create the migration**

`migrations/023_add_floor_raised_at.sql`:

```sql
-- When the confidence floor was last auto-raised by the system. NULL for
-- operator-set floors and for floors raised before this column existed.
-- Used by the max-age escape (FLOOR_AUTO_RAISE_MAX_AGE_DAYS) to clear a
-- system-raised floor that hysteresis would otherwise keep alive forever.
ALTER TABLE confidence_floor_store
    ADD COLUMN floor_raised_at TIMESTAMPTZ;
```

- [ ] **Step 4: Add the model field**

In `src/alpaca_bot/storage/models.py`, `ConfidenceFloor` — add as the last field (after `updated_at`):

```python
    floor_raised_at: datetime | None = None  # when the system last auto-raised; None for operator floors
```

- [ ] **Step 5: Extend the store**

In `ConfidenceFloorStore.upsert`, the SQL becomes:

```python
            """
            INSERT INTO confidence_floor_store (
                trading_mode, strategy_version, floor_value,
                manual_floor_baseline, equity_high_watermark,
                set_by, reason, updated_at, floor_raised_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (trading_mode, strategy_version)
            DO UPDATE SET
                floor_value = EXCLUDED.floor_value,
                manual_floor_baseline = EXCLUDED.manual_floor_baseline,
                equity_high_watermark = EXCLUDED.equity_high_watermark,
                set_by = EXCLUDED.set_by,
                reason = EXCLUDED.reason,
                updated_at = EXCLUDED.updated_at,
                floor_raised_at = EXCLUDED.floor_raised_at
            """,
```

and the params tuple gains `rec.floor_raised_at` at the end. In `load`, add `floor_raised_at` to the SELECT column list (last) and `floor_raised_at=row[8]` to the constructed `ConfidenceFloor`.

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/unit/test_confidence_floor_store.py tests/unit/test_admin_set_confidence_floor.py -v`
Expected: PASS (admin set-confidence-floor constructs `ConfidenceFloor` without `floor_raised_at`, which now defaults to None — the correct semantics for an operator-set floor).

- [ ] **Step 7: Commit**

```bash
git add migrations/023_add_floor_raised_at.sql src/alpaca_bot/storage/models.py src/alpaca_bot/storage/repositories.py tests/unit/test_confidence_floor_store.py
git commit -m "feat: track floor_raised_at on confidence floor records"
```

---

### Task 5: S3 — `FLOOR_AUTO_RAISE_MAX_AGE_DAYS` Settings field

**Files:**
- Modify: `src/alpaca_bot/config/__init__.py` (field ~line 82, parse ~line 222, validate ~line 631)
- Test: `tests/unit/test_config.py` (or the file where Settings validation tests live — find with `grep -rl "VOL_RAISE_THRESHOLD" tests/unit/`)

- [ ] **Step 1: Write the failing test**

```python
def test_floor_auto_raise_max_age_days_default_and_validation() -> None:
    settings = Settings.from_env(_base_env())
    assert settings.floor_auto_raise_max_age_days == 7

    with pytest.raises(ValueError, match="FLOOR_AUTO_RAISE_MAX_AGE_DAYS"):
        Settings.from_env({**_base_env(), "FLOOR_AUTO_RAISE_MAX_AGE_DAYS": "0"})
```

(Use the env-builder helper the test file already uses — `_base_env` from `tests/unit/helpers` or equivalent.)

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_config.py -v -k max_age`
Expected: FAIL — attribute does not exist.

- [ ] **Step 3: Implement**

Field, after `vol_raise_threshold: float = 0.025`:

```python
    floor_auto_raise_max_age_days: int = 7
```

Parse, after the `vol_raise_threshold` line in `from_env`:

```python
            floor_auto_raise_max_age_days=int(
                values.get("FLOOR_AUTO_RAISE_MAX_AGE_DAYS", "7")
            ),
```

Validate, after the `vol_raise_threshold` check in `validate()`:

```python
        if self.floor_auto_raise_max_age_days < 1:
            raise ValueError("FLOOR_AUTO_RAISE_MAX_AGE_DAYS must be at least 1")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_config.py -v`
Expected: PASS.

- [ ] **Step 5: Document the env var**

Add to the env-var table/template in `DEPLOYMENT.md` (next to the other floor vars):

```
# Days a system-raised confidence floor may persist without a fresh raise
# trigger before it auto-clears to the manual baseline (default 7)
FLOOR_AUTO_RAISE_MAX_AGE_DAYS=7
```

- [ ] **Step 6: Commit**

```bash
git add src/alpaca_bot/config/__init__.py tests/unit/test_config.py DEPLOYMENT.md
git commit -m "feat: add FLOOR_AUTO_RAISE_MAX_AGE_DAYS setting"
```

---

### Task 6: S3 — Max-age escape in `_check_and_update_floor_triggers`

**Files:**
- Modify: `src/alpaca_bot/runtime/supervisor.py:1807-1920`
- Test: `tests/unit/test_confidence_floor_triggers.py`

- [ ] **Step 1: Update the test harness and write failing tests**

In `tests/unit/test_confidence_floor_triggers.py`, extend `_make_floor` with optional raise metadata:

```python
def _make_floor(
    floor_value: float,
    watermark: float,
    manual_baseline: float | None = None,
    *,
    set_by: str = "system",
    floor_raised_at: datetime | None = None,
) -> ConfidenceFloor:
    return ConfidenceFloor(
        trading_mode=TradingMode.PAPER,
        strategy_version="v1",
        floor_value=floor_value,
        manual_floor_baseline=manual_baseline if manual_baseline is not None else floor_value,
        equity_high_watermark=watermark,
        set_by=set_by,
        reason="test",
        updated_at=datetime(2026, 5, 8, tzinfo=timezone.utc),
        floor_raised_at=floor_raised_at,
    )
```

Add tests (the harness `_make_supervisor_with_floor` already exists; seed the store via `_FakeFloorStore(rec)` — follow the pattern of `test_drawdown_hysteresis_keeps_floor_raised` for equity/watermark values that land in the hysteresis band, i.e. drawdown between 2.5% and 5% with default settings):

```python
def test_max_age_clears_system_raised_floor() -> None:
    """A system-raised floor older than FLOOR_AUTO_RAISE_MAX_AGE_DAYS clears to
    the manual baseline even while hysteresis would keep it alive."""
    now = datetime(2026, 6, 10, 14, 30, tzinfo=timezone.utc)
    rec = _make_floor(
        0.80, 100_000.0, manual_baseline=0.25,
        floor_raised_at=now - timedelta(days=8),
    )
    sup, store = _make_supervisor_with_floor()
    store._rec = rec
    # equity 97,100 → drawdown 2.9%: inside the hysteresis band (2.5%–5%)
    sup._check_and_update_floor_triggers(
        current_equity=97_100.0, now=now, daily_bars_for_vol=[],
    )
    assert store.upserted, "expected a floor write"
    updated = store.upserted[-1]
    assert updated.floor_value == pytest.approx(0.25)
    assert updated.floor_raised_at is None
    assert "max age exceeded" in updated.reason


def test_fresh_raise_trigger_resets_max_age_clock() -> None:
    """An active raise trigger (drawdown > threshold) re-stamps floor_raised_at."""
    now = datetime(2026, 6, 10, 14, 30, tzinfo=timezone.utc)
    rec = _make_floor(
        0.45, 100_000.0, manual_baseline=0.25,
        floor_raised_at=now - timedelta(days=8),
    )
    sup, store = _make_supervisor_with_floor()
    store._rec = rec
    # equity 94,000 → drawdown 6%: above DRAWDOWN_RAISE_PCT (5%) — fresh trigger
    sup._check_and_update_floor_triggers(
        current_equity=94_000.0, now=now, daily_bars_for_vol=[],
    )
    updated = store.upserted[-1]
    assert updated.floor_value > 0.45  # raised further, not cleared
    assert updated.floor_raised_at == now


def test_hysteresis_does_not_reset_max_age_clock() -> None:
    """Keep-alive must not refresh the clock — otherwise the escape never fires."""
    now = datetime(2026, 6, 10, 14, 30, tzinfo=timezone.utc)
    raised_at = now - timedelta(days=3)
    rec = _make_floor(
        0.80, 100_000.0, manual_baseline=0.25, floor_raised_at=raised_at,
    )
    sup, store = _make_supervisor_with_floor()
    store._rec = rec
    sup._check_and_update_floor_triggers(
        current_equity=97_100.0, now=now, daily_bars_for_vol=[],
    )
    # Hysteresis band, clock under max age: floor unchanged, clock unchanged.
    if store.upserted:
        assert store.upserted[-1].floor_raised_at == raised_at
        assert store.upserted[-1].floor_value == pytest.approx(0.80)


def test_legacy_raised_floor_with_null_clock_gets_backfilled() -> None:
    """Floors raised before the column existed start their clock on first sight."""
    now = datetime(2026, 6, 10, 14, 30, tzinfo=timezone.utc)
    rec = _make_floor(0.80, 100_000.0, manual_baseline=0.25, floor_raised_at=None)
    sup, store = _make_supervisor_with_floor()
    store._rec = rec
    sup._check_and_update_floor_triggers(
        current_equity=97_100.0, now=now, daily_bars_for_vol=[],
    )
    assert store.upserted, "expected a write to backfill the clock"
    updated = store.upserted[-1]
    assert updated.floor_raised_at == now
    assert updated.floor_value == pytest.approx(0.80)  # not cleared yet


def test_operator_set_floor_is_never_max_age_cleared() -> None:
    """set_by='operator' floors are exempt from the max-age escape."""
    now = datetime(2026, 6, 10, 14, 30, tzinfo=timezone.utc)
    rec = _make_floor(
        0.60, 100_000.0, manual_baseline=0.25,
        set_by="operator", floor_raised_at=now - timedelta(days=30),
    )
    sup, store = _make_supervisor_with_floor()
    store._rec = rec
    sup._check_and_update_floor_triggers(
        current_equity=97_100.0, now=now, daily_bars_for_vol=[],
    )
    for updated in store.upserted:
        assert updated.floor_value == pytest.approx(0.60)
```

Add `timedelta` to the test file's datetime import. Note: in the operator test, the floor sits above baseline while in the hysteresis band, so the existing keep-alive holds it; the assertion is that the max-age path specifically did not clear it.

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_confidence_floor_triggers.py -v -k "max_age or clock or operator_set"`
Expected: FAIL — no clock logic exists yet.

- [ ] **Step 3: Implement in the supervisor**

In `_check_and_update_floor_triggers` (`src/alpaca_bot/runtime/supervisor.py`):

(a) Initialize a flag after `reason_parts: list[str] = []`:

```python
        raise_triggered = False
```

(b) In the drawdown raise branch, after `reason_parts.append(f"drawdown {drawdown_pct:.1%}")`:

```python
                raise_triggered = True
```

(c) In the vol raise branch, after `reason_parts.append(f"vol {daily_vol:.2%}")`:

```python
                raise_triggered = True
```

(d) Replace the block from the `# Auto-clear:` comment through the `if not floor_changed and not watermark_changed:` return with:

```python
        # Auto-raise clock: a fresh raise trigger (re)stamps it; the hysteresis
        # keep-alive deliberately does not — otherwise the max-age escape below
        # could never fire while the band holds.
        prev_raised_at = rec.floor_raised_at if rec is not None else None
        new_raised_at = prev_raised_at
        if raise_triggered:
            new_raised_at = now
        elif current_floor > manual_baseline + 1e-9 and prev_raised_at is None:
            # Raised before floor_raised_at existed — start the clock now.
            new_raised_at = now

        # Auto-clear: when no trigger fired and floor was previously auto-raised,
        # restore it to the last operator-set baseline.
        reason_parts_for_clear: str | None = None
        if not reason_parts and new_floor > manual_baseline + 1e-9:
            new_floor = manual_baseline
            new_raised_at = None
            reason_parts_for_clear = "all triggers cleared"

        # Max-age escape: a system-raised floor kept alive only by hysteresis can
        # deadlock — the raised floor blocks entries, so equity can never climb
        # back above the clear threshold. After FLOOR_AUTO_RAISE_MAX_AGE_DAYS
        # without a fresh raise trigger, clear to the manual baseline.
        if (
            not raise_triggered
            and reason_parts_for_clear is None
            and new_floor > manual_baseline + 1e-9
            and (rec is None or rec.set_by == "system")
            and new_raised_at is not None
            and now - new_raised_at
                > timedelta(days=self.settings.floor_auto_raise_max_age_days)
        ):
            new_floor = manual_baseline
            new_raised_at = None
            reason_parts = []
            reason_parts_for_clear = (
                f"max age exceeded "
                f"({self.settings.floor_auto_raise_max_age_days}d)"
            )

        floor_changed = abs(new_floor - current_floor) > 1e-9
        watermark_changed = abs(new_watermark - watermark) > 1e-9
        raised_at_changed = new_raised_at != prev_raised_at

        if not floor_changed and not watermark_changed and not raised_at_changed:
            return
```

(e) Add `floor_raised_at=new_raised_at,` to the `ConfidenceFloor(...)` construction (`updated = ConfidenceFloor(`, line ~1889).

`timedelta` is already imported in supervisor.py (line 6). The audit-event block below needs no change — `reason_parts_for_clear` being set makes it emit `confidence_floor_auto_cleared` with the max-age reason, exactly per spec.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_confidence_floor_triggers.py tests/unit/test_supervisor_weights.py -v`
Expected: PASS, including all pre-existing floor-trigger tests (the new logic is a no-op when `floor_raised_at` is None and the floor is at baseline).

- [ ] **Step 5: Commit**

```bash
git add src/alpaca_bot/runtime/supervisor.py tests/unit/test_confidence_floor_triggers.py
git commit -m "feat: max-age escape clears stuck auto-raised confidence floor"
```

---

### Task 7: S4 — `DecisionLogStore.prune`

**Files:**
- Modify: `src/alpaca_bot/storage/repositories.py` (`DecisionLogStore`, after `bulk_insert`)
- Test: `tests/unit/test_decision_log.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/test_decision_log.py` (the file already has `_TrackingConnection`; add a variant with rowcount):

```python
class _PruneConn:
    def __init__(self, rowcount: int) -> None:
        self.executed: list[tuple[str, tuple]] = []
        self.committed = False
        self._rowcount = rowcount

    def cursor(self):
        conn = self

        class _Cursor:
            rowcount = conn._rowcount

            def execute(self, sql, params=None):
                conn.executed.append((sql, tuple(params or ())))

        return _Cursor()

    def commit(self) -> None:
        self.committed = True


def test_decision_log_prune_deletes_before_cutoff_and_returns_count() -> None:
    conn = _PruneConn(rowcount=12345)
    store = DecisionLogStore(conn)
    now = datetime(2026, 6, 10, 12, 0, tzinfo=timezone.utc)
    deleted = store.prune(older_than_days=30, now=now)
    assert deleted == 12345
    assert conn.committed
    sql, params = conn.executed[0]
    assert "DELETE FROM decision_log" in sql
    assert "cycle_at <" in sql
    assert params == (datetime(2026, 5, 11, 12, 0, tzinfo=timezone.utc),)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_decision_log.py -v -k prune`
Expected: FAIL — `DecisionLogStore` has no `prune`.

- [ ] **Step 3: Implement**

In `src/alpaca_bot/storage/repositories.py`, add to `DecisionLogStore` (after `bulk_insert`); add `timedelta` to the file's `from datetime import date, datetime` import:

```python
    def prune(self, *, older_than_days: int, now: datetime) -> int:
        """Delete rows whose cycle_at is older than the cutoff; return count.

        Single statement in one transaction — either all qualifying rows are
        gone or none are.
        """
        cutoff = now - timedelta(days=older_than_days)
        cursor = self._connection.cursor()
        cursor.execute(
            "DELETE FROM decision_log WHERE cycle_at < %s",
            (cutoff,),
        )
        deleted = int(getattr(cursor, "rowcount", 0) or 0)
        self._connection.commit()
        return deleted
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_decision_log.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/alpaca_bot/storage/repositories.py tests/unit/test_decision_log.py
git commit -m "feat: add DecisionLogStore.prune with cutoff by cycle_at"
```

---

### Task 8: S4 — `alpaca-bot-admin prune-decision-log`

**Files:**
- Modify: `src/alpaca_bot/admin/cli.py` (`build_parser` + `main` dispatch)
- Test: `tests/unit/test_admin_cli.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/test_admin_cli.py` (reuse the file's existing fakes for connection/settings/audit store — mirror how other command tests inject `connect=` and factories; the snippet below shows the shape, adapt fixture names to the file):

```python
class _FakeDecisionLogStore:
    def __init__(self) -> None:
        self.prune_calls: list[dict] = []

    def prune(self, *, older_than_days: int, now) -> int:
        self.prune_calls.append({"older_than_days": older_than_days, "now": now})
        return 42


def test_prune_decision_log_command_prunes_and_audits() -> None:
    dl_store = _FakeDecisionLogStore()
    audit_store = _RecordingAuditEventStore()  # reuse the file's recording fake
    out = io.StringIO()
    code = main(
        ["prune-decision-log", "--keep-days", "14"],
        settings=_settings(),
        connect=lambda: SimpleNamespace(close=lambda: None),
        audit_event_store_factory=lambda conn: audit_store,
        decision_log_store_factory=lambda conn: dl_store,
        now=lambda: datetime(2026, 6, 10, 12, 0, tzinfo=timezone.utc),
        stdout=out,
    )
    assert code == 0
    assert dl_store.prune_calls == [{
        "older_than_days": 14,
        "now": datetime(2026, 6, 10, 12, 0, tzinfo=timezone.utc),
    }]
    events = [e for e in audit_store.appended if e.event_type == "decision_log_pruned"]
    assert len(events) == 1
    assert events[0].payload["deleted_count"] == 42
    assert events[0].payload["keep_days"] == 14
    assert "deleted=42" in out.getvalue()


def test_prune_decision_log_rejects_keep_days_below_one() -> None:
    with pytest.raises(SystemExit):
        main(
            ["prune-decision-log", "--keep-days", "0"],
            settings=_settings(),
            connect=lambda: SimpleNamespace(close=lambda: None),
        )
```

(`_settings()` / `_RecordingAuditEventStore` — use whatever the file already provides; `tests/unit/test_admin_reset_weights.py` has both patterns if `test_admin_cli.py` differs.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_admin_cli.py -v -k prune`
Expected: FAIL — unknown command, unknown kwarg.

- [ ] **Step 3: Implement**

In `src/alpaca_bot/admin/cli.py`:

(a) Import `DecisionLogStore` (extend the existing `from alpaca_bot.storage...` import block — match where `AuditEventStore` is imported from).

(b) In `build_parser`, after the `set-confidence-floor` block:

```python
    pdl_parser = subparsers.add_parser("prune-decision-log")
    pdl_parser.add_argument(
        "--mode",
        choices=[mode.value for mode in TradingMode],
        default=defaults.trading_mode.value,
    )
    pdl_parser.add_argument("--strategy-version", default=defaults.strategy_version)
    pdl_parser.add_argument("--keep-days", type=int, default=30)
```

(c) In `main`, add a factory kwarg alongside the others:

```python
    decision_log_store_factory: Callable[[ConnectionProtocol], DecisionLogStore] = DecisionLogStore,
```

(d) In the command dispatch, after the `set-confidence-floor` branch:

```python
        elif args.command == "prune-decision-log":
            if args.keep_days < 1:
                raise SystemExit("--keep-days must be at least 1")
            dl_store = decision_log_store_factory(connection)
            deleted = dl_store.prune(older_than_days=args.keep_days, now=timestamp)
            audit_store.append(AuditEvent(
                event_type="decision_log_pruned",
                payload={
                    "deleted_count": deleted,
                    "keep_days": args.keep_days,
                    "source": "admin",
                },
                created_at=timestamp,
            ))
            output = (
                f"decision_log pruned: deleted={deleted} "
                f"keep_days={args.keep_days}"
            )
```

(Match how the file prints `output` at the end of `main` — it already routes `output` to `stdout`.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_admin_cli.py -v`
Expected: PASS.

- [ ] **Step 5: Update CLAUDE.md command list**

In `CLAUDE.md`, under the admin commands block, add:

```bash
alpaca-bot-admin prune-decision-log --keep-days 30
```

- [ ] **Step 6: Commit**

```bash
git add src/alpaca_bot/admin/cli.py tests/unit/test_admin_cli.py CLAUDE.md
git commit -m "feat: add alpaca-bot-admin prune-decision-log command"
```

---

### Task 9: S4 — Nightly pipeline prunes decision_log

**Files:**
- Modify: `src/alpaca_bot/nightly/cli.py`
- Test: `tests/unit/test_nightly_cli.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/test_nightly_cli.py` (the file's tests monkeypatch module-level store classes via `_patch_common_db`; follow that pattern):

```python
def test_nightly_cli_prunes_decision_log(monkeypatch, tmp_path):
    import alpaca_bot.nightly.cli as module
    _patch_env(monkeypatch)
    _patch_common_db(monkeypatch, module)

    prune_calls: list[dict] = []

    class FakeDecisionLogStore:
        def __init__(self, conn):
            pass

        def prune(self, *, older_than_days, now):
            prune_calls.append({"older_than_days": older_than_days})
            return 7

    monkeypatch.setattr(module, "DecisionLogStore", FakeDecisionLogStore)
    monkeypatch.setattr(sys, "argv", [
        "alpaca-bot-nightly", "--dry-run", "--no-db",
        "--output-dir", str(tmp_path),
    ])
    result = module.main()
    assert result == 0
    assert prune_calls == [{"older_than_days": 30}]
```

(Adapt the argv to match what the file's existing dry-run test passes — copy its argv and add nothing; `--dry-run` with an empty `--output-dir` is the established minimal path. If `_patch_common_db` patches `AuditEventStore`, the audit append is covered; otherwise also patch it with a recording fake as the existing `nightly_sweep_completed` tests do.)

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_nightly_cli.py -v -k prune`
Expected: FAIL — `module.DecisionLogStore` does not exist.

- [ ] **Step 3: Implement**

In `src/alpaca_bot/nightly/cli.py`:

(a) Add `DecisionLogStore` to the existing `from alpaca_bot.storage.repositories import (...)` block.

(b) Add a CLI flag in `main` next to the other arguments:

```python
    parser.add_argument("--prune-keep-days", type=int, default=30,
                        help="Prune decision_log rows older than N days after the report "
                             "(default: 30; 0 disables)")
```

(c) After the rolling-report section (immediately before the `finally:` block at line ~300), add:

```python
        # ── Decision-log retention ───────────────────────────────────────────
        if args.prune_keep_days > 0:
            try:
                deleted = DecisionLogStore(conn).prune(
                    older_than_days=args.prune_keep_days, now=now
                )
                AuditEventStore(conn).append(
                    AuditEvent(
                        event_type="decision_log_pruned",
                        payload={
                            "deleted_count": deleted,
                            "keep_days": args.prune_keep_days,
                            "source": "nightly",
                        },
                        created_at=now,
                    )
                )
                print(
                    f"\nDecision log pruned: {deleted} rows older than "
                    f"{args.prune_keep_days} days removed."
                )
            except Exception as exc:
                print(f"Warning: decision_log prune failed: {exc}", file=sys.stderr)
```

(The try/except mirrors the file's existing `nightly_sweep_completed` handling: retention must never fail the sweep.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_nightly_cli.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/alpaca_bot/nightly/cli.py tests/unit/test_nightly_cli.py
git commit -m "feat: nightly pipeline prunes decision_log (default 30-day retention)"
```

---

### Task 10: Full verification

- [ ] **Step 1: Run the entire suite**

Run: `pytest`
Expected: all tests pass (baseline before this work: 1,933 passing).

- [ ] **Step 2: Verify no TODOs or placeholders were left**

Run: `git diff main --stat && grep -rn "TODO" src/alpaca_bot/core/engine.py src/alpaca_bot/storage/repositories.py src/alpaca_bot/runtime/supervisor.py src/alpaca_bot/admin/cli.py src/alpaca_bot/nightly/cli.py | grep -v "pre-existing" || true`
Expected: no new TODOs.

- [ ] **Step 3: Confirm migration ordering**

Run: `ls migrations/ | tail -3`
Expected: `023_add_floor_raised_at.sql` is the highest-numbered file.

---

## Design decisions locked in (from spec + planning)

1. **Sentinel symbol `_capacity_`** cannot collide with a real ticker (underscores are invalid in equity symbols). Dashboard `list_recent` will show it; that is informative, not a bug.
2. **Funnel weighting is backward compatible:** historical per-symbol capacity rows have `filter_results='{}'` → weight 1.
3. **S2 fixes three methods, not one** — see the scope decision at the top. The exits-only and lifetime methods stay contaminated and are explicitly deferred.
4. **Max-age clock semantics:** fresh raise → re-stamp; hysteresis → no stamp; NULL clock on a raised floor → backfill to `now` (window starts at deploy, conservative); operator floors exempt; clear emits `confidence_floor_auto_cleared` with reason `max age exceeded (Nd)`.
5. **Prune is one DELETE in one transaction** — crash-safe; partial deletion is impossible. The existing `cycle_at DESC` index serves the cutoff scan.
6. **No behavior change to order submission, sizing, or stops** in any task; paper/live parity holds (no mode-conditional code anywhere in S1–S4).
