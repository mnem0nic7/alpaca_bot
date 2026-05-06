---
title: Afterhours Trading Correctness Fixes
date: 2026-05-06
status: approved
---

# Afterhours Trading Correctness Fixes

## Goal

Fix three concrete gaps that prevent `EXTENDED_HOURS_ENABLED=true` from actually working:
1. Bar age check blocks all entries after the first 30 minutes of the afterhours window
2. Cap-up stop pass incorrectly emits `UPDATE_STOP` intents during extended hours (causes `stop_update_failed` spam)
3. Spread filter threshold (calibrated for regular session) blocks all afterhours entries

---

## Root Cause Analysis

### Gap 1: Bar Age Check Blocks Afterhours Entries

`engine.py:337-339` (entry evaluation loop):

```python
bar_age_seconds = (now - latest_bar.timestamp.astimezone(timezone.utc)).total_seconds()
if bar_age_seconds > 2 * settings.entry_timeframe_minutes * 60:
    continue
```

With `ENTRY_TIMEFRAME_MINUTES=15`, this allows bars up to 30 minutes old. During afterhours (after 4pm ET), the last regular session bar is from ~3:55pm. By 4:35pm the bar is 40 minutes old → blocked. By 5pm it's 65 minutes old → blocked.

**Result:** Afterhours entries are impossible past 4:35pm ET. The entry window is configured as 4:05pm–7:30pm but only the first 30 minutes can produce entries.

**Fix:** During extended hours, skip the bar age check. The rationale: afterhours trading intentionally evaluates patterns from the regular session close. There is no newer bar available — the regular session bars are the correct and only available signal basis. The limit order execution (with `extended_hours=True`) provides the price guard; stale-signal protection is not meaningful when there is no fresher alternative.

---

### Gap 2: Cap-Up Pass Emits UPDATE_STOP During Extended Hours

`engine.py:278-295` (cap-up pass — runs AFTER the main position loop):

```python
# No is_extended guard here
for position in open_positions:
    ...
    cap_stop = round(position.entry_price * (1 - settings.max_stop_pct), 2)
    effective_stop = emitted_update_stops.get(position.symbol, position.stop_price)
    if effective_stop < cap_stop:
        intents.append(CycleIntent(intent_type=CycleIntentType.UPDATE_STOP, ...))
```

The ATR trailing stop pass (lines 116-228) correctly executes `if is_extended: continue` to skip stop management. The profit-trail pass (line 230) correctly guards with `if settings.enable_profit_trail and not is_extended:`. The cap-up pass has no such guard.

Any position whose `stop_price` is more than `max_stop_pct` below entry (typical for wide-stop small-cap entries) will have `UPDATE_STOP` emitted every afterhours cycle. These intents reach `_execute_update_stop` → `broker.replace_order()` → Alpaca rejects → `stop_update_failed` audit event every cycle.

**Fix:** Add `if not is_extended:` guard before the cap-up loop.

---

### Gap 3: Spread Filter Too Strict for Extended Hours

`engine.py:352-356` (entry loop):

```python
if settings.enable_spread_filter and quotes_by_symbol is not None:
    quote = quotes_by_symbol.get(symbol)
    if quote is not None and quote.spread_pct > settings.max_spread_pct:
        _spread_blocked.append(symbol)
        continue
```

`max_spread_pct` defaults to 0.002 (0.2%). NBBO spreads during extended hours are typically 0.5–2% for liquid large caps and 2–5% for smaller names. The regular-session threshold is approximately 5–25× too strict for afterhours, blocking every entry regardless of liquidity.

**Fix:** Add `EXTENDED_HOURS_MAX_SPREAD_PCT` (default 0.01 = 1%) and use it as the spread threshold when `is_extended=True`.

---

## Design

### Component Overview

| Component | File | Change |
|---|---|---|
| Settings | `config/__init__.py` | Add `extended_hours_max_spread_pct: float = 0.01` + env parse + validate |
| Engine | `core/engine.py` | Three targeted changes (see below) |
| Tests | `tests/unit/test_engine_extended_hours.py` | Add tests for each fix |
| Tests | `tests/unit/test_settings_extended_hours.py` | Add test for new setting |

---

## Section 1 — Settings

**File:** `src/alpaca_bot/config/__init__.py`

New field on `Settings`:

```python
extended_hours_max_spread_pct: float = 0.01
```

`from_env()` parsing:

```python
extended_hours_max_spread_pct=float(
    values.get("EXTENDED_HOURS_MAX_SPREAD_PCT", "0.01")
),
```

`validate()` assertion:

```python
if self.extended_hours_max_spread_pct < self.max_spread_pct:
    raise ValueError(
        f"EXTENDED_HOURS_MAX_SPREAD_PCT ({self.extended_hours_max_spread_pct}) "
        f"must be >= MAX_SPREAD_PCT ({self.max_spread_pct})"
    )
```

