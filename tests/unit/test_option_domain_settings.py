from __future__ import annotations

import pytest
from datetime import date, datetime, timezone
from alpaca_bot.domain.models import OptionContract
from tests.unit.helpers import _base_env


def test_option_contract_fields():
    contract = OptionContract(
        occ_symbol="AAPL241220C00150000",
        underlying="AAPL",
        option_type="call",
        strike=150.0,
        expiry=date(2024, 12, 20),
        bid=2.50,
        ask=2.75,
        delta=0.52,
    )
    assert contract.occ_symbol == "AAPL241220C00150000"
    assert contract.underlying == "AAPL"
    assert contract.option_type == "call"
    assert contract.strike == 150.0
    assert contract.expiry == date(2024, 12, 20)
    assert contract.bid == 2.50
    assert contract.ask == 2.75
    assert contract.delta == 0.52


def test_option_contract_delta_optional():
    contract = OptionContract(
        occ_symbol="AAPL241220C00150000",
        underlying="AAPL",
        option_type="call",
        strike=150.0,
        expiry=date(2024, 12, 20),
        bid=2.50,
        ask=2.75,
    )
    assert contract.delta is None


def test_settings_option_defaults():
    from alpaca_bot.config import Settings
    s = Settings.from_env(_base_env())
    assert s.option_dte_min == 21
    assert s.option_dte_max == 60
    assert s.option_delta_target == 0.50


def test_settings_option_from_env_override():
    from alpaca_bot.config import Settings
    env = _base_env()
    env["OPTION_DTE_MIN"] = "14"
    env["OPTION_DTE_MAX"] = "45"
    env["OPTION_DELTA_TARGET"] = "0.40"
    s = Settings.from_env(env)
    assert s.option_dte_min == 14
    assert s.option_dte_max == 45
    assert s.option_delta_target == 0.40


def test_settings_option_dte_min_must_be_at_least_1():
    from alpaca_bot.config import Settings
    env = _base_env()
    env["OPTION_DTE_MIN"] = "0"
    with pytest.raises(ValueError, match="OPTION_DTE_MIN"):
        Settings.from_env(env)


def test_settings_option_dte_max_must_be_greater_than_min():
    from alpaca_bot.config import Settings
    env = _base_env()
    env["OPTION_DTE_MIN"] = "30"
    env["OPTION_DTE_MAX"] = "20"
    with pytest.raises(ValueError, match="OPTION_DTE_MAX"):
        Settings.from_env(env)


def test_settings_option_delta_target_must_be_positive_fraction():
    from alpaca_bot.config import Settings
    env = _base_env()
    env["OPTION_DELTA_TARGET"] = "1.1"
    with pytest.raises(ValueError, match="OPTION_DELTA_TARGET"):
        Settings.from_env(env)
    env["OPTION_DELTA_TARGET"] = "0.0"
    with pytest.raises(ValueError, match="OPTION_DELTA_TARGET"):
        Settings.from_env(env)


def test_enable_options_trading_defaults_false():
    from alpaca_bot.config import Settings
    s = Settings.from_env(_base_env())
    assert s.enable_options_trading is False


def test_enable_options_trading_parsed_true():
    from alpaca_bot.config import Settings
    env = _base_env()
    env["ENABLE_OPTIONS_TRADING"] = "true"
    s = Settings.from_env(env)
    assert s.enable_options_trading is True
