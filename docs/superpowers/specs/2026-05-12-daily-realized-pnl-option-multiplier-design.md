# daily_realized_pnl Option ×100 Multiplier Fix

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Apply the ×100 contract multiplier to option trades in the three `OrderStore` PnL functions that feed the supervisor's portfolio loss limit, per-symbol loss limit, and losing-streak detection.

**Problem:** Each option contract represents 100 shares, so a fill price of $1.20 per share means the real position value is $120. Currently `daily_realized_pnl`, `daily_realized_pnl_by_symbol`, and `list_trade_pnl_by_strategy` compute `(exit - entry) * qty` without the ×100 factor. As a result:
- The portfolio loss-limit gate computes option losses as 100× too small → the gate is effectively disabled for option trades.
- The per-symbol loss-limit gate has the same defect.
- The losing-streak detector (sign is preserved by ×100, so streak logic is unaffected) and nightly sweep reporting show wrong dollar amounts.

**Architecture:**

A private `_contract_multiplier(strategy_name: str | None) -> int` helper is added at module level in `repositories.py`. It returns 100 when `strategy_name == "option"`, 1 otherwise. The three affected functions each select `strategy_name` from the `orders` table (two need a new column added to their SQL SELECT; one already has it) and apply the multiplier in Python.

`lifetime_pnl_by_strategy` is a pure-SQL CTE with a GROUP BY — fixing it requires a SQL `CASE WHEN strategy_name = 'option' THEN 100 ELSE 1 END` multiplier inside the CTE before aggregation. That function is reporting-only and not a safety gate; it is **deferred** to a separate spec. `win_loss_counts_by_strategy` only compares PnL to zero (sign), so ×100 is irrelevant.

**Safety implication:** After this fix the loss-limit check becomes 100× more sensitive to option losses — which is correct. A 2-contract position losing $0.40/share now reports -$80 instead of -$0.80. The limit threshold itself (`daily_loss_limit_pct × baseline_equity`) is already in dollar terms and does not change.

**Tech Stack:** Python, psycopg2-style fake connection pattern, pytest.

---

## Scope

### Functions fixed

| Function | Used by | Fix required |
|---|---|---|
| `daily_realized_pnl` | Supervisor portfolio loss-limit check | Add `x.strategy_name` to SELECT; apply `_contract_multiplier` in Python sum |
| `daily_realized_pnl_by_symbol` | Supervisor per-symbol loss-limit check | Same |
| `list_trade_pnl_by_strategy` | Losing-streak detection; nightly sweep reporting | `strategy_name` already in row[0]; apply `_contract_multiplier` in dict comprehension |

### Functions out of scope

| Function | Reason |
|---|---|
| `lifetime_pnl_by_strategy` | Pure-SQL CTE; reporting only; separate spec |
| `win_loss_counts_by_strategy` | Sign comparison only; ×100 does not affect result |

---

## Design Details

### `_contract_multiplier` helper

```python
def _contract_multiplier(strategy_name: str | None) -> int:
    return 100 if strategy_name == "option" else 1
```

Placed at module level in `repositories.py`, immediately before the `OrderStore` class. Matches the pattern of `_option_multiplier` in `web/service.py` but takes a bare string (not a position object) — appropriate for the storage layer.

### `daily_realized_pnl` SQL change

Add `x.strategy_name` as the last column of the SELECT. After the change the row tuple is:

```
(symbol:0, entry_fill:1, exit_fill:2, qty:3, strategy_name:4)
```

Python sum applies the multiplier to both the normal-trade and missing-entry-fill (fail-safe) paths:

```python
return sum(
    _contract_multiplier(row[4]) * (
        (float(row[2]) - float(row[1])) * float(row[3])
        if row[1] is not None
        else -(float(row[2]) * float(row[3]))
    )
    for row in rows
    if row[2] is not None
)
```

The fail-safe full-loss path also gets the ×100 multiplier: if an option order has no correlated entry fill, the full exit value is still per-share, so the real loss is ×100.

