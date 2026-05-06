# Profit Trail Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a per-position high-based trailing stop ("profit trail") that ratchets the stop up to `today_high × profit_trail_pct` whenever that value exceeds the current stop, layering on top of the existing ATR trailing stop.

**Architecture:** Pure engine change — a new post-loop pass in `evaluate_cycle()` scans intraday bars for the session high, computes `today_high × profit_trail_pct`, and emits an `UPDATE_STOP` intent only when the result exceeds the best stop already emitted. Two new `Settings` fields gate the feature. No database migration required.

**Tech Stack:** Python 3.13, pytest, existing `evaluate_cycle` / `CycleIntent` / `Settings` infrastructure.

---

## File Map

| File | Action | What changes |
|---|---|---|
| `src/alpaca_bot/config/__init__.py` | Modify | Add `enable_profit_trail` and `profit_trail_pct` fields, env-var parsing, validation |
| `src/alpaca_bot/core/engine.py` | Modify | Add profit trail pass between main loop and cap-up pass |
| `tests/unit/test_settings_profit_trail.py` | Create | 5 Settings unit tests |
| `tests/unit/test_cycle_engine.py` | Modify (append) | 8 engine unit tests |

---

## Task 1: Settings — tests

**Files:**
- Create: `tests/unit/test_settings_profit_trail.py`

- [ ] **Step 1: Create the test file**

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


def test_profit_trail_defaults():
    s = Settings.from_env(_base())
    assert s.enable_profit_trail is False
    assert s.profit_trail_pct == pytest.approx(0.95)


def test_enable_profit_trail_env_parsed():
    env = {**_base(), "ENABLE_PROFIT_TRAIL": "true"}
    s = Settings.from_env(env)
    assert s.enable_profit_trail is True


def test_profit_trail_pct_env_parsed():
    env = {**_base(), "PROFIT_TRAIL_PCT": "0.85"}
    s = Settings.from_env(env)
    assert s.profit_trail_pct == pytest.approx(0.85)


def test_profit_trail_pct_zero_raises():
    env = {**_base(), "PROFIT_TRAIL_PCT": "0.0"}
    with pytest.raises(ValueError, match="PROFIT_TRAIL_PCT"):
        Settings.from_env(env)


def test_profit_trail_pct_one_raises():
    env = {**_base(), "PROFIT_TRAIL_PCT": "1.0"}
    with pytest.raises(ValueError, match="PROFIT_TRAIL_PCT"):
        Settings.from_env(env)
```

- [ ] **Step 2: Run tests to verify they fail**

```
pytest tests/unit/test_settings_profit_trail.py -v
```

Expected: all 5 tests FAIL — `enable_profit_trail` and `profit_trail_pct` attributes do not exist yet.

---

## Task 2: Settings — implementation

**Files:**
- Modify: `src/alpaca_bot/config/__init__.py`

- [ ] **Step 3: Add the two fields to the `Settings` dataclass**

In `src/alpaca_bot/config/__init__.py`, find the line:
```python
    max_stop_pct: float = 0.05
```
Insert the two new fields directly after it:
```python
    max_stop_pct: float = 0.05
    enable_profit_trail: bool = False
    profit_trail_pct: float = 0.95
```

- [ ] **Step 4: Add env-var parsing to `from_env()`**

In `from_env()`, find:
```python
            max_stop_pct=float(values.get("MAX_STOP_PCT", "0.05")),
```
Insert after it (before the closing `)`):
```python
            max_stop_pct=float(values.get("MAX_STOP_PCT", "0.05")),
            enable_profit_trail=_parse_bool(
                "ENABLE_PROFIT_TRAIL", values.get("ENABLE_PROFIT_TRAIL", "false")
            ),
            profit_trail_pct=float(values.get("PROFIT_TRAIL_PCT", "0.95")),
