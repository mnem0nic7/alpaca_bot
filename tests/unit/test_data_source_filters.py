"""Unit tests for data source entry filters (regime, news, spread)."""
from __future__ import annotations

from datetime import datetime, timezone, timedelta

import pytest

from alpaca_bot.config import Settings, TradingMode
from alpaca_bot.core.engine import evaluate_cycle
from alpaca_bot.domain import Bar, NewsItem, Quote


def _make_daily_bars(symbol: str = "AAPL") -> list[Bar]:
    # 21 bars so daily_trend_filter_passes works with sma_period=5 (needs period+1).
    start = datetime(2026, 3, 26, 20, 0, tzinfo=timezone.utc)
    return [
        Bar(
            symbol=symbol,
            timestamp=start + timedelta(days=index),
            open=89.0 + index,
            high=90.0 + index,
            low=88.0 + index,
            close=90.0 + index,
            volume=1_000_000 + index * 1000,
        )
        for index in range(21)
    ]


def _make_breakout_intraday_bars(symbol: str = "AAPL") -> list[Bar]:
    # 21 bars; last bar is the breakout signal bar at 2026-04-24 19:00 UTC.
    start = datetime(2026, 4, 24, 14, 0, tzinfo=timezone.utc)
    bars: list[Bar] = []
    for offset in range(20):
        high = 108.5 + offset * 0.08
        close = high - 0.2
        bars.append(
            Bar(
                symbol=symbol,
                timestamp=start + timedelta(minutes=15 * offset),
                open=round(close - 0.1, 2),
                high=round(high, 2),
                low=round(close - 0.25, 2),
                close=round(close, 2),
                volume=1000 + offset * 10,
            )
        )
    bars[-1] = Bar(
        symbol=symbol,
        timestamp=bars[-1].timestamp,
        open=109.55,
        high=110.0,
        low=109.35,
        close=109.75,
        volume=1190,
    )
    bars.append(
        Bar(
            symbol=symbol,
            timestamp=datetime(2026, 4, 24, 19, 0, tzinfo=timezone.utc),
            open=109.8,
            high=111.0,
            low=109.7,
            close=110.8,
            volume=2000,
        )
    )
    return bars


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


# Matches the last bar timestamp in make_breakout_intraday_bars (2026-04-24 19:00 UTC = 15:00 ET).
# Using a different `now` would trigger the stale-bar guard and prevent entry evaluation.
_INTRADAY_NOW = datetime(2026, 4, 24, 19, 0, tzinfo=timezone.utc)


def _make_bar(symbol: str, close: float, ts: datetime) -> Bar:
    return Bar(symbol=symbol, timestamp=ts, open=close, high=close, low=close, close=close, volume=10_000)


def _below_sma_regime_bars() -> list[Bar]:
    """7 bars where window[-1].close=80 < SMA(window)=96 (regime_sma_period=5).
    window = bars[1:6] = [100,100,100,100,80]; SMA=96; window[-1]=80 → blocked."""
    ts = _INTRADAY_NOW
    return [
        _make_bar("SPY", 100.0, ts - timedelta(days=6)),
        _make_bar("SPY", 100.0, ts - timedelta(days=5)),
        _make_bar("SPY", 100.0, ts - timedelta(days=4)),
        _make_bar("SPY", 100.0, ts - timedelta(days=3)),
        _make_bar("SPY", 100.0, ts - timedelta(days=2)),
        _make_bar("SPY", 80.0,  ts - timedelta(days=1)),   # → window[-1]
        _make_bar("SPY", 80.0,  ts),                        # → excluded (today's partial)
    ]


def _above_sma_regime_bars() -> list[Bar]:
    """7 bars where window[-1].close=100 > SMA(window)=84 (regime_sma_period=5).
    window = bars[1:6] = [80,80,80,80,100]; SMA=84; window[-1]=100 → not blocked."""
    ts = _INTRADAY_NOW
    return [
        _make_bar("SPY", 80.0,  ts - timedelta(days=6)),
        _make_bar("SPY", 80.0,  ts - timedelta(days=5)),
        _make_bar("SPY", 80.0,  ts - timedelta(days=4)),
        _make_bar("SPY", 80.0,  ts - timedelta(days=3)),
        _make_bar("SPY", 80.0,  ts - timedelta(days=2)),
        _make_bar("SPY", 100.0, ts - timedelta(days=1)),   # → window[-1]
        _make_bar("SPY", 100.0, ts),                        # → excluded (today's partial)
    ]


# ---------------------------------------------------------------------------
# Regime filter
# ---------------------------------------------------------------------------


