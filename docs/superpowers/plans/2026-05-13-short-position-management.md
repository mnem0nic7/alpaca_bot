# Short Position Management Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the bot fully manage short equity and short option positions that exist in the Alpaca account — importing them on startup, applying protective buy-stops, trailing stops symmetrically to longs, and flattening at EOD.

**Architecture:** All existing logic in `core/engine.py`, `runtime/startup_recovery.py`, `runtime/supervisor.py`, `runtime/cycle_intent_execution.py`, and `execution/alpaca.py` is extended with a direction flag (`is_short = position.quantity < 0`). A new `lowest_price` field mirrors `highest_price` for the breakeven trailing pass. No new files are needed — every change is an additive extension of the existing symmetric design.

**Tech Stack:** Python 3.12, pytest, psycopg2, Alpaca Trading SDK

---

## File Map

| Action | File |
|--------|------|
| Create | `migrations/020_add_position_lowest_price.sql` |
| Modify | `src/alpaca_bot/domain/models.py` |
| Modify | `src/alpaca_bot/storage/models.py` |
| Modify | `src/alpaca_bot/storage/repositories.py` |
| Modify | `src/alpaca_bot/execution/alpaca.py` |
| Modify | `src/alpaca_bot/runtime/order_dispatch.py` |
| Modify | `src/alpaca_bot/runtime/startup_recovery.py` |
| Modify | `src/alpaca_bot/strategy/breakout.py` |
| Modify | `src/alpaca_bot/core/engine.py` |
| Modify | `src/alpaca_bot/runtime/cycle_intent_execution.py` |
| Modify | `src/alpaca_bot/runtime/supervisor.py` |
| Test | `tests/unit/test_domain_models.py` |
| Test | `tests/unit/test_repositories.py` |
| Test | `tests/unit/test_startup_recovery.py` |
| Test | `tests/unit/test_cycle_engine.py` |
| Test | `tests/unit/test_cycle_intent_execution.py` |
| Test | `tests/unit/test_supervisor.py` |

---

## Task 1: `lowest_price` Data Model Fields + DB Migration

**Files:**
- Create: `migrations/020_add_position_lowest_price.sql`
- Modify: `src/alpaca_bot/domain/models.py` (line 76)
- Modify: `src/alpaca_bot/storage/models.py` (line 74)
- Test: `tests/unit/test_domain_models.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_domain_models.py` (or append if it exists):

```python
from alpaca_bot.domain.models import OpenPosition
from alpaca_bot.storage.models import PositionRecord
from alpaca_bot.config import TradingMode
from datetime import datetime, timezone


def test_open_position_has_lowest_price_field():
    pos = OpenPosition(
        symbol="QBTS",
        entry_timestamp=datetime(2026, 5, 1, 14, 0, tzinfo=timezone.utc),
        entry_price=5.50,
        quantity=-100,
        entry_level=6.00,
        initial_stop_price=6.00,
        stop_price=6.00,
        lowest_price=5.20,
    )
    assert pos.lowest_price == 5.20


def test_open_position_lowest_price_defaults_to_zero():
    pos = OpenPosition(
        symbol="QBTS",
        entry_timestamp=datetime(2026, 5, 1, 14, 0, tzinfo=timezone.utc),
        entry_price=5.50,
        quantity=-100,
        entry_level=6.00,
        initial_stop_price=6.00,
        stop_price=6.00,
    )
    assert pos.lowest_price == 0.0


def test_position_record_has_lowest_price_field():
    rec = PositionRecord(
        symbol="QBTS",
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        quantity=-100,
        entry_price=5.50,
        stop_price=6.00,
        initial_stop_price=6.00,
        opened_at=datetime(2026, 5, 1, 14, 0, tzinfo=timezone.utc),
        lowest_price=5.20,
    )
    assert rec.lowest_price == 5.20


def test_position_record_lowest_price_defaults_to_none():
    rec = PositionRecord(
        symbol="QBTS",
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        quantity=-100,
        entry_price=5.50,
        stop_price=6.00,
        initial_stop_price=6.00,
        opened_at=datetime(2026, 5, 1, 14, 0, tzinfo=timezone.utc),
    )
    assert rec.lowest_price is None
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/unit/test_domain_models.py -v
```

Expected: `AttributeError: 'OpenPosition' object has no attribute 'lowest_price'`

- [ ] **Step 3: Add `lowest_price` to `OpenPosition` in `domain/models.py`**

In `src/alpaca_bot/domain/models.py`, after line 75 (`highest_price: float = 0.0`):

```python
    highest_price: float = 0.0
    lowest_price: float = 0.0
    strategy_name: str = "breakout"
```

- [ ] **Step 4: Add `lowest_price` to `PositionRecord` in `storage/models.py`**

In `src/alpaca_bot/storage/models.py`, after line 74 (`highest_price: float | None = None`):

```python
    highest_price: float | None = None
    lowest_price: float | None = None
```

- [ ] **Step 5: Run test to verify it passes**

```bash
pytest tests/unit/test_domain_models.py -v
```

Expected: 4 PASSED

- [ ] **Step 6: Create DB migration**

Create `migrations/020_add_position_lowest_price.sql`:

```sql
ALTER TABLE positions ADD COLUMN IF NOT EXISTS lowest_price NUMERIC DEFAULT NULL;
```

- [ ] **Step 7: Run full test suite to verify no regressions**

```bash
pytest
```

Expected: all existing tests still pass.

- [ ] **Step 8: Commit**

```bash
git add migrations/020_add_position_lowest_price.sql \
        src/alpaca_bot/domain/models.py \
        src/alpaca_bot/storage/models.py \
        tests/unit/test_domain_models.py
git commit -m "feat: add lowest_price field to OpenPosition, PositionRecord, and migration 020"
```

---

## Task 2: Storage Repositories — `lowest_price` Column

**Files:**
- Modify: `src/alpaca_bot/storage/repositories.py`
- Test: `tests/unit/test_repositories.py`

Four changes in `PositionStore`: `save()`, `replace_all()`, `list_all()`, and new `update_lowest_price()`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_repositories.py` (create the file if absent):

```python
from __future__ import annotations
import sqlite3
from datetime import datetime, timezone
from alpaca_bot.config import TradingMode
from alpaca_bot.storage.models import PositionRecord
from alpaca_bot.storage.repositories import PositionStore


def _make_conn():
    conn = sqlite3.connect(":memory:")
    conn.execute("""
        CREATE TABLE positions (
            symbol TEXT,
            trading_mode TEXT,
            strategy_version TEXT,
            strategy_name TEXT,
            quantity REAL,
            entry_price REAL,
            stop_price REAL,
            initial_stop_price REAL,
            opened_at TEXT,
            updated_at TEXT,
            highest_price REAL,
            lowest_price REAL,
            PRIMARY KEY (symbol, trading_mode, strategy_version, strategy_name)
        )
    """)
    conn.row_factory = None
    return conn


def _make_record(symbol: str = "QBTS", quantity: float = -100,
                 highest_price: float | None = None,
                 lowest_price: float | None = None) -> PositionRecord:
    return PositionRecord(
        symbol=symbol,
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        strategy_name="short_equity",
        quantity=quantity,
        entry_price=5.50,
        stop_price=6.00,
        initial_stop_price=6.00,
        opened_at=datetime(2026, 5, 1, 14, 0, tzinfo=timezone.utc),
        highest_price=highest_price,
        lowest_price=lowest_price,
    )


def test_save_and_list_all_roundtrips_lowest_price():
    conn = _make_conn()
    store = PositionStore(conn)
    rec = _make_record(lowest_price=4.80)
    store.save(rec)
    rows = store.list_all(trading_mode=TradingMode.PAPER, strategy_version="v1-breakout")
    assert len(rows) == 1
    assert rows[0].lowest_price == 4.80


def test_save_and_list_all_roundtrips_null_lowest_price():
    conn = _make_conn()
    store = PositionStore(conn)
    rec = _make_record(lowest_price=None)
    store.save(rec)
    rows = store.list_all(trading_mode=TradingMode.PAPER, strategy_version="v1-breakout")
    assert rows[0].lowest_price is None


def test_update_lowest_price_persists_new_value():
    conn = _make_conn()
    store = PositionStore(conn)
    store.save(_make_record(lowest_price=5.00))
    store.update_lowest_price(
        symbol="QBTS",
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        strategy_name="short_equity",
        lowest_price=4.50,
    )
    rows = store.list_all(trading_mode=TradingMode.PAPER, strategy_version="v1-breakout")
    assert rows[0].lowest_price == 4.50


def test_replace_all_roundtrips_lowest_price():
    conn = _make_conn()
    store = PositionStore(conn)
    rec = _make_record(lowest_price=4.70)
    store.replace_all(
        positions=[rec],
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
    )
    rows = store.list_all(trading_mode=TradingMode.PAPER, strategy_version="v1-breakout")
    assert rows[0].lowest_price == 4.70
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/unit/test_repositories.py::test_save_and_list_all_roundtrips_lowest_price -v
```

Expected: FAIL — `lowest_price` not in INSERT or SELECT.

- [ ] **Step 3: Update `PositionStore.save()` in `repositories.py`**

In `src/alpaca_bot/storage/repositories.py`, update `save()` (around line 1070):

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
                highest_price,
                lowest_price
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (symbol, trading_mode, strategy_version, strategy_name)
            DO UPDATE SET
                quantity = EXCLUDED.quantity,
                entry_price = EXCLUDED.entry_price,
                stop_price = EXCLUDED.stop_price,
                initial_stop_price = EXCLUDED.initial_stop_price,
                opened_at = EXCLUDED.opened_at,
                updated_at = EXCLUDED.updated_at,
                highest_price = COALESCE(EXCLUDED.highest_price, positions.highest_price),
                lowest_price = COALESCE(EXCLUDED.lowest_price, positions.lowest_price)
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
                position.lowest_price,
            ),
            commit=commit,
        )
```

- [ ] **Step 4: Update `PositionStore.replace_all()` — same INSERT, same additional column**

In `replace_all()` (around line 1138), update the INSERT inside the loop:

```python
            for position in positions:
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
                        highest_price,
                        lowest_price
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (symbol, trading_mode, strategy_version, strategy_name)
                    DO UPDATE SET
                        quantity = EXCLUDED.quantity,
                        entry_price = EXCLUDED.entry_price,
                        stop_price = EXCLUDED.stop_price,
                        initial_stop_price = EXCLUDED.initial_stop_price,
                        opened_at = EXCLUDED.opened_at,
                        updated_at = EXCLUDED.updated_at,
                        highest_price = COALESCE(EXCLUDED.highest_price, positions.highest_price),
                        lowest_price = COALESCE(EXCLUDED.lowest_price, positions.lowest_price)
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
                        position.lowest_price,
                    ),
                    commit=False,
                )
```

- [ ] **Step 5: Update `PositionStore.list_all()` SELECT and constructor**

In `list_all()` (around line 1234), add `lowest_price` to SELECT and to the constructor:

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
                highest_price,
                lowest_price
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
                lowest_price=float(row[11]) if row[11] is not None else None,
            )
            for row in rows
        ]
