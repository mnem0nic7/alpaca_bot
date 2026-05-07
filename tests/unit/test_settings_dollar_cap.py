from alpaca_bot.config import Settings
import pytest


def _base_env(**overrides):
    base = {
        "TRADING_MODE": "paper",
        "ENABLE_LIVE_TRADING": "false",
        "STRATEGY_VERSION": "v1",
        "DATABASE_URL": "postgresql://x:y@localhost/db",
        "MARKET_DATA_FEED": "sip",
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
        "PER_SYMBOL_LOSS_LIMIT_PCT": "0.0",
    }
    base.update(overrides)
    return base


def test_max_loss_per_trade_dollars_defaults_to_none():
    s = Settings.from_env(_base_env())
    assert s.max_loss_per_trade_dollars is None


def test_max_loss_per_trade_dollars_parsed_from_env():
    s = Settings.from_env(_base_env(MAX_LOSS_PER_TRADE_DOLLARS="15.0"))
    assert s.max_loss_per_trade_dollars == 15.0


def test_max_loss_per_trade_dollars_zero_raises():
    with pytest.raises(ValueError, match="MAX_LOSS_PER_TRADE_DOLLARS must be > 0"):
        Settings.from_env(_base_env(MAX_LOSS_PER_TRADE_DOLLARS="0"))


def test_max_loss_per_trade_dollars_negative_raises():
    with pytest.raises(ValueError, match="MAX_LOSS_PER_TRADE_DOLLARS must be > 0"):
        Settings.from_env(_base_env(MAX_LOSS_PER_TRADE_DOLLARS="-5"))