```

- [ ] **Step 5: Add validation to `validate()`**

In `validate()`, find the last validation block (currently the extended-hours checks). Insert before the closing of the method:
```python
        if not 0 < self.profit_trail_pct < 1.0:
            raise ValueError(
                "PROFIT_TRAIL_PCT must be between 0 (exclusive) and 1.0 (exclusive); "
                f"got {self.profit_trail_pct}"
            )
```

- [ ] **Step 6: Run settings tests to verify they pass**

```
pytest tests/unit/test_settings_profit_trail.py -v
```

Expected: all 5 PASS.

- [ ] **Step 7: Run full test suite to verify no regressions**

```
pytest
```

Expected: all existing tests PASS.

- [ ] **Step 8: Commit**

```bash
git add src/alpaca_bot/config/__init__.py tests/unit/test_settings_profit_trail.py
git commit -m "feat: add enable_profit_trail and profit_trail_pct settings"
```

---

## Task 3: Engine — tests

**Files:**
- Modify: `tests/unit/test_cycle_engine.py` (append at end)

All 8 tests use:
- `profit_trail_pct=0.90` (not the default 0.95 — catches hardcoded-constant bugs)
- Timestamp `datetime(2026, 4, 24, 19, 15, tzinfo=timezone.utc)` — 15:15 ET, within the default 10:00–15:30 entry window, before 15:45 flatten time
- ATR disabled via `TRAILING_STOP_PROFIT_TRIGGER_R="1000"` where isolation requires it
- `make_daily_bars()` yields ATR ≈ 2.0 (each bar's true range is exactly 2.0)

- [ ] **Step 9: Append 8 tests to `tests/unit/test_cycle_engine.py`**

```python
# ── profit trail ─────────────────────────────────────────────────────────────


def test_profit_trail_emits_when_trail_exceeds_current_stop() -> None:
    """Trail > current_stop → UPDATE_STOP at trail value."""
    CycleIntentType, evaluate_cycle = load_engine_api()
    bar = Bar(
        symbol="AAPL",
        timestamp=datetime(2026, 4, 24, 19, 15, tzinfo=timezone.utc),
        open=11.80,
        high=12.00,
        low=11.60,
        close=11.90,
        volume=3000,
    )
    position = OpenPosition(
        symbol="AAPL",
        entry_timestamp=datetime(2026, 4, 24, 18, 45, tzinfo=timezone.utc),
        entry_price=10.00,
        quantity=100,
        entry_level=9.90,
        initial_stop_price=8.50,
        stop_price=8.50,
    )
    result = evaluate_cycle(
        settings=make_settings(
            ENABLE_PROFIT_TRAIL="true",
            PROFIT_TRAIL_PCT="0.90",
            TRAILING_STOP_PROFIT_TRIGGER_R="1000",  # disable ATR block
        ),
        now=bar.timestamp,
        equity=100_000.0,
        intraday_bars_by_symbol={"AAPL": [bar]},
        daily_bars_by_symbol={"AAPL": make_daily_bars()},
        open_positions=[position],
        working_order_symbols=set(),
        traded_symbols_today=set(),
        entries_disabled=True,
    )
    # trail = 12.00 * 0.90 = 10.80 > stop 8.50 → emit
    trail_intents = [i for i in result.intents if i.intent_type == CycleIntentType.UPDATE_STOP]
    assert len(trail_intents) == 1
    assert trail_intents[0].stop_price == pytest.approx(10.80)
    assert trail_intents[0].reason == "profit_trail"


