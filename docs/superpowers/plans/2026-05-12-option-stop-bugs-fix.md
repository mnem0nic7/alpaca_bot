# Option Stop Bugs Fix — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix three production bugs that caused 534 stop-dispatch failures for option positions today: (1) stop price = entry price due to rounding, (2) infinite re-queue loop on unrecoverable broker errors, (3) strategy_name always 'breakout' for option positions.

**Architecture:** OCC symbol detection (`_is_option_symbol`) gates two startup_recovery paths — a wider stop buffer for broker-missing options (Bug 1) and a no-price skip guard for active options (Bug 2). A same-price guard in the re-queue logic (Bug 2) prevents re-submitting a stop Alpaca already rejected. Dispatch marks the rejected stop `canceled` (not `error`) and emits a dedicated audit event. Bug 3 is fixed by using `"option"` as `strategy_name` for broker-missing OCC positions.

**Tech Stack:** Python, pytest, existing DI fake-callables pattern (no mocks).

---

## File Map

| File | Change |
|---|---|
| `src/alpaca_bot/config/__init__.py` | Add `option_stop_buffer_pct: float` field + `from_env()` parsing |
| `src/alpaca_bot/runtime/startup_recovery.py` | Add `_is_option_symbol()`, update broker-missing path (Bug 1, Bug 3), update active-positions path (Bug 1, Bug 2) |
| `src/alpaca_bot/runtime/order_dispatch.py` | Add `_is_unrecoverable_stop_error()`, save `canceled` not `error` for 42210000 stops |
| `tests/unit/test_startup_recovery.py` | 8 new tests |
| `tests/unit/test_order_dispatch.py` | 2 new tests |

---

## Task 1: Add `option_stop_buffer_pct` to Settings

**Files:**
- Modify: `src/alpaca_bot/config/__init__.py:149-150` (field declaration, after `max_stop_pct`)
- Modify: `src/alpaca_bot/config/__init__.py:350-353` (from_env, after `option_chain_min_total_volume`)

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/test_startup_recovery.py` (the `make_settings()` function already exists at line 23; a separate test verifies the new field parses from env):

```python
def test_option_stop_buffer_pct_parses_from_env() -> None:
    settings = Settings.from_env(
        {
            "TRADING_MODE": "paper",
            "ENABLE_LIVE_TRADING": "false",
            "STRATEGY_VERSION": "v1-breakout",
            "DATABASE_URL": "postgresql://alpaca_bot:secret@db.example.com:5432/alpaca_bot",
            "MARKET_DATA_FEED": "sip",
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
            "OPTION_STOP_BUFFER_PCT": "0.12",
        }
    )
    assert settings.option_stop_buffer_pct == 0.12


def test_option_stop_buffer_pct_defaults_to_ten_percent() -> None:
    settings = make_settings()  # make_settings() does not set OPTION_STOP_BUFFER_PCT
    assert settings.option_stop_buffer_pct == 0.10
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/unit/test_startup_recovery.py::test_option_stop_buffer_pct_parses_from_env tests/unit/test_startup_recovery.py::test_option_stop_buffer_pct_defaults_to_ten_percent -v
```

Expected: FAIL with `AttributeError: 'Settings' object has no attribute 'option_stop_buffer_pct'` or similar.

- [ ] **Step 3: Add field to Settings dataclass**

In `src/alpaca_bot/config/__init__.py`, add after line 150 (`option_chain_min_total_volume: int = 0`):

```python
    option_stop_buffer_pct: float = 0.10
```

- [ ] **Step 4: Add from_env() parsing**

In `src/alpaca_bot/config/__init__.py`, after line 352 (`option_chain_min_total_volume=int(...)`):

```python
            option_stop_buffer_pct=float(
                values.get("OPTION_STOP_BUFFER_PCT", "0.10")
            ),
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
pytest tests/unit/test_startup_recovery.py::test_option_stop_buffer_pct_parses_from_env tests/unit/test_startup_recovery.py::test_option_stop_buffer_pct_defaults_to_ten_percent -v
```

Expected: PASS.

- [ ] **Step 6: Run full test suite to check for regressions**

```bash
pytest tests/unit/test_startup_recovery.py -v
```

Expected: All existing tests still pass.

- [ ] **Step 7: Commit**

```bash
git add src/alpaca_bot/config/__init__.py tests/unit/test_startup_recovery.py
git commit -m "feat: add option_stop_buffer_pct setting (default 10%) for option stop price computation"
```

---

## Task 2: Add `_is_option_symbol()` helper to startup_recovery

**Files:**
- Modify: `src/alpaca_bot/runtime/startup_recovery.py` (add helper near top of file, before `recover_startup_state`)

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/test_startup_recovery.py`:

```python
def test_is_option_symbol_detects_occ_format() -> None:
    from alpaca_bot.runtime.startup_recovery import _is_option_symbol
    # OCC format: TICKER + YYMMDD + P/C + 8-digit strike
    assert _is_option_symbol("ALHC260618P00017500") is True
    assert _is_option_symbol("AMLX260619C00050000") is True
    assert _is_option_symbol("SPY260620P00500000") is True   # short ticker
    assert _is_option_symbol("AAPL260620C00150000") is True
    # Equity tickers
    assert _is_option_symbol("AAPL") is False
    assert _is_option_symbol("SPY") is False
    assert _is_option_symbol("RMBS") is False
    assert _is_option_symbol("") is False
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/unit/test_startup_recovery.py::test_is_option_symbol_detects_occ_format -v
```

Expected: FAIL with `ImportError: cannot import name '_is_option_symbol'`.

- [ ] **Step 3: Add `_is_option_symbol` to startup_recovery.py**

Find the first module-level section of `src/alpaca_bot/runtime/startup_recovery.py` (after imports, before `recover_startup_state`). Add:

```python
import re as _re

_OCC_PATTERN = _re.compile(r'^[A-Z]{1,6}\d{6}[CP]\d{8}$')


def _is_option_symbol(symbol: str) -> bool:
    return bool(_OCC_PATTERN.match(symbol))
```

Note: check if `re` is already imported in startup_recovery.py. If `import re` already exists, do not add it again — just add `_OCC_PATTERN` and `_is_option_symbol` using `re`.

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/unit/test_startup_recovery.py::test_is_option_symbol_detects_occ_format -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/alpaca_bot/runtime/startup_recovery.py tests/unit/test_startup_recovery.py
git commit -m "feat: add _is_option_symbol() OCC format detector to startup_recovery"
```

---

## Task 3: Fix Bug 1 — Use option buffer for OCC broker-missing positions (+ Bug 3: strategy_name)

**Files:**
- Modify: `src/alpaca_bot/runtime/startup_recovery.py:131-159` (broker-missing path inside `recover_startup_state`)

The relevant code block to change is inside the `if not local_for_symbol:` branch:

Current (lines 133–158):
```python
resolved_entry_price = broker_position.entry_price
if resolved_entry_price is not None and resolved_entry_price != 0.0:
    stop_price = round(resolved_entry_price * (1 - settings.breakout_stop_buffer_pct), 2)
    initial_stop_price = stop_price
else:
    stop_price = 0.0
    initial_stop_price = 0.0
    mismatches.append(f"missing entry price at startup: {broker_position.symbol}")
    missing_entry_price_symbols.append(broker_position.symbol)
synced_positions.append(
    PositionRecord(
        ...
        strategy_name=default_strategy_name,
        ...
    )
)
if stop_price > 0.0:
    new_positions_needing_stop.append(
        (broker_position.symbol, broker_position.quantity, stop_price, default_strategy_name)
    )
```

- [ ] **Step 1: Write the failing tests**

Add to `tests/unit/test_startup_recovery.py`:

```python
def test_option_stop_uses_option_buffer_for_broker_missing_position() -> None:
    """OCC broker-missing position must use option_stop_buffer_pct, not breakout_stop_buffer_pct."""
    from alpaca_bot.runtime.startup_recovery import recover_startup_state
    settings = Settings.from_env(
        {
            "TRADING_MODE": "paper",
            "ENABLE_LIVE_TRADING": "false",
            "STRATEGY_VERSION": "v1-breakout",
            "DATABASE_URL": "postgresql://alpaca_bot:secret@db.example.com:5432/alpaca_bot",
            "MARKET_DATA_FEED": "sip",
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
            "OPTION_STOP_BUFFER_PCT": "0.10",
        }
    )
    occ_symbol = "ALHC260618P00017500"
    entry_price = 1.20
    runtime = make_runtime_context(positions=[], orders=[])
    report = recover_startup_state(
        settings=settings,
        runtime=runtime,
        broker_open_positions=[
            BrokerPosition(
                symbol=occ_symbol,
                quantity=5,
                entry_price=entry_price,
                market_value=None,
            )
        ],
        broker_open_orders=[],
        now=datetime(2026, 5, 12, 14, 0, tzinfo=timezone.utc),
        audit_event_type=None,
    )
    synced = runtime.position_store.replace_all_calls[0]["positions"]
    assert len(synced) == 1
    expected_stop = round(entry_price * (1 - 0.10), 2)  # = 1.08
    assert synced[0].stop_price == expected_stop, (
        f"Expected option stop={expected_stop}, got {synced[0].stop_price}"
    )
    # Stop must be strictly below entry after rounding
    assert synced[0].stop_price < entry_price


