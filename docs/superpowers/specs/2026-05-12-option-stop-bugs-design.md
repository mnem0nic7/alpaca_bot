# Option Stop Bugs — Design Spec

**Date:** 2026-05-12  
**Incident:** ALHC, AMLX, AROC, BCRX, BFLY, CMG, CNK — 534 stop-dispatch failures today.  
All 7 option positions had `stop_price = entry_price`. Alpaca rejected every stop with error
42210000 ("stop price must be less than current price"). The dispatch loop re-queued endlessly.

---

## Root Causes

### Bug 1 — Stop price rounds to entry price

`startup_recovery.py` line 135:

```python
stop_price = round(resolved_entry_price * (1 - settings.breakout_stop_buffer_pct), 2)
```

`BREAKOUT_STOP_BUFFER_PCT=0.001` (0.1%). On a $1.20 option:
`1.20 × 0.999 = 1.1988 → round 2dp → $1.20` — identical to entry. Alpaca rejects with 42210000.

The 0.1% buffer was calibrated for equities ($50–$200/share). Options trade at $0.50–$5.00 where
one tick ($0.01) is already 0.2–2% of price. A 0.1% buffer disappears after two-decimal rounding.

Additionally: `engine.py` line 754 sets `stop_price=None` on option entries — options' defined risk
is the premium paid, so no trailing stop is set at entry time. `startup_recovery.py` is therefore
the only stop-setter for all option positions, and it hits this rounding problem.

### Bug 2 — Infinite re-queue loop

When Alpaca returns 42210000, `order_dispatch.py` line 349 saves the stop as `status="error"`.

`startup_recovery.py` lines 448–449 re-queues a stop when:
```python
existing_recovery_stop.status not in {"expired", "cancelled", "canceled", "error"}
```

`"error"` is in the exclusion set, so the condition re-queues only when `status NOT in {...}` —
wait, reading carefully: the `continue` fires when status IS in that set. So "error" causes
`continue`, meaning: **if error → continue (skip re-queue)**. This should prevent the loop. Let me
re-examine.

Actually: line 448:
```python
if existing_recovery_stop is not None and existing_recovery_stop.status not in {
    "expired", "cancelled", "canceled", "error"
}:
    continue
```

This means: if existing stop IS NOT in terminal states → skip (no re-queue). If existing stop IS
in terminal states (error, canceled, etc.) → fall through and re-queue. "error" IS a terminal
state in the set, so when status="error" the code FALLS THROUGH and re-queues. This is the loop.

Every 60-second cycle:
1. `recover_startup_state()` called (not only at startup — line 259 of supervisor.py calls it every
   cycle with `audit_event_type=None`)
2. For OCC symbols, `broker_pos.market_value` is often `None` in Alpaca paper trading, so
   `current_price=None`
3. `current_price is None` prevents the "stop >= market → queue exit" path from firing (line 392:
   `if current_price is not None and pos.stop_price >= current_price`)
4. Falls to stop-queueing path with stop_price = entry_price
5. Dispatch rejects (42210000), marks "error"
6. Next cycle: "error" → fall through re-queue guard → re-queue same bad stop → repeat

### Bug 3 — strategy_name always 'breakout' for option positions

`startup_recovery.py` line 89 default parameter: `default_strategy_name: str = "breakout"`.

For broker-missing positions (no prior local record), line 147 writes:
`strategy_name=default_strategy_name` → always "breakout".

`_infer_strategy_name_from_client_order_id()` (line 709) only checks `STRATEGY_REGISTRY`, which
contains equity strategy names. "option" prefix (OCC client_order_ids use `option:v1-breakout:...`)
is not in `STRATEGY_REGISTRY` → returns "breakout" fallback.

The 7 affected positions and all their orders are labeled strategy_name='breakout' in the DB,
making the options dashboard misleading and breaking strategy-filtered queries.

---

## Scope

Three changes to two files. No schema changes, no new env vars beyond `OPTION_STOP_BUFFER_PCT`.

**Files:**
- `src/alpaca_bot/config/__init__.py` — add `option_stop_buffer_pct: float`
- `src/alpaca_bot/runtime/startup_recovery.py` — OCC detection, option-aware stop, skip when no price
- `src/alpaca_bot/runtime/order_dispatch.py` — circuit breaker for error 42210000
- `tests/unit/test_startup_recovery.py` — new tests for option paths
- `tests/unit/test_order_dispatch.py` — new test for circuit breaker

---

## Design

### OCC Symbol Detection

```python
import re
_OCC_PATTERN = re.compile(r'^[A-Z]{1,6}\d{6}[CP]\d{8}$')

def _is_option_symbol(symbol: str) -> bool:
    return bool(_OCC_PATTERN.match(symbol))
```

OCC format: `ALHC260618P00017500` = ticker (1–6 alpha) + YYMMDD + P/C + 8-digit strike×1000.
This regex is sufficient to distinguish OCC symbols from equity tickers, which are 1–5 alpha-only.

