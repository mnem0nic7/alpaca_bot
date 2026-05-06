---
title: Fractional Shares Support + Min Position Notional Guard
date: 2026-05-06
status: approved
---

# Fractional Shares Support + Min Position Notional Guard

## Goal

Raise capital deployment from ~2.7% to ~30% of equity by enabling fractional share sizing
for supported symbols. Add a configurable minimum position notional to drop sub-threshold
positions that survive the integer fallback for non-fractionable assets.

---

## Root Cause

`calculate_position_size()` currently returns `int` via `math.floor()`. With
`RISK_PER_TRADE_PCT=0.0025` (~$249 budget on $99.5K equity) and a low-priced, wide-stop
watchlist, the floor frequently produces 1–3 shares at $2–$5/share = $5–$15 positions.
Each slot is funded but deploys almost nothing. 17 of 20 slots filled = 17 tiny positions
= $2.67K deployed out of $29.9K max exposure (30% × $99.5K).

---

## Design

### Component Overview

| Component | File(s) | Change |
|---|---|---|
| DB migration | `migrations/014_fractional_quantity.sql` + `.down.sql` | INTEGER → NUMERIC(18,4) for 5 columns |
| Settings | `config/__init__.py` | Add `fractionable_symbols: frozenset[str]`, `min_position_notional: float` |
| Sizing | `risk/sizing.py` | Add `fractionable: bool = False` param; return `float` always |
| Domain types | `domain/__init__.py` | No change — `CycleIntent` carries no quantity field; entry qty is computed at dispatch |
| Storage models | `storage/models.py` | `OrderRecord.quantity: int` → `float`, `OrderRecord.filled_quantity: int\|None` → `float\|None`, `PositionRecord.quantity: int` → `float` |
| Repositories | `storage/repositories.py` | ~10 `int(row[...])` → `float(row[...])` casts |
| Engine | `core/engine.py` | Pass `fractionable=` to sizing; add min_notional gate |
| Broker | `execution/alpaca.py` | Add `get_fractionable_symbols()`, `quantity: int` → `float`, round to 4dp at submission |
| Supervisor | `runtime/supervisor.py` | Populate `fractionable_symbols` via `dataclasses.replace()` in `from_settings()` |

---

## Section 1 — Database Migration

**File:** `migrations/014_fractional_quantity.sql` (up), `migrations/014_fractional_quantity.down.sql` (down)

### Up migration

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

`NUMERIC(18,4)` provides exact decimal arithmetic — appropriate for share quantities
(Alpaca allows 4 decimal places of precision for fractional shares). Never use `FLOAT`
for financial quantities in Postgres; floating-point arithmetic accumulates rounding error.

### Down migration

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

The `USING quantity::INTEGER` cast truncates any fractional values — this is lossy but
safe. After rollback, any fractional-share orders/positions from the forward migration
would be truncated in place.

### psycopg2 type note

`NUMERIC` columns return `decimal.Decimal` from psycopg2, not `float`. All repository
`int(row[...])` casts must become `float(row[...])` — `float()` accepts both `int` and
`Decimal`, making the change safe across the migration boundary.

---

## Section 2 — Settings

**File:** `src/alpaca_bot/config/__init__.py`

Two new fields on the `Settings` dataclass:

```python
# Not from env — populated at startup after broker lookup
fractionable_symbols: frozenset[str] = dataclasses.field(default_factory=frozenset)

# From env — configurable threshold; 0.0 = disabled (default)
min_position_notional: float = 0.0
```

`from_env()` parsing:

```python
min_position_notional=float(values.get("MIN_POSITION_NOTIONAL", "0.0")),
```

`fractionable_symbols` is **not** read from env and is **not** validated in `validate()`.
It is an operational field set by `RuntimeSupervisor.from_settings()` after broker
initialisation. The default `frozenset()` means all symbols are treated as non-fractionable
until populated — safe for tests that don't need fractional behaviour.

`validate()` should assert `min_position_notional >= 0.0`.

---

## Section 3 — Position Sizing

**File:** `src/alpaca_bot/risk/sizing.py`

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
    if quantity < 1 and not fractionable:
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

Return type changes from `int` to `float`. Existing tests that assert `qty == 50` are
unaffected — `50.0 == 50` is `True` in Python.

The `fractionable=False` default means all call sites without the argument behave
identically to today. Only the engine call site adds the flag.

---

## Section 4 — Storage Models

**File:** `src/alpaca_bot/storage/models.py`

```python
# OrderRecord — before
quantity: int
filled_quantity: int | None

# OrderRecord — after
quantity: float
filled_quantity: float | None

# PositionRecord — before
quantity: int

# PositionRecord — after
quantity: float
```

No other field changes. Tests constructing these models with integer literals continue to
work because `int` satisfies `float` at runtime.

---

## Section 5 — Repositories

**File:** `src/alpaca_bot/storage/repositories.py`

All casts of quantity-related row columns must change from `int(row[...])` to
`float(row[...])`. Approximately 10 call sites, spread across order and position
repository methods. A focused grep for `int(row[` will enumerate them precisely.

`float()` handles both the pre-migration `int` values and post-migration `Decimal` values
from psycopg2.

No SQL query changes are required — `SELECT` and `INSERT/UPDATE` statements are
parameterised; Postgres handles the NUMERIC type transparently.

---

## Section 6 — Engine

**File:** `src/alpaca_bot/core/engine.py`

Two changes in the entry-sizing block:

**1. Pass fractionability flag to sizing:**

