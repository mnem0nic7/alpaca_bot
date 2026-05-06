# Strategy Panel Headers & Expanded Stats Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Convert the Strategies panel from unlabelled flex-div rows to a properly headed HTML table, adding Win %, Open position count, Today P&L, and Today trade count columns — all derived from data already in the template context.

**Architecture:** Pure template change. `snapshot` (`DashboardSnapshot`) and `metrics` (`MetricsSnapshot`) are both already in scope when the Strategies panel renders. New columns use Jinja2 filters (`selectattr`, `map`, `sum`) on those existing objects. No Python, no SQL, no service layer changes.

**Tech Stack:** Jinja2 template, FastAPI TestClient, pytest

---

## Files

| Action | Path |
|---|---|
| Modify | `src/alpaca_bot/web/templates/dashboard.html` — lines 319–354 (the Strategies flex-div block) |
| Modify | `tests/unit/test_web_app.py` — append 4 new rendering tests at end of file |

---

### Task 1: Write the Four Failing Rendering Tests

**Files:**
- Modify: `tests/unit/test_web_app.py`

- [ ] **Step 1: Append the four failing tests**

At the very end of `tests/unit/test_web_app.py`, add the following four tests.

> **Context for reading the helpers:**
> - `make_settings()` is defined at line 68 — returns a `Settings` object from env var defaults.
> - `FakeConnection` / `ConnectionFactory` are at lines 44–65 — fake Postgres connection.
> - `PositionRecord` is imported from `alpaca_bot.storage`.
> - `TradingMode` is imported from `alpaca_bot.config`.
> - `list_closed_trades` must be present on any `order_store` fake because `load_metrics_snapshot` calls it unconditionally. The dict shape it returns is `{"symbol": str, "strategy_name": str, "entry_fill": float, "exit_fill": float, "qty": int, "intent_type": str | None, "entry_limit": float | None, "entry_time": datetime | None, "exit_time": datetime | None}`. PnL is computed by `_to_trade_record` as `(exit_fill - entry_fill) * qty`.

```python
def test_dashboard_strategy_table_headers_rendered() -> None:
    """Strategies panel renders column headers for Win %, Today P&L, and Today."""
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
            list_closed_trades=lambda **_kwargs: [],
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
    assert "Win %" in response.text
    assert "Today P" in response.text  # "Today P&amp;L" column header


def test_dashboard_strategy_win_pct_rendered() -> None:
    """Strategy row shows win % derived from win_loss_counts_by_strategy."""
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
            list_closed_trades=lambda **_kwargs: [],
            # 3 wins, 1 loss → 75%
            win_loss_counts_by_strategy=lambda **_kwargs: {"breakout": (3, 1)},
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
    assert "75%" in response.text  # 3 / (3 + 1) * 100 = 75%


def test_dashboard_strategy_today_pnl_rendered() -> None:
    """Strategy row shows today's realized P&L from metrics.trades_by_strategy."""
    settings = make_settings()
    connection = FakeConnection(responses=[])
    now = datetime.now(timezone.utc)

    # entry_fill=100.0, exit_fill=115.0, qty=10 → pnl = (115 - 100) * 10 = $150.00
    fake_trade = {
        "symbol": "AAPL",
        "strategy_name": "breakout",
        "entry_fill": 100.0,
        "exit_fill": 115.0,
        "qty": 10,
        "intent_type": "exit",
        "entry_limit": None,
        "entry_time": now,
        "exit_time": now,
    }

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
            list_closed_trades=lambda **_kwargs: [fake_trade],
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
    assert "$150.00" in response.text


def test_dashboard_strategy_today_count_rendered() -> None:
    """Strategy row shows today's trade count from metrics.trades_by_strategy."""
    settings = make_settings()
    connection = FakeConnection(responses=[])
    now = datetime.now(timezone.utc)

    def make_trade(exit_fill: float) -> dict:
        return {
            "symbol": "AAPL",
            "strategy_name": "breakout",
            "entry_fill": 100.0,
            "exit_fill": exit_fill,
            "qty": 1,
            "intent_type": "exit",
            "entry_limit": None,
            "entry_time": now,
            "exit_time": now,
        }

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
            # 2 trades for breakout today
            list_closed_trades=lambda **_kwargs: [make_trade(105.0), make_trade(110.0)],
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
    # Today column shows integer count; combined P&L = (105-100)*1 + (110-100)*1 = $15.00
    assert "$15.00" in response.text
```

- [ ] **Step 2: Run the four new tests to confirm they all fail**

```bash
pytest tests/unit/test_web_app.py::test_dashboard_strategy_table_headers_rendered \
       tests/unit/test_web_app.py::test_dashboard_strategy_win_pct_rendered \
       tests/unit/test_web_app.py::test_dashboard_strategy_today_pnl_rendered \
       tests/unit/test_web_app.py::test_dashboard_strategy_today_count_rendered \
       -v
```

Expected: **4 FAILED** (assertions fail because the template still uses the old flex-div layout with no Win % or Today P&L columns).

---

### Task 2: Replace the Strategies Panel with an HTML Table

**Files:**
- Modify: `src/alpaca_bot/web/templates/dashboard.html` — lines 319–354

- [ ] **Step 3: Replace the flex-div Strategies block**

In `dashboard.html`, find and replace the block from `<div class="panel" style="margin-bottom: 1rem;">` (line 319) through the closing `</div>` at line 354 (the panel div that wraps the strategy `{% for %}` loop). Replace it with:

```html
      <div class="panel" style="margin-bottom: 1rem;">
        <h2>Strategies</h2>
        <div class="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Strategy</th>
                <th>Status</th>
                <th></th>
                <th>Entries</th>
                <th></th>
                <th>W / L</th>
                <th style="text-align: right">Win %</th>
                <th style="text-align: right">Capital</th>
                <th style="text-align: right">Open</th>
                <th style="text-align: right">Today P&amp;L</th>
                <th style="text-align: right">Today</th>
              </tr>
            </thead>
            <tbody>
              {% for name, flag in snapshot.strategy_flags %}
                {%- set wl = snapshot.strategy_win_loss.get(name) %}
                {%- set cap = snapshot.strategy_capital_pct.get(name, 0.0) %}
                {%- set entries_off = snapshot.strategy_entries_disabled.get(name, false) %}
                {%- set is_enabled = (flag is none or flag.enabled) %}
                {%- set open_count = snapshot.positions | selectattr('strategy_name', 'equalto', name) | list | length %}
                {%- set strat_trades = metrics.trades_by_strategy.get(name, []) %}
                {%- set today_count = strat_trades | length %}
                {%- set today_pnl = strat_trades | map(attribute='pnl') | sum %}
                <tr>
                  <td class="mono">{{ name }}</td>
                  <td class="{{ '' if is_enabled else 'warn' }}">{{ "Enabled" if is_enabled else "Disabled" }}</td>
                  <td style="white-space: nowrap">
                    <form method="post" action="/strategies/{{ name }}/toggle">
                      <input type="hidden" name="_csrf_token" value="{{ csrf_token_for(request, 'toggle') }}">
                      <button type="submit" class="btn-neutral">{{ "Disable" if is_enabled else "Enable" }}</button>
                    </form>
                  </td>
                  <td class="{{ 'warn' if entries_off else 'muted' }}">{{ "off" if entries_off else "on" }}</td>
                  <td style="white-space: nowrap">
                    <form method="post" action="/strategies/{{ name }}/toggle-entries">
                      <input type="hidden" name="_csrf_token" value="{{ csrf_token_for(request, 'toggle') }}">
                      <button type="submit" class="btn-neutral">{{ "Enable Entries" if entries_off else "Disable Entries" }}</button>
                    </form>
                  </td>
                  <td>{% if wl %}{{ wl[0] }}W / {{ wl[1] }}L{% else %}—{% endif %}</td>
                  <td style="text-align: right">{% if wl and (wl[0] + wl[1]) > 0 %}{{ "%.0f%%" | format(wl[0] / (wl[0] + wl[1]) * 100) }}{% else %}—{% endif %}</td>
                  <td style="text-align: right">{% if cap > 0 %}{{ "%.1f" | format(cap) }}%{% else %}0%{% endif %}</td>
                  <td style="text-align: right">{{ open_count }}</td>
                  <td style="text-align: right" class="{% if today_pnl < 0 %}warn{% endif %}">{% if today_count > 0 %}{{ format_price(today_pnl) }}{% else %}—{% endif %}</td>
                  <td style="text-align: right">{% if today_count > 0 %}{{ today_count }}{% else %}—{% endif %}</td>
                </tr>
              {% endfor %}
            </tbody>
          </table>
        </div>
      </div>
```

> **Landmark for the edit:** The old block begins with `<div class="panel" style="margin-bottom: 1rem;">` immediately followed by `<h2>Strategies</h2>` and a `{% for name, flag in snapshot.strategy_flags %}` loop. The old block ends with the matching `</div>` on a line by itself at approximately line 354. Replace the entire old `<div class="panel"...>...(strategy loop)...</div>` with the new block above.

> **Key behaviours to preserve:**
> - `wl` (win/loss tuple) comes from `snapshot.strategy_win_loss.get(name)` — same as before.
> - `cap` (capital %) comes from `snapshot.strategy_capital_pct.get(name, 0.0)` — same as before.
> - `entries_off` and `is_enabled` — same logic as before.
> - The Disable / Disable Entries forms have identical `action`, `_csrf_token`, and button text logic as the old template.
> - The em dash `—` (U+2014) is used for missing data — same character as before; do not use `&mdash;`.

- [ ] **Step 4: Run the four new tests — expect all pass**

```bash
pytest tests/unit/test_web_app.py::test_dashboard_strategy_table_headers_rendered \
       tests/unit/test_web_app.py::test_dashboard_strategy_win_pct_rendered \
       tests/unit/test_web_app.py::test_dashboard_strategy_today_pnl_rendered \
       tests/unit/test_web_app.py::test_dashboard_strategy_today_count_rendered \
       -v
```

Expected: **4 PASSED**

- [ ] **Step 5: Run the three pre-existing strategy rendering tests — expect all pass**

These tests verify the old W/L and capital % columns still work after the table refactor:

```bash
pytest tests/unit/test_web_app.py::test_dashboard_strategy_win_loss_rendered \
       tests/unit/test_web_app.py::test_dashboard_strategy_no_history_shows_dash \
       tests/unit/test_web_app.py::test_dashboard_strategy_capital_pct_rendered \
       -v
```

Expected: **3 PASSED**

- [ ] **Step 6: Run the full test suite — expect no regressions**

```bash
pytest
```

Expected: all tests pass (1269+ passing, 0 failed). If any test fails, investigate before proceeding.

- [ ] **Step 7: Commit**

```bash
git add src/alpaca_bot/web/templates/dashboard.html \
        tests/unit/test_web_app.py
git commit -m "feat: convert Strategies panel to table with column headers and expanded per-strategy stats"
```
