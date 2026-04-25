# Multi-Strategy Trading — Design Spec

**Date:** 2026-04-25
**Status:** Approved

---

## Problem

The system currently supports a single hardcoded breakout strategy. All positions, orders, and session state are keyed by `(trading_mode, strategy_version)` — there is no `strategy_name` dimension. The supervisor resolves one evaluator per cycle and stops at the first enabled entry in `STRATEGY_REGISTRY`. Adding a second strategy would cause client order ID collisions, position overwrites, and intermingled PnL.

---

## Goal

Run multiple trading strategies simultaneously within a single supervisor cycle. Each strategy operates with full isolation: independent position tracking, independent session state (`entries_disabled`, `flatten_complete`), independent slot limits, and attributable PnL. An operator can enable or disable any strategy from the dashboard without restarting the process.

---

## Non-Goals

- Per-strategy risk parameters (all strategies share `RISK_PER_TRADE_PCT`, `MAX_POSITION_PCT`, `MAX_OPEN_POSITIONS`)
- Cross-strategy position aggregation or netting
- Multiple supervisor processes (one process runs all strategies)
- Backtest-driven strategy selection

---

## Architecture

### Approach

Fan-out per strategy within a single supervisor cycle. `evaluate_cycle()` is called once per active strategy. Each call receives a filtered view of positions, working orders, and traded symbols scoped to that strategy. Results are merged before dispatch.

### Why not a composite evaluator

A composite evaluator (single `evaluate_cycle()` call, all strategies compete per symbol) would block two strategies from entering the same symbol simultaneously and prevent per-strategy PnL attribution. The fan-out approach gives true isolation at the cost of a DB migration.

---

## Section 1 — Signal Type Generalization

`BreakoutSignal` in `domain/models.py` is renamed `EntrySignal`. Fields are preserved with one rename: `breakout_level` → `entry_level` (the price threshold the bar crossed to trigger the signal).

The `StrategySignalEvaluator` Protocol return type changes from `BreakoutSignal | None` to `EntrySignal | None`. All strategies express their signal as a stop-limit entry — this shape is valid for any strategy that uses stop-limit orders at the broker.

The `breakout.py` evaluator is updated to return `EntrySignal`. No other interface changes are needed.

**Files:** `domain/models.py`, `strategy/__init__.py`, `strategy/breakout.py`, `core/engine.py` (type annotation only), affected tests.

---

## Section 2 — Database Schema

One migration (`006_add_strategy_name`) adds `strategy_name TEXT NOT NULL DEFAULT 'breakout'` to three tables. The default ensures existing rows are attributed to the breakout strategy without a backfill.

### `orders`

- `strategy_name` column added.
- `client_order_id` format changes from `{strategy_version}:{date}:{symbol}:entry:{ts}` to `{strategy_name}:{strategy_version}:{date}:{symbol}:entry:{ts}`.
- All queries filtering by `(trading_mode, strategy_version)` gain `AND strategy_name = %s`.

### `positions`

- `strategy_name` column added.
- Primary key changes from `(symbol, trading_mode, strategy_version)` to `(symbol, trading_mode, strategy_version, strategy_name)`.
- The `ON CONFLICT` upsert clause updated to match the new PK. Two strategies may now hold positions in the same symbol simultaneously.

### `daily_session_state`

- `strategy_name` column added.
- Primary key changes from `(session_date, trading_mode, strategy_version)` to `(session_date, trading_mode, strategy_version, strategy_name)`.
- Each strategy gets independent `entries_disabled` and `flatten_complete` flags. A loss-limit breach or EOD flatten in one strategy does not affect the other.

A reversible `.down.sql` drops the column and restores the original PKs.

**Files:** `migrations/006_add_strategy_name.sql`, `migrations/006_add_strategy_name.down.sql`, `storage/models.py`, `storage/repositories.py`.

---

## Section 3 — Engine Fan-out and Supervisor Dispatch

### `evaluate_cycle()` changes

- Gains `strategy_name: str` parameter.
- Threads `strategy_name` into every `CycleIntent` produced.
- `_client_order_id()` embeds `strategy_name` in the ID format.
- Function remains pure — no I/O.

### `CycleIntent` and `OrderRecord`

- Both gain `strategy_name: str` field.
- `run_cycle()` writes `strategy_name` when saving ENTRY intents as `pending_submit` orders.

### Supervisor changes

