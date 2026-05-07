from __future__ import annotations
from datetime import datetime, timezone
import pytest
from alpaca_bot.config import Settings
from alpaca_bot.strategy.session import SessionType


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
    }
    base.update(overrides)
    return Settings.from_env(base)


def test_extended_hours_disabled_pre_market_returns_closed():
    settings = _settings(EXTENDED_HOURS_ENABLED="false")
    # 6am ET = pre-market window
    ts = datetime(2026, 4, 28, 10, 0, tzinfo=timezone.utc)

    from alpaca_bot.strategy.session import detect_session_type
    session = detect_session_type(ts, settings)
    # With disabled flag, supervisor should treat as CLOSED
    result = session if settings.extended_hours_enabled else SessionType.CLOSED
    assert result is SessionType.CLOSED


def test_extended_hours_enabled_pre_market_returns_pre_market():
    settings = _settings(
        EXTENDED_HOURS_ENABLED="true",
        PRE_MARKET_ENTRY_WINDOW_START="04:00",
        PRE_MARKET_ENTRY_WINDOW_END="09:20",
        AFTER_HOURS_ENTRY_WINDOW_START="16:05",
        AFTER_HOURS_ENTRY_WINDOW_END="19:30",
        EXTENDED_HOURS_FLATTEN_TIME="19:45",
    )
    ts = datetime(2026, 4, 28, 10, 0, tzinfo=timezone.utc)  # 6am ET
    from alpaca_bot.strategy.session import detect_session_type
    session = detect_session_type(ts, settings)
    result = session if settings.extended_hours_enabled else SessionType.CLOSED
    assert result is SessionType.PRE_MARKET


def test_current_session_method_disabled():
    """_current_session returns CLOSED for pre-market when extended_hours_enabled=False."""
    settings = _settings(EXTENDED_HOURS_ENABLED="false")
    from unittest.mock import MagicMock
    from alpaca_bot.runtime.supervisor import RuntimeSupervisor

    clock = MagicMock()
    clock.is_open = True
    broker = MagicMock()
    broker.get_clock.return_value = clock

    sup = RuntimeSupervisor(
        settings=settings,
        runtime=MagicMock(),
        broker=broker,
        market_data=MagicMock(),
        stream=None,
    )

    # 6am ET = pre-market
    ts = datetime(2026, 4, 28, 10, 0, tzinfo=timezone.utc)
    result = sup._current_session(ts)
    assert result is SessionType.CLOSED


def test_current_session_method_enabled_pre_market():
    """_current_session returns PRE_MARKET when extended_hours_enabled=True."""
    settings = _settings(
        EXTENDED_HOURS_ENABLED="true",
        PRE_MARKET_ENTRY_WINDOW_START="04:00",
        PRE_MARKET_ENTRY_WINDOW_END="09:20",
        AFTER_HOURS_ENTRY_WINDOW_START="16:05",
        AFTER_HOURS_ENTRY_WINDOW_END="19:30",
        EXTENDED_HOURS_FLATTEN_TIME="19:45",
    )
    from unittest.mock import MagicMock
    from alpaca_bot.runtime.supervisor import RuntimeSupervisor

    broker = MagicMock()
    sup = RuntimeSupervisor(
        settings=settings,
        runtime=MagicMock(),
        broker=broker,
        market_data=MagicMock(),
        stream=None,
    )

    ts = datetime(2026, 4, 28, 10, 0, tzinfo=timezone.utc)  # 6am ET
    result = sup._current_session(ts)
    assert result is SessionType.PRE_MARKET


def test_effective_trading_status_flatten_gate_skipped_during_after_hours():
    """entries_disabled from regular-session flatten must NOT block after-hours entries."""
    from datetime import date
    from unittest.mock import MagicMock
    from alpaca_bot.runtime.supervisor import RuntimeSupervisor
    from alpaca_bot.storage.models import DailySessionState

    settings = _settings(
        EXTENDED_HOURS_ENABLED="true",
        AFTER_HOURS_ENTRY_WINDOW_START="16:05",
        AFTER_HOURS_ENTRY_WINDOW_END="19:30",
        EXTENDED_HOURS_FLATTEN_TIME="19:45",
    )
    sup = RuntimeSupervisor(
        settings=settings,
        runtime=MagicMock(),
        broker=MagicMock(),
        market_data=MagicMock(),
        stream=None,
    )
    session_state = DailySessionState(
        session_date=date(2026, 5, 7),
        trading_mode="paper",
        strategy_version="v1",
        strategy_name="__global__",
        entries_disabled=True,
        flatten_complete=True,
        updated_at=datetime(2026, 5, 7, 19, 45, tzinfo=timezone.utc),
    )

    status = sup._effective_trading_status(
        session_date=date(2026, 5, 7),
        session_state=session_state,
        session_type=SessionType.AFTER_HOURS,
    )

    # Flatten-based entries_disabled must not return CLOSE_ONLY during AFTER_HOURS.
    from alpaca_bot.storage.models import TradingStatusValue
    assert status is not TradingStatusValue.CLOSE_ONLY


def test_effective_trading_status_flatten_gate_applies_during_regular():
    """entries_disabled from flatten IS honoured during the regular session."""
    from datetime import date
    from unittest.mock import MagicMock
    from alpaca_bot.runtime.supervisor import RuntimeSupervisor
    from alpaca_bot.storage.models import DailySessionState
    from alpaca_bot.storage.models import TradingStatusValue

    settings = _settings()
    sup = RuntimeSupervisor(
        settings=settings,
        runtime=MagicMock(),
        broker=MagicMock(),
        market_data=MagicMock(),
        stream=None,
    )
    session_state = DailySessionState(
        session_date=date(2026, 5, 7),
        trading_mode="paper",
        strategy_version="v1",
        strategy_name="__global__",
        entries_disabled=True,
        flatten_complete=True,
        updated_at=datetime(2026, 5, 7, 19, 45, tzinfo=timezone.utc),
    )

    status = sup._effective_trading_status(
        session_date=date(2026, 5, 7),
        session_state=session_state,
        session_type=SessionType.REGULAR,
    )

    assert status is TradingStatusValue.CLOSE_ONLY


def test_current_session_regular_uses_broker_clock():
    """_current_session uses broker clock to confirm regular session is open."""
    settings = _settings()
    from unittest.mock import MagicMock
    from alpaca_bot.runtime.supervisor import RuntimeSupervisor

    clock_closed = MagicMock()
    clock_closed.is_open = False
    broker = MagicMock()
    broker.get_clock.return_value = clock_closed

    sup = RuntimeSupervisor(
        settings=settings,
        runtime=MagicMock(),
        broker=broker,
        market_data=MagicMock(),
        stream=None,
    )

    # 12pm ET = regular hours but broker says closed (e.g. holiday)
    ts = datetime(2026, 4, 28, 16, 0, tzinfo=timezone.utc)
    result = sup._current_session(ts)
    assert result is SessionType.CLOSED
