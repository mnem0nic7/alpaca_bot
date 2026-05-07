from bear_test_helpers import _bar, _downtrend_daily_bars, _put_contract, _settings, _uptrend_daily_bars

from alpaca_bot.strategy.bear_ema_rejection import evaluate_bear_ema_rejection_signal, make_bear_ema_rejection_evaluator


def _intraday_ema_rejection() -> list:
    """
    EMA period=9. Bars 0-12 decline slowly (EMA lags above close).
    Bar 13 bounces above EMA (close=108.0 > EMA≈107.68).
    Bar 14 drops sharply back below EMA (the rejection signal).
    """
    bars = []
    for i in range(13):
        close = 110.0 - i * 0.3
        bars.append(_bar(close, high=close + 0.5, low=close - 0.5))
    bars.append(_bar(108.0, high=108.5, low=107.5))
    bars.append(_bar(103.0, high=104.0, low=102.5))
    return bars


class TestBearEmaRejectionSignal:
    def test_fires_on_cross_below_ema(self):
        bars = _intraday_ema_rejection()
        result = evaluate_bear_ema_rejection_signal(
            symbol="AAPL",
            intraday_bars=bars,
            signal_index=14,
            daily_bars=_downtrend_daily_bars(),
            settings=_settings(ema_period=9),
        )
        assert result is not None

    def test_no_signal_when_uptrend(self):
        bars = _intraday_ema_rejection()
        result = evaluate_bear_ema_rejection_signal(
            symbol="AAPL",
            intraday_bars=bars,
            signal_index=14,
            daily_bars=_uptrend_daily_bars(),
            settings=_settings(ema_period=9),
        )
        assert result is None

    def test_no_signal_when_no_cross(self):
        bars = [_bar(110.0 + i * 0.5, high=110.0 + i * 0.5 + 0.3, low=110.0 + i * 0.5 - 0.3)
                for i in range(15)]
        result = evaluate_bear_ema_rejection_signal(
            symbol="AAPL",
            intraday_bars=bars,
            signal_index=14,
            daily_bars=_downtrend_daily_bars(),
            settings=_settings(ema_period=9),
        )
        assert result is None

    def test_no_signal_with_insufficient_bars(self):
        bars = [_bar(100.0) for _ in range(5)]
        result = evaluate_bear_ema_rejection_signal(
            symbol="AAPL",
            intraday_bars=bars,
            signal_index=4,
            daily_bars=_downtrend_daily_bars(),
            settings=_settings(ema_period=9),
        )
        assert result is None


class TestMakeBearEmaRejectionEvaluator:
    def test_evaluator_returns_entry_signal_with_put(self):
        chains = {"AAPL": [_put_contract()]}
        evaluator = make_bear_ema_rejection_evaluator(chains)
        bars = _intraday_ema_rejection()
        result = evaluator(
            symbol="AAPL",
            intraday_bars=bars,
            signal_index=14,
            daily_bars=_downtrend_daily_bars(),
            settings=_settings(ema_period=9),
        )
        assert result is not None
        assert result.option_contract is not None
        assert result.option_contract.option_type == "put"
