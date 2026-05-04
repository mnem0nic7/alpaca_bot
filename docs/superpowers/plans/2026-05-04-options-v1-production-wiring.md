# Options v1 Production Wiring Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire `AlpacaOptionChainAdapter` and `AlpacaBroker` into the production supervisor so options trading can be enabled at runtime via `ENABLE_OPTIONS_TRADING=true`.

**Architecture:** Add `enable_options_trading: bool` to `Settings`; add `AlpacaOptionChainAdapter.from_settings()`; update `RuntimeSupervisor.from_settings()` to wire both adapters when the flag is true; document all options env vars in DEPLOYMENT.md.

**Tech Stack:** Python, alpaca-py (`OptionHistoricalDataClient`), pytest

---

## Files

- Modify: `src/alpaca_bot/config/__init__.py` — add `enable_options_trading` field + parse
- Modify: `src/alpaca_bot/execution/option_chain.py` — add `from_settings()` classmethod
- Modify: `src/alpaca_bot/runtime/supervisor.py` — update `from_settings()` to wire options
- Modify: `DEPLOYMENT.md` — add options env var documentation block
- Modify: `tests/unit/test_option_domain_settings.py` — 2 new tests for new Settings field
- Modify: `tests/unit/test_option_chain.py` — 1 new test for `from_settings()` factory
- Modify: `tests/unit/test_runtime_supervisor.py` — extend existing `from_settings` test + add options-enabled variant

---

### Task 1: Add `ENABLE_OPTIONS_TRADING` to Settings

**Files:**
- Modify: `src/alpaca_bot/config/__init__.py:138-140`
- Modify: `src/alpaca_bot/config/__init__.py:297-300`
- Test: `tests/unit/test_option_domain_settings.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_option_domain_settings.py`:

```python
def test_enable_options_trading_defaults_false():
    from alpaca_bot.config import Settings
    s = Settings.from_env(_base_env())
    assert s.enable_options_trading is False


def test_enable_options_trading_parsed_true():
    from alpaca_bot.config import Settings
    env = _base_env()
    env["ENABLE_OPTIONS_TRADING"] = "true"
    s = Settings.from_env(env)
    assert s.enable_options_trading is True
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/unit/test_option_domain_settings.py::test_enable_options_trading_defaults_false tests/unit/test_option_domain_settings.py::test_enable_options_trading_parsed_true -v
```

Expected: FAIL with `TypeError: Settings.__init__() got an unexpected keyword argument 'enable_options_trading'`

- [ ] **Step 3: Add `enable_options_trading` field to Settings dataclass**

In `src/alpaca_bot/config/__init__.py`, after line 140 (`option_delta_target: float = 0.50`):

```python
    option_delta_target: float = 0.50
    enable_options_trading: bool = False
```

- [ ] **Step 4: Parse `ENABLE_OPTIONS_TRADING` in `from_env()`**

In `src/alpaca_bot/config/__init__.py`, after the `option_delta_target` line in `from_env()` (currently line 299):

```python
            option_delta_target=float(values.get("OPTION_DELTA_TARGET", "0.50")),
            enable_options_trading=_parse_bool(
                "ENABLE_OPTIONS_TRADING", values.get("ENABLE_OPTIONS_TRADING", "false")
            ),
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
pytest tests/unit/test_option_domain_settings.py -v
```

Expected: all 9 tests PASS (7 existing + 2 new)

- [ ] **Step 6: Run full suite to check no regressions**

```bash
pytest tests/unit/ -q
```

Expected: all tests PASS

- [ ] **Step 7: Commit**

```bash
git add src/alpaca_bot/config/__init__.py tests/unit/test_option_domain_settings.py
git commit -m "feat: add ENABLE_OPTIONS_TRADING flag to Settings (default false)"
```

---

### Task 2: Add `AlpacaOptionChainAdapter.from_settings()`

**Files:**
- Modify: `src/alpaca_bot/execution/option_chain.py`
- Test: `tests/unit/test_option_chain.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_option_chain.py`:

```python
def test_from_settings_constructs_adapter_with_injected_factory():
    """from_settings() wires a client built by _client_factory and returns an adapter."""
    from alpaca_bot.execution.option_chain import AlpacaOptionChainAdapter
    from tests.unit.helpers import _base_env
    from alpaca_bot.config import Settings

    built_clients = []

    class FakeClient:
        pass

    def fake_factory(api_key, secret_key):
        built_clients.append((api_key, secret_key))
        return FakeClient()

    settings = Settings.from_env(_base_env())
    adapter = AlpacaOptionChainAdapter.from_settings(settings, _client_factory=fake_factory)

    assert isinstance(adapter, AlpacaOptionChainAdapter)
    assert len(built_clients) == 1
    api_key, secret_key = built_clients[0]
    assert api_key  # non-empty
    assert secret_key  # non-empty
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/unit/test_option_chain.py::test_from_settings_constructs_adapter_with_injected_factory -v
```

