# Alpaca API Reference For This Bot

Researched on 2026-05-09 from Alpaca's official docs and Alpaca-py SDK docs.

This is a project-local memory of the Alpaca API facts that matter to `alpaca_bot`.
It is not a complete Alpaca reference. Prefer the source links at the end when a
behavior looks surprising or when changing broker/data code.

## Code Touchpoints

- `src/alpaca_bot/execution/alpaca.py`
  - `AlpacaExecutionAdapter`: account, clock, calendar, open orders, positions,
    order submit/replace/cancel, asset fractionability.
  - `AlpacaMarketDataAdapter`: stock bars, latest trades, news, latest quotes.
  - `AlpacaPortfolioReader`: reads `get_all_positions()` for broker-side current
    prices, useful after hours when IEX latest trades can lag/freeze.
  - `AlpacaTradingStreamAdapter`: subscribes to trade updates.
- `src/alpaca_bot/execution/option_chain.py`
  - `AlpacaOptionChainAdapter`: option chain snapshots with latest quote and
    greeks, currently using the free `indicative` feed.
- `src/alpaca_bot/runtime/order_dispatch.py`
  - Converts pending entry orders to Alpaca orders; uses extended-hours limit
    orders when session type is pre-market or after-hours.
- `src/alpaca_bot/runtime/cycle_intent_execution.py`
  - Direct stop updates, exits, and broker resync behavior.
- `src/alpaca_bot/runtime/trade_updates.py`
  - Consumes trade update stream events to reconcile fills.

## Authentication And Environments

- Trading API uses different hosts and separate credentials:
  - Paper: `https://paper-api.alpaca.markets`
  - Live: `https://api.alpaca.markets`
  - Market data for both uses `https://data.alpaca.markets`
- This repo's `resolve_alpaca_credentials()` maps:
  - `TRADING_MODE=paper` -> `ALPACA_PAPER_API_KEY` and `ALPACA_PAPER_SECRET_KEY`
  - `TRADING_MODE=live` -> `ALPACA_LIVE_API_KEY` and `ALPACA_LIVE_SECRET_KEY`
- Do not mix paper and live credentials. Alpaca explicitly treats them as
  different credentials for different Trading API domains.
- Trading and market data requests authenticate with `APCA-API-KEY-ID` and
  `APCA-API-SECRET-KEY`; Alpaca-py handles the headers when clients are built
  with `api_key` and `secret_key`.

## Trading API Notes

- `TradingClient(api_key, secret_key, paper=True|False)` is Alpaca-py's client
  for both paper and live trading.
- `get_account()` exposes account state such as buying power and
  `trading_blocked`; this bot normalizes `equity`, `buying_power`, and
  `trading_blocked`.
- `get_all_positions()` returns open positions. Alpaca says position values such
  as market value are updated live while the position is open; closed positions
  are no longer returned by this endpoint.
- `get_orders(status="open", limit=500)` should keep `limit=500`. Alpaca's
  orders endpoint defaults to 50 and has a documented max of 500. This project
  has already had production issues from silently missing open orders past the
  default page size.
- `get_clock()` returns current market timestamp, whether the market is open,
  and next open/close. `get_calendar(start, end)` returns market days plus open
  and close times, including early closes.
- `get_asset(symbol)` is the source for asset capability flags. This bot checks
  `fractionable` before treating a symbol as eligible for fractional sizing.

## Order Rules That Matter Here

- Alpaca supports market, limit, stop, and stop-limit orders for equities.
- Stop orders:
  - require `stop_price`;
  - sell stops become market orders when elected;
  - stop orders do not guarantee the final fill price.
- Stop-limit orders require both `stop_price` and `limit_price`; after election,
  the order is a limit order and can remain unfilled.
- Sub-penny validation is strict:
  - prices >= $1.00: max 2 decimals;
  - prices < $1.00: max 4 decimals.
  Round generated stop and limit prices before order submission.
- Extended-hours equity orders:
  - Alpaca's current docs include overnight, pre-market, and after-hours trading.
  - The order must be `limit` with `extended_hours=true`.
  - Alpaca docs currently allow `time_in_force=day` or `gtc`; this bot uses
    `day`, which remains valid.
  - Non-extended-hours-eligible orders submitted after regular close are queued
    for the next trading day.
- Fractional equities:
  - Fractional orders are supported for market, limit, stop, and stop-limit with
    `time_in_force=day`.
  - Check the asset's `fractionable` flag first; Alpaca rejects non-fractionable
    assets.
  - Fractional short sales are not supported.
- Trade state should be maintained from the `trade_updates` stream where
  possible. Alpaca's order docs describe streaming as the recommended way to
  maintain order state.

## Options Trading Rules

- Options trading uses the same `/v2/orders` API as equities.
- Paper options are enabled by default in Alpaca docs; live options require the
  account to be approved for the needed options trading level.
- Alpaca validates options orders differently from equities:
  - `qty` must be a whole number;
  - `notional` must not be populated;
  - `extended_hours` must be false or omitted;
  - valid order types currently include `market`, `limit`, `stop`, and
    `stop_limit`; stop and stop-limit are for single-leg orders;
  - current docs allow `time_in_force=day` or `gtc`.