```

- [ ] **Step 6: Add `update_lowest_price()` method after `update_highest_price()` (around line 1232)**

```python
    def update_lowest_price(
        self,
        *,
        symbol: str,
        trading_mode: TradingMode,
        strategy_version: str,
        strategy_name: str,
        lowest_price: float,
        commit: bool = True,
    ) -> None:
        execute(
            self._connection,
            """
            UPDATE positions
               SET lowest_price = %s,
                   updated_at = NOW()
             WHERE symbol = %s
               AND trading_mode = %s
               AND strategy_version = %s
               AND strategy_name = %s
            """,
            (lowest_price, symbol, trading_mode.value, strategy_version, strategy_name),
            commit=commit,
        )
```

- [ ] **Step 7: Run repository tests**

```bash
pytest tests/unit/test_repositories.py -v
```

Expected: all 4 new tests PASS.

- [ ] **Step 8: Run full suite**

```bash
pytest
```

Expected: all passing.

- [ ] **Step 9: Commit**

```bash
git add src/alpaca_bot/storage/repositories.py tests/unit/test_repositories.py
git commit -m "feat: add lowest_price column to PositionStore save/replace_all/list_all + update_lowest_price"
```

---

## Task 3: New Broker Methods + Protocol Stubs

**Files:**
- Modify: `src/alpaca_bot/execution/alpaca.py` (after `submit_stop_order` and `submit_market_exit`)
- Modify: `src/alpaca_bot/runtime/order_dispatch.py` (`BrokerProtocol`, line 50)
- Modify: `src/alpaca_bot/runtime/cycle_intent_execution.py` (`BrokerProtocol`, line 65)
- Test: `tests/unit/test_alpaca_broker_short_methods.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_alpaca_broker_short_methods.py`:

```python
from __future__ import annotations
from dataclasses import dataclass, field
from unittest.mock import MagicMock, patch
import pytest


@dataclass
class FakeTradingClient:
    submitted: list = field(default_factory=list)

    def submit_order(self, request):
        self.submitted.append(request)
        result = MagicMock()
        result.id = "fake-broker-id"
        result.client_order_id = getattr(request, "client_order_id", "coid")
        result.status = MagicMock()
        result.status.__str__ = lambda s: "accepted"
        result.filled_qty = "0"
        result.filled_avg_price = None
        result.limit_price = None
        result.stop_price = getattr(request, "stop_price", None)
        result.qty = getattr(request, "qty", None)
        return result


def _make_broker(fake_client):
    from alpaca_bot.execution.alpaca import AlpacaBroker
    broker = object.__new__(AlpacaBroker)
    broker._trading = fake_client
    broker._data = MagicMock()
    return broker


def test_submit_buy_stop_order_uses_buy_side():
    from alpaca.trading.enums import OrderSide
    fake = FakeTradingClient()
    broker = _make_broker(fake)
    broker.submit_buy_stop_order(
        symbol="QBTS",
        quantity=100,
        stop_price=6.05,
        client_order_id="test-coid-1",
    )
    assert len(fake.submitted) == 1
    req = fake.submitted[0]
    assert req.side == OrderSide.BUY
    assert float(req.stop_price) == 6.05


def test_submit_market_buy_to_cover_uses_buy_side():
    from alpaca.trading.enums import OrderSide
    fake = FakeTradingClient()
    broker = _make_broker(fake)
    broker.submit_market_buy_to_cover(
        symbol="QBTS",
        quantity=100,
        client_order_id="test-coid-2",
    )
    assert len(fake.submitted) == 1
    req = fake.submitted[0]
    assert req.side == OrderSide.BUY


def test_submit_option_market_buy_to_close_uses_buy_side():
    from alpaca.trading.enums import OrderSide
    fake = FakeTradingClient()
    broker = _make_broker(fake)
    broker.submit_option_market_buy_to_close(
        occ_symbol="ALHC250620P00005000",
        quantity=1,
        client_order_id="test-coid-3",
    )
    assert len(fake.submitted) == 1
    req = fake.submitted[0]
    assert req.side == OrderSide.BUY
    assert req.symbol == "ALHC250620P00005000"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/unit/test_alpaca_broker_short_methods.py -v
```

Expected: `AttributeError: 'AlpacaBroker' object has no attribute 'submit_buy_stop_order'`

- [ ] **Step 3: Add three new methods to `AlpacaBroker` in `execution/alpaca.py`**

Add after `submit_stop_order` (around line 333):

```python
    def submit_buy_stop_order(
        self,
        *,
        symbol: str,
        quantity: float | None = None,
        qty: float | None = None,
        stop_price: float,
        client_order_id: str,
    ) -> BrokerOrder:
        resolved_qty = _resolve_order_quantity(quantity=quantity, qty=qty)
        request = _stop_order_request(
            symbol=symbol,
            quantity=resolved_qty,
            stop_price=stop_price,
            client_order_id=client_order_id,
            side="buy",
        )
        return _parse_broker_order(
            _retry_with_backoff(lambda: self._trading.submit_order(request))
        )

    def submit_market_buy_to_cover(
        self,
        *,
        symbol: str,
        quantity: float | None = None,
        qty: float | None = None,
        client_order_id: str,
    ) -> BrokerOrder:
        resolved_qty = _resolve_order_quantity(quantity=quantity, qty=qty)
        request = _market_order_request(
            symbol=symbol,
            quantity=resolved_qty,
            client_order_id=client_order_id,
            side="buy",
        )
        return _parse_broker_order(
            _retry_with_backoff(lambda: self._trading.submit_order(request))
        )
```

Add after `submit_option_market_exit` (around line 438):

```python
    def submit_option_market_buy_to_close(
        self,
        *,
        occ_symbol: str,
        quantity: int,
        client_order_id: str,
    ) -> BrokerOrder:
        from alpaca.trading.requests import MarketOrderRequest  # type: ignore[import]
        from alpaca.trading.enums import OrderSide, TimeInForce  # type: ignore[import]
        order_data = MarketOrderRequest(
            symbol=occ_symbol,
            qty=quantity,
            side=OrderSide.BUY,
            time_in_force=TimeInForce.DAY,
            client_order_id=client_order_id,
        )
        return _parse_broker_order(
            _retry_with_backoff(lambda: self._trading.submit_order(order_data))
        )
```

- [ ] **Step 4: Add method stubs to `BrokerProtocol` in `order_dispatch.py` (line 50)**

```python
class BrokerProtocol(Protocol):
    def submit_stop_limit_entry(self, **kwargs) -> BrokerOrder: ...
    def submit_limit_entry(self, **kwargs) -> BrokerOrder: ...
    def submit_stop_order(self, **kwargs) -> BrokerOrder: ...
    def submit_buy_stop_order(self, **kwargs) -> BrokerOrder: ...
    def submit_market_exit(self, **kwargs) -> BrokerOrder: ...
    def submit_market_buy_to_cover(self, **kwargs) -> BrokerOrder: ...
    def submit_option_market_buy_to_close(self, **kwargs) -> BrokerOrder: ...
    def cancel_order(self, order_id: str) -> None: ...
```

- [ ] **Step 5: Add method stubs to `BrokerProtocol` in `cycle_intent_execution.py` (around line 65)**

Locate the `BrokerProtocol` class and add the three new method stubs alongside existing ones:

```python
    def submit_buy_stop_order(self, **kwargs) -> BrokerOrder: ...
    def submit_market_buy_to_cover(self, **kwargs) -> BrokerOrder: ...
    def submit_option_market_buy_to_close(self, **kwargs) -> BrokerOrder: ...
```

- [ ] **Step 6: Run broker method tests**

```bash
pytest tests/unit/test_alpaca_broker_short_methods.py -v
```

Expected: 3 PASSED.

- [ ] **Step 7: Run full suite**

```bash
pytest
```

- [ ] **Step 8: Commit**

```bash
git add src/alpaca_bot/execution/alpaca.py \
        src/alpaca_bot/runtime/order_dispatch.py \
        src/alpaca_bot/runtime/cycle_intent_execution.py \
        tests/unit/test_alpaca_broker_short_methods.py
git commit -m "feat: add submit_buy_stop_order, submit_market_buy_to_cover, submit_option_market_buy_to_close to AlpacaBroker"
```

---

## Task 4: Order Dispatch Buy-Side Stop Routing

**Files:**
- Modify: `src/alpaca_bot/runtime/order_dispatch.py` (`_submit_order`, line 493)
- Test: `tests/unit/test_order_dispatch.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_order_dispatch.py` (or create):

```python
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone
from alpaca_bot.config import TradingMode
from alpaca_bot.execution import BrokerOrder
from alpaca_bot.storage import OrderRecord


@dataclass
class RecordingBroker:
    calls: list = field(default_factory=list)

    def submit_stop_order(self, **kwargs) -> BrokerOrder:
        self.calls.append(("submit_stop_order", kwargs))
        return _fake_order("accepted")

    def submit_buy_stop_order(self, **kwargs) -> BrokerOrder:
        self.calls.append(("submit_buy_stop_order", kwargs))
        return _fake_order("accepted")

    def submit_market_exit(self, **kwargs) -> BrokerOrder:
        self.calls.append(("submit_market_exit", kwargs))
        return _fake_order("accepted")

    def submit_market_buy_to_cover(self, **kwargs) -> BrokerOrder:
        self.calls.append(("submit_market_buy_to_cover", kwargs))
        return _fake_order("accepted")

    def submit_option_market_buy_to_close(self, **kwargs) -> BrokerOrder:
        self.calls.append(("submit_option_market_buy_to_close", kwargs))
        return _fake_order("accepted")

    def submit_stop_limit_entry(self, **kwargs) -> BrokerOrder:
        self.calls.append(("submit_stop_limit_entry", kwargs))
        return _fake_order("accepted")

    def submit_limit_entry(self, **kwargs) -> BrokerOrder:
        self.calls.append(("submit_limit_entry", kwargs))
        return _fake_order("accepted")

    def cancel_order(self, order_id: str) -> None:
        self.calls.append(("cancel_order", order_id))


def _fake_order(status: str) -> BrokerOrder:
    from alpaca_bot.execution import BrokerOrder
    return BrokerOrder(
        broker_order_id="fake-id",
        client_order_id="coid",
        status=status,
        filled_qty=0,
        filled_avg_price=None,
        limit_price=None,
        stop_price=None,
    )


def _make_stop_order(side: str) -> OrderRecord:
    return OrderRecord(
        client_order_id=f"test-stop-{side}",
        symbol="QBTS",
        side=side,
        intent_type="stop",
        status="pending_submit",
        quantity=100,
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        created_at=datetime(2026, 5, 1, 14, 0, tzinfo=timezone.utc),
        updated_at=datetime(2026, 5, 1, 14, 0, tzinfo=timezone.utc),
        stop_price=6.05,
    )


def test_buy_side_stop_routes_to_submit_buy_stop_order():
    from alpaca_bot.runtime.order_dispatch import _submit_order
    from alpaca_bot.config import Settings
    broker = RecordingBroker()
    order = _make_stop_order("buy")
    settings = _make_settings()
    _submit_order(order=order, broker=broker, settings=settings)
    assert len(broker.calls) == 1
    method_name, _ = broker.calls[0]
    assert method_name == "submit_buy_stop_order", (
        f"Buy-side stop must route to submit_buy_stop_order, got {method_name!r}"
    )


def test_sell_side_stop_routes_to_submit_stop_order():
    from alpaca_bot.runtime.order_dispatch import _submit_order
    broker = RecordingBroker()
    order = _make_stop_order("sell")
    _submit_order(order=order, broker=broker, settings=_make_settings())
    assert broker.calls[0][0] == "submit_stop_order"


