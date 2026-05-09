# Market Context Filters Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add three entry filters (VIX regime gate, sector ETF breadth gate, VWAP entry filter) that default to disabled, store per-cycle market context in the DB, and enrich decision_log rows for ML training.

**Architecture:** A new pure `compute_market_context()` function computes VIX/sector state from fetched daily bars; the supervisor fetches, computes, saves to DB, and passes a `MarketContext` frozen dataclass into `evaluate_cycle()`; the engine applies two pre-loop gates and one per-symbol gate, stamping each `DecisionRecord` with the new context fields. All gates fail-open on missing data.

**Tech Stack:** Python 3.12, PostgreSQL (psycopg2-style sync cursors, `executemany()`), Alpaca-py stock bars, pytest with fake-callable DI pattern.

---

### Task 1: Domain types — `MarketContext` dataclass + 5 new `DecisionRecord` fields

**Files:**
- Modify: `src/alpaca_bot/domain/models.py`
- Modify: `src/alpaca_bot/domain/decision_record.py`
- Test: `tests/unit/test_domain_models.py` (create if absent)

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_domain_models.py
from datetime import datetime, timezone
from alpaca_bot.domain.models import MarketContext
from alpaca_bot.domain.decision_record import DecisionRecord


def test_market_context_defaults():
    ctx = MarketContext(as_of=datetime(2026, 1, 1, tzinfo=timezone.utc))
    assert ctx.vix_close is None
    assert ctx.vix_sma is None
    assert ctx.vix_above_sma is None
    assert ctx.sector_etf_states == {}
    assert ctx.sector_passing_pct is None


def test_market_context_populated():
    ctx = MarketContext(
        as_of=datetime(2026, 1, 1, tzinfo=timezone.utc),
        vix_close=18.5,
        vix_sma=17.2,
        vix_above_sma=True,
        sector_etf_states={"XLK": True, "XLF": False},
        sector_passing_pct=0.5,
    )
    assert ctx.vix_close == 18.5
    assert ctx.vix_above_sma is True
    assert ctx.sector_etf_states["XLK"] is True


def test_market_context_is_frozen():
    ctx = MarketContext(as_of=datetime(2026, 1, 1, tzinfo=timezone.utc))
    import pytest
    with pytest.raises(Exception):
        ctx.vix_close = 10.0  # type: ignore


def test_decision_record_new_fields_default_to_none():
    # Build a minimal valid DecisionRecord — check new fields default to None.
    dr = DecisionRecord(
        cycle_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        symbol="AAPL",
        strategy_name="breakout",
        trading_mode="paper",
        strategy_version="v1",
        decision="rejected",
        reject_stage="pre_filter",
        reject_reason="no_signal",
        entry_level=None,
        signal_bar_close=None,
        relative_volume=None,
        atr=None,
        stop_price=None,
        limit_price=None,
        initial_stop_price=None,
        quantity=None,
        risk_per_share=None,
        equity=10000.0,
        filter_results={},
    )
    assert dr.vix_close is None
    assert dr.vix_above_sma is None
    assert dr.sector_passing_pct is None
    assert dr.vwap_at_signal is None
    assert dr.signal_bar_above_vwap is None
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/unit/test_domain_models.py -v
```

Expected: `ImportError` or `AttributeError` — `MarketContext` does not exist yet.

- [ ] **Step 3: Add `MarketContext` to `domain/models.py`**

Open `src/alpaca_bot/domain/models.py`. At the top, `field` is already imported from `dataclasses` (it's used by `Bar`). The `datetime` import is already there. Add at the end of the file:

```python
@dataclass(frozen=True)
class MarketContext:
    as_of: datetime
    vix_close: float | None = None
    vix_sma: float | None = None
    vix_above_sma: bool | None = None
    sector_etf_states: dict[str, bool] = field(default_factory=dict)
    sector_passing_pct: float | None = None
```

- [ ] **Step 4: Add 5 new fields to `DecisionRecord`**

Open `src/alpaca_bot/domain/decision_record.py`. The frozen dataclass ends with `filter_results: dict`. Add after that last field:

```python
    vix_close: float | None = None
    vix_above_sma: bool | None = None
    sector_passing_pct: float | None = None
    vwap_at_signal: float | None = None
    signal_bar_above_vwap: bool | None = None
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
pytest tests/unit/test_domain_models.py -v
```

Expected: All 4 tests PASS.

- [ ] **Step 6: Run full test suite to check for regressions**

```bash
pytest
```

Expected: All previously passing tests still PASS.

- [ ] **Step 7: Commit**

```bash
git add src/alpaca_bot/domain/models.py src/alpaca_bot/domain/decision_record.py tests/unit/test_domain_models.py
git commit -m "feat: add MarketContext dataclass and 5 new DecisionRecord context fields"
```

---

### Task 2: Settings — 8 new configuration fields

**Files:**
- Modify: `src/alpaca_bot/config/__init__.py`
- Test: `tests/unit/test_config.py` (append to existing)

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_config.py`. The project's pattern for building a `Settings` in tests is to pass an explicit `environ` dict into `Settings.from_env(environ)` — avoid `os.environ` manipulation:

```python
def _base_env(**overrides: str) -> dict[str, str]:
    """Minimal env dict for Settings.from_env()."""
    base = {
        "TRADING_MODE": "paper",
        "ENABLE_LIVE_TRADING": "false",
        "STRATEGY_VERSION": "v1",
        "DATABASE_URL": "postgresql://x:y@localhost/z",
        "SYMBOLS": "AAPL",
    }
    base.update(overrides)
    return base


def test_market_context_filter_defaults():
    s = Settings.from_env(_base_env())
    assert s.enable_vix_filter is False
    assert s.vix_proxy_symbol == "VIXY"
    assert s.vix_lookback_bars == 20
    assert s.enable_sector_filter is False
    assert "XLK" in s.sector_etf_symbols
    assert len(s.sector_etf_symbols) == 11
    assert s.sector_etf_sma_period == 20
    assert s.sector_filter_min_passing_pct == 0.5
    assert s.enable_vwap_entry_filter is False


def test_market_context_filter_env_overrides():
    env = _base_env(
        ENABLE_VIX_FILTER="true",
        VIX_PROXY_SYMBOL="UVXY",
        VIX_LOOKBACK_BARS="30",
        ENABLE_SECTOR_FILTER="true",
        SECTOR_ETF_SYMBOLS="XLK,XLF,XLE",
        SECTOR_ETF_SMA_PERIOD="10",
        SECTOR_FILTER_MIN_PASSING_PCT="0.6",
        ENABLE_VWAP_ENTRY_FILTER="true",
    )
    s = Settings.from_env(env)
    assert s.enable_vix_filter is True
    assert s.vix_proxy_symbol == "UVXY"
    assert s.vix_lookback_bars == 30
    assert s.enable_sector_filter is True
    assert s.sector_etf_symbols == ("XLK", "XLF", "XLE")
    assert s.sector_etf_sma_period == 10
    assert s.sector_filter_min_passing_pct == 0.6
    assert s.enable_vwap_entry_filter is True
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/unit/test_config.py::test_market_context_filter_defaults tests/unit/test_config.py::test_market_context_filter_env_overrides -v
```

