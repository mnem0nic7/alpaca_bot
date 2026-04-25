# Phase 5 — Auto-Evolve: Parameter Tuning + Strategy Selection

**Date**: 2026-04-25  
**Status**: Spec

---

## Problem Statement

The bot uses fixed strategy parameters (BREAKOUT_LOOKBACK_BARS, RELATIVE_VOLUME_THRESHOLD, DAILY_SMA_PERIOD, etc.) that are set once at deployment. Different market regimes may perform better under different parameter sets. Currently there is no way to quantitatively compare parameter combinations against historical scenarios — the operator must guess.

Phase 4 delivered the replay/backtest infrastructure. Phase 5 adds an automated **parameter sweep** that runs the existing replay engine over a scenario, scores each candidate parameter set, persists the ranked results to Postgres, and produces a winning env block ready to apply to the next supervisor restart.

---

## What Is In Scope

1. **Sharpe ratio** — complete the stub from Phase 4 (`sharpe_ratio` is always `None` now); compute from per-trade returns.
2. **Parameter grid sweep** — iterate all combinations of a configurable parameter grid over a replay scenario; score each with the backtest report.
3. **Score function** — Sharpe-first composite: primary = sharpe_ratio; fallback = mean_return_pct / (max_drawdown_pct + ε).
4. **Persist results** — new `tuning_results` Postgres table + `TuningResultStore`; full migration.
5. **CLI** — `alpaca-bot-evolve --scenario FILE [--params-grid FILE] [--output-env FILE] [--min-trades N] [--no-db]`.
6. **Dashboard** — populate `MetricsSnapshot.last_backtest` (field added in Phase 4) from `TuningResultStore.load_latest_best()`.

## What Is NOT In Scope

- Hot-reload of running supervisor (impossible: frozen dataclass + advisory lock; operator must restart).
- Automatic supervisor restart (operator applies the winning env block manually).
- Multi-strategy router (Phase 6).
- ML signals (Phase 6).
- Multi-scenario aggregation (single scenario per sweep in Phase 5; multi-scenario is Phase 6 hardening).

---

## Architecture

### New module: `src/alpaca_bot/tuning/`

```
tuning/
  __init__.py     — exports TuningCandidate, ParameterGrid, DEFAULT_GRID, run_sweep, score_report
  sweep.py        — grid sweep engine (pure, no I/O, testable without DB)
  cli.py          — alpaca-bot-evolve entrypoint (reads env, loads scenario, calls sweep, saves to DB)
```

### Tunable parameter grid (default)

Only strategy-signal parameters that affect `evaluate_breakout_signal` outcome:

| Env Var | Default | Sweep Values |
|---------|---------|-------------|
| `BREAKOUT_LOOKBACK_BARS` | 20 | 15, 20, 25, 30 |
| `RELATIVE_VOLUME_THRESHOLD` | 1.5 | 1.3, 1.5, 1.8, 2.0 |
| `DAILY_SMA_PERIOD` | 20 | 10, 20, 30 |

Total: 4 × 4 × 3 = 48 combinations. Each run takes < 100ms on typical scenarios → full sweep < 5 seconds.

**Not swept**: risk sizing params (RISK_PER_TRADE_PCT, MAX_POSITION_PCT), session time params (ENTRY_WINDOW_*), credential/notification fields.

### Scoring function

```
primary:  sharpe_ratio  (if total_trades >= min_trades and sharpe is computed)
fallback: mean_return_pct / (max_drawdown_pct + 0.001)  (Calmar-like)
disqualify: total_trades < min_trades (return None — not enough sample)
```

### DB schema: `tuning_results` table

```sql
CREATE TABLE IF NOT EXISTS tuning_results (
    id BIGSERIAL PRIMARY KEY,
    run_id UUID NOT NULL,              -- groups all rows from one CLI invocation
    created_at TIMESTAMPTZ NOT NULL,
    scenario_name TEXT NOT NULL,
    trading_mode TEXT NOT NULL,        -- 'paper' or 'live'
    params JSONB NOT NULL,             -- {"BREAKOUT_LOOKBACK_BARS": "25", ...}
    score DOUBLE PRECISION,            -- NULL means disqualified (< min_trades)
    total_trades INTEGER NOT NULL DEFAULT 0,
    win_rate DOUBLE PRECISION,
    mean_return_pct DOUBLE PRECISION,
    max_drawdown_pct DOUBLE PRECISION,
    sharpe_ratio DOUBLE PRECISION,
    is_best BOOLEAN NOT NULL DEFAULT FALSE  -- TRUE on the single best row per run
);
```

### Parameter promotion

The CLI prints an env block for the winning candidate:
```
# Best params from tuning run {run_id} (score={score:.4f}, trades={n}, win={win:.0%})
BREAKOUT_LOOKBACK_BARS=25
RELATIVE_VOLUME_THRESHOLD=1.8
DAILY_SMA_PERIOD=20
```

Operator applies the block to their env file and restarts the supervisor. No automatic restart — consistent with the advisory lock constraint and "no magic" production safety principle.

---

## Safety Analysis

- **No broker contact**: tuner is pure offline — reads scenario files, runs ReplayRunner, writes to DB. Cannot submit orders.
- **No advisory lock conflict**: the tuner does not call `bootstrap_runtime()` and does not hold an advisory lock.
- **Paper vs live isolation**: `trading_mode` is passed through from the runtime env and stored in the DB row. The winning env block does not override `TRADING_MODE` — operator controls that.
- **ENABLE_LIVE_TRADING gate**: unchanged — the tuner only affects parameters, not the live trading gate.
- **Audit trail**: every sweep run is written to `tuning_results` with `run_id`, `created_at`, and full params. The winning row is flagged `is_best=TRUE`. No `AuditEvent` needed (tuning is an offline operation, not a runtime state change).
- **Migration safety**: migration 004 uses `CREATE TABLE IF NOT EXISTS` and `CREATE INDEX IF NOT EXISTS` — fully idempotent. No existing table or index is modified.
