# Portfolio Equity Chart Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a portfolio equity line/area chart to the `/metrics` page showing equity over time with a 1D/1M/1Y/All range selector, current value, and % change — derived entirely from existing `daily_session_state` and `orders` tables with no migration.

**Architecture:** Four thin layers modified in dependency order — repository → service → route → template. All new logic is tested with the project's SimpleNamespace stub pattern (no mocks, no real DB). Chart.js loaded from CDN; no build step.

**Tech Stack:** Python, psycopg2, FastAPI/Starlette, Jinja2, Chart.js 4.4.3 from CDN.

---

### File Map

| File | Change |
|------|--------|
| `src/alpaca_bot/storage/repositories.py` | Add `OrderStore.list_trade_exits_in_range()` and `DailySessionStateStore.list_equity_baselines()` |
| `src/alpaca_bot/web/service.py` | Add `EquityChartPoint`, `EquityChartData` dataclasses and `load_equity_chart_data()` |
| `src/alpaca_bot/web/app.py` | Add `/api/equity-chart` route and `equity_chart_data_factory` param on `create_app()` |
| `src/alpaca_bot/web/templates/dashboard.html` | Add Chart.js CDN, chart panel HTML, inline JS |
| `tests/unit/test_storage_db.py` | Tests 5–6: repository method unit tests |
| `tests/unit/test_web_service.py` | Tests 1–3: service-layer unit tests |
| `tests/unit/test_web_app.py` | Test 4: API route integration test |

---

### Task 1: Repository methods

**Files:**
- Modify: `src/alpaca_bot/storage/repositories.py`
- Test: `tests/unit/test_storage_db.py`

- [ ] **Step 1: Write the two failing storage tests**

Append to `tests/unit/test_storage_db.py`:

```python
# ---------------------------------------------------------------------------
# OrderStore.list_trade_exits_in_range
# ---------------------------------------------------------------------------

def _make_conn_with_fetchall(rows):
    cursor = SimpleNamespace(
        execute=lambda sql, params=None: None,
        fetchone=lambda: None,
        fetchall=lambda: rows,
    )
    return SimpleNamespace(cursor=lambda: cursor, commit=lambda: None)


def test_list_trade_exits_in_range_returns_pnl_per_exit():
    from alpaca_bot.storage.repositories import OrderStore

    rows = [
        # (exit_time, qty, exit_fill, entry_fill)
        (datetime(2026, 5, 5, 13, 45, tzinfo=timezone.utc), 10, 101.0, 100.0),
        (datetime(2026, 5, 5, 14, 0,  tzinfo=timezone.utc), 20,  48.0,  50.0),
    ]
    conn = _make_conn_with_fetchall(rows)

    result = OrderStore(conn).list_trade_exits_in_range(
        trading_mode=TradingMode.PAPER,
        strategy_version="v1",
        start_date=date(2026, 5, 5),
        end_date=date(2026, 5, 5),
    )

    assert len(result) == 2
    # pnl = (exit_fill - entry_fill) * qty
    assert result[0]["pnl"] == pytest.approx(10.0)   # (101 - 100) * 10
    assert result[1]["pnl"] == pytest.approx(-40.0)  # (48  - 50)  * 20
    assert result[0]["exit_time"] == datetime(2026, 5, 5, 13, 45, tzinfo=timezone.utc)


def test_list_trade_exits_in_range_excludes_null_entry_fill():
    from alpaca_bot.storage.repositories import OrderStore

    rows = [
        # entry_fill (row[3]) is None → must be excluded
        (datetime(2026, 5, 5, 13, 45, tzinfo=timezone.utc), 10, 101.0, None),
    ]
    conn = _make_conn_with_fetchall(rows)

    result = OrderStore(conn).list_trade_exits_in_range(
        trading_mode=TradingMode.PAPER,
        strategy_version="v1",
        start_date=date(2026, 5, 5),
        end_date=date(2026, 5, 5),
    )

    assert result == []


# ---------------------------------------------------------------------------
# DailySessionStateStore.list_equity_baselines
# ---------------------------------------------------------------------------

def test_list_equity_baselines_returns_dict_by_date():
    from alpaca_bot.storage.repositories import DailySessionStateStore

    rows = [
        (date(2026, 5, 1), 99000.0),
        (date(2026, 5, 2), 99500.0),
    ]
    conn = _make_conn_with_fetchall(rows)

    result = DailySessionStateStore(conn).list_equity_baselines(
        trading_mode=TradingMode.PAPER,
        strategy_version="v1",
        start_date=date(2026, 5, 1),
        end_date=date(2026, 5, 5),
    )

    assert result == {date(2026, 5, 1): 99000.0, date(2026, 5, 2): 99500.0}


def test_list_equity_baselines_returns_empty_dict_when_no_rows():
    from alpaca_bot.storage.repositories import DailySessionStateStore

    conn = _make_conn_with_fetchall([])

    result = DailySessionStateStore(conn).list_equity_baselines(
        trading_mode=TradingMode.PAPER,
        strategy_version="v1",
        start_date=date(2026, 5, 1),
        end_date=date(2026, 5, 5),
    )

    assert result == {}
```