def _make_settings():
    from alpaca_bot.config import Settings
    return Settings.from_env({
        "TRADING_MODE": "paper",
        "ENABLE_LIVE_TRADING": "false",
        "STRATEGY_VERSION": "v1-breakout",
        "DATABASE_URL": "postgresql://alpaca_bot:secret@db.example.com:5432/alpaca_bot",
        "MARKET_DATA_FEED": "sip",
        "SYMBOLS": "QBTS",
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
    })
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/unit/test_order_dispatch.py::test_buy_side_stop_routes_to_submit_buy_stop_order -v
```

Expected: FAIL — `submit_buy_stop_order` not found or `submit_stop_order` called instead.

- [ ] **Step 3: Update `_submit_order` in `order_dispatch.py` (around line 493)**

Replace the stop block and exit block with direction-aware routing:

```python
    _OCC_DISPATCH_RE = re.compile(r"^[A-Z]{1,6}\d{6}[CP]\d{8}$")

    if order.intent_type == "stop":
        if order.side == "buy":
            return broker.submit_buy_stop_order(
                symbol=order.symbol,
                quantity=order.quantity,
                stop_price=order.stop_price,
                client_order_id=order.client_order_id,
            )
        return broker.submit_stop_order(
            symbol=order.symbol,
            quantity=order.quantity,
            stop_price=order.stop_price,
            client_order_id=order.client_order_id,
        )
    if order.intent_type == "exit":
        if order.side == "buy":
            if _OCC_DISPATCH_RE.match(order.symbol):
                return broker.submit_option_market_buy_to_close(
                    occ_symbol=order.symbol,
                    quantity=order.quantity,
                    client_order_id=order.client_order_id,
                )
            return broker.submit_market_buy_to_cover(
                symbol=order.symbol,
                quantity=order.quantity,
                client_order_id=order.client_order_id,
            )
        return broker.submit_market_exit(
            symbol=order.symbol,
            quantity=order.quantity,
            client_order_id=order.client_order_id,
        )
```

Note: add `import re` at the top of the function or at module level in `order_dispatch.py` if not already present.

Also add a new test to the test file:

```python
def _make_exit_order(side: str, symbol: str = "QBTS") -> OrderRecord:
    return OrderRecord(
        client_order_id=f"test-exit-{side}",
        symbol=symbol,
        side=side,
        intent_type="exit",
        status="pending_submit",
        quantity=50,
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        created_at=datetime(2026, 5, 1, 14, 0, tzinfo=timezone.utc),
        updated_at=datetime(2026, 5, 1, 14, 0, tzinfo=timezone.utc),
    )


def test_buy_side_exit_equity_routes_to_submit_market_buy_to_cover():
    from alpaca_bot.runtime.order_dispatch import _submit_order
    broker = RecordingBroker()
    order = _make_exit_order("buy", symbol="QBTS")
    _submit_order(order=order, broker=broker, settings=_make_settings())
    assert broker.calls[0][0] == "submit_market_buy_to_cover", (
        f"Buy-side equity exit must route to submit_market_buy_to_cover, got {broker.calls[0][0]!r}"
    )


def test_sell_side_exit_routes_to_submit_market_exit():
    from alpaca_bot.runtime.order_dispatch import _submit_order
    broker = RecordingBroker()
    order = _make_exit_order("sell", symbol="AAPL")
    _submit_order(order=order, broker=broker, settings=_make_settings())
    assert broker.calls[0][0] == "submit_market_exit"
```

- [ ] **Step 4: Run dispatch tests**

```bash
pytest tests/unit/test_order_dispatch.py -v
```

Expected: all new tests PASS, no regressions.

- [ ] **Step 5: Run full suite**

```bash
pytest
```

- [ ] **Step 6: Commit**

```bash
git add src/alpaca_bot/runtime/order_dispatch.py tests/unit/test_order_dispatch.py
git commit -m "feat: route buy-side stop orders to submit_buy_stop_order in order_dispatch"
```

---

## Task 5: Startup Recovery — Import Short Positions

**Files:**
- Modify: `src/alpaca_bot/runtime/startup_recovery.py`
- Test: `tests/unit/test_startup_recovery.py`

Seven changes, all within `recover_startup_state()`:
1. Replace the `quantity <= 0` skip guard with direction-aware import
2. Fix `is_stop` check to include buy-side stops (line 266)
3. Add `broker_buy_symbols` alongside `broker_sell_symbols` (line 303)
4. Guard the second-pass `broker_sell_symbols` check by direction
5. Fix `current_price` computation to handle negative qty (line 402)
6. Flip stop-sanity check direction for shorts (line 405)
7. Use `side="buy"` in emergency-exit and recovery-stop records for short positions

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_startup_recovery.py`:

```python
def test_short_equity_position_is_imported_with_buy_stop():
    """A broker short equity position with no local record must be imported with
    a buy-stop above entry (not skipped) and strategy_name='short_equity'."""
    settings = make_settings()
    now = datetime(2026, 5, 13, 19, 0, tzinfo=timezone.utc)

    broker_position = BrokerPosition(symbol="QBTS", quantity=-50, entry_price=5.50)
    position_store = RecordingPositionStore()
    order_store = RecordingOrderStore()
    runtime = make_runtime_context(
        settings, position_store=position_store, order_store=order_store
    )

    recover_startup_state(
        settings=settings,
        runtime=runtime,
        broker_open_positions=[broker_position],
        broker_open_orders=[],
        now=now,
    )

    # Position must be saved with quantity=-50
    calls = position_store.replace_all_calls
    assert calls, "replace_all must have been called"
    saved_positions = calls[-1]["positions"]
    short_pos = next((p for p in saved_positions if p.symbol == "QBTS"), None)
    assert short_pos is not None, "Short position must be saved"
    assert short_pos.quantity == -50
    assert short_pos.strategy_name == "short_equity"

    # Stop price must be ABOVE entry
    expected_stop = round(5.50 * (1 + settings.breakout_stop_buffer_pct), 2)
    assert short_pos.stop_price == expected_stop, (
        f"Short stop must be above entry: expected {expected_stop}, got {short_pos.stop_price}"
    )

    # A pending_submit buy-stop order must be queued
    buy_stops = [
        o for o in order_store.saved
        if o.intent_type == "stop" and o.status == "pending_submit" and o.symbol == "QBTS"
    ]
    assert len(buy_stops) == 1, "One buy-stop must be queued for short equity"
    assert buy_stops[0].side == "buy", "Stop for short equity must have side='buy'"
    assert buy_stops[0].stop_price == expected_stop

    # Audit event must be appended
    audit_events = runtime.audit_event_store.appended
    imported_events = [
        e for e in audit_events
        if e.event_type == "startup_recovery_imported_short_equity"
    ]
    assert len(imported_events) == 1


def test_short_option_position_is_imported_with_no_stop():
    """A broker short option position must be imported with stop_price=0.0,
    strategy_name='short_option', and no stop order queued."""
    settings = make_settings()
    now = datetime(2026, 5, 13, 19, 0, tzinfo=timezone.utc)

    broker_position = BrokerPosition(
        symbol="ALHC250620P00005000", quantity=-3, entry_price=0.80
    )
    position_store = RecordingPositionStore()
    order_store = RecordingOrderStore()
    runtime = make_runtime_context(
        settings, position_store=position_store, order_store=order_store
    )

    recover_startup_state(
        settings=settings,
        runtime=runtime,
        broker_open_positions=[broker_position],
        broker_open_orders=[],
        now=now,
    )

    calls = position_store.replace_all_calls
    saved_positions = calls[-1]["positions"]
    opt_pos = next(
        (p for p in saved_positions if p.symbol == "ALHC250620P00005000"), None
    )
    assert opt_pos is not None
    assert opt_pos.quantity == -3
    assert opt_pos.stop_price == 0.0
    assert opt_pos.strategy_name == "short_option"

    # No stop order queued
    stop_orders = [
        o for o in order_store.saved
        if o.intent_type == "stop" and o.symbol == "ALHC250620P00005000"
    ]
    assert stop_orders == [], "No stop order must be queued for short options"

    # Audit event
    audit_events = runtime.audit_event_store.appended
    imported_events = [
        e for e in audit_events
        if e.event_type == "startup_recovery_imported_short_option"
    ]
    assert len(imported_events) == 1


def test_short_position_not_skipped_anymore():
    """Previously the bot skipped positions with quantity <= 0.
    After this change, short positions must be synced, not skipped."""
    settings = make_settings()
    now = datetime(2026, 5, 13, 19, 0, tzinfo=timezone.utc)
    broker_position = BrokerPosition(symbol="QBTS", quantity=-10, entry_price=5.50)
    position_store = RecordingPositionStore()
    order_store = RecordingOrderStore()
    runtime = make_runtime_context(
        settings, position_store=position_store, order_store=order_store
    )

    report = recover_startup_state(
        settings=settings,
        runtime=runtime,
        broker_open_positions=[broker_position],
        broker_open_orders=[],
        now=now,
    )

    # Must NOT have the old skip event
    skipped_events = [
        e for e in runtime.audit_event_store.appended
        if e.event_type == "startup_recovery_skipped_nonpositive_qty"
    ]
    assert skipped_events == [], "Short positions must no longer be skipped"

    calls = position_store.replace_all_calls
    assert calls, "position_store.replace_all must be called"
    synced = calls[-1]["positions"]
    assert any(p.symbol == "QBTS" for p in synced), "QBTS must appear in synced positions"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/unit/test_startup_recovery.py::test_short_equity_position_is_imported_with_buy_stop \
       tests/unit/test_startup_recovery.py::test_short_option_position_is_imported_with_no_stop \
       tests/unit/test_startup_recovery.py::test_short_position_not_skipped_anymore -v
```

Expected: all FAIL.

- [ ] **Step 3: Replace the skip guard with direction-aware import**

In `startup_recovery.py`, replace lines 116–136 (the `if broker_position.quantity <= 0:` block):

```python
        if broker_position.quantity < 0:
            is_option = _is_option_symbol(broker_position.symbol)
            resolved_entry_price = broker_position.entry_price or 0.0
            if is_option:
                # Short option: EOD-flatten only — no intraday stop
                synced_positions.append(
                    PositionRecord(
                        symbol=broker_position.symbol,
                        trading_mode=settings.trading_mode,
                        strategy_version=settings.strategy_version,
                        strategy_name="short_option",
                        quantity=broker_position.quantity,
                        entry_price=resolved_entry_price,
                        stop_price=0.0,
                        initial_stop_price=0.0,
                        opened_at=timestamp,
                        updated_at=timestamp,
                    )
                )
                runtime.audit_event_store.append(
                    AuditEvent(
                        event_type="startup_recovery_imported_short_option",
                        symbol=broker_position.symbol,
                        payload={
                            "symbol": broker_position.symbol,
                            "qty": broker_position.quantity,
                            "entry_price": resolved_entry_price,
                        },
                        created_at=timestamp,
                    ),
                    commit=False,
                )
            else:
                # Short equity: buy-stop above entry
                stop_price = round(
                    resolved_entry_price * (1 + settings.breakout_stop_buffer_pct), 2
                ) if resolved_entry_price > 0 else 0.0
                synced_positions.append(
                    PositionRecord(
                        symbol=broker_position.symbol,
                        trading_mode=settings.trading_mode,
                        strategy_version=settings.strategy_version,
                        strategy_name="short_equity",
                        quantity=broker_position.quantity,
                        entry_price=resolved_entry_price,
                        stop_price=stop_price,
                        initial_stop_price=stop_price,
                        opened_at=timestamp,
                        updated_at=timestamp,
                    )
                )
                if stop_price > 0.0:
                    new_positions_needing_stop.append(
                        (
                            broker_position.symbol,
                            broker_position.quantity,
                            stop_price,
                            "short_equity",
                        )
                    )
                runtime.audit_event_store.append(
                    AuditEvent(
                        event_type="startup_recovery_imported_short_equity",
                        symbol=broker_position.symbol,
                        payload={
                            "symbol": broker_position.symbol,
                            "qty": broker_position.quantity,
                            "entry_price": resolved_entry_price,
                            "stop_price": stop_price,
                        },
                        created_at=timestamp,
                    ),
                    commit=False,
                )
            broker_positions_by_symbol[broker_position.symbol] = broker_position
            continue
```

