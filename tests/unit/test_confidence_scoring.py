from __future__ import annotations

import pytest

from alpaca_bot.risk.confidence import compute_confidence_scores


def test_empty_sharpes_returns_empty() -> None:
    assert compute_confidence_scores({}, floor=0.25) == {}


def test_all_zero_sharpes_assigns_floor_to_everyone() -> None:
    # No history — all strategies get floor so they still participate.
    sharpes = {"a": 0.0, "b": 0.0, "c": 0.0}
    scores = compute_confidence_scores(sharpes, floor=0.25)
    assert set(scores.keys()) == {"a", "b", "c"}
    for v in scores.values():
        assert v == pytest.approx(0.25)


def test_all_equal_positive_sharpes_assigns_floor() -> None:
    sharpes = {"a": 1.0, "b": 1.0, "c": 1.0}
    scores = compute_confidence_scores(sharpes, floor=0.25)
    for v in scores.values():
        assert v == pytest.approx(0.25)


def test_highest_sharpe_gets_score_one() -> None:
    sharpes = {"low": 0.5, "mid": 1.0, "high": 2.0}
    scores = compute_confidence_scores(sharpes, floor=0.0)
    assert scores["high"] == pytest.approx(1.0)


def test_lowest_positive_sharpe_gets_floor() -> None:
    sharpes = {"low": 0.5, "mid": 1.0, "high": 2.0}
    scores = compute_confidence_scores(sharpes, floor=0.25)
    assert scores["low"] == pytest.approx(0.25)
    assert scores["high"] == pytest.approx(1.0)
    assert 0.25 < scores["mid"] < 1.0


def test_zero_sharpe_strategy_gets_floor_when_others_are_positive() -> None:
    sharpes = {"new": 0.0, "proven": 2.0}
    scores = compute_confidence_scores(sharpes, floor=0.20)
    assert scores["new"] == pytest.approx(0.20)
    assert scores["proven"] == pytest.approx(1.0)


def test_floor_raise_excludes_all_zero_sharpe_strategies() -> None:
    # Floor raised to 0.50; zero-sharpe strategies still get floor=0.50.
    sharpes = {"a": 0.0, "b": 0.0}
    scores = compute_confidence_scores(sharpes, floor=0.50)
    # All-zero → all get floor, all pass gate
    assert len(scores) == 2
    for v in scores.values():
        assert v == pytest.approx(0.50)


def test_scores_bounded_between_floor_and_one() -> None:
    sharpes = {f"s{i}": float(i) for i in range(10)}
    scores = compute_confidence_scores(sharpes, floor=0.10)
    for name, score in scores.items():
        assert 0.10 <= score <= 1.0 + 1e-9, f"{name}: {score}"


def test_tie_breaking_uses_bisect_left() -> None:
    # Two strategies with same Sharpe should get the same score.
    sharpes = {"a": 1.0, "b": 1.0, "c": 2.0}
    scores = compute_confidence_scores(sharpes, floor=0.0)
    assert scores["a"] == pytest.approx(scores["b"])
    assert scores["c"] > scores["a"]
