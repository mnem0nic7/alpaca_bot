# Option Chain Observability + Delta Fallback — Design Spec

## Problem

`ENABLE_OPTIONS_TRADING=true` has been set for the live deployment, and 12 OPT strategies
(breakout_calls + 11 bear_ strategies) are enabled in the dashboard. However, the `option_orders`
table contains **0 rows ever**. No option trade has been placed through the bot.

### Root Cause 1 — Silent Chain Failure

`AlpacaOptionChainAdapter.get_option_chain()` in `execution/option_chain.py` catches every
exception and returns `[]` without logging anything:

```python
try:
    snapshots = self._client.get_option_chain(request)
except Exception:
    return []   # ← swallowed silently
```

The supervisor's outer `try/except` (supervisor.py line 682) only fires if `get_option_chain()`
itself raises — which it never does. When the Alpaca API returns an error (auth failure, rate limit,
subscription issue), zero information is produced. `option_chains_by_symbol` stays empty for all
8 symbols. All 12 OPT strategies instantiate successfully but immediately return `None` on every
`evaluate()` call. Nothing in the audit log, nothing in the supervisor log.

### Root Cause 2 — Delta Filter Always Rejects

`select_call_contract()` / `select_put_contract()` in `strategy/option_selector.py` require
`c.delta is not None`:

```python
with_delta = [c for c in eligible if c.delta is not None and ...]
if not with_delta:
    return None
```

The Alpaca "indicative" feed for option chains may return snapshots without greeks populated
(depending on subscription tier and API version). If `snapshot.greeks` is `None` for all
contracts, every contract gets `delta=None`, `with_delta` is always empty, and the selector
always returns `None`. Even if chains ARE fetched successfully, no signal would ever fire.

### What is By Design

| Behavior | Verdict |
|---|---|
| 0% capital weight for all OPT strategies | **By design** — dashboard "Capital" column tracks equity position value only; options live in `option_order_store` and are invisible to this calc |
| One option position per underlying at a time | **By design** — `working_order_symbols` prevents a second OPT strategy from entering the same underlying |
| Bear strategies don't fire on bullish days | **By design** — they require `daily_downtrend_filter_passes` + breakdown below prior low |
| breakout_calls is the only OPT strategy that can fire on a bullish day | **By design** — it reuses `evaluate_breakout_signal()` which fires on bullish breakouts |

## Scope

Two additive fixes, zero schema changes, no new env vars:

1. **`execution/option_chain.py`** — log exception before returning `[]`; add per-symbol contract count to supervisor log
2. **`strategy/option_selector.py`** — fall back to ATM-by-strike when all contracts have `delta=None`
3. **`runtime/supervisor.py`** — emit `option_chains_fetched` audit event per cycle with per-symbol contract counts (0 = chain empty/failed)
4. **`tests/unit/test_option_chain.py`** and **`tests/unit/test_option_selector.py`** — add tests for new behavior

## Design

### Fix 1: Logging in `get_option_chain()`

**File**: `src/alpaca_bot/execution/option_chain.py`

```python
def get_option_chain(self, symbol: str, settings: Settings) -> list[OptionContract]:
    try:
        from alpaca.data.requests import OptionChainRequest
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

The `logger.warning(..., exc_info=True)` surfaces the failure in the supervisor log without
raising (the behaviour of "continue without options on transient failure" is correct).

### Fix 2: Delta Fallback in `select_call_contract()` / `select_put_contract()`

**File**: `src/alpaca_bot/strategy/option_selector.py`

When `with_delta` (contracts with a known delta) is empty but there are contracts passing the
DTE and ask filters, fall back to the contract whose strike is closest to the current price
(i.e., at-the-money selection by strike):

```python
def select_call_contract(
    chains: Sequence[OptionContract],
    current_price: float,
    today: date,
    settings: Settings,
) -> OptionContract | None:
    eligible = [
        c for c in chains
        if c.option_type == "call"
        and c.ask > 0
        and settings.option_dte_min <= (c.expiry - today).days <= settings.option_dte_max
    ]
    if not eligible:
        return None
    with_delta = [c for c in eligible if c.delta is not None]
    if with_delta:
        return min(with_delta, key=lambda c: abs(c.delta - settings.option_delta_target))
    # Fallback: no greeks available — select ATM by strike proximity
    return min(eligible, key=lambda c: abs(c.strike - current_price))
```

Same pattern for `select_put_contract()`: when no delta is available, select the put contract
with strike closest to current price (ATM put).

### Fix 3: `option_chains_fetched` Audit Event

**File**: `src/alpaca_bot/runtime/supervisor.py`

After the chain fetch loop, emit one audit event per cycle (not per symbol) containing the
per-symbol contract count:

```python
option_chain_counts = {sym: len(chains) for sym, chains in option_chains_by_symbol.items()}
# Symbols with no chains get 0
for sym in self.settings.symbols:
    option_chain_counts.setdefault(sym, 0)
self.runtime.audit_event_store.append(
    AuditEvent(
        event_type="option_chains_fetched",
        payload=option_chain_counts,
    ),
    commit=False,
)
```

This makes chain health visible in the audit trail on every cycle. An operator can query:

```sql
SELECT payload FROM audit_events
WHERE event_type = 'option_chains_fetched'
ORDER BY created_at DESC LIMIT 1;
```

A payload like `{"SPY": 0, "QQQ": 0, ...}` (all zeros) confirms the chain API is failing.
A payload like `{"SPY": 847, "QQQ": 621, ...}` confirms chains are healthy.

## Financial Safety

No order submission, position sizing, or stop placement is modified. The fixes add logging and
relax one filter (delta fallback). The fallback selects an ATM contract by strike, which is a
reasonable default for both calls and puts — no worse than the original intent of targeting 0.50
delta (which is ATM by definition). The fallback only activates when `delta=None` for all
eligible contracts; when greeks are available, behaviour is unchanged.

## Testing

### `tests/unit/test_option_chain.py` (new or extended)

- Test that `get_option_chain()` catches exceptions and returns `[]` (existing behaviour, now logged)
- Test that the logger warning is emitted on API failure

### `tests/unit/test_option_selector.py` (existing file, new tests)

- Test `select_call_contract()` with all contracts having `delta=None` → selects ATM by strike
- Test `select_put_contract()` with all contracts having `delta=None` → selects ATM by strike
- Test that delta-aware selection still wins when at least one contract has a valid delta

### `tests/unit/test_supervisor_weights.py` or new supervisor integration test

- Test that `option_chains_fetched` audit event is emitted when `_option_chain_adapter` is set
- Test that the event payload shows 0 for all symbols when chains are empty

## Out of Scope

- Changing the `option_dte_min`/`option_dte_max` defaults
- Fixing the `option_orders.strategy_name` column default (cosmetic, non-functional)
- Adding a CLI `check-options` diagnostic command (useful but separate effort)
- Dashboard display of option chain health (separate UI work)
