# Strategy Allocation % Column Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an "Alloc %" column to the Strategies table on the dashboard showing each strategy's Sharpe-weighted capital allocation percentage.

**Architecture:** Two-file Python change + one template change. `_load_dashboard_data()` in `app.py` is extended to call the already-existing `load_strategy_weights()` helper, returning a triplet `(snapshot, metrics, strategy_weights)`. The `/` route passes the real weights to the template. The Strategies table gains an "Alloc %" column that uses Jinja2's `selectattr` filter to look up each strategy's weight by name — the same pattern used for the open-position count column added previously. All infrastructure (`StrategyWeightStore`, `load_strategy_weights`, `StrategyWeightRow`) already exists; no new Python modules, no migrations.

**Tech Stack:** Jinja2 template, FastAPI, pytest

---

## Files

| Action | Path |
|---|---|
| Modify | `src/alpaca_bot/web/app.py` — `_load_dashboard_data()` return type and `/` route unpacking |
| Modify | `src/alpaca_bot/web/templates/dashboard.html` — Strategies `<thead>` and `<tbody>` |
| Modify | `tests/unit/test_web_app.py` — append one new rendering test |

---

### Task 1: Write the Failing Test

**Files:**
- Modify: `tests/unit/test_web_app.py`

**Context for reading the test file:**
- `make_settings()` is at line 68.
- `FakeConnection` / `ConnectionFactory` are at lines 44–65.
- `StrategyWeight` is the storage record — import it from `alpaca_bot.storage`.
- `TradingMode` is already imported from `alpaca_bot.config`.
- `strategy_weight_store_factory` is an existing parameter of `create_app()` (see `app.py:79`). The fake returns a `SimpleNamespace` with `load_all` returning a list of `StrategyWeight` objects.
- `STRATEGY_REGISTRY` always includes `"breakout"`, so `snapshot.strategy_flags` always has a "breakout" row even in tests with empty DB responses. The test only provides a weight for "breakout"; all other strategies show `—`.

- [ ] **Step 1: Add the `StrategyWeight` import to the test file**

In `tests/unit/test_web_app.py`, the existing import block at lines 11–16 reads:

```python
from alpaca_bot.storage import (
    DailySessionState,
    OrderRecord,
    PositionRecord,
    TradingStatus,
    TradingStatusValue,
)
```

Add `StrategyWeight` to that import:

```python
from alpaca_bot.storage import (
    DailySessionState,
    OrderRecord,
    PositionRecord,
    StrategyWeight,
    TradingStatus,
    TradingStatusValue,
)
```

- [ ] **Step 2: Append the failing test at the very end of the file**

```python
def test_dashboard_strategy_alloc_pct_rendered() -> None:
    """Strategy row shows Sharpe-weighted allocation % from strategy_weights store."""
    settings = make_settings()
    connection = FakeConnection(responses=[])
    now = datetime.now(timezone.utc)

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
            list_closed_trades=lambda **_kwargs: [],
            win_loss_counts_by_strategy=lambda **_kwargs: {},
        ),
        audit_event_store_factory=lambda _connection: SimpleNamespace(
            list_recent=lambda **_kwargs: [],
            load_latest=lambda **_kwargs: None,
            list_by_event_types=lambda **_kwargs: [],
        ),
        strategy_weight_store_factory=lambda _connection: SimpleNamespace(
            load_all=lambda **_kwargs: [
                StrategyWeight(
                    strategy_name="breakout",
                    trading_mode=TradingMode.PAPER,
                    strategy_version="v1-breakout",
                    weight=0.6,
                    sharpe=1.23,
                    computed_at=now,
                )
            ],
        ),
    )

    with TestClient(app) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert "Alloc %" in response.text   # column header present
    assert "60%" in response.text       # 0.6 * 100 = 60%
```

- [ ] **Step 3: Run the test to confirm it fails**

```bash
pytest tests/unit/test_web_app.py::test_dashboard_strategy_alloc_pct_rendered -v
```

Expected: **FAILED** — `AssertionError: assert "Alloc %" in response.text` because neither the column header nor the "60%" value exist yet.

---

### Task 2: Load Weights in `_load_dashboard_data` and Pass to Template

**Files:**
- Modify: `src/alpaca_bot/web/app.py` — lines ~882–918 (`_load_dashboard_data`) and ~143 (dashboard route unpacking)

**Context:** `load_strategy_weights` is already imported at line 53 of `app.py`. `_build_store` is the helper already used for every other store. The only call site for `_load_dashboard_data` is the `/` route handler at line 143.

- [ ] **Step 4: Extend `_load_dashboard_data()` to load and return weights**

Find the function `_load_dashboard_data(app: FastAPI) -> tuple:` (around line 882). Inside the `try` block, after the `load_metrics_snapshot(...)` call (which ends around line 913), add:

```python
        strategy_weights = load_strategy_weights(
            settings=settings,
            connection=connection,
            strategy_weight_store=_build_store(app.state.strategy_weight_store_factory, connection),
        )
        return snapshot, metrics, strategy_weights
```

Remove the old `return snapshot, metrics` line that was there before.

The full updated function body should look like:

```python
def _load_dashboard_data(app: FastAPI) -> tuple:
    connection = app.state.connect_postgres(app.state.settings.database_url)
    try:
        settings = app.state.settings
        order_store = _build_store(app.state.order_store_factory, connection)
        audit_event_store = _build_store(app.state.audit_event_store_factory, connection)
        position_store = _build_store(app.state.position_store_factory, connection)
        pre_positions = position_store.list_all(
            trading_mode=settings.trading_mode,
            strategy_version=settings.strategy_version,
        )
        latest_prices = _fetch_latest_prices(
            adapter=app.state.market_data_adapter,
            positions=pre_positions,
        )
        snapshot = load_dashboard_snapshot(
            settings=settings,
            connection=connection,
            trading_status_store=_build_store(app.state.trading_status_store_factory, connection),
            daily_session_state_store=_build_store(app.state.daily_session_state_store_factory, connection),
            position_store=position_store,
            order_store=order_store,
            audit_event_store=audit_event_store,
            strategy_flag_store=_build_store(app.state.strategy_flag_store_factory, connection),
            latest_prices=latest_prices,
        )
        metrics = load_metrics_snapshot(
            settings=settings,
            connection=connection,
            order_store=order_store,
            audit_event_store=audit_event_store,
        )
        strategy_weights = load_strategy_weights(
            settings=settings,
            connection=connection,
            strategy_weight_store=_build_store(app.state.strategy_weight_store_factory, connection),
        )
        return snapshot, metrics, strategy_weights
    finally:
        close = getattr(connection, "close", None)
        if callable(close):
            close()
```

- [ ] **Step 5: Update the `/` route handler to unpack the triplet and pass real weights**

Find the dashboard route handler (around line 133). Change:

```python
        try:
            snapshot, metrics = _load_dashboard_data(app)
        except Exception:
```

to:

```python
        try:
            snapshot, metrics, strategy_weights = _load_dashboard_data(app)
        except Exception:
```

And in the `TemplateResponse` context dict (around line 163), change:

```python
                "strategy_weights": [],
```

to:

```python
                "strategy_weights": strategy_weights,
```

---

### Task 3: Add the "Alloc %" Column to the Strategies Table

**Files:**
- Modify: `src/alpaca_bot/web/templates/dashboard.html` — Strategies table (currently lines 319–375)

**Context:** The Strategies table thead currently has these headers in order:
Strategy | Status | (toggle btn) | Entries | (toggle btn) | W / L | Win % | Capital | Open | Today P&L | Today

The new column "Alloc %" goes between "Win %" and "Capital".

- [ ] **Step 6: Add the `<th>Alloc %</th>` header**

In the `<thead>` block, find:

```html
                <th style="text-align: right">Win %</th>
                <th style="text-align: right">Capital</th>
```

Replace with:

```html
                <th style="text-align: right">Win %</th>
                <th style="text-align: right">Alloc %</th>
                <th style="text-align: right">Capital</th>
```

- [ ] **Step 7: Add the per-row `sw` lookup and `<td>` cell**

In the `<tbody>` loop, find the last set-variable line before `<tr>`:

```jinja2
                {%- set today_pnl = strat_trades | map(attribute='pnl') | sum %}
```

Add the weight lookup immediately after it:

```jinja2
                {%- set today_pnl = strat_trades | map(attribute='pnl') | sum %}
                {%- set sw = strategy_weights | selectattr('strategy_name', 'equalto', name) | first | default(none) %}
```

Then find the Win % `<td>` in the `<tr>` body:

```html
                  <td style="text-align: right">{% if wl and (wl[0] + wl[1]) > 0 %}{{ "%.0f%%" | format(wl[0] / (wl[0] + wl[1]) * 100) }}{% else %}—{% endif %}</td>
                  <td style="text-align: right">{% if cap > 0 %}{{ "%.1f" | format(cap) }}%{% else %}0%{% endif %}</td>
```

Replace with:

```html
                  <td style="text-align: right">{% if wl and (wl[0] + wl[1]) > 0 %}{{ "%.0f%%" | format(wl[0] / (wl[0] + wl[1]) * 100) }}{% else %}—{% endif %}</td>
                  <td style="text-align: right">{% if sw %}{{ "%.0f%%"|format(sw.weight * 100) }}{% else %}—{% endif %}</td>
                  <td style="text-align: right">{% if cap > 0 %}{{ "%.1f" | format(cap) }}%{% else %}0%{% endif %}</td>
```

- [ ] **Step 8: Run the new test — expect PASS**

```bash
pytest tests/unit/test_web_app.py::test_dashboard_strategy_alloc_pct_rendered -v
```

Expected: **PASSED**

- [ ] **Step 9: Run the seven pre-existing strategy rendering tests — expect all pass**

```bash
pytest tests/unit/test_web_app.py::test_dashboard_strategy_win_loss_rendered \
       tests/unit/test_web_app.py::test_dashboard_strategy_no_history_shows_dash \
       tests/unit/test_web_app.py::test_dashboard_strategy_capital_pct_rendered \
       tests/unit/test_web_app.py::test_dashboard_strategy_table_headers_rendered \
       tests/unit/test_web_app.py::test_dashboard_strategy_win_pct_rendered \
       tests/unit/test_web_app.py::test_dashboard_strategy_today_pnl_rendered \
       tests/unit/test_web_app.py::test_dashboard_strategy_today_count_rendered \
       -v
```

Expected: **7 PASSED** — existing tests use `FakeConnection(responses=[])`, which causes `StrategyWeightStore.load_all()` to return `[]`, so `strategy_weights=[]` and the Alloc % column shows `—` in all rows. No existing assertion breaks.

- [ ] **Step 10: Run the full test suite — expect no regressions**

```bash
pytest
```

Expected: all tests pass (1274+ passing, 0 failed).

- [ ] **Step 11: Commit**

```bash
git add src/alpaca_bot/web/app.py \
        src/alpaca_bot/web/templates/dashboard.html \
        tests/unit/test_web_app.py
git commit -m "feat: add Alloc % column to Strategies table from Sharpe-weighted store"
```
