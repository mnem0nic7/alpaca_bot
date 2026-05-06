# Afterhours Trading Correctness Fixes — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix four concrete bugs that prevent `EXTENDED_HOURS_ENABLED=true` from working: stale-bar rejection, wrong signal bar selection, cap-up UPDATE_STOP spam, and over-tight spread filter.

**Architecture:** Four targeted changes to `core/engine.py` plus one new setting in `config/__init__.py`. Each engine change is gated by the existing `is_extended` flag (computed at `engine.py:110`). A fifth engine change adjusts `signal_index` during extended hours to use the last bar within the regular entry window (not `bars[-1]` which is past `ENTRY_WINDOW_END` and blocked by `is_entry_session_time` inside every strategy evaluator).

**Tech Stack:** Python 3.12, pytest, Alpaca-py

---

## File Map

| File | Change |
|---|---|
| `src/alpaca_bot/config/__init__.py` | Add `extended_hours_max_spread_pct: float = 0.01` field, `from_env()` parse, `validate()` assertion |
| `src/alpaca_bot/core/engine.py` | Four targeted changes: bar age guard (line 337), signal_index walk-back (line 358), cap-up guard (line 278), spread threshold (line 354); plus import of `is_entry_window` |
| `tests/unit/test_settings_extended_hours.py` | Two new tests for the new setting |
| `tests/unit/test_engine_extended_hours.py` | Four new tests, one per engine behavior |

---

## Task 1: Settings — `extended_hours_max_spread_pct`

**Files:**
- Modify: `src/alpaca_bot/config/__init__.py`
- Modify: `tests/unit/test_settings_extended_hours.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_settings_extended_hours.py` (after the last test in the file):

```python
def test_extended_hours_max_spread_pct_defaults_to_1_pct():
    s = Settings.from_env(_base())
    assert s.extended_hours_max_spread_pct == pytest.approx(0.01)


def test_extended_hours_max_spread_pct_must_be_at_least_max_spread_pct():
    with pytest.raises(ValueError, match="EXTENDED_HOURS_MAX_SPREAD_PCT"):
        Settings.from_env({
            **_base(),
            "MAX_SPREAD_PCT": "0.01",
            "EXTENDED_HOURS_MAX_SPREAD_PCT": "0.005",  # stricter than regular — invalid
        })
```

- [ ] **Step 2: Run to verify FAIL**

```bash
pytest tests/unit/test_settings_extended_hours.py::test_extended_hours_max_spread_pct_defaults_to_1_pct -v
```

Expected: FAIL with `AttributeError: 'Settings' object has no attribute 'extended_hours_max_spread_pct'`

- [ ] **Step 3: Add field to Settings dataclass**

In `src/alpaca_bot/config/__init__.py`, after line 108 (`extended_hours_limit_offset_pct: float = 0.001`):

```python
    extended_hours_limit_offset_pct: float = 0.001
    extended_hours_max_spread_pct: float = 0.01
```

- [ ] **Step 4: Add parse in `from_env()`**

In the `cls(...)` call in `from_env()`, after the `extended_hours_limit_offset_pct=float(...)` block (lines 247–249):

```python
            extended_hours_limit_offset_pct=float(
                values.get("EXTENDED_HOURS_LIMIT_OFFSET_PCT", "0.001")
            ),
            extended_hours_max_spread_pct=float(
                values.get("EXTENDED_HOURS_MAX_SPREAD_PCT", "0.01")
            ),
```

- [ ] **Step 5: Add validation in `validate()`**

In `validate()`, after line 410 (`if self.extended_hours_limit_offset_pct <= 0:`):

```python
        if self.extended_hours_limit_offset_pct <= 0:
            raise ValueError("EXTENDED_HOURS_LIMIT_OFFSET_PCT must be positive")
        if self.extended_hours_max_spread_pct < self.max_spread_pct:
            raise ValueError(
                f"EXTENDED_HOURS_MAX_SPREAD_PCT ({self.extended_hours_max_spread_pct}) "
                f"must be >= MAX_SPREAD_PCT ({self.max_spread_pct})"
            )
```

- [ ] **Step 6: Run tests to verify PASS**

```bash
pytest tests/unit/test_settings_extended_hours.py -v
```