Expected: FAIL with `AttributeError: type object 'AlpacaOptionChainAdapter' has no attribute 'from_settings'`

- [ ] **Step 3: Add `from_settings()` to `AlpacaOptionChainAdapter`**

In `src/alpaca_bot/execution/option_chain.py`, after the `__init__` method (line 19):

```python
    def __init__(self, option_data_client: Any) -> None:
        self._client = option_data_client

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        *,
        _client_factory: Any | None = None,
    ) -> "AlpacaOptionChainAdapter":
        from alpaca.data.historical import OptionHistoricalDataClient  # type: ignore[import]
        from alpaca_bot.execution.alpaca import resolve_alpaca_credentials

        api_key, secret_key, _paper = resolve_alpaca_credentials(settings)
        factory = _client_factory if _client_factory is not None else OptionHistoricalDataClient
        return cls(factory(api_key, secret_key))
```

- [ ] **Step 4: Run test to verify it passes**

```bash
pytest tests/unit/test_option_chain.py -v
```

Expected: all 7 tests PASS (6 existing + 1 new)

- [ ] **Step 5: Run full suite**

```bash
pytest tests/unit/ -q
```

Expected: all tests PASS

- [ ] **Step 6: Commit**

```bash
git add src/alpaca_bot/execution/option_chain.py tests/unit/test_option_chain.py
git commit -m "feat: add AlpacaOptionChainAdapter.from_settings() using OptionHistoricalDataClient"
```

---

### Task 3: Wire options in `RuntimeSupervisor.from_settings()`

**Files:**
- Modify: `src/alpaca_bot/runtime/supervisor.py:138-146`
- Test: `tests/unit/test_runtime_supervisor.py`

- [ ] **Step 1: Write the failing test**

Find `test_runtime_supervisor_from_settings_bootstraps_runtime_and_builds_adapters` in
`tests/unit/test_runtime_supervisor.py`. After it, add a new test:

```python
def test_runtime_supervisor_from_settings_wires_option_adapters_when_enabled(monkeypatch) -> None:
    """When ENABLE_OPTIONS_TRADING=true, from_settings() wires option_chain_adapter and option_broker."""
    module, RuntimeSupervisor, _SupervisorCycleReport = load_supervisor_api()
    env = _base_env()
    env["ENABLE_OPTIONS_TRADING"] = "true"
    from alpaca_bot.config import Settings
    settings = Settings.from_env(env)
    runtime = make_runtime_context(settings)
    broker = FakeBroker()
    market_data = FakeMarketData(intraday_bars_by_symbol={}, daily_bars_by_symbol={})
    stream = FakeStream()

    from alpaca_bot.execution.option_chain import AlpacaOptionChainAdapter
    fake_chain_adapter = AlpacaOptionChainAdapter(object())

    monkeypatch.setattr(module, "bootstrap_runtime", lambda s: runtime)
    monkeypatch.setattr(module.AlpacaBroker, "from_settings", lambda s: broker)
    monkeypatch.setattr(module.AlpacaMarketDataAdapter, "from_settings", lambda s: market_data)
    monkeypatch.setattr(module.AlpacaTradingStreamAdapter, "from_settings", lambda s: stream)
    monkeypatch.setattr(
        module.AlpacaOptionChainAdapter,
        "from_settings",
        lambda s: fake_chain_adapter,
    )

    supervisor = RuntimeSupervisor.from_settings(settings)

    assert supervisor._option_chain_adapter is fake_chain_adapter
    assert supervisor._option_broker is broker
```

Also update the existing `test_runtime_supervisor_from_settings_bootstraps_runtime_and_builds_adapters`
to assert that `_option_chain_adapter` is `None` when `ENABLE_OPTIONS_TRADING` is not set:

```python
    # options adapters are absent when flag is off (default)
    assert supervisor._option_chain_adapter is None
    assert supervisor._option_broker is None
```