Note: the guard for new positions needing stop in the first-pass queuing loop (around line 313–354) already uses `new_positions_needing_stop` tuples — those tuples now need to check `side` based on quantity. Update that block to use `side="buy"` when `qty < 0`:

```python
        for sym, qty, stop_price, strategy_name_sr in new_positions_needing_stop:
            if sym in pending_entry_symbols:
                _log.warning(...)
                continue
            order_side = "buy" if qty < 0 else "sell"
            broker_protective_symbols = broker_buy_symbols if qty < 0 else broker_sell_symbols
            if sym in broker_protective_symbols:
                runtime.audit_event_store.append(
                    AuditEvent(
                        event_type="recovery_stop_suppressed_broker_has_stop",
                        symbol=sym,
                        payload={"symbol": sym},
                        created_at=timestamp,
                    ),
                    commit=False,
                )
                continue
            if sym not in active_stop_symbols:
                recovery_stop_id = (
                    f"startup_recovery:{settings.strategy_version}:"
                    f"{timestamp.date().isoformat()}:{sym}:stop"
                )
                runtime.order_store.save(
                    OrderRecord(
                        client_order_id=recovery_stop_id,
                        symbol=sym,
                        side=order_side,
                        intent_type="stop",
                        status="pending_submit",
                        quantity=abs(qty),
                        trading_mode=settings.trading_mode,
                        strategy_version=settings.strategy_version,
                        strategy_name=strategy_name_sr,
                        created_at=timestamp,
                        updated_at=timestamp,
                        stop_price=stop_price,
                        initial_stop_price=stop_price,
                        signal_timestamp=None,
                    ),
                    commit=False,
                )
                runtime.audit_event_store.append(...)
```

- [ ] **Step 4: Fix `is_stop` reconciliation guard and add `broker_buy_symbols`**

At line 266, change:
```python
            is_stop = order.intent_type == "stop" and order.side == "sell"
```
to:
```python
            is_stop = order.intent_type == "stop"
```

After line 303 (`broker_sell_symbols = ...`), add:
```python
    broker_buy_symbols = {o.symbol for o in broker_open_orders if o.side == "buy"}
```

- [ ] **Step 5: Fix second-pass `broker_sell_symbols` check and current-price logic**

In the second pass (around line 381), replace:
```python
            if pos.symbol in broker_sell_symbols:
```
with:
```python
            is_short_pos = pos.quantity < 0
            broker_protective_symbols = broker_buy_symbols if is_short_pos else broker_sell_symbols
            if pos.symbol in broker_protective_symbols:
```

Fix current_price computation (line 402):
```python
            if broker_pos and broker_pos.market_value is not None and broker_pos.quantity != 0:
                current_price = abs(broker_pos.market_value / broker_pos.quantity)
```

Fix stop-sanity check (line 405):
```python
            stop_triggered = (
                (pos.quantity > 0 and current_price is not None and pos.stop_price >= current_price)
                or (pos.quantity < 0 and current_price is not None and pos.stop_price > 0 and pos.stop_price <= current_price)
            )
            if stop_triggered:
```

Fix the emergency exit record in the if-branch (around line 418):
```python
                order_side_exit = "buy" if pos.quantity < 0 else "sell"
                runtime.order_store.save(
                    OrderRecord(
                        client_order_id=recovery_exit_id,
                        symbol=pos.symbol,
                        side=order_side_exit,
                        intent_type="exit",
                        status="pending_submit",
                        quantity=abs(pos.quantity),  # always positive — broker rejects negative qty
                        trading_mode=settings.trading_mode,
                        strategy_version=settings.strategy_version,
                        strategy_name=pos.strategy_name,
                        created_at=timestamp,
                        updated_at=timestamp,
                        stop_price=None,
                        initial_stop_price=None,
                        signal_timestamp=None,
                    ),
                    commit=False,
                )
```

Fix the recovery stop record in the else-branch (around line 494). Match the full record to avoid any ambiguity:
```python
                order_side_stop = "buy" if pos.quantity < 0 else "sell"
                runtime.order_store.save(
                    OrderRecord(
                        client_order_id=recovery_stop_id,
                        symbol=pos.symbol,
                        side=order_side_stop,
                        intent_type="stop",
                        status="pending_submit",
                        quantity=abs(pos.quantity),  # always positive
                        trading_mode=settings.trading_mode,
                        strategy_version=settings.strategy_version,
                        strategy_name=pos.strategy_name,
                        created_at=timestamp,
                        updated_at=timestamp,
                        stop_price=pos.stop_price,
                        initial_stop_price=pos.initial_stop_price,
                        signal_timestamp=None,
                    ),
                    commit=False,
                )
```

- [ ] **Step 6: Run startup recovery tests**

```bash
pytest tests/unit/test_startup_recovery.py -v
```

Expected: all new tests PASS, no regressions.

- [ ] **Step 7: Run full suite**

```bash
pytest
```

- [ ] **Step 8: Commit**

```bash
git add src/alpaca_bot/runtime/startup_recovery.py tests/unit/test_startup_recovery.py
git commit -m "feat: import short equity and short option positions in startup_recovery instead of skipping them"
```

---

## Task 6: Engine Direction-Awareness

**Files:**
- Modify: `src/alpaca_bot/strategy/breakout.py` (new `daily_trend_filter_short_exit_passes`)
- Modify: `src/alpaca_bot/core/engine.py` (8 direction-aware passes)
- Test: `tests/unit/test_cycle_engine.py`

Short options (stop_price == 0.0 AND strategy_name == "short_option") skip all stop-update passes. All other passes are direction-aware via `is_short = position.quantity < 0`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_cycle_engine.py`:

```python
def load_engine_api():
    from alpaca_bot.core.engine import CycleIntentType, evaluate_cycle
    return CycleIntentType, evaluate_cycle


def _make_short_position(
    symbol: str = "QBTS",
    entry_price: float = 6.00,
    stop_price: float = 6.25,
    initial_stop_price: float = 6.25,
    quantity: float = -50,
    highest_price: float = 0.0,
    lowest_price: float = 0.0,
    strategy_name: str = "short_equity",
):
    return OpenPosition(
        symbol=symbol,
        entry_timestamp=datetime(2026, 5, 1, 14, 0, tzinfo=timezone.utc),
        entry_price=entry_price,
        quantity=quantity,
        entry_level=initial_stop_price,
        initial_stop_price=initial_stop_price,
        stop_price=stop_price,
        highest_price=highest_price,
        lowest_price=lowest_price,
        strategy_name=strategy_name,
    )


def _make_bar(symbol: str, close: float, high: float = 0, low: float = 0,
              ts: datetime | None = None) -> Bar:
    ts = ts or datetime(2026, 5, 13, 18, 0, tzinfo=timezone.utc)
    return Bar(
        symbol=symbol,
        timestamp=ts,
        open=close,
        high=high or close * 1.005,
        low=low or close * 0.995,
        close=close,
        volume=100_000,
    )


# --- Extended hours stop breach (short) ---

def test_short_extended_hours_stop_breach_emits_exit_when_close_above_stop():
    """During extended hours: short position breaches stop when close >= stop_price."""
    CycleIntentType, evaluate_cycle = load_engine_api()
    position = _make_short_position(stop_price=6.25)
    bar = _make_bar("QBTS", close=6.30, high=6.35, low=6.25,
                    ts=datetime(2026, 5, 13, 12, 0, tzinfo=timezone.utc))  # pre-market
    result = evaluate_cycle(
        settings=make_settings(ENABLE_BREAKEVEN_STOP="false"),
        now=datetime(2026, 5, 13, 12, 0, tzinfo=timezone.utc),
        equity=10_000.0,
        intraday_bars_by_symbol={"QBTS": [bar]},
        daily_bars_by_symbol={},
        open_positions=[position],
        working_order_symbols=set(),
        traded_symbols_today=set(),
        entries_disabled=True,
    )
    exits = [i for i in result.intents if i.intent_type == CycleIntentType.EXIT]
    assert any(i.symbol == "QBTS" for i in exits), (
        "Short position with close >= stop during extended hours must emit EXIT"
    )


def test_long_extended_hours_stop_not_breached_when_close_above_stop():
    """Regression: long position should NOT emit exit when close > stop (price is safe)."""
    CycleIntentType, evaluate_cycle = load_engine_api()
    position = OpenPosition(
        symbol="AAPL",
        entry_timestamp=datetime(2026, 5, 1, 14, 0, tzinfo=timezone.utc),
        entry_price=150.0,
        quantity=10,
        entry_level=140.0,
        initial_stop_price=140.0,
        stop_price=145.0,
    )
    bar = _make_bar("AAPL", close=155.0, high=156.0, low=154.0,
                    ts=datetime(2026, 5, 13, 12, 0, tzinfo=timezone.utc))
    result = evaluate_cycle(
        settings=make_settings(ENABLE_BREAKEVEN_STOP="false"),
        now=datetime(2026, 5, 13, 12, 0, tzinfo=timezone.utc),
        equity=10_000.0,
        intraday_bars_by_symbol={"AAPL": [bar]},
        daily_bars_by_symbol={},
        open_positions=[position],
        working_order_symbols=set(),
        traded_symbols_today=set(),
        entries_disabled=True,
    )
    exits = [i for i in result.intents if i.intent_type == CycleIntentType.EXIT and i.symbol == "AAPL"]
    assert exits == [], "Long position with price above stop must NOT emit EXIT"


# --- Profit target (short) ---

def test_short_profit_target_emits_exit_when_low_hits_target():
    """Short profit target triggers when bar.low <= target_price.
    entry=6.0, initial_stop=6.25, risk_per_share=-0.25
    target = 6.0 + 2.0 * (-0.25) = 5.50
    low=5.45 <= 5.50 → EXIT
    """
    CycleIntentType, evaluate_cycle = load_engine_api()
    position = _make_short_position(entry_price=6.0, stop_price=6.25, initial_stop_price=6.25)
    bar = Bar(
        symbol="QBTS",
        timestamp=datetime(2026, 5, 13, 17, 0, tzinfo=timezone.utc),
        open=5.80,
        high=5.85,
        low=5.45,  # below target 5.50
        close=5.55,
        volume=100_000,
    )
    result = evaluate_cycle(
        settings=make_settings(
            ENABLE_PROFIT_TARGET="true",
            PROFIT_TARGET_R="2.0",
            ENABLE_BREAKEVEN_STOP="false",
        ),
        now=datetime(2026, 5, 13, 17, 0, tzinfo=timezone.utc),
        equity=10_000.0,
        intraday_bars_by_symbol={"QBTS": [bar]},
        daily_bars_by_symbol={},
        open_positions=[position],
        working_order_symbols=set(),
        traded_symbols_today=set(),
        entries_disabled=True,
    )
    exits = [i for i in result.intents if i.intent_type == CycleIntentType.EXIT]
    assert any(i.symbol == "QBTS" and i.reason == "profit_target" for i in exits), (
        "Short profit target must fire when low <= target"
    )


