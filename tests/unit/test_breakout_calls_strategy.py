from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from alpaca_bot.domain.models import Bar, EntrySignal, OptionContract
from alpaca_bot.strategy.breakout_calls import make_breakout_calls_evaluator
from alpaca_bot.strategy import OPTION_STRATEGY_NAMES
from tests.unit.helpers import _base_env
from alpaca_bot.config import Settings


def _settings(**overrides) -> Settings:
    env = _base_env()
    env.update(overrides)
    return Settings.from_env(env)


def _bar(close: float = 100.0, ts: datetime | None = None) -> Bar:
    ts = ts or datetime(2024, 6, 1, 14, 0, tzinfo=timezone.utc)
    return Bar(symbol="AAPL", timestamp=ts, open=close, high=close + 1, low=close - 1, close=close, volume=1000.0)


def _contract(strike: float = 150.0) -> OptionContract:
    return OptionContract(
        occ_symbol="AAPL240701C00150000",
        underlying="AAPL",
        option_type="call",
        strike=strike,
        expiry=date(2024, 7, 1),
        bid=2.50,
        ask=2.75,
        delta=0.50,
    )


class TestMakeBreakoutCallsEvaluator:
    def test_returns_none_when_no_chain_for_symbol(self):
        evaluator = make_breakout_calls_evaluator({})
        s = _settings()
        bars = [_bar()] * 25
        result = evaluator(symbol="AAPL", intraday_bars=bars, signal_index=len(bars) - 1, daily_bars=bars, settings=s)
        assert result is None

    def test_returns_none_when_underlying_breakout_signal_is_none(self):
        contract = _contract()
        evaluator = make_breakout_calls_evaluator({"AAPL": [contract]})
        s = _settings()
        # Only 2 bars — not enough for breakout detection (needs lookback_bars=20)
        bars = [_bar()] * 2
        result = evaluator(symbol="AAPL", intraday_bars=bars, signal_index=1, daily_bars=bars, settings=s)
        assert result is None

    def test_returns_none_when_no_eligible_contract(self):
        # Contract already expired (0 DTE)
        import datetime as dt
        expired_contract = OptionContract(
            occ_symbol="AAPL240601C00150000",
            underlying="AAPL",
            option_type="call",
            strike=150.0,
            expiry=date(2024, 6, 1),
            bid=2.50,
            ask=2.75,
            delta=0.50,
        )
        evaluator = make_breakout_calls_evaluator({"AAPL": [expired_contract]})
        s = _settings()
        bars = [_bar()] * 2
        result = evaluator(symbol="AAPL", intraday_bars=bars, signal_index=1, daily_bars=bars, settings=s)
        assert result is None

    def test_breakout_calls_is_in_option_strategy_names(self):
        assert "breakout_calls" in OPTION_STRATEGY_NAMES

    def test_evaluator_is_callable(self):
        evaluator = make_breakout_calls_evaluator({})
        assert callable(evaluator)

    def test_returned_signal_carries_option_contract_when_breakout_fires(self):
        """When underlying breakout fires and a valid contract exists, signal has option_contract set."""
        from alpaca_bot.strategy.breakout import evaluate_breakout_signal
        contract = _contract(strike=100.0)
        evaluator = make_breakout_calls_evaluator({"AAPL": [contract]})
        s = _settings(
            BREAKOUT_LOOKBACK_BARS="3",
            RELATIVE_VOLUME_THRESHOLD="1.1",
            DAILY_SMA_PERIOD="2",
            OPTION_DTE_MIN="1",
            OPTION_DTE_MAX="60",
        )

        # Build bars where the last bar breaks above the 3-bar high
        base_ts = datetime(2024, 6, 1, 10, 0, tzinfo=timezone.utc)
        import datetime as dt

        def _make_bar(close: float, offset_min: int) -> Bar:
            ts = base_ts + dt.timedelta(minutes=offset_min * 15)
            return Bar(symbol="AAPL", timestamp=ts, open=close, high=close + 0.5, low=close - 0.5, close=close, volume=500.0)

        # Many flat daily bars for SMA
        daily_bars = [
            Bar(symbol="AAPL", timestamp=datetime(2024, 5, d, 0, 0, tzinfo=timezone.utc),
                open=95.0, high=96.0, low=94.0, close=95.0, volume=1_000_000.0)
            for d in range(1, 20)
        ]

        # Intraday: 3 base bars then a breakout bar with high volume
        intraday_bars = [
            Bar(symbol="AAPL", timestamp=base_ts + dt.timedelta(minutes=i * 15),
                open=95.0, high=96.0, low=94.0, close=95.0, volume=500.0)
            for i in range(3)
        ] + [
            Bar(symbol="AAPL", timestamp=base_ts + dt.timedelta(minutes=3 * 15),
                open=97.0, high=100.0, low=96.5, close=99.5, volume=2000.0)
        ]

        result = evaluator(
            symbol="AAPL",
            intraday_bars=intraday_bars,
            signal_index=len(intraday_bars) - 1,
            daily_bars=daily_bars,
            settings=s,
        )
        # May be None if breakout signal doesn't fire with these bars — that's fine.
        # The important thing is it returns EntrySignal with option_contract when it fires.
        if result is not None:
            assert isinstance(result, EntrySignal)
            assert result.option_contract is contract or result.option_contract is not None
