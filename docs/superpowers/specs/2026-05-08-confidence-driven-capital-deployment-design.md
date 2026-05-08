# Confidence-Driven Capital Deployment — Design Spec

## Problem

The bot consistently deploys only ~2% of account equity despite a 30% portfolio exposure ceiling. The root cause is multiplicative shrinkage: position sizing uses `effective_equity = account.equity × strategy_weight`, and with 11+ active strategies each weight is ~9%, so `MAX_POSITION_PCT × weight ≈ 0.135%` per position. At 20 open positions that is ~2.7% total — far below the intended 30–50% target.

The weight system was designed to apportion capital across strategies, but it inadvertently acts as a divisor that makes every position tiny.

## Goal

Deploy 30–50% of account equity in normal market conditions. Limit entries to high-confidence signals (measured by strategy Sharpe rank). Allow the confidence gate to tighten automatically under drawdown, volatility, or losing streaks, but require operator approval to loosen it.

## Architecture

### Position Sizing

Replace `effective_equity = account.equity × strategy_weight` with:

```
position_notional = account.equity × MAX_POSITION_PCT × confidence_score
```

where:
- `MAX_POSITION_PCT` stays at 1.5% (unchanged), applied to **full account equity**
- `confidence_score = percentile_rank(strategy_sharpe, all_active_sharpes)`, normalized to `[0.0, 1.0]`
- Strategies with `confidence_score < confidence_floor` emit no entry intent
- `confidence_score` is clamped to `[confidence_floor, 1.0]` for strategies above the gate

`MAX_OPEN_POSITIONS = 20` and `MAX_PORTFOLIO_EXPOSURE_PCT = 0.30` remain hard ceilings. The new sizing raises the realistic deployment from ~2.7% toward the ceiling naturally as signals earn confidence.

### Sharpe Percentile Ranking

At cycle time, `evaluate_cycle()` receives a `strategy_sharpes: dict[str, float]` mapping of strategy name → rolling Sharpe ratio (computed nightly by the existing `StrategyWeightStore` pipeline). The engine normalizes these into percentile ranks:

```python
import bisect

def _compute_confidence_scores(
    sharpes: dict[str, float],
    floor: float,
) -> dict[str, float]:
    if not sharpes:
        return {}
    ranked = sorted(sharpes.values())
    scores = {}
    for name, sharpe in sharpes.items():
        idx = bisect.bisect_left(ranked, sharpe)
        scores[name] = idx / max(len(ranked) - 1, 1)
    return {k: v for k, v in scores.items() if v >= floor}
```

Strategies not in `sharpes` (no history yet) default to `confidence_score = floor` so they still participate at minimum weight.

### Confidence Floor

A single `confidence_floor` float stored in a new `confidence_floor_store` Postgres table (similar to `strategy_flag_store`). Defaults to `CONFIDENCE_FLOOR` env var (default: `0.25`).

**The floor can only move in one direction without approval:**

| Direction | Who | Mechanism |
|-----------|-----|-----------|
| Raise | System (automatic) | Any trigger fires → floor raised by `FLOOR_RAISE_STEP`, capped at `0.80` |
| Lower | Operator (manual) | `alpaca-bot-admin set-confidence-floor --value X --reason "..."` |

When a trigger clears (drawdown recovered, losing streak reset, vol subsides), the floor returns to the last manually-set value — not below it. This prevents auto-raise from permanently ratcheting up.

### Auto-Raise Triggers

Three independent triggers, evaluated each cycle:

**1. Drawdown trigger**
- Track `equity_high_watermark` (30-day rolling max) in `confidence_floor_store`
- If `(high_watermark - current_equity) / high_watermark > DRAWDOWN_RAISE_PCT` → floor raised
- Clears when equity recovers to within `DRAWDOWN_RAISE_PCT / 2` of high watermark

**2. Losing streak trigger**
- Per-strategy: if a strategy logs ≥ `LOSING_STREAK_N` consecutive closed-loss trades → that strategy's `confidence_score` is clamped to `0.0` (effectively excluded) until it logs a winning trade
- This is a per-strategy override stored in `strategy_flag_store`, not a global floor change

**3. Volatility trigger**
- Rolling 5-day realized volatility computed from daily bar closes already fetched each cycle
- If `realized_vol > VOL_RAISE_THRESHOLD` → global floor raised
- Clears when vol drops below `VOL_RAISE_THRESHOLD * 0.8` (hysteresis to prevent flapping)

All trigger activations and clearings emit `AuditEvent` rows with type `confidence_floor_auto_raised`, `confidence_floor_auto_cleared`, `strategy_confidence_excluded`, `strategy_confidence_restored`.

