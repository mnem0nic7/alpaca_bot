# Strategy Stats Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add historical win/loss counts and current capital allocation percentage to each row in the Strategies panel on the main dashboard.

**Architecture:** A new `OrderStore.win_loss_counts_by_strategy()` method aggregates all-time wins/losses from the `orders` table using a CTE + LATERAL JOIN. A pure helper `_compute_capital_pct()` derives capital exposure percentages from the positions list already fetched by `load_dashboard_snapshot()`. Two new optional fields (`strategy_win_loss`, `strategy_capital_pct`) are added to `DashboardSnapshot` and rendered inline in the existing Strategies panel for-loop.

**Tech Stack:** Python, PostgreSQL (psycopg2), Jinja2, pytest, FastAPI

---

## File Map

| File | Action | What changes |
|---|---|---|
| `src/alpaca_bot/storage/repositories.py` | Modify | Add `OrderStore.win_loss_counts_by_strategy()` after `list_trade_pnl_by_strategy` (~line 769) |
| `src/alpaca_bot/web/service.py` | Modify | Add `_compute_capital_pct()` helper; add two fields to `DashboardSnapshot`; update `load_dashboard_snapshot()` to populate them |
| `src/alpaca_bot/web/templates/dashboard.html` | Modify | Add W/L and capital % spans to each strategy row in the `{% for name, flag %}` loop |
| `tests/unit/test_storage_db.py` | Modify | Add `TestWinLossCountsByStrategy` class after `TestListTradePnlByStrategy` |
| `tests/unit/test_web_service.py` | Modify | Update `make_snapshot_stores` to include `win_loss_counts_by_strategy`; add `_compute_capital_pct` tests and snapshot field tests |
| `tests/unit/test_web_app.py` | Modify | Add two tests for W/L and capital % rendering in the strategy rows |

---

## Task 1: `OrderStore.win_loss_counts_by_strategy()`

**Files:**
- Modify: `src/alpaca_bot/storage/repositories.py` (after line 768, before `class DailySessionStateStore`)
- Test: `tests/unit/test_storage_db.py` (add `TestWinLossCountsByStrategy` after `TestListTradePnlByStrategy`)

- [ ] **Step 1: Write the failing tests**

Add this class to `tests/unit/test_storage_db.py` after the `TestListTradePnlByStrategy` class (around line 600, before `# ── test_StrategyWeightStore`):

```python
# ── test_win_loss_counts_by_strategy ─────────────────────────────────────────

class TestWinLossCountsByStrategy:
    """Unit tests for OrderStore.win_loss_counts_by_strategy()."""

    def _make_store(self, rows: list[tuple]) -> "OrderStore":
        return OrderStore(_make_fake_connection(rows))

    def test_returns_empty_when_no_rows(self) -> None:
        store = self._make_store([])
        result = store.win_loss_counts_by_strategy(
            trading_mode=TradingMode.PAPER,
            strategy_version="v1",
        )
        assert result == {}

    def test_single_win(self) -> None:
        # row: (strategy_name, wins, losses) — as returned by the aggregation query
        rows = [("breakout", 1, 0)]
        store = self._make_store(rows)
        result = store.win_loss_counts_by_strategy(
            trading_mode=TradingMode.PAPER,
            strategy_version="v1",
        )
        assert result == {"breakout": (1, 0)}

    def test_single_loss(self) -> None:
        rows = [("breakout", 0, 1)]
        store = self._make_store(rows)
        result = store.win_loss_counts_by_strategy(
            trading_mode=TradingMode.PAPER,
            strategy_version="v1",
        )
        assert result == {"breakout": (0, 1)}

    def test_multiple_strategies(self) -> None:
        rows = [
            ("breakout", 5, 2),
            ("momentum", 1, 3),
        ]
        store = self._make_store(rows)
        result = store.win_loss_counts_by_strategy(
            trading_mode=TradingMode.PAPER,
            strategy_version="v1",
        )
        assert result == {"breakout": (5, 2), "momentum": (1, 3)}

    def test_strategy_with_only_losses(self) -> None:
        rows = [("orb", 0, 4)]
        store = self._make_store(rows)
        result = store.win_loss_counts_by_strategy(
            trading_mode=TradingMode.PAPER,
            strategy_version="v1",
        )
        assert result["orb"] == (0, 4)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/unit/test_storage_db.py::TestWinLossCountsByStrategy -v
```

