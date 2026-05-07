from bear_test_helpers import _bar, _downtrend_daily_bars, _settings, _uptrend_daily_bars

from alpaca_bot.strategy.bear_vwap_breakdown import evaluate_bear_vwap_breakdown_signal


def _bars_for_vwap_breakdown() -> list:
    """Uniform bars so VWAP≈100, then signal bar spikes above VWAP and closes below."""
    bars = [_bar(100.0, high=100.5, low=99.5, volume=1000.0) for _ in range(4)]
    # threshold=0.015, VWAP*(1+0.015)≈101.5; signal.high=102>101.5; signal.close=98<VWAP
    bars.append(_bar(98.0, high=102.0, low=97.5, volume=2000.0))
    return bars


class TestBearVwapBreakdownSignal:
    def test_fires_on_rejection_above_vwap(self):
        bars = _bars_for_vwap_breakdown()
        result = evaluate_bear_vwap_breakdown_signal(
            symbol="AAPL",
            intraday_bars=bars,
            signal_index=4,
            daily_bars=_downtrend_daily_bars(),
            settings=_settings(),
        )
        assert result is not None

    def test_no_signal_when_close_above_vwap(self):
        bars = [_bar(100.0, high=100.5, low=99.5, volume=1000.0) for _ in range(4)]
        bars.append(_bar(101.0, high=102.0, low=99.5, volume=2000.0))
        result = evaluate_bear_vwap_breakdown_signal(
            symbol="AAPL",
            intraday_bars=bars,
            signal_index=4,
            daily_bars=_downtrend_daily_bars(),
            settings=_settings(),
        )
        assert result is None

    def test_no_signal_when_uptrend(self):
        bars = _bars_for_vwap_breakdown()
        result = evaluate_bear_vwap_breakdown_signal(
            symbol="AAPL",
            intraday_bars=bars,
            signal_index=4,
            daily_bars=_uptrend_daily_bars(),
            settings=_settings(),
        )
        assert result is None
