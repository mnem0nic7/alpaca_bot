# Live Dashboard Prices — Portfolio Reader Upgrade Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the IEX-feed `get_latest_prices()` call on the dashboard with `TradingClient.get_all_positions()` so that Last / Curr Val / Unreal P&L show live after-hours prices matching the Alpaca broker.

**Architecture:** Add `AlpacaPortfolioReader` to `execution/alpaca.py` — a small class wrapping `TradingClientProtocol.get_all_positions()` that returns `{symbol: current_price}`. Wire it into `web/app.py` as the primary price source inside `_fetch_latest_prices`, with `market_data_adapter` as fallback for any missing symbols.

**Tech Stack:** Python 3.13, alpaca-py `TradingClient`, FastAPI, pytest, existing `TradingClientProtocol` (already defined in `execution/alpaca.py:108`)

---

## File Map

| File | What changes |
|---|---|
| `src/alpaca_bot/execution/alpaca.py` | New `AlpacaPortfolioReader` class after `AlpacaExecutionAdapter` (line ~493) |
| `src/alpaca_bot/web/app.py` | Add `portfolio_reader` param to `create_app`; update `_fetch_latest_prices`; init reader at startup |
| `tests/unit/test_alpaca_order_execution.py` | 4 unit tests for `AlpacaPortfolioReader` |
| `tests/unit/test_web_app.py` | 3 tests for the updated `_fetch_latest_prices` behaviour |

---

### Task 1: `AlpacaPortfolioReader` — unit tests + implementation

**Files:**
- Modify: `tests/unit/test_alpaca_order_execution.py` (append to end of file)
- Modify: `src/alpaca_bot/execution/alpaca.py` (insert after line 491, before `_as_datetime`)

- [ ] **Step 1: Write the four failing tests**

Append to `tests/unit/test_alpaca_order_execution.py`:

```python
# ---------------------------------------------------------------------------
# AlpacaPortfolioReader
# ---------------------------------------------------------------------------

from alpaca_bot.execution.alpaca import AlpacaPortfolioReader
from dataclasses import dataclass as _dc


@_dc
class _PositionStub:
    symbol: str
    current_price: object  # str, float, or missing


class _TradingClientPositionsStub:
    def __init__(self, positions: list) -> None:
        self._positions = positions
        self.called = False

    def get_all_positions(self) -> list:
        self.called = True
        return self._positions


def test_portfolio_reader_returns_current_price_for_requested_symbols() -> None:
    stub = _TradingClientPositionsStub([
        _PositionStub(symbol="AAPL", current_price=175.50),
        _PositionStub(symbol="MSFT", current_price=410.25),
    ])
    reader = AlpacaPortfolioReader(stub)

    result = reader.get_current_prices(["AAPL"])

    assert result == {"AAPL": 175.50}
    assert stub.called


def test_portfolio_reader_returns_empty_when_symbols_is_empty() -> None:
    stub = _TradingClientPositionsStub([
        _PositionStub(symbol="AAPL", current_price=175.50),
    ])
    reader = AlpacaPortfolioReader(stub)

    result = reader.get_current_prices([])

    assert result == {}
    assert not stub.called


def test_portfolio_reader_skips_position_with_missing_current_price() -> None:
    class _NoPricePosition:
        symbol = "AAPL"
        # no current_price attribute

    stub = _TradingClientPositionsStub([_NoPricePosition()])
    reader = AlpacaPortfolioReader(stub)

    result = reader.get_current_prices(["AAPL"])

    assert result == {}


def test_portfolio_reader_skips_position_with_nonnumeric_current_price() -> None:
    stub = _TradingClientPositionsStub([
        _PositionStub(symbol="AAPL", current_price="N/A"),
    ])
    reader = AlpacaPortfolioReader(stub)

    result = reader.get_current_prices(["AAPL"])

    assert result == {}
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/unit/test_alpaca_order_execution.py::test_portfolio_reader_returns_current_price_for_requested_symbols -v
```

Expected: `FAILED` — `ImportError: cannot import name 'AlpacaPortfolioReader'`

- [ ] **Step 3: Implement `AlpacaPortfolioReader`**

In `src/alpaca_bot/execution/alpaca.py`, insert **after** the closing line of `AlpacaExecutionAdapter` (the `return TradingClient(api_key, secret_key, paper=paper)` line, currently ~line 491) and **before** `def _as_datetime`:

```python

class AlpacaPortfolioReader:
    """Read-only portfolio price reader using the Alpaca trading API.

    Uses get_all_positions() which reflects after-hours pricing, unlike
    the historical data feed which freezes at regular-session close.
    """

    def __init__(self, trading_client: TradingClientProtocol) -> None:
        self._trading = trading_client

    @classmethod
    def from_settings(cls, settings: Settings) -> "AlpacaPortfolioReader":
        api_key, secret_key, paper = resolve_alpaca_credentials(settings)
        try:
            from alpaca.trading.client import TradingClient
        except ModuleNotFoundError as exc:
            raise RuntimeError(
                "alpaca-py is required for portfolio price reads. Install dependencies first."
            ) from exc
        return cls(TradingClient(api_key, secret_key, paper=paper))

    def get_current_prices(self, symbols: Sequence[str]) -> dict[str, float]:
        if not symbols:
            return {}
        symbol_set = {s.upper() for s in symbols}
        raw = _retry_with_backoff(self._trading.get_all_positions)
        result: dict[str, float] = {}
        for position in raw:
            sym = str(position.symbol).upper()
            if sym not in symbol_set:
                continue
            raw_price = getattr(position, "current_price", None)
            if raw_price is None:
                continue
            try:
                result[sym] = float(raw_price)
            except (TypeError, ValueError):
                pass
        return result
```

- [ ] **Step 4: Run all four tests to verify they pass**

```bash
pytest tests/unit/test_alpaca_order_execution.py -k "portfolio_reader" -v
```

Expected: 4 PASSED

- [ ] **Step 5: Run full suite to check no regressions**

```bash
pytest tests/unit/test_alpaca_order_execution.py -v
```

Expected: all existing tests + 4 new = all PASSED

- [ ] **Step 6: Commit**

```bash
git add src/alpaca_bot/execution/alpaca.py tests/unit/test_alpaca_order_execution.py
git commit -m "feat: add AlpacaPortfolioReader for live after-hours prices"
```

---

### Task 2: Wire `portfolio_reader` into `web/app.py`

**Files:**
- Modify: `src/alpaca_bot/web/app.py` (three locations: `create_app` signature, startup init block, `_fetch_latest_prices`)

- [ ] **Step 1: Write three failing tests**

Append to `tests/unit/test_web_app.py` (after the existing `test_dashboard_skips_price_fetch_when_no_adapter` test block, before the `/healthz` section):