Also add `import pytest` to the imports in `test_storage_db.py` if not already present.

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/unit/test_storage_db.py::test_list_trade_exits_in_range_returns_pnl_per_exit tests/unit/test_storage_db.py::test_list_trade_exits_in_range_excludes_null_entry_fill tests/unit/test_storage_db.py::test_list_equity_baselines_returns_dict_by_date tests/unit/test_storage_db.py::test_list_equity_baselines_returns_empty_dict_when_no_rows -v
```

Expected: 4 FAILED — `AttributeError: 'OrderStore' object has no attribute 'list_trade_exits_in_range'`

- [ ] **Step 3: Add `EQUITY_SESSION_STATE_STRATEGY_NAME` to the models import in `repositories.py`**

In `src/alpaca_bot/storage/repositories.py`, update the import from `alpaca_bot.storage.models`:

```python
from alpaca_bot.storage.models import (
    AuditEvent,
    DailySessionState,
    EQUITY_SESSION_STATE_STRATEGY_NAME,
    OptionOrderRecord,
    OrderRecord,
    PositionRecord,
    StrategyFlag,
    TradingStatus,
    TradingStatusValue,
)
```

- [ ] **Step 4: Add `list_trade_exits_in_range()` to `OrderStore`**

In `src/alpaca_bot/storage/repositories.py`, append the following method to the `OrderStore` class (after `list_closed_trades()`, at line ~646, before the blank line before `class DailySessionStateStore`):

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
        rows = fetch_all(
            self._connection,
            """
            SELECT
                x.updated_at AS exit_time,
                COALESCE(x.filled_quantity, x.quantity) AS qty,
                x.fill_price AS exit_fill,
                (
                    SELECT e.fill_price
                    FROM orders e
                    WHERE e.symbol = x.symbol
                      AND e.trading_mode = x.trading_mode
                      AND e.strategy_version = x.strategy_version
                      AND e.strategy_name IS NOT DISTINCT FROM x.strategy_name
                      AND e.intent_type = 'entry'
                      AND e.fill_price IS NOT NULL
                      AND e.status = 'filled'
                      AND e.updated_at <= x.updated_at
                    ORDER BY e.updated_at DESC LIMIT 1
                ) AS entry_fill
            FROM orders x
            WHERE x.trading_mode = %s
              AND x.strategy_version = %s
              AND x.intent_type IN ('stop', 'exit')
              AND x.fill_price IS NOT NULL
              AND x.status = 'filled'
              AND DATE(x.updated_at AT TIME ZONE %s) >= %s
              AND DATE(x.updated_at AT TIME ZONE %s) <= %s
            ORDER BY x.updated_at
            """,
            (
                trading_mode.value,
                strategy_version,
                market_timezone,
                start_date,
                market_timezone,
                end_date,
            ),
        )
        return [
            {
                "exit_time": row[0],
                "pnl": (float(row[2]) - float(row[3])) * int(row[1]),
            }
            for row in rows
            if row[3] is not None
        ]
```

- [ ] **Step 5: Add `list_equity_baselines()` to `DailySessionStateStore`**

