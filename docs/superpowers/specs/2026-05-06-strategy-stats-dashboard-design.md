# Strategy Stats Dashboard Design

**Goal:** Add historical win/loss record and current capital allocation percentage to each row in the Strategies panel on the main dashboard.

---

## Background

The Strategies panel currently shows each strategy's name, enabled state, and intraday entries state. Operators have no quick way to see how each strategy is performing historically or how much portfolio capital is currently tied up in it. Both pieces of information are computable from existing tables (`orders` for win/loss, `positions` for exposure) with no schema changes needed.

The 2026-05-05 capital-allocation spec describes a Sharpe-based weighting system for the supervisor (how much equity each strategy is *allowed* to use for new positions). That is a different concept from what is built here: this spec adds a *read-only display* of how each strategy has performed historically (wins/losses) and what fraction of the portfolio it currently *holds* (live open positions).

---

## What We're Building

Two new read-only display fields added inline to every strategy row in the Strategies panel:

1. **Win/Loss** — historical closed-trade count from the `orders` table, e.g. `5W / 2L`. All-time, scoped to the current `(trading_mode, strategy_version)`. Shown as `—` when a strategy has no closed trades.

2. **Capital %** — percentage of total open-position exposure currently held by this strategy, e.g. `14.2%`. Computed from the `positions` table (already fetched for the dashboard). Uses `latest_prices` when available, falls back to `entry_price`. Shown as `0%` when a strategy has no open positions.

---

## Architecture

### Data Flow

```
GET / (dashboard)
  └─ _load_dashboard_data()
       └─ load_dashboard_snapshot()
            ├─ position_store.list_all()          [already called]
            ├─ order_store.win_loss_counts_by_strategy()  [NEW]
            ├─ _compute_capital_pct()             [NEW — pure, no I/O]
            └─ DashboardSnapshot(
                 ...existing fields...,
                 strategy_win_loss=...,           [NEW]
                 strategy_capital_pct=...,        [NEW]
               )
  └─ dashboard.html renders strategy row with W/L and capital %
```

No new routes. No new stores. No new migrations. No write paths.

### New `OrderStore` Method

```python
def win_loss_counts_by_strategy(
    self,
    *,
    trading_mode: TradingMode,
    strategy_version: str,
    market_timezone: str = "America/New_York",
) -> dict[str, tuple[int, int]]:
    """Return win and loss counts per strategy from all closed trades.

    Returns {strategy_name: (wins, losses)}.
    A "win" is a closed trade with pnl > 0; a "loss" is pnl <= 0.
    Excludes trades where no correlated filled entry order is found.
    """
```

SQL pattern — CTE + `LATERAL JOIN` (same correlated-entry approach as `list_trade_pnl_by_strategy`):

```sql
WITH trade_pnl AS (
    SELECT x.strategy_name,
           (x.fill_price - e.fill_price)
               * COALESCE(x.filled_quantity, x.quantity) AS pnl
    FROM orders x
    JOIN LATERAL (
        SELECT fill_price
        FROM orders e
        WHERE e.symbol = x.symbol
          AND e.trading_mode = x.trading_mode
          AND e.strategy_version = x.strategy_version
          AND e.strategy_name IS NOT DISTINCT FROM x.strategy_name
          AND e.intent_type = 'entry'
          AND e.fill_price IS NOT NULL
          AND e.status = 'filled'
          AND e.updated_at <= x.updated_at
        ORDER BY e.updated_at DESC
        LIMIT 1
    ) e ON true
    WHERE x.trading_mode = %s
      AND x.strategy_version = %s
      AND x.intent_type IN ('stop', 'exit')
      AND x.fill_price IS NOT NULL
      AND x.status = 'filled'
)
SELECT strategy_name,
       COUNT(*) FILTER (WHERE pnl > 0)  AS wins,
       COUNT(*) FILTER (WHERE pnl <= 0) AS losses
FROM trade_pnl
GROUP BY strategy_name
```

Returns `{strategy_name: (wins, losses)}`. Strategies with no closed trades are absent from the dict (template falls back to `—`).

### Capital Allocation Helper

Pure Python function in `service.py`, no I/O:

```python
def _compute_capital_pct(
    positions: list[PositionRecord],
    latest_prices: dict[str, float],
) -> dict[str, float]:
    """Return {strategy_name: pct_of_total} from open positions.

    Uses latest_prices when available, falls back to entry_price.
    All values in range [0, 100]. Returns {} when no positions.
    """
    strategy_value: dict[str, float] = {}
    for pos in positions:
        price = latest_prices.get(pos.symbol, pos.entry_price)
        val = price * pos.quantity
        strategy_value[pos.strategy_name] = strategy_value.get(pos.strategy_name, 0.0) + val
    total = sum(strategy_value.values())
    if total <= 0:
        return {}
    return {name: round(val / total * 100, 1) for name, val in strategy_value.items()}
```

### `DashboardSnapshot` New Fields

```python
@dataclass(frozen=True)
class DashboardSnapshot:
    ...
    strategy_win_loss: dict[str, tuple[int, int]] = dc_field(default_factory=dict)
    strategy_capital_pct: dict[str, float] = dc_field(default_factory=dict)
```

