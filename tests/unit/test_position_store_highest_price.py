from __future__ import annotations

from datetime import datetime, timezone

import pytest

from alpaca_bot.config import TradingMode
from alpaca_bot.storage.models import PositionRecord


def test_position_record_has_highest_price_field():
    """PositionRecord must carry highest_price through the storage layer."""
    rec = PositionRecord(
        symbol="AAPL",
        trading_mode=TradingMode.PAPER,
        strategy_version="v1",
        quantity=10.0,
        entry_price=3.00,
        stop_price=2.97,
        initial_stop_price=2.97,
        opened_at=datetime(2026, 5, 1, tzinfo=timezone.utc),
        highest_price=3.20,
    )
    assert rec.highest_price == 3.20


def test_position_record_highest_price_defaults_to_none():
    rec = PositionRecord(
        symbol="AAPL",
        trading_mode=TradingMode.PAPER,
        strategy_version="v1",
        quantity=10.0,
        entry_price=3.00,
        stop_price=2.97,
        initial_stop_price=2.97,
        opened_at=datetime(2026, 5, 1, tzinfo=timezone.utc),
    )
    assert rec.highest_price is None