def test_profit_trail_no_emit_when_trail_below_current_stop() -> None:
    """Trail < current_stop → no UPDATE_STOP emitted."""
    CycleIntentType, evaluate_cycle = load_engine_api()
    bar = Bar(
        symbol="AAPL",
        timestamp=datetime(2026, 4, 24, 19, 15, tzinfo=timezone.utc),
        open=9.70,
        high=9.80,
        low=9.60,
        close=9.75,
        volume=1500,
    )
    position = OpenPosition(
        symbol="AAPL",
        entry_timestamp=datetime(2026, 4, 24, 18, 45, tzinfo=timezone.utc),
        entry_price=10.00,
        quantity=100,
        entry_level=9.90,
        initial_stop_price=8.50,
        stop_price=9.50,
    )
    result = evaluate_cycle(
        settings=make_settings(
            ENABLE_PROFIT_TRAIL="true",
            PROFIT_TRAIL_PCT="0.90",
            TRAILING_STOP_PROFIT_TRIGGER_R="1000",  # disable ATR block
        ),
        now=bar.timestamp,
        equity=100_000.0,
        intraday_bars_by_symbol={"AAPL": [bar]},
        daily_bars_by_symbol={"AAPL": make_daily_bars()},
        open_positions=[position],
        working_order_symbols=set(),
        traded_symbols_today=set(),
        entries_disabled=True,
    )
    # trail = 9.80 * 0.90 = 8.82 < stop 9.50 → no emit
    update_stops = [i for i in result.intents if i.intent_type == CycleIntentType.UPDATE_STOP]
    assert update_stops == []


def test_profit_trail_defers_to_atr_when_atr_stop_is_higher() -> None:
    """ATR trail (11.80) > profit trail (10.80) → emit ATR stop, not profit trail."""
    CycleIntentType, evaluate_cycle = load_engine_api()
    # ATR≈2.0, multiplier=0.1 → ATR trail = 12.0-0.2=11.8 (well above profit trail 10.80)
    bar = Bar(
        symbol="AAPL",
        timestamp=datetime(2026, 4, 24, 19, 15, tzinfo=timezone.utc),
        open=11.80,
        high=12.00,
        low=11.60,
        close=11.90,
        volume=3000,
    )
    position = OpenPosition(
        symbol="AAPL",
        entry_timestamp=datetime(2026, 4, 24, 18, 45, tzinfo=timezone.utc),
        entry_price=10.00,
        quantity=100,
        entry_level=9.90,
        initial_stop_price=9.00,
        stop_price=9.00,
    )
    result = evaluate_cycle(
        settings=make_settings(
            ENABLE_PROFIT_TRAIL="true",
            PROFIT_TRAIL_PCT="0.90",
            TRAILING_STOP_ATR_MULTIPLIER="0.1",
            TRAILING_STOP_PROFIT_TRIGGER_R="1.0",
        ),
        now=bar.timestamp,
        equity=100_000.0,
        intraday_bars_by_symbol={"AAPL": [bar]},
        daily_bars_by_symbol={"AAPL": make_daily_bars()},
        open_positions=[position],
        working_order_symbols=set(),
        traded_symbols_today=set(),
        entries_disabled=True,
    )
    # ATR trail: 12.0 - 0.1*2.0 = 11.80; profit trail: 12.0*0.90=10.80 < 11.80 → no profit emit
    update_stops = [i for i in result.intents if i.intent_type == CycleIntentType.UPDATE_STOP]
    assert len(update_stops) == 1
    assert update_stops[0].stop_price == pytest.approx(11.80)
    assert update_stops[0].reason != "profit_trail"


def test_profit_trail_emits_above_atr_when_trail_is_higher() -> None:
    """Profit trail (10.80) > ATR trail (10.00) → UPDATE_STOP with reason=profit_trail at 10.80."""
    CycleIntentType, evaluate_cycle = load_engine_api()
    # ATR≈2.0, multiplier=1.5 → ATR candidate=12.0-3.0=9.0 → capped at entry 10.0
    # profit trail = 12.0*0.90=10.80 > 10.0 → profit trail emits
    bar = Bar(
        symbol="AAPL",
        timestamp=datetime(2026, 4, 24, 19, 15, tzinfo=timezone.utc),
        open=11.80,
        high=12.00,
        low=11.60,
        close=11.90,
        volume=3000,
    )
    position = OpenPosition(
        symbol="AAPL",
        entry_timestamp=datetime(2026, 4, 24, 18, 45, tzinfo=timezone.utc),
        entry_price=10.00,
        quantity=100,
        entry_level=9.90,
        initial_stop_price=9.00,
        stop_price=9.00,
    )
    result = evaluate_cycle(
        settings=make_settings(
            ENABLE_PROFIT_TRAIL="true",
            PROFIT_TRAIL_PCT="0.90",
            TRAILING_STOP_ATR_MULTIPLIER="1.5",
            TRAILING_STOP_PROFIT_TRIGGER_R="1.0",
        ),
        now=bar.timestamp,
        equity=100_000.0,
        intraday_bars_by_symbol={"AAPL": [bar]},
        daily_bars_by_symbol={"AAPL": make_daily_bars()},
        open_positions=[position],
        working_order_symbols=set(),
        traded_symbols_today=set(),
        entries_disabled=True,
    )
    profit_trail_intents = [
        i for i in result.intents
        if i.intent_type == CycleIntentType.UPDATE_STOP and i.reason == "profit_trail"
    ]
    assert len(profit_trail_intents) == 1
    assert profit_trail_intents[0].stop_price == pytest.approx(10.80)


