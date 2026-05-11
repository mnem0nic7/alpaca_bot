# Stop-Above-Market Guard Design

**Date:** 2026-05-11
**Status:** Approved for implementation

## Problem

`evaluate_cycle()` in `core/engine.py` can produce `UPDATE_STOP` intents with a `stop_price` that exceeds the current market price. This happens in four passes:

| Pass | Mechanism | Scenario |
|---|---|---|
| ATR trailing | `max(stop_price, entry_price, trailing_candidate)` | Stock gaps down below entry; `entry_price` floor raises stop above current close |
| Profit trail | `today_high * profit_trail_pct` | Stock pulls back sharply from session high; 0.95 × high > current close |
| Breakeven | `max(entry_price, trail_stop)` | Gap-down puts entry_price above market; guard only fires when `is_extended=True` |
| Cap-up | `entry_price * (1 - max_stop_pct)` | Extreme gap-down puts cap stop above current price; no close lookup at all |

When any of these produce a stop > current price, Alpaca rejects `replace_order` with a 4xx error that does not match any of the handled phrases in `_execute_update_stop()`. The result: `stop_update_failed` is emitted every cycle, indefinitely, with no corrective action taken on either the stop price or the position.

## Goal

No `UPDATE_STOP` intent should be emitted when `new_stop >= latest_bar.close`. The engine must enforce this invariant in all four passes. If a computed stop would exceed the current price, the pass silently skips it — the existing stop remains, and the position continues with its current protection level.

## Design

### Principle

The fix is in the engine (`core/engine.py`), not the executor. The engine should not generate intents it knows will fail. Defense-in-depth at the executor layer is not added — fixing the root violates YAGNI on the executor side.

### Per-pass changes

**ATR trailing pass (lines 262–271):**

Add `and new_stop < latest_bar.close` to the emit guard:

```python
if new_stop > position.stop_price and new_stop < latest_bar.close:
    intents.append(...)
```

`latest_bar` is in scope from line 154 in the enclosing position loop.

**Profit trail pass (lines 296–308):**

Add a close guard after computing `trail_candidate`. `bars[-1]` is the current bar for this position:

```python
trail_candidate = round(today_high * settings.profit_trail_pct, 2)
latest_close = bars[-1].close
prior_stop = _pt_prior_stops.get(position.symbol, position.stop_price)
if trail_candidate > prior_stop and trail_candidate < latest_close:
    intents.append(...)
```

**Breakeven pass (lines 336–337):**

Remove `is_extended and` so the guard fires unconditionally:

```python
if be_stop >= latest_bar.close:
    continue
```

This is the only pass that already had the guard; it was just gated on extended hours.

**Cap-up pass (lines 361–379):**

Add a bar lookup to get the current close, then skip if `cap_stop >= latest_close`:

```python
bars = intraday_bars_by_symbol.get(position.symbol, ())
if not bars:
    continue
latest_close = bars[-1].close
cap_stop = round(position.entry_price * (1 - settings.max_stop_pct), 2)
effective_stop = emitted_update_stops.get(position.symbol, position.stop_price)
if effective_stop < cap_stop and cap_stop < latest_close:
    intents.append(...)
```

### Invariant after the fix

For every emitted `UPDATE_STOP` intent: `stop_price < latest_bar.close` for that symbol. This is testable.

### What happens to positions caught by the guard

The position keeps its existing `stop_price` on that cycle. On the next cycle, if the market price has recovered above the would-be stop, the pass will fire normally. If the market continues to fall and the existing stop is breached, the normal stop-breach exit logic handles it.

### No schema changes, no env vars, no migration

Pure logic change in one file. No new settings.

## Components Affected

| File | Change |
|---|---|
| `src/alpaca_bot/core/engine.py` | Four guards — two combined with existing conditions, one guard extended, one new bar lookup added |
| `tests/unit/test_cycle_engine.py` | Four new tests: one per pass, each verifying no UPDATE_STOP emitted when stop ≥ close |

## Error Handling

- If `intraday_bars_by_symbol` has no entry for a position's symbol, the cap-up pass already returns early (`if not bars: continue`), so the close lookup is safe.
- The guard uses strict `<` (not `<=`): a stop equal to close is also rejected, as Alpaca rejects stop ≥ ask for a sell stop, and close ≈ ask at end of bar.

## Test Coverage

Each of the four passes needs a dedicated test:
1. ATR trailing: position with entry_price above current close → no UPDATE_STOP emitted
2. Profit trail: today_high × profit_trail_pct > current close → no UPDATE_STOP emitted
3. Breakeven: entry_price > close during regular session → no UPDATE_STOP emitted (was previously emitted)
4. Cap-up: entry_price × (1 - max_stop_pct) > close → no UPDATE_STOP emitted

## Risk Assessment

- **Financial safety:** The existing stop remains in place when the guard fires. The position is not left unprotected — it simply keeps its last valid stop. If a gap-down triggers the guard, the existing stop below entry provides protection (or the position is already at a loss, in which case the stop-breach exit handles it).
- **False suppression risk:** Could we suppress a legitimate stop update? Only if `new_stop >= close`, which means the stop would trigger immediately — that is never a legitimate update.
- **Rollback:** Single-file change; reverting the guard conditions restores prior behavior.
