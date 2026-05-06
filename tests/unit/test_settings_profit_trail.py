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


def test_profit_trail_defaults():
    s = Settings.from_env(_base())
    assert s.enable_profit_trail is False
    assert s.profit_trail_pct == pytest.approx(0.95)


def test_enable_profit_trail_env_parsed():
    env = {**_base(), "ENABLE_PROFIT_TRAIL": "true"}
    s = Settings.from_env(env)
    assert s.enable_profit_trail is True


def test_profit_trail_pct_env_parsed():
    env = {**_base(), "PROFIT_TRAIL_PCT": "0.85"}
    s = Settings.from_env(env)
    assert s.profit_trail_pct == pytest.approx(0.85)


def test_profit_trail_pct_zero_raises():
    env = {**_base(), "PROFIT_TRAIL_PCT": "0.0"}
    with pytest.raises(ValueError, match="PROFIT_TRAIL_PCT"):
        Settings.from_env(env)


def test_profit_trail_pct_one_raises():
    env = {**_base(), "PROFIT_TRAIL_PCT": "1.0"}
    with pytest.raises(ValueError, match="PROFIT_TRAIL_PCT"):
        Settings.from_env(env)
