# Lifetime P&L Display and All-Time Capital Allocation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `OrderStore.lifetime_pnl_by_strategy()`, surface it as a "Total P&L" column in the strategies dashboard table, and extend the supervisor's Sharpe window from 28-day rolling to all-time so long-term performance directly informs capital weights.

**Architecture:** Four surgical changes — one new repository method, one new `DashboardSnapshot` field populated the same way as `strategy_win_loss`, one new template column, and a one-line `start_date` change in the supervisor. No migrations, no new endpoints, no new settings.

**Tech Stack:** Python, pytest, Jinja2, psycopg2.

---

## Files

| File | Change |
|---|---|
| `src/alpaca_bot/storage/repositories.py` | Add `OrderStore.lifetime_pnl_by_strategy()` after `win_loss_counts_by_strategy` |
| `tests/unit/test_storage_db.py` | Add `TestLifetimePnlByStrategy` class |
| `src/alpaca_bot/web/service.py` | Add `strategy_lifetime_pnl` field to `DashboardSnapshot`; populate in `load_dashboard_snapshot()` |
| `tests/unit/test_web_service.py` | Update `make_snapshot_stores`; add two snapshot tests |
| `src/alpaca_bot/web/templates/dashboard.html` | Add Total P&L `<th>` and `<td>` to strategy rows |
| `src/alpaca_bot/runtime/supervisor.py` | Change `start_date` from rolling 28-day to `date(2000, 1, 1)` |
| `tests/unit/test_supervisor_weights.py` | Add test asserting `start_date == date(2000, 1, 1)` |

---

## Task 1: Add `lifetime_pnl_by_strategy()` to `OrderStore`

**Files:**
- Modify: `src/alpaca_bot/storage/repositories.py` (after `win_loss_counts_by_strategy` at line ~812)
- Modify: `tests/unit/test_storage_db.py` (append new class at end of file)

**Background:** `win_loss_counts_by_strategy` uses a CTE that joins exit orders with their most recent correlated entry order and computes `(exit_fill - entry_fill) * qty`. The new method reuses the same CTE but replaces `COUNT(*) FILTER` with `SUM(pnl)`. Row format returned by the query: `(strategy_name, total_pnl)`.

The test pattern: `OrderStore(_make_fake_connection(rows))` where `rows` is a list of tuples matching the query's column order. The fake connection's `fetchall()` returns those rows verbatim — so we test the Python deserialization, not the SQL.

- [ ] **Step 1: Write the failing tests**

Append to the end of `tests/unit/test_storage_db.py`:

```python


# ── test_lifetime_pnl_by_strategy ─────────────────────────────────────────────

class TestLifetimePnlByStrategy:
    """Unit tests for OrderStore.lifetime_pnl_by_strategy()."""

    def _make_store(self, rows: list[tuple]) -> "OrderStore":
        return OrderStore(_make_fake_connection(rows))

    def test_returns_empty_dict_when_no_closed_trades(self) -> None:
        store = self._make_store([])
        result = store.lifetime_pnl_by_strategy(
            trading_mode=TradingMode.PAPER,
            strategy_version="v1",
        )
        assert result == {}

    def test_single_strategy_positive_pnl(self) -> None:
        # row: (strategy_name, total_pnl)
        rows = [("breakout", 1234.56)]
        store = self._make_store(rows)
        result = store.lifetime_pnl_by_strategy(
            trading_mode=TradingMode.PAPER,
            strategy_version="v1",
        )
        assert result == {"breakout": pytest.approx(1234.56)}

    def test_single_strategy_negative_pnl(self) -> None:
        rows = [("breakout", -500.0)]
        store = self._make_store(rows)
        result = store.lifetime_pnl_by_strategy(
            trading_mode=TradingMode.PAPER,
            strategy_version="v1",
        )
        assert result == {"breakout": pytest.approx(-500.0)}

    def test_multiple_strategies_returned(self) -> None:
        rows = [
            ("breakout", 1234.56),
            ("momentum", -200.0),
        ]
        store = self._make_store(rows)
        result = store.lifetime_pnl_by_strategy(
            trading_mode=TradingMode.PAPER,
            strategy_version="v1",
        )
        assert result == {
            "breakout": pytest.approx(1234.56),
            "momentum": pytest.approx(-200.0),
        }

    def test_returns_float_values(self) -> None:
        """Values must be Python float, not Decimal or int."""
        rows = [("breakout", 100)]  # simulate DB returning int-like Decimal
        store = self._make_store(rows)
        result = store.lifetime_pnl_by_strategy(
            trading_mode=TradingMode.PAPER,
            strategy_version="v1",
        )
        assert isinstance(result["breakout"], float)
```

