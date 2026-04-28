---
title: Live Prices on Dashboard — Implementation Plan
date: 2026-04-28
status: approved
---

# Live Prices on Dashboard — Implementation Plan

## Overview

Adds Last Price, Unreal P&L $, and Unreal P&L % to the Open Positions table on the dashboard by fetching live prices via `StockLatestTradeRequest` through `AlpacaMarketDataAdapter`. No trading logic changes; dashboard degrades gracefully when Alpaca is unavailable.

## Task 1 — Extend `AlpacaMarketDataAdapter` with `get_latest_prices()`

**File:** `src/alpaca_bot/execution/alpaca.py`

### 1a — Extend `HistoricalDataClientProtocol`

Add `get_latest_stock_trades` to the protocol so the stub in tests can implement it:

```python
class HistoricalDataClientProtocol(Protocol):
    def get_stock_bars(self, request_params: Any) -> Any: ...
    def get_latest_stock_trades(self, request_params: Any) -> Any: ...  # ADD
```

### 1b — Add `get_latest_prices()` to `AlpacaMarketDataAdapter`

Insert after `get_daily_bars()`:

```python
def get_latest_prices(self, symbols: Sequence[str]) -> dict[str, float]:
    if not symbols:
        return {}
    request = _latest_trade_request(symbols=symbols)
    raw = _retry_with_backoff(lambda: self._historical.get_latest_stock_trades(request))
    return _parse_latest_trades(raw)
```

### 1c — Add `_latest_trade_request()` helper (near `_stock_bars_request`)

```python
def _latest_trade_request(*, symbols: Sequence[str]) -> Any:
    try:
        from alpaca.data.requests import StockLatestTradeRequest
    except ModuleNotFoundError:
        return {"symbol_or_symbols": list(symbols)}
    return StockLatestTradeRequest(symbol_or_symbols=list(symbols))
```

### 1d — Add `_parse_latest_trades()` helper

```python
def _parse_latest_trades(raw: Any) -> dict[str, float]:
    data = raw.data if hasattr(raw, "data") else raw
    if not isinstance(data, Mapping):
        return {}
    result: dict[str, float] = {}
    for symbol, trade in data.items():
        price = getattr(trade, "price", None)
        if price is not None:
            result[str(symbol).upper()] = float(price)
    return result
```

## Task 2 — `DashboardSnapshot` + `load_dashboard_snapshot()`

**File:** `src/alpaca_bot/web/service.py`

### 2a — Add `latest_prices` field to `DashboardSnapshot`

```python
@dataclass(frozen=True)
class DashboardSnapshot:
    generated_at: datetime
    trading_status: TradingStatus | None
    session_state: DailySessionState | None
    positions: list[PositionRecord]
    working_orders: list[OrderRecord]
    recent_orders: list[OrderRecord]
    recent_events: list[AuditEvent]
    worker_health: WorkerHealth
    strategy_flags: list[tuple[str, StrategyFlag | None]]
    strategy_entries_disabled: dict[str, bool] = dc_field(default_factory=dict)
    latest_prices: dict[str, float] = dc_field(default_factory=dict)  # ADD — last
```

`latest_prices` must be last (it has a default) after `strategy_entries_disabled`.

### 2b — Add `latest_prices` kwarg to `load_dashboard_snapshot()`

```python
def load_dashboard_snapshot(
    *,
    settings: Settings,
    connection: ConnectionProtocol,
    ...existing params...
    latest_prices: dict[str, float] | None = None,  # ADD
) -> DashboardSnapshot:
```

Pass it into the returned dataclass:

```python
return DashboardSnapshot(
    ...existing fields...,
    strategy_entries_disabled=strategy_entries_disabled,
    latest_prices=latest_prices or {},  # ADD
)
```

## Task 3 — Wire adapter in `app.py`

**File:** `src/alpaca_bot/web/app.py`

### 3a — Add `market_data_adapter` param to `create_app()`

**Important constraint**: `cli.py` uses `uvicorn.run("alpaca_bot.web.app:create_app", factory=True, ...)` which calls `create_app()` with no arguments. Therefore the adapter must be auto-constructed inside `create_app()` when the parameter is `None`, not injected from the CLI level.

```python
def create_app(
    *,
    settings: Settings | None = None,
    ...existing params...
    market_data_adapter: object | None = None,  # ADD — Any with get_latest_prices()
) -> FastAPI:
    app_settings = settings or Settings.from_env()
    ...
    # Auto-build the market data adapter when not injected (production path via uvicorn factory)
    if market_data_adapter is None:
        try:
            from alpaca_bot.execution.alpaca import AlpacaMarketDataAdapter
            market_data_adapter = AlpacaMarketDataAdapter.from_settings(app_settings)
        except Exception:
            market_data_adapter = None  # credentials missing or alpaca-py not installed
    app.state.market_data_adapter = market_data_adapter  # ADD
```

