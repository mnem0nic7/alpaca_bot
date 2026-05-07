from bear_test_helpers import _bar, _downtrend_daily_bars, _put_contract, _settings, _uptrend_daily_bars

from alpaca_bot.strategy.bear_bb_squeeze_down import (
    evaluate_bear_bb_squeeze_down_signal,
    make_bear_bb_squeeze_down_evaluator,
)


def _bars_for_bb_squeeze_down(signal_close: float = 95.0) -> list:
    """
    settings: bb_period=5, bb_std_dev=2.0, bb_squeeze_threshold_pct=0.03, bb_squeeze_min_bars=2
    Need len(prior_bars) = signal_index >= bb_period + bb_squeeze_min_bars = 7.

    Bars[0-6]: close=100.0, tight bands → σ≈0, band_width=0 ≤ 0.03 (squeeze).
    Bar[7] (signal): close=95.0 < lower_band=100.0 → fires.
    """
    bars = [_bar(100.0, high=100.1, low=99.9, volume=1000.0) for _ in range(7)]
    bars.append(_bar(signal_close, high=signal_close + 0.5, low=signal_close - 0.5, volume=2000.0))
    return bars


class TestBearBbSqueezeDownSignal:
    def test_fires_on_breakdown_below_lower_band_in_squeeze(self):
        bars = _bars_for_bb_squeeze_down(signal_close=95.0)
        result = evaluate_bear_bb_squeeze_down_signal(
            symbol="AAPL",
            intraday_bars=bars,
            signal_index=7,
            daily_bars=_downtrend_daily_bars(),
            settings=_settings(),
        )
        assert result is not None
        assert result.entry_level == 95.0

    def test_no_signal_when_signal_above_lower_band(self):
        bars = _bars_for_bb_squeeze_down(signal_close=101.0)
        result = evaluate_bear_bb_squeeze_down_signal(
            symbol="AAPL",
            intraday_bars=bars,
            signal_index=7,
            daily_bars=_downtrend_daily_bars(),
            settings=_settings(),
        )
        assert result is None

    def test_no_signal_when_bands_are_wide(self):
        bars = [_bar(100.0, high=100.1, low=99.9, volume=1000.0) for _ in range(2)]
        wide_closes = [95.0, 97.0, 100.0, 103.0, 105.0]
        for c in wide_closes:
            bars.append(_bar(c, high=c + 0.1, low=c - 0.1, volume=1000.0))
        bars.append(_bar(90.0, high=91.0, low=89.0, volume=2000.0))
        result = evaluate_bear_bb_squeeze_down_signal(
            symbol="AAPL",
            intraday_bars=bars,
            signal_index=7,
            daily_bars=_downtrend_daily_bars(),
            settings=_settings(),
        )
        assert result is None

    def test_no_signal_when_insufficient_prior_bars(self):
        bars = _bars_for_bb_squeeze_down()[:7]
        result = evaluate_bear_bb_squeeze_down_signal(
            symbol="AAPL",
            intraday_bars=bars,
            signal_index=5,
            daily_bars=_downtrend_daily_bars(),
            settings=_settings(),
        )
        assert result is None

    def test_no_signal_when_uptrend(self):
        bars = _bars_for_bb_squeeze_down(signal_close=95.0)
        result = evaluate_bear_bb_squeeze_down_signal(
            symbol="AAPL",
            intraday_bars=bars,
            signal_index=7,
            daily_bars=_uptrend_daily_bars(),
            settings=_settings(),
        )
        assert result is None


class TestMakeBearBbSqueezeDownEvaluator:
    def test_evaluator_returns_entry_signal_with_put(self):
        bars = _bars_for_bb_squeeze_down(signal_close=95.0)
        contract = _put_contract()
        evaluator = make_bear_bb_squeeze_down_evaluator({"AAPL": [contract]})
        result = evaluator(
            symbol="AAPL",
            intraday_bars=bars,
            signal_index=7,
            daily_bars=_downtrend_daily_bars(),
            settings=_settings(),
        )
        assert result is not None
        assert result.option_contract is not None
        assert result.limit_price == contract.ask

    def test_evaluator_returns_none_when_no_chains(self):
        bars = _bars_for_bb_squeeze_down(signal_close=95.0)
        evaluator = make_bear_bb_squeeze_down_evaluator({})
        result = evaluator(
            symbol="AAPL",
            intraday_bars=bars,
            signal_index=7,
            daily_bars=_downtrend_daily_bars(),
            settings=_settings(),
        )
        assert result is None
