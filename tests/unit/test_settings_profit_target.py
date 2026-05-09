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


def make_settings(**overrides) -> Settings:
    return Settings.from_env({**_base(), **{k: str(v) for k, v in overrides.items()}})


def test_profit_target_defaults():
    s = make_settings()
    assert s.enable_profit_target is False
    assert s.profit_target_r == pytest.approx(2.0)
    assert s.trend_filter_exit_lookback_days == 1


def test_profit_target_r_invalid():
    with pytest.raises(ValueError, match="PROFIT_TARGET_R"):
        make_settings(PROFIT_TARGET_R="0")


def test_profit_target_r_negative():
    with pytest.raises(ValueError, match="PROFIT_TARGET_R"):
        make_settings(PROFIT_TARGET_R="-1.0")


def test_trend_filter_exit_lookback_days_invalid():
    with pytest.raises(ValueError, match="TREND_FILTER_EXIT_LOOKBACK_DAYS"):
        make_settings(TREND_FILTER_EXIT_LOOKBACK_DAYS="0")


def test_profit_target_enabled_from_env():
    s = make_settings(ENABLE_PROFIT_TARGET="true", PROFIT_TARGET_R="3.5")
    assert s.enable_profit_target is True
    assert s.profit_target_r == pytest.approx(3.5)


def test_trend_filter_exit_lookback_days_from_env():
    s = make_settings(TREND_FILTER_EXIT_LOOKBACK_DAYS="3")
    assert s.trend_filter_exit_lookback_days == 3
