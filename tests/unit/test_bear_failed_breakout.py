from bear_test_helpers import _bar, _downtrend_daily_bars, _put_contract, _settings, _uptrend_daily_bars

from alpaca_bot.strategy.bear_failed_breakout import (
    evaluate_bear_failed_breakout_signal,
    make_bear_failed_breakout_evaluator,
)


def _bars_for_failed_breakout() -> list:
    """
    settings: breakout_lookback_bars=3, failed_breakdown_volume_ratio=2.0,
              failed_breakdown_recapture_buffer_pct=0.001.

    Bars[0-2]: high=100.0 → resistance=100.0.
    Bar[3] (signal): high=101.0 (spike above), close=98.0 < 99.9 (recapture threshold),
                     volume=3000 → rel_vol=3.0 ≥ 2.0 ✓
    """
    bars = [_bar(100.0, high=100.0, low=99.5, volume=1000.0) for _ in range(3)]
    bars.append(_bar(98.0, high=101.0, low=97.5, volume=3000.0))
    return bars


class TestBearFailedBreakoutSignal:
    def test_fires_on_failed_breakout(self):
        bars = _bars_for_failed_breakout()
        result = evaluate_bear_failed_breakout_signal(
            symbol="AAPL",
            intraday_bars=bars,
            signal_index=3,
            daily_bars=_downtrend_daily_bars(),
            settings=_settings(),
        )
        assert result is not None
        assert result.stop_price > bars[3].close

    def test_no_signal_when_high_does_not_exceed_resistance(self):
        bars = [_bar(100.0, high=100.0, low=99.5, volume=1000.0) for _ in range(3)]
        bars.append(_bar(98.0, high=100.0, low=97.5, volume=3000.0))
        result = evaluate_bear_failed_breakout_signal(
            symbol="AAPL",
            intraday_bars=bars,
            signal_index=3,
            daily_bars=_downtrend_daily_bars(),
            settings=_settings(),
        )
        assert result is None

    def test_no_signal_when_close_stays_above_resistance(self):
        bars = [_bar(100.0, high=100.0, low=99.5, volume=1000.0) for _ in range(3)]
        bars.append(_bar(100.5, high=101.0, low=99.5, volume=3000.0))
        result = evaluate_bear_failed_breakout_signal(
            symbol="AAPL",
            intraday_bars=bars,
            signal_index=3,
            daily_bars=_downtrend_daily_bars(),
            settings=_settings(),
        )
        assert result is None

    def test_no_signal_when_low_volume(self):
        bars = _bars_for_failed_breakout()
        bars[3] = _bar(98.0, high=101.0, low=97.5, volume=500.0)
        result = evaluate_bear_failed_breakout_signal(
            symbol="AAPL",
            intraday_bars=bars,
            signal_index=3,
            daily_bars=_downtrend_daily_bars(),
            settings=_settings(),
        )
        assert result is None

    def test_no_signal_when_uptrend(self):
        bars = _bars_for_failed_breakout()
        result = evaluate_bear_failed_breakout_signal(
            symbol="AAPL",
            intraday_bars=bars,
            signal_index=3,
            daily_bars=_uptrend_daily_bars(),
            settings=_settings(),
        )
        assert result is None


class TestMakeBearFailedBreakoutEvaluator:
    def test_evaluator_returns_entry_signal_with_put(self):
        bars = _bars_for_failed_breakout()
        contract = _put_contract()
        evaluator = make_bear_failed_breakout_evaluator({"AAPL": [contract]})
        result = evaluator(
            symbol="AAPL",
            intraday_bars=bars,
            signal_index=3,
            daily_bars=_downtrend_daily_bars(),
            settings=_settings(),
        )
        assert result is not None
        assert result.option_contract is not None
        assert result.limit_price == contract.ask

    def test_evaluator_returns_none_when_no_chains(self):
        bars = _bars_for_failed_breakout()
        evaluator = make_bear_failed_breakout_evaluator({})
        result = evaluator(
            symbol="AAPL",
            intraday_bars=bars,
            signal_index=3,
            daily_bars=_downtrend_daily_bars(),
            settings=_settings(),
        )
        assert result is None
