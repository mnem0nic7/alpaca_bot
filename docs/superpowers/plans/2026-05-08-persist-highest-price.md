# Persist `highest_price` for Breakeven Trail Accuracy — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist `OpenPosition.highest_price` in Postgres so the breakeven trail stop always computes from the true historical maximum bar-high since entry, not just the current bar's high.

**Architecture:** Add a nullable `highest_price` column to the `positions` table (migration 017). After every cycle's bar fetch — including after-hours — call a new `_apply_highest_price_updates()` supervisor method that writes any new bar-high to the DB and returns updated `OpenPosition` objects. The engine receives already-correct `highest_price` values; `evaluate_cycle()` remains a pure function.

**Tech Stack:** PostgreSQL, psycopg2, Python dataclasses (`replace()`), pytest fake-callable DI pattern.

---

## File Map

| File | Action | Purpose |
|---|---|---|
| `migrations/017_add_position_highest_price.sql` | **Create** | ALTER TABLE to add `highest_price NUMERIC DEFAULT NULL` |
| `src/alpaca_bot/storage/models.py` | **Modify** | Add `highest_price: float \| None = None` to `PositionRecord` |
| `src/alpaca_bot/storage/repositories.py` | **Modify** | Update `list_all()`, `save()`, `replace_all()`; add `update_highest_price()` |
| `src/alpaca_bot/runtime/supervisor.py` | **Modify** | Fix `_load_open_positions()` + add `_apply_highest_price_updates()` + call site in `run_cycle_once()` |
| `tests/unit/test_position_store_highest_price.py` | **Create** | Repository-layer unit tests |
| `tests/unit/test_supervisor_highest_price.py` | **Create** | Supervisor-layer unit tests for `_apply_highest_price_updates()` |
| `tests/unit/test_cycle_engine_highest_price.py` | **Create** | Engine regression test: trail locked from `highest_price`, not `bar.high` |

---

### Task 1: Migration + PositionRecord field

**Files:**
- Create: `migrations/017_add_position_highest_price.sql`
- Modify: `src/alpaca_bot/storage/models.py:62-73`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_position_store_highest_price.py` with a single import-level smoke test that shows `PositionRecord` doesn't yet have `highest_price`:

```python
from __future__ import annotations
import pytest
from alpaca_bot.storage.models import PositionRecord


def test_position_record_has_highest_price_field():
    """PositionRecord must carry highest_price through the storage layer."""
    from datetime import datetime, timezone
    from alpaca_bot.config import TradingMode
    rec = PositionRecord(
        symbol="AAPL",
        trading_mode=TradingMode.PAPER,
        strategy_version="v1",
        quantity=10.0,
        entry_price=3.00,
        stop_price=2.97,
        initial_stop_price=2.97,
        opened_at=datetime(2026, 5, 1, tzinfo=timezone.utc),
        highest_price=3.20,
    )
    assert rec.highest_price == 3.20


def test_position_record_highest_price_defaults_to_none():
    from datetime import datetime, timezone
    from alpaca_bot.config import TradingMode
    rec = PositionRecord(
        symbol="AAPL",
        trading_mode=TradingMode.PAPER,
        strategy_version="v1",
        quantity=10.0,
        entry_price=3.00,
        stop_price=2.97,
        initial_stop_price=2.97,
        opened_at=datetime(2026, 5, 1, tzinfo=timezone.utc),
    )
    assert rec.highest_price is None
```

- [ ] **Step 2: Run test to verify it fails**

```
pytest tests/unit/test_position_store_highest_price.py::test_position_record_has_highest_price_field -v
```

Expected: `TypeError: __init__() got an unexpected keyword argument 'highest_price'`

- [ ] **Step 3: Create the migration file**

Create `migrations/017_add_position_highest_price.sql`:

```sql
ALTER TABLE positions
    ADD COLUMN IF NOT EXISTS highest_price NUMERIC DEFAULT NULL;
```

- [ ] **Step 4: Add `highest_price` field to `PositionRecord`**

In `src/alpaca_bot/storage/models.py`, change the `PositionRecord` dataclass. The current last two fields are:

```python
    strategy_name: str = "breakout"
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
```

Replace with:

```python
    strategy_name: str = "breakout"
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    highest_price: float | None = None
```

- [ ] **Step 5: Run tests to verify they pass**

```
pytest tests/unit/test_position_store_highest_price.py -v
```

Expected: 2 PASSED

- [ ] **Step 6: Commit**

```bash
git add migrations/017_add_position_highest_price.sql \
        src/alpaca_bot/storage/models.py \
        tests/unit/test_position_store_highest_price.py