def test_profit_trail_monotonic_never_lowers_stop() -> None:
    """Trail candidate (10.35) < existing stop (11.40) → no profit_trail UPDATE_STOP emitted."""
    CycleIntentType, evaluate_cycle = load_engine_api()
    bar = Bar(
        symbol="AAPL",
        timestamp=datetime(2026, 4, 24, 19, 15, tzinfo=timezone.utc),
        open=11.40,
        high=11.50,
        low=11.30,
        close=11.45,
        volume=2000,
    )
    position = OpenPosition(
        symbol="AAPL",
        entry_timestamp=datetime(2026, 4, 24, 18, 45, tzinfo=timezone.utc),
        entry_price=10.00,
        quantity=100,
        entry_level=9.90,
        initial_stop_price=8.50,
        stop_price=11.40,  # already moved up from prior cycles
    )
    result = evaluate_cycle(
        settings=make_settings(
            ENABLE_PROFIT_TRAIL="true",
            PROFIT_TRAIL_PCT="0.90",
            TRAILING_STOP_PROFIT_TRIGGER_R="1000",  # disable ATR block
        ),
        now=bar.timestamp,
        equity=100_000.0,
        intraday_bars_by_symbol={"AAPL": [bar]},
        daily_bars_by_symbol={"AAPL": make_daily_bars()},
        open_positions=[position],
        working_order_symbols=set(),
        traded_symbols_today=set(),
        entries_disabled=True,
    )
    # trail = 11.50 * 0.90 = 10.35 < existing stop 11.40 → must not lower the stop
    for intent in result.intents:
        if intent.intent_type == CycleIntentType.UPDATE_STOP and intent.symbol == "AAPL":
            assert intent.stop_price >= 11.40, (
                f"profit trail emitted a stop ({intent.stop_price}) below existing stop (11.40)"
            )


def test_profit_trail_disabled_by_feature_flag() -> None:
    """enable_profit_trail=False → no profit_trail UPDATE_STOP regardless of price."""
    CycleIntentType, evaluate_cycle = load_engine_api()
    bar = Bar(
        symbol="AAPL",
        timestamp=datetime(2026, 4, 24, 19, 15, tzinfo=timezone.utc),
        open=11.80,
        high=12.00,
        low=11.60,
        close=11.90,
        volume=3000,
    )
    position = OpenPosition(
        symbol="AAPL",
        entry_timestamp=datetime(2026, 4, 24, 18, 45, tzinfo=timezone.utc),
        entry_price=10.00,
        quantity=100,
        entry_level=9.90,
        initial_stop_price=8.50,
        stop_price=8.50,
    )
    result = evaluate_cycle(
        settings=make_settings(
            TRAILING_STOP_PROFIT_TRIGGER_R="1000",  # disable ATR block too
            # ENABLE_PROFIT_TRAIL not set → defaults to false
        ),
        now=bar.timestamp,
        equity=100_000.0,
        intraday_bars_by_symbol={"AAPL": [bar]},
        daily_bars_by_symbol={"AAPL": make_daily_bars()},
        open_positions=[position],
        working_order_symbols=set(),
        traded_symbols_today=set(),
        entries_disabled=True,
    )
    profit_trail_intents = [
        i for i in result.intents
        if i.intent_type == CycleIntentType.UPDATE_STOP and i.reason == "profit_trail"
    ]
    assert profit_trail_intents == []


