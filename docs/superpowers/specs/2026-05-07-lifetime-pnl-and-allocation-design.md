# Lifetime P&L Display and All-Time Capital Allocation Design

**Date:** 2026-05-07
**Status:** Approved

---

## Goal

Surface the total realized P&L each strategy has generated over the lifetime of the application as a new "TOTAL P&L" column in the strategies dashboard table, and extend the supervisor's Sharpe-based capital allocation window from a 28-day rolling window to all-time so that long-term performance directly informs capital weights.

---

## Architecture

### Storage layer

Add one new method to `OrderStore` in `src/alpaca_bot/storage/repositories.py`:

```python
def lifetime_pnl_by_strategy(self) -> dict[str, float]:
```

SQL: uses the same correlated-subquery CTE pattern as `win_loss_counts_by_strategy()` — joins exit orders (`intent_type IN ('stop', 'exit')`, `status = 'filled'`, `fill_price IS NOT NULL`) with their most recent correlated entry order to compute `(exit_fill_price - entry_fill_price) * filled_quantity`. Sums that computed P&L `GROUP BY strategy_name`. Rows without a correlated entry fill are excluded (lateral join filters them). No date filter. Takes the same `trading_mode` and `strategy_version` keyword parameters as all other `OrderStore` methods. Returns `{"v1-breakout": 1234.56, ...}`. Returns an empty dict if no trades exist.

### Web service layer

Add one field to `DashboardSnapshot` in `src/alpaca_bot/web/service.py`:

```python
strategy_lifetime_pnl: dict[str, float]
```

Populated in `load_dashboard_snapshot()` by calling `order_store.lifetime_pnl_by_strategy()`. Rides the existing snapshot load path — no new endpoint, no schema change, no caching change.

### Dashboard template

Add a **TOTAL P&L** column to the strategies table in `src/alpaca_bot/web/templates/dashboard.html`. Position: after the WIN% column and before ALLOC%.

Formatting:
- Positive: green text, `+$1,234.56`
- Negative: red text, `-$234.56`
- Zero / not present: `—` (em dash, neutral color)

The column is always visible (not hidden when empty). Strategies with no closed trades show `—`.

### Supervisor weight window

In `src/alpaca_bot/runtime/supervisor.py`, change the `start_date` used to fetch trade rows for `compute_strategy_weights()`:

```python
# Before
start_date = end_date - timedelta(days=28)

# After
start_date = date(2000, 1, 1)  # all-time
```

`list_trade_pnl_by_strategy(start_date, end_date)` already accepts any date range. No query change needed. The `compute_strategy_weights()` function in `risk/weighting.py` is unchanged — it still requires ≥5 trades per strategy for a non-zero Sharpe and falls back to equal weights otherwise.

---

## Data flow

```
orders table (all time)
    │
    ├─ lifetime_pnl_by_strategy()   → DashboardSnapshot.strategy_lifetime_pnl
    │                                    → dashboard TOTAL P&L column
    │
    └─ list_trade_pnl_by_strategy(  → compute_strategy_weights()
         start_date=date(2000,1,1),      → weight_store.upsert_many()
         end_date=yesterday)                 → dashboard ALLOC% column
```

---

## Error handling

- `lifetime_pnl_by_strategy()` returns `{}` on empty result — template renders `—` for all strategies.
- `compute_strategy_weights()` already falls back to equal weights when all Sharpes are zero (e.g., fewer than 5 trades). No new fallback logic needed.
- The `start_date = date(2000, 1, 1)` change means the query may return more rows as history grows, but the Sharpe computation is O(n) over trade rows and runs once per weight-update cycle (not per supervisor tick), so performance impact is negligible.

---

## Testing

All tests use the project's fake-store DI pattern (no mocks).

- **`test_lifetime_pnl_by_strategy`** in `tests/unit/test_repositories.py`: seed orders table with known P&L rows across two strategies, assert sums are correct. Also test empty table → returns `{}`.
- **`test_load_dashboard_snapshot_includes_lifetime_pnl`** in `tests/unit/test_web_service.py`: fake `OrderStore` returning known lifetime P&L dict, assert `DashboardSnapshot.strategy_lifetime_pnl` matches.
- **`test_supervisor_uses_all_time_window_for_weights`** in `tests/unit/test_supervisor.py` (or existing weight-update test): assert `list_trade_pnl_by_strategy` is called with `start_date == date(2000, 1, 1)`.
- Dashboard template rendering is covered by the existing web integration tests; the new column follows the same Jinja pattern as WIN%.

---

## Files

| File | Change |
|---|---|
| `src/alpaca_bot/storage/repositories.py` | Add `OrderStore.lifetime_pnl_by_strategy()` |
| `src/alpaca_bot/web/service.py` | Add `strategy_lifetime_pnl` field to `DashboardSnapshot`; populate in `load_dashboard_snapshot()` |
| `src/alpaca_bot/web/templates/dashboard.html` | Add TOTAL P&L column to strategies table |
| `src/alpaca_bot/runtime/supervisor.py` | Change `start_date` from rolling 28-day to `date(2000, 1, 1)` |
| `tests/unit/test_repositories.py` | Add `lifetime_pnl_by_strategy()` tests |
| `tests/unit/test_web_service.py` | Add `DashboardSnapshot.strategy_lifetime_pnl` test |
| `tests/unit/test_supervisor.py` | Add / extend weight-window test |

---

## Out of scope

- No new database columns or migrations — `pnl` already exists on the `orders` table.
- No new API endpoints.
- No changes to `compute_strategy_weights()` logic or its min-trades threshold.
- No per-strategy P&L chart or historical breakdown (informational column only).
