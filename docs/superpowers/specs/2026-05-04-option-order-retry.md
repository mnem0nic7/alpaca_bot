# Option Order Retry Gap — Spec

**Date:** 2026-05-04

## Problem

`submit_option_limit_entry` and `submit_option_market_exit` in `AlpacaExecutionAdapter`
call `self._trading.submit_order()` directly. All five equity order submission methods
use `_retry_with_backoff(lambda: self._trading.submit_order(request))` to survive
transient Alpaca API errors (429, 5xx, connection-level). Option submissions lack this
wrapper, meaning a single transient failure will drop the order immediately rather than
retrying.

This is not a financial safety issue — paper/live routing is determined by `TradingClient`
construction, which is unaffected. It is a reliability concern that must be fixed before
live activation.

## Fix

Wrap both option `submit_order` calls in `_retry_with_backoff`, exactly mirroring the
equity method pattern.

```python
# Before (submit_option_limit_entry, line 394):
response = self._trading.submit_order(order_data)

# After:
response = _retry_with_backoff(lambda: self._trading.submit_order(order_data))
```

```python
# Before (submit_option_market_exit, line 413):
response = self._trading.submit_order(order_data)

# After:
response = _retry_with_backoff(lambda: self._trading.submit_order(order_data))
```

## Tests

Two new tests in `tests/unit/test_option_chain.py::TestAlpacaExecutionAdapterOptionMethods`:

- `test_submit_option_limit_entry_retries_on_transient_error` — FlakyTradingClient
  raises `"500 Internal Server Error"` on attempt 1, succeeds on attempt 2. Verify
  `calls == 2` and `slept == [1]` (monkeypatched `time.sleep`).
- `test_submit_option_market_exit_retries_on_transient_error` — same pattern for
  the market-sell path.

The existing `test_submit_option_limit_entry_calls_submit_order` and
`test_submit_option_market_exit_calls_submit_order` tests remain valid — a single
success with no retry means the wrapper is transparent.

## Scope

- One file changed: `src/alpaca_bot/execution/alpaca.py` (two lines).
- One test file extended: `tests/unit/test_option_chain.py` (two new tests).
- No migration, no env var, no Settings change.