Expected: FAIL with `AttributeError: 'OrderStore' object has no attribute 'win_loss_counts_by_strategy'`

- [ ] **Step 3: Implement the method**

In `src/alpaca_bot/storage/repositories.py`, add this method to `OrderStore` after `list_trade_pnl_by_strategy` (after line 768, before the blank line before `class DailySessionStateStore`):

```python
    def win_loss_counts_by_strategy(
        self,
        *,
        trading_mode: TradingMode,
        strategy_version: str,
    ) -> dict[str, tuple[int, int]]:
        """Return all-time win and loss counts per strategy.

        Returns {strategy_name: (wins, losses)}.
        A win is a closed trade with pnl > 0; a loss is pnl <= 0.
        Trades with no correlated filled entry order are excluded.
        """
        rows = fetch_all(
            self._connection,
            """
            WITH trade_pnl AS (
                SELECT x.strategy_name,
                       (x.fill_price - e.fill_price)
                           * COALESCE(x.filled_quantity, x.quantity) AS pnl
                  FROM orders x
                  JOIN LATERAL (
                      SELECT fill_price
                        FROM orders e
                       WHERE e.symbol = x.symbol
                         AND e.trading_mode = x.trading_mode
                         AND e.strategy_version = x.strategy_version
                         AND e.strategy_name IS NOT DISTINCT FROM x.strategy_name
                         AND e.intent_type = 'entry'
                         AND e.fill_price IS NOT NULL
                         AND e.status = 'filled'
                         AND e.updated_at <= x.updated_at
                       ORDER BY e.updated_at DESC
                       LIMIT 1
                  ) e ON true
                 WHERE x.trading_mode = %s
                   AND x.strategy_version = %s
                   AND x.intent_type IN ('stop', 'exit')
                   AND x.fill_price IS NOT NULL
                   AND x.status = 'filled'
            )
            SELECT strategy_name,
                   COUNT(*) FILTER (WHERE pnl > 0)  AS wins,
                   COUNT(*) FILTER (WHERE pnl <= 0) AS losses
              FROM trade_pnl
             GROUP BY strategy_name
            """,
            (trading_mode.value, strategy_version),
        )
        return {row[0]: (int(row[1]), int(row[2])) for row in rows}
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/unit/test_storage_db.py::TestWinLossCountsByStrategy -v
```

Expected: 5 tests PASS

- [ ] **Step 5: Run full suite for regressions**

```bash
pytest tests/unit/test_storage_db.py -v
```

Expected: All tests PASS (no regressions in storage layer)

- [ ] **Step 6: Commit**

```bash
git add src/alpaca_bot/storage/repositories.py tests/unit/test_storage_db.py
git commit -m "feat: add OrderStore.win_loss_counts_by_strategy() for dashboard display"
```

---

## Task 2: `_compute_capital_pct()` and `DashboardSnapshot` new fields

**Files:**
- Modify: `src/alpaca_bot/web/service.py`
- Modify: `tests/unit/test_web_service.py`

### Part A: `_compute_capital_pct()` helper and snapshot fields

- [ ] **Step 1: Write failing tests for `_compute_capital_pct()`**

In `tests/unit/test_web_service.py`, add this import at the top (in the import block from `alpaca_bot.web.service`):

```python
from alpaca_bot.web.service import (
    ...existing imports...,
    _compute_capital_pct,
)
```