```python
# ---------------------------------------------------------------------------
# _fetch_latest_prices — portfolio reader integration
# ---------------------------------------------------------------------------


class _FakePortfolioReader:
    def __init__(self, prices: dict[str, float]) -> None:
        self._prices = prices
        self.calls: list[list[str]] = []

    def get_current_prices(self, symbols: list[str]) -> dict[str, float]:
        self.calls.append(list(symbols))
        return {s: self._prices[s] for s in symbols if s in self._prices}


def _make_app_with_position_and_reader(
    settings,
    portfolio_reader=None,
    market_data_adapter=None,
):
    now = datetime(2026, 4, 28, 15, 0, tzinfo=timezone.utc)
    return create_app(
        settings=settings,
        connect_postgres_fn=lambda _url: FakeConnection(responses=[]),
        trading_status_store_factory=lambda _c: SimpleNamespace(
            load=lambda **_: None,
        ),
        daily_session_state_store_factory=lambda _c: SimpleNamespace(
            load=lambda **_: None,
            list_by_session=lambda **_: [],
        ),
        position_store_factory=lambda _c: SimpleNamespace(
            list_all=lambda **_: [
                PositionRecord(
                    symbol="AAPL",
                    trading_mode=TradingMode.PAPER,
                    strategy_version=settings.strategy_version,
                    quantity=10,
                    entry_price=170.00,
                    stop_price=168.00,
                    initial_stop_price=168.00,
                    opened_at=now,
                    updated_at=now,
                )
            ]
        ),
        order_store_factory=lambda _c: SimpleNamespace(
            list_by_status=lambda **_: [],
            list_recent=lambda **_: [],
            list_closed_trades=lambda **_: [],
        ),
        audit_event_store_factory=lambda _c: SimpleNamespace(
            list_recent=lambda **_: [],
            load_latest=lambda **_: None,
            list_by_event_types=lambda **_: [],
        ),
        strategy_flag_store_factory=lambda _c: SimpleNamespace(list_all=lambda **_: []),
        portfolio_reader=portfolio_reader,
        market_data_adapter=market_data_adapter,
    )


def test_dashboard_uses_portfolio_reader_when_available() -> None:
    settings = make_settings()
    reader = _FakePortfolioReader({"AAPL": 180.00})
    adapter = _FakeMarketDataAdapter({"AAPL": 170.00})
    app = _make_app_with_position_and_reader(
        settings, portfolio_reader=reader, market_data_adapter=adapter
    )
    client = TestClient(app)

    response = client.get("/", headers={"Authorization": f"Basic {b64encode(b'admin:secret').decode()}"})

    assert response.status_code == 200
    assert "180.00" in response.text
    assert reader.calls == [["AAPL"]]
    assert adapter.calls == []  # adapter NOT called when reader returns all symbols


def test_dashboard_falls_back_to_adapter_when_portfolio_reader_raises() -> None:
    settings = make_settings()

    class _RaisingReader:
        def get_current_prices(self, symbols: list[str]) -> dict[str, float]:
            raise RuntimeError("Trading client unavailable")

    adapter = _FakeMarketDataAdapter({"AAPL": 170.50})
    app = _make_app_with_position_and_reader(
        settings, portfolio_reader=_RaisingReader(), market_data_adapter=adapter
    )
    client = TestClient(app)

    response = client.get("/", headers={"Authorization": f"Basic {b64encode(b'admin:secret').decode()}"})

    assert response.status_code == 200
    assert "170.50" in response.text
    assert adapter.calls == [["AAPL"]]


def test_dashboard_merges_reader_and_adapter_for_missing_symbols() -> None:
    settings = make_settings(SYMBOLS="AAPL,MSFT,SPY")
    now = datetime(2026, 4, 28, 15, 0, tzinfo=timezone.utc)

    reader = _FakePortfolioReader({"AAPL": 180.00})  # only knows AAPL
    adapter = _FakeMarketDataAdapter({"MSFT": 410.00})  # handles MSFT

    app = create_app(
        settings=settings,
        connect_postgres_fn=lambda _url: FakeConnection(responses=[]),
        trading_status_store_factory=lambda _c: SimpleNamespace(load=lambda **_: None),
        daily_session_state_store_factory=lambda _c: SimpleNamespace(
            load=lambda **_: None,
            list_by_session=lambda **_: [],
        ),
        position_store_factory=lambda _c: SimpleNamespace(
            list_all=lambda **_: [
                PositionRecord(
                    symbol="AAPL",
                    trading_mode=TradingMode.PAPER,
                    strategy_version=settings.strategy_version,
                    quantity=10,
                    entry_price=170.00,
                    stop_price=168.00,
                    initial_stop_price=168.00,
                    opened_at=now,
                    updated_at=now,
                ),
                PositionRecord(
                    symbol="MSFT",
                    trading_mode=TradingMode.PAPER,
                    strategy_version=settings.strategy_version,
                    quantity=5,
                    entry_price=400.00,
                    stop_price=395.00,
                    initial_stop_price=395.00,
                    opened_at=now,
                    updated_at=now,
                ),
            ]
        ),
        order_store_factory=lambda _c: SimpleNamespace(
            list_by_status=lambda **_: [],
            list_recent=lambda **_: [],
            list_closed_trades=lambda **_: [],
        ),
        audit_event_store_factory=lambda _c: SimpleNamespace(
            list_recent=lambda **_: [],
            load_latest=lambda **_: None,
            list_by_event_types=lambda **_: [],
        ),
        strategy_flag_store_factory=lambda _c: SimpleNamespace(list_all=lambda **_: []),
        portfolio_reader=reader,
        market_data_adapter=adapter,
    )
    client = TestClient(app)

    response = client.get("/", headers={"Authorization": f"Basic {b64encode(b'admin:secret').decode()}"})

    assert response.status_code == 200
    assert "180.00" in response.text   # AAPL from reader
    assert "410.00" in response.text   # MSFT from adapter
    # adapter called only for MSFT (not AAPL which reader already handled)
    assert adapter.calls == [["MSFT"]]
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/unit/test_web_app.py::test_dashboard_uses_portfolio_reader_when_available -v
```

Expected: `FAILED` — `TypeError: create_app() got an unexpected keyword argument 'portfolio_reader'`

- [ ] **Step 3: Update `_fetch_latest_prices` in `app.py`**

Replace the current `_fetch_latest_prices` function (lines 870–879 of `src/alpaca_bot/web/app.py`):

```python
def _fetch_latest_prices(
    *,
    portfolio_reader: object | None,
    adapter: object | None,
    positions: list,
) -> dict[str, float]:
    if not positions:
        return {}
    symbols = list({p.symbol for p in positions})
    result: dict[str, float] = {}
    remaining = list(symbols)

    if portfolio_reader is not None:
        try:
            reader_prices = portfolio_reader.get_current_prices(symbols)  # type: ignore[union-attr]
            result.update(reader_prices)
            remaining = [s for s in symbols if s not in result]
        except Exception:
            import logging
            logging.getLogger(__name__).warning(
                "Failed to fetch prices from portfolio reader", exc_info=True
            )

    if remaining and adapter is not None:
        try:
            adapter_prices = adapter.get_latest_prices(remaining)  # type: ignore[union-attr]
            result.update(adapter_prices)
        except Exception:
            import logging
            logging.getLogger(__name__).warning(
                "Failed to fetch latest prices from market data adapter", exc_info=True
            )

    return result
```