Tests inject a fake adapter by passing `market_data_adapter=FakeMarketDataAdapter(...)` directly to `create_app()`. Tests that don't need prices pass `market_data_adapter=None` explicitly to skip the auto-build (since test settings don't have valid Alpaca credentials).

### 3b — Fetch live prices in `_load_dashboard_data()`

```python
def _load_dashboard_data(app: FastAPI) -> tuple:
    connection = app.state.connect_postgres(app.state.settings.database_url)
    try:
        ...existing store builds...
        snapshot_without_prices = load_dashboard_snapshot(
            settings=app.state.settings,
            connection=connection,
            ...existing stores...
        )
        latest_prices = _fetch_latest_prices(
            adapter=app.state.market_data_adapter,
            positions=snapshot_without_prices.positions,
        )
        snapshot = load_dashboard_snapshot(
            settings=app.state.settings,
            connection=connection,
            ...existing stores...
            latest_prices=latest_prices,
        )
        ...rest unchanged...
```

**Revised approach** — build the position store first, pre-fetch positions for the price call, then pass both into `load_dashboard_snapshot()`.

**Note on double-fetch**: `load_dashboard_snapshot()` calls `position_store.list_all()` internally. Fetching positions a second time to get symbols for the price call means two DB reads of the same small table. This is acceptable — positions is a handful of rows, and the alternative (refactoring `load_dashboard_snapshot` to accept a `positions` list) adds more churn. The plan explicitly accepts the double-fetch.

```python
def _load_dashboard_data(app: FastAPI) -> tuple:
    connection = app.state.connect_postgres(app.state.settings.database_url)
    try:
        settings = app.state.settings
        order_store = _build_store(app.state.order_store_factory, connection)
        audit_event_store = _build_store(app.state.audit_event_store_factory, connection)
        position_store = _build_store(app.state.position_store_factory, connection)
        # Pre-fetch positions to build the symbols list for live price fetch.
        # load_dashboard_snapshot will re-fetch via the same position_store (double-read, accepted).
        pre_positions = position_store.list_all(
            trading_mode=settings.trading_mode,
            strategy_version=settings.strategy_version,
        )
        latest_prices = _fetch_latest_prices(
            adapter=app.state.market_data_adapter,
            positions=pre_positions,
        )
        snapshot = load_dashboard_snapshot(
            settings=settings,
            connection=connection,
            position_store=position_store,
            order_store=order_store,
            audit_event_store=audit_event_store,
            trading_status_store=_build_store(app.state.trading_status_store_factory, connection),
            daily_session_state_store=_build_store(app.state.daily_session_state_store_factory, connection),
            strategy_flag_store=_build_store(app.state.strategy_flag_store_factory, connection),
            latest_prices=latest_prices,
        )
        metrics = load_metrics_snapshot(
            settings=settings,
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

### 3c — Add `_fetch_latest_prices()` helper

```python
def _fetch_latest_prices(*, adapter: object | None, positions: list) -> dict[str, float]:
    if adapter is None or not positions:
        return {}
    symbols = list({p.symbol for p in positions})
    try:
        return adapter.get_latest_prices(symbols)
    except Exception:
        import logging
        logging.getLogger(__name__).warning("Failed to fetch latest prices; degrading gracefully", exc_info=True)
        return {}
```

## Task 4 — Dashboard template

**File:** `src/alpaca_bot/web/templates/dashboard.html`

### 4a — Add 3 columns to the Open Positions table header

After the `Updated` column header (`<th>Updated</th>`), add:

```html
<th>Last</th>
<th>Unreal P&L $</th>
<th>Unreal P&L %</th>
```

### 4b — Add 3 cells to each position row

After the existing `updated_at` cell, add (using Jinja2):

```html
{% set last_price = snapshot.latest_prices.get(position.symbol) %}
<td>{{ format_price(last_price) if last_price is not none else "—" }}</td>
{% if last_price is not none %}
  {% set upnl = (last_price - position.entry_price) * position.quantity %}
  {% set upnl_pct = (last_price - position.entry_price) / position.entry_price * 100 %}
  <td style="color: {{ 'var(--accent)' if upnl >= 0 else '#c0392b' }}">
    {{ "$%.2f"|format(upnl) if upnl >= 0 else "-$%.2f"|format(upnl|abs) }}
  </td>
  <td style="color: {{ 'var(--accent)' if upnl_pct >= 0 else '#c0392b' }}">
    {{ "%.2f%%"|format(upnl_pct) }}
  </td>
{% else %}
  <td>—</td>
  <td>—</td>
{% endif %}
```

### 4c — Update `colspan`

Change `colspan="11"` to `colspan="14"` on the "No open positions" empty state row.

## Task 5 — Tests

### 5a — `tests/unit/test_alpaca_market_data.py`

Add `get_latest_stock_trades` to `HistoricalClientStub`:

```python
class HistoricalClientStub:
    def __init__(self) -> None:
        self.requests: list[object] = []
        self.latest_trades: dict[str, object] = {}  # ADD

    def get_stock_bars(self, request_params: object) -> BarSetStub: ...  # unchanged

    def get_latest_stock_trades(self, request_params: object) -> object:  # ADD
        self.requests.append(request_params)
        return SimpleNamespace(data={
            symbol: SimpleNamespace(price=trade.price)
            for symbol, trade in self.latest_trades.items()
        })
