from __future__ import annotations

from datetime import datetime, timedelta, timezone

from alpaca_bot.config import Settings
from alpaca_bot.domain.models import Bar
from alpaca_bot.strategy.orb import evaluate_orb_signal


# ---------------------------------------------------------------------------
# Settings helper
# ---------------------------------------------------------------------------

def make_settings(**overrides: str) -> Settings:
    values = {
        "TRADING_MODE": "paper",
        "ENABLE_LIVE_TRADING": "false",
        "STRATEGY_VERSION": "v1-breakout",
        "DATABASE_URL": "postgresql://alpaca_bot:secret@db.example.com:5432/alpaca_bot",
        "MARKET_DATA_FEED": "sip",
        "SYMBOLS": "AAPL,MSFT,SPY",
        "DAILY_SMA_PERIOD": "20",
        "BREAKOUT_LOOKBACK_BARS": "20",
        "RELATIVE_VOLUME_LOOKBACK_BARS": "20",
        "RELATIVE_VOLUME_THRESHOLD": "1.5",
        "ENTRY_TIMEFRAME_MINUTES": "15",
        "RISK_PER_TRADE_PCT": "0.0025",
        "MAX_POSITION_PCT": "0.05",
        "MAX_OPEN_POSITIONS": "3",
        "DAILY_LOSS_LIMIT_PCT": "0.01",
        "STOP_LIMIT_BUFFER_PCT": "0.001",
        "BREAKOUT_STOP_BUFFER_PCT": "0.001",
        "ENTRY_STOP_PRICE_BUFFER": "0.01",
        "ENTRY_WINDOW_START": "10:00",
        "ENTRY_WINDOW_END": "15:30",
        "FLATTEN_TIME": "15:45",
    }
    values.update(overrides)
    return Settings.from_env(values)


# ---------------------------------------------------------------------------
# Bar construction helpers
# ---------------------------------------------------------------------------

def make_daily_bars(symbol: str = "AAPL") -> list[Bar]:
    """21 daily bars starting 2026-03-26, with ascending closes."""
    start = datetime(2026, 3, 26, 20, 0, tzinfo=timezone.utc)
    return [
        Bar(
            symbol=symbol,
            timestamp=start + timedelta(days=i),
            open=100 + i - 0.5,
            high=100 + i + 0.3,
            low=100 + i - 1.0,
            close=100 + i + 0.0,
            volume=1_000_000 + i * 1000,
        )
        for i in range(21)
    ]


def make_orb_intraday_bars(
    *,
    signal_high: float = 112.0,
    signal_close: float = 111.5,
    signal_volume: float = 4000,
    opening_range_high: float = 111.0,
) -> list[Bar]:
    """
    Returns 2 opening-range bars + 1 signal bar, all on 2026-04-24 (ET).

    Opening range bars:
      Bar 0: 9:30 AM ET (13:30 UTC) — high = opening_range_high - 1.0 = 110.0
      Bar 1: 9:45 AM ET (13:45 UTC) — high = opening_range_high = 111.0
    Signal bar:
      Bar 2: 10:00 AM ET (14:00 UTC) — within entry window (10:00-15:30 ET)

    opening_range_high = max(110.0, 111.0) = 111.0
    opening_range_low  = min(109.0, 109.5) = 109.0
    avg_volume (opening bars) = (2000 + 2000) / 2 = 2000
    Default rel_vol = 4000 / 2000 = 2.0 >= 1.5 ✓
    """
    bars = [
        Bar(
            symbol="AAPL",
            timestamp=datetime(2026, 4, 24, 13, 30, tzinfo=timezone.utc),  # 9:30 AM ET
            open=109.5,
            high=opening_range_high - 1.0,
            low=109.0,
            close=109.8,
            volume=2000,
        ),
        Bar(
            symbol="AAPL",
            timestamp=datetime(2026, 4, 24, 13, 45, tzinfo=timezone.utc),  # 9:45 AM ET
            open=109.8,
            high=opening_range_high,
            low=109.5,
            close=110.5,
            volume=2000,
        ),
        Bar(
            symbol="AAPL",
            timestamp=datetime(2026, 4, 24, 14, 0, tzinfo=timezone.utc),  # 10:00 AM ET
            open=110.5,
            high=signal_high,
            low=110.0,
            close=signal_close,
            volume=signal_volume,
        ),
    ]
    return bars


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_orb_returns_none_for_empty_bars():
    settings = make_settings()
    daily_bars = make_daily_bars()
    result = evaluate_orb_signal(
        symbol="AAPL",
        intraday_bars=[],
        signal_index=0,
        daily_bars=daily_bars,
        settings=settings,
    )
    assert result is None


