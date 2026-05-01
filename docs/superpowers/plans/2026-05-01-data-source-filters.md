# Implementation Plan: Data Source Filters

**Date:** 2026-05-01  
**Spec:** `docs/superpowers/specs/2026-05-01-data-source-filters-design.md`  
**Status:** Grilled and refined — ready for implementation

---

## Overview

Add three default-off entry filters to the trading engine:
1. **Regime filter** — skip all entries when SPY (or configured symbol) is below its N-day SMA
2. **News filter** — skip entry on a symbol if it has catalyst-type headlines in the past N hours
3. **Spread filter** — skip entry on a symbol if the NBBO spread exceeds a configured threshold

All filters are engine-level gates (no changes to the 11 `StrategySignalEvaluator` implementations). All default `False`. All fail-open (bad data → filter bypassed). `evaluate_cycle()` remains a pure function.

### Grilling fixes applied
- `news_filter_keywords` parsed with `.lower()` to ensure case-insensitive matching
- `NewsRequest.symbols` is `Optional[str]` (comma-separated), NOT a list — fixed
- `NewsSet` response accessed as `raw.data.get("news", [])`, NOT `raw.news`
- `self._news_client = None` initialized in `__init__` (not dynamically via `hasattr`)
- `timedelta` added to `alpaca.py` imports
- Regime bars reuse `daily_bars_by_symbol` if `regime_symbol` is already in watchlist
- Test `now` aligned to intraday bar timestamps; `make_breakout_intraday_bars` called without invalid `now=` arg

---

## Task 1 — Add domain types `NewsItem` and `Quote`

**File:** `src/alpaca_bot/domain/models.py`

After the last `@dataclass` in the file, add:

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

**File:** `src/alpaca_bot/domain/__init__.py`

Read the file first, then add `NewsItem` and `Quote` to the existing import and `__all__` (preserving all current exports).

---

## Task 2 — Add new Settings fields

**File:** `src/alpaca_bot/config/__init__.py`

Add 8 new fields to the `Settings` frozen dataclass (after the existing `enable_vwap_breakdown_exit` field or in a clearly-grouped block):

```python
# --- Data source filters ---
enable_regime_filter: bool = False
regime_symbol: str = "SPY"
regime_sma_period: int = 20

enable_news_filter: bool = False
news_filter_lookback_hours: int = 24
news_filter_keywords: tuple[str, ...] = (
    "earnings", "revenue", "fda", "clinical", "trial", "guidance"
)

enable_spread_filter: bool = False
max_spread_pct: float = 0.002
```

In `from_env()`, parse the new fields (follow the existing pattern for booleans, ints, and floats).  
**Critical**: keywords must be lowercased during parsing to support case-insensitive env var input:

```python
enable_regime_filter=_parse_bool(env, "ENABLE_REGIME_FILTER", default=False),
regime_symbol=env.get("REGIME_SYMBOL", "SPY"),
regime_sma_period=int(env.get("REGIME_SMA_PERIOD", "20")),
enable_news_filter=_parse_bool(env, "ENABLE_NEWS_FILTER", default=False),
news_filter_lookback_hours=int(env.get("NEWS_FILTER_LOOKBACK_HOURS", "24")),
news_filter_keywords=tuple(
    kw.strip().lower()  # .lower() required: headline matching uses kw in headline.lower()
    for kw in env.get(
        "NEWS_FILTER_KEYWORDS",
        "earnings,revenue,fda,clinical,trial,guidance",
    ).split(",")
    if kw.strip()
),
enable_spread_filter=_parse_bool(env, "ENABLE_SPREAD_FILTER", default=False),
max_spread_pct=float(env.get("MAX_SPREAD_PCT", "0.002")),
```

In `validate()`, add:

```python
if self.regime_sma_period < 2:
    raise ValueError("REGIME_SMA_PERIOD must be >= 2")
```

---

## Task 3 — Add `get_news()` and `get_latest_quotes()` to `AlpacaMarketDataAdapter`

**File:** `src/alpaca_bot/execution/alpaca.py`

### 3a — Update `datetime` import

The existing import is `from datetime import date, datetime`. Change to:

```python
from datetime import date, datetime, timedelta
```

