# Option Strategy Rolling Loss Circuit Breaker — Design Spec

**Date:** 2026-05-28
**Author:** auto (plan-and-refine pipeline)
**Status:** Ready for implementation

---

## Problem Statement

The `bear_orb` short-put strategy ran for 9+ consecutive days losing ~-$21K with no automated stop. The only existing guard is `daily_loss_limit_pct`, which resets every morning. Short-put option strategies can bleed slowly across many sessions:

- Day 1–3: small losses, daily limit not breached
- Day 4–9: deeper losses as positions go ITM, still under daily limit each day
- Cumulative outcome: -$21K with no automatic intervention

A professional short-volatility trading system requires a **per-strategy rolling P&L circuit breaker** that accumulates loss across multiple sessions and stops new entries when the threshold is crossed.

---

## Root Cause

No code computes rolling P&L per option strategy. The data exists in the `option_orders` table (closed trades have `fill_price` on both sell and buy-to-close sides, with `strategy_name` populated). The supervisor's `_check_daily_loss_limit` method has the right pattern — it reads P&L and sets `entries_disabled` — but it only covers the whole account for a single day.

---

## Fix Design

### New Config Fields (Settings)

```python
option_strategy_max_rolling_loss_usd: float = 0.0  # 0 = disabled
option_strategy_rolling_loss_days: int = 7
```

`option_strategy_max_rolling_loss_usd = 0.0` means the circuit breaker is off by default, preserving current behavior. Production should set this to e.g. `500.0` (halt when a strategy has lost ≥ $500 in the rolling window).

### New Query Method

`OptionOrderRepository.rolling_realized_pnl_by_strategy(trading_mode, strategy_version, since_date, until_date)` → `dict[str, float]`

Returns `{strategy_name: sum_of_pnl}` for all closed option round-trips where the buy-to-close fill date falls within `[since_date, until_date]`. Reuses the same correlated-subquery pattern as `list_closed_option_trade_records`. Only strategies with at least one closed trade in the window appear in the result.

### New Supervisor Method

`_check_option_strategy_circuit_breakers(session_date: date, now: datetime)` called at the top of `run_cycle_once`, after `session_type` is determined but before option chain fetch and strategy evaluation.

Logic:
```
if settings.option_strategy_max_rolling_loss_usd <= 0:
    return  # disabled

since_date = session_date - timedelta(days=settings.option_strategy_rolling_loss_days - 1)
rolling_pnl = option_order_store.rolling_realized_pnl_by_strategy(
    trading_mode=settings.trading_mode,
    strategy_version=settings.strategy_version,
    since_date=since_date,
    until_date=session_date,
)
threshold = -abs(settings.option_strategy_max_rolling_loss_usd)
for strategy_name, pnl in rolling_pnl.items():
    if pnl <= threshold:
        existing = strategy_flag_store.load(strategy_name=...) 
        if existing is None or existing.enabled:
            strategy_flag_store.save(StrategyFlag(enabled=False, ...))
            emit audit event: "option_strategy_circuit_breaker_triggered"
            payload: {strategy_name, rolling_pnl_usd: pnl, threshold_usd: threshold,
                      window_days: settings.option_strategy_rolling_loss_days}
```

### Integration Point

Call `_check_option_strategy_circuit_breakers` only when `option_order_store is not None` and `strategy_flag_store is not None`. The existing option-chain fetch block already checks both.

The check fires every cycle, but only writes a new flag if the strategy was previously enabled. Subsequent cycles are no-ops (flag already disabled). This is safe to call on every cycle — the DB write is guarded by the enabled check.

### Re-enable Path

Manual operator action:
```bash
alpaca-bot-admin enable-strategy --strategy bear_orb
```

The admin CLI's existing `enable-strategy` command writes `StrategyFlag(enabled=True)` to the DB and emits a `strategy_flag_changed` audit event. No new CLI commands needed.

---

## Scope

**In scope:**
- `settings.py`: two new config fields
- `repositories.py`: new `OptionOrderRepository.rolling_realized_pnl_by_strategy()` query
- `supervisor.py`: new `_check_option_strategy_circuit_breakers()` method, called in `run_cycle_once`
- Unit tests for all three

**Out of scope:**
- Per-position unrealized loss stop multiplier (separate concern)
- Equity strategy circuit breaker (equity already has `daily_loss_limit_pct` + `per_symbol_loss_limit_pct`)
- Dashboard visualization of circuit-breaker state (existing strategy flags panel already shows disabled strategies)
- Auto re-enable when rolling window clears (requires operator judgment — losses may reflect a structural market problem)

---

## Data Flow

```
run_cycle_once()
  └─ _check_option_strategy_circuit_breakers(session_date, now)
       └─ OptionOrderRepository.rolling_realized_pnl_by_strategy(since=today-6d, until=today)
            → {bear_orb: -830.0}
       └─ -830.0 <= -500.0 (threshold)?  YES
            └─ strategy_flag_store.save(StrategyFlag(bear_orb, enabled=False))
            └─ emit option_strategy_circuit_breaker_triggered
       └─ Next cycle: load flag → already disabled → skip (no-op)

Option chain fetch (later in run_cycle_once)
  └─ load strategy flag for bear_orb → enabled=False → skip (already filtered by line 828–843)
```

---

## Risk Assessment

- **Financial risk:** Low — the circuit breaker only prevents opening new positions. Existing open positions continue to be managed (stops updated, exits executed) regardless of flag state. The flag gates the ENTRY evaluator, not the EXIT path.
- **Audit trail:** Full. Every flag change emits an audit event with payload. Supervisor-initiated flags look the same as admin-initiated flags in the audit log.
- **Rollback:** Feature is off by default (`option_strategy_max_rolling_loss_usd = 0.0`). Removing the env var reverts to disabled state.
- **Paper vs. live:** Identical behavior — strategy flags are keyed by `(strategy_name, trading_mode, strategy_version)`.
- **Edge cases:**
  - No closed trades in window: `rolling_pnl` is empty → no flags written → correct
  - Strategy already disabled by operator: `existing.enabled = False` → no-op → correct
  - Config threshold changes: on next cycle with same P&L, will re-evaluate with new threshold

---

## Testing

**Test 1:** `rolling_realized_pnl_by_strategy` returns correct per-strategy sums for mixed records.
**Test 2:** Circuit breaker writes `enabled=False` flag and emits audit event when rolling P&L ≤ threshold.
**Test 3:** Circuit breaker is a no-op when rolling P&L > threshold.
**Test 4:** Circuit breaker is a no-op when `option_strategy_max_rolling_loss_usd = 0.0`.
**Test 5:** Circuit breaker does not re-write flag if strategy is already disabled.
**Test 6:** Disabled strategy is excluded from `active_strategies` in supervisor (regression test for existing flag-check behavior).

All tests use fake callables / in-memory stores per project DI pattern.