A value smaller than `max_spread_pct` would be a configuration error (you'd be applying a stricter threshold during extended hours than during regular session).

---

## Section 2 — Engine Changes

**File:** `src/alpaca_bot/core/engine.py`

Three changes, each small and targeted.

### Change A: Skip bar age check during extended hours

**Location:** Entry evaluation loop, around line 337.

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

**Why `not is_extended` rather than a configurable threshold:** During extended hours, the last regular session bar IS the most recent available data. There is no fresher alternative. Any configurable threshold would need to be at least 4 hours (the entire afterhours window) to be useful — which is functionally equivalent to skipping the check.

### Change B: Gate cap-up pass behind `not is_extended`

**Location:** Cap-up pass, around line 278.

Before:
```python
for position in open_positions:
    if position.symbol in emitted_exit_syms:
        continue
    ...
    if effective_stop < cap_stop:
        intents.append(CycleIntent(intent_type=CycleIntentType.UPDATE_STOP, ...))
```

After:
```python
if not is_extended:
    for position in open_positions:
        if position.symbol in emitted_exit_syms:
            continue
        ...
        if effective_stop < cap_stop:
            intents.append(CycleIntent(intent_type=CycleIntentType.UPDATE_STOP, ...))
```

**Effect:** Cap-up stop adjustment never fires during extended hours. Stop orders remain at their pre-afterhours level. This is safe: the protective stop is already in place; the cap-up is an optimization (tightening a loose stop), not a protection mechanism.

### Change C: Use extended spread threshold during extended hours

**Location:** Entry loop spread filter, around line 352.

Before:
```python
if quote is not None and quote.spread_pct > settings.max_spread_pct:
```

After:
```python
spread_threshold = (
    settings.extended_hours_max_spread_pct
    if is_extended
    else settings.max_spread_pct
)
if quote is not None and quote.spread_pct > spread_threshold:
```

---

## Section 3 — Tests

### `test_engine_extended_hours.py` additions

**Test for bar age relaxation:**

```python
def test_afterhours_entry_not_blocked_by_stale_bars():
    """Entries must be possible during afterhours even with 3-hour-old bars."""
    settings = _settings()
    # 6pm ET = 3 hours after regular session close; bar from 3:55pm is stale by 30-min check
    now = datetime(2026, 4, 28, 22, 0, tzinfo=timezone.utc)
    stale_bar = _bar("AAPL", close=105.0, ts=datetime(2026, 4, 28, 19, 55, tzinfo=timezone.utc))

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
        signal_evaluator=lambda **kwargs: BreakoutSignal(
            symbol="AAPL", limit_price=105.1, stop_price=103.0, signal_timestamp=now
        ),
    )
    entries = [i for i in result.intents if i.intent_type is CycleIntentType.ENTRY]
    assert entries, (
        "AFTER_HOURS entries must not be blocked by the 30-minute bar-age check; "
        "regular session bars are the correct and only available signal basis"
    )
```

**Test for cap-up guard:**

```python
def test_cap_up_stop_not_emitted_in_after_hours():
    """Cap-up UPDATE_STOP must not be emitted during extended hours."""
    settings = _settings()
    now = datetime(2026, 4, 28, 21, 0, tzinfo=timezone.utc)
    # Position with stop much lower than cap (entry=100, max_stop_pct=5%, cap=95, stop=88)
    position = OpenPosition(
        symbol="AAPL",
        quantity=10,
        entry_price=100.0,
        stop_price=88.0,  # well below cap_stop of 95.0
        initial_stop_price=88.0,
        entry_level=88.0,
        entry_timestamp=datetime(2026, 4, 28, 14, 0, tzinfo=timezone.utc),
    )
    bar = _bar("AAPL", close=100.0, ts=now)

    result = evaluate_cycle(
        settings=_settings(MAX_STOP_PCT="0.05"),
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

**Test for extended spread threshold:**

```python
def test_afterhours_spread_filter_uses_extended_threshold():
    """During extended hours, the extended_hours_max_spread_pct threshold applies."""
    settings = _settings(
        EXTENDED_HOURS_MAX_SPREAD_PCT="0.01",
        ENABLE_SPREAD_FILTER="true",
        MAX_SPREAD_PCT="0.002",
    )
    # 0.5% spread: blocked by regular threshold (0.2%), allowed by extended (1%)
    class FakeQuote:
        spread_pct = 0.005

    now = datetime(2026, 4, 28, 21, 0, tzinfo=timezone.utc)
    bar = _bar("AAPL", close=105.0, ts=now)

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
        signal_evaluator=lambda **kwargs: BreakoutSignal(
            symbol="AAPL", limit_price=105.1, stop_price=103.0, signal_timestamp=now
        ),
    )
    assert result.spread_blocked_symbols == (), (
        "0.5% spread should pass the 1% extended-hours threshold; "
        "regular-session 0.2% threshold must not apply during extended hours"
    )
```

### `test_settings_extended_hours.py` addition

```python
def test_extended_hours_max_spread_pct_must_be_at_least_max_spread_pct():
    with pytest.raises(ValueError, match="EXTENDED_HOURS_MAX_SPREAD_PCT"):
        Settings.from_env({
            **BASE_ENV,
            "MAX_SPREAD_PCT": "0.01",
            "EXTENDED_HOURS_MAX_SPREAD_PCT": "0.005",  # stricter than regular — invalid
        })
```

---

## Non-Goals

- Fetching actual extended hours bar data (alpaca-py `StockBarsRequest` has no `extended_hours` field in the installed version; regular session bars are the correct signal basis)
- Afterhours stop replacement or cancellation (Alpaca API limitation; intentional design)
- New afterhours entry strategies (separate concern)
- Stop-replace `client_order_id` uniqueness fix (separate approved plan at `docs/superpowers/plans/2026-05-06-stop-replace-client-order-id-fix.md`)

---

## Expected Outcome

With these three changes and `EXTENDED_HOURS_ENABLED=true`:
- Entries can be evaluated and submitted throughout the configured `AFTER_HOURS_ENTRY_WINDOW_START`–`AFTER_HOURS_ENTRY_WINDOW_END` range (4:05pm–7:30pm ET default)
- Zero spurious `stop_update_failed` events from the cap-up pass during afterhours
- Entry signals are not blocked by spread filter at normal afterhours spreads up to 1%
