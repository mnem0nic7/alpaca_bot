from __future__ import annotations

from typing import Any


class SurrogateModel:
    """Gradient-boosted surrogate trained on historical (params, score) pairs.

    Cold-start: fit() returns False when fewer than min_samples scored rows exist.
    predict() returns None when not fitted — callers skip reordering.
    """

    def __init__(self, min_samples: int = 50) -> None:
        self._min_samples = min_samples
        self._model: Any = None
        self._keys: list[str] = []

    @property
    def is_fitted(self) -> bool:
        return self._model is not None

    def fit(self, records: list[dict]) -> bool:
        """Train on records [{params: dict[str,str], score: float}, ...].

        Returns True if the model was fitted, False if below min_samples.
        """
        scored = [r for r in records if r.get("score") is not None]
        if len(scored) < self._min_samples:
            return False

        try:
            from sklearn.ensemble import GradientBoostingRegressor
        except ImportError as exc:
            raise ImportError(
                "scikit-learn is required for SurrogateModel. "
                "Install it with: pip install scikit-learn>=1.4"
            ) from exc

        all_keys = sorted({k for r in scored for k in r["params"]})
        X = [
            [float(r["params"].get(k, "0")) for k in all_keys]
            for r in scored
        ]
        y = [float(r["score"]) for r in scored]

        model = GradientBoostingRegressor(
            n_estimators=100, max_depth=3, random_state=42
        )
        model.fit(X, y)
        self._model = model
        self._keys = all_keys
        return True

    def predict(self, params: dict[str, str]) -> float | None:
        """Return predicted score for params, or None if not fitted."""
        if self._model is None:
            return None
        features = [float(params.get(k, "0")) for k in self._keys]
        return float(self._model.predict([features])[0])
