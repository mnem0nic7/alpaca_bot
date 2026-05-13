from __future__ import annotations

from datetime import datetime, timezone

import pytest

from alpaca_bot.config import TradingMode
from alpaca_bot.domain.decision_record import DecisionRecord
from alpaca_bot.domain.models import MarketContext, OpenPosition
from alpaca_bot.storage.models import PositionRecord


def test_market_context_defaults():
    ctx = MarketContext(as_of=datetime(2026, 1, 1, tzinfo=timezone.utc))
    assert ctx.vix_close is None
    assert ctx.vix_sma is None
    assert ctx.vix_above_sma is None
    assert ctx.sector_etf_states == {}
    assert ctx.sector_passing_pct is None


def test_market_context_populated():
    ctx = MarketContext(
        as_of=datetime(2026, 1, 1, tzinfo=timezone.utc),
        vix_close=18.5,
        vix_sma=17.2,
        vix_above_sma=True,
        sector_etf_states={"XLK": True, "XLF": False},
        sector_passing_pct=0.5,
    )
    assert ctx.vix_close == 18.5
    assert ctx.vix_above_sma is True
    assert ctx.sector_etf_states["XLK"] is True


def test_market_context_is_frozen():
    ctx = MarketContext(as_of=datetime(2026, 1, 1, tzinfo=timezone.utc))
    with pytest.raises(Exception):
        ctx.vix_close = 10.0  # type: ignore


def test_decision_record_new_fields_default_to_none():
    dr = DecisionRecord(
        cycle_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        symbol="AAPL",
        strategy_name="breakout",
        trading_mode="paper",
        strategy_version="v1",
        decision="rejected",
        reject_stage="pre_filter",
        reject_reason="no_signal",
        entry_level=None,
        signal_bar_close=None,
        relative_volume=None,
        atr=None,
        stop_price=None,
        limit_price=None,
        initial_stop_price=None,
        quantity=None,
        risk_per_share=None,
        equity=10000.0,
        filter_results={},
    )
    assert dr.vix_close is None
    assert dr.vix_above_sma is None
    assert dr.sector_passing_pct is None
    assert dr.vwap_at_signal is None
    assert dr.signal_bar_above_vwap is None


def test_open_position_has_lowest_price_field():
    pos = OpenPosition(
        symbol="QBTS",
        entry_timestamp=datetime(2026, 5, 1, 14, 0, tzinfo=timezone.utc),
        entry_price=5.50,
        quantity=-100,
        entry_level=6.00,
        initial_stop_price=6.00,
        stop_price=6.00,
        lowest_price=5.20,
    )
    assert pos.lowest_price == 5.20


def test_open_position_lowest_price_defaults_to_zero():
    pos = OpenPosition(
        symbol="QBTS",
        entry_timestamp=datetime(2026, 5, 1, 14, 0, tzinfo=timezone.utc),
        entry_price=5.50,
        quantity=-100,
        entry_level=6.00,
        initial_stop_price=6.00,
        stop_price=6.00,
    )
    assert pos.lowest_price == 0.0


def test_position_record_has_lowest_price_field():
    rec = PositionRecord(
        symbol="QBTS",
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        quantity=-100,
        entry_price=5.50,
        stop_price=6.00,
        initial_stop_price=6.00,
        opened_at=datetime(2026, 5, 1, 14, 0, tzinfo=timezone.utc),
        lowest_price=5.20,
    )
    assert rec.lowest_price == 5.20


def test_position_record_lowest_price_defaults_to_none():
    rec = PositionRecord(
        symbol="QBTS",
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        quantity=-100,
        entry_price=5.50,
        stop_price=6.00,
        initial_stop_price=6.00,
        opened_at=datetime(2026, 5, 1, 14, 0, tzinfo=timezone.utc),
    )
    assert rec.lowest_price is None
