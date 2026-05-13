# Short Position Management — Full Symmetric Trailing

**Date:** 2026-05-13
**Status:** Approved

## Problem

The bot currently skips all broker positions with `qty <= 0` during startup recovery
(`startup_recovery.py:116`). This means any short equity position or short option
position that exists in the Alpaca account is invisible to the supervisor, absent from
the dashboard, and receives no stop management or EOD flatten treatment.

The account currently holds several externally-created short positions:

- Short put options (negative quantity): ALHC, AMLX, AROC, BCRX, BFLY, CMG, CNK
- Short equity (negative quantity): QBTS

## Scope

Fully manage short positions using a symmetric mirror of the existing long-position
logic. "Manage" means:

1. Import on startup and display on the dashboard
2. Place a protective buy-stop above entry on startup (equity shorts only)
3. Update that stop each cycle using the same trailing logic as longs, but inverted
4. Flatten at EOD via the existing flatten-all path
5. Exit via buy-to-cover (equity) or buy-to-close (options)

Short option positions use EOD-flatten-only treatment — no intraday stop is placed,
because the option's own expiry provides the natural terminal condition.

## Design

### 1. Data Model

**`domain/models.py` — `OpenPosition`**

Add `lowest_price: float = 0.0`. This field tracks the lowest bar low seen since entry,
mirroring `highest_price` (which tracks the highest bar high for longs). Both are used
exclusively by the breakeven trailing pass.

No other `OpenPosition` fields change. `quantity < 0` already encodes direction.
`risk_per_share = entry_price - initial_stop_price` is naturally negative for shorts
(stop above entry), which makes ATR and profit-target formulas work symmetrically
without special-casing.

**`storage/models.py` — `PositionRecord`**

Add `lowest_price: float | None = None`.

**Database migration**

New migration: `ALTER TABLE positions ADD COLUMN lowest_price REAL;`
Existing rows fill as `NULL` (treated as `entry_price` on first load).

---

### 2. Startup Recovery

**File:** `runtime/startup_recovery.py`

**Current behaviour (lines 115–136):**
```python
if broker_position.quantity <= 0:
    logger.warning("skipping non-positive qty position: %s", broker_position.symbol)
    audit_event_store.append(AuditEvent(event_type="startup_recovery_skipped_nonpositive_qty", ...))
    continue
```

**New behaviour:**

The guard is replaced with direction-aware import logic.

**Short equity (e.g., QBTS, quantity < 0, not an OCC symbol):**
- `entry_price` = broker avg cost
- `stop_price = entry_price * (1 + settings.breakout_stop_buffer_pct)` (above entry)
- `initial_stop_price = stop_price`
- `lowest_price = entry_price`
- `strategy_name = "short_equity"` (distinguishes from bear strategy option positions)
- Queue a pending-submit `OrderRecord` with `side="buy"` (buy-stop above entry)
- Audit event: `startup_recovery_imported_short_equity`

**Short options (OCC symbol format, quantity < 0):**
- `entry_price` = broker avg cost
- `stop_price = 0.0` (no protective stop — EOD flatten only)
- `initial_stop_price = 0.0`
- `lowest_price = entry_price`
- `strategy_name = "short_option"`
- No stop order queued
- Audit event: `startup_recovery_imported_short_option`

OCC symbol detection: use the existing `_is_option_symbol(symbol)` helper in
`startup_recovery.py` which matches the regex `r"^[A-Z]{1,6}\d{6}[CP]\d{8}$"`.

---

### 3. Engine (`core/engine.py`)

All passes in `evaluate_cycle()` gain a direction flag computed once per position:

```python
is_short = position.quantity < 0
```

Options positions (`stop_price == 0.0` AND `strategy_name == "short_option"`) skip all
stop-update passes and only receive the EOD flatten intent.

#### Stop breach
| Direction | Condition |
|-----------|-----------|
| Long | `close <= stop_price` |
| Short | `close >= stop_price` |

#### Profit target
| Direction | Condition |
|-----------|-----------|
| Long | `high >= target_price` |
| Short | `low <= target_price` |

