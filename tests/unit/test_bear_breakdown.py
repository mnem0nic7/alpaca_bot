import pytest
from tests.unit.bear_test_helpers import _bar, _settings, _downtrend_daily_bars, _uptrend_daily_bars, _put_contract
from alpaca_bot.strategy.bear_breakdown import evaluate_bear_breakdown_signal, make_bear_breakdown_evaluator


def _intraday(signal_close: float, *, avg_vol: float = 1000.0, signal_vol: float = 2000.0) -> list:
    """5 intraday bars, signal at index 4. Lookback=3 so lookback window=[1,2,3]."""
    prior_low = 100.0
    bars = [
        _bar(102.0, low=prior_low, volume=avg_vol),  # 0
        _bar(101.0, low=prior_low, volume=avg_vol),  # 1
        _bar(100.5, low=prior_low, volume=avg_vol),  # 2
        _bar(100.0, low=prior_low, volume=avg_vol),  # 3
        _bar(signal_close, low=signal_close - 0.5, high=signal_close + 0.5, volume=signal_vol),  # 4 = signal
    ]
    return bars


class TestBearBreakdownSignal:
    def test_breakdown_fires_on_new_low_with_volume_and_downtrend(self):
        bars = _intraday(98.0, signal_vol=2000.0)  # new low vs lookback [1,2,3]
        result = evaluate_bear_breakdown_signal(
            symbol="AAPL",
            intraday_bars=bars,
            signal_index=4,
            daily_bars=_downtrend_daily_bars(),
            settings=_settings(),
        )
        assert result is not None
        assert result.symbol == "AAPL"

    def test_no_signal_when_not_new_low(self):
        bars = _intraday(100.5)  # low=100.0, not below lookback low=100
        result = evaluate_bear_breakdown_signal(
            symbol="AAPL",
            intraday_bars=bars,
            signal_index=4,
            daily_bars=_downtrend_daily_bars(),
            settings=_settings(),
        )
        assert result is None

    def test_no_signal_when_uptrend(self):
        bars = _intraday(98.0, signal_vol=2000.0)
        result = evaluate_bear_breakdown_signal(
            symbol="AAPL",
            intraday_bars=bars,
            signal_index=4,
            daily_bars=_uptrend_daily_bars(),
            settings=_settings(),
        )
        assert result is None

    def test_no_signal_when_low_volume(self):
        bars = _intraday(98.0, avg_vol=1000.0, signal_vol=500.0)
        result = evaluate_bear_breakdown_signal(
            symbol="AAPL",
            intraday_bars=bars,
            signal_index=4,
            daily_bars=_downtrend_daily_bars(),
            settings=_settings(),
        )
        assert result is None


class TestMakeBearBreakdownEvaluator:
    def test_evaluator_returns_entry_signal_with_put(self):
        chains = {"AAPL": [_put_contract()]}
        evaluator = make_bear_breakdown_evaluator(chains)
        bars = _intraday(98.0, signal_vol=2000.0)
        result = evaluator(
            symbol="AAPL",
            intraday_bars=bars,
            signal_index=4,
            daily_bars=_downtrend_daily_bars(),
            settings=_settings(),
        )
        assert result is not None
        assert result.option_contract is not None
        assert result.option_contract.option_type == "put"

    def test_evaluator_returns_none_when_no_chains(self):
        evaluator = make_bear_breakdown_evaluator({})
        bars = _intraday(98.0, signal_vol=2000.0)
        result = evaluator(
            symbol="AAPL",
            intraday_bars=bars,
            signal_index=4,
            daily_bars=_downtrend_daily_bars(),
            settings=_settings(),
        )
        assert result is None
