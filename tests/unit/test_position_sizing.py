from __future__ import annotations

from types import SimpleNamespace

import pytest

from alpaca_bot.risk.sizing import calculate_position_size


def make_settings(
    *,
    risk_per_trade_pct: float = 0.0025,
    max_position_pct: float = 0.05,
) -> object:
    return SimpleNamespace(
        risk_per_trade_pct=risk_per_trade_pct,
        max_position_pct=max_position_pct,
    )


class TestCalculatePositionSize:
    def test_normal_case_returns_floor_of_risk_budget_divided_by_risk_per_share(self):
        """Risk budget / risk-per-share, floored to whole shares."""
        # equity=100_000, risk=0.25% → budget=$250; entry=100, stop=95 → risk/share=$5 → qty=50
        settings = make_settings(risk_per_trade_pct=0.0025, max_position_pct=0.10)
        qty = calculate_position_size(
            equity=100_000.0,
            entry_price=100.0,
            stop_price=95.0,
            settings=settings,
        )
        assert qty == 50

    def test_stop_price_at_entry_price_raises_value_error(self):
        """stop_price == entry_price is invalid for a long position."""
        settings = make_settings()
        with pytest.raises(ValueError, match="stop_price must be below entry_price"):
            calculate_position_size(
                equity=100_000.0,
                entry_price=100.0,
                stop_price=100.0,
                settings=settings,
            )

    def test_stop_price_above_entry_price_raises_value_error(self):
        """stop_price > entry_price is also invalid."""
        settings = make_settings()
        with pytest.raises(ValueError, match="stop_price must be below entry_price"):
            calculate_position_size(
                equity=100_000.0,
                entry_price=100.0,
                stop_price=105.0,
                settings=settings,
            )

    def test_equity_zero_returns_zero(self):
        """Zero equity → zero risk budget → zero quantity."""
        settings = make_settings()
        qty = calculate_position_size(
            equity=0.0,
            entry_price=100.0,
            stop_price=95.0,
            settings=settings,
        )
        assert qty == 0

    def test_equity_negative_returns_zero(self):
        """Negative equity (e.g. margin call scenario) must not produce a negative quantity."""
        settings = make_settings()
        qty = calculate_position_size(
            equity=-5_000.0,
            entry_price=100.0,
            stop_price=95.0,
            settings=settings,
        )
        assert qty == 0

    def test_max_position_cap_limits_quantity(self):
        """Quantity is capped when it would exceed max_position_pct of equity."""
        # equity=100_000, risk=25% would give huge qty, but max_position=2% → cap at floor(2000/100)=20
        settings = make_settings(risk_per_trade_pct=0.25, max_position_pct=0.02)
        qty = calculate_position_size(
            equity=100_000.0,
            entry_price=100.0,
            stop_price=99.0,
            settings=settings,
        )
        assert qty == 20

    def test_result_quantity_zero_when_risk_per_share_exceeds_budget(self):
        """When risk-per-share > risk-budget, floor division gives 0."""
        # equity=1_000, risk=0.1% → budget=$1; entry=100, stop=50 → risk/share=$50 → qty=0
        settings = make_settings(risk_per_trade_pct=0.001, max_position_pct=0.05)
        qty = calculate_position_size(
            equity=1_000.0,
            entry_price=100.0,
            stop_price=50.0,
            settings=settings,
        )
        assert qty == 0


def test_fractional_sizing_returns_float():
    settings = SimpleNamespace(risk_per_trade_pct=0.0025, max_position_pct=0.015)
    qty = calculate_position_size(
        equity=99_500.0,
        entry_price=3.00,
        stop_price=2.70,
        settings=settings,
        fractionable=True,
    )
    assert isinstance(qty, float)
    # risk_budget = $248.75, risk_per_share = $0.30 → raw qty = 829.17
    # max_notional = $1492.50 → capped at $1492.50 / $3.00 = 497.5
    assert qty == pytest.approx(497.5, rel=1e-4)


def test_fractional_sizing_no_floor_below_one():
    """For fractionable symbols, qty can be between 0 and 1."""
    settings = SimpleNamespace(risk_per_trade_pct=0.0025, max_position_pct=0.0001)
    qty = calculate_position_size(
        equity=99_500.0,
        entry_price=500.00,
        stop_price=495.00,
        settings=settings,
        fractionable=True,
    )
    assert isinstance(qty, float)
    assert 0.0 < qty < 1.0


def test_non_fractional_sizing_floors_to_integer_value():
    settings = SimpleNamespace(risk_per_trade_pct=0.0025, max_position_pct=0.015)
    qty = calculate_position_size(
        equity=99_500.0,
        entry_price=3.00,
        stop_price=2.70,
        settings=settings,
        fractionable=False,
    )
    assert qty == float(int(qty))  # whole number value stored as float


def test_non_fractional_sizing_returns_zero_for_sub_one():
    """Non-fractionable symbol with < 1 share budget returns 0."""
    settings = SimpleNamespace(risk_per_trade_pct=0.0025, max_position_pct=0.0001)
    qty = calculate_position_size(
        equity=99_500.0,
        entry_price=500.00,
        stop_price=495.00,
        settings=settings,
        fractionable=False,
    )
    assert qty == 0.0


def test_fractionable_returns_zero_for_negative_risk():
    settings = SimpleNamespace(risk_per_trade_pct=0.0025, max_position_pct=0.015)
    with pytest.raises(ValueError, match="stop_price must be below entry_price"):
        calculate_position_size(
            equity=99_500.0,
            entry_price=3.00,
            stop_price=3.00,  # zero risk (equal prices)
            settings=settings,
            fractionable=True,
        )
