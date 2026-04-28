from __future__ import annotations

from datetime import datetime, timezone

from alpaca_bot.config import Settings
from alpaca_bot.domain.enums import IntentType
from alpaca_bot.domain.models import Bar, ReplayScenario
from alpaca_bot.replay import ReplayRunner
from alpaca_bot.strategy import STRATEGY_REGISTRY


def make_settings(**overrides: str) -> Settings:
    base = {
        "TRADING_MODE": "paper",
        "ENABLE_LIVE_TRADING": "false",
        "STRATEGY_VERSION": "v1-breakout",
        "DATABASE_URL": "postgresql://alpaca_bot:secret@db.example.com:5432/alpaca_bot",
        "MARKET_DATA_FEED": "sip",
        "SYMBOLS": "TSLA",
        "DAILY_SMA_PERIOD": "5",
        "BREAKOUT_LOOKBACK_BARS": "5",
        "RELATIVE_VOLUME_LOOKBACK_BARS": "5",
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
        "ATR_PERIOD": "10",
        "ATR_STOP_MULTIPLIER": "1.0",
        "ORB_OPENING_BARS": "2",
        "HIGH_WATERMARK_LOOKBACK_DAYS": "5",
        "EMA_PERIOD": "5",
        "PRIOR_DAY_HIGH_LOOKBACK_BARS": "1",
    }
    base.update(overrides)
    return Settings.from_env(base)


def _bar(
    symbol: str,
    hour: int,
    minute: int,
    *,
    open_: float,
    high: float,
    low: float,
    close: float,
    volume: int,
    day: int = 15,
) -> Bar:
    ts = datetime(2024, 1, day, hour, minute, tzinfo=timezone.utc)
    return Bar(symbol=symbol, timestamp=ts, open=open_, high=high, low=low, close=close, volume=volume)


def _daily(close: float, high: float = 102.0, low: float = 98.0, day: int = 1) -> Bar:
    ts = datetime(2024, 1, day, 0, 0, tzinfo=timezone.utc)
    return Bar(symbol="TSLA", timestamp=ts, open=close - 0.5, high=high, low=low, close=close, volume=500_000)


# Seven daily bars (Jan 8–14) satisfying DAILY_SMA_PERIOD=5 trend filter:
#   window = daily_bars[-6:-1] = Jan 9–13, SMA = (99*4 + 101)/5 = 99.4
#   daily_bars[-2].close = Jan 13 close = 101.0 > 99.4  ✓
DAILY_BARS = [
    _daily(close=99.0, day=8),
    _daily(close=99.0, day=9),
    _daily(close=99.0, day=10),
    _daily(close=99.0, day=11),
    _daily(close=99.0, day=12),
    _daily(close=101.0, day=13),
    _daily(close=100.0, day=14),
]


# ─── momentum ───────────────────────────────────────────────────────────────

def test_momentum_happy_path_produces_entry_filled() -> None:
    """Full cycle: momentum signal fires, entry order placed, next bar fills it."""
    settings = make_settings()
    # DAILY_BARS[-1] (Jan 14) high=102.0 → yesterday_high=102.0
    # signal bar: high=105.0 and close=103.5 both exceed 102.0  ✓
    # volume: 5 prior bars at 1000 each → relative_volume = 2000/1000 = 2.0 > 1.5  ✓
    # execution bar open=105.01 == stop_price → fill at 105.01  ✓
    intraday_bars = [
        _bar("TSLA", 15, 0,  open_=99.8,   high=100.2,  low=99.5,   close=100.0, volume=1000),
        _bar("TSLA", 15, 15, open_=99.9,   high=100.3,  low=99.7,   close=100.0, volume=1000),
        _bar("TSLA", 15, 30, open_=100.0,  high=100.4,  low=99.8,   close=100.0, volume=1000),
        _bar("TSLA", 15, 45, open_=100.0,  high=100.5,  low=99.8,   close=100.0, volume=1000),
        _bar("TSLA", 16, 0,  open_=100.0,  high=100.5,  low=99.9,   close=100.0, volume=1000),
        _bar("TSLA", 16, 15, open_=102.0,  high=105.0,  low=101.8,  close=103.5, volume=2000),
        _bar("TSLA", 16, 30, open_=105.01, high=106.0,  low=104.5,  close=105.5, volume=1500),
    ]
    scenario = ReplayScenario(
        name="momentum_happy",
        symbol="TSLA",
        starting_equity=100_000.0,
        daily_bars=DAILY_BARS,
        intraday_bars=intraday_bars,
    )
    result = ReplayRunner(
        settings, signal_evaluator=STRATEGY_REGISTRY["momentum"], strategy_name="momentum"
    ).run(scenario)
    event_types = [e.event_type for e in result.events]
    assert IntentType.ENTRY_ORDER_PLACED in event_types
    assert IntentType.ENTRY_FILLED in event_types


