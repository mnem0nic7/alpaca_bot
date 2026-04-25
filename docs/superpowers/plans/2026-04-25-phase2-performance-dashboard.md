# Phase 2 — Performance Dashboard: Implementation Plan

**Spec:** `docs/superpowers/specs/2026-04-25-mvp-completion-design.md`
**Test command:** `pytest tests/unit/ -q`
**Goal:** Surface trading performance in the web UI via a `/metrics` JSON endpoint and expanded dashboard HTML panels.
**Prerequisites:** Phase 1 complete (fill_price, filled_quantity on OrderRecord; daily_realized_pnl on OrderStore).

---

## Task 1 — `TradeRecord` dataclass

**File:** `src/alpaca_bot/web/service.py`

Add after the `WorkerHealth` dataclass:

```python
@dataclass(frozen=True)
class TradeRecord:
    symbol: str
    entry_time: datetime
    exit_time: datetime
    entry_price: float
    exit_price: float
    quantity: int
    pnl: float
    slippage: float | None  # fill_price - limit_price on the entry order; None if no limit_price
```

Export from `alpaca_bot.web.service` only; no storage layer involvement.

---

## Task 2 — `OrderStore.list_closed_trades()`

**File:** `src/alpaca_bot/storage/repositories.py`

Add to `OrderStore` after `daily_realized_pnl()`:

```python
def list_closed_trades(
    self,
    *,
    trading_mode: TradingMode,
    strategy_version: str,
    session_date: date,
) -> list[dict]:
    """Return one row per closed round-trip trade for a session date.

    Each row: symbol, entry_fill, entry_limit, entry_updated_at,
               exit_fill, exit_updated_at, qty
    """
    rows = fetch_all(
        self._connection,
        """
        SELECT
            x.symbol,
            (
                SELECT e.fill_price
                FROM orders e
                WHERE e.symbol = x.symbol
                  AND e.trading_mode = x.trading_mode
                  AND e.strategy_version = x.strategy_version
                  AND e.intent_type = 'entry'
                  AND e.fill_price IS NOT NULL
                  AND DATE(e.updated_at AT TIME ZONE 'America/New_York') = %s
                ORDER BY e.updated_at DESC LIMIT 1
            ) AS entry_fill,
            (
                SELECT e.limit_price
                FROM orders e
                WHERE e.symbol = x.symbol
                  AND e.trading_mode = x.trading_mode
                  AND e.strategy_version = x.strategy_version
                  AND e.intent_type = 'entry'
                  AND e.fill_price IS NOT NULL
                  AND DATE(e.updated_at AT TIME ZONE 'America/New_York') = %s
                ORDER BY e.updated_at DESC LIMIT 1
            ) AS entry_limit,
            (
                SELECT e.updated_at
                FROM orders e
                WHERE e.symbol = x.symbol
                  AND e.trading_mode = x.trading_mode
                  AND e.strategy_version = x.strategy_version
                  AND e.intent_type = 'entry'
                  AND e.fill_price IS NOT NULL
                  AND DATE(e.updated_at AT TIME ZONE 'America/New_York') = %s
                ORDER BY e.updated_at DESC LIMIT 1
            ) AS entry_time,
            x.fill_price AS exit_fill,
            x.updated_at AS exit_time,
            COALESCE(x.filled_quantity, x.quantity) AS qty
        FROM orders x
        WHERE x.trading_mode = %s
          AND x.strategy_version = %s
          AND x.intent_type IN ('stop', 'exit')
          AND x.fill_price IS NOT NULL
          AND DATE(x.updated_at AT TIME ZONE 'America/New_York') = %s
        ORDER BY x.updated_at
        """,
        (
            session_date, session_date, session_date,
            trading_mode.value,
            strategy_version,
            session_date,
        ),
    )
    return [
        {
            "symbol": row[0],
            "entry_fill": float(row[1]) if row[1] is not None else None,
            "entry_limit": float(row[2]) if row[2] is not None else None,
            "entry_time": row[3],
            "exit_fill": float(row[4]) if row[4] is not None else None,
            "exit_time": row[5],
            "qty": int(row[6]),
        }
        for row in rows
        if row[1] is not None and row[4] is not None
    ]
```

