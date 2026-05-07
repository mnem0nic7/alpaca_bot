# Capital Floor Reduction and Deployment Visibility Design

## Goal

Lower the minimum per-strategy capital allocation from 5% to 1%, and add dashboard
visibility into total deployed capital vs. account equity so the deployment rate can
be monitored at a glance.

## Background

The system has 11 registered strategies. Only 2 (breakout, orb) have positive
Sharpe ratios. The remaining 9 receive Sharpe = 0 and are each assigned the
`min_weight` floor — currently 5%. This means 9 × 5% = 45% of account equity
is consumed by zero-Sharpe strategies before performing strategies get any capital.
Observed weights: breakout=33%, orb=22%, 9 others=5% each.

With `min_weight = 0.01` (1%), the floor strategies collectively consume 9% of
capital instead of 45%, freeing the performing strategies to receive substantially
more effective equity.

## Architecture

Two independent changes, shipped together as one PR:

1. **`risk/weighting.py`** — Change `min_weight` default from `0.05` to `0.01`.
   Update one test that hard-codes the 5% assertion.

2. **Dashboard deployment visibility** — Add `account_equity` to the
   `supervisor_cycle` audit event; read it in the web service; display deployed
   notional and deployment rate on the dashboard.

---

## Part 1: Lower `min_weight` to 1%

### Change

`compute_strategy_weights()` in `risk/weighting.py` has `min_weight: float = 0.05`
as a keyword default. Change to `min_weight: float = 0.01`.

The docstring already says "clipped to [min_weight, max_weight]" — no docstring
change needed.

### Algorithm interaction (known, accepted)

With min_weight=1% and 11 strategies (2 Sharpe>0):

- Phase 1 (cap enforcement) oscillates over 50 iterations and settles at
  breakout=60%, orb=40%, others=0%.
- Phase 2 (floor enforcement) applies the 1% floor: others go from 0% to 1% each.
  The 9% deficit is taken from breakout/orb proportionally to their phase-1 weights
  (60%/40%), landing on breakout=54.6%, orb=36.4%.
- breakout at 54.6% nominally exceeds the 40% cap.

This cap overshoot is **acceptable by design**:
- The cap is a soft concentration limit, not a safety gate.
- The overshoot favors the highest-Sharpe strategy — the correct direction.
- Fixing the phase-1/phase-2 interaction requires interleaving both phases into a
  joint constrained-optimization pass, which is a separate project.

Resulting weights with 2 performing strategies and 9 floor strategies:

| Strategy | weight (min=5%) | weight (min=1%) |
|---|---|---|
| breakout | 33% | 54.6% |
| orb | 22% | 36.4% |
| 9 others | 5% each | 1% each |

### Test update

`test_floor_applied_when_strategy_has_low_sharpe` asserts `w >= 0.05`. Change to
`w >= 0.01` to match the new default.

### Cache invalidation

The session-open weight computation is cached in `strategy_weights` with
`computed_at = today`. The supervisor skips recomputation if today's rows exist.
To apply the new weights immediately (without waiting for tomorrow's 4 AM session
open), delete today's rows from `strategy_weights` directly:

```sql
DELETE FROM strategy_weights WHERE computed_at::date = CURRENT_DATE;
```

This is a safe, reversible operation — the supervisor recomputes on the next cycle.

---

## Part 2: Dashboard Deployment Visibility

### Supervisor: emit `account_equity` on each cycle

The `supervisor_cycle` audit event currently emits:
```json
{"entries_disabled": true, "timestamp": "..."}
```

Add `account_equity` (the raw Alpaca account equity float):
```json
{"entries_disabled": true, "timestamp": "...", "account_equity": 9234.56}
```

The `account.equity` value is already available at the point in `run_cycle_once()`
where the audit event is written (`supervisor.py` line ~1074).

### Web service: read equity + compute deployed notional

In `load_dashboard_snapshot()`:

1. Query the latest `supervisor_cycle` audit event via
   `audit_event_store.load_latest(event_types=["supervisor_cycle"])`.
   Follow the existing pattern in `_compute_worker_health()`: use
   `getattr(audit_event_store, "load_latest", None)` for backwards-compatible
   stub compatibility.
2. Extract `account_equity` from its payload (default to `None` if the key is
   missing — audit events from before this feature won't have it).
3. Compute `total_deployed_notional` by summing
   `position.quantity × position.entry_price` over all open positions.

Add two fields to `DashboardSnapshot`:

```python
account_equity: float | None = None
total_deployed_notional: float = 0.0
```

### Dashboard template: deployment meter

Add a "Capital deployed" row to the existing summary section of `dashboard.html`,
between the realized P&L row and the strategies table:

```
Capital deployed:  $2,491  (27.0% of $9,234)
```

If `account_equity` is `None` (supervisor hasn't run yet or is running an older
version), show only the notional: `$2,491`.

Format: use the existing `format_price` Jinja2 filter. Show percentage with one
decimal place.

---

## Error handling

- Missing `account_equity` in the audit payload: `dict.get("account_equity")`
  returns `None`; template guards with `{% if snapshot.account_equity %}`.
- No positions open: `total_deployed_notional = 0.0`, displays as `$0`.
- No supervisor_cycle audit event yet (fresh install):
  `load_latest` returns `None`; `account_equity` stays `None`.

## AuditEventStore.load_latest contract

`load_latest(*, event_types: list[str]) -> AuditEvent | None` — takes a list,
returns the most recent matching event or `None`. Already used in
`_compute_worker_health()` in `web/service.py` as the reference pattern.

## Testing

### `test_weighting.py`
- Update `test_floor_applied_when_strategy_has_low_sharpe`: change `>= 0.05` to
  `>= 0.01`.
- Add `test_floor_is_one_percent_by_default`: call `compute_strategy_weights`
  with many zero-Sharpe strategies and one strong strategy; assert all weights
  >= 0.01.

### `test_web_service.py`
- Add `account_equity` to the fake supervisor_cycle audit event in the test
  fixture.
- Add `test_load_dashboard_snapshot_populates_account_equity`: verify
  `snapshot.account_equity` is populated from the latest `supervisor_cycle` event.
- Add `test_load_dashboard_snapshot_account_equity_none_when_no_cycle_event`:
  verify graceful `None` when no event exists.

### `test_supervisor_weights.py` (or new file)
- Add `test_supervisor_cycle_audit_includes_account_equity`: simulate
  `run_cycle_once()`; assert the emitted `supervisor_cycle` audit event payload
  contains `account_equity`.

## Safety checklist

- No new env vars.
- No changes to order submission, position sizing, or stop placement.
- No changes to `evaluate_cycle()` (pure function boundary preserved).
- Weight computation is Postgres-advisory-lock protected (unchanged).
- Paper vs. live: identical behavior — `account_equity` is read from
  `BrokerAccount.equity`, which already works for both modes.
- Cache invalidation SQL is safe and reversible.
- `ENABLE_LIVE_TRADING=false` gate: unchanged.
