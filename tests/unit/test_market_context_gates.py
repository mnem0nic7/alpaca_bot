"""Tests for the VIX regime gate, sector breadth gate, and VWAP entry filter
as implemented in core/engine.py evaluate_cycle()."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from alpaca_bot.config import Settings
from alpaca_bot.core.engine import CycleResult, evaluate_cycle
from alpaca_bot.domain import Bar, EntrySignal
from alpaca_bot.domain.models import MarketContext


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


_NOW = datetime(2026, 5, 9, 14, 30, tzinfo=timezone.utc)


def _daily_bars(symbol: str, count: int = 22) -> list[Bar]:
    from datetime import timedelta
    return [
        Bar(
            symbol=symbol,
            timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(days=i),
            open=100.0,
            high=101.0,
            low=99.0,
            close=100.0 + i * 0.1,
            volume=1_000_000,
        )
        for i in range(count)
    ]


def _intraday_bar(symbol: str) -> Bar:
    return Bar(
        symbol=symbol,
        timestamp=datetime(2026, 5, 9, 14, 15, tzinfo=timezone.utc),
        open=149.0,
        high=156.0,
        low=148.0,
        close=155.0,
        volume=2_000_000,
    )


def _fake_signal(*, symbol, intraday_bars, signal_index, daily_bars, settings):
    bar = intraday_bars[signal_index]
    return EntrySignal(
        symbol=symbol,
        signal_bar=bar,
        entry_level=bar.close - 2.0,
        relative_volume=2.5,
        stop_price=bar.close - 5.0,
        limit_price=bar.close,
        initial_stop_price=bar.close - 5.0,
    )


def _no_signal(*, symbol, intraday_bars, signal_index, daily_bars, settings):
    return None


def _run(settings: Settings, market_context: MarketContext | None = None) -> CycleResult:
    return evaluate_cycle(
        settings=settings,
        now=_NOW,
        equity=100_000.0,
        intraday_bars_by_symbol={"AAPL": [_intraday_bar("AAPL")]},
        daily_bars_by_symbol={"AAPL": _daily_bars("AAPL")},
        open_positions=[],
        working_order_symbols=set(),
        traded_symbols_today=set(),
        entries_disabled=False,
        signal_evaluator=_fake_signal,
        market_context=market_context,
    )


# ── VIX gate ─────────────────────────────────────────────────────────────────

def test_vix_gate_disabled_by_default_does_not_block() -> None:
    ctx = MarketContext(
        as_of=_NOW,
        vix_close=25.0,
        vix_sma=15.0,
        vix_above_sma=True,
    )
    result = _run(_make_settings(), ctx)
    assert result.vix_blocked is False
    assert not any(r.reject_reason == "vix_blocked" for r in result.decision_records)


def test_vix_gate_enabled_blocks_when_above_sma() -> None:
    ctx = MarketContext(as_of=_NOW, vix_close=25.0, vix_sma=15.0, vix_above_sma=True)
    result = _run(_make_settings(ENABLE_VIX_FILTER="true"), ctx)
    assert result.vix_blocked is True
    blocked = [r for r in result.decision_records if r.reject_reason == "vix_blocked"]
    assert len(blocked) == 1
    assert blocked[0].symbol == "AAPL"
    assert blocked[0].vix_close == 25.0
    assert blocked[0].vix_above_sma is True


def test_vix_gate_enabled_passes_when_below_sma() -> None:
    ctx = MarketContext(as_of=_NOW, vix_close=12.0, vix_sma=15.0, vix_above_sma=False)
    result = _run(_make_settings(ENABLE_VIX_FILTER="true"), ctx)
    assert result.vix_blocked is False
    assert not any(r.reject_reason == "vix_blocked" for r in result.decision_records)


def test_vix_gate_fail_open_when_context_none() -> None:
    result = _run(_make_settings(ENABLE_VIX_FILTER="true"), market_context=None)
    assert result.vix_blocked is False


def test_vix_gate_fail_open_when_vix_above_sma_none() -> None:
    ctx = MarketContext(as_of=_NOW, vix_above_sma=None)
    result = _run(_make_settings(ENABLE_VIX_FILTER="true"), ctx)
    assert result.vix_blocked is False


# ── Sector gate ───────────────────────────────────────────────────────────────

def test_sector_gate_disabled_by_default_does_not_block() -> None:
    ctx = MarketContext(as_of=_NOW, sector_passing_pct=0.2)
    result = _run(_make_settings(), ctx)
    assert result.sector_blocked is False


def test_sector_gate_blocks_when_below_threshold() -> None:
    ctx = MarketContext(as_of=_NOW, sector_passing_pct=0.3)
    result = _run(_make_settings(ENABLE_SECTOR_FILTER="true"), ctx)
    assert result.sector_blocked is True
    blocked = [r for r in result.decision_records if r.reject_reason == "sector_blocked"]
    assert len(blocked) == 1
    assert blocked[0].symbol == "AAPL"
    assert blocked[0].sector_passing_pct == 0.3


def test_sector_gate_passes_when_above_threshold() -> None:
    ctx = MarketContext(as_of=_NOW, sector_passing_pct=0.7)
    result = _run(_make_settings(ENABLE_SECTOR_FILTER="true"), ctx)
    assert result.sector_blocked is False


def test_sector_gate_passes_when_equal_to_threshold() -> None:
    ctx = MarketContext(as_of=_NOW, sector_passing_pct=0.5)
    result = _run(_make_settings(ENABLE_SECTOR_FILTER="true"), ctx)
    assert result.sector_blocked is False


def test_sector_gate_fail_open_when_passing_pct_none() -> None:
    ctx = MarketContext(as_of=_NOW, sector_passing_pct=None)
    result = _run(_make_settings(ENABLE_SECTOR_FILTER="true"), ctx)
    assert result.sector_blocked is False


# ── Context stamped on decision records ──────────────────────────────────────

def test_context_fields_stamped_on_accepted_record() -> None:
    ctx = MarketContext(
        as_of=_NOW,
        vix_close=14.0,
        vix_above_sma=False,
        sector_passing_pct=0.8,
    )
    result = _run(_make_settings(), ctx)
    accepted = [r for r in result.decision_records if r.decision == "accepted"]
    assert len(accepted) == 1
    assert accepted[0].vix_close == 14.0
    assert accepted[0].vix_above_sma is False
    assert accepted[0].sector_passing_pct == 0.8


def test_context_fields_none_when_context_not_passed() -> None:
    result = _run(_make_settings(), market_context=None)
    for r in result.decision_records:
        assert r.vix_close is None
        assert r.vix_above_sma is None
        assert r.sector_passing_pct is None


# ── VWAP entry filter ─────────────────────────────────────────────────────────

def test_vwap_filter_disabled_by_default_does_not_reject() -> None:
    result = _run(_make_settings())
    assert not any(r.reject_reason == "below_vwap" for r in result.decision_records)


def test_vwap_filter_rejects_when_signal_bar_below_vwap() -> None:
    """Signal bar close (100.0) is well below VWAP derived from bars with high volume prices."""
    settings = _make_settings(ENABLE_VWAP_ENTRY_FILTER="true")
    # Create bars where VWAP ends up above signal bar close (155.0)
    # VWAP = sum((h+l+c)/3 * v) / sum(v). With all bars at ~200 close, VWAP > 155.
    from datetime import timedelta
    high_price_bars = [
        Bar(
            symbol="AAPL",
            timestamp=datetime(2026, 5, 9, 14, 0, tzinfo=timezone.utc) + timedelta(minutes=i * 15),
            open=200.0,
            high=205.0,
            low=198.0,
            close=200.0,
            volume=1_000_000,
        )
        for i in range(3)
    ] + [
        # Signal bar: close=155 which is below VWAP of ~200
        Bar(
            symbol="AAPL",
            timestamp=datetime(2026, 5, 9, 14, 15, tzinfo=timezone.utc) + timedelta(minutes=45),
            open=149.0,
            high=156.0,
            low=148.0,
            close=155.0,
            volume=100,  # tiny volume so VWAP stays near 200
        ),
    ]
    result = evaluate_cycle(
        settings=settings,
        now=_NOW,
        equity=100_000.0,
        intraday_bars_by_symbol={"AAPL": high_price_bars},
        daily_bars_by_symbol={"AAPL": _daily_bars("AAPL")},
        open_positions=[],
        working_order_symbols=set(),
        traded_symbols_today=set(),
        entries_disabled=False,
        signal_evaluator=_fake_signal,
    )
    below_vwap = [r for r in result.decision_records if r.reject_reason == "below_vwap"]
    assert len(below_vwap) == 1
    assert below_vwap[0].symbol == "AAPL"
    assert below_vwap[0].signal_bar_above_vwap is False
    assert below_vwap[0].vwap_at_signal is not None


def test_vwap_filter_passes_when_signal_bar_above_vwap() -> None:
    """Signal bar close well above VWAP — should not be rejected."""
    settings = _make_settings(ENABLE_VWAP_ENTRY_FILTER="true")
    from datetime import timedelta
    low_price_bars = [
        Bar(
            symbol="AAPL",
            timestamp=datetime(2026, 5, 9, 13, 0, tzinfo=timezone.utc) + timedelta(minutes=i * 15),
            open=50.0,
            high=51.0,
            low=49.0,
            close=50.0,
            volume=100,
        )
        for i in range(3)
    ] + [
        Bar(
            symbol="AAPL",
            timestamp=datetime(2026, 5, 9, 14, 15, tzinfo=timezone.utc),
            open=149.0,
            high=156.0,
            low=148.0,
            close=155.0,
            volume=1_000_000,  # high volume pushes VWAP toward 155
        ),
    ]
    result = evaluate_cycle(
        settings=settings,
        now=_NOW,
        equity=100_000.0,
        intraday_bars_by_symbol={"AAPL": low_price_bars},
        daily_bars_by_symbol={"AAPL": _daily_bars("AAPL")},
        open_positions=[],
        working_order_symbols=set(),
        traded_symbols_today=set(),
        entries_disabled=False,
        signal_evaluator=_fake_signal,
    )
    assert not any(r.reject_reason == "below_vwap" for r in result.decision_records)
    accepted = [r for r in result.decision_records if r.decision == "accepted"]
    assert len(accepted) == 1
    assert accepted[0].signal_bar_above_vwap is True
