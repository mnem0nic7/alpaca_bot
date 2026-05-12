# Option Strategy Hardening — Design Spec

**Date:** 2026-05-12
**Status:** Approved

## Problem

Three correctness gaps in the current option handling, all discovered from today's 7-position put session:

1. **Dashboard ×100 display bug.** `init_val`, `curr_val`, unrealised P&L, and risk-dollar calculations in `dashboard.html` use `entry_price × quantity` — correct for equities, wrong for options (each contract controls 100 shares). A `$1.20` option with 1 contract showed as `$1`, real notional is `$120`. Same bug in `service.py`'s `_compute_capital_pct` and `total_deployed_notional`.

2. **No bid-ask spread filter for options.** `option_selector.py` filters by `ask > 0`, DTE range, and delta target — but not spread. A wide market (bid=0.10, ask=0.80) passes through, guaranteeing multi-dollar slippage per contract relative to theoretical value.

3. **No open interest filter.** Thin OI means few counterparties and poor fills at exit. The engine already applies `option_chain_min_total_volume` (daily volume) but not OI, which is the more stable measure of outstanding contracts.

## Scope

**In scope:**
1. Fix ×100 display in `dashboard.html` for option positions.
2. Fix ×100 in `service.py` (`_compute_capital_pct`, `total_deployed_notional`).
3. Add `spread_pct` property to `OptionContract`.
4. Add `open_interest: int | None` to `OptionContract`; extract from Alpaca snapshot.
5. Add `OPTION_MAX_SPREAD_PCT` and `OPTION_MIN_OPEN_INTEREST` settings.
6. Apply spread + OI filters in `select_call_contract` / `select_put_contract`.

**Out of scope:** Stop buffer size changes, position sizing changes (tracked separately), OPRA feed upgrade.

## Detection Signal for Option Positions

Option positions in the `positions` table always have `strategy_name == "option"` — set by `startup_recovery.py` line 142: `resolved_strategy_name = "option" if is_option else default_strategy_name`. The supervisor runs broker reconciliation every cycle, so filled option positions are synced into `positions` with this strategy_name. The template uses this as the multiplier gate.

## Architecture

Five components, no schema changes:

| Component | File | Change |
|---|---|---|
| Domain | `src/alpaca_bot/domain/models.py` | Add `open_interest`, `spread_pct` to `OptionContract` |
| Execution | `src/alpaca_bot/execution/option_chain.py` | Extract `open_interest` from snapshot |
| Config | `src/alpaca_bot/config/__init__.py` | Add `OPTION_MAX_SPREAD_PCT`, `OPTION_MIN_OPEN_INTEREST` |
| Strategy | `src/alpaca_bot/strategy/option_selector.py` | Apply spread + OI eligibility filters |
| Service + Template | `src/alpaca_bot/web/service.py` + `dashboard.html` | Fix ×100 multiplier |

## Component Design

### 1. `OptionContract` additions

```python
@dataclass(frozen=True)
class OptionContract:
    occ_symbol: str
    underlying: str
    option_type: str
    strike: float
    expiry: date
    bid: float
    ask: float
    delta: float | None = None
    open_interest: int | None = None  # NEW

    @property
    def spread_pct(self) -> float:  # NEW
        if self.ask <= 0:
            return 0.0
        return (self.ask - self.bid) / self.ask
```

### 2. `option_chain.py` — extract open_interest

In `_snapshot_to_contract`, after `delta`:
```python
open_interest: int | None = None
try:
    raw_oi = getattr(snapshot, "open_interest", None)
    if raw_oi is not None:
        open_interest = int(raw_oi)
except (TypeError, ValueError):
    open_interest = None
```

### 3. Settings additions

```python
option_max_spread_pct: float = 0.50   # 50% = permissive default; tighten in prod
option_min_open_interest: int = 0     # 0 = disabled
```

Env vars: `OPTION_MAX_SPREAD_PCT`, `OPTION_MIN_OPEN_INTEREST`.

Validation:
- `0 < option_max_spread_pct <= 1.0`
- `option_min_open_interest >= 0`

### 4. `option_selector.py` — eligibility filter additions

Both `select_call_contract` and `select_put_contract` apply these additional eligibility checks in the list comprehension:

```python
# Spread filter: reject if spread exceeds threshold
and c.spread_pct <= settings.option_max_spread_pct

# OI filter: reject only if OI is known and below minimum (fail-open when OI is None)
and (settings.option_min_open_interest == 0
     or c.open_interest is None
     or c.open_interest >= settings.option_min_open_interest)
```

**Fail-open rationale:** On the `indicative` feed, `open_interest` may not be populated for all strikes. Failing open (passing contracts with `open_interest=None` when a minimum is set) avoids silently rejecting all contracts on feeds where OI isn't reported.

### 5. Service + Template — ×100 fix

**`service.py`:** Add helper `_option_multiplier(pos) -> int` returning 100 if `pos.strategy_name == "option"` else 1. Apply in `_compute_capital_pct` (lines 194–195) and `total_deployed_notional` (lines 298–300).

**`dashboard.html`:** Add `{% set multiplier = 100 if position.strategy_name == "option" else 1 %}` at the start of each position row, then multiply all dollar amounts (`init_val`, `curr_val`, `upnl`, `risk_dollars`) by `multiplier`. The `stop_dist_pct` and `upnl_pct` are percentages — no multiplier needed there.

## Error Handling

| Scenario | Behavior |
|---|---|
| `snapshot.open_interest` attribute missing or non-integer | Silently set `open_interest = None`; contract passes OI filter |
| `spread_pct` with `ask == 0` | Returns `0.0`; contract already excluded by `ask > 0` guard |
| All contracts filtered by spread | `select_*_contract` returns `None`; engine does not emit option ENTRY intent |
| All contracts filtered by OI | Same — returns `None` |

## Testing

All tests use fake-callables DI pattern (no mocks).

| Test file | Tests |
|---|---|
| `tests/unit/test_option_domain_settings.py` | `OptionContract.spread_pct`; `open_interest` field; new settings defaults + env overrides + validation |
| `tests/unit/test_option_chain.py` | `open_interest` extracted when present; `None` when absent/malformed |
| `tests/unit/test_option_selector.py` | Spread filter rejects wide contracts, passes tight ones; OI filter rejects low-OI, passes high-OI; OI filter passes when `open_interest=None` (fail-open); OI filter off when `option_min_open_interest=0` |
| `tests/unit/test_web_service.py` | `_compute_capital_pct` multiplies option positions by 100; `total_deployed_notional` multiplies option positions by 100 |
