# Data Source Filters Design

**Date:** 2026-05-01  
**Status:** Approved  
**Author:** Claude (autonomous planning)

---

## Problem Statement

The current trading engine evaluates entry signals using only intraday OHLCV bars and daily OHLCV bars. It has no awareness of:

1. **Market regime**: Is the broad market trending or collapsing? Entries during a SPY downtrend have lower expected value.
2. **Catalyst risk**: Is a symbol in play due to earnings, FDA decisions, or other high-volatility news? Entries into catalyst-driven moves are unpredictable.
3. **Spread / liquidity**: Is the NBBO spread so wide that the theoretical edge is consumed by transaction cost?

These three blind spots allow entries in conditions where the breakout strategy's edge is empirically weaker. Adding filters (all default-off) gives operators a tunable mechanism to improve the signal-to-noise ratio.

---

## Architecture Decision: Engine-Level Gates, Not Signal Evaluators

The `StrategySignalEvaluator` Protocol (11 implementations) evaluates **signal logic** per symbol. The 3 new filters are **pre-conditions for entry** — they guard the entry block, not the signal logic.

This means:
- The 11 `StrategySignalEvaluator` implementations remain **unchanged**
- New data is fetched **upstream** in `supervisor.py` and passed into `evaluate_cycle()` as new kwargs
- `evaluate_cycle()` checks filters **before** calling `signal_evaluator` per symbol
- All 3 filter parameters default to `None` / empty — existing callers (tests, replay) require **no changes**

This mirrors the existing `daily_bars_by_symbol` pattern: fetch upstream, pass in, use as a gate.

---

## Filter 1: Market Regime (SPY SMA)

**Purpose**: Block new entries when the broad market is in a downtrend (SPY < SMA(N)).

**Implementation**:
- New `Settings` fields: `enable_regime_filter: bool = False`, `regime_symbol: str = "SPY"`, `regime_sma_period: int = 20`
- `supervisor.py` fetches daily bars for `settings.regime_symbol` when `enable_regime_filter=True`
- `evaluate_cycle()` receives `regime_bars: Sequence[Bar] | None = None`
- Before the entry loop, if `enable_regime_filter` and `regime_bars` is not None and len sufficient: call existing `daily_trend_filter_passes(bars=regime_bars, sma_period=settings.regime_sma_period)`. If False: skip all entries for this cycle.
- **Fail-open**: if `regime_bars` is None (fetch failed), log warning, skip regime check, allow entries.

**VIX note**: VIX cannot be fetched via `StockHistoricalDataClient` (equity only). SPY is sufficient and already fetched by the same client.

---

## Filter 2: News / Catalyst Awareness

**Purpose**: Skip entry on a symbol if it has had catalyst-type headlines in the past N hours.

**Implementation**:
- New `Settings` fields: `enable_news_filter: bool = False`, `news_filter_lookback_hours: int = 24`, `news_filter_keywords: tuple[str, ...] = ("earnings", "revenue", "fda", "clinical", "trial", "guidance")`
- `AlpacaMarketDataAdapter.get_news(symbols, lookback_hours)` method — uses `alpaca.data.historical.news.NewsClient`
- `supervisor.py` calls `get_news()` when `enable_news_filter=True`; catches exceptions and uses `{}` (fail-open)
- `evaluate_cycle()` receives `news_by_symbol: Mapping[str, Sequence[NewsItem]] | None = None`
- Per-symbol gate: if symbol has any `NewsItem` with a keyword match in headline (case-insensitive): skip entry for that symbol this cycle
- New domain type `NewsItem(symbol, headline, published_at)` in `domain/models.py`

**Fail-open**: if `news_by_symbol` is None, skip news check per symbol.

---

## Filter 3: Spread / Liquidity (NBBO)

**Purpose**: Skip entry on a symbol if the current NBBO spread exceeds `max_spread_pct`.

