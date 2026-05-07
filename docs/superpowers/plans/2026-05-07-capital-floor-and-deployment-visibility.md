# Capital Floor Reduction and Deployment Visibility Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Lower per-strategy capital floor from 5% to 1%, and add a dashboard deployment meter showing deployed notional vs. account equity.

**Architecture:** Two independent changes shipped as one PR. Part 1 is a one-line change plus test updates in `risk/weighting.py`. Part 2 requires `SupervisorCycleReport` to carry `account_equity` (so `run_forever` can include it in the audit event), then `web/service.py` reads it and computes deployed notional, and `dashboard.html` renders the meter.

**Tech Stack:** Python 3.12, pytest, Jinja2, FastAPI, Postgres (advisory-locked supervisor)

---

## File Map

| File | Action | Responsibility |
|---|---|---|
| `src/alpaca_bot/risk/weighting.py` | Modify line 16 | Change `min_weight` default from 0.05 → 0.01 |
| `tests/unit/test_weighting.py` | Modify + add | Update floor assertion; add default-1%-floor test |
| `src/alpaca_bot/runtime/supervisor.py` | Modify multiple | Add `account_equity` to `SupervisorCycleReport`; update all 3 return sites; thread it through to `supervisor_cycle` audit payload |
| `tests/unit/test_supervisor_weights.py` | Add | Test that `run_cycle_once()` returns `SupervisorCycleReport` with `account_equity` |
| `src/alpaca_bot/web/service.py` | Modify | Add two fields to `DashboardSnapshot`; populate from audit event + positions |
| `tests/unit/test_web_service.py` | Add | Three new tests for account_equity population and graceful None |
| `src/alpaca_bot/web/templates/dashboard.html` | Modify | Insert "Capital deployed" stat block after realized P&L section |

---

## Task 1: Lower `min_weight` default to 1%

**Files:**
- Modify: `src/alpaca_bot/risk/weighting.py:16`
- Modify: `tests/unit/test_weighting.py:60`
- Add test: `tests/unit/test_weighting.py`

**Grilling note:** Classic TDD red-green requires a test that fails under the OLD behavior and passes under the NEW. A test asserting `w >= 0.01` will pass under either 5% or 1% floor (5% satisfies >= 1%). The correct failing test must assert the new behavior is STRICTLY below the old floor — e.g., that floor strategies stay below 2%, which is impossible at 5% but is satisfied at 1%.

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/test_weighting.py` after `test_floor_applied_when_strategy_has_low_sharpe` (around line 62):

```python
def test_floor_strategies_capped_at_one_percent_not_five() -> None:
    # With 1 strong strategy and 10 zero-sharpe strategies, the floor strategies
    # should each get exactly min_weight=0.01 (1%), NOT 0.05 (5%).
    # With 5% floor: 10 * 5% = 50% consumed, strong strategy gets ~33%.
    # With 1% floor: 10 * 1% = 10% consumed, strong strategy gets ~54%.
    # We assert floor strategies are < 2% — impossible at 5% floor, correct at 1%.
    strategies = ["strong"] + [f"zero_{i}" for i in range(10)]
    rows = []
    for day in range(20):
        rows.append(_row("strong", date(2026, 1, day + 1), 100.0 * (day + 1)))
    # zero_0..zero_9 have no trade rows → sharpe=0 → hit the floor
    result = compute_strategy_weights(rows, strategies)
    for name in [f"zero_{i}" for i in range(10)]:
        assert result.weights[name] < 0.02, (
            f"{name}: weight {result.weights[name]:.4f} is at 5% floor — "
            "expected 1% floor after this change"
        )
    assert abs(sum(result.weights.values()) - 1.0) < 1e-9
```

- [ ] **Step 2: Run the test to confirm it fails**

```bash
pytest tests/unit/test_weighting.py::test_floor_strategies_capped_at_one_percent_not_five -v
```

Expected: FAIL — each zero strategy gets ~5% weight (which is >= 0.02), so the `< 0.02` assertion fires.

- [ ] **Step 3: Change the `min_weight` default**

In `src/alpaca_bot/risk/weighting.py:16`, change:
```python
    min_weight: float = 0.05,
```
to:
```python
    min_weight: float = 0.01,
```

- [ ] **Step 4: Run the new test — it should pass now**

```bash
pytest tests/unit/test_weighting.py::test_floor_strategies_capped_at_one_percent_not_five -v
```

Expected: PASS — each zero strategy now gets ~1% weight.

- [ ] **Step 5: Update the existing floor assertion**

In `tests/unit/test_weighting.py:60`, change:
```python
        assert w >= 0.05 - 1e-9, f"weight {w} below floor"
```
to:
```python
        assert w >= 0.01 - 1e-9, f"weight {w} below floor"