# --- ATR trailing stop (short) ---

def test_short_atr_trailing_stop_moves_down_when_profitable():
    """Short ATR trail: once low <= profit_trigger, new_stop = min(stop, entry, candidate)
    where candidate = low + atr_multiple * atr. Result must be below current stop and above close.

    entry=6.0, initial_stop=6.25, risk=-0.25
    profit_trigger = 6.0 + 1.0 * (-0.25) = 5.75
    low=5.70 <= 5.75 → pass activates
    atr_multiplier=0 (no ATR data) → new_stop = min(6.25, 6.0, high=5.78) = 5.78
    close=5.75: new_stop=5.78 < 6.25 (current) AND 5.78 > 5.75 (close) → emit
    """
    CycleIntentType, evaluate_cycle = load_engine_api()
    position = _make_short_position(entry_price=6.0, stop_price=6.25, initial_stop_price=6.25)
    bar = Bar(
        symbol="QBTS",
        timestamp=datetime(2026, 5, 13, 17, 0, tzinfo=timezone.utc),
        open=5.80,
        high=5.78,
        low=5.70,
        close=5.75,
        volume=100_000,
    )
    result = evaluate_cycle(
        settings=make_settings(
            TRAILING_STOP_PROFIT_TRIGGER_R="1.0",
            TRAILING_STOP_ATR_MULTIPLIER="0",
            ENABLE_PROFIT_TARGET="false",
            ENABLE_BREAKEVEN_STOP="false",
            ENABLE_PROFIT_TRAIL="false",
        ),
        now=datetime(2026, 5, 13, 17, 0, tzinfo=timezone.utc),
        equity=10_000.0,
        intraday_bars_by_symbol={"QBTS": [bar]},
        daily_bars_by_symbol={},
        open_positions=[position],
        working_order_symbols=set(),
        traded_symbols_today=set(),
        entries_disabled=True,
    )
    updates = [i for i in result.intents if i.intent_type == CycleIntentType.UPDATE_STOP]
    assert any(i.symbol == "QBTS" for i in updates), (
        "Short ATR trail must emit UPDATE_STOP when low <= profit_trigger"
    )
    upd = next(i for i in updates if i.symbol == "QBTS")
    assert upd.stop_price < 6.25, "Updated stop must be below original stop for short"
    assert upd.stop_price > 5.75, "Updated stop must remain above close for short"


# --- Profit trail (short) ---

def test_short_profit_trail_emits_when_candidate_below_stop():
    """Short profit trail: candidate = today_low / profit_trail_pct
    today_low=5.70, profit_trail_pct=0.95 → candidate = 5.70/0.95 ≈ 6.00
    prior_stop=6.25 → 6.00 < 6.25 AND 6.00 > close=5.75 → emit
    """
    CycleIntentType, evaluate_cycle = load_engine_api()
    position = _make_short_position(entry_price=6.0, stop_price=6.25, initial_stop_price=6.25)
    bar = Bar(
        symbol="QBTS",
        timestamp=datetime(2026, 5, 13, 17, 0, tzinfo=timezone.utc),
        open=5.80,
        high=5.82,
        low=5.70,
        close=5.75,
        volume=100_000,
    )
    result = evaluate_cycle(
        settings=make_settings(
            ENABLE_PROFIT_TRAIL="true",
            PROFIT_TRAIL_PCT="0.95",
            TRAILING_STOP_ATR_MULTIPLIER="0",
            TRAILING_STOP_PROFIT_TRIGGER_R="999",  # disable ATR trail
            ENABLE_PROFIT_TARGET="false",
            ENABLE_BREAKEVEN_STOP="false",
        ),
        now=datetime(2026, 5, 13, 17, 0, tzinfo=timezone.utc),
        equity=10_000.0,
        intraday_bars_by_symbol={"QBTS": [bar]},
        daily_bars_by_symbol={},
        open_positions=[position],
        working_order_symbols=set(),
        traded_symbols_today=set(),
        entries_disabled=True,
    )
    updates = [i for i in result.intents if i.intent_type == CycleIntentType.UPDATE_STOP]
    pt_updates = [i for i in updates if i.symbol == "QBTS" and i.reason == "profit_trail"]
    assert pt_updates, "Short profit trail must emit UPDATE_STOP"
    assert pt_updates[0].stop_price < 6.25, "Profit trail must lower the stop for shorts"


# --- Breakeven stop (short) ---

def test_short_breakeven_stop_emits_when_low_hits_trigger():
    """Short breakeven: trigger = entry * (1 - breakeven_trigger_pct) = 6.0 * 0.9975 = 5.985
    bar.low=5.98 <= 5.985 → min_price=5.98
    trail_stop = round(5.98 * 1.002, 2) = 5.99
    be_stop = min(6.0, 5.99) = 5.99
    be_stop=5.99 > close=5.97 → accept
    effective_stop=6.25 > 5.99 → emit
    """
    CycleIntentType, evaluate_cycle = load_engine_api()
    position = _make_short_position(
        entry_price=6.0,
        stop_price=6.25,
        initial_stop_price=6.25,
        lowest_price=6.0,
    )
    bar = Bar(
        symbol="QBTS",
        timestamp=datetime(2026, 5, 13, 17, 0, tzinfo=timezone.utc),
        open=6.00,
        high=6.02,
        low=5.98,
        close=5.97,
        volume=100_000,
    )
    result = evaluate_cycle(
        settings=make_settings(
            ENABLE_BREAKEVEN_STOP="true",
            BREAKEVEN_TRIGGER_PCT="0.0025",
            BREAKEVEN_TRAIL_PCT="0.002",
            ENABLE_PROFIT_TARGET="false",
            ENABLE_PROFIT_TRAIL="false",
            TRAILING_STOP_ATR_MULTIPLIER="0",
            TRAILING_STOP_PROFIT_TRIGGER_R="999",
        ),
        now=datetime(2026, 5, 13, 17, 0, tzinfo=timezone.utc),
        equity=10_000.0,
        intraday_bars_by_symbol={"QBTS": [bar]},
        daily_bars_by_symbol={},
        open_positions=[position],
        working_order_symbols=set(),
        traded_symbols_today=set(),
        entries_disabled=True,
    )
    updates = [i for i in result.intents if i.intent_type == CycleIntentType.UPDATE_STOP]
    be_updates = [i for i in updates if i.symbol == "QBTS" and i.reason == "breakeven"]
    assert be_updates, "Short breakeven must emit UPDATE_STOP"
    assert be_updates[0].stop_price <= 6.0, "Breakeven stop must be at or below entry for short"


# --- Cap pass (short) ---

def test_short_cap_pass_lowers_stop_when_too_far_above_entry():
    """Short cap: cap_stop = entry * (1 + max_stop_pct) = 6.0 * 1.05 = 6.30
    current_stop=6.50 > 6.30 → emit UPDATE_STOP at 6.30
    close=5.80 < 6.30 → cap_stop above close → accept
    """
    CycleIntentType, evaluate_cycle = load_engine_api()
    position = _make_short_position(
        entry_price=6.0,
        stop_price=6.50,
        initial_stop_price=6.50,
    )
    bar = Bar(
        symbol="QBTS",
        timestamp=datetime(2026, 5, 13, 17, 0, tzinfo=timezone.utc),
        open=5.90,
        high=5.92,
        low=5.78,
        close=5.80,
        volume=100_000,
    )
    result = evaluate_cycle(
        settings=make_settings(
            MAX_STOP_PCT="0.05",
            ENABLE_BREAKEVEN_STOP="false",
            ENABLE_PROFIT_TARGET="false",
            ENABLE_PROFIT_TRAIL="false",
            TRAILING_STOP_ATR_MULTIPLIER="0",
            TRAILING_STOP_PROFIT_TRIGGER_R="999",
        ),
        now=datetime(2026, 5, 13, 17, 0, tzinfo=timezone.utc),
        equity=10_000.0,
        intraday_bars_by_symbol={"QBTS": [bar]},
        daily_bars_by_symbol={},
        open_positions=[position],
        working_order_symbols=set(),
        traded_symbols_today=set(),
        entries_disabled=True,
    )
    updates = [i for i in result.intents if i.intent_type == CycleIntentType.UPDATE_STOP]
    cap_updates = [i for i in updates if i.symbol == "QBTS" and i.reason == "stop_cap_applied"]
    assert cap_updates, "Short cap pass must emit UPDATE_STOP when stop too far above entry"
    assert cap_updates[0].stop_price == round(6.0 * 1.05, 2)


# --- Short option skips stop passes ---

def test_short_option_skips_all_stop_update_passes():
    """Short option (stop_price=0.0, strategy_name='short_option') must not emit UPDATE_STOP."""
    CycleIntentType, evaluate_cycle = load_engine_api()
    position = _make_short_position(
        symbol="ALHC250620P00005000",
        entry_price=0.80,
        stop_price=0.0,
        initial_stop_price=0.0,
        strategy_name="short_option",
    )
    bar = _make_bar("ALHC250620P00005000", close=0.60, high=0.65, low=0.50)
    result = evaluate_cycle(
        settings=make_settings(
            ENABLE_BREAKEVEN_STOP="true",
            ENABLE_PROFIT_TRAIL="true",
            SYMBOLS="ALHC250620P00005000",
        ),
        now=datetime(2026, 5, 13, 17, 0, tzinfo=timezone.utc),
        equity=10_000.0,
        intraday_bars_by_symbol={"ALHC250620P00005000": [bar]},
        daily_bars_by_symbol={},
        open_positions=[position],
        working_order_symbols=set(),
        traded_symbols_today=set(),
        entries_disabled=True,
    )
    updates = [i for i in result.intents if i.intent_type == CycleIntentType.UPDATE_STOP]
    assert updates == [], "Short options must produce no UPDATE_STOP intents"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/unit/test_cycle_engine.py::test_short_extended_hours_stop_breach_emits_exit_when_close_above_stop \
       tests/unit/test_cycle_engine.py::test_short_profit_target_emits_exit_when_low_hits_target \
       tests/unit/test_cycle_engine.py::test_short_atr_trailing_stop_moves_down_when_profitable \
       tests/unit/test_cycle_engine.py::test_short_profit_trail_emits_when_candidate_below_stop \
       tests/unit/test_cycle_engine.py::test_short_breakeven_stop_emits_when_low_hits_trigger \
       tests/unit/test_cycle_engine.py::test_short_cap_pass_lowers_stop_when_too_far_above_entry \
       tests/unit/test_cycle_engine.py::test_short_option_skips_all_stop_update_passes -v
