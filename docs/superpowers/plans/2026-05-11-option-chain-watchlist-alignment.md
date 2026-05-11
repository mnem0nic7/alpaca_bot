# Option Chain Watchlist Alignment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the option chain fetch symbol universe identical to the equity watchlist by iterating `intraday_bars_by_symbol` (full DB watchlist) instead of `settings.symbols` (8 hardcoded big-cap symbols), and parallelize the fetch with `ThreadPoolExecutor(max_workers=20)` to keep cycle overhead within the 60-second cadence.

**Architecture:** Two targeted edits to `supervisor.py`: (1) replace the sequential `for symbol in self.settings.symbols` loop (line ~761) with a parallel `ThreadPoolExecutor` loop over `intraday_bars_by_symbol` keys, and (2) update the `option_chains_fetched` audit sentinel (line ~787) to use `intraday_bars_by_symbol` keys. One new test file. No new env vars, no schema changes, no migration.

**Tech Stack:** Python `concurrent.futures.ThreadPoolExecutor`, pytest, existing fake-callables DI pattern.

---

## Files Affected

| File | Action |
|---|---|
| `src/alpaca_bot/runtime/supervisor.py` | Add `ThreadPoolExecutor` import; replace sequential loop + audit sentinel |
| `tests/unit/test_supervisor_option_chains.py` | Create — three tests for watchlist-aligned chain fetch |

---

### Task 1: Write failing tests for watchlist-aligned option chain fetch

**Files:**
- Create: `tests/unit/test_supervisor_option_chains.py`

- [ ] **Step 1: Create the test file with the full content below**

