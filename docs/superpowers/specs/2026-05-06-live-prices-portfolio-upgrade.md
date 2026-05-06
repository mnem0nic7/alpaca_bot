---
title: Live Dashboard Prices — Portfolio Reader Upgrade
date: 2026-05-06
status: approved
---

# Live Dashboard Prices — Portfolio Reader Upgrade

## Problem

The dashboard's **Last**, **Curr Val**, and **Unreal P&L** columns freeze at the 4 pm ET close because `AlpacaMarketDataAdapter.get_latest_prices()` uses `StockLatestTradeRequest` with the IEX data feed. IEX only reports regular-session trades; after-hours and pre-market trades are absent from the response. The Alpaca mobile app and broker dashboard show current after-hours prices because they read from the trading API's `get_all_positions()` endpoint, which reflects portfolio valuation continuously.

Confirmed discrepancy (2026-05-06 evening):
- ACVA: IEX last $5.19 vs Alpaca portfolio $5.58 (14 shares → $5.46 vs $7.84 delta on Unreal P&L)
- CDNA: IEX last $20.93 vs Alpaca portfolio $21.50

## Goals

1. Dashboard **Last** / **Curr Val** / **Unreal P&L** columns show after-hours prices that match the Alpaca broker.
2. No new env vars, no config changes, no migrations.
3. Fall back gracefully — if the portfolio reader fails, use `market_data_adapter.get_latest_prices()` (IEX) as before. If that also fails, show `—`.
4. `evaluate_cycle()` remains pure — no changes to trading logic.
5. The existing `market_data_adapter` is still initialized; it continues to serve bar data and quotes for the trading engine.

## Non-goals

- Removing `market_data_adapter` from the web app.
- Streaming / real-time price updates (page-load only, existing auto-refresh remains).
- After-hours price data for the trading engine (engine uses bars; no change).

## Approach

### New class — `AlpacaPortfolioReader` (execution/alpaca.py)

A small read-only wrapper around `TradingClientProtocol`:

```python
class AlpacaPortfolioReader:
    def __init__(self, trading_client: TradingClientProtocol) -> None: ...

    @classmethod
    def from_settings(cls, settings: Settings) -> "AlpacaPortfolioReader": ...

    def get_current_prices(self, symbols: Sequence[str]) -> dict[str, float]: ...
```

`get_current_prices` calls `_retry_with_backoff(self._trading.get_all_positions)`, filters to the requested symbols, and returns `{symbol: float(position.current_price)}`. Positions with a missing or non-numeric `current_price` attribute are skipped. Returns `{}` when `symbols` is empty.

`from_settings` reuses `resolve_alpaca_credentials` + `_build_trading_client` (already exists on `AlpacaExecutionAdapter`) — specifically, it calls `resolve_alpaca_credentials(settings)` and constructs a `TradingClient(api_key, secret_key, paper=paper)` directly, so `AlpacaPortfolioReader` has no dependency on `AlpacaExecutionAdapter`.

### `web/app.py` — wiring

Add `portfolio_reader: object | None = None` parameter to `create_app()` (alongside the existing `market_data_adapter`).

At startup (inside `create_app`), if `portfolio_reader is None`:
```python
try:
    from alpaca_bot.execution.alpaca import AlpacaPortfolioReader
    portfolio_reader = AlpacaPortfolioReader.from_settings(app_settings)
except Exception:
    portfolio_reader = None
```
Store on `app.state.portfolio_reader`.

Update `_fetch_latest_prices`:
```python
def _fetch_latest_prices(
    *,
    portfolio_reader: object | None,
    adapter: object | None,
    positions: list,
) -> dict[str, float]:
```

Logic:
1. If no positions → return `{}`.
2. symbols = all distinct symbols from positions.
3. If `portfolio_reader` is not None: try `portfolio_reader.get_current_prices(symbols)`. On success, find `missing = symbols - result.keys()`. If `missing` is empty, return result. Otherwise merge with fallback for missing symbols.
4. If `portfolio_reader` is None or raised, fall through to `adapter`.
5. If `adapter` is not None: try `adapter.get_latest_prices(remaining_symbols)`. Merge into result.
6. Return merged result (or `{}` on total failure).

The `_load_dashboard_data` call site passes both:
```python
latest_prices = _fetch_latest_prices(
    portfolio_reader=app.state.portfolio_reader,
    adapter=app.state.market_data_adapter,
    positions=pre_positions,
)
```

### Protocol extension

`TradingClientProtocol.get_all_positions` already returns `list[Any]` (line 117 of `execution/alpaca.py`). No protocol change needed.

## Error handling

| Failure scenario | Behaviour |
|---|---|
| Portfolio reader raises (network, auth) | Log warning, fall through to market data adapter |
| Market data adapter raises (network, IEX down) | Log warning, return `{}` for all symbols |
| Both fail | `{}` — columns show `—` (existing behaviour) |
| Position missing `current_price` field | Skip that symbol, fall back to adapter |
| `current_price` not numeric | Skip that symbol, fall back to adapter |

## Testing

`tests/unit/test_alpaca_order_execution.py` — new tests for `AlpacaPortfolioReader`:
- `test_portfolio_reader_returns_current_price_for_requested_symbols` — stub `get_all_positions` returns two positions, only one symbol requested, correct `float` returned.
- `test_portfolio_reader_returns_empty_when_symbols_is_empty` — no API call made.
- `test_portfolio_reader_skips_position_with_missing_current_price` — position has no `current_price` attr; symbol absent from result.
- `test_portfolio_reader_skips_position_with_nonnumeric_current_price` — `current_price = "N/A"`; symbol absent from result.

Web app integration tested via existing `tests/unit/test_web_app.py` (or new test in same file):
- `test_fetch_latest_prices_uses_portfolio_reader_when_available` — portfolio reader returns prices for all symbols; adapter not called.
- `test_fetch_latest_prices_falls_back_to_adapter_when_portfolio_reader_raises` — reader raises; adapter called; adapter result returned.
- `test_fetch_latest_prices_merges_reader_and_adapter_for_missing_symbols` — reader returns subset; adapter called for remainder; results merged.

## Files changed

| File | Change |
|------|--------|
| `src/alpaca_bot/execution/alpaca.py` | Add `AlpacaPortfolioReader` class |
| `src/alpaca_bot/web/app.py` | Add `portfolio_reader` param, update `_fetch_latest_prices`, init at startup |
| `tests/unit/test_alpaca_order_execution.py` | 4 unit tests for `AlpacaPortfolioReader` |
| `tests/unit/test_web_app.py` | 3 integration tests for `_fetch_latest_prices` behaviour |