git commit -m "feat: add highest_price column to positions table and PositionRecord field"
```

---

### Task 2: PositionStore repository layer

**Files:**
- Modify: `src/alpaca_bot/storage/repositories.py:1026-1204`
- Modify: `tests/unit/test_position_store_highest_price.py` (expand with 4 more tests)

- [ ] **Step 1: Add tests for repository behaviour**

Append to `tests/unit/test_position_store_highest_price.py`:

```python
from datetime import datetime, timezone

from alpaca_bot.config import TradingMode
from alpaca_bot.storage.models import PositionRecord
from alpaca_bot.storage.repositories import PositionStore


def _make_record(**overrides) -> PositionRecord:
    base = dict(
        symbol="AAPL",
        trading_mode=TradingMode.PAPER,
        strategy_version="v1",
        quantity=10.0,
        entry_price=3.00,
        stop_price=2.97,
        initial_stop_price=2.97,
        opened_at=datetime(2026, 5, 1, tzinfo=timezone.utc),
    )
    base.update(overrides)
    return PositionRecord(**base)


class _FakeCursor:
    def __init__(self, rows=None):
        self._rows = rows or []
        self.executed: list[tuple] = []

    def execute(self, sql, params=None):
        self.executed.append((sql, params))

    def fetchall(self):
        return list(self._rows)

    def __enter__(self): return self
    def __exit__(self, *a): pass


class _FakeConn:
    def __init__(self, rows=None):
        self._cursor = _FakeCursor(rows)
        self.committed = False
        self.rolled_back = False

    def cursor(self): return self._cursor
    def commit(self): self.committed = True
    def rollback(self): self.rolled_back = True


def test_save_includes_highest_price_in_insert():
    conn = _FakeConn()
    store = PositionStore(conn)
    rec = _make_record(highest_price=3.20)
    store.save(rec)
    sql, params = conn._cursor.executed[0]
    assert "highest_price" in sql
    assert 3.20 in params


def test_save_with_none_highest_price_passes_none():
    conn = _FakeConn()
    store = PositionStore(conn)
    rec = _make_record(highest_price=None)
    store.save(rec)
    _, params = conn._cursor.executed[0]
    assert None in params


def test_list_all_populates_highest_price():
    # Simulate a row from DB with highest_price in position 10 (0-indexed)
    row = (
        "AAPL", "paper", "v1", "breakout",
        10.0, 3.00, 2.97, 2.97,
        datetime(2026, 5, 1, tzinfo=timezone.utc),
        datetime(2026, 5, 1, tzinfo=timezone.utc),
        3.20,  # highest_price
    )
    conn = _FakeConn(rows=[row])
    store = PositionStore(conn)
    records = store.list_all(trading_mode=TradingMode.PAPER, strategy_version="v1")
    assert len(records) == 1
    assert records[0].highest_price == 3.20


def test_list_all_handles_null_highest_price():
    row = (
        "AAPL", "paper", "v1", "breakout",
        10.0, 3.00, 2.97, 2.97,
        datetime(2026, 5, 1, tzinfo=timezone.utc),
        datetime(2026, 5, 1, tzinfo=timezone.utc),
        None,  # highest_price is NULL in DB
    )
    conn = _FakeConn(rows=[row])
    store = PositionStore(conn)
    records = store.list_all(trading_mode=TradingMode.PAPER, strategy_version="v1")
    assert records[0].highest_price is None


def test_update_highest_price_issues_targeted_update():
    conn = _FakeConn()
    store = PositionStore(conn)
    store.update_highest_price(
        symbol="AAPL",
        trading_mode=TradingMode.PAPER,
        strategy_version="v1",
        strategy_name="breakout",
        highest_price=3.50,
    )
    assert conn.committed
    sql, params = conn._cursor.executed[0]
    assert "UPDATE positions" in sql
    assert "highest_price" in sql
    assert 3.50 in params
    assert "AAPL" in params


def test_save_on_conflict_coalesce_preserves_existing_highest_price():
    """ON CONFLICT clause must use COALESCE so a stop update never overwrites an accumulated high."""
    conn = _FakeConn()
    store = PositionStore(conn)
    rec = _make_record(highest_price=None)
    store.save(rec)
    sql, _ = conn._cursor.executed[0]
    assert "COALESCE" in sql
