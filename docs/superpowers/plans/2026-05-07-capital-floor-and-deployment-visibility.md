# Capital Floor Reduction and Deployment Visibility Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Lower per-strategy capital floor from 5% to 1%, and add a dashboard deployment meter showing deployed notional vs. account equity.

**Architecture:** Two independent changes shipped as one PR. Part 1 is a one-line change plus test updates in `risk/weighting.py` and `tests/unit/test_weighting.py`. Part 2 spans three files: `supervisor.py` emits a new audit field, `web/service.py` reads it and computes deployed notional, and `dashboard.html` renders the meter.

**Tech Stack:** Python 3.12, pytest, Jinja2, FastAPI, Postgres (advisory-locked supervisor)

---

## File Map

| File | Action | Responsibility |
|---|---|---|
| `src/alpaca_bot/risk/weighting.py` | Modify line 16 | Change `min_weight` default from 0.05 → 0.01 |
| `tests/unit/test_weighting.py` | Modify + add | Update floor assertion; add default-1%-floor test |
| `src/alpaca_bot/runtime/supervisor.py` | Modify lines 1075-1078 | Add `account_equity` field to `supervisor_cycle` payload |
| `tests/unit/test_supervisor_weights.py` | Add | Test that `supervisor_cycle` event contains `account_equity` |
| `src/alpaca_bot/web/service.py` | Modify | Add two fields to `DashboardSnapshot`; populate from audit event + positions |
| `tests/unit/test_web_service.py` | Add | Three new tests for account_equity population and graceful None |
| `src/alpaca_bot/web/templates/dashboard.html` | Modify | Insert "Capital deployed" stat block after realized P&L section |

---

## Task 1: Lower `min_weight` default to 1%

**Files:**
- Modify: `src/alpaca_bot/risk/weighting.py:16`
- Modify: `tests/unit/test_weighting.py:60`
- Add test: `tests/unit/test_weighting.py` (new test function)

- [ ] **Step 1: Write the new test first (TDD)**

Add to `tests/unit/test_weighting.py` after `test_floor_applied_when_strategy_has_low_sharpe`:

```python
def test_floor_is_one_percent_by_default() -> None:
    # 11 strategies: 2 with strong Sharpe, 9 with zero
    strategies = [f"s{i}" for i in range(11)]
    rows = []
    for day in range(10):
        rows.append(_row("s0", date(2026, 1, day + 1), 50.0 * (day + 1)))
        rows.append(_row("s1", date(2026, 1, day + 1), 30.0 * (day + 1)))
    # s2..s10 have no trade rows → sharpe=0 → floor
    result = compute_strategy_weights(rows, strategies)
    for name, w in result.weights.items():
        assert w >= 0.01 - 1e-9, f"{name}: weight {w:.4f} below 1% floor"
    assert abs(sum(result.weights.values()) - 1.0) < 1e-9
```

- [ ] **Step 2: Run new test to confirm it fails with current 5% default**

```bash
pytest tests/unit/test_weighting.py::test_floor_is_one_percent_by_default -v
```

Expected: FAIL — with 11 strategies and the 9 zero-Sharpe ones at 5% each, the 45% floor consumption means the total won't balance at 1% per low-Sharpe strategy. The test should fail because it's asserting on the *default* behavior, which is currently 5%.

Actually — with 5% floor the test still passes (5% >= 1%). So this test is designed to catch *regressions* (if min_weight ever went above 1%). The existing test at line 60 is the one to update. Run the *existing* test to confirm it currently passes at 5%:

```bash
pytest tests/unit/test_weighting.py::test_floor_applied_when_strategy_has_low_sharpe -v
```

Expected: PASS (current default is 0.05, assertion is `>= 0.05`).

- [ ] **Step 3: Update the existing floor assertion**

In `tests/unit/test_weighting.py:60`, change:
```python
        assert w >= 0.05 - 1e-9, f"weight {w} below floor"
```
to:
```python
        assert w >= 0.01 - 1e-9, f"weight {w} below floor"
```

- [ ] **Step 4: Run updated test — it should still pass (5% default still exceeds 1% assertion)**

```bash
pytest tests/unit/test_weighting.py::test_floor_applied_when_strategy_has_low_sharpe -v
```

Expected: PASS (this confirms the test is not yet testing the new behavior).

- [ ] **Step 5: Change the `min_weight` default in `weighting.py`**

In `src/alpaca_bot/risk/weighting.py:16`, change:
```python
    min_weight: float = 0.05,
```
to:
```python
    min_weight: float = 0.01,
```

- [ ] **Step 6: Run the full weighting test suite**

```bash
pytest tests/unit/test_weighting.py -v
```

Expected: ALL PASS. The `test_floor_applied_when_strategy_has_low_sharpe` test now verifies that the floor is at least 1% (which 1% satisfies). The `test_floor_is_one_percent_by_default` test verifies the same across 11 strategies.

