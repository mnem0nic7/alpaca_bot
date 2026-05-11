# Option Chain Volume Pre-Filter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reduce the option chain fetch universe from 400+ symbols to only those with meaningful intraday volume, cutting ThreadPoolExecutor batches from ~80 to ~12 and keeping the fetch phase well within the 45-second budget every cycle.

**Architecture:** Three targeted changes: (1) add `option_chain_min_total_volume: int = 0` to `Settings`; (2) insert a one-line filter comprehension in `supervisor.py` before the executor submission; (3) add two tests to the existing option-chain test file. The audit event continues to cover all `intraday_bars_by_symbol` keys — filtered-out symbols appear as count=0. Production deployment requires adding `OPTION_CHAIN_MIN_TOTAL_VOLUME=50000` to `/etc/alpaca_bot/alpaca-bot.env`.

**Tech Stack:** Python dataclass field + `from_env()` parsing; existing `ThreadPoolExecutor` / `as_completed` pattern in `supervisor.py`; existing `_make_supervisor` DI helper in `test_supervisor_option_chains.py`.

---

## Files Affected

| File | Action |
|---|---|
| `src/alpaca_bot/config/__init__.py` | Add `option_chain_min_total_volume` field and `from_env()` parse |
| `src/alpaca_bot/runtime/supervisor.py` | Add filter comprehension before executor submission |
| `tests/unit/test_supervisor_option_chains.py` | Add `Bar` import, extend `_make_supervisor`, add two tests |

---

### Task 1: Add `option_chain_min_total_volume` setting to `Settings`

**Files:**
- Modify: `src/alpaca_bot/config/__init__.py`

- [ ] **Step 1: Add the field to the dataclass**

In `src/alpaca_bot/config/__init__.py`, locate line 148:

```python
    enable_options_trading: bool = False
```

Add the new field immediately after it:

```python
    enable_options_trading: bool = False
    option_chain_min_total_volume: int = 0
```

- [ ] **Step 2: Add the `from_env()` parse**

In `src/alpaca_bot/config/__init__.py`, locate the `enable_options_trading` parse (around line 346–348):

```python
            enable_options_trading=_parse_bool(
                "ENABLE_OPTIONS_TRADING", values.get("ENABLE_OPTIONS_TRADING", "false")
            ),
```

Add immediately after the closing `)`:

```python
            enable_options_trading=_parse_bool(
                "ENABLE_OPTIONS_TRADING", values.get("ENABLE_OPTIONS_TRADING", "false")
            ),
            option_chain_min_total_volume=int(
                values.get("OPTION_CHAIN_MIN_TOTAL_VOLUME", "0")
            ),
```

- [ ] **Step 3: Verify the Settings unit tests still pass**

```bash
pytest tests/unit/test_settings.py -q 2>/dev/null || pytest tests/ -k settings -q
```

Expected: all settings-related tests pass. If no settings test file exists, skip to the next task — the setting will be exercised by the supervisor tests.

- [ ] **Step 4: Commit**

```bash
git add src/alpaca_bot/config/__init__.py
git commit -m "feat: add option_chain_min_total_volume setting (default 0, disables filter)

New env var OPTION_CHAIN_MIN_TOTAL_VOLUME controls minimum total intraday
bar volume required for a symbol to reach the option chain executor.
0 (default) disables the filter — backward compatible."
```

---

### Task 2: Add volume filter in `supervisor.py`

**Files:**
- Modify: `src/alpaca_bot/runtime/supervisor.py` (lines 765–766)

- [ ] **Step 1: Insert the filter comprehension before executor submission**

In `src/alpaca_bot/runtime/supervisor.py`, replace:

```python
            executor = ThreadPoolExecutor(max_workers=5)
            futures = {executor.submit(_fetch_one, sym): sym for sym in intraday_bars_by_symbol}
```

With:

```python
            executor = ThreadPoolExecutor(max_workers=5)
            min_vol = self.settings.option_chain_min_total_volume
            symbols_to_fetch = [
                sym for sym, bars in intraday_bars_by_symbol.items()
                if min_vol == 0 or sum(b.volume for b in bars) >= min_vol
            ]
            futures = {executor.submit(_fetch_one, sym): sym for sym in symbols_to_fetch}
```

- [ ] **Step 2: Confirm the audit event loop is unchanged**

Verify that a few lines below (around line 799) the audit sentinel still reads:

```python
            for sym in intraday_bars_by_symbol:
                option_chain_counts.setdefault(sym, 0)
```

This loop must iterate `intraday_bars_by_symbol` (not `symbols_to_fetch`) so that filtered-out symbols appear in the audit event with a count of 0. Do NOT change this line.

