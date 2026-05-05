# Strategy Capital Allocation Design

**Goal:** Automatically allocate larger fractions of portfolio equity to strategies with stronger recent live performance, and smaller fractions to strategies that are underperforming or have insufficient history.

---

## Background

The supervisor runs up to 11 strategies per cycle, all sharing a single `account.equity` value. Every strategy sizes positions as if it has access to the full portfolio. There is no mechanism to direct more capital to better-performing strategies.

`STRATEGY_VERSION` is a database namespace (not a strategy selector). The 11 strategies (`breakout`, `momentum`, `orb`, etc.) are defined in `STRATEGY_REGISTRY` and run within a single supervisor process. The supervisor's per-strategy loop at `supervisor.py:613` passes `equity=account.equity` to each strategy's cycle call — this is the injection point for per-strategy capital weights.

---

## Design

### Core Mechanism

At session open (once per trading day), the supervisor computes a capital weight per active strategy from the last 20 trading days of realized trade exits. Each strategy's cycle receives `equity = account.equity × strategy_weight` instead of the full equity. This scales both position sizing (`calculate_position_size`) and the portfolio exposure check (`max_portfolio_exposure_pct`) proportionally.

Weights always sum to 1.0. No strategy can receive more than 40% of equity (concentration cap) or less than 5% (floor — prevents complete starvation from a bad run).

### Weight Formula

1. Query `OrderStore.list_trade_pnl_by_strategy()` for the previous 20 trading days. Returns one row per closed trade: `{strategy_name, exit_date, pnl}`.
2. Group by `(strategy_name, exit_date)` → daily PnL per strategy.
3. For each active strategy, compute annualized Sharpe:
   - If fewer than 5 completed trades in the window: `sharpe = 0.0` (insufficient data)
   - If std of daily PnL = 0 and mean > 0: `sharpe = 1.0` (consistent but low-trade count)
   - If std of daily PnL = 0 and mean ≤ 0: `sharpe = 0.0`
   - Otherwise: `sharpe = mean(daily_pnl) / std(daily_pnl) × √252`, floored at 0.0
4. If all active strategies have Sharpe = 0 (no history, or all losing): use equal weights across all active strategies.
5. Otherwise: `raw_weight_i = sharpe_i / Σ sharpe_j`
6. Clip each weight to `[min_weight, max_weight]` (defaults: 5%, 40%).
7. Re-normalize so weights sum to 1.0.

**Example (3 active strategies):**

| Strategy | Sharpe | Raw weight | After clip [5%–40%] | Re-normalized |
|----------|--------|------------|----------------------|---------------|
| breakout | 1.8    | 0.60       | 0.40 (capped)        | 0.444         |
| momentum | 0.9    | 0.30       | 0.30                 | 0.333         |
| orb      | 0.3    | 0.10       | 0.10                 | 0.222         |

After clipping, re-normalize by dividing each clipped weight by their sum (0.80 → 0.444, 0.333, 0.222 — all within bounds, done). In the general case, clipping can push a previously-in-bounds weight out of bounds after re-normalization. The algorithm iterates (clip → normalize → check) until stable, which converges within 2–3 passes for ≤11 strategies.

### Weight Persistence

A new `strategy_weights` table stores the computed weights. This is separate from `strategy_flags` because weights are derived from performance data, not operator configuration.

```sql
CREATE TABLE IF NOT EXISTS strategy_weights (
    strategy_name    TEXT NOT NULL,
    trading_mode     TEXT NOT NULL,
    strategy_version TEXT NOT NULL,
    weight           FLOAT NOT NULL,
    computed_at      TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (strategy_name, trading_mode, strategy_version)
);
```

The `StrategyWeightStore` provides:
- `upsert_many(weights: dict[str, float], trading_mode, strategy_version, computed_at)` — batch upsert
- `load_all(trading_mode, strategy_version) -> dict[str, float]` — returns `{strategy_name: weight}`

### Supervisor Integration

**Session-open weight update** (in `run_cycle_once()`, alongside the equity-baseline logic):

```python
if session_date not in self._session_capital_weights:
    weights = self._update_session_weights(session_date)
    self._session_capital_weights[session_date] = weights
```

`_update_session_weights()`:
1. First attempts to load today's weights from `StrategyWeightStore.load_all()`. If weights exist with `computed_at` date equal to `session_date` (set earlier in the same session before a crash), returns them immediately without recomputing.
2. Computes lookback window: `end_date = session_date - timedelta(days=1)`, `start_date = end_date - timedelta(days=28)` (28 calendar days ≈ 20 trading days; no market-calendar dependency).
3. Calls `OrderStore.list_trade_pnl_by_strategy(trading_mode, strategy_version, start_date, end_date)`.
4. Calls `compute_strategy_weights(trade_rows, active_strategies)` (pure function, no I/O).
5. Calls `StrategyWeightStore.upsert_many(weights, computed_at=now)`.
6. Writes an `AuditEvent` with `event_type="strategy_weights_updated"` and `details` = JSON string of `{strategy_name: weight, ...}`.
7. Returns the weights dict.

**Per-strategy effective equity** (in the strategy loop):