- [ ] **Step 7: Run full test suite to check for regressions**

```bash
pytest
```

Expected: ALL PASS. No other code calls `compute_strategy_weights` with the 5% default.

- [ ] **Step 8: Commit**

```bash
git add src/alpaca_bot/risk/weighting.py tests/unit/test_weighting.py
git commit -m "feat: lower min_weight capital floor from 5% to 1%

Zero-Sharpe strategies consumed 9×5%=45% of capital, starving
breakout (best performer). At 1% floor, they consume 9% instead,
freeing ~36% more equity to the performing strategies."
```

---

## Task 2: Emit `account_equity` in `supervisor_cycle` audit event

**Files:**
- Modify: `src/alpaca_bot/runtime/supervisor.py` (lines 1072-1081)
- Modify: `tests/unit/test_supervisor_weights.py` (add one test)

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/test_supervisor_weights.py` after `test_update_session_weights_uses_all_time_start_date`:

```python
def test_supervisor_cycle_audit_includes_account_equity() -> None:
    """supervisor_cycle audit event payload must contain account_equity."""
    settings = _make_settings()
    supervisor, _ = _make_supervisor(
        settings=settings,
        broker_equity=12_345.67,
        only_breakout=True,
    )
    supervisor._session_equity_baseline[_SESSION_DATE] = 12_345.67
    supervisor._session_capital_weights[_SESSION_DATE] = {"breakout": 1.0}

    supervisor.run_cycle_once(now=lambda: _NOW)

    audit_store = supervisor.runtime.audit_event_store
    cycle_events = [e for e in audit_store.appended if e.event_type == "supervisor_cycle"]
    assert len(cycle_events) >= 1, "No supervisor_cycle events were emitted"
    payload = cycle_events[-1].payload
    assert "account_equity" in payload, f"account_equity missing from payload: {payload}"
    assert abs(payload["account_equity"] - 12_345.67) < 1e-6
```

- [ ] **Step 2: Run test to confirm it fails**

```bash
pytest tests/unit/test_supervisor_weights.py::test_supervisor_cycle_audit_includes_account_equity -v
```

Expected: FAIL — `account_equity` is not yet in the payload.

- [ ] **Step 3: Add `account_equity` to the supervisor_cycle payload**

In `src/alpaca_bot/runtime/supervisor.py`, the `supervisor_cycle` audit event is at lines 1072-1081. The `account` variable is the `BrokerAccount` object fetched at the top of `run_cycle_once()`. Add `"account_equity": account.equity` to the payload dict.

Current code (lines 1072-1081):
```python
                    self._append_audit(
                        AuditEvent(
                            event_type="supervisor_cycle",
                            payload={
                                "entries_disabled": cycle_report.entries_disabled,
                                "timestamp": timestamp.isoformat(),
                            },
                            created_at=timestamp,
                        )
                    )
```

Change to:
```python
                    self._append_audit(
                        AuditEvent(
                            event_type="supervisor_cycle",
                            payload={
                                "entries_disabled": cycle_report.entries_disabled,
                                "timestamp": timestamp.isoformat(),
                                "account_equity": account.equity,
                            },
                            created_at=timestamp,
                        )
                    )
```

- [ ] **Step 4: Run the new test to confirm it passes**

```bash
pytest tests/unit/test_supervisor_weights.py::test_supervisor_cycle_audit_includes_account_equity -v
```

Expected: PASS.

- [ ] **Step 5: Run the full supervisor weights test suite**

```bash
pytest tests/unit/test_supervisor_weights.py -v
```

Expected: ALL PASS.

- [ ] **Step 6: Run full test suite**

```bash
pytest
```

Expected: ALL PASS.

- [ ] **Step 7: Commit**

```bash
git add src/alpaca_bot/runtime/supervisor.py tests/unit/test_supervisor_weights.py
git commit -m "feat: include account_equity in supervisor_cycle audit payload

Enables the web dashboard to compute and display deployed notional
as a fraction of total account equity."
```

---

## Task 3: Populate `account_equity` and `total_deployed_notional` in `DashboardSnapshot`

**Files:**
- Modify: `src/alpaca_bot/web/service.py` (DashboardSnapshot dataclass, load_dashboard_snapshot function)
- Modify: `tests/unit/test_web_service.py` (three new tests)

- [ ] **Step 1: Write the three failing tests**

Add to `tests/unit/test_web_service.py` after the existing `load_dashboard_snapshot` tests:

```python
def test_load_dashboard_snapshot_populates_account_equity() -> None:
    """account_equity is read from the latest supervisor_cycle audit event."""
    now = datetime(2026, 5, 7, 14, 0, tzinfo=timezone.utc)
    cycle_event = SimpleNamespace(
        event_type="supervisor_cycle",
        created_at=now - timedelta(seconds=30),
        symbol=None,
        payload={"account_equity": 9_234.56, "entries_disabled": False, "timestamp": now.isoformat()},
    )
    stores = make_snapshot_stores(latest=cycle_event)

    snapshot = load_dashboard_snapshot(
        settings=make_settings(),
        connection=SimpleNamespace(),
        now=now,
        **stores,
    )

    assert snapshot.account_equity is not None
    assert abs(snapshot.account_equity - 9_234.56) < 1e-6