- [ ] **Step 2: Run the tests to confirm red**

```bash
pytest tests/unit/test_storage_db.py::TestLifetimePnlByStrategy -v
```

Expected: **FAILED** — `AttributeError: 'OrderStore' object has no attribute 'lifetime_pnl_by_strategy'`.

- [ ] **Step 3: Implement the method**

In `src/alpaca_bot/storage/repositories.py`, add the following method immediately after `win_loss_counts_by_strategy` (after line 812, before the `class DailySessionStateStore:` line):

```python
    def lifetime_pnl_by_strategy(
        self,
        *,
        trading_mode: TradingMode,
        strategy_version: str,
    ) -> dict[str, float]:
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
                   SUM(pnl) AS total_pnl
              FROM trade_pnl
             GROUP BY strategy_name
            """,
            (trading_mode.value, strategy_version),
        )
        return {row[0]: float(row[1]) for row in rows}
```

- [ ] **Step 4: Run the tests to confirm green**

```bash
pytest tests/unit/test_storage_db.py::TestLifetimePnlByStrategy -v
```

Expected: all 5 tests **PASS**.

- [ ] **Step 5: Run the full storage test file**

```bash
pytest tests/unit/test_storage_db.py -v
```

Expected: all tests **PASS**.

- [ ] **Step 6: Commit**

```bash
git add src/alpaca_bot/storage/repositories.py tests/unit/test_storage_db.py
git commit -m "$(cat <<'EOF'
feat: add OrderStore.lifetime_pnl_by_strategy() for all-time P&L aggregation

Uses the same correlated-subquery CTE as win_loss_counts_by_strategy but
sums (exit_fill - entry_fill) * qty grouped by strategy_name. No date filter
— covers all closed trades in the database.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Add `strategy_lifetime_pnl` to `DashboardSnapshot` and populate it

**Files:**
- Modify: `src/alpaca_bot/web/service.py` (lines 148–286)
- Modify: `tests/unit/test_web_service.py` (update `make_snapshot_stores`; append two tests)

**Background:** `DashboardSnapshot` is a frozen dataclass. New fields with `dc_field(default_factory=dict)` are added after existing optional fields. The `load_dashboard_snapshot` function uses `hasattr` guards for methods that may not exist on all `order_store` stubs — follow the same pattern here. The `make_snapshot_stores` helper in the test file builds the default fake stores; it needs `lifetime_pnl_by_strategy=lambda **_: {}` added to its `order_store`.

- [ ] **Step 1: Write the failing tests**

In `tests/unit/test_web_service.py`, locate `make_snapshot_stores` (around line 68) and update the `order_store` SimpleNamespace to add `lifetime_pnl_by_strategy`:

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
            lifetime_pnl_by_strategy=lambda **_: {},
        ),
        audit_event_store=make_audit_store(events=events, latest=latest),
        strategy_flag_store=SimpleNamespace(list_all=lambda **_: []),
    )
```

Then append two new tests at the end of `tests/unit/test_web_service.py`:

```python


def test_load_dashboard_snapshot_populates_strategy_lifetime_pnl() -> None:
    fixed_now = datetime(2026, 5, 7, 14, 0, tzinfo=timezone.utc)
    lifetime_data = {"breakout": 1234.56, "momentum": -200.0}

    snapshot = load_dashboard_snapshot(
        settings=make_settings(),
        connection=SimpleNamespace(),
        now=fixed_now,
        **{
            **make_snapshot_stores(),
            "order_store": SimpleNamespace(
                list_by_status=lambda **_: [],
                list_recent=lambda **_: [],
                win_loss_counts_by_strategy=lambda **_: {},
                lifetime_pnl_by_strategy=lambda **_: lifetime_data,
            ),
        },
    )

    assert snapshot.strategy_lifetime_pnl == {"breakout": pytest.approx(1234.56), "momentum": pytest.approx(-200.0)}


def test_load_dashboard_snapshot_strategy_lifetime_pnl_empty_when_no_closed_trades() -> None:
    """strategy_lifetime_pnl is {} when lifetime_pnl_by_strategy returns {}."""
    fixed_now = datetime(2026, 5, 7, 14, 0, tzinfo=timezone.utc)

    snapshot = load_dashboard_snapshot(
        settings=make_settings(),
        connection=SimpleNamespace(),
        now=fixed_now,
        **make_snapshot_stores(),
    )

    assert snapshot.strategy_lifetime_pnl == {}
```

- [ ] **Step 2: Run the tests to confirm red**

```bash
pytest tests/unit/test_web_service.py::test_load_dashboard_snapshot_populates_strategy_lifetime_pnl tests/unit/test_web_service.py::test_load_dashboard_snapshot_strategy_lifetime_pnl_empty_when_no_closed_trades -v
```

Expected: **FAILED** — `DashboardSnapshot` has no `strategy_lifetime_pnl` attribute.

- [ ] **Step 3: Add the field to `DashboardSnapshot`**

In `src/alpaca_bot/web/service.py`, locate the `DashboardSnapshot` dataclass (line ~148). Add the new field after `strategy_capital_pct`:

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
    strategy_lifetime_pnl: dict[str, float] = dc_field(default_factory=dict)
```

- [ ] **Step 4: Populate the field in `load_dashboard_snapshot`**

In `src/alpaca_bot/web/service.py`, locate the block that populates `strategy_win_loss` (lines ~246–252). Add the equivalent block immediately after it (before `strategy_capital_pct = ...`):

```python
    if hasattr(order_store, "win_loss_counts_by_strategy"):
        strategy_win_loss: dict[str, tuple[int, int]] = order_store.win_loss_counts_by_strategy(
            trading_mode=settings.trading_mode,
            strategy_version=settings.strategy_version,
        )
    else:
        strategy_win_loss = {}
    if hasattr(order_store, "lifetime_pnl_by_strategy"):
        strategy_lifetime_pnl: dict[str, float] = order_store.lifetime_pnl_by_strategy(
            trading_mode=settings.trading_mode,
            strategy_version=settings.strategy_version,
        )
    else:
        strategy_lifetime_pnl = {}
    strategy_capital_pct = _compute_capital_pct(positions, latest_prices or {})
```

- [ ] **Step 5: Pass the field in the `DashboardSnapshot` constructor**

In `src/alpaca_bot/web/service.py`, locate the `return DashboardSnapshot(...)` call (line ~255). Add `strategy_lifetime_pnl=strategy_lifetime_pnl,` after `strategy_win_loss=strategy_win_loss,`:

```python
    return DashboardSnapshot(
        ...
        strategy_win_loss=strategy_win_loss,
        strategy_capital_pct=strategy_capital_pct,
        strategy_lifetime_pnl=strategy_lifetime_pnl,
    )
```

- [ ] **Step 6: Run the new tests to confirm green**

```bash
pytest tests/unit/test_web_service.py::test_load_dashboard_snapshot_populates_strategy_lifetime_pnl tests/unit/test_web_service.py::test_load_dashboard_snapshot_strategy_lifetime_pnl_empty_when_no_closed_trades -v
```

Expected: both **PASS**.

- [ ] **Step 7: Run the full web service test file**

```bash
pytest tests/unit/test_web_service.py -v
```

Expected: all tests **PASS**.

- [ ] **Step 8: Commit**

```bash
git add src/alpaca_bot/web/service.py tests/unit/test_web_service.py
git commit -m "$(cat <<'EOF'
feat: add strategy_lifetime_pnl to DashboardSnapshot