Then add these tests after the existing `test_win_rate_*` tests at the end of the file:

```python
# ---------------------------------------------------------------------------
# _compute_capital_pct
# ---------------------------------------------------------------------------

def test_compute_capital_pct_empty_positions() -> None:
    result = _compute_capital_pct([], {})
    assert result == {}


def test_compute_capital_pct_single_strategy_all_capital() -> None:
    from types import SimpleNamespace
    pos = SimpleNamespace(symbol="AAPL", entry_price=100.0, quantity=10, strategy_name="breakout")
    result = _compute_capital_pct([pos], {"AAPL": 105.0})
    assert result == {"breakout": pytest.approx(100.0)}


def test_compute_capital_pct_two_strategies() -> None:
    from types import SimpleNamespace
    pos_a = SimpleNamespace(symbol="AAPL", entry_price=100.0, quantity=10, strategy_name="breakout")
    pos_b = SimpleNamespace(symbol="MSFT", entry_price=200.0, quantity=5, strategy_name="momentum")
    # AAPL: 10*100=1000, MSFT: 5*200=1000 (no latest_prices, uses entry_price)
    result = _compute_capital_pct([pos_a, pos_b], {})
    assert result["breakout"] == pytest.approx(50.0)
    assert result["momentum"] == pytest.approx(50.0)


def test_compute_capital_pct_uses_latest_price_over_entry_price() -> None:
    from types import SimpleNamespace
    pos = SimpleNamespace(symbol="AAPL", entry_price=100.0, quantity=10, strategy_name="breakout")
    # latest price is 110, so value = 1100, not 1000
    result = _compute_capital_pct([pos], {"AAPL": 110.0})
    assert result == {"breakout": pytest.approx(100.0)}  # still 100% — only one strategy


def test_compute_capital_pct_falls_back_to_entry_price_when_no_latest() -> None:
    from types import SimpleNamespace
    pos_a = SimpleNamespace(symbol="AAPL", entry_price=100.0, quantity=10, strategy_name="breakout")
    pos_b = SimpleNamespace(symbol="MSFT", entry_price=300.0, quantity=10, strategy_name="momentum")
    # No latest prices → AAPL: 1000, MSFT: 3000 → breakout=25%, momentum=75%
    result = _compute_capital_pct([pos_a, pos_b], {})
    assert result["breakout"] == pytest.approx(25.0)
    assert result["momentum"] == pytest.approx(75.0)


def test_compute_capital_pct_rounds_to_one_decimal() -> None:
    from types import SimpleNamespace
    pos_a = SimpleNamespace(symbol="AAPL", entry_price=100.0, quantity=1, strategy_name="breakout")
    pos_b = SimpleNamespace(symbol="MSFT", entry_price=200.0, quantity=1, strategy_name="momentum")
    # breakout: 100/300 = 33.333...% → rounds to 33.3
    result = _compute_capital_pct([pos_a, pos_b], {})
    assert result["breakout"] == pytest.approx(33.3, abs=0.05)
    assert result["momentum"] == pytest.approx(66.7, abs=0.05)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/unit/test_web_service.py -k "compute_capital_pct" -v
```

Expected: FAIL with `ImportError: cannot import name '_compute_capital_pct'`

- [ ] **Step 3: Add `_compute_capital_pct()` to `service.py`**

In `src/alpaca_bot/web/service.py`, add this function. Place it just before `load_dashboard_snapshot()` (before its `def` line, around line 164):

```python
def _compute_capital_pct(
    positions: list,
    latest_prices: dict[str, float],
) -> dict[str, float]:
    """Return {strategy_name: pct_of_total} from open positions.

    Uses latest_prices when available, falls back to entry_price.
    Returns {} when positions is empty.
    """
    strategy_value: dict[str, float] = {}
    for pos in positions:
        price = latest_prices.get(pos.symbol, pos.entry_price)
        val = price * pos.quantity
        strategy_value[pos.strategy_name] = strategy_value.get(pos.strategy_name, 0.0) + val
    total = sum(strategy_value.values())
    if total <= 0:
        return {}
    return {name: round(val / total * 100, 1) for name, val in strategy_value.items()}
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/unit/test_web_service.py -k "compute_capital_pct" -v
```