```

- [ ] **Step 6: Run the full weighting test suite**

```bash
pytest tests/unit/test_weighting.py -v
```

Expected: ALL PASS.

- [ ] **Step 7: Run full test suite**

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
- Modify: `src/alpaca_bot/runtime/supervisor.py` (3 locations)
- Add test: `tests/unit/test_supervisor_weights.py`

**Architecture note (grilling fix):** The `supervisor_cycle` audit event is emitted in `run_forever()`, NOT in `run_cycle_once()`. The `account` object only exists inside `run_cycle_once()`. The solution is to add `account_equity: float` to `SupervisorCycleReport` so `run_forever()` can include it in the audit payload. There are three `return SupervisorCycleReport(...)` call sites — all must be updated.

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/test_supervisor_weights.py` after `test_update_session_weights_uses_all_time_start_date`:

```python
def test_run_cycle_once_report_includes_account_equity() -> None:
    """run_cycle_once() returns SupervisorCycleReport with account_equity set."""
    settings = _make_settings()
    supervisor, _ = _make_supervisor(
        settings=settings,
        broker_equity=12_345.67,
        only_breakout=True,
    )
    supervisor._session_equity_baseline[_SESSION_DATE] = 12_345.67
    supervisor._session_capital_weights[_SESSION_DATE] = {"breakout": 1.0}

    report = supervisor.run_cycle_once(now=lambda: _NOW)

    assert hasattr(report, "account_equity"), (
        "SupervisorCycleReport missing account_equity field"
    )
    assert abs(report.account_equity - 12_345.67) < 1e-6
```

- [ ] **Step 2: Run test to confirm it fails**

```bash
pytest tests/unit/test_supervisor_weights.py::test_run_cycle_once_report_includes_account_equity -v
```

Expected: FAIL — `SupervisorCycleReport` has no `account_equity` field.

- [ ] **Step 3: Add `account_equity` to `SupervisorCycleReport`**

In `src/alpaca_bot/runtime/supervisor.py`, the dataclass is at lines 67-71:

```python
@dataclass(frozen=True)
class SupervisorCycleReport:
    entries_disabled: bool
    cycle_result: object
    dispatch_report: object
```

Change to:
```python
@dataclass(frozen=True)
class SupervisorCycleReport:
    entries_disabled: bool
    cycle_result: object
    dispatch_report: object
    account_equity: float = 0.0
```

- [ ] **Step 4: Update all three `return SupervisorCycleReport(...)` call sites**

There are three early/normal return sites in `run_cycle_once()`. All three are after `account = self.broker.get_account()` at line 305, so `account.equity` is available at all of them.

**Return site 1 — line ~522 (empty watchlist early return):**
```python
                return SupervisorCycleReport(
                    entries_disabled=entries_disabled,
                    cycle_result=_SN(intents=[]),
                    dispatch_report={"submitted_count": 0},
                    account_equity=account.equity,
                )
```

**Return site 2 — line ~809 (HALTED status early return):**
```python
            return SupervisorCycleReport(
                entries_disabled=True,
                cycle_result=cycle_result,
                dispatch_report={"submitted_count": 0},
                account_equity=account.equity,
            )
```

**Return site 3 — line ~861 (normal return):**
```python
        return SupervisorCycleReport(
            entries_disabled=entries_disabled,
            cycle_result=cycle_result,
            dispatch_report=dispatch_report,
            account_equity=account.equity,
        )
```

- [ ] **Step 5: Thread `account_equity` into the `supervisor_cycle` audit payload**

In `run_forever()`, the `supervisor_cycle` event is at lines 1072-1081:

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
                                "account_equity": cycle_report.account_equity,
                            },
                            created_at=timestamp,
                        )
                    )
```

- [ ] **Step 6: Run the new test**

```bash
pytest tests/unit/test_supervisor_weights.py::test_run_cycle_once_report_includes_account_equity -v
```

Expected: PASS.

- [ ] **Step 7: Run full supervisor test suite**

```bash
pytest tests/unit/test_supervisor_weights.py -v
```

Expected: ALL PASS.

- [ ] **Step 8: Run full test suite**

```bash
pytest
```

Expected: ALL PASS.

- [ ] **Step 9: Commit**

```bash
git add src/alpaca_bot/runtime/supervisor.py tests/unit/test_supervisor_weights.py
git commit -m "feat: include account_equity in supervisor_cycle audit payload

