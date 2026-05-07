from datetime import date

from alpaca_bot.domain.models import OptionContract
from alpaca_bot.strategy.breakout import daily_downtrend_filter_passes
from alpaca_bot.strategy.option_selector import select_put_contract
from tests.unit.bear_test_helpers import _bar, _settings, _downtrend_daily_bars, _uptrend_daily_bars, _put_contract


class TestDailyDowntrendFilter:
    def test_downtrend_returns_true(self):
        bars = _downtrend_daily_bars()
        assert daily_downtrend_filter_passes(bars, _settings()) is True

    def test_uptrend_returns_false(self):
        bars = _uptrend_daily_bars()
        assert daily_downtrend_filter_passes(bars, _settings()) is False

    def test_insufficient_bars_returns_false(self):
        bars = [_bar(80.0) for _ in range(3)]
        assert daily_downtrend_filter_passes(bars, _settings()) is False


class TestSelectPutContract:
    def test_selects_put_by_delta(self):
        today = date(2024, 1, 15)
        c1 = _put_contract(strike=95.0, delta=-0.5)
        c2 = _put_contract(strike=90.0, delta=-0.3)
        result = select_put_contract([c1, c2], current_price=100.0, today=today, settings=_settings())
        assert result is c1  # abs(-0.5) closer to target 0.5

    def test_skips_calls(self):
        today = date(2024, 1, 15)
        call = OptionContract(
            occ_symbol="AAPL240216C00095000",
            underlying="AAPL",
            option_type="call",
            strike=95.0,
            expiry=date(2024, 2, 16),
            bid=2.0,
            ask=2.10,
            delta=0.5,
        )
        result = select_put_contract([call], current_price=100.0, today=today, settings=_settings())
        assert result is None

    def test_skips_zero_ask(self):
        today = date(2024, 1, 15)
        c = OptionContract(
            occ_symbol="AAPL240216P00095000",
            underlying="AAPL",
            option_type="put",
            strike=95.0,
            expiry=date(2024, 2, 16),
            bid=0.0,
            ask=0.0,
            delta=-0.5,
        )
        result = select_put_contract([c], current_price=100.0, today=today, settings=_settings())
        assert result is None

    def test_dte_filter(self):
        today = date(2024, 1, 15)
        c_near = OptionContract(
            occ_symbol="AAPL240120P00095000",
            underlying="AAPL",
            option_type="put",
            strike=95.0,
            expiry=date(2024, 1, 20),  # 5 DTE — below min 21
            bid=0.5,
            ask=0.6,
            delta=-0.5,
        )
        result = select_put_contract([c_near], current_price=100.0, today=today, settings=_settings())
        assert result is None

    def test_falls_back_to_strike_proximity_without_delta(self):
        today = date(2024, 1, 15)
        c1 = OptionContract(
            occ_symbol="AAPL240216P00098000",
            underlying="AAPL",
            option_type="put",
            strike=98.0,
            expiry=date(2024, 2, 16),
            bid=2.0,
            ask=2.10,
            delta=None,
        )
        c2 = OptionContract(
            occ_symbol="AAPL240216P00090000",
            underlying="AAPL",
            option_type="put",
            strike=90.0,
            expiry=date(2024, 2, 16),
            bid=1.5,
            ask=1.60,
            delta=None,
        )
        result = select_put_contract([c1, c2], current_price=100.0, today=today, settings=_settings())
        assert result is c1  # 98 closer to 100