def test_orb_returns_none_outside_entry_window():
    # 9:35 AM ET = 13:35 UTC — REGULAR session but before entry window (10:00–15:30 ET).
    # is_entry_window returns False for regular bars before 10:00 ET.
    settings = make_settings()
    daily_bars = make_daily_bars()
    intraday_bars = make_orb_intraday_bars()
    # Replace signal bar with one at 9:35 AM ET (13:35 UTC): REGULAR session, before entry window.
    pre_window_bar = Bar(
        symbol="AAPL",
        timestamp=datetime(2026, 4, 24, 13, 35, tzinfo=timezone.utc),
        open=110.5,
        high=112.0,
        low=110.0,
        close=111.5,
        volume=4000,
    )
    intraday_bars[2] = pre_window_bar
    result = evaluate_orb_signal(
        symbol="AAPL",
        intraday_bars=intraday_bars,
        signal_index=2,
        daily_bars=daily_bars,
        settings=settings,
    )
    assert result is None


def test_orb_returns_none_when_daily_trend_fails():
    # Build daily bars with closes below their own SMA by making a descending series.
    # With DAILY_SMA_PERIOD=20, need at least 21 bars, but last close must be <= SMA of prior 20.
    settings = make_settings()
    # Descending closes: close[i] = 120 - i, so recent prices are lower than earlier ones.
    start = datetime(2026, 3, 26, 20, 0, tzinfo=timezone.utc)
    daily_bars = [
        Bar(
            symbol="AAPL",
            timestamp=start + timedelta(days=i),
            open=120 - i - 0.5,
            high=120 - i + 0.3,
            low=120 - i - 1.0,
            close=120 - i + 0.0,
            volume=1_000_000,
        )
        for i in range(21)
    ]
    intraday_bars = make_orb_intraday_bars()
    result = evaluate_orb_signal(
        symbol="AAPL",
        intraday_bars=intraday_bars,
        signal_index=2,
        daily_bars=daily_bars,
        settings=settings,
    )
    assert result is None


def test_orb_returns_none_when_insufficient_today_bars():
    # Exactly orb_opening_bars (2) today bars — need > 2, so returns None.
    # signal_index=1 means today_bars[:2] = 2 bars, len == orb_opening_bars → None.
    settings = make_settings()
    daily_bars = make_daily_bars()
    # Only 2 bars today; signal_index=1 (last bar), so today_bars has 2 entries.
    bars = [
        Bar(
            symbol="AAPL",
            timestamp=datetime(2026, 4, 24, 13, 30, tzinfo=timezone.utc),  # 9:30 AM ET
            open=109.5,
            high=110.0,
            low=109.0,
            close=109.8,
            volume=2000,
        ),
        Bar(
            symbol="AAPL",
            timestamp=datetime(2026, 4, 24, 14, 0, tzinfo=timezone.utc),  # 10:00 AM ET (entry window)
            open=110.5,
            high=112.0,
            low=110.0,
            close=111.5,
            volume=4000,
        ),
    ]
    result = evaluate_orb_signal(
        symbol="AAPL",
        intraday_bars=bars,
        signal_index=1,
        daily_bars=daily_bars,
        settings=settings,
    )
    assert result is None


def test_orb_returns_none_when_high_at_or_below_opening_range_high():
    # signal_high=111.0 equals opening_range_high=111.0 — guard requires strictly greater.
    settings = make_settings()
    daily_bars = make_daily_bars()
    intraday_bars = make_orb_intraday_bars(signal_high=111.0, signal_close=110.5)
    result = evaluate_orb_signal(
        symbol="AAPL",
        intraday_bars=intraday_bars,
        signal_index=2,
        daily_bars=daily_bars,
        settings=settings,
    )
    assert result is None


