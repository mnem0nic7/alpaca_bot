# Option Chain Volume Pre-Filter Design

**Date:** 2026-05-11
**Status:** Approved for implementation

## Problem

The option chain fetch iterates every symbol in `intraday_bars_by_symbol` (400+ symbols on active days) and issues one Alpaca REST call per symbol via a `ThreadPoolExecutor(max_workers=5)`. With 400 symbols and 5 workers, the executor runs ~80 serial batches. Alpaca's options snapshot endpoint responds with HTTP 429 under sustained load; the SDK retries with exponential backoff, holding worker threads for seconds each. The combined effect: the 45-second `as_completed` timeout fires on every cycle.

The timeout is correctly implemented (no blocking stall), but 45 of every ~65 active seconds are consumed by option chain fetching, pushing the effective cycle cadence to ~125 seconds instead of the designed 60.

The vast majority of the 400+ symbols are micro-caps and thin small-caps with no listed options. Alpaca returns an empty snapshot dict for these (no exception, just an empty response), but each still consumes a worker slot and contributes to the 429 rate-limit pressure.

## Goal

Reduce the option chain fetch universe to symbols with meaningful intraday trading volume. Symbols below the threshold are extremely unlikely to have liquid options worth selecting; fetching them wastes API capacity and produces no usable chain data.

Target: fewer than 60 symbols reaching the executor, fitting comfortably within the 45-second window with headroom.

## Design

### Filter location

Inside `supervisor.py`'s `run_cycle_once()`, between building `intraday_bars_by_symbol` and submitting to the `ThreadPoolExecutor`. The filter is a single list comprehension:

```python
min_vol = self.settings.option_chain_min_total_volume
symbols_to_fetch = [
    sym for sym, bars in intraday_bars_by_symbol.items()
    if min_vol == 0 or sum(b.volume for b in bars) >= min_vol
]
```

Then the executor iterates `symbols_to_fetch` instead of `intraday_bars_by_symbol`:

```python
futures = {executor.submit(_fetch_one, sym): sym for sym in symbols_to_fetch}
```

### Volume metric

**Total volume across all bars in `intraday_bars_by_symbol`** — the 5-day window of 15-minute bars. This is stable (not sensitive to a single quiet bar) and uses data already in memory. No additional API calls.

A symbol that traded 50,000 shares over the past 5 days almost certainly has no listed options, or has options so illiquid they would never pass the delta/DTE/spread selectors downstream.

### New setting

`option_chain_min_total_volume: int = 0` added to `Settings`. `0` means no filter (backward compatible default). In production, set `OPTION_CHAIN_MIN_TOTAL_VOLUME=50000`.

Parsed in `Settings.from_env()`:
```python
option_chain_min_total_volume=int(values.get("OPTION_CHAIN_MIN_TOTAL_VOLUME", "0")),
```

### Audit event unchanged

The `option_chains_fetched` audit event still iterates `intraday_bars_by_symbol` keys (all watchlist symbols with bars), not the filtered set. Filtered-out symbols appear with a count of `0`. This preserves the audit event's role as a full-coverage report — operators can see which symbols were eligible but skipped the fetch due to low volume.

### What happens to filtered symbols

They are treated identically to symbols that returned an empty chain: count = 0, never added to `option_chains_by_symbol`, option strategies return `None` for them. No behavioral change for equities.

## Components Affected

| File | Change |
|---|---|
| `src/alpaca_bot/config/__init__.py` | Add `option_chain_min_total_volume: int = 0` field and `from_env()` parsing |
| `src/alpaca_bot/runtime/supervisor.py` | Add volume filter before executor submission; iterate `symbols_to_fetch` |
| `tests/unit/test_supervisor_option_chains.py` | Add test: only symbols with volume >= threshold are fetched |
| `/etc/alpaca_bot/alpaca-bot.env` (production) | Add `OPTION_CHAIN_MIN_TOTAL_VOLUME=50000` (deploy step, not code) |

## No Schema Change, No Migration

The setting is read from environment; it is not persisted. No database changes.

## Error Handling

- If `option_chain_min_total_volume` is set too high and all symbols are filtered, `symbols_to_fetch` is empty, the executor submits nothing, and `option_chains_by_symbol` remains `{}`. Option strategies receive no chains and produce no signals — safe, not a crash.
- The executor and `as_completed` handle an empty future set correctly (the `for future in as_completed(...)` loop simply does not execute).

## Test Coverage

Add to `tests/unit/test_supervisor_option_chains.py`:
- `test_volume_filter_excludes_low_volume_symbols`: watchlist has 3 symbols, one below threshold; verify adapter is called only for the two above threshold.
- `test_volume_filter_zero_disables_filter`: `option_chain_min_total_volume=0`; verify all symbols are fetched regardless of volume.

The existing `_make_supervisor` helper already controls `get_stock_bars`, making it straightforward to inject bars with specific volumes.

## Risk Assessment

- **Financial safety:** No change to order submission, position sizing, or stop placement. Fewer chain fetches = fewer option strategy signals, which is conservative (fewer trades, not riskier trades).
- **False exclusion risk:** A high-volume day for a symbol that normally has thin options might still pass the filter and waste a fetch. That's acceptable — the filter is a heuristic, not a guarantee.
- **Threshold calibration:** 50,000 shares over 5 days (~200 bars at 15-min resolution) is ~250 shares/bar average. This excludes genuinely dormant symbols while keeping any actively-traded name. Operators can tune via env var without redeployment.
- **Rollback:** Remove `OPTION_CHAIN_MIN_TOTAL_VOLUME` from env file → filter disabled → full symbol universe → prior behavior (with 45-second timeout on every cycle, but no stall).
