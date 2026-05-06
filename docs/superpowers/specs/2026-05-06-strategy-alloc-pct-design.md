# Strategy Allocation % Column Design

**Goal:** Show each strategy's current Sharpe-weighted capital allocation percentage inline in the Strategies table on the dashboard, so operators can see budget and performance data in one place.

**Architecture:** Two-part change. (1) `_load_dashboard_data()` in `app.py` is extended to call the already-existing `load_strategy_weights()` helper and return the result alongside `snapshot` and `metrics`. (2) The Strategies table in `dashboard.html` gains an "Alloc %" column that does a per-row lookup against the loaded weights list using Jinja2's `selectattr` filter — the same pattern already used for open-position counting.

**Tech Stack:** Python (FastAPI), Jinja2, pytest

---

## Background

`StrategyWeightStore`, `load_strategy_weights()`, and `StrategyWeightRow` are fully implemented in `web/service.py`. The supervisor computes Sharpe-proportional weights each morning via `compute_strategy_weights()` in `risk/weighting.py` and persists them to the `strategy_weights` Postgres table via `StrategyWeightStore.upsert_many()`.

The `/metrics` route already loads and passes weights to the template. The `/` (dashboard) route hardcodes `strategy_weights: []`, leaving the Strategies table without weight data.

---

## Data Flow

```
strategy_weights Postgres table
        │
        ▼
StrategyWeightStore.load_all(trading_mode, strategy_version)
        │
        ▼
load_strategy_weights() → list[StrategyWeightRow]
        │
        ▼
_load_dashboard_data() → (snapshot, metrics, strategy_weights)
        │
        ▼
dashboard route → template context["strategy_weights"]
        │
        ▼
Strategies table: selectattr lookup per strategy name → "Alloc %" cell
```

---

## Files Changed

| Action | Path |
|---|---|
| Modify | `src/alpaca_bot/web/app.py` — `_load_dashboard_data()` and `/` route handler |
| Modify | `src/alpaca_bot/web/templates/dashboard.html` — Strategies `<thead>` and `<tbody>` |
| Modify | `tests/unit/test_web_app.py` — one new rendering test |

---

## `app.py` Changes

### `_load_dashboard_data()` (line ~882)

Add a `load_strategy_weights()` call inside the existing `try` block, after `load_metrics_snapshot()`. Return `(snapshot, metrics, strategy_weights)` instead of `(snapshot, metrics)`.

```python
strategy_weights = load_strategy_weights(
    settings=settings,
    connection=connection,
    strategy_weight_store=_build_store(app.state.strategy_weight_store_factory, connection),
)
return snapshot, metrics, strategy_weights
```

### `/` route handler (line ~143)

Change:
```python
snapshot, metrics = _load_dashboard_data(app)
```
to:
```python
snapshot, metrics, strategy_weights = _load_dashboard_data(app)
```

And in the `TemplateResponse` context, change:
```python
"strategy_weights": [],
```
to:
```python
"strategy_weights": strategy_weights,
```

---

## Template Changes (`dashboard.html` — Strategies table)

### `<thead>`

Add after the existing `<th style="text-align: right">Win %</th>`:

```html
<th style="text-align: right">Alloc %</th>
```

### `<tbody>` per row (inside `{% for name, flag ... %}`)

Add after the existing `{%- set today_pnl ... %}` set-block:

```jinja2
{%- set sw = strategy_weights | selectattr('strategy_name', 'equalto', name) | first | default(none) %}
```

Add after the Win % `<td>`:

```html
<td style="text-align: right">{% if sw %}{{ "%.0f%%"|format(sw.weight * 100) }}{% else %}—{% endif %}</td>
```

---

## Rendering Rules

- **When weight exists:** `"%.0f%%"|format(sw.weight * 100)` — e.g., `0.6` → `"60%"`.
- **When no weight:** `—` (U+2014 em dash, consistent with other missing-data cells).
- **New deployments / fewer than 5 trades:** Weights default to equal split (computed by `compute_strategy_weights` with Sharpe=0 fallback), so "—" only appears if the supervisor has never run weight computation yet.

---

## Testing

One new rendering test in `tests/unit/test_web_app.py`:

**`test_dashboard_strategy_alloc_pct_rendered`** — passes a `strategy_weight_store_factory` whose fake returns a `StrategyWeight` with `weight=0.6` for "breakout". Asserts `"60%"` in `response.text`.

The fake `strategy_weight_store_factory` returns a `SimpleNamespace` with `load_all=lambda **_kwargs: [StrategyWeight(...)]`.

### Existing test compatibility

All existing tests pass `FakeConnection(responses=[])`. `StrategyWeightStore.load_all()` calls `fetch_all()` → `cursor.fetchall()` → `[]`. So `load_strategy_weights()` returns `[]`, the template renders `—` in the Alloc % column, and no existing assertion breaks.

---

## What Is Not Changed

- `load_strategy_weights()` in `web/service.py` — no change.
- `StrategyWeightStore` — no change.
- The `/metrics` route — no change.
- The separate "Capital Allocation" panel in the template — no change. It already renders on `/metrics`; once the dashboard route passes real weights it will also render on `/`. This is acceptable: the panel also shows Sharpe ratios, which are not in the Strategies table.
- No new env vars, no migrations, no Python or SQL beyond the two lines added to `_load_dashboard_data`.