Expected: `AttributeError` — `Settings` has no `enable_vix_filter`.

- [ ] **Step 3: Add 8 fields to the `Settings` frozen dataclass**

Open `src/alpaca_bot/config/__init__.py`. Find the last field before `__post_init__` (currently `max_loss_per_trade_dollars: float | None = None`). Add after it:

```python
    enable_vix_filter: bool = False
    vix_proxy_symbol: str = "VIXY"
    vix_lookback_bars: int = 20
    enable_sector_filter: bool = False
    sector_etf_symbols: tuple[str, ...] = (
        "XLK", "XLF", "XLE", "XLV", "XLU", "XLI", "XLB", "XLRE", "XLC", "XLY", "XLP"
    )
    sector_etf_sma_period: int = 20
    sector_filter_min_passing_pct: float = 0.5
    enable_vwap_entry_filter: bool = False
```

- [ ] **Step 4: Add parsing in `from_env()`**

In `from_env()`, inside the `cls(...)` call, add these keyword arguments after `max_loss_per_trade_dollars`:

```python
            enable_vix_filter=_parse_bool(
                "ENABLE_VIX_FILTER", values.get("ENABLE_VIX_FILTER", "false")
            ),
            vix_proxy_symbol=values.get("VIX_PROXY_SYMBOL", "VIXY"),
            vix_lookback_bars=int(values.get("VIX_LOOKBACK_BARS", "20")),
            enable_sector_filter=_parse_bool(
                "ENABLE_SECTOR_FILTER", values.get("ENABLE_SECTOR_FILTER", "false")
            ),
            sector_etf_symbols=tuple(
                s.strip()
                for s in values.get(
                    "SECTOR_ETF_SYMBOLS",
                    "XLK,XLF,XLE,XLV,XLU,XLI,XLB,XLRE,XLC,XLY,XLP",
                ).split(",")
                if s.strip()
            ),
            sector_etf_sma_period=int(values.get("SECTOR_ETF_SMA_PERIOD", "20")),
            sector_filter_min_passing_pct=float(
                values.get("SECTOR_FILTER_MIN_PASSING_PCT", "0.5")
            ),
            enable_vwap_entry_filter=_parse_bool(
                "ENABLE_VWAP_ENTRY_FILTER", values.get("ENABLE_VWAP_ENTRY_FILTER", "false")
            ),
```

Note: `_parse_bool(name, value)` is the existing helper — takes the env var name as first arg for error messages, and the string value as second arg. `values` is already defined earlier in `from_env()` as `dict(os.environ if environ is None else environ)`.

- [ ] **Step 5: Run tests to verify they pass**

```bash
pytest tests/unit/test_config.py -v
```

Expected: All config tests PASS including the two new ones.

- [ ] **Step 6: Commit**

```bash
git add src/alpaca_bot/config/__init__.py tests/unit/test_config.py
git commit -m "feat: add 8 market context filter Settings fields with env var parsing"
```

---

### Task 3: Database migrations

**Files:**
- Create: `migrations/018_add_market_context.sql`
- Create: `migrations/019_add_decision_log_context_columns.sql`

- [ ] **Step 1: Verify migration numbering**

```bash
ls migrations/
```

Expected: Files named `017_*` or lower exist; no `018_` or `019_`.

- [ ] **Step 2: Create migration 018**

```sql
-- migrations/018_add_market_context.sql
CREATE TABLE market_context (
    id SERIAL PRIMARY KEY,
    as_of TIMESTAMPTZ NOT NULL,
    trading_mode VARCHAR(10) NOT NULL,
    vix_close FLOAT,
    vix_sma FLOAT,
    vix_above_sma BOOLEAN,
    sector_etf_states JSONB,
    sector_passing_pct FLOAT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX ON market_context (as_of, trading_mode);
```

- [ ] **Step 3: Create migration 019**

```sql
-- migrations/019_add_decision_log_context_columns.sql
ALTER TABLE decision_log
    ADD COLUMN vix_close FLOAT,
    ADD COLUMN vix_above_sma BOOLEAN,
    ADD COLUMN sector_passing_pct FLOAT,
    ADD COLUMN vwap_at_signal FLOAT,
    ADD COLUMN signal_bar_above_vwap BOOLEAN;
```

- [ ] **Step 4: Run migrations against the local DB**

```bash
alpaca-bot-migrate
```

Expected: Both new migrations applied without error. Verify with:

```bash
docker exec $(docker ps -qf name=postgres) psql -U alpaca_bot -d alpaca_bot -c "\d market_context"
docker exec $(docker ps -qf name=postgres) psql -U alpaca_bot -d alpaca_bot -c "\d decision_log" | grep vix
```

Expected: `market_context` table exists; `decision_log` shows `vix_close`, `vix_above_sma`, `sector_passing_pct`, `vwap_at_signal`, `signal_bar_above_vwap` columns.

- [ ] **Step 5: Commit**

```bash
git add migrations/018_add_market_context.sql migrations/019_add_decision_log_context_columns.sql
git commit -m "feat: add migrations 018/019 for market_context table and decision_log context columns"
```

---

### Task 4: Storage layer — `MarketContextStore` + `DecisionLogStore` extension

