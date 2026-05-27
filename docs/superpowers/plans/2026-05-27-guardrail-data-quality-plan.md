# Guardrail Data Quality Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix three silent data-quality failures in the trading engine: emit DecisionRecords when sizing rejects an entry, block new entries on stale daily bars, and filter zero-close bars before ATR computation.

**Architecture:** All changes are confined to `src/alpaca_bot/core/engine.py`. Component 1 extends two existing `continue` statements with `DecisionRecord` emissions. Component 2 adds a daily bar age check before `signal_evaluator()`, symmetric with the existing viability exit check at line 218. Component 3 adds a private `_filter_valid_bars` helper (requiring a new `import logging` / `logger` in `engine.py`) called once per symbol after the daily bars fetch. No migrations, no new Settings fields, no signature changes to `calculate_position_size`.

**Tech Stack:** Python, existing `DecisionRecord` dataclass (`domain/decision_record.py`), existing `viability_daily_bar_max_age_days` Settings field (default 5).

**IMPORTANT fixture note:** Five test files use `make_daily_bars()` helpers with stale timestamps that will trigger the new stale bar guard and silently break existing tests. All five must be fixed **before** the stale bar guard is added (Task 3, Steps 3.1–3.5):

| File | Current start | Test `now` | Last bar age | Fix: new start |
|------|--------------|-----------|-------------|---------------|
| `tests/unit/test_cycle_engine.py` | `2026-03-26 20:00` | `2026-04-24 19:00` | 8 days | `2026-04-04 20:00` |
| `tests/unit/test_data_source_filters.py` | `2026-03-26 20:00` | `2026-04-24 19:00` | 8 days | `2026-04-04 20:00` |
| `tests/unit/test_decision_log.py` | `2026-03-26 20:00` | `2026-05-07 14:30` | 20 days | `2026-04-17 20:00` |
| `tests/unit/test_engine_stop_cap.py` | base `2026-04-01` | `2026-05-06 19:00` | 11 days | base `2026-05-01` |
| `tests/unit/test_replay_runner_engine_delegation.py` | `2026-03-26 20:00` | `2026-04-24 14:00` | 9 days | `2026-04-04 20:00` |

---

## File Map

| File | Action | What changes |
|------|--------|-------------|
| `src/alpaca_bot/core/engine.py` | Modify | Add `import logging` + `logger`; add `_filter_valid_bars` helper; call it after daily_bars fetch; add stale-bar DecisionRecord before signal_evaluator; add sizing-rejection DecisionRecords at lines 884–890 |
| `tests/unit/test_cycle_engine.py` | Modify | Update `make_daily_bars` start date; add 3 new tests for sizing rejection, stale bar guard, zero-close filter |
| `tests/unit/test_data_source_filters.py` | Modify | Update `_make_daily_bars` start date to keep bars fresh relative to `_INTRADAY_NOW` |
| `tests/unit/test_decision_log.py` | Modify | Update `make_daily_bars` start date to keep bars fresh relative to `_NOW` |
| `tests/unit/test_engine_stop_cap.py` | Modify | Update `_make_daily_bars` base date to keep bars fresh relative to `_now()` |
| `tests/unit/test_replay_runner_engine_delegation.py` | Modify | Update `_make_daily_bars` start date to keep bars fresh relative to intraday `now` |

---

## Task 1: Sizing Rejection DecisionRecord (Component 1)

**Files:**
- Modify: `src/alpaca_bot/core/engine.py:884-890`
- Test: `tests/unit/test_cycle_engine.py`

### Background

`engine.py:884-890` has two silent `continue` statements. When `quantity <= 0.0` (stop is too close to entry, or equity too small) or `quantity * limit_price < min_position_notional`, the engine discards the signal with no audit record. Operators can't distinguish "no signal" from "signal but priced out."

The fix emits a `DecisionRecord(decision="rejected", reject_stage="sizing", reject_reason="quantity_zero")` or `reject_reason="below_min_notional"` at each site.

- [ ] **Step 1.1: Write the failing test for quantity_zero**

In `tests/unit/test_cycle_engine.py`, append:

```python
def test_sizing_rejection_quantity_zero_emits_decision_record() -> None:
    """When position size rounds to zero, a DecisionRecord with reject_stage='sizing',
    reject_reason='quantity_zero' must appear in cycle_result.decision_records."""
    from alpaca_bot.domain import DecisionRecord
    _CycleIntentType, evaluate_cycle = load_engine_api()
    now = datetime(2026, 4, 24, 19, 0, tzinfo=timezone.utc)

    result = evaluate_cycle(
        settings=make_settings(),
        now=now,
        equity=0.01,  # so tiny that quantity rounds to 0
        intraday_bars_by_symbol={"AAPL": make_breakout_intraday_bars()},
        daily_bars_by_symbol={"AAPL": make_daily_bars()},
        open_positions=[],
        working_order_symbols=set(),
        traded_symbols_today=set(),
        entries_disabled=False,
    )

    sizing_rejections = [
        dr for dr in result.decision_records
        if dr.reject_stage == "sizing" and dr.reject_reason == "quantity_zero"
    ]
    assert len(sizing_rejections) == 1, (
        f"Expected 1 sizing/quantity_zero DecisionRecord, got {len(sizing_rejections)}: "
        f"{result.decision_records!r}"
    )
    r = sizing_rejections[0]
    assert r.symbol == "AAPL"
    assert r.decision == "rejected"
    assert r.quantity == 0.0
    assert r.equity == pytest.approx(0.01)
```

- [ ] **Step 1.2: Run test to verify it fails**

```bash
pytest tests/unit/test_cycle_engine.py::test_sizing_rejection_quantity_zero_emits_decision_record -v
```

Expected: `FAILED` — `AssertionError: Expected 1 sizing/quantity_zero DecisionRecord, got 0`

- [ ] **Step 1.3: Write the failing test for below_min_notional**

Append to `tests/unit/test_cycle_engine.py`:

```python
def test_sizing_rejection_below_min_notional_emits_decision_record() -> None:
    """When quantity * limit_price < MIN_POSITION_NOTIONAL, a DecisionRecord with
    reject_stage='sizing', reject_reason='below_min_notional' must be emitted."""
    from alpaca_bot.domain import DecisionRecord
    _CycleIntentType, evaluate_cycle = load_engine_api()
    now = datetime(2026, 4, 24, 19, 0, tzinfo=timezone.utc)

    # AAPL signal limit_price is ~110.12 (from make_breakout_intraday_bars).
    # With RISK_PER_TRADE_PCT=0.0025 and equity=10_000: risk_budget=25,
    # risk_per_share≈2.12, qty_float≈11.8.
    # MAX_POSITION_PCT=0.05 caps at floor(0.05*10_000/110.12)=4 shares.
    # 4 * 110.12 = 440 < 2000 → below_min_notional fires.
    result = evaluate_cycle(
        settings=make_settings(MIN_POSITION_NOTIONAL="2000"),
        now=now,
        equity=10_000.0,
        intraday_bars_by_symbol={"AAPL": make_breakout_intraday_bars()},
        daily_bars_by_symbol={"AAPL": make_daily_bars()},
        open_positions=[],
        working_order_symbols=set(),
        traded_symbols_today=set(),
        entries_disabled=False,
    )

    notional_rejections = [
        dr for dr in result.decision_records
        if dr.reject_stage == "sizing" and dr.reject_reason == "below_min_notional"
    ]
    assert len(notional_rejections) == 1, (
        f"Expected 1 sizing/below_min_notional DecisionRecord, got "
        f"{len(notional_rejections)}: {result.decision_records!r}"
    )
    r = notional_rejections[0]
    assert r.symbol == "AAPL"
    assert r.decision == "rejected"
    assert r.quantity is not None and r.quantity > 0
```

- [ ] **Step 1.4: Run test to verify it fails**

```bash
pytest tests/unit/test_cycle_engine.py::test_sizing_rejection_below_min_notional_emits_decision_record -v
```

Expected: `FAILED` — `AssertionError: Expected 1 sizing/below_min_notional DecisionRecord, got 0`

- [ ] **Step 1.5: Implement the fix in engine.py**

In `src/alpaca_bot/core/engine.py`, replace lines 884–890:

```python
                    if quantity <= 0.0:
                        continue
                    if (
                        settings.min_position_notional > 0
                        and quantity * signal.limit_price < settings.min_position_notional
                    ):
                        continue
```

with:

```python
                    if quantity <= 0.0:
                        _decision_records.append(DecisionRecord(
                            cycle_at=now,
                            symbol=symbol,
                            strategy_name=strategy_name,
                            trading_mode=_tm,
                            strategy_version=_sv,
                            decision="rejected",
                            reject_stage="sizing",
                            reject_reason="quantity_zero",
                            entry_level=signal.entry_level,
                            signal_bar_close=signal.signal_bar.close,
                            relative_volume=signal.relative_volume,
                            atr=None,
                            stop_price=signal.stop_price,
                            limit_price=signal.limit_price,
                            initial_stop_price=effective_initial_stop,
                            quantity=0.0,
                            risk_per_share=round(signal.limit_price - effective_initial_stop, 4),
                            equity=equity,
                            filter_results={},
                            vix_close=_ctx_vix_close,
                            vix_above_sma=_ctx_vix_above_sma,
                            sector_passing_pct=_ctx_sector_passing_pct,
                        ))
                        continue
                    if (
                        settings.min_position_notional > 0
                        and quantity * signal.limit_price < settings.min_position_notional
                    ):
                        _decision_records.append(DecisionRecord(
                            cycle_at=now,
                            symbol=symbol,
                            strategy_name=strategy_name,
                            trading_mode=_tm,
                            strategy_version=_sv,
                            decision="rejected",
                            reject_stage="sizing",
                            reject_reason="below_min_notional",
                            entry_level=signal.entry_level,
                            signal_bar_close=signal.signal_bar.close,
                            relative_volume=signal.relative_volume,
                            atr=None,
                            stop_price=signal.stop_price,
                            limit_price=signal.limit_price,
                            initial_stop_price=effective_initial_stop,
                            quantity=quantity,
                            risk_per_share=round(signal.limit_price - effective_initial_stop, 4),
                            equity=equity,
                            filter_results={},
                            vix_close=_ctx_vix_close,
                            vix_above_sma=_ctx_vix_above_sma,
                            sector_passing_pct=_ctx_sector_passing_pct,
                        ))
                        continue
```

- [ ] **Step 1.6: Run both new tests to verify they pass**

```bash
pytest tests/unit/test_cycle_engine.py::test_sizing_rejection_quantity_zero_emits_decision_record tests/unit/test_cycle_engine.py::test_sizing_rejection_below_min_notional_emits_decision_record -v
```

Expected: Both `PASSED`.

- [ ] **Step 1.7: Run the full test suite to verify no regressions**

```bash
pytest tests/unit/test_cycle_engine.py -v
```

Expected: All tests pass. The existing `test_evaluate_cycle_skips_entry_when_position_size_rounds_to_zero` still passes (it only asserts `entry_intents == []`, which remains true).

- [ ] **Step 1.8: Commit**

```bash
git add src/alpaca_bot/core/engine.py tests/unit/test_cycle_engine.py
git commit -m "feat: emit DecisionRecord for sizing rejections (quantity_zero, below_min_notional)"
```

---

## Task 2: Zero-Close Bar Filter (Component 3)

**Files:**
- Modify: `src/alpaca_bot/core/engine.py` — add `import logging` + `logger`; add `_filter_valid_bars` helper; call it after daily_bars fetch at line 679
- Test: `tests/unit/test_cycle_engine.py`

### Background

A bar with `close <= 0` from the data feed passes unchecked into `calculate_atr`, where `_tr(i) = max(bar.high - bar.low, abs(bar.high - prev_close), abs(bar.low - prev_close))`. A zero prev_close inflates `abs(bar.high - 0)` by the full price, corrupting the ATR stop. The fix filters these bars at the engine call site before ATR is ever computed.

**Note:** `engine.py` currently contains zero logging — no `import logging` and no `logger`. Adding `_filter_valid_bars` with `logger.warning()` requires adding both. This step must come before adding the helper function.

- [ ] **Step 2.1: Write the failing test**

Append to `tests/unit/test_cycle_engine.py`:

```python
def test_zero_close_bar_is_filtered_before_atr(caplog) -> None:
    """A daily bar with close=0 must be dropped before ATR computation.
    The engine must log a warning and the remaining bars must be used."""
    import logging
    _CycleIntentType, evaluate_cycle = load_engine_api()
    now = datetime(2026, 4, 24, 19, 0, tzinfo=timezone.utc)

    good_bars = make_daily_bars()
    # Insert a zero-close bar in the middle of the sequence.
    zero_bar = Bar(
        symbol="AAPL",
        timestamp=good_bars[10].timestamp,
        open=0.0, high=0.0, low=0.0, close=0.0, volume=0,
    )
    contaminated = good_bars[:10] + [zero_bar] + good_bars[10:]

    with caplog.at_level(logging.WARNING, logger="alpaca_bot.core.engine"):
        result = evaluate_cycle(
            settings=make_settings(),
            now=now,
            equity=100_000.0,
            intraday_bars_by_symbol={"AAPL": make_breakout_intraday_bars()},
            daily_bars_by_symbol={"AAPL": contaminated},
            open_positions=[],
            working_order_symbols=set(),
            traded_symbols_today=set(),
            entries_disabled=False,
        )

    # A warning must have been logged about dropped bars.
    warning_msgs = [r.message for r in caplog.records if r.levelno == logging.WARNING]
    assert any("zero-close" in m for m in warning_msgs), (
        f"Expected a zero-close warning; got: {warning_msgs!r}"
    )
    # Engine must still produce an ENTRY intent (good bars survive filtering).
    entry_intents = [i for i in result.intents if i.intent_type == _CycleIntentType.ENTRY]
    assert len(entry_intents) == 1, (
        f"Expected 1 ENTRY intent after zero-bar filtering, got {len(entry_intents)}"
    )
```

- [ ] **Step 2.2: Run test to verify it fails**

```bash
pytest tests/unit/test_cycle_engine.py::test_zero_close_bar_is_filtered_before_atr -v
```

Expected: `FAILED` — no warning logged, because the filter helper doesn't exist yet.

- [ ] **Step 2.3: Add `import logging` and `logger` to engine.py**

`engine.py` currently has no logging. The `_filter_valid_bars` helper uses `logger.warning()`, which requires both an import and a module-level logger.

In `src/alpaca_bot/core/engine.py`, add `import logging` to the stdlib imports block (between `from __future__ import annotations` and `from dataclasses import ...`):

```python
from __future__ import annotations

import logging
from dataclasses import dataclass, field
```

Then, after the last `from alpaca_bot...` import line (approximately line 23, before the `if TYPE_CHECKING:` block), add:

```python
logger = logging.getLogger(__name__)
```

- [ ] **Step 2.4: Add `_filter_valid_bars` to engine.py**

In `src/alpaca_bot/core/engine.py`, add the helper as a module-level private function after the `logger = ...` line and before the `CycleIntentType` class definition:

```python
def _filter_valid_bars(bars: Sequence[Bar], *, label: str = "") -> tuple[Bar, ...]:
    valid = tuple(b for b in bars if b.close > 0)
    if len(valid) < len(bars):
        logger.warning(
            "_filter_valid_bars: dropped %d zero-close bars%s",
            len(bars) - len(valid),
            f" for {label}" if label else "",
        )
    return valid
```

- [ ] **Step 2.5: Call `_filter_valid_bars` after the daily_bars fetch**

In `src/alpaca_bot/core/engine.py`, find line 679:

```python
                daily_bars = daily_bars_by_symbol.get(symbol, ())
```

Change to:

```python
                daily_bars = daily_bars_by_symbol.get(symbol, ())
                daily_bars = _filter_valid_bars(daily_bars, label=symbol)
```

- [ ] **Step 2.6: Run the new test**

```bash
pytest tests/unit/test_cycle_engine.py::test_zero_close_bar_is_filtered_before_atr -v
```

Expected: `PASSED`.

- [ ] **Step 2.7: Run the full suite**

```bash
pytest tests/unit/test_cycle_engine.py -v
```

Expected: All tests pass.

- [ ] **Step 2.8: Commit**

```bash
git add src/alpaca_bot/core/engine.py tests/unit/test_cycle_engine.py
git commit -m "feat: filter zero-close daily bars before ATR computation"
```

---

## Task 3: Stale Daily Bar Guard at Entry (Component 2)

**Files:**
- Modify: `tests/unit/test_cycle_engine.py` — update `make_daily_bars` start date **first**
- Modify: `tests/unit/test_data_source_filters.py` — update `_make_daily_bars` start date
- Modify: `tests/unit/test_decision_log.py` — update `make_daily_bars` start date
- Modify: `tests/unit/test_engine_stop_cap.py` — update `_make_daily_bars` base date
- Modify: `tests/unit/test_replay_runner_engine_delegation.py` — update `_make_daily_bars` start date
- Modify: `src/alpaca_bot/core/engine.py` — add stale-bar DecisionRecord before signal_evaluator call

