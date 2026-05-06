# Fractional Shares Support + Min Position Notional Guard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Raise capital deployment from ~2.7% to ~30% of equity by enabling fractional share sizing for supported symbols and adding a configurable minimum position notional to drop sub-threshold positions from non-fractionable assets.

**Architecture:** Add `fractionable_symbols: frozenset[str]` and `min_position_notional: float` to Settings; query Alpaca for fractionability at startup in `RuntimeSupervisor.from_settings()`; pass the flag through engine → sizing; change `calculate_position_size()` to return `float` and skip `math.floor()` when fractionable; update all quantity fields from `int` to `float` across models, repositories, and broker adapter; migrate DB columns from INTEGER to NUMERIC(18,4).

**Tech Stack:** Python, PostgreSQL (psycopg2, NUMERIC(18,4)), Alpaca Trading SDK (alpaca-py), pytest, dataclasses.replace().

---

## Files

| File | Change |
|---|---|
| `migrations/014_fractional_quantity.sql` | Create — INTEGER → NUMERIC(18,4) for 5 quantity columns |
| `migrations/014_fractional_quantity.down.sql` | Create — NUMERIC(18,4) → INTEGER (lossy rollback) |
| `tests/unit/test_settings_fractional_shares.py` | Create — Settings tests for new fields |
| `src/alpaca_bot/config/__init__.py` | Add `fractionable_symbols`, `min_position_notional` fields |
| `tests/unit/test_position_sizing.py` | Add fractional sizing tests |
| `src/alpaca_bot/risk/sizing.py` | Add `fractionable: bool = False` param; return `float` |
| `src/alpaca_bot/storage/models.py` | `OrderRecord.quantity`, `filled_quantity`, `PositionRecord.quantity`: `int` → `float` |
| `src/alpaca_bot/storage/repositories.py` | ~9 `int(row[...])` → `float(row[...])` |
| `src/alpaca_bot/domain/__init__.py` | `CycleIntent.quantity: int | None` → `float | None` |
| `tests/unit/test_cycle_engine.py` | Add fractionable + min_notional engine tests |
| `src/alpaca_bot/core/engine.py` | Pass `fractionable=` to sizing; add min_notional gate |
| `src/alpaca_bot/execution/alpaca.py` | Add `get_fractionable_symbols()`; `quantity: int` → `float`; round to 4dp at submission |
| `src/alpaca_bot/runtime/order_dispatch.py` | `int(broker_order.quantity)` → `float()`; fix `%d` log format |
| `tests/unit/test_runtime_supervisor.py` | Add `get_fractionable_symbols` to `FakeBroker` |
| `src/alpaca_bot/runtime/supervisor.py` | Populate `fractionable_symbols` via `dataclasses.replace()` |

---

## Task 1: Database Migration

**Files:**
- Create: `migrations/014_fractional_quantity.sql`
- Create: `migrations/014_fractional_quantity.down.sql`

The existing migrations use numbered `.sql` files; `013_add_strategy_weights.sql` is the latest. `NUMERIC(18,4)` gives exact decimal arithmetic (psycopg2 returns `decimal.Decimal` from these columns — handled by `float()` casts added in Task 4). `option_orders` rows are included defensively even though option quantities are always integer; the schema type change is harmless and keeps the schema consistent.

- [ ] **Step 1: Write the up migration**

Create `migrations/014_fractional_quantity.sql`:

```sql
-- 014_fractional_quantity.sql
ALTER TABLE orders
    ALTER COLUMN quantity TYPE NUMERIC(18,4) USING quantity::NUMERIC(18,4),
    ALTER COLUMN filled_quantity TYPE NUMERIC(18,4) USING filled_quantity::NUMERIC(18,4);

ALTER TABLE positions
    ALTER COLUMN quantity TYPE NUMERIC(18,4) USING quantity::NUMERIC(18,4);

ALTER TABLE option_orders
    ALTER COLUMN quantity TYPE NUMERIC(18,4) USING quantity::NUMERIC(18,4),
    ALTER COLUMN filled_quantity TYPE NUMERIC(18,4) USING filled_quantity::NUMERIC(18,4);
```