### `daily_realized_pnl_by_symbol` SQL change

Same column addition (`x.strategy_name` → row[4]). Python loop applies multiplier:

```python
multiplier = _contract_multiplier(row[4])
pnl = (
    (exit_fill - float(entry_fill)) * qty * multiplier
    if entry_fill is not None
    else -(exit_fill * qty) * multiplier
)
```

### `list_trade_pnl_by_strategy` Python change only

Row format is `(strategy_name:0, exit_date:1, qty:2, exit_fill:3, entry_fill:4)`. No SQL change needed.

```python
"pnl": (float(row[3]) - float(row[4])) * float(row[2]) * _contract_multiplier(row[0]),
```

---

## Testing

All tests use `_make_fake_connection(rows)` from `test_storage_db.py` (existing helper). No real database required.

### `TestDailyRealizedPnl` — update existing + add option tests

The 7 existing test rows are 4-tuples `(symbol, entry_fill, exit_fill, qty)`. After the SQL change, `fetchall` must return 5-tuples `(symbol, entry_fill, exit_fill, qty, strategy_name)`. All existing rows get `"breakout"` appended as row[4] so results are unchanged (multiplier=1).

New tests:
- `test_option_trade_applies_100x_multiplier`: row `("AAPL", 1.20, 0.80, 2, "option")` → pnl = (0.80 - 1.20) × 2 × 100 = −80.0
- `test_mixed_equity_and_option_sums_correctly`: equity row `("MSFT", 150.0, 155.0, 10, "breakout")` (+50) + option row `("AAPL", 1.20, 0.80, 2, "option")` (−80) → total = −30.0
- `test_option_null_entry_fill_fail_safe_also_multiplied`: row `("AAPL", None, 1.20, 2, "option")` → -(1.20 × 2 × 100) = −240.0

### `TestDailyRealizedPnlBySymbol` — new class

No test class exists for this function. Create it:
- `test_returns_empty_when_no_rows`
- `test_equity_trade_not_multiplied`: row `("MSFT", 150.0, 155.0, 10, "breakout")` → pnl = +50.0
- `test_option_trade_applies_100x_multiplier`: row `("AAPL", 1.20, 0.80, 2, "option")` → pnl = −80.0
- `test_option_null_entry_fill_fail_safe_multiplied`: row `("AAPL", None, 1.20, 2, "option")` → pnl = −240.0

Note: `daily_realized_pnl_by_symbol` rows have format `(symbol:0, entry_fill:1, exit_fill:2, qty:3, strategy_name:4)`. The `_make_fake_connection` stub uses `fetchall` so rows are passed directly as 5-tuples.

### `TestListTradePnlByStrategy` — add option test

Existing rows are `(strategy_name, exit_date, qty, exit_fill, entry_fill)`. No format change.

New test:
- `test_option_trade_pnl_applies_100x_multiplier`: row `("option", date(2026, 1, 2), 2, 0.80, 1.20)` → pnl = (0.80 − 1.20) × 2 × 100 = −80.0

---

## Files to Create / Modify

- **Modify:** `src/alpaca_bot/storage/repositories.py`
  - Add `_contract_multiplier` helper before `OrderStore` class
  - Modify `daily_realized_pnl`: SQL SELECT + Python sum
  - Modify `daily_realized_pnl_by_symbol`: SQL SELECT + Python loop
  - Modify `list_trade_pnl_by_strategy`: Python dict comprehension
- **Modify:** `tests/unit/test_storage_db.py`
  - Update all 7 existing rows in `TestDailyRealizedPnl` to 5-tuples
  - Add 3 new option tests to `TestDailyRealizedPnl`
  - Create `TestDailyRealizedPnlBySymbol` with 4 tests
  - Add 1 option test to `TestListTradePnlByStrategy`

No schema migrations, no new environment variables, no supervisor behavioral changes beyond the safety gates now correctly firing for option losses.