def test_momentum_insufficient_daily_bars_produces_no_trades() -> None:
    """Trend filter requires DAILY_SMA_PERIOD+1=6 bars; 2 bars → no signal."""
    settings = make_settings()
    intraday_bars = [
        _bar("TSLA", 15, 0,  open_=99.8,   high=100.2,  low=99.5,   close=100.0, volume=1000),
        _bar("TSLA", 15, 15, open_=99.9,   high=100.3,  low=99.7,   close=100.0, volume=1000),
        _bar("TSLA", 15, 30, open_=100.0,  high=100.4,  low=99.8,   close=100.0, volume=1000),
        _bar("TSLA", 15, 45, open_=100.0,  high=100.5,  low=99.8,   close=100.0, volume=1000),
        _bar("TSLA", 16, 0,  open_=100.0,  high=100.5,  low=99.9,   close=100.0, volume=1000),
        _bar("TSLA", 16, 15, open_=102.0,  high=105.0,  low=101.8,  close=103.5, volume=2000),
        _bar("TSLA", 16, 30, open_=105.01, high=106.0,  low=104.5,  close=105.5, volume=1500),
    ]
    short_daily = [_daily(close=99.0, day=13), _daily(close=101.0, day=14)]
    scenario = ReplayScenario(
        name="momentum_no_daily",
        symbol="TSLA",
        starting_equity=100_000.0,
        daily_bars=short_daily,
        intraday_bars=intraday_bars,
    )
    result = ReplayRunner(
        settings, signal_evaluator=STRATEGY_REGISTRY["momentum"], strategy_name="momentum"
    ).run(scenario)
    assert result.events == []


# ─── orb ────────────────────────────────────────────────────────────────────

def test_orb_happy_path_produces_entry_filled() -> None:
    """Full cycle: ORB breakout fires after 2-bar opening range; next bar fills."""
    settings = make_settings()
    # opening range bars 0–1: high=100.8 → opening_range_high=100.8
    # signal bar: high=101.5 and close=101.2 both exceed 100.8  ✓
    # relative_volume = 2000 / ((1000+1000)/2) = 2.0 > 1.5  ✓
    # execution bar open=101.51 == stop_price → fill at 101.51  ✓
    intraday_bars = [
        _bar("TSLA", 15, 0,  open_=100.0,  high=100.5,  low=99.5,   close=100.2, volume=1000),
        _bar("TSLA", 15, 15, open_=100.2,  high=100.8,  low=99.8,   close=100.5, volume=1000),
        _bar("TSLA", 15, 30, open_=100.5,  high=101.5,  low=100.4,  close=101.2, volume=2000),
        _bar("TSLA", 15, 45, open_=101.51, high=102.5,  low=101.0,  close=102.0, volume=1500),
    ]
    scenario = ReplayScenario(
        name="orb_happy",
        symbol="TSLA",
        starting_equity=100_000.0,
        daily_bars=DAILY_BARS,
        intraday_bars=intraday_bars,
    )
    result = ReplayRunner(
        settings, signal_evaluator=STRATEGY_REGISTRY["orb"], strategy_name="orb"
    ).run(scenario)
    event_types = [e.event_type for e in result.events]
    assert IntentType.ENTRY_ORDER_PLACED in event_types
    assert IntentType.ENTRY_FILLED in event_types


