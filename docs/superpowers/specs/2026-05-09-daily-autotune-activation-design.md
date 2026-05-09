# Daily Auto-Tune Activation — Design Spec

**Date:** 2026-05-09

---

## Problem

The nightly parameter sweep infrastructure is fully built and tested:
- `alpaca-bot-nightly` fetches bars, runs multi-strategy sweep with OOS validation, writes `candidate.env`
- `apply_candidate.sh` diffs candidate params vs env file, patches it, redeploys
- `deploy/cron.d/alpaca-bot` chains the two together at 22:30 UTC weekdays

The cron was not installed until today (2026-05-09). Now that it's active, two gaps remain:

1. **Dashboard "Last Backtest" panel is broken.** The template references `mean_return_pct` and
   `max_drawdown_pct` but `load_latest_best()` does not SELECT those columns. The panel also omits
   the tuning run date, score, and winning params — the operator has no visibility into *when* tuning
   last ran or *what* it selected.

2. **No audit trail for nightly runs.** The nightly CLI does not write an `AuditEvent` row, so there
   is no record in the dashboard's admin history when params were auto-applied.

---

## Fix

### Phase B — Dashboard Visibility

**`storage/repositories.py` → `TuningResultStore.load_latest_best()`**

Add `mean_return_pct` and `max_drawdown_pct` to the SELECT. Return `sharpe_ratio` and `created_at`
too (already fetched, not exposed). Return all seven fields:
`params`, `score`, `total_trades`, `win_rate`, `mean_return_pct`, `max_drawdown_pct`,
`sharpe_ratio`, `created_at`.

**`web/templates/dashboard.html` → "Last Backtest" panel**

Enhance the panel to show:
- Run date (`created_at`)
- Score
- Total trades, win rate, mean return, max drawdown, Sharpe
- Winning params (key=value list)

No changes to `MetricsSnapshot` or `service.py` — the dict returned by `load_latest_best` already
flows through as `last_backtest`; adding more keys to the dict is enough.

### Phase C — Audit Event

**`nightly/cli.py` → `main()`**

After saving tuning results to DB, append a `nightly_sweep_completed` AuditEvent with payload:
- `strategy_count`: number of strategies swept
- `candidates_accepted`: number that passed OOS gate
- `best_score`: highest score found (or `None`)
- `best_strategy`: strategy name of winner (or `None`)
- `candidate_env_written`: bool

This gives the operator an audit log entry in the dashboard "Admin History" panel showing when nightly
last ran and what it found.

### Phase D — Tests

**`tests/unit/test_nightly_audit.py`**

Unit test: inject a fake `AuditEventStore` into a monkeypatched nightly run, assert the event is
written with the correct payload fields.

**`tests/unit/test_tuning_result_store.py`** (expand existing or create)

Test `load_latest_best` returns all expected keys including `mean_return_pct`, `max_drawdown_pct`,
`sharpe_ratio`, `created_at`.

---

## Files Changed

| File | Change |
|---|---|
| `src/alpaca_bot/storage/repositories.py` | Add `mean_return_pct`, `max_drawdown_pct` to `load_latest_best` SELECT; include `sharpe_ratio`, `created_at` in returned dict |
| `src/alpaca_bot/web/templates/dashboard.html` | Enhance "Last Backtest" panel: run date, score, params, all metrics |
| `src/alpaca_bot/nightly/cli.py` | Write `nightly_sweep_completed` AuditEvent after sweep |
| `tests/unit/test_tuning_result_store.py` | Assert all seven fields present in `load_latest_best` result |
| `tests/unit/test_nightly_audit.py` | Assert AuditEvent written with correct payload |

No migrations. No new env vars. No changes to `evaluate_cycle()`, order dispatch, or Settings.

---

## Safety Analysis

**Financial safety:** No changes to order submission, position sizing, stop placement, or
`evaluate_cycle()`. The AuditEvent is a write to the `audit_events` table — same store used by the
supervisor for every cycle. No risk.

**Dashboard:** Read-only. Template changes have zero production impact.

**Audit log:** Only additive — a new `nightly_sweep_completed` row. Existing events are unchanged.

**Rollback:** Template and repository changes are trivially reverted. The AuditEvent is append-only;
there's nothing to roll back.

**Pure engine boundary:** `evaluate_cycle()` is untouched.

**Paper vs. live:** `ENABLE_LIVE_TRADING=false` is unaffected. The nightly CLI writes an AuditEvent
using the connection/trading_mode from Settings — same in both modes.
