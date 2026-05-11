# Option Chain Watchlist Alignment

**Date:** 2026-05-11  
**Status:** Approved for implementation

## Problem

Option chain fetches are hardcoded to `settings.symbols` (8 big-cap symbols: AAPL, MSFT, AMZN, NVDA, META, SPY, QQQ, IWM). Equity strategies use the full DB-backed watchlist (`symbol_watchlist` table, 400+ symbols). This mismatch means option strategies never get chain data for the actively-traded smaller names (ACHR, METC, SLS, etc.) that appear in equity entries every day.

`settings.symbols` is the fallback for watchlist-less mode. It is not the canonical symbol universe.

## Goal

The equity watchlist and the option chain symbol universe must be identical: whatever symbols have intraday bars this cycle → fetch option chains for them.

## Design

### Symbol source change

In `supervisor.py` inside the cycle method, at the point where option chains are fetched (currently line 761), replace:

```python
for symbol in self.settings.symbols:
```

with:

```python
for symbol in intraday_bars_by_symbol:
```

`intraday_bars_by_symbol` is already populated from `watchlist_symbols` earlier in the same cycle. Using its keys guarantees alignment with the equity universe and automatically excludes halted/missing symbols (no bars = no chain fetch).

The audit event sentinel (currently line 787) changes from `self.settings.symbols` to `intraday_bars_by_symbol.keys()`, so `option_chains_fetched` shows zero-counts for every watchlist symbol evaluated.

### Parallel fetching

The current sequential loop is fine for 8 symbols (~3s). With 300+ symbols it would push cycle time past 60s. Replace with `concurrent.futures.ThreadPoolExecutor(max_workers=20)`:

- Each symbol's chain fetch runs in a thread.
- Exceptions per symbol are caught and logged (same as today).
- Results are merged into `option_chains_by_symbol` after all futures complete.
- Expected cycle overhead: ~3–6 seconds (vs 60+ sequential).

### No new env var or migration

The change is purely in the supervisor's cycle method. The watchlist store already exists and is already the equity symbol source. No schema change needed.

## Components affected

| File | Change |
|---|---|
| `src/alpaca_bot/runtime/supervisor.py` | Option chain fetch loop + parallel fetch + audit sentinel |
| `tests/unit/test_supervisor_option_chains.py` (new or existing) | Assert chain fetch iterates watchlist symbols, not settings.symbols |

## Data flow (after change)

```
watchlist_store.list_enabled()
  → watchlist_symbols (400+ symbols)
  → intraday_bars_by_symbol (bars fetched for all of them)
  → option chain fetch (parallel, for keys of intraday_bars_by_symbol)
  → option_chains_by_symbol (chains for any symbol with liquid options)
  → option strategy evaluators receive full chain coverage
  → option_chains_fetched audit event shows per-symbol counts
```

## Error handling

- Symbols with no listed options: API returns empty → not added to `option_chains_by_symbol` → option strategies return `None` for that symbol (unchanged behavior).
- API exceptions per symbol: caught, logged, symbol skipped (unchanged behavior).
- Thread pool does not raise on partial failure; all futures are awaited.

## Test coverage

- Verify: when `intraday_bars_by_symbol` contains watchlist symbols (e.g., ACHR, METC), the chain adapter is called for those symbols.
- Verify: when `settings.symbols` ≠ `intraday_bars_by_symbol.keys()`, chain fetch uses the latter.
- Verify: `option_chains_fetched` audit event keys match `intraday_bars_by_symbol.keys()`.
- Verify: a chain fetch exception for one symbol does not prevent others from completing.

## Risk assessment

- **Financial safety:** No change to position sizing, stop logic, or order dispatch. More chains = more opportunities, each within existing risk parameters (`RISK_PER_TRADE_PCT`).
- **Cycle time:** Parallel fetch adds ~3–6s. Acceptable within the 60s cycle cadence.
- **Engine purity:** `evaluate_cycle()` remains pure. Chains are passed in unchanged.
- **Rollback:** Remove the `ThreadPoolExecutor` wrapper to revert to sequential (with the `intraday_bars_by_symbol` source intact, or revert both).
- **Paper/live parity:** Behavior is identical in both modes.
