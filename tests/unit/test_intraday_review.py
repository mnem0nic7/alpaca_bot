from __future__ import annotations

from datetime import date, datetime, timezone
from importlib import import_module
from types import SimpleNamespace

import pytest

from alpaca_bot.config import Settings
from alpaca_bot.runtime.daily_summary import trailing_consecutive_losses, build_intraday_digest
from alpaca_bot.storage import AuditEvent, DailySessionState
from alpaca_bot.execution import BrokerAccount


# ── Shared helpers ────────────────────────────────────────────────────────────

_SESSION_DATE = date(2026, 5, 6)
_NOW = datetime(2026, 5, 6, 18, 30, tzinfo=timezone.utc)  # 14:30 ET


def _make_settings(**overrides) -> Settings:
    base = {
        "TRADING_MODE": "paper",
        "ENABLE_LIVE_TRADING": "false",
        "STRATEGY_VERSION": "v1-breakout",
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
    }
    base.update(overrides)
    return Settings.from_env(base)


def _trade(
    exit_fill: float = 155.0,
    entry_fill: float = 150.0,
    qty: int = 10,
    exit_time: str = "2026-05-06T14:00:00+00:00",
) -> dict:
    return {
        "entry_fill": entry_fill,
        "exit_fill": exit_fill,
        "qty": qty,
        "exit_time": exit_time,
    }


# ── Settings tests ────────────────────────────────────────────────────────────


def test_settings_intraday_digest_interval_cycles_default_zero():
    s = _make_settings()
    assert s.intraday_digest_interval_cycles == 0


def test_settings_intraday_consecutive_loss_gate_default_zero():
    s = _make_settings()
    assert s.intraday_consecutive_loss_gate == 0


def test_settings_intraday_digest_interval_cycles_parsed():
    s = _make_settings(INTRADAY_DIGEST_INTERVAL_CYCLES="60")
    assert s.intraday_digest_interval_cycles == 60


def test_settings_intraday_consecutive_loss_gate_parsed():
    s = _make_settings(INTRADAY_CONSECUTIVE_LOSS_GATE="3")
    assert s.intraday_consecutive_loss_gate == 3


def test_settings_intraday_digest_interval_cycles_negative_raises():
    with pytest.raises(ValueError, match="INTRADAY_DIGEST_INTERVAL_CYCLES"):
        _make_settings(INTRADAY_DIGEST_INTERVAL_CYCLES="-1")


def test_settings_intraday_consecutive_loss_gate_negative_raises():
    with pytest.raises(ValueError, match="INTRADAY_CONSECUTIVE_LOSS_GATE"):
        _make_settings(INTRADAY_CONSECUTIVE_LOSS_GATE="-1")
