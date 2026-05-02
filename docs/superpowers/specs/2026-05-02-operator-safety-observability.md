# Operator Safety Observability: Exit Failure Notification + Dashboard Session P&L

## Problem

Two gaps limit operator awareness of dangerous conditions during live trading:

**Gap 1 — Exit hard failure is silent to the operator.** When `_execute_exit` cancels the broker stop and then fails to submit the market/limit exit order, the position is live and unprotected. The code logs CRITICAL, queues a recovery stop, and writes an `exit_hard_failed` audit event — but never calls `notifier.send()`. An operator monitoring their phone or email gets no alert. They must notice the issue through the dashboard or logs. The parallel case (`_execute_update_stop` unrecognized error) already calls the notifier (line 321–325), so this is an omission not a design decision.

**Gap 2 — Dashboard shows no session P&L vs loss limit.** `DailySessionState.equity_baseline` is persisted every cycle, and `OrderStore.daily_realized_pnl()` can compute today's closed-trade P&L, but neither is surfaced on the dashboard. An operator looking at the page during a bad session must query Postgres to know how close they are to halting. The info needed to make a manual halt/close-only call isn't visible at a glance.

## Design

### Fix 1: Exit hard failure notification

`_execute_exit` is an internal helper function with no `notifier` parameter. The fix has two parts:

1. Add `notifier: Notifier | None = None` to `_execute_exit`'s keyword-only signature.
2. In the `except Exception` block following `submit_market_exit`/`submit_limit_exit` (and following the stop-cancel failure path), add:
   ```python
   if notifier is not None:
       try:
           notifier.send(
               subject=f"Exit HARD FAILED: {symbol}/{strategy_name} — position UNPROTECTED",
               body=(
                   f"Stop cancel succeeded but exit submission failed for "
                   f"{symbol} ({strategy_name}). Position is live and unprotected.\n"
                   f"Reason: {reason}\n"
                   f"A recovery stop has been queued. Manual verification required."
               ),
           )
       except Exception:
           logger.exception("cycle_intent_execution: notifier failed for exit_hard_failed %s", symbol)
   ```
3. Update the `_execute_exit` call in `execute_cycle_intents` to pass `notifier=notifier`.

There are two hard-failure return paths in `_execute_exit`:
- Line ~549: stop cancel raised (returns `hard_failed=1`, already calls `exit_hard_failed` audit event)
- Line ~708: exit submission raised (returns `hard_failed=1`, already calls `exit_hard_failed` audit event)

Both paths should call the notifier with the same subject/body pattern. The stop-cancel failure is arguably more dangerous (position still open, stop still pending but now in unknown state) so it should also alert.

The notifier is swallowed (try/except) to match the pattern in `_execute_update_stop`.

### Fix 2: Dashboard session P&L panel

**Model change** — add two fields to `DashboardSnapshot` in `service.py`:
```python
realized_pnl: float | None = None
loss_limit_amount: float | None = None
```

`realized_pnl` is `None` when no session has started today (no `equity_baseline`). `loss_limit_amount` is `None` when `equity_baseline` is `None`.

**Service change** — in `load_dashboard_snapshot`, after loading `session_state`:
```python
equity_baseline = session_state.equity_baseline if session_state else None
realized_pnl = (
    order_store.daily_realized_pnl(
        trading_mode=settings.trading_mode,
        strategy_version=settings.strategy_version,
        session_date=session_date,
        market_timezone=str(settings.market_timezone),
    )
    if equity_baseline is not None
    else None
)
loss_limit_amount = (
    equity_baseline * settings.daily_loss_limit_pct
    if equity_baseline is not None
    else None
)
```

Pass `realized_pnl=realized_pnl, loss_limit_amount=loss_limit_amount` to `DashboardSnapshot(...)`.

**Template change** — add a compact panel in the stats row (near the existing "X working orders" stat) showing:

```
Session P&L: +$142.50  |  Limit: $450.00 (31.7% used)
```

Color the P&L green/red. Color the limit percentage amber when >75% used, red when >90%.

If `realized_pnl is None` (no session today), show "—" for session P&L; omit the limit usage.

The panel lives under the existing stats cards at the top of the dashboard. It does not require a page reload — it refreshes with the rest of the dashboard.

## Files Changed

| File | Change |
|---|---|
| `src/alpaca_bot/runtime/cycle_intent_execution.py` | Add `notifier` param to `_execute_exit`; call `notifier.send()` on both hard-failure return paths; pass `notifier=notifier` at call site in `execute_cycle_intents` |
| `src/alpaca_bot/web/service.py` | Add `realized_pnl: float \| None` and `loss_limit_amount: float \| None` to `DashboardSnapshot`; compute both in `load_dashboard_snapshot` |
| `src/alpaca_bot/web/templates/dashboard.html` | Add session P&L / loss-limit panel to stats row |
| `tests/unit/test_cycle_intent_execution.py` | Test: exit hard failure triggers notifier; test: stop cancel failure triggers notifier |
| `tests/unit/test_web_service.py` | Test: `realized_pnl` and `loss_limit_amount` populated when equity_baseline set; test: both `None` when no session state |

## Non-Goals

- Slippage threshold notification (`notify_slippage_threshold_pct`) — separate feature
- Real-time unrealized P&L in the session panel (requires live price; daily_realized_pnl is DB-only)
- Historical P&L sparklines

## Test Scenarios

### Test 1: Exit submission failure triggers notifier
Setup: `submit_market_exit` raises; stop was successfully cancelled.
Expect: `notifier.send()` called once with subject containing "Exit HARD FAILED" and symbol name.

### Test 2: Stop cancel failure triggers notifier
Setup: Stop cancel raises; position is live.
Expect: `notifier.send()` called once with subject containing "Exit HARD FAILED".

### Test 3: No notifier (notifier=None) — no crash
Setup: `notifier=None`, `submit_market_exit` raises.
Expect: No exception; `hard_failed=1` returned; no notifier call.

### Test 4: DashboardSnapshot populated with realized_pnl and loss_limit_amount
Setup: `session_state` has `equity_baseline=50000.0`; `order_store.daily_realized_pnl` returns `142.50`; `settings.daily_loss_limit_pct=0.01`.
Expect: `snapshot.realized_pnl == 142.50`; `snapshot.loss_limit_amount == 500.0`.

### Test 5: DashboardSnapshot fields None when no session
Setup: No session state today (`session_state=None`).
Expect: `snapshot.realized_pnl is None`; `snapshot.loss_limit_amount is None`.

## Safety and Audit Trail

No new state is written. Notification is fire-and-forget (logged on failure). The existing `exit_hard_failed` audit event is unchanged — the notification supplements but does not replace it. The recovery stop queue path is unchanged.

The `daily_realized_pnl` query runs on every dashboard page load. It is a read-only correlated subquery against the `orders` table, already in use by `load_metrics_snapshot`. Added latency is sub-millisecond for typical session trade counts (<20 round trips/day).