- [ ] **Step 4: Add `portfolio_reader` parameter to `create_app` signature**

In `src/alpaca_bot/web/app.py`, add `portfolio_reader: object | None = None` to `create_app`'s parameter list (after the `market_data_adapter` param):

```python
def create_app(
    *,
    settings: Settings | None = None,
    connect: Callable[[str], ConnectionProtocol] | None = None,
    connection: ConnectionProtocol | None = None,
    db_connection: ConnectionProtocol | None = None,
    connect_postgres_fn: Callable[[str], ConnectionProtocol] | None = None,
    trading_status_store_factory: Callable[[ConnectionProtocol], object] | None = None,
    position_store_factory: Callable[[ConnectionProtocol], object] | None = None,
    order_store_factory: Callable[[ConnectionProtocol], object] | None = None,
    daily_session_state_store_factory: Callable[[ConnectionProtocol], object] | None = None,
    audit_event_store_factory: Callable[[ConnectionProtocol], object] | None = None,
    strategy_flag_store_factory: Callable[[ConnectionProtocol], object] | None = None,
    watchlist_store_factory: Callable[[ConnectionProtocol], object] | None = None,
    strategy_weight_store_factory: Callable[[ConnectionProtocol], object] | None = None,
    notifier: Notifier | None = None,
    market_data_adapter: object | None = None,
    portfolio_reader: object | None = None,
    equity_chart_data_factory: Callable[..., EquityChartData] | None = None,
) -> FastAPI:
```

- [ ] **Step 5: Initialize `portfolio_reader` at startup and store on `app.state`**

In `create_app`, after the existing `market_data_adapter` initialization block (lines 124–130), add:

```python
    if portfolio_reader is None:
        try:
            from alpaca_bot.execution.alpaca import AlpacaPortfolioReader
            portfolio_reader = AlpacaPortfolioReader.from_settings(app_settings)
        except Exception:
            portfolio_reader = None
    app.state.portfolio_reader = portfolio_reader
```

- [ ] **Step 6: Update the `_load_dashboard_data` call to pass both sources**

Replace the `_fetch_latest_prices` call in `_load_dashboard_data` (lines 893–896):

```python
        latest_prices = _fetch_latest_prices(
            portfolio_reader=app.state.portfolio_reader,
            adapter=app.state.market_data_adapter,
            positions=pre_positions,
        )
```

- [ ] **Step 7: Run the three new tests**

```bash
pytest tests/unit/test_web_app.py -k "portfolio_reader or reader" -v
```

Expected: 3 PASSED

- [ ] **Step 8: Run full web app test suite to check no regressions**

```bash
pytest tests/unit/test_web_app.py -v
```

Expected: all existing tests + 3 new = all PASSED

- [ ] **Step 9: Run full test suite**

```bash
pytest
```

Expected: all PASSED (count increases by 7 vs baseline)

- [ ] **Step 10: Commit**

```bash
git add src/alpaca_bot/web/app.py tests/unit/test_web_app.py
git commit -m "feat: use portfolio reader for live after-hours prices on dashboard"
```

---

### Task 3: Deploy and verify

**Files:** none — deploy only

- [ ] **Step 1: Deploy**

```bash
./scripts/deploy.sh /etc/alpaca_bot/alpaca-bot.env
```

Expected: migrate → supervisor → web → all healthy

- [ ] **Step 2: Verify services are up**

```bash
docker compose -f deploy/compose.yaml ps
```

Expected: `supervisor`, `web`, `postgres` all `Up`

- [ ] **Step 3: Tail supervisor logs for startup errors**

```bash
docker logs deploy-supervisor-1 --tail 30
```

Expected: no `AlpacaPortfolioReader` import errors, normal cycle startup

- [ ] **Step 4: Verify dashboard shows live prices**

Open the dashboard in a browser (or `curl -s http://localhost:18080/ | grep -o '[0-9]\+\.[0-9]\+'`). For open positions, the **Last** column should now match the `current_price` from Alpaca's broker — including after-hours pricing if the market is closed.

- [ ] **Step 5: Commit tag (optional)**

```bash
git tag live-prices-portfolio-$(date +%Y%m%d)
```