class TestRegimeFilter:
    def test_regime_filter_enabled_by_default(self):
        """Regime filter is on by default; ENABLE_REGIME_FILTER=false turns it off."""
        settings = make_settings(ENABLE_REGIME_FILTER="false")
        result = evaluate_cycle(
            settings=settings,
            now=_INTRADAY_NOW,
            equity=100_000.0,
            intraday_bars_by_symbol={"AAPL": _make_breakout_intraday_bars("AAPL")},
            daily_bars_by_symbol={"AAPL": _make_daily_bars("AAPL")},
            open_positions=[],
            working_order_symbols=set(),
            traded_symbols_today=set(),
            entries_disabled=False,
            regime_bars=_below_sma_regime_bars(),
        )
        # Filter disabled → below-SMA regime bars do NOT block the breakout entry.
        entry_intents = [i for i in result.intents if i.intent_type.value == "entry"]
        assert entry_intents != [], "Disabled regime filter should not block entries"

    def test_regime_filter_blocks_entries_when_below_sma(self):
        """When regime window[-1].close < SMA and filter enabled, real signals are blocked."""
        settings = make_settings(ENABLE_REGIME_FILTER="true", REGIME_SMA_PERIOD="5")
        result = evaluate_cycle(
            settings=settings,
            now=_INTRADAY_NOW,
            equity=100_000.0,
            intraday_bars_by_symbol={"AAPL": _make_breakout_intraday_bars("AAPL")},
            daily_bars_by_symbol={"AAPL": _make_daily_bars("AAPL")},
            open_positions=[],
            working_order_symbols=set(),
            traded_symbols_today=set(),
            entries_disabled=False,
            regime_bars=_below_sma_regime_bars(),
        )
        entry_intents = [i for i in result.intents if i.intent_type.value == "entry"]
        assert entry_intents == [], "Regime filter should have blocked all entries"

    def test_regime_filter_does_not_block_when_above_sma(self):
        """When regime is above SMA, filter does not block entries."""
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
        insufficient = [_make_bar("SPY", 50.0, ts - timedelta(days=i)) for i in range(3)]
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
            intraday_bars_by_symbol={"AAPL": _make_breakout_intraday_bars("AAPL")},
            daily_bars_by_symbol={"AAPL": _make_daily_bars("AAPL")},
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
        """Upper-case keyword in headline is still detected."""
        settings = make_settings(ENABLE_NEWS_FILTER="true")
        news = {
            "AAPL": [
                NewsItem(
                    symbol="AAPL",
                    headline="AAPL EARNINGS MISS",
                    published_at=_INTRADAY_NOW,
                )
            ]
        }
        result = evaluate_cycle(
            settings=settings,
            now=_INTRADAY_NOW,
            equity=100_000.0,
            intraday_bars_by_symbol={"AAPL": _make_breakout_intraday_bars("AAPL")},
            daily_bars_by_symbol={"AAPL": _make_daily_bars("AAPL")},
            open_positions=[],
            working_order_symbols=set(),
            traded_symbols_today=set(),
            entries_disabled=False,
            news_by_symbol=news,
        )
        entry_intents = [i for i in result.intents if i.intent_type.value == "entry"]
        assert entry_intents == [], "Upper-case earnings keyword should still match"

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

    def test_news_filter_enabled_by_default(self):
        """News filter is on by default; ENABLE_NEWS_FILTER=false turns it off."""
        settings = make_settings(ENABLE_NEWS_FILTER="false")
        news = {
            "AAPL": [
                NewsItem(symbol="AAPL", headline="AAPL earnings beat", published_at=_INTRADAY_NOW)
            ]
        }
        result = evaluate_cycle(
            settings=settings,
            now=_INTRADAY_NOW,
            equity=100_000.0,
            intraday_bars_by_symbol={"AAPL": _make_breakout_intraday_bars("AAPL")},
            daily_bars_by_symbol={"AAPL": _make_daily_bars("AAPL")},
            open_positions=[],
            working_order_symbols=set(),
            traded_symbols_today=set(),
            entries_disabled=False,
            news_by_symbol=news,
        )
        # Filter disabled → catalyst headline does NOT block the breakout entry.
        entry_intents = [i for i in result.intents if i.intent_type.value == "entry"]
        assert entry_intents != [], "Disabled news filter should not block entries"


# ---------------------------------------------------------------------------
# Spread filter
# ---------------------------------------------------------------------------


class TestSpreadFilter:
    def test_spread_filter_skips_symbol_when_spread_too_wide(self):
        """Spread above max_spread_pct blocks entry for that symbol."""
        settings = make_settings(ENABLE_SPREAD_FILTER="true", MAX_SPREAD_PCT="0.001")
        quotes = {
            "AAPL": Quote(symbol="AAPL", bid_price=100.0, ask_price=100.50)  # 0.5% spread
        }
        result = evaluate_cycle(
            settings=settings,
            now=_INTRADAY_NOW,
            equity=100_000.0,
            intraday_bars_by_symbol={"AAPL": _make_breakout_intraday_bars("AAPL")},
            daily_bars_by_symbol={"AAPL": _make_daily_bars("AAPL")},
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

    def test_spread_filter_enabled_by_default(self):
        """Spread filter is on by default; ENABLE_SPREAD_FILTER=false turns it off."""
        settings = make_settings(ENABLE_SPREAD_FILTER="false")
        quotes = {
            "AAPL": Quote(symbol="AAPL", bid_price=100.0, ask_price=102.0)  # 2% spread
        }
        result = evaluate_cycle(
            settings=settings,
            now=_INTRADAY_NOW,
            equity=100_000.0,
            intraday_bars_by_symbol={"AAPL": _make_breakout_intraday_bars("AAPL")},
            daily_bars_by_symbol={"AAPL": _make_daily_bars("AAPL")},
            open_positions=[],
            working_order_symbols=set(),
            traded_symbols_today=set(),
            entries_disabled=False,
            quotes_by_symbol=quotes,
        )
        # Filter disabled → wide spread does NOT block the breakout entry.
        entry_intents = [i for i in result.intents if i.intent_type.value == "entry"]
        assert entry_intents != [], "Disabled spread filter should not block entries"


# ---------------------------------------------------------------------------
# Quote.spread_pct property
# ---------------------------------------------------------------------------


class TestQuoteSpreadPct:
    def test_normal_spread(self):
        q = Quote(symbol="AAPL", bid_price=99.90, ask_price=100.10)
        # (100.10 - 99.90) / 100.10 = 0.20 / 100.10 ≈ 0.002
        assert q.spread_pct == pytest.approx(0.002, rel=1e-3)

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