In `src/alpaca_bot/storage/repositories.py`, append the following method to the `DailySessionStateStore` class (after `list_by_session()`, before `class PositionStore`):

```python
    def list_equity_baselines(
        self,
        *,
        trading_mode: TradingMode,
        strategy_version: str,
        start_date: date,
        end_date: date,
    ) -> dict[date, float]:
        rows = fetch_all(
            self._connection,
            """
            SELECT session_date, equity_baseline
            FROM daily_session_state
            WHERE trading_mode = %s
              AND strategy_version = %s
              AND strategy_name = %s
              AND equity_baseline IS NOT NULL
              AND session_date >= %s
              AND session_date <= %s
            ORDER BY session_date
            """,
            (
                trading_mode.value,
                strategy_version,
                EQUITY_SESSION_STATE_STRATEGY_NAME,
                start_date,
                end_date,
            ),
        )
        return {row[0]: float(row[1]) for row in rows}
```

- [ ] **Step 6: Run storage tests to verify they pass**

```bash
pytest tests/unit/test_storage_db.py::test_list_trade_exits_in_range_returns_pnl_per_exit tests/unit/test_storage_db.py::test_list_trade_exits_in_range_excludes_null_entry_fill tests/unit/test_storage_db.py::test_list_equity_baselines_returns_dict_by_date tests/unit/test_storage_db.py::test_list_equity_baselines_returns_empty_dict_when_no_rows -v
```

Expected: 4 PASSED.

- [ ] **Step 7: Run full test suite to check for regressions**

```bash
pytest tests/unit/ -q
```

Expected: all tests pass (no regressions).

- [ ] **Step 8: Commit**

```bash
git add src/alpaca_bot/storage/repositories.py tests/unit/test_storage_db.py
git commit -m "feat: add list_trade_exits_in_range and list_equity_baselines to storage repos"
```

---

### Task 2: Service layer — `load_equity_chart_data()`

**Files:**
- Modify: `src/alpaca_bot/web/service.py`
- Test: `tests/unit/test_web_service.py`

- [ ] **Step 1: Write the three failing service tests**

Append to `tests/unit/test_web_service.py`:

```python
# ---------------------------------------------------------------------------
# load_equity_chart_data
# ---------------------------------------------------------------------------

def test_load_equity_chart_data_1d_builds_cumulative_series():
    from alpaca_bot.web.service import load_equity_chart_data
    from datetime import timezone
    from zoneinfo import ZoneInfo

    anchor = date(2026, 5, 5)
    ET = ZoneInfo("America/New_York")
    # Two exit timestamps (any valid UTC datetimes within the session)
    t1 = datetime(2026, 5, 5, 14, 0, tzinfo=timezone.utc)   # 10:00 ET
    t2 = datetime(2026, 5, 5, 15, 0, tzinfo=timezone.utc)   # 11:00 ET

    order_store = SimpleNamespace(
        list_trade_exits_in_range=lambda **_: [
            {"exit_time": t1, "pnl": 200.0},
            {"exit_time": t2, "pnl": -100.0},
        ]
    )
    dss_store = SimpleNamespace(
        list_equity_baselines=lambda **_: {anchor: 100000.0}
    )

    result = load_equity_chart_data(
        settings=make_settings(),
        connection=SimpleNamespace(),
        range_code="1d",
        anchor_date=anchor,
        order_store=order_store,
        daily_session_state_store=dss_store,
    )

    assert len(result.points) == 3
    assert result.points[0].v == pytest.approx(100000.0)
    assert result.points[1].v == pytest.approx(100200.0)
    assert result.points[2].v == pytest.approx(100100.0)
    assert result.current == pytest.approx(100100.0)
    assert result.pct_change == pytest.approx(0.1)


def test_load_equity_chart_data_1d_no_trades_returns_single_point():
    from alpaca_bot.web.service import load_equity_chart_data

    anchor = date(2026, 5, 5)
    order_store = SimpleNamespace(list_trade_exits_in_range=lambda **_: [])
    dss_store = SimpleNamespace(list_equity_baselines=lambda **_: {anchor: 100000.0})

    result = load_equity_chart_data(
        settings=make_settings(),
        connection=SimpleNamespace(),
        range_code="1d",
        anchor_date=anchor,
        order_store=order_store,
        daily_session_state_store=dss_store,
    )

    assert len(result.points) == 1
    assert result.points[0].v == pytest.approx(100000.0)
    assert result.current == pytest.approx(100000.0)
    assert result.pct_change is None


def test_load_equity_chart_data_multi_session_range():
    from alpaca_bot.web.service import load_equity_chart_data
    from datetime import timezone

    d1 = date(2026, 5, 1)
    d2 = date(2026, 5, 2)
    d3 = date(2026, 5, 3)

    baselines = {d1: 99000.0, d2: 99500.0, d3: 100000.0}
    exits = [
        # exit_time in UTC; date in ET (UTC-4) → same calendar date as UTC here
        {"exit_time": datetime(2026, 5, 1, 19, 0, tzinfo=timezone.utc), "pnl": 500.0},
        {"exit_time": datetime(2026, 5, 2, 19, 0, tzinfo=timezone.utc), "pnl": 500.0},
        {"exit_time": datetime(2026, 5, 3, 19, 0, tzinfo=timezone.utc), "pnl": -400.0},
    ]

    order_store = SimpleNamespace(list_trade_exits_in_range=lambda **_: exits)
    dss_store = SimpleNamespace(list_equity_baselines=lambda **_: baselines)

    result = load_equity_chart_data(
        settings=make_settings(),
        connection=SimpleNamespace(),
        range_code="1m",
        anchor_date=date(2026, 5, 5),
        order_store=order_store,
        daily_session_state_store=dss_store,
    )

    assert len(result.points) == 3
    assert result.points[0].v == pytest.approx(99500.0)   # 99000 + 500
    assert result.points[1].v == pytest.approx(100000.0)  # 99500 + 500
    assert result.points[2].v == pytest.approx(99600.0)   # 100000 - 400
```

