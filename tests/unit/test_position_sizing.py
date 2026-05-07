from __future__ import annotations

from types import SimpleNamespace

import pytest

from alpaca_bot.risk.sizing import calculate_position_size


def make_settings(
    *,
    risk_per_trade_pct: float = 0.0025,
    max_position_pct: float = 0.05,
    max_loss_per_trade_dollars: float | None = None,
) -> object:
    return SimpleNamespace(
        risk_per_trade_pct=risk_per_trade_pct,
        max_position_pct=max_position_pct,
        max_loss_per_trade_dollars=max_loss_per_trade_dollars,
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


class TestDollarLossCap:
    def test_dollar_cap_is_binding_when_stop_is_tight(self):
        """When dollar cap < risk budget, quantity is reduced to honour the cap."""
        # equity=10_000, risk=0.25% → budget=$25; entry=100, stop=99 → risk/share=$1 → qty=25
        # dollar_cap=10 → dollar_cap_qty=10/1=10 → 10 < 25 → cap wins → qty=10
        settings = make_settings(
            risk_per_trade_pct=0.0025,
            max_position_pct=0.50,
            max_loss_per_trade_dollars=10.0,
        )
        qty = calculate_position_size(
            equity=10_000.0,
            entry_price=100.0,
            stop_price=99.0,
            settings=settings,
        )
        assert qty == 10

    def test_dollar_cap_is_not_binding_when_stop_is_wide(self):
        """When dollar cap > risk budget, the risk budget is the binding constraint."""
        # equity=10_000, risk=0.25% → budget=$25; entry=100, stop=95 → risk/share=$5 → qty=5
        # dollar_cap=50 → dollar_cap_qty=50/5=10 → 10 > 5 → risk budget wins → qty=5
        settings = make_settings(
            risk_per_trade_pct=0.0025,
            max_position_pct=0.50,
            max_loss_per_trade_dollars=50.0,
        )
        qty = calculate_position_size(
            equity=10_000.0,
            entry_price=100.0,
            stop_price=95.0,
            settings=settings,
        )
        assert qty == 5

    def test_dollar_cap_none_preserves_existing_behaviour(self):
        """When max_loss_per_trade_dollars is None, behaviour is unchanged."""
        settings = make_settings(
            risk_per_trade_pct=0.0025,
            max_position_pct=0.50,
            max_loss_per_trade_dollars=None,
        )
        qty = calculate_position_size(
            equity=10_000.0,
            entry_price=100.0,
            stop_price=99.0,
            settings=settings,
        )
        # risk_budget=$25, risk/share=$1 → qty=25 (no cap applied)
        assert qty == 25