```

- [ ] **Step 2: Run tests to verify they fail**

```
pytest tests/unit/test_position_store_highest_price.py -k "not test_position_record" -v
```

Expected: all 6 new tests FAIL (AttributeError or AssertionError)

- [ ] **Step 3: Update `PositionStore.list_all()`**

In `src/alpaca_bot/storage/repositories.py`, replace the `list_all()` SELECT and result construction (lines ~1169-1204):

```python
    def list_all(
        self,
        *,
        trading_mode: TradingMode,
        strategy_version: str,
        strategy_name: str | None = None,
    ) -> list[PositionRecord]:
        strategy_clause = "AND strategy_name IS NOT DISTINCT FROM %s" if strategy_name is not None else ""
        strategy_params = (strategy_name,) if strategy_name is not None else ()
        cursor = self._connection.cursor()
        cursor.execute(
            f"""
            SELECT
                symbol,
                trading_mode,
                strategy_version,
                strategy_name,
                quantity,
                entry_price,
                stop_price,
                initial_stop_price,
                opened_at,
                updated_at,
                highest_price
            FROM positions
            WHERE trading_mode = %s AND strategy_version = %s
              {strategy_clause}
            ORDER BY symbol
            """,
            (trading_mode.value, strategy_version, *strategy_params),
        )
        rows = cursor.fetchall()
        return [
            PositionRecord(
                symbol=row[0],
                trading_mode=TradingMode(row[1]),
                strategy_version=row[2],
                strategy_name=row[3],
                quantity=float(row[4]),
                entry_price=float(row[5]),
                stop_price=float(row[6]),
                initial_stop_price=float(row[7]),
                opened_at=row[8],
                updated_at=row[9],
                highest_price=float(row[10]) if row[10] is not None else None,
            )
            for row in rows
        ]
```

- [ ] **Step 4: Update `PositionStore.save()`**

Replace the existing `save()` method (lines ~1026-1065):

```python
    def save(self, position: PositionRecord, *, commit: bool = True) -> None:
        execute(
            self._connection,
            """
            INSERT INTO positions (
                symbol,
                trading_mode,
                strategy_version,
                strategy_name,
                quantity,
                entry_price,
                stop_price,
                initial_stop_price,
                opened_at,
                updated_at,
                highest_price
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (symbol, trading_mode, strategy_version, strategy_name)
            DO UPDATE SET
                quantity = EXCLUDED.quantity,
                entry_price = EXCLUDED.entry_price,
                stop_price = EXCLUDED.stop_price,
                initial_stop_price = EXCLUDED.initial_stop_price,
                opened_at = EXCLUDED.opened_at,
                updated_at = EXCLUDED.updated_at,
                highest_price = COALESCE(EXCLUDED.highest_price, positions.highest_price)
            """,
            (
                position.symbol,
                position.trading_mode.value,
                position.strategy_version,
                position.strategy_name,
                position.quantity,
                position.entry_price,
                position.stop_price,
                position.initial_stop_price,
                position.opened_at,
                position.updated_at,
                position.highest_price,
            ),
            commit=commit,
        )
```

- [ ] **Step 5: Update `PositionStore.replace_all()` inserts**

In `replace_all()` (lines ~1091-1130), replace the inner INSERT with:

```python
                execute(
                    self._connection,
                    """
                    INSERT INTO positions (
                        symbol,
                        trading_mode,
                        strategy_version,
                        strategy_name,
                        quantity,
                        entry_price,
                        stop_price,
                        initial_stop_price,
                        opened_at,
                        updated_at,
                        highest_price
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (symbol, trading_mode, strategy_version, strategy_name)
                    DO UPDATE SET
                        quantity = EXCLUDED.quantity,
                        entry_price = EXCLUDED.entry_price,
                        stop_price = EXCLUDED.stop_price,
                        initial_stop_price = EXCLUDED.initial_stop_price,
                        opened_at = EXCLUDED.opened_at,
                        updated_at = EXCLUDED.updated_at,
                        highest_price = COALESCE(EXCLUDED.highest_price, positions.highest_price)
                    """,
                    (
                        position.symbol,
                        position.trading_mode.value,
                        position.strategy_version,
                        position.strategy_name,
                        position.quantity,
                        position.entry_price,
                        position.stop_price,
                        position.initial_stop_price,
                        position.opened_at,
                        position.updated_at,
                        position.highest_price,
                    ),
                    commit=False,
                )
```

- [ ] **Step 6: Add `PositionStore.update_highest_price()`**

Add immediately after `delete()` (after line 1157):

```python
    def update_highest_price(
        self,
        *,
        symbol: str,
        trading_mode: TradingMode,
        strategy_version: str,
        strategy_name: str,
        highest_price: float,
        commit: bool = True,
    ) -> None:
        execute(
            self._connection,
            """
            UPDATE positions
               SET highest_price = %s,
                   updated_at = NOW()
             WHERE symbol = %s
               AND trading_mode = %s
               AND strategy_version = %s
               AND strategy_name = %s
            """,
            (highest_price, symbol, trading_mode.value, strategy_version, strategy_name),
            commit=commit,
        )
```

- [ ] **Step 7: Run all repository tests to verify they pass**

```
pytest tests/unit/test_position_store_highest_price.py -v
```

Expected: all 8 tests PASSED

- [ ] **Step 8: Run full suite to verify no regressions**

```
pytest -x -q
```

Expected: all tests PASS

- [ ] **Step 9: Commit**

```bash
git add src/alpaca_bot/storage/repositories.py \
        tests/unit/test_position_store_highest_price.py