def test_option_strategy_name_is_option_for_broker_missing_occ_position() -> None:
    """Broker-missing OCC position must get strategy_name='option', not 'breakout'."""
    from alpaca_bot.runtime.startup_recovery import recover_startup_state
    settings = make_settings()
    occ_symbol = "ALHC260618P00017500"
    runtime = make_runtime_context(positions=[], orders=[])
    recover_startup_state(
        settings=settings,
        runtime=runtime,
        broker_open_positions=[
            BrokerPosition(
                symbol=occ_symbol,
                quantity=5,
                entry_price=1.20,
                market_value=None,
            )
        ],
        broker_open_orders=[],
        now=datetime(2026, 5, 12, 14, 0, tzinfo=timezone.utc),
        audit_event_type=None,
    )
    synced = runtime.position_store.replace_all_calls[0]["positions"]
    assert synced[0].strategy_name == "option"


def test_equity_strategy_name_unchanged_for_broker_missing_equity_position() -> None:
    """Broker-missing equity position must still get strategy_name='breakout' (default)."""
    from alpaca_bot.runtime.startup_recovery import recover_startup_state
    settings = make_settings()
    runtime = make_runtime_context(positions=[], orders=[])
    recover_startup_state(
        settings=settings,
        runtime=runtime,
        broker_open_positions=[
            BrokerPosition(
                symbol="AAPL",
                quantity=10,
                entry_price=189.25,
                market_value=None,
            )
        ],
        broker_open_orders=[],
        now=datetime(2026, 5, 12, 14, 0, tzinfo=timezone.utc),
        audit_event_type=None,
    )
    synced = runtime.position_store.replace_all_calls[0]["positions"]
    assert synced[0].strategy_name == "breakout"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/unit/test_startup_recovery.py::test_option_stop_uses_option_buffer_for_broker_missing_position tests/unit/test_startup_recovery.py::test_option_strategy_name_is_option_for_broker_missing_occ_position tests/unit/test_startup_recovery.py::test_equity_strategy_name_unchanged_for_broker_missing_equity_position -v
```

Expected: FAIL (stop uses breakout buffer, strategy_name is 'breakout' for OCC).

- [ ] **Step 3: Update broker-missing path in `recover_startup_state`**

In `src/alpaca_bot/runtime/startup_recovery.py`, locate the `if not local_for_symbol:` branch (around line 131). Replace:

```python
            resolved_entry_price = broker_position.entry_price
            if resolved_entry_price is not None and resolved_entry_price != 0.0:
                stop_price = round(resolved_entry_price * (1 - settings.breakout_stop_buffer_pct), 2)
                initial_stop_price = stop_price
            else:
                stop_price = 0.0
                initial_stop_price = 0.0
                mismatches.append(f"missing entry price at startup: {broker_position.symbol}")
                missing_entry_price_symbols.append(broker_position.symbol)
            synced_positions.append(
                PositionRecord(
                    symbol=broker_position.symbol,
                    trading_mode=settings.trading_mode,
                    strategy_version=settings.strategy_version,
                    strategy_name=default_strategy_name,
                    quantity=broker_position.quantity,
                    entry_price=broker_position.entry_price if broker_position.entry_price is not None else 0.0,
                    stop_price=stop_price,
                    initial_stop_price=initial_stop_price,
                    opened_at=timestamp,
                    updated_at=timestamp,
                )
            )
            if stop_price > 0.0:
                new_positions_needing_stop.append(
                    (broker_position.symbol, broker_position.quantity, stop_price, default_strategy_name)
                )
```

With:

```python
            is_option = _is_option_symbol(broker_position.symbol)
            resolved_strategy_name = "option" if is_option else default_strategy_name
            resolved_entry_price = broker_position.entry_price
            if resolved_entry_price is not None and resolved_entry_price != 0.0:
                buffer_pct = settings.option_stop_buffer_pct if is_option else settings.breakout_stop_buffer_pct
                stop_price = round(resolved_entry_price * (1 - buffer_pct), 2)
                # Ensure rounding did not collapse the buffer (e.g., $0.01 entry with 10% buffer)
                if stop_price >= resolved_entry_price:
                    stop_price = 0.0
                initial_stop_price = stop_price
            else:
                stop_price = 0.0
                initial_stop_price = 0.0
                mismatches.append(f"missing entry price at startup: {broker_position.symbol}")
                missing_entry_price_symbols.append(broker_position.symbol)
            synced_positions.append(
                PositionRecord(
                    symbol=broker_position.symbol,
                    trading_mode=settings.trading_mode,
                    strategy_version=settings.strategy_version,
                    strategy_name=resolved_strategy_name,
                    quantity=broker_position.quantity,
                    entry_price=broker_position.entry_price if broker_position.entry_price is not None else 0.0,
                    stop_price=stop_price,
                    initial_stop_price=initial_stop_price,
                    opened_at=timestamp,
                    updated_at=timestamp,
                )
            )
            if stop_price > 0.0:
                new_positions_needing_stop.append(
                    (broker_position.symbol, broker_position.quantity, stop_price, resolved_strategy_name)
                )
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/unit/test_startup_recovery.py::test_option_stop_uses_option_buffer_for_broker_missing_position tests/unit/test_startup_recovery.py::test_option_strategy_name_is_option_for_broker_missing_occ_position tests/unit/test_startup_recovery.py::test_equity_strategy_name_unchanged_for_broker_missing_equity_position -v
```

Expected: PASS.

- [ ] **Step 5: Run full startup_recovery tests**

```bash
pytest tests/unit/test_startup_recovery.py -v
```

Expected: All tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/alpaca_bot/runtime/startup_recovery.py tests/unit/test_startup_recovery.py
git commit -m "fix: use option_stop_buffer_pct for OCC broker-missing positions; set strategy_name='option'"
```

