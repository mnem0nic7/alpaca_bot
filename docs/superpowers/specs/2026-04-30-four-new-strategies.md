# Spec: Four New Intraday Strategies

**Date:** 2026-04-30
**Strategies:** bull_flag, vwap_cross, bb_squeeze, failed_breakdown

---

## Context

The alpaca_bot now runs 7 strategies (breakout, momentum, orb, high_watermark, ema_pullback,
vwap_reversion, gap_and_go). This spec adds 4 more to cover remaining pattern archetypes:
continuation after a pole (bull_flag), VWAP reclaim (vwap_cross), volatility contraction breakout
(bb_squeeze), and bear-trap reversal (failed_breakdown).

All four follow the existing `StrategySignalEvaluator` protocol and are registered in
`STRATEGY_REGISTRY`. No engine, runtime, or database schema changes are required — adding a new
strategy is purely a matter of adding a module in `strategy/`, updating `Settings`, and registering
the function.

---

## Shared Infrastructure

### `strategy/indicators.py` (new file)

Two strategies (vwap_cross, bb_squeeze) need indicator math that also applies to existing
strategies. We create a shared module:

```
src/alpaca_bot/strategy/indicators.py
```

Exports:
- `calculate_vwap(bars: Sequence[Bar]) -> float | None` — moved from `_calculate_vwap` in
  `vwap_reversion.py`. Public function. Returns `Σ(typical_price × volume) / Σ(volume)`.
- `calculate_bollinger_bands(bars: Sequence[Bar], period: int, std_dev: float) -> tuple[float, float, float] | None`
  — returns `(lower, midline, upper)`. Uses population std dev (÷N, per Bollinger's original
  definition). Returns None if `len(bars) < period`.

`vwap_reversion.py` is updated to import `calculate_vwap` from `indicators` instead of defining
its own private `_calculate_vwap`. Existing tests that import `_calculate_vwap` from
`vwap_reversion` must be updated to import from `indicators` or the function re-exported with
backward-compat alias.

---

## Strategy 1: Bull Flag Continuation (`bull_flag`)

### Concept

After a strong early-session run (the "pole"), the stock consolidates in a tight range with
declining volume — then enters on the breakout above the consolidation bar (the "flag").

### Signal conditions

