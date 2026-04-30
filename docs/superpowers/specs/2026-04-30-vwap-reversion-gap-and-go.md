# Spec: VWAP Reversion and Gap & Go Strategies

## Problem

The existing strategy set (breakout, momentum, ORB, EMA pullback, high watermark) is entirely trend-following. All five fire in the same market regime — strong upward momentum with volume. On choppy, range-bound, or low-momentum days, none fire, and equity sits idle. Two new strategies diversify the signal set:

1. **VWAP Reversion** — counter-trend, fires on dips in uptrends
2. **Gap & Go** — opening-range momentum, fires on the first bar of a gap-up day

## Goals

1. Add `vwap_reversion` and `gap_and_go` to `STRATEGY_REGISTRY`.
2. Both follow the existing `StrategySignalEvaluator` protocol — same signature, same return type.
3. Both are independently enable/disable-able via the dashboard (same `strategy_flags` table mechanism as existing strategies).
4. New settings have safe defaults so existing deployments start unchanged.
5. All 842+ existing tests continue to pass.

## Non-Goals

- No changes to `evaluate_cycle()`, the supervisor, or storage.
- No new database tables or migrations.
- No changes to order dispatch, position management, or stop logic.

## Strategy Designs

### VWAP Reversion

**Premise:** On an uptrending stock, price temporarily dips below intraday VWAP with high volume, then closes back above it — a mean-reversion entry with the trend acting as a floor.

**VWAP formula:** VWAP = Σ(typical_price × volume) / Σ(volume)  
where typical_price = (high + low + close) / 3, computed over all today's bars up to and including the signal bar.

**Signal conditions (all must be true):**
1. Signal bar is within the entry window (`is_entry_session_time`)
2. Daily trend filter passes (close > 20-day SMA — buying a dip in an uptrend)
3. VWAP is computable from today's bars (total volume > 0)
4. `signal_bar.low ≤ vwap × (1 − vwap_dip_threshold_pct)` — bar dipped sufficiently below VWAP
5. `signal_bar.close ≥ vwap` — bar closed back above VWAP (reversion confirmed)
6. Relative volume ≥ `relative_volume_threshold` (vs prior intraday lookback)
7. ATR data available from daily bars

**Entry details:**
- `entry_level` = VWAP (rounded to 2dp)
- `stop_price` = `signal_bar.high + entry_stop_price_buffer`
- `limit_price` = `stop_price × (1 + stop_limit_buffer_pct)`
- `initial_stop_price` = ATR-based buffer below `signal_bar.low`

**New setting:** `VWAP_DIP_THRESHOLD_PCT` (default `0.015` = 1.5%)

### Gap & Go

**Premise:** A stock opens significantly above its prior close with strong volume — institutional conviction. The first 15-min bar of the session IS the signal: if it gaps up, closes above the prior day's high, and shows 2× volume, that's the setup. Enter on the opening bar, stop below the prior day's high (the key gap-over level).

**Signal conditions (all must be true):**
1. Signal bar is within the entry window
2. Signal bar is the **first bar of today's session** (`len(today_bars) == 1`)
3. Daily trend filter passes
4. `signal_bar.open > prior_day_close × (1 + gap_threshold_pct)` — gapped up
5. `signal_bar.close > prior_day_high` — gap holds, closed above prior day's high
6. Relative volume ≥ `gap_volume_threshold` (vs prior intraday lookback from yesterday's bars)
7. ATR data available from daily bars

**Entry details:**
- `entry_level` = `prior_day_high`
- `stop_price` = `signal_bar.high + entry_stop_price_buffer`
- `limit_price` = `stop_price × (1 + stop_limit_buffer_pct)`
- `initial_stop_price` = ATR-based buffer below `prior_day_high`

**New settings:**
- `GAP_THRESHOLD_PCT` (default `0.02` = 2%)
- `GAP_VOLUME_THRESHOLD` (default `2.0` = 2× average)

**Why separate `GAP_VOLUME_THRESHOLD`?** The gap bar is the first bar of the session and is inherently high-volume; `relative_volume_threshold` (default 1.5) is calibrated for continuation bars. Keeping the gap volume threshold independent lets operators tune them separately without mutual interference.

**Why only the first bar?** If the gap-and-go hasn't fired by bar 2, the stock has already run away from the entry level — entering late introduces unfavorable risk/reward. Constraining to bar 1 is a hard timing gate that prevents chasing.

## New Settings Summary

| Env Var | Field | Default | Validation |
|---|---|---|---|
| `VWAP_DIP_THRESHOLD_PCT` | `vwap_dip_threshold_pct` | `0.015` | `> 0` and `< 1.0` |
| `GAP_THRESHOLD_PCT` | `gap_threshold_pct` | `0.02` | `> 0` and `< 1.0` |
| `GAP_VOLUME_THRESHOLD` | `gap_volume_threshold` | `2.0` | `> 0` |

All have defaults, so no existing deployment needs to change.

## Files Changed

| File | Change |
|---|---|
| `src/alpaca_bot/config/__init__.py` | Add 3 settings fields + `from_env` parsing + `validate()` rules |
| `src/alpaca_bot/strategy/vwap_reversion.py` | New: `_calculate_vwap()` + `evaluate_vwap_reversion_signal()` |
| `src/alpaca_bot/strategy/gap_and_go.py` | New: `evaluate_gap_and_go_signal()` |
| `src/alpaca_bot/strategy/__init__.py` | Register both new evaluators in `STRATEGY_REGISTRY` |
| `tests/unit/test_vwap_reversion_strategy.py` | New: 12+ tests |
| `tests/unit/test_gap_and_go_strategy.py` | New: 14+ tests |

## Safety Analysis

**Financial safety:** No changes to order submission, stop placement, or position sizing. New evaluators are pure functions inside the engine boundary. Worst case if stale data: same bar-age check (`2 × entry_timeframe_minutes`) suppresses stale-bar signals — identical to all existing strategies.

**Audit trail:** No new state mutations. CycleIntents from new strategies flow through the existing audit pipeline unchanged.

**Intent / dispatch separation:** Unaffected — evaluators return `EntrySignal`, engine wraps into `CycleIntent`, supervisor writes to Postgres, dispatcher submits to broker.

**Postgres advisory lock:** Unaffected.

**Pure engine boundary:** `evaluate_cycle()` is unchanged. New evaluators are pure functions, no I/O.

**Paper vs live parity:** Identical — no mode branching in new code.

**Market-hours guards:** Both evaluators call `is_entry_session_time()` as the first bar check. Cannot fire outside the entry window.

**No rollback needed:** No database migrations.

**Restart safety for Gap & Go:** If the supervisor restarts mid-session after the first bar has passed, `len(today_bars) > 1` at signal time, so the signal is suppressed. This is correct — entering a gap trade at bar 3 would be chasing.