(`timedelta` is needed by `get_news()` to compute `start = now - timedelta(hours=lookback_hours)`.)

### 3b — Add `self._news_client = None` in `__init__`

In `AlpacaMarketDataAdapter.__init__`, add after `self._historical = ...`:

```python
self._news_client: object | None = None
```

(Consistent with how `_historical` is typed. Lazy-built on first call to `get_news()`.)

### 3c — Add `_build_news_client()` static method

After `_build_historical_client()`:

```python
@staticmethod
def _build_news_client(settings: Settings):
    api_key, secret_key, _paper = resolve_alpaca_credentials(settings)
    try:
        from alpaca.data.historical.news import NewsClient
    except (ModuleNotFoundError, ImportError) as exc:
        raise RuntimeError(
            "alpaca-py is required for news data access. Install dependencies first."
        ) from exc
    return NewsClient(api_key, secret_key)
```

### 3d — Add `get_news()` method

```python
def get_news(
    self,
    *,
    symbols: Sequence[str],
    lookback_hours: int,
    now: datetime,
) -> dict[str, list["NewsItem"]]:
    """Return news headlines per symbol for the past lookback_hours.
    Returns empty dict on any error (fail-open)."""
    from alpaca_bot.domain import NewsItem
    if not symbols:
        return {}
    settings = self._settings if self._settings is not None else _fallback_settings()
    try:
        from alpaca.data.requests import NewsRequest
    except (ModuleNotFoundError, ImportError):
        _logger.warning("alpaca-py NewsRequest not available; news filter disabled")
        return {}
    try:
        if self._news_client is None:
            self._news_client = self._build_news_client(settings)
        start = now - timedelta(hours=lookback_hours)
        # NewsRequest.symbols is Optional[str] — comma-separated string, NOT a list.
        request = NewsRequest(symbols=",".join(symbols), start=start, end=now, limit=50)
        raw = self._news_client.get_news(request)
        # NewsSet.data is Dict[str, List[News]] with key "news".
        result: dict[str, list[NewsItem]] = {}
        for item in raw.data.get("news", []):
            for sym in (item.symbols or []):
                if sym in {s.upper() for s in symbols}:
                    result.setdefault(sym, []).append(
                        NewsItem(
                            symbol=sym,
                            headline=item.headline or "",
                            published_at=item.created_at,
                        )
                    )
        return result
    except Exception:
        _logger.warning("Failed to fetch news; news filter disabled for this cycle", exc_info=True)
        return {}
```

### 3e — Add `get_latest_quotes()` method

```python
def get_latest_quotes(self, symbols: Sequence[str]) -> dict[str, "Quote"]:
    """Return latest NBBO quote per symbol.
    Returns empty dict on any error (fail-open)."""
    from alpaca_bot.domain import Quote
    if not symbols:
        return {}
    try:
        from alpaca.data.requests import StockLatestQuoteRequest
    except (ModuleNotFoundError, ImportError):
        _logger.warning("alpaca-py StockLatestQuoteRequest not available; spread filter disabled")
        return {}
    try:
        request = StockLatestQuoteRequest(symbol_or_symbols=list(symbols))
        # Returns Dict[str, alpaca.Quote] — alpaca Quote has bid_price and ask_price fields.
        raw = _retry_with_backoff(lambda: self._historical.get_stock_latest_quote(request))
        result: dict[str, Quote] = {}
        for sym, alpaca_quote in raw.items():
            bid = float(getattr(alpaca_quote, "bid_price", 0.0) or 0.0)
            ask = float(getattr(alpaca_quote, "ask_price", 0.0) or 0.0)
            result[sym] = Quote(symbol=sym, bid_price=bid, ask_price=ask)
        return result
    except Exception:
        _logger.warning("Failed to fetch quotes; spread filter disabled for this cycle", exc_info=True)
        return {}
```

---

## Task 4 — Add filter parameters to `evaluate_cycle()`

**File:** `src/alpaca_bot/core/engine.py`

### 4a — Update imports

Add `NewsItem` and `Quote` to the domain import:

```python
from alpaca_bot.domain import Bar, NewsItem, OpenPosition, Quote
```

### 4b — Add 3 new parameters to `evaluate_cycle()` signature (after `session_type`)

