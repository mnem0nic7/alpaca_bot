from bear_test_helpers import _bar, _downtrend_daily_bars, _settings, _uptrend_daily_bars

from alpaca_bot.strategy.bear_gap_and_drop import evaluate_bear_gap_and_drop_signal


def _bars_for_gap_drop() -> list:
    """
    _downtrend_daily_bars: daily[-2].close=80, daily[-2].low=78, daily[-2].volume=1000
    gap_threshold_pct=0.02: open < 80*(1-0.02)=78.4 ✓ (open=77)
    close=77.5 < prior_day_low=78 ✓
    vol=2500, prior_day.vol=1000: 2.5 ≥ gap_volume_threshold=2.0 ✓
    """
    return [_bar(77.5, open=77.0, high=77.8, low=77.0, volume=2500.0)]


class TestBearGapAndDropSignal:
    def test_fires_on_gap_down_with_continuation(self):
        bars = _bars_for_gap_drop()
        result = evaluate_bear_gap_and_drop_signal(
            symbol="AAPL",
            intraday_bars=bars,
            signal_index=0,
            daily_bars=_downtrend_daily_bars(),
            settings=_settings(),
        )
        assert result is not None

    def test_no_signal_on_non_zero_index(self):
        bars = _bars_for_gap_drop() + [_bar(77.0)]
        result = evaluate_bear_gap_and_drop_signal(
            symbol="AAPL",
            intraday_bars=bars,
            signal_index=1,
            daily_bars=_downtrend_daily_bars(),
            settings=_settings(),
        )
        assert result is None

    def test_no_signal_when_close_above_prior_day_low(self):
        bars = [_bar(80.0, open=77.0, high=80.5, low=76.5, volume=2500.0)]
        result = evaluate_bear_gap_and_drop_signal(
            symbol="AAPL",
            intraday_bars=bars,
            signal_index=0,
            daily_bars=_downtrend_daily_bars(),
            settings=_settings(),
        )
        assert result is None

    def test_no_signal_when_uptrend(self):
        bars = _bars_for_gap_drop()
        result = evaluate_bear_gap_and_drop_signal(
            symbol="AAPL",
            intraday_bars=bars,
            signal_index=0,
            daily_bars=_uptrend_daily_bars(),
            settings=_settings(),
        )
        assert result is None

    def test_no_signal_when_low_volume(self):
        bars = [_bar(77.5, open=77.0, high=77.8, low=77.0, volume=500.0)]
        result = evaluate_bear_gap_and_drop_signal(
            symbol="AAPL",
            intraday_bars=bars,
            signal_index=0,
            daily_bars=_downtrend_daily_bars(),
            settings=_settings(),
        )
        assert result is None