Also add `import pytest` to `test_web_service.py` imports if not present.

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/unit/test_web_service.py::test_load_equity_chart_data_1d_builds_cumulative_series tests/unit/test_web_service.py::test_load_equity_chart_data_1d_no_trades_returns_single_point tests/unit/test_web_service.py::test_load_equity_chart_data_multi_session_range -v
```

Expected: 3 FAILED — `ImportError: cannot import name 'load_equity_chart_data' from 'alpaca_bot.web.service'`

- [ ] **Step 3: Add imports to `service.py`**

In `src/alpaca_bot/web/service.py`, update the datetime import line from:
```python
from datetime import date, datetime, timezone
```
to:
```python
from datetime import date, datetime, time, timezone
from zoneinfo import ZoneInfo
```

Also add `EQUITY_SESSION_STATE_STRATEGY_NAME` to the `alpaca_bot.storage` import block:
```python
from alpaca_bot.storage import (
    AuditEvent,
    AuditEventStore,
    DailySessionState,
    DailySessionStateStore,
    EQUITY_SESSION_STATE_STRATEGY_NAME,
    GLOBAL_SESSION_STATE_STRATEGY_NAME,
    OrderRecord,
    OrderStore,
    PositionRecord,
    PositionStore,
    StrategyFlag,
    StrategyFlagStore,
    TradingStatus,
    TradingStatusStore,
)
```

- [ ] **Step 4: Add `EquityChartPoint`, `EquityChartData`, and `load_equity_chart_data()` to `service.py`**

Append to `src/alpaca_bot/web/service.py` (at the end of the file):

```python
# ---------------------------------------------------------------------------
# Equity chart
# ---------------------------------------------------------------------------

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


def _equity_label(now: datetime) -> str:
    ET = ZoneInfo("America/New_York")
    return now.astimezone(ET).strftime("%b %d, %I:%M %p ET")


