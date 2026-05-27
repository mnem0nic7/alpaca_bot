# Option Chain Symbol Decoupling Fix — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Introduce `OPTION_CHAIN_SYMBOLS` env var so supervisor fetches option chains only for a small curated list of underlyings, rather than all 1003 equity-watchlist symbols.

**Architecture:** Add `option_chain_symbols: tuple[str, ...]` to `Settings`; replace the volume-filter symbol selection in the supervisor's option chain block with a direct intersection of `intraday_bars_by_symbol` against that set; increase `max_workers` to 10; update the audit payload to only include the configured symbols; add a startup warning when the flag is on but the list is empty; update tests to supply the new config key.

**Tech Stack:** Python 3.11, pytest, existing fake-callable DI pattern (no mocks).

---

## Files Changed

| File | Change |
|------|--------|
| `src/alpaca_bot/config/__init__.py` | Add `option_chain_symbols` field + `from_env` parsing |
| `src/alpaca_bot/runtime/supervisor.py` | Replace `symbols_to_fetch`; `max_workers` 5→10; audit payload; startup warning |
| `tests/unit/test_config.py` | Add `option_chain_symbols` parse tests |
| `tests/unit/test_supervisor_option_chains.py` | Supply `OPTION_CHAIN_SYMBOLS` in helper; replace volume-filter tests |
| `DEPLOYMENT.md` | Document `OPTION_CHAIN_SYMBOLS` env var |
| `/etc/alpaca_bot/alpaca-bot.env` | Add `OPTION_CHAIN_SYMBOLS=ALHC,AMLX,AROC,BCRX,BFLY,CMG,CNK,ASAN,ATEC` |

---

## Task 1: Add `option_chain_symbols` to Settings

**Files:**
- Modify: `src/alpaca_bot/config/__init__.py:149-155` (field) and `~353-358` (from_env)
- Test: `tests/unit/test_config.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/unit/test_config.py`:

```python
def test_option_chain_symbols_default_is_empty():
    s = Settings.from_env(_base_env())
    assert s.option_chain_symbols == ()


def test_option_chain_symbols_parsed_from_csv():
    env = _base_env(OPTION_CHAIN_SYMBOLS="ALHC,AMLX,AROC")
    s = Settings.from_env(env)
    assert s.option_chain_symbols == ("ALHC", "AMLX", "AROC")


def test_option_chain_symbols_strips_whitespace():
    env = _base_env(OPTION_CHAIN_SYMBOLS=" ALHC , AMLX ")
    s = Settings.from_env(env)
    assert s.option_chain_symbols == ("ALHC", "AMLX")
```

Note: `_base_env` in `tests/unit/test_config.py` accepts `**overrides` (different from `helpers.py`); the call form `_base_env(OPTION_CHAIN_SYMBOLS="...")` works.

- [ ] **Step 2: Run tests to verify they fail**

```
pytest tests/unit/test_config.py::test_option_chain_symbols_default_is_empty tests/unit/test_config.py::test_option_chain_symbols_parsed_from_csv tests/unit/test_config.py::test_option_chain_symbols_strips_whitespace -v
```

Expected: FAIL — `TypeError: Settings.__init__() got an unexpected keyword argument 'option_chain_symbols'` (field not yet defined).

- [ ] **Step 3: Add the field to Settings**

In `src/alpaca_bot/config/__init__.py`, after line 149 (`option_chain_min_total_volume: int = 0`), insert:

```python
    option_chain_symbols: tuple[str, ...] = ()
```

So the block looks like:

```python
    enable_options_trading: bool = False
    option_chain_min_total_volume: int = 0
    option_chain_symbols: tuple[str, ...] = ()
    option_stop_buffer_pct: float = 0.10
```

- [ ] **Step 4: Add parsing to `from_env`**

In `src/alpaca_bot/config/__init__.py`, after the `option_chain_min_total_volume` parsing block (around line 355), insert:

```python
            option_chain_symbols=tuple(
                s.strip()
                for s in values.get("OPTION_CHAIN_SYMBOLS", "").split(",")
                if s.strip()
            ),
```

So the surrounding block looks like:

```python
            option_chain_min_total_volume=int(
                values.get("OPTION_CHAIN_MIN_TOTAL_VOLUME", "0")
            ),
            option_chain_symbols=tuple(
                s.strip()
                for s in values.get("OPTION_CHAIN_SYMBOLS", "").split(",")
                if s.strip()
            ),
            option_stop_buffer_pct=float(values.get("OPTION_STOP_BUFFER_PCT", "0.10")),
```