git commit -m "feat: persist highest_price in PositionStore (list_all, save, replace_all, update_highest_price)"
```

---

### Task 3: Supervisor — load fix + `_apply_highest_price_updates`

**Files:**
- Modify: `src/alpaca_bot/runtime/supervisor.py:1268` (one-line fix)
- Modify: `src/alpaca_bot/runtime/supervisor.py` (add `_apply_highest_price_updates` method)
- Modify: `src/alpaca_bot/runtime/supervisor.py:613-619` (call site in `run_cycle_once()`)
- Create: `tests/unit/test_supervisor_highest_price.py`

Note: `contextlib`, `replace` (from dataclasses), and `logger` are already imported at the top of `supervisor.py`.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_supervisor_highest_price.py`:

```python
from __future__ import annotations

import threading
from datetime import datetime, timezone
from importlib import import_module
from types import SimpleNamespace

import pytest

from alpaca_bot.config import TradingMode
from alpaca_bot.domain import Bar, OpenPosition
from alpaca_bot.storage.models import PositionRecord


def _make_settings(**overrides):
    from alpaca_bot.config import Settings
    base = {
        "TRADING_MODE": "paper",
        "ENABLE_LIVE_TRADING": "false",
        "STRATEGY_VERSION": "v1",
        "DATABASE_URL": "postgresql://x:y@localhost/db",
        "MARKET_DATA_FEED": "sip",
        "SYMBOLS": "AAPL",
        "DAILY_SMA_PERIOD": "20",
        "BREAKOUT_LOOKBACK_BARS": "20",
        "RELATIVE_VOLUME_LOOKBACK_BARS": "20",
        "RELATIVE_VOLUME_THRESHOLD": "1.5",
        "ENTRY_TIMEFRAME_MINUTES": "15",
        "RISK_PER_TRADE_PCT": "0.0025",
        "MAX_POSITION_PCT": "0.05",
        "MAX_OPEN_POSITIONS": "3",
        "DAILY_LOSS_LIMIT_PCT": "0.01",
        "STOP_LIMIT_BUFFER_PCT": "0.001",
        "BREAKOUT_STOP_BUFFER_PCT": "0.001",
        "ENTRY_STOP_PRICE_BUFFER": "0.01",
        "ENTRY_WINDOW_START": "10:00",
        "ENTRY_WINDOW_END": "15:30",
        "FLATTEN_TIME": "15:45",
        "PER_SYMBOL_LOSS_LIMIT_PCT": "0.0",
    }
    base.update(overrides)
    return Settings.from_env(base)


def _make_bar(high: float) -> Bar:
    return Bar(
        symbol="AAPL",
        timestamp=datetime(2026, 5, 1, 14, 0, tzinfo=timezone.utc),
        open=high - 0.10,
        high=high,
        low=high - 0.20,
        close=high - 0.05,
        volume=100_000,
        vwap=high - 0.05,
    )


def _make_position(highest_price: float = 3.00) -> OpenPosition:
    return OpenPosition(
        symbol="AAPL",
        entry_timestamp=datetime(2026, 5, 1, 10, 0, tzinfo=timezone.utc),
        entry_price=3.00,
        quantity=100.0,
        entry_level=2.97,
        initial_stop_price=2.97,
        stop_price=2.97,
        trailing_active=False,
        highest_price=highest_price,
        strategy_name="breakout",
    )


class _RecordingPositionStore:
    def __init__(self):
        self.update_calls: list[dict] = []

    def update_highest_price(self, **kwargs):
        self.update_calls.append(kwargs)


def _make_supervisor(settings, position_store=None):
    module = import_module("alpaca_bot.runtime.supervisor")
    RuntimeSupervisor = module.RuntimeSupervisor

    class _FakeRuntimeContext:
        connection = SimpleNamespace(commit=lambda: None, rollback=lambda: None)
        store_lock = None
        order_store = SimpleNamespace(
            save=lambda *a, **kw: None,
            list_by_status=lambda **kw: [],
            list_pending_submit=lambda **kw: [],
            daily_realized_pnl=lambda **kw: 0.0,
            daily_realized_pnl_by_symbol=lambda **kw: {},
        )
        strategy_weight_store = None
        option_order_store = None
        trading_status_store = SimpleNamespace(load=lambda **kw: None)
        position_store = position_store or _RecordingPositionStore()
        daily_session_state_store = SimpleNamespace(
            load=lambda **kw: None, save=lambda **kw: None, list_by_session=lambda **kw: []
        )
        audit_event_store = SimpleNamespace(
            append=lambda *a, **kw: None,
            load_latest=lambda **kw: None,
            list_recent=lambda **kw: [],
            list_by_event_types=lambda **kw: [],
        )
        strategy_flag_store = SimpleNamespace(list_all=lambda **kw: [], load=lambda **kw: None)
        watchlist_store = SimpleNamespace(list_enabled=lambda *a: ["AAPL"], list_ignored=lambda *a: [])

        def commit(self): pass

    return RuntimeSupervisor(
        settings=settings,
        runtime=_FakeRuntimeContext(),
        broker=SimpleNamespace(
            get_account=lambda: SimpleNamespace(equity=10_000.0, buying_power=20_000.0, trading_blocked=False),
            list_open_orders=lambda: [],
        ),
        market_data=SimpleNamespace(get_stock_bars=lambda **kw: {}, get_daily_bars=lambda **kw: {}),
        stream=None,
        close_runtime_fn=lambda _: None,
        connection_checker=lambda _: True,
        cycle_runner=lambda **kw: SimpleNamespace(intents=[]),
        cycle_intent_executor=lambda **kw: SimpleNamespace(submitted_exit_count=0, failed_exit_count=0),
        order_dispatcher=lambda **kw: {"submitted_count": 0},
    )


def test_apply_highest_price_updates_bar_high_exceeds_current():
    """When bar.high > position.highest_price: DB updated, returned list has new value."""
    settings = _make_settings()
    pstore = _RecordingPositionStore()
    supervisor, = [_make_supervisor(settings, pstore)]
    supervisor.runtime.position_store = pstore

    position = _make_position(highest_price=3.00)
    bars = {"AAPL": [_make_bar(high=3.20)]}

    result = supervisor._apply_highest_price_updates([position], bars)

    assert len(result) == 1
    assert result[0].highest_price == 3.20
    assert len(pstore.update_calls) == 1
    assert pstore.update_calls[0]["highest_price"] == 3.20
    assert pstore.update_calls[0]["symbol"] == "AAPL"


def test_apply_highest_price_updates_bar_high_equal_no_update():
    """When bar.high == position.highest_price: no DB call, position unchanged."""
    settings = _make_settings()
    pstore = _RecordingPositionStore()
    supervisor = _make_supervisor(settings, pstore)
    supervisor.runtime.position_store = pstore

    position = _make_position(highest_price=3.20)
    bars = {"AAPL": [_make_bar(high=3.20)]}

    result = supervisor._apply_highest_price_updates([position], bars)

    assert result[0].highest_price == 3.20
    assert pstore.update_calls == []


def test_apply_highest_price_updates_bar_high_lower_no_update():
    """When bar.high < position.highest_price: no DB call, position unchanged."""
    settings = _make_settings()
    pstore = _RecordingPositionStore()
    supervisor = _make_supervisor(settings, pstore)
    supervisor.runtime.position_store = pstore

    position = _make_position(highest_price=3.20)
    bars = {"AAPL": [_make_bar(high=3.09)]}

    result = supervisor._apply_highest_price_updates([position], bars)

    assert result[0].highest_price == 3.20
    assert pstore.update_calls == []


def test_apply_highest_price_updates_no_bars_skipped():
    """Position absent from bars dict: skipped, returned unchanged."""
    settings = _make_settings()
    pstore = _RecordingPositionStore()
    supervisor = _make_supervisor(settings, pstore)
    supervisor.runtime.position_store = pstore

    position = _make_position(highest_price=3.00)
    bars = {}  # no bars for AAPL

    result = supervisor._apply_highest_price_updates([position], bars)

    assert result[0].highest_price == 3.00
    assert pstore.update_calls == []


def test_apply_highest_price_updates_store_lock_held():
    """DB write must occur inside store_lock."""
    settings = _make_settings()
    pstore = _RecordingPositionStore()
    lock = threading.Lock()
    lock_acquired_during_update = []

    original_update = pstore.update_highest_price

    def recording_update(**kwargs):
        lock_acquired_during_update.append(not lock.acquire(blocking=False))
        if not lock_acquired_during_update[-1]:
            lock.release()
        original_update(**kwargs)

    pstore.update_highest_price = recording_update

    supervisor = _make_supervisor(settings, pstore)
    supervisor.runtime.position_store = pstore
    supervisor.runtime.store_lock = lock

    position = _make_position(highest_price=3.00)
    bars = {"AAPL": [_make_bar(high=3.20)]}
    supervisor._apply_highest_price_updates([position], bars)

    # Lock must have been held during the update call (lock.acquire returned False)
    assert lock_acquired_during_update == [True], "store_lock was not held during DB update"


def test_load_open_positions_uses_db_highest_price():
    """_load_open_positions() must use position.highest_price (or entry_price if None), not always entry_price."""
    settings = _make_settings()

    class _PositionStoreWithRecord:
        def list_all(self, **kwargs):
            from datetime import datetime, timezone
            return [
                PositionRecord(
                    symbol="AAPL",
                    trading_mode=TradingMode.PAPER,
                    strategy_version="v1",
                    quantity=10.0,
                    entry_price=3.00,
                    stop_price=2.97,
                    initial_stop_price=2.97,
                    opened_at=datetime(2026, 5, 1, tzinfo=timezone.utc),
                    highest_price=3.20,
                )
            ]

    supervisor = _make_supervisor(settings)
    supervisor.runtime.position_store = _PositionStoreWithRecord()

    positions = supervisor._load_open_positions()
    assert len(positions) == 1
    assert positions[0].highest_price == 3.20


def test_load_open_positions_null_highest_price_falls_back_to_entry_price():
    """When DB highest_price is NULL, fall back to entry_price."""
    settings = _make_settings()

    class _PositionStoreNullHighest:
        def list_all(self, **kwargs):
            from datetime import datetime, timezone
            return [
                PositionRecord(
                    symbol="AAPL",
                    trading_mode=TradingMode.PAPER,
                    strategy_version="v1",
                    quantity=10.0,
                    entry_price=3.00,
                    stop_price=2.97,
                    initial_stop_price=2.97,
                    opened_at=datetime(2026, 5, 1, tzinfo=timezone.utc),
                    highest_price=None,
                )
            ]

    supervisor = _make_supervisor(settings)
    supervisor.runtime.position_store = _PositionStoreNullHighest()

    positions = supervisor._load_open_positions()
    assert positions[0].highest_price == 3.00  # falls back to entry_price
```

