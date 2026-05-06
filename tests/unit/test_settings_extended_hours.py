from __future__ import annotations

import pytest

from alpaca_bot.config import Settings


def _base() -> dict:
    return {
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
    }


def test_settings_extended_hours_defaults():
    s = Settings.from_env(_base())
    assert s.extended_hours_enabled is False
    from datetime import time
    assert s.pre_market_entry_window_start == time(4, 0)
    assert s.pre_market_entry_window_end == time(9, 20)
    assert s.after_hours_entry_window_start == time(16, 5)
    assert s.after_hours_entry_window_end == time(19, 30)
    assert s.extended_hours_flatten_time == time(19, 45)
    assert s.extended_hours_limit_offset_pct == pytest.approx(0.001)


def test_settings_extended_hours_can_be_enabled():
    env = {**_base(), "EXTENDED_HOURS_ENABLED": "true"}
    s = Settings.from_env(env)
    assert s.extended_hours_enabled is True


def test_validation_pre_market_end_must_be_before_regular_open():
    env = {
        **_base(),
        "EXTENDED_HOURS_ENABLED": "true",
        "PRE_MARKET_ENTRY_WINDOW_START": "04:00",
        "PRE_MARKET_ENTRY_WINDOW_END": "09:35",  # after 09:30
    }
    with pytest.raises(ValueError, match="PRE_MARKET_ENTRY_WINDOW_END"):
        Settings.from_env(env)


def test_validation_pre_market_start_must_be_before_end():
    env = {
        **_base(),
        "EXTENDED_HOURS_ENABLED": "true",
        "PRE_MARKET_ENTRY_WINDOW_START": "09:00",
        "PRE_MARKET_ENTRY_WINDOW_END": "08:00",
    }
    with pytest.raises(ValueError, match="PRE_MARKET_ENTRY_WINDOW_START"):
        Settings.from_env(env)


def test_validation_after_hours_start_must_be_after_regular_close():
    env = {
        **_base(),
        "EXTENDED_HOURS_ENABLED": "true",
        "AFTER_HOURS_ENTRY_WINDOW_START": "15:59",
    }
    with pytest.raises(ValueError, match="AFTER_HOURS_ENTRY_WINDOW_START"):
        Settings.from_env(env)


def test_validation_after_hours_ordering():
    env = {
        **_base(),
        "EXTENDED_HOURS_ENABLED": "true",
        "AFTER_HOURS_ENTRY_WINDOW_START": "16:05",
        "AFTER_HOURS_ENTRY_WINDOW_END": "19:30",
        "EXTENDED_HOURS_FLATTEN_TIME": "19:20",  # before end
    }
    with pytest.raises(ValueError, match="EXTENDED_HOURS_FLATTEN_TIME"):
        Settings.from_env(env)


def test_validation_limit_offset_must_be_positive():
    env = {**_base(), "EXTENDED_HOURS_LIMIT_OFFSET_PCT": "0"}
    with pytest.raises(ValueError, match="EXTENDED_HOURS_LIMIT_OFFSET_PCT"):
        Settings.from_env(env)


def test_validation_only_runs_when_enabled():
    """Invalid window times are not checked when extended_hours_enabled=False."""
    env = {
        **_base(),
        "EXTENDED_HOURS_ENABLED": "false",
        "PRE_MARKET_ENTRY_WINDOW_END": "09:45",  # invalid but not checked
    }
    s = Settings.from_env(env)
    assert s.extended_hours_enabled is False


def test_extended_hours_max_spread_pct_defaults_to_1_pct():
    s = Settings.from_env(_base())
    assert s.extended_hours_max_spread_pct == pytest.approx(0.01)


def test_extended_hours_max_spread_pct_must_be_at_least_max_spread_pct():
    with pytest.raises(ValueError, match="EXTENDED_HOURS_MAX_SPREAD_PCT"):
        Settings.from_env({
            **_base(),
            "MAX_SPREAD_PCT": "0.01",
            "EXTENDED_HOURS_MAX_SPREAD_PCT": "0.005",  # stricter than regular — invalid
        })
