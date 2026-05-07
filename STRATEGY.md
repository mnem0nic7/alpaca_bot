# Strategy Reference

Derived from the live codebase (`src/alpaca_bot/`). All file references are relative to the repo root.

---

## Table of Contents

1. [Trading Cycle Overview](#trading-cycle-overview)
2. [Universal Pre-Entry Filters](#universal-pre-entry-filters)
3. [Entry Ranking and Capacity Management](#entry-ranking-and-capacity-management)
4. [Stop Management](#stop-management)
5. [Exit Logic](#exit-logic)
6. [Risk and Position Sizing](#risk-and-position-sizing)
7. [Session Time Rules](#session-time-rules)
8. [Strategy Weighting](#strategy-weighting)
9. [Individual Strategies](#individual-strategies)
10. [Extended Hours Trading](#extended-hours-trading)
11. [Decision Audit Trail](#decision-audit-trail)
12. [Parameter Reference](#parameter-reference)

---

## Trading Cycle Overview

`evaluate_cycle()` (`src/alpaca_bot/core/engine.py`) is a **pure function** — no I/O, no side effects. It runs on a 60-second polling loop and produces a `CycleResult` containing `CycleIntent` objects (ENTRY, UPDATE_STOP, EXIT). Intents are written to Postgres and dispatched separately.

**Order of operations per cycle:**

1. If `flatten_all=True`: emit EXIT for every open position → return immediately.
2. **Manage existing positions**: for each open position, evaluate exit conditions (eod_flatten, trend filter, VWAP breakdown) and stop-update rules (trailing stop, profit trail, stop cap).
3. **Pre-entry gates**: check `entries_disabled`, then regime filter, then capacity.
4. **Per-symbol evaluation**: for each symbol in the configured list, apply entry filters (existing position, already traded, bar age, news, spread) → call signal evaluator → size position → collect as candidate.
5. **Rank and select**: sort candidates by signal strength and volume; greedily select up to `MAX_OPEN_POSITIONS` slots subject to `MAX_PORTFOLIO_EXPOSURE_PCT`.
6. **Decision records**: emit a `DecisionRecord` for every symbol evaluated, whether accepted or rejected.

---

## Universal Pre-Entry Filters

These gates block all new entries for the cycle. Existing positions are unaffected.

### `entries_disabled`

Set by the session manager when the daily loss limit is breached or an operator `halt` command is issued. Checked before every other entry gate.

### Regime Filter

**Parameter**: `ENABLE_REGIME_FILTER` (default: off)

Checks whether the broad market (default: SPY) is above its `REGIME_SMA_PERIOD`-day simple moving average. Uses the **same window calculation as `daily_trend_filter_passes()`** — excludes today's potentially partial bar:

```
window = regime_bars[-(REGIME_SMA_PERIOD + 1) : -1]
sma = mean(close for bar in window)
if window[-1].close <= sma → block all entries
```

When blocked, each eligible symbol gets a `DecisionRecord` with `reject_stage="pre_filter"`, `reject_reason="regime_blocked"`.

**Why exclude today's bar**: Alpaca streams in-progress daily bars before market close. Using them creates look-ahead bias — decisions are made on incomplete data.

### Capacity Check

```
available_slots = MAX_OPEN_POSITIONS - len(open_positions) - len(working_order_symbols)
```

If `global_open_count` is supplied (multi-strategy mode), it replaces `len(open_positions)`. When `available_slots == 0`, all candidate symbols receive a `reject_stage="capacity"`, `reject_reason="capacity_full"` record.

### Per-Symbol Filters (inside entry loop)

| Filter | Condition | Decision |
|---|---|---|
| Existing position | symbol in open_position_symbols or working_order_symbols | `skipped_existing_position` |
| Already traded today | (symbol, session_day) in traded_symbols_today | `skipped_already_traded` |
| Stale bars | bar_age > 2 × ENTRY_TIMEFRAME_MINUTES (regular session only) | skip (no record) |
| News filter | headline contains any of NEWS_FILTER_KEYWORDS | `rejected`, stage=`filter`, reason=`news_blocked` |
| Spread filter | NBBO spread_pct > MAX_SPREAD_PCT (or EXTENDED_HOURS_MAX_SPREAD_PCT) | `rejected`, stage=`filter`, reason=`spread_blocked` |
| No signal | signal_evaluator returns None | `skipped_no_signal` |
| Invalid sizing | quantity ≤ 0 or notional < MIN_POSITION_NOTIONAL | skip (no record) |

**News filter keywords** (default): `earnings`, `revenue`, `fda`, `clinical`, `trial`, `guidance`

**Why news filter**: Binary catalyst events produce gap moves that violate stop assumptions. The filter avoids entering into known event risk.

**Why spread filter**: Wide spreads indicate illiquidity. The cost of entry/exit exceeds the expected edge. Extended hours uses a looser threshold (100 bps vs 20 bps regular) because pre/after-market spreads are structurally wider.

---

## Entry Ranking and Capacity Management

Eligible candidates are sorted (descending priority):

1. **Relative strength**: `(signal_bar.close / entry_level) - 1` — how far price has moved above the breakout level. Higher = stronger breakout.
2. **Relative volume**: volume / average_volume — conviction behind the move.
3. **Symbol** (ascending) — deterministic tie-break.

Candidates are then selected greedily in sorted order, stopping when either:
- `available_slots` is exhausted, or
- Adding the next candidate would push `current_exposure + candidate_exposure` above `MAX_PORTFOLIO_EXPOSURE_PCT`.

**Why relative strength as primary sort**: A bar that closes significantly above the breakout level is more likely to continue; a bar that barely closes above it is a weak signal. This preference concentrates capital in the highest-conviction candidates.

**Portfolio exposure cap**: Prevents over-concentration in a single direction even when many signals appear simultaneously. At 30% gross notional and ~1.5% max per position, the system can hold up to 20 positions while staying under the cap.

---

## Stop Management

Three rules run in sequence for each existing position during each cycle. Rules can compound — a position may receive multiple UPDATE_STOP intents, and the executor takes the highest.

### Rule 1: Trailing Stop (ATR-based)

**Trigger**: `latest_bar.high >= entry_price + (TRAILING_STOP_PROFIT_TRIGGER_R × risk_per_share)`

Default `TRAILING_STOP_PROFIT_TRIGGER_R = 1.0`, meaning the position must reach breakeven + full initial risk before trailing activates. This prevents whipsaw on positions that haven't proven profitable.

**Calculation** (if `TRAILING_STOP_ATR_MULTIPLIER > 0`):
```
atr = calculate_atr(daily_bars, ATR_PERIOD)
trailing_candidate = latest_bar.high - (TRAILING_STOP_ATR_MULTIPLIER × atr)
new_stop = max(current_stop, entry_price, trailing_candidate)
```

If `TRAILING_STOP_ATR_MULTIPLIER == 0` (default), falls back to `max(current_stop, entry_price, latest_bar.low)`.

The stop can only rise — never falls once set.

### Rule 2: Profit Trail (percentage of session high)

**Activation**: `ENABLE_PROFIT_TRAIL = true`, regular session only (disabled in extended hours due to illiquidity).

```
today_high = max(bar.high for bar in today's intraday bars)
trail_candidate = round(today_high × PROFIT_TRAIL_PCT, 2)
new_stop = max(prior_stop_from_rule_1, trail_candidate)
```

Default `PROFIT_TRAIL_PCT = 0.95` → stop trails 5% below the session high. Emits UPDATE_STOP if `trail_candidate > prior_stop`.

**Why session high (not bar high)**: The full intraday excursion is the relevant reference for day-trading. Using the session high captures any morning spike even if price has since pulled back.

### Rule 3: Stop Cap

**Activation**: Regular session only.

```
cap_stop = round(entry_price × (1 - MAX_STOP_PCT), 2)
effective_stop = max(from UPDATE_STOP intents this cycle, or current position.stop_price)
if effective_stop < cap_stop → emit UPDATE_STOP to cap_stop
```

Default `MAX_STOP_PCT = 0.05` (5% below entry). Hard rule — overrides any stop that has drifted too far below entry. Prevents outsized losses on stalled or gap-down positions.

**Initial stop cap at entry**: The same `max_stop_pct` cap is also applied to `initial_stop_price` during entry sizing: `effective_initial_stop = max(signal.initial_stop_price, entry_price × (1 - MAX_STOP_PCT))`. This ensures the ATR-derived stop can never be deeper than the policy limit even for high-ATR stocks.

---

## Exit Logic

Exit conditions are evaluated for each open position in each cycle. The first matching condition emits an EXIT intent and moves to the next position.

### End-of-Day Flatten (`eod_flatten`)

```
if is_flatten_time(now, settings, session_type):
    emit EXIT(reason="eod_flatten")
```

**Regular session**: `local_time >= FLATTEN_TIME` (default 15:45 ET)
**After-hours** (if enabled): `local_time >= EXTENDED_HOURS_FLATTEN_TIME` (default 19:45 ET)

In extended hours, exits use `limit_price = close × (1 - EXTENDED_HOURS_LIMIT_OFFSET_PCT)` to ensure fills in thin markets.

**Why 15:45 (not 16:00)**: The 15-minute buffer before close protects against market-on-close auction risk and last-minute volatility. Positions held to the close risk wider spreads and unexpected final prints.

### Loss Limit Flatten (`loss_limit_flatten`)

Triggered when the session's cumulative P&L breaches `DAILY_LOSS_LIMIT_PCT`. The supervisor sets `flatten_all=True` which causes `evaluate_cycle()` to emit EXIT for every position before any other logic runs.

### Viability: Trend Filter (`viability_trend_filter_failed`)

**Activation**: `ENABLE_TREND_FILTER_EXIT = true` AND `position_age >= VIABILITY_MIN_HOLD_MINUTES` AND fresh daily data (bar_age ≤ `VIABILITY_DAILY_BAR_MAX_AGE_DAYS`).

Re-runs `daily_trend_filter_passes()` on current bars. If the daily SMA condition is now False (price has fallen below the 20-day SMA since entry), emit EXIT.

**Why minimum hold time**: Prevents exiting a position that triggered the trend filter due to a brief intraday dip. The hold time requirement (default 0 → disabled) allows the position to "breathe" before the viability logic can trigger.

### Viability: VWAP Breakdown (`viability_vwap_breakdown`)

**Activation**: `ENABLE_VWAP_BREAKDOWN_EXIT = true` AND `position_age >= VIABILITY_MIN_HOLD_MINUTES` AND `len(today_bars) >= VWAP_BREAKDOWN_MIN_BARS`.

```
vwap = Σ((high + low + close) / 3 × volume) / Σ(volume)
if latest_close < vwap → emit EXIT
```

**Why VWAP as a viability signal**: VWAP is the institutional cost basis for the day. When price breaks below it, the institutional bid that sustained the breakout has likely exhausted — the move is fading.

---

## Risk and Position Sizing

**Source**: `src/alpaca_bot/risk/` (`__init__.py`, `option_sizing.py`, `atr.py`)

### Equity Position Sizing

```
risk_per_share = entry_price - initial_stop_price

STEP 1: Risk-based quantity
    quantity = (equity × RISK_PER_TRADE_PCT) / risk_per_share

STEP 2: Dollar cap (if MAX_LOSS_PER_TRADE_DOLLARS set)
    quantity = min(quantity, MAX_LOSS_PER_TRADE_DOLLARS / risk_per_share)

STEP 3: Position size cap
    max_notional = equity × MAX_POSITION_PCT
    if quantity × entry_price > max_notional:
        quantity = max_notional / entry_price

STEP 4: Fractional rounding
    if not fractionable: quantity = floor(quantity)
    if quantity < 1: return 0.0
```

**Example**: Equity $100,000, entry $50, initial stop $45, RISK_PER_TRADE_PCT=0.0025, MAX_POSITION_PCT=0.015
- risk_per_share = $5
- risk_budget = $250 → quantity = 50 shares → notional $2,500
- max_notional = $1,500 → capped at 30 shares

**Why two caps**: `RISK_PER_TRADE_PCT` controls expected loss in normal operation. `MAX_POSITION_PCT` prevents extreme position concentration when the stop is very tight (e.g., $0.10 away on a $50 stock would produce an enormous quantity under risk-only sizing).

### ATR Stop Buffer Calculation

The initial stop is placed below the breakout level using the ATR:

```
atr = calculate_atr(daily_bars, ATR_PERIOD)
stop_buffer = max(MIN_BUFFER, ATR_STOP_MULTIPLIER × atr)
initial_stop_price = max(0.01, breakout_level - stop_buffer)
```

Falls back to `breakout_level × BREAKOUT_STOP_BUFFER_PCT` if ATR is unavailable.

**Why ATR for stops**: ATR adapts to each stock's volatility regime. A fixed-percentage stop is too tight for high-ATR stocks (frequent stops on noise) and too loose for low-ATR stocks (excessive loss per trade). ATR produces stops that reflect the stock's normal daily range.

### Options Position Sizing

```
contract_cost = ask × 100
risk_budget = equity × RISK_PER_TRADE_PCT
contracts = floor(risk_budget / contract_cost)
max_contracts = floor((equity × MAX_POSITION_PCT) / contract_cost)
quantity = max(0, min(contracts, max_contracts))
```

Options are defined-risk (premium paid = max loss), so no stop price is used.

---

## Session Time Rules

**Source**: `src/alpaca_bot/strategy/session.py`

### Session Detection

Times are in US/Eastern. Detection uses `settings.market_timezone` (default `America/New_York`).

| Session | Time Range (ET) |
|---|---|
| `CLOSED` | 20:00 – 04:00 |
| `PRE_MARKET` | 04:00 – 09:30 |
| `REGULAR` | 09:30 – 16:00 |
| `AFTER_HOURS` | 16:00 – 20:00 |

### Entry Windows

| Session | Start | End | Parameter Names |
|---|---|---|---|
| Regular | 10:00 | 15:30 | `ENTRY_WINDOW_START`, `ENTRY_WINDOW_END` |
| Pre-Market | 04:00 | 09:20 | `PRE_MARKET_ENTRY_WINDOW_START`, `PRE_MARKET_ENTRY_WINDOW_END` |
| After-Hours | 16:05 | 19:30 | `AFTER_HOURS_ENTRY_WINDOW_START`, `AFTER_HOURS_ENTRY_WINDOW_END` |

**Why 10:00 start (not 09:30)**: The first 30 minutes after the open are characterised by erratic price discovery and wide spreads. Breakout signals during this period have higher false-positive rates. Waiting until 10:00 allows the opening auction and early noise to settle.

### Flatten Times

| Session | Flatten Time | Parameter |
|---|---|---|
| Regular | 15:45 | `FLATTEN_TIME` |
| After-Hours (if enabled) | 19:45 | `EXTENDED_HOURS_FLATTEN_TIME` |

### After-Hours Signal Evaluation

During `AFTER_HOURS`, the signal evaluator is called on the **last bar that falls within the regular session entry window**, not the most recent bar. This prevents after-hours price action from distorting the breakout level used during the regular session.

---

## Strategy Weighting

**Source**: `src/alpaca_bot/risk/weighting.py`

In multi-strategy mode, each strategy is allocated a fraction of total equity based on its historical Sharpe ratio.

### Sharpe Calculation

```
For each strategy, collect daily P&L from all closed trades:
    daily_pnl[strategy][date] += trade.pnl

annualized_sharpe = (mean(daily_pnl) / std(daily_pnl)) × sqrt(252)
```

Strategies with fewer than `MIN_TRADES` (default 5) receive Sharpe = 0.

### Weight Derivation

```
STEP 1: Sharpe-proportional weights
    weight[s] = sharpe[s] / sum(sharpes)
    (if all Sharpes = 0: equal weight)

STEP 2: Cap overweighted strategies (max_weight=0.40)
    Redistribute excess proportionally to under-cap strategies

STEP 3: Floor underweighted strategies (min_weight=0.01)
    Raise to floor, take shortfall from above-floor strategies

STEP 4: Normalize to sum = 1.0
```

**Why Sharpe not raw returns**: Raw returns ignore risk — a strategy that makes $1,000 with extreme volatility is less desirable than one that makes $800 with smooth daily gains. Sharpe ratio rewards consistent returns.

**Why minimum weight (1%)**: Keeps all configured strategies "alive" even through drawdown periods. A strategy that has temporarily underperformed retains a small stake so it can benefit from mean-reversion without needing manual intervention.

**Why maximum weight (40%)**: Prevents over-reliance on a single strategy that happens to have a strong recent Sharpe. Diversification across strategies reduces strategy-specific risk.

---

## Individual Strategies

All strategies implement the `StrategySignalEvaluator` protocol and are registered in `STRATEGY_REGISTRY` at `src/alpaca_bot/strategy/__init__.py`.

### 1. Breakout (`breakout`)

**Source**: `src/alpaca_bot/strategy/breakout.py`

The primary and most-tested strategy.

**Signal conditions** (all must be true):
1. `signal_bar.high > breakout_level` — bar breaks above the level
2. `signal_bar.close > breakout_level` — bar closes above (no wick reversal)
3. `relative_volume >= RELATIVE_VOLUME_THRESHOLD` — elevated volume confirms conviction
4. `daily_trend_filter_passes()` — daily SMA trend is up
5. Bar's timestamp is within the entry window for its session
6. ATR data is available for stop calculation

**Breakout level**: `max(high for bar in last BREAKOUT_LOOKBACK_BARS intraday bars)` — the highest high over the lookback window.

**Stop placement**: ATR-based below the breakout level; entry stop (for limit order) is at `breakout_level + ENTRY_STOP_PRICE_BUFFER`.

**Rationale**: A stock breaking to new N-bar highs on elevated volume is exhibiting relative strength. The daily trend filter ensures entries are with the primary trend, not against it. Volume confirmation distinguishes genuine breakouts from false ones.

### 2. Momentum (`momentum`)

**Source**: `src/alpaca_bot/strategy/momentum.py`

Enters on continuation of a prior-day high breakout.

**Signal conditions**:
1. `signal_bar.high > prior_day_high` — exceeds yesterday's high
2. `signal_bar.close > prior_day_high` — closes above
3. Relative volume above threshold
4. Daily trend filter passes

**Entry level**: Prior-day high (`PRIOR_DAY_HIGH_LOOKBACK_BARS` days back, default 1).

**Rationale**: Prior-day high is a well-known resistance level. When it breaks on volume, it signals a continuation of upward momentum that often attracts additional buyers.

### 3. Opening Range Breakout (`orb`)

**Source**: `src/alpaca_bot/strategy/orb.py`

Trades the first clean breakout above the opening range.

**Opening range**: High/low established over the first `ORB_OPENING_BARS` 15-minute bars (default 2 = first 30 minutes).

**Signal conditions**:
1. Current bar is after the opening range formation period
2. `signal_bar.high > opening_range_high` and `signal_bar.close > opening_range_high`
3. Relative volume above threshold
4. Daily trend filter passes

**Rationale**: The opening range captures the first directional conviction of the day. A breakout above it, especially on volume, often sets the directional tone for the session.

### 4. High Watermark (`high_watermark`)

**Source**: `src/alpaca_bot/strategy/high_watermark.py`

Enters when price breaks to a multi-month high.

**Entry level**: Maximum high over last `HIGH_WATERMARK_LOOKBACK_DAYS` (default 252 = 1 year) daily bars.

**Signal conditions**:
1. `signal_bar.close > high_watermark` (intraday bar)
2. Relative volume above threshold
3. Daily trend filter passes

**Rationale**: A 52-week high is a significant psychological and technical level. Breakouts at such levels often attract momentum buyers and short-covering, producing sustained moves.

### 5. EMA Pullback (`ema_pullback`)

**Source**: `src/alpaca_bot/strategy/ema_pullback.py`

Buys a pullback to the exponential moving average in an uptrend.

**Setup conditions**:
1. Daily trend filter passes (uptrend on daily SMA)
2. Recent bars have been above the `EMA_PERIOD`-bar EMA (default 9)
3. Latest bar has pulled back to touch or briefly breach the EMA
4. Current bar bounces and closes above the EMA on elevated volume

**Entry level**: EMA value at signal time.

**Rationale**: In strong trends, pullbacks to short-term EMAs offer entries with defined risk (stop below the EMA or pullback low). The EMA acts as dynamic support.

### 6. VWAP Reversion (`vwap_reversion`)

**Source**: `src/alpaca_bot/strategy/vwap_reversion.py`

Mean-reversion entry when price dips below VWAP by a threshold.

**Signal conditions**:
1. Price has dipped below VWAP by at least `VWAP_DIP_THRESHOLD_PCT` (default 1.5%)
2. Current bar closes back above VWAP (reversal confirmed)
3. Relative volume above threshold
4. Daily trend filter passes (entry in uptrending stocks only)

**Entry level**: VWAP at signal time.

**Rationale**: VWAP is the volume-weighted cost basis for institutional participants. A brief dip below it in an uptrending stock often represents a flush-and-recover pattern where institutional buyers step in at the VWAP level.

### 7. Gap and Go (`gap_and_go`)

**Source**: `src/alpaca_bot/strategy/gap_and_go.py`

Trades intraday continuation after a significant opening gap.

**Gap detection**: `open_today > prior_close × (1 + GAP_THRESHOLD_PCT)` (default 2% gap).

**Signal conditions**:
1. Stock gapped up by at least `GAP_THRESHOLD_PCT` at open
2. `open_volume > GAP_VOLUME_THRESHOLD × average_volume` — gap is supported by heavy volume
3. Signal bar closes above its open (gap filling is not occurring)
4. Entry is within the entry window (default 10:00–15:30)

**Entry level**: Opening bar's close or the pre-market high.

**Rationale**: Large gap-ups on big volume often indicate a fundamental catalyst. If the stock holds the gap in the first hour, it frequently continues higher as short-sellers cover and momentum buyers pile in.

### 8. Bull Flag (`bull_flag`)

**Source**: `src/alpaca_bot/strategy/bull_flag.py`

Enters on the breakout from a consolidation following an initial strong move.

**Flag detection**:
1. Initial "pole": a run-up of at least `BULL_FLAG_MIN_RUN_PCT` (default 2%) over recent bars
2. "Flag" (consolidation): subsequent bars with:
   - Volume declining: consolidation volume < `BULL_FLAG_CONSOLIDATION_VOLUME_RATIO` × pole volume
   - Price range contracting: consolidation range < `BULL_FLAG_CONSOLIDATION_RANGE_PCT` × pole range
3. Breakout: signal bar closes above the consolidation high on elevated volume

**Entry level**: Top of the consolidation (flag top).

**Rationale**: Bull flags represent institutional accumulation during a pause in upward momentum. The contraction in volume and range shows selling pressure is absorbed. The breakout from the flag signals resumption of the original move.

### 9. VWAP Cross (`vwap_cross`)

**Source**: `src/alpaca_bot/strategy/vwap_cross.py`

Enters on the first clean cross above VWAP after trading below it.

**Signal conditions**:
1. Prior bars were below VWAP
2. Signal bar closes above VWAP for the first time today
3. Relative volume above threshold
4. Daily trend filter passes

**Entry level**: VWAP at cross time.

**Rationale**: The first VWAP cross of the session often marks a shift in intraday supply/demand balance. Once the stock reclaims VWAP, the day's losing sellers are underwater and buying pressure can accelerate.

### 10. Bollinger Band Squeeze (`bb_squeeze`)

**Source**: `src/alpaca_bot/strategy/bb_squeeze.py`

Trades the expansion out of a period of low volatility (squeeze).

**Squeeze detection**:
1. Bollinger Bands (`BB_PERIOD`=20, `BB_STD_DEV`=2.0) have been contracting for `BB_SQUEEZE_MIN_BARS` (default 5) consecutive bars
2. Band width < `BB_SQUEEZE_THRESHOLD_PCT` (default 3% of price) — unusually narrow bands

**Breakout conditions**:
1. Signal bar closes above the upper Bollinger Band
2. Relative volume above threshold
3. Daily trend filter passes

**Entry level**: Upper Bollinger Band at signal time.

**Rationale**: Low volatility (squeeze) periods precede high volatility expansions. When the bands narrow to extremes and price breaks the upper band on volume, it indicates pent-up energy releasing to the upside.

### 11. Failed Breakdown (`failed_breakdown`)

**Source**: `src/alpaca_bot/strategy/failed_breakdown.py`

Enters on a "bear trap" — a breakdown below support that quickly reverses.

**Failed breakdown detection**:
1. Price briefly trades below a support level (prior day's low or recent consolidation low)
2. Reversal: signal bar closes back above the support level
3. Volume spike: `signal_bar.volume > FAILED_BREAKDOWN_VOLUME_RATIO × average_volume` (default 2× — higher threshold than other strategies)
4. Close above support level by at least `FAILED_BREAKDOWN_RECAPTURE_BUFFER_PCT`

**Entry level**: Support level that was recaptured.

**Rationale**: When shorts pile in on a breakdown and the stock immediately reverses, trapped shorts must cover, creating a squeeze. The high volume requirement filters for genuine reversals vs. noise. This strategy operates counter to the trend-following strategies and provides diversification.

### Options Strategy (`breakout_calls`)

**Source**: `src/alpaca_bot/strategy/breakout.py` (options path in `evaluate_breakout_signal`)

Applies the same breakout signal logic but enters call options when `ENABLE_OPTIONS_TRADING = true` and a suitable contract exists.

**Option selection criteria**:
- DTE: between `OPTION_DTE_MIN` (21) and `OPTION_DTE_MAX` (60)
- Delta: closest to `OPTION_DELTA_TARGET` (0.50 = at-the-money)

**Sizing**: Premium paid = max loss; `calculate_option_position_size()` uses the premium as the risk amount (no stop price required).

---

## Extended Hours Trading

**Parameter**: `EXTENDED_HOURS_ENABLED` (default: off)

When enabled, the supervisor detects session type (`PRE_MARKET` or `AFTER_HOURS`) and passes it to `evaluate_cycle()`. Several guard rails apply:

### Extended Hours Entry Guards

- Spread filter uses the looser `EXTENDED_HOURS_MAX_SPREAD_PCT` (default 100 bps vs 20 bps regular).
- Bar age check is **bypassed** — bars may be sparse in extended hours, so the age threshold used during regular session would incorrectly reject valid signals.
- After-hours signal evaluation uses the **last regular session bar** within the entry window, not the most recent after-hours bar. This prevents after-hours noise from invalidating a valid regular-session signal.

### Extended Hours Stop Management

- **Trailing stop** (Rule 1): ATR-based stop still applies during extended hours.
- **Profit trail** (Rule 2): **Disabled** in extended hours — illiquid sessions make intraday highs unreliable as trailing references.
- **Stop cap** (Rule 3): **Disabled** in extended hours — the cap-up pass runs only during regular session.

### Extended Hours Exits

- End-of-day flatten uses `limit_price = close × (1 - EXTENDED_HOURS_LIMIT_OFFSET_PCT)` to account for wide spreads.
- `is_flatten_time()` returns True immediately on transitioning to after-hours if `extended_hours_enabled = false`, ensuring all positions are exited before after-hours opens.

---

## Decision Audit Trail

Every `evaluate_cycle()` call produces a `DecisionRecord` for every symbol evaluated. Records are stored in the `decision_log` table (migration 015) and written best-effort from `run_cycle()` in `src/alpaca_bot/runtime/cycle.py`.

### Decision Values

| `decision` | Meaning |
|---|---|
| `accepted` | Signal passed all filters; entry intent emitted |
| `rejected` | Signal failed a filter |
| `skipped_existing_position` | Symbol has an open position or working order |
| `skipped_already_traded` | Symbol has already traded today in this session |
| `skipped_no_signal` | Signal evaluator returned None |

### Reject Stages and Reasons

| `reject_stage` | `reject_reason` | Meaning |
|---|---|---|
| `pre_filter` | `regime_blocked` | Regime filter blocked all entries |
| `capacity` | `capacity_full` | No open slots available |
| `filter` | `news_blocked` | News filter matched a keyword |
| `filter` | `spread_blocked` | NBBO spread exceeded threshold |
| `sizing` | `quantity_zero` | Sized to zero shares |
| `sizing` | `below_min_notional` | Position too small in dollar terms |

### Useful Query Patterns

```sql
-- Win rate by strategy for accepted entries that have closed
SELECT strategy_name,
       COUNT(*) FILTER (WHERE decision = 'accepted') AS accepted,
       COUNT(*) FILTER (WHERE reject_stage = 'pre_filter') AS regime_blocked
FROM decision_log
WHERE cycle_at > now() - interval '30 days'
GROUP BY strategy_name
ORDER BY accepted DESC;

-- What rejected most signals today?
SELECT reject_stage, reject_reason, COUNT(*) AS n
FROM decision_log
WHERE cycle_at::date = current_date
  AND decision = 'rejected'
GROUP BY reject_stage, reject_reason
ORDER BY n DESC;

-- Accepted entries with their sizing
SELECT cycle_at, symbol, strategy_name, entry_level, relative_volume,
       quantity, limit_price, risk_per_share, equity
FROM decision_log
WHERE decision = 'accepted'
ORDER BY cycle_at DESC
LIMIT 50;
```

---

## Parameter Reference

All parameters are read from environment variables at startup via `Settings.from_env()` (`src/alpaca_bot/config/__init__.py`). No `.env` autoload — values must be set in the environment.

### Entry Signal Parameters

| Parameter | Default | Unit | Rationale |
|---|---|---|---|
| `ENTRY_TIMEFRAME_MINUTES` | 15 | minutes | Hardcoded in `Settings.validate()`. 15-min bars balance signal frequency with noise reduction. |
| `ENTRY_WINDOW_START` | 10:00 | ET | Avoids opening 30-minute noise and wide spreads. |
| `ENTRY_WINDOW_END` | 15:30 | ET | Stops new entries 15 min before flatten; no new positions into close. |
| `FLATTEN_TIME` | 15:45 | ET | 15-min buffer before close for orderly exit. |
| `BREAKOUT_LOOKBACK_BARS` | 20 | bars | ~5 hours of 15-min bars. Broad enough to capture meaningful levels without being too historical. |
| `RELATIVE_VOLUME_THRESHOLD` | 1.5 | ratio | 50% above average. Filters out low-conviction bars. Higher = fewer but stronger signals. |
| `RELATIVE_VOLUME_LOOKBACK_BARS` | 20 | bars | Same window as breakout lookback for consistency. |
| `DAILY_SMA_PERIOD` | 20 | days | ~1 trading month. Standard short-term trend definition. |

### Risk Parameters

| Parameter | Default | Unit | Rationale |
|---|---|---|---|
| `RISK_PER_TRADE_PCT` | 0.0025 | fraction | 0.25% equity at risk per trade. At 20 positions, total theoretical risk is 5% — manageable. |
| `MAX_POSITION_PCT` | 0.015 | fraction | 1.5% of equity max per position. Limits concentration; 20 positions = 30% gross notional. |
| `MAX_OPEN_POSITIONS` | 20 | count | Balances diversification benefit against tracking complexity. |
| `MAX_PORTFOLIO_EXPOSURE_PCT` | 0.30 | fraction | 30% gross notional cap. Prevents over-leveraged portfolios even with many small positions. |
| `MAX_STOP_PCT` | 0.05 | fraction | 5% max drawdown from entry before forced stop-up. Hard limit on individual position losses. |
| `MIN_POSITION_NOTIONAL` | 0.0 | dollars | Disabled by default. Set to filter out penny-stock sizing anomalies. |

### Stop and ATR Parameters

| Parameter | Default | Unit | Rationale |
|---|---|---|---|
| `ATR_PERIOD` | 14 | days | Standard Wilder ATR period. Captures ~3 weeks of volatility history. |
| `ATR_STOP_MULTIPLIER` | 1.0 | × ATR | 1 ATR below breakout level. Allows normal daily noise without stopping out. |
| `BREAKOUT_STOP_BUFFER_PCT` | 0.001 | fraction | 0.1% fallback buffer when ATR unavailable. Tiny — prefer ATR. |
| `ENTRY_STOP_PRICE_BUFFER` | 0.01 | dollars | $0.01 above breakout level for entry stop placement. Ensures fill above the breakout level. |
| `STOP_LIMIT_BUFFER_PCT` | 0.001 | fraction | 0.1% above stop for limit price. Small buffer to improve fill probability. |
| `TRAILING_STOP_PROFIT_TRIGGER_R` | 1.0 | × risk | Trailing activates at breakeven + full initial risk. Protects gains before trailing. |
| `TRAILING_STOP_ATR_MULTIPLIER` | 0.0 | × ATR | Disabled by default. Set to 1.0–2.0 for ATR-based trailing. |
| `PROFIT_TRAIL_PCT` | 0.95 | fraction | Stop trails 5% below session high. Tight enough to capture most of the move. |
| `ENABLE_PROFIT_TRAIL` | false | bool | Disabled by default. Enable to activate both profit trail and trailing stop. |

### Filter Parameters

| Parameter | Default | Unit | Rationale |
|---|---|---|---|
| `ENABLE_REGIME_FILTER` | false | bool | Disabled by default. Enable to block entries in downtrending broad market. |
| `REGIME_SMA_PERIOD` | 20 | days | Same as daily SMA period for consistency. |
| `ENABLE_NEWS_FILTER` | false | bool | Disabled by default. Enable to avoid catalyst events. |
| `NEWS_FILTER_LOOKBACK_HOURS` | 24 | hours | Rolling 24-hour window for news headlines. |
| `ENABLE_SPREAD_FILTER` | false | bool | Disabled by default. Enable to skip illiquid symbols. |
| `MAX_SPREAD_PCT` | 0.002 | fraction | 20 bps regular session threshold. |
| `EXTENDED_HOURS_MAX_SPREAD_PCT` | 0.01 | fraction | 100 bps — extended hours spreads are structurally wider. |

### Extended Hours Parameters

| Parameter | Default | Unit | Rationale |
|---|---|---|---|
| `EXTENDED_HOURS_ENABLED` | false | bool | Off by default; enable to trade pre/post market. |
| `PRE_MARKET_ENTRY_WINDOW_START` | 04:00 | ET | Earliest supported pre-market entry. |
| `PRE_MARKET_ENTRY_WINDOW_END` | 09:20 | ET | Stops pre-market entries before regular open. |
| `AFTER_HOURS_ENTRY_WINDOW_START` | 16:05 | ET | 5-min buffer after regular close. |
| `AFTER_HOURS_ENTRY_WINDOW_END` | 19:30 | ET | Most after-hours liquidity dries up past 19:30. |
| `EXTENDED_HOURS_FLATTEN_TIME` | 19:45 | ET | Exits 15 min before extended trading ends at 20:00. |
| `EXTENDED_HOURS_LIMIT_OFFSET_PCT` | 0.001 | fraction | 0.1% below close for limit exit orders in thin markets. |

### Intraday Review Parameters

| Parameter | Default | Unit | Rationale |
|---|---|---|---|
| `INTRADAY_DIGEST_INTERVAL_CYCLES` | 0 | cycles | 0 = disabled. Set to 60 for hourly summary. |
| `INTRADAY_CONSECUTIVE_LOSS_GATE` | 0 | count | 0 = disabled. Set to 3 to pause after 3 consecutive losses. |
| `DAILY_LOSS_LIMIT_PCT` | 0.01 | fraction | 1% daily portfolio loss → flatten all and halt entries. |

### Strategy Weighting Parameters

| Parameter | Default | Unit | Rationale |
|---|---|---|---|
| `MIN_TRADES` | 5 | count | Minimum trades before a non-zero Sharpe is computed. Avoids noise from small samples. |
| `MIN_WEIGHT` | 0.01 | fraction | 1% floor per strategy. Keeps all strategies active. |
| `MAX_WEIGHT` | 0.40 | fraction | 40% cap per strategy. Prevents single-strategy over-reliance. |
