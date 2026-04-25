# Phase 1 — Financial Safety: Implementation Plan

**Spec:** `docs/superpowers/specs/2026-04-25-mvp-completion-design.md`
**Test command:** `pytest tests/unit/ -q`
**Goal:** Replace the `realized_loss = 0.0` stub with real P&L enforcement by tracking actual fill prices.

---

## Task 0 — Add DOWN migration convention to the migration runner

**File:** `src/alpaca_bot/storage/migrations.py`

In `discover_migrations()`, skip files whose stem ends with `.down`:

```python
for file in path.glob("*.sql"):
    if not file.is_file():
        continue
    prefix, _, rest = file.stem.partition("_")
    if not prefix.isdigit():
        continue
    if rest.endswith(".down"):  # exclude rollback files from auto-apply
        continue
    ...
```

Document the convention in a comment above the loop:
- Forward migration: `NNN_description.sql` — applied automatically by `alpaca-bot-migrate`
- Rollback migration: `NNN_description.down.sql` — applied manually when rolling back a deploy

---

## Task 1 — Add `fill_price` and `filled_quantity` columns to `order_records`

**File:** `migrations/003_add_fill_price_to_orders.sql` (NEW)

```sql
ALTER TABLE orders
ADD COLUMN IF NOT EXISTS fill_price NUMERIC;

ALTER TABLE orders
ADD COLUMN IF NOT EXISTS filled_quantity INTEGER;
```

**File:** `migrations/003_add_fill_price_to_orders.down.sql` (NEW)

```sql
ALTER TABLE orders DROP COLUMN IF EXISTS fill_price;
ALTER TABLE orders DROP COLUMN IF EXISTS filled_quantity;
```

**Why additive/nullable:** No existing rows are affected. `IF NOT EXISTS` makes the migration idempotent.

---

## Task 2 — Add `fill_price` and `filled_quantity` fields to `OrderRecord`

**File:** `src/alpaca_bot/storage/models.py`

Add two nullable fields to the `OrderRecord` dataclass (after `signal_timestamp`):

```python
fill_price: float | None = None
filled_quantity: int | None = None
```

Full updated dataclass:
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
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    stop_price: float | None = None
    limit_price: float | None = None
    initial_stop_price: float | None = None
    broker_order_id: str | None = None
    signal_timestamp: datetime | None = None
    fill_price: float | None = None
    filled_quantity: int | None = None
```

---

## Task 3 — Update `OrderStore` to persist and load `fill_price` / `filled_quantity`

**File:** `src/alpaca_bot/storage/repositories.py`

### 3a. `OrderStore.save()` — add two columns to INSERT and UPDATE SET

Replace the INSERT SQL and params tuple in `save()`:

```python
def save(self, order: OrderRecord) -> None:
    execute(
        self._connection,
        """
        INSERT INTO orders (
            client_order_id,
            symbol,
            side,
            intent_type,
            status,
            quantity,
            trading_mode,
            strategy_version,
            stop_price,
            limit_price,
            initial_stop_price,
            broker_order_id,
            signal_timestamp,
            fill_price,
            filled_quantity,
            created_at,
            updated_at
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
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
            order.client_order_id,
            order.symbol,
            order.side,
            order.intent_type,
            order.status,
            order.quantity,
            order.trading_mode.value,
            order.strategy_version,
            order.stop_price,
            order.limit_price,
            order.initial_stop_price,
            order.broker_order_id,
            order.signal_timestamp,
            order.fill_price,
            order.filled_quantity,
            order.created_at,
            order.updated_at,
        ),
    )
```

### 3b. `_row_to_order_record()` — extract into helper and add new columns

All three `SELECT` queries in `OrderStore` (`load`, `load_by_broker_order_id`, `list_by_status`, `list_recent`) repeat the same column list and mapping. Extract a helper and add the two new columns:

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
    filled_quantity
"""

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
        stop_price=row[10],
        limit_price=row[11],
        initial_stop_price=row[12],
        broker_order_id=row[13],
        signal_timestamp=row[14],
        fill_price=float(row[15]) if row[15] is not None else None,
        filled_quantity=int(row[16]) if row[16] is not None else None,
    )
