from datetime import datetime, timedelta, timezone

import pytest

from alpaca_bot.config import Settings
from alpaca_bot.domain import Bar
from alpaca_bot.risk import calculate_position_size
from alpaca_bot.strategy.breakout import (
    daily_trend_filter_passes,
    evaluate_breakout_signal,
)
from alpaca_bot.web.auth import hash_password


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


def test_settings_require_explicit_live_gate() -> None:
    with pytest.raises(ValueError, match="ENABLE_LIVE_TRADING=true"):
        make_settings(TRADING_MODE="live", ENABLE_LIVE_TRADING="false")


def test_settings_require_dashboard_auth_fields_when_enabled() -> None:
    with pytest.raises(ValueError, match="DASHBOARD_AUTH_USERNAME"):
        make_settings(DASHBOARD_AUTH_ENABLED="true")


def test_settings_parse_dashboard_auth_configuration() -> None:
    settings = make_settings(
        DASHBOARD_AUTH_ENABLED="true",
        DASHBOARD_AUTH_USERNAME="m7ga.77@gmail.com",
        DASHBOARD_AUTH_PASSWORD_HASH=hash_password(
            "secret-password",
            salt=bytes.fromhex("000102030405060708090a0b0c0d0e0f"),
        ),
    )

    assert settings.dashboard_auth_enabled is True
    assert settings.dashboard_auth_username == "m7ga.77@gmail.com"


def test_daily_trend_filter_requires_latest_close_above_sma() -> None:
    failing_daily_bars = make_daily_bars([100 + index for index in range(19)] + [80.0])

    assert daily_trend_filter_passes(failing_daily_bars, make_settings()) is False


def test_breakout_uses_previous_twenty_completed_bars_only() -> None:
    daily_bars = make_daily_bars([90 + index for index in range(20)])
    intraday_bars = make_intraday_bars(
        signal_timestamp=datetime(2026, 4, 24, 19, 0, tzinfo=timezone.utc),
        signal_high=111.0,
        signal_close=110.8,
        signal_volume=2000,
    )
    intraday_bars.append(
        Bar(
            symbol="AAPL",
            timestamp=datetime(2026, 4, 24, 19, 15, tzinfo=timezone.utc),
            open=199.0,
            high=200.0,
            low=198.0,
            close=199.5,
            volume=9000,
        )
    )

    signal = evaluate_breakout_signal(
        symbol="AAPL",
        intraday_bars=intraday_bars,
        signal_index=20,
        daily_bars=daily_bars,
        settings=make_settings(),
    )

    assert signal is not None
    assert signal.entry_level == 110.0


def test_breakout_rejected_before_entry_window() -> None:
    daily_bars = make_daily_bars([90 + index for index in range(20)])
    intraday_bars = make_intraday_bars(
        signal_timestamp=datetime(2026, 4, 24, 13, 45, tzinfo=timezone.utc),
        signal_high=111.0,
        signal_close=110.8,
        signal_volume=2000,
    )

    signal = evaluate_breakout_signal(
        symbol="AAPL",
        intraday_bars=intraday_bars,
        signal_index=20,
        daily_bars=daily_bars,
        settings=make_settings(),
    )

    assert signal is None


def test_position_size_respects_risk_and_notional_caps() -> None:
    quantity = calculate_position_size(
        equity=100000,
        entry_price=111.01,
        stop_price=109.89,
        settings=make_settings(),
    )

    assert quantity == 45