Adds account_equity to SupervisorCycleReport (returned by run_cycle_once)
so run_forever can include it in the supervisor_cycle audit event.
Enables the web dashboard to compute and display deployed capital rate."
```

---

## Task 3: Populate `account_equity` and `total_deployed_notional` in `DashboardSnapshot`

**Files:**
- Modify: `src/alpaca_bot/web/service.py` (DashboardSnapshot dataclass + load_dashboard_snapshot)
- Add tests: `tests/unit/test_web_service.py`

**Architecture note:** `load_latest` in the `make_audit_store` stub accepts `**_` and ignores `event_types`, so the new `getattr` guard pattern works transparently with existing test fixtures. `load_latest(event_types=["supervisor_cycle"])` will correctly return whatever `latest` was passed to `make_snapshot_stores(latest=...)`.

- [ ] **Step 1: Write the three failing tests**

Add to `tests/unit/test_web_service.py` after the existing `load_dashboard_snapshot` test block:

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

    snapshot = load_dashboard_snapshot(
        settings=make_settings(),
        connection=SimpleNamespace(),
        now=now,
        **make_snapshot_stores(latest=cycle_event),
    )

    assert snapshot.account_equity is not None
    assert abs(snapshot.account_equity - 9_234.56) < 1e-6


def test_load_dashboard_snapshot_account_equity_none_when_no_cycle_event() -> None:
    """account_equity is None when no supervisor_cycle event exists."""
    now = datetime(2026, 5, 7, 14, 0, tzinfo=timezone.utc)

    snapshot = load_dashboard_snapshot(
        settings=make_settings(),
        connection=SimpleNamespace(),
        now=now,
        **make_snapshot_stores(latest=None),
    )

    assert snapshot.account_equity is None


def test_load_dashboard_snapshot_total_deployed_notional() -> None:
    """total_deployed_notional sums quantity × entry_price over all open positions."""
    now = datetime(2026, 5, 7, 14, 0, tzinfo=timezone.utc)

    pos1 = SimpleNamespace(
        symbol="AAPL", strategy_name="breakout", quantity=10.0, entry_price=175.00,
        initial_stop_price=170.0, stop_price=173.0,
    )
    pos2 = SimpleNamespace(
        symbol="MSFT", strategy_name="orb", quantity=5.0, entry_price=400.00,
        initial_stop_price=390.0, stop_price=395.0,
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

In `src/alpaca_bot/web/service.py`, after line 164 (`strategy_lifetime_pnl: dict[str, float] = dc_field(default_factory=dict)`), add:

```python
    account_equity: float | None = None
    total_deployed_notional: float = 0.0
```

- [ ] **Step 4: Add computation in `load_dashboard_snapshot()`**

In `src/alpaca_bot/web/service.py`, after the `strategy_capital_pct = _compute_capital_pct(positions, latest_prices or {})` line and before the `return DashboardSnapshot(...)` call, add:

```python
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

- [ ] **Step 5: Pass both fields through to the returned `DashboardSnapshot`**

In the `return DashboardSnapshot(...)` call, add after `strategy_lifetime_pnl=strategy_lifetime_pnl,`:

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

No automated tests — this is a Jinja2 template change. Manual verification instructions are included.

**Template note:** `{% if snapshot.account_equity %}` correctly handles both `None` (no event yet) and `0.0` (both are falsy), preventing division-by-zero in the percentage calculation.

- [ ] **Step 1: Locate the insertion point**

In `src/alpaca_bot/web/templates/dashboard.html`, the realized P&L / loss limit block ends around line 264 with:

```html
            {% endif %}
            {% endif %}
          </div>
        </div>
```

The first `{% endif %}` closes `{% if snapshot.loss_limit_amount ... %}`. The second closes `{% if snapshot.realized_pnl is not none %}`. The new block goes between that second `{% endif %}` and the `</div>` closing the stats row.

- [ ] **Step 2: Insert the deployment meter block**

After the `{% endif %}` that closes `{% if snapshot.realized_pnl is not none %}` (around line 264), add:

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

- [ ] **Step 3: Verify the template renders (manual)**

Start the web server:

```bash
alpaca-bot-web
```

Open `http://localhost:18080/`. Verify:
1. "Capital deployed" appears in the summary stats row.
2. If positions are open, the dollar notional is shown (e.g., `$3,750`).
3. If the supervisor has run recently, the percentage appears (e.g., `(40.6% of $9,234)`).
4. If no supervisor has run, only the notional is shown with no percentage.
5. With no open positions: shows `$0`.

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

After deploying (supervisor restart picks up the new 1% floor automatically on the next morning's session open), to apply the new weights **today without waiting until tomorrow**, run this against the production Postgres DB:

```sql
DELETE FROM strategy_weights WHERE computed_at::date = CURRENT_DATE;
```

The supervisor's next cycle detects no today's rows and recomputes weights with the 1% floor. Safe and reversible — the supervisor recreates the rows on the next cycle.

---

## Grilling Summary (answered from codebase)

| Question | Answer |
|---|---|
| Does this affect order submission, position sizing, or stop placement? | Only indirectly: higher effective equity → proportionally larger position sizes for breakout/orb. Stop placement logic unchanged. |
| Can two concurrent cycles submit conflicting orders? | No — advisory lock unchanged. |
| Does every state change have an audit event? | `account_equity` in the audit payload is read-only for the dashboard; it produces no state changes. |
| Does `evaluate_cycle()` remain pure? | Yes — `min_weight` change is upstream of `evaluate_cycle()`; the function receives `equity` as a param. |
| Is the cache invalidation SQL reversible? | Yes — supervisor recomputes on next cycle. |
| Paper vs. live mode? | Identical — `BrokerAccount.equity` already works for both modes. |
| New env vars? | None. |
| Market-hours guards affected? | No — audit payload addition and weight floor change do not touch market-hours logic. |
| `account` variable available at all 3 `SupervisorCycleReport` return sites? | Yes — all 3 are after `account = self.broker.get_account()` at line 305. |
| `load_latest` stub accepts `event_types` kwarg? | Yes — stub uses `lambda **_: latest`, so the call `load_latest(event_types=["supervisor_cycle"])` works correctly. |
