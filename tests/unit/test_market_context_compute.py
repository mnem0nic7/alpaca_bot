from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest

from alpaca_bot.config import Settings
from alpaca_bot.domain.models import Bar, MarketContext
from alpaca_bot.strategy.market_context import compute_market_context


def _make_settings(**overrides: str) -> Settings:
    base = {
        "TRADING_MODE": "paper",
        "ENABLE_LIVE_TRADING": "false",
        "STRATEGY_VERSION": "v1",
        "DATABASE_URL": "postgresql://x:y@localhost/z",
        "SYMBOLS": "AAPL",
    }
    base.update(overrides)
    return Settings.from_env(base)


_AS_OF = datetime(2026, 5, 9, 14, 30, tzinfo=timezone.utc)


def _bars(symbol: str, closes: list[float]) -> list[Bar]:
    return [
        Bar(
            symbol=symbol,
            timestamp=datetime(2026, 1, 1 + i, 20, tzinfo=timezone.utc),
            open=c - 0.5,
            high=c + 0.5,
            low=c - 1.0,
            close=c,
            volume=1_000_000,
        )
        for i, c in enumerate(closes)
    ]


# ── VIX gate ──────────────────────────────────────────────────────────────────

def test_vix_above_sma_sets_above_true() -> None:
    s = _make_settings(VIX_LOOKBACK_BARS="5")
    # Closes trending up; last bar well above mean of the window
    bars = _bars("VIXY", [10.0, 10.0, 10.0, 10.0, 20.0])
    ctx = compute_market_context(
        as_of=_AS_OF, vix_bars=bars, sector_bars_by_etf={}, settings=s
    )
    assert ctx.vix_close == 20.0
    assert ctx.vix_above_sma is True


def test_vix_below_sma_sets_above_false() -> None:
    s = _make_settings(VIX_LOOKBACK_BARS="5")
    bars = _bars("VIXY", [20.0, 20.0, 20.0, 20.0, 10.0])
    ctx = compute_market_context(
        as_of=_AS_OF, vix_bars=bars, sector_bars_by_etf={}, settings=s
    )
    assert ctx.vix_above_sma is False


def test_vix_insufficient_history_returns_none() -> None:
    s = _make_settings(VIX_LOOKBACK_BARS="20")
    bars = _bars("VIXY", [15.0] * 5)  # only 5, need 20
    ctx = compute_market_context(
        as_of=_AS_OF, vix_bars=bars, sector_bars_by_etf={}, settings=s
    )
    assert ctx.vix_close is None
    assert ctx.vix_sma is None
    assert ctx.vix_above_sma is None


def test_vix_empty_bars_returns_none() -> None:
    s = _make_settings()
    ctx = compute_market_context(
        as_of=_AS_OF, vix_bars=[], sector_bars_by_etf={}, settings=s
    )
    assert ctx.vix_above_sma is None


# ── Sector gate ───────────────────────────────────────────────────────────────

def test_sector_all_above_sma() -> None:
    s = _make_settings(SECTOR_ETF_SMA_PERIOD="3")
    sector = {
        "XLK": _bars("XLK", [10.0, 10.0, 15.0]),
        "XLF": _bars("XLF", [10.0, 10.0, 15.0]),
    }
    ctx = compute_market_context(
        as_of=_AS_OF, vix_bars=[], sector_bars_by_etf=sector, settings=s
    )
    assert ctx.sector_passing_pct == 1.0
    assert ctx.sector_etf_states == {"XLK": True, "XLF": True}


def test_sector_half_above_sma() -> None:
    s = _make_settings(SECTOR_ETF_SMA_PERIOD="3")
    sector = {
        "XLK": _bars("XLK", [10.0, 10.0, 15.0]),  # above
        "XLF": _bars("XLF", [15.0, 15.0, 5.0]),   # below
    }
    ctx = compute_market_context(
        as_of=_AS_OF, vix_bars=[], sector_bars_by_etf=sector, settings=s
    )
    assert ctx.sector_passing_pct == 0.5


def test_sector_etf_with_insufficient_history_excluded() -> None:
    s = _make_settings(SECTOR_ETF_SMA_PERIOD="5")
    sector = {
        "XLK": _bars("XLK", [10.0, 10.0, 10.0, 10.0, 15.0]),  # 5 bars → included
        "XLF": _bars("XLF", [10.0, 10.0]),                      # 2 bars → excluded
    }
    ctx = compute_market_context(
        as_of=_AS_OF, vix_bars=[], sector_bars_by_etf=sector, settings=s
    )
    assert "XLF" not in ctx.sector_etf_states
    assert "XLK" in ctx.sector_etf_states
    assert ctx.sector_passing_pct == 1.0


def test_sector_no_etfs_with_history_returns_none() -> None:
    s = _make_settings(SECTOR_ETF_SMA_PERIOD="20")
    sector = {"XLK": _bars("XLK", [10.0] * 3)}
    ctx = compute_market_context(
        as_of=_AS_OF, vix_bars=[], sector_bars_by_etf=sector, settings=s
    )
    assert ctx.sector_passing_pct is None
    assert ctx.sector_etf_states == {}


def test_sector_empty_dict_returns_none() -> None:
    s = _make_settings()
    ctx = compute_market_context(
        as_of=_AS_OF, vix_bars=[], sector_bars_by_etf={}, settings=s
    )
    assert ctx.sector_passing_pct is None


# ── MarketContext result shape ────────────────────────────────────────────────

def test_compute_returns_market_context_instance() -> None:
    s = _make_settings()
    ctx = compute_market_context(
        as_of=_AS_OF, vix_bars=[], sector_bars_by_etf={}, settings=s
    )
    assert isinstance(ctx, MarketContext)
    assert ctx.as_of is _AS_OF