Populated from OrderStore.lifetime_pnl_by_strategy() using the same
hasattr guard pattern as strategy_win_loss. Defaults to {} when the
method is absent (backwards-compatible with any stub that lacks it).

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Add Total P&L column to dashboard template

**Files:**
- Modify: `src/alpaca_bot/web/templates/dashboard.html`

**Background:** The strategies table headers live at lines ~325–336. Each data row is in a `{% for name, flag in snapshot.strategy_flags %}` loop. Variables are set via `{%- set ... %}` at the top of the loop body. `format_price(value)` returns `$value:,.2f`. Positive values use `+` prefix and `style="color: var(--accent)"` (green); negatives use `-` prefix with `0 - value` and `style="color: #c0392b"` (red), matching the unrealized P&L column pattern.

- [ ] **Step 1: Add the `<th>` header**

In `src/alpaca_bot/web/templates/dashboard.html`, locate the table headers section:

```html
                <th style="text-align: right">Win %</th>
                <th style="text-align: right">Alloc %</th>
```

Replace with:

```html
                <th style="text-align: right">Win %</th>
                <th style="text-align: right">Total P&amp;L</th>
                <th style="text-align: right">Alloc %</th>
```

- [ ] **Step 2: Add the `<td>` data cell**

In the loop body, locate the `{%- set sw = ... %}` line (the last `{%- set %}` before `<tr>`). Add a new set variable immediately after it:

```html
                {%- set sw = strategy_weights | selectattr('strategy_name', 'equalto', name) | first | default(none) %}
                {%- set ltpnl = snapshot.strategy_lifetime_pnl.get(name) %}
                <tr>
```

Then locate the Win % `<td>` and the Alloc % `<td>`:

```html
                  <td style="text-align: right">{% if wl and (wl[0] + wl[1]) > 0 %}{{ "%.0f%%" | format(wl[0] / (wl[0] + wl[1]) * 100) }}{% else %}—{% endif %}</td>
                  <td style="text-align: right">{% if sw %}{{ "%.0f%%"|format(sw.weight * 100) }}{% else %}—{% endif %}</td>
```

Replace with:

```html
                  <td style="text-align: right">{% if wl and (wl[0] + wl[1]) > 0 %}{{ "%.0f%%" | format(wl[0] / (wl[0] + wl[1]) * 100) }}{% else %}—{% endif %}</td>
                  <td style="text-align: right; color: {{ 'var(--accent)' if ltpnl is not none and ltpnl >= 0 else ('#c0392b' if ltpnl is not none and ltpnl < 0 else 'inherit') }}">
                    {%- if ltpnl is not none -%}
                      {%- if ltpnl >= 0 %}+{{ format_price(ltpnl) }}{% else %}-{{ format_price(0 - ltpnl) }}{% endif %}
                    {%- else %}—{% endif %}
                  </td>
                  <td style="text-align: right">{% if sw %}{{ "%.0f%%"|format(sw.weight * 100) }}{% else %}—{% endif %}</td>
```

- [ ] **Step 3: Run the full test suite**

```bash
pytest -x
```

Expected: all tests **PASS** (template changes are not unit-tested directly, but the snapshot field added in Task 2 powers this column).

- [ ] **Step 4: Commit**