- [ ] **Step 2: Run tests to verify they fail**

```
pytest tests/unit/test_supervisor_highest_price.py -v
```

Expected: 7 tests FAIL (AttributeError: `_apply_highest_price_updates` not found; assertion errors on `highest_price`)

- [ ] **Step 3: Fix `_load_open_positions()` (one-line change)**

In `src/alpaca_bot/runtime/supervisor.py` at line 1268, change:

```python
                highest_price=position.entry_price,
```

to:

```python
                highest_price=position.highest_price or position.entry_price,
```

- [ ] **Step 4: Add `_apply_highest_price_updates()` method**

Add this method to `RuntimeSupervisor`, just before `_load_open_positions()` (around line 1257):

```python
    def _apply_highest_price_updates(
        self,
        positions: list[OpenPosition],
        intraday_bars_by_symbol: dict,
    ) -> list[OpenPosition]:
        position_store = getattr(self.runtime, "position_store", None)
        update_fn = (
            getattr(position_store, "update_highest_price", None)
            if position_store is not None
            else None
        )
        store_lock = getattr(self.runtime, "store_lock", None)
        result = []
        for position in positions:
            bars = intraday_bars_by_symbol.get(position.symbol, ())
            if not bars:
                result.append(position)
                continue
            bar_high = bars[-1].high
            if bar_high <= position.highest_price:
                result.append(position)
                continue
            if update_fn is not None:
                try:
                    with store_lock if store_lock is not None else contextlib.nullcontext():
                        update_fn(
                            symbol=position.symbol,
                            trading_mode=self.settings.trading_mode,
                            strategy_version=self.settings.strategy_version,
                            strategy_name=position.strategy_name,
                            highest_price=bar_high,
                        )
                except Exception:
                    logger.warning(
                        "Failed to persist highest_price for %s; using in-memory value",
                        position.symbol,
                        exc_info=True,
                    )
            result.append(replace(position, highest_price=bar_high))
        return result
```