```python
strategy_weight = self._session_capital_weights[session_date].get(strategy_name, 1.0 / len(active_strategies))
effective_equity = account.equity * strategy_weight

cycle_result = self._cycle_runner(
    ...
    equity=effective_equity,   # was account.equity
    ...
)
```

The fallback `1.0 / len(active_strategies)` (equal weight) handles the case where the weight store has no entry for a strategy (e.g., strategy was just enabled mid-session).

### New Repository Method

`OrderStore.list_trade_pnl_by_strategy()`:

```python
def list_trade_pnl_by_strategy(
    self,
    *,
    trading_mode: TradingMode,
    strategy_version: str,
    start_date: date,
    end_date: date,
    market_timezone: str = "America/New_York",
) -> list[dict]:
    """Return one dict per closed trade in the date range with strategy_name.

    Each dict: {strategy_name: str, exit_date: date, pnl: float}
    Excludes trades where entry_fill is NULL.
    """
```

SQL pattern: same correlated subquery as `list_trade_exits_in_range`, plus `x.strategy_name` in the SELECT and `DATE(x.updated_at AT TIME ZONE %s) AS exit_date`.

---

## File Map

| File | Action | What changes |
|---|---|---|
| `migrations/013_add_strategy_weights.sql` | Create | New `strategy_weights` table |
| `src/alpaca_bot/risk/weighting.py` | Create | `compute_strategy_weights()` pure function |
| `src/alpaca_bot/storage/models.py` | Modify | New `StrategyWeight` dataclass |
| `src/alpaca_bot/storage/repositories.py` | Modify | `OrderStore.list_trade_pnl_by_strategy()`; new `StrategyWeightStore` class |
| `src/alpaca_bot/runtime/supervisor.py` | Modify | `_session_capital_weights` cache; `_update_session_weights()`; `effective_equity` injection |
| `src/alpaca_bot/web/service.py` | Modify | `load_strategy_weights()` for dashboard |
| `src/alpaca_bot/web/app.py` | Modify | Pass weights to metrics template context |
| `src/alpaca_bot/web/templates/dashboard.html` | Modify | Strategy weights panel |

---

## Audit Trail

Every session-open weight recomputation writes an `AuditEvent`:
- `event_type`: `"strategy_weights_updated"`
- `details`: JSON object of `{strategy_name: weight, ...}` so the weights are queryable in the audit log

This means every change to capital allocation is permanently recorded.

---

## Dashboard Display

The existing metrics page (`/metrics`) gains a **Capital Allocation** panel showing a table:

| Strategy | Weight | Sharpe (20d) |
|---|---|---|
| breakout | 42.1% | 1.8 |
| momentum | 31.6% | 0.9 |
| orb | 26.3% | 0.3 |

Loaded from `StrategyWeightStore.load_all()`. If the table is empty (pre-first-trading-session), the panel is hidden.

---

## Safety Properties

- **No leverage**: weights sum to 1.0; total effective equity equals `account.equity` (no leveraging)
- **No starvation**: min 5% floor ensures every enabled strategy retains a capital slice even after a poor run
- **No over-concentration**: 40% cap prevents a single strategy from dominating allocation
- **Day-1 safe**: with no history, all strategies receive equal weights — identical to current behavior
- **Loss limit unaffected**: the daily loss limit check uses `baseline_equity` (total account equity), not `effective_equity`. A strategy's loss contribution still counts against the shared daily limit. No change to loss limit logic.
- **Pure engine boundary preserved**: `evaluate_cycle()` remains a pure function. The `equity` parameter change is at the call site in `supervisor.py`, not inside the engine.
- **Paper/live parity**: weight computation uses `trading_mode` as a filter; paper and live sessions have independent weights
- **Crash recovery**: weights are stored in DB with `computed_at`. If the supervisor crashes and restarts mid-session, `_session_capital_weights` is empty; the session-open guard calls `_update_session_weights()` which first checks the DB for today's already-computed weights before recomputing. No trade data is lost and no re-computation is needed if the crash happened after the session-open write.

---

## Testing Approach

**`tests/unit/test_weighting.py`** (new): pure function tests for `compute_strategy_weights`:
- Equal weights when no history
- Proportional to Sharpe when all strategies have history
- Floor applied when one strategy has very low Sharpe
- Cap applied when one strategy has dominant Sharpe
- Weights sum to 1.0 in all cases
- Strategies with < 5 trades treated as zero Sharpe

**`tests/unit/test_storage_db.py`** (extend): DB integration tests for `list_trade_pnl_by_strategy` and `StrategyWeightStore`.

**`tests/unit/test_web_service.py`** (extend): service-layer test for `load_strategy_weights()`.

**`tests/unit/test_cycle_intent_execution.py`** or new test file (extend): supervisor test that `effective_equity = account.equity * weight` is passed to `_cycle_runner` rather than `account.equity`.

---

## Non-Goals

- Weights do not affect the `daily_loss_limit_pct` computation (that uses total equity baseline)
- Weights do not change the `max_open_positions` slot count — global slot cap is unchanged
- No per-symbol capital allocation — weighting is at the strategy level only
- Weights are not exposed via env var — they are computed from live data only
- No automated strategy disabling based on performance (that is `StrategyFlag.enabled`, a separate operator concern)