---

## Task 4: Fix Bug 2 — Skip stop queueing for options with no price; add same-price re-queue guard

**Files:**
- Modify: `src/alpaca_bot/runtime/startup_recovery.py:387-491` (active positions stop-queueing path)

The relevant section starts at the line:
```python
broker_pos = broker_positions_by_symbol.get(pos.symbol)
current_price: float | None = None
if broker_pos and broker_pos.market_value is not None and broker_pos.quantity > 0:
    current_price = broker_pos.market_value / broker_pos.quantity
```

And continues through the re-queue guard and stop ordering at lines ~444–491.

- [ ] **Step 1: Write the failing tests**

Add to `tests/unit/test_startup_recovery.py`:

```python
def test_option_stop_skipped_when_no_current_price() -> None:
    """Active OCC position with current_price=None must not re-queue a stop.
    Should emit option_stop_skipped_no_price audit event instead."""
    from alpaca_bot.runtime.startup_recovery import recover_startup_state
    settings = make_settings()
    occ_symbol = "ALHC260618P00017500"
    existing_pos = PositionRecord(
        symbol=occ_symbol,
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        strategy_name="option",
        quantity=5,
        entry_price=1.20,
        stop_price=1.08,
        initial_stop_price=1.08,
        opened_at=datetime(2026, 5, 12, 10, 0, tzinfo=timezone.utc),
        updated_at=datetime(2026, 5, 12, 10, 0, tzinfo=timezone.utc),
    )
    runtime = make_runtime_context(positions=[existing_pos], orders=[])
    recover_startup_state(
        settings=settings,
        runtime=runtime,
        # market_value=None simulates Alpaca paper option with no price
        broker_open_positions=[
            BrokerPosition(symbol=occ_symbol, quantity=5, entry_price=1.20, market_value=None)
        ],
        broker_open_orders=[],
        now=datetime(2026, 5, 12, 14, 0, tzinfo=timezone.utc),
        audit_event_type=None,
    )
    # No stop order should have been queued
    stop_orders = [o for o in runtime.order_store.saved if o.intent_type == "stop"]
    assert stop_orders == [], f"Expected no stop orders queued, got: {stop_orders}"
    # Audit event must be emitted
    skip_events = [
        e for e in runtime.audit_event_store.appended
        if e.event_type == "option_stop_skipped_no_price"
    ]
    assert len(skip_events) == 1
    assert skip_events[0].symbol == occ_symbol


def test_equity_stop_still_queued_when_no_current_price() -> None:
    """Equity position with current_price=None must still queue a recovery stop (existing behavior)."""
    from alpaca_bot.runtime.startup_recovery import recover_startup_state
    settings = make_settings()
    existing_pos = PositionRecord(
        symbol="AAPL",
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        strategy_name="breakout",
        quantity=10,
        entry_price=189.25,
        stop_price=round(189.25 * (1 - settings.breakout_stop_buffer_pct), 2),
        initial_stop_price=round(189.25 * (1 - settings.breakout_stop_buffer_pct), 2),
        opened_at=datetime(2026, 5, 12, 10, 0, tzinfo=timezone.utc),
        updated_at=datetime(2026, 5, 12, 10, 0, tzinfo=timezone.utc),
    )
    runtime = make_runtime_context(positions=[existing_pos], orders=[])
    recover_startup_state(
        settings=settings,
        runtime=runtime,
        broker_open_positions=[
            BrokerPosition(symbol="AAPL", quantity=10, entry_price=189.25, market_value=None)
        ],
        broker_open_orders=[],
        now=datetime(2026, 5, 12, 14, 0, tzinfo=timezone.utc),
        audit_event_type=None,
    )
    stop_orders = [o for o in runtime.order_store.saved if o.intent_type == "stop"]
    assert len(stop_orders) == 1, "Equity position must have a recovery stop queued"
    assert stop_orders[0].symbol == "AAPL"


def test_recovery_stop_not_requeued_when_same_price_terminal() -> None:
    """A terminal stop (error/canceled) with the same stop_price must not be re-queued.
    This prevents the infinite loop when Alpaca rejects 42210000."""
    from alpaca_bot.runtime.startup_recovery import recover_startup_state
    settings = make_settings()
    occ_symbol = "ALHC260618P00017500"
    existing_pos = PositionRecord(
        symbol=occ_symbol,
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        strategy_name="option",
        quantity=5,
        entry_price=1.20,
        stop_price=1.20,  # stop = entry (the bug condition)
        initial_stop_price=1.20,
        opened_at=datetime(2026, 5, 12, 10, 0, tzinfo=timezone.utc),
        updated_at=datetime(2026, 5, 12, 10, 0, tzinfo=timezone.utc),
    )
    # Simulate an existing error-status stop with the same price
    failed_stop = OrderRecord(
        client_order_id="startup_recovery:v1-breakout:2026-05-12:ALHC260618P00017500:stop",
        symbol=occ_symbol,
        side="sell",
        intent_type="stop",
        status="error",
        quantity=5,
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        created_at=datetime(2026, 5, 12, 13, 0, tzinfo=timezone.utc),
        updated_at=datetime(2026, 5, 12, 13, 30, tzinfo=timezone.utc),
        stop_price=1.20,  # same as pos.stop_price
        initial_stop_price=1.20,
    )
    runtime = make_runtime_context(positions=[existing_pos], orders=[failed_stop])
    recover_startup_state(
        settings=settings,
        runtime=runtime,
        broker_open_positions=[
            BrokerPosition(symbol=occ_symbol, quantity=5, entry_price=1.20, market_value=5.50)
        ],
        broker_open_orders=[],
        now=datetime(2026, 5, 12, 14, 0, tzinfo=timezone.utc),
        audit_event_type=None,
    )
    new_stops = [
        o for o in runtime.order_store.saved
        if o.intent_type == "stop" and o.status == "pending_submit"
    ]
    assert new_stops == [], f"Must not re-queue stop with same price. Got: {new_stops}"


def test_recovery_stop_requeued_when_price_changed_after_terminal() -> None:
    """A terminal stop with a DIFFERENT stop_price must be re-queued (price was updated)."""
    from alpaca_bot.runtime.startup_recovery import recover_startup_state
    settings = make_settings()
    # Use an equity symbol to sidestep the option-no-price skip guard
    existing_pos = PositionRecord(
        symbol="AAPL",
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        strategy_name="breakout",
        quantity=10,
        entry_price=189.25,
        stop_price=190.00,  # updated stop (higher than the old failed stop)
        initial_stop_price=188.00,
        opened_at=datetime(2026, 5, 12, 10, 0, tzinfo=timezone.utc),
        updated_at=datetime(2026, 5, 12, 13, 0, tzinfo=timezone.utc),
    )
    failed_stop = OrderRecord(
        client_order_id="startup_recovery:v1-breakout:2026-05-12:AAPL:stop",
        symbol="AAPL",
        side="sell",
        intent_type="stop",
        status="canceled",
        quantity=10,
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        created_at=datetime(2026, 5, 12, 10, 0, tzinfo=timezone.utc),
        updated_at=datetime(2026, 5, 12, 12, 0, tzinfo=timezone.utc),
        stop_price=185.00,  # old price — different from 190.00
        initial_stop_price=185.00,
    )
    runtime = make_runtime_context(positions=[existing_pos], orders=[failed_stop])
    recover_startup_state(
        settings=settings,
        runtime=runtime,
        broker_open_positions=[
            BrokerPosition(symbol="AAPL", quantity=10, entry_price=189.25, market_value=1900.0)
        ],
        broker_open_orders=[],
        now=datetime(2026, 5, 12, 14, 0, tzinfo=timezone.utc),
        audit_event_type=None,
    )
    new_stops = [
        o for o in runtime.order_store.saved
        if o.intent_type == "stop" and o.status == "pending_submit"
    ]
    assert len(new_stops) == 1, f"Must re-queue stop when price changed. Got: {new_stops}"
    assert new_stops[0].stop_price == 190.00
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/unit/test_startup_recovery.py::test_option_stop_skipped_when_no_current_price tests/unit/test_startup_recovery.py::test_equity_stop_still_queued_when_no_current_price tests/unit/test_startup_recovery.py::test_recovery_stop_not_requeued_when_same_price_terminal tests/unit/test_startup_recovery.py::test_recovery_stop_requeued_when_price_changed_after_terminal -v
```

