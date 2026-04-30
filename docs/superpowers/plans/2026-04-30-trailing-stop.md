# Plan: Trailing Stop / Gain Preservation

Spec: `docs/superpowers/specs/2026-04-30-trailing-stop.md`

---

## Task 1 — Add Settings fields

**File:** `src/alpaca_bot/config/__init__.py`

After `atr_stop_multiplier: float = 1.5` (~line 83), add:

```python
trailing_stop_atr_multiplier: float = 0.0
trailing_stop_profit_trigger_r: float = 1.0
```

In `from_env()`, after `atr_stop_multiplier=float(values.get("ATR_STOP_MULTIPLIER", "1.5")),` add:

```python
trailing_stop_atr_multiplier=float(
    values.get("TRAILING_STOP_ATR_MULTIPLIER", "0.0")
),
trailing_stop_profit_trigger_r=float(
    values.get("TRAILING_STOP_PROFIT_TRIGGER_R", "1.0")
),
```

In `validate()`, after `if self.atr_stop_multiplier > 10.0:` block, add:

```python
if self.trailing_stop_atr_multiplier < 0:
    raise ValueError("TRAILING_STOP_ATR_MULTIPLIER must be >= 0")
if self.trailing_stop_atr_multiplier > 10.0:
    raise ValueError(
        "TRAILING_STOP_ATR_MULTIPLIER must be <= 10.0 (got a suspiciously large value)"
    )
if self.trailing_stop_profit_trigger_r <= 0:
    raise ValueError("TRAILING_STOP_PROFIT_TRIGGER_R must be > 0")
```

**Test command:** `pytest tests/unit/test_settings.py -q`

---

## Task 2 — Engine change

**File:** `src/alpaca_bot/core/engine.py`

Add import after `from alpaca_bot.risk import calculate_position_size`:

```python
from alpaca_bot.risk.atr import calculate_atr
```

Replace the existing stop-update block (currently lines 135–146):

```python
        if latest_bar.high >= position.entry_price + position.risk_per_share:
            new_stop = round(max(position.stop_price, position.entry_price, latest_bar.low), 2)
            if new_stop > position.stop_price:
                intents.append(
                    CycleIntent(
                        intent_type=CycleIntentType.UPDATE_STOP,
                        symbol=position.symbol,
                        timestamp=latest_bar.timestamp,
                        stop_price=new_stop,
                        strategy_name=strategy_name,
                    )
                )
```

with:

```python
        profit_trigger = (
            position.entry_price
            + settings.trailing_stop_profit_trigger_r * position.risk_per_share
        )
        if latest_bar.high >= profit_trigger:
            atr = (
                calculate_atr(
                    daily_bars_by_symbol.get(position.symbol, ()),
                    settings.atr_period,
                )
                if settings.trailing_stop_atr_multiplier > 0
                else None
            )
            if atr is not None:
                trailing_candidate = (
                    latest_bar.high - settings.trailing_stop_atr_multiplier * atr
                )
                new_stop = round(
                    max(position.stop_price, position.entry_price, trailing_candidate), 2
                )
            else:
                new_stop = round(
                    max(position.stop_price, position.entry_price, latest_bar.low), 2
                )
            if new_stop > position.stop_price:
                intents.append(
                    CycleIntent(
                        intent_type=CycleIntentType.UPDATE_STOP,
                        symbol=position.symbol,
                        timestamp=latest_bar.timestamp,
                        stop_price=new_stop,
                        strategy_name=strategy_name,
                    )
                )
```

**Test command:** `pytest tests/unit/test_cycle_engine.py -q`

---

## Task 3 — Tests

### `tests/unit/test_cycle_engine.py` — add these tests

The existing `make_settings()` helper accepts `**overrides: str` and calls `Settings.from_env()`.
Pass `TRAILING_STOP_ATR_MULTIPLIER` and `TRAILING_STOP_PROFIT_TRIGGER_R` as overrides.

The existing `make_daily_bars()` produces 21 bars with ATR = 2.0 (each bar: high-low=2,
price drift 1/day → TR=max(2,1,1)=2 throughout). Use that for ATR computations.

