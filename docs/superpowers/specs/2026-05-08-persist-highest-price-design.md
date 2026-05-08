# Persist `highest_price` for Breakeven Trail Accuracy

## Problem

`OpenPosition.highest_price` is initialised to `entry_price` on every cycle
([supervisor.py:1268](../../../src/alpaca_bot/runtime/supervisor.py#L1268)).
`PositionRecord` (the Postgres-backed view) has no `highest_price` column, so
the running maximum price since entry is discarded between cycles.

The breakeven trail stop uses:

```python
max_price = max(position.highest_price, latest_bar.high)
trail_stop = round(max_price * (1 - settings.breakeven_trail_pct), 2)
```

Because `highest_price` always equals `entry_price`, `max_price` always equals
`latest_bar.high` (the current 15-minute bar's high).  This under-protects
gains: if a position ran to $3.20 on an earlier bar but the current bar is
$3.09, the trail stop is computed from $3.09 ($3.087) instead of $3.20
($3.194).  A 1.28% intra-position pullback escapes the 0.2% trail window.

## Goal

Persist the running maximum observed bar-high since entry in Postgres.  Update
it every cycle (including after-hours) so the breakeven trail always computes
from the true historical maximum.

## Out of Scope

- The ATR-based trailing stop (`trailing_stop_profit_trigger_r` path) — it uses
  `latest_bar.high` directly, not `highest_price`, and is unaffected.
- The profit trail pass — uses `today_high` derived from all session bars and is
  also unaffected.
- The breakeven *trigger* condition (`latest_bar.high >= entry * 1.0025`) —
  unchanged; still requires the current bar to show the position is profitable
  before any stop intent is emitted.

## Architecture

### Storage layer

**Migration 017** adds one nullable column to `positions`:

```sql
ALTER TABLE positions
    ADD COLUMN IF NOT EXISTS highest_price NUMERIC DEFAULT NULL;
```

NULL means "not yet tracked" and is treated as `entry_price` in code.

**`PositionRecord`** gains an optional field:

```python
highest_price: float | None = None
```

**`PositionStore`** changes:

| Method | Change |
|---|---|
| `list_all()` | SELECT the new column; populate `highest_price` |
| `save()` | Include in INSERT; ON CONFLICT uses `COALESCE(EXCLUDED.highest_price, positions.highest_price)` so that a stop-price update never overwrites an accumulated high |
| `replace_all()` | Include `highest_price` in INSERT (passes whatever the PositionRecord carries; NULL for broker-sourced reconciliation records) |
| `update_highest_price()` | **New method** — targeted single-column UPDATE for a position identified by `(symbol, trading_mode, strategy_version, strategy_name)` |

### Supervisor layer

**`_load_open_positions()`** changes one line:

```python
highest_price=position.highest_price or position.entry_price,
# was: highest_price=position.entry_price,
```

**New method `_apply_highest_price_updates()`** runs in `run_cycle_once()`
after bars are fetched, before the engine runs:

```python
def _apply_highest_price_updates(
    self,
    positions: list[OpenPosition],
    intraday_bars_by_symbol: dict[str, Sequence[Bar]],
) -> list[OpenPosition]:
    ...
```

For each position:
1. Get `bars = intraday_bars_by_symbol.get(symbol, [])`.  Skip if empty.
2. `bar_high = bars[-1].high`
3. If `bar_high > position.highest_price`:
   - Call `position_store.update_highest_price(...)` inside `store_lock`
   - Return a `dataclasses.replace(position, highest_price=bar_high)` copy
4. Otherwise return the position unchanged.

Returns an updated `list[OpenPosition]` with in-memory `highest_price` values
matching what was just written to the DB.  The engine runs against this list.

### Call site in `run_cycle_once()`

```python
intraday_bars_by_symbol = self.market_data.get_stock_bars(...)   # existing

# NEW — runs before the engine, including after-hours cycles
open_positions = self._apply_highest_price_updates(
    open_positions, intraday_bars_by_symbol
)
```

This placement means:
- After-hours cycles update `highest_price` in DB even though UPDATE_STOP is
  suppressed.  So when market opens the next morning the engine immediately sees
  the correct maximum.
- The update is inside `store_lock`, consistent with all other position writes.

## Data Flow

```
cycle start
  │
  ├─ _load_open_positions()
  │    reads positions from DB
  │    highest_price ← DB column (or entry_price if NULL)
  │
  ├─ market_data.get_stock_bars()
  │    returns intraday_bars_by_symbol
  │
  ├─ _apply_highest_price_updates()        ← NEW
  │    for each position:
  │      if latest_bar.high > position.highest_price:
  │        DB: UPDATE positions SET highest_price = bar_high
  │        memory: return updated OpenPosition
  │
  ├─ evaluate_cycle()   (pure function, reads OpenPosition.highest_price)
  │    breakeven trail: max_price = max(persisted_high, latest_bar.high)
  │                                              ^^^^^^^ now correct
  │
  └─ execute_cycle_intents()  (unchanged)
```

## Error Handling

- If `position_store.update_highest_price()` raises, log a warning and continue
  with the in-memory value.  A missed DB write is not financial-safety-critical:
  the trail will be recomputed next cycle from the DB value that was there before
  the error.
- If a position has no bars in `intraday_bars_by_symbol`, skip the update
  silently (same as today — the engine already skips positions with no bars).

## Reconciliation / `replace_all` behaviour

`replace_all()` is used in startup reconciliation and nightly flatten.  After a
reconciliation, `highest_price` is reset to NULL (no broker-sourced position
knows its historical max).  The very next `_apply_highest_price_updates()` call
(first cycle after reconciliation) will re-populate from the current bar's high.
This is the same as today's behaviour — not worse — and the window where
`highest_price` is missing is at most one cycle.

## Testing

Three test modules:

1. **`test_position_store_highest_price.py`** — unit tests for the repository
   layer:
   - `save()` with `highest_price` set → reads back correct value
   - `save()` with `highest_price = None` on conflict → preserves existing
   - `update_highest_price()` happy path
   - `list_all()` returns correct `highest_price`

2. **`test_supervisor_highest_price.py`** — unit tests for
   `_apply_highest_price_updates()`:
   - Bar high exceeds current → DB updated, returned list has new value
   - Bar high equal or lower → no DB call, list unchanged
   - Position absent from bars dict → skipped, returned unchanged
   - Store-lock is held during the DB write

3. **`test_cycle_engine_highest_price.py`** — engine-level test confirming the
   breakeven trail stop is locked in from `highest_price`, not just
   `latest_bar.high`:
   - Position with `highest_price = 3.20`, `latest_bar.high = 3.09`.
     Breakeven triggered.  Expected stop > 3.09 × 0.998 = 3.0862.

## Audit trail

`update_highest_price()` does not append an `AuditEvent`.  `highest_price` is a
rolling computed value derived entirely from market data; it has no financial
state implications on its own (it drives the *magnitude* of a future stop move,
not the stop move itself).  The stop move itself is already audited via
`cycle_intent_executed` when `_execute_update_stop()` runs.

## Safety properties

| Property | Status |
|---|---|
| `evaluate_cycle()` remains pure | ✅ reads `highest_price` from its input, never writes |
| Order submission unaffected | ✅ this change only affects stop *levels*, not order submission logic |
| Paper vs live | ✅ identical code path; no broker calls in this path |
| Market-hours gate | ✅ UPDATE_STOP intents still suppressed after hours; `highest_price` update is unconditional and safe |
| Advisory lock | ✅ only one supervisor instance can run; no concurrency concern |
| Rollback | ✅ migration adds a nullable column with DEFAULT NULL — fully reversible with `ALTER TABLE positions DROP COLUMN highest_price` |