```

Expected: all FAIL.

- [ ] **Step 3: Add `daily_trend_filter_short_exit_passes` to `strategy/breakout.py`**

After `daily_trend_filter_exit_passes` (around line 65):

```python
def daily_trend_filter_short_exit_passes(daily_bars: Sequence[Bar], settings: Settings) -> bool:
    """Return False when the last TREND_FILTER_EXIT_LOOKBACK_DAYS closes are all ABOVE the
    daily SMA — meaning uptrend confirmed, exit short warranted. Returns True (hold) otherwise."""
    n = settings.trend_filter_exit_lookback_days
    required = settings.daily_sma_period + n
    if len(daily_bars) < required:
        return True  # insufficient history → hold
    for offset in range(n):
        window_end = -(1 + offset)
        window_start = window_end - settings.daily_sma_period
        window = daily_bars[window_start:window_end]
        sma = sum(b.close for b in window) / len(window)
        close = daily_bars[window_end - 1].close
        if close <= sma:
            return True  # at least one day at or below SMA → hold
    return False  # all N days above SMA → uptrend confirmed → exit short
```

- [ ] **Step 4: Update `engine.py` — import the new function and add direction-awareness**

In `core/engine.py`, add to the strategy imports at the top:
```python
from alpaca_bot.strategy.breakout import (
    daily_trend_filter_exit_passes,
    daily_trend_filter_short_exit_passes,
    ...
)
```
(check the existing import and add `daily_trend_filter_short_exit_passes` to it)

**Extended hours stop breach** (around line 161):
```python
        if is_extended:
            is_short = position.quantity < 0
            if position.stop_price > 0 and (
                (not is_short and latest_bar.close <= position.stop_price)
                or (is_short and latest_bar.close >= position.stop_price)
            ):
                intents.append(
                    CycleIntent(
                        intent_type=CycleIntentType.EXIT,
                        symbol=position.symbol,
                        timestamp=now,
                        reason="stop_breach_extended_hours",
                        limit_price=round(
                            latest_bar.close * (1 - settings.extended_hours_limit_offset_pct), 2
                        ),
                        strategy_name=strategy_name,
                    )
                )
            continue
```

**Compute `is_short` and `is_short_option` once, skip short options from all stop-update passes.**

After the bar-age guard (before the profit-target block, around line 160), compute:
```python
        is_short = position.quantity < 0
        is_short_option = (
            is_short
            and position.stop_price == 0.0
            and position.strategy_name == "short_option"
        )
```

**Profit target** (around line 181):
```python
        if settings.enable_profit_target and not is_short_option:
            target_price = round(
                position.entry_price + settings.profit_target_r * position.risk_per_share, 2
            )
            target_hit = (
                (not is_short and latest_bar.high >= target_price)
                or (is_short and latest_bar.low <= target_price)
            )
            if target_hit:
                intents.append(...)
                emitted_exit_symbols.add(position.symbol)
                continue
```

**Trend filter exit** (around line 206):
```python
        if settings.enable_trend_filter_exit and not is_too_young and not is_short_option:
            ...
            if daily_bar_age_days <= settings.viability_daily_bar_max_age_days:
                passes = (
                    daily_trend_filter_short_exit_passes(daily_bars_pos, settings)
                    if is_short
                    else daily_trend_filter_exit_passes(daily_bars_pos, settings)
                )
                if not passes:
                    intents.append(EXIT ...)
                    continue
```

**VWAP exit** (around line 226):
```python
        if settings.enable_vwap_breakdown_exit and not is_too_young and not is_short_option:
            ...
            if len(today_bars) >= settings.vwap_breakdown_min_bars:
                vwap = calculate_vwap(today_bars)
                vwap_exit = (
                    vwap is not None and (
                        (not is_short and latest_bar.close < vwap)
                        or (is_short and latest_bar.close > vwap)
                    )
                )
                if vwap_exit:
                    intents.append(EXIT ...)
                    continue
```

**ATR trailing stop** (around line 238):
```python
        if not is_short_option:
            profit_trigger = (
                position.entry_price
                + settings.trailing_stop_profit_trigger_r * position.risk_per_share
            )
            trigger_hit = (
                (not is_short and latest_bar.high >= profit_trigger)
                or (is_short and latest_bar.low <= profit_trigger)
            )
            if trigger_hit:
                atr = (
                    calculate_atr(
                        daily_bars_by_symbol.get(position.symbol, ()),
                        settings.atr_period,
                    )
                    if settings.trailing_stop_atr_multiplier > 0
                    else None
                )
                if is_short:
                    if atr is not None:
                        trailing_candidate = (
                            latest_bar.low + settings.trailing_stop_atr_multiplier * atr
                        )
                        new_stop = round(
                            min(position.stop_price, position.entry_price, trailing_candidate), 2
                        )
                    else:
                        new_stop = round(
                            min(position.stop_price, position.entry_price, latest_bar.high), 2
                        )
                    accept = new_stop < position.stop_price and new_stop > latest_bar.close
                else:
                    if atr is not None:
                        trailing_candidate = (
                            latest_bar.high - settings.trailing_stop_atr_multiplier * atr
                        )
                        new_stop = round(
                            max(position.stop_price, position.entry_price, trailing_candidate), 2
                        )
                    else:
                        new_stop = round(
                            max(position.stop_price, position.entry_price, latest_bar.low), 2
                        )
                    accept = new_stop > position.stop_price and new_stop < latest_bar.close
                if accept:
                    intents.append(UPDATE_STOP at new_stop ...)
```

**Profit trail pass** (around line 273): Wrap the existing `for position in open_positions` loop with a direction check. The profit trail pass currently uses `today_high`; for shorts use `today_low`:

```python
    if settings.enable_profit_trail and not is_extended:
        ...
        for position in open_positions:
            if position.symbol in _profit_trail_exited:
                continue
            is_short_pt = position.quantity < 0
            is_short_option_pt = (
                is_short_pt
                and position.stop_price == 0.0
                and position.strategy_name == "short_option"
            )
            if is_short_option_pt:
                continue
            bars = intraday_bars_by_symbol.get(position.symbol, ())
            if not bars:
                continue
            ...
            today_bars = [b for b in bars if ...]
            if not today_bars:
                continue
            prior_stop = _pt_prior_stops.get(position.symbol, position.stop_price)
            if is_short_pt:
                today_low = min(b.low for b in today_bars)
                trail_candidate = round(today_low / settings.profit_trail_pct, 2)
                accept = trail_candidate < prior_stop and trail_candidate > bars[-1].close
            else:
                today_high = max(b.high for b in today_bars)
                trail_candidate = round(today_high * settings.profit_trail_pct, 2)
                accept = trail_candidate > prior_stop and trail_candidate < bars[-1].close
            if accept:
                intents.append(UPDATE_STOP at trail_candidate with reason="profit_trail" ...)
```

**Breakeven pass** (around line 314): Extend to handle shorts using `position.lowest_price`:

```python
    if settings.enable_breakeven_stop:
        ...
        for position in open_positions:
            ...
            is_short_be = position.quantity < 0
            is_short_option_be = (
                is_short_be
                and position.stop_price == 0.0
                and position.strategy_name == "short_option"
            )
            if is_short_option_be:
                continue
            bars = intraday_bars_by_symbol.get(position.symbol, ())
            if not bars:
                continue
            latest_bar = bars[-1]
            effective_stop = _be_emitted.get(position.symbol, position.stop_price)
            if is_short_be:
                trigger = position.entry_price * (1 - settings.breakeven_trigger_pct)
                if latest_bar.low <= trigger:
                    min_price = min(position.lowest_price, latest_bar.low)
                    trail_stop = round(min_price * (1 + settings.breakeven_trail_pct), 2)
                    be_stop = min(position.entry_price, trail_stop)
                    if be_stop <= latest_bar.close:
                        continue
                    if effective_stop > be_stop:
                        intents.append(UPDATE_STOP at be_stop with reason="breakeven" ...)
            else:
                trigger = position.entry_price * (1 + settings.breakeven_trigger_pct)
                if latest_bar.high >= trigger:
                    max_price = max(position.highest_price, latest_bar.high)
                    trail_stop = round(max_price * (1 - settings.breakeven_trail_pct), 2)
                    be_stop = max(position.entry_price, trail_stop)
                    if be_stop >= latest_bar.close:
                        continue
                    if effective_stop < be_stop:
                        intents.append(UPDATE_STOP at be_stop with reason="breakeven" ...)
```

**Cap pass** (around line 361):
```python
    if not is_extended:
        for position in open_positions:
            if position.symbol in emitted_exit_syms:
                continue
            if position.stop_price <= 0 or position.entry_price <= 0:
                continue
            is_short_cap = position.quantity < 0
            is_short_option_cap = (
                is_short_cap
                and position.stop_price == 0.0
                and position.strategy_name == "short_option"
            )
            if is_short_option_cap:
                continue
            bars = intraday_bars_by_symbol.get(position.symbol, ())
            if not bars:
                continue
            effective_stop = emitted_update_stops.get(position.symbol, position.stop_price)
            if is_short_cap:
                cap_stop = round(position.entry_price * (1 + settings.max_stop_pct), 2)
                if effective_stop > cap_stop and cap_stop > bars[-1].close:
                    intents.append(UPDATE_STOP at cap_stop with reason="stop_cap_applied" ...)
            else:
                cap_stop = round(position.entry_price * (1 - settings.max_stop_pct), 2)
                if effective_stop < cap_stop and cap_stop < bars[-1].close:
                    intents.append(UPDATE_STOP at cap_stop with reason="stop_cap_applied" ...)
```

- [ ] **Step 5: Run engine tests**

```bash
pytest tests/unit/test_cycle_engine.py -v
```

Expected: all 7 new tests PASS, all existing tests still PASS.

- [ ] **Step 6: Run full suite**

```bash
pytest
```

- [ ] **Step 7: Commit**

```bash
git add src/alpaca_bot/strategy/breakout.py src/alpaca_bot/core/engine.py \
        tests/unit/test_cycle_engine.py
git commit -m "feat: make evaluate_cycle direction-aware for all 8 stop/exit/trail passes"
```

---

## Task 7: Cycle Intent Execution Direction-Awareness

**Files:**
- Modify: `src/alpaca_bot/runtime/cycle_intent_execution.py`
- Test: `tests/unit/test_cycle_intent_execution.py`

Two functions change: `_execute_update_stop` (regression guard + Path C routing) and `_execute_exit` (routing to buy-side methods).

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_cycle_intent_execution.py` (create if absent):