Expected: 6 tests PASS

### Part B: `DashboardSnapshot` new fields and `load_dashboard_snapshot()` update

- [ ] **Step 5: Update `make_snapshot_stores` in `test_web_service.py`**

Find `make_snapshot_stores` in `tests/unit/test_web_service.py` (around line 67) and add `win_loss_counts_by_strategy` to the `order_store`:

```python
def make_snapshot_stores(*, events=None, latest=None):
    return dict(
        trading_status_store=SimpleNamespace(load=lambda **_: None),
        daily_session_state_store=SimpleNamespace(load=lambda **_: None),
        position_store=SimpleNamespace(list_all=lambda **_: []),
        order_store=SimpleNamespace(
            list_by_status=lambda **_: [],
            list_recent=lambda **_: [],
            win_loss_counts_by_strategy=lambda **_: {},
        ),
        audit_event_store=make_audit_store(events=events, latest=latest),
        strategy_flag_store=SimpleNamespace(list_all=lambda **_: []),
    )
```

- [ ] **Step 6: Write failing tests for snapshot fields**

Add these tests to `tests/unit/test_web_service.py` after the `_compute_capital_pct` tests:

```python
# ---------------------------------------------------------------------------
# DashboardSnapshot.strategy_win_loss and strategy_capital_pct
# ---------------------------------------------------------------------------

def test_load_dashboard_snapshot_populates_strategy_win_loss() -> None:
    """win_loss_counts_by_strategy result is exposed on the snapshot."""
    fixed_now = datetime(2026, 5, 6, 14, 0, tzinfo=timezone.utc)
    win_loss_data = {"breakout": (5, 2), "momentum": (1, 3)}

    snapshot = load_dashboard_snapshot(
        settings=make_settings(),
        connection=SimpleNamespace(),
        now=fixed_now,
        **{
            **make_snapshot_stores(),
            "order_store": SimpleNamespace(
                list_by_status=lambda **_: [],
                list_recent=lambda **_: [],
                win_loss_counts_by_strategy=lambda **_: win_loss_data,
            ),
        },
    )

    assert snapshot.strategy_win_loss == {"breakout": (5, 2), "momentum": (1, 3)}


def test_load_dashboard_snapshot_strategy_win_loss_empty_when_no_closed_trades() -> None:
    """strategy_win_loss is {} when win_loss_counts_by_strategy returns empty dict."""
    fixed_now = datetime(2026, 5, 6, 14, 0, tzinfo=timezone.utc)

    snapshot = load_dashboard_snapshot(
        settings=make_settings(),
        connection=SimpleNamespace(),
        now=fixed_now,
        **make_snapshot_stores(),
    )

    assert snapshot.strategy_win_loss == {}


def test_load_dashboard_snapshot_populates_strategy_capital_pct() -> None:
    """Capital pct is computed from positions returned by position_store.list_all."""
    from types import SimpleNamespace as SN
    fixed_now = datetime(2026, 5, 6, 14, 0, tzinfo=timezone.utc)
    pos = SN(symbol="AAPL", entry_price=100.0, quantity=10, strategy_name="breakout")

    snapshot = load_dashboard_snapshot(
        settings=make_settings(),
        connection=SimpleNamespace(),
        now=fixed_now,
        **{
            **make_snapshot_stores(),
            "position_store": SimpleNamespace(list_all=lambda **_: [pos]),
        },
        latest_prices={"AAPL": 105.0},
    )

    assert snapshot.strategy_capital_pct == {"breakout": pytest.approx(100.0)}
```