```python
def test_trailing_stop_disabled_uses_bar_low() -> None:
    """TRAILING_STOP_ATR_MULTIPLIER=0.0 → original bar.low behavior."""
    CycleIntentType, evaluate_cycle = load_engine_api()
    # bar.high=112.40 >= entry(111.02) + risk(1.13) = 112.15 → trigger met
    # With multiplier=0, falls back to bar.low=111.70
    latest_bar = Bar(
        symbol="AAPL",
        timestamp=datetime(2026, 4, 24, 19, 15, tzinfo=timezone.utc),
        open=111.80,
        high=112.40,
        low=111.70,
        close=112.10,
        volume=2400,
    )
    position = OpenPosition(
        symbol="AAPL",
        entry_timestamp=datetime(2026, 4, 24, 18, 45, tzinfo=timezone.utc),
        entry_price=111.02,
        quantity=45,
        entry_level=109.90,
        initial_stop_price=109.89,
        stop_price=109.89,
    )
    result = evaluate_cycle(
        settings=make_settings(TRAILING_STOP_ATR_MULTIPLIER="0.0"),
        now=latest_bar.timestamp,
        equity=100_000.0,
        intraday_bars_by_symbol={"AAPL": [latest_bar]},
        daily_bars_by_symbol={"AAPL": make_daily_bars()},
        open_positions=[position],
        working_order_symbols=set(),
        traded_symbols_today=set(),
        entries_disabled=True,
    )
    assert [i.intent_type for i in result.intents] == [CycleIntentType.UPDATE_STOP]
    assert result.intents[0].stop_price == 111.70


def test_trailing_stop_uses_atr_distance_from_bar_high() -> None:
    """ATR=2.0, multiplier=1.5 → trailing_candidate = bar.high - 3.0."""
    CycleIntentType, evaluate_cycle = load_engine_api()
    # entry=110.0, initial_stop=108.5, risk_per_share=1.5
    # trigger_r=1.0 → profit_trigger = 111.5
    # bar.high=116.0 > 111.5 → trailing_candidate = 116.0 - 3.0 = 113.0
    # new_stop = max(108.5, 110.0, 113.0) = 113.0
    latest_bar = Bar(
        symbol="AAPL",
        timestamp=datetime(2026, 4, 24, 19, 15, tzinfo=timezone.utc),
        open=115.0,
        high=116.0,
        low=114.5,
        close=115.8,
        volume=3000,
    )
    position = OpenPosition(
        symbol="AAPL",
        entry_timestamp=datetime(2026, 4, 24, 18, 45, tzinfo=timezone.utc),
        entry_price=110.0,
        quantity=50,
        entry_level=109.0,
        initial_stop_price=108.5,
        stop_price=108.5,
    )
    result = evaluate_cycle(
        settings=make_settings(
            TRAILING_STOP_ATR_MULTIPLIER="1.5",
            TRAILING_STOP_PROFIT_TRIGGER_R="1.0",
        ),
        now=latest_bar.timestamp,
        equity=100_000.0,
        intraday_bars_by_symbol={"AAPL": [latest_bar]},
        daily_bars_by_symbol={"AAPL": make_daily_bars()},
        open_positions=[position],
        working_order_symbols=set(),
        traded_symbols_today=set(),
        entries_disabled=True,
    )
    assert [i.intent_type for i in result.intents] == [CycleIntentType.UPDATE_STOP]
    assert result.intents[0].stop_price == 113.0


def test_trailing_stop_never_regresses() -> None:
    """Trailing candidate below existing stop → no UPDATE_STOP emitted."""
    CycleIntentType, evaluate_cycle = load_engine_api()
    # entry=110.0, risk=1.5, stop already at 114.0 (from prior trailing at high=117.0)
    # bar.high=115.0 → candidate = 115.0 - 3.0 = 112.0
    # new_stop = max(114.0, 110.0, 112.0) = 114.0 — not > 114.0, no intent
    latest_bar = Bar(
        symbol="AAPL",
        timestamp=datetime(2026, 4, 24, 19, 15, tzinfo=timezone.utc),
        open=114.5,
        high=115.0,
        low=113.8,
        close=114.2,
        volume=2500,
    )
    position = OpenPosition(
        symbol="AAPL",
        entry_timestamp=datetime(2026, 4, 24, 18, 45, tzinfo=timezone.utc),
        entry_price=110.0,
        quantity=50,
        entry_level=109.0,
        initial_stop_price=108.5,
        stop_price=114.0,
    )
    result = evaluate_cycle(
        settings=make_settings(TRAILING_STOP_ATR_MULTIPLIER="1.5"),
        now=latest_bar.timestamp,
        equity=100_000.0,
        intraday_bars_by_symbol={"AAPL": [latest_bar]},
        daily_bars_by_symbol={"AAPL": make_daily_bars()},
        open_positions=[position],
        working_order_symbols=set(),
        traded_symbols_today=set(),
        entries_disabled=True,
    )
    assert result.intents == []


def test_trailing_stop_respects_breakeven_floor() -> None:
    """When ATR-candidate < entry_price, stop moves to entry_price (break-even)."""
    CycleIntentType, evaluate_cycle = load_engine_api()
    # entry=110.0, risk=1.5, trigger at 111.5
    # bar.high=111.5 (exactly at trigger) → candidate = 111.5 - 3.0 = 108.5 < entry
    # new_stop = max(108.5, 110.0, 108.5) = 110.0 (break-even floor)
    latest_bar = Bar(
        symbol="AAPL",
        timestamp=datetime(2026, 4, 24, 19, 15, tzinfo=timezone.utc),
        open=110.8,
        high=111.5,
        low=110.5,
        close=111.0,
        volume=1800,
    )
    position = OpenPosition(
        symbol="AAPL",
        entry_timestamp=datetime(2026, 4, 24, 18, 45, tzinfo=timezone.utc),
        entry_price=110.0,
        quantity=50,
        entry_level=109.0,
        initial_stop_price=108.5,
        stop_price=108.5,
    )
    result = evaluate_cycle(
        settings=make_settings(TRAILING_STOP_ATR_MULTIPLIER="1.5"),
        now=latest_bar.timestamp,
        equity=100_000.0,
        intraday_bars_by_symbol={"AAPL": [latest_bar]},
        daily_bars_by_symbol={"AAPL": make_daily_bars()},
        open_positions=[position],
        working_order_symbols=set(),
        traded_symbols_today=set(),
        entries_disabled=True,
    )
    assert [i.intent_type for i in result.intents] == [CycleIntentType.UPDATE_STOP]
    assert result.intents[0].stop_price == 110.0


def test_trailing_stop_atr_unavailable_falls_back_to_bar_low() -> None:
    """When daily bars < ATR period + 1, falls back to bar.low (original behavior)."""
    CycleIntentType, evaluate_cycle = load_engine_api()
    # ATR period=14, need 15 bars; provide only 5 → calculate_atr returns None
    short_daily_bars = make_daily_bars()[:5]
    latest_bar = Bar(
        symbol="AAPL",
        timestamp=datetime(2026, 4, 24, 19, 15, tzinfo=timezone.utc),
        open=115.0,
        high=116.0,
        low=114.0,
        close=115.5,
        volume=2800,
    )
    position = OpenPosition(
        symbol="AAPL",
        entry_timestamp=datetime(2026, 4, 24, 18, 45, tzinfo=timezone.utc),
        entry_price=110.0,
        quantity=50,
        entry_level=109.0,
        initial_stop_price=108.5,
        stop_price=108.5,
    )
    result = evaluate_cycle(
        settings=make_settings(TRAILING_STOP_ATR_MULTIPLIER="1.5"),
        now=latest_bar.timestamp,
        equity=100_000.0,
        intraday_bars_by_symbol={"AAPL": [latest_bar]},
        daily_bars_by_symbol={"AAPL": short_daily_bars},
        open_positions=[position],
        working_order_symbols=set(),
        traded_symbols_today=set(),
        entries_disabled=True,
    )
    # Falls back to bar.low=114.0 > entry=110.0 → stop=114.0
    assert [i.intent_type for i in result.intents] == [CycleIntentType.UPDATE_STOP]
    assert result.intents[0].stop_price == 114.0


def test_trailing_stop_profit_trigger_r_controls_activation() -> None:
    """With profit_trigger_r=2.0, no trailing until price is 2R above entry."""
    CycleIntentType, evaluate_cycle = load_engine_api()
    # entry=110.0, risk=1.5 → trigger at 110.0 + 2.0*1.5 = 113.0
    # bar.high=112.0 < 113.0 → no intent
    latest_bar = Bar(
        symbol="AAPL",
        timestamp=datetime(2026, 4, 24, 19, 15, tzinfo=timezone.utc),
        open=111.5,
        high=112.0,
        low=111.0,
        close=111.8,
        volume=2000,
    )
    position = OpenPosition(
        symbol="AAPL",
        entry_timestamp=datetime(2026, 4, 24, 18, 45, tzinfo=timezone.utc),
        entry_price=110.0,
        quantity=50,
        entry_level=109.0,
        initial_stop_price=108.5,
        stop_price=108.5,
    )
    result = evaluate_cycle(
        settings=make_settings(
            TRAILING_STOP_ATR_MULTIPLIER="1.5",
            TRAILING_STOP_PROFIT_TRIGGER_R="2.0",
        ),
        now=latest_bar.timestamp,
        equity=100_000.0,
        intraday_bars_by_symbol={"AAPL": [latest_bar]},
        daily_bars_by_symbol={"AAPL": make_daily_bars()},
        open_positions=[position],
        working_order_symbols=set(),
        traded_symbols_today=set(),
        entries_disabled=True,
    )
    assert result.intents == []
```

