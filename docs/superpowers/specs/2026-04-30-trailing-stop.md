# Spec: Trailing Stop / Gain Preservation

## Problem

When a long position is profitable and price reverses intraday, the existing stop-loss does
not protect accumulated gains. The current engine moves the stop to break-even (entry price)
once the position reaches 1R profit — but then the stop stays at entry even as price climbs
$5 or $10 above it. A full intraday reversal returns all gains.

Real example: AIN was up all day then reversed and ended lower. The bot holds through the
entire reversal rather than locking in the gain as the reversal develops.

## Goals

1. When price rises above entry by a configurable profit trigger (default: 1R), begin
   trailing the stop below the running bar high by `N × ATR`.
2. The trail is additive to the existing break-even floor: the stop can never move below
   `entry_price` once trailing is active.
3. `evaluate_cycle()` remains a pure function — no I/O or state mutations.
4. Default settings preserve the exact existing behavior (disabled by default).
5. No database migration required.

## Non-Goals

- Trailing stop order types at the broker (Alpaca supports these, but the intraday poll
  cycle already manages stop placement via standard stop orders). Using broker-side
  trailing stops would conflict with the existing per-cycle UPDATE_STOP logic.
- Partial position scaling out on profit targets.
- Per-symbol trailing configurations.

## Design

### Settings fields (new)

```python
trailing_stop_atr_multiplier: float = 0.0   # 0 = disabled (original bar.low logic)
trailing_stop_profit_trigger_r: float = 1.0  # R-multiples profit before trailing activates
```

Parsed from environment:
```
TRAILING_STOP_ATR_MULTIPLIER   (default "0.0")
TRAILING_STOP_PROFIT_TRIGGER_R (default "1.0")
```

Validation:
- `trailing_stop_atr_multiplier >= 0` (0 disables, positive activates)
- `trailing_stop_profit_trigger_r > 0`

### Engine change (`core/engine.py`)

Add import:
```python
from alpaca_bot.risk.atr import calculate_atr
```

Replace lines 135–146 with:

```python
profit_trigger = (
    position.entry_price
    + settings.trailing_stop_profit_trigger_r * position.risk_per_share
)
if latest_bar.high >= profit_trigger:
    atr = (
        calculate_atr(
            daily_bars_by_symbol.get(position.symbol, ()),
            settings.atr_period,
        )
        if settings.trailing_stop_atr_multiplier > 0
        else None
    )
    if atr is not None:
        trailing_candidate = latest_bar.high - settings.trailing_stop_atr_multiplier * atr
        new_stop = round(
            max(position.stop_price, position.entry_price, trailing_candidate), 2
        )
    else:
        # Original behavior: bar.low-based break-even
        new_stop = round(
            max(position.stop_price, position.entry_price, latest_bar.low), 2
        )
    if new_stop > position.stop_price:
        intents.append(
            CycleIntent(
                intent_type=CycleIntentType.UPDATE_STOP,
                symbol=position.symbol,
                timestamp=latest_bar.timestamp,
                stop_price=new_stop,
                strategy_name=strategy_name,
            )
        )
```

**Why `max(stop_price, entry_price, trailing_candidate)`?**
- `stop_price` — stop never regresses (monotonically increasing invariant)
- `entry_price` — break-even floor always maintained once trailing activates
- `trailing_candidate` — the new ATR-based level if it's higher than both

**Why stale `stop_price` is sufficient (no `highest_price` persistence):**
The stop is only ever increased. If price reached $120 last cycle and trailing set the stop
to $117, this cycle's `position.stop_price = $117`. If this cycle's bar has `high = $118`,
the candidate is `$118 - ATR_distance`. If that's below $117, `max(...)` keeps it at $117.
If it's above $117, the stop advances. The historical high is implicitly encoded in the
current stop_price — no separate `highest_price` column is needed.

### No execution layer changes

`execute_cycle_intents()` processes `UPDATE_STOP` intents by calling `broker.replace_order()`
and saving the new `stop_price` to `PositionRecord`. This path is unchanged. The new trailing
stop level is just a different `stop_price` value in the same intent.

### Replay compatibility

The replay runner calls `evaluate_cycle()` and processes `UPDATE_STOP` via
`_handle_stop_update()`. Since the trailing stop logic lives entirely inside `evaluate_cycle()`,
the replay runner automatically trails stops when `TRAILING_STOP_ATR_MULTIPLIER > 0`.
No replay runner changes needed.

## Files Changed

| File | Change |
|------|--------|
| `src/alpaca_bot/config/__init__.py` | Add `trailing_stop_atr_multiplier`, `trailing_stop_profit_trigger_r` fields; parse from env; validate |
| `src/alpaca_bot/core/engine.py` | Add `calculate_atr` import; replace stop-update block with ATR-trailing logic |
| `tests/unit/test_cycle_engine.py` | Tests for trailing stop at various price levels, disabled path, ATR-unavailable fallback |
| `tests/unit/test_settings.py` | Validate env parsing and validation constraints |

## Safety Analysis

**Worst-case financial scenario:** If ATR is very small (thin daily bars) and
`trailing_stop_atr_multiplier` is small (e.g. 0.5), the trailing stop could be very close
to the current bar's high. On a volatile bar, the stop fires immediately. Mitigation: the
existing `max(position.stop_price, ...)` guard ensures the stop never drops; it can only
move up. A tight trailing stop that fires early is a gain-locking outcome, not a loss.

**ATR unavailable (< 15 daily bars):** `calculate_atr()` returns `None`. The code falls
through to the existing bar.low logic unchanged. No silent degradation.

**`TRAILING_STOP_ATR_MULTIPLIER = 0.0` (default):** The `if settings.trailing_stop_atr_multiplier > 0`
guard skips the ATR path entirely. Behavior is byte-for-byte identical to pre-feature code.

**Pure function preserved:** `calculate_atr()` is a pure computation over a `Sequence[Bar]`
slice. No I/O, no randomness, no side effects.

**`ENABLE_LIVE_TRADING=false` gate:** Unaffected. The trailing stop change is upstream of
order submission.

**Concurrent cycles:** Impossible (advisory lock). The monotonically-increasing stop
invariant also means even a duplicate UPDATE_STOP intent is a no-op at the execution layer
(`if stop_price <= position.stop_price: return None` in `_execute_update_stop`).

**Market-hours guards:** The existing `is_extended` guard (`continue` on lines 132-133 of
engine.py) already skips all stop updates during extended hours. The trailing stop inherits
this guard — it only runs inside the regular-session stop-update block.

**Paper vs. live parity:** Identical logic — no mode-conditional branches.

**New env vars:** Two new optional vars with documented safe defaults. Neither can enable
live trading, bypass safety gates, or change credentials.
