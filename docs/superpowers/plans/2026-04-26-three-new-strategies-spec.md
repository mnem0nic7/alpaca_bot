# Spec: Three New Long-Timeframe Trading Strategies

**Date:** 2026-04-26  
**Status:** Refined (post-grill)

## Intent

Extend the multi-strategy architecture with three additional trading strategies that complement the existing `breakout` and `momentum` strategies. Each new strategy must:

- Match the `StrategySignalEvaluator` protocol (pure stateless function)
- Register in `STRATEGY_REGISTRY` so it is auto-discovered by the supervisor fan-out
- Be independently toggle-able via the existing `StrategyFlagStore`
- Introduce minimal new Settings fields (each with safe defaults)
- Require no new migrations or storage changes

## Strategies

### 1. Opening Range Breakout (`orb`)

**Concept:** The first N 15-minute bars of the session establish a "range" (high/low). A close above that range on elevated volume signals an intraday momentum continuation.

- **Entry level:** `opening_range_high` = max high of first `orb_opening_bars` intraday bars
- **Signal condition:** `signal_bar.high > opening_range_high AND signal_bar.close > opening_range_high`
- **Volume baseline:** average volume of the opening range bars (not relative_volume_lookback_bars), so the signal can fire early
- **Stop:** `opening_range_low - buffer` (using existing `breakout_stop_buffer_pct`)
- **Trend filter:** `daily_trend_filter_passes()` (same as other strategies)
- **New setting:** `orb_opening_bars: int = 2` (first 30 minutes with 15-min bars)
- **Guard:** `signal_index < orb_opening_bars → return None`

**Distinct from breakout:** breakout uses the prior N *intraday* bars' high; ORB uses the session's own first N bars — a self-contained intraday range.

### 2. N-Day High Watermark (`high_watermark`)

**Concept:** A stock making a new N-day high (default 252 days ≈ 1 year) exhibits strong long-term momentum. The signal fires intraday when a bar closes above the historical maximum daily high.

- **Entry level:** `historical_high` = max daily high over `high_watermark_lookback_days` complete daily bars
- **Signal condition:** `signal_bar.high > historical_high AND signal_bar.close > historical_high`
- **Volume:** same `relative_volume_lookback_bars` / `relative_volume_threshold` as other strategies
- **Stop:** `historical_high - buffer` (same stop pattern as breakout/momentum)
- **Trend filter:** `daily_trend_filter_passes()` (making a new high implies uptrend, but explicit check for consistency)
- **New setting:** `high_watermark_lookback_days: int = 252`
- **Guard:** `len(daily_bars) < high_watermark_lookback_days → return None`

**Data constraint:** Requires widening the supervisor's daily bar fetch window from `max(sma_period*3, 60)` to `max(sma_period*3, 60, high_watermark_lookback_days + 10)`. This change is in `supervisor.py` line 246.

### 3. EMA Pullback (`ema_pullback`)

**Concept:** In an established uptrend, price pulls back toward the fast EMA then recovers. The signal fires when the bar close crosses back above the EMA after the prior bar was at or below it (mean-reversion-in-uptrend character).

- **EMA:** calculated from intraday bar closes over `ema_period` bars
- **Signal condition:** `prior_bar.close <= prior_ema AND signal_bar.close > current_ema`
- **Pullback definition:** user-implemented `_detect_ema_pullback()` — captures the design choice of strict (close-based) vs. loose (low-based) pullback detection
- **Entry level:** `current_ema` (rounded to 2dp)
- **Stop:** `prior_bar.low - buffer` (stop below the pullback bar's low)
- **Volume:** same relative volume check
- **Trend filter:** `daily_trend_filter_passes()`
- **New setting:** `ema_period: int = 9`
- **Guard:** `signal_index < ema_period → return None` (EMA warmup)

**Distinct from breakout/momentum:** mean-reversion character — enters on a pullback to EMA, not on a breakout above a prior high.

## Settings Summary

Three new fields added to `Settings`, all with defaults (backward-compatible):

| Field | Default | Env Var |
|---|---|---|
| `orb_opening_bars` | `2` | `ORB_OPENING_BARS` |
| `high_watermark_lookback_days` | `252` | `HIGH_WATERMARK_LOOKBACK_DAYS` |
| `ema_period` | `9` | `EMA_PERIOD` |

## Constraints & Decisions

**Multi-strategy exposure:** With 5 strategies enabled simultaneously and `max_open_positions=3` per strategy, up to 15 concurrent positions are theoretically possible. The `max_portfolio_exposure_pct` check inside `evaluate_cycle()` is per-strategy and does not aggregate cross-strategy. This is a known architectural limitation pre-existing with breakout + momentum. No change needed — the user controls exposure via toggle endpoints and position size settings.

**New strategies default to enabled:** `_resolve_active_strategies()` treats absent flag rows as enabled. All 3 new strategies will be active on first deployment. Document this clearly; operators should toggle off unwanted strategies.

**Daily bar fetch window:** Updated from `max(sma_period*3, 60)` to `max(sma_period*3, 60, high_watermark_lookback_days + 10)`. The +10 accounts for weekends and holidays. When `high_watermark_lookback_days=252`, this becomes 262 days. No new Settings field needed — the formula derives from the existing `high_watermark_lookback_days` setting.

**EMA calculation:** Computed from scratch each cycle using the Wilder/standard EMA formula starting from the first bar's close. At `relative_volume_lookback_bars=20` (default), signal requires `signal_index >= 20`, giving 20 bars of EMA warmup — sufficient for `ema_period=9`.

**No new tables:** All three strategies use the existing `strategy_flags` table (already built in Task 7 of phase 7). No migration needed.

**Paper vs live:** Signal functions are pure and mode-agnostic. `ENABLE_LIVE_TRADING=false` remains an effective gate at the broker call level (unchanged).

**Market-hours guards:** All three call `is_entry_session_time()` which checks the signal bar's timestamp against `entry_window_start`/`entry_window_end`.
