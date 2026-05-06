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
        "SYMBOLS": "AAPL,MSFT",
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


def test_fractionable_symbols_defaults_to_empty_frozenset():
    settings = Settings.from_env(_base())
    assert settings.fractionable_symbols == frozenset()
    assert isinstance(settings.fractionable_symbols, frozenset)


def test_fractionable_symbols_not_read_from_env():
    """fractionable_symbols is an operational field — never from env."""
    env = {**_base(), "FRACTIONABLE_SYMBOLS": "AAPL,MSFT"}
    settings = Settings.from_env(env)
    assert settings.fractionable_symbols == frozenset()


def test_min_position_notional_defaults_to_zero():
    settings = Settings.from_env(_base())
    assert settings.min_position_notional == 0.0


def test_min_position_notional_read_from_env():
    settings = Settings.from_env({**_base(), "MIN_POSITION_NOTIONAL": "50.0"})
    assert settings.min_position_notional == 50.0


def test_min_position_notional_zero_disables_guard():
    settings = Settings.from_env({**_base(), "MIN_POSITION_NOTIONAL": "0.0"})
    assert settings.min_position_notional == 0.0


def test_min_position_notional_negative_raises():
    with pytest.raises(ValueError, match="MIN_POSITION_NOTIONAL"):
        Settings.from_env({**_base(), "MIN_POSITION_NOTIONAL": "-1.0"})