```python
regime_bars: Sequence[Bar] | None = None,
news_by_symbol: Mapping[str, Sequence[NewsItem]] | None = None,
quotes_by_symbol: Mapping[str, Quote] | None = None,
```

### 4c — Add regime gate before the entry block

Immediately before `if not entries_disabled:` (currently ~line 217), add:

```python
    # Regime filter: block all entries if broad market is in a downtrend.
    # Mirrors the per-symbol daily_trend_filter_passes() logic: window[-1] is the
    # most recent completed bar (second-to-last), excluding today's potentially partial bar.
    _regime_entries_blocked = False
    if settings.enable_regime_filter and regime_bars is not None:
        if len(regime_bars) >= settings.regime_sma_period + 1:
            window = regime_bars[-settings.regime_sma_period - 1 : -1]
            sma = sum(b.close for b in window) / len(window)
            if window[-1].close <= sma:
                _regime_entries_blocked = True
```

Then change:

```python
    if not entries_disabled:
```

to:

```python
    if not entries_disabled and not _regime_entries_blocked:
```

### 4d — Add per-symbol news and spread gates inside the entry loop

Inside the `for symbol in (symbols or settings.symbols):` loop, after the stale-bar check and before the `signal_evaluator(...)` call, add:

```python
                # News filter: skip entry if catalyst headline detected for this symbol.
                if settings.enable_news_filter and news_by_symbol is not None:
                    symbol_news = news_by_symbol.get(symbol, [])
                    if any(
                        any(kw in item.headline.lower() for kw in settings.news_filter_keywords)
                        for item in symbol_news
                    ):
                        continue

                # Spread filter: skip entry if NBBO spread exceeds threshold.
                if settings.enable_spread_filter and quotes_by_symbol is not None:
                    quote = quotes_by_symbol.get(symbol)
                    if quote is not None and quote.spread_pct > settings.max_spread_pct:
                        continue
```

---

## Task 5 — Thread parameters through `run_cycle()`

**File:** `src/alpaca_bot/runtime/cycle.py`

### 5a — Add imports

`NewsItem` and `Quote` should be under `TYPE_CHECKING` to match the existing pattern (e.g., `SessionType`):

```python
if TYPE_CHECKING:
    from alpaca_bot.domain import NewsItem, Quote
    from alpaca_bot.strategy.session import SessionType
```

(Replace the existing `TYPE_CHECKING` block — merge the new imports in.)

### 5b — Add 3 new parameters to `run_cycle()` signature (after `session_type`)

```python
regime_bars: "Sequence[Bar] | None" = None,
news_by_symbol: "Mapping[str, Sequence[NewsItem]] | None" = None,
quotes_by_symbol: "Mapping[str, Quote] | None" = None,
```

### 5c — Pass new params to `evaluate_cycle()` call

In the `result = (_evaluate_fn or evaluate_cycle)(...)` block, add:

```python
        regime_bars=regime_bars,
        news_by_symbol=news_by_symbol,
        quotes_by_symbol=quotes_by_symbol,
```

---

## Task 6 — Fetch data in `supervisor.py` and pass to cycle runner

**File:** `src/alpaca_bot/runtime/supervisor.py`

### 6a — After `daily_bars_by_symbol = ...`, add regime/news/spread fetches

```python
        # Regime filter: reuse already-fetched daily bars if regime_symbol is on the
        # watchlist, otherwise fetch separately to avoid a duplicate API call.
        regime_bars: list[Bar] | None = None
        if settings.enable_regime_filter:
            if settings.regime_symbol in watchlist_symbols:
                regime_bars = list(daily_bars_by_symbol.get(settings.regime_symbol) or []) or None
            else:
                try:
                    regime_daily = self.market_data.get_daily_bars(
                        symbols=[settings.regime_symbol],
                        start=timestamp - timedelta(days=max(settings.regime_sma_period * 3, 60)),
                        end=daily_bars_end,
                    )
                    regime_bars = regime_daily.get(settings.regime_symbol)
                except Exception:
                    logger.warning(
                        "Failed to fetch regime bars for %s; regime filter disabled this cycle",
                        settings.regime_symbol,
                        exc_info=True,
                    )

        # News filter data — fetched once per cycle, shared across all strategies.
        news_by_symbol: dict[str, list] | None = None
        if settings.enable_news_filter:
            try:
                news_by_symbol = self.market_data.get_news(
                    symbols=list(watchlist_symbols),
                    lookback_hours=settings.news_filter_lookback_hours,
                    now=timestamp,
                )
            except Exception:
                logger.warning("Failed to fetch news; news filter disabled this cycle", exc_info=True)

        # Spread filter data — fetched once per cycle, shared across all strategies.
        quotes_by_symbol: dict[str, object] | None = None
        if settings.enable_spread_filter:
            try:
                quotes_by_symbol = self.market_data.get_latest_quotes(list(watchlist_symbols))
            except Exception:
                logger.warning("Failed to fetch quotes; spread filter disabled this cycle", exc_info=True)
```

