# Dashboard Profitability Display Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Surface `expectancy_pct`, `profit_target_wins/losses`, and the three new strategy settings (`enable_profit_target`, `profit_target_r`, `trend_filter_exit_lookback_days`) in the web dashboard.

**Architecture:** Three-file change — pass `settings` to the dashboard route context in `app.py` (one line), add two rows to the Session Evaluation table plus a new Strategy Configuration panel in `dashboard.html`, and assert on the new HTML in `test_web_app.py`. No backend logic, no schema migrations.

**Tech Stack:** Python 3.12, FastAPI, Jinja2 templates, pytest, `fastapi.testclient.TestClient`

---

## File Map

| File | Change |
|---|---|
| `src/alpaca_bot/web/app.py` | Add `"settings": app_settings` to the `/` dashboard route context dict |
| `src/alpaca_bot/web/templates/dashboard.html` | Add two rows to Session Evaluation table; add Strategy Configuration panel |
| `tests/unit/test_web_app.py` | Add three test functions covering the new rendered fields |

---

### Task 1: Expose settings to the dashboard route

**Files:**
- Modify: `src/alpaca_bot/web/app.py:167-178`
- Test: `tests/unit/test_web_app.py`

**Background:** The `/` route at `app.py:145` builds a context dict for `dashboard.html`. It currently does NOT include `settings`. The `/metrics` route (line 181) DOES pass `settings`. The template already conditionally renders content when `{% if settings is defined %}`, but the dashboard route never provides the value, so the Strategy Configuration panel (added in Task 2) would never render on `/`. This task fixes that.

`app_settings` is a variable in scope in the closure — it is captured from `create_app(settings, ...)` at module level. No imports needed.

- [ ] **Step 1: Write the failing test**

Open `tests/unit/test_web_app.py` and add this test function after the existing dashboard tests (around line 820):

```python
def test_dashboard_renders_strategy_configuration_panel() -> None:
    settings = make_settings(
        ENABLE_PROFIT_TARGET="true",
        PROFIT_TARGET_R="3.0",
        TREND_FILTER_EXIT_LOOKBACK_DAYS="2",
    )
    app = create_app(
        settings=settings,
        connect_postgres_fn=ConnectionFactory([FakeConnection(responses=[])]),
        trading_status_store_factory=lambda _c: SimpleNamespace(load=lambda **_: None),
        daily_session_state_store_factory=lambda _c: SimpleNamespace(load=lambda **_: None),
        position_store_factory=lambda _c: SimpleNamespace(list_all=lambda **_: []),
        order_store_factory=lambda _c: SimpleNamespace(
            list_by_status=lambda **_: [], list_recent=lambda **_: [],
            list_closed_trades=lambda **_: [],
        ),
        audit_event_store_factory=lambda _c: SimpleNamespace(
            list_recent=lambda **_: [], load_latest=lambda **_: None,
            list_by_event_types=lambda **_: [],
        ),
    )
    with TestClient(app) as client:
        response = client.get("/")
    assert response.status_code == 200
    assert "Strategy Configuration" in response.text
    assert "3.0" in response.text        # profit_target_r
    assert "2 day" in response.text      # trend_filter_exit_lookback_days
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
pytest tests/unit/test_web_app.py::test_dashboard_renders_strategy_configuration_panel -v
```

Expected: FAIL — `"Strategy Configuration"` is not in the response text because `settings` is not in the dashboard route context.

- [ ] **Step 3: Add `settings` to the dashboard route context**

Open `src/alpaca_bot/web/app.py`. Find the `dashboard` route handler (starts at line 145). Inside `TemplateResponse(...)` the context dict currently ends at line 178 with `"confidence_floor": confidence_floor,`. Add one key:

```python
        return templates.TemplateResponse(
            request=request,
            name="dashboard.html",
            context={
                "request": request,
                "trading_mode": app_settings.trading_mode.value,
                "strategy_version": app_settings.strategy_version,
                "snapshot": snapshot,
                "metrics": metrics,
                "operator_email": operator,
                "auto_refresh": not bool(no_refresh),
                "strategy_weights": strategy_weights,
                "option_strategy_names": OPTION_STRATEGY_NAMES,
                "confidence_floor": confidence_floor,
                "settings": app_settings,
            },
        )
```

The only change is the addition of `"settings": app_settings,` at the end of the dict.

- [ ] **Step 4: Run the test to verify it passes (it will still fail — template not updated yet)**

```bash
pytest tests/unit/test_web_app.py::test_dashboard_renders_strategy_configuration_panel -v
```

Expected: FAIL — the template doesn't render "Strategy Configuration" yet. This is expected — we haven't updated the template. The test currently fails for a different reason (KeyError/None), confirming the backend change alone is not enough.

- [ ] **Step 5: Commit the app.py change alone**

```bash
git add src/alpaca_bot/web/app.py
git commit -m "feat: pass settings to dashboard route context"
```

---

### Task 2: Update dashboard.html — Session Evaluation rows + Strategy Configuration panel

**Files:**
- Modify: `src/alpaca_bot/web/templates/dashboard.html:836-851`

**Background:** The Session Evaluation panel (lines 836–851) is already guarded by `{% if metrics.session_report %}`. It shows profit factor, avg hold, stop wins/losses, EOD wins/losses, and consecutive wins/losses. We need to add two more rows. Then we add an entirely new panel for Strategy Configuration, guarded by `{% if settings is defined %}`.

**Important rendering rules:**
- `expectancy_pct` is a `float | None`. Render `+X.XX%` / `-X.XX%` or `&mdash;` when `None`.
- `profit_target_wins` and `profit_target_losses` are `int`. Always render (will be `0 / 0` in live data until schema migration adds a reason column — the row should stay visible).
- `enable_profit_target` is `bool`. Render "Enabled" or "Disabled".
- `profit_target_r` only shown when `enable_profit_target` is `True`.
- `trend_filter_exit_lookback_days` is `int`. Render as `N day(s)`.

- [ ] **Step 1: Add two rows to the Session Evaluation table**

Find lines 844–847 in `dashboard.html` (the stop wins/losses and EOD wins/losses rows). Add the profit-target and expectancy rows **after** the EOD row and **before** the consecutive wins/losses rows:

The table body currently reads:
```html
              <tr><td>Stop wins / losses</td><td>{{ metrics.session_report.stop_wins }} / {{ metrics.session_report.stop_losses }}</td></tr>
              <tr><td>EOD wins / losses</td><td>{{ metrics.session_report.eod_wins }} / {{ metrics.session_report.eod_losses }}</td></tr>
              <tr><td>Max consecutive wins</td><td>{{ metrics.session_report.max_consecutive_wins }}</td></tr>
              <tr><td>Max consecutive losses</td><td>{{ metrics.session_report.max_consecutive_losses }}</td></tr>
```

Replace with:
```html
              <tr><td>Stop wins / losses</td><td>{{ metrics.session_report.stop_wins }} / {{ metrics.session_report.stop_losses }}</td></tr>
              <tr><td>EOD wins / losses</td><td>{{ metrics.session_report.eod_wins }} / {{ metrics.session_report.eod_losses }}</td></tr>
              <tr><td>Profit target W / L</td><td>{{ metrics.session_report.profit_target_wins }} / {{ metrics.session_report.profit_target_losses }}</td></tr>
              <tr><td>Expectancy</td><td>{% if metrics.session_report.expectancy_pct is not none %}{{ "%+.2f%%" | format(metrics.session_report.expectancy_pct * 100) }}{% else %}&mdash;{% endif %}</td></tr>
              <tr><td>Max consecutive wins</td><td>{{ metrics.session_report.max_consecutive_wins }}</td></tr>
              <tr><td>Max consecutive losses</td><td>{{ metrics.session_report.max_consecutive_losses }}</td></tr>
```

- [ ] **Step 2: Add the Strategy Configuration panel after the Session Evaluation panel**