- [ ] **Step 5: Add call site in `run_cycle_once()`**

In `src/alpaca_bot/runtime/supervisor.py`, find the block after `intraday_bars_by_symbol` is assigned (around line 618). The current code reads:

```python
        intraday_bars_by_symbol = self.market_data.get_stock_bars(
            symbols=list(watchlist_symbols),
            start=timestamp - timedelta(days=5),
            end=timestamp,
            timeframe_minutes=self.settings.entry_timeframe_minutes,
        )
        daily_bars_end = datetime.combine(session_date, ...
```

Insert after the `get_stock_bars` assignment (immediately before `daily_bars_end`):

```python
        open_positions = self._apply_highest_price_updates(
            open_positions, intraday_bars_by_symbol
        )
```

Note: `open_positions` is already in scope at this point — it is loaded earlier in `run_cycle_once()` via `_load_open_positions()` before bars are fetched.

- [ ] **Step 6: Run supervisor tests to verify they pass**

```
pytest tests/unit/test_supervisor_highest_price.py -v
```

Expected: all 7 tests PASSED

- [ ] **Step 7: Run full suite**

```
pytest -x -q
```

Expected: all tests PASS

- [ ] **Step 8: Commit**

```bash
git add src/alpaca_bot/runtime/supervisor.py \
        tests/unit/test_supervisor_highest_price.py
git commit -m "feat: add _apply_highest_price_updates and fix _load_open_positions to use persisted highest_price"
```