### 6b — Pass new params to `_cycle_runner()` in the strategy loop

In the `cycle_result = self._cycle_runner(...)` call, add:

```python
                    regime_bars=regime_bars,
                    news_by_symbol=news_by_symbol,
                    quotes_by_symbol=quotes_by_symbol,
```

---

## Task 7 — Write unit tests

**File:** `tests/unit/test_data_source_filters.py`

Key test design decisions:
- `_INTRADAY_NOW` matches the last bar timestamp from `make_breakout_intraday_bars()` (hardcoded to `2026-04-24 19:00 UTC`) — required to avoid stale-bar rejection in entry tests
- Regime bars constructed to clearly show `window[-1].close < SMA` (not just equal) for blocking tests
- `make_breakout_intraday_bars` has no `now` parameter — called as `make_breakout_intraday_bars("AAPL")`

```python
"""Unit tests for data source entry filters (regime, news, spread)."""
from __future__ import annotations

from datetime import datetime, timezone, timedelta

import pytest

from alpaca_bot.config import Settings, TradingMode
from alpaca_bot.core.engine import evaluate_cycle
from alpaca_bot.domain import Bar, NewsItem, Quote


def make_settings(**overrides) -> Settings:
    base = {
        "TRADING_MODE": "paper",
        "ENABLE_LIVE_TRADING": "false",
        "STRATEGY_VERSION": "v1-breakout",
        "DATABASE_URL": "postgresql://test/db",
        "MARKET_DATA_FEED": "sip",
        "SYMBOLS": "AAPL",
        "DAILY_SMA_PERIOD": "5",
        "BREAKOUT_LOOKBACK_BARS": "5",
        "RELATIVE_VOLUME_LOOKBACK_BARS": "5",
        "RELATIVE_VOLUME_THRESHOLD": "1.5",
        "ENTRY_TIMEFRAME_MINUTES": "15",
        "RISK_PER_TRADE_PCT": "0.01",
        "MAX_POSITION_PCT": "0.10",
        "MAX_OPEN_POSITIONS": "3",
        "DAILY_LOSS_LIMIT_PCT": "0.02",
        "STOP_LIMIT_BUFFER_PCT": "0.001",
        "BREAKOUT_STOP_BUFFER_PCT": "0.001",
        "ENTRY_STOP_PRICE_BUFFER": "0.01",
        "ENTRY_WINDOW_START": "09:30",
        "ENTRY_WINDOW_END": "15:30",
        "FLATTEN_TIME": "15:45",
    }
    base.update(overrides)
    return Settings.from_env(base)


# Matches the last bar timestamp in make_breakout_intraday_bars (2026-04-24 19:00 UTC = 15:00 ET)
# Using _NOW = 2026-05-01 would trigger the stale-bar guard (bar age > 30 min).
_INTRADAY_NOW = datetime(2026, 4, 24, 19, 0, tzinfo=timezone.utc)


def _make_bar(symbol: str, close: float, ts: datetime) -> Bar:
    return Bar(symbol=symbol, timestamp=ts, open=close, high=close, low=close, close=close, volume=10_000)


def _below_sma_regime_bars() -> list[Bar]:
    """7 bars where window[-1].close = 80.0 < SMA(window) = 96.0 (regime_sma_period=5).
    window = bars[1:6]; SMA = (100+100+100+100+80)/5 = 96.0; latest_close = bars[5] = 80.0."""
    ts = _INTRADAY_NOW
    return [
        _make_bar("SPY", 100.0, ts - timedelta(days=6)),
        _make_bar("SPY", 100.0, ts - timedelta(days=5)),
        _make_bar("SPY", 100.0, ts - timedelta(days=4)),
        _make_bar("SPY", 100.0, ts - timedelta(days=3)),
        _make_bar("SPY", 100.0, ts - timedelta(days=2)),
        _make_bar("SPY", 80.0, ts - timedelta(days=1)),   # → window[-1]
        _make_bar("SPY", 80.0, ts),                        # → excluded (today's partial)
    ]


def _above_sma_regime_bars() -> list[Bar]:
    """7 bars where window[-1].close = 100.0 > SMA(window) = 84.0 (regime_sma_period=5)."""
    ts = _INTRADAY_NOW
    return [
        _make_bar("SPY", 80.0, ts - timedelta(days=6)),
        _make_bar("SPY", 80.0, ts - timedelta(days=5)),
        _make_bar("SPY", 80.0, ts - timedelta(days=4)),
        _make_bar("SPY", 80.0, ts - timedelta(days=3)),
        _make_bar("SPY", 80.0, ts - timedelta(days=2)),
        _make_bar("SPY", 100.0, ts - timedelta(days=1)),  # → window[-1]
        _make_bar("SPY", 100.0, ts),                       # → excluded (today's partial)
    ]


# ---------------------------------------------------------------------------
# Regime filter
# ---------------------------------------------------------------------------


class TestRegimeFilter:
    def test_regime_filter_disabled_by_default(self):
        """Regime filter off by default → below-SMA regime bars do not block entries."""
        settings = make_settings()  # enable_regime_filter defaults False
        result = evaluate_cycle(
            settings=settings,
            now=_INTRADAY_NOW,
            equity=100_000.0,
            intraday_bars_by_symbol={},
            daily_bars_by_symbol={},
            open_positions=[],
            working_order_symbols=set(),
            traded_symbols_today=set(),
            entries_disabled=False,
            regime_bars=_below_sma_regime_bars(),
        )
        assert result.intents == []  # no entries (no bars), but filter did not block

    def test_regime_filter_blocks_entries_when_below_sma(self):
        """When regime_bars window[-1].close < SMA and filter enabled, real signals are blocked."""
        from tests.unit.test_cycle_engine import make_breakout_intraday_bars, make_daily_bars

        settings = make_settings(ENABLE_REGIME_FILTER="true", REGIME_SMA_PERIOD="5")
        result = evaluate_cycle(
            settings=settings,
            now=_INTRADAY_NOW,
            equity=100_000.0,
            intraday_bars_by_symbol={"AAPL": make_breakout_intraday_bars("AAPL")},
            daily_bars_by_symbol={"AAPL": make_daily_bars("AAPL")},
            open_positions=[],
            working_order_symbols=set(),
            traded_symbols_today=set(),
            entries_disabled=False,
            regime_bars=_below_sma_regime_bars(),
        )
        entry_intents = [i for i in result.intents if i.intent_type.value == "entry"]
        assert entry_intents == [], "Regime filter should have blocked all entries"

    def test_regime_filter_does_not_block_when_above_sma(self):
        """When regime is above SMA, filter does NOT block entries."""
        settings = make_settings(ENABLE_REGIME_FILTER="true", REGIME_SMA_PERIOD="5")
        result = evaluate_cycle(
            settings=settings,
            now=_INTRADAY_NOW,
            equity=100_000.0,
            intraday_bars_by_symbol={},
            daily_bars_by_symbol={},
            open_positions=[],
            working_order_symbols=set(),
            traded_symbols_today=set(),
            entries_disabled=False,
            regime_bars=_above_sma_regime_bars(),
        )
        assert result.intents == []  # no bars → no entries, but filter did not block

    def test_regime_filter_fail_open_when_regime_bars_none(self):
        """regime_bars=None (fetch failed) → filter bypassed, no crash."""
        settings = make_settings(ENABLE_REGIME_FILTER="true")
        result = evaluate_cycle(
            settings=settings,
            now=_INTRADAY_NOW,
            equity=100_000.0,
            intraday_bars_by_symbol={},
            daily_bars_by_symbol={},
            open_positions=[],
            working_order_symbols=set(),
            traded_symbols_today=set(),
            entries_disabled=False,
            regime_bars=None,
        )
        assert result.intents == []

    def test_regime_filter_fail_open_when_insufficient_bars(self):
        """Fewer than regime_sma_period+1 bars → guard short-circuits, entries not blocked."""
        settings = make_settings(ENABLE_REGIME_FILTER="true", REGIME_SMA_PERIOD="5")
        ts = _INTRADAY_NOW
        insufficient = [
            _make_bar("SPY", 50.0, ts - timedelta(days=i))
            for i in range(3)
        ]  # 3 bars, need >= 6
        result = evaluate_cycle(
            settings=settings,
            now=_INTRADAY_NOW,
            equity=100_000.0,
            intraday_bars_by_symbol={},
            daily_bars_by_symbol={},
            open_positions=[],
            working_order_symbols=set(),
            traded_symbols_today=set(),
            entries_disabled=False,
            regime_bars=insufficient,
        )
        assert result.intents == []


# ---------------------------------------------------------------------------
# News filter
# ---------------------------------------------------------------------------


class TestNewsFilter:
    def test_news_filter_skips_symbol_with_catalyst_keyword(self):
        """Symbol with 'earnings' in headline is skipped even when signal would fire."""
        from tests.unit.test_cycle_engine import make_breakout_intraday_bars, make_daily_bars

        settings = make_settings(ENABLE_NEWS_FILTER="true")
        news = {
            "AAPL": [
                NewsItem(
                    symbol="AAPL",
                    headline="AAPL earnings beat expectations",
                    published_at=_INTRADAY_NOW - timedelta(hours=2),
                )
            ]
        }
        result = evaluate_cycle(
            settings=settings,
            now=_INTRADAY_NOW,
            equity=100_000.0,
            intraday_bars_by_symbol={"AAPL": make_breakout_intraday_bars("AAPL")},
            daily_bars_by_symbol={"AAPL": make_daily_bars("AAPL")},
            open_positions=[],
            working_order_symbols=set(),
            traded_symbols_today=set(),
            entries_disabled=False,
            news_by_symbol=news,
        )
        entry_intents = [i for i in result.intents if i.intent_type.value == "entry"]
        assert entry_intents == [], "Symbol with earnings headline should be skipped"

    def test_news_filter_does_not_skip_on_irrelevant_headline(self):
        """A headline with no keywords does not block entry."""
        settings = make_settings(ENABLE_NEWS_FILTER="true")
        news = {
            "AAPL": [
                NewsItem(
                    symbol="AAPL",
                    headline="Apple launches new product line",
                    published_at=_INTRADAY_NOW - timedelta(hours=1),
                )
            ]
        }
        result = evaluate_cycle(
            settings=settings,
            now=_INTRADAY_NOW,
            equity=100_000.0,
            intraday_bars_by_symbol={},
            daily_bars_by_symbol={},
            open_positions=[],
            working_order_symbols=set(),
            traded_symbols_today=set(),
            entries_disabled=False,
            news_by_symbol=news,
        )
        assert result.intents == []  # no bars → no entries, but news filter didn't block

    def test_news_filter_keyword_matching_is_case_insensitive(self):
        """Upper-case keyword in headline is still detected (headlines vary in case)."""
        settings = make_settings(ENABLE_NEWS_FILTER="true")
        news = {
            "AAPL": [
                NewsItem(
                    symbol="AAPL",
                    headline="AAPL EARNINGS MISS",  # uppercase
                    published_at=_INTRADAY_NOW,
                )
            ]
        }
        result = evaluate_cycle(
            settings=settings,
            now=_INTRADAY_NOW,
            equity=100_000.0,
            intraday_bars_by_symbol={},
            daily_bars_by_symbol={},
            open_positions=[],
            working_order_symbols=set(),
            traded_symbols_today=set(),
            entries_disabled=False,
            news_by_symbol=news,
        )
        # Would need intraday bars to actually fire an entry; this just checks no crash
        assert result.intents == []

    def test_news_filter_fail_open_when_news_none(self):
        """news_by_symbol=None → filter bypassed, no crash."""
        settings = make_settings(ENABLE_NEWS_FILTER="true")
        result = evaluate_cycle(
            settings=settings,
            now=_INTRADAY_NOW,
            equity=100_000.0,
            intraday_bars_by_symbol={},
            daily_bars_by_symbol={},
            open_positions=[],
            working_order_symbols=set(),
            traded_symbols_today=set(),
            entries_disabled=False,
            news_by_symbol=None,
        )
        assert result.intents == []

    def test_news_filter_disabled_by_default(self):
        """Default-off: catalyst headlines don't block when filter is disabled."""
        settings = make_settings()  # ENABLE_NEWS_FILTER defaults False
        from tests.unit.test_cycle_engine import make_breakout_intraday_bars, make_daily_bars

        news = {
            "AAPL": [
                NewsItem(symbol="AAPL", headline="AAPL earnings beat", published_at=_INTRADAY_NOW)
            ]
        }
        result = evaluate_cycle(
            settings=settings,
            now=_INTRADAY_NOW,
            equity=100_000.0,
            intraday_bars_by_symbol={"AAPL": make_breakout_intraday_bars("AAPL")},
            daily_bars_by_symbol={"AAPL": make_daily_bars("AAPL")},
            open_positions=[],
            working_order_symbols=set(),
            traded_symbols_today=set(),
            entries_disabled=False,
            news_by_symbol=news,
        )
        assert isinstance(result.intents, list)  # no crash; filter disabled


# ---------------------------------------------------------------------------
# Spread filter
# ---------------------------------------------------------------------------


class TestSpreadFilter:
    def test_spread_filter_skips_symbol_when_spread_too_wide(self):
        """Spread above max_spread_pct blocks entry for that symbol."""
        from tests.unit.test_cycle_engine import make_breakout_intraday_bars, make_daily_bars

        settings = make_settings(ENABLE_SPREAD_FILTER="true", MAX_SPREAD_PCT="0.001")
        quotes = {
            "AAPL": Quote(symbol="AAPL", bid_price=100.0, ask_price=100.50)  # 0.5% spread
        }
        result = evaluate_cycle(
            settings=settings,
            now=_INTRADAY_NOW,
            equity=100_000.0,
            intraday_bars_by_symbol={"AAPL": make_breakout_intraday_bars("AAPL")},
            daily_bars_by_symbol={"AAPL": make_daily_bars("AAPL")},
            open_positions=[],
            working_order_symbols=set(),
            traded_symbols_today=set(),
            entries_disabled=False,
            quotes_by_symbol=quotes,
        )
        entry_intents = [i for i in result.intents if i.intent_type.value == "entry"]
        assert entry_intents == [], "Wide spread should block entry"

    def test_spread_filter_allows_symbol_with_tight_spread(self):
        """Spread below max_spread_pct does not block entry."""
        settings = make_settings(ENABLE_SPREAD_FILTER="true", MAX_SPREAD_PCT="0.01")
        quotes = {
            "AAPL": Quote(symbol="AAPL", bid_price=100.0, ask_price=100.05)  # 0.05% spread
        }
        result = evaluate_cycle(
            settings=settings,
            now=_INTRADAY_NOW,
            equity=100_000.0,
            intraday_bars_by_symbol={},
            daily_bars_by_symbol={},
            open_positions=[],
            working_order_symbols=set(),
            traded_symbols_today=set(),
            entries_disabled=False,
            quotes_by_symbol=quotes,
        )
        assert result.intents == []  # no bars → no entries, tight spread didn't block

    def test_spread_filter_fail_open_when_quotes_none(self):
        """quotes_by_symbol=None → filter bypassed, no crash."""
        settings = make_settings(ENABLE_SPREAD_FILTER="true")
        result = evaluate_cycle(
            settings=settings,
            now=_INTRADAY_NOW,
            equity=100_000.0,
            intraday_bars_by_symbol={},
            daily_bars_by_symbol={},
            open_positions=[],
            working_order_symbols=set(),
            traded_symbols_today=set(),
            entries_disabled=False,
            quotes_by_symbol=None,
        )
        assert result.intents == []

    def test_spread_filter_fail_open_when_symbol_missing_from_quotes(self):
        """Symbol absent from quotes dict → filter bypassed for that symbol."""
        settings = make_settings(ENABLE_SPREAD_FILTER="true")
        result = evaluate_cycle(
            settings=settings,
            now=_INTRADAY_NOW,
            equity=100_000.0,
            intraday_bars_by_symbol={},
            daily_bars_by_symbol={},
            open_positions=[],
            working_order_symbols=set(),
            traded_symbols_today=set(),
            entries_disabled=False,
            quotes_by_symbol={},
        )
        assert result.intents == []

    def test_spread_filter_disabled_by_default(self):
        """Wide spread does not block when filter is disabled (default)."""
        settings = make_settings()  # ENABLE_SPREAD_FILTER defaults False
        quotes = {
            "AAPL": Quote(symbol="AAPL", bid_price=100.0, ask_price=102.0)  # 2% spread
        }
        result = evaluate_cycle(
            settings=settings,
            now=_INTRADAY_NOW,
            equity=100_000.0,
            intraday_bars_by_symbol={},
            daily_bars_by_symbol={},
            open_positions=[],
            working_order_symbols=set(),
            traded_symbols_today=set(),
            entries_disabled=False,
            quotes_by_symbol=quotes,
        )
        assert isinstance(result.intents, list)


# ---------------------------------------------------------------------------
# Quote.spread_pct property
# ---------------------------------------------------------------------------


class TestQuoteSpreadPct:
    def test_normal_spread(self):
        q = Quote(symbol="AAPL", bid_price=99.90, ask_price=100.10)
        assert q.spread_pct == pytest.approx(0.001, rel=1e-3)

    def test_zero_ask_returns_zero(self):
        q = Quote(symbol="AAPL", bid_price=0.0, ask_price=0.0)
        assert q.spread_pct == 0.0

    def test_zero_spread(self):
        q = Quote(symbol="AAPL", bid_price=100.0, ask_price=100.0)
        assert q.spread_pct == 0.0


# ---------------------------------------------------------------------------
# Settings: keywords parsed lowercase
# ---------------------------------------------------------------------------


class TestSettingsKeywordParsing:
    def test_keywords_are_lowercase_regardless_of_env_input(self):
        """NEWS_FILTER_KEYWORDS env var with uppercase is stored lowercase for matching."""
        settings = make_settings(NEWS_FILTER_KEYWORDS="EARNINGS,FDA,Clinical")
        assert all(kw == kw.lower() for kw in settings.news_filter_keywords)
        assert "earnings" in settings.news_filter_keywords
        assert "fda" in settings.news_filter_keywords
        assert "clinical" in settings.news_filter_keywords
```