def test_orb_returns_none_when_close_below_opening_range_high():
    # high=112.0 breaks out but close=110.5 < 111.0 (opening_range_high) → None.
    settings = make_settings()
    daily_bars = make_daily_bars()
    intraday_bars = make_orb_intraday_bars(signal_high=112.0, signal_close=110.5)
    result = evaluate_orb_signal(
        symbol="AAPL",
        intraday_bars=intraday_bars,
        signal_index=2,
        daily_bars=daily_bars,
        settings=settings,
    )
    assert result is None


def test_orb_returns_none_when_relative_volume_too_low():
    # avg_volume of opening bars = 2000; threshold = 1.5 → need >= 3000.
    # signal_volume=100 → rel_vol = 0.05 < 1.5.
    settings = make_settings()
    daily_bars = make_daily_bars()
    intraday_bars = make_orb_intraday_bars(signal_volume=100)
    result = evaluate_orb_signal(
        symbol="AAPL",
        intraday_bars=intraday_bars,
        signal_index=2,
        daily_bars=daily_bars,
        settings=settings,
    )
    assert result is None


def test_orb_returns_signal_on_valid_breakout():
    settings = make_settings()
    daily_bars = make_daily_bars()
    intraday_bars = make_orb_intraday_bars()
    result = evaluate_orb_signal(
        symbol="AAPL",
        intraday_bars=intraday_bars,
        signal_index=2,
        daily_bars=daily_bars,
        settings=settings,
    )
    assert result is not None


def test_orb_signal_entry_level_equals_opening_range_high():
    # opening_range_high = max(bar0.high=110.0, bar1.high=111.0) = 111.0
    settings = make_settings()
    daily_bars = make_daily_bars()
    intraday_bars = make_orb_intraday_bars(opening_range_high=111.0)
    result = evaluate_orb_signal(
        symbol="AAPL",
        intraday_bars=intraday_bars,
        signal_index=2,
        daily_bars=daily_bars,
        settings=settings,
    )
    assert result is not None
    assert result.entry_level == 111.0


def test_orb_signal_stop_price_above_signal_bar_high():
    # stop_price = round(signal_bar.high + entry_stop_price_buffer, 2)
    #            = round(112.0 + 0.01, 2) = 112.01
    settings = make_settings()
    daily_bars = make_daily_bars()
    intraday_bars = make_orb_intraday_bars(signal_high=112.0)
    result = evaluate_orb_signal(
        symbol="AAPL",
        intraday_bars=intraday_bars,
        signal_index=2,
        daily_bars=daily_bars,
        settings=settings,
    )
    assert result is not None
    expected_stop_price = round(112.0 + 0.01, 2)
    assert result.stop_price == expected_stop_price


def test_orb_signal_initial_stop_below_opening_range_low():
    # opening_range_low = min(bar0.low=109.0, bar1.low=109.5) = 109.0
    # initial_stop_price = round(max(0.01, opening_range_low - stop_buffer), 2)
    # stop_buffer > 0, so initial_stop_price < 109.0
    settings = make_settings()
    daily_bars = make_daily_bars()
    intraday_bars = make_orb_intraday_bars()
    result = evaluate_orb_signal(
        symbol="AAPL",
        intraday_bars=intraday_bars,
        signal_index=2,
        daily_bars=daily_bars,
        settings=settings,
    )
    assert result is not None
    assert result.initial_stop_price < 109.0


def test_orb_returns_none_when_atr_not_computable():
    # With only 5 daily bars and default ATR_PERIOD=14, calculate_atr returns None.
    settings = make_settings()
    start = datetime(2026, 4, 20, 20, 0, tzinfo=timezone.utc)
    daily_bars = [
        Bar(
            symbol="AAPL",
            timestamp=start + timedelta(days=i),
            open=100 + i - 0.5,
            high=100 + i + 0.3,
            low=100 + i - 1.0,
            close=100 + i + 0.0,
            volume=1_000_000,
        )
        for i in range(5)
    ]
    intraday_bars = make_orb_intraday_bars()
    result = evaluate_orb_signal(
        symbol="AAPL",
        intraday_bars=intraday_bars,
        signal_index=2,
        daily_bars=daily_bars,
        settings=settings,
    )
    assert result is None