Expected: FAIL.

- [ ] **Step 3: Update active-positions path in `recover_startup_state`**

In `src/alpaca_bot/runtime/startup_recovery.py`, locate the else-branch of the `if current_price is not None and pos.stop_price >= current_price:` block (around line 439). This entire else-block handles stop queueing for open positions.

**First**, insert an early-return for OCC symbols with no price, BEFORE the existing recovery stop ID / re-queue guard. Replace the start of the else-block:

```python
            else:
                recovery_stop_id = (
```

With:

```python
            else:
                if _is_option_symbol(pos.symbol) and current_price is None:
                    _log.info(
                        "startup_recovery: skipping recovery stop for option %s — no current price available",
                        pos.symbol,
                    )
                    runtime.audit_event_store.append(
                        AuditEvent(
                            event_type="option_stop_skipped_no_price",
                            symbol=pos.symbol,
                            payload={"symbol": pos.symbol, "stop_price": pos.stop_price},
                            created_at=timestamp,
                        ),
                        commit=False,
                    )
                    continue
                recovery_stop_id = (
```

**Second**, update the re-queue guard (around lines 447–451). Replace:

```python
                existing_recovery_stop = runtime.order_store.load(recovery_stop_id)
                if existing_recovery_stop is not None and existing_recovery_stop.status not in {
                    "expired", "cancelled", "canceled", "error"
                }:
                    continue
```