def test_orb_insufficient_daily_bars_produces_no_trades() -> None:
    """Trend filter blocks signal when fewer than DAILY_SMA_PERIOD+1 bars exist."""
    settings = make_settings()
    intraday_bars = [
        _bar("TSLA", 15, 0,  open_=100.0,  high=100.5,  low=99.5,   close=100.2, volume=1000),
        _bar("TSLA", 15, 15, open_=100.2,  high=100.8,  low=99.8,   close=100.5, volume=1000),
        _bar("TSLA", 15, 30, open_=100.5,  high=101.5,  low=100.4,  close=101.2, volume=2000),
        _bar("TSLA", 15, 45, open_=101.51, high=102.5,  low=101.0,  close=102.0, volume=1500),
    ]
    short_daily = [_daily(close=99.0, day=13), _daily(close=101.0, day=14)]
    scenario = ReplayScenario(
        name="orb_no_daily",
        symbol="TSLA",
        starting_equity=100_000.0,
        daily_bars=short_daily,
        intraday_bars=intraday_bars,
    )
    result = ReplayRunner(
        settings, signal_evaluator=STRATEGY_REGISTRY["orb"], strategy_name="orb"
    ).run(scenario)
    assert result.events == []


# ─── high_watermark ──────────────────────────────────────────────────────────

def test_high_watermark_happy_path_produces_entry_filled() -> None:
    """Signal fires when price exceeds 5-day high watermark; execution bar fills."""
    settings = make_settings()
    # completed_bars[-5:] = Jan 9–13, each high=102.0 → historical_high=102.0
    # signal bar: high=103.5 and close=103.0 both exceed 102.0  ✓
    # relative_volume = 2000/1000 = 2.0 > 1.5  ✓
    # stop_price = round(102.0 + 0.01, 2) = 102.01 (anchored at historical_high)
    # execution bar open=102.01 == stop_price → fill at 102.01  ✓
    intraday_bars = [
        _bar("TSLA", 15, 0,  open_=100.0,  high=100.5,  low=99.8,   close=100.2, volume=1000),
        _bar("TSLA", 15, 15, open_=100.2,  high=100.7,  low=100.0,  close=100.4, volume=1000),
        _bar("TSLA", 15, 30, open_=100.4,  high=100.9,  low=100.2,  close=100.6, volume=1000),
        _bar("TSLA", 15, 45, open_=100.6,  high=101.2,  low=100.4,  close=101.0, volume=1000),
        _bar("TSLA", 16, 0,  open_=101.0,  high=101.5,  low=100.8,  close=101.2, volume=1000),
        _bar("TSLA", 16, 15, open_=102.0,  high=103.5,  low=101.8,  close=103.0, volume=2000),
        _bar("TSLA", 16, 30, open_=102.01, high=104.0,  low=101.5,  close=103.5, volume=1500),
    ]
    scenario = ReplayScenario(
        name="high_watermark_happy",
        symbol="TSLA",
        starting_equity=100_000.0,
        daily_bars=DAILY_BARS,
        intraday_bars=intraday_bars,
    )
    result = ReplayRunner(
        settings, signal_evaluator=STRATEGY_REGISTRY["high_watermark"], strategy_name="high_watermark"
    ).run(scenario)
    event_types = [e.event_type for e in result.events]
    assert IntentType.ENTRY_ORDER_PLACED in event_types
    assert IntentType.ENTRY_FILLED in event_types


def test_high_watermark_insufficient_daily_bars_produces_no_trades() -> None:
    """Requires HIGH_WATERMARK_LOOKBACK_DAYS=5 completed bars + trend filter; 2 bars → no trades."""
    settings = make_settings()
    intraday_bars = [
        _bar("TSLA", 15, 0,  open_=100.0,  high=100.5,  low=99.8,   close=100.2, volume=1000),
        _bar("TSLA", 15, 15, open_=100.2,  high=100.7,  low=100.0,  close=100.4, volume=1000),
        _bar("TSLA", 15, 30, open_=100.4,  high=100.9,  low=100.2,  close=100.6, volume=1000),
        _bar("TSLA", 15, 45, open_=100.6,  high=101.2,  low=100.4,  close=101.0, volume=1000),
        _bar("TSLA", 16, 0,  open_=101.0,  high=101.5,  low=100.8,  close=101.2, volume=1000),
        _bar("TSLA", 16, 15, open_=102.0,  high=103.5,  low=101.8,  close=103.0, volume=2000),
        _bar("TSLA", 16, 30, open_=102.01, high=104.0,  low=101.5,  close=103.5, volume=1500),
    ]
    short_daily = [_daily(close=99.0, day=13), _daily(close=101.0, day=14)]
    scenario = ReplayScenario(
        name="high_watermark_no_daily",
        symbol="TSLA",
        starting_equity=100_000.0,
        daily_bars=short_daily,
        intraday_bars=intraday_bars,
    )
    result = ReplayRunner(
        settings, signal_evaluator=STRATEGY_REGISTRY["high_watermark"], strategy_name="high_watermark"
    ).run(scenario)
    assert result.events == []