---

## Task 3 — `AuditEventStore.list_by_event_types()`

**File:** `src/alpaca_bot/storage/repositories.py`

Add to `AuditEventStore` after `load_latest()`:

```python
def list_by_event_types(
    self,
    *,
    event_types: list[str],
    limit: int = 20,
) -> list[AuditEvent]:
    if not event_types:
        return []
    placeholders = ", ".join(["%s"] * len(event_types))
    rows = fetch_all(
        self._connection,
        f"""
        SELECT event_type, symbol, payload, created_at
        FROM audit_events
        WHERE event_type IN ({placeholders})
        ORDER BY created_at DESC, event_id DESC
        LIMIT %s
        """,
        (*event_types, limit),
    )
    return [
        AuditEvent(
            event_type=row[0],
            symbol=row[1],
            payload=_load_json_payload(row[2]),
            created_at=row[3],
        )
        for row in rows
    ]
```

---

## Task 4 — `MetricsSnapshot` dataclass and `load_metrics_snapshot()` in service.py

**File:** `src/alpaca_bot/web/service.py`

### 4a. Add `MetricsSnapshot` after `DashboardSnapshot`:

```python
@dataclass(frozen=True)
class MetricsSnapshot:
    generated_at: datetime
    session_date: date
    trades: list[TradeRecord]
    total_pnl: float
    win_rate: float | None       # None when no trades
    mean_return_pct: float | None
    max_drawdown_pct: float | None
    sharpe_ratio: float | None   # None until 5+ trading days of history
    admin_history: list[AuditEvent]
```

### 4b. Add `load_metrics_snapshot()`:

```python
ADMIN_EVENT_TYPES = ["trading_status_changed"]

def load_metrics_snapshot(
    *,
    settings: Settings,
    connection: ConnectionProtocol,
    order_store: OrderStore | None = None,
    audit_event_store: AuditEventStore | None = None,
    now: datetime | None = None,
) -> MetricsSnapshot:
    generated_at = now or datetime.now(timezone.utc)
    session_date = generated_at.astimezone(settings.market_timezone).date()
    order_store = order_store or OrderStore(connection)
    audit_event_store = audit_event_store or AuditEventStore(connection)

    raw_trades = order_store.list_closed_trades(
        trading_mode=settings.trading_mode,
        strategy_version=settings.strategy_version,
        session_date=session_date,
    )
    trades = [_to_trade_record(t) for t in raw_trades]
    admin_history = audit_event_store.list_by_event_types(
        event_types=ADMIN_EVENT_TYPES,
        limit=20,
    )

    return MetricsSnapshot(
        generated_at=generated_at,
        session_date=session_date,
        trades=trades,
        total_pnl=sum(t.pnl for t in trades),
        win_rate=_win_rate(trades),
        mean_return_pct=_mean_return_pct(trades),
        max_drawdown_pct=_max_drawdown_pct(trades),
        sharpe_ratio=None,  # Requires multi-day history — Phase 2 defers to Phase 4+
        admin_history=admin_history,
    )


def _to_trade_record(row: dict) -> TradeRecord:
    entry_fill = row["entry_fill"]
    exit_fill = row["exit_fill"]
    qty = row["qty"]
    pnl = (exit_fill - entry_fill) * qty
    # positive slippage = favorable (got better price than limit); negative = adverse
    slippage = (
        row["entry_limit"] - entry_fill
        if row.get("entry_limit") is not None
        else None
    )
    return TradeRecord(
        symbol=row["symbol"],
        entry_time=row["entry_time"],
        exit_time=row["exit_time"],
        entry_price=entry_fill,
        exit_price=exit_fill,
        quantity=qty,
        pnl=pnl,
        slippage=slippage,
    )


def _win_rate(trades: list[TradeRecord]) -> float | None:
    if not trades:
        return None
    winners = sum(1 for t in trades if t.pnl > 0)
    return winners / len(trades)


def _mean_return_pct(trades: list[TradeRecord]) -> float | None:
    if not trades:
        return None
    returns = [(t.pnl / (t.entry_price * t.quantity)) for t in trades if t.entry_price > 0]
    return sum(returns) / len(returns) if returns else None


def _max_drawdown_pct(trades: list[TradeRecord]) -> float | None:
    if not trades:
        return None
    cumulative = 0.0
    peak = 0.0
    max_dd = 0.0
    for t in trades:
        cumulative += t.pnl
        if cumulative > peak:
            peak = cumulative
        if peak > 0:
            dd = (peak - cumulative) / peak
            if dd > max_dd:
                max_dd = dd
    # If peak never exceeded 0 (all losses), drawdown is undefined — return None
    return max_dd if peak > 0 else None
```

