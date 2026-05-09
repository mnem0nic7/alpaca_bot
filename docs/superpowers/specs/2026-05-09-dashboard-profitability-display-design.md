# Dashboard Profitability Display — Design Spec

**Date:** 2026-05-09
**Feature:** Surface expectancy, profit-target, and strategy-config data added by the profitability improvements feature in the web dashboard

---

## Background

The profitability improvements feature (committed on 2026-05-09) added three new data points to `BacktestReport`:
- `expectancy_pct: float | None` — expected return per trade given win rate and avg win/loss
- `profit_target_wins: int` — trades exited at profit target with pnl > 0
- `profit_target_losses: int` — trades exited at profit target with pnl ≤ 0

It also added three new `Settings` fields:
- `enable_profit_target: bool` — whether the profit-target exit is active
- `profit_target_r: float` — the reward multiple (e.g. 2.0 = 2×R)
- `trend_filter_exit_lookback_days: int` — debounce window for trend-filter exits

All of this data is computed in the backend but invisible in the dashboard. This spec closes that gap.

---

## Goals

1. Show `expectancy_pct` in the Session Evaluation panel.
2. Show `profit_target_wins` and `profit_target_losses` in the Session Evaluation panel (alongside existing stop/EOD breakdown).
3. Show the three new strategy settings in a dedicated "Strategy Configuration" panel.
4. No new routes, no schema migrations, no new backend logic.

---

## Non-Goals

- A dedicated profitability page or tab.
- Historical per-setting tracking.
- Surfacing these fields in the JSON/CSV export from the CLI.

---

## Architecture

### Data flow

```
Settings.from_env()
    └─► app.py dashboard route ──► dashboard.html (settings panel)

DB rows → report_from_records() → MetricsSnapshot.session_report (BacktestReport)
    └─► app.py metrics route ──► dashboard.html (session eval panel)
```

The metrics route already passes `settings` to the template context. The dashboard route does not — it must be updated.

### Files to change

| File | Change |
|---|---|
| `src/alpaca_bot/web/app.py` | Add `"settings": app_settings` to the dashboard route context dict (one line) |
| `src/alpaca_bot/web/templates/dashboard.html` | Add two rows to Session Evaluation table; add new Strategy Configuration panel |
| `tests/unit/test_web_app.py` | Assertions that the new rows/panel render when data is present |

---

## Dashboard Template Changes

### Session Evaluation panel (existing, lines ~836-851)

Append two rows to the exit breakdown table:

```
Profit target W/L  {profit_target_wins} / {profit_target_losses}
Expectancy         {expectancy_pct formatted as ±X.XX%} (or "—" when None)
```

Guard condition: `{% if metrics.session_report %}` already wraps the whole panel — no extra guard needed for these rows.

### Strategy Configuration panel (new)

Placed after the Session Evaluation panel. Guarded by `{% if settings is defined %}` so the `/` route works safely even before the one-line `app.py` fix is applied (defence in depth).

Displays:
| Field | Value |
|---|---|
| Profit target | Enabled / Disabled |
| Profit target R | `{profit_target_r}×R` (only shown when enabled) |
| Trend filter exit debounce | `{trend_filter_exit_lookback_days} day(s)` |

The profit_target_r row is conditionally shown only when `enable_profit_target` is `True`, because the value is irrelevant when the feature is off.

---

## app.py Change

**Location:** `app.py` dashboard route context dict (the `/` handler, currently line ~167).

Before (excerpt):
```python
context = {
    "snapshot": snapshot,
    "health": health,
    ...
}
```

After:
```python
context = {
    "snapshot": snapshot,
    "health": health,
    ...
    "settings": app_settings,
}
```

`app_settings` is already in scope at module level (loaded once at startup). This is the identical pattern used by the metrics route.

---

## Template Rendering Rules

- `expectancy_pct`: render as `+X.XX%` / `-X.XX%`; render `—` when `None`.
- `profit_target_wins / profit_target_losses`: render as two integers separated by `/`.
- `enable_profit_target`: render as `Enabled` (green badge) or `Disabled` (grey badge), consistent with existing boolean styling in the dashboard.
- `profit_target_r`: render as `{value}×R` (e.g. `2.0×R`).
- `trend_filter_exit_lookback_days`: render as `{value} day(s)`.

---

## Testing

New assertions in `tests/unit/test_web_app.py`:

1. Dashboard route (`/`) renders `enable_profit_target`, `profit_target_r` (when enabled), and `trend_filter_exit_lookback_days` in the page HTML.
2. Metrics route (`/metrics`) renders `expectancy_pct` and `profit_target_wins / profit_target_losses` when `session_report` is present.
3. When `session_report.expectancy_pct` is `None`, the template renders `—` rather than a Python `None` string.

Existing test helpers (`make_settings()`, `FakeConnection`) are sufficient — no new test infrastructure needed.

---

## Known Limitation (documented, not fixed here)

`_row_to_replay_record()` in `service.py` maps `intent_type == "stop"` → `"stop"` and everything else → `"eod"`. Profit-target exits in live-traded data cannot yet be distinguished from EOD exits because the `orders` table has no `reason` column. `profit_target_wins` and `profit_target_losses` will therefore always be `0` in the live session view until a schema migration adds that column. This is the same documented limitation present in `session_eval_cli.py`. The template should render `0 / 0` for these counters rather than hide the row — the row being present signals to the operator that the feature is wired up.

---

## Acceptance Criteria

- [ ] `/` route renders the Strategy Configuration panel with correct values.
- [ ] `/metrics` route renders `expectancy_pct` in the Session Evaluation panel.
- [ ] `/metrics` route renders `profit_target_wins / profit_target_losses` row.
- [ ] `None` expectancy renders as `—`, not the string `"None"`.
- [ ] All existing tests still pass.
- [ ] Three new test assertions added.
