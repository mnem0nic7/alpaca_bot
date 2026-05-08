from __future__ import annotations

import math
import pytest
from datetime import date
from alpaca_bot.domain.models import OptionContract
from alpaca_bot.strategy.option_selector import select_call_contract, select_put_contract
from alpaca_bot.risk.option_sizing import calculate_option_position_size
from tests.unit.helpers import _base_env
from alpaca_bot.config import Settings


def _settings(**overrides) -> Settings:
    env = _base_env()
    env.update(overrides)
    return Settings.from_env(env)


def _contract(strike: float, expiry: date, ask: float, delta: float | None = None, option_type: str = "call") -> OptionContract:
    return OptionContract(
        occ_symbol=f"AAPL{expiry.strftime('%y%m%d')}C{int(strike * 1000):08d}",
        underlying="AAPL",
        option_type=option_type,
        strike=strike,
        expiry=expiry,
        bid=ask - 0.05,
        ask=ask,
        delta=delta,
    )


TODAY = date(2024, 6, 1)
FAR_EXPIRY = date(2024, 8, 1)   # 61 days from TODAY — outside DTE_MAX=60 default
NEAR_EXPIRY = date(2024, 7, 1)  # 30 days from TODAY — within [21, 60] default


class TestSelectCallContract:
    def test_returns_none_when_no_contracts(self):
        s = _settings()
        assert select_call_contract([], current_price=150.0, today=TODAY, settings=s) is None

    def test_returns_none_when_no_eligible_contracts_by_dte(self):
        s = _settings()
        # 5 days to expiry — below DTE_MIN=21
        too_soon = date(2024, 6, 6)
        c = _contract(150.0, too_soon, ask=2.0, delta=0.50)
        assert select_call_contract([c], current_price=150.0, today=TODAY, settings=s) is None

    def test_returns_none_when_contract_outside_dte_max(self):
        s = _settings()
        # FAR_EXPIRY is 61 days out — exceeds DTE_MAX=60
        c = _contract(150.0, FAR_EXPIRY, ask=2.0, delta=0.50)
        assert select_call_contract([c], current_price=150.0, today=TODAY, settings=s) is None

    def test_returns_none_when_ask_is_zero(self):
        s = _settings()
        c = OptionContract(
            occ_symbol="AAPL240701C00150000",
            underlying="AAPL",
            option_type="call",
            strike=150.0,
            expiry=NEAR_EXPIRY,
            bid=0.0,
            ask=0.0,
            delta=0.50,
        )
        assert select_call_contract([c], current_price=150.0, today=TODAY, settings=s) is None

    def test_selects_by_delta_closest_to_target(self):
        s = _settings(OPTION_DELTA_TARGET="0.50")
        c30 = _contract(160.0, NEAR_EXPIRY, ask=2.0, delta=0.30)
        c50 = _contract(150.0, NEAR_EXPIRY, ask=3.0, delta=0.50)
        c70 = _contract(140.0, NEAR_EXPIRY, ask=5.0, delta=0.70)
        result = select_call_contract([c30, c50, c70], current_price=150.0, today=TODAY, settings=s)
        assert result is c50

    def test_selects_atm_by_strike_when_no_delta(self):
        s = _settings()
        c140 = _contract(140.0, NEAR_EXPIRY, ask=10.0)
        c150 = _contract(150.0, NEAR_EXPIRY, ask=3.0)
        c160 = _contract(160.0, NEAR_EXPIRY, ask=1.5)
        result = select_call_contract([c140, c150, c160], current_price=150.0, today=TODAY, settings=s)
        assert result is c150

    def test_skips_put_contracts(self):
        s = _settings()
        put = OptionContract(
            occ_symbol="AAPL240701P00150000",
            underlying="AAPL",
            option_type="put",
            strike=150.0,
            expiry=NEAR_EXPIRY,
            bid=2.50,
            ask=2.75,
            delta=None,
        )
        call = _contract(150.0, NEAR_EXPIRY, ask=3.0, delta=0.50)
        result = select_call_contract([put, call], current_price=150.0, today=TODAY, settings=s)
        assert result is call


class TestCalculateOptionPositionSize:
    def test_basic_sizing(self):
        s = _settings(RISK_PER_TRADE_PCT="0.01", MAX_POSITION_PCT="0.05")
        # equity=100_000, risk_budget=1000, contract_cost=5*100=500 → 2 contracts
        result = calculate_option_position_size(equity=100_000, ask=5.0, settings=s)
        assert result == 2

    def test_capped_by_max_position_pct(self):
        s = _settings(RISK_PER_TRADE_PCT="0.20", MAX_POSITION_PCT="0.01")
        # equity=100_000, max_notional=1000, contract_cost=5*100=500 → max 2 contracts
        # risk_budget=20_000 / 500 = 40, but capped at floor(1000/500)=2
        result = calculate_option_position_size(equity=100_000, ask=5.0, settings=s)
        assert result == 2

    def test_returns_zero_when_ask_exceeds_budget(self):
        s = _settings(RISK_PER_TRADE_PCT="0.001", MAX_POSITION_PCT="0.05")
        # equity=10_000, risk_budget=10, contract_cost=500 → 0 contracts
        result = calculate_option_position_size(equity=10_000, ask=5.0, settings=s)
        assert result == 0

    def test_returns_zero_when_ask_is_zero(self):
        s = _settings()
        result = calculate_option_position_size(equity=100_000, ask=0.0, settings=s)
        assert result == 0


def _put_contract(
    strike: float, expiry: date, ask: float, delta: float | None = None
) -> OptionContract:
    return OptionContract(
        occ_symbol=f"AAPL{expiry.strftime('%y%m%d')}P{int(strike * 1000):08d}",
        underlying="AAPL",
        option_type="put",
        strike=strike,
        expiry=expiry,
        bid=ask - 0.05,
        ask=ask,
        delta=delta,
    )


class TestSelectPutContract:
    def test_selects_atm_by_strike_when_no_delta(self):
        s = _settings()
        p140 = _put_contract(140.0, NEAR_EXPIRY, ask=1.5)
        p150 = _put_contract(150.0, NEAR_EXPIRY, ask=3.0)
        p160 = _put_contract(160.0, NEAR_EXPIRY, ask=10.0)
        result = select_put_contract(
            [p140, p150, p160], current_price=150.0, today=TODAY, settings=s
        )
        assert result is p150

    def test_selects_by_delta_when_available(self):
        # Put deltas are negative; abs(-0.50) == 0.50 matches option_delta_target=0.50
        s = _settings(OPTION_DELTA_TARGET="0.50")
        p30 = _put_contract(140.0, NEAR_EXPIRY, ask=1.5, delta=-0.30)
        p50 = _put_contract(150.0, NEAR_EXPIRY, ask=3.0, delta=-0.50)
        p70 = _put_contract(160.0, NEAR_EXPIRY, ask=10.0, delta=-0.70)
        result = select_put_contract(
            [p30, p50, p70], current_price=150.0, today=TODAY, settings=s
        )
        assert result is p50