---

### Task 4: Engine regression test

**Files:**
- Create: `tests/unit/test_cycle_engine_highest_price.py`

This test verifies the end-to-end correctness: when `highest_price` from a prior cycle exceeds the current bar's high, the trail stop is computed from the historical maximum — not from the current bar.

- [ ] **Step 1: Write the regression test**

Create `tests/unit/test_cycle_engine_highest_price.py`:

```python
from __future__ import annotations

from datetime import date, datetime, timezone

from alpaca_bot.config import Settings
from alpaca_bot.core.engine import CycleIntentType, evaluate_cycle
from alpaca_bot.domain import Bar, OpenPosition


def _make_settings(**overrides) -> Settings:
    base = {
        "TRADING_MODE": "paper",
        "ENABLE_LIVE_TRADING": "false",
        "STRATEGY_VERSION": "v1",
        "DATABASE_URL": "postgresql://x:y@localhost/db",
        "MARKET_DATA_FEED": "sip",
        "SYMBOLS": "AAPL",
        "DAILY_SMA_PERIOD": "20",
        "BREAKOUT_LOOKBACK_BARS": "20",
        "RELATIVE_VOLUME_LOOKBACK_BARS": "20",
        "RELATIVE_VOLUME_THRESHOLD": "1.5",
        "ENTRY_TIMEFRAME_MINUTES": "15",
        "RISK_PER_TRADE_PCT": "0.0025",
        "MAX_POSITION_PCT": "0.05",
        "MAX_OPEN_POSITIONS": "3",
        "DAILY_LOSS_LIMIT_PCT": "0.01",
        "STOP_LIMIT_BUFFER_PCT": "0.001",
        "BREAKOUT_STOP_BUFFER_PCT": "0.001",
        "ENTRY_STOP_PRICE_BUFFER": "0.01",
        "ENTRY_WINDOW_START": "09:30",
        "ENTRY_WINDOW_END": "15:30",
        "FLATTEN_TIME": "15:45",
        "PER_SYMBOL_LOSS_LIMIT_PCT": "0.0",
        "BREAKEVEN_TRAIL_PCT": "0.002",
        "BREAKEVEN_TRIGGER_PCT": "0.0025",
    }
    base.update(overrides)
    return Settings.from_env(base)


def _make_bar(symbol: str, high: float, ts: datetime | None = None) -> Bar:
    ts = ts or datetime(2026, 5, 1, 14, 0, tzinfo=timezone.utc)
    return Bar(
        symbol=symbol,
        timestamp=ts,
        open=high - 0.10,
        high=high,
        low=high - 0.20,
        close=high - 0.05,
        volume=500_000,
        vwap=high - 0.05,
    )


def test_breakeven_trail_uses_highest_price_not_current_bar():
    """
    Regression: the breakeven trail stop must be computed from max(highest_price, bar.high),
    where highest_price is the persisted historical maximum — NOT just the current bar.

    Setup:
      entry_price = 3.00
      highest_price = 3.20  (persisted from a prior cycle)
      current bar.high = 3.09  (retrace — below the historical max)

    Breakeven trail (0.2%):
      Correct:  3.20 * (1 - 0.002) = 3.1936
      Buggy:    3.09 * (1 - 0.002) = 3.0838

    The stop intent's stop_price must be >= 3.1936.
    """
    settings = _make_settings()
    entry_price = 3.00
    highest_price = 3.20  # from a prior cycle — retraced
    bar_high = 3.09

    position = OpenPosition(
        symbol="AAPL",
        entry_timestamp=datetime(2026, 5, 1, 10, 0, tzinfo=timezone.utc),
        entry_price=entry_price,
        quantity=100.0,
        entry_level=2.94,
        initial_stop_price=2.94,
        stop_price=2.94,
        trailing_active=False,
        highest_price=highest_price,
        strategy_name="breakout",
    )

    # Current bar has high=3.09, which is > entry * 1.0025 (breakeven trigger)
    current_bar = _make_bar("AAPL", high=bar_high)

    # Need enough historical bars to satisfy lookback; use flat bars below breakeven trigger
    historical_bar = _make_bar(
        "AAPL",
        high=2.98,
        ts=datetime(2026, 5, 1, 9, 30, tzinfo=timezone.utc),
    )

    # Daily bars: provide minimal data (no high-watermark hit needed)
    daily_bar = Bar(
        symbol="AAPL",
        timestamp=datetime(2026, 4, 30, tzinfo=timezone.utc),
        open=2.90, high=3.05, low=2.85, close=3.00,
        volume=1_000_000, vwap=2.95,
    )

    intraday_bars = {"AAPL": [historical_bar] * 19 + [current_bar]}
    daily_bars = {"AAPL": [daily_bar] * 60}

    now = datetime(2026, 5, 1, 14, 0, tzinfo=timezone.utc)
    session_date = date(2026, 5, 1)

    result = evaluate_cycle(
        settings=settings,
        timestamp=now,
        session_date=session_date,
        account_equity=10_000.0,
        open_positions=[position],
        intraday_bars_by_symbol=intraday_bars,
        daily_bars_by_symbol=daily_bars,
        entry_symbols=(),
        is_entry_window=True,
        is_extended=False,
    )

    update_intents = [i for i in result.intents if i.intent_type == CycleIntentType.UPDATE_STOP]
    assert len(update_intents) == 1, f"Expected 1 UPDATE_STOP intent, got {len(update_intents)}"

    stop = update_intents[0].stop_price
    correct_floor = round(highest_price * (1 - settings.breakeven_trail_pct), 2)
    buggy_ceiling = round(bar_high * (1 - settings.breakeven_trail_pct), 2)

    assert stop >= correct_floor, (
        f"Stop {stop:.4f} is below the correct trail floor {correct_floor:.4f} "
        f"(computed from highest_price={highest_price}). "
        f"Buggy value based on bar.high alone would be {buggy_ceiling:.4f}."
    )
    assert stop > buggy_ceiling, (
        f"Stop {stop:.4f} should exceed the buggy bar-only trail {buggy_ceiling:.4f}"
    )
```