```python
# tests/unit/test_supervisor_option_chains.py
from __future__ import annotations

from datetime import datetime, timezone
from importlib import import_module
from types import SimpleNamespace

from tests.unit.helpers import _base_env
from alpaca_bot.config import Settings

_NOW = datetime(2026, 5, 1, 14, 30, tzinfo=timezone.utc)
# Symbols on the watchlist but NOT in settings.symbols ("AAPL,MSFT" from _base_env)
_WATCHLIST_SYMBOLS = ["ACHR", "METC", "SLS"]


class RecordingOptionChainAdapter:
    """Records which symbols were attempted; optionally raises for specific ones."""

    def __init__(self, *, raise_for: set[str] | None = None) -> None:
        self.fetched: list[str] = []
        self._raise_for = raise_for or set()

    def get_option_chain(self, symbol: str, settings) -> list:
        self.fetched.append(symbol)
        if symbol in self._raise_for:
            raise RuntimeError(f"simulated API error for {symbol}")
        return []


class RecordingAuditStore:
    def __init__(self) -> None:
        self.events: list = []

    def append(self, event, **_) -> None:
        self.events.append(event)

    def load_latest(self, **_): return None
    def list_recent(self, **_): return []
    def list_by_event_types(self, **_): return []


def _make_supervisor(*, adapter, audit_store=None):
    """Build a RuntimeSupervisor wired with a watchlist returning _WATCHLIST_SYMBOLS."""
    RuntimeSupervisor = import_module("alpaca_bot.runtime.supervisor").RuntimeSupervisor
    env = {**_base_env(), "ENABLE_OPTIONS_TRADING": "true"}
    settings = Settings.from_env(env)

    class _FakeConn:
        def commit(self): pass
        def rollback(self): pass

    _audit = audit_store or SimpleNamespace(
        append=lambda *a, **k: None,
        load_latest=lambda **_: None,
        list_recent=lambda **_: [],
        list_by_event_types=lambda **_: [],
    )

    runtime = SimpleNamespace(
        connection=_FakeConn(),
        store_lock=None,
        order_store=SimpleNamespace(
            save=lambda *a, **k: None,
            list_by_status=lambda **k: [],
            list_pending_submit=lambda **k: [],
            daily_realized_pnl=lambda **k: 0.0,
            daily_realized_pnl_by_symbol=lambda **k: {},
            list_trade_pnl_by_strategy=lambda **k: [],
        ),
        strategy_weight_store=None,
        trading_status_store=SimpleNamespace(load=lambda **_: None),
        position_store=SimpleNamespace(list_all=lambda **_: [], replace_all=lambda **_: None),
        daily_session_state_store=SimpleNamespace(
            load=lambda **_: None,
            save=lambda state, **_: None,
            list_by_session=lambda **_: [],
        ),
        audit_event_store=_audit,
        strategy_flag_store=None,
        watchlist_store=SimpleNamespace(
            list_enabled=lambda *a: list(_WATCHLIST_SYMBOLS),
            list_ignored=lambda *a: [],
        ),
        option_order_store=None,
    )

    return RuntimeSupervisor(
        settings=settings,
        runtime=runtime,
        broker=SimpleNamespace(
            get_account=lambda: SimpleNamespace(
                equity=10_000.0, buying_power=20_000.0, trading_blocked=False
            ),
            list_open_orders=lambda: [],
            get_open_positions=lambda: [],
            get_clock=lambda: SimpleNamespace(is_open=False),
        ),
        market_data=SimpleNamespace(
            # Return bars dict keyed by watchlist symbols so intraday_bars_by_symbol
            # has the right keys for the option chain loop to iterate.
            get_stock_bars=lambda **_: {sym: [] for sym in _WATCHLIST_SYMBOLS},
            get_daily_bars=lambda **_: {},
        ),
        stream=None,
        close_runtime_fn=lambda _: None,
        connection_checker=lambda _: True,
        cycle_runner=lambda *, strategy_name, **kwargs: SimpleNamespace(intents=[]),
        cycle_intent_executor=lambda **kwargs: SimpleNamespace(
            submitted_exit_count=0, failed_exit_count=0
        ),
        order_dispatcher=lambda **kwargs: {"submitted_count": 0},
        option_chain_adapter=adapter,
    )


def test_option_chain_fetch_uses_watchlist_not_settings_symbols():
    """Adapter must be called for intraday_bars_by_symbol keys, not settings.symbols."""
    adapter = RecordingOptionChainAdapter()
    supervisor = _make_supervisor(adapter=adapter)
    supervisor.run_cycle_once(now=lambda: _NOW)

    assert set(adapter.fetched) == set(_WATCHLIST_SYMBOLS), (
        f"Expected fetches for {_WATCHLIST_SYMBOLS!r}, got {adapter.fetched!r}"
    )
    for sym in ("AAPL", "MSFT"):
        assert sym not in adapter.fetched, (
            f"{sym!r} is in settings.symbols but not the watchlist — must not be fetched"
        )


def test_option_chain_exception_does_not_block_other_symbols():
    """A fetch exception for one symbol must not prevent other symbols from being attempted."""
    adapter = RecordingOptionChainAdapter(raise_for={"METC"})
    supervisor = _make_supervisor(adapter=adapter)
    supervisor.run_cycle_once(now=lambda: _NOW)  # must not raise

    assert "ACHR" in adapter.fetched
    assert "SLS" in adapter.fetched
    assert "METC" in adapter.fetched  # attempted even though it raised


def test_option_chains_fetched_audit_event_keys_match_watchlist():
    """option_chains_fetched payload keys must equal intraday_bars_by_symbol keys."""
    audit_store = RecordingAuditStore()
    adapter = RecordingOptionChainAdapter()
    supervisor = _make_supervisor(adapter=adapter, audit_store=audit_store)
    supervisor.run_cycle_once(now=lambda: _NOW)

    chain_events = [e for e in audit_store.events if e.event_type == "option_chains_fetched"]
    assert len(chain_events) == 1, f"Expected 1 option_chains_fetched event, got {len(chain_events)}"
    assert set(chain_events[0].payload) == set(_WATCHLIST_SYMBOLS), (
        f"Audit payload keys {set(chain_events[0].payload)!r} must equal "
        f"watchlist {set(_WATCHLIST_SYMBOLS)!r}"
    )
```

