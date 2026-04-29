from __future__ import annotations
import pytest
from alpaca_bot.execution.alpaca import extended_hours_limit_price


def test_buy_adds_offset():
    result = extended_hours_limit_price("buy", ref_price=100.0, offset_pct=0.001)
    assert result == pytest.approx(100.10, abs=0.01)


def test_sell_subtracts_offset():
    result = extended_hours_limit_price("sell", ref_price=100.0, offset_pct=0.001)
    assert result == pytest.approx(99.90, abs=0.01)


def test_buy_result_always_positive():
    result = extended_hours_limit_price("buy", ref_price=0.05, offset_pct=0.001)
    assert result > 0


def test_invalid_side_raises():
    with pytest.raises(ValueError):
        extended_hours_limit_price("short", ref_price=100.0, offset_pct=0.001)
