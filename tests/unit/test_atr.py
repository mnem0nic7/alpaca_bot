from __future__ import annotations

from datetime import datetime, timezone

import pytest

from alpaca_bot.domain.models import Bar
from alpaca_bot.risk.atr import calculate_atr, atr_stop_buffer


def _bar(close: float, high: float | None = None, low: float | None = None) -> Bar:
    h = high if high is not None else close + 1.0
    l = low if low is not None else close - 1.0
    return Bar(
        symbol="TEST",
        timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
        open=close - 0.5,
        high=h,
        low=l,
        close=close,
        volume=1_000.0,
    )


def test_calculate_atr_returns_none_with_insufficient_bars():
    bars = [_bar(100.0 + i) for i in range(3)]  # need period+1=5 for period=4
    assert calculate_atr(bars, period=4) is None


def test_calculate_atr_returns_none_when_exactly_period_bars():
    # period=4 requires 5 bars; exactly 4 bars is one short
    bars = [_bar(100.0 + i) for i in range(4)]
    assert calculate_atr(bars, period=4) is None


def test_calculate_atr_basic():
    # period=2, constant TR=4 → ATR should be 4.0
    # bars[0]: close=100 (baseline)
    # bars[1]: high=104, low=100, prev_close=100 → TR = max(4, 4, 0) = 4
    # bars[2]: high=106, low=102, prev_close=102 → TR = max(4, 4, 0) = 4
    # First ATR = (4+4)/2 = 4.0
    bars = [
        _bar(100.0, high=101.0, low=99.0),
        _bar(102.0, high=104.0, low=100.0),
        _bar(104.0, high=106.0, low=102.0),
    ]
    result = calculate_atr(bars, period=2)
    assert result == pytest.approx(4.0)


def test_calculate_atr_uses_true_range_not_high_minus_low():
    # bars[1] gaps up: high-low=2, but |high-prev_close|=10 → TR=10
    bars = [
        _bar(100.0, high=101.0, low=99.0),
        _bar(109.0, high=110.0, low=108.0),  # gap up: prev_close=100, high=110 → TR=10
        _bar(110.0, high=111.0, low=109.0),  # TR = max(2, 1, 1) = 2
    ]
    result = calculate_atr(bars, period=2)
    # First ATR = (10+2)/2 = 6.0; naive high-low gives (2+2)/2=2.0
    assert result == pytest.approx(6.0)


def test_calculate_atr_wilders_smoothing():
    # period=2
    # bars[1]: TR=4, bars[2]: TR=4 → seed ATR=4.0
    # bars[3]: high=110, low=106, prev_close=104 → TR=max(4,6,2)=6
    # ATR = (4.0*(2-1) + 6) / 2 = 5.0
    bars = [
        _bar(100.0, high=101.0, low=99.0),
        _bar(102.0, high=104.0, low=100.0),
        _bar(104.0, high=106.0, low=102.0),
        _bar(108.0, high=110.0, low=106.0),
    ]
    result = calculate_atr(bars, period=2)
    assert result == pytest.approx(5.0)


def test_calculate_atr_returns_float_not_none_when_exactly_period_plus_one_bars():
    bars = [_bar(100.0 + i, high=100.0 + i + 1.0, low=100.0 + i - 1.0) for i in range(4)]
    result = calculate_atr(bars, period=3)
    assert result is not None
    assert isinstance(result, float)


# ── atr_stop_buffer tests ─────────────────────────────────────────────────────

def _uniform_bars(n: int, tr: float = 2.0) -> list[Bar]:
    """n bars each with a constant true range of `tr` (close=100, spread around it)."""
    half = tr / 2
    return [_bar(100.0, high=100.0 + half, low=100.0 - half) for _ in range(n)]


def test_atr_stop_buffer_uses_atr_when_enough_bars():
    # period=2, 3 bars → ATR computable; constant TR=2 so ATR=2.0
    bars = _uniform_bars(n=3, tr=2.0)
    result = atr_stop_buffer(bars, atr_period=2, atr_stop_multiplier=1.5, fallback_anchor=100.0, fallback_pct=0.001)
    assert result == pytest.approx(3.0)  # 1.5 * 2.0


def test_atr_stop_buffer_returns_fallback_when_insufficient_bars():
    # period=10, only 3 bars → ATR returns None → fallback fires
    bars = _uniform_bars(n=3, tr=2.0)
    result = atr_stop_buffer(bars, atr_period=10, atr_stop_multiplier=1.5, fallback_anchor=100.0, fallback_pct=0.001)
    assert result == pytest.approx(0.1)  # max(0.01, 100.0 * 0.001)


def test_atr_stop_buffer_floors_at_min_buffer_on_atr_path():
    # period=2, TR so tiny that atr * multiplier < 0.01 → floor fires
    tiny_tr = 0.001
    bars = _uniform_bars(n=3, tr=tiny_tr)
    result = atr_stop_buffer(bars, atr_period=2, atr_stop_multiplier=1.5, fallback_anchor=100.0, fallback_pct=0.0)
    assert result == pytest.approx(0.01)  # clamped to _MIN_BUFFER


def test_atr_stop_buffer_floors_at_min_buffer_on_fallback_path():
    # fallback_anchor * fallback_pct < 0.01 → floor fires
    bars = _uniform_bars(n=2, tr=2.0)  # insufficient → fallback
    result = atr_stop_buffer(bars, atr_period=10, atr_stop_multiplier=1.5, fallback_anchor=0.001, fallback_pct=0.001)
    assert result == pytest.approx(0.01)