def test_profit_trail_no_emit_when_trail_below_current_stop_at_boundary() -> None:
    """today_high == entry ($10.00) → trail=$9.00 < current_stop=$9.50 → no emit."""
    CycleIntentType, evaluate_cycle = load_engine_api()
    bar = Bar(
        symbol="AAPL",
        timestamp=datetime(2026, 4, 24, 19, 15, tzinfo=timezone.utc),
        open=9.90,
        high=10.00,
        low=9.80,
        close=9.95,
        volume=1200,
    )
    position = OpenPosition(
        symbol="AAPL",
        entry_timestamp=datetime(2026, 4, 24, 18, 45, tzinfo=timezone.utc),
        entry_price=10.00,
        quantity=100,
        entry_level=9.90,
        initial_stop_price=8.50,
        stop_price=9.50,
    )
    result = evaluate_cycle(
        settings=make_settings(
            ENABLE_PROFIT_TRAIL="true",
            PROFIT_TRAIL_PCT="0.90",
            TRAILING_STOP_PROFIT_TRIGGER_R="1000",  # disable ATR block
        ),
        now=bar.timestamp,
        equity=100_000.0,
        intraday_bars_by_symbol={"AAPL": [bar]},
        daily_bars_by_symbol={"AAPL": make_daily_bars()},
        open_positions=[position],
        working_order_symbols=set(),
        traded_symbols_today=set(),
        entries_disabled=True,
    )
    # trail = 10.00 * 0.90 = 9.00 < current_stop 9.50 → no emit (mechanism: trail < stop)
    update_stops = [i for i in result.intents if i.intent_type == CycleIntentType.UPDATE_STOP]
    assert update_stops == []


def test_profit_trail_emits_when_trail_exceeds_stop_even_below_entry() -> None:
    """Gray zone: today_high=$10.50, trail=$9.45 > stop=$8.50 → emit even though trail < entry."""
    CycleIntentType, evaluate_cycle = load_engine_api()
    bar = Bar(
        symbol="AAPL",
        timestamp=datetime(2026, 4, 24, 19, 15, tzinfo=timezone.utc),
        open=10.30,
        high=10.50,
        low=10.10,
        close=10.40,
        volume=2000,
    )
    position = OpenPosition(
        symbol="AAPL",
        entry_timestamp=datetime(2026, 4, 24, 18, 45, tzinfo=timezone.utc),
        entry_price=10.00,
        quantity=100,
        entry_level=9.90,
        initial_stop_price=8.50,
        stop_price=8.50,
    )
    result = evaluate_cycle(
        settings=make_settings(
            ENABLE_PROFIT_TRAIL="true",
            PROFIT_TRAIL_PCT="0.90",
            TRAILING_STOP_PROFIT_TRIGGER_R="1000",  # disable ATR block
        ),
        now=bar.timestamp,
        equity=100_000.0,
        intraday_bars_by_symbol={"AAPL": [bar]},
        daily_bars_by_symbol={"AAPL": make_daily_bars()},
        open_positions=[position],
        working_order_symbols=set(),
        traded_symbols_today=set(),
        entries_disabled=True,
    )
    # trail = 10.50 * 0.90 = 9.45 > stop 8.50 → emit (activation rule: trail > current_stop)
    trail_intents = [
        i for i in result.intents
        if i.intent_type == CycleIntentType.UPDATE_STOP and i.reason == "profit_trail"
    ]
    assert len(trail_intents) == 1
    assert trail_intents[0].stop_price == pytest.approx(9.45)
