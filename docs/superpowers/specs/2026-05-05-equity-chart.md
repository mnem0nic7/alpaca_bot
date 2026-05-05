# Portfolio Equity Chart — Spec

**Date:** 2026-05-05

## Problem

The metrics page shows trade-level statistics (win rate, P&L, Sharpe) for a single session date but gives no visual sense of how portfolio equity has evolved over time. The Alpaca web app shows a portfolio equity chart at a glance; the bot's dashboard has no equivalent.

## Goal

Add a portfolio equity line/area chart to the metrics page (`/metrics`) that shows:
- Current portfolio value and % change for the selected time window
- A timestamp label (e.g. "May 05, 12:39 PM ET")
- A filled area/line chart of equity over time
- A time-range selector: **1D · 1M · 1Y · All**

## Data source — no migration required

All required data already exists:

| Range | Source | Resolution |
|-------|--------|------------|
| 1D | `equity_baseline` from `daily_session_state` (strategy_name=`_equity`) for the session + cumulative P&L per trade exit from `orders` | Per-trade (intraday) |
| 1M | One data point per session: equity_baseline + total session realized P&L | Per-session (daily) |
| 1Y | Same as 1M, last 365 calendar days | Per-session (daily) |
| All | Same as 1M, all sessions with equity_baseline | Per-session (daily) |

For 1D: points are `(exit_time, equity_baseline + cumulative_pnl_up_to_that_trade)`. First point is `(market_open_approx, equity_baseline)` — the baseline is plotted as the flat starting line. If no trades closed that day, the chart shows a single horizontal line at equity_baseline.

For 1M/1Y/All: points are `(session_date noon ET, equity_baseline + total_pnl_for_session)`. Sessions without an `_equity` baseline row are omitted.

## API endpoint

```
GET /api/equity-chart?range=1d|1m|1y|all&date=YYYY-MM-DD
```

- `range` defaults to `1d`
- `date` defaults to today (same as session_date logic in `/metrics`)
- Auth-protected: same `current_operator` check as other routes; returns 401 if unauthenticated
- Returns JSON:
  ```json
  {
    "points": [
      {"t": "2026-05-05T09:30:00-04:00", "v": 99800.00},
      ...
    ],
    "current": 99598.86,
    "pct_change": -0.78,
    "label": "May 05, 12:39 PM ET",
    "range": "1d"
  }
  ```
- `points` is empty array if no data is available
- `pct_change` is `(current - first_point_value) / first_point_value * 100`; `null` if fewer than 2 points

## Repository changes

### `OrderStore.list_trade_exits_in_range()`

New method on `OrderStore`:

```python
def list_trade_exits_in_range(
    self,
    *,
    trading_mode: TradingMode,
    strategy_version: str,
    start_date: date,
    end_date: date,
    market_timezone: str = "America/New_York",
) -> list[dict]:
```

Returns dicts with `{exit_time: datetime, pnl: float}` for all filled exit/stop orders in the date range, ordered by `exit_time`. `pnl = (exit_fill - entry_fill) * qty`. Rows where entry_fill is NULL are excluded (same pattern as `list_closed_trades`).

### `DailySessionStateStore.list_equity_baselines()`

New method on `DailySessionStateStore`:

```python
def list_equity_baselines(
    self,
    *,
    trading_mode: TradingMode,
    strategy_version: str,
    start_date: date,
    end_date: date,
) -> dict[date, float]:
```

Returns `{session_date: equity_baseline}` for all sessions where `strategy_name = '_equity'` and `equity_baseline IS NOT NULL` in the date range, ordered by session_date.

## Service layer

New function in `web/service.py`:

```python
@dataclass(frozen=True)
class EquityChartPoint:
    t: datetime
    v: float

@dataclass(frozen=True)
class EquityChartData:
    points: list[EquityChartPoint]
    current: float | None
    pct_change: float | None
    label: str
    range_code: str

def load_equity_chart_data(
    *,
    settings: Settings,
    connection: ConnectionProtocol,
    range_code: str,          # "1d", "1m", "1y", "all"
    anchor_date: date,        # the session date (from /metrics date param)
    now: datetime | None = None,
    order_store: OrderStore | None = None,
    daily_session_state_store: DailySessionStateStore | None = None,
) -> EquityChartData:
```

