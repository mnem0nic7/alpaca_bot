from bear_test_helpers import _bar, _downtrend_daily_bars, _put_contract, _settings, _uptrend_daily_bars

from alpaca_bot.strategy.bear_flag import evaluate_bear_flag_signal, make_bear_flag_evaluator


def _bars_for_bear_flag() -> list:
    """
    Pole: bars[0:4] — open=110.0, pole_low=107.5, drop=2.27% ≥ 2% → valid pole.
    Consolidation: bars[4:6] — close≈108, vol=400.
    Signal: bar[6] — close=107.0 < consol_low=107.7 → fires.

    pole_avg_vol=(1000+1200+1100+400)/4=925; consol_avg_vol=400; ratio=0.43 ≤ 0.6 ✓
    """
    bars = [
        _bar(110.0, open=110.0, high=110.5, low=109.5, volume=1000.0),
        _bar(109.0, open=109.5, high=109.8, low=108.5, volume=1200.0),
        _bar(107.8, open=108.5, high=108.7, low=107.5, volume=1100.0),
        _bar(108.0, open=107.9, high=108.3, low=107.7, volume=400.0),
        _bar(108.0, open=107.9, high=108.3, low=107.7, volume=400.0),
        _bar(108.0, open=107.9, high=108.3, low=107.7, volume=400.0),
        _bar(107.0, open=107.6, high=107.8, low=106.8, volume=1500.0),
    ]
    return bars


class TestBearFlagSignal:
    def test_fires_on_breakdown_below_consolidation_low(self):
        bars = _bars_for_bear_flag()
        result = evaluate_bear_flag_signal(
            symbol="AAPL",
            intraday_bars=bars,
            signal_index=6,
            daily_bars=_downtrend_daily_bars(),
            settings=_settings(),
        )
        assert result is not None
        assert result.stop_price > bars[6].close

    def test_no_signal_when_close_above_consolidation_low(self):
        bars = _bars_for_bear_flag()
        bars[6] = _bar(108.2, open=107.6, high=108.5, low=107.8, volume=1500.0)
        result = evaluate_bear_flag_signal(
            symbol="AAPL",
            intraday_bars=bars,
            signal_index=6,
            daily_bars=_downtrend_daily_bars(),
            settings=_settings(),
        )
        assert result is None

    def test_no_signal_when_uptrend(self):
        bars = _bars_for_bear_flag()
        result = evaluate_bear_flag_signal(
            symbol="AAPL",
            intraday_bars=bars,
            signal_index=6,
            daily_bars=_uptrend_daily_bars(),
            settings=_settings(),
        )
        assert result is None

    def test_no_signal_when_insufficient_bars(self):
        bars = _bars_for_bear_flag()[:4]
        result = evaluate_bear_flag_signal(
            symbol="AAPL",
            intraday_bars=bars,
            signal_index=3,
            daily_bars=_downtrend_daily_bars(),
            settings=_settings(),
        )
        assert result is None

    def test_no_signal_when_consolidation_volume_too_high(self):
        bars = _bars_for_bear_flag()
        for i in range(4, 6):
            bars[i] = _bar(108.0, open=107.9, high=108.3, low=107.7, volume=2000.0)
        result = evaluate_bear_flag_signal(
            symbol="AAPL",
            intraday_bars=bars,
            signal_index=6,
            daily_bars=_downtrend_daily_bars(),
            settings=_settings(),
        )
        assert result is None


class TestMakeBearFlagEvaluator:
    def test_evaluator_returns_entry_signal_with_put(self):
        bars = _bars_for_bear_flag()
        contract = _put_contract()
        evaluator = make_bear_flag_evaluator({"AAPL": [contract]})
        result = evaluator(
            symbol="AAPL",
            intraday_bars=bars,
            signal_index=6,
            daily_bars=_downtrend_daily_bars(),
            settings=_settings(),
        )
        assert result is not None
        assert result.option_contract is not None
        assert result.limit_price == contract.ask

    def test_evaluator_returns_none_when_no_chains(self):
        bars = _bars_for_bear_flag()
        evaluator = make_bear_flag_evaluator({})
        result = evaluator(
            symbol="AAPL",
            intraday_bars=bars,
            signal_index=6,
            daily_bars=_downtrend_daily_bars(),
            settings=_settings(),
        )
        assert result is None
