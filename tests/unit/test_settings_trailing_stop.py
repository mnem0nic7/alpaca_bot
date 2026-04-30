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


def test_trailing_stop_settings_defaults():
    s = Settings.from_env(_base())
    assert s.trailing_stop_atr_multiplier == pytest.approx(0.0)
    assert s.trailing_stop_profit_trigger_r == pytest.approx(1.0)


def test_trailing_stop_atr_multiplier_env_parsed():
    env = {**_base(), "TRAILING_STOP_ATR_MULTIPLIER": "1.5"}
    s = Settings.from_env(env)
    assert s.trailing_stop_atr_multiplier == pytest.approx(1.5)


def test_trailing_stop_profit_trigger_r_env_parsed():
    env = {**_base(), "TRAILING_STOP_PROFIT_TRIGGER_R": "2.0"}
    s = Settings.from_env(env)
    assert s.trailing_stop_profit_trigger_r == pytest.approx(2.0)


def test_trailing_stop_atr_multiplier_negative_raises():
    env = {**_base(), "TRAILING_STOP_ATR_MULTIPLIER": "-0.1"}
    with pytest.raises(ValueError, match="TRAILING_STOP_ATR_MULTIPLIER"):
        Settings.from_env(env)


def test_trailing_stop_atr_multiplier_too_large_raises():
    env = {**_base(), "TRAILING_STOP_ATR_MULTIPLIER": "10.1"}
    with pytest.raises(ValueError, match="TRAILING_STOP_ATR_MULTIPLIER"):
        Settings.from_env(env)


def test_trailing_stop_profit_trigger_r_zero_raises():
    env = {**_base(), "TRAILING_STOP_PROFIT_TRIGGER_R": "0.0"}
    with pytest.raises(ValueError, match="TRAILING_STOP_PROFIT_TRIGGER_R"):
        Settings.from_env(env)


def test_trailing_stop_profit_trigger_r_negative_raises():
    env = {**_base(), "TRAILING_STOP_PROFIT_TRIGGER_R": "-1.0"}
    with pytest.raises(ValueError, match="TRAILING_STOP_PROFIT_TRIGGER_R"):
        Settings.from_env(env)