**Implementation**:
- New `Settings` fields: `enable_spread_filter: bool = False`, `max_spread_pct: float = 0.002`
- `AlpacaMarketDataAdapter.get_latest_quotes(symbols)` method — uses existing `self._historical.get_stock_latest_quote()`
- `supervisor.py` calls `get_latest_quotes()` when `enable_spread_filter=True`; catches exceptions and uses `{}` (fail-open)
- `evaluate_cycle()` receives `quotes_by_symbol: Mapping[str, Quote] | None = None`
- Per-symbol gate: if `Quote.spread_pct > settings.max_spread_pct`: skip entry for that symbol this cycle
- New domain type `Quote(symbol, bid_price, ask_price)` with `spread_pct` property in `domain/models.py`

**Fail-open**: if `quotes_by_symbol` is None or symbol not in it, skip spread check.

---

## New Domain Types

```python
@dataclass(frozen=True)
class NewsItem:
    symbol: str
    headline: str
    published_at: datetime

@dataclass(frozen=True)
class Quote:
    symbol: str
    bid_price: float
    ask_price: float

    @property
    def spread_pct(self) -> float:
        if self.ask_price <= 0:
            return 0.0
        return (self.ask_price - self.bid_price) / self.ask_price
```

---

## New Settings Fields

```python
# Regime filter
enable_regime_filter: bool = False
regime_symbol: str = "SPY"
regime_sma_period: int = 20

# News / catalyst filter
enable_news_filter: bool = False
news_filter_lookback_hours: int = 24
news_filter_keywords: tuple[str, ...] = (
    "earnings", "revenue", "fda", "clinical", "trial", "guidance"
)

# Spread filter
enable_spread_filter: bool = False
max_spread_pct: float = 0.002
```

All default to off/safe values. `news_filter_keywords` is parsed from `NEWS_FILTER_KEYWORDS` env var (comma-separated). `regime_sma_period` validated: must be `>= 2`.

---

## New `evaluate_cycle()` Parameters

```python
regime_bars: Sequence[Bar] | None = None,
news_by_symbol: Mapping[str, Sequence[NewsItem]] | None = None,
quotes_by_symbol: Mapping[str, Quote] | None = None,
```

All default to `None`, making them backward-compatible: existing callers (tests, replay) pass nothing and all filters are bypassed.

---

## Files to Modify

| File | Change |
|---|---|
| `src/alpaca_bot/config/__init__.py` | Add 8 new Settings fields; parse `NEWS_FILTER_KEYWORDS`; validate `regime_sma_period >= 2` |
| `src/alpaca_bot/domain/models.py` | Add `NewsItem`, `Quote` dataclasses |
| `src/alpaca_bot/domain/__init__.py` | Export `NewsItem`, `Quote` |
| `src/alpaca_bot/execution/alpaca.py` | Add `get_news()`, `get_latest_quotes()` methods; add `_build_news_client()` |
| `src/alpaca_bot/core/engine.py` | Add 3 new parameters to `evaluate_cycle()`; add 3 filter gates |
| `src/alpaca_bot/runtime/cycle.py` | Thread 3 new params through `run_cycle()` → `evaluate_cycle()` |
| `src/alpaca_bot/runtime/supervisor.py` | Fetch regime bars, news, quotes upstream; pass to `_cycle_runner` |

---

## New Files

| File | Purpose |
|---|---|
| `tests/unit/test_data_source_filters.py` | Unit tests: regime gate blocks entries, news gate skips symbol, spread gate skips symbol, fail-open for all 3, default-off |

---

## Safety Properties

- All 3 features default `False` — zero behavioral change in existing deployments
- Fail-open: bad data → filter bypassed, trading continues normally
- No changes to `StrategySignalEvaluator` Protocol — all 11 strategies unaffected
- `evaluate_cycle()` remains a pure function (no I/O added)
- `ENABLE_LIVE_TRADING=false` gate is upstream of these filters — unchanged

---

## Out of Scope

- Level 2 order book (not available in `StockHistoricalDataClient`)
- VIX (not an equity; cannot be fetched via equity data client)
- News sentiment scoring (keyword matching is sufficient for catalyst-risk avoidance)
- Historical backtesting of the 3 filters (covered by the replay framework separately)