(Add these two lines after the existing `assert calls == {...}` block.)

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/unit/test_runtime_supervisor.py::test_runtime_supervisor_from_settings_bootstraps_runtime_and_builds_adapters tests/unit/test_runtime_supervisor.py::test_runtime_supervisor_from_settings_wires_option_adapters_when_enabled -v
```

Expected: FAIL — the new test fails because options are never wired; the existing test may fail on the new assertions if options are accidentally already wired (they shouldn't be).

- [ ] **Step 3: Update `RuntimeSupervisor.from_settings()`**

In `src/alpaca_bot/runtime/supervisor.py`, replace the current `from_settings()` (lines 137-146):

```python
    @classmethod
    def from_settings(cls, settings: Settings) -> "RuntimeSupervisor":
        broker = AlpacaBroker.from_settings(settings)
        option_chain_adapter = None
        option_broker = None
        if settings.enable_options_trading:
            option_chain_adapter = AlpacaOptionChainAdapter.from_settings(settings)
            option_broker = broker
        return cls(
            settings=settings,
            runtime=bootstrap_runtime(settings),
            broker=broker,
            market_data=AlpacaMarketDataAdapter.from_settings(settings),
            stream=AlpacaTradingStreamAdapter.from_settings(settings),
            notifier=build_notifier(settings),
            option_chain_adapter=option_chain_adapter,
            option_broker=option_broker,
        )
```

Also add the import of `AlpacaOptionChainAdapter` to the import block at the top of `supervisor.py`.
Find the existing imports from `alpaca_bot.execution`:

```python
from alpaca_bot.execution.alpaca import (
    AlpacaBroker,
    AlpacaMarketDataAdapter,
    AlpacaTradingStreamAdapter,
)
```

Add the option chain import alongside:

```python
from alpaca_bot.execution.alpaca import (
    AlpacaBroker,
    AlpacaMarketDataAdapter,
    AlpacaTradingStreamAdapter,
)
from alpaca_bot.execution.option_chain import AlpacaOptionChainAdapter
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/unit/test_runtime_supervisor.py::test_runtime_supervisor_from_settings_bootstraps_runtime_and_builds_adapters tests/unit/test_runtime_supervisor.py::test_runtime_supervisor_from_settings_wires_option_adapters_when_enabled -v
```

Expected: both PASS

- [ ] **Step 5: Run full suite**

```bash
pytest tests/unit/ -q
```

Expected: all tests PASS

- [ ] **Step 6: Commit**

```bash
git add src/alpaca_bot/runtime/supervisor.py tests/unit/test_runtime_supervisor.py
git commit -m "feat: wire AlpacaOptionChainAdapter and option_broker in from_settings() when ENABLE_OPTIONS_TRADING=true"
```

---

### Task 4: Document options env vars in DEPLOYMENT.md

**Files:**
- Modify: `DEPLOYMENT.md`

- [ ] **Step 1: Add options block to the env file template**

In `DEPLOYMENT.md`, after the line `FLATTEN_TIME=15:45` and before the blank line leading to
`ALPACA_PAPER_API_KEY`, insert:

```dotenv
# Options trading (disabled by default; set ENABLE_OPTIONS_TRADING=true to activate)
# ENABLE_OPTIONS_TRADING=false
# OPTION_DTE_MIN=21        # minimum days-to-expiry when selecting contracts
# OPTION_DTE_MAX=60        # maximum days-to-expiry when selecting contracts
# OPTION_DELTA_TARGET=0.50 # target delta for contract selection (0 < value ≤ 1.0)
```

- [ ] **Step 2: Verify the template renders correctly**

```bash
grep -A 6 "FLATTEN_TIME" DEPLOYMENT.md
```

Expected output shows the options block immediately after `FLATTEN_TIME=15:45`.

- [ ] **Step 3: Commit**

```bash
git add DEPLOYMENT.md
git commit -m "docs: document ENABLE_OPTIONS_TRADING and option tuning env vars in DEPLOYMENT.md"
```

---

## Regression Checklist

After all 4 tasks are committed:

```bash
pytest tests/unit/ -q
```

Expected: all tests PASS (1176 + new tests)

Verify the wiring is live end-to-end by inspecting the supervisor factory:

```bash
python3 -c "
from alpaca_bot.config import Settings
from tests.unit.helpers import _base_env
env = _base_env()
env['ENABLE_OPTIONS_TRADING'] = 'true'
s = Settings.from_env(env)
print('enable_options_trading:', s.enable_options_trading)
print('option_dte_min:', s.option_dte_min)
print('option_dte_max:', s.option_dte_max)
print('option_delta_target:', s.option_delta_target)
"
```

Expected:
```
enable_options_trading: True
option_dte_min: 21
option_dte_max: 60
option_delta_target: 0.5
```