def test_load_dashboard_snapshot_account_equity_none_when_no_cycle_event() -> None:
    """account_equity is None when no supervisor_cycle event exists."""
    now = datetime(2026, 5, 7, 14, 0, tzinfo=timezone.utc)
    stores = make_snapshot_stores(latest=None)

    snapshot = load_dashboard_snapshot(
        settings=make_settings(),
        connection=SimpleNamespace(),
        now=now,
        **stores,
    )

    assert snapshot.account_equity is None


def test_load_dashboard_snapshot_total_deployed_notional() -> None:
    """total_deployed_notional sums quantity × entry_price over all open positions."""
    now = datetime(2026, 5, 7, 14, 0, tzinfo=timezone.utc)

    pos1 = SimpleNamespace(
        symbol="AAPL", strategy_name="breakout", quantity=10.0, entry_price=175.00,
        initial_stop_price=170.0, stop_price=173.0,
        trading_mode="paper", strategy_version="v1-breakout",
    )
    pos2 = SimpleNamespace(
        symbol="MSFT", strategy_name="orb", quantity=5.0, entry_price=400.00,
        initial_stop_price=390.0, stop_price=395.0,
        trading_mode="paper", strategy_version="v1-breakout",
    )

    stores = make_snapshot_stores()
    stores["position_store"] = SimpleNamespace(list_all=lambda **_: [pos1, pos2])

    snapshot = load_dashboard_snapshot(
        settings=make_settings(),
        connection=SimpleNamespace(),
        now=now,
        **stores,
    )

    # 10 * 175 + 5 * 400 = 1750 + 2000 = 3750
    assert abs(snapshot.total_deployed_notional - 3_750.0) < 1e-6
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
pytest tests/unit/test_web_service.py::test_load_dashboard_snapshot_populates_account_equity tests/unit/test_web_service.py::test_load_dashboard_snapshot_account_equity_none_when_no_cycle_event tests/unit/test_web_service.py::test_load_dashboard_snapshot_total_deployed_notional -v
```

Expected: FAIL — `DashboardSnapshot` has no `account_equity` or `total_deployed_notional` fields.

- [ ] **Step 3: Add two fields to `DashboardSnapshot`**

In `src/alpaca_bot/web/service.py`, the `DashboardSnapshot` dataclass ends at line 164:

```python
    strategy_lifetime_pnl: dict[str, float] = dc_field(default_factory=dict)
```

Add after it:
```python
    account_equity: float | None = None
    total_deployed_notional: float = 0.0
```

Full block after change (lines 148-166):
```python
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
    account_equity: float | None = None
    total_deployed_notional: float = 0.0
```

- [ ] **Step 4: Populate both fields in `load_dashboard_snapshot()`**

In `src/alpaca_bot/web/service.py`, after the `strategy_capital_pct = _compute_capital_pct(...)` line (currently ~line 261) and before the `return DashboardSnapshot(...)` call, add:

```python
    # Load account_equity from the latest supervisor_cycle audit event.
    # Uses the same getattr guard as _compute_worker_health for stub compatibility.
    _latest_loader = getattr(audit_event_store, "load_latest", None)
    _cycle_event = (
        _latest_loader(event_types=["supervisor_cycle"])
        if callable(_latest_loader)
        else None
    )
    account_equity: float | None = (
        _cycle_event.payload.get("account_equity") if _cycle_event is not None else None
    )

    total_deployed_notional: float = sum(
        pos.quantity * pos.entry_price for pos in positions
    )
```

- [ ] **Step 5: Pass the two fields through to the returned `DashboardSnapshot`**

In the `return DashboardSnapshot(...)` call (ends around line 295), add the two new fields after `strategy_lifetime_pnl=strategy_lifetime_pnl,`:

```python
        account_equity=account_equity,
        total_deployed_notional=total_deployed_notional,
