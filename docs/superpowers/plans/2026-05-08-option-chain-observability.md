# Option Chain Observability + Delta Fallback Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Surface option chain failures in logs and the audit trail, add tests for put contract ATM fallback, and emit a per-cycle `option_chains_fetched` audit event so chain health is queryable from the DB.

**Architecture:** Three additive changes: (1) add a `logger.warning` with `exc_info=True` to the silent catch in `AlpacaOptionChainAdapter.get_option_chain()`; (2) add the missing put-contract ATM fallback tests (implementation already exists); (3) emit one `option_chains_fetched` audit event per cycle from the supervisor, containing a per-symbol contract count dict (0 for symbols with no chains). No schema changes, no new env vars.

**Tech Stack:** Python 3.12, pytest, standard library `logging`, existing `AuditEvent` domain type, existing `_append_audit` supervisor helper.

---

## File Map

| File | Change |
|---|---|
| `src/alpaca_bot/execution/option_chain.py` | Add `import logging` + module logger + `logger.warning(..., exc_info=True)` before `return []` |
| `src/alpaca_bot/runtime/supervisor.py` | Emit `option_chains_fetched` AuditEvent after chain fetch loop |
| `tests/unit/test_option_chain.py` | Add 2 tests: exception returns `[]`, warning is logged |
| `tests/unit/test_option_selector.py` | Add `TestSelectPutContract` class with 2 tests |
| `tests/unit/test_supervisor_weights.py` | Add `option_chain_adapter` param to `_make_supervisor` + 2 audit event tests |

---

## Task 1: Log exception in `get_option_chain()`

**Files:**
- Modify: `src/alpaca_bot/execution/option_chain.py`
- Test: `tests/unit/test_option_chain.py`

### Background

`AlpacaOptionChainAdapter.get_option_chain()` currently has this silent catch:

```python
try:
    snapshots: dict[str, Any] = self._client.get_option_chain(request)
except Exception:
    return []   # ← swallowed silently, no logging
```

When the Alpaca API returns an auth failure, rate limit error, or subscription issue, zero
information is produced. The supervisor's outer `try/except` at line 682 never fires because
`get_option_chain()` itself never raises. This is the root cause of 0 option trades ever being
placed despite `ENABLE_OPTIONS_TRADING=true`.

- [ ] **Step 1: Write the failing test (warning logged on API failure)**

Add to `tests/unit/test_option_chain.py` inside `class TestAlpacaOptionChainAdapter:`:

```python
def test_warning_logged_on_api_failure(self, caplog):
    import logging

    class _RaisingClient:
        def get_option_chain(self, request):
            raise RuntimeError("403 Forbidden")

    adapter = AlpacaOptionChainAdapter(_RaisingClient())
    s = _settings()
    with caplog.at_level(logging.WARNING, logger="alpaca_bot.execution.option_chain"):
        result = adapter.get_option_chain("AAPL", s)

    assert result == []
    assert any("AAPL" in rec.message for rec in caplog.records)
    assert any(rec.levelno == logging.WARNING for rec in caplog.records)
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/unit/test_option_chain.py::TestAlpacaOptionChainAdapter::test_warning_logged_on_api_failure -v
```

Expected: FAIL — no WARNING is logged because the current code has bare `return []`.

- [ ] **Step 3: Add logger and warning to `option_chain.py`**

At the top of `src/alpaca_bot/execution/option_chain.py`, after the existing imports, add:

```python
import logging

logger = logging.getLogger(__name__)
```

Then change the inner exception handler (the one that catches `get_option_chain` failures)
from:

```python
    try:
        snapshots: dict[str, Any] = self._client.get_option_chain(request)
    except Exception:
        return []
```

to:

```python
    try:
        snapshots: dict[str, Any] = self._client.get_option_chain(request)
    except Exception:
        logger.warning("option chain fetch failed for %s", symbol, exc_info=True)
        return []
```

The full updated method looks like this (for reference — only the `except` block changes):

```python
def get_option_chain(self, symbol: str, settings: Settings) -> list[OptionContract]:
    try:
        from alpaca.data.requests import OptionChainRequest  # type: ignore[import]
        request = OptionChainRequest(underlying_symbol=symbol, feed="indicative")
    except ImportError:
        return []

    try:
        snapshots: dict[str, Any] = self._client.get_option_chain(request)
    except Exception:
        logger.warning("option chain fetch failed for %s", symbol, exc_info=True)
        return []

    contracts = []
    for occ_symbol, snapshot in snapshots.items():
        try:
            contracts.append(_snapshot_to_contract(occ_symbol, symbol, snapshot))
        except Exception:
            continue
    return contracts
```