Logic:
- **1d**: `list_equity_baselines(anchor_date, anchor_date)` → baseline; `list_trade_exits_in_range(anchor_date, anchor_date)` → exits sorted by `exit_time`; build cumulative series: first point is `(session_start_approximated_as_9:30_ET, baseline)`, then one point per exit at `(exit_time, baseline + cumulative_pnl)`. If no baseline, return empty.
- **1m/1y/all**: compute `start_date` (30/365/epoch days back); call `list_equity_baselines(start_date, anchor_date)` and `list_trade_exits_in_range(start_date, anchor_date)`; group exits by session date; for each date in baselines, eod_value = baseline + sum(pnl for exits on that date); one point per session at `(datetime.combine(session_date, time(16, 0), tzinfo=ET))`. Sessions with a baseline but zero exits are plotted at baseline (flat trading day).

## Web app route

New route in `app.py`:

```python
@app.get("/api/equity-chart")
def equity_chart_api(request: Request, range: str = "1d", date: str = "") -> Response:
```

- Auth check: if auth enabled and no operator, return `JSONResponse({"error": "unauthorized"}, status_code=401)`
- Parse `date` param the same way `/metrics` does; fall back to today
- Validate `range` — if not in `{"1d", "1m", "1y", "all"}`, return `{"error": "invalid range"}` with 400
- Call `load_equity_chart_data()`; serialize to JSON; return `JSONResponse`

## Template changes (`dashboard.html`)

Chart is shown only when `session_date` is in the template context (i.e., the `/metrics` route).

1. Add Chart.js from CDN in `<head>`:
   ```html
   <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.3/dist/chart.umd.min.js"></script>
   ```

2. Add a new `panel` block in the metrics section (above the trades table):
   ```html
   <div class="panel" id="equity-chart-panel" style="margin-bottom:1.5rem">
     <div style="display:flex; justify-content:space-between; align-items:flex-start; margin-bottom:0.5rem">
       <div>
         <div class="eyebrow">Portfolio Equity</div>
         <div id="equity-value" style="font-size:1.6rem; font-weight:700"></div>
         <div id="equity-change" style="font-size:0.9rem; color:var(--muted)"></div>
       </div>
       <div id="range-buttons" style="display:flex; gap:0.4rem">
         <!-- 1D / 1M / 1Y / All buttons rendered here -->
       </div>
     </div>
     <canvas id="equity-canvas" height="160"></canvas>
   </div>
   ```

3. Inline `<script>` block at bottom of template (inside `{% if session_date %}` block):
   - On page load, fetch `/api/equity-chart?range=1d&date={{ session_date }}` and render
   - Range button clicks re-fetch with the selected range
   - Chart uses `Chart.js` line chart with `fill: true`, `tension: 0.3`, color `var(--accent)` (`#1f6f78`)
   - The flat starting-line baseline effect comes naturally from the data

## Scope

- Modify: `src/alpaca_bot/storage/repositories.py` — add `list_trade_exits_in_range()` to `OrderStore` and `list_equity_baselines()` to `DailySessionStateStore`
- Modify: `src/alpaca_bot/web/service.py` — add `EquityChartPoint`, `EquityChartData`, `load_equity_chart_data()`
- Modify: `src/alpaca_bot/web/app.py` — add `/api/equity-chart` route
- Modify: `src/alpaca_bot/web/templates/dashboard.html` — add chart panel and JS

No migrations. No new Python dependencies. Chart.js loaded from CDN.

## Tests

Four new tests in `tests/unit/test_web_service.py`:

1. `test_load_equity_chart_data_1d_builds_cumulative_series`
   - Stub stores: equity_baseline=100000, two exits with pnl=+200 then pnl=-100 (chronological)
   - baseline=100000 → after first exit: 100200 → after second exit: 100100
   - Assert: 3 points with values [100000, 100200, 100100], current=100100, pct_change=+0.1

2. `test_load_equity_chart_data_1d_no_trades_returns_single_point`
   - equity_baseline=100000, no exits → 1 point at baseline, current=100000, pct_change=None

3. `test_load_equity_chart_data_multi_session_range`
   - 3 sessions: baselines=[99000, 99500, 100000], per-session pnl=[+500, +500, -400]
   - eod values=[99500, 100000, 99600]
   - Assert: 3 points with those values

4. `test_equity_chart_api_returns_json`
   - Stub `load_equity_chart_data` via injectable factory; assert response status 200, JSON content-type, `points` key present

Two new tests in `tests/unit/test_storage_db.py` (or `test_storage.py`):

5. `test_list_trade_exits_in_range_returns_pnl_per_exit`
6. `test_list_equity_baselines_returns_dict_by_date`

## Safety

- Read-only: no orders, positions, or state mutated
- Auth: endpoint respects the same `current_operator` gate as all other authenticated routes
- No new env vars, no new settings
- Chart.js CDN load failure degrades gracefully — the canvas shows blank, JS error is silent in console
- Empty data state: `points=[]` → chart renders empty axes, no JS crash