`target_price` formula stays the same — `entry + N * risk_per_share` — and naturally
produces a value below entry for shorts (since `risk_per_share` is negative).

#### VWAP exit
| Direction | Condition |
|-----------|-----------|
| Long | `close < vwap` |
| Short | `close > vwap` |

#### ATR trailing stop
| Direction | Logic |
|-----------|-------|
| Long | `new_stop = max(stop, entry, candidate)` where `new_stop < close` |
| Short | `new_stop = min(stop, entry, candidate)` where `new_stop > close` |

`candidate` formula: `close - atr_multiple * atr` for longs, `close + atr_multiple * atr`
for shorts. The `atr_multiple` setting is shared.

#### Profit trail pass
| Direction | Logic |
|-----------|-------|
| Long | `candidate = today_high * profit_trail_pct` (e.g., `0.95` → 5% below today's high) |
| Short | `candidate = today_low / profit_trail_pct` (e.g., `0.95` → ~5.3% above today's low) |

Accept candidate when: long `candidate > stop and candidate < close`; short
`candidate < stop and candidate > close`.

#### Breakeven trailing pass

Long (existing):
```
trigger = entry * (1 + breakeven_trigger_pct)   # e.g. entry * 1.0025
if high >= trigger:
    max_price = max(position.highest_price, bar.high)
    trail_stop = round(max_price * (1 - breakeven_trail_pct), 2)   # 0.2% below best
    be_stop = max(entry_price, trail_stop)                          # at least breakeven
    if be_stop < bar.close: emit UPDATE_STOP
```

Short (new symmetric mirror):
```
trigger = entry * (1 - breakeven_trigger_pct)   # e.g. entry * 0.9975
if low <= trigger:
    min_price = min(position.lowest_price, bar.low)
    trail_stop = round(min_price * (1 + breakeven_trail_pct), 2)   # 0.2% above best
    be_stop = min(entry_price, trail_stop)                          # at most breakeven
    if be_stop > bar.close: emit UPDATE_STOP
```

#### Cap pass (max stop distance)
| Direction | Logic |
|-----------|-------|
| Long | stop floor = `entry * (1 - max_stop_pct)` — if stop too far below, bring it up |
| Short | stop ceiling = `entry * (1 + max_stop_pct)` — if stop too far above, bring it down |

#### Trend filter exit
| Direction | Condition |
|-----------|-----------|
| Long | Last N daily closes all below SMA → exit (downtrend, long no longer valid) |
| Short | Last N daily closes all above SMA → exit (uptrend, short no longer valid) |

---

### 4. Broker Methods

**File:** `execution/alpaca.py`

Three new methods added to `AlpacaBroker`, each mirroring an existing sell-side method
with `side=BUY`:

```python
def submit_buy_stop_order(
    self, *, symbol: str, qty: int, stop_price: float, client_order_id: str
) -> BrokerOrder:
    """Protective stop for a short equity position (buy-stop above entry).
    Mirrors submit_stop_order but with OrderSide.BUY."""

def submit_market_buy_to_cover(
    self, *, symbol: str, qty: int, client_order_id: str
) -> BrokerOrder:
    """Market buy-to-cover to close a short equity position.
    Mirrors submit_market_exit but with OrderSide.BUY."""

def submit_option_market_buy_to_close(
    self, *, occ_symbol: str, quantity: int, client_order_id: str
) -> BrokerOrder:
    """Market buy-to-close for a short option position.
    Mirrors submit_option_market_exit but with OrderSide.BUY."""
```

Alpaca uses `OrderSide.BUY` for both equity buy-to-cover and option buy-to-close —
there are no separate `BUY_TO_COVER` / `BUY_TO_CLOSE` side values in the SDK. Market
orders are used for all three (same as the existing sell-side exits) for fill certainty
at EOD.

---

### 5. Order Dispatch

**File:** `runtime/order_dispatch.py`

When dispatching a `pending_submit` stop order, inspect `order.side`:

- `"sell"` → existing `broker.submit_stop_order` (long protective stop)
- `"buy"` → new `broker.submit_buy_stop_order` (short protective stop)

No other changes to dispatch logic.

---

### 6. Cycle Intent Execution

**File:** `runtime/cycle_intent_execution.py`

**`_execute_update_stop`:**
- Regression guard flips for shorts: for a long, skip if `new_stop <= current_stop`;
  for a short, skip if `new_stop >= current_stop` (stop should only move down for shorts).
- `OrderRecord.side` = `"buy"` when `position.quantity < 0`.

**`_execute_exit`:**
- Routes to `broker.submit_market_buy_to_cover(symbol=..., qty=abs(position.quantity))`
  when `position.quantity < 0` and not an OCC symbol (equity short).
- Routes to `broker.submit_option_market_buy_to_close(occ_symbol=..., quantity=abs(position.quantity))`
  when short option (OCC symbol, `strategy_name == "short_option"`).

---

### 7. Supervisor

**File:** `runtime/supervisor.py`

**`_apply_highest_price_updates`** (existing):
Unchanged — only fires for positions with `quantity > 0`.

**`_apply_lowest_price_updates`** (new):
Mirror of the above for short positions. On each cycle, for each position with
`quantity < 0`, if `bar.low < position.lowest_price`, call
`position_store.update_lowest_price(symbol, bar.low)` and update the in-memory record.

**`_load_open_positions`:**
Initialises `lowest_price = position.lowest_price or position.entry_price` (same
pattern as `highest_price`).

---

### 8. Storage

**File:** `storage/repositories.py`

**`PositionStore.save()` and `replace_all()`:**
Include `lowest_price` column in INSERT/UPDATE.

**`update_lowest_price(symbol: str, lowest_price: float) -> None`:**
New method, mirrors `update_highest_price`. Updates the `lowest_price` column for the
given symbol.

**Row read:**
`lowest_price = float(row[11])` (appended after existing `highest_price` at index 10).

---

### 9. Dashboard

No template changes required. The existing P&L formula:

```
upnl = (last_price - entry_price) * quantity * multiplier
```

naturally produces correct values for negative `quantity` — when price rises against a
short, `last_price - entry_price > 0` multiplied by `quantity < 0` yields a negative
unrealised P&L.

`stop_dist_pct = (entry_price - stop_price) / entry_price * 100` will show negative
for shorts (stop above entry), which visually signals the reversed direction. This is
acceptable behaviour.

---

## Safety Properties

**Financial safety:**
- Short positions receive a buy-stop immediately on import; the stop is bounded by
  `max_stop_pct` on each cycle, preventing runaway loss.
- Short option positions use EOD-flatten-only — no intraday stop is submitted because
  option pricing can gap; the flatten path already handles this case.

**Two-phase design:**
- Stop orders for shorts are queued as `pending_submit` `OrderRecord` rows with
  `side="buy"` and dispatched by the existing dispatch loop. No broker call happens
  inside `evaluate_cycle()`.

**Audit trail:**
- Two new audit event types: `startup_recovery_imported_short_equity` and
  `startup_recovery_imported_short_option`. Every state change is logged.

**Paper vs. live:**
- `settings.trading_mode` gates remain unchanged. Shorts are managed identically in
  paper and live modes.

**Crash recovery:**
- If the supervisor crashes after importing a short position but before submitting its
  stop, the stop is re-queued on the next startup because the position is in Postgres
  with `stop_price > 0` but no `alpaca_order_id` on the associated `OrderRecord`.
  The existing reconciliation logic handles this.

---

## Out of Scope

- **Opening new short positions from strategy signals.** Bear strategies continue to
  BUY put contracts (long options), not short equities. This spec covers management of
  externally-created short positions only.
- **Partial fill handling for buy-to-cover.** Treated identically to the existing
  partial fill logic for sell orders.
- **Short margin / buying power calculations.** The supervisor reads account equity from
  Alpaca; margin requirements are enforced by the broker, not the bot.
