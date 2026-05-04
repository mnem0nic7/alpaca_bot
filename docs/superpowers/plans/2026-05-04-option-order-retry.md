# Option Order Retry Gap — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wrap `submit_option_limit_entry` and `submit_option_market_exit` in `_retry_with_backoff` so transient Alpaca API errors are retried like all equity order methods.

**Architecture:** Two-line change in `AlpacaExecutionAdapter`. Two new tests verify retry fires on a transient error.

**Tech Stack:** Python, pytest, existing `_retry_with_backoff` in `execution/alpaca.py`.

---

### Task 1: Add retry tests for option order methods

**Files:**
- Modify: `tests/unit/test_option_chain.py` — add 2 tests to `TestAlpacaExecutionAdapterOptionMethods`

- [ ] **Step 1: Write the two failing tests**

Add after `test_submit_option_market_exit_calls_submit_order` in the class:

```python
def test_submit_option_limit_entry_retries_on_transient_error(
    self, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alpaca_bot.execution.alpaca import AlpacaExecutionAdapter
    from tests.unit.helpers import _base_env
    from alpaca_bot.config import Settings
    from dataclasses import dataclass

    slept: list[float] = []
    monkeypatch.setattr("alpaca_bot.execution.alpaca.time.sleep", lambda s: slept.append(s))

    calls = 0

    class FlakyTradingClient:
        def submit_order(self, order_data):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError("500 Internal Server Error")

            class FakeOrder:
                id = "broker-456"
                client_order_id = "option:v1:2024-06-01:AAPL240701C00100000:entry:2024-06-01T14:00:00+00:00"
                symbol = "AAPL240701C00100000"
                side = "buy"
                status = "accepted"
                qty = 2
            return FakeOrder()

    adapter = AlpacaExecutionAdapter(FlakyTradingClient(), settings=Settings.from_env(_base_env()))
    result = adapter.submit_option_limit_entry(
        occ_symbol="AAPL240701C00100000",
        quantity=2,
        limit_price=3.00,
        client_order_id="option:v1:2024-06-01:AAPL240701C00100000:entry:2024-06-01T14:00:00+00:00",
    )
    assert result.broker_order_id == "broker-456"
    assert calls == 2
    assert slept == [1]

def test_submit_option_market_exit_retries_on_transient_error(
    self, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alpaca_bot.execution.alpaca import AlpacaExecutionAdapter
    from tests.unit.helpers import _base_env
    from alpaca_bot.config import Settings

    slept: list[float] = []
    monkeypatch.setattr("alpaca_bot.execution.alpaca.time.sleep", lambda s: slept.append(s))

    calls = 0

    class FlakyTradingClient:
        def submit_order(self, order_data):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError("500 Internal Server Error")

            class FakeOrder:
                id = "broker-789"
                client_order_id = "option:v1:2024-06-01:AAPL240701C00100000:sell:2024-06-01T15:50:00+00:00"
                symbol = "AAPL240701C00100000"
                side = "sell"
                status = "accepted"
                qty = 2
            return FakeOrder()

    adapter = AlpacaExecutionAdapter(FlakyTradingClient(), settings=Settings.from_env(_base_env()))
    result = adapter.submit_option_market_exit(
        occ_symbol="AAPL240701C00100000",
        quantity=2,
        client_order_id="option:v1:2024-06-01:AAPL240701C00100000:sell:2024-06-01T15:50:00+00:00",
    )
    assert result.broker_order_id == "broker-789"
    assert calls == 2
    assert slept == [1]
```

Also add `import pytest` at the top of the file if it isn't already imported.

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/unit/test_option_chain.py::TestAlpacaExecutionAdapterOptionMethods -v
```

Expected: `test_submit_option_limit_entry_retries_on_transient_error` and
`test_submit_option_market_exit_retries_on_transient_error` FAIL (calls==1, not 2;
or RuntimeError propagates).

- [ ] **Step 3: Commit failing tests**

```bash
git add tests/unit/test_option_chain.py
git commit -m "test: add failing retry tests for option order methods"
```

---

### Task 2: Wrap option order calls in `_retry_with_backoff`

**Files:**
- Modify: `src/alpaca_bot/execution/alpaca.py` lines 394 and 413

- [ ] **Step 4: Apply the two-line fix**

In `submit_option_limit_entry` (around line 394), change:
```python
        response = self._trading.submit_order(order_data)
        return _parse_broker_order(response)
```
to:
```python
        return _parse_broker_order(
            _retry_with_backoff(lambda: self._trading.submit_order(order_data))
        )
```

In `submit_option_market_exit` (around line 413), change:
```python
        response = self._trading.submit_order(order_data)
        return _parse_broker_order(response)
```
to:
```python
        return _parse_broker_order(
            _retry_with_backoff(lambda: self._trading.submit_order(order_data))
        )
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
pytest tests/unit/test_option_chain.py -v
```

Expected: all 9 tests pass (7 existing + 2 new).

- [ ] **Step 6: Run full test suite**

```bash
pytest tests/unit/ -q
```

Expected: all tests pass.

- [ ] **Step 7: Commit**

```bash
git add src/alpaca_bot/execution/alpaca.py tests/unit/test_option_chain.py docs/superpowers/specs/2026-05-04-option-order-retry.md docs/superpowers/plans/2026-05-04-option-order-retry.md
git commit -m "fix: wrap option order submit calls in _retry_with_backoff"
```
