# Multi-Strategy Trading — Implementation Plan

**Date**: 2026-04-25  
**Spec**: `docs/superpowers/specs/2026-04-25-multi-strategy-design.md`  
**Status**: Plan (v2)

---

## Execution Order

Task 1 → Task 2 → Task 3 → Task 4 → Task 5 → Task 6 → Task 7

Each task: write failing test first → run pytest (red) → implement → run pytest (green) → commit.

Regression gate after every task: `pytest tests/unit/ -q`

---

## Task 1 — Rename `BreakoutSignal` → `EntrySignal`

### 1a. Write failing test

**File**: `tests/unit/test_entry_signal.py`

```python
from __future__ import annotations

import pytest
from datetime import datetime, timezone

from alpaca_bot.domain.models import Bar, EntrySignal
from alpaca_bot.strategy import StrategySignalEvaluator


def _make_bar(symbol: str = "AAPL", high: float = 102.0, close: float = 101.5) -> Bar:
    return Bar(
        symbol=symbol,
        timestamp=datetime(2026, 1, 2, 14, 0, tzinfo=timezone.utc),
        open=100.0,
        high=high,
        low=99.0,
        close=close,
        volume=50000.0,
    )


def test_entry_signal_fields():
    sig = EntrySignal(
        symbol="AAPL",
        signal_bar=_make_bar(),
        entry_level=100.0,
        relative_volume=2.5,
        stop_price=102.1,
        limit_price=102.2,
        initial_stop_price=99.9,
    )
    assert sig.entry_level == 100.0
    assert sig.symbol == "AAPL"
    assert not hasattr(sig, "breakout_level")


def test_entry_signal_is_frozen():
    sig = EntrySignal(
        symbol="AAPL",
        signal_bar=_make_bar(),
        entry_level=100.0,
        relative_volume=2.5,
        stop_price=102.1,
        limit_price=102.2,
        initial_stop_price=99.9,
    )
    with pytest.raises(Exception):
        sig.entry_level = 99.0  # type: ignore[misc]


def test_strategy_registry_evaluator_protocol():
    from alpaca_bot.strategy import STRATEGY_REGISTRY, StrategySignalEvaluator
    for name, evaluator in STRATEGY_REGISTRY.items():
        assert isinstance(evaluator, StrategySignalEvaluator), (
            f"STRATEGY_REGISTRY[{name!r}] does not satisfy StrategySignalEvaluator Protocol"
        )


def test_breakout_evaluator_returns_entry_signal_type():
    from alpaca_bot.strategy.breakout import evaluate_breakout_signal
    from tests.helpers import make_settings  # or inline Settings
    # Just verify the return annotation is EntrySignal, not BreakoutSignal
    import inspect
    hints = inspect.get_annotations(evaluate_breakout_signal, eval_str=True)
    assert "EntrySignal" in str(hints.get("return", ""))
```

Run: `pytest tests/unit/test_entry_signal.py -q` → expect ImportError (EntrySignal not found).

### 1b. Implement

**File**: `src/alpaca_bot/domain/models.py`

Rename `BreakoutSignal` → `EntrySignal`; rename field `breakout_level` → `entry_level`:

```python
@dataclass(frozen=True)
class EntrySignal:
    symbol: str
    signal_bar: Bar
    entry_level: float      # was breakout_level
    relative_volume: float
    stop_price: float
    limit_price: float
    initial_stop_price: float
```

Rename `OpenPosition.breakout_level` → `OpenPosition.entry_level`:

```python
@dataclass
class OpenPosition:
    symbol: str
    entry_timestamp: datetime
    entry_price: float
    quantity: int
    entry_level: float          # was breakout_level
    initial_stop_price: float
    stop_price: float
    trailing_active: bool = False
    highest_price: float = 0.0
    strategy_name: str = "breakout"   # added here for Task 4 filtering

    @property
    def risk_per_share(self) -> float:
        return self.entry_price - self.initial_stop_price
```

Note: `strategy_name` on `OpenPosition` is added here so Task 4 can filter positions by strategy without a separate pass. Default is backward-compatible.

Rename `WorkingEntryOrder.breakout_level` → `WorkingEntryOrder.entry_level`:

```python
@dataclass
class WorkingEntryOrder:
    symbol: str
    signal_timestamp: datetime
    active_bar_timestamp: datetime
    stop_price: float
    limit_price: float
    initial_stop_price: float
    entry_level: float          # was breakout_level
    relative_volume: float
```

**File**: `src/alpaca_bot/domain/__init__.py`

```python
from alpaca_bot.domain.models import (
    Bar,
    EntrySignal,           # was BreakoutSignal
    OpenPosition,
    ReplayEvent,
    ReplayResult,
    ReplayScenario,
    WorkingEntryOrder,
)

__all__ = [
    "Bar",
    "EntrySignal",         # was BreakoutSignal
    "IntentType",
    "OpenPosition",
    "ReplayEvent",
    "ReplayResult",
    "ReplayScenario",
    "WorkingEntryOrder",
]
```

**File**: `src/alpaca_bot/strategy/__init__.py`

```python
from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from alpaca_bot.config import Settings
from alpaca_bot.domain.models import Bar, EntrySignal
from alpaca_bot.strategy.breakout import evaluate_breakout_signal


@runtime_checkable
class StrategySignalEvaluator(Protocol):
    def __call__(
        self,
        *,
        symbol: str,
        intraday_bars: Sequence[Bar],
        signal_index: int,
        daily_bars: Sequence[Bar],
        settings: Settings,
    ) -> EntrySignal | None: ...


STRATEGY_REGISTRY: dict[str, StrategySignalEvaluator] = {
    "breakout": evaluate_breakout_signal,
}
```

**File**: `src/alpaca_bot/strategy/breakout.py`

Change import from `BreakoutSignal` to `EntrySignal` and update the return statement:

```python
from alpaca_bot.domain.models import Bar, EntrySignal

# ... unchanged logic ...

return EntrySignal(
    symbol=symbol,
    signal_bar=signal_bar,
    entry_level=breakout_level,    # local variable name stays breakout_level; field is entry_level
    relative_volume=relative_volume,
    stop_price=stop_price,
    limit_price=limit_price,
    initial_stop_price=initial_stop_price,
)
```

**File**: `src/alpaca_bot/core/engine.py`

Update `signal.breakout_level` → `signal.entry_level` in scoring (line ~159):

```python
entry_candidates.append(
    (
        round((signal.signal_bar.close / signal.entry_level) - 1, 6),  # was signal.breakout_level
        round(signal.relative_volume, 6),
        CycleIntent(...)
    )
)
```

Update import annotation:

```python
from alpaca_bot.strategy import StrategySignalEvaluator
# The Protocol's return type is now EntrySignal | None — no direct import needed in engine
```

**File**: `src/alpaca_bot/runtime/supervisor.py`

In `_load_open_positions()`, rename `breakout_level=` → `entry_level=`:

```python
def _load_open_positions(self) -> list[OpenPosition]:
    return [
        OpenPosition(
            symbol=position.symbol,
            entry_timestamp=position.opened_at,
            entry_price=position.entry_price,
            quantity=position.quantity,
            entry_level=position.initial_stop_price,   # was breakout_level
            initial_stop_price=position.initial_stop_price,
            stop_price=position.stop_price,
            trailing_active=position.stop_price > position.initial_stop_price,
            highest_price=position.entry_price,
            strategy_name=getattr(position, "strategy_name", "breakout"),  # forward-compat
        )
        for position in self._load_position_records()
    ]
```

**File**: `src/alpaca_bot/replay/runner.py`

Update all `BreakoutSignal` → `EntrySignal` and `breakout_level` → `entry_level` references.

### 1c. Update existing tests

Search and replace `BreakoutSignal` → `EntrySignal` and `breakout_level` → `entry_level` in:

- `tests/unit/test_cycle_engine.py`
- `tests/unit/test_runtime_supervisor.py`
- `tests/unit/test_alpaca_execution.py`
- `tests/unit/test_replay_runner_engine_delegation.py`
- Any other test referencing these names

Run: `grep -r "BreakoutSignal\|breakout_level" tests/` to find all occurrences.

### 1d. Verify and commit

```bash
pytest tests/unit/ -q
git add -A
git commit -m "Rename BreakoutSignal → EntrySignal, breakout_level → entry_level"
```

---

## Task 2 — DB Migration + `strategy_name` on Storage Models

### 2a. Write failing test

**File**: `tests/unit/test_storage_strategy_name.py`

```python
from __future__ import annotations

from datetime import date, datetime, timezone
from types import SimpleNamespace

from alpaca_bot.config import TradingMode
from alpaca_bot.storage.models import DailySessionState, OrderRecord, PositionRecord
from alpaca_bot.storage.repositories import DailySessionStateStore, OrderStore, PositionStore


class FakeCursor:
    def __init__(self):
        self.rows = []
        self.executed = []

    def execute(self, sql, params=()):
        self.executed.append((sql, params))

    def fetchall(self):
        return self.rows

    def fetchone(self):
        return self.rows[0] if self.rows else None


class FakeConnection:
    def __init__(self):
        self._cursor = FakeCursor()

    def cursor(self):
        return self._cursor

    def commit(self):
        pass


def _make_position(strategy_name: str = "breakout") -> PositionRecord:
    return PositionRecord(
        symbol="AAPL",
        trading_mode=TradingMode.PAPER,
        strategy_version="v1",
        strategy_name=strategy_name,
        quantity=10,
        entry_price=150.0,
        stop_price=148.0,
        initial_stop_price=147.0,
        opened_at=datetime(2026, 1, 2, 14, tzinfo=timezone.utc),
    )


def test_position_record_has_strategy_name():
    pos = _make_position("momentum")
    assert pos.strategy_name == "momentum"


def test_position_record_default_strategy_name():
    pos = PositionRecord(
        symbol="AAPL",
        trading_mode=TradingMode.PAPER,
        strategy_version="v1",
        quantity=10,
        entry_price=150.0,
        stop_price=148.0,
        initial_stop_price=147.0,
        opened_at=datetime(2026, 1, 2, 14, tzinfo=timezone.utc),
    )
    assert pos.strategy_name == "breakout"


def test_order_record_has_strategy_name():
    order = OrderRecord(
        client_order_id="breakout:v1:2026-01-02:AAPL:entry:...",
        symbol="AAPL",
        side="buy",
        intent_type="entry",
        status="pending_submit",
        quantity=10,
        trading_mode=TradingMode.PAPER,
        strategy_version="v1",
        strategy_name="momentum",
    )
    assert order.strategy_name == "momentum"


def test_order_record_default_strategy_name():
    order = OrderRecord(
        client_order_id="v1:2026-01-02:AAPL:entry:...",
        symbol="AAPL",
        side="buy",
        intent_type="entry",
        status="pending_submit",
        quantity=10,
        trading_mode=TradingMode.PAPER,
        strategy_version="v1",
    )
    assert order.strategy_name == "breakout"


def test_daily_session_state_has_strategy_name():
    state = DailySessionState(
        session_date=date(2026, 1, 2),
        trading_mode=TradingMode.PAPER,
        strategy_version="v1",
        strategy_name="momentum",
        entries_disabled=False,
        flatten_complete=False,
    )
    assert state.strategy_name == "momentum"


def test_position_store_save_includes_strategy_name():
    conn = FakeConnection()
    store = PositionStore(conn)
    store.save(_make_position("momentum"))
    sql, params = conn._cursor.executed[0]
    assert "strategy_name" in sql
    assert "momentum" in params


def test_position_store_list_all_filters_by_strategy_name():
    conn = FakeConnection()
    conn._cursor.rows = []  # empty result
    store = PositionStore(conn)
    result = store.list_all(
        trading_mode=TradingMode.PAPER,
        strategy_version="v1",
        strategy_name="momentum",
    )
    sql, params = conn._cursor.executed[0]
    assert "strategy_name" in sql
    assert "momentum" in params
    assert result == []


def test_daily_session_state_store_load_filters_by_strategy_name():
    conn = FakeConnection()
    conn._cursor.rows = []
    store = DailySessionStateStore(conn)
    result = store.load(
        session_date=date(2026, 1, 2),
        trading_mode=TradingMode.PAPER,
        strategy_version="v1",
        strategy_name="momentum",
    )
    sql, params = conn._cursor.executed[0]
    assert "strategy_name" in sql
    assert "momentum" in params
    assert result is None


def test_daily_session_state_store_save_includes_strategy_name():
    conn = FakeConnection()
    store = DailySessionStateStore(conn)
    state = DailySessionState(
        session_date=date(2026, 1, 2),
        trading_mode=TradingMode.PAPER,
        strategy_version="v1",
        strategy_name="momentum",
        entries_disabled=False,
        flatten_complete=False,
    )
    store.save(state)
    sql, params = conn._cursor.executed[0]
    assert "strategy_name" in sql
    assert "momentum" in params
```