def load_equity_chart_data(
    *,
    settings: Settings,
    connection: ConnectionProtocol,
    range_code: str,
    anchor_date: date,
    now: datetime | None = None,
    order_store: OrderStore | None = None,
    daily_session_state_store: DailySessionStateStore | None = None,
) -> EquityChartData:
    if now is None:
        now = datetime.now(timezone.utc)
    label = _equity_label(now)

    _order_store = order_store or OrderStore(connection)
    _dss_store = daily_session_state_store or DailySessionStateStore(connection)

    ET = ZoneInfo("America/New_York")

    if range_code == "1d":
        baselines = _dss_store.list_equity_baselines(
            trading_mode=settings.trading_mode,
            strategy_version=settings.strategy_version,
            start_date=anchor_date,
            end_date=anchor_date,
        )
        if anchor_date not in baselines:
            return EquityChartData(
                points=[], current=None, pct_change=None, label=label, range_code=range_code
            )
        baseline = baselines[anchor_date]
        exits = _order_store.list_trade_exits_in_range(
            trading_mode=settings.trading_mode,
            strategy_version=settings.strategy_version,
            start_date=anchor_date,
            end_date=anchor_date,
        )
        session_start = datetime.combine(anchor_date, time(9, 30), tzinfo=ET)
        points = [EquityChartPoint(t=session_start, v=round(baseline, 2))]
        cumulative = 0.0
        for exit_row in exits:
            cumulative += exit_row["pnl"]
            points.append(
                EquityChartPoint(
                    t=exit_row["exit_time"],
                    v=round(baseline + cumulative, 2),
                )
            )
    else:
        from datetime import timedelta

        if range_code == "1m":
            start_date = anchor_date - timedelta(days=30)
        elif range_code == "1y":
            start_date = anchor_date - timedelta(days=365)
        else:  # "all"
            start_date = date(2000, 1, 1)

        baselines = _dss_store.list_equity_baselines(
            trading_mode=settings.trading_mode,
            strategy_version=settings.strategy_version,
            start_date=start_date,
            end_date=anchor_date,
        )
        exits = _order_store.list_trade_exits_in_range(
            trading_mode=settings.trading_mode,
            strategy_version=settings.strategy_version,
            start_date=start_date,
            end_date=anchor_date,
        )

        exits_by_date: dict[date, float] = {}
        for exit_row in exits:
            d = exit_row["exit_time"].astimezone(ET).date()
            exits_by_date[d] = exits_by_date.get(d, 0.0) + exit_row["pnl"]

        points = [
            EquityChartPoint(
                t=datetime.combine(session_d, time(16, 0), tzinfo=ET),
                v=round(baseline + exits_by_date.get(session_d, 0.0), 2),
            )
            for session_d, baseline in sorted(baselines.items())
        ]

    current = points[-1].v if points else None
    first_v = points[0].v if points else None
    pct_change = (
        round((current - first_v) / first_v * 100, 4)
        if len(points) >= 2 and first_v
        else None
    )
    return EquityChartData(
        points=points,
        current=current,
        pct_change=pct_change,
        label=label,
        range_code=range_code,
    )
```

- [ ] **Step 5: Run service tests to verify they pass**

```bash
pytest tests/unit/test_web_service.py::test_load_equity_chart_data_1d_builds_cumulative_series tests/unit/test_web_service.py::test_load_equity_chart_data_1d_no_trades_returns_single_point tests/unit/test_web_service.py::test_load_equity_chart_data_multi_session_range -v
```

Expected: 3 PASSED.

- [ ] **Step 6: Run full test suite**

```bash
pytest tests/unit/ -q
```

Expected: all tests pass.

- [ ] **Step 7: Commit**

```bash
git add src/alpaca_bot/web/service.py tests/unit/test_web_service.py
git commit -m "feat: add load_equity_chart_data service function and EquityChartData types"
```

---

### Task 3: API route `/api/equity-chart`

**Files:**
- Modify: `src/alpaca_bot/web/app.py`
- Test: `tests/unit/test_web_app.py`

- [ ] **Step 1: Write the failing API test**

Append to `tests/unit/test_web_app.py`:

```python
def test_equity_chart_api_returns_json() -> None:
    from alpaca_bot.web.service import EquityChartData, EquityChartPoint

    fake_data = EquityChartData(
        points=[
            EquityChartPoint(
                t=datetime(2026, 5, 5, 13, 30, tzinfo=timezone.utc),
                v=100000.0,
            )
        ],
        current=100000.0,
        pct_change=None,
        label="May 05, 09:30 AM ET",
        range_code="1d",
    )

    app = create_app(
        settings=make_settings(),
        connect_postgres_fn=ConnectionFactory([FakeConnection(responses=[])]),
        equity_chart_data_factory=lambda **_: fake_data,
    )

    with TestClient(app) as client:
        response = client.get("/api/equity-chart?range=1d&date=2026-05-05")

    assert response.status_code == 200
    assert "application/json" in response.headers["content-type"]
    body = response.json()
    assert "points" in body
    assert body["range"] == "1d"
    assert body["current"] == pytest.approx(100000.0)
    assert body["pct_change"] is None


