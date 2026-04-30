from datetime import datetime, timedelta, timezone

from alpaca_bot.config import Settings
from alpaca_bot.domain import Bar
from alpaca_bot.strategy.momentum import evaluate_momentum_signal


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


def make_daily_bars(closes: list[float]) -> list[Bar]:
    # Start 2026-03-26 at 20:00 UTC; date() in ET (UTC-4) = 2026-03-26.
    # For a signal on 2026-04-24 ET, all 21 bars (Mar 26 – Apr 15) are prior_daily.
    start = datetime(2026, 3, 26, 20, 0, tzinfo=timezone.utc)
    bars: list[Bar] = []
    for offset, close in enumerate(closes):
        bars.append(
            Bar(
                symbol="AAPL",
                timestamp=start + timedelta(days=offset),
                open=close - 0.5,
                high=close + 0.3,
                low=close - 1.0,
                close=close,
                volume=1_000_000 + offset * 1000,
            )
        )
    return bars


def make_intraday_bars(
    *,
    signal_timestamp: datetime,
    signal_high: float,
    signal_close: float,
    signal_volume: float,
) -> list[Bar]:
    start = signal_timestamp - timedelta(minutes=15 * 20)
    bars: list[Bar] = []
    for offset in range(20):
        high = 108.5 + offset * 0.08
        close = high - 0.2
        bars.append(
            Bar(
                symbol="AAPL",
                timestamp=start + timedelta(minutes=15 * offset),
                open=round(close - 0.1, 2),
                high=round(high, 2),
                low=round(close - 0.25, 2),
                close=round(close, 2),
                volume=1000 + offset * 10,
            )
        )
    bars[-1] = Bar(
        symbol="AAPL",
        timestamp=bars[-1].timestamp,
        open=109.55,
        high=110.0,
        low=109.35,
        close=109.75,
        volume=1190,
    )
    bars.append(
        Bar(
            symbol="AAPL",
            timestamp=signal_timestamp,
            open=109.8,
            high=signal_high,
            low=109.7,
            close=signal_close,
            volume=signal_volume,
        )
    )
    return bars


# Signal timestamp at 15:00 UTC = 11:00 AM ET — inside the 10:00–15:30 ET entry window.
_SIGNAL_TS = datetime(2026, 4, 24, 15, 0, tzinfo=timezone.utc)

# 21 daily bars with closes [100..120]; last bar high = 120 + 0.3 = 120.3.
# yesterday_high = max(prior_daily[-1:]).high = 120.3
_DAILY_CLOSES = [100 + i for i in range(21)]


def _make_valid_signal():
    """Return (intraday_bars, daily_bars, settings) for a fully valid momentum setup."""
    daily_bars = make_daily_bars(_DAILY_CLOSES)
    intraday_bars = make_intraday_bars(
        signal_timestamp=_SIGNAL_TS,
        signal_high=121.0,
        signal_close=120.8,
        signal_volume=3000,
    )
    return intraday_bars, daily_bars, make_settings()


def test_momentum_returns_none_for_empty_bars() -> None:
    _, daily_bars, settings = _make_valid_signal()
    result = evaluate_momentum_signal(
        symbol="AAPL",
        intraday_bars=[],
        signal_index=0,
        daily_bars=daily_bars,
        settings=settings,
    )
    assert result is None


def test_momentum_returns_none_outside_entry_window() -> None:
    # 13:45 UTC = 09:45 ET — inside the REGULAR session but before the 10:00 ET entry window start.
    early_ts = datetime(2026, 4, 24, 13, 45, tzinfo=timezone.utc)
    daily_bars = make_daily_bars(_DAILY_CLOSES)
    intraday_bars = make_intraday_bars(
        signal_timestamp=early_ts,
        signal_high=121.0,
        signal_close=120.8,
        signal_volume=3000,
    )
    result = evaluate_momentum_signal(
        symbol="AAPL",
        intraday_bars=intraday_bars,
        signal_index=len(intraday_bars) - 1,
        daily_bars=daily_bars,
        settings=make_settings(),
    )
    assert result is None


def test_momentum_returns_none_when_daily_trend_fails() -> None:
    # bars[19].close=80 falls below the 20-bar SMA (~107); trend filter rejects it.
    failing_daily_bars = make_daily_bars([100 + i for i in range(19)] + [80.0, 90.0])
    intraday_bars, _, settings = _make_valid_signal()
    result = evaluate_momentum_signal(
        symbol="AAPL",
        intraday_bars=intraday_bars,
        signal_index=len(intraday_bars) - 1,
        daily_bars=failing_daily_bars,
        settings=settings,
    )
    assert result is None