Run: `pytest tests/unit/test_storage_strategy_name.py -q` → red (PositionRecord lacks `strategy_name`).

### 2b. Create migration files

**File**: `migrations/006_add_strategy_name.sql`

```sql
-- Add strategy_name to orders (no PK change; client_order_id remains PK)
ALTER TABLE orders ADD COLUMN IF NOT EXISTS strategy_name TEXT NOT NULL DEFAULT 'breakout';

-- Add strategy_name to positions and change PK
ALTER TABLE positions ADD COLUMN IF NOT EXISTS strategy_name TEXT NOT NULL DEFAULT 'breakout';
ALTER TABLE positions DROP CONSTRAINT positions_pkey;
ALTER TABLE positions ADD PRIMARY KEY (symbol, trading_mode, strategy_version, strategy_name);

-- Add strategy_name to daily_session_state and change PK
ALTER TABLE daily_session_state ADD COLUMN IF NOT EXISTS strategy_name TEXT NOT NULL DEFAULT 'breakout';
ALTER TABLE daily_session_state DROP CONSTRAINT daily_session_state_pkey;
ALTER TABLE daily_session_state ADD PRIMARY KEY (session_date, trading_mode, strategy_version, strategy_name);
```

**File**: `migrations/006_add_strategy_name.down.sql`

```sql
-- Reverse: daily_session_state
ALTER TABLE daily_session_state DROP CONSTRAINT daily_session_state_pkey;
ALTER TABLE daily_session_state DROP COLUMN strategy_name;
ALTER TABLE daily_session_state ADD PRIMARY KEY (session_date, trading_mode, strategy_version);

-- Reverse: positions
ALTER TABLE positions DROP CONSTRAINT positions_pkey;
ALTER TABLE positions DROP COLUMN strategy_name;
ALTER TABLE positions ADD PRIMARY KEY (symbol, trading_mode, strategy_version);

-- Reverse: orders
ALTER TABLE orders DROP COLUMN strategy_name;
```

### 2c. Update storage models

**File**: `src/alpaca_bot/storage/models.py`

Add `strategy_name: str = "breakout"` to `OrderRecord`, `PositionRecord`, and `DailySessionState`:

```python
@dataclass(frozen=True)
class OrderRecord:
    client_order_id: str
    symbol: str
    side: str
    intent_type: str
    status: str
    quantity: int
    trading_mode: TradingMode
    strategy_version: str
    strategy_name: str = "breakout"             # NEW
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    stop_price: float | None = None
    limit_price: float | None = None
    initial_stop_price: float | None = None
    broker_order_id: str | None = None
    signal_timestamp: datetime | None = None
    fill_price: float | None = None
    filled_quantity: int | None = None


@dataclass(frozen=True)
class PositionRecord:
    symbol: str
    trading_mode: TradingMode
    strategy_version: str
    quantity: int
    entry_price: float
    stop_price: float
    initial_stop_price: float
    opened_at: datetime
    strategy_name: str = "breakout"             # NEW
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(frozen=True)
class DailySessionState:
    session_date: date
    trading_mode: TradingMode
    strategy_version: str
    entries_disabled: bool
    flatten_complete: bool
    strategy_name: str = "breakout"             # NEW
    last_reconciled_at: datetime | None = None
    notes: str | None = None
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
```

### 2d. Update storage repositories

**File**: `src/alpaca_bot/storage/repositories.py`

**`_ORDER_SELECT_COLUMNS`** — add `strategy_name` as the last column:

```python
_ORDER_SELECT_COLUMNS = """
    client_order_id,
    symbol,
    side,
    intent_type,
    status,
    quantity,
    trading_mode,
    strategy_version,
    created_at,
    updated_at,
    stop_price,
    limit_price,
    initial_stop_price,
    broker_order_id,
    signal_timestamp,
    fill_price,
    filled_quantity,
    strategy_name
"""
```

**`_row_to_order_record()`** — add `strategy_name=row[17]` (index 17):

```python
def _row_to_order_record(row: Any) -> OrderRecord:
    return OrderRecord(
        client_order_id=row[0],
        symbol=row[1],
        side=row[2],
        intent_type=row[3],
        status=row[4],
        quantity=int(row[5]),
        trading_mode=TradingMode(row[6]),
        strategy_version=row[7],
        created_at=row[8],
        updated_at=row[9],
        stop_price=float(row[10]) if row[10] is not None else None,
        limit_price=float(row[11]) if row[11] is not None else None,
        initial_stop_price=float(row[12]) if row[12] is not None else None,
        broker_order_id=row[13],
        signal_timestamp=row[14],
        fill_price=float(row[15]) if row[15] is not None else None,
        filled_quantity=int(row[16]) if row[16] is not None else None,
        strategy_name=row[17] if row[17] is not None else "breakout",  # NEW
    )
```

**`OrderStore.save()`** — add `strategy_name` column and parameter:

```python
def save(self, order: OrderRecord) -> None:
    execute(
        self._connection,
        """
        INSERT INTO orders (
            client_order_id, symbol, side, intent_type, status, quantity,
            trading_mode, strategy_version, strategy_name,
            stop_price, limit_price, initial_stop_price,
            broker_order_id, signal_timestamp, fill_price, filled_quantity,
            created_at, updated_at
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (client_order_id)
        DO UPDATE SET
            status = EXCLUDED.status,
            quantity = EXCLUDED.quantity,
            stop_price = EXCLUDED.stop_price,
            limit_price = EXCLUDED.limit_price,
            initial_stop_price = EXCLUDED.initial_stop_price,
            broker_order_id = EXCLUDED.broker_order_id,
            signal_timestamp = EXCLUDED.signal_timestamp,
            fill_price = EXCLUDED.fill_price,
            filled_quantity = EXCLUDED.filled_quantity,
            updated_at = EXCLUDED.updated_at
        """,
        (
            order.client_order_id, order.symbol, order.side, order.intent_type,
            order.status, order.quantity, order.trading_mode.value, order.strategy_version,
            order.strategy_name,
            order.stop_price, order.limit_price, order.initial_stop_price,
            order.broker_order_id, order.signal_timestamp, order.fill_price,
            order.filled_quantity, order.created_at, order.updated_at,
        ),
    )
```

**`OrderStore.list_by_status()`** — add optional `strategy_name` filter:

```python
def list_by_status(
    self,
    *,
    trading_mode: TradingMode,
    strategy_version: str,
    statuses: list[str],
    strategy_name: str | None = None,
) -> list[OrderRecord]:
    if not statuses:
        return []
    placeholders = ", ".join(["%s"] * len(statuses))
    strategy_clause = "AND strategy_name = %s" if strategy_name is not None else ""
    strategy_params = (strategy_name,) if strategy_name is not None else ()
    rows = fetch_all(
        self._connection,
        f"""
        SELECT {_ORDER_SELECT_COLUMNS}
        FROM orders
        WHERE trading_mode = %s
          AND strategy_version = %s
          AND status IN ({placeholders})
          {strategy_clause}
        ORDER BY created_at, client_order_id
        """,
        (trading_mode.value, strategy_version, *statuses, *strategy_params),
    )
    return [_row_to_order_record(row) for row in rows]
```

**`OrderStore.list_closed_trades()`** — add `strategy_name` to the SELECT and return dict, and filter correlated entry subqueries by strategy_name:

```python
def list_closed_trades(
    self,
    *,
    trading_mode: TradingMode,
    strategy_version: str,
    session_date: date,
    strategy_name: str | None = None,
) -> list[dict]:
    strategy_clause = "AND x.strategy_name = %s" if strategy_name is not None else ""
    strategy_params = (strategy_name,) if strategy_name is not None else ()
    rows = fetch_all(
        self._connection,
        f"""
        SELECT
            x.symbol,
            x.strategy_name,
            (
                SELECT e.fill_price
                FROM orders e
                WHERE e.symbol = x.symbol
                  AND e.trading_mode = x.trading_mode
                  AND e.strategy_version = x.strategy_version
                  AND e.strategy_name = x.strategy_name
                  AND e.intent_type = 'entry'
                  AND e.fill_price IS NOT NULL
                  AND DATE(e.updated_at AT TIME ZONE 'America/New_York') = %s
                ORDER BY e.updated_at DESC LIMIT 1
            ) AS entry_fill,
            (
                SELECT e.limit_price
                FROM orders e
                WHERE e.symbol = x.symbol
                  AND e.trading_mode = x.trading_mode
                  AND e.strategy_version = x.strategy_version
                  AND e.strategy_name = x.strategy_name
                  AND e.intent_type = 'entry'
                  AND e.fill_price IS NOT NULL
                  AND DATE(e.updated_at AT TIME ZONE 'America/New_York') = %s
                ORDER BY e.updated_at DESC LIMIT 1
            ) AS entry_limit,
            (
                SELECT e.updated_at
                FROM orders e
                WHERE e.symbol = x.symbol
                  AND e.trading_mode = x.trading_mode
                  AND e.strategy_version = x.strategy_version
                  AND e.strategy_name = x.strategy_name
                  AND e.intent_type = 'entry'
                  AND e.fill_price IS NOT NULL
                  AND DATE(e.updated_at AT TIME ZONE 'America/New_York') = %s
                ORDER BY e.updated_at DESC LIMIT 1
            ) AS entry_time,
            x.fill_price AS exit_fill,
            x.updated_at AS exit_time,
            COALESCE(x.filled_quantity, x.quantity) AS qty
        FROM orders x
        WHERE x.trading_mode = %s
          AND x.strategy_version = %s
          AND x.intent_type IN ('stop', 'exit')
          AND x.fill_price IS NOT NULL
          AND DATE(x.updated_at AT TIME ZONE 'America/New_York') = %s
          {strategy_clause}
        ORDER BY x.updated_at
        """,
        (
            session_date, session_date, session_date,
            trading_mode.value, strategy_version, session_date,
            *strategy_params,
        ),
    )
    return [
        {
            "symbol": row[0],
            "strategy_name": row[1],
            "entry_fill": float(row[2]) if row[2] is not None else None,
            "entry_limit": float(row[3]) if row[3] is not None else None,
            "entry_time": row[4],
            "exit_fill": float(row[5]) if row[5] is not None else None,
            "exit_time": row[6],
            "qty": int(row[7]),
        }
        for row in rows
        if row[2] is not None and row[5] is not None
    ]
```