### Background

The engine checks daily bar age before viability exits (line 218) but not for new entries. A stale daily bar (e.g., from a holiday data gap) passes into `atr_stop_buffer()` unchecked. The `calculate_atr() is None` guard only catches missing/insufficient bars, not stale-but-present bars.

**Fixture fix required first:** Five test files generate daily bars with timestamps that would trip the new guard. Each must be updated before the guard is added. The required changes are purely timestamp shifts — the OHLC values (`close=90.0+i`, `high=90.0+i`, etc.) are unchanged, so all ATR and SMA assertions remain valid.

- [ ] **Step 3.1: Update `make_daily_bars` start date in `test_cycle_engine.py`**

In `tests/unit/test_cycle_engine.py`, find `make_daily_bars` (starts around line 40):

```python
    start = datetime(2026, 3, 26, 20, 0, tzinfo=timezone.utc)
```

Change to:

```python
    # Start chosen so bar[-1] lands on 2026-04-24 (same day as the test `now`),
    # keeping bar age < viability_daily_bar_max_age_days (default 5).
    start = datetime(2026, 4, 4, 20, 0, tzinfo=timezone.utc)
```

With 21 bars and 1-day step: last bar = `2026-04-24 20:00`. Test `now = 2026-04-24 19:00`. Age = -1 days → fresh.

- [ ] **Step 3.2: Update `_make_daily_bars` start date in `test_data_source_filters.py`**

In `tests/unit/test_data_source_filters.py`, find `_make_daily_bars` (line ~13):

```python
    start = datetime(2026, 3, 26, 20, 0, tzinfo=timezone.utc)
```

Change to:

```python
    # Start chosen so bar[-1] lands on 2026-04-24 (same day as _INTRADAY_NOW),
    # keeping bar age < viability_daily_bar_max_age_days (default 5).
    start = datetime(2026, 4, 4, 20, 0, tzinfo=timezone.utc)
```

`_INTRADAY_NOW = datetime(2026, 4, 24, 19, 0)`. With 21 bars: last bar = `2026-04-24 20:00`. Age = -1 days → fresh.

- [ ] **Step 3.3: Update `make_daily_bars` start date in `test_decision_log.py`**

In `tests/unit/test_decision_log.py`, find `make_daily_bars` (line ~131):

```python
    start = datetime(2026, 3, 26, 20, 0, tzinfo=timezone.utc)
```

Change to:

```python
    # Start chosen so bar[-1] lands on 2026-05-08 (day after _NOW = 2026-05-07),
    # keeping bar age < viability_daily_bar_max_age_days (default 5).
    start = datetime(2026, 4, 17, 20, 0, tzinfo=timezone.utc)
```

`_NOW = datetime(2026, 5, 7, 14, 30)`. With 22 bars: last bar = `2026-05-08 20:00`. Age = -2 days → fresh.

- [ ] **Step 3.4: Update `_make_daily_bars` base date in `test_engine_stop_cap.py`**

In `tests/unit/test_engine_stop_cap.py`, find `_make_daily_bars` (line ~88):

```python
    base = datetime(2026, 4, 1, 14, 0, tzinfo=timezone.utc)
```

Change to:

```python
    base = datetime(2026, 5, 1, 14, 0, tzinfo=timezone.utc)
```

The function uses `base.replace(day=i+1)` for `i in range(25)`, producing bars on days 1–25 of the base month. With `base = 2026-05-01`, bars run May 1–25. `_now() = 2026-05-06`. Last bar = May 25 → age = -19 days → fresh. OHLC values (`open=100, high=102, low=98, close=101`) are unchanged, so all stop-cap assertions remain valid.

- [ ] **Step 3.5: Update `_make_daily_bars` start date in `test_replay_runner_engine_delegation.py`**

In `tests/unit/test_replay_runner_engine_delegation.py`, find `_make_daily_bars` (line ~59):

```python
    start = datetime(2026, 3, 26, 20, 0, tzinfo=timezone.utc)
```

Change to:

```python
    # Start chosen so bar[-1] lands on 2026-04-24 (same day as intraday bars),
    # keeping bar age < viability_daily_bar_max_age_days (default 5).
    start = datetime(2026, 4, 4, 20, 0, tzinfo=timezone.utc)
```

Intraday bars start at `2026-04-24 14:00`. With 21 daily bars: last bar = `2026-04-24 20:00`. Age = -1 days → fresh.

- [ ] **Step 3.6: Run full test suite to confirm all fixture changes are safe**

```bash
pytest tests/unit/ -v
```

Expected: All tests pass. No assertions on price values are affected because OHLC values (`close=90.0+i`, etc.) are timestamp-independent.

- [ ] **Step 3.7: Write the failing test for stale daily bar guard**

Append to `tests/unit/test_cycle_engine.py`:

```python
def test_stale_daily_bar_at_entry_emits_decision_record() -> None:
    """When daily_bars[-1] is older than viability_daily_bar_max_age_days, the engine
    must skip the entry and emit a DecisionRecord with reject_stage='stale_data',
    reject_reason='daily_bars_stale'."""
    _CycleIntentType, evaluate_cycle = load_engine_api()
    now = datetime(2026, 4, 24, 19, 0, tzinfo=timezone.utc)

    # Build daily bars that are 7 days old — over the 5-day default threshold.
    stale_start = datetime(2026, 3, 28, 20, 0, tzinfo=timezone.utc)  # last bar: 2026-04-17
    stale_bars = [
        Bar(
            symbol="AAPL",
            timestamp=stale_start + timedelta(days=i),
            open=89.0 + i,
            high=90.0 + i,
            low=88.0 + i,
            close=90.0 + i,
            volume=1_000_000 + i * 1000,
        )
        for i in range(21)
    ]
    # stale_bars[-1].timestamp = 2026-04-17 20:00 UTC
    # age = (2026-04-24 19:00 - 2026-04-17 20:00).days = 6 > 5 → guard fires

    result = evaluate_cycle(
        settings=make_settings(),
        now=now,
        equity=100_000.0,
        intraday_bars_by_symbol={"AAPL": make_breakout_intraday_bars()},
        daily_bars_by_symbol={"AAPL": stale_bars},
        open_positions=[],
        working_order_symbols=set(),
        traded_symbols_today=set(),
        entries_disabled=False,
    )

    entry_intents = [i for i in result.intents if i.intent_type == _CycleIntentType.ENTRY]
    assert entry_intents == [], "No ENTRY expected when daily bars are stale"

    stale_rejections = [
        dr for dr in result.decision_records
        if dr.reject_stage == "stale_data" and dr.reject_reason == "daily_bars_stale"
    ]
    assert len(stale_rejections) == 1, (
        f"Expected 1 stale_data/daily_bars_stale DecisionRecord, got "
        f"{len(stale_rejections)}: {result.decision_records!r}"
    )
    r = stale_rejections[0]
    assert r.symbol == "AAPL"
    assert r.decision == "rejected"
```

- [ ] **Step 3.8: Run test to verify it fails**

```bash
pytest tests/unit/test_cycle_engine.py::test_stale_daily_bar_at_entry_emits_decision_record -v
```

Expected: `FAILED` — `AssertionError: Expected 1 stale_data/daily_bars_stale DecisionRecord, got 0`. (The engine currently produces an ENTRY intent instead.)

- [ ] **Step 3.9: Add the stale daily bar guard to engine.py**

In `src/alpaca_bot/core/engine.py`, find the block ending with the spread filter (around line 735) and before the session_type branch (`if session_type is SessionType.AFTER_HOURS:`). Insert after the spread filter's closing `continue` and before the session_type check.

**Note:** Do NOT wrap the guard in `if daily_bars:`. At this insertion point, the `if not bars or not daily_bars: continue` guard at line 680 has already guaranteed `daily_bars` is non-empty, so `daily_bars[-1]` is always safe.