- [ ] **Step 2: Run to verify the tests FAIL before the fix**

```bash
pytest tests/unit/test_supervisor_option_chains.py -v
```

Expected: all three tests FAIL. `test_option_chain_fetch_uses_watchlist_not_settings_symbols` fails because `adapter.fetched` contains `['AAPL', 'MSFT']` (settings.symbols), not the watchlist symbols.

---

### Task 2: Implement parallel watchlist-aligned option chain fetch in supervisor.py

**Files:**
- Modify: `src/alpaca_bot/runtime/supervisor.py`

- [ ] **Step 1: Add `ThreadPoolExecutor` import at the top of the file**

In `src/alpaca_bot/runtime/supervisor.py`, replace:

```python
import contextlib
from dataclasses import dataclass, replace
```

With:

```python
import contextlib
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
```

- [ ] **Step 2: Replace the sequential chain fetch loop with a parallel fetch over `intraday_bars_by_symbol`**

In `src/alpaca_bot/runtime/supervisor.py`, locate the option chain fetch block inside `run_cycle_once`. Replace the sequential loop:

```python
            for symbol in self.settings.symbols:
                try:
                    chains = self._option_chain_adapter.get_option_chain(symbol, self.settings)
                    if chains:
                        option_chains_by_symbol[symbol] = chains
                except Exception:
                    logger.exception("option chain fetch failed for %s", symbol)
```

With:

```python
            def _fetch_one(sym: str) -> tuple[str, list]:
                return sym, self._option_chain_adapter.get_option_chain(sym, self.settings)

            with ThreadPoolExecutor(max_workers=20) as executor:
                future_to_sym = {
                    executor.submit(_fetch_one, sym): sym
                    for sym in intraday_bars_by_symbol
                }
                for future, symbol in future_to_sym.items():
                    try:
                        _, chains = future.result()
                        if chains:
                            option_chains_by_symbol[symbol] = chains
                    except Exception:
                        logger.exception("option chain fetch failed for %s", symbol)
```

- [ ] **Step 3: Update the audit sentinel to use `intraday_bars_by_symbol` keys**

A few lines below the loop (in the same `if self._option_chain_adapter is not None:` block), replace:

```python
            for sym in self.settings.symbols:
                option_chain_counts.setdefault(sym, 0)
```

With:

```python
            for sym in intraday_bars_by_symbol:
                option_chain_counts.setdefault(sym, 0)
```

---

### Task 3: Verify tests pass and commit

- [ ] **Step 1: Run the new tests — expect all three to PASS**

```bash
pytest tests/unit/test_supervisor_option_chains.py -v
```

Expected:
```
PASSED tests/unit/test_supervisor_option_chains.py::test_option_chain_fetch_uses_watchlist_not_settings_symbols
PASSED tests/unit/test_supervisor_option_chains.py::test_option_chain_exception_does_not_block_other_symbols
PASSED tests/unit/test_supervisor_option_chains.py::test_option_chains_fetched_audit_event_keys_match_watchlist
```

- [ ] **Step 2: Run the full test suite — expect no regressions**

```bash
pytest --tb=short -q
```

All existing tests must pass. Key test to watch: `tests/unit/test_supervisor_option_integration.py::test_disabled_option_strategy_excluded_from_cycle` uses `get_stock_bars=lambda **_: {}`, so `intraday_bars_by_symbol={}` and the `ThreadPoolExecutor` loop iterates over nothing — unchanged behavior.

- [ ] **Step 3: Commit**

```bash
git add src/alpaca_bot/runtime/supervisor.py tests/unit/test_supervisor_option_chains.py
git commit -m "feat: align option chain fetch with equity watchlist, parallel fetch via ThreadPoolExecutor

Option chains were fetched only for settings.symbols (8 big-cap hardcodes). Now fetches
for all symbols in intraday_bars_by_symbol (full DB watchlist, 400+ symbols). Parallel
fetch via ThreadPoolExecutor(max_workers=20) keeps cycle overhead to ~3-6s vs 60+s sequential."
```