**`PositionStore.save()`** — add `strategy_name`, update `ON CONFLICT`:

```python
def save(self, position: PositionRecord) -> None:
    execute(
        self._connection,
        """
        INSERT INTO positions (
            symbol, trading_mode, strategy_version, strategy_name,
            quantity, entry_price, stop_price, initial_stop_price,
            opened_at, updated_at
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (symbol, trading_mode, strategy_version, strategy_name)
        DO UPDATE SET
            quantity = EXCLUDED.quantity,
            entry_price = EXCLUDED.entry_price,
            stop_price = EXCLUDED.stop_price,
            initial_stop_price = EXCLUDED.initial_stop_price,
            opened_at = EXCLUDED.opened_at,
            updated_at = EXCLUDED.updated_at
        """,
        (
            position.symbol, position.trading_mode.value, position.strategy_version,
            position.strategy_name,
            position.quantity, position.entry_price, position.stop_price,
            position.initial_stop_price, position.opened_at, position.updated_at,
        ),
    )
```

**`PositionStore.replace_all()`** — add optional `strategy_name` filter on DELETE:

```python
def replace_all(
    self,
    *,
    positions: list[PositionRecord],
    trading_mode: TradingMode,
    strategy_version: str,
    strategy_name: str | None = None,
) -> None:
    if strategy_name is not None:
        execute(
            self._connection,
            "DELETE FROM positions WHERE trading_mode = %s AND strategy_version = %s AND strategy_name = %s",
            (trading_mode.value, strategy_version, strategy_name),
        )
    else:
        execute(
            self._connection,
            "DELETE FROM positions WHERE trading_mode = %s AND strategy_version = %s",
            (trading_mode.value, strategy_version),
        )
    for position in positions:
        self.save(position)
```

**`PositionStore.delete()`** — add `strategy_name` to WHERE:

```python
def delete(
    self,
    *,
    symbol: str,
    trading_mode: TradingMode,
    strategy_version: str,
    strategy_name: str = "breakout",
) -> None:
    execute(
        self._connection,
        """
        DELETE FROM positions
        WHERE symbol = %s AND trading_mode = %s AND strategy_version = %s AND strategy_name = %s
        """,
        (symbol, trading_mode.value, strategy_version, strategy_name),
    )
```

**`PositionStore.list_all()`** — add `strategy_name` column to SELECT and optional filter:

```python
def list_all(
    self,
    *,
    trading_mode: TradingMode,
    strategy_version: str,
    strategy_name: str | None = None,
) -> list[PositionRecord]:
    strategy_clause = "AND strategy_name = %s" if strategy_name is not None else ""
    strategy_params = (strategy_name,) if strategy_name is not None else ()
    cursor = self._connection.cursor()
    cursor.execute(
        f"""
        SELECT
            symbol, trading_mode, strategy_version, strategy_name,
            quantity, entry_price, stop_price, initial_stop_price,
            opened_at, updated_at
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
            quantity=int(row[4]),
            entry_price=float(row[5]),
            stop_price=float(row[6]),
            initial_stop_price=float(row[7]),
            opened_at=row[8],
            updated_at=row[9],
        )
        for row in rows
    ]
```

**`DailySessionStateStore.save()`** — add `strategy_name`, update `ON CONFLICT`:

```python
def save(self, state: DailySessionState) -> None:
    execute(
        self._connection,
        """
        INSERT INTO daily_session_state (
            session_date, trading_mode, strategy_version, strategy_name,
            entries_disabled, flatten_complete, last_reconciled_at, notes, updated_at
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (session_date, trading_mode, strategy_version, strategy_name)
        DO UPDATE SET
            entries_disabled = EXCLUDED.entries_disabled,
            flatten_complete = EXCLUDED.flatten_complete,
            last_reconciled_at = EXCLUDED.last_reconciled_at,
            notes = EXCLUDED.notes,
            updated_at = EXCLUDED.updated_at
        """,
        (
            state.session_date, state.trading_mode.value, state.strategy_version,
            state.strategy_name,
            state.entries_disabled, state.flatten_complete, state.last_reconciled_at,
            state.notes, state.updated_at,
        ),
    )
```

**`DailySessionStateStore.load()`** — add `strategy_name: str = "breakout"` parameter and filter:

```python
def load(
    self,
    *,
    session_date: Any,
    trading_mode: TradingMode,
    strategy_version: str,
    strategy_name: str = "breakout",
) -> DailySessionState | None:
    row = fetch_one(
        self._connection,
        """
        SELECT
            session_date, trading_mode, strategy_version, strategy_name,
            entries_disabled, flatten_complete, last_reconciled_at, notes, updated_at
        FROM daily_session_state
        WHERE session_date = %s AND trading_mode = %s AND strategy_version = %s
          AND strategy_name = %s
        """,
        (session_date, trading_mode.value, strategy_version, strategy_name),
    )
    if row is None:
        return None
    return DailySessionState(
        session_date=row[0],
        trading_mode=TradingMode(row[1]),
        strategy_version=row[2],
        strategy_name=row[3],
        entries_disabled=bool(row[4]),
        flatten_complete=bool(row[5]),
        last_reconciled_at=row[6],
        notes=row[7],
        updated_at=row[8],
    )
```

### 2e. Update storage/__init__.py

Ensure `PositionRecord` and `DailySessionState` are exported (they already are; verify the exports include new fields — no import change needed).

### 2f. Update existing storage tests

In `tests/unit/test_storage.py` (and any other storage tests using `FakeCursor`): update row tuples returned by `fetchall`/`fetchone` to include the new `strategy_name` column at the correct index.

### 2g. Update startup_recovery.py

**Grilling Q1 gap:** `recover_startup_state()` builds `local_positions_by_symbol` keyed by `symbol` alone (line 81). With two strategies holding the same symbol, the second position is silently dropped, causing the first strategy's data to overwrite the second. Also, reconstructed `PositionRecord` and `OrderRecord` objects are created without `strategy_name`, so every recovery cycle would attribute all positions to "breakout".

**File**: `src/alpaca_bot/runtime/startup_recovery.py`

Replace the `local_positions_by_symbol` dict and the `synced_positions` loop:

```python
# Group local positions by symbol; each symbol may have multiple strategy entries
local_positions_by_symbol: dict[str, list[PositionRecord]] = {}
for position in local_positions:
    local_positions_by_symbol.setdefault(position.symbol, []).append(position)

synced_positions: list[PositionRecord] = []
for broker_position in broker_open_positions:
    local_for_symbol = local_positions_by_symbol.get(broker_position.symbol, [])

    if not local_for_symbol:
        mismatches.append(f"broker position missing locally: {broker_position.symbol}")
        resolved_entry_price = broker_position.entry_price
        if resolved_entry_price is not None and resolved_entry_price != 0.0:
            stop_price = resolved_entry_price * (1 - settings.breakout_stop_buffer_pct)
            initial_stop_price = stop_price
        else:
            stop_price = 0.0
            initial_stop_price = 0.0
            runtime.audit_event_store.append(
                AuditEvent(
                    event_type="startup_recovery_missing_entry_price",
                    payload={"symbol": broker_position.symbol},
                    created_at=timestamp,
                )
            )
        synced_positions.append(
            PositionRecord(
                symbol=broker_position.symbol,
                trading_mode=settings.trading_mode,
                strategy_version=settings.strategy_version,
                strategy_name="breakout",  # fallback for broker-only positions
                quantity=broker_position.quantity,
                entry_price=broker_position.entry_price if broker_position.entry_price is not None else 0.0,
                stop_price=stop_price,
                initial_stop_price=initial_stop_price,
                opened_at=timestamp,
                updated_at=timestamp,
            )
        )
    elif len(local_for_symbol) == 1:
        existing = local_for_symbol[0]
        if existing.quantity != broker_position.quantity or (
            broker_position.entry_price is not None
            and round(existing.entry_price, 4) != round(broker_position.entry_price, 4)
        ):
            mismatches.append(f"broker position differs locally: {broker_position.symbol}")
        synced_positions.append(
            PositionRecord(
                symbol=broker_position.symbol,
                trading_mode=settings.trading_mode,
                strategy_version=settings.strategy_version,
                strategy_name=existing.strategy_name,  # preserve from local record
                quantity=broker_position.quantity,
                entry_price=broker_position.entry_price if broker_position.entry_price is not None else existing.entry_price,
                stop_price=existing.stop_price,
                initial_stop_price=existing.initial_stop_price,
                opened_at=existing.opened_at,
                updated_at=timestamp,
            )
        )
    else:
        # Multiple strategies hold this symbol simultaneously.
        # Broker reports aggregate qty; local records are per-strategy.
        total_local_qty = sum(p.quantity for p in local_for_symbol)
        if total_local_qty != broker_position.quantity:
            mismatches.append(f"broker position differs locally: {broker_position.symbol}")
        for existing in local_for_symbol:
            synced_positions.append(
                PositionRecord(
                    symbol=broker_position.symbol,
                    trading_mode=settings.trading_mode,
                    strategy_version=settings.strategy_version,
                    strategy_name=existing.strategy_name,  # preserve per-strategy attribution
                    quantity=existing.quantity,  # preserve per-strategy qty
                    entry_price=broker_position.entry_price if broker_position.entry_price is not None else existing.entry_price,
                    stop_price=existing.stop_price,
                    initial_stop_price=existing.initial_stop_price,
                    opened_at=existing.opened_at,
                    updated_at=timestamp,
                )
            )
```

Also replace the `local position missing at broker` loop — key it off `broker_positions_by_symbol` against each local entry:

```python
broker_positions_by_symbol = {position.symbol: position for position in broker_open_positions}

cleared_position_count = 0
seen_symbols_with_mismatch: set[str] = set()
for position in local_positions:
    if position.symbol not in broker_positions_by_symbol:
        if position.symbol not in seen_symbols_with_mismatch:
            mismatches.append(f"local position missing at broker: {position.symbol}")
            seen_symbols_with_mismatch.add(position.symbol)
        cleared_position_count += 1
```

For `OrderRecord` construction, add an `_infer_strategy_name_from_client_order_id()` helper and use it:

```python
def _infer_strategy_name_from_client_order_id(client_order_id: str) -> str:
    """Parse strategy_name from new-format client_order_id: {strategy}:{version}:..."""
    from alpaca_bot.strategy import STRATEGY_REGISTRY
    if not client_order_id:
        return "breakout"
    first_segment = client_order_id.split(":")[0]
    return first_segment if first_segment in STRATEGY_REGISTRY else "breakout"
```

In the `runtime.order_store.save(OrderRecord(...))` calls (both the "broker order synced" path and the "local order missing at broker / reconciled_missing" path), add `strategy_name`:

```python
# For broker orders:
strategy_name=(
    existing.strategy_name
    if existing is not None
    else _infer_strategy_name_from_client_order_id(broker_order.client_order_id)
),

# For reconciled_missing orders (preserves existing attribution):
strategy_name=order.strategy_name,
```

### 2h. Verify and commit

```bash
pytest tests/unit/ -q
git add -A
git commit -m "Add strategy_name to DB migration and storage layer"
```

---

## Task 3 — `CycleIntent.strategy_name` + `evaluate_cycle()` + `run_cycle()`

### 3a. Write failing test

**File**: `tests/unit/test_cycle_engine_multi_strategy.py`