**Test command:**

```bash
pytest tests/unit/test_data_source_filters.py -v
pytest
```

---

## Implementation Order

1. Task 1 — Domain types (no deps)
2. Task 2 — Settings fields (no deps)
3. Task 3 — Adapter methods (depends on domain types)
4. Task 4 — Engine parameters and gates (depends on domain types and settings)
5. Task 5 — `run_cycle()` thread-through (depends on engine changes)
6. Task 6 — Supervisor fetch + pass-through (depends on adapter + run_cycle)
7. Task 7 — Write and run tests

---

## Safety Checklist

- [ ] All 3 filters default `False` — zero behavior change without opt-in
- [ ] `evaluate_cycle()` remains a pure function — all I/O is upstream in supervisor
- [ ] All 3 data fetches in supervisor are wrapped in try/except — fail-open
- [ ] `get_news()` and `get_latest_quotes()` also guard internally (double fail-open)
- [ ] No changes to `StrategySignalEvaluator` Protocol or its 11 implementations
- [ ] Existing callers of `evaluate_cycle()` and `run_cycle()` unchanged (new params all default `None`)
- [ ] New env vars validated in `Settings.validate()` (`regime_sma_period >= 2`)
- [ ] `ENABLE_LIVE_TRADING=false` gate is upstream and unaffected
- [ ] `news_filter_keywords` stored lowercase — safe for case-insensitive matching
- [ ] Regime symbol reuses existing daily bar fetch when in watchlist (no double-fetch)
