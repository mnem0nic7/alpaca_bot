from alpaca_bot.strategy import OPTION_STRATEGY_FACTORIES, OPTION_STRATEGY_NAMES


_EXPECTED_BEAR_STRATEGIES = {
    "bear_breakdown",
    "bear_momentum",
    "bear_orb",
    "bear_low_watermark",
    "bear_ema_rejection",
    "bear_vwap_breakdown",
    "bear_gap_and_drop",
    "bear_flag",
    "bear_vwap_cross_down",
    "bear_bb_squeeze_down",
    "bear_failed_breakout",
}


class TestOptionStrategyRegistry:
    def test_all_bear_strategies_registered(self):
        assert _EXPECTED_BEAR_STRATEGIES <= OPTION_STRATEGY_FACTORIES.keys()

    def test_breakout_calls_registered(self):
        assert "breakout_calls" in OPTION_STRATEGY_FACTORIES

    def test_option_strategy_names_matches_factories(self):
        assert OPTION_STRATEGY_NAMES == frozenset(OPTION_STRATEGY_FACTORIES)

    def test_all_factories_are_callable(self):
        for name, factory in OPTION_STRATEGY_FACTORIES.items():
            evaluator = factory({})
            assert callable(evaluator), f"{name} factory did not return a callable"

    def test_each_evaluator_returns_none_without_chains(self):
        from datetime import datetime, timezone
        from alpaca_bot.domain.models import Bar

        def _bar(close: float) -> Bar:
            return Bar(
                symbol="AAPL",
                timestamp=datetime(2024, 1, 15, 10, 0, tzinfo=timezone.utc),
                open=close,
                high=close,
                low=close,
                close=close,
                volume=1000.0,
            )

        from types import SimpleNamespace
        from zoneinfo import ZoneInfo

        settings = SimpleNamespace(
            daily_sma_period=5,
            breakout_lookback_bars=3,
            relative_volume_lookback_bars=3,
            relative_volume_threshold=1.5,
            atr_period=3,
            atr_stop_multiplier=1.0,
            entry_stop_price_buffer=0.01,
            ema_period=9,
            high_watermark_lookback_days=5,
            orb_opening_bars=2,
            vwap_dip_threshold_pct=0.015,
            gap_threshold_pct=0.02,
            gap_volume_threshold=2.0,
            bull_flag_min_run_pct=0.02,
            bull_flag_consolidation_volume_ratio=0.6,
            bull_flag_consolidation_range_pct=0.5,
            bb_period=5,
            bb_std_dev=2.0,
            bb_squeeze_threshold_pct=0.03,
            bb_squeeze_min_bars=2,
            failed_breakdown_volume_ratio=2.0,
            failed_breakdown_recapture_buffer_pct=0.001,
            option_dte_min=21,
            option_dte_max=60,
            option_delta_target=0.5,
            market_timezone=ZoneInfo("America/New_York"),
        )
        bars = [_bar(100.0) for _ in range(10)]
        daily_bars = [_bar(100.0) for _ in range(7)]

        for name, factory in OPTION_STRATEGY_FACTORIES.items():
            evaluator = factory({})
            result = evaluator(
                symbol="AAPL",
                intraday_bars=bars,
                signal_index=9,
                daily_bars=daily_bars,
                settings=settings,
            )
            assert result is None, f"{name}: expected None when no chains, got {result}"
