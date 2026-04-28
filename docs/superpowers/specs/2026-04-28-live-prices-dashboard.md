---
title: Live Prices on Dashboard (StockLatestTradeRequest)
date: 2026-04-28
status: approved
---

# Live Prices on Dashboard — StockLatestTradeRequest

## Problem

The Open Positions table on the dashboard shows entry price, stop price, and risk $ but has no view of current market value. An operator cannot see unrealized P&L without opening a broker UI. This is a friction point and a precursor to supporting longer-holding strategies that are not exited at market close.

## Goals

1. Show **Last Price**, **Unreal P&L $**, and **Unreal P&L %** for each open position on the dashboard.
2. Fetch live prices through the existing `AlpacaMarketDataAdapter` using `StockLatestTradeRequest`.
3. Keep `service.py` Alpaca-free — dashboard snapshot loads prices as an injected `dict[str, float]`, not from the adapter directly.
4. Degrade gracefully when Alpaca is unreachable — show `—` for price columns rather than failing the page.
5. Keep `evaluate_cycle()` pure — no changes to trading logic.
6. Design so that future data sources (NewsClient, OptionHistoricalDataClient) can be added as separate adapters wired at `create_app()` time.

## Non-goals

- Polling / real-time streaming of prices (the page refreshes on load only).
- Auto-refresh for prices (handled by the existing dashboard auto-refresh mechanism).
- NewsClient or other market data types (future work).
- Any change to trading logic, order submission, or position sizing.

## Approach

### Layer 1 — `AlpacaMarketDataAdapter` (execution/alpaca.py)

Extend `HistoricalDataClientProtocol` with a `get_latest_stock_trades()` method. Add `get_latest_prices(symbols: Sequence[str]) -> dict[str, float]` to `AlpacaMarketDataAdapter`. It constructs a `StockLatestTradeRequest`, calls `_retry_with_backoff`, and returns `{symbol: price}`. Returns `{}` when `symbols` is empty. Falls back to `{}` (not raises) when `alpaca-py` is not importable.

### Layer 2 — `DashboardSnapshot` + `load_dashboard_snapshot()` (web/service.py)

Add `latest_prices: dict[str, float]` field (default `{}`) to `DashboardSnapshot`. Add `latest_prices: dict[str, float] | None = None` kwarg to `load_dashboard_snapshot()`. The function passes the provided dict (or empty dict) straight into the snapshot — it does **not** call any adapter.

### Layer 3 — `app.py` wiring

Add `market_data_adapter` optional parameter to `create_app()`. Store it at `app.state.market_data_adapter`. In `_load_dashboard_data()`: if the adapter is present and positions is non-empty, call `adapter.get_latest_prices(symbols)`, catch all exceptions (log a warning, use `{}`), and pass into `load_dashboard_snapshot()`.

### Layer 4 — Dashboard template

Add 3 new columns after `Updated`: **Last**, **Unreal P&L $**, **Unreal P&L %**. When `latest_prices` has no entry for a symbol, render `—` for all three. Update `colspan` from 11 to 14.

### Extensibility note

Future adapters (NewsClient, etc.) will be added as new `Optional[object]` parameters to `create_app()` following the same pattern: auto-built from settings when `None`, stored on `app.state`, injected as fakes in tests. No "adapter registry" is needed at this scale.

## Design constraints resolved during grilling

- **uvicorn factory mode**: `cli.py` calls `uvicorn.run(..., factory=True)` which invokes `create_app()` with no args. The adapter must be auto-constructed inside `create_app()` with a try/except fallback to `None` — it cannot be injected from outside the process.
- **Double position fetch**: `_load_dashboard_data()` pre-fetches positions to build the symbols list, then passes `position_store` to `load_dashboard_snapshot()` which re-fetches. Two cheap DB reads — accepted as simpler than refactoring `load_dashboard_snapshot`.
- **Market hours**: `StockLatestTradeRequest` returns the last recorded trade regardless of market state. Works outside trading hours.
- **Credentials**: `AlpacaMarketDataAdapter.from_settings()` respects `settings.trading_mode`, so paper vs live is handled automatically.

## Files changed

| File | Change |
|------|--------|
| `src/alpaca_bot/execution/alpaca.py` | Extend protocol + add `get_latest_prices()` |
| `src/alpaca_bot/web/service.py` | `latest_prices` field on `DashboardSnapshot`; optional param on `load_dashboard_snapshot()` |
| `src/alpaca_bot/web/app.py` | Wire `market_data_adapter` in `create_app()` and `_load_dashboard_data()` |
| `src/alpaca_bot/web/templates/dashboard.html` | 3 new columns: Last, Unreal P&L $, Unreal P&L % |
| `tests/unit/test_alpaca_market_data.py` | Tests for `get_latest_prices()` |
| `tests/unit/test_web_service.py` | Test `latest_prices` field passthrough |
| `tests/unit/test_web_app.py` | Tests for adapter injection, graceful degradation |