### Bug 1 Fix — Option stop buffer in Settings + startup_recovery

**Settings** (`config/__init__.py`):
```python
option_stop_buffer_pct: float  # e.g. 0.10 for 10%
```

Parsed in `from_env()`:
```python
option_stop_buffer_pct=float(os.environ.get("OPTION_STOP_BUFFER_PCT", "0.10")),
```

Default 10% ensures a $1.20 option gets stop at $1.08, well below any plausible market price.

**Startup recovery** — broker-missing path (lines 131–159):

```python
is_option = _is_option_symbol(broker_position.symbol)
if is_option:
    buffer_pct = settings.option_stop_buffer_pct
else:
    buffer_pct = settings.breakout_stop_buffer_pct
stop_price = round(resolved_entry_price * (1 - buffer_pct), 2)
```

Additionally: ensure `stop_price < resolved_entry_price` after rounding. If after applying the
buffer the rounded result equals entry_price (pathological case: entry=$0.01), set
`stop_price = 0.0` and do not queue a stop (same as missing-entry-price path).

**Startup recovery** — active positions path (lines 439–491):

When `current_price is None` and `_is_option_symbol(pos.symbol)` → **skip stop queueing**. Do not
re-queue a stop when there is no current price available for an option, because:
1. We cannot determine whether the stop is above or below market.
2. The broker already rejected the stop with 42210000; re-queuing is pointless.
3. Options have defined risk = premium paid; a missing stop is less dangerous than for equities.

Emit an audit event `option_stop_skipped_no_price` when this skip fires, so operators can see it.

For equity symbols, the behavior is unchanged — continue to queue the recovery stop even when
current_price is None (existing behavior protects against open positions with no stop at all).

### Bug 2 Fix — Circuit breaker in order_dispatch.py

In `order_dispatch.py` failure handler (after line 320), detect Alpaca error 42210000:

```python
UNRECOVERABLE_STOP_ERRORS = frozenset({"42210000"})

def _is_unrecoverable_stop_error(exc: Exception) -> bool:
    msg = str(exc)
    return any(code in msg for code in UNRECOVERABLE_STOP_ERRORS)
```