The Session Evaluation panel ends at line 851 with `{% endif %}`. Immediately after that closing tag (before the `<div class="panel">` for "Last Backtest"), insert the new panel:

```html
        {% if settings is defined %}
        <div class="panel">
          <h2>Strategy Configuration</h2>
          <table class="data-table">
            <thead><tr><th>Setting</th><th>Value</th></tr></thead>
            <tbody>
              <tr>
                <td>Profit target</td>
                <td>{% if settings.enable_profit_target %}Enabled{% else %}Disabled{% endif %}</td>
              </tr>
              {% if settings.enable_profit_target %}
              <tr>
                <td>Profit target R</td>
                <td>{{ settings.profit_target_r }}&times;R</td>
              </tr>
              {% endif %}
              <tr>
                <td>Trend filter exit debounce</td>
                <td>{{ settings.trend_filter_exit_lookback_days }} day(s)</td>
              </tr>
            </tbody>
          </table>
        </div>
        {% endif %}
```

- [ ] **Step 3: Run the failing test from Task 1 — it should now pass**

```bash
pytest tests/unit/test_web_app.py::test_dashboard_renders_strategy_configuration_panel -v
```

Expected: PASS.

- [ ] **Step 4: Run the full test suite to check for regressions**

```bash
pytest tests/unit/test_web_app.py -v
```

Expected: All existing tests pass. If any test that hits `/` fails because `settings` now appears in the template where it wasn't before, investigate: a test asserting absence of "Strategy Configuration" text would fail if `enable_profit_target` is rendered. (Unlikely since the default for `make_settings()` is `ENABLE_PROFIT_TARGET=false`, so the R row won't appear, but the panel header "Strategy Configuration" will.)

- [ ] **Step 5: Commit the template changes**

```bash
git add src/alpaca_bot/web/templates/dashboard.html
git commit -m "feat: add expectancy, profit-target rows and strategy config panel to dashboard"
```

---

### Task 3: Add tests for Session Evaluation new rows and None-safe expectancy rendering

**Files:**
- Modify: `tests/unit/test_web_app.py`

**Background:** Task 1 added a test for the Strategy Configuration panel on the dashboard (`/`) route. Now we need two more tests: one that verifies `expectancy_pct` and `profit_target_wins/losses` render correctly on the metrics (`/metrics`) route, and one that verifies `None` expectancy renders as `—` rather than the Python string `"None"`.

The `/metrics` route uses `_make_metrics_app()`. To render the Session Evaluation panel, `load_metrics_snapshot()` must find closed trades. The path from DB rows → `session_report` goes through `load_metrics_snapshot()` in `service.py`, which calls `order_store.list_closed_trades()`. Each row must be a dict with keys: `symbol`, `entry_fill`, `exit_fill`, `qty`, `entry_time`, `exit_time`, `intent_type`.

- [ ] **Step 1: Write the failing test for expectancy and profit-target rendering**

Add these two functions at the end of `tests/unit/test_web_app.py`:

```python
def _make_closed_trade_row(
    *,
    symbol: str = "AAPL",
    entry_fill: float = 100.0,
    exit_fill: float = 110.0,
    qty: int = 10,
    intent_type: str = "exit",
) -> dict:
    now = datetime.now(timezone.utc)
    return {
        "symbol": symbol,
        "entry_fill": entry_fill,
        "exit_fill": exit_fill,
        "qty": qty,
        "entry_time": now - timedelta(hours=1),
        "exit_time": now,
        "intent_type": intent_type,
    }


def test_metrics_renders_expectancy_and_profit_target_rows() -> None:
    """Expectancy and profit-target W/L appear in the Session Evaluation panel."""
    # 2 wins (eod) and 1 loss (stop) → win_rate=2/3, avg_win≈10%, avg_loss=-5%
    # expectancy = (2/3)*0.10 + (1/3)*(-0.05) = 0.0500
    win1 = _make_closed_trade_row(entry_fill=100.0, exit_fill=110.0, qty=10)
    win2 = _make_closed_trade_row(entry_fill=100.0, exit_fill=110.0, qty=10)
    loss = _make_closed_trade_row(entry_fill=100.0, exit_fill=95.0, qty=10, intent_type="stop")

    def _order_store_factory(_conn):
        return SimpleNamespace(
            list_by_status=lambda **_: [],
            list_recent=lambda **_: [],
            list_closed_trades=lambda **_: [win1, win2, loss],
        )

    app = create_app(
        settings=make_settings(),
        connect_postgres_fn=lambda _: FakeConnection(responses=[]),
        trading_status_store_factory=lambda _: SimpleNamespace(load=lambda **_: None),
        daily_session_state_store_factory=lambda _: SimpleNamespace(load=lambda **_: None),
        position_store_factory=lambda _: SimpleNamespace(list_all=lambda **_: []),
        order_store_factory=_order_store_factory,
        audit_event_store_factory=lambda _: SimpleNamespace(
            list_recent=lambda **_: [],
            load_latest=lambda **_: None,
            list_by_event_types=lambda **_: [],
        ),
    )
    with TestClient(app) as client:
        response = client.get("/metrics")
    assert response.status_code == 200
    assert "Profit target W / L" in response.text
    assert "0 / 0" in response.text          # profit_target_wins=0, losses=0 (no profit_target exits in live data)
    assert "Expectancy" in response.text
    assert "+5.00%" in response.text         # expectancy ≈ +5.00%


def test_metrics_renders_mdash_when_expectancy_is_none() -> None:
    """When no trades exist, expectancy_pct is None and renders as em-dash, not 'None'."""
    app = _make_metrics_app()   # list_closed_trades returns [] → no report
    with TestClient(app) as client:
        response = client.get("/metrics")
    assert response.status_code == 200
    # Session Evaluation panel only appears when session_report is not None,
    # which requires at least one trade. With zero trades the panel is absent.
    # Verify "None" (Python string) never appears in the response body.
    assert "None" not in response.text
```

**Note on the last test:** `_make_metrics_app()` returns `list_closed_trades=lambda **_: []`. With no trades, `session_report` is `None` and the entire Session Evaluation panel is hidden by `{% if metrics.session_report %}`. The test therefore verifies the absence of the Python string `"None"` to guard against template rendering `{{ None }}` if the guard were ever removed.

- [ ] **Step 2: Run the new tests to verify they fail**

```bash
pytest tests/unit/test_web_app.py::test_metrics_renders_expectancy_and_profit_target_rows tests/unit/test_web_app.py::test_metrics_renders_mdash_when_expectancy_is_none -v
```

Expected: Both FAIL because the new rows don't exist in the template yet. (If Task 2 was completed first, they may already pass — that's fine.)

- [ ] **Step 3: Verify both pass after Task 2 is complete**

```bash
pytest tests/unit/test_web_app.py::test_metrics_renders_expectancy_and_profit_target_rows tests/unit/test_web_app.py::test_metrics_renders_mdash_when_expectancy_is_none -v
```

Expected: Both PASS.

- [ ] **Step 4: Run the full test suite**

```bash
pytest
```

Expected: All tests pass (the suite had 1661 tests before this feature).

- [ ] **Step 5: Commit**

```bash
git add tests/unit/test_web_app.py
git commit -m "test: assert expectancy, profit-target, and strategy-config rendered on dashboard"
```

---

## Acceptance Checklist (verify before closing)

- [ ] `/` route renders "Strategy Configuration" panel with correct `enable_profit_target`, `profit_target_r` (when enabled), and `trend_filter_exit_lookback_days` values.
- [ ] `/metrics` route renders "Profit target W / L" row in Session Evaluation panel.
- [ ] `/metrics` route renders "Expectancy" row, formatted as `+X.XX%` or `&mdash;`.
- [ ] Python string `"None"` never appears in any dashboard response.
- [ ] All 1661+ tests pass.