- This bot's current options path uses whole-contract quantities, `DAY` time in
  force, limit entries, market exits, and no extended-hours flag. That matches
  the conservative subset of Alpaca's rules.
- The option chain endpoint returns snapshots with latest trade, latest quote,
  and greeks for each contract. The feed can be:
  - `opra`: official OPRA feed, subscription-gated;
  - `indicative`: free indicative feed where trades are delayed and quotes are
    modified.
- `AlpacaOptionChainAdapter` currently requests `feed="indicative"` and filters
  DTE/delta after parsing. If broad chains look truncated, re-check Alpaca-py's
  `OptionChainRequest` behavior against the REST endpoint, which documents
  `limit` and `page_token`.

## Market Data Notes

- Stock historical data uses `StockHistoricalDataClient`.
- This bot's bars requests explicitly pass `settings.market_data_feed`:
  - `MARKET_DATA_FEED=iex`: free, single-exchange data; Alpaca describes IEX as
    useful for testing or when precise pricing is not primary.
  - `MARKET_DATA_FEED=sip`: full US exchange consolidated feed; requires the
    right market data subscription for recent/latest data.
- Alpaca says paper-only accounts are entitled to IEX market data. Keep
  production paper defaults on `iex` unless a paid plan is confirmed.
- Latest stock trades and latest quotes default to the best feed available to
  the authenticated account. Recent/latest SIP queries without the subscription
  can return subscription errors.
- Latest stock trades exclude trade conditions that do not update bar price, such
  as odd lots. Do not assume latest trade equals every last tape print.
- Bars are aggregated from trades. Minute bars are timestamped at the left edge
  of the interval; daily bars are grouped by the New York trading day.
- For real-time price-sensitive logic, Alpaca recommends market data streams over
  polling latest historical endpoints. This bot intentionally runs a 60-second
  polling cycle, so be conservative around stale extended-hours signals.

## Rate Limits And Retry Posture

- Alpaca's support docs state Trading/Paper Trading API calls are throttled at
  200 requests per minute per account. Current reference pages document `429`
  responses and `X-RateLimit-*` headers.
- `src/alpaca_bot/execution/alpaca.py` retries transient Alpaca failures:
  - `429`, rate-limit text, and "too many";
  - standalone 5xx status codes;
  - connection, timeout, and OS-level errors.
- Retry shape is currently 3 attempts with 1s then 2s backoff. Do not add broad
  symbol-by-symbol Alpaca calls inside the cycle without checking total request
  count against the 60-second supervisor cadence.
- Existing market-data bars are chunked at 200 symbols. Keep batch endpoints and
  chunked calls where Alpaca supports them.

## Useful Source Links

- Alpaca authentication: https://docs.alpaca.markets/docs/authentication
- Paper trading environment: https://docs.alpaca.markets/docs/trading/paper-trading/
- Alpaca-py `TradingClient`: https://alpaca.markets/sdks/python/api_reference/trading/trading-client.html
- Alpaca-py trading reference: https://alpaca.markets/sdks/python/api_reference/trading_api.html
- Alpaca-py stock data reference: https://alpaca.markets/sdks/python/api_reference/data/stock.html
- Alpaca-py stock historical client: https://alpaca.markets/sdks/python/api_reference/data/stock/historical.html
- Alpaca-py stock request models: https://alpaca.markets/sdks/python/api_reference/data/stock/requests.html
- Alpaca-py option data reference: https://alpaca.markets/sdks/python/api_reference/data/option.html
- Alpaca-py option request models: https://alpaca.markets/sdks/python/api_reference/data/option/requests.html
- Orders at Alpaca: https://docs.alpaca.markets/docs/orders-at-alpaca
- Get all orders: https://docs.alpaca.markets/reference/getallorders-1
- Account guide: https://docs.alpaca.markets/docs/working-with-account
- Positions guide: https://docs.alpaca.markets/v1.3/docs/working-with-positions
- All open positions reference: https://docs.alpaca.markets/reference/getallopenpositions
- Get asset by symbol: https://docs.alpaca.markets/reference/get-v2-assets-symbol_or_asset_id
- US market clock: https://docs.alpaca.markets/reference/legacyclock
- US market calendar: https://docs.alpaca.markets/reference/legacycalendar
- Market data FAQ: https://docs.alpaca.markets/docs/market-data-faq
- Historical stock data guide: https://docs.alpaca.markets/v1.3/docs/historical-stock-data-1
- Historical bars reference: https://docs.alpaca.markets/reference/stockbars
- Latest stock trades reference: https://docs.alpaca.markets/reference/stocklatesttrades-1
- Latest stock quotes reference: https://docs.alpaca.markets/v1.3/reference/stocklatestquotes
- Real-time stock data: https://docs.alpaca.markets/docs/real-time-stock-pricing-data
- Trading WebSocket stream: https://docs.alpaca.markets/docs/websocket-streaming
- Market data WebSocket stream: https://docs.alpaca.markets/docs/streaming-market-data
- Options trading: https://docs.alpaca.markets/docs/options-trading
- Options trading overview: https://docs.alpaca.markets/docs/options-trading-overview
- Options orders examples: https://docs.alpaca.markets/v1.3/docs/options-orders
- Option chain reference: https://docs.alpaca.markets/reference/optionchain
- Rate limit support note: https://alpaca.markets/support/usage-limit-api-calls