- [ ] **Step 5: Run tests to verify they pass**

```
pytest tests/unit/test_config.py::test_option_chain_symbols_default_is_empty tests/unit/test_config.py::test_option_chain_symbols_parsed_from_csv tests/unit/test_config.py::test_option_chain_symbols_strips_whitespace -v
```

Expected: PASS (3 tests).

- [ ] **Step 6: Run the full test suite to check for regressions**

```
pytest tests/unit/test_config.py -v
```

Expected: all config tests PASS.

- [ ] **Step 7: Commit**

```bash
git add src/alpaca_bot/config/__init__.py tests/unit/test_config.py
git commit -m "feat: add option_chain_symbols to Settings"
```

---

## Task 2: Update Supervisor — Symbol Filtering, max_workers, Audit Payload, Warning

**Files:**
- Modify: `src/alpaca_bot/runtime/supervisor.py:182-184` (startup warning), `804-809` (executor + symbols_to_fetch), `840-844` (audit payload)
- Test: `tests/unit/test_supervisor_option_chains.py`

`★ Insight ─────────────────────────────────────`
The existing tests will fail after the supervisor change because `_make_supervisor()` sets `ENABLE_OPTIONS_TRADING=true` but never sets `OPTION_CHAIN_SYMBOLS`. With the new code, `settings.option_chain_symbols=()` → `symbols_to_fetch=[]` → no chains fetched → tests that assert specific symbols were fetched break. The fix: supply `OPTION_CHAIN_SYMBOLS=ACHR,METC,SLS` (the exact `_WATCHLIST_SYMBOLS`) in `_make_supervisor()`'s env.
`─────────────────────────────────────────────────`

- [ ] **Step 1: Write the new failing tests and update the helper**

Replace the full contents of `tests/unit/test_supervisor_option_chains.py` with:

```python
from __future__ import annotations

from datetime import datetime, timezone
from importlib import import_module
from types import SimpleNamespace

from tests.unit.helpers import _base_env
from alpaca_bot.config import Settings
from alpaca_bot.domain import Bar

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


def _make_supervisor(*, adapter, audit_store=None, get_stock_bars=None, extra_env=None):
    """Build a RuntimeSupervisor wired with a watchlist returning _WATCHLIST_SYMBOLS."""
    RuntimeSupervisor = import_module("alpaca_bot.runtime.supervisor").RuntimeSupervisor
    env = {
        **_base_env(),
        "ENABLE_OPTIONS_TRADING": "true",
        "OPTION_CHAIN_SYMBOLS": ",".join(_WATCHLIST_SYMBOLS),
        **(extra_env or {}),
    }
    settings = Settings.from_env(env)
    if get_stock_bars is None:
        get_stock_bars = lambda **_: {sym: [] for sym in _WATCHLIST_SYMBOLS}

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
            get_stock_bars=get_stock_bars,
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


def test_option_chain_fetch_uses_option_chain_symbols_not_full_watchlist():
    """Only OPTION_CHAIN_SYMBOLS must be fetched, not every symbol in intraday_bars_by_symbol."""
    # Watchlist has ACHR, METC, SLS but OPTION_CHAIN_SYMBOLS only covers ACHR and METC.
    adapter = RecordingOptionChainAdapter()
    supervisor = _make_supervisor(
        adapter=adapter,
        extra_env={"OPTION_CHAIN_SYMBOLS": "ACHR,METC"},
    )
    supervisor.run_cycle_once(now=lambda: _NOW)

    assert set(adapter.fetched) == {"ACHR", "METC"}, (
        f"Expected only ACHR,METC; got {adapter.fetched!r}"
    )
    assert "SLS" not in adapter.fetched, "SLS not in OPTION_CHAIN_SYMBOLS — must not be fetched"


def test_option_chain_fetch_symbol_not_in_bars_is_skipped():
    """A symbol in OPTION_CHAIN_SYMBOLS that has no intraday bars must not be fetched."""
    # Bars only cover ACHR and METC — SLS is configured but absent from bars.
    bars_by_symbol = {
        "ACHR": [Bar(symbol="ACHR", timestamp=_NOW, open=10.0, high=11.0, low=9.0, close=10.5, volume=100_000)],
        "METC": [Bar(symbol="METC", timestamp=_NOW, open=20.0, high=21.0, low=19.0, close=20.5, volume=100_000)],
    }
    adapter = RecordingOptionChainAdapter()
    supervisor = _make_supervisor(
        adapter=adapter,
        get_stock_bars=lambda **_: bars_by_symbol,
        # OPTION_CHAIN_SYMBOLS includes SLS but bars don't — SLS must be skipped
    )
    supervisor.run_cycle_once(now=lambda: _NOW)

    assert "SLS" not in adapter.fetched, (
        "SLS has no intraday bars — must not be fetched even though it's in OPTION_CHAIN_SYMBOLS"
    )
    assert "ACHR" in adapter.fetched
    assert "METC" in adapter.fetched


def test_option_chain_empty_symbols_fetches_nothing():
    """With OPTION_CHAIN_SYMBOLS=[], no chains should be fetched."""
    adapter = RecordingOptionChainAdapter()
    supervisor = _make_supervisor(
        adapter=adapter,
        extra_env={"OPTION_CHAIN_SYMBOLS": ""},
    )
    supervisor.run_cycle_once(now=lambda: _NOW)

    assert adapter.fetched == [], (
        f"Expected no fetches with empty OPTION_CHAIN_SYMBOLS; got {adapter.fetched!r}"
    )


def test_option_chain_exception_does_not_block_other_symbols():
    """A fetch exception for one symbol must not prevent other symbols from being attempted."""
    adapter = RecordingOptionChainAdapter(raise_for={"METC"})
    supervisor = _make_supervisor(adapter=adapter)
    supervisor.run_cycle_once(now=lambda: _NOW)  # must not raise

    assert "ACHR" in adapter.fetched
    assert "SLS" in adapter.fetched
    assert "METC" in adapter.fetched  # attempted even though it raised


def test_option_chains_fetched_audit_payload_only_contains_option_chain_symbols():
    """option_chains_fetched payload keys must be exactly OPTION_CHAIN_SYMBOLS, not the full watchlist."""
    audit_store = RecordingAuditStore()
    adapter = RecordingOptionChainAdapter()
    # OPTION_CHAIN_SYMBOLS only covers ACHR and METC — SLS is on the watchlist but not configured
    supervisor = _make_supervisor(
        adapter=adapter,
        audit_store=audit_store,
        extra_env={"OPTION_CHAIN_SYMBOLS": "ACHR,METC"},
    )
    supervisor.run_cycle_once(now=lambda: _NOW)

    chain_events = [e for e in audit_store.events if e.event_type == "option_chains_fetched"]
    assert len(chain_events) == 1, f"Expected 1 option_chains_fetched event, got {len(chain_events)}"
    assert set(chain_events[0].payload) == {"ACHR", "METC"}, (
        f"Audit payload keys {set(chain_events[0].payload)!r} must equal OPTION_CHAIN_SYMBOLS"
    )
    assert "SLS" not in chain_events[0].payload, "SLS is not in OPTION_CHAIN_SYMBOLS — must not appear in payload"
```

- [ ] **Step 2: Run tests to verify they fail**

```
pytest tests/unit/test_supervisor_option_chains.py -v
```

Expected: the new tests FAIL (supervisor still uses volume-filter logic); the two volume-filter tests are removed so no old tests pass.

- [ ] **Step 3: Update supervisor `from_settings` to log a warning**

In `src/alpaca_bot/runtime/supervisor.py`, replace:

```python
        if settings.enable_options_trading:
            option_chain_adapter = AlpacaOptionChainAdapter.from_settings(settings)
            option_broker = broker
```

with:

```python
        if settings.enable_options_trading:
            option_chain_adapter = AlpacaOptionChainAdapter.from_settings(settings)
            option_broker = broker
            if not settings.option_chain_symbols:
                logger.warning(
                    "ENABLE_OPTIONS_TRADING=true but OPTION_CHAIN_SYMBOLS is empty"
                    " — option strategies will be disabled this session"
                )
```

- [ ] **Step 4: Replace `symbols_to_fetch` construction and increase `max_workers`**

In `src/alpaca_bot/runtime/supervisor.py`, replace:

```python
            executor = ThreadPoolExecutor(max_workers=5)
            min_vol = self.settings.option_chain_min_total_volume
            symbols_to_fetch = [
                sym for sym, bars in intraday_bars_by_symbol.items()
                if min_vol == 0 or sum(b.volume for b in bars) >= min_vol
            ]
```