- [ ] **Step 7: Run tests to verify they fail**

```bash
pytest tests/unit/test_web_service.py -k "strategy_win_loss or strategy_capital_pct" -v
```

Expected: FAIL with `AttributeError: 'DashboardSnapshot' object has no attribute 'strategy_win_loss'`

- [ ] **Step 8: Add new fields to `DashboardSnapshot` and update `load_dashboard_snapshot()`**

In `src/alpaca_bot/web/service.py`, find `DashboardSnapshot` (around line 147) and add the two new fields at the end (they must have defaults to avoid breaking existing code that constructs `DashboardSnapshot` without these fields):

```python
@dataclass(frozen=True)
class DashboardSnapshot:
    generated_at: datetime
    trading_status: TradingStatus | None
    session_state: DailySessionState | None
    positions: list[PositionRecord]
    working_orders: list[OrderRecord]
    recent_orders: list[OrderRecord]
    recent_events: list[AuditEvent]
    worker_health: WorkerHealth
    strategy_flags: list[tuple[str, StrategyFlag | None]]
    strategy_entries_disabled: dict[str, bool] = dc_field(default_factory=dict)
    latest_prices: dict[str, float] = dc_field(default_factory=dict)
    realized_pnl: float | None = None
    loss_limit_amount: float | None = None
    strategy_win_loss: dict[str, tuple[int, int]] = dc_field(default_factory=dict)
    strategy_capital_pct: dict[str, float] = dc_field(default_factory=dict)
```

Then in `load_dashboard_snapshot()`, hoist the positions query out of the `DashboardSnapshot(...)` constructor call and compute the two new values. Find the `return DashboardSnapshot(` block and restructure it as follows:

**Before** (find the `positions=position_store.list_all(...)` inside the constructor call and the final `return` statement):

```python
    return DashboardSnapshot(
        generated_at=generated_at,
        trading_status=trading_status_store.load(
            trading_mode=settings.trading_mode,
            strategy_version=settings.strategy_version,
        ),
        session_state=session_state,
        positions=position_store.list_all(
            trading_mode=settings.trading_mode,
            strategy_version=settings.strategy_version,
        ),
        working_orders=order_store.list_by_status(
```

**After** (hoist positions, add win_loss and capital_pct):

```python
    positions = position_store.list_all(
        trading_mode=settings.trading_mode,
        strategy_version=settings.strategy_version,
    )
    if hasattr(order_store, "win_loss_counts_by_strategy"):
        strategy_win_loss: dict[str, tuple[int, int]] = order_store.win_loss_counts_by_strategy(
            trading_mode=settings.trading_mode,
            strategy_version=settings.strategy_version,
        )
    else:
        strategy_win_loss = {}
    strategy_capital_pct = _compute_capital_pct(positions, latest_prices or {})

    return DashboardSnapshot(
        generated_at=generated_at,
        trading_status=trading_status_store.load(
            trading_mode=settings.trading_mode,
            strategy_version=settings.strategy_version,
        ),
        session_state=session_state,
        positions=positions,
        working_orders=order_store.list_by_status(
```

And at the end of the constructor call, add the two new fields before the closing `)`):

```python
        strategy_flags=strategy_flags,
        strategy_entries_disabled=strategy_entries_disabled,
        latest_prices=latest_prices or {},
        realized_pnl=realized_pnl,
        loss_limit_amount=loss_limit_amount,
        strategy_win_loss=strategy_win_loss,
        strategy_capital_pct=strategy_capital_pct,
    )
```

- [ ] **Step 9: Run tests to verify they pass**

```bash
pytest tests/unit/test_web_service.py -k "strategy_win_loss or strategy_capital_pct or compute_capital_pct" -v
```

Expected: All new tests PASS

- [ ] **Step 10: Run full service test suite for regressions**

```bash
pytest tests/unit/test_web_service.py -v
```

Expected: All tests PASS