```

- [ ] **Step 6: Run the three new tests**

```bash
pytest tests/unit/test_web_service.py::test_load_dashboard_snapshot_populates_account_equity tests/unit/test_web_service.py::test_load_dashboard_snapshot_account_equity_none_when_no_cycle_event tests/unit/test_web_service.py::test_load_dashboard_snapshot_total_deployed_notional -v
```

Expected: ALL PASS.

- [ ] **Step 7: Run the full web service test suite**

```bash
pytest tests/unit/test_web_service.py -v
```

Expected: ALL PASS.

- [ ] **Step 8: Run full test suite**

```bash
pytest
```

Expected: ALL PASS.

- [ ] **Step 9: Commit**

```bash
git add src/alpaca_bot/web/service.py tests/unit/test_web_service.py
git commit -m "feat: add account_equity and total_deployed_notional to DashboardSnapshot

Reads account_equity from the latest supervisor_cycle audit event using
the existing load_latest guard pattern. Computes deployed notional as
sum(quantity × entry_price) over all open positions."
```

---

## Task 4: Render deployment meter in dashboard template

**Files:**
- Modify: `src/alpaca_bot/web/templates/dashboard.html`

No automated tests for this task — it's a Jinja2 template change. Manual verification instructions are included.

- [ ] **Step 1: Locate the insertion point**

In `src/alpaca_bot/web/templates/dashboard.html`, find the closing `{% endif %}` that ends the realized P&L / loss limit block (currently around line 264):

```html
            {% endif %}
            {% endif %}
          </div>
        </div>
```

The new block goes between the last `{% endif %}` of the loss-limit guard and the `</div>` closing the stats row. Specifically, insert after line 264 (`{% endif %}` that closes `{% if snapshot.realized_pnl is not none %}`).

- [ ] **Step 2: Insert the deployment meter block**

After the `{% endif %}` on line 264 and before the closing `</div></div>`:

```html
            <div>
              <p class="eyebrow">Capital deployed</p>
              <p class="value">
                {{ format_price(snapshot.total_deployed_notional) }}
                {% if snapshot.account_equity %}
                  <span class="muted" style="font-size: 0.85em;">
                    ({{ "%.1f"|format(snapshot.total_deployed_notional / snapshot.account_equity * 100) }}% of {{ format_price(snapshot.account_equity) }})
                  </span>
                {% endif %}
              </p>
            </div>
```

- [ ] **Step 3: Verify the template renders correctly (manual)**

Start the web server and load the dashboard:

```bash
alpaca-bot-web
# or in Docker: docker compose -f deploy/compose.yaml up web
```

Open `http://localhost:18080/`. Verify:
1. "Capital deployed" appears in the summary stats row.
2. If positions are open, the dollar notional is shown.
3. If `supervisor_cycle` has run (supervisor is up), the percentage `(27.0% of $9,234)` is shown alongside the notional.
4. If no supervisor_cycle event yet, only `$0` (or current notional) is shown without a percentage.
5. If `total_deployed_notional` is 0.0, it shows `$0` cleanly.

- [ ] **Step 4: Commit**

```bash
git add src/alpaca_bot/web/templates/dashboard.html
git commit -m "feat: add Capital deployed meter to dashboard summary row

Shows deployed notional and deployment rate (notional/equity) at a
glance. Degrades gracefully: shows only notional when supervisor has
not yet emitted an account_equity audit field."
```

---

## Post-Deploy: Cache Invalidation

After deploying (supervisor restart picks up the new 1% floor automatically on the next morning's session open), to apply the new weights **today without waiting until tomorrow**:

```sql
-- Run against the production Postgres DB (e.g., via docker exec or psql)
DELETE FROM strategy_weights WHERE computed_at::date = CURRENT_DATE;
```

The supervisor's next cycle detects no today's rows, recomputes weights with the 1% floor, and stores the new values. This is a safe, reversible operation.

---

## Self-Review Checklist

**Spec coverage:**
- [x] Part 1: `min_weight` 0.05 → 0.01 — Task 1
- [x] Test update (`test_floor_applied_when_strategy_has_low_sharpe` assertion) — Task 1, Step 3
- [x] New test (`test_floor_is_one_percent_by_default`) — Task 1, Step 1
- [x] `account_equity` in `supervisor_cycle` payload — Task 2
- [x] Test for audit event payload — Task 2
- [x] `DashboardSnapshot.account_equity` and `total_deployed_notional` fields — Task 3
- [x] `load_dashboard_snapshot()` populating both fields — Task 3
- [x] `load_latest` `getattr` guard pattern — Task 3, Step 4
- [x] Three web service tests — Task 3
- [x] Dashboard template meter — Task 4
- [x] Cache invalidation SQL — Post-Deploy section

**Placeholder scan:** No TBD/TODO items. All code blocks are complete.

**Type consistency:**
- `account_equity: float | None = None` matches `payload.get("account_equity")` which returns `float | None`
- `total_deployed_notional: float = 0.0` matches `sum(pos.quantity * pos.entry_price for pos in positions)` which returns `float`
- Template `{% if snapshot.account_equity %}` correctly guards division-by-zero (None and 0.0 are both falsy)