1. **Entry window** — `is_entry_session_time` passes.
2. **Trend filter** — `daily_trend_filter_passes` passes.
3. **Pole exists** — today_bars (all signal_index's session bars, excluding signal bar) must be
   non-empty.
4. **Pole run** — `(pole_high - pole_open) / pole_open >= bull_flag_min_run_pct` where:
   - `pole_open` = `pole_bars[0].open` (first today bar's open)
   - `pole_high` = `max(b.high for b in pole_bars)`
5. **Tight consolidation range** — `signal_range <= pole_range * bull_flag_consolidation_range_pct`
   where:
   - `pole_range` = `pole_high - min(b.low for b in pole_bars)`
   - `signal_range` = `signal_bar.high - signal_bar.low`
6. **Declining consolidation volume** — `signal_bar.volume <= pole_avg_volume * bull_flag_consolidation_volume_ratio`
   where `pole_avg_volume` = mean volume of pole bars.
7. **Pole on elevated volume** — `pole_avg_volume / baseline_avg_volume >= relative_volume_threshold`
   where `baseline_avg_volume` = mean volume of the `relative_volume_lookback_bars` bars before
   today's first bar.
8. **ATR guard** — `calculate_atr(daily_bars, atr_period) is not None`.

Note: The consolidation bar (signal bar) intentionally has LOW volume — checking the standard
relative_volume_threshold against the signal bar would falsely reject valid bull flags. Instead,
we check the pole bars against the pre-session baseline to confirm the run-up was on elevated
volume.

### Output

- `entry_level` = `signal_bar.high` (stop order triggers above consolidation high)
- `stop_price` = `round(signal_bar.high + entry_stop_price_buffer, 2)`
- `limit_price` = `round(stop_price * (1 + stop_limit_buffer_pct), 2)`
- `initial_stop_price` = ATR-based below `signal_bar.low`
- `relative_volume` = `pole_avg_volume / baseline_avg_volume` (reflects the pole strength)

### New Settings fields

| Field | Env var | Default | Validation |
|---|---|---|---|
| `bull_flag_min_run_pct` | `BULL_FLAG_MIN_RUN_PCT` | `0.02` | `> 0` and `< 1.0` |
| `bull_flag_consolidation_volume_ratio` | `BULL_FLAG_CONSOLIDATION_VOLUME_RATIO` | `0.6` | `> 0` and `< 1.0` |
| `bull_flag_consolidation_range_pct` | `BULL_FLAG_CONSOLIDATION_RANGE_PCT` | `0.5` | `> 0` and `< 1.0` |

---

## Strategy 2: VWAP Cross (`vwap_cross`)

### Concept

Enter when a stock crosses back above VWAP after having been below it — the prior bar closed below
VWAP and the signal bar closes above it, confirmed by above-average volume. This captures momentum
re-establishing after a morning pullback in an uptrending stock.

### Signal conditions

1. **Entry window** — `is_entry_session_time` passes.
2. **Trend filter** — `daily_trend_filter_passes` passes.
3. **Today has a prior bar** — signal bar is not the first today bar (we need a prior bar to check
   it was below VWAP).
4. **Prior bar below VWAP** — VWAP computed over today_bars excluding signal bar; prior bar's close
   < prior_vwap.
5. **Signal bar above VWAP** — VWAP computed over today_bars including signal bar; signal bar's
   close >= current_vwap.
6. **Relative volume** — `signal_bar.volume / avg_lookback_volume >= relative_volume_threshold`.
7. **ATR guard** — `calculate_atr(daily_bars, atr_period) is not None`.

### Output

- `entry_level` = `round(current_vwap, 2)` (the cross level)
- `stop_price` = `round(signal_bar.high + entry_stop_price_buffer, 2)`
- `limit_price` = `round(stop_price * (1 + stop_limit_buffer_pct), 2)`
- `initial_stop_price` = ATR-based below `signal_bar.low`
- `relative_volume` = computed normally against lookback bars

### New Settings fields

None. VWAP cross reuses `relative_volume_threshold`, `relative_volume_lookback_bars`, `atr_period`,
and other existing settings.

---

## Strategy 3: Bollinger Band Squeeze Breakout (`bb_squeeze`)

### Concept

Periods of low volatility (tight Bollinger Bands, a "squeeze") are followed by breakouts. Enter
when price closes above the upper band after at least `bb_squeeze_min_bars` consecutive bars of
squeeze (band width / midline < `bb_squeeze_threshold_pct`), confirmed by above-average volume.

### Signal conditions

1. **Entry window** — `is_entry_session_time` passes.
2. **Trend filter** — `daily_trend_filter_passes` passes.
3. **Enough history** — `signal_index >= bb_period + bb_squeeze_min_bars` (need enough bars to
   compute BB for the last squeeze bar and have `bb_squeeze_min_bars` squeeze bars).
4. **Squeeze confirmed** — for each of the last `bb_squeeze_min_bars` bars before signal_index,
   compute Bollinger Bands and verify `(upper - lower) / midline < bb_squeeze_threshold_pct`.
5. **Breakout** — signal bar's close > upper band (computed over signal_index's window).
6. **Relative volume** — `signal_bar.volume / avg_lookback_volume >= relative_volume_threshold`.
7. **ATR guard** — `calculate_atr(daily_bars, atr_period) is not None`.

### Bollinger Band calculation detail

For a given bar at index `i`, compute BB over `intraday_bars[i - bb_period + 1 : i + 1]`.
Population std dev (÷N). Returns `(lower, midline, upper)`.

### Output

- `entry_level` = `upper_band` (the breakout level, rounded to 2dp)
- `stop_price` = `round(signal_bar.high + entry_stop_price_buffer, 2)`
- `limit_price` = `round(stop_price * (1 + stop_limit_buffer_pct), 2)`
- `initial_stop_price` = ATR-based below `signal_bar.low`
- `relative_volume` = computed normally

### New Settings fields

| Field | Env var | Default | Validation |
|---|---|---|---|
| `bb_period` | `BB_PERIOD` | `20` | `>= 2` |
| `bb_std_dev` | `BB_STD_DEV` | `2.0` | `> 0` and `<= 5.0` |
| `bb_squeeze_threshold_pct` | `BB_SQUEEZE_THRESHOLD_PCT` | `0.03` | `> 0` and `< 1.0` |
| `bb_squeeze_min_bars` | `BB_SQUEEZE_MIN_BARS` | `5` | `>= 1` |

---

## Strategy 4: Failed Breakdown Reversal (`failed_breakdown`)

### Concept

Price breaks below the prior session's low (a "breakdown") but then recaptures it by bar close —
a bear trap. Sellers failed to hold the breakdown; strong volume on the recapture confirms buyers
stepped in.

### Signal conditions

1. **Entry window** — `is_entry_session_time` passes.
2. **Trend filter** — `daily_trend_filter_passes` passes (stock is in an uptrend).
3. **Prior daily bar exists** — `prior_daily = [b for b in daily_bars if b.timestamp.date() < today]`
   must be non-empty.
4. **Breakdown** — `signal_bar.low < prior_session_low` where `prior_session_low = prior_daily[-1].low`.
5. **Recapture** — `signal_bar.close >= prior_session_low * (1 + failed_breakdown_recapture_buffer_pct)`.
6. **High volume** — `signal_bar.volume / avg_lookback_volume >= failed_breakdown_volume_ratio`
   (uses relative_volume_lookback_bars for baseline, but a separate min-threshold setting).
7. **ATR guard** — `calculate_atr(daily_bars, atr_period) is not None`.

### Output

- `entry_level` = `round(prior_session_low, 2)` (the recaptured level)
- `stop_price` = `round(signal_bar.high + entry_stop_price_buffer, 2)`
- `limit_price` = `round(stop_price * (1 + stop_limit_buffer_pct), 2)`
- `initial_stop_price` = ATR-based below `signal_bar.low`
- `relative_volume` = `signal_bar.volume / avg_lookback_volume`

### New Settings fields

| Field | Env var | Default | Validation |
|---|---|---|---|
| `failed_breakdown_volume_ratio` | `FAILED_BREAKDOWN_VOLUME_RATIO` | `2.0` | `> 0` |
| `failed_breakdown_recapture_buffer_pct` | `FAILED_BREAKDOWN_RECAPTURE_BUFFER_PCT` | `0.001` | `> 0` and `< 1.0` |

---

## Safety Analysis

**Financial safety:** All four strategies produce `EntrySignal` objects consumed by the existing
`evaluate_cycle()` → intent queue → dispatch pipeline. No changes to order submission, position
sizing, or stop placement logic. Stop loss placement uses the existing `atr_stop_buffer` function.
Worst-case loss is bounded by the existing `risk_per_trade_pct` sizing — unchanged.

**Pure engine boundary:** `evaluate_cycle()` remains pure. All four strategy functions are
stateless pure functions.

**Audit trail:** No new state mutations; strategy flags are already tracked per-strategy via the
existing `DailySessionState` mechanism.

**No schema migrations:** The `strategy_name` field in `open_positions` and `daily_session_state`
accepts any string — adding new names requires no migration.

**Market hours guards:** All four use `is_entry_session_time`, which enforces entry window.
Extended hours guard is inherited.

**Paper vs live:** Identical behavior in both modes — no mode-sensitive branches.

**Env var safety:** All new fields have safe defaults in `Settings`. Existing deployments without
the new env vars pick up defaults and continue trading existing strategies.

---

## Files Changed

| File | Change |
|---|---|
| `src/alpaca_bot/strategy/indicators.py` | New — `calculate_vwap`, `calculate_bollinger_bands` |
| `src/alpaca_bot/strategy/vwap_reversion.py` | Import `calculate_vwap` from `indicators` |
| `src/alpaca_bot/strategy/bull_flag.py` | New strategy |
| `src/alpaca_bot/strategy/vwap_cross.py` | New strategy |
| `src/alpaca_bot/strategy/bb_squeeze.py` | New strategy |
| `src/alpaca_bot/strategy/failed_breakdown.py` | New strategy |
| `src/alpaca_bot/strategy/__init__.py` | Register 4 new evaluators |
| `src/alpaca_bot/config/__init__.py` | 9 new Settings fields + from_env + validate |
| `tests/unit/test_indicators.py` | New — unit tests for `calculate_vwap`, `calculate_bollinger_bands` |
| `tests/unit/test_bull_flag_strategy.py` | New — strategy tests |
| `tests/unit/test_vwap_cross_strategy.py` | New — strategy tests |
| `tests/unit/test_bb_squeeze_strategy.py` | New — strategy tests |
| `tests/unit/test_failed_breakdown_strategy.py` | New — strategy tests |
| `tests/unit/test_strategy_flags.py` | Add 4 new strategy entries to snapshot assertion |
| `tests/unit/test_vwap_reversion_strategy.py` | Update `_calculate_vwap` import to `indicators` |