---

## Task 5 — `GET /metrics` endpoint in app.py

**File:** `src/alpaca_bot/web/app.py`

### 5a. Import `load_metrics_snapshot`:

```python
from alpaca_bot.web.service import load_dashboard_snapshot, load_health_snapshot, load_metrics_snapshot
```

### 5b. Add `/metrics` route inside `create_app()`:

```python
@app.get("/metrics")
def metrics(request: Request) -> JSONResponse:
    operator = current_operator(request, settings=app_settings)
    if auth_enabled(app_settings) and operator is None:
        return Response(
            status_code=status.HTTP_401_UNAUTHORIZED,
            headers={"WWW-Authenticate": 'Basic realm="alpaca_bot"'},
        )
    try:
        snapshot = _load_metrics(app)
    except Exception as exc:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"status": "error", "reason": str(exc)},
        )
    return JSONResponse(
        {
            "generated_at": snapshot.generated_at.isoformat(),
            "session_date": snapshot.session_date.isoformat(),
            "total_pnl": snapshot.total_pnl,
            "win_rate": snapshot.win_rate,
            "mean_return_pct": snapshot.mean_return_pct,
            "max_drawdown_pct": snapshot.max_drawdown_pct,
            "sharpe_ratio": snapshot.sharpe_ratio,
            "trades": [
                {
                    "symbol": t.symbol,
                    "entry_time": t.entry_time.isoformat() if t.entry_time else None,
                    "exit_time": t.exit_time.isoformat() if t.exit_time else None,
                    "entry_price": t.entry_price,
                    "exit_price": t.exit_price,
                    "quantity": t.quantity,
                    "pnl": t.pnl,
                    "slippage": t.slippage,
                }
                for t in snapshot.trades
            ],
            "admin_history": [
                {
                    "event_type": e.event_type,
                    "created_at": e.created_at.isoformat(),
                    "payload": e.payload,
                }
                for e in snapshot.admin_history
            ],
        }
    )
```

### 5c. Add `_load_metrics()` helper after `_load_health()`:

```python
def _load_metrics(app: FastAPI):
    connection = app.state.connect_postgres(app.state.settings.database_url)
    try:
        return load_metrics_snapshot(
            settings=app.state.settings,
            connection=connection,
            order_store=_build_store(app.state.order_store_factory, connection),
            audit_event_store=_build_store(app.state.audit_event_store_factory, connection),
        )
    finally:
        close = getattr(connection, "close", None)
        if callable(close):
            close()
```

---

## Task 6 — Dashboard HTML additions

**File:** `src/alpaca_bot/web/templates/dashboard.html`

Add five new panels **after** the existing panels. Each panel follows the existing `.panel` CSS class pattern:

### 6a. P&L Summary card
```html
<div class="panel">
  <h2>Today's P&amp;L</h2>
  <p class="metric">{{ format_price(metrics.total_pnl) }}</p>
  <p>{{ metrics.trades|selectattr("pnl", "gt", 0)|list|length }} wins /
     {{ metrics.trades|selectattr("pnl", "le", 0)|list|length }} losses</p>
  {% if metrics.win_rate is not none %}
  <p>Win rate: {{ "%.0f"|format(metrics.win_rate * 100) }}%</p>
  {% endif %}
</div>
```

