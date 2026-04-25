# MVP Completion & Auto-Evolution Design

**Date:** 2026-04-25
**Scope:** Complete the alpaca_bot trading system across 6 sequential phases. Each phase is independently shippable. Tests must pass before proceeding to the next phase.

---

## Context

The core trading loop (signal → order → fill → stop → flatten) is production-ready. This spec covers the remaining 15–20 features needed to run the system in live mode with confidence, measure its performance, and eventually self-improve.

**Hard constraint:** Phase 1 is a prerequisite for live trading. The daily loss limit is currently hardwired to `realized_loss = 0.0` in `supervisor.py:204` — a financial safety gate that does nothing. Nothing else ships to live until this is enforced.

**Phasing rule:** Sequential. Each phase has a test gate. Once `pytest` passes, the next phase begins without manual approval.

---

## Phase 1 — Financial Safety

**Goal:** Enforce `DAILY_LOSS_LIMIT_PCT` in real trading by tracking actual fill prices.

### Data model change

Add two nullable columns to `order_records`:
- `fill_price NUMERIC` — actual execution price from the broker fill event
- `filled_quantity INTEGER` — actual quantity filled (may differ from order quantity on partials)

Migration: additive, nullable — no existing rows are affected.

### Fill price persistence

`apply_trade_update()` in `runtime/trade_updates.py` already receives `filled_avg_price` from the stream. On `filled` and `partially_filled` events, write it back to `OrderRecord` via `order_store.save()`.

### Daily realized PnL

New method `OrderStore.daily_realized_pnl(trading_mode, strategy_version, session_date) → float`.

Query: for each symbol, find the entry order (`intent_type='entry'`, `fill_price IS NOT NULL`) and the exit order (`intent_type IN ('stop','exit')`, `fill_price IS NOT NULL`) for today's session. Compute `(exit_fill - entry_fill) × filled_quantity`. Sum across all symbols.

Edge cases:
- Partial fills: use `filled_quantity` not `quantity`
- Multiple trades per symbol per day: blocked by `traded_symbols_today` guard; query is defensive
- Session date: use ET-local date, consistent with `_session_date()` in supervisor

### Supervisor enforcement

Replace the stub in `supervisor.py:199–206` with:
```python
realized_loss = self.runtime.order_store.daily_realized_pnl(...)
limit = settings.daily_loss_limit_pct * account.equity
if realized_loss < -limit:
    entries_disabled = True
    # emit audit event: daily_loss_limit_breached
```

**Test gate:** All existing tests pass + new tests for fill_price persistence, daily_realized_pnl calculation, and loss limit enforcement.

---

## Phase 2 — Performance Dashboard

**Goal:** Surface trading performance in the web UI.

### New data: BacktestReport and TradeRecord

`OrderStore.list_closed_trades(trading_mode, strategy_version, session_date) → list[TradeRecord]`

`TradeRecord`: symbol, entry_time, exit_time, entry_price, exit_price, quantity, pnl, slippage (fill_price - limit_price).

### New metrics endpoint

`GET /metrics` returns JSON:
- `win_rate`: fraction of trades with positive PnL
- `mean_return_pct`: mean PnL / entry_value per trade
- `max_drawdown_pct`: maximum peak-to-trough equity drop (rolling, session-scoped)
- `sharpe_ratio`: mean daily return / std dev (requires multi-day history — show `null` until 5+ days)
- `total_pnl`: sum of all closed trade PnL for today
- `trades`: list of TradeRecord dicts

### Dashboard HTML page additions