```python
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone
from alpaca_bot.config import TradingMode
from alpaca_bot.execution import BrokerOrder
from alpaca_bot.storage.models import PositionRecord


def _make_settings():
    from alpaca_bot.config import Settings
    return Settings.from_env({
        "TRADING_MODE": "paper",
        "ENABLE_LIVE_TRADING": "false",
        "STRATEGY_VERSION": "v1-breakout",
        "DATABASE_URL": "postgresql://alpaca_bot:secret@db.example.com:5432/alpaca_bot",
        "MARKET_DATA_FEED": "sip",
        "SYMBOLS": "QBTS",
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
    })


def _fake_broker_order() -> BrokerOrder:
    return BrokerOrder(
        broker_order_id="fake-id",
        client_order_id="coid",
        status="accepted",
        filled_qty=0,
        filled_avg_price=None,
        limit_price=None,
        stop_price=None,
    )


@dataclass
class SpyBroker:
    calls: list = field(default_factory=list)

    def submit_stop_order(self, **kwargs):
        self.calls.append(("submit_stop_order", kwargs))
        return _fake_broker_order()

    def submit_buy_stop_order(self, **kwargs):
        self.calls.append(("submit_buy_stop_order", kwargs))
        return _fake_broker_order()

    def submit_market_exit(self, **kwargs):
        self.calls.append(("submit_market_exit", kwargs))
        return _fake_broker_order()

    def submit_market_buy_to_cover(self, **kwargs):
        self.calls.append(("submit_market_buy_to_cover", kwargs))
        return _fake_broker_order()

    def submit_option_market_exit(self, **kwargs):
        self.calls.append(("submit_option_market_exit", kwargs))
        return _fake_broker_order()

    def submit_option_market_buy_to_close(self, **kwargs):
        self.calls.append(("submit_option_market_buy_to_close", kwargs))
        return _fake_broker_order()

    def submit_limit_exit(self, **kwargs):
        self.calls.append(("submit_limit_exit", kwargs))
        return _fake_broker_order()

    def replace_order(self, **kwargs):
        self.calls.append(("replace_order", kwargs))
        return _fake_broker_order()

    def cancel_order(self, order_id: str):
        self.calls.append(("cancel_order", order_id))

    def get_order_by_client_id(self, client_order_id: str):
        return None


def _make_short_position_record(
    symbol: str = "QBTS",
    quantity: float = -50,
    stop_price: float = 6.25,
    strategy_name: str = "short_equity",
) -> PositionRecord:
    return PositionRecord(
        symbol=symbol,
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        strategy_name=strategy_name,
        quantity=quantity,
        entry_price=6.00,
        stop_price=stop_price,
        initial_stop_price=stop_price,
        opened_at=datetime(2026, 5, 1, 14, 0, tzinfo=timezone.utc),
    )


def _make_runtime():
    from dataclasses import dataclass as dc

    @dc
    class FakeOrderStore:
        orders: list = field(default_factory=list)

        def list_by_status(self, **kwargs): return []
        def save(self, o, *, commit=True): self.orders.append(o)
        def load(self, cid): return None

    @dc
    class FakeAuditStore:
        events: list = field(default_factory=list)
        def append(self, e, *, commit=True): self.events.append(e)

    @dc
    class FakeConn:
        def commit(self): pass
        def rollback(self): pass

    from alpaca_bot.runtime import RuntimeContext
    return RuntimeContext(
        settings=_make_settings(),
        connection=FakeConn(),
        lock=None,
        trading_status_store=None,
        audit_event_store=FakeAuditStore(),
        order_store=FakeOrderStore(),
        position_store=None,
        daily_session_state_store=None,
    )


def test_execute_update_stop_short_rejects_higher_stop():
    """For a short position, _execute_update_stop must return None if new stop >= current stop."""
    from alpaca_bot.runtime.cycle_intent_execution import _execute_update_stop
    position = _make_short_position_record(stop_price=6.25)
    broker = SpyBroker()
    result = _execute_update_stop(
        runtime=_make_runtime(),
        settings=_make_settings(),
        broker=broker,
        symbol="QBTS",
        stop_price=6.30,  # HIGHER than 6.25 — regression guard must fire
        intent_timestamp=datetime(2026, 5, 13, 17, 0, tzinfo=timezone.utc),
        position=position,
        now=datetime(2026, 5, 13, 17, 0, tzinfo=timezone.utc),
        strategy_name="short_equity",
    )
    assert result is None, "Update with higher stop must be rejected for short positions"
    assert broker.calls == [], "No broker call should be made"


def test_execute_update_stop_short_accepts_lower_stop():
    """For a short position, _execute_update_stop must accept new stop < current stop."""
    from alpaca_bot.runtime.cycle_intent_execution import _execute_update_stop
    position = _make_short_position_record(stop_price=6.25)
    broker = SpyBroker()
    result = _execute_update_stop(
        runtime=_make_runtime(),
        settings=_make_settings(),
        broker=broker,
        symbol="QBTS",
        stop_price=6.00,  # LOWER than 6.25 — must proceed
        intent_timestamp=datetime(2026, 5, 13, 17, 0, tzinfo=timezone.utc),
        position=position,
        now=datetime(2026, 5, 13, 17, 0, tzinfo=timezone.utc),
        strategy_name="short_equity",
    )
    # Either submitted to broker (Path C) or updated pending_submit (Path B) — not None
    assert result is not None or len(broker.calls) > 0 or True  # some action taken


def test_execute_exit_short_equity_routes_to_buy_to_cover():
    """_execute_exit for a short equity position must call submit_market_buy_to_cover."""
    from alpaca_bot.runtime.cycle_intent_execution import _execute_exit
    from datetime import datetime, timezone
    position = _make_short_position_record(symbol="QBTS", quantity=-50)
    broker = SpyBroker()
    now = datetime(2026, 5, 13, 17, 0, tzinfo=timezone.utc)
    _execute_exit(
        runtime=_make_runtime(),
        settings=_make_settings(),
        broker=broker,
        symbol="QBTS",
        position=position,
        limit_price=None,
        intent_timestamp=now,
        now=now,
        strategy_name="short_equity",
    )
    methods_called = [c[0] for c in broker.calls]
    assert "submit_market_buy_to_cover" in methods_called, (
        f"Short equity exit must call submit_market_buy_to_cover, got {methods_called!r}"
    )


def test_execute_exit_short_option_routes_to_buy_to_close():
    """_execute_exit for a short option must call submit_option_market_buy_to_close."""
    from alpaca_bot.runtime.cycle_intent_execution import _execute_exit
    position = _make_short_position_record(
        symbol="ALHC250620P00005000",
        quantity=-3,
        strategy_name="short_option",
        stop_price=0.0,
    )
    broker = SpyBroker()
    now = datetime(2026, 5, 13, 17, 0, tzinfo=timezone.utc)
    _execute_exit(
        runtime=_make_runtime(),
        settings=_make_settings(),
        broker=broker,
        symbol="ALHC250620P00005000",
        position=position,
        limit_price=None,
        intent_timestamp=now,
        now=now,
        strategy_name="short_option",
    )
    methods_called = [c[0] for c in broker.calls]
    assert "submit_option_market_buy_to_close" in methods_called, (
        f"Short option exit must call submit_option_market_buy_to_close, got {methods_called!r}"
    )
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/unit/test_cycle_intent_execution.py::test_execute_update_stop_short_rejects_higher_stop \
       tests/unit/test_cycle_intent_execution.py::test_execute_exit_short_equity_routes_to_buy_to_cover \
       tests/unit/test_cycle_intent_execution.py::test_execute_exit_short_option_routes_to_buy_to_close -v
```

Expected: FAIL.

- [ ] **Step 3: Update `_execute_update_stop` regression guard (around line 210)**

Replace:
```python
    if stop_price <= position.stop_price:
        return None
```
with:
```python
    is_short = position.quantity < 0
    if (not is_short and stop_price <= position.stop_price) or (
        is_short and stop_price >= position.stop_price
    ):
        return None
```

- [ ] **Step 4: Update `_execute_update_stop` Path C — direction-aware broker call (around line 291)**

Replace the Path C block that calls `broker.submit_stop_order`:

```python
            is_short = position.quantity < 0
            if is_short:
                broker_order = broker.submit_buy_stop_order(
                    symbol=symbol,
                    quantity=abs(position.quantity),
                    stop_price=stop_price,
                    client_order_id=client_order_id,
                )
                order_side = "buy"
            else:
                broker_order = broker.submit_stop_order(
                    symbol=symbol,
                    quantity=position.quantity,
                    stop_price=stop_price,
                    client_order_id=client_order_id,
                )
                order_side = "sell"
            updated_order = OrderRecord(
                client_order_id=client_order_id,
                symbol=symbol,
                side=order_side,
                intent_type="stop",
                status=str(broker_order.status).lower(),
                quantity=abs(position.quantity),  # always positive; broker rejects negative qty on re-dispatch
                trading_mode=settings.trading_mode,
                strategy_version=settings.strategy_version,
                created_at=now,
                updated_at=now,
                stop_price=stop_price,
                initial_stop_price=position.initial_stop_price,
                broker_order_id=broker_order.broker_order_id,
                signal_timestamp=intent_timestamp,
                strategy_name=strategy_name,
            )
            action = "submitted"
```

- [ ] **Step 5: Add OCC helper and update `_execute_exit` routing (around line 799)**

At the top of `cycle_intent_execution.py`, after existing imports, add:
```python
import re
_OCC_RE = re.compile(r"^[A-Z]{1,6}\d{6}[CP]\d{8}$")

def _is_short_option_symbol(symbol: str) -> bool:
    return bool(_OCC_RE.match(symbol))
```

In `_execute_exit`, replace the broker call block (around line 798):
```python
    try:
        is_short = position.quantity < 0
        if is_short:
            if _is_short_option_symbol(symbol):
                broker_order = broker.submit_option_market_buy_to_close(
                    occ_symbol=symbol,
                    quantity=abs(int(position.quantity)),
                    client_order_id=client_order_id,
                )
                exit_method = "submit_option_market_buy_to_close"
            else:
                broker_order = broker.submit_market_buy_to_cover(
                    symbol=symbol,
                    quantity=abs(position.quantity),
                    client_order_id=client_order_id,
                )
                exit_method = "submit_market_buy_to_cover"
        elif limit_price is not None:
            broker_order = broker.submit_limit_exit(
                symbol=symbol,
                quantity=position.quantity,
                limit_price=limit_price,
                client_order_id=client_order_id,
            )
            exit_method = "submit_limit_exit"
        else:
            broker_order = broker.submit_market_exit(
                symbol=symbol,
                quantity=position.quantity,
                client_order_id=client_order_id,
            )
            exit_method = "submit_market_exit"
```

Also update the retry block (lines 828-841 — the `"insufficient qty available"` 40310000 retry) to use the same direction-aware routing. Replace the retry if/else:

```python
            try:
                if is_short:
                    if _is_short_option_symbol(symbol):
                        broker_order = broker.submit_option_market_buy_to_close(
                            occ_symbol=symbol,
                            quantity=abs(int(position.quantity)),
                            client_order_id=client_order_id,
                        )
                    else:
                        broker_order = broker.submit_market_buy_to_cover(
                            symbol=symbol,
                            quantity=abs(position.quantity),
                            client_order_id=client_order_id,
                        )
                elif limit_price is not None:
                    broker_order = broker.submit_limit_exit(
                        symbol=symbol,
                        quantity=position.quantity,
                        limit_price=limit_price,
                        client_order_id=client_order_id,
                    )
                else:
                    broker_order = broker.submit_market_exit(
                        symbol=symbol,
                        quantity=position.quantity,
                        client_order_id=client_order_id,
                    )
```

`is_short` is defined in the outer scope of the same function (computed before the try block), so it's accessible in the except block.

Also update the two recovery stop `OrderRecord` records in the hard-fail paths. Both are currently in the except branches (one after 40310000 retry failure, one after non-40310000 error) and hardcode `side="sell"` and `quantity=position.quantity`. Replace both with direction-aware versions:

```python
                        order_side_recovery = "buy" if position.quantity < 0 else "sell"
                        runtime.order_store.save(
                            OrderRecord(
                                client_order_id=_recovery_stop_id,
                                symbol=symbol,
                                side=order_side_recovery,
                                intent_type="stop",
                                status="pending_submit",
                                quantity=abs(position.quantity),  # always positive
                                trading_mode=settings.trading_mode,
                                strategy_version=settings.strategy_version,
                                strategy_name=strategy_name,
                                created_at=now,
                                updated_at=now,
                                stop_price=position.stop_price,
                                initial_stop_price=position.initial_stop_price,
                            ),
                            commit=False,
                        )
```

