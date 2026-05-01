# Plan: Position Viability Checks

**Date:** 2026-05-01
**Spec:** docs/superpowers/specs/2026-05-01-position-viability-checks.md

---

## Overview

Add two opt-in viability checks to `evaluate_cycle()` that can emit EXIT intents for open positions mid-session:

1. **Trend filter reversal exit** — daily SMA filter now fails for a held position
2. **VWAP breakdown exit** — current bar closes below today's session VWAP

Both are off by default. No schema migration needed.

---

## Tasks

### Task 1 — Add settings fields to `src/alpaca_bot/config/__init__.py`

**1a. Add two fields to the `Settings` dataclass** (after `failed_breakdown_recapture_buffer_pct`, line 120):

```python
    enable_trend_filter_exit: bool = False
    enable_vwap_breakdown_exit: bool = False
```

**1b. Add two entries to `from_env()`** (after the `failed_breakdown_recapture_buffer_pct` assignment, before the closing `)`):

```python
            enable_trend_filter_exit=_parse_bool(
                "ENABLE_TREND_FILTER_EXIT", values.get("ENABLE_TREND_FILTER_EXIT", "false")
            ),
            enable_vwap_breakdown_exit=_parse_bool(
                "ENABLE_VWAP_BREAKDOWN_EXIT", values.get("ENABLE_VWAP_BREAKDOWN_EXIT", "false")
            ),
```

No changes to `validate()` — booleans have no range constraints.

---

### Task 2 — Add viability checks to `src/alpaca_bot/core/engine.py`

**2a. Update imports** — add `daily_trend_filter_passes` to the existing breakout import, and add a new import for `calculate_vwap`:

Replace:
```python
from alpaca_bot.strategy.breakout import (
    evaluate_breakout_signal,
    is_past_flatten_time,
    session_day,
)
```

With:
```python
from alpaca_bot.strategy.breakout import (
    daily_trend_filter_passes,
    evaluate_breakout_signal,
    is_past_flatten_time,
    session_day,
)
from alpaca_bot.strategy.indicators import calculate_vwap
```

**2b. Insert viability check block in the per-position loop**

The block is inserted **after** the stale bar guard (`continue` on the `bar_age_seconds` check) and **before** the `if is_extended: continue` guard. In the current code, this is between lines 131 and 133.

Replace this section:
```python
        bar_age_seconds = (now - latest_bar.timestamp.astimezone(timezone.utc)).total_seconds()
        if bar_age_seconds > 2 * settings.entry_timeframe_minutes * 60:
            continue

        if is_extended:
            continue
```

With:
```python
        bar_age_seconds = (now - latest_bar.timestamp.astimezone(timezone.utc)).total_seconds()
        if bar_age_seconds > 2 * settings.entry_timeframe_minutes * 60:
            continue

        if settings.enable_trend_filter_exit:
            daily_bars = daily_bars_by_symbol.get(position.symbol, ())
            if len(daily_bars) >= settings.daily_sma_period + 1:
                if not daily_trend_filter_passes(daily_bars, settings):
                    intents.append(
                        CycleIntent(
                            intent_type=CycleIntentType.EXIT,
                            symbol=position.symbol,
                            timestamp=now,
                            reason="viability_trend_filter_failed",
                            strategy_name=strategy_name,
                        )
                    )
                    continue

        if settings.enable_vwap_breakdown_exit:
            session_date = now.astimezone(settings.market_timezone).date()
            today_bars = [
                b for b in bars
                if b.timestamp.astimezone(settings.market_timezone).date() == session_date
            ]
            if today_bars:
                vwap = calculate_vwap(today_bars)
                if vwap is not None and latest_bar.close < vwap:
                    intents.append(
                        CycleIntent(
                            intent_type=CycleIntentType.EXIT,
                            symbol=position.symbol,
                            timestamp=now,
                            reason="viability_vwap_breakdown",
                            strategy_name=strategy_name,
                        )
                    )
                    continue

        if is_extended:
            continue
```

---

### Task 3 — Create `tests/unit/test_position_viability.py`