```python
from __future__ import annotations

from datetime import datetime, timezone, date
from types import SimpleNamespace

from alpaca_bot.core.engine import CycleIntent, CycleIntentType, evaluate_cycle
from alpaca_bot.domain.models import Bar, OpenPosition


def _make_settings(
    symbols=("AAPL",),
    max_open_positions=3,
    entries_disabled=False,
):
    from alpaca_bot.config import Settings, TradingMode, MarketDataFeed
    from zoneinfo import ZoneInfo
    from datetime import time
    return Settings(
        trading_mode=TradingMode.PAPER,
        enable_live_trading=False,
        strategy_version="v1",
        database_url="postgresql://localhost/test",
        market_data_feed=MarketDataFeed.SIP,
        symbols=symbols,
        daily_sma_period=20,
        breakout_lookback_bars=20,
        relative_volume_lookback_bars=20,
        relative_volume_threshold=1.5,
        entry_timeframe_minutes=15,
        risk_per_trade_pct=0.01,
        max_position_pct=0.1,
        max_open_positions=max_open_positions,
        daily_loss_limit_pct=0.01,
        stop_limit_buffer_pct=0.001,
        breakout_stop_buffer_pct=0.001,
        entry_stop_price_buffer=0.01,
        entry_window_start=time(10, 0),
        entry_window_end=time(15, 30),
        flatten_time=time(15, 45),
    )


def test_cycle_intent_has_strategy_name():
    intent = CycleIntent(
        intent_type=CycleIntentType.EXIT,
        symbol="AAPL",
        timestamp=datetime(2026, 1, 2, 14, tzinfo=timezone.utc),
        strategy_name="momentum",
    )
    assert intent.strategy_name == "momentum"


def test_cycle_intent_default_strategy_name():
    intent = CycleIntent(
        intent_type=CycleIntentType.EXIT,
        symbol="AAPL",
        timestamp=datetime(2026, 1, 2, 14, tzinfo=timezone.utc),
    )
    assert intent.strategy_name == "breakout"


def test_evaluate_cycle_threads_strategy_name():
    settings = _make_settings()
    now = datetime(2026, 1, 2, 15, 50, tzinfo=timezone.utc)  # past flatten time

    open_positions = [
        OpenPosition(
            symbol="AAPL",
            entry_timestamp=datetime(2026, 1, 2, 10, tzinfo=timezone.utc),
            entry_price=150.0,
            quantity=10,
            entry_level=148.0,
            initial_stop_price=147.0,
            stop_price=147.0,
            strategy_name="momentum",
        )
    ]

    result = evaluate_cycle(
        settings=settings,
        now=now,
        equity=100_000.0,
        intraday_bars_by_symbol={"AAPL": [Bar(
            symbol="AAPL",
            timestamp=now,
            open=151.0, high=152.0, low=150.0, close=151.5, volume=10000.0,
        )]},
        daily_bars_by_symbol={"AAPL": []},
        open_positions=open_positions,
        working_order_symbols=set(),
        traded_symbols_today=set(),
        entries_disabled=True,
        strategy_name="momentum",
    )
    # All intents should carry strategy_name="momentum"
    for intent in result.intents:
        assert intent.strategy_name == "momentum"


def test_client_order_id_includes_strategy_name():
    from alpaca_bot.core.engine import _client_order_id
    from datetime import datetime, timezone

    settings = _make_settings()
    ts = datetime(2026, 1, 2, 14, 0, 0, tzinfo=timezone.utc)
    cid = _client_order_id(settings=settings, symbol="AAPL", signal_timestamp=ts, strategy_name="momentum")
    assert cid.startswith("momentum:")


def test_run_cycle_writes_order_with_strategy_name():
    from alpaca_bot.runtime.cycle import run_cycle

    saved_orders = []

    def fake_save(order):
        saved_orders.append(order)

    settings = _make_settings()
    now = datetime(2026, 1, 2, 14, 0, tzinfo=timezone.utc)

    # signal evaluator that always fires
    from alpaca_bot.domain.models import Bar, EntrySignal
    from datetime import time
    signal_bar = Bar(symbol="AAPL", timestamp=now, open=100.0, high=105.0, low=99.0, close=104.0, volume=100_000.0)

    def fake_evaluator(*, symbol, intraday_bars, signal_index, daily_bars, settings):
        return EntrySignal(
            symbol=symbol,
            signal_bar=signal_bar,
            entry_level=100.0,
            relative_volume=3.0,
            stop_price=99.5,
            limit_price=99.6,
            initial_stop_price=98.0,
        )

    runtime = SimpleNamespace(
        order_store=SimpleNamespace(save=fake_save),
        audit_event_store=SimpleNamespace(append=lambda _: None),
    )

    run_cycle(
        settings=settings,
        runtime=runtime,
        now=now,
        equity=100_000.0,
        intraday_bars_by_symbol={"AAPL": [signal_bar] * 25},
        daily_bars_by_symbol={"AAPL": [signal_bar] * 25},
        open_positions=[],
        working_order_symbols=set(),
        traded_symbols_today=set(),
        entries_disabled=False,
        strategy_name="momentum",
    )
    assert len(saved_orders) == 1
    assert saved_orders[0].strategy_name == "momentum"
```

Run: `pytest tests/unit/test_cycle_engine_multi_strategy.py -q` → red.

### 3b. Implement

**File**: `src/alpaca_bot/core/engine.py`

Add `strategy_name: str = "breakout"` field to `CycleIntent`:

```python
@dataclass(frozen=True)
class CycleIntent:
    intent_type: CycleIntentType
    symbol: str
    timestamp: datetime
    quantity: int | None = None
    stop_price: float | None = None
    limit_price: float | None = None
    initial_stop_price: float | None = None
    client_order_id: str | None = None
    reason: str | None = None
    signal_timestamp: datetime | None = None
    strategy_name: str = "breakout"             # NEW
```

Add `strategy_name: str = "breakout"` parameter to `evaluate_cycle()`:

```python
def evaluate_cycle(
    *,
    settings: Settings,
    now: datetime,
    equity: float,
    intraday_bars_by_symbol: Mapping[str, Sequence[Bar]],
    daily_bars_by_symbol: Mapping[str, Sequence[Bar]],
    open_positions: Sequence[OpenPosition],
    working_order_symbols: set[str],
    traded_symbols_today: set[tuple[str, date]],
    entries_disabled: bool,
    flatten_all: bool = False,
    signal_evaluator: StrategySignalEvaluator | None = None,
    session_state: "DailySessionState | None" = None,
    strategy_name: str = "breakout",            # NEW
) -> CycleResult:
```

Thread `strategy_name` into every `CycleIntent` produced. In the `flatten_all` branch:

```python
CycleIntent(
    intent_type=CycleIntentType.EXIT,
    symbol=position.symbol,
    timestamp=now,
    reason="loss_limit_flatten",
    strategy_name=strategy_name,               # NEW
)
```

In the EOD flatten branch:

```python
CycleIntent(
    intent_type=CycleIntentType.EXIT,
    symbol=position.symbol,
    timestamp=latest_bar.timestamp,
    reason="eod_flatten",
    strategy_name=strategy_name,               # NEW
)
```

In the UPDATE_STOP intent:

```python
CycleIntent(
    intent_type=CycleIntentType.UPDATE_STOP,
    symbol=position.symbol,
    timestamp=latest_bar.timestamp,
    stop_price=new_stop,
    strategy_name=strategy_name,               # NEW
)
```

In the ENTRY intent:

```python
CycleIntent(
    intent_type=CycleIntentType.ENTRY,
    symbol=symbol,
    timestamp=signal.signal_bar.timestamp,
    quantity=quantity,
    stop_price=signal.stop_price,
    limit_price=signal.limit_price,
    initial_stop_price=signal.initial_stop_price,
    client_order_id=_client_order_id(
        settings=settings,
        symbol=symbol,
        signal_timestamp=signal.signal_bar.timestamp,
        strategy_name=strategy_name,           # NEW
    ),
    signal_timestamp=signal.signal_bar.timestamp,
    strategy_name=strategy_name,               # NEW
)
```

Update `_client_order_id()` to accept and embed `strategy_name`:

```python
def _client_order_id(
    *,
    settings: Settings,
    symbol: str,
    signal_timestamp: datetime,
    strategy_name: str = "breakout",           # NEW
) -> str:
    return (
        f"{strategy_name}:"                    # NEW prefix
        f"{settings.strategy_version}:"
        f"{signal_timestamp.date().isoformat()}:"
        f"{symbol}:entry:{signal_timestamp.isoformat()}"
    )
```

**File**: `src/alpaca_bot/runtime/cycle.py`

Add `strategy_name: str = "breakout"` to `run_cycle()`, pass to `evaluate_cycle()`, and include in `OrderRecord.save()`:

```python
def run_cycle(
    *,
    settings: Settings,
    runtime: RuntimeProtocol,
    now: datetime,
    equity: float,
    intraday_bars_by_symbol: Mapping[str, Sequence[Bar]],
    daily_bars_by_symbol: Mapping[str, Sequence[Bar]],
    open_positions: Sequence[OpenPosition],
    working_order_symbols: set[str],
    traded_symbols_today: set[tuple[str, date]],
    entries_disabled: bool,
    flatten_all: bool = False,
    session_state: DailySessionState | None = None,
    signal_evaluator: StrategySignalEvaluator | None = None,
    strategy_name: str = "breakout",           # NEW
) -> CycleResult:
    result = evaluate_cycle(
        settings=settings,
        now=now,
        equity=equity,
        intraday_bars_by_symbol=intraday_bars_by_symbol,
        daily_bars_by_symbol=daily_bars_by_symbol,
        open_positions=open_positions,
        working_order_symbols=working_order_symbols,
        traded_symbols_today=traded_symbols_today,
        entries_disabled=entries_disabled,
        flatten_all=flatten_all,
        session_state=session_state,
        signal_evaluator=signal_evaluator,
        strategy_name=strategy_name,           # NEW
    )

    for intent in result.intents:
        if intent.intent_type is not CycleIntentType.ENTRY:
            continue
        runtime.order_store.save(
            OrderRecord(
                client_order_id=intent.client_order_id or "",
                symbol=intent.symbol,
                side="buy",
                intent_type=intent.intent_type.value,
                status="pending_submit",
                quantity=intent.quantity or 0,
                trading_mode=settings.trading_mode,
                strategy_version=settings.strategy_version,
                strategy_name=strategy_name,   # NEW
                created_at=now,
                updated_at=now,
                stop_price=intent.stop_price,
                limit_price=intent.limit_price,
                initial_stop_price=intent.initial_stop_price,
                signal_timestamp=intent.signal_timestamp,
            )
        )

    runtime.audit_event_store.append(
        AuditEvent(
            event_type="decision_cycle_completed",
            payload={
                "trading_mode": settings.trading_mode.value,
                "strategy_version": settings.strategy_version,
                "strategy_name": strategy_name,    # NEW
                "intent_count": len(result.intents),
                "intent_types": [intent.intent_type.value for intent in result.intents],
                "cycle_timestamp": now.isoformat(),
            },
            created_at=now,
        )
    )

    return result
```

### 3c. Update cycle_intent_execution.py

**Grilling Q3 gap:** `_execute_update_stop()` and `_execute_exit()` create `OrderRecord`/`PositionRecord` without `strategy_name`. After Task 2 adds the default `"breakout"`, all momentum stop replacements and exit orders would be silently attributed to breakout. Worse, `_active_stop_orders()` and `_positions_by_symbol()` have no strategy_name filter — a momentum EXIT intent for AAPL would cancel breakout's stop on AAPL.

**File**: `src/alpaca_bot/runtime/cycle_intent_execution.py`

Update `_positions_by_symbol()` to key by `(symbol, strategy_name)`:

```python
def _positions_by_symbol(
    runtime: RuntimeProtocol, settings: Settings
) -> dict[tuple[str, str], PositionRecord]:
    positions = runtime.position_store.list_all(
        trading_mode=settings.trading_mode,
        strategy_version=settings.strategy_version,
    )
    return {(p.symbol, getattr(p, "strategy_name", "breakout")): p for p in positions}
```

Update `_active_stop_orders()` and `_latest_active_stop_order()` to accept and filter by `strategy_name`:

```python
def _active_stop_orders(
    runtime: RuntimeProtocol,
    settings: Settings,
    symbol: str,
    strategy_name: str = "breakout",
) -> list[OrderRecord]:
    orders = runtime.order_store.list_by_status(
        trading_mode=settings.trading_mode,
        strategy_version=settings.strategy_version,
        statuses=list(ACTIVE_STOP_STATUSES),
        strategy_name=strategy_name,
    )
    return [o for o in orders if o.symbol == symbol and o.intent_type == "stop" and o.side == "sell"]


def _latest_active_stop_order(
    runtime: RuntimeProtocol, settings: Settings, symbol: str, strategy_name: str = "breakout"
) -> OrderRecord | None:
    orders = _active_stop_orders(runtime, settings, symbol, strategy_name)
    if not orders:
        return None
    return max(orders, key=lambda o: (o.updated_at, o.created_at, o.client_order_id))
```

In `execute_cycle_intents()`, extract `strategy_name` from each intent and thread it through:

```python
strategy_name = getattr(intent, "strategy_name", "breakout")   # NEW

# UPDATE_STOP path:
position=positions_by_symbol.get((symbol, strategy_name)),     # keyed by (symbol, strategy_name)
...
_execute_update_stop(..., strategy_name=strategy_name)

# EXIT path:
position=positions_by_symbol.get((symbol, strategy_name)),     # keyed by (symbol, strategy_name)
...
_execute_exit(..., strategy_name=strategy_name)
```

Add `strategy_name: str = "breakout"` parameter to both `_execute_update_stop()` and `_execute_exit()`. All `OrderRecord(...)` and `PositionRecord(...)` constructed inside them must include `strategy_name=strategy_name`.

For the duplicate-exit guard in `_execute_exit()`, scope the `list_by_status()` call to the strategy:

```python
active_exit_orders = runtime.order_store.list_by_status(
    trading_mode=settings.trading_mode,
    strategy_version=settings.strategy_version,
    statuses=list(ACTIVE_STOP_STATUSES),
    strategy_name=strategy_name,   # scope to this strategy
)
```

**Regression test** — add to `tests/unit/test_cycle_engine_multi_strategy.py`:

```python
def test_exit_intent_does_not_cancel_other_strategy_stop():
    """A momentum EXIT intent must NOT cancel breakout's stop on the same symbol."""
    from alpaca_bot.runtime.cycle_intent_execution import execute_cycle_intents
    from alpaca_bot.core.engine import CycleIntent, CycleIntentType
    from alpaca_bot.storage.models import OrderRecord, PositionRecord
    from alpaca_bot.config import TradingMode
    from types import SimpleNamespace

    now = datetime(2026, 1, 2, 14, 0, tzinfo=timezone.utc)
    settings = _make_settings()

    breakout_stop = OrderRecord(
        client_order_id="breakout:v1:2026-01-02:AAPL:stop:t",
        symbol="AAPL", side="sell", intent_type="stop", status="new",
        quantity=10, trading_mode=TradingMode.PAPER, strategy_version="v1",
        strategy_name="breakout", broker_order_id="broker-breakout-1",
    )
    momentum_stop = OrderRecord(
        client_order_id="momentum:v1:2026-01-02:AAPL:stop:t",
        symbol="AAPL", side="sell", intent_type="stop", status="new",
        quantity=5, trading_mode=TradingMode.PAPER, strategy_version="v1",
        strategy_name="momentum", broker_order_id="broker-momentum-1",
    )
    momentum_position = PositionRecord(
        symbol="AAPL", trading_mode=TradingMode.PAPER, strategy_version="v1",
        strategy_name="momentum", quantity=5, entry_price=150.0,
        stop_price=148.0, initial_stop_price=147.0,
        opened_at=now,
    )

    canceled_ids = []

    def fake_list_by_status(*, trading_mode, strategy_version, statuses, strategy_name=None):
        orders = [breakout_stop, momentum_stop]
        if strategy_name is not None:
            orders = [o for o in orders if o.strategy_name == strategy_name]
        return [o for o in orders if o.status in statuses]

    runtime = SimpleNamespace(
        order_store=SimpleNamespace(
            list_by_status=fake_list_by_status,
            save=lambda _: None,
        ),
        position_store=SimpleNamespace(
            list_all=lambda **_: [momentum_position],
            save=lambda _: None,
        ),
        audit_event_store=SimpleNamespace(append=lambda _: None),
    )
    fake_broker = SimpleNamespace(
        cancel_order=lambda order_id: canceled_ids.append(order_id),
        submit_market_exit=lambda **kw: SimpleNamespace(
            status="pending_new", broker_order_id="exit-1", quantity=kw["quantity"]
        ),
    )

    execute_cycle_intents(
        settings=settings,
        runtime=runtime,
        broker=fake_broker,
        cycle_result=SimpleNamespace(intents=[
            CycleIntent(
                intent_type=CycleIntentType.EXIT,
                symbol="AAPL",
                timestamp=now,
                strategy_name="momentum",
            )
        ]),
        now=now,
    )

    assert "broker-momentum-1" in canceled_ids
    assert "broker-breakout-1" not in canceled_ids  # breakout stop must survive
    assert "broker-breakout-1" not in canceled_ids, "EXIT for momentum must NOT cancel breakout stop"
```

### 3d. Update existing tests

In `tests/unit/test_cycle_engine.py`: the `CycleIntent` constructor calls that don't pass `strategy_name` will still work because of the default. Assertions that check `client_order_id` format need to be updated to expect the new `{strategy_name}:{strategy_version}:...` prefix.

In `tests/unit/test_trader_entrypoint.py` and `tests/unit/test_runtime_supervisor.py`: `run_cycle` calls without `strategy_name` still work because of the default.

### 3e. Verify and commit

```bash
pytest tests/unit/ -q
git add -A
git commit -m "Add strategy_name to CycleIntent, evaluate_cycle, and run_cycle"
```

---

## Task 4 — Supervisor Fan-out

### 4a. Write failing test

**File**: `tests/unit/test_runtime_supervisor_multi_strategy.py`

```python
from __future__ import annotations

from datetime import date, datetime, timezone
from types import SimpleNamespace

from alpaca_bot.config import TradingMode
from alpaca_bot.domain.models import OpenPosition
from alpaca_bot.runtime.supervisor import RuntimeSupervisor
from alpaca_bot.storage.models import StrategyFlag


def _make_settings(symbols=("AAPL",)):
    from alpaca_bot.config import Settings, TradingMode, MarketDataFeed
    from datetime import time
    return Settings(
        trading_mode=TradingMode.PAPER,
        enable_live_trading=False,
        strategy_version="v1",
        database_url="postgresql://localhost/test",
        market_data_feed=MarketDataFeed.SIP,
        symbols=symbols,
        daily_sma_period=20,
        breakout_lookback_bars=20,
        relative_volume_lookback_bars=20,
        relative_volume_threshold=1.5,
        entry_timeframe_minutes=15,
        risk_per_trade_pct=0.01,
        max_position_pct=0.1,
        max_open_positions=3,
        daily_loss_limit_pct=0.01,
        stop_limit_buffer_pct=0.001,
        breakout_stop_buffer_pct=0.001,
        entry_stop_price_buffer=0.01,
        entry_window_start=time(10, 0),
        entry_window_end=time(15, 30),
        flatten_time=time(15, 45),
    )


def _make_runtime(flags=None):
    return SimpleNamespace(
        connection=SimpleNamespace(),
        trading_status_store=SimpleNamespace(load=lambda **_: None),
        order_store=SimpleNamespace(
            save=lambda _: None,
            list_by_status=lambda **_: [],
            list_pending_submit=lambda **_: [],
            daily_realized_pnl=lambda **_: 0.0,
        ),
        daily_session_state_store=SimpleNamespace(
            load=lambda **_: None,
            save=lambda _: None,
        ),
        position_store=SimpleNamespace(list_all=lambda **_: []),
        audit_event_store=SimpleNamespace(append=lambda _: None),
        strategy_flag_store=SimpleNamespace(
            load=lambda **_: None,
            list_all=lambda **_: flags or [],
        ),
    )


def _make_supervisor(runtime=None, cycle_calls=None):
    settings = _make_settings()
    if runtime is None:
        runtime = _make_runtime()
    if cycle_calls is None:
        cycle_calls = []

    def fake_cycle_runner(**kwargs):
        cycle_calls.append(kwargs)
        return SimpleNamespace(intents=[])

    return RuntimeSupervisor(
        settings=settings,
        runtime=runtime,
        broker=SimpleNamespace(
            get_account=lambda: SimpleNamespace(equity=100_000.0),
            get_open_orders=lambda: [],
            get_open_positions=lambda: [],
            get_clock=lambda: SimpleNamespace(is_open=False),
        ),
        market_data=SimpleNamespace(
            get_stock_bars=lambda **_: {},
            get_daily_bars=lambda **_: {},
        ),
        stream=SimpleNamespace(
            subscribe_trade_updates=lambda _: None,
            run=lambda: None,
            stop=lambda: None,
        ),
        cycle_runner=fake_cycle_runner,
        order_dispatcher=lambda **_: {"submitted_count": 0},
        cycle_intent_executor=lambda **_: None,
        close_runtime_fn=lambda _: None,
        connection_checker=lambda _: True,
    ), cycle_calls


def test_resolve_active_strategies_both_enabled():
    supervisor, _ = _make_supervisor()
    # No flags stored → both enabled (missing row = enabled)
    active = supervisor._resolve_active_strategies()
    strategy_names = [name for name, _ in active]
    assert "breakout" in strategy_names


def test_resolve_active_strategies_one_disabled():
    runtime = _make_runtime(flags=[
        StrategyFlag(
            strategy_name="breakout",
            trading_mode=TradingMode.PAPER,
            strategy_version="v1",
            enabled=False,
            updated_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
        )
    ])
    # Override load to return the flag
    def fake_load(*, strategy_name, trading_mode, strategy_version):
        for f in runtime.strategy_flag_store.list_all():
            if f.strategy_name == strategy_name:
                return f
        return None
    runtime.strategy_flag_store.load = fake_load

    supervisor, _ = _make_supervisor(runtime=runtime)
    active = supervisor._resolve_active_strategies()
    strategy_names = [name for name, _ in active]
    assert "breakout" not in strategy_names


def test_run_cycle_once_calls_cycle_runner_per_strategy(monkeypatch):
    """Supervisor calls cycle_runner once per active strategy."""
    import alpaca_bot.strategy as strategy_mod
    # Temporarily add momentum to registry
    from alpaca_bot.strategy.breakout import evaluate_breakout_signal
    original_registry = dict(strategy_mod.STRATEGY_REGISTRY)
    strategy_mod.STRATEGY_REGISTRY["momentum"] = evaluate_breakout_signal
    try:
        supervisor, cycle_calls = _make_supervisor()
        supervisor.run_cycle_once(now=lambda: datetime(2026, 1, 2, 11, tzinfo=timezone.utc))
        # market is closed (broker returns is_open=False) so no cycle runs
        # Reset broker to simulate open market
    finally:
        strategy_mod.STRATEGY_REGISTRY.clear()
        strategy_mod.STRATEGY_REGISTRY.update(original_registry)


def test_cycle_runner_receives_strategy_name():
    cycle_calls = []
    supervisor, _ = _make_supervisor(cycle_calls=cycle_calls)
    # Patch market to open
    supervisor.broker = SimpleNamespace(
        get_account=lambda: SimpleNamespace(equity=100_000.0),
        get_open_orders=lambda: [],
        get_open_positions=lambda: [],
        get_clock=lambda: SimpleNamespace(is_open=True),
    )
    supervisor.run_cycle_once(now=lambda: datetime(2026, 1, 2, 11, tzinfo=timezone.utc))
    assert len(cycle_calls) >= 1
    for call in cycle_calls:
        assert "strategy_name" in call
```

