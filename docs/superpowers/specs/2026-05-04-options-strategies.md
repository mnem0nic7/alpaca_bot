# Options Strategies Design Spec

**Date:** 2026-05-04

## Goal

Enable the bot to buy call option contracts on breakout signals, providing defined-risk leveraged exposure to the same breakout patterns that drive equity entries. The maximum loss on any options trade is the premium paid (intrinsically defined risk).

## Scope

**In scope (v1):**
- Long call buying only (no puts, no spreads, no multi-leg)
- `breakout_calls` strategy: runs existing breakout detection on underlying, selects a call contract, produces an option ENTRY intent
- Premium-based position sizing (contracts scaled to `risk_per_trade_pct` budget, capped by `max_position_pct`)
- EOD flatten via market sell (same timing as equity flatten)
- New `option_orders` DB table (separate from equity `orders` — no pollution of existing schema)
- Live/paper trading only — no backtest/replay support in v1

**Out of scope (v1):**
- Puts, spreads, collars, covered calls
- Intraday option price stops (option is held until EOD or manual exit)
- Historical options data for backtesting
- Options in the nightly evolve pipeline
- Greeks-based position sizing (delta-adjusted)

---

## Architecture

### Component Map

```
Supervisor
  ├── fetch equity bars           (existing)
  ├── fetch option chains         (NEW: AlpacaOptionChainAdapter)
  ├── evaluate_cycle(             (extend: add option_chains_by_symbol=)
  │     ..., option_chains_by_symbol
  │   ) → CycleResult
  │         intents may now include is_option=True entries
  ├── run_cycle()                 (extend: writes ENTRY intents for options to option_orders)
  ├── dispatch_pending_orders()   (existing, equity unchanged)
  ├── dispatch_pending_option_orders()  (NEW)
  ├── option EOD flatten          (NEW: supervisor generates sell intents past flatten time)
  └── trade_stream                (extend: route option fills to OptionOrderRepository)
```

### New files

| File | Purpose |
|---|---|
| `src/alpaca_bot/execution/option_chain.py` | `OptionChainAdapter` protocol + `AlpacaOptionChainAdapter` implementation |
| `src/alpaca_bot/strategy/breakout_calls.py` | `breakout_calls` strategy: runs breakout signal, selects call contract |
| `src/alpaca_bot/strategy/option_selector.py` | `select_call_contract()` pure function |
| `src/alpaca_bot/risk/option_sizing.py` | `calculate_option_position_size()` |
| `src/alpaca_bot/runtime/option_dispatch.py` | `dispatch_pending_option_orders()` |
| `migrations/012_add_option_orders.sql` | New `option_orders` table |

### Modified files

| File | Change |
|---|---|
| `src/alpaca_bot/domain/models.py` | Add `OptionContract` frozen dataclass |
| `src/alpaca_bot/core/engine.py` | Add `option_chains_by_symbol` param; add `underlying_symbol` + `is_option` to `CycleIntent` |
| `src/alpaca_bot/storage/models.py` | Add `OptionOrderRecord` frozen dataclass |
| `src/alpaca_bot/storage/repositories.py` | Add `OptionOrderRepository` |
| `src/alpaca_bot/execution/alpaca.py` | Add `submit_option_limit_entry()`, `submit_option_market_exit()` to `AlpacaExecutionAdapter` |
| `src/alpaca_bot/runtime/supervisor.py` | Fetch option chains; block underlying duplicates; trigger EOD flatten for options |
| `src/alpaca_bot/runtime/trade_stream.py` | Route option fills to `OptionOrderRepository` |
| `src/alpaca_bot/strategy/__init__.py` | Register `breakout_calls`; add `OPTION_STRATEGY_NAMES` |
| `src/alpaca_bot/config/__init__.py` | Add `OPTION_DTE_MIN`, `OPTION_DTE_MAX`, `OPTION_DELTA_TARGET` to `Settings` |

---

## Data Flow

### Entry