def test_equity_chart_api_rejects_invalid_range() -> None:
    app = create_app(
        settings=make_settings(),
        connect_postgres_fn=ConnectionFactory([FakeConnection(responses=[])]),
        equity_chart_data_factory=lambda **_: (_ for _ in ()).throw(AssertionError("should not call")),
    )

    with TestClient(app) as client:
        response = client.get("/api/equity-chart?range=bad")

    assert response.status_code == 400
    assert response.json()["error"] == "invalid range"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/unit/test_web_app.py::test_equity_chart_api_returns_json tests/unit/test_web_app.py::test_equity_chart_api_rejects_invalid_range -v
```

Expected: 2 FAILED — `TypeError: create_app() got an unexpected keyword argument 'equity_chart_data_factory'`

- [ ] **Step 3: Add `load_equity_chart_data` and `EquityChartData` to the import in `app.py`**

In `src/alpaca_bot/web/app.py`, update the import from `alpaca_bot.web.service`:

```python
from alpaca_bot.web.service import (
    ALL_AUDIT_EVENT_TYPES,
    EquityChartData,
    load_audit_page,
    load_dashboard_snapshot,
    load_equity_chart_data,
    load_health_snapshot,
    load_metrics_snapshot,
)
```

- [ ] **Step 4: Add `equity_chart_data_factory` parameter to `create_app()` and wire it to `app.state`**

In `src/alpaca_bot/web/app.py`, update the `create_app()` signature — add the new parameter after `market_data_adapter`:

```python
def create_app(
    *,
    settings: Settings | None = None,
    connect: Callable[[str], ConnectionProtocol] | None = None,
    connection: ConnectionProtocol | None = None,
    db_connection: ConnectionProtocol | None = None,
    connect_postgres_fn: Callable[[str], ConnectionProtocol] | None = None,
    trading_status_store_factory: Callable[[ConnectionProtocol], object] | None = None,
    position_store_factory: Callable[[ConnectionProtocol], object] | None = None,
    order_store_factory: Callable[[ConnectionProtocol], object] | None = None,
    daily_session_state_store_factory: Callable[[ConnectionProtocol], object] | None = None,
    audit_event_store_factory: Callable[[ConnectionProtocol], object] | None = None,
    strategy_flag_store_factory: Callable[[ConnectionProtocol], object] | None = None,
    watchlist_store_factory: Callable[[ConnectionProtocol], object] | None = None,
    notifier: Notifier | None = None,
    market_data_adapter: object | None = None,
    equity_chart_data_factory: Callable[..., EquityChartData] | None = None,
) -> FastAPI:
```

Then, after `app.state.market_data_adapter = market_data_adapter` (around line 122), add:

```python
    app.state.equity_chart_data_factory = equity_chart_data_factory or load_equity_chart_data
