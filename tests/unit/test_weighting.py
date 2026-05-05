from __future__ import annotations

import math
from datetime import date

import pytest

from alpaca_bot.risk.weighting import WeightResult, compute_strategy_weights


def _row(strategy: str, exit_date: date, pnl: float) -> dict:
    return {"strategy_name": strategy, "exit_date": exit_date, "pnl": pnl}


def test_equal_weights_when_no_history() -> None:
    result = compute_strategy_weights([], ["breakout", "momentum", "orb"])
    assert set(result.weights.keys()) == {"breakout", "momentum", "orb"}
    for w in result.weights.values():
        assert abs(w - 1 / 3) < 1e-9
    assert abs(sum(result.weights.values()) - 1.0) < 1e-9
    for s in result.sharpes.values():
        assert s == 0.0


def test_equal_weights_when_fewer_than_min_trades() -> None:
    d = date(2026, 1, 1)
    rows = [_row("breakout", d, 100.0), _row("breakout", d, 100.0)]  # only 2 trades
    result = compute_strategy_weights(rows, ["breakout", "momentum"])
    assert abs(result.weights["breakout"] - 0.5) < 1e-9
    assert abs(result.weights["momentum"] - 0.5) < 1e-9
    assert result.sharpes["breakout"] == 0.0


def test_weights_proportional_to_sharpe() -> None:
    # Give breakout 5 winning trades (high Sharpe), momentum 5 flat/zero trades
    rows = []
    for i in range(5):
        rows.append(_row("breakout", date(2026, 1, i + 1), 100.0))
    for i in range(5):
        rows.append(_row("momentum", date(2026, 1, i + 1), 0.0))
    result = compute_strategy_weights(rows, ["breakout", "momentum"])
    # momentum has std=0, mean=0 → sharpe=0
    assert result.sharpes["momentum"] == 0.0
    # breakout gets std=0, mean>0 → sharpe=1.0 → all weight goes to breakout
    # but floor prevents complete starvation
    assert result.weights["breakout"] > result.weights["momentum"]
    assert abs(sum(result.weights.values()) - 1.0) < 1e-9


def test_floor_applied_when_strategy_has_low_sharpe() -> None:
    # Give 5 strategies varying Sharpes; one very low
    strategies = ["a", "b", "c", "d", "e"]
    rows = []
    sharpe_inputs = [2.0, 2.0, 2.0, 2.0, 0.01]
    for i, (name, s) in enumerate(zip(strategies, sharpe_inputs)):
        for day in range(5):
            rows.append(_row(name, date(2026, 1, day + 1), s * (day + 1)))
    result = compute_strategy_weights(rows, strategies)
    for w in result.weights.values():
        assert w >= 0.05 - 1e-9, f"weight {w} below floor"
    assert abs(sum(result.weights.values()) - 1.0) < 1e-9


def test_cap_applied_when_one_strategy_dominates() -> None:
    # Give breakout a very high Sharpe, others much lower
    rows = []
    for day in range(10):
        rows.append(_row("breakout", date(2026, 1, day + 1), 500.0 * (day + 1)))
    for name in ["momentum", "orb"]:
        for day in range(5):
            rows.append(_row(name, date(2026, 1, day + 1), 1.0))
    result = compute_strategy_weights(rows, ["breakout", "momentum", "orb"])
    assert result.weights["breakout"] <= 0.40 + 1e-9
    assert abs(sum(result.weights.values()) - 1.0) < 1e-9


def test_weights_sum_to_one_in_all_cases() -> None:
    for n in [1, 2, 5, 11]:
        strategies = [f"s{i}" for i in range(n)]
        rows = []
        for i, name in enumerate(strategies):
            for day in range(5):
                rows.append(_row(name, date(2026, 1, day + 1), float(i + 1) * 10.0))
        result = compute_strategy_weights(rows, strategies)
        assert abs(sum(result.weights.values()) - 1.0) < 1e-9


def test_empty_active_strategies_returns_empty() -> None:
    result = compute_strategy_weights([], [])
    assert result.weights == {}
    assert result.sharpes == {}


def test_trade_rows_for_inactive_strategy_are_ignored() -> None:
    # "ghost" is in rows but not in active_strategies
    rows = [_row("ghost", date(2026, 1, 1), 999.0)]
    result = compute_strategy_weights(rows, ["breakout"])
    assert "ghost" not in result.weights
    assert result.weights == {"breakout": 1.0}


def test_single_strategy_gets_weight_one() -> None:
    rows = []
    for day in range(5):
        rows.append(_row("breakout", date(2026, 1, day + 1), 100.0))
    result = compute_strategy_weights(rows, ["breakout"])
    assert abs(result.weights["breakout"] - 1.0) < 1e-9


def test_sharpe_uses_annualised_formula() -> None:
    # 5 trades, one per day, all same pnl → std=0, mean>0 → sharpe=1.0
    rows = [_row("breakout", date(2026, 1, i + 1), 50.0) for i in range(5)]
    result = compute_strategy_weights(rows, ["breakout"])
    assert result.sharpes["breakout"] == 1.0

    # 5 trades, varying pnl → check formula: mean/std * sqrt(252)
    rows2 = [_row("momentum", date(2026, 1, i + 1), float(i + 1) * 10.0) for i in range(5)]
    result2 = compute_strategy_weights(rows2, ["momentum"])
    daily_pnl = [10.0, 20.0, 30.0, 40.0, 50.0]
    mean_pnl = sum(daily_pnl) / 5
    variance = sum((v - mean_pnl) ** 2 for v in daily_pnl) / 4
    expected_sharpe = max(0.0, mean_pnl / variance ** 0.5 * math.sqrt(252))
    assert abs(result2.sharpes["momentum"] - expected_sharpe) < 1e-6