def test_momentum_returns_none_when_insufficient_prior_daily_bars() -> None:
    # Only 1 daily bar, dated today (2026-04-24 ET), so prior_daily will be empty.
    today_bar = Bar(
        symbol="AAPL",
        timestamp=datetime(2026, 4, 24, 20, 0, tzinfo=timezone.utc),
        open=119.5,
        high=120.3,
        low=119.0,
        close=120.0,
        volume=1_000_000,
    )
    intraday_bars, _, settings = _make_valid_signal()
    result = evaluate_momentum_signal(
        symbol="AAPL",
        intraday_bars=intraday_bars,
        signal_index=len(intraday_bars) - 1,
        daily_bars=[today_bar],
        settings=settings,
    )
    assert result is None


def test_momentum_returns_none_when_high_below_yesterday_high() -> None:
    # yesterday_high = 120.3; signal_bar.high=119.0 <= 120.3 → rejected.
    daily_bars = make_daily_bars(_DAILY_CLOSES)
    intraday_bars = make_intraday_bars(
        signal_timestamp=_SIGNAL_TS,
        signal_high=119.0,
        signal_close=119.5,
        signal_volume=3000,
    )
    result = evaluate_momentum_signal(
        symbol="AAPL",
        intraday_bars=intraday_bars,
        signal_index=len(intraday_bars) - 1,
        daily_bars=daily_bars,
        settings=make_settings(),
    )
    assert result is None


def test_momentum_returns_none_when_close_below_yesterday_high() -> None:
    # signal_bar.high=121.0 (above 120.3) but close=119.5 (below 120.3) → rejected.
    daily_bars = make_daily_bars(_DAILY_CLOSES)
    intraday_bars = make_intraday_bars(
        signal_timestamp=_SIGNAL_TS,
        signal_high=121.0,
        signal_close=119.5,
        signal_volume=3000,
    )
    result = evaluate_momentum_signal(
        symbol="AAPL",
        intraday_bars=intraday_bars,
        signal_index=len(intraday_bars) - 1,
        daily_bars=daily_bars,
        settings=make_settings(),
    )
    assert result is None


def test_momentum_returns_none_when_relative_volume_too_low() -> None:
    # Prior bars avg volume ~1095; signal_bar.volume=500 → rel_vol < 1.5.
    daily_bars = make_daily_bars(_DAILY_CLOSES)
    intraday_bars = make_intraday_bars(
        signal_timestamp=_SIGNAL_TS,
        signal_high=121.0,
        signal_close=120.8,
        signal_volume=500,
    )
    result = evaluate_momentum_signal(
        symbol="AAPL",
        intraday_bars=intraday_bars,
        signal_index=len(intraday_bars) - 1,
        daily_bars=daily_bars,
        settings=make_settings(),
    )
    assert result is None


def test_momentum_returns_signal_on_valid_breakout() -> None:
    intraday_bars, daily_bars, settings = _make_valid_signal()
    result = evaluate_momentum_signal(
        symbol="AAPL",
        intraday_bars=intraday_bars,
        signal_index=len(intraday_bars) - 1,
        daily_bars=daily_bars,
        settings=settings,
    )
    assert result is not None
    assert result.entry_level == 120.3


def test_momentum_signal_entry_level_equals_yesterday_high() -> None:
    # yesterday_high = prior_daily[-1].high = 120 + 0.3 = 120.3
    intraday_bars, daily_bars, settings = _make_valid_signal()
    result = evaluate_momentum_signal(
        symbol="AAPL",
        intraday_bars=intraday_bars,
        signal_index=len(intraday_bars) - 1,
        daily_bars=daily_bars,
        settings=settings,
    )
    assert result is not None
    assert result.entry_level == 120.3


def test_momentum_signal_stop_price_above_signal_bar_high() -> None:
    # stop_price = round(121.0 + 0.01, 2) = 121.01
    intraday_bars, daily_bars, settings = _make_valid_signal()
    result = evaluate_momentum_signal(
        symbol="AAPL",
        intraday_bars=intraday_bars,
        signal_index=len(intraday_bars) - 1,
        daily_bars=daily_bars,
        settings=settings,
    )
    assert result is not None
    assert result.stop_price == 121.01


def test_momentum_signal_initial_stop_below_entry_level() -> None:
    intraday_bars, daily_bars, settings = _make_valid_signal()
    result = evaluate_momentum_signal(
        symbol="AAPL",
        intraday_bars=intraday_bars,
        signal_index=len(intraday_bars) - 1,
        daily_bars=daily_bars,
        settings=settings,
    )
    assert result is not None
    assert result.initial_stop_price < result.entry_level


def test_momentum_returns_none_when_atr_not_computable() -> None:
    # calculate_atr needs >= period+1 = 15 bars; only 5 daily bars here → None.
    sparse_daily = make_daily_bars([100 + i for i in range(5)])
    intraday_bars = make_intraday_bars(
        signal_timestamp=_SIGNAL_TS,
        signal_high=121.0,
        signal_close=120.8,
        signal_volume=3000,
    )
    result = evaluate_momentum_signal(
        symbol="AAPL",
        intraday_bars=intraday_bars,
        signal_index=len(intraday_bars) - 1,
        daily_bars=sparse_daily,
        settings=make_settings(),
    )
    assert result is None