**Files:**
- Modify: `src/alpaca_bot/storage/repositories.py`
- Modify: `src/alpaca_bot/storage/__init__.py`
- Test: `tests/unit/test_repositories.py` (append to existing)

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_repositories.py`. The project's fake-cursor pattern: create a `_FakeCursor` that records `execute()` and `executemany()` calls, and a `_FakeConnection` with `.cursor()`. Note: `DecisionLogStore.bulk_insert()` uses `cursor().executemany()`, so the fake cursor MUST implement `executemany()`.

```python
from alpaca_bot.storage.repositories import MarketContextStore
from alpaca_bot.domain.models import MarketContext
from datetime import datetime, timezone
import json


class _FakeCursor:
    def __init__(self):
        self.executed = []
        self.many_executed = []
        self.rowcount = 1

    def execute(self, sql, params=None):
        self.executed.append((sql, params))

    def executemany(self, sql, params_list):
        self.many_executed.append((sql, params_list))

    def fetchone(self):
        return None

    def fetchall(self):
        return []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


class _FakeConnection:
    def __init__(self):
        self._cursor = _FakeCursor()

    def cursor(self):
        return self._cursor

    def commit(self):
        pass


def test_market_context_store_save_vix_only():
    conn = _FakeConnection()
    store = MarketContextStore(conn)
    ctx = MarketContext(
        as_of=datetime(2026, 1, 2, tzinfo=timezone.utc),
        vix_close=22.1,
        vix_sma=19.5,
        vix_above_sma=True,
        sector_etf_states={},
        sector_passing_pct=None,
    )
    store.save(ctx, "paper")
    assert len(conn._cursor.executed) == 1
    sql, params = conn._cursor.executed[0]
    assert "INSERT INTO market_context" in sql
    assert params[1] == "paper"
    assert params[2] == 22.1
    assert params[4] is True


def test_market_context_store_save_with_sector_states():
    conn = _FakeConnection()
    store = MarketContextStore(conn)
    states = {"XLK": True, "XLF": False}
    ctx = MarketContext(
        as_of=datetime(2026, 1, 2, tzinfo=timezone.utc),
        sector_etf_states=states,
        sector_passing_pct=0.72,
    )
    store.save(ctx, "live")
    sql, params = conn._cursor.executed[0]
    sector_json = params[5]
    assert json.loads(sector_json) == states
    assert params[6] == 0.72


def test_decision_log_store_bulk_insert_includes_context_columns():
    """DecisionLogStore.bulk_insert() SQL must include the 5 new context columns."""
    from alpaca_bot.storage.repositories import DecisionLogStore
    from alpaca_bot.domain.decision_record import DecisionRecord

    conn = _FakeConnection()
    store = DecisionLogStore(conn)
    dr = DecisionRecord(
        cycle_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        symbol="AAPL",
        strategy_name="breakout",
        trading_mode="paper",
        strategy_version="v1",
        decision="rejected",
        reject_stage="entry_filter",
        reject_reason="vwap_below",
        entry_level=None,
        signal_bar_close=None,
        relative_volume=None,
        atr=None,
        stop_price=None,
        limit_price=None,
        initial_stop_price=None,
        quantity=None,
        risk_per_share=None,
        equity=10000.0,
        filter_results={},
        vix_close=18.5,
        vix_above_sma=False,
        sector_passing_pct=0.64,
        vwap_at_signal=155.0,
        signal_bar_above_vwap=True,
    )
    # bulk_insert signature: (records: list, conn: ConnectionProtocol) -> None
    store.bulk_insert([dr], conn)
    assert len(conn._cursor.many_executed) == 1
    sql, params_list = conn._cursor.many_executed[0]
    assert "vix_close" in sql
    assert "vix_above_sma" in sql
    assert "sector_passing_pct" in sql
    assert "vwap_at_signal" in sql
    assert "signal_bar_above_vwap" in sql
    # Verify values round-tripped through params (params_list is a list of tuples)
    row = params_list[0]
    assert 18.5 in row
    assert 155.0 in row
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/unit/test_repositories.py::test_market_context_store_save_vix_only tests/unit/test_repositories.py::test_market_context_store_save_with_sector_states tests/unit/test_repositories.py::test_decision_log_store_bulk_insert_includes_context_columns -v
```

Expected: `ImportError` — `MarketContextStore` does not exist yet.

- [ ] **Step 3: Add `MarketContextStore` class to `repositories.py`**

At the end of `src/alpaca_bot/storage/repositories.py`, add:

```python
class MarketContextStore:
    def __init__(self, connection: "ConnectionProtocol") -> None:
        self._connection = connection

    def save(self, ctx: "MarketContext", trading_mode: str) -> None:
        import json as _json

        sector_json = (
            _json.dumps(ctx.sector_etf_states) if ctx.sector_etf_states else None
        )
        sql = """
            INSERT INTO market_context
                (as_of, trading_mode, vix_close, vix_sma, vix_above_sma,
                 sector_etf_states, sector_passing_pct)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """
        cur = self._connection.cursor()
        cur.execute(
            sql,
            (
                ctx.as_of,
                trading_mode,
                ctx.vix_close,
                ctx.vix_sma,
                ctx.vix_above_sma,
                sector_json,
                ctx.sector_passing_pct,
            ),
        )
```

Add the `TYPE_CHECKING` import for `MarketContext` (the file already uses `from __future__ import annotations` and `TYPE_CHECKING`):

```python
if TYPE_CHECKING:
    from alpaca_bot.domain.models import MarketContext
```

Add this inside the existing `if TYPE_CHECKING:` block already in the file.

- [ ] **Step 4: Extend `DecisionLogStore.bulk_insert()` with 5 new columns**

In `repositories.py`, find `DecisionLogStore.bulk_insert()`. The current SQL ends at `filter_results` with 19 columns and 19 `%s` placeholders. Add 5 more columns:

Current SQL column list (last line): `quantity, risk_per_share, equity, filter_results`
New SQL column list (last line): `quantity, risk_per_share, equity, filter_results, vix_close, vix_above_sma, sector_passing_pct, vwap_at_signal, signal_bar_above_vwap`

Current VALUES `%s` count: 19. New count: 24. Add 5 more `%s` placeholders.

In the params list comprehension, the current last line is `json.dumps(r.filter_results),`. Add after it:

```python
                r.vix_close,
                r.vix_above_sma,
                r.sector_passing_pct,
                r.vwap_at_signal,
                r.signal_bar_above_vwap,
