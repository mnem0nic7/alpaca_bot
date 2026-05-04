from __future__ import annotations

import pytest

from alpaca_bot.tuning.surrogate import SurrogateModel


def _make_records(n: int, score_fn=None) -> list[dict]:
    """Synthetic training records with 2 params."""
    records = []
    for i in range(n):
        p1 = float(10 + (i % 4) * 5)    # 10, 15, 20, 25, repeating
        p2 = float(1.0 + (i % 3) * 0.2)  # 1.0, 1.2, 1.4, repeating
        score = score_fn(p1, p2) if score_fn else float(i % 5) * 0.1 + 0.05
        records.append({
            "params": {"LOOKBACK": str(int(p1)), "THRESHOLD": str(p2)},
            "score": score,
        })
    return records


def test_surrogate_cold_start_returns_false() -> None:
    model = SurrogateModel(min_samples=50)
    records = _make_records(49)
    result = model.fit(records)
    assert result is False
    assert not model.is_fitted


def test_surrogate_fits_when_enough_samples() -> None:
    model = SurrogateModel(min_samples=50)
    records = _make_records(60)
    result = model.fit(records)
    assert result is True
    assert model.is_fitted


def test_surrogate_predict_returns_none_when_not_fitted() -> None:
    model = SurrogateModel()
    pred = model.predict({"LOOKBACK": "20", "THRESHOLD": "1.5"})
    assert pred is None


def test_surrogate_predict_returns_float_after_fit() -> None:
    model = SurrogateModel(min_samples=10)
    records = _make_records(20)
    model.fit(records)
    pred = model.predict({"LOOKBACK": "20", "THRESHOLD": "1.5"})
    assert isinstance(pred, float)


def test_surrogate_predict_is_deterministic() -> None:
    model = SurrogateModel(min_samples=10)
    records = _make_records(20)
    model.fit(records)
    params = {"LOOKBACK": "20", "THRESHOLD": "1.5"}
    assert model.predict(params) == model.predict(params)


# --- TuningResultStore.load_all_scored integration ---

import json


class _FakeConn:
    """Minimal ConnectionProtocol stub for TuningResultStore tests."""

    def __init__(self, rows):
        self._rows = rows

    def cursor(self):
        return self

    def execute(self, sql, params=None):
        self._last_params = params

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def commit(self): pass
    def rollback(self): pass


def test_load_all_scored_returns_list_of_dicts() -> None:
    from alpaca_bot.storage.repositories import TuningResultStore

    raw_params = {"BREAKOUT_LOOKBACK_BARS": "20", "RELATIVE_VOLUME_THRESHOLD": "1.5"}
    rows = [(json.dumps(raw_params), 0.75)]
    conn = _FakeConn(rows)
    store = TuningResultStore(conn)
    results = store.load_all_scored(trading_mode="paper")
    assert len(results) == 1
    assert results[0]["params"] == raw_params
    assert results[0]["score"] == 0.75
