# Market Context Filters Design

**Date:** 2026-05-09  
**Goal:** Add three new entry filters (VIX regime gate, sector ETF breadth gate, VWAP-relative entry gate) to reduce bad trades and improve data richness for ML training.

---

## 1. Architecture Overview

| Layer | What changes |
|---|---|
| `domain/models.py` | Add `MarketContext` frozen dataclass |
| `domain/decision_record.py` | Add 5 new optional fields |
| `config/__init__.py` | Add 8 new `Settings` fields with env vars |
| `execution/alpaca.py` | No change — reuses existing `get_stock_bars()` |
| `storage/repositories.py` | 2 new migrations + `MarketContextStore` repo class |
| `core/engine.py` | 3 new filter gates; `market_context` param added |
| `runtime/supervisor.py` | Fetch context bars; compute; save; pass to engine |
| `strategy/market_context.py` | New pure function `compute_market_context()` |
| `migrations/018_add_market_context.sql` | New table |
| `migrations/019_add_decision_log_context_columns.sql` | Enrich decision_log |

All three filters default to disabled — no behavior change until explicitly enabled via env vars.

---

## 2. Domain Types and Settings

### `MarketContext` frozen dataclass (`domain/models.py`)

```python
@dataclass(frozen=True)
class MarketContext:
    as_of: datetime
    vix_close: float | None = None
    vix_sma: float | None = None
    vix_above_sma: bool | None = None   # True = elevated fear = block entries
    sector_etf_states: dict[str, bool] = field(default_factory=dict)  # symbol -> above_sma
    sector_passing_pct: float | None = None  # fraction of sectors passing (0.0–1.0)
```

### New `Settings` fields (`config/__init__.py`)

| Env var | Field name | Type | Default | Purpose |
|---|---|---|---|---|
| `ENABLE_VIX_FILTER` | `enable_vix_filter` | `bool` | `False` | Gate all entries on VIX regime |
| `VIX_PROXY_SYMBOL` | `vix_proxy_symbol` | `str` | `"VIXY"` | ETF used as VIX proxy |
| `VIX_LOOKBACK_BARS` | `vix_lookback_bars` | `int` | `20` | SMA period for VIX |
| `ENABLE_SECTOR_FILTER` | `enable_sector_filter` | `bool` | `False` | Gate all entries on sector breadth |
| `SECTOR_ETF_SYMBOLS` | `sector_etf_symbols` | `tuple[str,...]` | `("XLK","XLF","XLE","XLV","XLU","XLI","XLB","XLRE","XLC","XLY","XLP")` | 11 sector ETFs |
| `SECTOR_ETF_SMA_PERIOD` | `sector_etf_sma_period` | `int` | `20` | SMA period per sector ETF |
| `SECTOR_FILTER_MIN_PASSING_PCT` | `sector_filter_min_passing_pct` | `float` | `0.5` | Min fraction above SMA to allow entries |
| `ENABLE_VWAP_ENTRY_FILTER` | `enable_vwap_entry_filter` | `bool` | `False` | Per-symbol: reject entries when close < VWAP |

`SECTOR_ETF_SYMBOLS` is parsed from a comma-separated string in `from_env()`.

### New `DecisionRecord` fields (`domain/decision_record.py`)

All 5 new fields are added to the frozen dataclass with `= None` defaults so existing construction sites in `engine.py` do not need positional updates:

| Field | Type | Default | Populated when |
|---|---|---|---|
| `vix_close` | `float \| None` | `None` | VIX filter enabled |
| `vix_above_sma` | `bool \| None` | `None` | VIX filter enabled |
| `sector_passing_pct` | `float \| None` | `None` | Sector filter enabled |
| `vwap_at_signal` | `float \| None` | `None` | VWAP filter enabled |
| `signal_bar_above_vwap` | `bool \| None` | `None` | VWAP filter enabled |

Existing DB rows get NULLs — no constraint violation. All `DecisionRecord(...)` construction sites in `engine.py` must be updated to pass these 5 keyword arguments explicitly (even as `None`) so the dataclass is fully explicit at every callsite.

`filter_results` dict extended with `"vix"`, `"sector"`, `"vwap"` keys when the respective filter is enabled.

---

## 3. Storage Schema and Repositories

### Migration 018 — new `market_context` table

File: `migrations/018_add_market_context.sql`

```sql
CREATE TABLE market_context (
    id SERIAL PRIMARY KEY,
    as_of TIMESTAMPTZ NOT NULL,
    trading_mode VARCHAR(10) NOT NULL,
    vix_close FLOAT,
    vix_sma FLOAT,
    vix_above_sma BOOLEAN,
    sector_etf_states JSONB,
    sector_passing_pct FLOAT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX ON market_context (as_of, trading_mode);
```

One row per cycle. `sector_etf_states` stored as JSONB (`{"XLK": true, "XLF": false, ...}`).

### Migration 019 — enrich `decision_log`

File: `migrations/019_add_decision_log_context_columns.sql`

```sql
ALTER TABLE decision_log
    ADD COLUMN vix_close FLOAT,
    ADD COLUMN vix_above_sma BOOLEAN,
    ADD COLUMN sector_passing_pct FLOAT,
    ADD COLUMN vwap_at_signal FLOAT,
    ADD COLUMN signal_bar_above_vwap BOOLEAN;
```

`ADD COLUMN` without `NOT NULL` — zero downtime migration, existing rows get NULLs.

### `MarketContextStore` (new class in `storage/repositories.py`)

