from __future__ import annotations

import math
from datetime import datetime, timezone

import pytest

from alpaca_bot.domain.models import Bar
from alpaca_bot.strategy.indicators import calculate_bollinger_bands, calculate_vwap


def _make_bar(
    close: float,
    volume: float = 1.0,
    high: float | None = None,
    low: float | None = None,
) -> Bar:
    return Bar(
        symbol="TEST",
        timestamp=datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc),
        open=close,
        high=high if high is not None else close,
        low=low if low is not None else close,
        close=close,
        volume=volume,
    )


# ---------------------------------------------------------------------------
# calculate_vwap
# ---------------------------------------------------------------------------


def test_vwap_single_bar() -> None:
    bar = _make_bar(close=100.0, volume=1000.0, high=102.0, low=98.0)
    # typical_price = (102 + 98 + 100) / 3 = 100.0
    result = calculate_vwap([bar])
    assert result == pytest.approx(100.0)


def test_vwap_volume_weighted() -> None:
    bar1 = _make_bar(close=100.0, volume=1000.0, high=102.0, low=98.0)   # tp=100.0
    bar2 = _make_bar(close=110.0, volume=4000.0, high=112.0, low=108.0)  # tp=110.0
    # vwap = (100*1000 + 110*4000) / 5000 = 540_000 / 5000 = 108.0
    result = calculate_vwap([bar1, bar2])
    assert result == pytest.approx(108.0)


def test_vwap_zero_total_volume_returns_none() -> None:
    bar = _make_bar(close=100.0, volume=0.0)
    assert calculate_vwap([bar]) is None


def test_vwap_empty_sequence_returns_none() -> None:
    assert calculate_vwap([]) is None


def test_vwap_uses_typical_price() -> None:
    # high=110, low=90, close=100 → tp=100.0
    bar = _make_bar(close=100.0, volume=500.0, high=110.0, low=90.0)
    result = calculate_vwap([bar])
    assert result == pytest.approx(100.0)


# ---------------------------------------------------------------------------
# calculate_bollinger_bands
# ---------------------------------------------------------------------------


def test_bb_insufficient_bars_returns_none() -> None:
    bars = [_make_bar(100.0) for _ in range(4)]
    assert calculate_bollinger_bands(bars, 5, 2.0) is None


def test_bb_exact_period_does_not_return_none() -> None:
    bars = [_make_bar(100.0) for _ in range(5)]
    assert calculate_bollinger_bands(bars, 5, 2.0) is not None


def test_bb_flat_series_returns_zero_width_bands() -> None:
    bars = [_make_bar(100.0) for _ in range(5)]
    result = calculate_bollinger_bands(bars, 5, 2.0)
    assert result is not None
    lower, midline, upper = result
    assert midline == pytest.approx(100.0)
    assert upper == pytest.approx(100.0)
    assert lower == pytest.approx(100.0)


def test_bb_midline_is_mean_of_window() -> None:
    closes = [98.0, 99.0, 100.0, 101.0, 102.0]
    bars = [_make_bar(c) for c in closes]
    result = calculate_bollinger_bands(bars, 5, 2.0)
    assert result is not None
    _, midline, _ = result
    assert midline == pytest.approx(100.0)


def test_bb_bands_are_symmetric_around_midline() -> None:
    closes = [98.0, 99.0, 100.0, 101.0, 102.0]
    bars = [_make_bar(c) for c in closes]
    result = calculate_bollinger_bands(bars, 5, 2.0)
    assert result is not None
    lower, midline, upper = result
    assert upper - midline == pytest.approx(midline - lower)


def test_bb_uses_population_std_dev() -> None:
    # Known values: closes [100, 102, 104, 106, 108] → midline=104, pop variance=8
    closes = [100.0, 102.0, 104.0, 106.0, 108.0]
    bars = [_make_bar(c) for c in closes]
    result = calculate_bollinger_bands(bars, 5, 1.0)
    assert result is not None
    lower, midline, upper = result
    assert midline == pytest.approx(104.0)
    expected_sigma = math.sqrt(8.0)
    assert upper == pytest.approx(104.0 + expected_sigma)
    assert lower == pytest.approx(104.0 - expected_sigma)


def test_bb_uses_last_period_bars_only() -> None:
    # First 5 bars at 50, last 5 at 100 — midline should reflect only the last 5
    bars = [_make_bar(50.0) for _ in range(5)] + [_make_bar(100.0) for _ in range(5)]
    result = calculate_bollinger_bands(bars, 5, 2.0)
    assert result is not None
    lower, midline, upper = result
    assert midline == pytest.approx(100.0)
    assert upper == pytest.approx(100.0)
    assert lower == pytest.approx(100.0)


def test_bb_std_dev_multiplier_scales_band_width() -> None:
    closes = [100.0, 102.0, 104.0, 106.0, 108.0]
    bars = [_make_bar(c) for c in closes]
    result1 = calculate_bollinger_bands(bars, 5, 1.0)
    result2 = calculate_bollinger_bands(bars, 5, 2.0)
    assert result1 is not None and result2 is not None
    _, _, upper1 = result1
    _, midline2, upper2 = result2
    # width doubles when std_dev multiplier doubles
    assert (upper2 - midline2) == pytest.approx(2 * (upper1 - result1[1]))
