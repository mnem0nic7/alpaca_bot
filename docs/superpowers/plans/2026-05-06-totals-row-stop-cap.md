# Totals Row + 5% Stop Cap Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a portfolio totals row to the Open Positions table and enforce a configurable max stop distance (default 5%) for all equity positions in the trading engine.

**Architecture:** Two independent changes: (1) a pure template change that accumulates running totals using Jinja2 `namespace()` and renders a `<tfoot>` row; (2) a two-part engine change in `evaluate_cycle()` — clamp `effective_initial_stop` for new entries before sizing, then run a cap-up pass over existing positions that emits UPDATE_STOP intents.

**Tech Stack:** Python dataclasses (Settings), Jinja2 templates (dashboard), pytest (unit tests).

---

### Task 1: Add `max_stop_pct` to Settings

**Files:**
- Modify: `src/alpaca_bot/config/__init__.py`
- Create: `tests/unit/test_settings_stop_cap.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_settings_stop_cap.py`:

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


def test_max_stop_pct_defaults_to_five_percent():
    s = Settings.from_env(_base())
    assert s.max_stop_pct == pytest.approx(0.05)


def test_max_stop_pct_env_parsed():
    env = {**_base(), "MAX_STOP_PCT": "0.08"}
    s = Settings.from_env(env)
    assert s.max_stop_pct == pytest.approx(0.08)


def test_max_stop_pct_zero_raises():
    env = {**_base(), "MAX_STOP_PCT": "0.0"}
    with pytest.raises(ValueError, match="MAX_STOP_PCT"):
        Settings.from_env(env)


def test_max_stop_pct_above_50_raises():
    env = {**_base(), "MAX_STOP_PCT": "0.51"}
    with pytest.raises(ValueError, match="MAX_STOP_PCT"):
        Settings.from_env(env)


def test_max_stop_pct_exactly_50_is_valid():
    env = {**_base(), "MAX_STOP_PCT": "0.50"}
    s = Settings.from_env(env)
    assert s.max_stop_pct == pytest.approx(0.50)
```

- [ ] **Step 2: Run tests to verify they fail**

```
pytest tests/unit/test_settings_stop_cap.py -v
```

Expected: FAIL — `Settings` has no attribute `max_stop_pct`.

- [ ] **Step 3: Add `max_stop_pct` field to Settings dataclass**

In `src/alpaca_bot/config/__init__.py`, add the field after `enable_options_trading` (line 141):

```python
    enable_options_trading: bool = False
    max_stop_pct: float = 0.05
```

- [ ] **Step 4: Parse `MAX_STOP_PCT` in `from_env()`**

In `from_env()`, add before the closing `)` of the `cls(...)` call (after the `enable_options_trading` line, around line 302):

```python
            enable_options_trading=_parse_bool(
                "ENABLE_OPTIONS_TRADING", values.get("ENABLE_OPTIONS_TRADING", "false")
            ),
            max_stop_pct=float(values.get("MAX_STOP_PCT", "0.05")),
```

- [ ] **Step 5: Add validation in `validate()`**

In `validate()`, add after the `option_delta_target` check (after the `if not 0.0 < self.option_delta_target <= 1.0:` block, around line 446):

```python
        if not 0 < self.max_stop_pct <= 0.50:
            raise ValueError(
                "MAX_STOP_PCT must be between 0 (exclusive) and 0.50 (inclusive)"
            )
```

- [ ] **Step 6: Run tests to verify they pass**

```
pytest tests/unit/test_settings_stop_cap.py -v
```

Expected: All 5 tests PASS.

- [ ] **Step 7: Run full test suite to check for regressions**

```
pytest
```

Expected: All tests pass.

- [ ] **Step 8: Commit**

```bash
git add src/alpaca_bot/config/__init__.py tests/unit/test_settings_stop_cap.py
git commit -m "feat: add MAX_STOP_PCT setting (default 5%) to Settings"
```

---

### Task 2: Cap initial stop for new equity entries

**Files:**
- Modify: `src/alpaca_bot/core/engine.py`
- Create: `tests/unit/test_engine_stop_cap.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_engine_stop_cap.py`:

```python
from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from alpaca_bot.domain import Bar, OpenPosition
from alpaca_bot.core.engine import CycleIntentType, evaluate_cycle