Expected: All pass

- [ ] **Step 7: Commit**

```bash
git add src/alpaca_bot/config/__init__.py tests/unit/test_settings_extended_hours.py
git commit -m "feat: add EXTENDED_HOURS_MAX_SPREAD_PCT setting (default 1%)"
```

---

## Task 2: Engine — skip bar age check during extended hours

**Files:**
- Modify: `src/alpaca_bot/core/engine.py` (lines 337–339)
- Modify: `tests/unit/test_engine_extended_hours.py`

- [ ] **Step 1: Add `EntrySignal` to imports in the test file**

In `tests/unit/test_engine_extended_hours.py`, change line 7:

```python
from alpaca_bot.domain.models import Bar, EntrySignal, OpenPosition
```

- [ ] **Step 2: Write the failing test**

Append to `tests/unit/test_engine_extended_hours.py`:

```python
def test_afterhours_entry_not_blocked_by_stale_bars():
    """Entries must be possible during afterhours even with 2.5-hour-old bars."""
    settings = _settings()
    # 6pm ET = 22:00 UTC; bar from 3:30pm ET = 19:30 UTC → 2.5h old → fails 30-min check
    # Bar is at ENTRY_WINDOW_END (15:30 ET) so signal_index walk-back (Task 5) finds it.
    now = datetime(2026, 4, 28, 22, 0, tzinfo=timezone.utc)
    stale_bar = _bar("AAPL", close=105.0, ts=datetime(2026, 4, 28, 19, 30, tzinfo=timezone.utc))

    result = evaluate_cycle(
        settings=settings,
        now=now,
        equity=100_000.0,
        intraday_bars_by_symbol={"AAPL": [stale_bar]},
        daily_bars_by_symbol={"AAPL": [stale_bar]},
        open_positions=[],
        working_order_symbols=set(),
        traded_symbols_today=set(),
        entries_disabled=False,
        session_type=SessionType.AFTER_HOURS,
        signal_evaluator=lambda **kwargs: EntrySignal(
            symbol="AAPL",
            signal_bar=kwargs["intraday_bars"][-1],
            entry_level=105.1,
            relative_volume=2.0,
            stop_price=103.0,
            limit_price=105.2,
            initial_stop_price=103.0,
        ),
    )
    entries = [i for i in result.intents if i.intent_type is CycleIntentType.ENTRY]
    assert entries, (
        "AFTER_HOURS entries must not be blocked by the 30-minute bar-age check; "
        "regular session bars are the correct and only available signal basis"
    )
```

- [ ] **Step 3: Run to verify FAIL**

```bash
pytest tests/unit/test_engine_extended_hours.py::test_afterhours_entry_not_blocked_by_stale_bars -v
```

Expected: FAIL — entries list is empty because bar_age is ~2.5h > 30-min limit → `continue`

- [ ] **Step 4: Apply engine change**

In `src/alpaca_bot/core/engine.py`, replace lines 337–339:

Before:
```python
                bar_age_seconds = (now - latest_bar.timestamp.astimezone(timezone.utc)).total_seconds()
                if bar_age_seconds > 2 * settings.entry_timeframe_minutes * 60:
                    continue
```

After:
```python
                if not is_extended:
                    bar_age_seconds = (now - latest_bar.timestamp.astimezone(timezone.utc)).total_seconds()
                    if bar_age_seconds > 2 * settings.entry_timeframe_minutes * 60:
                        continue
```

- [ ] **Step 5: Run tests to verify PASS**

```bash
pytest tests/unit/test_engine_extended_hours.py -v
```

Expected: All pass

- [ ] **Step 6: Commit**

```bash
git add src/alpaca_bot/core/engine.py tests/unit/test_engine_extended_hours.py
git commit -m "fix: skip bar age check during extended hours"
```

---

## Task 3: Engine — gate cap-up pass behind `not is_extended`

**Files:**
- Modify: `src/alpaca_bot/core/engine.py` (lines 278–295)
- Modify: `tests/unit/test_engine_extended_hours.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_engine_extended_hours.py`:

```python
def test_cap_up_stop_not_emitted_in_after_hours():
    """Cap-up UPDATE_STOP must not be emitted during extended hours."""
    # entry=100, max_stop_pct=5% → cap_stop=95.0; position stop=88.0 is below cap.
    # In regular session this would emit UPDATE_STOP; in AFTER_HOURS it must not.
    settings = _settings(MAX_STOP_PCT="0.05")
    now = datetime(2026, 4, 28, 21, 0, tzinfo=timezone.utc)  # 5pm ET
    position = OpenPosition(
        symbol="AAPL",
        quantity=10,
        entry_price=100.0,
        stop_price=88.0,
        initial_stop_price=88.0,
        entry_level=88.0,
        entry_timestamp=datetime(2026, 4, 28, 14, 0, tzinfo=timezone.utc),
    )
    bar = _bar("AAPL", close=100.0, ts=now)

    result = evaluate_cycle(
        settings=settings,
        now=now,
        equity=100_000.0,
        intraday_bars_by_symbol={"AAPL": [bar]},
        daily_bars_by_symbol={},
        open_positions=[position],
        working_order_symbols=set(),
        traded_symbols_today=set(),
        entries_disabled=True,
        session_type=SessionType.AFTER_HOURS,
    )
    update_stops = [i for i in result.intents if i.intent_type is CycleIntentType.UPDATE_STOP]
    assert update_stops == [], "cap-up UPDATE_STOP must be suppressed during extended hours"
```

- [ ] **Step 2: Run to verify FAIL**

```bash
pytest tests/unit/test_engine_extended_hours.py::test_cap_up_stop_not_emitted_in_after_hours -v
```

Expected: FAIL — cap-up emits UPDATE_STOP even in AFTER_HOURS (no guard exists yet)

- [ ] **Step 3: Apply engine change**

In `src/alpaca_bot/core/engine.py`, wrap the cap-up loop (starting at line 278) in `if not is_extended:`.

Before:
```python
    for position in open_positions:
        if position.symbol in emitted_exit_syms:
            continue
        if position.stop_price <= 0 or position.entry_price <= 0:
            continue
        cap_stop = round(position.entry_price * (1 - settings.max_stop_pct), 2)
        effective_stop = emitted_update_stops.get(position.symbol, position.stop_price)
        if effective_stop < cap_stop:
            intents.append(
                CycleIntent(
                    intent_type=CycleIntentType.UPDATE_STOP,
                    symbol=position.symbol,
                    timestamp=now,
                    stop_price=cap_stop,
                    strategy_name=strategy_name,
                    reason="stop_cap_applied",
                )
            )
```

After:
```python
    if not is_extended:
        for position in open_positions:
            if position.symbol in emitted_exit_syms:
                continue
            if position.stop_price <= 0 or position.entry_price <= 0:
                continue
            cap_stop = round(position.entry_price * (1 - settings.max_stop_pct), 2)
            effective_stop = emitted_update_stops.get(position.symbol, position.stop_price)
            if effective_stop < cap_stop:
                intents.append(
                    CycleIntent(
                        intent_type=CycleIntentType.UPDATE_STOP,
                        symbol=position.symbol,
                        timestamp=now,
                        stop_price=cap_stop,
                        strategy_name=strategy_name,
                        reason="stop_cap_applied",
                    )
                )
```

- [ ] **Step 4: Run tests to verify PASS**

```bash
pytest tests/unit/test_engine_extended_hours.py -v
```

Expected: All pass

- [ ] **Step 5: Commit**

```bash
git add src/alpaca_bot/core/engine.py tests/unit/test_engine_extended_hours.py
git commit -m "fix: suppress cap-up UPDATE_STOP during extended hours"
```

---

## Task 4: Engine — use extended spread threshold during extended hours

**Files:**
- Modify: `src/alpaca_bot/core/engine.py` (lines 352–356)
- Modify: `tests/unit/test_engine_extended_hours.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_engine_extended_hours.py`:

```python
def test_afterhours_spread_filter_uses_extended_threshold():
    """During extended hours, extended_hours_max_spread_pct applies, not max_spread_pct."""
    settings = _settings(
        EXTENDED_HOURS_MAX_SPREAD_PCT="0.01",
        ENABLE_SPREAD_FILTER="true",
        MAX_SPREAD_PCT="0.002",
    )
    # 0.5% spread: blocked by regular 0.2% threshold, allowed by extended 1% threshold
    class FakeQuote:
        spread_pct = 0.005

    now = datetime(2026, 4, 28, 22, 0, tzinfo=timezone.utc)  # 6pm ET
    # Bar at ENTRY_WINDOW_END (3:30pm ET = 19:30 UTC) so signal_index walk-back (Task 5) finds it.
    bar = _bar("AAPL", close=105.0, ts=datetime(2026, 4, 28, 19, 30, tzinfo=timezone.utc))

    result = evaluate_cycle(
        settings=settings,
        now=now,
        equity=100_000.0,
        intraday_bars_by_symbol={"AAPL": [bar]},
        daily_bars_by_symbol={"AAPL": [bar]},
        open_positions=[],
        working_order_symbols=set(),
        traded_symbols_today=set(),
        entries_disabled=False,
        session_type=SessionType.AFTER_HOURS,
        quotes_by_symbol={"AAPL": FakeQuote()},
        signal_evaluator=lambda **kwargs: EntrySignal(
            symbol="AAPL",
            signal_bar=kwargs["intraday_bars"][-1],
            entry_level=105.1,
            relative_volume=2.0,
            stop_price=103.0,
            limit_price=105.2,
            initial_stop_price=103.0,
        ),
    )
    assert result.spread_blocked_symbols == (), (
        "0.5% spread should pass the 1% extended-hours threshold; "
        "regular-session 0.2% threshold must not apply during extended hours"
    )
```

- [ ] **Step 2: Run to verify FAIL**

```bash
pytest tests/unit/test_engine_extended_hours.py::test_afterhours_spread_filter_uses_extended_threshold -v
```

Expected: FAIL — 0.5% spread is blocked because regular 0.2% threshold is still used

- [ ] **Step 3: Apply engine change**

In `src/alpaca_bot/core/engine.py`, replace lines 352–356:

Before:
```python
                if settings.enable_spread_filter and quotes_by_symbol is not None:
                    quote = quotes_by_symbol.get(symbol)
                    if quote is not None and quote.spread_pct > settings.max_spread_pct:
                        _spread_blocked.append(symbol)
                        continue
```

After:
```python
                if settings.enable_spread_filter and quotes_by_symbol is not None:
                    quote = quotes_by_symbol.get(symbol)
                    spread_threshold = (
                        settings.extended_hours_max_spread_pct
                        if is_extended
                        else settings.max_spread_pct
                    )
                    if quote is not None and quote.spread_pct > spread_threshold:
                        _spread_blocked.append(symbol)
                        continue
```

- [ ] **Step 4: Run all related tests to verify PASS**

```bash
pytest tests/unit/test_engine_extended_hours.py tests/unit/test_settings_extended_hours.py -v
```

Expected: All pass

- [ ] **Step 5: Run full test suite**

```bash
pytest
```

Expected: All pass

- [ ] **Step 6: Commit**

```bash
git add src/alpaca_bot/core/engine.py tests/unit/test_engine_extended_hours.py
git commit -m "fix: use extended_hours_max_spread_pct threshold during extended hours"
```

---

## Task 5: Engine — walk back to last in-window bar as signal_index during extended hours

**Background:** Every strategy evaluator (breakout, momentum, orb, etc.) calls
`is_entry_session_time(signal_bar.timestamp, settings)` which checks that the bar's timestamp
falls within `[ENTRY_WINDOW_START, ENTRY_WINDOW_END]`. During afterhours, `bars[-1]` is the
3:45pm bar (timestamp past `ENTRY_WINDOW_END=15:30`) so ALL evaluators return `None` —
even after the bar age fix. The engine must find the most recent bar within the regular entry
window and pass that as `signal_index`.

**Files:**
- Modify: `src/alpaca_bot/core/engine.py` (line 21 import + lines 358–364)
- Modify: `tests/unit/test_engine_extended_hours.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_engine_extended_hours.py`:

```python
def test_afterhours_signal_uses_last_in_window_bar():
    """During extended hours, signal_evaluator must receive the last bar within ENTRY_WINDOW_END."""
    settings = _settings()  # ENTRY_WINDOW_END=15:30
    now = datetime(2026, 4, 28, 22, 0, tzinfo=timezone.utc)  # 6pm ET

    # Two bars: 3:30pm ET (within ENTRY_WINDOW_END=15:30) and 3:45pm ET (past it)
    bar_in_window = _bar("AAPL", close=105.0, ts=datetime(2026, 4, 28, 19, 30, tzinfo=timezone.utc))
    bar_past_window = _bar("AAPL", close=106.0, ts=datetime(2026, 4, 28, 19, 45, tzinfo=timezone.utc))

    seen_signal_ts: list = []

    def recording_evaluator(**kwargs) -> EntrySignal | None:
        seen_signal_ts.append(kwargs["intraday_bars"][kwargs["signal_index"]].timestamp)
        return EntrySignal(
            symbol="AAPL",
            signal_bar=kwargs["intraday_bars"][kwargs["signal_index"]],
            entry_level=105.1,
            relative_volume=2.0,
            stop_price=103.0,
            limit_price=105.2,
            initial_stop_price=103.0,
        )

    evaluate_cycle(
        settings=settings,
        now=now,
        equity=100_000.0,
        intraday_bars_by_symbol={"AAPL": [bar_in_window, bar_past_window]},
        daily_bars_by_symbol={"AAPL": [bar_in_window]},
        open_positions=[],
        working_order_symbols=set(),
        traded_symbols_today=set(),
        entries_disabled=False,
        session_type=SessionType.AFTER_HOURS,
        signal_evaluator=recording_evaluator,
    )
    assert seen_signal_ts, "signal_evaluator must be called during AFTER_HOURS"
    assert seen_signal_ts[0] == bar_in_window.timestamp, (
        "signal_index must point to the last bar within ENTRY_WINDOW_END, not bars[-1]"
    )
```

- [ ] **Step 2: Run to verify FAIL**

```bash
pytest tests/unit/test_engine_extended_hours.py::test_afterhours_signal_uses_last_in_window_bar -v
```

Expected: FAIL — `seen_signal_ts[0]` is `bar_past_window.timestamp` (3:45pm, i.e., `bars[-1]`), not `bar_in_window.timestamp`

- [ ] **Step 3: Add import to engine**

In `src/alpaca_bot/core/engine.py`, change line 21:

Before:
```python
from alpaca_bot.strategy.session import SessionType, is_flatten_time as _session_flatten_time
```

After:
```python
from alpaca_bot.strategy.session import (
    SessionType,
    is_entry_window as _is_entry_window,
    is_flatten_time as _session_flatten_time,
)
```

- [ ] **Step 4: Apply engine change**

In `src/alpaca_bot/core/engine.py`, replace lines 358–364:

Before:
```python
                signal = signal_evaluator(
                    symbol=symbol,
                    intraday_bars=bars,
                    signal_index=len(bars) - 1,
                    daily_bars=daily_bars,
                    settings=settings,
                )
```

After:
```python
                if is_extended:
                    # bars[-1] may be a bar past ENTRY_WINDOW_END (e.g., 3:45pm when
                    # ENTRY_WINDOW_END=15:30). Walk back to the last bar within the
                    # regular entry window so is_entry_session_time() inside each
                    # strategy evaluator does not reject the signal bar.
                    signal_index = next(
                        (
                            i
                            for i in range(len(bars) - 1, -1, -1)
                            if _is_entry_window(bars[i].timestamp, settings, SessionType.REGULAR)
                        ),
                        -1,
                    )
                    if signal_index < 0:
                        continue
                else:
                    signal_index = len(bars) - 1
                signal = signal_evaluator(
                    symbol=symbol,
                    intraday_bars=bars,
                    signal_index=signal_index,
                    daily_bars=daily_bars,
                    settings=settings,
                )
```

- [ ] **Step 5: Run all tests to verify PASS**

```bash
pytest tests/unit/test_engine_extended_hours.py tests/unit/test_settings_extended_hours.py -v
```

Expected: All pass

- [ ] **Step 6: Run full test suite**

```bash
pytest
```

Expected: All pass

- [ ] **Step 7: Commit**

```bash
git add src/alpaca_bot/core/engine.py tests/unit/test_engine_extended_hours.py
git commit -m "fix: walk back signal_index to last in-window bar during extended hours"
```