- [ ] **Step 4: Run all option chain tests**

```bash
pytest tests/unit/test_option_chain.py -v
```

Expected: All tests PASS (existing tests untouched, new test now passes).

- [ ] **Step 5: Commit**

```bash
git add src/alpaca_bot/execution/option_chain.py tests/unit/test_option_chain.py
git commit -m "fix: log warning when option chain fetch fails instead of swallowing silently"
```

---

## Task 2: Tests for `select_put_contract()` ATM fallback

**Files:**
- Modify: `tests/unit/test_option_selector.py`

### Background

The implementation of `select_put_contract()` already has the ATM-by-strike fallback (line 49
of `option_selector.py`). The existing test file only has tests for `select_call_contract` and
does not import `select_put_contract`. This task adds the missing tests so the fallback is
verified for puts too.

Note: put deltas are negative (e.g., -0.50 for ATM put). The selector uses
`abs(c.delta)` internally so `option_delta_target=0.50` matches a put with `delta=-0.50`.

- [ ] **Step 1: Write the put-contract tests**

At the top of `tests/unit/test_option_selector.py`, update the import line from:

```python
from alpaca_bot.strategy.option_selector import select_call_contract
```

to:

```python
from alpaca_bot.strategy.option_selector import select_call_contract, select_put_contract
```

Then add a new helper and test class at the end of the file:

```python
def _put_contract(
    strike: float, expiry: date, ask: float, delta: float | None = None
) -> OptionContract:
    return OptionContract(
        occ_symbol=f"AAPL{expiry.strftime('%y%m%d')}P{int(strike * 1000):08d}",
        underlying="AAPL",
        option_type="put",
        strike=strike,
        expiry=expiry,
        bid=ask - 0.05,
        ask=ask,
        delta=delta,
    )


class TestSelectPutContract:
    def test_selects_atm_by_strike_when_no_delta(self):
        s = _settings()
        p140 = _put_contract(140.0, NEAR_EXPIRY, ask=1.5)
        p150 = _put_contract(150.0, NEAR_EXPIRY, ask=3.0)
        p160 = _put_contract(160.0, NEAR_EXPIRY, ask=10.0)
        result = select_put_contract(
            [p140, p150, p160], current_price=150.0, today=TODAY, settings=s
        )
        assert result is p150

    def test_selects_by_delta_when_available(self):
        s = _settings(OPTION_DELTA_TARGET="0.50")
        # Put deltas are negative; abs(-0.30)=0.30, abs(-0.50)=0.50 matches target
        p30 = _put_contract(140.0, NEAR_EXPIRY, ask=1.5, delta=-0.30)
        p50 = _put_contract(150.0, NEAR_EXPIRY, ask=3.0, delta=-0.50)
        p70 = _put_contract(160.0, NEAR_EXPIRY, ask=10.0, delta=-0.70)
        result = select_put_contract(
            [p30, p50, p70], current_price=150.0, today=TODAY, settings=s
        )
        assert result is p50
```

- [ ] **Step 2: Run put contract tests**

```bash
pytest tests/unit/test_option_selector.py -v
```

Expected: All tests PASS (the implementation already has the fallback).

- [ ] **Step 3: Commit**

```bash
git add tests/unit/test_option_selector.py
git commit -m "test: add put contract ATM fallback and delta selection tests"
```

---

## Task 3: Emit `option_chains_fetched` audit event in supervisor

**Files:**
- Modify: `src/alpaca_bot/runtime/supervisor.py`
- Modify: `tests/unit/test_supervisor_weights.py`

### Background

After the chain-fetch loop in `run_cycle_once()` (currently lines 676–699), the supervisor has
no record in the audit trail of whether chains were fetched successfully. An operator cannot
distinguish "the API call failed" from "no symbols requested" without checking raw logs.

The fix emits one `option_chains_fetched` event per cycle containing a dict keyed by symbol
with the contract count as value (0 for symbols with failed/empty chains). This makes health
immediately queryable:

```sql
SELECT payload FROM audit_events
WHERE event_type = 'option_chains_fetched'
ORDER BY created_at DESC LIMIT 1;
-- {"AAPL": 0, "MSFT": 0} → chains failing
-- {"AAPL": 847, "MSFT": 621} → chains healthy
```

### Why `_append_audit` instead of direct `.append()`

`_append_audit` acquires `store_lock` before writing, preventing races with the trade update
stream thread. Direct calls to `audit_event_store.append()` that bypass this lock are only
safe during startup (before the stream thread starts). All in-cycle audit events should use
`_append_audit`.

- [ ] **Step 1: Write failing tests**

At the end of `tests/unit/test_supervisor_weights.py`, add:

```python
# ── helpers for option chain adapter tests ────────────────────────────────────

class _FakeOptionChainAdapter:
    """Returns the provided chains dict on every call."""
    def __init__(self, chains_by_symbol: dict | None = None):
        self._chains = chains_by_symbol or {}

    def get_option_chain(self, symbol: str, settings):
        return self._chains.get(symbol, [])


def _make_supervisor_with_option_adapter(
    adapter,
    *,
    settings: Settings | None = None,
):
    """Thin wrapper: builds a supervisor with an injected option chain adapter."""
    s = settings or _make_settings()
    module = import_module("alpaca_bot.runtime.supervisor")
    RuntimeSupervisor = module.RuntimeSupervisor

    class _FakeBroker:
        def get_account(self):
            return BrokerAccount(equity=10_000.0, buying_power=20_000.0, trading_blocked=False)
        def list_open_orders(self): return []

    class _FakeMarketData:
        def get_stock_bars(self, **kwargs): return {}
        def get_daily_bars(self, **kwargs): return {}

    class _FakeConn2:
        def commit(self): pass
        def rollback(self): pass

    class _FakeTradingStatusStore:
        def load(self, **kwargs): return None

    class _FakePositionStore:
        def list_all(self, **kwargs): return []
        def replace_all(self, **kwargs): pass

    class _FakeStrategyFlagStore:
        def list_all(self, **kwargs): return []
        def load(self, *, strategy_name, **kwargs):
            from alpaca_bot.storage import StrategyFlag
            return StrategyFlag(
                strategy_name=strategy_name,
                trading_mode=s.trading_mode,
                strategy_version=s.strategy_version,
                enabled=False,
                updated_at=_NOW,
            )

    class _FakeSessionStateStore:
        def load(self, **kwargs): return None
        def save(self, state=None, **kwargs): pass
        def list_by_session(self, **kwargs): return []

    class _FakeWatchlistStore:
        def list_enabled(self, *args): return list(s.symbols)
        def list_ignored(self, *args): return []

    audit_store = _RecordingAuditStore()

    class _FakeRuntimeCtx:
        connection = _FakeConn2()
        store_lock = None
        order_store = _RecordingOrderStore()
        strategy_weight_store = None
        trading_status_store = _FakeTradingStatusStore()
        position_store = _FakePositionStore()
        daily_session_state_store = _FakeSessionStateStore()
        audit_event_store = audit_store
        strategy_flag_store = _FakeStrategyFlagStore()
        watchlist_store = _FakeWatchlistStore()
        def commit(self): pass

    runtime_ctx = _FakeRuntimeCtx()

    supervisor = RuntimeSupervisor(
        settings=s,
        runtime=runtime_ctx,
        broker=_FakeBroker(),
        market_data=_FakeMarketData(),
        stream=None,
        close_runtime_fn=lambda _: None,
        connection_checker=lambda _: True,
        cycle_runner=lambda **kwargs: SimpleNamespace(intents=[]),
        cycle_intent_executor=lambda **kwargs: SimpleNamespace(
            submitted_exit_count=0, failed_exit_count=0
        ),
        order_dispatcher=lambda **kwargs: {"submitted_count": 0},
        option_chain_adapter=adapter,
    )
    # Pre-seed session state to bypass session-open DB writes
    supervisor._session_equity_baseline[_SESSION_DATE] = 10_000.0
    supervisor._session_capital_weights[_SESSION_DATE] = {}
    supervisor._session_sharpes[_SESSION_DATE] = {}
    return supervisor, runtime_ctx


def test_option_chains_fetched_event_emitted_when_adapter_set() -> None:
    """option_chains_fetched audit event is emitted once per cycle when adapter is configured."""
    adapter = _FakeOptionChainAdapter()  # returns [] for every symbol
    supervisor, ctx = _make_supervisor_with_option_adapter(adapter)

    supervisor.run_cycle_once(now=lambda: _NOW)

    fetched_events = [
        e for e in ctx.audit_event_store.appended
        if e.event_type == "option_chains_fetched"
    ]
    assert len(fetched_events) == 1


def test_option_chains_fetched_payload_shows_zero_when_chains_empty() -> None:
    """When all chain fetches return [], the payload contains 0 for every symbol."""
    adapter = _FakeOptionChainAdapter()  # returns [] for all symbols
    supervisor, ctx = _make_supervisor_with_option_adapter(adapter)

    supervisor.run_cycle_once(now=lambda: _NOW)

    fetched_events = [
        e for e in ctx.audit_event_store.appended
        if e.event_type == "option_chains_fetched"
    ]
    assert len(fetched_events) == 1
    payload = fetched_events[0].payload
    # _make_settings uses SYMBOLS="AAPL,MSFT"
    assert payload == {"AAPL": 0, "MSFT": 0}
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/unit/test_supervisor_weights.py::test_option_chains_fetched_event_emitted_when_adapter_set tests/unit/test_supervisor_weights.py::test_option_chains_fetched_payload_shows_zero_when_chains_empty -v
```