With:

```python
                existing_recovery_stop = runtime.order_store.load(recovery_stop_id)
                if existing_recovery_stop is not None:
                    is_terminal = existing_recovery_stop.status in {
                        "expired", "cancelled", "canceled", "error"
                    }
                    same_price = (
                        existing_recovery_stop.stop_price is not None
                        and pos.stop_price is not None
                        and abs(existing_recovery_stop.stop_price - pos.stop_price) < 0.001
                    )
                    if not is_terminal or same_price:
                        continue
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/unit/test_startup_recovery.py::test_option_stop_skipped_when_no_current_price tests/unit/test_startup_recovery.py::test_equity_stop_still_queued_when_no_current_price tests/unit/test_startup_recovery.py::test_recovery_stop_not_requeued_when_same_price_terminal tests/unit/test_startup_recovery.py::test_recovery_stop_requeued_when_price_changed_after_terminal -v
```

Expected: PASS.

- [ ] **Step 5: Run full startup_recovery tests**

```bash
pytest tests/unit/test_startup_recovery.py -v
```

Expected: All tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/alpaca_bot/runtime/startup_recovery.py tests/unit/test_startup_recovery.py
git commit -m "fix: skip option stop queueing when no price; add same-price guard to prevent infinite re-queue loop"
```

---

## Task 5: Fix `_infer_strategy_name_from_client_order_id` for OCC prefixes

**Files:**
- Modify: `src/alpaca_bot/runtime/startup_recovery.py:709-715`

Current:
```python
def _infer_strategy_name_from_client_order_id(client_order_id: str) -> str:
    """Parse strategy_name from new-format client_order_id: {strategy}:{version}:..."""
    from alpaca_bot.strategy import STRATEGY_REGISTRY
    if not client_order_id:
        return "breakout"
    first_segment = client_order_id.split(":")[0]
    return first_segment if first_segment in STRATEGY_REGISTRY else "breakout"
```

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/test_startup_recovery.py`:

```python
def test_infer_strategy_name_returns_option_for_occ_client_order_id() -> None:
    from alpaca_bot.runtime.startup_recovery import _infer_strategy_name_from_client_order_id
    # OCC client_order_ids use "option" as first segment
    assert _infer_strategy_name_from_client_order_id(
        "option:v1-breakout:2026-05-12:ALHC260618P00017500:stop"
    ) == "option"
    assert _infer_strategy_name_from_client_order_id(
        "option:v1-breakout:2026-05-12:AMLX260619C00050000:exit"
    ) == "option"
    # Equity strategy IDs unchanged
    assert _infer_strategy_name_from_client_order_id(
        "breakout:v1-breakout:2026-05-12:AAPL:stop"
    ) == "breakout"
    # Unknown → fallback to breakout
    assert _infer_strategy_name_from_client_order_id(
        "unknown:v1-breakout:2026-05-12:AAPL:stop"
    ) == "breakout"
    # Empty string → breakout
    assert _infer_strategy_name_from_client_order_id("") == "breakout"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/unit/test_startup_recovery.py::test_infer_strategy_name_returns_option_for_occ_client_order_id -v
```

Expected: FAIL (returns "breakout" for "option:..." prefix).

- [ ] **Step 3: Update `_infer_strategy_name_from_client_order_id`**

In `src/alpaca_bot/runtime/startup_recovery.py` at line 709, replace the function body:

```python
def _infer_strategy_name_from_client_order_id(client_order_id: str) -> str:
    """Parse strategy_name from new-format client_order_id: {strategy}:{version}:..."""
    from alpaca_bot.strategy import STRATEGY_REGISTRY
    if not client_order_id:
        return "breakout"
    first_segment = client_order_id.split(":")[0]
    return first_segment if first_segment in STRATEGY_REGISTRY else "breakout"
```

With:

```python
def _infer_strategy_name_from_client_order_id(client_order_id: str) -> str:
    """Parse strategy_name from new-format client_order_id: {strategy}:{version}:..."""
    from alpaca_bot.strategy import STRATEGY_REGISTRY
    if not client_order_id:
        return "breakout"
    first_segment = client_order_id.split(":")[0]
    if first_segment in STRATEGY_REGISTRY:
        return first_segment
    if first_segment == "option":
        return "option"
    return "breakout"
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/unit/test_startup_recovery.py::test_infer_strategy_name_returns_option_for_occ_client_order_id -v
```