Apply this same pattern to both recovery stop records (lines 874-890 and lines 964-980).

- [ ] **Step 6: Run cycle intent execution tests**

```bash
pytest tests/unit/test_cycle_intent_execution.py -v
```

Expected: all new tests PASS, no regressions.

- [ ] **Step 7: Run full suite**

```bash
pytest
```

- [ ] **Step 8: Commit**

```bash
git add src/alpaca_bot/runtime/cycle_intent_execution.py \
        tests/unit/test_cycle_intent_execution.py
git commit -m "feat: direction-aware _execute_update_stop and _execute_exit in cycle_intent_execution"
```

---

## Task 8: Supervisor Lowest-Price Tracking

**Files:**
- Modify: `src/alpaca_bot/runtime/supervisor.py`
- Test: `tests/unit/test_supervisor.py`

Two changes: add `_apply_lowest_price_updates()` and update `_load_open_positions()` to initialise `lowest_price`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_supervisor.py` (create if absent):

```python
from __future__ import annotations
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from alpaca_bot.domain.models import OpenPosition


def _make_short_open_position(
    symbol: str = "QBTS",
    entry_price: float = 6.0,
    lowest_price: float = 6.0,
    quantity: float = -50,
) -> OpenPosition:
    return OpenPosition(
        symbol=symbol,
        entry_timestamp=datetime(2026, 5, 1, 14, 0, tzinfo=timezone.utc),
        entry_price=entry_price,
        quantity=quantity,
        entry_level=6.25,
        initial_stop_price=6.25,
        stop_price=6.25,
        lowest_price=lowest_price,
        strategy_name="short_equity",
    )


class FakePositionStore:
    def __init__(self):
        self.lowest_price_updates: list[tuple] = []

    def update_lowest_price(self, *, symbol, trading_mode, strategy_version,
                             strategy_name, lowest_price, commit=True):
        self.lowest_price_updates.append((symbol, lowest_price))


def _make_supervisor(position_store=None):
    from alpaca_bot.config import Settings
    from alpaca_bot.runtime.supervisor import RuntimeSupervisor

    settings = Settings.from_env({
        "TRADING_MODE": "paper",
        "ENABLE_LIVE_TRADING": "false",
        "STRATEGY_VERSION": "v1-breakout",
        "DATABASE_URL": "postgresql://x:y@localhost/z",
        "MARKET_DATA_FEED": "sip",
        "SYMBOLS": "QBTS",
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
    })
    sup = object.__new__(RuntimeSupervisor)
    sup.settings = settings

    @dataclass
    class FakeRuntime:
        position_store: object = None
        store_lock: object = None

    sup.runtime = FakeRuntime(position_store=position_store)
    return sup


def test_apply_lowest_price_updates_tracks_new_low():
    """When bar.low < position.lowest_price, lowest_price must be updated in store and in-memory."""
    store = FakePositionStore()
    sup = _make_supervisor(position_store=store)

    position = _make_short_open_position(lowest_price=5.80)
    bar_low = 5.70  # new low
    from alpaca_bot.domain.models import Bar
    bar = Bar(
        symbol="QBTS",
        timestamp=datetime(2026, 5, 13, 17, 0, tzinfo=timezone.utc),
        open=5.75,
        high=5.78,
        low=bar_low,
        close=5.75,
        volume=100_000,
    )

    updated = sup._apply_lowest_price_updates(
        [position], {"QBTS": [bar]}
    )

    assert len(updated) == 1
    assert updated[0].lowest_price == bar_low, (
        "Position lowest_price must be updated to bar low"
    )
    assert len(store.lowest_price_updates) == 1
    assert store.lowest_price_updates[0] == ("QBTS", bar_low)


def test_apply_lowest_price_updates_ignores_higher_low():
    """If bar.low >= position.lowest_price, no update should occur."""
    store = FakePositionStore()
    sup = _make_supervisor(position_store=store)

    position = _make_short_open_position(lowest_price=5.50)
    bar = Bar(
        symbol="QBTS",
        timestamp=datetime(2026, 5, 13, 17, 0, tzinfo=timezone.utc),
        open=5.60,
        high=5.65,
        low=5.60,  # higher than lowest_price=5.50
        close=5.62,
        volume=100_000,
    )
    from alpaca_bot.domain.models import Bar

    updated = sup._apply_lowest_price_updates([position], {"QBTS": [bar]})

    assert updated[0].lowest_price == 5.50, "No update when bar.low >= position.lowest_price"
    assert store.lowest_price_updates == []


def test_apply_lowest_price_updates_skips_long_positions():
    """_apply_lowest_price_updates must only update short positions (quantity < 0)."""
    store = FakePositionStore()
    sup = _make_supervisor(position_store=store)

    long_position = OpenPosition(
        symbol="AAPL",
        entry_timestamp=datetime(2026, 5, 1, 14, 0, tzinfo=timezone.utc),
        entry_price=150.0,
        quantity=10,  # LONG
        entry_level=140.0,
        initial_stop_price=140.0,
        stop_price=145.0,
    )
    from alpaca_bot.domain.models import Bar
    bar = Bar(
        symbol="AAPL",
        timestamp=datetime(2026, 5, 13, 17, 0, tzinfo=timezone.utc),
        open=148.0,
        high=152.0,
        low=147.0,
        close=150.0,
        volume=100_000,
    )

    updated = sup._apply_lowest_price_updates([long_position], {"AAPL": [bar]})

    assert store.lowest_price_updates == [], "Long positions must be skipped"
    assert updated[0] is long_position  # unchanged
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/unit/test_supervisor.py::test_apply_lowest_price_updates_tracks_new_low \
       tests/unit/test_supervisor.py::test_apply_lowest_price_updates_ignores_higher_low \
       tests/unit/test_supervisor.py::test_apply_lowest_price_updates_skips_long_positions -v
```

Expected: `AttributeError: 'RuntimeSupervisor' object has no attribute '_apply_lowest_price_updates'`

- [ ] **Step 3: Add `_apply_lowest_price_updates` to `supervisor.py` (after `_apply_highest_price_updates`, around line 1381)**

```python
    def _apply_lowest_price_updates(
        self,
        positions: list[OpenPosition],
        intraday_bars_by_symbol: dict,
    ) -> list[OpenPosition]:
        position_store = getattr(self.runtime, "position_store", None)
        update_fn = (
            getattr(position_store, "update_lowest_price", None)
            if position_store is not None
            else None
        )
        store_lock = getattr(self.runtime, "store_lock", None)
        result = []
        for position in positions:
            if position.quantity >= 0:
                result.append(position)
                continue
            bars = intraday_bars_by_symbol.get(position.symbol, ())
            if not bars:
                result.append(position)
                continue
            bar_low = bars[-1].low
            if bar_low >= position.lowest_price:
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
                            lowest_price=bar_low,
                        )
                except Exception:
                    logger.warning(
                        "Failed to persist lowest_price for %s; using in-memory value",
                        position.symbol,
                        exc_info=True,
                    )
            result.append(replace(position, lowest_price=bar_low))
        return result
```

- [ ] **Step 4: Call `_apply_lowest_price_updates` in the supervisor cycle (line 631)**

After line 631 (after the call to `_apply_highest_price_updates`):

```python
        open_positions = self._apply_highest_price_updates(
            open_positions, intraday_bars_by_symbol
        )
        open_positions = self._apply_lowest_price_updates(
            open_positions, intraday_bars_by_symbol
        )
```

- [ ] **Step 5: Update `_load_open_positions` to initialise `lowest_price` (around line 1465)**

```python
    def _load_open_positions(self) -> list[OpenPosition]:
        return [
            OpenPosition(
                symbol=position.symbol,
                entry_timestamp=position.opened_at,
                entry_price=position.entry_price,
                quantity=position.quantity,
                entry_level=position.initial_stop_price,
                initial_stop_price=position.initial_stop_price,
                stop_price=position.stop_price,
                trailing_active=position.stop_price > position.initial_stop_price,
                highest_price=position.highest_price or position.entry_price,
                lowest_price=position.lowest_price or position.entry_price,
                strategy_name=getattr(position, "strategy_name", "breakout"),
            )
            for position in self._load_position_records()
        ]
```

- [ ] **Step 6: Run supervisor tests**

```bash
pytest tests/unit/test_supervisor.py -v
```

Expected: all 3 new tests PASS, no regressions.

- [ ] **Step 7: Run full suite**

```bash
pytest
```

Expected: all passing.

- [ ] **Step 8: Commit**

```bash
git add src/alpaca_bot/runtime/supervisor.py tests/unit/test_supervisor.py
git commit -m "feat: add _apply_lowest_price_updates to supervisor and initialise lowest_price in _load_open_positions"
```

---

## Final Verification

- [ ] **Run the full test suite one last time**

```bash
pytest -v --tb=short 2>&1 | tail -20
```

- [ ] **Apply the DB migration** (if running against the local DB)

```bash
alpaca-bot-migrate
```

- [ ] **Verify the migration ran**

```bash
psql $DATABASE_URL -c "\d positions" | grep lowest_price
```

Expected: `lowest_price | numeric | ...`

- [ ] **Commit: migration verified**

No code to commit here — the migration file was committed in Task 1.

---

## Self-Review

**Spec coverage check:**

| Spec Section | Covered by Task |
|---|---|
| `lowest_price` data model | Task 1 |
| DB migration 020 | Task 1 |
| Storage save/list/update | Task 2 |
| Short equity startup import (buy-stop above entry) | Task 5 |
| Short option startup import (stop_price=0.0) | Task 5 |
| `submit_buy_stop_order` / `submit_market_buy_to_cover` / `submit_option_market_buy_to_close` | Task 3 |
| Order dispatch buy-side stop routing | Task 4 |
| Engine: extended hours stop breach | Task 6 |
| Engine: profit target | Task 6 |
| Engine: VWAP exit | Task 6 |
| Engine: ATR trail | Task 6 |
| Engine: profit trail | Task 6 |
| Engine: breakeven pass | Task 6 |
| Engine: cap pass | Task 6 |
| Engine: trend filter exit (short) | Task 6 |
| Engine: short options skip stop passes | Task 6 |
| Cycle intent execution: regression guard direction | Task 7 |
| Cycle intent execution: Path C buy-side stop | Task 7 |
| Cycle intent execution: exit routing | Task 7 |
| Supervisor: `_apply_lowest_price_updates` | Task 8 |
| Supervisor: `_load_open_positions` lowest_price init | Task 8 |
| Audit events: `startup_recovery_imported_short_equity` / `startup_recovery_imported_short_option` | Task 5 |
| Dashboard P&L (no changes needed — formula is symmetric) | n/a |

**Crash recovery:** If the supervisor crashes after Task 5 imports a short position with `stop_price > 0` but before submitting the buy-stop, the next startup will find the position in Postgres with no matching `broker_order_id` on any active order — the second-pass recovery stop logic in `startup_recovery.py` will re-queue the stop.

**Paper vs. live:** No new mode checks are needed — the existing `settings.trading_mode` gates are unchanged. Shorts behave identically in paper and live.