def make_settings(**overrides: str):
    from alpaca_bot.config import Settings

    values = {
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
    values.update(overrides)
    return Settings.from_env(values)


def _now() -> datetime:
    return datetime(2026, 5, 6, 19, 0, tzinfo=timezone.utc)


def _make_signal(
    *,
    symbol: str = "AAPL",
    limit_price: float,
    initial_stop_price: float,
    stop_price: float | None = None,
):
    from alpaca_bot.domain.models import EntrySignal

    bar = Bar(
        symbol=symbol,
        timestamp=_now(),
        open=limit_price - 0.5,
        high=limit_price,
        low=limit_price - 1.0,
        close=limit_price - 0.1,
        volume=100000,
    )
    return EntrySignal(
        symbol=symbol,
        signal_bar=bar,
        entry_level=limit_price - 0.1,
        relative_volume=2.0,
        stop_price=stop_price if stop_price is not None else initial_stop_price,
        limit_price=limit_price,
        initial_stop_price=initial_stop_price,
    )


def test_new_entry_stop_within_cap_is_unchanged():
    """initial_stop_price already within 5% — engine must not alter it."""
    settings = make_settings(MAX_STOP_PCT="0.05")
    limit_price = 100.0
    initial_stop = 97.0  # 3% below entry — within the 5% cap

    def signal_evaluator(*, symbol, intraday_bars, signal_index, daily_bars, settings):
        return _make_signal(
            limit_price=limit_price,
            initial_stop_price=initial_stop,
        )

    result = evaluate_cycle(
        settings=settings,
        now=_now(),
        equity=100_000.0,
        intraday_bars_by_symbol={"AAPL": _make_intraday_bars(limit_price)},
        daily_bars_by_symbol={"AAPL": _make_daily_bars()},
        open_positions=[],
        working_order_symbols=set(),
        traded_symbols_today=set(),
        entries_disabled=False,
        signal_evaluator=signal_evaluator,
    )

    entries = [i for i in result.intents if i.intent_type == CycleIntentType.ENTRY]
    assert len(entries) == 1
    assert entries[0].initial_stop_price == pytest.approx(97.0)


def test_new_entry_stop_beyond_cap_is_raised_to_cap():
    """initial_stop_price 8% below entry — engine must clamp to 5%."""
    settings = make_settings(MAX_STOP_PCT="0.05")
    limit_price = 100.0
    # 8% below entry — exceeds the 5% cap
    initial_stop = 92.0
    intraday_bars = _make_intraday_bars(limit_price)

    def signal_evaluator(*, symbol, intraday_bars, signal_index, daily_bars, settings):
        return _make_signal(
            limit_price=limit_price,
            initial_stop_price=initial_stop,
        )

    result = evaluate_cycle(
        settings=settings,
        now=_now(),
        equity=100_000.0,
        intraday_bars_by_symbol={"AAPL": intraday_bars},
        daily_bars_by_symbol={"AAPL": _make_daily_bars()},
        open_positions=[],
        working_order_symbols=set(),
        traded_symbols_today=set(),
        entries_disabled=False,
        signal_evaluator=signal_evaluator,
    )

    entries = [i for i in result.intents if i.intent_type == CycleIntentType.ENTRY]
    assert len(entries) == 1
    # 5% cap: 100.0 * (1 - 0.05) = 95.0
    assert entries[0].initial_stop_price == pytest.approx(95.0)


def test_new_entry_quantity_reflects_capped_stop():
    """Capped stop reduces risk per share, which increases share count."""
    settings = make_settings(MAX_STOP_PCT="0.05")
    limit_price = 100.0
    initial_stop_uncapped = 80.0  # 20% below entry
    intraday_bars = _make_intraday_bars(limit_price)

    def signal_evaluator(*, symbol, intraday_bars, signal_index, daily_bars, settings):
        return _make_signal(
            limit_price=limit_price,
            initial_stop_price=initial_stop_uncapped,
        )

    result = evaluate_cycle(
        settings=settings,
        now=_now(),
        equity=100_000.0,
        intraday_bars_by_symbol={"AAPL": intraday_bars},
        daily_bars_by_symbol={"AAPL": _make_daily_bars()},
        open_positions=[],
        working_order_symbols=set(),
        traded_symbols_today=set(),
        entries_disabled=False,
        signal_evaluator=signal_evaluator,
    )

    entries = [i for i in result.intents if i.intent_type == CycleIntentType.ENTRY]
    assert len(entries) == 1
    # Effective stop = 95.0; risk/share = 5.0
    # risk_dollars = 100_000 * 0.0025 = 250; qty = 250 / 5.0 = 50
    assert entries[0].initial_stop_price == pytest.approx(95.0)
    assert entries[0].quantity == 50


def _make_intraday_bars(price: float) -> list[Bar]:
    return [
        Bar(
            symbol="AAPL",
            timestamp=_now(),
            open=price - 0.5,
            high=price,
            low=price - 1.0,
            close=price - 0.1,
            volume=100_000,
        )
    ]


def _make_daily_bars() -> list[Bar]:
    bars = []
    base = datetime(2026, 4, 1, 14, 0, tzinfo=timezone.utc)
    for i in range(25):
        bars.append(
            Bar(
                symbol="AAPL",
                timestamp=base.replace(day=i + 1) if i < 28 else base,
                open=100.0,
                high=102.0,
                low=98.0,
                close=101.0,
                volume=1_000_000,
            )
        )
    return bars
```

- [ ] **Step 2: Run tests to verify they fail**

```
pytest tests/unit/test_engine_stop_cap.py -v
```

Expected: FAIL — `initial_stop_price` is not being capped.

- [ ] **Step 3: Add cap logic to the equity entry block in `engine.py`**

In `src/alpaca_bot/core/engine.py`, find the equity entry block (lines 340–376). Replace:

```python
                else:
                    # Equity entry: stop-based sizing
                    if signal.initial_stop_price >= signal.limit_price:
                        continue
                    if signal.limit_price - signal.initial_stop_price < 0.01:
                        continue
                    quantity = calculate_position_size(
                        equity=equity,
                        entry_price=signal.limit_price,
                        stop_price=signal.initial_stop_price,
                        settings=settings,
                    )
                    if quantity < 1:
                        continue
                    entry_candidates.append(
                        (
                            round((signal.signal_bar.close / signal.entry_level) - 1, 6),
                            round(signal.relative_volume, 6),
                            CycleIntent(
                                intent_type=CycleIntentType.ENTRY,
                                symbol=symbol,
                                timestamp=signal.signal_bar.timestamp,
                                quantity=quantity,
                                stop_price=signal.stop_price,
                                limit_price=signal.limit_price,
                                initial_stop_price=signal.initial_stop_price,
                                client_order_id=_client_order_id(
                                    settings=settings,
                                    symbol=symbol,
                                    signal_timestamp=signal.signal_bar.timestamp,
                                    strategy_name=strategy_name,
                                ),
                                signal_timestamp=signal.signal_bar.timestamp,
                                strategy_name=strategy_name,
                            ),
                        )
                    )
```

With:

```python
                else:
                    # Equity entry: stop-based sizing
                    if signal.initial_stop_price >= signal.limit_price:
                        continue
                    if signal.limit_price - signal.initial_stop_price < 0.01:
                        continue
                    cap_stop = round(signal.limit_price * (1 - settings.max_stop_pct), 2)
                    effective_initial_stop = max(signal.initial_stop_price, cap_stop)
                    quantity = calculate_position_size(
                        equity=equity,
                        entry_price=signal.limit_price,
                        stop_price=effective_initial_stop,
                        settings=settings,
                    )
                    if quantity < 1:
                        continue
                    entry_candidates.append(
                        (
                            round((signal.signal_bar.close / signal.entry_level) - 1, 6),
                            round(signal.relative_volume, 6),
                            CycleIntent(
                                intent_type=CycleIntentType.ENTRY,
                                symbol=symbol,
                                timestamp=signal.signal_bar.timestamp,
                                quantity=quantity,
                                stop_price=signal.stop_price,
                                limit_price=signal.limit_price,
                                initial_stop_price=effective_initial_stop,
                                client_order_id=_client_order_id(
                                    settings=settings,
                                    symbol=symbol,
                                    signal_timestamp=signal.signal_bar.timestamp,
                                    strategy_name=strategy_name,
                                ),
                                signal_timestamp=signal.signal_bar.timestamp,
                                strategy_name=strategy_name,
                            ),
                        )
                    )
```

- [ ] **Step 4: Run tests to verify they pass**

```
pytest tests/unit/test_engine_stop_cap.py::test_new_entry_stop_within_cap_is_unchanged tests/unit/test_engine_stop_cap.py::test_new_entry_stop_beyond_cap_is_raised_to_cap tests/unit/test_engine_stop_cap.py::test_new_entry_quantity_reflects_capped_stop -v
```

Expected: All 3 PASS.

- [ ] **Step 5: Run the full suite**

```
pytest
```

Expected: All tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/alpaca_bot/core/engine.py tests/unit/test_engine_stop_cap.py
git commit -m "feat: cap new entry initial_stop_price to MAX_STOP_PCT in evaluate_cycle"
```

---

### Task 3: Cap-up existing positions each cycle

**Files:**
- Modify: `src/alpaca_bot/core/engine.py`
- Modify: `tests/unit/test_engine_stop_cap.py` (add tests)

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_engine_stop_cap.py`:

```python
def _make_position(
    symbol: str = "AAPL",
    entry_price: float = 100.0,
    stop_price: float = 90.0,
    initial_stop_price: float = 90.0,
) -> OpenPosition:
    return OpenPosition(
        symbol=symbol,
        entry_timestamp=datetime(2026, 5, 6, 14, 0, tzinfo=timezone.utc),
        entry_price=entry_price,
        quantity=10,
        entry_level=entry_price - 1.0,
        initial_stop_price=initial_stop_price,
        stop_price=stop_price,
    )


def test_existing_position_stop_beyond_cap_emits_update_stop():
    """Position with stop 10% below entry gets an UPDATE_STOP to the 5% cap level."""
    settings = make_settings(MAX_STOP_PCT="0.05")
    position = _make_position(entry_price=100.0, stop_price=90.0, initial_stop_price=90.0)
    intraday_bars = _make_intraday_bars(101.0)

    result = evaluate_cycle(
        settings=settings,
        now=_now(),
        equity=100_000.0,
        intraday_bars_by_symbol={"AAPL": intraday_bars},
        daily_bars_by_symbol={"AAPL": _make_daily_bars()},
        open_positions=[position],
        working_order_symbols=set(),
        traded_symbols_today=set(),
        entries_disabled=False,
    )

    update_stops = [i for i in result.intents if i.intent_type == CycleIntentType.UPDATE_STOP]
    assert len(update_stops) == 1
    assert update_stops[0].symbol == "AAPL"
    # 5% cap: 100.0 * (1 - 0.05) = 95.0
    assert update_stops[0].stop_price == pytest.approx(95.0)
    assert update_stops[0].reason == "stop_cap_applied"


def test_existing_position_stop_within_cap_no_intent():
    """Position already within 5% cap produces no UPDATE_STOP."""
    settings = make_settings(MAX_STOP_PCT="0.05")
    position = _make_position(entry_price=100.0, stop_price=96.0, initial_stop_price=96.0)
    intraday_bars = _make_intraday_bars(101.0)

    result = evaluate_cycle(
        settings=settings,
        now=_now(),
        equity=100_000.0,
        intraday_bars_by_symbol={"AAPL": intraday_bars},
        daily_bars_by_symbol={"AAPL": _make_daily_bars()},
        open_positions=[position],
        working_order_symbols=set(),
        traded_symbols_today=set(),
        entries_disabled=False,
    )

    cap_updates = [
        i for i in result.intents
        if i.intent_type == CycleIntentType.UPDATE_STOP and i.reason == "stop_cap_applied"
    ]
    assert cap_updates == []


def test_cap_up_skips_position_scheduled_for_exit():
    """Position that gets an EXIT from the viability trend-filter must not also get a cap UPDATE_STOP.

    This exercises the emitted_exit_syms derivation from intents — emitted_exit_symbols in the
    engine is only populated by the past_flatten path; trend-filter exits are not tracked there.
    """
    settings = make_settings(
        MAX_STOP_PCT="0.05",
        ENABLE_TREND_FILTER_EXIT="true",
        DAILY_SMA_PERIOD="5",
    )
    # Stop 10% below entry — would trigger cap — but position will be exited by trend filter.
    position = _make_position(entry_price=100.0, stop_price=90.0)

    # Build daily bars with a downtrend so the trend filter fires: SMA window close > last close
    base = datetime(2026, 5, 1, 14, 0, tzinfo=timezone.utc)
    daily_bars = [
        Bar(symbol="AAPL", timestamp=base.replace(day=i + 1), open=110.0, high=112.0, low=108.0, close=110.0 - i * 2.0, volume=1_000_000)
        for i in range(7)
    ]
    intraday_bars = _make_intraday_bars(101.0)

    result = evaluate_cycle(
        settings=settings,
        now=_now(),
        equity=100_000.0,
        intraday_bars_by_symbol={"AAPL": intraday_bars},
        daily_bars_by_symbol={"AAPL": daily_bars},
        open_positions=[position],
        working_order_symbols=set(),
        traded_symbols_today=set(),
        entries_disabled=False,
    )

    exit_intents = [i for i in result.intents if i.intent_type == CycleIntentType.EXIT]
    cap_updates = [
        i for i in result.intents
        if i.intent_type == CycleIntentType.UPDATE_STOP and i.reason == "stop_cap_applied"
    ]
    assert len(exit_intents) == 1
    assert cap_updates == [], "cap-up must not fire when position already scheduled for exit"


def test_cap_up_does_not_duplicate_trailing_stop_intent():
    """If trailing stop already raised stop above cap level, no second UPDATE_STOP is emitted."""
    settings = make_settings(
        MAX_STOP_PCT="0.05",
        TRAILING_STOP_ATR_MULTIPLIER="0.5",
        TRAILING_STOP_PROFIT_TRIGGER_R="0.1",
    )
    # Entry 100, initial stop 95 (5%), trailing fires and raises stop to 97 (above cap)
    position = _make_position(
        entry_price=100.0,
        stop_price=95.0,
        initial_stop_price=95.0,
    )
    # High of 101 triggers trailing with 0.5 * ATR; let trailing push stop above 95
    latest_bar = Bar(
        symbol="AAPL",
        timestamp=_now(),
        open=100.5,
        high=102.0,  # profit trigger: 100 + 0.1 * (100 - 95) = 100.5; 102 > 100.5
        low=100.0,
        close=101.0,
        volume=50_000,
    )

    result = evaluate_cycle(
        settings=settings,
        now=_now(),
        equity=100_000.0,
        intraday_bars_by_symbol={"AAPL": [latest_bar]},
        daily_bars_by_symbol={"AAPL": _make_daily_bars()},
        open_positions=[position],
        working_order_symbols=set(),
        traded_symbols_today=set(),
        entries_disabled=False,
    )

    update_stops = [i for i in result.intents if i.intent_type == CycleIntentType.UPDATE_STOP]
    # Trailing raises stop; that already satisfies or exceeds cap. Only one UPDATE_STOP.
    assert len(update_stops) == 1
    assert update_stops[0].reason != "stop_cap_applied"
```

- [ ] **Step 2: Run tests to verify they fail**

```
pytest tests/unit/test_engine_stop_cap.py::test_existing_position_stop_beyond_cap_emits_update_stop tests/unit/test_engine_stop_cap.py::test_existing_position_stop_within_cap_no_intent tests/unit/test_engine_stop_cap.py::test_cap_up_skips_position_scheduled_for_exit tests/unit/test_engine_stop_cap.py::test_cap_up_does_not_duplicate_trailing_stop_intent -v
```

Expected: FAIL — no cap-up logic exists yet.

- [ ] **Step 3: Add the cap-up pass to `engine.py`**

In `src/alpaca_bot/core/engine.py`, after the position loop ends (after line 228, before line 230 which starts `# Regime filter:`), insert:

```python
    # Cap-up pass: raise stop to MAX_STOP_PCT cap for any existing position whose stop
    # is more than max_stop_pct below entry. Trailing logic ran first; check emitted
    # UPDATE_STOP intents so we don't emit a duplicate for the same symbol.
    # Derive exit set from intents rather than emitted_exit_symbols — that set is only
    # populated in the past_flatten branch; trend-filter and VWAP exits are not tracked there.
    emitted_exit_syms = {i.symbol for i in intents if i.intent_type == CycleIntentType.EXIT}
    emitted_update_stops: dict[str, float] = {
        i.symbol: (i.stop_price or 0.0)
        for i in intents
        if i.intent_type == CycleIntentType.UPDATE_STOP
    }
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

- [ ] **Step 4: Run tests to verify they pass**

```
pytest tests/unit/test_engine_stop_cap.py -v
```

Expected: All tests PASS.

- [ ] **Step 5: Run the full suite**

```
pytest
```

Expected: All tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/alpaca_bot/core/engine.py tests/unit/test_engine_stop_cap.py
git commit -m "feat: cap-up existing positions to MAX_STOP_PCT via UPDATE_STOP intents"
```

---

### Task 4: Add totals row to Open Positions table

**Files:**
- Modify: `src/alpaca_bot/web/templates/dashboard.html`
- Modify: `tests/unit/test_web_app.py` (add one test)

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_web_app.py`:

```python
def test_dashboard_open_positions_totals_row_rendered() -> None:
    """TOTAL row appears in the Open Positions table when positions exist."""
    now = datetime.now(timezone.utc)
    settings = make_settings()
    connection = FakeConnection(responses=[])

    app = create_app(
        settings=settings,
        connect_postgres_fn=ConnectionFactory([connection]),
        trading_status_store_factory=lambda _connection: SimpleNamespace(
            load=lambda **_kwargs: TradingStatus(
                trading_mode=TradingMode.PAPER,
                strategy_version=settings.strategy_version,
                status=TradingStatusValue.ENABLED,
                kill_switch_enabled=False,
                updated_at=now,
            )
        ),
        daily_session_state_store_factory=lambda _connection: SimpleNamespace(
            load=lambda **_kwargs: DailySessionState(
                session_date=date(2026, 4, 25),
                trading_mode=TradingMode.PAPER,
                strategy_version=settings.strategy_version,
                entries_disabled=False,
                flatten_complete=False,
                last_reconciled_at=now,
                notes="ready",
                updated_at=now,
            )
        ),
        position_store_factory=lambda _connection: SimpleNamespace(
            list_all=lambda **_kwargs: [
                PositionRecord(
                    symbol="AAPL",
                    trading_mode=TradingMode.PAPER,
                    strategy_version=settings.strategy_version,
                    quantity=10,
                    entry_price=100.0,
                    stop_price=96.0,
                    initial_stop_price=96.0,
                    opened_at=now,
                    updated_at=now,
                ),
                PositionRecord(
                    symbol="MSFT",
                    trading_mode=TradingMode.PAPER,
                    strategy_version=settings.strategy_version,
                    quantity=5,
                    entry_price=200.0,
                    stop_price=192.0,
                    initial_stop_price=192.0,
                    opened_at=now,
                    updated_at=now,
                ),
            ]
        ),
        order_store_factory=lambda _connection: SimpleNamespace(
            list_by_status=lambda **_kwargs: [],
            list_recent=lambda **_kwargs: [],
            list_closed_trades=lambda **_kwargs: [],
        ),
        audit_event_store_factory=lambda _connection: SimpleNamespace(
            list_recent=lambda **_kwargs: [],
            load_latest=lambda **_kwargs: SimpleNamespace(
                event_type="supervisor_cycle",
                symbol=None,
                payload={"entries_disabled": False},
                created_at=now,
            ),
            list_by_event_types=lambda **_kwargs: [],
        ),
    )

    with TestClient(app) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert "TOTAL" in response.text
    # Total qty = 10 + 5 = 15
    assert ">15<" in response.text
    # tfoot element is present
    assert "<tfoot>" in response.text


def test_dashboard_no_totals_row_without_positions() -> None:
    """TOTAL row must NOT appear when there are no open positions."""
    now = datetime.now(timezone.utc)
    settings = make_settings()
    connection = FakeConnection(responses=[])

    app = create_app(
        settings=settings,
        connect_postgres_fn=ConnectionFactory([connection]),
        trading_status_store_factory=lambda _connection: SimpleNamespace(
            load=lambda **_kwargs: TradingStatus(
                trading_mode=TradingMode.PAPER,
                strategy_version=settings.strategy_version,
                status=TradingStatusValue.ENABLED,
                kill_switch_enabled=False,
                updated_at=now,
            )
        ),
        daily_session_state_store_factory=lambda _connection: SimpleNamespace(
            load=lambda **_kwargs: DailySessionState(
                session_date=date(2026, 4, 25),
                trading_mode=TradingMode.PAPER,
                strategy_version=settings.strategy_version,
                entries_disabled=False,
                flatten_complete=False,
                last_reconciled_at=now,
                notes="ready",
                updated_at=now,
            )
        ),
        position_store_factory=lambda _connection: SimpleNamespace(
            list_all=lambda **_kwargs: []
        ),
        order_store_factory=lambda _connection: SimpleNamespace(
            list_by_status=lambda **_kwargs: [],
            list_recent=lambda **_kwargs: [],
            list_closed_trades=lambda **_kwargs: [],
        ),
        audit_event_store_factory=lambda _connection: SimpleNamespace(
            list_recent=lambda **_kwargs: [],
            load_latest=lambda **_kwargs: SimpleNamespace(
                event_type="supervisor_cycle",
                symbol=None,
                payload={"entries_disabled": False},
                created_at=now,
            ),
            list_by_event_types=lambda **_kwargs: [],
        ),
    )

    with TestClient(app) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert "TOTAL" not in response.text
    assert "<tfoot>" not in response.text
```

- [ ] **Step 2: Run tests to verify they fail**

```
pytest tests/unit/test_web_app.py::test_dashboard_open_positions_totals_row_rendered tests/unit/test_web_app.py::test_dashboard_no_totals_row_without_positions -v
```

Expected: FAIL — no `<tfoot>` or `TOTAL` in the template yet.

- [ ] **Step 3: Add namespace accumulator and totals row to the template**

In `src/alpaca_bot/web/templates/dashboard.html`, replace the Open Positions `<tbody>` block (lines 373–426) with the version below. The key changes are: (a) `{% set ns = namespace(...) %}` before the loop, (b) accumulation statements after each `</tr>`, (c) `<tfoot>` block after `</tbody>`.

Replace:
```html
              <tbody>
                {% for position in snapshot.positions %}
                  {% set stop_dist_pct = ((position.entry_price - position.stop_price) / position.entry_price * 100) if (position.entry_price and position.stop_price) else none %}
                  {% set is_trailing = (position.stop_price and position.initial_stop_price and position.stop_price > position.initial_stop_price) %}
                  {% set stop_moved = (position.stop_price - position.initial_stop_price) if (position.stop_price and position.initial_stop_price) else none %}
                  {% set risk_dollars = (position.quantity * (position.entry_price - position.initial_stop_price)) if (position.quantity and position.entry_price and position.initial_stop_price) else none %}
                  {% set last_price = snapshot.latest_prices.get(position.symbol) %}
                  <tr>
                    <td class="mono">{{ position.symbol }}</td>
                    <td>{{ position.strategy_name }}</td>
                    <td>{{ position.quantity }}</td>
                    <td>{{ format_price(position.entry_price) }}</td>
                    <td class="muted">{{ format_price(position.initial_stop_price) }}</td>
                    <td>{{ format_price(position.stop_price) }}</td>
                    <td class="muted">
                      {% if stop_dist_pct is not none %}{{ "%.2f" | format(stop_dist_pct) }}%{% else %}n/a{% endif %}
                    </td>
                    <td>
                      {% if is_trailing and stop_moved is not none %}
                        <span style="color: var(--accent);">+{{ format_price(stop_moved) }}</span>
                      {% else %}
                        <span class="muted">—</span>
                      {% endif %}
                    </td>
                    <td>
                      {% if risk_dollars is not none %}{{ "$%.0f" | format(risk_dollars) }}{% else %}n/a{% endif %}
                    </td>
                    <td>{{ format_timestamp(position.opened_at) }}</td>
                    <td class="muted">{{ format_timestamp(position.updated_at) }}</td>
                    <td>{{ format_price(last_price) if last_price is not none else "—" }}</td>
                    <td>
                      {% if position.entry_price %}{{ "$%.0f" | format(position.entry_price * position.quantity) }}{% else %}n/a{% endif %}
                    </td>
                    <td>
                      {% if last_price is not none %}{{ "$%.0f" | format(last_price * position.quantity) }}{% else %}—{% endif %}
                    </td>
                    {% if last_price is not none and position.entry_price %}
                      {% set upnl = (last_price - position.entry_price) * position.quantity %}
                      {% set upnl_pct = (last_price - position.entry_price) / position.entry_price * 100 %}
                      <td style="color: {{ 'var(--accent)' if upnl >= 0 else '#c0392b' }}">
                        {% if upnl >= 0 %}+{{ format_price(upnl) }}{% else %}-{{ format_price(0 - upnl) }}{% endif %}
                      </td>
                      <td style="color: {{ 'var(--accent)' if upnl_pct >= 0 else '#c0392b' }}">
                        {% if upnl_pct >= 0 %}+{{ "%.2f" | format(upnl_pct) }}%{% else %}{{ "%.2f" | format(upnl_pct) }}%{% endif %}
                      </td>
                    {% else %}
                      <td class="muted">—</td>
                      <td class="muted">—</td>
                    {% endif %}
                  </tr>
                {% else %}
                  <tr><td colspan="16" class="muted">No open positions.</td></tr>
                {% endfor %}
              </tbody>
```

With:
```html
              <tbody>
                {% set ns = namespace(
                    total_qty=0,
                    total_risk=0.0,
                    total_init_val=0.0,
                    total_curr_val=0.0,
                    total_upnl=0.0,
                    weighted_stop_num=0.0,
                    stop_denom=0.0,
                    weighted_upnl_num=0.0,
                    upnl_denom=0.0
                ) %}
                {% for position in snapshot.positions %}
                  {% set stop_dist_pct = ((position.entry_price - position.stop_price) / position.entry_price * 100) if (position.entry_price and position.stop_price) else none %}
                  {% set is_trailing = (position.stop_price and position.initial_stop_price and position.stop_price > position.initial_stop_price) %}
                  {% set stop_moved = (position.stop_price - position.initial_stop_price) if (position.stop_price and position.initial_stop_price) else none %}
                  {% set risk_dollars = (position.quantity * (position.entry_price - position.initial_stop_price)) if (position.quantity and position.entry_price and position.initial_stop_price) else none %}
                  {% set last_price = snapshot.latest_prices.get(position.symbol) %}
                  {% set init_val = (position.entry_price * position.quantity) if position.entry_price else 0.0 %}
                  <tr>
                    <td class="mono">{{ position.symbol }}</td>
                    <td>{{ position.strategy_name }}</td>
                    <td>{{ position.quantity }}</td>
                    <td>{{ format_price(position.entry_price) }}</td>
                    <td class="muted">{{ format_price(position.initial_stop_price) }}</td>
                    <td>{{ format_price(position.stop_price) }}</td>
                    <td class="muted">
                      {% if stop_dist_pct is not none %}{{ "%.2f" | format(stop_dist_pct) }}%{% else %}n/a{% endif %}
                    </td>
                    <td>
                      {% if is_trailing and stop_moved is not none %}
                        <span style="color: var(--accent);">+{{ format_price(stop_moved) }}</span>
                      {% else %}
                        <span class="muted">—</span>
                      {% endif %}
                    </td>
                    <td>
                      {% if risk_dollars is not none %}{{ "$%.0f" | format(risk_dollars) }}{% else %}n/a{% endif %}
                    </td>
                    <td>{{ format_timestamp(position.opened_at) }}</td>
                    <td class="muted">{{ format_timestamp(position.updated_at) }}</td>
                    <td>{{ format_price(last_price) if last_price is not none else "—" }}</td>
                    <td>
                      {% if position.entry_price %}{{ "$%.0f" | format(position.entry_price * position.quantity) }}{% else %}n/a{% endif %}
                    </td>
                    <td>
                      {% if last_price is not none %}{{ "$%.0f" | format(last_price * position.quantity) }}{% else %}—{% endif %}
                    </td>
                    {% if last_price is not none and position.entry_price %}
                      {% set upnl = (last_price - position.entry_price) * position.quantity %}
                      {% set upnl_pct = (last_price - position.entry_price) / position.entry_price * 100 %}
                      <td style="color: {{ 'var(--accent)' if upnl >= 0 else '#c0392b' }}">
                        {% if upnl >= 0 %}+{{ format_price(upnl) }}{% else %}-{{ format_price(0 - upnl) }}{% endif %}
                      </td>
                      <td style="color: {{ 'var(--accent)' if upnl_pct >= 0 else '#c0392b' }}">
                        {% if upnl_pct >= 0 %}+{{ "%.2f" | format(upnl_pct) }}%{% else %}{{ "%.2f" | format(upnl_pct) }}%{% endif %}
                      </td>
                    {% else %}
                      <td class="muted">—</td>
                      <td class="muted">—</td>
                    {% endif %}
                  </tr>
                  {%- set ns.total_qty = ns.total_qty + (position.quantity or 0) %}
                  {%- if risk_dollars is not none %}{% set ns.total_risk = ns.total_risk + risk_dollars %}{% endif %}
                  {%- set ns.total_init_val = ns.total_init_val + init_val %}
                  {%- if last_price is not none %}{% set ns.total_curr_val = ns.total_curr_val + last_price * position.quantity %}{% endif %}
                  {%- if last_price is not none and position.entry_price %}
                    {%- set upnl_acc = (last_price - position.entry_price) * position.quantity %}
                    {%- set upnl_pct_acc = (last_price - position.entry_price) / position.entry_price * 100 %}
                    {%- set ns.total_upnl = ns.total_upnl + upnl_acc %}
                    {%- set ns.weighted_upnl_num = ns.weighted_upnl_num + upnl_pct_acc * init_val %}
                    {%- set ns.upnl_denom = ns.upnl_denom + init_val %}
                  {%- endif %}
                  {%- if stop_dist_pct is not none and init_val > 0 %}
                    {%- set ns.weighted_stop_num = ns.weighted_stop_num + stop_dist_pct * init_val %}
                    {%- set ns.stop_denom = ns.stop_denom + init_val %}
                  {%- endif %}
                {% else %}
                  <tr><td colspan="16" class="muted">No open positions.</td></tr>
                {% endfor %}
              </tbody>
              {% if snapshot.positions %}
              <tfoot>
                <tr>
                  <td><strong>TOTAL</strong></td>
                  <td>—</td>
                  <td>{{ ns.total_qty }}</td>
                  <td>—</td>
                  <td class="muted">—</td>
                  <td>—</td>
                  <td class="muted">
                    {% if ns.stop_denom > 0 %}{{ "%.2f" | format(ns.weighted_stop_num / ns.stop_denom) }}%{% else %}—{% endif %}
                  </td>
                  <td>—</td>
                  <td>{{ "$%.0f" | format(ns.total_risk) }}</td>
                  <td>—</td>
                  <td class="muted">—</td>
                  <td>—</td>
                  <td>{{ "$%.0f" | format(ns.total_init_val) }}</td>
                  <td>{{ "$%.0f" | format(ns.total_curr_val) }}</td>
                  <td style="color: {{ 'var(--accent)' if ns.total_upnl >= 0 else '#c0392b' }}">
                    {% if ns.total_upnl >= 0 %}+{{ format_price(ns.total_upnl) }}{% else %}-{{ format_price(0 - ns.total_upnl) }}{% endif %}
                  </td>
                  <td>
                    {% if ns.upnl_denom > 0 %}
                      {% set avg_upnl_pct = ns.weighted_upnl_num / ns.upnl_denom %}
                      <span style="color: {{ 'var(--accent)' if avg_upnl_pct >= 0 else '#c0392b' }}">
                        {% if avg_upnl_pct >= 0 %}+{{ "%.2f" | format(avg_upnl_pct) }}%{% else %}{{ "%.2f" | format(avg_upnl_pct) }}%{% endif %}
                      </span>
                    {% else %}—{% endif %}
                  </td>
                </tr>
              </tfoot>
              {% endif %}
```

- [ ] **Step 4: Run tests to verify they pass**

```
pytest tests/unit/test_web_app.py::test_dashboard_open_positions_totals_row_rendered tests/unit/test_web_app.py::test_dashboard_no_totals_row_without_positions -v
```

Expected: Both PASS.

- [ ] **Step 5: Run the full test suite**

```
pytest
```

Expected: All tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/alpaca_bot/web/templates/dashboard.html tests/unit/test_web_app.py
git commit -m "feat: add portfolio totals row to Open Positions table"
```