```

Update all four `SELECT` calls to use `_ORDER_SELECT_COLUMNS` and call `_row_to_order_record(row)`.

### 3c. `OrderStore.daily_realized_pnl()` — new method

Add after `list_recent()`:

```python
def daily_realized_pnl(
    self,
    *,
    trading_mode: TradingMode,
    strategy_version: str,
    session_date: date,
) -> float:
    """Return sum of closed-trade PnL for today's session.

    For each exit order, looks up the most recent filled entry for the same
    symbol using a correlated subquery (safe even if the one-trade-per-symbol
    invariant is ever violated — uses the latest entry fill price).
    Returns 0.0 if no completed trades exist.
    """
    rows = fetch_all(
        self._connection,
        """
        SELECT
            x.symbol,
            (
                SELECT e.fill_price
                FROM orders e
                WHERE e.symbol = x.symbol
                  AND e.trading_mode = x.trading_mode
                  AND e.strategy_version = x.strategy_version
                  AND e.intent_type = 'entry'
                  AND e.fill_price IS NOT NULL
                  AND DATE(e.updated_at AT TIME ZONE 'America/New_York') = %s
                ORDER BY e.updated_at DESC
                LIMIT 1
            ) AS entry_fill,
            x.fill_price AS exit_fill,
            COALESCE(x.filled_quantity, x.quantity) AS qty
        FROM orders x
        WHERE x.trading_mode = %s
          AND x.strategy_version = %s
          AND x.intent_type IN ('stop', 'exit')
          AND x.fill_price IS NOT NULL
          AND DATE(x.updated_at AT TIME ZONE 'America/New_York') = %s
        """,
        (
            session_date,
            trading_mode.value,
            strategy_version,
            session_date,
        ),
    )
    return sum(
        (float(row[2]) - float(row[1])) * int(row[3])
        for row in rows
        if row[1] is not None and row[2] is not None
    )
```

---

## Task 4 — Persist `fill_price` / `filled_quantity` in `apply_trade_update()`

**File:** `src/alpaca_bot/runtime/trade_updates.py`

In the `saved_order = OrderRecord(...)` construction near line 82, add the two new fields — passing through from the existing `normalized` object:

```python
saved_order = OrderRecord(
    client_order_id=matched_order.client_order_id,
    symbol=matched_order.symbol,
    side=matched_order.side,
    intent_type=matched_order.intent_type,
    status=normalized.status,
    quantity=normalized.quantity or matched_order.quantity,
    trading_mode=matched_order.trading_mode,
    strategy_version=matched_order.strategy_version,
    created_at=matched_order.created_at,
    updated_at=timestamp,
    stop_price=matched_order.stop_price,
    limit_price=matched_order.limit_price,
    initial_stop_price=matched_order.initial_stop_price,
    broker_order_id=normalized.broker_order_id or matched_order.broker_order_id,
    signal_timestamp=matched_order.signal_timestamp,
    fill_price=(
        normalized.filled_avg_price
        if normalized.status in {"filled", "partially_filled"}
        else matched_order.fill_price
    ),
    filled_quantity=(
        normalized.filled_qty
        if normalized.status in {"filled", "partially_filled"} and normalized.filled_qty is not None
        else matched_order.filled_quantity
    ),
)
```

**Key invariant:** Only overwrite `fill_price` on `filled`/`partially_filled` events; preserve the matched order's existing value otherwise. This prevents a later `cancelled` event from erasing a previously recorded fill.

Also extend the `audit_payload` dict (near line 195) to include fill data when present:

```python
if saved_order.fill_price is not None:
    audit_payload["fill_price"] = saved_order.fill_price
if saved_order.filled_quantity is not None:
    audit_payload["filled_quantity"] = saved_order.filled_quantity
```

---

## Task 5 — Replace the `realized_loss = 0.0` stub in supervisor

**File:** `src/alpaca_bot/runtime/supervisor.py`

Replace lines 186–217 (the `# Fix 4: Daily loss limit enforcement` block) with:

```python
realized_pnl = self.runtime.order_store.daily_realized_pnl(
    trading_mode=self.settings.trading_mode,
    strategy_version=self.settings.strategy_version,
    session_date=session_date,
)
loss_limit = self.settings.daily_loss_limit_pct * account.equity
daily_loss_limit_breached = realized_pnl < -loss_limit
if daily_loss_limit_breached:
    self.runtime.audit_event_store.append(
        AuditEvent(
            event_type="daily_loss_limit_breached",
            payload={
                "realized_pnl": realized_pnl,
                "limit": loss_limit,
                "timestamp": timestamp.isoformat(),
            },
            created_at=timestamp,
        )
    )
```

**Note:** The supervisor's `RuntimeProtocol` and `OrderStoreProtocol` (in `trade_updates.py`) both need `daily_realized_pnl` added to the protocol if typed. Add it there too.

