from bear_test_helpers import _bar, _downtrend_daily_bars, _settings, _uptrend_daily_bars

from alpaca_bot.strategy.bear_low_watermark import evaluate_bear_low_watermark_signal


def _intraday_bars(signal_close: float, signal_low: float) -> list:
    return [
        _bar(105.0),
        _bar(104.0),
        _bar(signal_close, low=signal_low),
    ]


class TestBearLowWatermarkSignal:
    def test_fires_on_new_session_low(self):
        # _downtrend_daily_bars: bars[-1]=today, bars[:-1] completed (6 bars)
        # completed[-5:] = bars[1:6], lows=[108,108,108,108,78], historical_low=78
        daily = _downtrend_daily_bars()
        bars = _intraday_bars(75.5, 75.0)
        result = evaluate_bear_low_watermark_signal(
            symbol="AAPL",
            intraday_bars=bars,
            signal_index=2,
            daily_bars=daily,
            settings=_settings(),
        )
        assert result is not None

    def test_no_signal_above_historical_low(self):
        daily = _downtrend_daily_bars()
        bars = _intraday_bars(80.0, 79.0)  # 79 > historical_low 78
        result = evaluate_bear_low_watermark_signal(
            symbol="AAPL",
            intraday_bars=bars,
            signal_index=2,
            daily_bars=daily,
            settings=_settings(),
        )
        assert result is None

    def test_no_signal_when_uptrend(self):
        daily = _uptrend_daily_bars()
        bars = _intraday_bars(75.0, 74.0)
        result = evaluate_bear_low_watermark_signal(
            symbol="AAPL",
            intraday_bars=bars,
            signal_index=2,
            daily_bars=daily,
            settings=_settings(),
        )
        assert result is None