Expected: PASS.

- [ ] **Step 5: Run full test suite**

```bash
pytest tests/unit/test_startup_recovery.py -v
```

Expected: All tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/alpaca_bot/runtime/startup_recovery.py tests/unit/test_startup_recovery.py
git commit -m "fix: _infer_strategy_name_from_client_order_id returns 'option' for OCC client_order_ids"
```

---

## Task 6: Circuit breaker in order_dispatch — mark 42210000 stops as `canceled`

**Files:**
- Modify: `src/alpaca_bot/runtime/order_dispatch.py:316-383` (broker submission failure handler)

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/test_order_dispatch.py`:

```python
def test_circuit_breaker_42210000_marks_stop_canceled_not_error() -> None:
    """Alpaca error 42210000 on a stop order must mark it canceled (not error)
    and emit order_dispatch_stop_price_rejected, not order_dispatch_failed."""
    _, dispatch_pending_orders = load_order_dispatch_api()
    settings = make_settings()
    now = datetime(2026, 5, 12, 14, 0, tzinfo=timezone.utc)

    class StopPriceRejectedBroker:
        """Raises the Alpaca 42210000 error for stop submissions."""
        def submit_stop_order(self, **kwargs):
            raise Exception("42210000: stop price must be less than current price")
        def submit_entry_order(self, **kwargs):
            raise RuntimeError("unexpected")
        def submit_limit_exit(self, **kwargs):
            raise RuntimeError("unexpected")
        def submit_market_exit(self, **kwargs):
            raise RuntimeError("unexpected")

    stop_order = OrderRecord(
        client_order_id="startup_recovery:v1-breakout:2026-05-12:ALHC260618P00017500:stop",
        symbol="ALHC260618P00017500",
        side="sell",
        intent_type="stop",
        status="pending_submit",
        quantity=5,
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        created_at=now,
        updated_at=now,
        stop_price=1.20,
        initial_stop_price=1.20,
        signal_timestamp=now,
    )
    order_store = RecordingOrderStore([stop_order])
    audit_store = RecordingAuditEventStore()
    runtime = SimpleNamespace(
        order_store=order_store,
        audit_event_store=audit_store,
        connection=FakeConnection(),
    )

    dispatch_pending_orders(
        settings=settings,
        runtime=runtime,
        broker=StopPriceRejectedBroker(),
        now=now,
    )

    # Must be saved as canceled, not error
    final_status = order_store.saved[-1].status
    assert final_status == "canceled", f"Expected 'canceled', got '{final_status}'"
    # Must emit the specific audit event
    event_types = [e.event_type for e in audit_store.appended]
    assert "order_dispatch_stop_price_rejected" in event_types
    assert "order_dispatch_failed" not in event_types


def test_non_42210000_stop_error_still_marks_error() -> None:
    """Non-42210000 broker errors on stop orders must still mark status='error'."""
    _, dispatch_pending_orders = load_order_dispatch_api()
    settings = make_settings()
    now = datetime(2026, 5, 12, 14, 0, tzinfo=timezone.utc)

    class GenericStopFailingBroker:
        """Raises a generic (non-42210000) error on stop submissions."""
        def submit_stop_order(self, **kwargs):
            raise Exception("500: internal server error")
        def submit_stop_limit_entry(self, **kwargs):
            raise Exception("500: internal server error")
        def submit_limit_entry(self, **kwargs):
            raise RuntimeError("unexpected")
        def submit_market_exit(self, **kwargs):
            raise RuntimeError("unexpected")

    stop_order = OrderRecord(
        client_order_id="startup_recovery:v1-breakout:2026-05-12:AAPL:stop",
        symbol="AAPL",
        side="sell",
        intent_type="stop",
        status="pending_submit",
        quantity=10,
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        created_at=now,
        updated_at=now,
        stop_price=185.0,
        initial_stop_price=185.0,
        signal_timestamp=now,
    )
    order_store = RecordingOrderStore([stop_order])
    audit_store = RecordingAuditEventStore()
    runtime = SimpleNamespace(
        order_store=order_store,
        audit_event_store=audit_store,
        connection=FakeConnection(),
    )

    dispatch_pending_orders(
        settings=settings,
        runtime=runtime,
        broker=GenericStopFailingBroker(),
        now=now,
    )

    final_status = order_store.saved[-1].status
    assert final_status == "error", f"Expected 'error', got '{final_status}'"
    event_types = [e.event_type for e in audit_store.appended]
    assert "order_dispatch_failed" in event_types
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/unit/test_order_dispatch.py::test_circuit_breaker_42210000_marks_stop_canceled_not_error tests/unit/test_order_dispatch.py::test_non_42210000_stop_error_still_marks_error -v
```

Expected: FAIL (both saves `error`, both emit `order_dispatch_failed`).

- [ ] **Step 3: Add `_is_unrecoverable_stop_error` and update failure handler**