```python
class MarketContextStore:
    def __init__(self, conn): ...

    def save(self, ctx: MarketContext, trading_mode: str) -> None:
        # INSERT INTO market_context (...) VALUES (...)
```

`save()` is a **synchronous** method — consistent with all existing repository classes in `repositories.py` which call `execute()` directly without `await`. The supervisor calls it without `await`.

### `DecisionLogStore.bulk_insert()` changes

Extend the existing SQL and params tuple to include 5 new columns. NULLs are passed when the corresponding filter is disabled.

---

## 4. Engine Changes (`core/engine.py`)

### New `market_context` parameter

```python
def evaluate_cycle(
    *,
    ...existing params...,
    market_context: MarketContext | None = None,
) -> CycleResult:
```

Defaults to `None` — safe when filter is disabled or fetch failed.

### Two new pre-loop gates (block ALL entries)

Placed immediately after the existing `regime_filter` gate, before the per-symbol loop:

```python
# VIX gate
if (
    settings.enable_vix_filter
    and market_context is not None
    and market_context.vix_above_sma is True
):
    vix_blocked = True

# Sector breadth gate
if (
    settings.enable_sector_filter
    and market_context is not None
    and market_context.sector_passing_pct is not None
    and market_context.sector_passing_pct < settings.sector_filter_min_passing_pct
):
    sector_blocked = True
```

Both gates fail-open: if `market_context` is `None`, entries proceed normally.

### One new per-symbol gate (inside the symbol loop)

Placed alongside existing per-symbol filters (news, spread), after `evaluate_breakout_signal()`:

```python
if settings.enable_vwap_entry_filter:
    session_vwap = calculate_vwap(intraday_bars_by_symbol[symbol])
    vwap_at_signal = session_vwap  # None when volume is zero
    if session_vwap is None:
        signal_bar_above_vwap = None  # fail-open: insufficient data, allow entry
    else:
        signal_bar_above_vwap = signal.signal_bar.close >= session_vwap
        if not signal_bar_above_vwap:
            # record rejection in DecisionRecord; continue
```

`calculate_vwap()` returns `float | None` (None when total volume is zero). Fail-open: a None VWAP does not block the entry. `calculate_vwap()` already imported in engine — no new imports needed.

### `CycleResult` additions

Two new fields:

```python
vix_blocked: bool = False
sector_blocked: bool = False
```

Same pattern as existing `news_blocked_symbols` and `spread_blocked_symbols`.

### `DecisionRecord` stamping

When building each `DecisionRecord`, stamp the 5 new fields from `market_context` (or `None` if disabled). The `filter_results` dict gets `"vix"`, `"sector"`, `"vwap"` keys, all gated on `settings.enable_*` flags.

---

## 5. Supervisor Integration (`runtime/supervisor.py`)

### New fetch block in `run_cycle_once()`

Placed after existing regime/news/quote fetches, before `evaluate_cycle()` call:

```python
market_context: MarketContext | None = None

if settings.enable_vix_filter or settings.enable_sector_filter:
    context_symbols = []
    if settings.enable_vix_filter:
        context_symbols.append(settings.vix_proxy_symbol)
    if settings.enable_sector_filter:
        context_symbols.extend(settings.sector_etf_symbols)

    context_bars = await market_data.get_daily_bars(
        context_symbols,
        lookback_days=max(settings.vix_lookback_bars, settings.sector_etf_sma_period) + 5,
    )
    market_context = compute_market_context(context_bars, settings, now)
```

One additional `get_daily_bars()` batch call — all symbols fetched together. Fits within the 200 req/min Alpaca rate limit.

### `compute_market_context()` — new pure function

New module: `src/alpaca_bot/strategy/market_context.py`

```python
def compute_market_context(
    bars_by_symbol: dict[str, list[Bar]],
    settings: Settings,
    as_of: datetime,
) -> MarketContext:
```

Pure function (no I/O) that computes VIX SMA comparison and sector breadth from fetched bars. Returns a `MarketContext` with all fields populated (or `None` values for unavailable data). Fails gracefully: insufficient history → returns `MarketContext(as_of=as_of)` with all filter fields `None` (fail-open).

### DB write after compute

```python
if market_context is not None:
    market_context_store.save(market_context, settings.trading_mode)  # synchronous
```

Written every cycle when either filter is enabled — feeds the training dataset.

### Pass into engine

```python
result = evaluate_cycle(
    ...existing args...,
    market_context=market_context,
)
```

### Logging

Structured log lines when gates block entries:

```
market_context: vix_above_sma=True — all entries blocked (VIX gate)
market_context: sector_passing_pct=0.36 — all entries blocked (sector gate, threshold=0.50)
```

---

## 6. Key Design Decisions

- **VIXY not VIX**: Alpaca stock bars work with ETFs, not volatility indices. VIXY is configurable via `VIX_PROXY_SYMBOL`.
- **Fail-open on missing context**: If `market_context` is `None` or filter fields are `None` (insufficient history, fetch error), entries proceed. Never block on missing data.
- **No symbol-to-sector mapping**: Sector gate uses market breadth (fraction of sector ETFs above SMA), not per-symbol sector lookup. Simpler, no external data needed.
- **Batch fetch**: VIX proxy + all sector ETFs fetched in a single `get_daily_bars()` call per cycle.
- **Both tables**: `market_context` table captures per-cycle time series; enriched `decision_log` rows capture per-signal snapshot. Both feed the `tuning/surrogate.py` ML pipeline.
- **All filters default disabled**: No behavior change on deploy until env vars explicitly enable them.
