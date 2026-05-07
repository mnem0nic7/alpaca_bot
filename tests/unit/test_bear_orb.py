from bear_test_helpers import _bar, _downtrend_daily_bars, _put_contract, _settings, _uptrend_daily_bars

from alpaca_bot.strategy.bear_orb import evaluate_bear_orb_signal, make_bear_orb_evaluator


def _intraday(signal_close: float, *, opening_low: float = 100.0, signal_vol: float = 2000.0) -> list:
    avg_vol = 1000.0
    return [
        _bar(101.0, low=opening_low, high=102.0, volume=avg_vol),
        _bar(100.5, low=opening_low, high=101.5, volume=avg_vol),
        _bar(signal_close, low=signal_close - 0.5, high=signal_close + 0.2, volume=signal_vol),
    ]


class TestBearOrbSignal:
    def test_fires_below_opening_range_low(self):
        bars = _intraday(98.5, opening_low=100.0, signal_vol=2500.0)
        result = evaluate_bear_orb_signal(
            symbol="AAPL",
            intraday_bars=bars,
            signal_index=2,
            daily_bars=_downtrend_daily_bars(),
            settings=_settings(),
        )
        assert result is not None

    def test_no_signal_above_opening_range_low(self):
        bars = _intraday(100.5, opening_low=100.0)
        result = evaluate_bear_orb_signal(
            symbol="AAPL",
            intraday_bars=bars,
            signal_index=2,
            daily_bars=_downtrend_daily_bars(),
            settings=_settings(),
        )
        assert result is None

    def test_no_signal_when_uptrend(self):
        bars = _intraday(98.5, opening_low=100.0, signal_vol=2500.0)
        result = evaluate_bear_orb_signal(
            symbol="AAPL",
            intraday_bars=bars,
            signal_index=2,
            daily_bars=_uptrend_daily_bars(),
            settings=_settings(),
        )
        assert result is None

    def test_no_signal_before_orb_complete(self):
        bars = _intraday(98.5, opening_low=100.0, signal_vol=2500.0)
        result = evaluate_bear_orb_signal(
            symbol="AAPL",
            intraday_bars=bars,
            signal_index=1,
            daily_bars=_downtrend_daily_bars(),
            settings=_settings(),
        )
        assert result is None


class TestMakeBearOrbEvaluator:
    def test_evaluator_returns_entry_signal_with_put(self):
        chains = {"AAPL": [_put_contract()]}
        evaluator = make_bear_orb_evaluator(chains)
        bars = _intraday(98.5, opening_low=100.0, signal_vol=2500.0)
        result = evaluator(
            symbol="AAPL",
            intraday_bars=bars,
            signal_index=2,
            daily_bars=_downtrend_daily_bars(),
            settings=_settings(),
        )
        assert result is not None
        assert result.option_contract is not None
        assert result.option_contract.option_type == "put"

    def test_evaluator_returns_none_when_no_chains(self):
        evaluator = make_bear_orb_evaluator({})
        bars = _intraday(98.5, opening_low=100.0, signal_vol=2500.0)
        result = evaluator(
            symbol="AAPL",
            intraday_bars=bars,
            signal_index=2,
            daily_bars=_downtrend_daily_bars(),
            settings=_settings(),
        )
        assert result is None
