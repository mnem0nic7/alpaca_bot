# Short Option ×100 Multiplier Fix

**Goal:** Apply the ×100 contract multiplier to `"short_option"` positions in all 5 locations that currently only apply it to `"option"`.

**Problem:** `"short_option"` was added as a strategy name in `startup_recovery.py:145` after the original multiplier logic was written. The 5 downstream checks all use `== "option"` and miss `"short_option"` entirely. Short options positions show P&L, notional value, and stop risk 100× too small in the dashboard, in closed-trade history, and in the lifetime P&L rollup.

**Architecture:** Change every `strategy_name == "option"` guard to `strategy_name in ("option", "short_option")`. The two Python helpers (`_option_multiplier` in `web/service.py` and `_contract_multiplier` in `storage/repositories.py`) already centralize the check for their callers — updating them fixes those callers automatically. The two remaining inline checks (`_to_trade_record` and `dashboard.html`) are updated directly. The SQL CASE in `lifetime_pnl_by_strategy` gets a matching `IN ('option', 'short_option')`.

**Tech Stack:** Python, Jinja2, SQL (psycopg2-style).

---

## Locations

| # | File | Location | Change |
|---|---|---|---|
| 1 | `src/alpaca_bot/web/service.py:189` | `_option_multiplier()` | `== "option"` → `in ("option", "short_option")` |
| 2 | `src/alpaca_bot/web/service.py:512` | `_to_trade_record()` | `== "option"` → `in ("option", "short_option")` |
| 3 | `src/alpaca_bot/storage/repositories.py:247` | `_contract_multiplier()` | `== "option"` → `in ("option", "short_option")` |
| 4 | `src/alpaca_bot/storage/repositories.py:871` | `lifetime_pnl_by_strategy` SQL | `= 'option'` → `IN ('option', 'short_option')` |
| 5 | `src/alpaca_bot/web/templates/dashboard.html:451` | Jinja2 template | `== "option"` → `in ("option", "short_option")` |

## Why Not `"option" in name`?

An explicit tuple is clearer. A substring check would silently apply ×100 to any future strategy name containing the word "option" (e.g., `"no_option"` or `"spread_option_pair"`). The two-element tuple documents every valid strategy name explicitly.

## Impact

- **Dashboard:** `INIT VAL`, `CURR VAL`, `UNREAL P&L $`, `RISK $` columns for short_option rows corrected.
- **Closed trades:** P&L in trade history now correctly reflects contract multiplier.
- **Loss limits:** `daily_realized_pnl` and `daily_realized_pnl_by_symbol` already use `_contract_multiplier` — updating the helper fixes the safety gates automatically.
- **Lifetime P&L:** `lifetime_pnl_by_strategy` SQL CTE fixed.
- **No schema change, no migration, no new env vars.**

## Testing

Add `short_option` counterpart tests to `tests/unit/test_web_service.py`:
- `test_to_trade_record_short_option_pnl_multiplied_by_100`: mirrors `test_to_trade_record_option_pnl_multiplied_by_100`
- `test_compute_capital_pct_short_option_multiplies_by_100`: mirrors `test_compute_capital_pct_option_multiplies_by_100`
- `test_total_deployed_notional_short_option_multiplied`: mirrors `test_total_deployed_notional_option_multiplied`

Add to `tests/unit/test_storage_db.py`:
- `test_contract_multiplier_short_option_returns_100`: unit test for `_contract_multiplier("short_option")`
- `test_option_and_short_option_daily_realized_pnl`: row with `strategy_name="short_option"` → multiplied ×100

No repository-level test needed for the SQL CASE — `_contract_multiplier` is the authoritative Python gate and is already tested for `"option"`. The SQL is a direct mirror and will be verified by the existing integration path.