### 6b. Per-symbol P&L table
```html
<div class="panel">
  <h2>Trade Results</h2>
  {% if metrics.trades %}
  <table>
    <thead><tr><th>Symbol</th><th>Entry</th><th>Exit</th><th>Qty</th><th>P&amp;L</th></tr></thead>
    <tbody>
    {% for t in metrics.trades %}
      <tr>
        <td>{{ t.symbol }}</td>
        <td>{{ format_price(t.entry_price) }}</td>
        <td>{{ format_price(t.exit_price) }}</td>
        <td>{{ t.quantity }}</td>
        <td class="{{ 'gain' if t.pnl > 0 else 'loss' }}">{{ format_price(t.pnl) }}</td>
      </tr>
    {% endfor %}
    </tbody>
  </table>
  {% else %}
  <p class="muted">No closed trades today.</p>
  {% endif %}
</div>
```

### 6c. Slippage report
```html
<div class="panel">
  <h2>Slippage Report</h2>
  {% if metrics.trades %}
  <table>
    <thead><tr><th>Symbol</th><th>Fill Price</th><th>Limit Price</th><th>Slippage</th></tr></thead>
    <tbody>
    {% for t in metrics.trades %}
      <tr>
        <td>{{ t.symbol }}</td>
        <td>{{ format_price(t.entry_price) }}</td>
        <td>{{ format_price(t.slippage + t.entry_price) if t.slippage is not none else "n/a" }}</td>
        <td>{{ format_price(t.slippage) if t.slippage is not none else "n/a" }}</td>
      </tr>
    {% endfor %}
    </tbody>
  </table>
  {% else %}
  <p class="muted">No trades.</p>
  {% endif %}
</div>
```

### 6d. Position age warning
```html
<div class="panel">
  <h2>Open Positions</h2>
  {% if snapshot.positions %}
  <table>
    <thead><tr><th>Symbol</th><th>Qty</th><th>Entry</th><th>Opened</th><th>Age</th></tr></thead>
    <tbody>
    {% for p in snapshot.positions %}
      {% set age_days = ((snapshot.generated_at - p.opened_at).total_seconds() / 86400)|int %}
      <tr class="{{ 'warn' if age_days >= 2 else '' }}">
        <td>{{ p.symbol }}</td>
        <td>{{ p.quantity }}</td>
        <td>{{ format_price(p.entry_price) }}</td>
        <td>{{ format_timestamp(p.opened_at) }}</td>
        <td>{{ age_days }} day{{ 's' if age_days != 1 else '' }}{% if age_days >= 2 %} ⚠{% endif %}</td>
      </tr>
    {% endfor %}
    </tbody>
  </table>
  {% else %}
  <p class="muted">No open positions.</p>
  {% endif %}
</div>
```

### 6e. Admin command history
```html
<div class="panel">
  <h2>Admin Commands</h2>
  {% if metrics.admin_history %}
  <table>
    <thead><tr><th>Time</th><th>Status</th><th>Reason</th></tr></thead>
    <tbody>
    {% for e in metrics.admin_history %}
      <tr>
        <td>{{ format_timestamp(e.created_at) }}</td>
        <td>{{ e.payload.get("status", e.event_type) }}</td>
        <td>{{ e.payload.get("reason", "") }}</td>
      </tr>
    {% endfor %}
    </tbody>
  </table>
  {% else %}
  <p class="muted">No admin commands recorded.</p>
  {% endif %}
</div>
```

### 6f. Pass `metrics` into dashboard template context (single connection)

Rename `_load_snapshot()` → `_load_dashboard_data()`, returning `(DashboardSnapshot, MetricsSnapshot)` over a **single** DB connection:

```python
def _load_dashboard_data(app: FastAPI) -> tuple:
    connection = app.state.connect_postgres(app.state.settings.database_url)
    try:
        order_store = _build_store(app.state.order_store_factory, connection)
        audit_event_store = _build_store(app.state.audit_event_store_factory, connection)
        snapshot = load_dashboard_snapshot(
            settings=app.state.settings,
            connection=connection,
            trading_status_store=_build_store(app.state.trading_status_store_factory, connection),
            daily_session_state_store=_build_store(
                app.state.daily_session_state_store_factory, connection
            ),
            position_store=_build_store(app.state.position_store_factory, connection),
            order_store=order_store,
            audit_event_store=audit_event_store,
        )
        metrics = load_metrics_snapshot(
            settings=app.state.settings,
            connection=connection,
            order_store=order_store,
            audit_event_store=audit_event_store,
        )
        return snapshot, metrics
    finally:
        close = getattr(connection, "close", None)
        if callable(close):
            close()
```