```

The complete updated `bulk_insert` method:

```python
    def bulk_insert(self, records: list, conn: ConnectionProtocol) -> None:
        if not records:
            return
        sql = """
            INSERT INTO decision_log (
                cycle_at, symbol, strategy_name, trading_mode, strategy_version,
                decision, reject_stage, reject_reason,
                entry_level, signal_bar_close, relative_volume, atr,
                stop_price, limit_price, initial_stop_price,
                quantity, risk_per_share, equity, filter_results,
                vix_close, vix_above_sma, sector_passing_pct,
                vwap_at_signal, signal_bar_above_vwap
            ) VALUES (
                %s, %s, %s, %s, %s,
                %s, %s, %s,
                %s, %s, %s, %s,
                %s, %s, %s,
                %s, %s, %s, %s,
                %s, %s, %s,
                %s, %s
            )
        """
        params = [
            (
                r.cycle_at,
                r.symbol,
                r.strategy_name,
                r.trading_mode,
                r.strategy_version,
                r.decision,
                r.reject_stage,
                r.reject_reason,
                r.entry_level,
                r.signal_bar_close,
                r.relative_volume,
                r.atr,
                r.stop_price,
                r.limit_price,
                r.initial_stop_price,
                r.quantity,
                r.risk_per_share,
                r.equity,
                json.dumps(r.filter_results),
                r.vix_close,
                r.vix_above_sma,
                r.sector_passing_pct,
                r.vwap_at_signal,
                r.signal_bar_above_vwap,
            )
            for r in records
        ]
        cur = conn.cursor()
        cur.executemany(sql, params)
```

- [ ] **Step 5: Export `MarketContextStore` from `storage/__init__.py`**

Open `src/alpaca_bot/storage/__init__.py`. Find where `DecisionLogStore` is imported from `repositories`. Add `MarketContextStore` to the same import and to `__all__` (if it exists):

```python
from alpaca_bot.storage.repositories import (
    ...,
    DecisionLogStore,
    MarketContextStore,  # add this
)
```

- [ ] **Step 6: Run tests to verify they pass**

```bash
pytest tests/unit/test_repositories.py -v
```

Expected: All repository tests PASS.

- [ ] **Step 7: Commit**

```bash
git add src/alpaca_bot/storage/repositories.py src/alpaca_bot/storage/__init__.py tests/unit/test_repositories.py
git commit -m "feat: add MarketContextStore and extend DecisionLogStore with 5 context columns"
```

---

### Task 5: Bootstrap — wire `MarketContextStore` into `RuntimeContext`

**Files:**
- Modify: `src/alpaca_bot/runtime/bootstrap.py`
- Test: `tests/unit/test_bootstrap.py` (append or create)

- [ ] **Step 1: Write the failing test**

```python
# Append to tests/unit/test_bootstrap.py (or create it)
import dataclasses
from alpaca_bot.runtime.bootstrap import RuntimeContext


def test_runtime_context_has_market_context_store_field():
    """RuntimeContext must have a market_context_store field defaulting to None."""
    fields = {f.name: f for f in dataclasses.fields(RuntimeContext)}
    assert "market_context_store" in fields
    assert fields["market_context_store"].default is None
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/unit/test_bootstrap.py::test_runtime_context_has_market_context_store_field -v
```

Expected: FAIL — `market_context_store` field not found on `RuntimeContext`.

- [ ] **Step 3: Add `market_context_store` field to `RuntimeContext`**

Open `src/alpaca_bot/runtime/bootstrap.py`. Find the `RuntimeContext` dataclass. After `decision_log_store: DecisionLogStore | None = None`, add:

```python
    market_context_store: MarketContextStore | None = None
```

Add the import (in the `TYPE_CHECKING` block if the file uses that pattern, otherwise at the top):

```python
from alpaca_bot.storage import MarketContextStore
```

- [ ] **Step 4: Instantiate `MarketContextStore` in `bootstrap_runtime()`**

In the same file, find `bootstrap_runtime()`. Locate where `decision_log_store=DecisionLogStore(runtime_connection)` is set in the `RuntimeContext(...)` call. Add after `decision_log_store`:

```python
        market_context_store=MarketContextStore(runtime_connection),
```

- [ ] **Step 5: Update `reconnect_runtime_connection()` attribute list**

In `reconnect_runtime_connection()`, find the `for attr in (...)` tuple that lists store names to rewire. The current list ends with `"decision_log_store"`. Add `"market_context_store"` to the tuple:

```python
    for attr in (
        "trading_status_store",
        "audit_event_store",
        "order_store",
        "daily_session_state_store",
        "position_store",
        "strategy_flag_store",
        "watchlist_store",
        "option_order_store",
        "strategy_weight_store",
        "confidence_floor_store",
        "decision_log_store",
        "market_context_store",  # add this
    ):
```

This ensures the store gets a fresh connection after a reconnect, matching the pattern for all other stores.

- [ ] **Step 6: Run tests to verify they pass**

```bash
pytest tests/unit/test_bootstrap.py -v
```

Expected: All bootstrap tests PASS.

- [ ] **Step 7: Commit**

```bash
git add src/alpaca_bot/runtime/bootstrap.py tests/unit/test_bootstrap.py
git commit -m "feat: add market_context_store to RuntimeContext, bootstrap_runtime, and reconnect"
```

---

### Task 6: Pure compute function — `strategy/market_context.py`

**Files:**
- Create: `src/alpaca_bot/strategy/market_context.py`
- Test: `tests/unit/test_market_context_compute.py`

- [ ] **Step 1: Write the failing tests**

`Bar` has fields: `symbol`, `timestamp`, `open`, `high`, `low`, `close`, `volume` — NO `vwap` field. The `_make_bars()` helper must NOT include `vwap`.

```python
# tests/unit/test_market_context_compute.py
from datetime import datetime, timezone, timedelta
from alpaca_bot.domain.models import Bar
from alpaca_bot.strategy.market_context import compute_market_context
from alpaca_bot.config import Settings


def _make_settings(**overrides: str) -> Settings:
    """Build a minimal Settings using the project's from_env(environ) pattern."""
    env = {
        "TRADING_MODE": "paper",
        "ENABLE_LIVE_TRADING": "false",
        "STRATEGY_VERSION": "v1",
        "DATABASE_URL": "postgresql://x:y@localhost/z",
        "SYMBOLS": "AAPL",
    }
    env.update(overrides)
    return Settings.from_env(env)