1. Supervisor calls `AlpacaOptionChainAdapter.get_option_chain(symbol, settings)` for each symbol in `settings.symbols` if `strategy_name in OPTION_STRATEGY_NAMES`.
2. Returns `list[OptionContract]` per symbol — call contracts within `[OPTION_DTE_MIN, OPTION_DTE_MAX]` days to expiry, with bid/ask and delta (if available).
3. `evaluate_cycle(..., option_chains_by_symbol={"AAPL": [...]})` is called.
4. `breakout_calls` strategy evaluator: runs existing breakout detection on equity bars. If breakout signal fires, calls `select_call_contract(chains["AAPL"], current_price=bars[-1].close, settings)` → selects contract closest to `OPTION_DELTA_TARGET` delta (or ATM by strike as fallback).
5. Returns a modified `EntrySignal` where `limit_price = contract.ask`, `stop_price = None`, `initial_stop_price = None`.
6. `evaluate_cycle` calls `calculate_option_position_size(equity, contract.ask, settings)` → number of contracts.
7. Emits `CycleIntent(ENTRY, symbol=contract.occ_symbol, underlying_symbol="AAPL", is_option=True, quantity=contracts, limit_price=contract.ask)`.
8. Supervisor `run_cycle()` writes `OptionOrderRecord(status="pending_submit")` to `option_orders`.
9. `dispatch_pending_option_orders()` submits a `LimitOrderRequest(symbol=occ_symbol, qty=contracts, limit_price=ask)` to Alpaca.

### EOD Flatten

1. Supervisor detects it is past `settings.flatten_time` for the session.
2. Loads open option positions: `OptionOrderRepository.list_open_option_positions()` → filled buys with no corresponding filled sell.
3. For each open option position, writes `OptionOrderRecord(side="sell", status="pending_submit")` to `option_orders`.
4. `dispatch_pending_option_orders()` submits `MarketOrderRequest(symbol=occ_symbol, qty=filled_qty, side=sell)` to Alpaca.

### Fill Routing

1. Alpaca trade stream sends fill event with `client_order_id`.
2. Trade stream handler checks: if `client_order_id` starts with `"option:"` → routes to `OptionOrderRepository.update_fill()`.
3. Otherwise → existing equity fill routing (unchanged).

---

## Domain Types

### `OptionContract` (new, `domain/models.py`)

```python
@dataclass(frozen=True)
class OptionContract:
    occ_symbol: str        # OCC format: "AAPL241220C00150000"
    underlying: str        # equity ticker: "AAPL"
    option_type: str       # "call" or "put"
    strike: float
    expiry: date
    bid: float
    ask: float
    delta: float | None = None
```

### `CycleIntent` additions (two new optional fields)

```python
underlying_symbol: str | None = None   # set when is_option=True
is_option: bool = False
```

`symbol` field holds the OCC contract symbol when `is_option=True`.

### `OptionOrderRecord` (new, `storage/models.py`)

```python
@dataclass(frozen=True)
class OptionOrderRecord:
    client_order_id: str          # prefix "option:" for stream routing
    occ_symbol: str
    underlying_symbol: str
    option_type: str               # "call" or "put"
    strike: float
    expiry: date
    side: str                      # "buy" or "sell"
    status: str
    quantity: int
    trading_mode: TradingMode
    strategy_version: str
    strategy_name: str = "breakout_calls"
    created_at: datetime = ...
    updated_at: datetime = ...
    limit_price: float | None = None
    broker_order_id: str | None = None
    fill_price: float | None = None
    filled_quantity: int | None = None
```

---

## DB Schema