Update `dashboard()` route to unpack the tuple and pass `metrics` to the template context.

---

## Task 7 — Update `__init__.py` exports

No new public types need to be exported from `storage/__init__.py`. `TradeRecord` lives in `web/service.py`. `list_closed_trades` and `list_by_event_types` are new methods on existing concrete classes.

---

## Task 8 — New tests

### 8a. `test_storage_db.py` — `list_closed_trades` and `list_by_event_types`

Use the `_make_fake_connection(rows)` pattern established in Phase 1.

```python
class TestListClosedTrades:
    def test_returns_one_trade_record_per_closed_symbol(self):
        rows = [("AAPL", 150.0, 149.0, 150.0, None, datetime_now, datetime_now, 10)]
        # verify fill, slippage, qty
    def test_row_with_null_entry_fill_excluded(self):
        rows = [("AAPL", None, None, None, None, 155.0, datetime_now, 10)]
        # verify returns []

class TestListByEventTypes:
    def test_returns_matching_events_in_desc_order(self):
        # fake connection returns 2 rows, verify order
    def test_empty_event_types_returns_empty_list(self):
        # no DB call needed
```

### 8b. `test_web_service.py` — metrics computation

```python
def test_load_metrics_snapshot_computes_win_rate_correctly():
    # inject order_store with list_closed_trades returning 3 trades (2 wins, 1 loss)
    # verify win_rate == 2/3

def test_load_metrics_snapshot_total_pnl_sums_all_trades():
    # inject 2 trades with known pnl
    # verify total_pnl

def test_load_metrics_snapshot_max_drawdown_detects_drawdown():
    # trades: +100, -150, +50
    # peak=100, trough=-50, drawdown=150/100=1.5 → expressed as fraction
    # verify max_drawdown_pct > 0

def test_load_metrics_snapshot_max_drawdown_returns_none_when_all_losses():
    # trades: -50, -30 → peak never rises above 0 → None
    # verify max_drawdown_pct is None

def test_load_metrics_snapshot_sharpe_is_none():
    # Phase 2 always returns None for sharpe_ratio
    # verify sharpe_ratio is None

def test_load_metrics_snapshot_returns_empty_when_no_trades():
    # win_rate, mean_return_pct, max_drawdown_pct all None
```

### 8c. `test_web_app.py` — `/metrics` route

Follow `test_dashboard_route_renders_runtime_snapshot` pattern:

```python
def test_metrics_route_returns_json_with_correct_structure():
    # inject order_store with list_closed_trades returning 1 trade
    # inject audit_event_store with list_by_event_types returning 1 event
    # GET /metrics → 200, verify keys: total_pnl, win_rate, trades, admin_history

def test_metrics_route_returns_401_when_auth_enabled_and_no_credentials():
    # Settings with auth enabled, no auth header → 401

def test_metrics_route_returns_503_when_db_raises():
    # connect_postgres_fn raises → 503
```

---

## Execution order

1. Task 1 — TradeRecord dataclass
2. Task 2 — OrderStore.list_closed_trades()
3. Task 3 — AuditEventStore.list_by_event_types()
4. Task 4 — MetricsSnapshot + load_metrics_snapshot()
5. Task 5 — /metrics route in app.py
6. Task 6 — Dashboard HTML additions
7. Task 8 — New tests
8. `pytest tests/unit/ -q` — must pass

---

## Acceptance criteria

- `GET /metrics` returns JSON with `total_pnl`, `win_rate`, `mean_return_pct`, `max_drawdown_pct`, `sharpe_ratio` (always null in Phase 2), `trades`, `admin_history`
- Dashboard HTML includes P&L summary, trade results table, slippage report, position age warning, admin command history
- Position age warns (CSS class / ⚠ symbol) if any position is held ≥ 2 days
- `/metrics` requires auth when auth is enabled
- `pytest tests/unit/ -q` passes
