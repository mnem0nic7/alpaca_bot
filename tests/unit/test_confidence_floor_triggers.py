from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from alpaca_bot.config import TradingMode
from alpaca_bot.storage.models import ConfidenceFloor


def _make_floor(floor_value: float, watermark: float, manual_baseline: float | None = None) -> ConfidenceFloor:
    return ConfidenceFloor(
        trading_mode=TradingMode.PAPER,
        strategy_version="v1",
        floor_value=floor_value,
        manual_floor_baseline=manual_baseline if manual_baseline is not None else floor_value,
        equity_high_watermark=watermark,
        set_by="system",
        reason="test",
        updated_at=datetime(2026, 5, 8, tzinfo=timezone.utc),
    )


class _FakeFloorStore:
    def __init__(self, rec: ConfidenceFloor | None = None):
        self._rec = rec
        self.upserted: list[ConfidenceFloor] = []

    def load(self, **kwargs) -> ConfidenceFloor | None:
        return self._rec

    def upsert(self, rec: ConfidenceFloor, *, commit: bool = True) -> None:
        self._rec = rec
        self.upserted.append(rec)


def _make_supervisor_with_floor(settings_overrides=None):
    """Return (supervisor, floor_store) with floor store injected into _FakeRuntimeContext."""
    from tests.unit.test_supervisor_weights import _make_settings, _make_supervisor
    overrides = settings_overrides or {}
    settings = _make_settings(**overrides)
    floor_store = _FakeFloorStore()
    sup, _FakeRt = _make_supervisor(settings=settings)
    _FakeRt.confidence_floor_store = floor_store
    return sup, floor_store


def test_drawdown_trigger_raises_floor() -> None:
    """When equity drops >DRAWDOWN_RAISE_PCT below watermark, floor should be raised."""
    sup, floor_store = _make_supervisor_with_floor({
        "DRAWDOWN_RAISE_PCT": "0.05",
        "FLOOR_RAISE_STEP": "0.10",
        "CONFIDENCE_FLOOR": "0.25",
    })
    now = datetime(2026, 5, 8, 14, 30, tzinfo=timezone.utc)

    # Watermark was 10000, current equity is 9000 → 10% drop, exceeds 5% threshold.
    # manual_baseline=0.25 (same as floor_value so it was operator-set initially).
    rec = _make_floor(floor_value=0.25, watermark=10000.0, manual_baseline=0.25)
    floor_store._rec = rec

    sup._check_and_update_floor_triggers(
        current_equity=9000.0,
        now=now,
        daily_bars_for_vol=[],
    )

    assert len(floor_store.upserted) == 1
    raised = floor_store.upserted[0]
    assert raised.floor_value == pytest.approx(0.35)  # 0.25 + 0.10
    assert raised.manual_floor_baseline == pytest.approx(0.25)  # unchanged by auto-raise
    assert raised.set_by == "system"
    assert "drawdown" in raised.reason.lower()


def test_drawdown_trigger_does_not_fire_when_within_threshold() -> None:
    sup, floor_store = _make_supervisor_with_floor({
        "DRAWDOWN_RAISE_PCT": "0.05",
        "FLOOR_RAISE_STEP": "0.10",
        "CONFIDENCE_FLOOR": "0.25",
    })
    rec = _make_floor(floor_value=0.25, watermark=10000.0)
    floor_store._rec = rec

    # Only 3% drop — below 5% threshold
    sup._check_and_update_floor_triggers(
        current_equity=9700.0,
        now=datetime(2026, 5, 8, 14, 30, tzinfo=timezone.utc),
        daily_bars_for_vol=[],
    )

    assert len(floor_store.upserted) == 0


def test_watermark_updates_when_equity_improves() -> None:
    sup, floor_store = _make_supervisor_with_floor({
        "DRAWDOWN_RAISE_PCT": "0.05",
        "FLOOR_RAISE_STEP": "0.10",
        "CONFIDENCE_FLOOR": "0.25",
    })
    rec = _make_floor(floor_value=0.25, watermark=8000.0)
    floor_store._rec = rec

    # Equity 10000 > watermark 8000 → update watermark
    sup._check_and_update_floor_triggers(
        current_equity=10000.0,
        now=datetime(2026, 5, 8, 14, 30, tzinfo=timezone.utc),
        daily_bars_for_vol=[],
    )

    assert len(floor_store.upserted) == 1
    updated = floor_store.upserted[0]
    assert updated.equity_high_watermark == pytest.approx(10000.0)
    assert updated.floor_value == pytest.approx(0.25)  # unchanged