- [ ] **Step 3: Run the existing option chain tests to confirm no regression**

```bash
pytest tests/unit/test_supervisor_option_chains.py -v
```

Expected:
```
PASSED tests/unit/test_supervisor_option_chains.py::test_option_chain_fetch_uses_watchlist_not_settings_symbols
PASSED tests/unit/test_supervisor_option_chains.py::test_option_chain_exception_does_not_block_other_symbols
PASSED tests/unit/test_supervisor_option_chains.py::test_option_chains_fetched_audit_event_keys_match_watchlist
```

All three existing tests pass because `min_vol=0` (default from `_base_env()`) disables the filter — behavior is unchanged.

- [ ] **Step 4: Commit**

```bash
git add src/alpaca_bot/runtime/supervisor.py
git commit -m "feat: pre-filter option chain symbols by intraday volume before executor

Symbols whose total intraday bar volume is below OPTION_CHAIN_MIN_TOTAL_VOLUME
are excluded from the ThreadPoolExecutor fetch. Reduces executor batches from
~80 to ~12 at threshold=50000, keeping the fetch phase inside the 45s budget.
Audit event still reports all intraday_bars_by_symbol keys (0 for filtered)."
```

---

### Task 3: Write and verify tests for the volume filter

**Files:**
- Modify: `tests/unit/test_supervisor_option_chains.py`

- [ ] **Step 1: Add `Bar` import and extend `_make_supervisor`**

At the top of `tests/unit/test_supervisor_option_chains.py`, add `Bar` to the import:

```python
from alpaca_bot.domain import Bar
```

Modify the `_make_supervisor` signature to accept `get_stock_bars` and `extra_env` overrides (preserving default behavior for existing tests):

Replace:

```python
def _make_supervisor(*, adapter, audit_store=None):
    """Build a RuntimeSupervisor wired with a watchlist returning _WATCHLIST_SYMBOLS."""
    RuntimeSupervisor = import_module("alpaca_bot.runtime.supervisor").RuntimeSupervisor
    env = {**_base_env(), "ENABLE_OPTIONS_TRADING": "true"}
    settings = Settings.from_env(env)
```

With:

```python
def _make_supervisor(*, adapter, audit_store=None, get_stock_bars=None, extra_env=None):
    """Build a RuntimeSupervisor wired with a watchlist returning _WATCHLIST_SYMBOLS."""
    RuntimeSupervisor = import_module("alpaca_bot.runtime.supervisor").RuntimeSupervisor
    env = {**_base_env(), "ENABLE_OPTIONS_TRADING": "true", **(extra_env or {})}
    settings = Settings.from_env(env)
    if get_stock_bars is None:
        get_stock_bars = lambda **_: {sym: [] for sym in _WATCHLIST_SYMBOLS}
```

And replace the `market_data` SimpleNamespace inside `_make_supervisor`:

```python
        market_data=SimpleNamespace(
            # Dict keyed by watchlist symbols so intraday_bars_by_symbol has the right
            # keys for the option chain loop to iterate.
            get_stock_bars=lambda **_: {sym: [] for sym in _WATCHLIST_SYMBOLS},
            get_daily_bars=lambda **_: {},
        ),
```

With:

```python
        market_data=SimpleNamespace(
            get_stock_bars=get_stock_bars,
            get_daily_bars=lambda **_: {},
        ),
```

- [ ] **Step 2: Run existing tests to confirm no regression from helper changes**

```bash
pytest tests/unit/test_supervisor_option_chains.py -v
```

Expected: all three existing tests still PASS (default `get_stock_bars` and `extra_env` preserve prior behavior).

- [ ] **Step 3: Write the two new failing tests**

Add these two functions to `tests/unit/test_supervisor_option_chains.py`:

```python
def test_volume_filter_excludes_low_volume_symbols() -> None:
    """Symbols below OPTION_CHAIN_MIN_TOTAL_VOLUME must not reach the adapter."""
    # ACHR and METC each have 100,000 volume (>= threshold=50,000) → fetched.
    # SLS has 10,000 volume (< threshold) → must NOT be fetched.
    bars_by_symbol = {
        "ACHR": [Bar(symbol="ACHR", timestamp=_NOW, open=10.0, high=11.0, low=9.0, close=10.5, volume=100_000)],
        "METC": [Bar(symbol="METC", timestamp=_NOW, open=20.0, high=21.0, low=19.0, close=20.5, volume=100_000)],
        "SLS":  [Bar(symbol="SLS",  timestamp=_NOW, open=5.0,  high=6.0,  low=4.5,  close=5.5,  volume=10_000)],
    }
    adapter = RecordingOptionChainAdapter()
    supervisor = _make_supervisor(
        adapter=adapter,
        get_stock_bars=lambda **_: bars_by_symbol,
        extra_env={"OPTION_CHAIN_MIN_TOTAL_VOLUME": "50000"},
    )
    supervisor.run_cycle_once(now=lambda: _NOW)

    assert "ACHR" in adapter.fetched, "ACHR (volume=100k) must be fetched"
    assert "METC" in adapter.fetched, "METC (volume=100k) must be fetched"
    assert "SLS" not in adapter.fetched, (
        f"SLS (volume=10k < threshold=50k) must not be fetched; got {adapter.fetched!r}"
    )


def test_volume_filter_zero_disables_filter() -> None:
    """OPTION_CHAIN_MIN_TOTAL_VOLUME=0 must fetch all symbols regardless of volume."""
    # SLS has only 1 share of volume — far below any practical threshold.
    # With min_vol=0, the filter is disabled and SLS must still be fetched.
    bars_by_symbol = {
        "ACHR": [Bar(symbol="ACHR", timestamp=_NOW, open=10.0, high=11.0, low=9.0, close=10.5, volume=100_000)],
        "METC": [Bar(symbol="METC", timestamp=_NOW, open=20.0, high=21.0, low=19.0, close=20.5, volume=100_000)],
        "SLS":  [Bar(symbol="SLS",  timestamp=_NOW, open=5.0,  high=6.0,  low=4.5,  close=5.5,  volume=1)],
    }
    adapter = RecordingOptionChainAdapter()
    supervisor = _make_supervisor(
        adapter=adapter,
        get_stock_bars=lambda **_: bars_by_symbol,
        # extra_env omitted → OPTION_CHAIN_MIN_TOTAL_VOLUME defaults to "0"
    )
    supervisor.run_cycle_once(now=lambda: _NOW)

    assert set(adapter.fetched) == {"ACHR", "METC", "SLS"}, (
        f"All symbols must be fetched when min_vol=0; got {adapter.fetched!r}"
    )
```

- [ ] **Step 4: Run to verify the new tests FAIL before the implementation**

Wait — the implementation was committed in Task 2. If you are running these tasks in order, the tests should now PASS. Run to confirm:

```bash
pytest tests/unit/test_supervisor_option_chains.py -v
```

Expected output:
```
PASSED tests/unit/test_supervisor_option_chains.py::test_option_chain_fetch_uses_watchlist_not_settings_symbols
PASSED tests/unit/test_supervisor_option_chains.py::test_option_chain_exception_does_not_block_other_symbols
PASSED tests/unit/test_supervisor_option_chains.py::test_option_chains_fetched_audit_event_keys_match_watchlist
PASSED tests/unit/test_supervisor_option_chains.py::test_volume_filter_excludes_low_volume_symbols
PASSED tests/unit/test_supervisor_option_chains.py::test_volume_filter_zero_disables_filter
```

If `test_volume_filter_excludes_low_volume_symbols` FAILS (SLS is still being fetched), verify that:
1. `option_chain_min_total_volume` was added to `Settings` (Task 1)
2. The filter comprehension was added to `supervisor.py` (Task 2)
3. `_make_supervisor` is correctly passing `extra_env` to `Settings.from_env()`

- [ ] **Step 5: Run the full test suite**

```bash
pytest --tb=short -q
```

Expected: all tests pass with no regressions.

- [ ] **Step 6: Commit**

```bash
git add tests/unit/test_supervisor_option_chains.py
git commit -m "test: verify option chain volume filter excludes low-volume symbols

Two new tests: one verifies symbols below OPTION_CHAIN_MIN_TOTAL_VOLUME
are excluded from the executor fetch; one verifies min_vol=0 disables
the filter and fetches all symbols."
```

---

## Production Deployment Note

After deploying the code, add the env var to `/etc/alpaca_bot/alpaca-bot.env`:

```
OPTION_CHAIN_MIN_TOTAL_VOLUME=50000
```

Then redeploy:

```bash
./scripts/deploy.sh /etc/alpaca_bot/alpaca-bot.env
```

This is a manual deploy step, not tracked by the code plan. Without this env var, the deployed code uses the default (0 = no filter) and behavior is identical to before — safe to deploy first.

## Rollback

Remove `OPTION_CHAIN_MIN_TOTAL_VOLUME` from the env file and redeploy. The setting defaults to 0, disabling the filter and restoring full-universe fetch behavior.