- [ ] **Step 11: Commit**

```bash
git add src/alpaca_bot/web/service.py tests/unit/test_web_service.py
git commit -m "feat: add strategy win/loss and capital allocation fields to DashboardSnapshot"
```

---

## Task 3: Dashboard template and rendering tests

**Files:**
- Modify: `src/alpaca_bot/web/templates/dashboard.html`
- Modify: `tests/unit/test_web_app.py`

### Part A: Template update

- [ ] **Step 1: Update the Strategies panel in `dashboard.html`**

> **Character note:** The `—` below is the literal Unicode em dash (U+2014), not the HTML entity `&mdash;`. The test asserts `"—" in response.text` against the raw response string, so both template and test must use the same literal character. Copy-paste as-is.

Find the `{% for name, flag in snapshot.strategy_flags %}` loop in `src/alpaca_bot/web/templates/dashboard.html` (around line 321). The current strategy row ends after the "Disable Entries" form `</form>` at line 343. Add two read-only spans immediately after that closing `</form>` tag, before the closing `</div>`:

**Find this block** (lines 321–345):
```html
        {% for name, flag in snapshot.strategy_flags %}
          {% set entries_off = snapshot.strategy_entries_disabled.get(name, false) %}
          <div style="display: flex; flex-wrap: wrap; align-items: center; gap: 0.6rem; margin-bottom: 0.7rem;">
            <span class="mono" style="min-width: 8rem;">{{ name }}</span>
            {% set is_enabled = (flag is none or flag.enabled) %}
            <span class="{{ '' if is_enabled else 'warn' }}" style="min-width: 5rem;">
              {{ "Enabled" if is_enabled else "Disabled" }}
            </span>
            <form method="post" action="/strategies/{{ name }}/toggle">
              <input type="hidden" name="_csrf_token" value="{{ csrf_token_for(request, 'toggle') }}">
              <button type="submit" class="btn-neutral">
                {{ "Disable" if is_enabled else "Enable" }}
              </button>
            </form>
            <span class="{{ 'warn' if entries_off else 'muted' }}" style="min-width: 8rem;">
              Entries: {{ "disabled" if entries_off else "enabled" }}
            </span>
            <form method="post" action="/strategies/{{ name }}/toggle-entries">
              <input type="hidden" name="_csrf_token" value="{{ csrf_token_for(request, 'toggle') }}">
              <button type="submit" class="btn-neutral">
                {{ "Enable Entries" if entries_off else "Disable Entries" }}
              </button>
            </form>
          </div>
        {% endfor %}
```

**Replace with:**
```html
        {% for name, flag in snapshot.strategy_flags %}
          {%- set entries_off = snapshot.strategy_entries_disabled.get(name, false) %}
          {%- set wl = snapshot.strategy_win_loss.get(name) %}
          {%- set cap = snapshot.strategy_capital_pct.get(name, 0.0) %}
          <div style="display: flex; flex-wrap: wrap; align-items: center; gap: 0.6rem; margin-bottom: 0.7rem;">
            <span class="mono" style="min-width: 8rem;">{{ name }}</span>
            {% set is_enabled = (flag is none or flag.enabled) %}
            <span class="{{ '' if is_enabled else 'warn' }}" style="min-width: 5rem;">
              {{ "Enabled" if is_enabled else "Disabled" }}
            </span>
            <form method="post" action="/strategies/{{ name }}/toggle">
              <input type="hidden" name="_csrf_token" value="{{ csrf_token_for(request, 'toggle') }}">
              <button type="submit" class="btn-neutral">
                {{ "Disable" if is_enabled else "Enable" }}
              </button>
            </form>
            <span class="{{ 'warn' if entries_off else 'muted' }}" style="min-width: 8rem;">
              Entries: {{ "disabled" if entries_off else "enabled" }}
            </span>
            <form method="post" action="/strategies/{{ name }}/toggle-entries">
              <input type="hidden" name="_csrf_token" value="{{ csrf_token_for(request, 'toggle') }}">
              <button type="submit" class="btn-neutral">
                {{ "Enable Entries" if entries_off else "Disable Entries" }}
              </button>
            </form>
            <span class="muted" style="min-width: 7rem; white-space: nowrap;">
              {% if wl %}{{ wl[0] }}W / {{ wl[1] }}L{% else %}—{% endif %}
            </span>
            <span class="muted" style="min-width: 4rem; text-align: right;">
              {% if cap > 0 %}{{ "%.1f" | format(cap) }}%{% else %}0%{% endif %}
            </span>
          </div>
        {% endfor %}
```

