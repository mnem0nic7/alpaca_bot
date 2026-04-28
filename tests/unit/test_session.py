from __future__ import annotations

from datetime import datetime, time, timezone

import pytest

from alpaca_bot.config import Settings
from alpaca_bot.strategy.session import (
    SessionType,
    detect_session_type,
    is_entry_window,
    is_flatten_time,
)


def _make_ts(hour: int, minute: int = 0) -> datetime:
    """UTC timestamp that maps to the given ET wall clock time on 2026-04-28."""
    from datetime import timedelta
    # ET is UTC-4 on 2026-04-28 (EDT)
    base = datetime(2026, 4, 28, 0, 0, tzinfo=timezone.utc)
    return base + timedelta(hours=hour + 4, minutes=minute)


def _settings(**overrides) -> Settings:
    base = {
        "TRADING_MODE": "paper",
        "ENABLE_LIVE_TRADING": "false",
        "STRATEGY_VERSION": "v1",
        "DATABASE_URL": "postgresql://test/db",
        "MARKET_DATA_FEED": "iex",
        "SYMBOLS": "AAPL",
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
        "EXTENDED_HOURS_ENABLED": "false",
        "PRE_MARKET_ENTRY_WINDOW_START": "04:00",
        "PRE_MARKET_ENTRY_WINDOW_END": "09:20",
        "AFTER_HOURS_ENTRY_WINDOW_START": "16:05",
        "AFTER_HOURS_ENTRY_WINDOW_END": "19:30",
        "EXTENDED_HOURS_FLATTEN_TIME": "19:45",
        "EXTENDED_HOURS_LIMIT_OFFSET_PCT": "0.001",
    }
    base.update(overrides)
    return Settings.from_env(base)


# --- detect_session_type ---

@pytest.mark.parametrize("hour, minute, expected", [
    (3, 59, SessionType.CLOSED),
    (4, 0, SessionType.PRE_MARKET),
    (9, 29, SessionType.PRE_MARKET),
    (9, 30, SessionType.REGULAR),
    (15, 59, SessionType.REGULAR),
    (16, 0, SessionType.AFTER_HOURS),
    (19, 59, SessionType.AFTER_HOURS),
    (20, 0, SessionType.CLOSED),
    (0, 0, SessionType.CLOSED),
])
def test_detect_session_type_boundaries(hour, minute, expected):
    settings = _settings()
    ts = _make_ts(hour, minute)
    assert detect_session_type(ts, settings) is expected


# --- is_entry_window ---

def test_is_entry_window_pre_market_inside():
    settings = _settings()
    ts = _make_ts(6, 0)
    assert is_entry_window(ts, settings, SessionType.PRE_MARKET) is True


def test_is_entry_window_pre_market_outside():
    settings = _settings()
    ts = _make_ts(9, 25)  # past PRE_MARKET_ENTRY_WINDOW_END 09:20
    assert is_entry_window(ts, settings, SessionType.PRE_MARKET) is False


def test_is_entry_window_after_hours_inside():
    settings = _settings()
    ts = _make_ts(17, 0)
    assert is_entry_window(ts, settings, SessionType.AFTER_HOURS) is True


def test_is_entry_window_after_hours_outside():
    settings = _settings()
    ts = _make_ts(20, 0)
    assert is_entry_window(ts, settings, SessionType.AFTER_HOURS) is False


def test_is_entry_window_regular_delegates_to_settings():
    settings = _settings()
    ts = _make_ts(12, 0)
    assert is_entry_window(ts, settings, SessionType.REGULAR) is True


def test_is_entry_window_closed_always_false():
    settings = _settings()
    ts = _make_ts(1, 0)
    assert is_entry_window(ts, settings, SessionType.CLOSED) is False


# --- is_flatten_time ---

def test_is_flatten_time_after_hours_before():
    settings = _settings()
    ts = _make_ts(19, 30)  # before 19:45
    assert is_flatten_time(ts, settings, SessionType.AFTER_HOURS) is False


def test_is_flatten_time_after_hours_at():
    settings = _settings()
    ts = _make_ts(19, 45)
    assert is_flatten_time(ts, settings, SessionType.AFTER_HOURS) is True


def test_is_flatten_time_regular():
    settings = _settings()
    ts = _make_ts(15, 45)
    assert is_flatten_time(ts, settings, SessionType.REGULAR) is True


def test_is_flatten_time_pre_market_never():
    settings = _settings()
    ts = _make_ts(9, 25)
    assert is_flatten_time(ts, settings, SessionType.PRE_MARKET) is False