def _make_bars(symbol: str, closes: list[float], base_date: datetime) -> list[Bar]:
    return [
        Bar(
            symbol=symbol,
            timestamp=base_date - timedelta(days=len(closes) - 1 - i),
            open=c,
            high=c,
            low=c,
            close=c,
            volume=1000,
        )
        for i, c in enumerate(closes)
    ]


NOW = datetime(2026, 1, 30, tzinfo=timezone.utc)


def test_compute_market_context_fail_open_on_empty_bars():
    """If bars dict is empty, return MarketContext with all filter fields None."""
    s = _make_settings()
    ctx = compute_market_context({}, s, NOW)
    assert ctx.as_of == NOW
    assert ctx.vix_close is None
    assert ctx.vix_above_sma is None
    assert ctx.sector_passing_pct is None


def test_compute_vix_above_sma():
    """VIX close above 20-bar SMA → vix_above_sma=True."""
    s = _make_settings()
    # 20 bars at 15.0 for SMA window, then final bar at 20.0 (above SMA of 15)
    closes = [15.0] * 20 + [20.0]
    bars = {"VIXY": _make_bars("VIXY", closes, NOW)}
    ctx = compute_market_context(bars, s, NOW)
    assert ctx.vix_close == 20.0
    assert ctx.vix_above_sma is True


def test_compute_vix_below_sma():
    """VIX close below 20-bar SMA → vix_above_sma=False."""
    s = _make_settings()
    closes = [20.0] * 20 + [15.0]
    bars = {"VIXY": _make_bars("VIXY", closes, NOW)}
    ctx = compute_market_context(bars, s, NOW)
    assert ctx.vix_close == 15.0
    assert ctx.vix_above_sma is False


def test_compute_vix_insufficient_history_fail_open():
    """Fewer than vix_lookback_bars+1 bars → vix fields None (fail-open)."""
    s = _make_settings()
    bars = {"VIXY": _make_bars("VIXY", [15.0] * 5, NOW)}
    ctx = compute_market_context(bars, s, NOW)
    assert ctx.vix_close is None
    assert ctx.vix_above_sma is None


def test_compute_sector_breadth():
    """6 of 11 ETFs above SMA → sector_passing_pct ≈ 0.545."""
    s = _make_settings()
    all_bars: dict = {}
    etfs = list(s.sector_etf_symbols)
    for i, etf in enumerate(etfs):
        # 20 bars at 100.0 for SMA window; last bar at 110 (above) for first 6, 90 (below) for rest
        final = 110.0 if i < 6 else 90.0
        all_bars[etf] = _make_bars(etf, [100.0] * 20 + [final], NOW)
    ctx = compute_market_context(all_bars, s, NOW)
    assert ctx.sector_passing_pct is not None
    assert abs(ctx.sector_passing_pct - 6 / 11) < 0.01
    true_count = sum(1 for v in ctx.sector_etf_states.values() if v)
    assert true_count == 6
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/unit/test_market_context_compute.py -v
```

Expected: `ModuleNotFoundError` — `strategy.market_context` does not exist.

- [ ] **Step 3: Create `strategy/market_context.py`**

```python
# src/alpaca_bot/strategy/market_context.py
from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime

from alpaca_bot.config import Settings
from alpaca_bot.domain.models import Bar, MarketContext


def compute_market_context(
    bars_by_symbol: Mapping[str, Sequence[Bar]],
    settings: Settings,
    as_of: datetime,
) -> MarketContext:
    vix_close, vix_sma, vix_above_sma = _compute_vix(bars_by_symbol, settings)
    sector_etf_states, sector_passing_pct = _compute_sector(bars_by_symbol, settings)
    return MarketContext(
        as_of=as_of,
        vix_close=vix_close,
        vix_sma=vix_sma,
        vix_above_sma=vix_above_sma,
        sector_etf_states=sector_etf_states,
        sector_passing_pct=sector_passing_pct,
    )


def _compute_vix(
    bars_by_symbol: Mapping[str, Sequence[Bar]],
    settings: Settings,
) -> tuple[float | None, float | None, bool | None]:
    bars = bars_by_symbol.get(settings.vix_proxy_symbol)
    if not bars or len(bars) < settings.vix_lookback_bars + 1:
        return None, None, None
    window = bars[-settings.vix_lookback_bars - 1 : -1]
    sma = sum(b.close for b in window) / len(window)
    close = bars[-1].close
    return close, sma, close > sma


def _compute_sector(
    bars_by_symbol: Mapping[str, Sequence[Bar]],
    settings: Settings,
) -> tuple[dict[str, bool], float | None]:
    states: dict[str, bool] = {}
    for etf in settings.sector_etf_symbols:
        bars = bars_by_symbol.get(etf)
        if not bars or len(bars) < settings.sector_etf_sma_period + 1:
            continue
        window = bars[-settings.sector_etf_sma_period - 1 : -1]
        sma = sum(b.close for b in window) / len(window)
        states[etf] = bars[-1].close > sma
    if not states:
        return {}, None
    passing_pct = sum(1 for v in states.values() if v) / len(states)
    return states, passing_pct
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/unit/test_market_context_compute.py -v
```

Expected: All 5 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/alpaca_bot/strategy/market_context.py tests/unit/test_market_context_compute.py
git commit -m "feat: add compute_market_context pure function for VIX and sector breadth"
```

---

### Task 7: Engine gates — VIX pre-loop, sector pre-loop, VWAP per-symbol

**Files:**
- Modify: `src/alpaca_bot/core/engine.py`
- Modify: `src/alpaca_bot/runtime/cycle.py`
- Test: `tests/unit/test_cycle_engine.py` (append to existing)

- [ ] **Step 1: Write the failing tests**

The existing test file uses `make_settings(**overrides)` where `overrides` is a `dict[str, str]` of env var values. Use the same helper. `evaluate_cycle()` requires: `settings`, `now`, `equity`, `intraday_bars_by_symbol`, `daily_bars_by_symbol`, `open_positions`, `working_order_symbols`, `traded_symbols_today`, `entries_disabled`. All keyword-only.

Append to `tests/unit/test_cycle_engine.py`:

```python
from alpaca_bot.domain.models import MarketContext

_MCX_NOW = datetime(2026, 4, 24, 19, 15, tzinfo=timezone.utc)


def _make_vix_settings(**overrides: str) -> Settings:
    return make_settings(ENABLE_VIX_FILTER="true", **overrides)


def _make_sector_settings(**overrides: str) -> Settings:
    return make_settings(ENABLE_SECTOR_FILTER="true", **overrides)


def _intraday() -> dict:
    return {"AAPL": make_breakout_intraday_bars("AAPL")}


def _daily() -> dict:
    return {"AAPL": make_daily_bars("AAPL")}


def test_vix_gate_blocks_entries_when_above_sma():
    """When VIX filter enabled and vix_above_sma=True, no entry intents emitted."""
    evaluate_cycle, _ = load_engine_api()
    ctx = MarketContext(as_of=_MCX_NOW, vix_above_sma=True)
    result = evaluate_cycle(
        settings=_make_vix_settings(),
        now=_MCX_NOW,
        equity=100_000.0,
        intraday_bars_by_symbol=_intraday(),
        daily_bars_by_symbol=_daily(),
        open_positions=[],
        working_order_symbols=set(),
        traded_symbols_today=set(),
        entries_disabled=False,
        market_context=ctx,
    )
    assert result.vix_blocked is True
    assert all(i.intent_type.value != "entry" for i in result.intents)


def test_vix_gate_allows_entries_when_below_sma():
    """When vix_above_sma=False, VIX gate does not block entries."""
    evaluate_cycle, _ = load_engine_api()
    ctx = MarketContext(as_of=_MCX_NOW, vix_above_sma=False)
    result = evaluate_cycle(
        settings=_make_vix_settings(),
        now=_MCX_NOW,
        equity=100_000.0,
        intraday_bars_by_symbol=_intraday(),
        daily_bars_by_symbol=_daily(),
        open_positions=[],
        working_order_symbols=set(),
        traded_symbols_today=set(),
        entries_disabled=False,
        market_context=ctx,
    )
    assert result.vix_blocked is False


def test_vix_gate_fail_open_when_context_is_none():
    """When VIX filter enabled but market_context=None, entries proceed (fail-open)."""
    evaluate_cycle, _ = load_engine_api()
    result = evaluate_cycle(
        settings=_make_vix_settings(),
        now=_MCX_NOW,
        equity=100_000.0,
        intraday_bars_by_symbol=_intraday(),
        daily_bars_by_symbol=_daily(),
        open_positions=[],
        working_order_symbols=set(),
        traded_symbols_today=set(),
        entries_disabled=False,
        market_context=None,
    )
    assert result.vix_blocked is False


def test_sector_gate_blocks_when_below_threshold():
    """Sector gate blocks entries when sector_passing_pct < 0.5 (default threshold)."""
    evaluate_cycle, _ = load_engine_api()
    ctx = MarketContext(as_of=_MCX_NOW, sector_passing_pct=0.3)
    result = evaluate_cycle(
        settings=_make_sector_settings(),
        now=_MCX_NOW,
        equity=100_000.0,
        intraday_bars_by_symbol=_intraday(),
        daily_bars_by_symbol=_daily(),
        open_positions=[],
        working_order_symbols=set(),
        traded_symbols_today=set(),
        entries_disabled=False,
        market_context=ctx,
    )
    assert result.sector_blocked is True


def test_sector_gate_allows_when_above_threshold():
    """Sector gate allows entries when sector_passing_pct >= 0.5."""
    evaluate_cycle, _ = load_engine_api()
    ctx = MarketContext(as_of=_MCX_NOW, sector_passing_pct=0.8)
    result = evaluate_cycle(
        settings=_make_sector_settings(),
        now=_MCX_NOW,
        equity=100_000.0,
        intraday_bars_by_symbol=_intraday(),
        daily_bars_by_symbol=_daily(),
        open_positions=[],
        working_order_symbols=set(),
        traded_symbols_today=set(),
        entries_disabled=False,
        market_context=ctx,
    )
    assert result.sector_blocked is False


def test_decision_records_stamped_with_vix_context():
    """DecisionRecords carry vix_close and vix_above_sma when VIX filter enabled."""
    evaluate_cycle, _ = load_engine_api()
    ctx = MarketContext(as_of=_MCX_NOW, vix_close=19.5, vix_sma=17.0, vix_above_sma=False)
    result = evaluate_cycle(
        settings=_make_vix_settings(),
        now=_MCX_NOW,
        equity=100_000.0,
        intraday_bars_by_symbol=_intraday(),
        daily_bars_by_symbol=_daily(),
        open_positions=[],
        working_order_symbols=set(),
        traded_symbols_today=set(),
        entries_disabled=False,
        market_context=ctx,
    )
    for dr in result.decision_records:
        assert dr.vix_close == 19.5
        assert dr.vix_above_sma is False
```

Note: `load_engine_api()` returns `(evaluate_cycle, CycleIntentType)` — it's already defined at the top of `test_cycle_engine.py`. `make_settings`, `make_daily_bars`, `make_breakout_intraday_bars` are also already defined in that file.

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/unit/test_cycle_engine.py::test_vix_gate_blocks_entries_when_above_sma -v
```

Expected: `TypeError` — `evaluate_cycle()` does not accept `market_context` parameter.

- [ ] **Step 3: Add `market_context` import to `engine.py`**

Open `src/alpaca_bot/core/engine.py`. Find the `if TYPE_CHECKING:` block. Add `MarketContext` to the import from `domain.models`:

```python
if TYPE_CHECKING:
    from alpaca_bot.storage import DailySessionState
    from alpaca_bot.domain.models import OptionContract, MarketContext
```

Since `MarketContext` is only used as a type annotation and in `isinstance` checks, `TYPE_CHECKING` is sufficient. Alternatively, import it directly at the top level if preferred — either works.

- [ ] **Step 4: Add `vix_blocked` and `sector_blocked` to `CycleResult`**

Find the `CycleResult` dataclass in `engine.py`. After `spread_blocked_symbols: tuple[str, ...] = ()`, add:

```python
    vix_blocked: bool = False
    sector_blocked: bool = False
```

- [ ] **Step 5: Add `market_context` parameter to `evaluate_cycle()`**

Find the `evaluate_cycle()` function signature. After `quotes_by_symbol: Mapping[str, Quote] | None = None,`, add:

```python
    market_context: "MarketContext | None" = None,