def test_floor_capped_at_0_80() -> None:
    sup, floor_store = _make_supervisor_with_floor({
        "DRAWDOWN_RAISE_PCT": "0.05",
        "FLOOR_RAISE_STEP": "0.10",
        "CONFIDENCE_FLOOR": "0.25",
    })
    rec = _make_floor(floor_value=0.75, watermark=10000.0, manual_baseline=0.25)
    floor_store._rec = rec

    sup._check_and_update_floor_triggers(
        current_equity=9000.0,  # triggers drawdown
        now=datetime(2026, 5, 8, 14, 30, tzinfo=timezone.utc),
        daily_bars_for_vol=[],
    )

    assert floor_store.upserted[0].floor_value == pytest.approx(0.80)  # capped at 0.80


def test_floor_clears_to_manual_baseline_when_triggers_resolve() -> None:
    """When drawdown recovers, floor_value returns to manual_floor_baseline."""
    sup, floor_store = _make_supervisor_with_floor({
        "DRAWDOWN_RAISE_PCT": "0.05",
        "FLOOR_RAISE_STEP": "0.10",
        "CONFIDENCE_FLOOR": "0.25",
    })
    # floor was auto-raised to 0.40; operator's baseline is 0.25.
    # Equity recovered to 9800 (from watermark 10000) → drawdown = 2% < 5%/2 = 2.5%.
    rec = _make_floor(floor_value=0.40, watermark=10000.0, manual_baseline=0.25)
    floor_store._rec = rec

    sup._check_and_update_floor_triggers(
        current_equity=9800.0,  # recovered enough (drawdown < 2.5%)
        now=datetime(2026, 5, 8, 14, 30, tzinfo=timezone.utc),
        daily_bars_for_vol=[],
    )

    assert len(floor_store.upserted) == 1
    cleared = floor_store.upserted[0]
    assert cleared.floor_value == pytest.approx(0.25)  # reset to manual baseline


def test_vol_trigger_raises_floor() -> None:
    from alpaca_bot.domain import Bar

    sup, floor_store = _make_supervisor_with_floor({
        "VOL_RAISE_THRESHOLD": "0.02",  # 2% average daily move threshold
        "FLOOR_RAISE_STEP": "0.10",
        "CONFIDENCE_FLOOR": "0.25",
    })
    rec = _make_floor(floor_value=0.25, watermark=10000.0, manual_baseline=0.25)
    floor_store._rec = rec

    # Build 6 daily bars with ~3% daily moves (exceeds 2% threshold)
    closes = [100.0, 103.0, 99.9, 103.1, 99.7, 103.2]  # ~3% daily swings
    bars = [
        Bar(
            symbol="SPY",
            timestamp=datetime(2026, 4, i + 1, tzinfo=timezone.utc),
            open=c,
            high=c * 1.01,
            low=c * 0.99,
            close=c,
            volume=1_000_000,
        )
        for i, c in enumerate(closes)
    ]

    sup._check_and_update_floor_triggers(
        current_equity=10000.0,  # no drawdown
        now=datetime(2026, 5, 8, 14, 30, tzinfo=timezone.utc),
        daily_bars_for_vol=bars,
    )

    assert len(floor_store.upserted) == 1
    assert floor_store.upserted[0].floor_value == pytest.approx(0.35)
    assert "vol" in floor_store.upserted[0].reason.lower()


def test_no_record_first_cycle_writes_nothing() -> None:
    """On the very first cycle (no DB record), no write should occur."""
    sup, floor_store = _make_supervisor_with_floor({
        "DRAWDOWN_RAISE_PCT": "0.05",
        "FLOOR_RAISE_STEP": "0.10",
        "CONFIDENCE_FLOOR": "0.25",
    })
    # floor_store._rec is None by default
    sup._check_and_update_floor_triggers(
        current_equity=10000.0,
        now=datetime(2026, 5, 8, 14, 30, tzinfo=timezone.utc),
        daily_bars_for_vol=[],
    )
    assert len(floor_store.upserted) == 0  # no write on first cycle with no trigger


