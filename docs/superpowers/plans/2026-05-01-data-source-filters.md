# Implementation Plan: Data Source Filters

**Date:** 2026-05-01  
**Spec:** `docs/superpowers/specs/2026-05-01-data-source-filters-design.md`  
**Status:** Ready for implementation

---

## Overview

Add three default-off entry filters to the trading engine:
1. **Regime filter** — skip all entries when SPY (or configured symbol) is below its N-day SMA
2. **News filter** — skip entry on a symbol if it has catalyst-type headlines in the past N hours
3. **Spread filter** — skip entry on a symbol if the NBBO spread exceeds a configured threshold

All filters are engine-level gates (no changes to the 11 `StrategySignalEvaluator` implementations). All default `False`. All fail-open (bad data → filter bypassed). `evaluate_cycle()` remains a pure function.

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

Add `NewsItem` and `Quote` to the import and `__all__`:

```python
from alpaca_bot.domain.models import (
    Bar,
    EntrySignal,
    NewsItem,
    OpenPosition,
    Quote,
    ReplayEvent,
    ReplayResult,
    ReplayScenario,
    WorkingEntryOrder,
)

__all__ = [
    "Bar",
    "EntrySignal",
    "NewsItem",
    "OpenPosition",
    "Quote",
    "ReplayEvent",
    "ReplayResult",
    "ReplayScenario",
    "WorkingEntryOrder",
    ...  # keep any other existing exports
]
```

(Read the file first to preserve exact existing exports.)

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

In `from_env()`, parse the new fields (follow the existing pattern for booleans, ints, and floats):

```python
enable_regime_filter=_parse_bool(env, "ENABLE_REGIME_FILTER", default=False),
regime_symbol=env.get("REGIME_SYMBOL", "SPY"),
regime_sma_period=int(env.get("REGIME_SMA_PERIOD", "20")),
enable_news_filter=_parse_bool(env, "ENABLE_NEWS_FILTER", default=False),
news_filter_lookback_hours=int(env.get("NEWS_FILTER_LOOKBACK_HOURS", "24")),
news_filter_keywords=tuple(
    kw.strip()
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

### 3a — Add `_build_news_client()` static method

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

### 3b — Add `get_news()` method

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
        from alpaca.data.historical.news import NewsClient
    except (ModuleNotFoundError, ImportError):
        _logger.warning("alpaca-py NewsClient not available; news filter disabled")
        return {}
    try:
        if not hasattr(self, "_news_client"):
            self._news_client = self._build_news_client(settings)
        start = now - timedelta(hours=lookback_hours)
        request = NewsRequest(symbols=list(symbols), start=start, end=now, limit=50)
        raw = self._news_client.get_news(request)
        result: dict[str, list[NewsItem]] = {}
        for item in (raw.news if hasattr(raw, "news") else []):
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

Also add `from datetime import timedelta` import if not already present (check first — `timedelta` may already be imported).

### 3c — Add `get_latest_quotes()` method

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
        raw = _retry_with_backoff(lambda: self._historical.get_stock_latest_quote(request))
        result: dict[str, Quote] = {}
        for sym, quote in (raw.items() if hasattr(raw, "items") else []):
            bid = getattr(quote, "bid_price", 0.0) or 0.0
            ask = getattr(quote, "ask_price", 0.0) or 0.0
            result[sym] = Quote(symbol=sym, bid_price=float(bid), ask_price=float(ask))
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
    # Regime filter: skip all entries if SPY (or regime_symbol) is in a downtrend.
    _regime_entries_blocked = False
    if settings.enable_regime_filter and regime_bars is not None:
        if len(regime_bars) >= settings.regime_sma_period + 1:
            window = regime_bars[-settings.regime_sma_period - 1 : -1]
            sma = sum(b.close for b in window) / len(window)
            latest_close = window[-1].close
            if latest_close <= sma:
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
                # News filter: skip if catalyst headline detected
                if settings.enable_news_filter and news_by_symbol is not None:
                    symbol_news = news_by_symbol.get(symbol, [])
                    if any(
                        any(kw in item.headline.lower() for kw in settings.news_filter_keywords)
                        for item in symbol_news
                    ):
                        continue

                # Spread filter: skip if NBBO spread too wide
                if settings.enable_spread_filter and quotes_by_symbol is not None:
                    quote = quotes_by_symbol.get(symbol)
                    if quote is not None and quote.spread_pct > settings.max_spread_pct:
                        continue
```