- `_resolve_signal_evaluator()` is replaced by `_resolve_active_strategies()`, which returns `list[tuple[str, StrategySignalEvaluator]]` — all enabled strategies.
- The supervisor calls `run_cycle()` once per active strategy per cycle.
- Before each call, it filters `open_positions`, `working_order_symbols`, and `traded_symbols_today` to records belonging to that strategy only.
- `max_open_positions` applies independently per strategy.
- All intents from all strategies are collected, then passed together to `execute_cycle_intents()`.
- A single `AuditEvent("decision_cycle_completed")` is written with a per-strategy intent count breakdown in the payload.

**Files:** `core/engine.py`, `domain/models.py`, `runtime/cycle.py`, `runtime/supervisor.py`, `runtime/cycle_intent_execution.py`.

---

## Section 4 — Prior-Day-High Momentum Strategy

A second strategy is implemented alongside the infrastructure work.

### Signal logic (`strategy/momentum.py`)

For each symbol, `evaluate_momentum_signal()` checks:

1. **Time guard** — `is_entry_session_time()` (reused from `strategy/breakout.py`)
2. **Trend filter** — `daily_trend_filter_passes()` SMA check (reused, reads `settings.daily_sma_period`)
3. **Prior-day high** — `yesterday_high = daily_bars[-1].high` (most recent completed daily bar)
4. **Breakout condition** — `signal_bar.high > yesterday_high AND signal_bar.close > yesterday_high`
5. **Volume confirmation** — `relative_volume >= settings.relative_volume_threshold`
6. **Stop/limit math** — same formulas as breakout: stop at `signal_bar.low - buffer`, limit at `signal_bar.high + buffer`, `entry_level = yesterday_high`

Returns `EntrySignal` or `None`.

### Distinction from breakout

Breakout fires on the N-bar intraday high (within the current session). Momentum fires when the current session's price crosses yesterday's session high. They use different reference levels and will not produce identical signals on the same bar.

### New Settings parameter

`PRIOR_DAY_HIGH_LOOKBACK_BARS: int = 1` — how many daily bars back to use for "yesterday's high". Default 1 means the most recent completed session. Validated in `Settings.validate()`.

### Removed constraint

The `ENTRY_TIMEFRAME_MINUTES must be 15` validation in `Settings.validate()` is removed. It was breakout-specific. Both strategies work on 15-minute bars, but the constraint is unnecessary and prevents future strategies from using different timeframes.

### Registry

`STRATEGY_REGISTRY` gains `"momentum": evaluate_momentum_signal`.

**Files:** `strategy/momentum.py` (new), `strategy/__init__.py`, `config/__init__.py`.

---

## Section 5 — Dashboard Changes

### Positions panel

Each position row gains a `Strategy` column. With the new PK, the same symbol can appear twice (one row per strategy). Both rows are rendered.

### Metrics panel

`load_metrics_snapshot()` groups closed trades by `strategy_name`. `OrderStore.list_closed_trades()` is extended to return `strategy_name` in each row. `MetricsSnapshot` gains `trades_by_strategy: dict[str, list[TradeRecord]]` alongside the existing aggregate `trades` (which remains the union of all strategies). The dashboard metrics section shows a breakdown table: one row per strategy with PnL, win rate, and trade count.

### Strategies panel

No structural change. The existing toggle buttons work per `strategy_name`. The panel additionally shows the current open position count per strategy (derived from `DashboardSnapshot.positions` filtered by `strategy_name` in the template).

**Files:** `web/service.py`, `web/templates/dashboard.html`, `storage/repositories.py`.

---

## Migration Safety

- The `DEFAULT 'breakout'` on all three new columns means no existing row requires a backfill.
- The migration is additive — existing queries continue to work until they are updated.
- The `.down.sql` fully reverses the migration.
- The Postgres advisory lock remains keyed by `(trading_mode, strategy_version)` — one supervisor process per version, unchanged.

---

## Test Strategy

All new logic follows the existing fake-callables DI pattern (no mocks). Key test files:

- `test_entry_signal.py` — rename/field change on `BreakoutSignal → EntrySignal`
- `test_momentum_strategy.py` — unit tests for `evaluate_momentum_signal()` covering all guards and the prior-day-high condition
- `test_cycle_engine_multi_strategy.py` — `evaluate_cycle()` with `strategy_name`; `CycleIntent` carries it through
- `test_runtime_supervisor_multi_strategy.py` — `_resolve_active_strategies()` returns correct list; two strategies produce non-interleaved intents
- `test_storage_strategy_name.py` — `PositionStore`, `OrderStore`, `DailySessionStateStore` filter by `strategy_name`; no cross-strategy leakage
- Existing tests updated to pass `strategy_name="breakout"` where required by new signatures

Regression gate: `pytest tests/unit/ -q` must pass after each task.