```

- [ ] **Step 5: Add the `/api/equity-chart` route**

In `src/alpaca_bot/web/app.py`, add the following route after the `/healthz` route (after line ~311):

```python
    @app.get("/api/equity-chart")
    def equity_chart_api(
        request: Request,
        range: str = "1d",
        date: str = "",
    ) -> Response:
        operator = current_operator(request, settings=app_settings)
        if auth_enabled(app_settings) and operator is None:
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        if range not in {"1d", "1m", "1y", "all"}:
            return JSONResponse({"error": "invalid range"}, status_code=400)
        now = datetime.now(timezone.utc)
        today = now.astimezone(app_settings.market_timezone).date()
        anchor_date, _ = _parse_date_param(date, today=today)
        try:
            connection = app.state.connect_postgres(app_settings.database_url)
            try:
                data = app.state.equity_chart_data_factory(
                    settings=app_settings,
                    connection=connection,
                    range_code=range,
                    anchor_date=anchor_date,
                    now=now,
                    order_store=_build_store(app.state.order_store_factory, connection),
                    daily_session_state_store=_build_store(
                        app.state.daily_session_state_store_factory, connection
                    ),
                )
            finally:
                close = getattr(connection, "close", None)
                if callable(close):
                    close()
        except Exception:
            import logging
            logging.getLogger(__name__).exception("/api/equity-chart failed")
            return JSONResponse({"error": "service unavailable"}, status_code=503)
        return JSONResponse(
            {
                "points": [{"t": p.t.isoformat(), "v": p.v} for p in data.points],
                "current": data.current,
                "pct_change": data.pct_change,
                "label": data.label,
                "range": data.range_code,
            }
        )
```

- [ ] **Step 6: Run API tests to verify they pass**

```bash
pytest tests/unit/test_web_app.py::test_equity_chart_api_returns_json tests/unit/test_web_app.py::test_equity_chart_api_rejects_invalid_range -v
```

Expected: 2 PASSED.

- [ ] **Step 7: Run full test suite**

```bash
pytest tests/unit/ -q
```

Expected: all tests pass.

- [ ] **Step 8: Commit**

```bash
git add src/alpaca_bot/web/app.py tests/unit/test_web_app.py
git commit -m "feat: add /api/equity-chart JSON endpoint with injectable factory"
```

---

### Task 4: Template — chart panel and JS

**Files:**
- Modify: `src/alpaca_bot/web/templates/dashboard.html`

No pure-Python unit test covers this task (HTML/JS is tested manually). Verify visually by starting the dev server and loading `/metrics`.

- [ ] **Step 1: Add Chart.js CDN script tag to `<head>`**

In `src/alpaca_bot/web/templates/dashboard.html`, find the closing `</style>` tag (line ~138) and insert the Chart.js CDN script tag immediately before `</head>`:

```html
    <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.3/dist/chart.umd.min.js"></script>
  </head>
```

So the end of `<head>` becomes:

```html
    </style>
    <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.3/dist/chart.umd.min.js"></script>
  </head>
```

- [ ] **Step 2: Add chart panel HTML inside `{% if metrics %}`**

In `src/alpaca_bot/web/templates/dashboard.html`, find the line (around line 534–535):

```html
      {% if metrics %}
      <section class="section-grid" style="margin-top: 1rem;">
```

Insert the chart panel between `{% if metrics %}` and `<section class="section-grid"...>`:

```html
      {% if metrics %}
      {% if session_date %}
      <div class="panel" id="equity-chart-panel" style="margin-bottom:1.5rem">
        <div style="display:flex; justify-content:space-between; align-items:flex-start; margin-bottom:0.5rem">
          <div>
            <div class="eyebrow">Portfolio Equity</div>
            <div id="equity-value" style="font-size:1.6rem; font-weight:700"></div>
            <div id="equity-change" style="font-size:0.9rem; color:var(--muted)"></div>
          </div>
          <div id="range-buttons" style="display:flex; gap:0.4rem">
            <button class="btn-neutral" id="btn-1d" onclick="loadEquityChart('1d')">1D</button>
            <button class="btn-neutral" id="btn-1m" onclick="loadEquityChart('1m')">1M</button>
            <button class="btn-neutral" id="btn-1y" onclick="loadEquityChart('1y')">1Y</button>
            <button class="btn-neutral" id="btn-all" onclick="loadEquityChart('all')">All</button>
          </div>
        </div>
        <canvas id="equity-canvas" height="160"></canvas>
      </div>
      {% endif %}
      <section class="section-grid" style="margin-top: 1rem;">