### Part B: Rendering tests

- [ ] **Step 2: Write the failing tests**

Append these two tests to the end of `tests/unit/test_web_app.py`. Note that the `order_store_factory` must include `win_loss_counts_by_strategy` since `load_dashboard_snapshot()` uses `hasattr` to call it:

```python
def test_dashboard_strategy_win_loss_rendered() -> None:
    """Strategy row shows W/L counts from snapshot.strategy_win_loss."""
    now = datetime.now(timezone.utc)
    settings = make_settings()
    connection = FakeConnection(responses=[])

    app = create_app(
        settings=settings,
        connect_postgres_fn=ConnectionFactory([connection]),
        trading_status_store_factory=lambda _connection: SimpleNamespace(
            load=lambda **_kwargs: None
        ),
        daily_session_state_store_factory=lambda _connection: SimpleNamespace(
            load=lambda **_kwargs: None
        ),
        position_store_factory=lambda _connection: SimpleNamespace(
            list_all=lambda **_kwargs: []
        ),
        order_store_factory=lambda _connection: SimpleNamespace(
            list_by_status=lambda **_kwargs: [],
            list_recent=lambda **_kwargs: [],
            win_loss_counts_by_strategy=lambda **_kwargs: {"breakout": (5, 2)},
        ),
        audit_event_store_factory=lambda _connection: SimpleNamespace(
            list_recent=lambda **_kwargs: [],
            load_latest=lambda **_kwargs: None,
            list_by_event_types=lambda **_kwargs: [],
        ),
    )

    with TestClient(app) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert "5W / 2L" in response.text


def test_dashboard_strategy_no_history_shows_dash() -> None:
    """Strategy row shows — when win_loss is empty (no closed trades)."""
    now = datetime.now(timezone.utc)
    settings = make_settings()
    connection = FakeConnection(responses=[])

    app = create_app(
        settings=settings,
        connect_postgres_fn=ConnectionFactory([connection]),
        trading_status_store_factory=lambda _connection: SimpleNamespace(
            load=lambda **_kwargs: None
        ),
        daily_session_state_store_factory=lambda _connection: SimpleNamespace(
            load=lambda **_kwargs: None
        ),
        position_store_factory=lambda _connection: SimpleNamespace(
            list_all=lambda **_kwargs: []
        ),
        order_store_factory=lambda _connection: SimpleNamespace(
            list_by_status=lambda **_kwargs: [],
            list_recent=lambda **_kwargs: [],
            win_loss_counts_by_strategy=lambda **_kwargs: {},
        ),
        audit_event_store_factory=lambda _connection: SimpleNamespace(
            list_recent=lambda **_kwargs: [],
            load_latest=lambda **_kwargs: None,
            list_by_event_types=lambda **_kwargs: [],
        ),
    )

    with TestClient(app) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert "—" in response.text


def test_dashboard_strategy_capital_pct_rendered() -> None:
    """Strategy row shows capital % when positions exist."""
    now = datetime.now(timezone.utc)
    settings = make_settings()
    connection = FakeConnection(responses=[])

    app = create_app(
        settings=settings,
        connect_postgres_fn=ConnectionFactory([connection]),
        trading_status_store_factory=lambda _connection: SimpleNamespace(
            load=lambda **_kwargs: None
        ),
        daily_session_state_store_factory=lambda _connection: SimpleNamespace(
            load=lambda **_kwargs: None
        ),
        position_store_factory=lambda _connection: SimpleNamespace(
            list_all=lambda **_kwargs: [
                PositionRecord(
                    symbol="AAPL",
                    trading_mode=TradingMode.PAPER,
                    strategy_version=settings.strategy_version,
                    quantity=10,
                    entry_price=100.0,
                    stop_price=95.0,
                    initial_stop_price=95.0,
                    opened_at=now,
                    updated_at=now,
                    strategy_name="breakout",
                )
            ]
        ),
        order_store_factory=lambda _connection: SimpleNamespace(
            list_by_status=lambda **_kwargs: [],
            list_recent=lambda **_kwargs: [],
            win_loss_counts_by_strategy=lambda **_kwargs: {},
        ),
        audit_event_store_factory=lambda _connection: SimpleNamespace(
            list_recent=lambda **_kwargs: [],
            load_latest=lambda **_kwargs: None,
            list_by_event_types=lambda **_kwargs: [],
        ),
    )

    with TestClient(app) as client:
        response = client.get("/")

    assert response.status_code == 200
    # breakout is the only strategy with a position → 100.0%
    assert "100.0%" in response.text
```

