# Trading System Fixes — Design Spec

**Date:** 2026-05-13  
**Scope:** Four targeted fixes identified during trading-system examination

---

## Background

A review of the recently-shipped short-selling plumbing identified four issues:
1. A spurious DB write for short positions in `_apply_highest_price_updates`
2. Two untested code paths in the engine's short breakeven and trailing stop logic
3. No observability when strategies are floor-gated by the confidence scorer
4. Silent ATR fallback in the trailing stop pass

All fixes are small and isolated. None touch order submission, position sizing, or stop placement math.

---

## Issue 1 — Short guard in `_apply_highest_price_updates`

### Problem

`_apply_highest_price_updates` (supervisor.py:1345) iterates all positions. For a short position whose bar high exceeds its tracked `highest_price`, it calls `update_highest_price()` on the DB and updates the in-memory position. `highest_price` is only consumed by the long-side breakeven pass (engine.py:410) — it is never read for shorts.

`_apply_lowest_price_updates` already has the correct guard: `if position.quantity >= 0: continue`. The long method has no equivalent.

### Fix

Add `if position.quantity < 0: result.append(position); continue` at the top of the loop body in `_apply_highest_price_updates`, mirroring the symmetric guard.

### Test

Add `test_apply_highest_price_updates_skips_short_positions` to `tests/unit/test_supervisor_highest_price.py`. Scenario: short position with `highest_price=5.00`, bar high=4.70 (stock dropped — good for short, but bar high > historical high relative to tracker). Assert no DB call and position unchanged.

---

## Issue 2 — Test coverage for lowest_price → breakeven / ATR-fallback paths

### Problem A: Pre-tracked lowest_price path

`test_short_breakeven_stop_emits_when_low_hits_trigger` (test_cycle_engine.py:2554) sets `lowest_price=6.0 == entry_price`. Engine line 391:

```python
min_price = min(position.lowest_price, latest_bar.low) if position.lowest_price > 0 else latest_bar.low
```

When `lowest_price=entry_price`, `min(6.0, bar.low)=bar.low`, so the pre-tracked path never fires. The scenario where `lowest_price=5.85` (tracked from a prior cycle) and `bar.low=5.90` — which uses the historical 5.85 for a tighter stop — is completely untested.

### Fix A

Add `test_short_breakeven_uses_tracked_lowest_price` to `tests/unit/test_cycle_engine.py`. Scenario:
- `entry_price=6.00`, `lowest_price=5.85` (pre-tracked), current `bar.low=5.90`
- `trigger = 6.00 * (1 - 0.0025) = 5.985`. bar.low=5.90 ≤ 5.985 → trigger fires
- `min_price = min(5.85, 5.90) = 5.85` (uses tracked historical low, NOT current bar)
- `trail_stop = round(5.85 * 1.002, 2) = 5.87`. `be_stop = min(6.00, 5.87) = 5.87`
- Assert `be_stop == 5.87` (which is tighter than 5.99 from the same setup with `lowest_price=6.0`)

### Problem B: ATR-unavailable short fallback

`test_trailing_stop_atr_unavailable_falls_back_to_bar_low` (test_cycle_engine.py:1041) covers the long path. The short-side fallback (engine.py:290-291):

```python
new_stop = round(min(position.stop_price, position.entry_price, latest_bar.high), 2)
```

uses `latest_bar.high` as the trailing candidate — completely untested.

### Fix B

Add `test_short_trailing_stop_atr_unavailable_falls_back_to_bar_high` to `tests/unit/test_cycle_engine.py`. Scenario:
- Short position: `entry_price=10.00`, `stop_price=10.50`, `quantity=-50`
- `trailing_stop_profit_trigger_r=1.0`, `risk_per_share=0.30` → trigger at `10.00 - 0.30 = 9.70`
- `bar.low=9.60` (below trigger), `bar.high=9.80`
- Only 3 daily bars (< period+1=15) → ATR=None
- Expected fallback: `new_stop = round(min(10.50, 10.00, 9.80), 2) = 9.80`
- `new_stop=9.80 < position.stop_price=10.50` AND `new_stop=9.80 > bar.close` → accept
- Assert `UPDATE_STOP` with `stop_price=9.80`

---

## Issue 3 — Confidence floor observability

### Problem

When `compute_weights` assigns `sharpe=0.0` to strategies below `min_trades` (default 5), the confidence scorer maps them to `floor_score` (default 0.25). This silently gates entries for warming-up strategies. As new bear strategies accumulate trade history, operators have no visibility into which strategies are floor-gated without querying the DB directly.

### Fix

Two `logger.debug` lines only — no audit events, no structural changes:

1. **`weighting.py:45`**: Inside `if trade_count[name] < min_trades:`, add:
   ```python
   logger.debug("strategy %s has %d trades (< min %d): sharpe=0.0", name, trade_count[name], min_trades)
   ```

2. **`supervisor.py` after line 369**: After computing `session_confidence_scores`, log strategies at floor:
   ```python
   for _sn, _sc in session_confidence_scores.items():
       if _sc <= confidence_floor:
           logger.debug("strategy %s confidence at floor %.2f", _sn, _sc)
   ```

### Rationale

Debug-level logging is appropriate — these are per-session observations, not state changes. An audit event would be too noisy (fires every cycle for every warming-up strategy).

---

## Issue 4 — ATR fallback logging

### Problem

In `engine.py:281-304`, when `calculate_atr` returns `None` (insufficient daily bars), the trailing stop silently falls back to `latest_bar.high` (short) or `latest_bar.low` (long). During the first trading day for a new symbol, or when the watchlist has a newly-added ticker, this happens every cycle with no trace in logs.

### Fix

Add a `logger.debug` line inside each `atr is None` branch:

```python
# Short fallback (line ~290):
logger.debug("trailing stop ATR unavailable for %s (short): using bar.high fallback", position.symbol)

# Long fallback (line ~303):
logger.debug("trailing stop ATR unavailable for %s (long): using bar.low fallback", position.symbol)
```

`logger` is already imported in `engine.py`.

---

## What this does NOT change

- Order submission paths — untouched
- Position sizing math — untouched
- Stop placement calculations — untouched
- DB schema — untouched
- Audit event schema — untouched
- TRADING_MODE gating — untouched
- `evaluate_cycle()` purity — debug logging uses the module logger, not I/O side effects

---

## Files changed

| File | Change |
|---|---|
| `src/alpaca_bot/runtime/supervisor.py` | Add short guard to `_apply_highest_price_updates`; add confidence floor debug log |
| `src/alpaca_bot/risk/weighting.py` | Add min_trades debug log |
| `src/alpaca_bot/core/engine.py` | Add ATR fallback debug log (2 branches) |
| `tests/unit/test_supervisor_highest_price.py` | Add `test_apply_highest_price_updates_skips_short_positions` |
| `tests/unit/test_cycle_engine.py` | Add `test_short_breakeven_uses_tracked_lowest_price` and `test_short_trailing_stop_atr_unavailable_falls_back_to_bar_high` |

No new files. No migration needed.