```

- [ ] **Step 3: Add inline JS block inside `{% if session_date %}`**

In the same file, find the `{% endif %}` that closes the outer `{% if metrics %}` block (line ~695). Insert the inline script block just before that `{% endif %}`:

```html
      {% if session_date %}
      <script>
        (function() {
          var _equityChart = null;

          function setActiveEquityBtn(range) {
            ['1d','1m','1y','all'].forEach(function(r) {
              var btn = document.getElementById('btn-' + r);
              if (btn) {
                btn.style.fontWeight = r === range ? '700' : '';
                btn.style.borderColor = r === range ? 'var(--accent)' : '';
                btn.style.color = r === range ? 'var(--accent)' : '';
              }
            });
          }

          function loadEquityChart(range) {
            setActiveEquityBtn(range);
            fetch('/api/equity-chart?range=' + range + '&date={{ session_date }}')
              .then(function(r) { return r.json(); })
              .then(function(data) {
                var valEl = document.getElementById('equity-value');
                var chgEl = document.getElementById('equity-change');
                if (data.current != null) {
                  valEl.textContent = '$' + data.current.toLocaleString('en-US', {
                    minimumFractionDigits: 2, maximumFractionDigits: 2
                  });
                } else {
                  valEl.textContent = '—';
                }
                if (data.pct_change != null) {
                  var sign = data.pct_change >= 0 ? '+' : '';
                  chgEl.textContent = sign + data.pct_change.toFixed(2) + '%  ' + (data.label || '');
                  chgEl.style.color = data.pct_change >= 0 ? 'var(--accent)' : 'var(--warn)';
                } else {
                  chgEl.textContent = data.label || '';
                  chgEl.style.color = 'var(--muted)';
                }
                var labels = data.points.map(function(p) { return p.t; });
                var values = data.points.map(function(p) { return p.v; });
                if (_equityChart) { _equityChart.destroy(); }
                var ctx = document.getElementById('equity-canvas').getContext('2d');
                _equityChart = new Chart(ctx, {
                  type: 'line',
                  data: {
                    labels: labels,
                    datasets: [{
                      data: values,
                      fill: true,
                      tension: 0.3,
                      borderColor: '#1f6f78',
                      backgroundColor: 'rgba(31, 111, 120, 0.12)',
                      pointRadius: 0,
                      borderWidth: 2
                    }]
                  },
                  options: {
                    responsive: true,
                    plugins: {
                      legend: { display: false },
                      tooltip: { mode: 'index', intersect: false }
                    },
                    scales: {
                      x: { display: false },
                      y: {
                        display: true,
                        grid: { color: 'rgba(216, 210, 197, 0.5)' },
                        ticks: { color: 'var(--muted)', font: { size: 11 } }
                      }
                    }
                  }
                });
              })
              .catch(function() {});
          }

          loadEquityChart('1d');
        })();
      </script>
      {% endif %}
      {% endif %}
```

Note: this replaces the existing `{% endif %}` that closes `{% if metrics %}` — make sure the new structure is:
```
{% if metrics %}
  {% if session_date %} ... chart panel ... {% endif %}
  <section class="section-grid" ...> ... </section>
  ...rest of metrics content...
  {% if session_date %} ... chart script ... {% endif %}
{% endif %}
```

- [ ] **Step 4: Run the full test suite to confirm nothing broke**

```bash
pytest tests/unit/ -q
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/alpaca_bot/web/templates/dashboard.html
git commit -m "feat: add portfolio equity chart panel and Chart.js script to metrics page"
```

---

### Task 5: End-to-end smoke test

These steps verify the feature works in the running app. They require a local dev environment with DB access.

- [ ] **Step 1: Start the web server**

```bash
alpaca-bot-web
```

- [ ] **Step 2: Open `/metrics` in a browser**

Navigate to `http://localhost:18080/metrics` (or the configured port). The equity chart panel should appear above the Session P&L Summary grid showing:
- A "Portfolio Equity" eyebrow label
- A value display (shows `—` if no equity baseline for today)
- Range buttons 1D / 1M / 1Y / All
- A Chart.js canvas (empty axes if no data)

- [ ] **Step 3: Check `/api/equity-chart` directly**

```bash
curl "http://localhost:18080/api/equity-chart?range=1d" | python3 -m json.tool
```

Expected: JSON with `points`, `current`, `pct_change`, `label`, `range` keys. `points` may be `[]` if no `_equity` session state exists for today.

- [ ] **Step 4: Final full test suite**

```bash
pytest tests/unit/ -q
```

Expected: all tests pass.