Both fields default to `{}` so existing code that constructs `DashboardSnapshot` in tests remains valid without updating.

### `load_dashboard_snapshot()` Changes

After `position_store.list_all(...)` is called (already in `return DashboardSnapshot(...)` block), compute both new values and pass them in:

```python
positions = position_store.list_all(
    trading_mode=settings.trading_mode,
    strategy_version=settings.strategy_version,
)
strategy_win_loss = order_store.win_loss_counts_by_strategy(
    trading_mode=settings.trading_mode,
    strategy_version=settings.strategy_version,
    market_timezone=str(settings.market_timezone),
)
strategy_capital_pct = _compute_capital_pct(positions, latest_prices or {})

return DashboardSnapshot(
    ...
    positions=positions,
    strategy_win_loss=strategy_win_loss,
    strategy_capital_pct=strategy_capital_pct,
)
```

Note: `positions` is currently passed inline to `DashboardSnapshot` via `position_store.list_all(...)` in the constructor call. We need to hoist it into a local variable so we can reuse it in `_compute_capital_pct`.

### Template Changes

In `dashboard.html`, inside the `{% for name, flag in snapshot.strategy_flags %}` loop, add two read-only spans after the existing "Entries: enabled/disabled" form:

```jinja
{%- set wl = snapshot.strategy_win_loss.get(name) %}
{%- set cap = snapshot.strategy_capital_pct.get(name, 0.0) %}
<span class="muted" style="min-width: 7rem; white-space: nowrap;">
  {% if wl %}{{ wl[0] }}W / {{ wl[1] }}L{% else %}—{% endif %}
</span>
<span class="muted" style="min-width: 4rem; text-align: right;">
  {% if cap > 0 %}{{ "%.1f" | format(cap) }}%{% else %}0%{% endif %}
</span>
```

---

## File Map

| File | Action | What changes |
|---|---|---|
| `src/alpaca_bot/storage/repositories.py` | Modify | Add `OrderStore.win_loss_counts_by_strategy()` |
| `src/alpaca_bot/web/service.py` | Modify | Add `_compute_capital_pct()` helper; add two fields to `DashboardSnapshot`; update `load_dashboard_snapshot()` |
| `src/alpaca_bot/web/templates/dashboard.html` | Modify | Add W/L and capital % spans to each strategy row |
| `tests/unit/test_web_app.py` | Modify | Add test cases for W/L and capital % rendering |
| `tests/unit/test_web_service.py` | Modify | Add tests for `_compute_capital_pct()` and snapshot field population |

No migrations. No new env vars. No new routes.

---

## Safety Properties

- **Read-only**: no write paths, no `AuditEvent` needed (no state change)
- **Pure engine boundary preserved**: `evaluate_cycle()` untouched
- **No order submission risk**: data flows only from DB → snapshot → template
- **Paper/live parity**: `win_loss_counts_by_strategy` filters by `trading_mode`, so paper and live histories are independent
- **`ENABLE_LIVE_TRADING` gate unaffected**: no code path near order submission
- **Stale data safe**: if `latest_prices` is empty (price fetch failed), capital % falls back to entry_price — denominator is still valid
- **Zero positions safe**: `_compute_capital_pct` returns `{}` when no positions; template renders `0%` for all strategies

---

## Testing Approach

**`tests/unit/test_web_service.py`**:
- `test_compute_capital_pct_empty_positions`: empty list → `{}`
- `test_compute_capital_pct_single_strategy`: one position, latest_price available → `100.0%`
- `test_compute_capital_pct_two_strategies`: positions split between two strategies → percentages sum to 100
- `test_compute_capital_pct_uses_entry_price_fallback`: no latest_price → uses entry_price
- `test_load_dashboard_snapshot_populates_win_loss_and_capital`: integration-style test with fake stores

**`tests/unit/test_web_app.py`**:
- `test_dashboard_strategy_win_loss_rendered`: snapshot with `strategy_win_loss = {"breakout": (5, 2)}` → `"5W / 2L"` in response
- `test_dashboard_strategy_no_history_shows_dash`: snapshot with empty `strategy_win_loss` → `"—"` in response
- `test_dashboard_strategy_capital_pct_rendered`: snapshot with `strategy_capital_pct = {"breakout": 42.5}` → `"42.5%"` in response

**`tests/unit/test_storage_db.py`** (if DB integration tests exist):
- `test_win_loss_counts_by_strategy_empty`: no closed trades → `{}`
- `test_win_loss_counts_by_strategy_wins_and_losses`: mixed exits → correct counts per strategy
- `test_win_loss_counts_by_strategy_excludes_missing_entry`: exit with no correlated entry fill → excluded from count

---

## Non-Goals

- No sorting of strategies by win rate or capital %
- No Sharpe or average return display (that belongs to the metrics page)
- No all-time P&L per strategy (out of scope — this is win/loss counts only)
- No date-range filter for win/loss history (all-time is sufficient for the operator glance view)
- Win/loss counts are display-only and do not affect position sizing or strategy weighting