In `src/alpaca_bot/runtime/order_dispatch.py`, add near the top of the file (before `dispatch_pending_orders`):

```python
_UNRECOVERABLE_STOP_CODES = frozenset({"42210000"})


def _is_unrecoverable_stop_error(exc: Exception) -> bool:
    msg = str(exc)
    return any(code in msg for code in _UNRECOVERABLE_STOP_CODES)
```

Then in the broker submission failure handler (inside `except Exception as exc:` block at ~line 320), replace the block that saves `status="error"` and emits `order_dispatch_failed`. Currently it unconditionally emits `order_dispatch_failed` and saves `status="error"`. Change to:

```python
        except Exception as exc:
            logger.warning(
                "order_dispatch: broker submission failed for %s %s: %s",
                order.symbol,
                order.intent_type,
                exc,
            )
            is_stop_price_rejected = (
                order.intent_type == "stop"
                and _is_unrecoverable_stop_error(exc)
            )
            final_status = "canceled" if is_stop_price_rejected else "error"
            audit_event_type = (
                "order_dispatch_stop_price_rejected"
                if is_stop_price_rejected
                else "order_dispatch_failed"
            )
            with lock_ctx:
                try:
                    runtime.audit_event_store.append(
                        AuditEvent(
                            event_type=audit_event_type,
                            symbol=order.symbol,
                            payload={
                                "error": str(exc),
                                "symbol": order.symbol,
                                "intent_type": order.intent_type,
                                "timestamp": timestamp.isoformat(),
                            },
                            created_at=timestamp,
                        ),
                        commit=False,
                    )
                    runtime.order_store.save(
                        OrderRecord(
                            client_order_id=order.client_order_id,
                            symbol=order.symbol,
                            side=order.side,
                            intent_type=order.intent_type,
                            status=final_status,
                            quantity=order.quantity,
                            trading_mode=order.trading_mode,
                            strategy_version=order.strategy_version,
                            strategy_name=order.strategy_name,
                            created_at=order.created_at,
                            updated_at=timestamp,
                            stop_price=order.stop_price,
                            limit_price=order.limit_price,
                            initial_stop_price=order.initial_stop_price,
                            broker_order_id=order.broker_order_id,
                            signal_timestamp=order.signal_timestamp,
                        ),
                        commit=False,
                    )
                    runtime.connection.commit()
                except Exception:
                    try:
                        runtime.connection.rollback()
                    except Exception:
                        pass
                    raise
            if notifier is not None:
                try:
                    notifier.send(
                        subject=f"Order dispatch failed: {order.symbol} {order.intent_type}",
                        body=(
                            f"Failed to submit {order.intent_type} order for {order.symbol}.\n"
                            f"client_order_id: {order.client_order_id}\n"
                            f"Error: {exc}"
                        ),
                    )
                except Exception:
                    logger.exception("Notifier failed to send order dispatch failure alert")
            continue
```

Note: The `with lock_ctx:` block, the `notifier` call, and the `continue` are present in the existing code. The only structural change is computing `final_status` and `audit_event_type` before the lock block, then using them inside it. Make sure to preserve all existing surrounding code exactly.

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/unit/test_order_dispatch.py::test_circuit_breaker_42210000_marks_stop_canceled_not_error tests/unit/test_order_dispatch.py::test_non_42210000_stop_error_still_marks_error -v
```

Expected: PASS.

- [ ] **Step 5: Run full order_dispatch tests**

```bash
pytest tests/unit/test_order_dispatch.py -v
```

Expected: All tests pass. Pay special attention to `test_dispatch_records_error_status_on_broker_failure` — it uses an entry order with a generic failing broker, so `is_stop_price_rejected=False` and it must still emit `order_dispatch_failed` / `status=error`.

- [ ] **Step 6: Commit**

```bash
git add src/alpaca_bot/runtime/order_dispatch.py tests/unit/test_order_dispatch.py
git commit -m "fix: circuit breaker for Alpaca 42210000 stop rejection — mark canceled, emit order_dispatch_stop_price_rejected"
```

---

## Task 7: Final verification

- [ ] **Step 1: Run the complete test suite**

```bash
pytest -v
```

Expected: All tests pass.

- [ ] **Step 2: Verify audit event names are consistent**

```bash
grep -rn "option_stop_skipped_no_price\|order_dispatch_stop_price_rejected\|stale_exit_canceled" src/ tests/
```

Expected: Each event name appears in exactly one emit site (src) and at least one assertion (tests).

- [ ] **Step 3: Commit**

If no code was changed in Step 2, no commit needed. If any inconsistencies were fixed, commit those.

---

## What this does NOT change

- `engine.py` — `stop_price=None` on option entries is intentional (defined risk = premium)
- Equity stop buffer (`breakout_stop_buffer_pct`) and equity position labeling
- EOD flatten logic, AH session handling
- `dispatch_pending_orders()` for entry or exit orders (only stop handling changes)
- DB schema — no migrations needed
