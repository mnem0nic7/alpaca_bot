# Alpaca Bot — System & Strategy Overview

*Document version: 2026-05-12*

---

## Table of Contents

1. [Executive Summary](#executive-summary)
2. [Architecture Overview](#architecture-overview)
3. [Trading Cycle](#trading-cycle)
4. [Strategy Library](#strategy-library)
5. [Risk Management Framework](#risk-management-framework)
6. [Stop Management](#stop-management)
7. [Exit Logic](#exit-logic)
8. [Multi-Strategy Portfolio Construction](#multi-strategy-portfolio-construction)
9. [Session Time Rules](#session-time-rules)
10. [Decision Audit Trail](#decision-audit-trail)
11. [Infrastructure & Reliability](#infrastructure--reliability)
12. [Automated Nightly Optimization](#automated-nightly-optimization)
13. [Current Configuration (Paper Trading)](#current-configuration-paper-trading)
14. [Limitations and Current State](#limitations-and-current-state)

---

## Executive Summary

Alpaca Bot is an automated, fully systematic equity (and options) trading system that runs 24×7 on US markets via the Alpaca brokerage API. It executes a portfolio of 11 distinct intraday long strategies — each with a complementary bear-side variant — on a 60-second polling loop backed by 15-minute bars.

The system is designed around three principles:

1. **Purity of signal evaluation** — the core `evaluate_cycle()` function is a pure, side-effect-free function that can be replayed offline against historical data identically to how it runs in production.
2. **Layered risk control** — position sizing, per-trade stop caps, daily loss limits, regime filters, and portfolio exposure limits operate independently and compound their protection.
3. **Continuous self-optimisation** — a nightly pipeline backtests all strategies across a rolling one-year window, applies an out-of-sample gate, and updates parameters automatically.

**Current mode:** paper trading on 8 liquid US symbols (AAPL, MSFT, AMZN, NVDA, META, SPY, QQQ, IWM) with up to 20 concurrent positions and 30% gross notional exposure cap.

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│  Alpaca Brokerage API (paper or live)                       │
│  ├── REST: bars, account equity, order management           │
│  └── WebSocket: real-time trade fill stream                 │
└─────────────────────────┬───────────────────────────────────┘
                          │
┌─────────────────────────▼───────────────────────────────────┐
│  RuntimeSupervisor  (60-second polling loop)                │
│  ├── Fetch: account equity + intraday/daily bars            │
│  ├── evaluate_cycle()  ← pure function, no I/O              │
│  │     ├── Pre-entry gates (entries_disabled, regime, cap)  │
│  │     ├── Per-symbol filter chain                          │
│  │     ├── 11 strategy signal evaluators                    │
│  │     ├── Ranking & portfolio selection                    │
│  │     └── Stop management for open positions               │
│  ├── Write intents → Postgres                               │
│  ├── dispatch_pending_orders() → Alpaca REST                │
│  └── Background: trade update WebSocket stream              │
└─────────────────────────┬───────────────────────────────────┘
                          │
┌─────────────────────────▼───────────────────────────────────┐
│  Postgres  (local, advisory-locked per trading_mode)        │
│  ├── orders         — lifecycle: pending_submit → filled    │
│  ├── positions      — open positions with stop prices       │
│  ├── decision_log   — per-symbol per-cycle audit records    │
│  └── audit_events   — supervisor state transitions          │
└─────────────────────────────────────────────────────────────┘
```

**Key design decisions:**

- **Intent → dispatch separation.** The cycle emits typed intents (ENTRY, UPDATE_STOP, EXIT) written to Postgres first; a separate dispatch step sends them to the broker. Intents survive a crash and are replayed on the next cycle — no trade is silently lost.
- **Postgres advisory lock.** A per-(trading_mode, strategy_version) lock prevents two supervisor instances from running simultaneously.
- **Dependency injection.** All major components (cycle runner, order dispatcher, intent executor) accept callable overrides, enabling lightweight fakes in tests and real implementations in production.

---

## Trading Cycle

Each 60-second cycle executes the following sequence:

```
1. If flatten_all = true  →  EXIT all open positions, return
2. Manage existing positions:
      a. Evaluate exit conditions (EOD, loss limit, trend filter, VWAP breakdown)
      b. Apply stop update rules (trailing stop, profit trail, stop cap)
3. Pre-entry gates:
      a. entries_disabled?  →  skip all new entries
      b. Regime filter?     →  block all new entries if market below SMA
      c. Capacity check?    →  block if MAX_OPEN_POSITIONS reached
4. Per-symbol evaluation (for each configured symbol):
      a. Skip filters (existing position, already traded, stale bars)
      b. News filter  →  skip catalyst-event symbols
      c. Spread filter  →  skip illiquid symbols
      d. Call strategy signal evaluator  →  signal or None
      e. Size position (risk-based + two caps)
      f. Add to candidate list
5. Rank candidates by signal strength × volume; greedily fill slots
6. Write accepted entries as pending_submit orders
7. Emit DecisionRecord for every symbol evaluated (accept or reject with reason)
```

The cycle is entirely **deterministic** — given the same bars and positions, it produces the same output every time. This makes it safe to replay offline for backtesting.

---

## Strategy Library

All strategies share the same infrastructure (session time guards, entry ranking, risk sizing, stop management) and differ only in their signal conditions. Each strategy has a mirror bear-side variant for future short-selling capability.

### 1. Breakout

Enters when price breaks to a new N-bar high on elevated volume, confirming trend alignment on the daily chart.

- **Signal**: high > 20-bar rolling high, close above that level, relative volume ≥ 1.5×, daily price above 20-day SMA
- **Entry window**: 10:00 – 15:30 ET
- **Edge**: relative strength on volume indicates institutional buying pressure, not random noise

### 2. Momentum

Continuation entry on a break above the prior day's high.

- **Signal**: high > prior-day high, close above it, volume confirmation, daily trend up
- **Edge**: the prior-day high is a well-known resistance that, once broken, attracts short covering and momentum buyers

### 3. Opening Range Breakout (ORB)

Trades the first directional break above the range established in the opening 30 minutes.

- **Signal**: current bar closes above the high of the first two 15-minute bars, on volume
- **Edge**: the opening range captures early directional conviction; a clean breakout above it often sets the intraday tone

### 4. High Watermark

Enters when price closes above its 52-week high intraday.

- **Signal**: intraday close > max daily high over last 252 days, elevated volume
- **Edge**: 52-week highs are psychologically significant; breakouts often attract momentum-driven buyers and force short-sellers to cover

### 5. EMA Pullback

Buys a pullback to the 9-bar EMA in an established uptrend.

- **Signal**: prior bars above EMA, current bar touches/breaches EMA then closes back above it, volume confirms
- **Edge**: in strong trends, short-term EMAs act as dynamic support; defined risk (stop below EMA)

### 6. VWAP Reversion

Mean-reversion entry when an uptrending stock dips below VWAP by 1.5% then recovers.

- **Signal**: dip ≥ 1.5% below VWAP, current bar closes above VWAP, volume confirmation, daily trend up
- **Edge**: VWAP is the institutional cost basis; a flush-and-recover pattern shows demand absorbing selling

### 7. Gap and Go

Trades intraday continuation following a significant opening gap on heavy volume.

- **Signal**: open > prior close × 1.02 (2% gap), opening volume > 2× average, bar holds above open
- **Edge**: catalyst-driven gaps with high volume indicate fundamental interest; if the stock holds in the first hour, latecomers drive continuation

### 8. Bull Flag

Enters on the breakout from a consolidation (flag) following a strong initial move (pole).

- **Signal**: pole ≥ 2% run-up, consolidation volume declining and range contracting, breakout close above flag top on volume
- **Edge**: flags represent institutional accumulation during a pause; the volume/range contraction shows supply being absorbed before resumption

### 9. VWAP Cross

Enters on the first clean cross above VWAP after trading below it.

- **Signal**: prior bars below VWAP, current bar closes above VWAP for the first time, elevated volume
- **Edge**: the first reclaim of VWAP marks a shift in intraday supply/demand; sellers from below are now underwater

### 10. Bollinger Band Squeeze

Trades the expansion out of a low-volatility squeeze.

- **Signal**: bands narrower than 3% of price for ≥ 5 bars, current bar closes above upper band, volume, daily trend
- **Edge**: periods of extreme low volatility (squeeze) statistically precede high-volatility expansions; a directional break out of the squeeze captures that energy

### 11. Failed Breakdown

Contrarian entry on a "bear trap" — a break below support that immediately reverses.

- **Signal**: price briefly breaks below prior day's low or recent consolidation low, then closes back above it by a buffer, with volume spike ≥ 2× average
- **Edge**: aggressive short sellers piling in on the breakdown are forced to cover when the stock reverses, creating a squeeze; this strategy is uncorrelated with trend-following strategies and provides diversification

### Options Overlay: Breakout Calls

Applies the same breakout signal but enters call options instead of shares when `ENABLE_OPTIONS_TRADING = true`.

- **Contract selection**: 21–60 DTE, delta closest to 0.50 (near-the-money)
- **Sizing**: premium paid = defined risk; risk budget = `RISK_PER_TRADE_PCT × equity`
- **Edge**: leveraged exposure with capped downside; useful for high-conviction breakout signals where the risk/reward in calls is superior to stock

---

## Risk Management Framework

Risk is controlled at four independent levels, each acting as a separate check:

### 1. Per-Trade Position Sizing

```
risk_per_share       = entry_price - initial_stop_price
risk_budget          = equity × RISK_PER_TRADE_PCT        (default: 0.25%)
step 1: quantity     = risk_budget / risk_per_share
step 2: dollar cap   = min(quantity, MAX_LOSS_PER_TRADE_DOLLARS / risk_per_share)   [if set]
step 3: notional cap = min(quantity, equity × MAX_POSITION_PCT / entry_price)       (default: 1.5%)
step 4: round down to whole shares (non-fractionable symbols)
```

At default settings on a $100,000 account: max theoretical loss per trade ≈ $250 (0.25%); max position notional ≈ $1,500 (1.5%).

The two-cap design handles the edge case where a very tight stop (e.g., $0.05 away on a $50 stock) would produce an enormous quantity under risk-only sizing.

### 2. Portfolio Exposure Cap

```
available_slots = MAX_OPEN_POSITIONS - open_positions - working_orders
max_gross_notional = equity × MAX_PORTFOLIO_EXPOSURE_PCT   (default: 30%)
```

New entries are blocked once either limit binds. At maximum utilisation: 20 positions × 1.5% = 30% gross notional, leaving 70% in cash — conservative by design.

### 3. Daily Loss Limit

```
if session_pnl < -equity × DAILY_LOSS_LIMIT_PCT (default: 1%):
    flatten all positions
    disable all new entries for the remainder of the session
```

This is a hard circuit breaker. A 1% daily loss on $100,000 = $1,000 maximum daily loss before automatic shutdown.

### 4. Stop Cap (per position)

```
cap_stop = entry_price × (1 - MAX_STOP_PCT)   (default: 5%)
```

Applied both at entry and on every cycle. Any ATR-derived stop that is deeper than 5% below entry is overridden upward to the cap. Prevents outsized losses on gap-down scenarios.

### ATR-Based Stop Placement

Initial stops use the Average True Range to adapt to each symbol's volatility:

```
atr            = 14-day ATR (Wilder method)
stop_buffer    = max(MIN_BUFFER, ATR_STOP_MULTIPLIER × atr)   (multiplier = 1.0×)
initial_stop   = max(0.01, breakout_level - stop_buffer)
```

A fixed-percentage stop is too tight for high-ATR stocks and too loose for low-ATR stocks. ATR-based stops reflect the stock's actual noise level, reducing both false stops and excessive loss per trade.

---

## Stop Management

Three rules run in sequence each cycle for each open position. Rules compound — the executor takes the highest stop candidate across all three.

### Rule 1: Trailing Stop (ATR-based)

Activates only after the position has reached breakeven + 1R (full initial risk). Prevents trailing too early on positions that haven't proven themselves.

```
if latest_bar.high >= entry_price + (1.0 × risk_per_share):
    atr_trail = latest_bar.high - (1.5 × atr)    [current config: multiplier = 1.5]
    new_stop = max(current_stop, entry_price, atr_trail)
```

The stop can only rise — never falls once set.

### Rule 2: Profit Trail (session high)

Active during regular session only (disabled in extended hours due to illiquidity).

```
today_high = max(bar.high for all intraday bars today)
trail = today_high × 0.95    [5% below session high]
new_stop = max(rule_1_result, trail)
```

Using the full session high (not just the most recent bar's high) captures any morning spike even if price has pulled back since.

### Rule 3: Stop Cap

Active during regular session only.

```
if effective_stop < entry_price × 0.95:
    override to entry_price × 0.95
```

Hard limit — overrides any stop that has drifted too far from entry due to a high-ATR stock with a deep natural stop.

---

## Exit Logic

Conditions are evaluated in order; the first match triggers EXIT.

| Condition | Trigger | Reason |
|---|---|---|
| End-of-day flatten | Local time ≥ 15:45 ET | Close before market close to avoid auction risk |
| Daily loss limit | Session P&L < −1% equity | Circuit breaker — flatten all |
| Trend filter failure | Price falls below 20-day SMA (after min hold) | Position no longer in the intended trend |
| VWAP breakdown | Latest close < VWAP (after min hold, min bars) | Institutional bid exhausted; move fading |

The 15:45 close buffer (15 minutes before the 16:00 close) protects against market-on-close auction volatility and wider spreads at the end of the session.

---

## Multi-Strategy Portfolio Construction

When multiple strategies are active, capital is allocated by Sharpe ratio:

```
annualised_sharpe[s] = mean(daily_pnl[s]) / std(daily_pnl[s]) × √252

weight[s] = sharpe[s] / Σ(sharpes)
    → capped at 40% (no single strategy dominates)
    → floored at 1% (all strategies remain active)
    → normalised to sum = 1.0
```

Strategies with fewer than 5 historical trades receive Sharpe = 0 (equal weight among them). This prevents noisy Sharpe estimates from small samples from dominating allocation.

**Why Sharpe, not raw returns:** A strategy that earns $1,000 with 20% daily volatility is less valuable than one earning $800 with 5% daily volatility. Sharpe ratio rewards consistent risk-adjusted performance rather than peak returns.

---

## Session Time Rules

| Session | Hours (ET) | Entry Window |
|---|---|---|
| Regular | 09:30 – 16:00 | 10:00 – 15:30 |
| Pre-Market (if enabled) | 04:00 – 09:30 | 04:00 – 09:20 |
| After-Hours (if enabled) | 16:00 – 20:00 | 16:05 – 19:30 |
| Closed | 20:00 – 04:00 | — |

**10:00 start, not 09:30:** The first 30 minutes after open are characterised by erratic price discovery, wide spreads, and higher false-positive breakout rates. The 30-minute delay lets the opening auction settle.

**Extended hours guard rails:** Profit trail disabled (illiquid session highs are unreliable); stop cap disabled; spread threshold loosened to 100 bps (vs 20 bps regular); limit orders used for exits to account for wide spreads.

---

## Decision Audit Trail

Every `evaluate_cycle()` call produces a `DecisionRecord` for every symbol evaluated — whether accepted, rejected, or skipped. Records are stored in the `decision_log` Postgres table and are queryable in real time.

| Field | Values |
|---|---|
| `decision` | `accepted`, `rejected`, `skipped_existing_position`, `skipped_already_traded`, `skipped_no_signal` |
| `reject_stage` | `pre_filter`, `capacity`, `filter`, `sizing` |
| `reject_reason` | `regime_blocked`, `capacity_full`, `news_blocked`, `spread_blocked`, `quantity_zero`, `below_min_notional` |

This allows post-hoc analysis of exactly why any symbol was not entered on any given cycle, and makes the system's decision-making fully transparent and auditable.

---

## Infrastructure & Reliability

### Deployment

The system runs in Docker Compose with four services:

| Service | Role |
|---|---|
| `supervisor` | Long-running trading loop (60-second poll) |
| `web` | Read-only FastAPI dashboard on port 8080 |
| `migrate` | One-shot database migration at deploy time |
| `postgres` | Local state store |

TLS and routing are handled by a Caddy reverse proxy (`alpaca.ai-al.site`).

### Crash Recovery

On startup, the supervisor:
1. Acquires a Postgres advisory lock (prevents double-running)
2. Reconciles Postgres orders against broker state (syncs any fills that occurred while the process was down)
3. Cancels any stale open stops that were placed before a hard restart
4. Resumes the trading loop

Because intents are written to Postgres before being dispatched to the broker, no trade intent is lost across restarts.

### Observability

- Every significant state change writes an `AuditEvent` row (cycle run, order dispatch, reconciliation, stream start/stop, halt/resume)
- Dashboard shows live equity, open positions, recent trades, and last nightly sweep result
- Decision log allows querying why any symbol was or was not entered on any cycle
- Test suite: **1427 tests** covering core engine, all strategies, risk sizing, stop management, session logic, and order dispatch

---

## Automated Nightly Optimization

A cron job runs `alpaca-bot-nightly` at 22:30 UTC Monday–Friday:

```
1. Backfill: fetch latest 252-day bar data for all symbols → write ReplayScenario JSON files
2. Sweep: grid search over key parameters (lookback bars, volume threshold, SMA period) for each strategy × symbol
3. OOS gate: validate best parameter set against a held-out out-of-sample period
4. Candidate: if OOS gate passes → write candidate.env with updated parameters
5. Audit: record nightly_sweep_completed AuditEvent (visible on dashboard)
```

This pipeline prevents manual parameter tuning and continuously adapts to changing market regimes. The OOS gate ensures that only parameter sets that generalise beyond the in-sample period are promoted.

---

## Current Configuration (Paper Trading)

| Parameter | Value | Meaning |
|---|---|---|
| `TRADING_MODE` | paper | Paper trading via Alpaca sandbox |
| `SYMBOLS` | AAPL, MSFT, AMZN, NVDA, META, SPY, QQQ, IWM | 8 liquid large-caps and ETFs |
| `RISK_PER_TRADE_PCT` | 0.25% | Expected loss per trade as % of equity |
| `MAX_POSITION_PCT` | 1.5% | Max single position as % of equity |
| `MAX_OPEN_POSITIONS` | 20 | Maximum concurrent positions |
| `MAX_PORTFOLIO_EXPOSURE_PCT` | 30% | Gross notional cap |
| `DAILY_LOSS_LIMIT_PCT` | 1% | Circuit breaker threshold |
| `ATR_STOP_MULTIPLIER` | 1.0× | Initial stop depth in ATR units |
| `TRAILING_STOP_ATR_MULTIPLIER` | 1.5× | Trailing stop in ATR units |
| `TRAILING_STOP_PROFIT_TRIGGER_R` | 1.0R | Trailing activates at breakeven + 1R |
| `ENABLE_PROFIT_TRAIL` | true | 5% trail below session high enabled |
| `ENABLE_OPTIONS_TRADING` | true | Options overlay active |
| `EXTENDED_HOURS_ENABLED` | true | Pre-market and after-hours trading active |

---

## Limitations and Current State

**Backtesting coverage**: Paper trading began in April 2026. The validation data available as of April 30, 2026 shows statistically meaningful trade counts only for AAPL (due to the recency of the paper trading period). Conclusions about cross-symbol edge are not yet possible from internal data.

**Symbol universe**: Currently limited to 8 symbols. The architecture supports any number; the universe can be expanded once the paper trading track record is established.

**Bear-side strategies**: 12 short-side strategy variants (bear_breakout, bear_momentum, bear_orb, etc.) are implemented in the codebase but not yet activated. They require `ENABLE_SHORT_SELLING = true` and broker-level margin permission.

**Live trading**: The system has `ENABLE_LIVE_TRADING=false`. The full feature set (live orders, real P&L) is gated behind an explicit environment flag change plus `TRADING_MODE=live`. All risk controls described in this document were designed and validated in paper mode before any transition to live trading.

**Strategy Sharpe weights**: In paper mode, most strategies have insufficient trade history (< 5 trades minimum) for non-zero Sharpe estimates, so equal weighting is applied. Sharpe-based allocation will activate naturally as the trade log grows.
