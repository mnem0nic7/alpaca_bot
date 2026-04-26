# Alpaca Trading Bot: Strategy & Architecture Plan

## 1. Strategy decisions to make first

Before any code, lock these down. Every architecture choice downstream depends on them.

### 1.1 Asset class

Alpaca supports stocks, crypto (24/7), and options. Each has different implications:

- **Stocks**: PDT rule applies under $25k, market hours only, simplest data model
- **Crypto**: 24/7 markets, no PDT, no settlement, but lower liquidity and wider spreads
- **Options**: Complex pricing (greeks), expiration management, multi-leg orders, much harder to backtest correctly

If you're learning the system, start with **stocks on liquid large-caps** or **BTC/ETH** to avoid edge cases.

### 1.2 Strategy family

Pick one to start. Trying to build a "general framework" before you have one working strategy is a classic trap.

| Family | Holding period | Edge source | Data needs | Difficulty |
|---|---|---|---|---|
| Momentum / trend | Hours to weeks | Persistence of returns | Daily or hourly bars | Low |
| Mean reversion | Minutes to days | Overreaction snap-back | Minute bars, sometimes order book | Medium |
| Pairs / stat arb | Hours to days | Cointegration between assets | Synchronized minute bars | Medium |
| Event-driven | Variable | Earnings, news, FOMC reactions | Bars + news/calendar feed | Medium |
| ML / signal blending | Variable | Model edge over heuristics | Years of clean features | High |
| HFT / market making | Sub-second | Latency and queue position | L2 order book | Don't (Alpaca isn't built for this) |

For a first build, **a momentum or mean-reversion swing strategy on a curated stock universe** is the most forgiving: minute-bar data is enough, you don't fight latency, and the math is tractable. Move to ML signal blending only after the base infrastructure is rock solid.

### 1.3 Universe selection

Hard-code or filter? Most retail bots benefit from a fixed universe (S&P 100, top-50 liquid stocks, top-20 crypto pairs) refreshed weekly. Dynamic universes pull in low-liquidity garbage and create reproducibility nightmares for backtests. Filter rules to consider:

- ADV (average daily volume) above some threshold
- Spread below some threshold
- Price above $5 (avoid penny stocks)
- Tradable=True and shortable=True via Alpaca's Assets endpoint

### 1.4 Position sizing

Pick one and document why:

- **Fixed fractional**: 1-2% of equity per position, simple and robust
- **Volatility-targeted**: position size = target_vol / asset_vol, normalizes risk across assets
- **Kelly fractional**: 0.25 × Kelly is a sane upper bound; full Kelly will blow you up
- **Equal weight** across N positions: trivially simple, fine for a small universe

Volatility-targeted is the right default for a portfolio of more than one symbol.

### 1.5 Risk management (the part that actually keeps you alive)

These are not optional:

- **Per-trade stop loss**: hard stop at N × ATR or fixed % below entry
- **Per-position max size**: cap at 5-10% of equity
- **Portfolio max gross exposure**: e.g., 100-150% (no leverage early)
- **Daily loss killswitch**: if equity drops X% in a session, flatten and stop trading until manual reset
- **Max consecutive losses**: pause after N losing trades for human review
- **PDT compliance**: track day trades over rolling 5 days if account < $25k

The killswitch is the single most important component. It lives in the risk manager and overrides every signal.

---

## 2. Architecture: layer responsibilities

### 2.1 Data ingestion layer

**Job**: get prices in, normalize them, hand them off.