- [ ] **Step 3: Run failing tests**

```bash
pytest tests/unit/test_web_app.py::test_dashboard_strategy_win_loss_rendered tests/unit/test_web_app.py::test_dashboard_strategy_no_history_shows_dash tests/unit/test_web_app.py::test_dashboard_strategy_capital_pct_rendered -v
```

Expected: FAIL (template doesn't have the new spans yet — apply template change first, or FAIL with wrong content)

- [ ] **Step 4: Apply the template change** (from Step 1 of Part A above if not already done)

Edit `src/alpaca_bot/web/templates/dashboard.html` as described in Step 1.

- [ ] **Step 5: Run tests to verify they pass**

```bash
pytest tests/unit/test_web_app.py::test_dashboard_strategy_win_loss_rendered tests/unit/test_web_app.py::test_dashboard_strategy_no_history_shows_dash tests/unit/test_web_app.py::test_dashboard_strategy_capital_pct_rendered -v
```

Expected: All 3 PASS

- [ ] **Step 6: Run full test suite for regressions**

```bash
pytest
```

Expected: All tests PASS (no regressions)

- [ ] **Step 7: Commit**

```bash
git add src/alpaca_bot/web/templates/dashboard.html tests/unit/test_web_app.py
git commit -m "feat: display strategy win/loss and capital allocation in Strategies panel"
```

---

## Self-Review Checklist

**Spec coverage:**
- ✅ `OrderStore.win_loss_counts_by_strategy()` — Task 1
- ✅ `_compute_capital_pct()` — Task 2 Part A
- ✅ `DashboardSnapshot` new fields — Task 2 Part B
- ✅ `load_dashboard_snapshot()` populates new fields — Task 2 Part B
- ✅ Template displays W/L and capital % — Task 3 Part A
- ✅ Renders `—` when no history — Task 3 Part B (test_dashboard_strategy_no_history_shows_dash)
- ✅ Renders `0%` when no positions — tested implicitly (test_dashboard_strategy_win_loss_rendered has no positions → capital_pct empty → template renders `0%`)

**Type consistency:**
- `win_loss_counts_by_strategy` → `dict[str, tuple[int, int]]` — consistent throughout Tasks 1, 2, 3
- `_compute_capital_pct` → `dict[str, float]` — consistent throughout Tasks 2, 3
- `strategy_win_loss` / `strategy_capital_pct` field names — consistent in `DashboardSnapshot`, `load_dashboard_snapshot()`, and template

**No placeholders:** All steps have complete code.
