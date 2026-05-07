from datetime import datetime
from zoneinfo import ZoneInfo

from bear_test_helpers import _bar, _downtrend_daily_bars, _put_contract, _settings, _uptrend_daily_bars

from alpaca_bot.strategy.bear_momentum import evaluate_bear_momentum_signal, make_bear_momentum_evaluator


def _three_down_bars(n_leading: int = 1) -> list:
    """n_leading neutral bars then 3 consecutive descending-close bars."""
    tz = ZoneInfo("America/New_York")
    bars = []
    close = 105.0
    for i in range(n_leading):
        ts = datetime(2024, 1, 2, 10, i, 0, tzinfo=tz)
        bars.append(_bar(close, ts=ts, high=close + 0.5, low=close - 0.5))
    for j in range(3):
        close -= 1.0
        ts = datetime(2024, 1, 2, 10, n_leading + j, 0, tzinfo=tz)
        bars.append(_bar(close, ts=ts, high=close + 0.5, low=close - 0.5))
    return bars


class TestEvaluateBearMomentumSignal:
    def test_momentum_fires_on_three_consecutive_down_bars(self):
        bars = _three_down_bars()
        signal_index = len(bars) - 1
        result = evaluate_bear_momentum_signal(
            symbol="AAPL",
            intraday_bars=bars,
            signal_index=signal_index,
            daily_bars=_downtrend_daily_bars(),
            settings=_settings(),
        )
        assert result is not None
        assert result.entry_level == bars[signal_index].close

    def test_no_signal_when_only_two_down_bars(self):
        tz = ZoneInfo("America/New_York")
        bars = [
            _bar(105.0, ts=datetime(2024, 1, 2, 10, 0, 0, tzinfo=tz), high=105.5, low=104.5),
            _bar(104.0, ts=datetime(2024, 1, 2, 10, 1, 0, tzinfo=tz), high=104.5, low=103.5),
            _bar(103.0, ts=datetime(2024, 1, 2, 10, 2, 0, tzinfo=tz), high=103.5, low=102.5),
            _bar(103.0, ts=datetime(2024, 1, 2, 10, 3, 0, tzinfo=tz), high=103.5, low=102.5),
        ]
        result = evaluate_bear_momentum_signal(
            symbol="AAPL",
            intraday_bars=bars,
            signal_index=3,
            daily_bars=_downtrend_daily_bars(),
            settings=_settings(),
        )
        assert result is None

    def test_no_signal_when_uptrend(self):
        bars = _three_down_bars()
        result = evaluate_bear_momentum_signal(
            symbol="AAPL",
            intraday_bars=bars,
            signal_index=len(bars) - 1,
            daily_bars=_uptrend_daily_bars(),
            settings=_settings(),
        )
        assert result is None

    def test_no_signal_when_signal_index_too_small(self):
        bars = _three_down_bars()
        result = evaluate_bear_momentum_signal(
            symbol="AAPL",
            intraday_bars=bars,
            signal_index=2,
            daily_bars=_downtrend_daily_bars(),
            settings=_settings(),
        )
        assert result is None

    def test_stop_price_above_signal_bar_high(self):
        bars = _three_down_bars()
        signal_index = len(bars) - 1
        result = evaluate_bear_momentum_signal(
            symbol="AAPL",
            intraday_bars=bars,
            signal_index=signal_index,
            daily_bars=_downtrend_daily_bars(),
            settings=_settings(atr_stop_multiplier=1.0),
        )
        assert result is not None
        assert result.stop_price > bars[signal_index].high


class TestMakeBearMomentumEvaluator:
    def test_evaluator_returns_entry_signal_with_put(self):
        chains = {"AAPL": [_put_contract()]}
        evaluator = make_bear_momentum_evaluator(chains)
        bars = _three_down_bars()
        result = evaluator(
            symbol="AAPL",
            intraday_bars=bars,
            signal_index=len(bars) - 1,
            daily_bars=_downtrend_daily_bars(),
            settings=_settings(),
        )
        assert result is not None
        assert result.option_contract is not None
        assert result.option_contract.option_type == "put"

    def test_evaluator_returns_none_when_no_chains(self):
        evaluator = make_bear_momentum_evaluator({})
        bars = _three_down_bars()
        result = evaluator(
            symbol="AAPL",
            intraday_bars=bars,
            signal_index=len(bars) - 1,
            daily_bars=_downtrend_daily_bars(),
            settings=_settings(),
        )
        assert result is None