```

- [ ] **Step 6: Add VIX and sector pre-loop gates**

In `evaluate_cycle()`, find the regime filter block which ends at approximately:

```python
    _news_blocked: list[str] = []
    _spread_blocked: list[str] = []
```

Between the regime filter block and those two lines, add:

```python
    _vix_entries_blocked = False
    if (
        settings.enable_vix_filter
        and market_context is not None
        and market_context.vix_above_sma is True
    ):
        _vix_entries_blocked = True
        logger.info(
            "market_context: vix_above_sma=True — all entries blocked (VIX gate)"
        )

    _sector_entries_blocked = False
    if (
        settings.enable_sector_filter
        and market_context is not None
        and market_context.sector_passing_pct is not None
        and market_context.sector_passing_pct < settings.sector_filter_min_passing_pct
    ):
        _sector_entries_blocked = True
        logger.info(
            "market_context: sector_passing_pct=%.2f — all entries blocked "
            "(sector gate, threshold=%.2f)",
            market_context.sector_passing_pct,
            settings.sector_filter_min_passing_pct,
        )
```

Find the existing entry guard at approximately line 416:

```python
    if not entries_disabled and not _regime_entries_blocked:
```

Extend it to:

```python
    if (
        not entries_disabled
        and not _regime_entries_blocked
        and not _vix_entries_blocked
        and not _sector_entries_blocked
    ):
```

- [ ] **Step 7: Add VWAP per-symbol gate inside the symbol loop**

Inside the per-symbol loop, find where `signal_evaluator()` is called (currently around line 556 in engine.py). The VWAP gate must be placed AFTER `evaluate_breakout_signal()` returns a non-None signal, but BEFORE the quantity calculation.

Immediately after the `if signal is None: ... continue` block, add the VWAP gate:

```python
                _vwap_at_signal: float | None = None
                _signal_bar_above_vwap: bool | None = None
                if settings.enable_vwap_entry_filter:
                    _vwap_at_signal = calculate_vwap(bars)
                    if _vwap_at_signal is not None:
                        _signal_bar_above_vwap = signal.signal_bar.close >= _vwap_at_signal
                        if not _signal_bar_above_vwap:
                            _decision_records.append(DecisionRecord(
                                cycle_at=now,
                                symbol=symbol,
                                strategy_name=strategy_name,
                                trading_mode=_tm,
                                strategy_version=_sv,
                                decision="rejected",
                                reject_stage="entry_filter",
                                reject_reason="vwap_below",
                                entry_level=signal.entry_level,
                                signal_bar_close=signal.signal_bar.close,
                                relative_volume=signal.relative_volume,
                                atr=None,
                                stop_price=signal.stop_price,
                                limit_price=signal.limit_price,
                                initial_stop_price=signal.initial_stop_price,
                                quantity=None,
                                risk_per_share=None,
                                equity=equity,
                                filter_results={"vwap": False},
                                vix_close=market_context.vix_close if market_context else None,
                                vix_above_sma=market_context.vix_above_sma if market_context else None,
                                sector_passing_pct=market_context.sector_passing_pct if market_context else None,
                                vwap_at_signal=_vwap_at_signal,
                                signal_bar_above_vwap=_signal_bar_above_vwap,
                            ))
                            continue
