# Day Trade Evaluation Dashboard Panel — Design Spec

## Goal

Extend the `/metrics` web dashboard to show `BacktestReport`-quality statistics for today's live trades — profit factor, average hold time, stop/EOD exit breakdown, and consecutive win/loss stats. These metrics are already computed by `alpaca-bot-session-eval` (CLI) but are absent from the web UI.

## Architecture

The computation already exists in `replay/report.py:report_from_records()`. This design wires that function into the service layer so the dashboard can display the same stats the CLI shows.

No new DB queries, no new routes, no new pages. The change is a data-layer extension (`service.py`) plus template additions.

**Tech stack:** Python dataclasses (existing pattern), Jinja2 template (existing), `BacktestReport` from `replay/report.py` (existing).

---

## Components

### 1. `TradeRecord` (extended)

`src/alpaca_bot/web/service.py`

Add two fields:

```python
exit_reason: str = "eod"          # "stop" or "eod", derived from intent_type
hold_minutes: float | None = None  # (exit_time - entry_time).total_seconds() / 60
```

`_to_trade_record(row)` populates them:
- `exit_reason = "stop" if row.get("intent_type") == "stop" else "eod"`
- `hold_minutes = (exit_time - entry_time).total_seconds() / 60` when both timestamps are present, else `None`

### 2. `MetricsSnapshot` (extended)

Add one field:

```python
session_report: BacktestReport | None = None
```

This is `None` when there are no closed trades for the session (same guard as existing stats).

### 3. `load_metrics_snapshot()` (extended)

Add an optional DI parameter:

```python
daily_session_state_store: DailySessionStateStore | None = None,
```

After fetching `raw_trades`, the function:

1. Creates a `DailySessionStateStore` (if not injected) and loads `starting_equity` for the session date — falling back to `100_000.0` if no state row exists (matches `session_eval_cli` behaviour).
2. Calls a new private helper `_row_to_replay_record(row)` to convert each raw dict to a `ReplayTradeRecord`. This helper lives in `service.py` (not imported from admin, to avoid cross-layer dependency) and uses the same logic as `session_eval_cli._row_to_trade_record()`.
3. Calls `report_from_records(replay_records, starting_equity=starting_equity, strategy_name="all")`.
4. Stores the result as `session_report`.

When `raw_trades` is empty, `session_report` is `None`.

### 4. Dashboard template (`dashboard.html`)

**New "Session Evaluation" panel** — inserted after the existing stats grid on `/metrics`, before the Trade Results table:

Shows (only rendered when `metrics.session_report` is not None):
- Profit factor
- Average hold time (minutes)
- Stop exits: wins / losses
- EOD exits: wins / losses
- Max consecutive wins / max consecutive losses

**Enhanced Trade Results table** — two new columns appended after "Slippage":
- **Hold** — `trade.hold_minutes` formatted as `NNm`; `—` if None
- **Exit** — `trade.exit_reason` ("stop" or "eod")

---

## Data Flow

```
list_closed_trades()  →  raw row dicts (intent_type, entry_fill, exit_fill, …)
         │
         ├──  _to_trade_record()  →  TradeRecord (now with exit_reason, hold_minutes)
         │         └──  metrics.trades  (per-trade table)
         │
         └──  _row_to_replay_record()  →  ReplayTradeRecord
                   └──  report_from_records()  →  BacktestReport
                             └──  metrics.session_report  (evaluation panel)
```

---

## Behavior Details

### `starting_equity` for `session_report`

Loaded from `DailySessionStateStore.load()` using the session date, trading mode, strategy version, and `EQUITY_SESSION_STATE_STRATEGY_NAME`. Falls back to `100_000.0` when no state row exists (same behaviour as CLI). This only affects `max_drawdown_pct` inside `session_report`; the `max_drawdown_pct` already displayed on the page is unaffected (still computed by `_max_drawdown_pct(trades)`).

### `session_report` vs existing stats

The existing `win_rate`, `mean_return_pct`, `max_drawdown_pct`, `sharpe_ratio`, `total_pnl` on `MetricsSnapshot` are **not replaced** — they remain independently computed. `session_report` is additive: it surfaces stats not currently shown anywhere on the page.

### Empty-trades guard

When `raw_trades` is empty: `session_report = None`. The new template panel checks `{% if metrics.session_report %}` and renders nothing.

### Multi-strategy sessions

`report_from_records()` is called with `strategy_name="all"` (aggregates all strategies). The existing per-strategy breakdown table is unchanged.

---

## Files Changed

| File | Change |
|------|--------|
| `src/alpaca_bot/web/service.py` | Extend `TradeRecord`, `MetricsSnapshot`, `load_metrics_snapshot()` |
| `src/alpaca_bot/web/templates/dashboard.html` | Add Session Evaluation panel; add Hold/Exit columns to trade table |
| `tests/unit/test_web_service.py` | Extend `TestLoadMetricsSnapshot` to cover new fields |

---

## Test Coverage

Extend `TestLoadMetricsSnapshot` in `test_web_service.py`:

1. `test_session_report_populated_from_trades` — when raw_trades contains 2 trades (one stop win, one eod loss), `session_report` is not None, `profit_factor` is set, `stop_wins == 1`, `eod_losses == 1`.
2. `test_session_report_none_when_no_trades` — empty trade list → `session_report is None`.
3. `test_trade_record_exit_reason_and_hold_minutes` — `_to_trade_record()` maps `intent_type="stop"` to `exit_reason="stop"` and computes `hold_minutes` from entry/exit timestamps.
4. `test_session_report_uses_starting_equity_from_store` — injected `daily_session_state_store` returning `equity_baseline=50_000.0` is used as `starting_equity` in `report_from_records()`.

---

## Out of Scope

- No new API endpoint (JSON)
- No new page or route
- No per-strategy session reports (aggregated only)
- No persistence of the daily `BacktestReport` to the database