Expected: Both FAIL — no `option_chains_fetched` event is emitted yet.

- [ ] **Step 3: Add audit event emission to `supervisor.py`**

In `src/alpaca_bot/runtime/supervisor.py`, locate the `if self._option_chain_adapter is not None:` block
that ends around line 699 (after the `with _store_lock` strategy factory loop). Insert the
audit event emission immediately after that closing brace, before the open option positions block:

```python
        # Fetch option chains and append option strategies when adapter is configured.
        # breakout_calls is NOT in STRATEGY_REGISTRY — it is a factory that closes over chains.
        option_chains_by_symbol: dict = {}
        option_order_store = getattr(self.runtime, "option_order_store", None)
        if self._option_chain_adapter is not None:
            for symbol in self.settings.symbols:
                try:
                    chains = self._option_chain_adapter.get_option_chain(symbol, self.settings)
                    if chains:
                        option_chains_by_symbol[symbol] = chains
                except Exception:
                    logger.exception("option chain fetch failed for %s", symbol)
            _flag_store = getattr(self.runtime, "strategy_flag_store", None)
            _store_lock = getattr(self.runtime, "store_lock", None)
            with _store_lock if _store_lock is not None else contextlib.nullcontext():
                for opt_name in OPTION_STRATEGY_NAMES:
                    if _flag_store is not None:
                        _flag = _flag_store.load(
                            strategy_name=opt_name,
                            trading_mode=self.settings.trading_mode,
                            strategy_version=self.settings.strategy_version,
                        )
                        if _flag is not None and not _flag.enabled:
                            continue
                    factory = OPTION_STRATEGY_FACTORIES[opt_name]
                    active_strategies.append(
                        (opt_name, factory(option_chains_by_symbol))
                    )
            # ↓ NEW: emit chain health event (0 = chain empty or failed)
            option_chain_counts = {
                sym: len(chains) for sym, chains in option_chains_by_symbol.items()
            }
            for sym in self.settings.symbols:
                option_chain_counts.setdefault(sym, 0)
            self._append_audit(
                AuditEvent(
                    event_type="option_chains_fetched",
                    payload=option_chain_counts,
                    created_at=timestamp,
                )
            )
```

The exact edit: find the line `active_strategies.append(` block ending with `)` and the
closing of the `with _store_lock` block. Insert the three new statements after that block,
still inside the outer `if self._option_chain_adapter is not None:` block.

- [ ] **Step 4: Run the new tests**

```bash
pytest tests/unit/test_supervisor_weights.py::test_option_chains_fetched_event_emitted_when_adapter_set tests/unit/test_supervisor_weights.py::test_option_chains_fetched_payload_shows_zero_when_chains_empty -v
```

Expected: Both PASS.

- [ ] **Step 5: Run full test suite**

```bash
pytest -x
```

Expected: All tests pass. If there are failures, fix them before committing.

- [ ] **Step 6: Commit**

```bash
git add src/alpaca_bot/runtime/supervisor.py tests/unit/test_supervisor_weights.py
git commit -m "feat: emit option_chains_fetched audit event per cycle with per-symbol contract counts"
```