def test_drawdown_hysteresis_keeps_floor_raised() -> None:
    """When drawdown is in the hysteresis band (between clear_threshold and raise_threshold),
    the floor should stay raised (not cleared), but not raised further."""
    sup, floor_store = _make_supervisor_with_floor({
        "DRAWDOWN_RAISE_PCT": "0.05",    # 5% raise threshold
        "FLOOR_RAISE_STEP": "0.10",
        "CONFIDENCE_FLOOR": "0.25",
    })
    # Floor was already raised from 0.25 to 0.35. manual_baseline=0.25.
    # Equity recovered to 9700 → drawdown = 3% which is above clear_threshold (2.5%)
    # but below raise_threshold (5%). Should stay at 0.35 (not clear, not raise further).
    rec = _make_floor(floor_value=0.35, watermark=10000.0, manual_baseline=0.25)
    floor_store._rec = rec

    sup._check_and_update_floor_triggers(
        current_equity=9700.0,  # 3% drawdown — in hysteresis band
        now=datetime(2026, 5, 8, 14, 30, tzinfo=timezone.utc),
        daily_bars_for_vol=[],
    )

    # No change to floor (still 0.35), no clear, no raise
    if floor_store.upserted:
        # If a write occurred, it should only be for watermark (no equity improvement here)
        # Actually equity=9700 < watermark=10000 so watermark unchanged too
        # No write should occur at all
        assert False, f"Unexpected upsert: {floor_store.upserted}"
    assert len(floor_store.upserted) == 0


def test_both_triggers_active_raises_floor_by_one_step_only() -> None:
    """When both drawdown and vol triggers fire, floor should advance by exactly one step."""
    from alpaca_bot.domain import Bar

    sup, floor_store = _make_supervisor_with_floor({
        "DRAWDOWN_RAISE_PCT": "0.05",
        "VOL_RAISE_THRESHOLD": "0.02",
        "FLOOR_RAISE_STEP": "0.10",
        "CONFIDENCE_FLOOR": "0.25",
    })
    rec = _make_floor(floor_value=0.25, watermark=10000.0, manual_baseline=0.25)
    floor_store._rec = rec

    # Equity drops 10% (triggers drawdown) AND vol is ~3% (triggers vol)
    closes = [100.0, 103.0, 99.9, 103.1, 99.7, 103.2]
    bars = [
        Bar(
            symbol="SPY",
            timestamp=datetime(2026, 4, i + 1, tzinfo=timezone.utc),
            open=c, high=c * 1.01, low=c * 0.99, close=c,
            volume=1_000_000,
        )
        for i, c in enumerate(closes)
    ]

    sup._check_and_update_floor_triggers(
        current_equity=9000.0,  # 10% drop — triggers drawdown
        now=datetime(2026, 5, 8, 14, 30, tzinfo=timezone.utc),
        daily_bars_for_vol=bars,
    )

    assert len(floor_store.upserted) == 1
    # Should advance by ONE step (0.10), not two (0.20)
    assert floor_store.upserted[0].floor_value == pytest.approx(0.35)  # 0.25 + 0.10


def test_compute_losing_day_streaks_detects_consecutive_losses() -> None:
    from datetime import date
    from alpaca_bot.risk.weighting import compute_losing_day_streaks

    rows = [
        {"strategy_name": "breakout", "exit_date": date(2026, 5, 1), "pnl": -100.0},
        {"strategy_name": "breakout", "exit_date": date(2026, 5, 2), "pnl": -50.0},
        {"strategy_name": "breakout", "exit_date": date(2026, 5, 3), "pnl": -75.0},
        {"strategy_name": "momentum", "exit_date": date(2026, 5, 1), "pnl": 200.0},
        {"strategy_name": "momentum", "exit_date": date(2026, 5, 2), "pnl": -10.0},
    ]
    streaks = compute_losing_day_streaks(rows, ["breakout", "momentum"])
    assert streaks["breakout"] == 3  # three consecutive losing days
    assert streaks["momentum"] == 1  # only one consecutive losing day


def test_compute_losing_day_streaks_resets_on_win() -> None:
    from datetime import date
    from alpaca_bot.risk.weighting import compute_losing_day_streaks

    rows = [
        {"strategy_name": "breakout", "exit_date": date(2026, 5, 1), "pnl": -100.0},
        {"strategy_name": "breakout", "exit_date": date(2026, 5, 2), "pnl": 50.0},  # win
        {"strategy_name": "breakout", "exit_date": date(2026, 5, 3), "pnl": -25.0},
    ]
    streaks = compute_losing_day_streaks(rows, ["breakout"])
    assert streaks["breakout"] == 1  # only the final day counts


def test_compute_losing_day_streaks_zero_when_last_day_was_win() -> None:
    from datetime import date
    from alpaca_bot.risk.weighting import compute_losing_day_streaks

    rows = [
        {"strategy_name": "breakout", "exit_date": date(2026, 5, 1), "pnl": -100.0},
        {"strategy_name": "breakout", "exit_date": date(2026, 5, 2), "pnl": 50.0},
    ]
    streaks = compute_losing_day_streaks(rows, ["breakout"])
    assert streaks["breakout"] == 0
