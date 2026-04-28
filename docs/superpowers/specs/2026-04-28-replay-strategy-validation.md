---
title: Replay Strategy Validation
date: 2026-04-28
status: approved
---

# Replay Strategy Validation

## Problem

Four strategies (momentum, orb, high_watermark, ema_pullback) have thorough signal-level unit tests but have never been exercised through `ReplayRunner.run()`. The replay runner integration test file (`tests/unit/test_replay_golden.py`) only covers the `breakout` strategy. This means regressions in the runner's event loop, order placement, fill simulation, stop processing, and EOD exit logic would go undetected for the four newer strategies.

## Goals

1. Close the ReplayRunner integration gap for all four non-breakout strategies.
2. Prove that each strategy can produce `ENTRY_ORDER_PLACED` → `ENTRY_FILLED` through the full simulation loop.
3. Verify guard cases: insufficient daily bars, insufficient intraday bars, and no signal fire → zero trades.
4. Keep tests fast (no I/O, all in-memory) and deterministic (no randomness).

## Non-goals

- Downloading real historical data (that's backfill, already implemented).
- Comparing PnL across strategies (that's sweep/compare, already implemented).
- Adding new golden JSON scenario files (we use in-memory `ReplayScenario` objects instead).
- Changing any production code.

## Approach

Create `tests/unit/test_replay_strategies.py` with self-contained in-memory `ReplayScenario` objects. Use small `Settings` parameters so minimal bar counts are needed. For each of the four strategies:

- **Happy path**: construct minimal bars that satisfy all entry conditions; assert the result contains `ENTRY_ORDER_PLACED` and `ENTRY_FILLED` events.
- **Guard path**: construct bars with insufficient data (too few daily bars, too few intraday bars); assert zero trades.

Reuse the `_make_settings()` helper pattern already established in the test suite.

## Strategy-specific requirements

### momentum
- `DAILY_SMA_PERIOD=5`, `PRIOR_DAY_HIGH_LOOKBACK_BARS=1`, `RELATIVE_VOLUME_LOOKBACK_BARS=5`
- Needs 6+ prior daily bars (5 for SMA + 1 for trend filter's close > SMA check), today's daily bar, and 6+ intraday bars (5 for relative volume + 1 signal bar)
- Signal fires when `signal_bar.high > yesterday_high AND signal_bar.close > yesterday_high AND relative_volume ≥ threshold`

### orb
- `ORB_OPENING_BARS=2`, `DAILY_SMA_PERIOD=5`, `RELATIVE_VOLUME_LOOKBACK_BARS=2`
- Needs 6+ daily bars; 3+ intraday bars today (2 opening range bars + 1 signal bar)
- Volume baseline is the 2 opening range bars; signal bar must beat the opening range high by close

### high_watermark
- `HIGH_WATERMARK_LOOKBACK_DAYS=5`, `DAILY_SMA_PERIOD=5`, `RELATIVE_VOLUME_LOOKBACK_BARS=5`
- Needs 6 completed daily bars (5 lookback + 1 for trend SMA trailing), plus today's partial bar
- Signal fires when `signal_bar.high > max(last-5-day-highs) AND signal_bar.close > that high`

### ema_pullback
- `EMA_PERIOD=5`, `DAILY_SMA_PERIOD=5`, `RELATIVE_VOLUME_LOOKBACK_BARS=5`
- Needs `signal_index >= ema_period` (≥ 5 intraday bars before signal); 6+ daily bars
- Signal fires when prior bar closed ≤ prior EMA and current bar closes above current EMA

## Bar construction contract

All bars must use ET session timestamps (14:30 UTC = 10:30 ET, within 10:00–15:30 entry window). Prices must be consistent: `low ≤ open, close ≤ high`. For fill simulation, the execution bar must have `high ≥ stop_price` and `open ≤ limit_price`.

## Files changed

| File | Change |
|------|--------|
| `tests/unit/test_replay_strategies.py` | New — 8 test functions (2 per strategy: happy + guard) |

No production code changes.