with:

```python
            executor = ThreadPoolExecutor(max_workers=10)
            configured = set(s.upper() for s in self.settings.option_chain_symbols)
            # Fetch only configured underlyings that also have intraday bars this session.
            # OPTION_CHAIN_MIN_TOTAL_VOLUME is no longer used for symbol selection.
            symbols_to_fetch = [sym for sym in intraday_bars_by_symbol if sym in configured]
```

- [ ] **Step 5: Replace audit payload to exclude zero-count watchlist entries**

In `src/alpaca_bot/runtime/supervisor.py`, replace:

```python
            option_chain_counts = {
                sym: len(chains) for sym, chains in option_chains_by_symbol.items()
            }
            for sym in intraday_bars_by_symbol:
                option_chain_counts.setdefault(sym, 0)
```

with:

```python
            option_chain_counts = {
                sym: len(option_chains_by_symbol.get(sym, []))
                for sym in self.settings.option_chain_symbols
            }
```

- [ ] **Step 6: Run new tests to verify they pass**

```
pytest tests/unit/test_supervisor_option_chains.py -v
```

Expected: all 5 new tests PASS.

- [ ] **Step 7: Run the full test suite**

```
pytest
```

Expected: all tests PASS. No regressions.

- [ ] **Step 8: Commit**

```bash
git add src/alpaca_bot/runtime/supervisor.py tests/unit/test_supervisor_option_chains.py
git commit -m "feat: decouple option chain fetch from equity watchlist via OPTION_CHAIN_SYMBOLS"
```

---

## Task 3: Update Production Env and Redeploy

**Files:**
- Modify: `DEPLOYMENT.md`
- Modify: `/etc/alpaca_bot/alpaca-bot.env`
- Action: redeploy

`★ Insight ─────────────────────────────────────`
This task is configuration, not code — but it's the step that actually unblocks live option entries. The 9 symbols listed are the underlyings confirmed from the `option_orders` table (the last session that had positions). Without this, `option_chain_symbols=()` in production and the warning log fires every cycle.
`─────────────────────────────────────────────────`

- [ ] **Step 1: Document `OPTION_CHAIN_SYMBOLS` in `DEPLOYMENT.md`**

In `DEPLOYMENT.md`, after line 83 (`# OPTION_DELTA_TARGET=0.50 ...`), insert:

```
# OPTION_CHAIN_SYMBOLS=ALHC,AMLX,AROC  # required when ENABLE_OPTIONS_TRADING=true;
#   comma-separated list of underlying tickers to fetch option chains for.
#   If empty (the default), option strategies are silently disabled even when
#   ENABLE_OPTIONS_TRADING=true (a warning is logged at startup).
```

Commit:

```bash
git add DEPLOYMENT.md
git commit -m "docs: document OPTION_CHAIN_SYMBOLS env var in DEPLOYMENT.md"
```

- [ ] **Step 2: Add `OPTION_CHAIN_SYMBOLS` to the production env file**

Open `/etc/alpaca_bot/alpaca-bot.env` and add:

```
OPTION_CHAIN_SYMBOLS=ALHC,AMLX,AROC,BCRX,BFLY,CMG,CNK,ASAN,ATEC
```

Place it near the other `OPTION_*` variables for readability.

- [ ] **Step 3: Redeploy**

```bash
./scripts/deploy.sh /etc/alpaca_bot/alpaca-bot.env
```

Expected: migrate runs, then supervisor restarts. First cycle after restart will log an `option_chains_fetched` audit event with only the 9 configured symbols — no timeout.

- [ ] **Step 4: Verify in production logs**

```bash
docker logs alpaca_bot-supervisor-1 --tail 50 | grep -E "option_chain|OPTION_CHAIN"
```

Expected output: no "timed out" line; `option_chains_fetched` event logged with a payload that only contains the 9 configured symbols.

- [ ] **Step 5: Verify in Postgres**

```sql
SELECT payload
FROM audit_events
WHERE event_type = 'option_chains_fetched'
ORDER BY created_at DESC
LIMIT 1;
```

Expected: `{"ALHC": 0, "AMLX": 0, ...}` — 9 keys, not 1003. Counts may be 0 initially (outside market hours) and will be non-zero once chains are fetched during market hours.