Run: `pytest tests/unit/test_runtime_supervisor_multi_strategy.py -q` → red (`_resolve_active_strategies` does not exist, `run_cycle_once` doesn't pass `strategy_name`).

### 4b. Implement

**File**: `src/alpaca_bot/runtime/supervisor.py`

Replace `_resolve_signal_evaluator()` with `_resolve_active_strategies()`:

```python
def _resolve_active_strategies(self) -> list[tuple[str, StrategySignalEvaluator]]:
    """Return (strategy_name, evaluator) for every enabled strategy."""
    store = getattr(self.runtime, "strategy_flag_store", None)
    active = []
    for name, evaluator in STRATEGY_REGISTRY.items():
        if store is not None:
            flag = store.load(
                strategy_name=name,
                trading_mode=self.settings.trading_mode,
                strategy_version=self.settings.strategy_version,
            )
            if flag is not None and not flag.enabled:
                continue
        active.append((name, evaluator))
    if not active:
        active.append(("breakout", _default_evaluator))
    return active
```

Restructure `run_cycle_once()` to fan-out per strategy. Replace the section from `signal_evaluator = self._resolve_signal_evaluator()` through the dispatch call with:

```python
active_strategies = self._resolve_active_strategies()
all_cycle_results = []

for strategy_name, evaluator in active_strategies:
    # Filter inputs to this strategy
    strategy_positions = [
        p for p in open_positions
        if getattr(p, "strategy_name", "breakout") == strategy_name
    ]
    strategy_working_symbols = self._working_symbols_for_strategy(
        strategy_name=strategy_name,
        broker_open_orders=broker_open_orders,
    )
    strategy_traded_symbols = self._load_traded_symbols(
        session_date=session_date,
        strategy_name=strategy_name,
    )
    strategy_session_state = self._load_session_state(
        session_date=session_date,
        strategy_name=strategy_name,
    )
    if strategy_session_state is not None and strategy_session_state.session_date != session_date:
        strategy_session_state = None

    strategy_entries_disabled = (
        entries_disabled
        or (strategy_session_state is not None and strategy_session_state.entries_disabled)
    )

    cycle_result = self._cycle_runner(
        settings=self.settings,
        runtime=self.runtime,
        now=timestamp,
        equity=account.equity,
        intraday_bars_by_symbol=intraday_bars_by_symbol,
        daily_bars_by_symbol=daily_bars_by_symbol,
        open_positions=strategy_positions,
        working_order_symbols=strategy_working_symbols,
        traded_symbols_today=strategy_traded_symbols,
        entries_disabled=strategy_entries_disabled,
        flatten_all=daily_loss_limit_breached,
        session_state=strategy_session_state,
        signal_evaluator=evaluator,
        strategy_name=strategy_name,
    )
    all_cycle_results.append((strategy_name, cycle_result))

    # Handle session state writes per strategy
    has_flatten_intents = any(
        getattr(intent, "reason", None) in {"eod_flatten", "loss_limit_flatten"}
        for intent in getattr(cycle_result, "intents", [])
    )
    if has_flatten_intents and self.runtime.daily_session_state_store is not None and hasattr(
        self.runtime.daily_session_state_store, "save"
    ):
        self.runtime.daily_session_state_store.save(
            DailySessionState(
                session_date=session_date,
                trading_mode=self.settings.trading_mode,
                strategy_version=self.settings.strategy_version,
                strategy_name=strategy_name,       # per-strategy
                entries_disabled=True,
                flatten_complete=True,
                updated_at=timestamp,
            )
        )

    if status is not TradingStatusValue.HALTED:
        self._cycle_intent_executor(
            settings=self.settings,
            runtime=self.runtime,
            broker=self.broker,
            cycle_result=cycle_result,
            now=timestamp,
        )

# Use the last cycle_result for the report (or first non-empty)
cycle_result = all_cycle_results[-1][1] if all_cycle_results else SimpleNamespace(intents=[])
```

Remove the old `has_flatten_intents` block that follows (now inside the loop).

Add `_working_symbols_for_strategy()` helper:

```python
def _working_symbols_for_strategy(
    self,
    *,
    strategy_name: str,
    broker_open_orders: list,
) -> set[str]:
    """Return symbols with working orders for this strategy.

    Checks pending_submit orders in DB (filtered by strategy_name) plus
    broker orders whose client_order_id starts with the strategy name prefix.
    """
    symbols: set[str] = set()
    # DB pending orders for this strategy
    if hasattr(self.runtime.order_store, "list_by_status"):
        for order in self.runtime.order_store.list_by_status(
            trading_mode=self.settings.trading_mode,
            strategy_version=self.settings.strategy_version,
            statuses=["pending_submit"],
            strategy_name=strategy_name,
        ):
            symbols.add(order.symbol)
    # Broker orders: match by parsing client_order_id prefix
    for order in broker_open_orders:
        cid = getattr(order, "client_order_id", "") or ""
        if cid.startswith(f"{strategy_name}:"):
            symbols.add(getattr(order, "symbol", ""))
    return symbols
```

Update `_load_session_state()` to accept `strategy_name`:

```python
def _load_session_state(
    self,
    *,
    session_date: date,
    strategy_name: str = "breakout",
) -> DailySessionState | None:
    if self.runtime.daily_session_state_store is None or not hasattr(
        self.runtime.daily_session_state_store, "load"
    ):
        return None
    return self.runtime.daily_session_state_store.load(
        session_date=session_date,
        trading_mode=self.settings.trading_mode,
        strategy_version=self.settings.strategy_version,
        strategy_name=strategy_name,
    )
```

Update `_load_traded_symbols()` to accept `strategy_name`:

```python
def _load_traded_symbols(
    self,
    *,
    session_date: date,
    strategy_name: str = "breakout",
) -> set[tuple[str, date]]:
    if not hasattr(self.runtime.order_store, "list_by_status"):
        return set()
    orders = self.runtime.order_store.list_by_status(
        trading_mode=self.settings.trading_mode,
        strategy_version=self.settings.strategy_version,
        statuses=["filled", "partially_filled"],
        strategy_name=strategy_name,
    )
    traded_symbols: set[tuple[str, date]] = set()
    for order in orders:
        if getattr(order, "intent_type", None) != "entry":
            continue
        signal_timestamp = getattr(order, "signal_timestamp", None)
        if signal_timestamp is None:
            continue
        if _session_date(signal_timestamp, self.settings) == session_date:
            traded_symbols.add((order.symbol, session_date))
    return traded_symbols
```

Remove `_resolve_signal_evaluator()` (replaced by `_resolve_active_strategies()`). Also remove the old `signal_evaluator = self._resolve_signal_evaluator()` call from `run_cycle_once()`.

Add import at top of supervisor.py:

```python
from types import SimpleNamespace  # for fallback cycle_result
```

### 4c. Update dispatch_pending_orders (per-strategy entries_disabled)

**Problem:** `dispatch_pending_orders()` processes all `pending_submit` orders without knowing which strategies have `entries_disabled=True`. After the fan-out loop, a strategy whose session state has `entries_disabled=True` should not have its entry orders dispatched to the broker.

**In `runtime/supervisor.py`** — inside the fan-out loop in `run_cycle_once()`, after evaluating each strategy's session state, collect which strategies are blocked:

```python
# At the top of run_cycle_once(), before the fan-out loop:
entries_disabled_strategies: set[str] = set()

# Inside the fan-out loop, after loading strategy_session_state:
if strategy_session_state is not None and strategy_session_state.entries_disabled:
    entries_disabled_strategies.add(strategy_name)
```

Then pass the set to the dispatch call (after the fan-out loop — note: `dispatch_pending_orders` is synchronous):

```python
dispatch_pending_orders(
    settings=self._settings,
    runtime=self._runtime,
    broker=self._broker,
    now=now,
    blocked_strategy_names=entries_disabled_strategies,
)
```

**In `runtime/order_dispatch.py`** — add `blocked_strategy_names` parameter and guard:

```python
async def dispatch_pending_orders(
    *,
    settings: Settings,
    runtime: RuntimeContext,
    broker: BrokerProtocol,
    now: datetime,
    allowed_intent_types: set[str] | None = None,
    blocked_strategy_names: set[str] | None = None,
) -> None:
    ...
    for order in pending_orders:
        if (
            blocked_strategy_names is not None
            and order.intent_type == "entry"
            and getattr(order, "strategy_name", "breakout") in blocked_strategy_names
        ):
            continue
        # existing dispatch logic follows
```

The `getattr` fallback ensures backward compatibility with any `OrderRecord` that may not carry `strategy_name` yet (e.g., in tests using older fakes).

**Test:** Add to `tests/unit/test_runtime_supervisor_multi_strategy.py`:

```python
def test_blocked_strategy_entries_not_dispatched():
    """Entry orders for a strategy with entries_disabled are not sent to broker."""
    dispatched_strategy_names = []

    def fake_dispatch(*, settings, runtime, broker, now, blocked_strategy_names=None):
        # record what was blocked
        dispatched_strategy_names.extend(list(blocked_strategy_names or []))

    # Build supervisor with two strategies; momentum has entries_disabled=True
    # Verify that when dispatch is called, "momentum" appears in blocked_strategy_names
    ...
```

### 4d. Update existing tests

`tests/unit/test_runtime_supervisor.py`: existing tests call `run_cycle_once()` with a `cycle_runner` that receives kwargs. Check that the `strategy_name` kwarg is now present in the call — update assertions accordingly.

The existing `_resolve_signal_evaluator` tests in `tests/unit/test_strategy_flags.py` will need to be updated:
- The method is renamed to `_resolve_active_strategies()`
- It returns `list[tuple[str, evaluator]]` instead of a single evaluator
- Update the `test_resolve_signal_evaluator_*` tests accordingly

### 4f. Verify and commit

```bash
pytest tests/unit/ -q
git add -A
git commit -m "Supervisor fan-out: run_cycle per active strategy"
```

---

## Task 5 — Prior-Day-High Momentum Strategy + Settings

### 5a. Write failing test

**File**: `tests/unit/test_momentum_strategy.py`

```python
from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from alpaca_bot.domain.models import Bar, EntrySignal


def _make_settings(**overrides):
    from alpaca_bot.config import Settings, TradingMode, MarketDataFeed
    from datetime import time
    defaults = dict(
        trading_mode=TradingMode.PAPER,
        enable_live_trading=False,
        strategy_version="v1",
        database_url="postgresql://localhost/test",
        market_data_feed=MarketDataFeed.SIP,
        symbols=("AAPL",),
        daily_sma_period=5,
        breakout_lookback_bars=5,
        relative_volume_lookback_bars=5,
        relative_volume_threshold=1.5,
        entry_timeframe_minutes=15,
        risk_per_trade_pct=0.01,
        max_position_pct=0.1,
        max_open_positions=3,
        daily_loss_limit_pct=0.01,
        stop_limit_buffer_pct=0.001,
        breakout_stop_buffer_pct=0.001,
        entry_stop_price_buffer=0.01,
        entry_window_start=time(10, 0),
        entry_window_end=time(15, 30),
        flatten_time=time(15, 45),
        prior_day_high_lookback_bars=1,
    )
    defaults.update(overrides)
    return Settings(**defaults)


def _make_daily_bar(high: float, close: float = None) -> Bar:
    return Bar(
        symbol="AAPL",
        timestamp=datetime(2026, 1, 1, 21, 0, tzinfo=timezone.utc),  # prev day close
        open=close or high - 1.0,
        high=high,
        low=high - 2.0,
        close=close or high - 0.5,
        volume=1_000_000.0,
    )


def _make_intraday_bar(
    high: float,
    close: float,
    ts: datetime = None,
    volume: float = 200_000.0,
) -> Bar:
    if ts is None:
        ts = datetime(2026, 1, 2, 14, 0, tzinfo=ZoneInfo("America/New_York"))
    return Bar(
        symbol="AAPL",
        timestamp=ts,
        open=close - 1.0,
        high=high,
        low=close - 2.0,
        close=close,
        volume=volume,
    )


def _make_daily_bars(n: int = 10, high: float = 100.0) -> list[Bar]:
    return [_make_daily_bar(high=high + i * 0.1) for i in range(n)]


def _make_intraday_bars(n: int = 6, high: float = 102.0, close: float = 101.5) -> list[Bar]:
    base = datetime(2026, 1, 2, 10, 0, tzinfo=ZoneInfo("America/New_York"))
    from datetime import timedelta
    bars = []
    for i in range(n):
        ts = base + timedelta(minutes=15 * i)
        vol = 50_000.0 if i < n - 1 else 200_000.0  # last bar has high volume
        bars.append(_make_intraday_bar(high=high, close=close, ts=ts, volume=vol))
    return bars


def test_momentum_evaluator_returns_entry_signal_when_all_conditions_met():
    from alpaca_bot.strategy.momentum import evaluate_momentum_signal
    settings = _make_settings()
    daily_bars = _make_daily_bars(n=10, high=100.0)  # prior-day high ≈100.x
    intraday_bars = _make_intraday_bars(n=6, high=102.0, close=101.5)  # crosses 100.x
    result = evaluate_momentum_signal(
        symbol="AAPL",
        intraday_bars=intraday_bars,
        signal_index=len(intraday_bars) - 1,
        daily_bars=daily_bars,
        settings=settings,
    )
    assert result is not None
    assert isinstance(result, EntrySignal)
    assert result.symbol == "AAPL"


def test_momentum_entry_level_equals_prior_day_high():
    from alpaca_bot.strategy.momentum import evaluate_momentum_signal
    settings = _make_settings()
    daily_bars = _make_daily_bars(n=10, high=100.0)
    prior_day_high = daily_bars[-1].high
    intraday_bars = _make_intraday_bars(n=6, high=prior_day_high + 2.0, close=prior_day_high + 1.5)
    result = evaluate_momentum_signal(
        symbol="AAPL",
        intraday_bars=intraday_bars,
        signal_index=len(intraday_bars) - 1,
        daily_bars=daily_bars,
        settings=settings,
    )
    assert result is not None
    assert result.entry_level == prior_day_high


def test_momentum_returns_none_outside_entry_window():
    from alpaca_bot.strategy.momentum import evaluate_momentum_signal
    settings = _make_settings()
    daily_bars = _make_daily_bars(n=10, high=100.0)
    # Bar at 16:00 ET — past entry window
    from datetime import timedelta
    ts = datetime(2026, 1, 2, 16, 0, tzinfo=ZoneInfo("America/New_York"))
    base_bars = _make_intraday_bars(n=5, high=102.0, close=101.5)
    late_bar = _make_intraday_bar(high=102.0, close=101.5, ts=ts)
    intraday_bars = base_bars + [late_bar]
    result = evaluate_momentum_signal(
        symbol="AAPL",
        intraday_bars=intraday_bars,
        signal_index=len(intraday_bars) - 1,
        daily_bars=daily_bars,
        settings=settings,
    )
    assert result is None


def test_momentum_returns_none_when_trend_filter_fails():
    from alpaca_bot.strategy.momentum import evaluate_momentum_signal
    settings = _make_settings()
    # Declining daily bars: last close < SMA
    daily_bars = [
        Bar(
            symbol="AAPL",
            timestamp=datetime(2026, 1, 1, 21, 0, tzinfo=timezone.utc),
            open=110.0, high=111.0, low=109.0, close=90.0,  # close < SMA
            volume=1_000_000.0,
        )
        for _ in range(10)
    ]
    intraday_bars = _make_intraday_bars(n=6, high=102.0, close=101.5)
    result = evaluate_momentum_signal(
        symbol="AAPL",
        intraday_bars=intraday_bars,
        signal_index=len(intraday_bars) - 1,
        daily_bars=daily_bars,
        settings=settings,
    )
    assert result is None


def test_momentum_returns_none_when_bar_does_not_cross_prior_day_high():
    from alpaca_bot.strategy.momentum import evaluate_momentum_signal
    settings = _make_settings()
    daily_bars = _make_daily_bars(n=10, high=105.0)  # prior day high ≈105.x
    # Intraday bars top out at 103 — below 105
    intraday_bars = _make_intraday_bars(n=6, high=103.0, close=102.5)
    result = evaluate_momentum_signal(
        symbol="AAPL",
        intraday_bars=intraday_bars,
        signal_index=len(intraday_bars) - 1,
        daily_bars=daily_bars,
        settings=settings,
    )
    assert result is None


def test_momentum_returns_none_when_volume_below_threshold():
    from alpaca_bot.strategy.momentum import evaluate_momentum_signal
    settings = _make_settings()
    daily_bars = _make_daily_bars(n=10, high=100.0)
    # All bars same low volume → relative_volume < threshold
    base = datetime(2026, 1, 2, 10, 0, tzinfo=ZoneInfo("America/New_York"))
    from datetime import timedelta
    intraday_bars = [
        Bar(
            symbol="AAPL",
            timestamp=base + timedelta(minutes=15 * i),
            open=100.0, high=102.0, low=99.0, close=101.5,
            volume=10_000.0,  # all same low volume → rel_vol = 1.0 < 1.5
        )
        for i in range(6)
    ]
    result = evaluate_momentum_signal(
        symbol="AAPL",
        intraday_bars=intraday_bars,
        signal_index=len(intraday_bars) - 1,
        daily_bars=daily_bars,
        settings=settings,
    )
    assert result is None


def test_settings_has_prior_day_high_lookback_bars():
    settings = _make_settings(prior_day_high_lookback_bars=2)
    assert settings.prior_day_high_lookback_bars == 2


def test_settings_validates_prior_day_high_lookback_bars():
    with pytest.raises(ValueError, match="PRIOR_DAY_HIGH_LOOKBACK_BARS"):
        _make_settings(prior_day_high_lookback_bars=0)


def test_settings_no_longer_requires_15_minute_timeframe():
    # This should NOT raise — the 15-minute constraint is removed
    settings = _make_settings(entry_timeframe_minutes=5)
    assert settings.entry_timeframe_minutes == 5


def test_momentum_in_strategy_registry():
    from alpaca_bot.strategy import STRATEGY_REGISTRY
    assert "momentum" in STRATEGY_REGISTRY
```

Run: `pytest tests/unit/test_momentum_strategy.py -q` → red (momentum module doesn't exist, Settings lacks `prior_day_high_lookback_bars`).

### 5b. Implement

**File**: `src/alpaca_bot/strategy/momentum.py` (new file)

```python
from __future__ import annotations

from collections.abc import Sequence

from alpaca_bot.config import Settings
from alpaca_bot.domain.models import Bar, EntrySignal
from alpaca_bot.strategy.breakout import daily_trend_filter_passes, is_entry_session_time


def evaluate_momentum_signal(
    *,
    symbol: str,
    intraday_bars: Sequence[Bar],
    signal_index: int,
    daily_bars: Sequence[Bar],
    settings: Settings,
) -> EntrySignal | None:
    if symbol not in settings.symbols:
        return None
    if not intraday_bars or signal_index < 0 or signal_index >= len(intraday_bars):
        return None

    signal_bar = intraday_bars[signal_index]
    if signal_bar.symbol != symbol:
        return None
    if not is_entry_session_time(signal_bar.timestamp, settings):
        return None
    if not daily_trend_filter_passes(daily_bars, settings):
        return None

    lookback = settings.prior_day_high_lookback_bars
    if len(daily_bars) < lookback:
        return None
    yesterday_high = daily_bars[-lookback].high

    if signal_bar.high <= yesterday_high:
        return None
    if signal_bar.close <= yesterday_high:
        return None

    # Volume confirmation: compare to average of prior intraday bars
    if signal_index < settings.relative_volume_lookback_bars:
        return None
    prior_bars = intraday_bars[signal_index - settings.relative_volume_lookback_bars:signal_index]
    average_volume = sum(bar.volume for bar in prior_bars) / len(prior_bars)
    relative_volume = signal_bar.volume / average_volume if average_volume > 0 else 0.0
    if relative_volume < settings.relative_volume_threshold:
        return None

    stop_price = round(signal_bar.high + settings.entry_stop_price_buffer, 2)
    limit_price = round(stop_price * (1 + settings.stop_limit_buffer_pct), 2)
    stop_buffer = max(0.01, yesterday_high * settings.breakout_stop_buffer_pct)
    initial_stop_price = round(yesterday_high - stop_buffer, 2)

    return EntrySignal(
        symbol=symbol,
        signal_bar=signal_bar,
        entry_level=yesterday_high,
        relative_volume=relative_volume,
        stop_price=stop_price,
        limit_price=limit_price,
        initial_stop_price=initial_stop_price,
    )
```

**File**: `src/alpaca_bot/config/__init__.py`

Add `prior_day_high_lookback_bars: int = 1` to `Settings` dataclass (after `notify_slippage_threshold_pct`):

```python
prior_day_high_lookback_bars: int = 1
```

Add to `from_env()`:

```python
prior_day_high_lookback_bars=int(values.get("PRIOR_DAY_HIGH_LOOKBACK_BARS", "1")),
```

In `validate()`: remove the hardcoded 15-minute constraint and add the new validation:

```python
# Remove this block entirely:
# if self.entry_timeframe_minutes != 15:
#     raise ValueError("ENTRY_TIMEFRAME_MINUTES must be 15 for this strategy")

# Add:
if self.prior_day_high_lookback_bars < 1:
    raise ValueError("PRIOR_DAY_HIGH_LOOKBACK_BARS must be at least 1")
```

**File**: `src/alpaca_bot/strategy/__init__.py`

Add momentum to registry:

```python
from alpaca_bot.strategy.breakout import evaluate_breakout_signal
from alpaca_bot.strategy.momentum import evaluate_momentum_signal


STRATEGY_REGISTRY: dict[str, StrategySignalEvaluator] = {
    "breakout": evaluate_breakout_signal,
    "momentum": evaluate_momentum_signal,
}
```

### 5c. Update existing tests

Any test that:
- Tests the "ENTRY_TIMEFRAME_MINUTES must be 15" validation error → remove the assertion (the constraint is gone)
- Tests `Settings.validate()` with `entry_timeframe_minutes != 15` → update to not expect ValueError

Search: `grep -r "ENTRY_TIMEFRAME_MINUTES must be 15" tests/`

### 5d. Verify and commit

```bash
pytest tests/unit/ -q
git add -A
git commit -m "Add prior-day-high momentum strategy and PRIOR_DAY_HIGH_LOOKBACK_BARS setting"
```

---

## Task 6 — Dashboard: `trades_by_strategy` + Strategy Column

### 6a. Write failing test

Add to `tests/unit/test_web_service.py` (or a new `tests/unit/test_dashboard_strategy_breakdown.py`):

```python
def test_metrics_snapshot_has_trades_by_strategy():
    from alpaca_bot.web.service import MetricsSnapshot
    import inspect
    fields = {f.name for f in MetricsSnapshot.__dataclass_fields__.values()}
    assert "trades_by_strategy" in fields


def test_load_metrics_snapshot_groups_by_strategy():
    from alpaca_bot.web.service import load_metrics_snapshot
    from alpaca_bot.config import TradingMode
    from datetime import datetime, timezone, date

    # Two trades: one breakout, one momentum
    raw_trades = [
        {
            "symbol": "AAPL",
            "strategy_name": "breakout",
            "entry_fill": 150.0,
            "entry_limit": 150.1,
            "entry_time": datetime(2026, 1, 2, 10, tzinfo=timezone.utc),
            "exit_fill": 155.0,
            "exit_time": datetime(2026, 1, 2, 14, tzinfo=timezone.utc),
            "qty": 10,
        },
        {
            "symbol": "TSLA",
            "strategy_name": "momentum",
            "entry_fill": 200.0,
            "entry_limit": 200.2,
            "entry_time": datetime(2026, 1, 2, 10, tzinfo=timezone.utc),
            "exit_fill": 210.0,
            "exit_time": datetime(2026, 1, 2, 14, tzinfo=timezone.utc),
            "qty": 5,
        },
    ]

    from types import SimpleNamespace
    from alpaca_bot.config import Settings, TradingMode, MarketDataFeed
    from datetime import time

    settings = Settings(
        trading_mode=TradingMode.PAPER,
        enable_live_trading=False,
        strategy_version="v1",
        database_url="postgresql://localhost/test",
        market_data_feed=MarketDataFeed.SIP,
        symbols=("AAPL",),
        daily_sma_period=20,
        breakout_lookback_bars=20,
        relative_volume_lookback_bars=20,
        relative_volume_threshold=1.5,
        entry_timeframe_minutes=15,
        risk_per_trade_pct=0.01,
        max_position_pct=0.1,
        max_open_positions=3,
        daily_loss_limit_pct=0.01,
        stop_limit_buffer_pct=0.001,
        breakout_stop_buffer_pct=0.001,
        entry_stop_price_buffer=0.01,
        entry_window_start=time(10, 0),
        entry_window_end=time(15, 30),
        flatten_time=time(15, 45),
    )

    snapshot = load_metrics_snapshot(
        settings=settings,
        connection=SimpleNamespace(),
        order_store=SimpleNamespace(
            list_closed_trades=lambda **_: raw_trades,
        ),
        audit_event_store=SimpleNamespace(
            list_by_event_types=lambda **_: [],
        ),
        tuning_result_store=SimpleNamespace(
            load_latest_best=lambda **_: None,
        ),
    )

    assert "breakout" in snapshot.trades_by_strategy
    assert "momentum" in snapshot.trades_by_strategy
    assert len(snapshot.trades_by_strategy["breakout"]) == 1
    assert len(snapshot.trades_by_strategy["momentum"]) == 1
    # Aggregate trades still includes both
    assert len(snapshot.trades) == 2
```

Run: `pytest tests/unit/test_web_service.py -q` or the new test file → red (`MetricsSnapshot` lacks `trades_by_strategy`).

### 6b. Implement

**File**: `src/alpaca_bot/web/service.py`

Add `strategy_name` field to `TradeRecord`:

```python
@dataclass(frozen=True)
class TradeRecord:
    symbol: str
    strategy_name: str
    entry_time: datetime | None
    exit_time: datetime | None
    entry_price: float
    exit_price: float
    quantity: int
    pnl: float
    slippage: float | None
```

Add `trades_by_strategy` to `MetricsSnapshot`:

```python
@dataclass(frozen=True)
class MetricsSnapshot:
    generated_at: datetime
    session_date: date
    trades: list[TradeRecord]
    trades_by_strategy: dict[str, list[TradeRecord]]   # NEW
    total_pnl: float
    win_rate: float | None
    mean_return_pct: float | None
    max_drawdown_pct: float | None
    sharpe_ratio: float | None
    admin_history: list[AuditEvent]
    last_backtest: object | None = None
```

Update `_to_trade_record()` to include `strategy_name`:

```python
def _to_trade_record(row: dict) -> TradeRecord:
    entry_fill = row["entry_fill"]
    exit_fill = row["exit_fill"]
    qty = row["qty"]
    pnl = (exit_fill - entry_fill) * qty
    slippage = (
        row["entry_limit"] - entry_fill
        if row.get("entry_limit") is not None
        else None
    )
    return TradeRecord(
        symbol=row["symbol"],
        strategy_name=row.get("strategy_name", "breakout"),    # NEW
        entry_time=row.get("entry_time"),
        exit_time=row.get("exit_time"),
        entry_price=entry_fill,
        exit_price=exit_fill,
        quantity=qty,
        pnl=pnl,
        slippage=slippage,
    )
```

Update `load_metrics_snapshot()` to build `trades_by_strategy`:

```python
trades = [_to_trade_record(t) for t in raw_trades]

trades_by_strategy: dict[str, list[TradeRecord]] = {}
for trade in trades:
    trades_by_strategy.setdefault(trade.strategy_name, []).append(trade)

return MetricsSnapshot(
    generated_at=generated_at,
    session_date=session_date,
    trades=trades,
    trades_by_strategy=trades_by_strategy,    # NEW
    total_pnl=sum(t.pnl for t in trades),
    win_rate=_win_rate(trades),
    mean_return_pct=_mean_return_pct(trades),
    max_drawdown_pct=_max_drawdown_pct(trades),
    sharpe_ratio=_compute_sharpe_from_trade_records(trades),
    admin_history=admin_history,
    last_backtest=last_tuning,
)
```

**File**: `src/alpaca_bot/web/templates/dashboard.html`

In the positions panel (inside the `{% if snapshot %}` block): add a `Strategy` column header and data cell. Find the positions table and add:

```html
<!-- In <thead> -->
<th>Strategy</th>

<!-- In <tbody> row, after symbol -->
<td>{{ position.strategy_name }}</td>
```

In the metrics panel: add a per-strategy breakdown table after the aggregate metrics. Inside the `{% if metrics %}` block, after existing trade metrics:

```html
{% if metrics.trades_by_strategy %}
<h3>By Strategy</h3>
<table>
  <thead>
    <tr>
      <th>Strategy</th>
      <th>Trades</th>
      <th>PnL</th>
      <th>Win Rate</th>
    </tr>
  </thead>
  <tbody>
    {% for strategy_name, strat_trades in metrics.trades_by_strategy.items() %}
    {% set strat_pnl = strat_trades | map(attribute='pnl') | sum %}
    {% set wins = strat_trades | selectattr('pnl', 'gt', 0) | list | length %}
    <tr>
      <td>{{ strategy_name }}</td>
      <td>{{ strat_trades | length }}</td>
      <td>{{ format_price(strat_pnl) }}</td>
      <td>{% if strat_trades %}{{ "%.0f%%" | format(wins / strat_trades | length * 100) }}{% else %}n/a{% endif %}</td>
    </tr>
    {% endfor %}
  </tbody>
</table>
{% endif %}
```

### 6c. Update existing tests

In `tests/unit/test_web_service.py`: any existing `MetricsSnapshot` construction or assertion needs to include `trades_by_strategy`. Also update `_to_trade_record` tests if they exist.

Search: `grep -n "MetricsSnapshot\|TradeRecord" tests/unit/test_web_service.py`

Any existing test that constructs a `TradeRecord` without `strategy_name` will now fail — add `strategy_name="breakout"` (or `strategy_name=...`) to all such constructions.

### 6d. Verify and commit

```bash
pytest tests/unit/ -q
git add -A
git commit -m "Dashboard: trades_by_strategy breakdown and Strategy column in positions"
```

---

## Task 7 — Full Test Suite Verification

```bash
pytest tests/unit/ -q
```

Expected: all tests pass (329+ after new test files). If any fail:
- Check for remaining `BreakoutSignal` / `breakout_level` references: `grep -r "BreakoutSignal\|breakout_level" src/ tests/`
- Check for stale FakeCursor row tuples missing the new `strategy_name` column
- Check for `CycleIntent` / `OrderRecord` constructions missing `strategy_name` (safe because of defaults, but explicit is better)
- Check for `MetricsSnapshot` constructions missing `trades_by_strategy`
- Check that `ENTRY_TIMEFRAME_MINUTES must be 15` assertion was removed from any test

Final commit:

```bash
git add -A
git commit -m "Verify: full test suite passes for multi-strategy feature"
```

---

## Summary of Files Modified

| File | Change |
|---|---|
| `src/alpaca_bot/domain/models.py` | `BreakoutSignal→EntrySignal`, `breakout_level→entry_level`, `OpenPosition.strategy_name`, `WorkingEntryOrder.entry_level` |
| `src/alpaca_bot/domain/__init__.py` | Export `EntrySignal` |
| `src/alpaca_bot/strategy/__init__.py` | Protocol return `EntrySignal`, add momentum to registry |
| `src/alpaca_bot/strategy/breakout.py` | Return `EntrySignal` |
| `src/alpaca_bot/strategy/momentum.py` | New file: `evaluate_momentum_signal()` |
| `src/alpaca_bot/core/engine.py` | `CycleIntent.strategy_name`, `evaluate_cycle(strategy_name)`, `_client_order_id` format |
| `src/alpaca_bot/runtime/cycle.py` | `run_cycle(strategy_name)`, passes to `evaluate_cycle`, writes to `OrderRecord` |
| `src/alpaca_bot/runtime/supervisor.py` | `_resolve_active_strategies()`, fan-out loop, per-strategy session state, `entries_disabled_strategies` collection |
| `src/alpaca_bot/runtime/order_dispatch.py` | `blocked_strategy_names` param, skip blocked entry orders |
| `src/alpaca_bot/runtime/cycle_intent_execution.py` | `_positions_by_symbol` keyed by `(symbol, strategy_name)`, `_active_stop_orders` filtered by `strategy_name` |
| `src/alpaca_bot/runtime/startup_recovery.py` | Multi-strategy position reconciliation, `_infer_strategy_name_from_client_order_id()` |
| `src/alpaca_bot/config/__init__.py` | `prior_day_high_lookback_bars`, remove 15-min constraint |
| `src/alpaca_bot/storage/models.py` | `strategy_name` on `OrderRecord`, `PositionRecord`, `DailySessionState` |
| `src/alpaca_bot/storage/repositories.py` | All store queries updated with `strategy_name` |
| `src/alpaca_bot/web/service.py` | `TradeRecord.strategy_name`, `MetricsSnapshot.trades_by_strategy` |
| `src/alpaca_bot/web/templates/dashboard.html` | Strategy column, per-strategy metrics table |
| `migrations/006_add_strategy_name.sql` | New migration |
| `migrations/006_add_strategy_name.down.sql` | Reversible migration |
| `tests/unit/test_entry_signal.py` | New |
| `tests/unit/test_storage_strategy_name.py` | New |
| `tests/unit/test_cycle_engine_multi_strategy.py` | New |
| `tests/unit/test_runtime_supervisor_multi_strategy.py` | New |
| `tests/unit/test_momentum_strategy.py` | New |
| Various existing test files | Updated for renames and new fields |
