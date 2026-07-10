from __future__ import annotations

import pytest

from alpaca_bot.nightly.candidate_evidence import (
    candidate_contribution,
    contribution_status,
    evidence_verdict,
)


def _audit_row(
    *,
    basket_verdict: str = "positive-edge",
    candidate_trades: int = 40,
    candidate_pnl: float = 25.0,
    candidate_verdict: str | None = "positive-edge",
) -> dict:
    return {
        "verdict": basket_verdict,
        "trade_diagnostics": {
            "strategies": [
                {
                    "strategy": "bull_flag",
                    "trades": 20,
                    "total_pnl": 100.0,
                    "verdict": "positive-edge",
                },
                {
                    "strategy": "orb",
                    "trades": candidate_trades,
                    "total_pnl": candidate_pnl,
                    "mean_trade_pnl": 0.625,
                    "ci_low": 0.1,
                    "ci_high": 1.1,
                    "p_mean_le_zero": 0.02,
                    "verdict": candidate_verdict,
                },
            ]
        },
    }


def test_candidate_contribution_selects_named_strategy() -> None:
    contribution = candidate_contribution(_audit_row(), "orb")

    assert contribution == {
        "trades": 40,
        "total_pnl": 25.0,
        "mean_trade_pnl": 0.625,
        "ci_low": 0.1,
        "ci_high": 1.1,
        "p_mean_le_zero": 0.02,
        "verdict": "positive-edge",
    }
    assert contribution_status(contribution) == "positive_pnl"
    assert evidence_verdict(_audit_row(), "orb") == "positive-edge"


@pytest.mark.parametrize(
    ("trades", "pnl", "expected_status", "expected_verdict"),
    [
        (0, 0.0, "no_trades", "no-candidate-trades"),
        (20, 0.0, "non_positive_pnl", "non-positive-candidate-pnl"),
        (20, -5.0, "non_positive_pnl", "non-positive-candidate-pnl"),
    ],
)
def test_candidate_evidence_rejects_missing_or_non_positive_contribution(
    trades: int,
    pnl: float,
    expected_status: str,
    expected_verdict: str,
) -> None:
    row = _audit_row(candidate_trades=trades, candidate_pnl=pnl)

    assert contribution_status(candidate_contribution(row, "orb")) == expected_status
    assert evidence_verdict(row, "orb") == expected_verdict


def test_candidate_evidence_does_not_inherit_positive_basket_verdict() -> None:
    row = {"verdict": "positive-edge"}

    assert contribution_status(candidate_contribution(row, "orb")) == "unknown"
    assert evidence_verdict(row, "orb") == "missing-candidate-edge-diagnostics"


def test_candidate_evidence_enforces_minimum_trade_sample() -> None:
    row = _audit_row(candidate_trades=7, candidate_pnl=25.0)

    assert (
        evidence_verdict(row, "orb", min_trades=30)
        == "insufficient-candidate-trades"
    )
    assert evidence_verdict(row, "orb", min_trades=7) == "positive-edge"


def test_candidate_evidence_preserves_non_positive_basket_without_diagnostics() -> None:
    row = {"verdict": "no-evidence"}

    assert evidence_verdict(row, "orb") == "no-evidence"