- P&L summary card (today's total, win/loss count)
- Per-symbol P&L table
- Trade execution report: fill vs. limit price, slippage per trade
- Position age: warn if any position is held > 2 sessions
- Admin command history: query audit_events for `halt`, `resume`, `close_only` event types; display as a table with timestamp and reason

### Settings additions

No new env vars needed — all data comes from existing audit_events and order_records tables.

**Test gate:** New unit tests for all metrics calculations + dashboard route tests confirming new sections render.

---

## Phase 3 — Notifications

**Goal:** Alert the operator when something important happens, without requiring dashboard polling.

### Architecture

Pluggable `Notifier` protocol:
```python
class Notifier(Protocol):
    def send(self, subject: str, body: str) -> None: ...
```

Two implementations: `SlackNotifier` (webhook POST), `EmailNotifier` (SMTP). Both configured via env vars; either or both can be active.

### Trigger points

| Event | Trigger location |
|-------|-----------------|
| Daily loss limit breached | supervisor.py, after enforcement check |
| Trading halted (admin command) | admin/cli.py halt handler |
| Stop order hit (position closed) | trade_updates.py, on stop fill |
| Stream restart failed (5th attempt) | supervisor.py stream watchdog |
| Startup mismatch detected | startup_recovery.py |

### New env vars

```
SLACK_WEBHOOK_URL=https://hooks.slack.com/...   # optional
NOTIFY_EMAIL_FROM=bot@example.com               # optional
NOTIFY_EMAIL_TO=operator@example.com            # optional
NOTIFY_SMTP_HOST=smtp.example.com               # optional
NOTIFY_SMTP_PORT=587                            # optional, default 587
NOTIFY_SMTP_USER=...                            # optional
NOTIFY_SMTP_PASSWORD=...                        # optional
```

All optional. If neither Slack nor email is configured, notifications are logged only.

### Settings validation

`Settings.from_env()` validates that if any `NOTIFY_EMAIL_*` var is set, all required SMTP vars are also set. Slack only requires `SLACK_WEBHOOK_URL`.

**Test gate:** Unit tests for each notifier (using fake HTTP/SMTP), integration tests for each trigger point using a recording notifier.

---

## Phase 4 — Replay Metrics Export

**Goal:** Make backtesting results machine-readable and surface them in the dashboard.

### BacktestReport dataclass

```python
@dataclass
class BacktestReport:
    scenario_name: str
    generated_at: datetime
    total_trades: int
    win_rate: float
    mean_return_pct: float
    max_drawdown_pct: float
    sharpe_ratio: float | None
    total_pnl: float
    trades: list[ReplayTradeRecord]
```

`ReplayRunner.run()` returns `(list[ReplayEvent], BacktestReport)`.

### CLI export

`alpaca-bot-backtest` command:
- `--scenario FILE` path to scenario YAML/JSON
- `--output FILE` path for JSON or CSV export (format inferred from extension)
- `--format json|csv` override

### Dashboard integration

Last backtest result persisted to `backtest_results` table (one row per scenario). Dashboard `/metrics` endpoint includes `last_backtest` section.

### Scenario format

Define a simple YAML schema for replay scenarios so operators can write them without Python:
```yaml
name: "AAPL breakout 2026-Q1"
symbol: AAPL
bars_file: data/AAPL_15min_2026Q1.csv
settings_overrides:
  breakout_lookback_bars: 15
  relative_volume_threshold: 1.8
```

**Test gate:** Unit tests for BacktestReport calculation, CLI export (JSON and CSV), dashboard rendering of last_backtest.

---

## Phase 5 — Auto-Evolve: Parameter Tuning + Strategy Selection

**Goal:** System self-optimizes strategy parameters using replay; human approves before applying.

### Parameter grid search

New module `evolution/tuner.py`. Accepts a parameter grid (dict of param → list of values) and runs `ReplayRunner` for each combination. Returns the combination that maximises Sharpe ratio (or configurable metric).

### Strategy params table

New table `strategy_params` (trading_mode, strategy_version, param_name, param_value, approved, proposed_at, approved_at).

Supervisor reads live params from this table at startup (overrides env vars for strategy-specific params). Falls back to env vars if no approved row exists.

### Human-in-the-loop variant

Tuner writes proposed params with `approved=false`. New admin command `alpaca-bot-admin approve-params` lists pending proposals and approves the best set. Supervisor picks up approved params on next restart.

### Fully automated variant

Tuner runs nightly (configurable cron). If proposed params improve Sharpe by > threshold, auto-approve and hot-reload (supervisor re-reads params table at cycle start without restart).

### Strategy selection

Multiple strategy modules registered via entry points. Each implements `evaluate_cycle()` with the same signature. Selection module runs each strategy's replay over the last N days and sets `active_strategy` in a DB table. Supervisor loads the active strategy at startup.

### New env vars

```
EVOLUTION_ENABLED=true|false          # default false
EVOLUTION_CRON=0 2 * * 1-5           # nightly weekdays at 2am
EVOLUTION_AUTO_APPROVE=false          # require human approval by default
EVOLUTION_METRIC=sharpe               # optimisation target
EVOLUTION_MIN_IMPROVEMENT=0.1         # 10% improvement required to auto-approve
```

**Test gate:** Unit tests for parameter grid search, strategy selection scoring, param hot-reload in supervisor, human approval flow via admin CLI.

---

## Phase 6 — Auto-Evolve: ML Signals + Multi-Strategy Router

**Goal:** Augment or replace rule-based signals with a trained model; run multiple strategies in parallel.

### ML signal module

New module `strategy/ml_signal.py`. Feature vector per bar: OHLCV, rolling returns, volume ratio, ATR, SMA distance. Label: profitable breakout (exit price > entry price + 1 ATR within 5 bars). Model: gradient boosting (scikit-learn `GradientBoostingClassifier`).

Training data sourced from `trade_results` table (Phase 1) + historical bar data. Model serialised to `models/breakout_classifier.pkl`.

ML signal integrates as an optional filter on top of the existing rule-based signal: rule fires → ML model scores → entry only if score > threshold.

### Multi-strategy router

`StrategyRouter` accepts a list of registered strategies. Each strategy runs its own `evaluate_cycle()`. Router aggregates intents, deduplicates by symbol (no double entries), routes to the best-scoring strategy per symbol.

Each strategy tracks its own P&L independently via `strategy_version` scoping already present in all store methods.

### Retraining pipeline

`alpaca-bot-train` CLI command. Downloads recent bar data via Alpaca historical API. Trains model. Validates on holdout. Saves if validation Sharpe > existing model's Sharpe. Admin approval required before router switches to new model.

### New env vars

```
ML_ENABLED=false                   # default false; opt-in
ML_MODEL_PATH=models/breakout.pkl  # path to serialised model
ML_SCORE_THRESHOLD=0.6             # minimum score to allow entry
ML_RETRAIN_ON_STARTUP=false        # retrain on each supervisor start
```

**Test gate:** Unit tests for feature extraction, model training pipeline (with synthetic data), router intent deduplication, admin model approval flow.

---

## Cross-cutting constraints

- **Pure engine boundary preserved throughout.** `evaluate_cycle()` must not do I/O in any phase. ML model inference is I/O-free (in-memory predict call); training happens in the CLI, not the supervisor loop.
- **Audit trail for every state change.** Every phase that writes to the DB must emit an `AuditEvent`. Auto-evolved parameter changes must be auditable.
- **Paper/live parity.** Every feature must work identically in paper mode. ML model and evolution features default to disabled (`false`) and require explicit opt-in.
- **All new env vars validated in `Settings.from_env()`.** No silent fallbacks to insecure defaults.
- **No phase ships without a passing test suite.** `pytest tests/unit/ -q` must be green before the next phase begins.
