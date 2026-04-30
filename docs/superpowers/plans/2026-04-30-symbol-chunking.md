# Plan: Symbol Chunking for Market Data Fetches

Spec: `docs/superpowers/specs/2026-04-30-symbol-chunking.md`

## Task 1 — Add `_SYMBOL_CHUNK_SIZE` constant and `_fetch_bars_chunked()` helper; delegate from public methods

**File:** `src/alpaca_bot/execution/alpaca.py`

After line 17 (`_MAX_ATTEMPTS = 3`), add:

```python
_SYMBOL_CHUNK_SIZE = 200
```

Replace `get_stock_bars()` and `get_daily_bars()` (lines 481–514) and add the private helper as a new method on `AlpacaMarketDataAdapter`:

```python
def get_stock_bars(
    self,
    *,
    symbols: Sequence[str],
    start: datetime,
    end: datetime,
    timeframe_minutes: int,
) -> dict[str, list[Bar]]:
    return self._fetch_bars_chunked(
        symbols=symbols,
        start=start,
        end=end,
        timeframe_minutes=timeframe_minutes,
    )

def get_daily_bars(
    self,
    *,
    symbols: Sequence[str],
    start: datetime,
    end: datetime,
) -> dict[str, list[Bar]]:
    return self._fetch_bars_chunked(
        symbols=symbols,
        start=start,
        end=end,
        timeframe_minutes=None,
    )

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

Verify with:
```bash
pytest tests/unit/test_alpaca_market_data.py -q
```
All existing tests must pass (the stub returns `{"AAPL": [...]}` for any request, so existing single-symbol tests are unaffected).

## Task 2 — Add tests for chunked fetching

**File:** `tests/unit/test_alpaca_market_data.py`

The existing `HistoricalClientStub.get_stock_bars()` always returns the same
`{"AAPL": [...]}` regardless of which symbols are requested. Add a second stub
that captures per-chunk calls and returns symbol-specific data.

Add the following after `test_get_stock_bars_returns_domain_bars_and_builds_request`:

```python
class MultiSymbolHistoricalClientStub:
    """Returns a bar for each requested symbol, keyed by symbol name."""

    def __init__(self) -> None:
        self.call_count = 0
        self.requested_symbol_lists: list[list[str]] = []

    def get_stock_bars(self, request_params: object) -> BarSetStub:
        self.call_count += 1
        req = _normalize_request(request_params)
        symbols = req["symbol_or_symbols"] or []
        self.requested_symbol_lists.append(list(symbols))
        data = {
            sym: [
                RawBarStub(
                    symbol=sym,
                    timestamp=datetime(2026, 4, 24, 19, 0, tzinfo=timezone.utc),
                    open=100.0,
                    high=101.0,
                    low=99.0,
                    close=100.5,
                    volume=1000,
                )
            ]
            for sym in symbols
        }
        return BarSetStub(data)

    def get_stock_latest_trade(self, request_params: object) -> SimpleNamespace:
        return SimpleNamespace(data={})


def _make_symbols(n: int) -> list[str]:
    """Generate n fake ticker symbols: SYM000, SYM001, ..."""
    return [f"SYM{i:03d}" for i in range(n)]


def test_get_stock_bars_makes_one_request_per_chunk() -> None:
    client = MultiSymbolHistoricalClientStub()
    adapter = AlpacaMarketDataAdapter(client, settings=make_settings())
    symbols = _make_symbols(250)  # 200 + 50 → 2 chunks
    start = datetime(2026, 4, 24, 14, 0, tzinfo=timezone.utc)
    end = datetime(2026, 4, 24, 19, 0, tzinfo=timezone.utc)

    adapter.get_stock_bars(symbols=symbols, start=start, end=end, timeframe_minutes=15)

    assert client.call_count == 2
    assert len(client.requested_symbol_lists[0]) == 200
    assert len(client.requested_symbol_lists[1]) == 50


def test_get_stock_bars_merges_results_from_all_chunks() -> None:
    client = MultiSymbolHistoricalClientStub()
    adapter = AlpacaMarketDataAdapter(client, settings=make_settings())
    symbols = _make_symbols(250)
    start = datetime(2026, 4, 24, 14, 0, tzinfo=timezone.utc)
    end = datetime(2026, 4, 24, 19, 0, tzinfo=timezone.utc)

    result = adapter.get_stock_bars(symbols=symbols, start=start, end=end, timeframe_minutes=15)

    assert len(result) == 250
    for sym in symbols:
        assert sym in result
        assert len(result[sym]) == 1


def test_get_stock_bars_empty_symbols_returns_empty_dict_without_api_call() -> None:
    client = MultiSymbolHistoricalClientStub()
    adapter = AlpacaMarketDataAdapter(client, settings=make_settings())
    start = datetime(2026, 4, 24, 14, 0, tzinfo=timezone.utc)
    end = datetime(2026, 4, 24, 19, 0, tzinfo=timezone.utc)

    result = adapter.get_stock_bars(symbols=[], start=start, end=end, timeframe_minutes=15)

    assert result == {}
    assert client.call_count == 0


def test_get_daily_bars_chunks_large_symbol_list() -> None:
    client = MultiSymbolHistoricalClientStub()
    adapter = AlpacaMarketDataAdapter(client, settings=make_settings())
    symbols = _make_symbols(250)  # 200 + 50 → 2 chunks
    start = datetime(2026, 4, 1, 0, 0, tzinfo=timezone.utc)
    end = datetime(2026, 4, 24, 0, 0, tzinfo=timezone.utc)

    result = adapter.get_daily_bars(symbols=symbols, start=start, end=end)

    assert client.call_count == 2
    assert len(result) == 250
```

Verify with:
```bash
pytest tests/unit/test_alpaca_market_data.py -q
```
Expected: all tests pass including the 3 new ones.

## Task 3 — Run full test suite

```bash
pytest -q
```

Expected: all tests pass. No regressions.

## Task 4 — Commit

```bash
git add src/alpaca_bot/execution/alpaca.py tests/unit/test_alpaca_market_data.py
git commit -m "fix: chunk symbol list into batches of 200 for Alpaca bar fetches"
```