```python
from __future__ import annotations

from datetime import datetime, timezone

from alpaca_bot.config import Settings
from alpaca_bot.core.engine import CycleIntentType, evaluate_cycle
from alpaca_bot.domain import Bar, OpenPosition


def _make_settings(**overrides: str) -> Settings:
    values = {
        "TRADING_MODE": "paper",
        "ENABLE_LIVE_TRADING": "false",
        "STRATEGY_VERSION": "v1",
        "DATABASE_URL": "postgresql://localhost/test",
        "MARKET_DATA_FEED": "sip",
        "SYMBOLS": "AAPL",
        "DAILY_SMA_PERIOD": "5",
        "BREAKOUT_LOOKBACK_BARS": "5",
        "RELATIVE_VOLUME_LOOKBACK_BARS": "5",
        "RELATIVE_VOLUME_THRESHOLD": "1.5",
        "ENTRY_TIMEFRAME_MINUTES": "15",
        "RISK_PER_TRADE_PCT": "0.01",
        "MAX_POSITION_PCT": "0.1",
        "MAX_OPEN_POSITIONS": "3",
        "DAILY_LOSS_LIMIT_PCT": "0.05",
        "STOP_LIMIT_BUFFER_PCT": "0.001",
        "BREAKOUT_STOP_BUFFER_PCT": "0.001",
        "ENTRY_STOP_PRICE_BUFFER": "0.01",
        "ENTRY_WINDOW_START": "10:00",
        "ENTRY_WINDOW_END": "15:30",
        "FLATTEN_TIME": "15:45",
        "ATR_PERIOD": "5",
    }
    values.update(overrides)
    return Settings.from_env(values)


def _make_position(symbol: str = "AAPL") -> OpenPosition:
    return OpenPosition(
        symbol=symbol,
        entry_timestamp=datetime(2026, 5, 1, 14, 0, tzinfo=timezone.utc),
        entry_price=100.0,
        quantity=10,
        entry_level=100.0,
        initial_stop_price=98.0,
        stop_price=98.0,
    )


# 2026-05-01 10:15 ET = 14:15 UTC — within entry window, well before flatten time
_NOW = datetime(2026, 5, 1, 14, 15, tzinfo=timezone.utc)


def _fresh_bar(
    close: float,
    *,
    high: float | None = None,
    low: float | None = None,
    volume: float = 10_000.0,
) -> Bar:
    """A today bar fresh relative to _NOW (10:00 ET = 14:00 UTC, 15 min old)."""
    return Bar(
        symbol="AAPL",
        timestamp=datetime(2026, 5, 1, 14, 0, tzinfo=timezone.utc),
        open=99.0,
        high=high if high is not None else close + 1.0,
        low=low if low is not None else close - 1.0,
        close=close,
        volume=volume,
    )


def _falling_daily_bars(n: int = 6) -> list[Bar]:
    """n daily bars with declining closes — trend filter fails (close < SMA)."""
    return [
        Bar(
            symbol="AAPL",
            timestamp=datetime(2026, 4, 1 + i, 21, 0, tzinfo=timezone.utc),
            open=100.0 - i,
            high=100.5 - i,
            low=99.0 - i,
            close=100.0 - i,
            volume=1_000_000,
        )
        for i in range(n)
    ]


def _rising_daily_bars(n: int = 6) -> list[Bar]:
    """n daily bars with rising closes — trend filter passes (close > SMA)."""
    return [
        Bar(
            symbol="AAPL",
            timestamp=datetime(2026, 4, 1 + i, 21, 0, tzinfo=timezone.utc),
            open=90.0 + i,
            high=91.0 + i,
            low=89.0 + i,
            close=90.0 + i,
            volume=1_000_000,
        )
        for i in range(n)
    ]


# ─── Trend filter reversal tests ──────────────────────────────────────────────

def test_trend_filter_exit_fires_when_filter_fails() -> None:
    settings = _make_settings(ENABLE_TREND_FILTER_EXIT="true")
    result = evaluate_cycle(
        settings=settings,
        now=_NOW,
        equity=100_000.0,
        intraday_bars_by_symbol={"AAPL": [_fresh_bar(101.0)]},
        daily_bars_by_symbol={"AAPL": _falling_daily_bars()},
        open_positions=[_make_position()],
        working_order_symbols=set(),
        traded_symbols_today=set(),
        entries_disabled=True,
    )
    exits = [i for i in result.intents if i.intent_type == CycleIntentType.EXIT]
    assert len(exits) == 1
    assert exits[0].symbol == "AAPL"
    assert exits[0].reason == "viability_trend_filter_failed"


def test_trend_filter_exit_does_not_fire_when_disabled() -> None:
    settings = _make_settings()  # ENABLE_TREND_FILTER_EXIT defaults to false
    result = evaluate_cycle(
        settings=settings,
        now=_NOW,
        equity=100_000.0,
        intraday_bars_by_symbol={"AAPL": [_fresh_bar(101.0)]},
        daily_bars_by_symbol={"AAPL": _falling_daily_bars()},
        open_positions=[_make_position()],
        working_order_symbols=set(),
        traded_symbols_today=set(),
        entries_disabled=True,
    )
    exits = [i for i in result.intents if i.intent_type == CycleIntentType.EXIT]
    assert len(exits) == 0


def test_trend_filter_exit_does_not_fire_when_filter_passes() -> None:
    settings = _make_settings(ENABLE_TREND_FILTER_EXIT="true")
    result = evaluate_cycle(
        settings=settings,
        now=_NOW,
        equity=100_000.0,
        intraday_bars_by_symbol={"AAPL": [_fresh_bar(101.0)]},
        daily_bars_by_symbol={"AAPL": _rising_daily_bars()},
        open_positions=[_make_position()],
        working_order_symbols=set(),
        traded_symbols_today=set(),
        entries_disabled=True,
    )
    exits = [i for i in result.intents if i.intent_type == CycleIntentType.EXIT]
    assert len(exits) == 0


def test_trend_filter_exit_does_not_fire_when_insufficient_daily_bars() -> None:
    # daily_sma_period=5 needs len>=6; only 3 bars → guard prevents exit
    settings = _make_settings(ENABLE_TREND_FILTER_EXIT="true")
    result = evaluate_cycle(
        settings=settings,
        now=_NOW,
        equity=100_000.0,
        intraday_bars_by_symbol={"AAPL": [_fresh_bar(101.0)]},
        daily_bars_by_symbol={"AAPL": _falling_daily_bars(n=3)},
        open_positions=[_make_position()],
        working_order_symbols=set(),
        traded_symbols_today=set(),
        entries_disabled=True,
    )
    exits = [i for i in result.intents if i.intent_type == CycleIntentType.EXIT]
    assert len(exits) == 0


def test_trend_filter_exit_does_not_fire_when_no_daily_bars() -> None:
    settings = _make_settings(ENABLE_TREND_FILTER_EXIT="true")
    result = evaluate_cycle(
        settings=settings,
        now=_NOW,
        equity=100_000.0,
        intraday_bars_by_symbol={"AAPL": [_fresh_bar(101.0)]},
        daily_bars_by_symbol={},
        open_positions=[_make_position()],
        working_order_symbols=set(),
        traded_symbols_today=set(),
        entries_disabled=True,
    )
    exits = [i for i in result.intents if i.intent_type == CycleIntentType.EXIT]
    assert len(exits) == 0


def test_trend_filter_exit_does_not_double_emit_past_flatten_time() -> None:
    # At 15:46 ET the EOD flatten block fires first; viability check must not also fire.
    settings = _make_settings(ENABLE_TREND_FILTER_EXIT="true")
    flatten_now = datetime(2026, 5, 1, 19, 46, tzinfo=timezone.utc)  # 15:46 ET
    fresh_bar = Bar(
        symbol="AAPL",
        timestamp=datetime(2026, 5, 1, 19, 30, tzinfo=timezone.utc),  # 15:30 ET, 16 min old
        open=99.0, high=102.0, low=98.0, close=101.0, volume=10_000.0,
    )
    result = evaluate_cycle(
        settings=settings,
        now=flatten_now,
        equity=100_000.0,
        intraday_bars_by_symbol={"AAPL": [fresh_bar]},
        daily_bars_by_symbol={"AAPL": _falling_daily_bars()},
        open_positions=[_make_position()],
        working_order_symbols=set(),
        traded_symbols_today=set(),
        entries_disabled=True,
    )
    exits = [i for i in result.intents if i.intent_type == CycleIntentType.EXIT]
    assert len(exits) == 1
    assert exits[0].reason == "eod_flatten"


# ─── VWAP breakdown exit tests ────────────────────────────────────────────────

def test_vwap_breakdown_exit_fires_when_close_below_vwap() -> None:
    # bar1: TP=(101+99+102)/3≈100.67, vol=50000 → dominates VWAP ~100.63
    # bar2: TP=(101+97+98)/3≈98.67, vol=1000 → close=98 < VWAP ≈100.63 → fires
    settings = _make_settings(ENABLE_VWAP_BREAKDOWN_EXIT="true")
    bar1 = Bar(
        symbol="AAPL",
        timestamp=datetime(2026, 5, 1, 14, 0, tzinfo=timezone.utc),
        open=101.0, high=101.0, low=99.0, close=102.0, volume=50_000.0,
    )
    bar2 = Bar(
        symbol="AAPL",
        timestamp=datetime(2026, 5, 1, 14, 15, tzinfo=timezone.utc),
        open=101.0, high=101.0, low=97.0, close=98.0, volume=1_000.0,
    )
    result = evaluate_cycle(
        settings=settings,
        now=_NOW,
        equity=100_000.0,
        intraday_bars_by_symbol={"AAPL": [bar1, bar2]},
        daily_bars_by_symbol={"AAPL": _rising_daily_bars()},
        open_positions=[_make_position()],
        working_order_symbols=set(),
        traded_symbols_today=set(),
        entries_disabled=True,
    )
    exits = [i for i in result.intents if i.intent_type == CycleIntentType.EXIT]
    assert len(exits) == 1
    assert exits[0].symbol == "AAPL"
    assert exits[0].reason == "viability_vwap_breakdown"


def test_vwap_breakdown_exit_does_not_fire_when_close_above_vwap() -> None:
    # single bar: TP=(102+100+103)/3≈101.67; close=103 > VWAP → no exit
    settings = _make_settings(ENABLE_VWAP_BREAKDOWN_EXIT="true")
    bar = Bar(
        symbol="AAPL",
        timestamp=datetime(2026, 5, 1, 14, 0, tzinfo=timezone.utc),
        open=101.0, high=102.0, low=100.0, close=103.0, volume=10_000.0,
    )
    result = evaluate_cycle(
        settings=settings,
        now=_NOW,
        equity=100_000.0,
        intraday_bars_by_symbol={"AAPL": [bar]},
        daily_bars_by_symbol={"AAPL": _rising_daily_bars()},
        open_positions=[_make_position()],
        working_order_symbols=set(),
        traded_symbols_today=set(),
        entries_disabled=True,
    )
    exits = [i for i in result.intents if i.intent_type == CycleIntentType.EXIT]
    assert len(exits) == 0


def test_vwap_breakdown_exit_does_not_fire_when_disabled() -> None:
    settings = _make_settings()  # ENABLE_VWAP_BREAKDOWN_EXIT defaults to false
    bar1 = Bar(
        symbol="AAPL",
        timestamp=datetime(2026, 5, 1, 14, 0, tzinfo=timezone.utc),
        open=101.0, high=101.0, low=99.0, close=102.0, volume=50_000.0,
    )
    bar2 = Bar(
        symbol="AAPL",
        timestamp=datetime(2026, 5, 1, 14, 15, tzinfo=timezone.utc),
        open=101.0, high=101.0, low=97.0, close=98.0, volume=1_000.0,
    )
    result = evaluate_cycle(
        settings=settings,
        now=_NOW,
        equity=100_000.0,
        intraday_bars_by_symbol={"AAPL": [bar1, bar2]},
        daily_bars_by_symbol={"AAPL": _rising_daily_bars()},
        open_positions=[_make_position()],
        working_order_symbols=set(),
        traded_symbols_today=set(),
        entries_disabled=True,
    )
    exits = [i for i in result.intents if i.intent_type == CycleIntentType.EXIT]
    assert len(exits) == 0


def test_vwap_breakdown_exit_does_not_fire_when_no_today_bars() -> None:
    # Yesterday bar is stale → stale bar guard fires before viability checks
    settings = _make_settings(ENABLE_VWAP_BREAKDOWN_EXIT="true")
    yesterday_bar = Bar(
        symbol="AAPL",
        timestamp=datetime(2026, 4, 30, 14, 0, tzinfo=timezone.utc),
        open=99.0, high=101.0, low=97.0, close=95.0, volume=10_000.0,
    )
    result = evaluate_cycle(
        settings=settings,
        now=_NOW,
        equity=100_000.0,
        intraday_bars_by_symbol={"AAPL": [yesterday_bar]},
        daily_bars_by_symbol={"AAPL": _rising_daily_bars()},
        open_positions=[_make_position()],
        working_order_symbols=set(),
        traded_symbols_today=set(),
        entries_disabled=True,
    )
    exits = [i for i in result.intents if i.intent_type == CycleIntentType.EXIT]
    assert len(exits) == 0


def test_vwap_breakdown_exit_does_not_double_emit_past_flatten_time() -> None:
    settings = _make_settings(ENABLE_VWAP_BREAKDOWN_EXIT="true")
    flatten_now = datetime(2026, 5, 1, 19, 46, tzinfo=timezone.utc)  # 15:46 ET
    bar1 = Bar(
        symbol="AAPL",
        timestamp=datetime(2026, 5, 1, 19, 30, tzinfo=timezone.utc),
        open=101.0, high=101.0, low=99.0, close=102.0, volume=50_000.0,
    )
    bar2 = Bar(
        symbol="AAPL",
        timestamp=datetime(2026, 5, 1, 19, 45, tzinfo=timezone.utc),
        open=101.0, high=101.0, low=97.0, close=98.0, volume=1_000.0,
    )
    result = evaluate_cycle(
        settings=settings,
        now=flatten_now,
        equity=100_000.0,
        intraday_bars_by_symbol={"AAPL": [bar1, bar2]},
        daily_bars_by_symbol={"AAPL": _rising_daily_bars()},
        open_positions=[_make_position()],
        working_order_symbols=set(),
        traded_symbols_today=set(),
        entries_disabled=True,
    )
    exits = [i for i in result.intents if i.intent_type == CycleIntentType.EXIT]
    assert len(exits) == 1
    assert exits[0].reason == "eod_flatten"


# ─── Settings defaults ────────────────────────────────────────────────────────

def test_settings_enable_trend_filter_exit_defaults_false() -> None:
    assert _make_settings().enable_trend_filter_exit is False


def test_settings_enable_vwap_breakdown_exit_defaults_false() -> None:
    assert _make_settings().enable_vwap_breakdown_exit is False


def test_settings_enable_trend_filter_exit_can_be_enabled() -> None:
    assert _make_settings(ENABLE_TREND_FILTER_EXIT="true").enable_trend_filter_exit is True


def test_settings_enable_vwap_breakdown_exit_can_be_enabled() -> None:
    assert _make_settings(ENABLE_VWAP_BREAKDOWN_EXIT="true").enable_vwap_breakdown_exit is True
```

---

### Task 4 — Run tests

```bash
# Targeted run first
pytest tests/unit/test_position_viability.py -v

# Full suite — all 900+ existing tests must still pass
pytest
```

---

## Safety Checklist

- [x] `evaluate_cycle()` remains pure — no I/O introduced
- [x] Both checks default to `False` — zero behavior change for existing deployments
- [x] Data guards prevent exit when bars are missing or insufficient
- [x] Viability checks placed AFTER EOD flatten block — no double-EXIT per symbol possible
- [x] Viability checks placed AFTER stale bar guard — data always fresh when checks run
- [x] `_execute_exit()` duplicate-exit guard provides a second layer of protection
- [x] No new DB tables, columns, or migrations
- [x] `reason` strings propagate automatically to `cycle_intent_executed` audit events
- [x] Paper/live symmetry maintained — checks operate identically in both modes