- [ ] **Step 2: Write the down migration**

Create `migrations/014_fractional_quantity.down.sql`:

```sql
-- 014_fractional_quantity.down.sql
ALTER TABLE orders
    ALTER COLUMN quantity TYPE INTEGER USING quantity::INTEGER,
    ALTER COLUMN filled_quantity TYPE INTEGER USING filled_quantity::INTEGER;

ALTER TABLE positions
    ALTER COLUMN quantity TYPE INTEGER USING quantity::INTEGER;

ALTER TABLE option_orders
    ALTER COLUMN quantity TYPE INTEGER USING quantity::INTEGER,
    ALTER COLUMN filled_quantity TYPE INTEGER USING filled_quantity::INTEGER;
```

Note: `USING quantity::INTEGER` truncates fractional values — lossy but safe. After rollback, any fractional-share orders from the forward migration would be truncated in place.

- [ ] **Step 3: Commit the migration files**

```bash
git add migrations/014_fractional_quantity.sql migrations/014_fractional_quantity.down.sql
git commit -m "migration: ALTER quantity columns from INTEGER to NUMERIC(18,4) for fractional share support"
```

---

## Task 2: Settings — Add fractionable_symbols and min_position_notional

**Files:**
- Create: `tests/unit/test_settings_fractional_shares.py`
- Modify: `src/alpaca_bot/config/__init__.py`

