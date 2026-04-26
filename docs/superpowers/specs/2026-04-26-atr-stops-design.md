# ATR-Based Stops and Volatility-Normalized Sizing — Design Spec

**Date:** 2026-04-26
**Status:** Approved

---

## Problem

All five strategies compute `initial_stop_price` using a fixed-percentage buffer:

```python
stop_buffer = max(0.01, anchor * settings.breakout_stop_buffer_pct)  # e.g. 0.1%
initial_stop_price = round(anchor - stop_buffer, 2)
```

This is blind to symbol volatility. NVDA has a daily ATR of ~$20 on a $900 stock (2.2%); SPY has a daily ATR of ~$5 on a $500 stock (1.0%). A 0.1% stop on NVDA ($0.90) fires on noise every cycle. A 0.1% stop on SPY ($0.50) is too tight for normal intraday variance. The result:
- NVDA positions get stopped out constantly on normal price action
- SPY positions carry more intraday risk than intended
- Position sizing (derived from stop distance) is inconsistent across the universe

## Solution

Replace the fixed-percentage buffer with **Average True Range (ATR)** scaled by a multiplier. The stop anchor (the support level each strategy defends) stays strategy-specific and unchanged. Only the buffer between anchor and stop changes.

**Before:** `stop = anchor − max(0.01, anchor × 0.001)`
**After:** `stop = anchor − atr_stop_multiplier × ATR(daily_bars, atr_period)`

Position sizing requires no code change — `calculate_position_size` already uses `entry_price − initial_stop_price` as risk-per-share, so a wider ATR stop automatically reduces shares, and a tighter ATR stop increases them. The risk budget per trade (`risk_per_trade_pct`) is preserved.

---

## New Settings Fields

Two new fields in `Settings`:

```python
atr_period: int = 14           # Wilder's 14-day ATR
atr_stop_multiplier: float = 1.5  # stop = anchor − 1.5 × ATR
```

Validation:
- `atr_period >= 2` (need at least 2 bars to compute a true range)
- `atr_stop_multiplier > 0`

No new env vars surfaced to operators beyond `ATR_PERIOD` / `ATR_STOP_MULTIPLIER`. Existing deployments that don't set them get the safe defaults.

---

## ATR Calculation

**File:** `src/alpaca_bot/risk/atr.py` (new)

ATR uses Wilder's smoothed formula — the industry standard:
1. True Range for bar i = max(high − low, |high − prev_close|, |low − prev_close|)
2. First ATR = simple mean of first `period` True Ranges
3. ATR[i] = (ATR[i-1] × (period − 1) + TR[i]) / period  (Wilder's smoothing)

Returns `None` if `len(daily_bars) < atr_period + 1` (need at least `period + 1` bars to compute `period` true ranges, since TR[i] requires prev_close).

Fallback: when ATR is None, each strategy falls back to the existing `max(0.01, anchor × breakout_stop_buffer_pct)` logic. The `breakout_stop_buffer_pct` setting stays in Settings and is not removed — it becomes the warm-up fallback.

---

## Strategy Changes

All five strategies follow the same pattern change:

```python
# Before
stop_buffer = max(0.01, anchor * settings.breakout_stop_buffer_pct)

# After
from alpaca_bot.risk.atr import calculate_atr
atr = calculate_atr(daily_bars, settings.atr_period)
if atr is not None:
    stop_buffer = settings.atr_stop_multiplier * atr
else:
    stop_buffer = max(0.01, anchor * settings.breakout_stop_buffer_pct)
initial_stop_price = round(anchor - stop_buffer, 2)
```

Strategy anchors (unchanged):
| Strategy | Anchor |
|---|---|
| `breakout` | `breakout_level` (N-bar high) |
| `momentum` | `yesterday_high` |
| `orb` | `opening_range_low` |
| `high_watermark` | `historical_high` |
| `ema_pullback` | `prior_bar.low` |

---

## Architecture

```
risk/
  atr.py          calculate_atr(bars, period) -> float | None
  sizing.py       unchanged
strategy/
  breakout.py     use ATR stop
  momentum.py     use ATR stop
  orb.py          use ATR stop
  high_watermark.py use ATR stop
  ema_pullback.py use ATR stop
config/__init__.py  add atr_period, atr_stop_multiplier
```

`calculate_atr` takes `Sequence[Bar]` and an integer period. It lives in `risk/` because ATR is a volatility/risk metric, not a signal metric.

---

## Edge Cases

**`initial_stop_price >= stop_price`**: Structurally impossible. `stop_price` (the order trigger) = `signal_bar.high + entry_stop_price_buffer` ≥ anchor. `initial_stop_price` = `anchor − buffer` < anchor ≤ stop_price. ✓

**ATR very large (e.g. NVDA $20 ATR, 1.5× = $30 stop)**: Sizing automatically shrinks — at 0.25% risk on $100k = $250 risk budget, quantity = floor(250/30) = 8 shares. ✓

**ATR very small (near-zero volatility)**: `stop_buffer = 1.5 × ATR` could be < $0.01. The existing `max(0.01, ...)` guard in the fallback covered this — the ATR path has no min guard. Add: `stop_buffer = max(0.01, settings.atr_stop_multiplier * atr)`. ✓

**Insufficient daily bars (< atr_period + 1)**: Returns None, falls back to buffer-pct. ✓

---

## Testing

**New file:** `tests/unit/test_atr.py`
- `test_calculate_atr_basic` — hand-calculated 3-bar ATR verifies formula
- `test_calculate_atr_returns_none_with_insufficient_bars`
- `test_calculate_atr_uses_true_range_not_high_minus_low` — bars with gaps verify abs(high-prev_close) path
- `test_calculate_atr_wilders_smoothing` — verify exponential smoothing vs. simple average

**Updates to existing strategy tests** (one new test per strategy):
- `test_X_stop_uses_atr_when_enough_daily_bars` — verify stop is ~1.5×ATR below anchor
- `test_X_stop_falls_back_to_buffer_pct_when_insufficient_bars` — fewer daily bars than atr_period+1

**Settings validation tests** appended to `test_momentum_strategy.py` (existing pattern):
- `test_atr_period_must_be_at_least_2`
- `test_atr_stop_multiplier_must_be_positive`

---

## Financial Safety

- This change affects `initial_stop_price` only — not `stop_price` (the order trigger) or `limit_price`. The broker order is unchanged. Only the protective stop level changes.
- Worst case: ATR is miscalculated → stop is too wide or too narrow. Wide stop means larger loss if hit (but position is smaller due to sizing). Narrow stop means early exit on noise. Neither is catastrophic.
- `evaluate_cycle()` remains a pure function — `calculate_atr` takes only the daily_bars sequence, no I/O.
- No new Postgres migrations required.
- Paper and live mode behave identically — the change is in the strategy/risk layer, not execution.

---

## Out of Scope

- ATR-based trailing stops (existing trailing logic stays unchanged)
- ATR as a signal filter (not a filter, only affects stop placement)
- Sector/correlation-based position limits
- Earnings blackout filter