### Weight System (demoted role)

`StrategyWeightStore` remains unchanged. Weights continue to be computed nightly. Their role changes:

- **Old role:** Divide equity pool for position sizing
- **New role:** Priority ordering when `MAX_OPEN_POSITIONS` is reached. If 20 slots are full, the strategy with the highest weight gets the next available slot when one opens.

Weights are no longer passed into `calculate_position_size()`. The `effective_equity` parameter is removed; full `account.equity` is used directly.

### New Settings

```python
# In Settings (all validated in Settings.from_env())
confidence_floor: float = 0.25          # CONFIDENCE_FLOOR env var
floor_raise_step: float = 0.10          # FLOOR_RAISE_STEP env var
drawdown_raise_pct: float = 0.05        # DRAWDOWN_RAISE_PCT env var (5%)
losing_streak_n: int = 3                # LOSING_STREAK_N env var
vol_raise_threshold: float = 0.025      # VOL_RAISE_THRESHOLD env var (2.5% daily)
```

All env vars are optional with the defaults above. Missing vars do not silently change behavior.

### Database Changes

New table: `confidence_floor_store`

```sql
CREATE TABLE confidence_floor_store (
    id SERIAL PRIMARY KEY,
    trading_mode TEXT NOT NULL,
    strategy_version INTEGER NOT NULL,
    floor_value REAL NOT NULL,
    equity_high_watermark REAL NOT NULL DEFAULT 0.0,
    set_by TEXT NOT NULL,          -- 'system' | 'operator'
    reason TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (trading_mode, strategy_version)   -- upserted
);
```

### Admin CLI

```bash
alpaca-bot-admin set-confidence-floor --value 0.15 --reason "vol subsided, deploying more"
alpaca-bot-admin set-confidence-floor --value 0.40 --reason "risk-off, being selective"
```

Validates: `0.0 ≤ value ≤ 1.0`. Writes to `confidence_floor_store` with `set_by='operator'`. Emits `AuditEvent` with type `confidence_floor_manual_set`. Lowering below the current value requires the `--reason` flag (enforced).

### Dashboard

Add confidence floor to the dashboard overview panel:

- Current `confidence_floor` value (e.g., "25%")
- Whether any trigger is active and which one ("↑ auto-raised: drawdown")
- Per-strategy `confidence_score` shown alongside existing ALLOC % column

### `evaluate_cycle()` Changes

Signature addition:

```python
def evaluate_cycle(
    settings: Settings,
    account: AccountState,
    positions: list[OpenPosition],
    bars: dict[str, list[Bar]],
    strategy_sharpes: dict[str, float],   # new
    confidence_floor: float,              # new
) -> CycleResult:
```

`evaluate_cycle()` remains a pure function. `strategy_sharpes` and `confidence_floor` are fetched by the supervisor before calling the engine, not inside it.

## Data Flow

```
Supervisor (each cycle)
  ├── fetch account equity
  ├── load confidence_floor from confidence_floor_store
  ├── load strategy_sharpes from strategy_weight_store
  ├── evaluate auto-raise triggers (drawdown, vol) → maybe update floor
  ├── call evaluate_cycle(... strategy_sharpes, confidence_floor)
  │     └── compute confidence_scores
  │     └── filter signals below floor
  │     └── size entries: equity × MAX_POSITION_PCT × confidence_score
  │     └── return CycleResult
  ├── execute intents
  └── emit audit events
```

## Testing

- `test_confidence_score_computation`: verify percentile rank normalization, floor filtering
- `test_sizing_uses_full_equity`: assert position notional uses account equity not weight-shrunk equity
- `test_drawdown_trigger`: mock equity time series, verify floor raises at threshold and clears on recovery
- `test_vol_trigger`: mock bar closes, verify hysteresis behavior
- `test_losing_streak_trigger`: simulate N consecutive losses, verify strategy excluded then restored
- `test_admin_set_confidence_floor`: verify CLI lowers/raises, audit event emitted, floor < 0.0 rejected
- `test_floor_does_not_drop_below_manual_on_clear`: verify trigger clear returns to manual value, not lower

## Migration

One Alembic migration: create `confidence_floor_store` table. Non-destructive. Existing `strategy_weight_store` untouched.

## Out of Scope

- Per-strategy confidence floors (single global floor + per-strategy exclusion from losing streak trigger is sufficient)
- Machine learning confidence models (Sharpe percentile is the sole input)
- Intraday Sharpe updates (nightly Sharpe computation is the existing cadence)