# ─── ema_pullback ────────────────────────────────────────────────────────────

def test_ema_pullback_happy_path_produces_entry_filled() -> None:
    """EMA pullback: prior bar closes below EMA, signal bar closes above EMA; fills."""
    settings = make_settings()
    # EMA warmup (alpha=1/3): bars 0–3 close=100.0 → ema_3=100.0
    # bar 4 (prior): close=95.0 → ema_4 = 1/3*95 + 2/3*100 ≈ 98.33; close ≤ ema ✓
    # bar 5 (signal): close=105.0 → ema_5 = 1/3*105 + 2/3*98.33 ≈ 100.55; close > ema ✓
    # relative_volume = 2000/1000 = 2.0 > 1.5  ✓
    # stop_price = round(106.0 + 0.01, 2) = 106.01
    # execution bar open=106.01 == stop_price → fill at 106.01  ✓
    intraday_bars = [
        _bar("TSLA", 15, 0,  open_=99.8,   high=100.3,  low=99.5,   close=100.0, volume=1000),
        _bar("TSLA", 15, 15, open_=99.9,   high=100.4,  low=99.7,   close=100.0, volume=1000),
        _bar("TSLA", 15, 30, open_=100.0,  high=100.5,  low=99.8,   close=100.0, volume=1000),
        _bar("TSLA", 15, 45, open_=100.0,  high=100.5,  low=99.8,   close=100.0, volume=1000),
        _bar("TSLA", 16, 0,  open_=97.0,   high=97.5,   low=94.0,   close=95.0,  volume=1000),
        _bar("TSLA", 16, 15, open_=100.0,  high=106.0,  low=99.5,   close=105.0, volume=2000),
        _bar("TSLA", 16, 30, open_=106.01, high=107.0,  low=105.5,  close=106.5, volume=1500),
    ]
    scenario = ReplayScenario(
        name="ema_pullback_happy",
        symbol="TSLA",
        starting_equity=100_000.0,
        daily_bars=DAILY_BARS,
        intraday_bars=intraday_bars,
    )
    result = ReplayRunner(
        settings, signal_evaluator=STRATEGY_REGISTRY["ema_pullback"], strategy_name="ema_pullback"
    ).run(scenario)
    event_types = [e.event_type for e in result.events]
    assert IntentType.ENTRY_ORDER_PLACED in event_types
    assert IntentType.ENTRY_FILLED in event_types


def test_ema_pullback_insufficient_daily_bars_produces_no_trades() -> None:
    """Trend filter with DAILY_SMA_PERIOD=5 requires 6+ bars; 2 bars → no trades."""
    settings = make_settings()
    intraday_bars = [
        _bar("TSLA", 15, 0,  open_=99.8,   high=100.3,  low=99.5,   close=100.0, volume=1000),
        _bar("TSLA", 15, 15, open_=99.9,   high=100.4,  low=99.7,   close=100.0, volume=1000),
        _bar("TSLA", 15, 30, open_=100.0,  high=100.5,  low=99.8,   close=100.0, volume=1000),
        _bar("TSLA", 15, 45, open_=100.0,  high=100.5,  low=99.8,   close=100.0, volume=1000),
        _bar("TSLA", 16, 0,  open_=97.0,   high=97.5,   low=94.0,   close=95.0,  volume=1000),
        _bar("TSLA", 16, 15, open_=100.0,  high=106.0,  low=99.5,   close=105.0, volume=2000),
        _bar("TSLA", 16, 30, open_=106.01, high=107.0,  low=105.5,  close=106.5, volume=1500),
    ]
    short_daily = [_daily(close=99.0, day=13), _daily(close=101.0, day=14)]
    scenario = ReplayScenario(
        name="ema_pullback_no_daily",
        symbol="TSLA",
        starting_equity=100_000.0,
        daily_bars=short_daily,
        intraday_bars=intraday_bars,
    )
    result = ReplayRunner(
        settings, signal_evaluator=STRATEGY_REGISTRY["ema_pullback"], strategy_name="ema_pullback"
    ).run(scenario)
    assert result.events == []
