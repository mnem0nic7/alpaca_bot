from bear_test_helpers import _bar, _downtrend_daily_bars, _put_contract, _settings, _uptrend_daily_bars

from alpaca_bot.strategy.bear_vwap_cross_down import (
    evaluate_bear_vwap_cross_down_signal,
    make_bear_vwap_cross_down_evaluator,
)


def _bars_for_vwap_cross_down() -> list:
    """
    Bars[0-2]: close=101, VWAP≈101.
    Bar[3] (prior): close=101.5, high=101.5, low=100.5 → VWAP(0:4)≈101.04; 101.5 ≥ 101.04 ✓
    Bar[4] (signal): close=97, high=98, low=96.5 → VWAP(0:5)≈100.27; 97.0 < 100.27 ✓
    """
    bars = [_bar(101.0, high=101.0, low=101.0, volume=1000.0) for _ in range(3)]
    bars.append(_bar(101.5, high=101.5, low=100.5, volume=1000.0))
    bars.append(_bar(97.0, high=98.0, low=96.5, volume=1000.0))
    return bars


class TestBearVwapCrossDownSignal:
    def test_fires_on_cross_below_vwap(self):
        bars = _bars_for_vwap_cross_down()
        result = evaluate_bear_vwap_cross_down_signal(
            symbol="AAPL",
            intraday_bars=bars,
            signal_index=4,
            daily_bars=_downtrend_daily_bars(),
            settings=_settings(),
        )
        assert result is not None

    def test_no_signal_when_prior_already_below_vwap(self):
        bars = [_bar(101.0, high=101.0, low=101.0, volume=1000.0) for _ in range(3)]
        bars.append(_bar(98.0, high=98.5, low=97.5, volume=1000.0))  # prior below VWAP
        bars.append(_bar(97.0, high=98.0, low=96.5, volume=1000.0))
        result = evaluate_bear_vwap_cross_down_signal(
            symbol="AAPL",
            intraday_bars=bars,
            signal_index=4,
            daily_bars=_downtrend_daily_bars(),
            settings=_settings(),
        )
        assert result is None

    def test_no_signal_when_signal_still_above_vwap(self):
        bars = _bars_for_vwap_cross_down()
        bars[4] = _bar(102.0, high=103.0, low=101.5, volume=1000.0)
        result = evaluate_bear_vwap_cross_down_signal(
            symbol="AAPL",
            intraday_bars=bars,
            signal_index=4,
            daily_bars=_downtrend_daily_bars(),
            settings=_settings(),
        )
        assert result is None

    def test_no_signal_when_uptrend(self):
        bars = _bars_for_vwap_cross_down()
        result = evaluate_bear_vwap_cross_down_signal(
            symbol="AAPL",
            intraday_bars=bars,
            signal_index=4,
            daily_bars=_uptrend_daily_bars(),
            settings=_settings(),
        )
        assert result is None

    def test_no_signal_at_first_bar(self):
        bars = _bars_for_vwap_cross_down()
        result = evaluate_bear_vwap_cross_down_signal(
            symbol="AAPL",
            intraday_bars=bars,
            signal_index=0,
            daily_bars=_downtrend_daily_bars(),
            settings=_settings(),
        )
        assert result is None


class TestMakeBearVwapCrossDownEvaluator:
    def test_evaluator_returns_entry_signal_with_put(self):
        bars = _bars_for_vwap_cross_down()
        contract = _put_contract()
        evaluator = make_bear_vwap_cross_down_evaluator({"AAPL": [contract]})
        result = evaluator(
            symbol="AAPL",
            intraday_bars=bars,
            signal_index=4,
            daily_bars=_downtrend_daily_bars(),
            settings=_settings(),
        )
        assert result is not None
        assert result.option_contract is not None
        assert result.limit_price == contract.ask

    def test_evaluator_returns_none_when_no_chains(self):
        bars = _bars_for_vwap_cross_down()
        evaluator = make_bear_vwap_cross_down_evaluator({})
        result = evaluator(
            symbol="AAPL",
            intraday_bars=bars,
            signal_index=4,
            daily_bars=_downtrend_daily_bars(),
            settings=_settings(),
        )
        assert result is None