- [ ] **Step 2: Run test to verify it fails (before supervisor fix)**

```
pytest tests/unit/test_cycle_engine_highest_price.py::test_breakeven_trail_uses_highest_price_not_current_bar -v
```

Expected: FAIL — stop is around 3.0838 (buggy), not 3.1936 (correct)

Note: If you run this test after Task 3 is already applied, the `evaluate_cycle()` function already reads `position.highest_price` — but the *supervisor* was previously not passing it correctly. Since this is a unit test of the pure engine, `position.highest_price=3.20` is passed directly, so the engine test should PASS even before Task 3 if the engine code already does `max(position.highest_price, latest_bar.high)`. Verify by checking `src/alpaca_bot/core/engine.py` around the breakeven trail computation. If the test passes here, it confirms the engine logic was never the bug — only the supervisor-level data flow was.

- [ ] **Step 3: Run the test after all supervisor changes are applied**

```
pytest tests/unit/test_cycle_engine_highest_price.py -v
pytest -x -q
```

Expected: PASSED; full suite green

- [ ] **Step 4: Commit**

```bash
git add tests/unit/test_cycle_engine_highest_price.py
git commit -m "test: add engine regression test for breakeven trail computed from highest_price"
```

---

## Self-Review

**Spec coverage check:**

| Spec requirement | Task covering it |
|---|---|
| Migration 017 adds nullable `highest_price NUMERIC DEFAULT NULL` | Task 1 Step 3 |
| `PositionRecord.highest_price: float \| None = None` | Task 1 Step 4 |
| `list_all()` SELECTs and populates `highest_price` | Task 2 Step 3 |
| `save()` includes `highest_price` in INSERT | Task 2 Step 4 |
| `save()` ON CONFLICT uses COALESCE to preserve existing high | Task 2 Step 4 |
| `replace_all()` includes `highest_price` | Task 2 Step 5 |
| `update_highest_price()` new targeted UPDATE method | Task 2 Step 6 |
| `_load_open_positions()` uses `position.highest_price or entry_price` | Task 3 Step 3 |
| `_apply_highest_price_updates()` new supervisor method | Task 3 Step 4 |
| Call site in `run_cycle_once()` after bars fetch | Task 3 Step 5 |
| Store-lock held during DB write | Task 3 Step 4 + test |
| Error handling: log warning on DB failure, continue with in-memory value | Task 3 Step 4 |
| Engine regression test: trail from `highest_price` not `bar.high` | Task 4 |

**No placeholders found.**

**Type consistency check:** `PositionRecord.highest_price: float | None = None` → `list_all()` returns `float(row[10]) if row[10] is not None else None` → supervisor reads `position.highest_price or position.entry_price` (handles None) → `OpenPosition.highest_price: float`. Consistent.

**`open_positions` availability at call site:** In `run_cycle_once()`, `open_positions = self._load_open_positions()` is called before `intraday_bars_by_symbol` is assigned (confirmed from the code read). The call site at Task 3 Step 5 inserts after the bars fetch, using the already-in-scope `open_positions` variable. Correct.