```bash
git add src/alpaca_bot/web/templates/dashboard.html
git commit -m "$(cat <<'EOF'
feat: add Total P&L column to strategies dashboard table

Shows lifetime realized P&L per strategy from DashboardSnapshot.strategy_lifetime_pnl.
Positive values render green with + prefix; negatives render red with - prefix;
strategies with no closed trades show an em dash.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Extend supervisor Sharpe window to all-time

**Files:**
- Modify: `src/alpaca_bot/runtime/supervisor.py` (line 1249)
- Modify: `tests/unit/test_supervisor_weights.py` (append new test)

**Background:** `_update_session_weights` calls `list_trade_pnl_by_strategy(start_date=..., end_date=...)`. Currently `start_date = end_date - timedelta(days=28)`. Change to `date(2000, 1, 1)` to make Sharpe cover all trade history. The test subclasses `_RecordingOrderStore` to capture kwargs, then asserts `start_date == date(2000, 1, 1)`.

- [ ] **Step 1: Write the failing test**

Append to the end of `tests/unit/test_supervisor_weights.py`:

```python


def test_update_session_weights_uses_all_time_start_date() -> None:
    """Weight computation must use start_date=date(2000,1,1) for all-time Sharpe.

    Before the fix, start_date = end_date - timedelta(days=28) — only 28
    calendar days of trades feed into the Sharpe computation, so long-term
    strategy performance has no influence on capital allocation.
    """
    captured_kwargs: list[dict] = []

    class _CapturingOrderStore(_RecordingOrderStore):
        def list_trade_pnl_by_strategy(self, **kwargs):
            captured_kwargs.append(dict(kwargs))
            return []

    order_store = _CapturingOrderStore()
    weight_store = _FakeWeightStore(preloaded=[])

    settings = _make_settings()
    supervisor, _ = _make_supervisor(
        settings=settings,
        weight_store=weight_store,
        order_store=order_store,
        only_breakout=True,
    )

    supervisor._update_session_weights(_SESSION_DATE)

    assert len(captured_kwargs) == 1, "list_trade_pnl_by_strategy must be called exactly once"
    assert captured_kwargs[0]["start_date"] == date(2000, 1, 1), (
        f"Expected all-time start_date=date(2000,1,1), got {captured_kwargs[0]['start_date']}. "
        "The 28-day rolling window has not been changed to all-time."
    )
```

- [ ] **Step 2: Run the test to confirm red**

```bash
pytest tests/unit/test_supervisor_weights.py::test_update_session_weights_uses_all_time_start_date -v
```

Expected: **FAILED** — assertion error showing `start_date` is `date(2026, 4, 30)` (yesterday minus 28 days) instead of `date(2000, 1, 1)`.

- [ ] **Step 3: Change `start_date` in supervisor.py**

In `src/alpaca_bot/runtime/supervisor.py`, locate line 1249:

```python
        start_date = end_date - timedelta(days=28)  # 28 calendar days ≈ 20 trading days
```

Replace with:

```python
        start_date = date(2000, 1, 1)
```

Verify that `date` is already imported at the top of the file (it is — the file imports `from datetime import date, datetime, timedelta, timezone`). The `timedelta` import is still used elsewhere so do not remove it.

- [ ] **Step 4: Run the test to confirm green**

```bash
pytest tests/unit/test_supervisor_weights.py::test_update_session_weights_uses_all_time_start_date -v
```

Expected: **PASSED**.

- [ ] **Step 5: Run the full supervisor weights test file**

```bash
pytest tests/unit/test_supervisor_weights.py -v
```

Expected: all tests **PASS**.

- [ ] **Step 6: Run the full test suite**

```bash
pytest -x
```

Expected: all tests **PASS**.

- [ ] **Step 7: Commit**

```bash
git add src/alpaca_bot/runtime/supervisor.py tests/unit/test_supervisor_weights.py
git commit -m "$(cat <<'EOF'
feat: extend Sharpe weight window from 28-day rolling to all-time

Changes start_date in _update_session_weights from end_date - 28 days to
date(2000, 1, 1), so the Sharpe computation covers the full trade history.
Strategies with consistent long-term gains now receive proportionally more
capital without any new weighting logic.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
EOF
)"
```

---

## Verification

After all four tasks, run the complete suite one final time:

```bash
pytest
```

Expected: all tests **PASS**.