```

- [ ] **Step 10: Run new tests to verify they fail**

```
pytest tests/unit/test_cycle_engine.py -k "profit_trail" -v
```

Expected: all 8 FAIL — `profit_trail` reason never emitted, `enable_profit_trail` attribute not wired to any engine logic.

---

## Task 4: Engine — implementation

**Files:**
- Modify: `src/alpaca_bot/core/engine.py`

- [ ] **Step 11: Add the profit trail pass**

In `src/alpaca_bot/core/engine.py`, find the blank line between the end of the main `for position in open_positions:` loop and the cap-up pass comment block. The ATR block ends here:

```python
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

    # Cap-up pass: raise stop to MAX_STOP_PCT cap for any existing position whose stop
```

Insert the profit trail pass in the blank line between those two blocks (at 4-space indent, same level as the cap-up pass):

```python
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

    if settings.enable_profit_trail and not is_extended:
        _profit_trail_exited = {
            i.symbol for i in intents if i.intent_type == CycleIntentType.EXIT
        }
        for position in open_positions:
            if position.symbol in _profit_trail_exited:
                continue
            bars = intraday_bars_by_symbol.get(position.symbol, ())
            if not bars:
                continue
            session_date = now.astimezone(settings.market_timezone).date()
            today_bars = [
                b for b in bars
                if b.timestamp.astimezone(settings.market_timezone).date() == session_date
            ]
            if not today_bars:
                continue
            today_high = max(b.high for b in today_bars)
            trail_candidate = round(today_high * settings.profit_trail_pct, 2)
            prior_stop = next(
                (
                    i.stop_price
                    for i in reversed(intents)
                    if i.intent_type == CycleIntentType.UPDATE_STOP
                    and i.symbol == position.symbol
                    and i.stop_price is not None
                ),
                position.stop_price,
            )
            if trail_candidate > prior_stop:
                intents.append(
                    CycleIntent(
                        intent_type=CycleIntentType.UPDATE_STOP,
                        symbol=position.symbol,
                        timestamp=now,
                        stop_price=trail_candidate,
                        strategy_name=strategy_name,
                        reason="profit_trail",
                    )
                )

    # Cap-up pass: raise stop to MAX_STOP_PCT cap for any existing position whose stop
```

- [ ] **Step 12: Run the 8 new engine tests to verify they pass**

```
pytest tests/unit/test_cycle_engine.py -k "profit_trail" -v
```

Expected: all 8 PASS.

- [ ] **Step 13: Run full test suite to verify no regressions**

```
pytest
```

Expected: all tests PASS.

- [ ] **Step 14: Commit**

```bash
git add src/alpaca_bot/core/engine.py tests/unit/test_cycle_engine.py
git commit -m "feat: add per-position profit trailing stop (profit_trail pass)"
```

---

## Self-Review

**Spec coverage:**
- Section 1 (Settings): covered by Tasks 1–2 — two fields, env parsing, validation ✓
- Section 2 (Engine logic): covered by Tasks 3–4 — profit trail pass with correct activation rule (Option 3) ✓
- Section 3 (Testing): all 8 tests in Task 3 ✓
- `is_extended` guard: `not is_extended` wraps the entire profit trail pass ✓
- `today_bars` pattern: same session-date filter as VWAP breakdown exit ✓
- `prior_stop` reads existing UPDATE_STOP intents: `reversed(intents)` scan picks up ATR-emitted stop ✓
- Non-default pct (0.90): all tests use `PROFIT_TRAIL_PCT="0.90"` ✓

**Placeholder scan:** No TBDs. All code blocks are complete and runnable.

**Type consistency:**
- `trail_candidate: float` — consistent with `stop_price: float | None` on `CycleIntent` ✓
- `prior_stop: float` — falls back to `position.stop_price` (float) if no prior UPDATE_STOP ✓
- `reason="profit_trail"` — `CycleIntent.reason: str | None`, `"profit_trail"` is a str ✓
- `settings.enable_profit_trail: bool` and `settings.profit_trail_pct: float` — match field declarations ✓
- `_parse_bool` used for `ENABLE_PROFIT_TRAIL` — matches pattern of all other bool settings ✓
