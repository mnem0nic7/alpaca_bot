from __future__ import annotations


def candidate_contribution(audit_row: dict, candidate: str) -> dict:
    diagnostics = audit_row.get("trade_diagnostics") or {}
    for row in diagnostics.get("strategies", []):
        if row.get("strategy") == candidate:
            return {
                "trades": int(row.get("trades") or 0),
                "total_pnl": row.get("total_pnl"),
                "mean_trade_pnl": row.get("mean_trade_pnl"),
                "ci_low": row.get("ci_low"),
                "ci_high": row.get("ci_high"),
                "p_mean_le_zero": row.get("p_mean_le_zero"),
                "verdict": row.get("verdict"),
            }
    return {
        "trades": None,
        "total_pnl": None,
        "mean_trade_pnl": None,
        "ci_low": None,
        "ci_high": None,
        "p_mean_le_zero": None,
        "verdict": None,
    }


def contribution_status(contribution: dict) -> str:
    trades = contribution["trades"]
    total_pnl = contribution["total_pnl"]
    if trades is None:
        return "unknown"
    if trades <= 0:
        return "no_trades"
    if total_pnl is not None and float(total_pnl) <= 0.0:
        return "non_positive_pnl"
    return "positive_pnl"


def evidence_verdict(
    audit_row: dict,
    candidate: str,
    *,
    min_trades: int = 0,
) -> str | None:
    basket_verdict = audit_row.get("verdict")
    contribution = candidate_contribution(audit_row, candidate)
    status = contribution_status(contribution)
    if status == "no_trades":
        return "no-candidate-trades"
    trades = contribution.get("trades")
    if trades is not None and trades < min_trades:
        return "insufficient-candidate-trades"
    if status == "non_positive_pnl":
        return "non-positive-candidate-pnl"
    candidate_verdict = contribution.get("verdict")
    if candidate_verdict:
        return str(candidate_verdict)
    if basket_verdict == "positive-edge":
        return "missing-candidate-edge-diagnostics"
    return str(basket_verdict) if basket_verdict is not None else None