When `_is_unrecoverable_stop_error(exc)` is True and `order.intent_type == "stop"`:
- Save order as `status="canceled"` (not "error") — this prevents startup_recovery from
  re-queuing it, because "canceled" IS in the terminal set AND does not trigger re-queue
  (since the re-queue guard `continue`s when status not in terminal set, and "canceled" IS in
  terminal set, so the guard does NOT continue — wait, let me re-read:

  ```python
  if existing_recovery_stop is not None and existing_recovery_stop.status not in {
      "expired", "cancelled", "canceled", "error"
  }:
      continue  # <- skip re-queue
  ```

  If status IS in {"expired", "cancelled", "canceled", "error"}, the condition is False → no
  continue → falls through to re-queue. "canceled" falls through. So saving as "canceled" does
  NOT prevent re-queue either — both "error" and "canceled" fall through and get re-queued!

  This confirms Bug 2's re-queue guard is inverted for all terminal states. The fix needs to
  be in startup_recovery's re-queue logic, not just in the status.

**Revised Bug 2 fix — guard in startup_recovery:**

In the re-queue guard (lines 448–451), add a second condition: don't re-queue if the existing
stop has the same stop_price as the position's current stop_price. This prevents cycling the
same invalid price even after a terminal failure:

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
    if not is_terminal or (is_terminal and same_price):
        continue
```

Semantics:
- Non-terminal (pending_submit, new, accepted, etc.) → `continue` (existing behavior)
- Terminal with same price → `continue` (new: don't retry the same rejected price)
- Terminal with different price → fall through (new stop price is valid, re-queue is safe)

Additionally, change status to `"canceled"` (not `"error"`) in order_dispatch for 42210000 to
give cleaner audit trail and better signal for future status queries. Emit:

```python
AuditEvent(
    event_type="order_dispatch_stop_price_rejected",
    symbol=order.symbol,
    payload={
        "client_order_id": order.client_order_id,
        "stop_price": order.stop_price,
        "error": str(exc),
    },
)
```

Non-42210000 failures continue to use `status="error"` (existing behavior). The same_price guard
in startup_recovery also catches those: if a stop fails for any unrecoverable reason and the price
hasn't changed, it won't be resubmitted.

### Bug 3 Fix — strategy_name for OCC symbols

**Broker-missing path** (`startup_recovery.py` lines 131–159):

```python
is_option = _is_option_symbol(broker_position.symbol)
resolved_strategy_name = "option" if is_option else default_strategy_name
```

Use `resolved_strategy_name` instead of `default_strategy_name` when creating the PositionRecord
and when queuing the stop.

**`_infer_strategy_name_from_client_order_id()`**:

```python
def _infer_strategy_name_from_client_order_id(client_order_id: str) -> str:
    from alpaca_bot.strategy import STRATEGY_REGISTRY, OPTION_STRATEGY_NAMES
    if not client_order_id:
        return "breakout"
    first_segment = client_order_id.split(":")[0]
    if first_segment in STRATEGY_REGISTRY:
        return first_segment
    if first_segment == "option":
        # OCC client_order_id format: option:{version}:{date}:{symbol}:{type}
        # Try to recover the actual sub-strategy from the second segment
        parts = client_order_id.split(":")
        if len(parts) >= 2 and parts[1] in OPTION_STRATEGY_NAMES:
            return parts[1]
        return "option"
    return "breakout"
```

Wait — looking at the OCC client_order_id format in `engine.py` line 893: `_client_order_id()`
uses "option" as prefix when `is_option=True`. The strategy name (e.g., "bear_breakdown") is
passed separately via `strategy_name=strategy_name` in the CycleIntent. The client_order_id
format is `option:{version}:{date}:{occ_symbol}:{side}` — the actual sub-strategy name is NOT
encoded in the client_order_id, only the "option" prefix.

Therefore `_infer_strategy_name_from_client_order_id` can only return "option" for OCC order IDs,
not the specific sub-strategy. The specific sub-strategy is only recoverable from the position
record's `strategy_name`, which is set at position creation time.

Fix: `first_segment == "option"` → return `"option"`.

**Strategy_name for `_infer_strategy_name()`** (line 718): Already checks synced_positions first.
If the position was created with `strategy_name="option"` (after Bug 3 fix), the inference via
position lookup will correctly return "option". Only broker orders with no matching position use
the client_order_id parser — which will now return "option" for OCC prefixes.

---

## Decision Record

**Why 10% default for OPTION_STOP_BUFFER_PCT?**  
Options' defined risk is the premium. A 10% stop at $1.20 = $0.12 buffer (12 cents). Even
after two-decimal rounding, $1.20 × 0.90 = $1.08 — clearly below any same-day market price.
The equity buffer (0.1%) is intentionally tight to preserve profit. Options with defined risk
can tolerate a wider stop because the worst case is already capped at premium paid.

**Why "option" as strategy_name rather than the specific sub-strategy?**  
The specific sub-strategy (e.g., "bear_breakdown") is NOT encoded in the OCC client_order_id.
We cannot recover it at startup for broker-missing positions. Writing "option" is accurate (it is
an option position) and better than "breakout" (which is wrong). The sub-strategy is available
during normal operation (engine.py passes it via CycleIntent.strategy_name), so new positions
created through normal flow retain the correct sub-strategy name.

**Why skip stop when current_price is None for options but not equities?**  
Equities always have a market price during regular hours. If `current_price=None` for an equity,
it's a data gap — queuing a stop is the safe action. Options under Alpaca paper trading often
return `market_value=None` from the positions API, which our code uses as the price proxy. Queuing
a stop for an option when we have no price is guaranteed to loop (we've proven it today).
Emitting `option_stop_skipped_no_price` keeps operators informed without triggering 534 failures.

---

## Audit Events

| Event | When |
|---|---|
| `order_dispatch_stop_price_rejected` | 42210000 from Alpaca on a stop order |
| `option_stop_skipped_no_price` | current_price is None for an OCC symbol in active recovery |

---

## Tests

New tests in `tests/unit/test_startup_recovery.py`:

1. `test_option_stop_uses_option_buffer` — OCC broker-missing position gets stop at entry × (1 - option_stop_buffer_pct), not breakout buffer
2. `test_option_stop_skipped_when_no_current_price` — active OCC position with current_price=None emits option_stop_skipped_no_price and does not queue stop
3. `test_equity_stop_queued_when_no_current_price` — equity active position with current_price=None still queues stop (unchanged behavior)
4. `test_option_strategy_name_broker_missing` — broker-missing OCC position gets strategy_name="option"
5. `test_equity_strategy_name_broker_missing` — broker-missing equity position gets strategy_name="breakout" (unchanged)
6. `test_recovery_stop_same_price_not_requeued` — existing terminal stop with same stop_price → skip re-queue
7. `test_recovery_stop_different_price_requeued` — existing terminal stop with different stop_price → re-queue proceeds
8. `test_infer_strategy_name_option_prefix` — "option:v1-breakout:..." → returns "option"

New tests in `tests/unit/test_order_dispatch.py`:

9. `test_circuit_breaker_42210000_marks_canceled` — Alpaca 42210000 on stop → status="canceled", audit event "order_dispatch_stop_price_rejected"
10. `test_non_42210000_still_marks_error` — other broker errors on stop → status="error" (unchanged)

---

## What This Does NOT Change

- `engine.py` — `stop_price=None` for option entries is correct (defined risk = premium)
- EOD flatten logic
- Equity stop buffer (`breakout_stop_buffer_pct`)
- `dispatch_pending_orders()` for entry or exit orders
- Any DB schema or migrations
- Normal (non-recovery) cycle flow for options