- Subscribe to Alpaca's `StockDataStream` (or `CryptoDataStream`) for real-time bars/trades/quotes
- Use REST historical endpoints for backfills and warm-up indicators
- Normalize all timestamps to UTC immediately; timezone bugs are a top cause of "why is my backtest different from live" pain
- Gap detection: if a bar is missing, log and either backfill or skip the cycle (don't silently feed stale data to the strategy)
- Subscribe to `TradingStream` for order/fill updates (separate from market data)

### 2.2 Storage layer

**Job**: durable, queryable history of everything.

Two tiers:

- **Hot**: in-memory deque of recent bars per symbol (last N for indicator calculation), plus a cache for the current portfolio state
- **Cold**: persistent store of all bars, all signals generated, all orders, all fills, all account snapshots

For cold storage, you have three reasonable options:

| Option | Pros | Cons |
|---|---|---|
| **TimescaleDB** (Postgres extension) | SQL, time-bucket queries, joins with other tables | Heavier ops, needs a server |
| **DuckDB + Parquet** | Zero ops, fast analytical queries, great for research | Single-writer, less great for concurrent live writes |
| **Plain SQLite** | Trivial setup | Slow on large datasets, locking issues |

Given your existing Cloud Run + Postgres stack from dnd-booker, **TimescaleDB on the same Postgres instance** is the path of least resistance. Reuse Prisma if you want, or drop to raw SQL for time-series queries (Prisma handles time-series poorly).

### 2.3 Strategy engine

**Job**: take bars in, emit signals out. No knowledge of orders, accounts, or money.

Strategy interface should look something like:

```python
class Strategy(Protocol):
    def warm_up(self, history: pd.DataFrame) -> None: ...
    def on_bar(self, bar: Bar, state: StrategyState) -> list[Signal]: ...
```

A `Signal` is `{symbol, side, strength, reason, timestamp}` — never a dollar amount or share count. Sizing is the risk manager's job. This separation is what makes the same strategy code work in backtesting and live trading without modification.

Indicator computation: use **pandas-ta** or **TA-Lib** for canonical indicators. Don't roll your own RSI; you'll get a subtle off-by-one and waste a weekend.

### 2.4 Risk manager

**Job**: turn signals into sized orders, or reject them.

Inputs: signal, current portfolio state, account equity, configured risk limits.
Outputs: a sized `OrderIntent` or a rejection with reason logged.

This is where the killswitch lives. It is checked **before every order**, not on a timer. If equity is below threshold or daily loss exceeded, every signal is rejected and an alert fires.

### 2.5 Order manager

**Job**: translate `OrderIntent` to Alpaca order, track its lifecycle, reconcile fills.

- Use `MarketOrderRequest`, `LimitOrderRequest`, etc. from alpaca-py (pydantic models, validate on construction)
- Submit via `TradingClient.submit_order`
- Subscribe to `TradingStream` for fill events; update portfolio state on fill, not on submit
- Maintain idempotency: every internal order has a `client_order_id` (UUID) so retries don't double-submit
- Reconciliation loop: every N seconds, compare your portfolio state to Alpaca's `get_all_positions()` and alert on drift

### 2.6 Portfolio state

**Job**: single source of truth for what you own, what you're worth, what you've made.

Updated by fills (from `TradingStream`), not by your own optimistic guesses. Persisted to DB after every update so a crash doesn't lose state.

### 2.7 Backtester

**Job**: run the same strategy code over historical data and produce a performance report.

The key design rule: **the strategy class is identical between backtest and live**. The backtester is just a different driver that feeds bars in chronologically and simulates fills with realistic slippage and commission models.

Two paths:

- **Build your own**: ~300 lines of Python, fully controlled, matches your live system exactly. Recommended.
- **Use vectorbt or backtrader**: faster to start, but you'll fight the framework when your strategy doesn't fit its assumptions. Vectorbt is excellent for parameter sweeps but assumes vectorizable strategies.

Slippage model to start: assume fills happen at the next bar's open with a fixed bps cost. Refine later.

### 2.8 Observability

**Job**: tell you when something's wrong without you having to look.

- **Structured logs** (JSON) for every signal, order, fill, error → ship to a log aggregator or just rotate to disk
- **Metrics**: equity curve, position count, signal rate, fill latency, API error rate → Prometheus or just a Postgres table you graph in Grafana
- **Alerts**: Discord webhook, Telegram bot, or email for: killswitch triggered, order rejected by Alpaca, unexpected position drift, websocket disconnect lasting > N seconds

Treat this as P0, not an afterthought. The first time your bot does something weird at 2am, you'll be glad you can answer "what happened" without re-running the strategy in your head.

---

## 3. Tech stack

### Core

- **Python 3.11+** (pattern matching, better error messages, faster)
- **alpaca-py** (current official SDK; `alpaca-trade-api` is deprecated)
- **pandas + numpy** for data manipulation
- **pandas-ta** or **TA-Lib** for indicators
- **pydantic** for config and data models (alpaca-py already uses it)
- **httpx** if you need any direct REST calls beyond the SDK

### Async and scheduling

- **asyncio** as the concurrency primitive (alpaca-py's streaming clients are async)
- **APScheduler** for cron-style jobs (universe refresh, end-of-day reconciliation, daily reports)

### Storage

- **PostgreSQL + TimescaleDB extension** for bars, signals, orders, fills
- **SQLAlchemy** (Core, not ORM) or **asyncpg** for DB access
- **Alembic** for migrations

### Backtesting / research

- **DuckDB** for ad-hoc analytical queries against historical Parquet files
- **Jupyter** for research notebooks
- **matplotlib + plotly** for equity curves, drawdown charts

### Ops

- **Docker + docker-compose** for local dev
- **systemd** or a simple supervisor for the live process if running on a VPS
- **Cloud Run** is fine for the dashboard but **not** for the live trading process — Cloud Run scales to zero and your bot needs to stay up. Use Compute Engine, a small VPS, or your home server with monitoring
- **GitHub Actions** for CI: run the test suite and a backtest smoke-test on every PR

### Secrets

- API keys in environment variables, never in code
- Separate `.env.paper` and `.env.live` files (or use a real secrets manager)
- A `--paper` / `--live` CLI flag that's required (no default) so you can never accidentally run live

---

## 4. Repository structure

```
dnd-trader/  (or whatever you name it)
├── pyproject.toml
├── README.md
├── docker-compose.yml
├── .env.example
├── alembic/                    # DB migrations
├── config/
│   ├── strategies/
│   │   └── momentum_v1.yaml    # strategy params
│   ├── universe.yaml           # symbol list
│   └── risk.yaml               # risk limits
├── src/
│   └── trader/
│       ├── __init__.py
│       ├── main.py             # entry point
│       ├── config.py           # pydantic settings
│       ├── data/
│       │   ├── ingestion.py    # WebSocket subscriber
│       │   ├── store.py        # DB read/write
│       │   └── models.py       # Bar, Quote, etc.
│       ├── strategy/
│       │   ├── base.py         # Strategy protocol
│       │   ├── momentum.py
│       │   └── mean_reversion.py
│       ├── risk/
│       │   ├── manager.py      # main RiskManager class
│       │   ├── sizing.py       # position sizing functions
│       │   └── killswitch.py
│       ├── execution/
│       │   ├── order_manager.py
│       │   └── reconciler.py
│       ├── portfolio/
│       │   └── state.py
│       ├── backtest/
│       │   ├── engine.py       # event-driven backtester
│       │   └── reports.py
│       ├── observability/
│       │   ├── logging.py
│       │   ├── metrics.py
│       │   └── alerts.py
│       └── alpaca/
│           ├── clients.py      # client factories
│           └── streams.py      # stream wrappers
├── tests/
│   ├── unit/
│   ├── integration/            # against paper account
│   └── fixtures/               # sample bars for replay
├── notebooks/
│   ├── universe_research.ipynb
│   └── strategy_tuning.ipynb
└── scripts/
    ├── backfill_history.py
    ├── run_backtest.py
    └── run_live.py
```

The thing to defend: **strategies, risk, and execution stay in separate modules with no cross-imports**. The strategy doesn't know what an Alpaca order looks like. The order manager doesn't know what RSI is. This is what lets you swap any layer.

---

## 5. Implementation roadmap

Don't try to do this in parallel. Each phase produces something you can run and verify.

### Phase 1: Skeleton (1-2 weekends)

- Repo, dependencies, config loading, logging
- Connect to Alpaca paper account, fetch account info, list a few historical bars
- Schema + migrations for bars, orders, signals
- A "hello world" main loop that subscribes to one symbol's bars and writes them to DB
- **Done when**: you can run `python -m trader.main --paper` and see bars stream in for 30 minutes without errors

### Phase 2: One strategy end-to-end (1-2 weekends)

- Implement one simple strategy (e.g., 20/50 SMA crossover on SPY)
- Implement risk manager with fixed-fractional sizing and a hard daily loss killswitch
- Implement order manager that submits market orders and tracks fills via TradingStream
- Reconciliation: log a warning if internal positions drift from Alpaca's
- **Done when**: paper account makes trades for a full week with no manual intervention and end-of-week P&L matches your DB-derived P&L

### Phase 3: Backtester + observability (1-2 weekends)

- Event-driven backtester that runs the same strategy code as live
- Slippage and commission models
- Daily report: equity curve, trade list, drawdown stats, Sharpe/Sortino
- Discord webhook for killswitch trips, websocket disconnects, big P&L moves
- **Done when**: backtest of last 6 months produces results you can sanity-check, and you get a Discord ping if you yank the network cable mid-run

### Phase 4: Multi-strategy and research (open-ended)

- Strategy registry so multiple strategies can run simultaneously with separate sub-portfolios
- Parameter sweep harness using vectorbt or your own backtester
- Universe refresh job (weekly cron)
- Walk-forward validation framework

### Phase 5: Live (only when paper is solid for 4+ weeks)

- Start with capital you are emotionally fine losing
- Reduce all sizing parameters by 50% on first live deploy
- Run live and paper in parallel for at least 2 weeks; investigate any divergence
- Live capital scales up only after live results match paper within reasonable execution drag

---

## 6. Operational landmines

Things that will hurt if you don't plan for them.

### 6.1 PDT rule

If your stock account is under $25k and you make 4+ day trades in 5 business days, you get flagged and restricted. The risk manager should track day trades and refuse new ones at 3 in a 5-day window if equity < $25k. Crypto doesn't have this.

### 6.2 Websocket disconnects

They will happen. Your stream client must auto-reconnect with backoff, and on reconnect you must **backfill the gap** via REST so the strategy sees a continuous bar series. Many bots silently miss bars during reconnects and the strategy decisions go subtly wrong.

### 6.3 Time-of-day handling

Market open and close have very different microstructure than mid-day. A strategy tuned on mid-day data may fail in the open-auction chaos. Either (a) skip the first/last 15 minutes, or (b) explicitly model open/close as a feature.

### 6.4 Look-ahead bias in backtests

The single most common backtest bug: using a bar's close to make a decision that you then "execute" at the same bar's close. In live trading you can only act on bar N+1's open or later. Enforce this in the backtester by passing the strategy bar N and only allowing it to fill at bar N+1.

### 6.5 Survivorship bias

If your universe is "current S&P 500 constituents", your backtest is fictional. Use a point-in-time index membership dataset, or accept the bias and discount your backtest Sharpe accordingly (rule of thumb: backtest Sharpe → divide by 2 for a sane live expectation).

### 6.6 Tax and compliance

Wash sales, short-term vs long-term cap gains, and 1099 reporting will become a problem at year end. Log every trade with enough detail (lot, basis, holding period) that you or your accountant can reconstruct it. Alpaca provides 1099s but you want your own records.

### 6.7 Idempotency on restart

If the bot crashes mid-order-submit, on restart it must not re-submit the same order. The `client_order_id` UUID per intent solves this — Alpaca will reject duplicates with the same client_order_id. Always set it.

### 6.8 The killswitch must be testable

Write an integration test where you simulate a 5% equity drop and assert that the next signal is rejected and an alert fires. Untested killswitches don't trigger when you need them.

---

## 7. What "done" looks like for v1

A reasonable v1 milestone:

- One strategy running on paper for 4+ weeks
- Backtest results within 10% of paper results (realistic discrepancy due to slippage)
- Killswitch tested and proven
- Reconciliation has caught at least one issue (or you've manually injected one to verify it works)
- You can answer in under 5 minutes: "what trades did the bot make yesterday and why?"
- A bus-factor doc: if you got hit by a bus, someone else can stop the bot

Then, and only then, consider live capital.

---

## 8. Open questions for you

To narrow this further, decisions to make:

1. **Asset class**: stocks, crypto, or both?
2. **Strategy family for v1**: momentum, mean reversion, pairs, or something else?
3. **Where does it run**: existing Cloud Run-adjacent infra, dedicated VPS, home server?
4. **How does it integrate with the existing stack**: standalone repo, or does it share the Postgres + observability you've built for dnd-booker?
5. **Capital target for live phase**: this affects whether PDT is relevant and how much risk machinery you need

Answer those and the architecture above collapses to a much smaller, more concrete build.