```

New tests:

```python
def test_get_latest_prices_returns_prices_for_symbols() -> None:
    stub = HistoricalClientStub()
    stub.latest_trades = {
        "AAPL": SimpleNamespace(price=175.50),
        "MSFT": SimpleNamespace(price=420.00),
    }
    adapter = AlpacaMarketDataAdapter(historical_client=stub)
    prices = adapter.get_latest_prices(["AAPL", "MSFT"])
    assert prices == {"AAPL": 175.50, "MSFT": 420.00}


def test_get_latest_prices_empty_symbols_returns_empty_dict() -> None:
    stub = HistoricalClientStub()
    adapter = AlpacaMarketDataAdapter(historical_client=stub)
    prices = adapter.get_latest_prices([])
    assert prices == {}
    assert stub.requests == []  # no network call


def test_get_latest_prices_missing_symbol_skipped() -> None:
    stub = HistoricalClientStub()
    stub.latest_trades = {"AAPL": SimpleNamespace(price=100.0)}
    adapter = AlpacaMarketDataAdapter(historical_client=stub)
    prices = adapter.get_latest_prices(["AAPL", "TSLA"])
    assert prices == {"AAPL": 100.0}
    # TSLA not in response — not in result
```

### 5b — `tests/unit/test_web_service.py`

```python
def test_load_dashboard_snapshot_includes_latest_prices() -> None:
    settings = make_settings()
    stores = make_snapshot_stores()
    snapshot = load_dashboard_snapshot(
        settings=settings,
        connection=SimpleNamespace(commit=lambda: None),
        latest_prices={"AAPL": 175.50},
        **stores,
    )
    assert snapshot.latest_prices == {"AAPL": 175.50}


def test_load_dashboard_snapshot_latest_prices_defaults_to_empty() -> None:
    settings = make_settings()
    stores = make_snapshot_stores()
    snapshot = load_dashboard_snapshot(
        settings=settings,
        connection=SimpleNamespace(commit=lambda: None),
        **stores,
    )
    assert snapshot.latest_prices == {}
```

### 5c — `tests/unit/test_web_app.py`

Add a `FakeMarketDataAdapter` stub with `get_latest_prices()` and test:

```python
class FakeMarketDataAdapter:
    def __init__(self, prices: dict[str, float]) -> None:
        self._prices = prices
        self.calls: list[list[str]] = []

    def get_latest_prices(self, symbols: list[str]) -> dict[str, float]:
        self.calls.append(symbols)
        return {s: self._prices[s] for s in symbols if s in self._prices}


def test_dashboard_includes_live_prices_when_adapter_present(...) -> None:
    # positions=[AAPL], adapter returns {AAPL: 175.50}
    # snapshot.latest_prices == {"AAPL": 175.50}


def test_dashboard_degrades_gracefully_when_adapter_raises(...) -> None:
    # adapter raises Exception → snapshot.latest_prices == {}
    # response still 200


def test_dashboard_skips_price_fetch_when_no_adapter(...) -> None:
    # no adapter → snapshot.latest_prices == {}
```

## Test command

```bash
pytest tests/unit/test_alpaca_market_data.py tests/unit/test_web_service.py tests/unit/test_web_app.py -v
pytest  # full suite
```

## Files changed

| File | Change |
|------|--------|
| `src/alpaca_bot/execution/alpaca.py` | Protocol + `get_latest_prices()` + 2 helpers |
| `src/alpaca_bot/web/service.py` | `latest_prices` on `DashboardSnapshot`; kwarg on `load_dashboard_snapshot()` |
| `src/alpaca_bot/web/app.py` | `market_data_adapter` param; `_fetch_latest_prices()` helper; refactored `_load_dashboard_data()` |
| `src/alpaca_bot/web/templates/dashboard.html` | 3 new columns; colspan 11→14 |
| `tests/unit/test_alpaca_market_data.py` | 3 new tests for `get_latest_prices()` |
| `tests/unit/test_web_service.py` | 2 new passthrough tests |
| `tests/unit/test_web_app.py` | 3 new integration tests for adapter injection |

No production code changes outside the listed files. No new migrations. No new env vars.