```python
                # Guard: stale daily bars for new entries — symmetric with viability exit check.
                daily_bar_age_days = (
                    now - daily_bars[-1].timestamp.astimezone(timezone.utc)
                ).days
                if daily_bar_age_days > settings.viability_daily_bar_max_age_days:
                    _decision_records.append(DecisionRecord(
                        cycle_at=now,
                        symbol=symbol,
                        strategy_name=strategy_name,
                        trading_mode=_tm,
                        strategy_version=_sv,
                        decision="rejected",
                        reject_stage="stale_data",
                        reject_reason="daily_bars_stale",
                        entry_level=None,
                        signal_bar_close=None,
                        relative_volume=None,
                        atr=None,
                        stop_price=None,
                        limit_price=None,
                        initial_stop_price=None,
                        quantity=None,
                        risk_per_share=None,
                        equity=equity,
                        filter_results={},
                        vix_close=_ctx_vix_close,
                        vix_above_sma=_ctx_vix_above_sma,
                        sector_passing_pct=_ctx_sector_passing_pct,
                    ))
                    continue
```

The exact insertion point is after the closing `continue` of the spread filter block and before `if session_type is SessionType.AFTER_HOURS:`. Look for the comment `# Spread filter:` to locate it precisely.

- [ ] **Step 3.10: Run the new test**

```bash
pytest tests/unit/test_cycle_engine.py::test_stale_daily_bar_at_entry_emits_decision_record -v
```

Expected: `PASSED`.

- [ ] **Step 3.11: Run the full suite**

```bash
pytest tests/unit/test_cycle_engine.py -v
```

Expected: All tests pass.

- [ ] **Step 3.12: Run the full unit test suite**

```bash
pytest tests/unit/ -v
```

Expected: All tests pass.

- [ ] **Step 3.13: Commit**

```bash
git add src/alpaca_bot/core/engine.py \
        tests/unit/test_cycle_engine.py \
        tests/unit/test_data_source_filters.py \
        tests/unit/test_decision_log.py \
        tests/unit/test_engine_stop_cap.py \
        tests/unit/test_replay_runner_engine_delegation.py
git commit -m "feat: guard new entries against stale daily bars; emit stale_data DecisionRecord"
```

---

## Self-Review

**Spec coverage:**

| Spec requirement | Task covering it |
|----------------|-----------------|
| Sizing rejection silent failure — emit DecisionRecord | Task 1 |
| `quantity_zero` reject_reason | Task 1, Step 1.5 |
| `below_min_notional` reject_reason | Task 1, Step 1.5 |
| `calculate_position_size` signature unchanged | Not a code task — confirmed by inspection; no callers touched |
| Stale daily bar guard at entry | Task 3 |
| `daily_bars_stale` reject_reason, `stale_data` reject_stage | Task 3, Step 3.9 |
| Uses `viability_daily_bar_max_age_days` (not a new Setting) | Task 3, Step 3.9 |
| `_filter_valid_bars` filters `close <= 0` | Task 2 |
| `_filter_valid_bars` does NOT filter zero-volume bars | Task 2, Step 2.4 — only `b.close > 0` predicate |
| Warning logged when bars dropped | Task 2, Step 2.4 — `logger.warning(...)` |
| `import logging` + `logger` added to engine.py | Task 2, Step 2.3 |
| Only `daily_bars` filtered; intraday `bars` not touched | Task 2, Step 2.5 — applied only at the `daily_bars` fetch line |
| No new Settings fields | Confirmed — `viability_daily_bar_max_age_days` already exists |
| No migrations | Confirmed — no DB schema changes |

**Placeholder scan:** No TBD or TODO entries found.

**Type consistency:** `DecisionRecord` fields match `domain/decision_record.py` exactly — `cycle_at`, `symbol`, `strategy_name`, `trading_mode`, `strategy_version`, `decision`, `reject_stage`, `reject_reason`, `entry_level`, `signal_bar_close`, `relative_volume`, `atr`, `stop_price`, `limit_price`, `initial_stop_price`, `quantity`, `risk_per_share`, `equity`, `filter_results`, `vix_close`, `vix_above_sma`, `sector_passing_pct`.

**Spec inconsistency resolved:** The spec's "Error Handling" section says "All soft failures return `(0.0, reason_string)`" — this is a leftover from an earlier design iteration. The actual design keeps `calculate_position_size` returning `float`. No callers (`replay/runner.py`, `test_position_sizing.py`, `test_strategy_rules.py`) are modified. The reject reason is derived from context at the call site in `engine.py`.

**Dead code removed:** Step 3.9's guard code does NOT include an `if daily_bars:` wrapper. At the insertion point, `daily_bars` is guaranteed non-empty by the `if not bars or not daily_bars: continue` guard at line 680.