```python
fractionable = signal.symbol in settings.fractionable_symbols
quantity = calculate_position_size(
    equity=equity,
    entry_price=signal.limit_price,
    stop_price=cap_stop,
    settings=settings,
    fractionable=fractionable,
)
```

**2. Add min_notional gate (after existing `quantity < 1` check):**

```python
if quantity <= 0.0:
    continue
if settings.min_position_notional > 0 and quantity * signal.limit_price < settings.min_position_notional:
    continue
```

The `evaluate_cycle()` function remains **pure** — `fractionable_symbols` is a field on
the `Settings` dataclass that was populated before the function was called; no I/O inside
the engine.

For stop and exit order quantities: `active_stop.quantity` and `position.quantity` are
already stored in Postgres and carried through as `float` after the migration. No engine
changes are needed for those paths.

---

## Section 7 — Broker Adapter

**File:** `src/alpaca_bot/execution/alpaca.py`

### New method: `get_fractionable_symbols`

```python
def get_fractionable_symbols(self, symbols: Sequence[str]) -> frozenset[str]:
    """Query Alpaca for fractionability of each symbol. Returns the subset that supports fractional trading."""
    result = set()
    for symbol in symbols:
        try:
            asset = self._trading_client.get_asset(symbol)
            if asset.fractionable:
                result.add(symbol)
        except Exception:
            pass  # non-fractionable or asset not found; default to integer
    return frozenset(result)
```

Errors are swallowed per-symbol — a failed lookup means the symbol is treated as
non-fractionable, which is the safe fallback (integer sizing still works).

### Order submission quantity changes

All submission methods change `quantity: int` → `quantity: float`:

- `submit_stop_limit_entry(*, ..., quantity: float, ...)`
- `submit_limit_entry(*, ..., quantity: float, ...)`
- `submit_stop_order(*, ..., quantity: float, ...)`
- `submit_market_exit(*, ..., quantity: float, ...)`
- `submit_limit_exit(*, ..., quantity: float, ...)`

Before constructing the Alpaca SDK order request, round to 4 decimal places:

```python
qty = round(quantity, 4)
```

Alpaca's fractional share API accepts up to 4 decimal places. Rounding at the submission
layer means DB storage retains full precision while the API receives a clean value.

For non-fractionable symbols, `calculate_position_size` already floors to an integer
before returning; `round(50.0, 4)` = `50.0` — the Alpaca SDK interprets `50.0` as an
integer quantity for non-fractionable assets.

### Paper/live parity

Fractional orders work in both paper and live Alpaca environments. The `fractionable`
flag on `Asset` is the same in both. No mode-specific branching is needed.

---

## Section 8 — Supervisor

**File:** `src/alpaca_bot/runtime/supervisor.py`

In `RuntimeSupervisor.from_settings()`, after broker creation:

```python
broker = AlpacaBroker.from_settings(settings)
fractionable = broker.get_fractionable_symbols(settings.symbols)
settings = dataclasses.replace(settings, fractionable_symbols=fractionable)
```

`Settings` is a frozen dataclass; `dataclasses.replace()` creates a new instance with the
updated field. All downstream callees (engine, sizing, repositories) receive the
fractionable-aware `Settings` object from their respective call sites.

This is the only code path that loads fractionability data. The supervisor loop itself
does not reload it per cycle — symbol fractionability is stable for the life of the
process.

---

## Section 9 — Tests

### `test_position_sizing.py` — additions

Add tests for fractional sizing:

```python
def test_fractional_sizing_returns_float():
    settings = SimpleNamespace(risk_per_trade_pct=0.0025, max_position_pct=0.015)
    qty = calculate_position_size(
        equity=99_500.0, entry_price=3.00, stop_price=2.70,
        settings=settings, fractionable=True,
    )
    assert isinstance(qty, float)
    assert qty > 1.0  # should not be floored to 1

def test_non_fractional_sizing_floors():
    settings = SimpleNamespace(risk_per_trade_pct=0.0025, max_position_pct=0.015)
    qty = calculate_position_size(
        equity=99_500.0, entry_price=3.00, stop_price=2.70,
        settings=settings, fractionable=False,
    )
    assert qty == float(int(qty))  # must be a whole number
```

### `test_cycle_engine.py` — additions

Add tests for the min_notional gate:

```python
def test_min_notional_gate_drops_tiny_position():
    # symbol is non-fractionable; integer sizing gives 1 share × $3 = $3 notional
    # min_position_notional = 100 → should be dropped
    ...

def test_fractionable_symbol_produces_larger_position():
    # symbol in fractionable_symbols; fractional sizing reaches max_position_pct
    ...
```

Existing tests that use `Settings.from_env()` or `make_settings()` fakes are unaffected —
`fractionable_symbols` defaults to `frozenset()` and `min_position_notional` defaults to
`0.0`.

---

## Non-Goals

- No watchlist composition changes (separate concern).
- No `RISK_PER_TRADE_PCT` adjustment (separate concern; fractional shares solves the same
  problem more cleanly).
- No changes to the reconciliation logic, trade stream handler, or audit event schema.
- No changes to the replay / backtesting runner.
- No option_orders fractional support beyond the migration — option quantities are always
  integer; the migration is defensive to match the schema type change.

---

## Expected Outcome

With fractionable symbols, `calculate_position_size` returns `max_notional / entry_price`
(up to 4dp) rather than `1`. At $99.5K equity, `max_position_pct=0.015`:
`max_notional = $1,492.50`. For 20 slots × $1,492.50 = $29,850 / $99,500 = **30.0%** —
exactly at the exposure cap. Deployment should move from 2.7% to ~30% within one session
after deploying this change.
