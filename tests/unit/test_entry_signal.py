from __future__ import annotations

import inspect

import pytest
from datetime import datetime, timezone

from alpaca_bot.domain.models import Bar, EntrySignal
from alpaca_bot.strategy import StrategySignalEvaluator


def _make_bar(symbol: str = "AAPL", high: float = 102.0, close: float = 101.5) -> Bar:
    return Bar(
        symbol=symbol,
        timestamp=datetime(2026, 1, 2, 14, 0, tzinfo=timezone.utc),
        open=100.0,
        high=high,
        low=99.0,
        close=close,
        volume=50000.0,
    )


def test_entry_signal_fields():
    sig = EntrySignal(
        symbol="AAPL",
        signal_bar=_make_bar(),
        entry_level=100.0,
        relative_volume=2.5,
        stop_price=102.1,
        limit_price=102.2,
        initial_stop_price=99.9,
    )
    assert sig.entry_level == 100.0
    assert sig.symbol == "AAPL"
    assert not hasattr(sig, "breakout_level")


def test_entry_signal_is_frozen():
    sig = EntrySignal(
        symbol="AAPL",
        signal_bar=_make_bar(),
        entry_level=100.0,
        relative_volume=2.5,
        stop_price=102.1,
        limit_price=102.2,
        initial_stop_price=99.9,
    )
    with pytest.raises(Exception):
        sig.entry_level = 99.0  # type: ignore[misc]


def test_strategy_registry_evaluator_protocol():
    from alpaca_bot.strategy import STRATEGY_REGISTRY, StrategySignalEvaluator
    for name, evaluator in STRATEGY_REGISTRY.items():
        assert isinstance(evaluator, StrategySignalEvaluator), (
            f"STRATEGY_REGISTRY[{name!r}] does not satisfy StrategySignalEvaluator Protocol"
        )


def test_breakout_evaluator_returns_entry_signal_type():
    from alpaca_bot.strategy.breakout import evaluate_breakout_signal
    hints = inspect.get_annotations(evaluate_breakout_signal, eval_str=True)
    assert "EntrySignal" in str(hints.get("return", ""))