Also update `OrderStoreProtocol` in `runtime/trade_updates.py` to declare `fill_price` and `filled_quantity` are now fields on `OrderRecord` (no protocol change needed — it's a data field not a method).

---

## Task 6 — Update `OrderStoreProtocol` in supervisor / trade_updates to include `daily_realized_pnl`

**File:** `src/alpaca_bot/runtime/trade_updates.py`

`OrderStoreProtocol` (line 11) does not need `daily_realized_pnl` — it's only used by trade_updates. The supervisor accesses `self.runtime.order_store` directly (typed as the concrete `OrderStore`), so no protocol changes are needed there.

---

## Task 7 — New tests

### 7a. Fill price persistence test
**File:** `tests/unit/test_trade_updates.py` — add:

```python
def test_fill_price_persisted_on_filled_event():
    order = make_order(intent_type="entry", status="new")
    order_store = FakeOrderStore([order])
    update = {
        "event": "fill",
        "client_order_id": order.client_order_id,
        "symbol": order.symbol,
        "side": "buy",
        "status": "filled",
        "qty": 10,
        "filled_qty": 10,
        "filled_avg_price": 155.50,
        "timestamp": "2026-04-25T14:30:00+00:00",
    }
    apply_trade_update(settings=make_settings(), runtime=make_runtime(order_store), update=update)
    saved = order_store.saved[-1]
    assert saved.fill_price == 155.50
    assert saved.filled_quantity == 10


def test_fill_price_not_overwritten_on_cancel():
    order = make_order(intent_type="entry", status="filled", fill_price=155.50, filled_quantity=10)
    order_store = FakeOrderStore([order])
    update = {
        "event": "canceled",
        "client_order_id": order.client_order_id,
        "symbol": order.symbol,
        "side": "buy",
        "status": "cancelled",
        "qty": 10,
        "filled_qty": None,
        "filled_avg_price": None,
        "timestamp": "2026-04-25T15:00:00+00:00",
    }
    apply_trade_update(settings=make_settings(), runtime=make_runtime(order_store), update=update)
    saved = order_store.saved[-1]
    assert saved.fill_price == 155.50  # preserved, not erased
    assert saved.filled_quantity == 10
```

### 7b. `daily_realized_pnl` unit tests
**File:** `tests/unit/test_storage_db.py` (or a new `tests/unit/test_order_store.py`) — add:

Use an in-memory fake `ConnectionProtocol` (already established pattern) that returns rows, and verify:
- Two symbols, each with entry/exit fill → sum is correct
- Partial fill uses `filled_quantity`
- No completed trades → returns 0.0
- Exit with `NULL` fill_price excluded from sum

### 7c. Loss limit enforcement test
**File:** `tests/unit/test_runtime_supervisor.py` — add:

```python
def test_daily_loss_limit_disables_entries_when_breached():
    # order_store returns daily_realized_pnl = -600, equity = 10_000, limit_pct = 0.05
    # → limit = 500, loss exceeds limit → entries_disabled = True
    settings = make_settings(daily_loss_limit_pct=0.05)
    order_store = FakeOrderStore([], daily_pnl=-600.0)
    account = FakeAccount(equity=10_000.0)
    ...
    report = supervisor.run_cycle_once(now=lambda: fixed_now)
    assert report.entries_disabled is True
    breach_event = next(
        e for e in runtime.audit_event_store.events
        if e.event_type == "daily_loss_limit_breached"
    )
    assert breach_event.payload["realized_pnl"] == -600.0
    assert breach_event.payload["limit"] == 500.0


def test_daily_loss_limit_allows_entries_when_not_breached():
    settings = make_settings(daily_loss_limit_pct=0.05)
    order_store = FakeOrderStore([], daily_pnl=-100.0)  # well within limit
    account = FakeAccount(equity=10_000.0)
    ...
    report = supervisor.run_cycle_once(now=lambda: fixed_now)
    assert "daily_loss_limit_breached" not in [e.event_type for e in runtime.audit_event_store.events]
```

---

## Execution order

1. Task 1 — migration SQL file
2. Task 2 — `OrderRecord` model
3. Task 3a+3b — `OrderStore.save()` + SELECT helper
4. Task 3c — `OrderStore.daily_realized_pnl()`
5. Task 4 — `apply_trade_update()` fill persistence
6. Task 5 — supervisor stub replacement
7. Task 7 — all new tests
8. `pytest tests/unit/ -q` — must be green before Phase 2 begins

---

## Acceptance criteria

- `pytest tests/unit/ -q` passes with no failures
- `OrderRecord` has `fill_price` and `filled_quantity` fields
- `apply_trade_update()` sets them on `filled`/`partially_filled` events
- `OrderStore.daily_realized_pnl()` returns correct sum for a session with two closed trades
- Supervisor's `daily_loss_limit_breached` flag is driven by real PnL, not hardcoded 0.0
- Audit event `daily_loss_limit_breached` is emitted when PnL < -limit