### `tests/unit/test_settings_trailing_stop.py` — new file:

Pattern matches `tests/unit/test_settings_extended_hours.py`: use a `_base()` dict helper,
not a fixture.

```python
from __future__ import annotations

import pytest

from alpaca_bot.config import Settings


def _base() -> dict:
    return {
        "TRADING_MODE": "paper",
        "ENABLE_LIVE_TRADING": "false",
        "STRATEGY_VERSION": "v1",
        "DATABASE_URL": "postgresql://test/db",
        "MARKET_DATA_FEED": "iex",
        "SYMBOLS": "AAPL",
        "DAILY_SMA_PERIOD": "20",
        "BREAKOUT_LOOKBACK_BARS": "20",
        "RELATIVE_VOLUME_LOOKBACK_BARS": "20",
        "RELATIVE_VOLUME_THRESHOLD": "1.5",
        "ENTRY_TIMEFRAME_MINUTES": "15",
        "RISK_PER_TRADE_PCT": "0.0025",
        "MAX_POSITION_PCT": "0.05",
        "MAX_OPEN_POSITIONS": "3",
        "DAILY_LOSS_LIMIT_PCT": "0.01",
        "STOP_LIMIT_BUFFER_PCT": "0.001",
        "BREAKOUT_STOP_BUFFER_PCT": "0.001",
        "ENTRY_STOP_PRICE_BUFFER": "0.01",
        "ENTRY_WINDOW_START": "10:00",
        "ENTRY_WINDOW_END": "15:30",
        "FLATTEN_TIME": "15:45",
    }


def test_trailing_stop_settings_defaults() -> None:
    s = Settings.from_env(_base())
    assert s.trailing_stop_atr_multiplier == 0.0
    assert s.trailing_stop_profit_trigger_r == 1.0


def test_trailing_stop_atr_multiplier_env_parsed() -> None:
    s = Settings.from_env({**_base(), "TRAILING_STOP_ATR_MULTIPLIER": "2.0"})
    assert s.trailing_stop_atr_multiplier == 2.0


def test_trailing_stop_profit_trigger_r_env_parsed() -> None:
    s = Settings.from_env({**_base(), "TRAILING_STOP_PROFIT_TRIGGER_R": "0.5"})
    assert s.trailing_stop_profit_trigger_r == 0.5


def test_trailing_stop_atr_multiplier_negative_raises() -> None:
    with pytest.raises(ValueError, match="TRAILING_STOP_ATR_MULTIPLIER"):
        Settings.from_env({**_base(), "TRAILING_STOP_ATR_MULTIPLIER": "-0.1"})


def test_trailing_stop_atr_multiplier_too_large_raises() -> None:
    with pytest.raises(ValueError, match="TRAILING_STOP_ATR_MULTIPLIER"):
        Settings.from_env({**_base(), "TRAILING_STOP_ATR_MULTIPLIER": "11.0"})


def test_trailing_stop_profit_trigger_r_zero_raises() -> None:
    with pytest.raises(ValueError, match="TRAILING_STOP_PROFIT_TRIGGER_R"):
        Settings.from_env({**_base(), "TRAILING_STOP_PROFIT_TRIGGER_R": "0.0"})


def test_trailing_stop_profit_trigger_r_negative_raises() -> None:
    with pytest.raises(ValueError, match="TRAILING_STOP_PROFIT_TRIGGER_R"):
        Settings.from_env({**_base(), "TRAILING_STOP_PROFIT_TRIGGER_R": "-1.0"})
```

**Test command:** `pytest tests/unit/test_cycle_engine.py tests/unit/test_settings_trailing_stop.py -q`

---

## Task 4 — Full test suite

```bash
pytest -q
```

All 825+ tests must pass.