Settings tests follow the pattern in `tests/unit/test_settings_stop_cap.py`: a `_base()` helper returns a minimal valid env dict, tests call `Settings.from_env()`. The `fractionable_symbols` field is **not** read from env (it's populated at runtime by the supervisor) — tests verify it defaults to `frozenset()`. `min_position_notional` is read from `MIN_POSITION_NOTIONAL` env var.

- [ ] **Step 1: Write failing tests for the new Settings fields**

Create `tests/unit/test_settings_fractional_shares.py`:

```python
from alpaca_bot.config import Settings


def _base() -> dict:
    return {
        "ALPACA_PAPER_API_KEY": "key",
        "ALPACA_PAPER_SECRET_KEY": "secret",
        "SYMBOLS": "AAPL,MSFT",
        "TRADING_MODE": "paper",
        "STRATEGY_VERSIONS": "v1-breakout",
        "STRATEGY_WEIGHTS": "v1-breakout=1.0",
    }


def test_fractionable_symbols_defaults_to_empty_frozenset():
    settings = Settings.from_env(_base())
    assert settings.fractionable_symbols == frozenset()
    assert isinstance(settings.fractionable_symbols, frozenset)


def test_fractionable_symbols_not_read_from_env():
    """fractionable_symbols is an operational field — never from env."""
    env = {**_base(), "FRACTIONABLE_SYMBOLS": "AAPL,MSFT"}
    settings = Settings.from_env(env)
    assert settings.fractionable_symbols == frozenset()


def test_min_position_notional_defaults_to_zero():
    settings = Settings.from_env(_base())
    assert settings.min_position_notional == 0.0


def test_min_position_notional_read_from_env():
    settings = Settings.from_env({**_base(), "MIN_POSITION_NOTIONAL": "50.0"})
    assert settings.min_position_notional == 50.0


def test_min_position_notional_zero_disables_guard():
    settings = Settings.from_env({**_base(), "MIN_POSITION_NOTIONAL": "0.0"})
    assert settings.min_position_notional == 0.0


def test_min_position_notional_negative_raises():
    import pytest
    with pytest.raises(ValueError, match="MIN_POSITION_NOTIONAL"):
        Settings.from_env({**_base(), "MIN_POSITION_NOTIONAL": "-1.0"})
```

- [ ] **Step 2: Run tests to confirm red**

```bash
pytest tests/unit/test_settings_fractional_shares.py -v
```

Expected: **FAILED** — `fractionable_symbols` and `min_position_notional` attributes don't exist yet.

- [ ] **Step 3: Add fields to the Settings dataclass**

In `src/alpaca_bot/config/__init__.py`, add two fields after `profit_trail_pct` (the last field, at line ~144):

```python
    # Not from env — populated at startup after broker lookup
    fractionable_symbols: frozenset[str] = dataclasses.field(default_factory=frozenset)

    # From env — configurable threshold; 0.0 = disabled (default)
    min_position_notional: float = 0.0
```

- [ ] **Step 4: Add min_position_notional parsing in from_env()**

In `from_env()`, add after the last existing parsed field (before the closing `)` of the `Settings(...)` constructor call):

```python
        min_position_notional=float(values.get("MIN_POSITION_NOTIONAL", "0.0")),
```

- [ ] **Step 5: Add validation in validate()**

In `validate()`, add after the last existing assertion:

```python
        if self.min_position_notional < 0:
            raise ValueError(
                f"MIN_POSITION_NOTIONAL must be >= 0, got {self.min_position_notional}"
            )
```

- [ ] **Step 6: Run tests to confirm green**

```bash
pytest tests/unit/test_settings_fractional_shares.py -v
```

Expected: **7 PASSED**.

- [ ] **Step 7: Run the full test suite**

```bash
pytest -x
```

Expected: all tests PASS.

- [ ] **Step 8: Commit**

```bash
git add tests/unit/test_settings_fractional_shares.py src/alpaca_bot/config/__init__.py
git commit -m "feat: add fractionable_symbols and min_position_notional to Settings"
```

---

## Task 3: Position Sizing — Return float, add fractionable parameter

**Files:**
- Modify: `tests/unit/test_position_sizing.py`
- Modify: `src/alpaca_bot/risk/sizing.py`

Existing tests assert `qty == 50` etc. — these pass unchanged because `50.0 == 50` is `True` in Python. The `make_settings()` helper in `test_position_sizing.py` uses `SimpleNamespace(risk_per_trade_pct=..., max_position_pct=...)` — it doesn't need `fractionable_symbols` because sizing only reads the two numeric fields.

- [ ] **Step 1: Add failing tests for fractional sizing**

In `tests/unit/test_position_sizing.py`, append at the end of the file:

```python
def test_fractional_sizing_returns_float():
    settings = SimpleNamespace(risk_per_trade_pct=0.0025, max_position_pct=0.015)
    qty = calculate_position_size(
        equity=99_500.0,
        entry_price=3.00,
        stop_price=2.70,
        settings=settings,
        fractionable=True,
    )
    assert isinstance(qty, float)
    # risk_budget = $248.75, risk_per_share = $0.30 → raw qty = 829.17
    # max_notional = $1492.50 → capped at $1492.50 / $3.00 = 497.5
    assert qty == pytest.approx(497.5, rel=1e-4)


def test_fractional_sizing_no_floor_below_one():
    """For fractionable symbols, qty can be between 0 and 1."""
    settings = SimpleNamespace(risk_per_trade_pct=0.0025, max_position_pct=0.015)
    qty = calculate_position_size(
        equity=99_500.0,
        entry_price=500.00,
        stop_price=495.00,
        settings=settings,
        fractionable=True,
    )
    assert isinstance(qty, float)
    assert 0.0 < qty < 1.0


def test_non_fractional_sizing_floors_to_integer_value():
    settings = SimpleNamespace(risk_per_trade_pct=0.0025, max_position_pct=0.015)
    qty = calculate_position_size(
        equity=99_500.0,
        entry_price=3.00,
        stop_price=2.70,
        settings=settings,
        fractionable=False,
    )
    assert qty == float(int(qty))  # whole number value stored as float


def test_non_fractional_sizing_returns_zero_for_sub_one():
    """Non-fractionable symbol with < 1 share budget returns 0."""
    settings = SimpleNamespace(risk_per_trade_pct=0.0025, max_position_pct=0.015)
    qty = calculate_position_size(
        equity=99_500.0,
        entry_price=500.00,
        stop_price=495.00,
        settings=settings,
        fractionable=False,
    )
    assert qty == 0.0


def test_fractionable_returns_zero_for_negative_risk():
    settings = SimpleNamespace(risk_per_trade_pct=0.0025, max_position_pct=0.015)
    qty = calculate_position_size(
        equity=99_500.0,
        entry_price=3.00,
        stop_price=3.00,  # zero risk
        settings=settings,
        fractionable=True,
    )
    assert qty == 0.0
```

- [ ] **Step 2: Run the new tests to confirm red**

```bash
pytest tests/unit/test_position_sizing.py::test_fractional_sizing_returns_float tests/unit/test_position_sizing.py::test_fractional_sizing_no_floor_below_one tests/unit/test_position_sizing.py::test_non_fractional_sizing_floors_to_integer_value tests/unit/test_position_sizing.py::test_non_fractional_sizing_returns_zero_for_sub_one tests/unit/test_position_sizing.py::test_fractionable_returns_zero_for_negative_risk -v
```

Expected: **FAILED** — `calculate_position_size` doesn't accept a `fractionable` parameter yet.

- [ ] **Step 3: Rewrite calculate_position_size in sizing.py**

Replace the entire function in `src/alpaca_bot/risk/sizing.py`:

```python
def calculate_position_size(
    *,
    equity: float,
    entry_price: float,
    stop_price: float,
    settings,
    fractionable: bool = False,
) -> float:
    risk_per_share = entry_price - stop_price
    if risk_per_share <= 0:
        return 0.0
    risk_budget = equity * settings.risk_per_trade_pct
    quantity = risk_budget / risk_per_share
    if not fractionable:
        quantity = math.floor(quantity)
    if not fractionable and quantity < 1:
        return 0.0
    if quantity <= 0.0:
        return 0.0
    max_notional = equity * settings.max_position_pct
    if quantity * entry_price > max_notional:
        quantity = max_notional / entry_price
        if not fractionable:
            quantity = math.floor(quantity)
    return max(float(quantity), 0.0)
```

- [ ] **Step 4: Run tests to confirm green**

```bash
pytest tests/unit/test_position_sizing.py -v
```

Expected: all tests PASS (new + existing).

- [ ] **Step 5: Commit**

```bash
git add tests/unit/test_position_sizing.py src/alpaca_bot/risk/sizing.py
git commit -m "feat: calculate_position_size returns float and supports fractionable=True to skip math.floor"
```

---

## Task 4: Storage Types — quantity int → float across models, repositories, domain

**Files:**
- Modify: `src/alpaca_bot/domain/__init__.py`
- Modify: `src/alpaca_bot/storage/models.py`
- Modify: `src/alpaca_bot/storage/repositories.py`

No new tests needed — existing tests cover these models; integer literals satisfy `float` at runtime. The `int(row[...])` → `float(row[...])` changes in repositories ensure both pre-migration `int` and post-migration `Decimal` values from psycopg2 are handled. `OptionOrderRecord.quantity` stays `int` — option contracts always integer quantities.

- [ ] **Step 1: Change CycleIntent.quantity type in domain/__init__.py**

Find `CycleIntent` in `src/alpaca_bot/domain/__init__.py` and change:

```python
# Before
quantity: int | None = None

# After
quantity: float | None = None
```

- [ ] **Step 2: Change quantity fields in storage/models.py**

In `src/alpaca_bot/storage/models.py`:

`OrderRecord` — change two fields:
```python
# Before
quantity: int
filled_quantity: int | None = None

# After
quantity: float
filled_quantity: float | None = None
```

`PositionRecord` — change one field:
```python
# Before
quantity: int

# After
quantity: float
```

Leave `OptionOrderRecord.quantity: int` and `OptionOrderRecord.filled_quantity: int | None` unchanged.

- [ ] **Step 3: Change int(row[...]) → float(row[...]) in repositories.py**

In `src/alpaca_bot/storage/repositories.py`, apply these exact changes:

**`_row_to_order_record` (~line 219, 230):**
```python
# Before
quantity=int(row[5]),
# After
quantity=float(row[5]),
```
```python
# Before
filled_quantity=int(row[16]) if row[16] is not None else None,
# After
filled_quantity=float(row[16]) if row[16] is not None else None,
```

**PnL methods (grep for `int(row[3])`, `int(row[8])`, `int(row[1])`, `int(row[2])` in the context of order/position quantity columns):**

Run `grep -n "int(row\[" src/alpaca_bot/storage/repositories.py` to enumerate all call sites. For each `int(row[...])` that is a quantity/filled_quantity column (not a count, ID, or flag), change to `float(row[...])`.

The expected changes based on prior analysis:
- Line ~459: `int(row[3])` → `float(row[3])` (quantity in PnL calculation)
- Line ~461: `int(row[3])` → `float(row[3])` (quantity in PnL calculation)
- Line ~540: `qty = int(row[3])` → `qty = float(row[3])`
- Line ~643: `"qty": int(row[8])` → `"qty": float(row[8])`
- Line ~702: `int(row[1])` → `float(row[1])` (position quantity)
- Line ~764: `int(row[2])` → `float(row[2])` (position quantity)
- Line ~1152: `quantity=int(row[4])` → `quantity=float(row[4])` (PositionRecord builder)

Do NOT change:
- Line ~812: `int(row[...])` for win/loss counts (not a quantity)
- Line ~1684, ~1691: `int(row[...])` for `OptionOrderRecord.quantity` (stays int)

- [ ] **Step 4: Run the full test suite**

```bash
pytest -x
```

Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/alpaca_bot/domain/__init__.py src/alpaca_bot/storage/models.py src/alpaca_bot/storage/repositories.py
git commit -m "feat: change quantity fields from int to float across domain, models, and repositories for fractional share support"
```

---

## Task 5: Engine — Pass fractionable flag + add min_notional gate

**Files:**
- Modify: `tests/unit/test_cycle_engine.py`
- Modify: `src/alpaca_bot/core/engine.py`

The engine's `evaluate_cycle()` must remain pure. `fractionable_symbols` is already loaded into the frozen `Settings` object before the engine is called — no I/O inside the engine. The `make_settings()` helper in `test_cycle_engine.py` uses `Settings.from_env()` with a full env dict; `fractionable_symbols` defaults to `frozenset()` so existing tests are unaffected.

- [ ] **Step 1: Add failing engine tests**

In `tests/unit/test_cycle_engine.py`, append at the end of the file:

```python
# ---------------------------------------------------------------------------
# Fractional shares + min_notional gate
# ---------------------------------------------------------------------------

def test_fractionable_symbol_produces_larger_position():
    """Symbol in fractionable_symbols bypasses math.floor and reaches max_position_pct."""
    settings_env = {
        **_base_env(),
        "SYMBOLS": "CLOV",
        "FRACTIONABLE_SYMBOLS_IN_TEST": "",  # not a real field — fractionable_symbols set below
    }
    settings = Settings.from_env(settings_env)
    import dataclasses
    settings = dataclasses.replace(settings, fractionable_symbols=frozenset({"CLOV"}))

    # CLOV: entry=$3.00, stop=$2.70 → risk_per_share=$0.30
    # risk_budget = equity * 0.0025; max_notional = equity * 0.015
    # With fractionable: qty = min(risk_budget/0.30, max_notional/3.00)
    # At any equity > 0, qty should be > 1 (avoids the integer-floor = 1 trap)
    equity = 99_500.0
    # max_notional = 1492.50 → qty = 497.5
    from alpaca_bot.domain import Bar
    from datetime import datetime, timezone
    now = datetime(2026, 4, 24, 15, 0, tzinfo=timezone.utc)

    result = evaluate_cycle(
        settings=settings,
        equity=equity,
        intraday_bars={"CLOV": [
            Bar(symbol="CLOV", timestamp=now, open=2.80, high=3.10, low=2.75, close=3.00, volume=500_000),
        ]},
        daily_bars={"CLOV": []},
        open_positions=[],
        open_orders=[],
        now=now,
    )

    entry_intents = [i for i in result.intents if i.intent_type.name == "ENTRY" and i.symbol == "CLOV"]
    if entry_intents:
        qty = entry_intents[0].quantity
        assert qty is not None
        assert qty > 1.0, f"Expected fractional qty > 1, got {qty}"


def test_min_notional_gate_drops_tiny_non_fractionable_position():
    """Non-fractionable symbol below min_position_notional threshold is dropped."""
    settings_env = _base_env()
    settings_env["SYMBOLS"] = "CLOV"
    settings_env["MIN_POSITION_NOTIONAL"] = "100.0"
    settings = Settings.from_env(settings_env)
    # fractionable_symbols is empty (default) → CLOV treated as non-fractionable
    # integer sizing for cheap stock + 0.25% risk gives tiny qty * $3 << $100

    equity = 99_500.0
    from alpaca_bot.domain import Bar
    from datetime import datetime, timezone
    now = datetime(2026, 4, 24, 15, 0, tzinfo=timezone.utc)

    result = evaluate_cycle(
        settings=settings,
        equity=equity,
        intraday_bars={"CLOV": [
            Bar(symbol="CLOV", timestamp=now, open=2.80, high=3.10, low=2.75, close=3.00, volume=500_000),
        ]},
        daily_bars={"CLOV": []},
        open_positions=[],
        open_orders=[],
        now=now,
    )

    entry_intents = [i for i in result.intents if i.intent_type.name == "ENTRY" and i.symbol == "CLOV"]
    # If an entry was generated, its notional must be >= min_position_notional
    for intent in entry_intents:
        if intent.quantity is not None and intent.limit_price is not None:
            notional = intent.quantity * intent.limit_price
            assert notional >= 100.0, f"Tiny position leaked through min_notional gate: {notional}"
```

- [ ] **Step 2: Run the new tests to see their current state**

```bash
pytest tests/unit/test_cycle_engine.py::test_fractionable_symbol_produces_larger_position tests/unit/test_cycle_engine.py::test_min_notional_gate_drops_tiny_non_fractionable_position -v
```

Note: these tests may pass or fail depending on whether the strategy generates signals for CLOV given the test bar data. The tests are structured to only assert if an intent is actually generated — the min_notional test verifies no tiny position leaks through.

- [ ] **Step 3: Update the engine's sizing call in engine.py**

In `src/alpaca_bot/core/engine.py`, locate the `calculate_position_size` call in the entry-sizing block (lines ~413–422). Replace:

```python
quantity = calculate_position_size(
    equity=equity,
    entry_price=signal.limit_price,
    stop_price=effective_initial_stop,
    settings=settings,
)
if quantity < 1:
    continue
```

With:

```python
fractionable = signal.symbol in settings.fractionable_symbols
quantity = calculate_position_size(
    equity=equity,
    entry_price=signal.limit_price,
    stop_price=effective_initial_stop,
    settings=settings,
    fractionable=fractionable,
)
if quantity <= 0.0:
    continue
if settings.min_position_notional > 0 and quantity * signal.limit_price < settings.min_position_notional:
    continue
```

- [ ] **Step 4: Run the full test suite**

```bash
pytest -x
```

Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/unit/test_cycle_engine.py src/alpaca_bot/core/engine.py
git commit -m "feat: engine passes fractionable flag to sizing and applies min_position_notional gate"
```

---

## Task 6: Broker Adapter — get_fractionable_symbols, quantity float, 4dp rounding

**Files:**
- Modify: `src/alpaca_bot/execution/alpaca.py`

There are no TDD-runnable unit tests for the broker adapter (it talks to the Alpaca SDK). This task is structural: add `get_fractionable_symbols()`, change `BrokerOrder.quantity` and `BrokerPosition.quantity` to `float`, update all submission method signatures and private helpers.

- [ ] **Step 1: Change BrokerOrder and BrokerPosition quantity fields to float**

In `src/alpaca_bot/execution/alpaca.py`:

`BrokerOrder` (~line 90):
```python
# Before
quantity: int

# After
quantity: float
```

`BrokerPosition` (~line 96):
```python
# Before
quantity: int

# After
quantity: float
```

- [ ] **Step 2: Change int(float(...)) casts to float() in list_open_orders and list_positions**

`list_open_orders` (~line 240):
```python
# Before
quantity=int(float(order.qty)),

# After
quantity=float(order.qty),
```

`list_positions` (~line 250):
```python
# Before
quantity=int(float(position.qty)),

# After
quantity=float(position.qty),
```

- [ ] **Step 3: Change _parse_broker_order (~line 833)**

```python
# Before
quantity=int(float(getattr(raw, "qty"))),

# After
quantity=float(getattr(raw, "qty", 0)),
```

- [ ] **Step 4: Change all submit_* method signatures from int to float**

Change `quantity: int | None = None` → `quantity: float | None = None` in:
- `submit_stop_limit_entry` (~line 269)
- `submit_limit_entry` (~line 292)
- `submit_stop_order` (~line 313)
- `submit_market_exit` (~line 332)
- `submit_limit_exit` (~line 354)

Also change the local `qty: int | None = None` variable in any of these methods to `qty: float | None = None` where applicable.

Leave `submit_option_limit_entry` and `submit_option_market_exit` with `quantity: int` — options always integer.

- [ ] **Step 5: Add round(quantity, 4) and change signatures in private helpers**

In `_stop_limit_order_request` (~line 838):
```python
# Before
def _stop_limit_order_request(self, *, symbol: str, quantity: int, ...):
    ...

# After
def _stop_limit_order_request(self, *, symbol: str, quantity: float, ...):
    qty = round(quantity, 4)
    # use qty (not quantity) in the SDK/dict request construction below
```

Apply the same pattern to:
- `_stop_order_request` (~line 876)
- `_market_order_request` (~line 942)
- `_build_extended_hours_limit_order` (~line 969)

For each helper: change `quantity: int` → `quantity: float` in the signature, add `qty = round(quantity, 4)` at the top of the function body, use `qty` (not `quantity`) when building the SDK request or fallback dict.

- [ ] **Step 6: Change _resolve_order_quantity return type and parameters**

`_resolve_order_quantity` (~line 1011):
```python
# Before
def _resolve_order_quantity(
    self, *, quantity: int | None, position_quantity: int | None
) -> int:

# After
def _resolve_order_quantity(
    self, *, quantity: float | None, position_quantity: float | None
) -> float:
```

- [ ] **Step 7: Add get_fractionable_symbols method**

Add the following method to `AlpacaBroker` (place it near `list_open_orders` or after broker utility methods):

```python
def get_fractionable_symbols(self, symbols: Sequence[str]) -> frozenset[str]:
    """Return the subset of symbols that Alpaca supports for fractional trading."""
    result = set()
    for symbol in symbols:
        try:
            asset = self._trading_client.get_asset(symbol)
            if asset.fractionable:
                result.add(symbol)
        except Exception:
            pass  # non-fractionable or asset not found; default to integer sizing
    return frozenset(result)
```

Ensure `from typing import Sequence` is present in the imports (it likely already is).

- [ ] **Step 8: Run the full test suite**

```bash
pytest -x
```

Expected: all tests PASS.

- [ ] **Step 9: Commit**

```bash
git add src/alpaca_bot/execution/alpaca.py
git commit -m "feat: broker adapter supports fractional quantities — get_fractionable_symbols(), float qty, 4dp rounding at submission"
```

---

## Task 7: Wiring — Supervisor populates fractionable_symbols; order_dispatch fixes

**Files:**
- Modify: `tests/unit/test_runtime_supervisor.py`
- Modify: `src/alpaca_bot/runtime/supervisor.py`
- Modify: `src/alpaca_bot/runtime/order_dispatch.py`

The supervisor is the only place that calls `get_fractionable_symbols()`. `Settings` is frozen; `dataclasses.replace()` creates a new instance with the updated field. `order_dispatch.py` casts `broker_order.quantity` to `int` — this must become `float`.

- [ ] **Step 1: Add get_fractionable_symbols to FakeBroker in supervisor tests**

In `tests/unit/test_runtime_supervisor.py`, find `FakeBroker` (~line 237). Add the method:

```python
class FakeBroker:
    # ... existing methods ...

    def get_fractionable_symbols(self, symbols) -> frozenset:
        return frozenset()
```

- [ ] **Step 2: Run the supervisor tests to confirm they still pass with the existing code**

```bash
pytest tests/unit/test_runtime_supervisor.py -v
```

Expected: PASS (FakeBroker now has the method; supervisor doesn't call it yet).

- [ ] **Step 3: Update imports in supervisor.py**

In `src/alpaca_bot/runtime/supervisor.py`, ensure `replace` is imported from `dataclasses`. Find the existing `from dataclasses import dataclass` import and change to:

```python
from dataclasses import dataclass, replace
```

(If `replace` is already imported, skip this step.)

- [ ] **Step 4: Add fractionable_symbols lookup in from_settings()**

In `src/alpaca_bot/runtime/supervisor.py`, in `from_settings()`, after broker creation, add:

```python
broker = AlpacaBroker.from_settings(settings)
fractionable = broker.get_fractionable_symbols(settings.symbols)
settings = replace(settings, fractionable_symbols=fractionable)
```

The exact insertion point is after `broker = AlpacaBroker.from_settings(settings)` and before any subsequent use of `settings` in the constructor.

- [ ] **Step 5: Fix int(broker_order.quantity) in order_dispatch.py**

In `src/alpaca_bot/runtime/order_dispatch.py`:

Find `int(broker_order.quantity)` (~line 357) and change:

```python
# Before
quantity=int(broker_order.quantity),

# After
quantity=float(broker_order.quantity),
```

Also find the `%d` format specifier in the log message at ~line 342 that formats a quantity and change it to `%g`:

```python
# Before
logger.info("... quantity=%d ...", ..., order.quantity, ...)

# After
logger.info("... quantity=%g ...", ..., order.quantity, ...)
```

(Use `grep -n "%d" src/alpaca_bot/runtime/order_dispatch.py` to find the exact line.)

- [ ] **Step 6: Run the supervisor tests to confirm green**

```bash
pytest tests/unit/test_runtime_supervisor.py -v
```

Expected: all PASS.

- [ ] **Step 7: Run the full test suite**

```bash
pytest
```

Expected: all tests PASS.

- [ ] **Step 8: Commit**

```bash
git add tests/unit/test_runtime_supervisor.py src/alpaca_bot/runtime/supervisor.py src/alpaca_bot/runtime/order_dispatch.py
git commit -m "feat: supervisor populates fractionable_symbols at startup; order_dispatch handles float quantities"
```

---

## Verification

After all tasks, run the full suite one final time:

```bash
pytest
```

Expected: all existing tests pass + new tests from Tasks 2, 3, and 5 pass.

**Expected outcome in production:** After deploying and applying migration 014, with fractionable symbols, `calculate_position_size` returns up to `max_notional / entry_price` (4dp) rather than `math.floor(...)`. At $99.5K equity, `max_position_pct=0.015`: `max_notional = $1,492.50`. For 20 slots × $1,492.50 = $29,850 / $99,500 = **30.0%** — exactly at the exposure cap.

**Migration application:**

```bash
alpaca-bot-migrate
```

Run this after deploying the code changes. The migration is non-destructive for any existing integer values (integer → NUMERIC(18,4) is lossless).