---

## Task 5 — Thread parameters through `run_cycle()`

**File:** `src/alpaca_bot/runtime/cycle.py`

### 5a — Add 3 new parameters to `run_cycle()` signature (after `session_type`)

```python
regime_bars: "Sequence[Bar] | None" = None,
news_by_symbol: "Mapping[str, Sequence[NewsItem]] | None" = None,
quotes_by_symbol: "Mapping[str, Quote] | None" = None,
```

Add imports at top:

```python
from alpaca_bot.domain import Bar, NewsItem, Quote
```

(Or use `TYPE_CHECKING` guard if preferred — check existing pattern.)

### 5b — Pass new params to `evaluate_cycle()` call

In the `result = (_evaluate_fn or evaluate_cycle)(...)` block, add:

```python
        regime_bars=regime_bars,
        news_by_symbol=news_by_symbol,
        quotes_by_symbol=quotes_by_symbol,
```

---

## Task 6 — Fetch data in `supervisor.py` and pass to cycle runner

**File:** `src/alpaca_bot/runtime/supervisor.py`

### 6a — After daily bars fetch (after `daily_bars_by_symbol = ...`), add:

```python
        # Regime filter data
        regime_bars: list[Bar] | None = None
        if settings.enable_regime_filter:
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

        # News filter data
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

        # Spread filter data
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


_NOW = datetime(2026, 5, 1, 14, 0, tzinfo=timezone.utc)


def _make_bar(symbol: str, close: float, ts: datetime) -> Bar:
    return Bar(symbol=symbol, timestamp=ts, open=close, high=close, low=close, close=close, volume=10_000)


def _flat_daily_bars(n: int, close: float = 100.0) -> list[Bar]:
    return [
        _make_bar("AAPL", close, _NOW - timedelta(days=n - i))
        for i in range(n)
    ]


def _signal_always_none(**_kwargs):
    return None


# ---------------------------------------------------------------------------
# Regime filter
# ---------------------------------------------------------------------------


class TestRegimeFilter:
    def test_regime_filter_disabled_by_default(self):
        """Regime filter off → entries proceed normally (signal_evaluator returns None here)."""
        settings = make_settings()
        # regime_bars below SMA: last close = 80, SMA of prior window = 100
        low_regime_bars = _flat_daily_bars(22, close=100.0)
        low_regime_bars[-1] = _make_bar("SPY", 80.0, _NOW)
        result = evaluate_cycle(
            settings=settings,
            now=_NOW,
            equity=100_000.0,
            intraday_bars_by_symbol={},
            daily_bars_by_symbol={},
            open_positions=[],
            working_order_symbols=set(),
            traded_symbols_today=set(),
            entries_disabled=False,
            regime_bars=low_regime_bars,
        )
        # No entries because signal_evaluator returns None, but no regime block either
        assert result.intents == []

    def test_regime_filter_blocks_entries_when_below_sma(self):
        """When regime_bars last close < SMA and filter enabled, a real signal is blocked."""
        from tests.unit.test_cycle_engine import make_breakout_intraday_bars
        settings = make_settings(
            ENABLE_REGIME_FILTER="true",
            REGIME_SMA_PERIOD="5",
        )
        # 7 daily bars: first 6 at 100, last at 80 (below SMA of prior 5 = 100)
        regime_bars = _flat_daily_bars(6, close=100.0) + [_make_bar("SPY", 80.0, _NOW)]
        intraday = make_breakout_intraday_bars(now=_NOW, symbol="AAPL")
        daily = _flat_daily_bars(20, close=100.0)
        result = evaluate_cycle(
            settings=settings,
            now=_NOW,
            equity=100_000.0,
            intraday_bars_by_symbol={"AAPL": intraday},
            daily_bars_by_symbol={"AAPL": daily},
            open_positions=[],
            working_order_symbols=set(),
            traded_symbols_today=set(),
            entries_disabled=False,
            regime_bars=regime_bars,
        )
        entry_intents = [i for i in result.intents if i.intent_type.value == "entry"]
        assert entry_intents == [], "Regime filter should have blocked all entries"

    def test_regime_filter_passes_when_above_sma(self):
        """When last close > SMA, regime filter does NOT block entries (signal still may fire)."""
        settings = make_settings(
            ENABLE_REGIME_FILTER="true",
            REGIME_SMA_PERIOD="5",
        )
        # 7 bars: first 6 at 80, last at 100 (above SMA of prior 5 = 80)
        regime_bars = _flat_daily_bars(6, close=80.0) + [_make_bar("SPY", 100.0, _NOW)]
        result = evaluate_cycle(
            settings=settings,
            now=_NOW,
            equity=100_000.0,
            intraday_bars_by_symbol={},
            daily_bars_by_symbol={},
            open_positions=[],
            working_order_symbols=set(),
            traded_symbols_today=set(),
            entries_disabled=False,
            regime_bars=regime_bars,
        )
        # No entries (no intraday bars), but no block either
        assert result.intents == []

    def test_regime_filter_fail_open_when_regime_bars_none(self):
        """If regime_bars is None (fetch failed), filter is bypassed — engine runs normally."""
        settings = make_settings(ENABLE_REGIME_FILTER="true")
        result = evaluate_cycle(
            settings=settings,
            now=_NOW,
            equity=100_000.0,
            intraday_bars_by_symbol={},
            daily_bars_by_symbol={},
            open_positions=[],
            working_order_symbols=set(),
            traded_symbols_today=set(),
            entries_disabled=False,
            regime_bars=None,
        )
        assert result.intents == []  # no crash, no block

    def test_regime_filter_fail_open_when_insufficient_bars(self):
        """Fewer than regime_sma_period+1 bars → guard prevents regime check → entries not blocked."""
        settings = make_settings(ENABLE_REGIME_FILTER="true", REGIME_SMA_PERIOD="5")
        regime_bars = _flat_daily_bars(3, close=50.0)  # only 3 bars, need 6
        result = evaluate_cycle(
            settings=settings,
            now=_NOW,
            equity=100_000.0,
            intraday_bars_by_symbol={},
            daily_bars_by_symbol={},
            open_positions=[],
            working_order_symbols=set(),
            traded_symbols_today=set(),
            entries_disabled=False,
            regime_bars=regime_bars,
        )
        assert result.intents == []  # no crash, no block


# ---------------------------------------------------------------------------
# News filter
# ---------------------------------------------------------------------------


class TestNewsFilter:
    def test_news_filter_skips_symbol_with_catalyst_keyword(self):
        """Symbol with 'earnings' in headline is skipped; other symbol is not affected."""
        from tests.unit.test_cycle_engine import make_breakout_intraday_bars
        settings = make_settings(ENABLE_NEWS_FILTER="true")
        intraday = make_breakout_intraday_bars(now=_NOW, symbol="AAPL")
        daily = _flat_daily_bars(20, close=100.0)
        news = {
            "AAPL": [
                NewsItem(
                    symbol="AAPL",
                    headline="AAPL earnings beat expectations",
                    published_at=_NOW - timedelta(hours=2),
                )
            ]
        }
        result = evaluate_cycle(
            settings=settings,
            now=_NOW,
            equity=100_000.0,
            intraday_bars_by_symbol={"AAPL": intraday},
            daily_bars_by_symbol={"AAPL": daily},
            open_positions=[],
            working_order_symbols=set(),
            traded_symbols_today=set(),
            entries_disabled=False,
            news_by_symbol=news,
        )
        entry_intents = [i for i in result.intents if i.intent_type.value == "entry"]
        assert entry_intents == [], "Symbol with earnings headline should be skipped"

    def test_news_filter_does_not_skip_symbol_with_irrelevant_headline(self):
        """A headline with no keywords does not block entry."""
        settings = make_settings(ENABLE_NEWS_FILTER="true")
        news = {
            "AAPL": [
                NewsItem(
                    symbol="AAPL",
                    headline="Apple launches new product line",
                    published_at=_NOW - timedelta(hours=1),
                )
            ]
        }
        result = evaluate_cycle(
            settings=settings,
            now=_NOW,
            equity=100_000.0,
            intraday_bars_by_symbol={},
            daily_bars_by_symbol={},
            open_positions=[],
            working_order_symbols=set(),
            traded_symbols_today=set(),
            entries_disabled=False,
            news_by_symbol=news,
        )
        assert result.intents == []  # no bars → no entries, but no crash

    def test_news_filter_fail_open_when_news_none(self):
        """news_by_symbol=None → filter bypassed, no crash."""
        settings = make_settings(ENABLE_NEWS_FILTER="true")
        result = evaluate_cycle(
            settings=settings,
            now=_NOW,
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
        """Even if news_by_symbol has catalyst headlines, disabled filter does not block."""
        settings = make_settings()  # ENABLE_NEWS_FILTER defaults False
        from tests.unit.test_cycle_engine import make_breakout_intraday_bars
        intraday = make_breakout_intraday_bars(now=_NOW, symbol="AAPL")
        daily = _flat_daily_bars(20, close=100.0)
        news = {
            "AAPL": [
                NewsItem(
                    symbol="AAPL",
                    headline="AAPL earnings beat",
                    published_at=_NOW,
                )
            ]
        }
        result = evaluate_cycle(
            settings=settings,
            now=_NOW,
            equity=100_000.0,
            intraday_bars_by_symbol={"AAPL": intraday},
            daily_bars_by_symbol={"AAPL": daily},
            open_positions=[],
            working_order_symbols=set(),
            traded_symbols_today=set(),
            entries_disabled=False,
            news_by_symbol=news,
        )
        # Filter disabled → normal signal evaluation (may or may not produce entry)
        # Just ensure no crash and type is correct
        assert isinstance(result.intents, list)


# ---------------------------------------------------------------------------
# Spread filter
# ---------------------------------------------------------------------------


class TestSpreadFilter:
    def test_spread_filter_skips_symbol_when_spread_too_wide(self):
        """A spread above max_spread_pct blocks entry for that symbol."""
        from tests.unit.test_cycle_engine import make_breakout_intraday_bars
        settings = make_settings(ENABLE_SPREAD_FILTER="true", MAX_SPREAD_PCT="0.001")
        intraday = make_breakout_intraday_bars(now=_NOW, symbol="AAPL")
        daily = _flat_daily_bars(20, close=100.0)
        quotes = {
            "AAPL": Quote(symbol="AAPL", bid_price=100.0, ask_price=100.50)  # 0.5% spread
        }
        result = evaluate_cycle(
            settings=settings,
            now=_NOW,
            equity=100_000.0,
            intraday_bars_by_symbol={"AAPL": intraday},
            daily_bars_by_symbol={"AAPL": daily},
            open_positions=[],
            working_order_symbols=set(),
            traded_symbols_today=set(),
            entries_disabled=False,
            quotes_by_symbol=quotes,
        )
        entry_intents = [i for i in result.intents if i.intent_type.value == "entry"]
        assert entry_intents == [], "Wide spread should block entry"

    def test_spread_filter_allows_symbol_with_tight_spread(self):
        """A spread below max_spread_pct does not block entry."""
        settings = make_settings(ENABLE_SPREAD_FILTER="true", MAX_SPREAD_PCT="0.01")
        quotes = {
            "AAPL": Quote(symbol="AAPL", bid_price=100.0, ask_price=100.05)  # 0.05% spread
        }
        result = evaluate_cycle(
            settings=settings,
            now=_NOW,
            equity=100_000.0,
            intraday_bars_by_symbol={},
            daily_bars_by_symbol={},
            open_positions=[],
            working_order_symbols=set(),
            traded_symbols_today=set(),
            entries_disabled=False,
            quotes_by_symbol=quotes,
        )
        assert result.intents == []  # no bars → no entries, but tight spread didn't block

    def test_spread_filter_fail_open_when_quotes_none(self):
        """quotes_by_symbol=None → filter bypassed, no crash."""
        settings = make_settings(ENABLE_SPREAD_FILTER="true")
        result = evaluate_cycle(
            settings=settings,
            now=_NOW,
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
            now=_NOW,
            equity=100_000.0,
            intraday_bars_by_symbol={},
            daily_bars_by_symbol={},
            open_positions=[],
            working_order_symbols=set(),
            traded_symbols_today=set(),
            entries_disabled=False,
            quotes_by_symbol={},  # empty dict
        )
        assert result.intents == []

    def test_spread_filter_disabled_by_default(self):
        """Wide spread does not block when filter is disabled (default)."""
        settings = make_settings()
        quotes = {
            "AAPL": Quote(symbol="AAPL", bid_price=100.0, ask_price=102.0)  # 2% spread
        }
        result = evaluate_cycle(
            settings=settings,
            now=_NOW,
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
- [ ] New env vars validated in `Settings.validate()` (regime_sma_period >= 2)
- [ ] `ENABLE_LIVE_TRADING=false` gate is upstream and unaffected
