# Spec: Daily Summary Notification

**File:** `docs/superpowers/specs/2026-04-28-daily-summary.md`
**Date:** 2026-04-28
**Status:** Draft

---

## Motivation

At the end of every trading session the supervisor goes silent. There is no consolidated view of what happened during the day unless an operator opens the dashboard. A small loss, a strategy that had zero fills, or an unexpected open position at close are all invisible until the operator actively checks.

A daily summary notification sent once per session — at market close — closes that gap. The operator receives a structured message covering realized PnL, trade count, win rate, per-strategy breakdown, open position count at close, and whether the daily loss limit was breached. It requires no new infrastructure and integrates into the existing `Notifier` pipeline.

---

## Goals

1. After market close (first `_market_is_open()` returning False on a day when it previously returned True), the supervisor sends exactly one summary notification for that session date.
2. The summary covers: total realized PnL, trade count, win rate, per-strategy trade counts and PnL, residual open position count, and daily loss limit status.
3. The feature follows the existing fake-callable DI pattern. No real DB or notifier is touched in tests.
4. No new env vars, no migration, no schema changes.

---

## Non-Goals

- Real-time intraday summaries or hourly digests. One message per session, after close.
- Per-strategy separate notification messages. One consolidated message per session.
- Summary on days when the market never opened (weekends, holidays, supervisor started after close).
- Replay or backtest summary notifications.
- Slippage analysis in the summary body.

---

## Design

### Where `build_daily_summary()` lives

New module: `src/alpaca_bot/runtime/daily_summary.py`

Reasons:
- Belongs with runtime orchestration, not with the `notifications/` transport layer. `notifications/` owns *how* to send; `runtime/` owns *what* to compute from trading state.
- Keeps `supervisor.py` uncluttered. The supervisor calls a single function; all assembly logic is isolated and independently testable.
- Mirrors the pattern of `runtime/cycle.py`, `runtime/order_dispatch.py`, etc. — one focused module per runtime concern.

### Function signature

```python
def build_daily_summary(
    *,
    settings: Settings,
    order_store: object,
    position_store: object,
    session_date: date,
    daily_loss_limit_breached: bool,
) -> tuple[str, str]:
```

Returns `(subject, body)`. Pure read — no writes, no side effects. Takes stores as parameters (not a raw DB connection), consistent with `load_metrics_snapshot()` in `web/service.py`.

Calls:
- `order_store.list_closed_trades(trading_mode, strategy_version, session_date, market_timezone="America/New_York")`
- `order_store.daily_realized_pnl(trading_mode, strategy_version, session_date, market_timezone="America/New_York")`
- `position_store.list_all(trading_mode, strategy_version)`

All three methods already exist on `OrderStore` and `PositionStore` in `storage/repositories.py`.

### Summary body format

**Subject:**
```
Daily session summary — {session_date} [{trading_mode}]
```

**Body (plain text):**
```
Session: 2026-04-28  Mode: paper  Strategy: v1-breakout

--- P&L ---
Realized PnL : $142.50
Trades       : 7
Win rate     : 71.4%  (5W / 2L)

--- Strategy Breakdown ---
breakout   : 4 trades  $98.20 PnL
momentum   : 3 trades  $44.30 PnL

--- Positions at Close ---
Open positions: 0

--- Risk ---
Daily loss limit breached: No
```

Rules:
- If no closed trades: show `Trades: 0`, omit the Win rate line.
- If open positions remain: list each as `{symbol} x{qty} @ {entry_price:.2f} (stop {stop_price:.2f})`.
- Negative PnL: `-$42.00` (not `$-42.00`).
- Win rate: `{pct:.1%}  ({wins}W / {losses}L)`.

### Trigger: open→closed transition

The supervisor's `run_forever()` loop already bifurcates on `_market_is_open()`:
- `True` branch: runs a cycle.
- `False` branch: logs `supervisor_idle`.

The summary fires in the `False` branch, gated by two sets on the supervisor:
- `self._summary_sent: set[date]` — prevents sending twice for the same day.
- `self._session_had_active_cycle: set[date]` — prevents sending on days where the supervisor started after market close (no active cycles ran).

Gate condition:
```python
if (
    session_date not in self._summary_sent
    and session_date in self._session_had_active_cycle
):
    self._send_daily_summary(session_date=session_date, timestamp=timestamp)
    self._summary_sent.add(session_date)
```

`_session_had_active_cycle` is populated in the `True` (active) branch before `run_cycle_once()`:
```python
self._session_had_active_cycle.add(session_date)
```

### Supervisor changes

`RuntimeSupervisor.__init__` gains:
```python
self._summary_sent: set[date] = set()
self._session_had_active_cycle: set[date] = set()
```

New private method `_send_daily_summary(*, session_date, timestamp)`:
1. Returns immediately if `self._notifier is None`.
2. Acquires `store_lock` around the `build_daily_summary()` call (matches `_load_position_records()` and `_load_session_state()` patterns).
3. Calls `self._notifier.send(subject, body)` — failures are caught and logged, never raised.
4. Appends a `daily_summary_sent` audit event via `_append_audit()` on success.