```

`calculate_vwap` is already imported in `engine.py` from `alpaca_bot.strategy.indicators`.

- [ ] **Step 8: Stamp all existing `DecisionRecord` construction sites with 5 new fields**

Search `engine.py` for all `DecisionRecord(` occurrences. There are approximately 6 existing sites (regime_blocked, capacity_full, skipped_existing_position, skipped_already_traded, skipped_no_signal, accepted/rejected by capacity). For each one, add these keyword arguments:

```python
                    vix_close=market_context.vix_close if market_context else None,
                    vix_above_sma=market_context.vix_above_sma if market_context else None,
                    sector_passing_pct=market_context.sector_passing_pct if market_context else None,
                    vwap_at_signal=None,
                    signal_bar_above_vwap=None,
```

The VWAP gate site (added in Step 7) already has the full 5 fields; skip it. For all other sites, `vwap_at_signal=None` and `signal_bar_above_vwap=None` are always correct because those are per-symbol values only meaningful at the VWAP gate.

- [ ] **Step 9: Update `return CycleResult(...)`**

Find the `return CycleResult(...)` at the end of `evaluate_cycle()`. Add the two new fields:

```python
        vix_blocked=_vix_entries_blocked,
        sector_blocked=_sector_entries_blocked,
```

- [ ] **Step 10: Update `cycle.py` to accept and pass `market_context`**

Open `src/alpaca_bot/runtime/cycle.py`. Find `run_cycle()` (the keyword-only parameter list ends around `quotes_by_symbol`). Add parameter:

```python
    market_context: "MarketContext | None" = None,
```

Add the `TYPE_CHECKING` import at the top:

```python
if TYPE_CHECKING:
    ...
    from alpaca_bot.domain.models import MarketContext
```

In the `evaluate_cycle(...)` call inside `run_cycle()`, add:

```python
        market_context=market_context,
```

- [ ] **Step 11: Run engine tests**

```bash
pytest tests/unit/test_cycle_engine.py -v
```

Expected: All engine tests PASS including the 6 new gate tests.

- [ ] **Step 12: Run full test suite**

```bash
pytest
```

Expected: All tests PASS.

- [ ] **Step 13: Commit**

```bash
git add src/alpaca_bot/core/engine.py src/alpaca_bot/runtime/cycle.py tests/unit/test_cycle_engine.py
git commit -m "feat: add VIX/sector pre-loop gates and VWAP per-symbol gate to evaluate_cycle"
```

---

### Task 8: Supervisor integration — fetch, compute, save, pass

**Files:**
- Modify: `src/alpaca_bot/runtime/supervisor.py`
- Test: `tests/unit/test_supervisor.py` (append to existing)

- [ ] **Step 1: Inspect existing supervisor test pattern**

Before writing tests, read `tests/unit/test_supervisor.py` to understand how the supervisor is constructed in tests and which fixtures exist. The supervisor uses dependency injection for `cycle_runner` and `market_data` — tests pass fakes for both.

```bash
grep -n "def test_\|class Fake\|_cycle_runner\|market_data\|run_cycle_once" tests/unit/test_supervisor.py | head -40
```

Use the pattern you find there to build the tests below.

- [ ] **Step 2: Write the failing tests**

Append to `tests/unit/test_supervisor.py`. Adapt the fixture pattern from Step 1:

```python
from alpaca_bot.domain.models import MarketContext


def test_supervisor_fetches_context_when_vix_filter_enabled():
    """When ENABLE_VIX_FILTER=true, supervisor fetches daily bars including VIXY."""
    # Build settings with VIX filter enabled
    from tests.unit.test_cycle_engine import make_settings
    settings = make_settings(ENABLE_VIX_FILTER="true")

    daily_bar_calls: list[list[str]] = []

    class FakeMarketData:
        def get_daily_bars(self, symbols, **kwargs):
            daily_bar_calls.append(list(symbols))
            return {}
        async def get_daily_bars_async(self, *a, **kw):
            return {}
        def get_stock_bars(self, *a, **kw):
            return {}
        async def get_news(self, *a, **kw):
            return {}
        async def get_latest_quotes(self, *a, **kw):
            return {}

    # Use the existing supervisor test fixture pattern from this file to build a
    # supervisor instance with FakeMarketData and settings above, then call
    # run_cycle_once() once. After the call:
    assert any(settings.vix_proxy_symbol in call for call in daily_bar_calls), (
        f"Expected VIXY in daily bar fetch calls, got: {daily_bar_calls}"
    )


def test_supervisor_passes_market_context_to_cycle_runner():
    """market_context kwarg is passed from supervisor into the cycle runner."""
    from tests.unit.test_cycle_engine import make_settings
    settings = make_settings(ENABLE_VIX_FILTER="true")

    captured_kwargs: dict = {}

    def fake_cycle_runner(**kwargs):
        captured_kwargs.update(kwargs)
        from alpaca_bot.core.engine import CycleResult
        from datetime import datetime, timezone
        return CycleResult(as_of=datetime.now(timezone.utc))

    # Use the existing supervisor test fixture pattern from this file to build a
    # supervisor instance with fake_cycle_runner injected, then call run_cycle_once().
    # After the call:
    assert "market_context" in captured_kwargs, (
        f"Expected market_context in cycle runner kwargs, got: {list(captured_kwargs)}"
    )
```

Note: Fill in the supervisor construction details after reading the existing test file in Step 1. The key assertions are what matter — the exact supervisor wiring mirrors whatever pattern already exists in the file.

- [ ] **Step 3: Run tests to verify they fail**

```bash
pytest tests/unit/test_supervisor.py::test_supervisor_fetches_context_when_vix_filter_enabled -v
```

Expected: FAIL — supervisor does not yet fetch context bars or pass `market_context`.

- [ ] **Step 4: Add imports to `supervisor.py`**

Open `src/alpaca_bot/runtime/supervisor.py`. Add imports:

```python
from alpaca_bot.strategy.market_context import compute_market_context
from alpaca_bot.domain.models import MarketContext
```

- [ ] **Step 5: Add the context fetch block in `run_cycle_once()`**

In `run_cycle_once()`, find the existing daily bars fetch block (uses `get_daily_bars()` for regime bars). After that block and BEFORE the `self._cycle_runner(...)` call, add:

```python
        market_context: MarketContext | None = None

        if self.settings.enable_vix_filter or self.settings.enable_sector_filter:
            context_symbols: list[str] = []
            if self.settings.enable_vix_filter:
                context_symbols.append(self.settings.vix_proxy_symbol)
            if self.settings.enable_sector_filter:
                context_symbols.extend(self.settings.sector_etf_symbols)

            try:
                lookback_days = (
                    max(self.settings.vix_lookback_bars, self.settings.sector_etf_sma_period) + 10
                )
                context_bars = self.market_data.get_daily_bars(
                    symbols=context_symbols,
                    start=timestamp - timedelta(days=lookback_days * 2),
                    end=daily_bars_end,
                )
                market_context = compute_market_context(context_bars, self.settings, timestamp)
            except Exception:
                logger.warning(
                    "Failed to fetch market context bars; VIX/sector filters disabled this cycle",
                    exc_info=True,
                )
```

`timestamp` and `daily_bars_end` are variables already present in `run_cycle_once()` — match the exact names used by the existing daily bars fetch.

- [ ] **Step 6: Save market context to DB**

Immediately after the `compute_market_context()` call (inside the `try` block), add:

```python
            if market_context is not None:
                market_context_store = getattr(self.runtime, "market_context_store", None)
                if market_context_store is not None:
                    market_context_store.save(market_context, self.settings.trading_mode.value)
                    self.runtime.connection.commit()
```

`getattr` with a default of `None` ensures backward compatibility with existing tests that use a `RuntimeContext` without `market_context_store`.

- [ ] **Step 7: Pass `market_context` into the cycle runner**

Find the `self._cycle_runner(...)` call. Add `market_context=market_context` as a keyword argument:

```python
        result = self._cycle_runner(
            ...,
            market_context=market_context,
        )
```

- [ ] **Step 8: Run the supervisor tests**

```bash
pytest tests/unit/test_supervisor.py -v
```

Expected: All supervisor tests PASS.

- [ ] **Step 9: Run the full test suite**

```bash
pytest
```

Expected: All tests PASS.

- [ ] **Step 10: Commit**

```bash
git add src/alpaca_bot/runtime/supervisor.py tests/unit/test_supervisor.py
git commit -m "feat: supervisor fetches/computes/saves market context and passes to cycle runner"
```

---

## Final Verification

- [ ] **Run full test suite one more time**

```bash
pytest -v
```

Expected: All tests PASS, no regressions.

- [ ] **Verify the three filters default to disabled**

```bash
python -c "
from alpaca_bot.config import Settings
s = Settings.from_env({'TRADING_MODE':'paper','ENABLE_LIVE_TRADING':'false','STRATEGY_VERSION':'v1','DATABASE_URL':'postgresql://x:y@localhost/z','SYMBOLS':'AAPL'})
print(s.enable_vix_filter, s.enable_sector_filter, s.enable_vwap_entry_filter)
"
```

Expected: `False False False`

- [ ] **Verify migrations are listed in order**

```bash
ls migrations/ | grep -E "01[89]"
```

Expected: `018_add_market_context.sql` and `019_add_decision_log_context_columns.sql`.

- [ ] **Final commit if any files were missed**

```bash
git status
```

If any modified files are unstaged, stage and commit them.
