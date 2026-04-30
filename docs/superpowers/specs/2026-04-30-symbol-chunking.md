# Spec: Symbol Chunking for Market Data Fetches

## Problem

`AlpacaMarketDataAdapter.get_stock_bars()` and `get_daily_bars()` pass the entire
symbol list in a single Alpaca API call. With 1003 symbols in the watchlist, the
Alpaca SDK paginates the response — the production fetch returns only a fraction of
symbols each cycle. The engine silently skips symbols with no bar data, so signals
for most of the watchlist are never evaluated.

Verified: the IEX coverage check fetching in 200-symbol chunks returned 998/1003
symbols; the production single-call path likely returns far fewer.

## Goals

1. Split the symbol list into chunks of ≤ 200 before calling the Alpaca API.
2. Merge all chunk results into a single `dict[str, list[Bar]]` before returning.
3. Callers see no change — same signatures, same return types.
4. Both `get_stock_bars()` and `get_daily_bars()` are fixed.

## Non-Goals

- `get_latest_prices()` has the same issue but is not in scope here.
- No new Settings fields — chunk size is a module-level constant.
- No changes to `evaluate_cycle()`, the supervisor, or storage.

## Design

### Module-level constant

```python
_SYMBOL_CHUNK_SIZE = 200
```

Chosen because 200-symbol chunks are proven to work against the IEX feed
(`/tmp/check_iex_coverage.py`). Alpaca's docs do not publish a hard per-request
symbol limit, but 200 is a safe, empirically validated ceiling.

### Private helper method

```python
def _fetch_bars_chunked(
    self,
    *,
    symbols: Sequence[str],
    start: datetime,
    end: datetime,
    timeframe_minutes: int | None,
) -> dict[str, list[Bar]]:
    if not symbols:
        return {}
    settings = self._settings if self._settings is not None else _fallback_settings()
    result: dict[str, list[Bar]] = {}
    sym_list = list(symbols)
    for i in range(0, len(sym_list), _SYMBOL_CHUNK_SIZE):
        chunk = sym_list[i : i + _SYMBOL_CHUNK_SIZE]
        request = _stock_bars_request(
            symbols=chunk,
            start=start,
            end=end,
            timeframe_minutes=timeframe_minutes,
            settings=settings,
        )
        raw = _retry_with_backoff(lambda req=request: self._historical.get_stock_bars(req))
        result.update(_parse_barset(raw))
    return result
```

**Why `lambda req=request`?** The lambda captures `request` as a default argument to
avoid the common loop-closure bug. Although `_retry_with_backoff` calls the lambda
synchronously (so the closure would be safe in this specific case), the default-arg
form is an explicit, unambiguous capture.

**Why `result.update()`?** Symbols are unique across chunks — no key collision is
possible.

### Updated public methods

```python
def get_stock_bars(self, *, symbols, start, end, timeframe_minutes):
    return self._fetch_bars_chunked(
        symbols=symbols, start=start, end=end, timeframe_minutes=timeframe_minutes
    )

def get_daily_bars(self, *, symbols, start, end):
    return self._fetch_bars_chunked(
        symbols=symbols, start=start, end=end, timeframe_minutes=None
    )
```

## Files Changed

| File | Change |
|------|--------|
| `src/alpaca_bot/execution/alpaca.py` | Add `_SYMBOL_CHUNK_SIZE`; add `_fetch_bars_chunked()`; delegate from `get_stock_bars()` and `get_daily_bars()` |
| `tests/unit/test_alpaca_market_data.py` | Add 3 new tests: multi-chunk call count, result merge, empty-list fast-path |

## Safety Analysis

**Financial safety:** No changes to order submission, position sizing, or stop
placement. This change only affects how market data is fetched before
`evaluate_cycle()` is called. Worst case: a chunk API call fails and raises —
`_retry_with_backoff` retries up to 3 times then re-raises, causing the cycle to
abort the same way it does today.

**Pure engine boundary:** `evaluate_cycle()` is untouched. The fix is entirely
inside the I/O adapter layer.

**Audit trail:** No state mutations. No `AuditEvent` needed.

**Intent/dispatch separation:** Unaffected — this is upstream of intent generation.

**Advisory lock:** Unaffected — one supervisor instance, one cycle at a time.

**Paper vs. live parity:** Identical — chunking does not branch on trading mode.

**No new env vars:** `_SYMBOL_CHUNK_SIZE = 200` is a code constant.

**Retry semantics:** Each chunk is retried independently by `_retry_with_backoff`.
A transient error in chunk 3 retries chunk 3 up to 3 times before propagating. The
already-fetched chunks 1–2 are not re-fetched. This is acceptable — the cycle aborts
and retries on the next tick.

**Partial-fetch scenario (chunk error after partial success):** The cycle will abort
mid-fetch, `evaluate_cycle()` is not called, no intents are generated. The positions
already open continue to be managed on the next cycle (which completes
successfully). No financial risk introduced.
