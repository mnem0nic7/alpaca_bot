# Strategy Panel Headers & Expanded Stats Design

**Goal:** Convert the Strategies panel from flex-row divs to a proper HTML table with column headers and additional per-strategy metrics — all from data already in the template context.

**Architecture:** Pure template change in `dashboard.html`. No Python, no SQL, no service layer changes. All new data is derived in Jinja2 from `snapshot` and `metrics` variables already passed to the template.

---

## Current Layout

Each strategy is rendered as a flex `<div>` row:

```
name | Enabled | [Disable] | Entries: enabled | [Disable Entries] | 10W / 10L | 12.0%
```

No headers. Min-width tricks simulate alignment. The W/L and capital % columns have no labels.

## Proposed Layout

Replace with an HTML table. Each strategy is a `<tr>`. A `<thead>` row provides column labels.

### Columns

| Column | Header | Source | Notes |
|---|---|---|---|
| 1 | Strategy | `name` from `snapshot.strategy_flags` | Monospace font |
| 2 | Status | `flag.enabled` + toggle form | "Enabled" / "Disabled" + [Disable]/[Enable] button |
| 3 | Entries | `strategy_entries_disabled` + toggle form | "on" / "off" + [Disable Entries]/[Enable Entries] button |
| 4 | W / L | `snapshot.strategy_win_loss.get(name)` | All-time wins and losses; `—` when no history |
| 5 | Win % | Derived from W/L in Jinja2 | `wins / (wins + losses) * 100`; `—` when no history |
| 6 | Capital | `snapshot.strategy_capital_pct.get(name, 0.0)` | % of open position value; "0%" when none |
| 7 | Open | `snapshot.positions \| selectattr('strategy_name', 'equalto', name) \| list \| length` | Count of live open positions |
| 8 | Today P&L | `metrics.trades_by_strategy.get(name, []) \| map(attribute='pnl') \| sum` | Today's realized PnL; `—` when 0 trades; green if positive, red (warn class) if negative |
| 9 | Today | `metrics.trades_by_strategy.get(name, []) \| length` | Count of today's closed trades; `—` when 0 |

### Rendering Rules

- **Win %**: `—` when `wl` is None (no all-time history). Otherwise `"%.0f%%" | format(wl[0] / (wl[0] + wl[1]) * 100)` if total > 0, else `—`.
- **Today P&L**: `—` when `today_count == 0`. Otherwise `format_price(today_pnl)` with class `warn` if `today_pnl < 0`.
- **Today count**: `—` when 0, otherwise the integer.
- **Open**: Always show the integer (0 is meaningful).
- **Capital**: `"%.1f%%" | format(cap)` when `cap > 0`, otherwise `"0%"`.

### Styling

- Table uses existing `.table-wrap` + `<table>` pattern from the rest of the dashboard.
- `<thead>` uses the same `<th>` style as other tables.
- Button cells use `style="white-space: nowrap"` to prevent wrapping.
- Numeric columns (Win %, Capital, Open, Today P&L, Today) right-align via `style="text-align: right"` on both `<th>` and `<td>`.
- Strategy name in `<td class="mono">`.

## Data Flow

Both sources are already in scope when the template renders the Strategies panel:

- `snapshot` — `DashboardSnapshot`, loaded by `load_dashboard_snapshot()`
- `metrics` — `MetricsSnapshot`, loaded by `load_metrics_snapshot()`

Both are passed to the Jinja2 template by `_load_dashboard_data()` in `app.py`. No changes to any Python file.

## Files Changed

- **Modify:** `src/alpaca_bot/web/templates/dashboard.html` — replace the `{% for name, flag in snapshot.strategy_flags %}` flex-div block (lines ~321–354) with a `<table>` block

## Testing

- Three existing rendering tests (`test_dashboard_strategy_win_loss_rendered`, `test_dashboard_strategy_no_history_shows_dash`, `test_dashboard_strategy_capital_pct_rendered`) must continue to pass.
- New rendering tests verify each new column:
  - `test_dashboard_strategy_win_pct_rendered` — snapshot with W/L → assert `"50%"` in response
  - `test_dashboard_strategy_open_count_rendered` — snapshot with one open position → assert `">1<"` or `"<td>1</td>"` in response
  - `test_dashboard_strategy_today_pnl_rendered` — metrics with one trade → assert formatted PnL in response
  - `test_dashboard_strategy_today_count_rendered` — metrics with two trades → assert `">2<"` in response
  - `test_dashboard_strategy_headers_rendered` — assert header strings `"Win %"`, `"Capital"`, `"Open"`, `"Today P&L"`, `"Today"` in response
- All tests in `tests/unit/test_web_app.py`. No DB, no broker calls.
