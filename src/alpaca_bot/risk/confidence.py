from __future__ import annotations

import bisect


def compute_confidence_scores(
    sharpes: dict[str, float],
    floor: float,
) -> dict[str, float]:
    """Return per-strategy confidence score in [floor, 1.0] from Sharpe percentile rank.

    Strategies with sharpe <= 0 (no positive history) receive `floor` so they
    still participate at minimum size rather than being shut out.

    When all strategies have equal Sharpe (common at startup), all receive `floor`.

    Only strategies that pass the floor gate are included in the returned dict.
    Strategies absent from `sharpes` should be handled by callers (pass floor).
    """
    if not sharpes:
        return {}

    positive = {k: v for k, v in sharpes.items() if v > 0}

    # No positive-Sharpe strategies — no differentiation possible
    if not positive:
        return {name: floor for name in sharpes}

    # All positive Sharpes are equal and there are multiple strategies — no ranking useful
    ranked = sorted(positive.values())
    if len(positive) > 1 and len(set(ranked)) == 1:
        result: dict[str, float] = {}
        for name, sharpe in sharpes.items():
            result[name] = floor
        return result

    n = len(ranked)
    scores: dict[str, float] = {}
    for name, sharpe in sharpes.items():
        if sharpe <= 0:
            scores[name] = floor
        else:
            idx = bisect.bisect_left(ranked, sharpe)
            # Map [0, n-1] index to [floor, 1.0] range
            if n == 1:
                raw = 1.0  # Single positive Sharpe gets score 1.0
            else:
                raw = idx / (n - 1)  # [0.0, 1.0]
            scores[name] = floor + raw * (1.0 - floor)

    return {k: v for k, v in scores.items() if v >= floor}