```sql
CREATE TABLE IF NOT EXISTS option_orders (
    client_order_id TEXT PRIMARY KEY,
    occ_symbol TEXT NOT NULL,
    underlying_symbol TEXT NOT NULL,
    option_type TEXT NOT NULL CHECK (option_type IN ('call', 'put')),
    strike DOUBLE PRECISION NOT NULL,
    expiry DATE NOT NULL,
    side TEXT NOT NULL CHECK (side IN ('buy', 'sell')),
    status TEXT NOT NULL,
    quantity INTEGER NOT NULL CHECK (quantity >= 0),
    trading_mode TEXT NOT NULL CHECK (trading_mode IN ('paper', 'live')),
    strategy_version TEXT NOT NULL,
    strategy_name TEXT NOT NULL DEFAULT 'breakout_calls',
    limit_price DOUBLE PRECISION,
    broker_order_id TEXT,
    fill_price DOUBLE PRECISION,
    filled_quantity INTEGER,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_option_orders_underlying_status
    ON option_orders (underlying_symbol, status);

CREATE INDEX IF NOT EXISTS idx_option_orders_broker_order_id
    ON option_orders (broker_order_id)
    WHERE broker_order_id IS NOT NULL;
```

---

## Settings Additions

| Env var | Type | Default | Meaning |
|---|---|---|---|
| `OPTION_DTE_MIN` | int | 21 | Minimum days-to-expiry for contract selection |
| `OPTION_DTE_MAX` | int | 60 | Maximum days-to-expiry for contract selection |
| `OPTION_DELTA_TARGET` | float | 0.50 | Target delta for contract selection (0.0–1.0) |

All three are optional. They are only used when `STRATEGY=breakout_calls`. Validated in `Settings.from_env()`:
- `OPTION_DTE_MIN >= 1`
- `OPTION_DTE_MAX > OPTION_DTE_MIN`
- `0.0 < OPTION_DELTA_TARGET <= 1.0`

---

## Position Sizing

```
contract_cost = ask × 100          (each contract = 100 shares)
risk_budget = equity × risk_per_trade_pct
contracts = floor(risk_budget / contract_cost)
max_notional = equity × max_position_pct
max_contracts = floor(max_notional / contract_cost)
result = max(0, min(contracts, max_contracts))
```

Risk is **defined**: maximum loss per trade = `contracts × ask × 100`. No stop-loss needed.

---

## Pure Engine Boundary

`evaluate_cycle()` remains a pure function. Option chain data is fetched by the supervisor before the call and passed as a new optional parameter `option_chains_by_symbol: Mapping[str, Sequence[OptionContract]] | None = None`. The strategy evaluator reads from this mapping (no I/O). If the mapping is `None`, `breakout_calls` returns `None` (no signal without chain data).

---

## Deduplication

The supervisor loads open option position underlying symbols from `OptionOrderRepository.list_open_option_positions()` and adds them to `working_order_symbols` before calling `evaluate_cycle()`. This prevents entering both an equity position AND an option position on the same underlying in the same cycle.

---

## Audit Trail

All option orders follow the same write-before-dispatch pattern: `OptionOrderRecord(status="pending_submit")` is committed to `option_orders` before any Alpaca API call. On dispatch failure, the record stays in `pending_submit` and is retried next cycle.

---

## Safety Gates

- `ENABLE_LIVE_TRADING=false` blocks live order submission (unchanged — all order submission goes through `AlpacaExecutionAdapter` which checks this gate).
- `TRADING_MODE=paper` uses Alpaca paper trading API, which supports options. All option order tests can run in paper mode.
- Market hours: option entries are blocked by `is_entry_session_time()` (same session guard as equity). EOD flatten runs past `flatten_time` (same logic).
- Options cannot be submitted extended-hours (no `extended_hours=True` for option orders — Alpaca doesn't support extended-hours options).

---

## Non-Goals and Deferred Work

- **Backtest/replay for options**: Historical options chain data requires a separate data provider. Not in scope.
- **Nightly evolve pipeline for options**: Can't optimize option parameters without backtesting. Deferred.
- **Option price stops**: Intraday stop-on-premium-loss requires fetching option quotes every cycle. Added complexity deferred to v2.
- **Puts**: Require a bearish signal source the current system doesn't have.
- **Multi-leg strategies**: Out of scope entirely for v1.