`daily_loss_limit_breached` is derived from `self._loss_limit_alerted` — no extra store query.

### Multi-strategy handling

One consolidated summary. `list_closed_trades()` and `daily_realized_pnl()` are called without `strategy_name=` filter — they return data for all strategies. Per-strategy breakdown is computed in Python by grouping the returned `list[dict]` by `row["strategy_name"]`, matching the grouping in `load_metrics_snapshot()`.

### Audit event

`"daily_summary_sent"` is added to `ALL_AUDIT_EVENT_TYPES` in `web/service.py` so it appears in the audit log dropdown.

### `_summary_sent` is in-memory

A supervisor restart on the same session day will re-send the summary if the market is still closed. This is acceptable — one duplicate on rare mid-day-close restarts is preferable to the complexity of a DB flag (which would require a migration and new store method).

---

## Files Changed

| File | Change |
|------|--------|
| `src/alpaca_bot/runtime/daily_summary.py` | New file. `build_daily_summary()`. |
| `src/alpaca_bot/runtime/supervisor.py` | Add `_summary_sent`, `_session_had_active_cycle`; add `_send_daily_summary()`; modify `run_forever()` active branch (track `_session_had_active_cycle`) and idle branch (call `_send_daily_summary()` when gated). |
| `src/alpaca_bot/web/service.py` | Add `"daily_summary_sent"` to `ALL_AUDIT_EVENT_TYPES`. |
| `tests/unit/test_daily_summary.py` | New file. Unit tests for `build_daily_summary()` and supervisor trigger logic. |

No migration. No new env vars. No schema changes.

---

## Safety Analysis

- No broker API calls. Pure read-side notification path.
- `store_lock` held during store reads — matches existing locking convention.
- Notifier failures caught and logged, never raised. Loop continues normally.
- The `_summary_sent` gate is in-memory — deliberate (see above).
- No DB writes except the `daily_summary_sent` audit event via existing `_append_audit()` helper.
- `ENABLE_LIVE_TRADING` gate unaffected — summary fires in both paper and live modes.
- No new env vars.

---

## Test Plan

### `build_daily_summary()` unit tests (`tests/unit/test_daily_summary.py`)

Fake stores follow the project's fake-callable DI pattern — plain classes with hardcoded return values, no mocks:

```python
class FakeOrderStore:
    def __init__(self, *, trades=None, pnl=0.0):
        self._trades = trades or []
        self._pnl = pnl

    def list_closed_trades(self, *, trading_mode, strategy_version, session_date, market_timezone="America/New_York"):
        return list(self._trades)

    def daily_realized_pnl(self, *, trading_mode, strategy_version, session_date, market_timezone="America/New_York"):
        return self._pnl

class FakePositionStore:
    def __init__(self, *, positions=None):
        self._positions = positions or []

    def list_all(self, *, trading_mode, strategy_version):
        return list(self._positions)
```

Tests:

1. `test_zero_trades_no_positions` — empty stores, loss limit not breached. Subject contains session date. Body contains "0" for trades. Win rate line absent. Loss limit: "No".
2. `test_positive_pnl_multiple_trades` — 3W/1L. Body: correct PnL, `75.0%  (3W / 1L)`.
3. `test_per_strategy_breakdown` — trades for "breakout" (2) and "momentum" (1). Both appear in body.
4. `test_open_positions_at_close` — 2 open positions. Body: `Open positions: 2`, lists each.
5. `test_loss_limit_breached_true` — `daily_loss_limit_breached=True`. Body: "Yes".
6. `test_subject_contains_session_date_and_mode` — subject has ISO date and trading mode value.
7. `test_negative_pnl_formats_correctly` — total PnL negative. Body shows `-$42.00`.

### Supervisor trigger tests (`tests/unit/test_daily_summary.py`)

Use `RecordingNotifier` (same class used in `tests/unit/test_notifications.py`) injected into supervisor via `RuntimeSupervisor(notifier=recording_notifier)`.

8. `test_summary_sent_once_after_active_session_closes` — broker: open on iterations 1-2, closed on 3-4. Notifier has exactly 1 call after loop.
9. `test_summary_not_sent_if_market_never_opened` — broker: always closed. Notifier: 0 calls.
10. `test_summary_not_sent_twice_same_day` — broker alternates open/closed twice in same session date. Notifier: exactly 1 call.
11. `test_summary_sent_per_day_on_multi_day_run` — broker simulates two trading days. Notifier: exactly 2 calls, distinct dates in subjects.
12. `test_summary_not_sent_when_notifier_is_none` — supervisor with `notifier=None`. No exception raised.
13. `test_notifier_failure_does_not_abort_loop` — notifier raises on `send()`. Loop continues. Audit event not appended.
14. `test_audit_event_appended_on_success` — successful send. `audit_event_store` contains `daily_summary_sent` event with correct `session_date` payload.
