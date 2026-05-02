# Operator Safety Observability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Alert the operator immediately when a market exit hard-fails (position is unprotected), and show session P&L versus daily loss limit on the dashboard so operators can make halt/close-only decisions without querying Postgres.

**Architecture:** Fix 1 adds a `notifier` parameter to `_execute_exit` and calls it on both hard-failure return paths (stop-cancel unrecognized error and exit-submission failure), following the identical pattern already used by `_execute_update_stop`. Fix 2 extracts `session_state` from the inline `DashboardSnapshot(...)` constructor call, uses it to conditionally call `order_store.daily_realized_pnl()`, and adds two new optional fields (`realized_pnl`, `loss_limit_amount`) to the snapshot dataclass; the template renders them inside the existing `status-grid` div.

**Tech Stack:** Python 3.12, pytest, Jinja2 (dashboard template), psycopg2 (DB via repositories).

---

## File Map

| File | Change |
|---|---|
| `src/alpaca_bot/runtime/cycle_intent_execution.py` | Add `notifier` param to `_execute_exit`; call `notifier.send()` on both hard-fail paths; pass `notifier=notifier` at call site |
| `src/alpaca_bot/web/service.py` | Add `realized_pnl` and `loss_limit_amount` fields to `DashboardSnapshot`; extract and compute in `load_dashboard_snapshot` |
| `src/alpaca_bot/web/templates/dashboard.html` | Add session P&L + loss-limit cells to the `status-grid` div |
| `tests/unit/test_cycle_intent_execution.py` | 3 new tests: exit-fail notifies, cancel-fail notifies, `notifier=None` no crash |
| `tests/unit/test_web_service.py` | 2 new tests: fields populated when equity_baseline set; fields None when no session |

---

## Task 1: Test and implement exit hard-failure notifier

**Files:**
- Modify: `tests/unit/test_cycle_intent_execution.py` (append 3 tests at the end)
- Modify: `src/alpaca_bot/runtime/cycle_intent_execution.py:386-399,509-549,633-708,165-177`

### Step 1: Write the three failing tests

Open `tests/unit/test_cycle_intent_execution.py` and append the following three tests at the very end of the file:

```python
# ---------------------------------------------------------------------------
# Exit hard-failure notifier
# ---------------------------------------------------------------------------

def test_exit_submission_failure_fires_notifier() -> None:
    """When submit_market_exit raises after the stop is already canceled,
    notifier.send() must be called exactly once with the symbol in the subject."""
    from alpaca_bot.runtime.cycle_intent_execution import execute_cycle_intents

    settings = make_settings()
    now = datetime(2026, 5, 2, 19, 0, tzinfo=timezone.utc)

    active_stop = OrderRecord(
        client_order_id="v1-breakout:2026-05-02:AAPL:stop:1",
        symbol="AAPL",
        side="sell",
        intent_type="stop",
        status="accepted",
        quantity=25,
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        created_at=now,
        updated_at=now,
        stop_price=109.89,
        initial_stop_price=109.89,
        broker_order_id="broker-stop-notify",
        signal_timestamp=now,
    )
    position = PositionRecord(
        symbol="AAPL",
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        quantity=25,
        entry_price=111.02,
        stop_price=109.89,
        initial_stop_price=109.89,
        opened_at=now,
        updated_at=now,
    )

    class ExitRaisesBroker(RecordingBroker):
        def submit_market_exit(self, **kwargs):
            raise RuntimeError("broker_connection_error")

    notifier_calls: list[dict] = []

    class RecordingNotifier:
        def send(self, *, subject: str, body: str) -> None:
            notifier_calls.append({"subject": subject, "body": body})

    runtime = SimpleNamespace(
        order_store=RecordingOrderStore(orders=[active_stop]),
        position_store=RecordingPositionStore(positions=[position]),
        audit_event_store=RecordingAuditEventStore(),
        connection=FakeConnection(),
    )

    report = execute_cycle_intents(
        settings=settings,
        runtime=runtime,
        broker=ExitRaisesBroker(),
        cycle_result=CycleResult(
            as_of=now,
            intents=[CycleIntent(
                intent_type=CycleIntentType.EXIT,
                symbol="AAPL",
                timestamp=now,
                reason="eod_flatten",
            )],
        ),
        now=now,
        notifier=RecordingNotifier(),
    )

    assert report.failed_exit_count == 1
    assert len(notifier_calls) == 1, "notifier.send must be called exactly once on exit submission failure"
    assert "AAPL" in notifier_calls[0]["subject"]
    assert "HARD FAILED" in notifier_calls[0]["subject"]


def test_stop_cancel_failure_fires_notifier() -> None:
    """When cancel_order raises an unrecognized error (cancel_hard_failed path),
    notifier.send() must be called exactly once with the symbol in the subject."""
    from alpaca_bot.runtime.cycle_intent_execution import execute_cycle_intents

    settings = make_settings()
    now = datetime(2026, 5, 2, 19, 5, tzinfo=timezone.utc)

    active_stop = OrderRecord(
        client_order_id="v1-breakout:2026-05-02:AAPL:stop:cancel-fail",
        symbol="AAPL",
        side="sell",
        intent_type="stop",
        status="accepted",
        quantity=25,
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        created_at=now,
        updated_at=now,
        stop_price=109.89,
        initial_stop_price=109.89,
        broker_order_id="broker-stop-cancel-fail",
        signal_timestamp=now,
    )
    position = PositionRecord(
        symbol="AAPL",
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        quantity=25,
        entry_price=111.02,
        stop_price=109.89,
        initial_stop_price=109.89,
        opened_at=now,
        updated_at=now,
    )

    notifier_calls: list[dict] = []

    class RecordingNotifier:
        def send(self, *, subject: str, body: str) -> None:
            notifier_calls.append({"subject": subject, "body": body})

    runtime = SimpleNamespace(
        order_store=RecordingOrderStore(orders=[active_stop]),
        position_store=RecordingPositionStore(positions=[position]),
        audit_event_store=RecordingAuditEventStore(),
        connection=FakeConnection(),
    )

    report = execute_cycle_intents(
        settings=settings,
        runtime=runtime,
        broker=RecordingBroker(cancel_raises=RuntimeError("rate_limit_exceeded_unknown")),
        cycle_result=CycleResult(
            as_of=now,
            intents=[CycleIntent(
                intent_type=CycleIntentType.EXIT,
                symbol="AAPL",
                timestamp=now,
                reason="eod_flatten",
            )],
        ),
        now=now,
        notifier=RecordingNotifier(),
    )

    assert report.failed_exit_count == 1
    assert len(notifier_calls) == 1, "notifier.send must be called exactly once on cancel hard-failure"
    assert "AAPL" in notifier_calls[0]["subject"]


def test_exit_failure_none_notifier_does_not_raise() -> None:
    """When notifier=None (the default), a hard-failed exit must not raise."""
    from alpaca_bot.runtime.cycle_intent_execution import execute_cycle_intents

    settings = make_settings()
    now = datetime(2026, 5, 2, 19, 10, tzinfo=timezone.utc)

    active_stop = OrderRecord(
        client_order_id="v1-breakout:2026-05-02:AAPL:stop:no-notifier",
        symbol="AAPL",
        side="sell",
        intent_type="stop",
        status="accepted",
        quantity=25,
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        created_at=now,
        updated_at=now,
        stop_price=109.89,
        initial_stop_price=109.89,
        broker_order_id="broker-stop-no-notifier",
        signal_timestamp=now,
    )
    position = PositionRecord(
        symbol="AAPL",
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        quantity=25,
        entry_price=111.02,
        stop_price=109.89,
        initial_stop_price=109.89,
        opened_at=now,
        updated_at=now,
    )

    class ExitRaisesBroker(RecordingBroker):
        def submit_market_exit(self, **kwargs):
            raise RuntimeError("timeout")

    runtime = SimpleNamespace(
        order_store=RecordingOrderStore(orders=[active_stop]),
        position_store=RecordingPositionStore(positions=[position]),
        audit_event_store=RecordingAuditEventStore(),
        connection=FakeConnection(),
    )

    # Must not raise even though notifier is absent
    report = execute_cycle_intents(
        settings=settings,
        runtime=runtime,
        broker=ExitRaisesBroker(),
        cycle_result=CycleResult(
            as_of=now,
            intents=[CycleIntent(
                intent_type=CycleIntentType.EXIT,
                symbol="AAPL",
                timestamp=now,
                reason="eod_flatten",
            )],
        ),
        now=now,
        # notifier omitted — tests the default None path
    )

    assert report.failed_exit_count == 1
```

- [ ] **Step 2: Run the three tests to verify they fail**

```bash
pytest tests/unit/test_cycle_intent_execution.py::test_exit_submission_failure_fires_notifier tests/unit/test_cycle_intent_execution.py::test_stop_cancel_failure_fires_notifier tests/unit/test_cycle_intent_execution.py::test_exit_failure_none_notifier_does_not_raise -v
```

Expected: all three FAIL — the first two fail because `notifier.send()` is never called (`len(notifier_calls) == 0`), the third passes already (no-notifier path doesn't crash today).

- [ ] **Step 3: Add `notifier` parameter to `_execute_exit`**

In `src/alpaca_bot/runtime/cycle_intent_execution.py`, change the `_execute_exit` function signature from:

```python
def _execute_exit(
    *,
    settings: Settings,
    runtime: RuntimeProtocol,
    broker: BrokerProtocol,
    symbol: str,
    intent_timestamp: datetime,
    reason: str | None,
    position: PositionRecord | None,
    now: datetime,
    strategy_name: str = "breakout",
    lock_ctx: Any = None,
    limit_price: float | None = None,
) -> tuple[int, int, int]:
```

to:

```python
def _execute_exit(
    *,
    settings: Settings,
    runtime: RuntimeProtocol,
    broker: BrokerProtocol,
    symbol: str,
    intent_timestamp: datetime,
    reason: str | None,
    position: PositionRecord | None,
    now: datetime,
    strategy_name: str = "breakout",
    lock_ctx: Any = None,
    limit_price: float | None = None,
    notifier: Notifier | None = None,
) -> tuple[int, int, int]:
```

- [ ] **Step 4: Add notifier call to the cancel-hard-failed return path**

In `src/alpaca_bot/runtime/cycle_intent_execution.py`, find the block that ends with `return canceled_stop_count, 0, 1  # hard_failed: stop cancel had unrecognized error` (around line 549). Add the notifier call **between** the closing `with lock_ctx:` block and the `return` statement:

Replace:
```python
        return canceled_stop_count, 0, 1  # hard_failed: stop cancel had unrecognized error
```

with:

```python
    if notifier is not None:
        try:
            notifier.send(
                subject=f"Exit HARD FAILED: {symbol}/{strategy_name} — stop state UNKNOWN, exit aborted",
                body=(
                    f"cancel_order raised an unrecognized error for {symbol} ({strategy_name}).\n"
                    f"The stop order status at the broker is unknown. Exit was aborted to prevent double-sell.\n"
                    f"Position may still be protected (stop may be live). Manual verification required.\n"
                    f"Reason: {reason}"
                ),
            )
        except Exception:
            logger.exception(
                "cycle_intent_execution: notifier failed for cancel_hard_failed on %s", symbol
            )
    return canceled_stop_count, 0, 1  # hard_failed: stop cancel had unrecognized error
```

- [ ] **Step 5: Add notifier call to the exit-submission failure return path**

In `src/alpaca_bot/runtime/cycle_intent_execution.py`, find the block that ends with `return canceled_stop_count, 0, 1  # hard_failed: exit submission raised` (around line 708). Add the notifier call between the closing `with lock_ctx:` block and the `return` statement:

Replace:
```python
        return canceled_stop_count, 0, 1  # hard_failed: exit submission raised
```

with:

```python
    if notifier is not None:
        try:
            notifier.send(
                subject=f"Exit HARD FAILED: {symbol}/{strategy_name} — position UNPROTECTED",
                body=(
                    f"Stop cancel succeeded but {exit_method} raised for {symbol} ({strategy_name}).\n"
                    f"Position is live and unprotected. A recovery stop has been queued.\n"
                    f"Manual verification required.\n"
                    f"Reason: {reason}"
                ),
            )
        except Exception:
            logger.exception(
                "cycle_intent_execution: notifier failed for exit submission failure on %s", symbol
            )
    return canceled_stop_count, 0, 1  # hard_failed: exit submission raised
```

- [ ] **Step 6: Pass `notifier=notifier` at the call site in `execute_cycle_intents`**

In `src/alpaca_bot/runtime/cycle_intent_execution.py`, find the `_execute_exit(...)` call (around line 165). It currently ends with `limit_price=getattr(intent, "limit_price", None),`. Add `notifier=notifier,` as the final keyword argument:

Replace:
```python
            canceled, submitted, hard_failed = _execute_exit(
                settings=settings,
                runtime=runtime,
                broker=broker,
                symbol=symbol,
                intent_timestamp=getattr(intent, "timestamp", timestamp),
                reason=getattr(intent, "reason", None),
                position=positions_by_symbol.get((symbol, strategy_name)),
                now=timestamp,
                strategy_name=strategy_name,
                lock_ctx=lock_ctx,
                limit_price=getattr(intent, "limit_price", None),
            )
```

with:

```python
            canceled, submitted, hard_failed = _execute_exit(
                settings=settings,
                runtime=runtime,
                broker=broker,
                symbol=symbol,
                intent_timestamp=getattr(intent, "timestamp", timestamp),
                reason=getattr(intent, "reason", None),
                position=positions_by_symbol.get((symbol, strategy_name)),
                now=timestamp,
                strategy_name=strategy_name,
                lock_ctx=lock_ctx,
                limit_price=getattr(intent, "limit_price", None),
                notifier=notifier,
            )
```

- [ ] **Step 7: Run the three new tests to verify they pass**

```bash
pytest tests/unit/test_cycle_intent_execution.py::test_exit_submission_failure_fires_notifier tests/unit/test_cycle_intent_execution.py::test_stop_cancel_failure_fires_notifier tests/unit/test_cycle_intent_execution.py::test_exit_failure_none_notifier_does_not_raise -v
```

Expected: all three PASS.

- [ ] **Step 8: Run the full intent-execution test suite to confirm no regressions**

```bash
pytest tests/unit/test_cycle_intent_execution.py -v
```

Expected: all tests PASS. If any existing test now fails because `_execute_exit` has a new `notifier` param, verify the call site was updated in Step 6 and all test `execute_cycle_intents` calls still work (the param has a default so no existing tests should break).

- [ ] **Step 9: Commit**

```bash
git add src/alpaca_bot/runtime/cycle_intent_execution.py tests/unit/test_cycle_intent_execution.py
git commit -m "feat: notify operator on exit hard failure (stop cancel + exit submission)"
```

---

## Task 2: Test and implement dashboard session P&L fields

**Files:**
- Modify: `tests/unit/test_web_service.py` (append 2 tests at the end)
- Modify: `src/alpaca_bot/web/service.py` (DashboardSnapshot + load_dashboard_snapshot)

### Step 1: Write the two failing tests

Open `tests/unit/test_web_service.py` and append the following two tests at the very end of the file:

```python
# ---------------------------------------------------------------------------
# Dashboard session P&L and loss-limit fields
# ---------------------------------------------------------------------------


def test_load_dashboard_snapshot_populates_realized_pnl_and_loss_limit_when_baseline_set() -> None:
    """When session_state has equity_baseline, load_dashboard_snapshot must populate
    realized_pnl from daily_realized_pnl() and loss_limit_amount from the settings pct."""
    from datetime import date as date_cls
    from alpaca_bot.storage import DailySessionState
    from alpaca_bot.config import TradingMode

    fixed_now = datetime(2026, 5, 2, 15, 0, tzinfo=timezone.utc)
    settings = make_settings(DAILY_LOSS_LIMIT_PCT="0.01")  # 1% => loss_limit = 500 on 50000 baseline

    session_state = DailySessionState(
        session_date=date_cls(2026, 5, 2),
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        entries_disabled=False,
        flatten_complete=False,
        equity_baseline=50000.0,
        updated_at=fixed_now,
    )

    realized_pnl_calls: list[dict] = []

    def fake_daily_realized_pnl(**kwargs):
        realized_pnl_calls.append(kwargs)
        return 142.50

    snapshot = load_dashboard_snapshot(
        settings=settings,
        connection=SimpleNamespace(),
        now=fixed_now,
        trading_status_store=SimpleNamespace(load=lambda **_: None),
        daily_session_state_store=SimpleNamespace(load=lambda **_: session_state),
        position_store=SimpleNamespace(list_all=lambda **_: []),
        order_store=SimpleNamespace(
            list_by_status=lambda **_: [],
            list_recent=lambda **_: [],
            daily_realized_pnl=fake_daily_realized_pnl,
        ),
        audit_event_store=make_audit_store(),
        strategy_flag_store=SimpleNamespace(list_all=lambda **_: []),
    )

    assert snapshot.realized_pnl == pytest.approx(142.50)
    assert snapshot.loss_limit_amount == pytest.approx(500.0)  # 50000 * 0.01
    assert len(realized_pnl_calls) == 1, "daily_realized_pnl must be called exactly once"


def test_load_dashboard_snapshot_realized_pnl_and_loss_limit_none_when_no_session() -> None:
    """When session_state is None (no equity_baseline), both realized_pnl and
    loss_limit_amount must be None; daily_realized_pnl must NOT be called."""
    realized_pnl_calls: list[dict] = []

    def should_not_be_called(**kwargs):
        realized_pnl_calls.append(kwargs)
        return 0.0

    fixed_now = datetime(2026, 5, 2, 15, 0, tzinfo=timezone.utc)

    snapshot = load_dashboard_snapshot(
        settings=make_settings(),
        connection=SimpleNamespace(),
        now=fixed_now,
        trading_status_store=SimpleNamespace(load=lambda **_: None),
        daily_session_state_store=SimpleNamespace(load=lambda **_: None),
        position_store=SimpleNamespace(list_all=lambda **_: []),
        order_store=SimpleNamespace(
            list_by_status=lambda **_: [],
            list_recent=lambda **_: [],
            daily_realized_pnl=should_not_be_called,
        ),
        audit_event_store=make_audit_store(),
        strategy_flag_store=SimpleNamespace(list_all=lambda **_: []),
    )

    assert snapshot.realized_pnl is None
    assert snapshot.loss_limit_amount is None
    assert realized_pnl_calls == [], "daily_realized_pnl must NOT be called when equity_baseline is None"
```

- [ ] **Step 2: Run the two tests to verify they fail**

```bash
pytest tests/unit/test_web_service.py::test_load_dashboard_snapshot_populates_realized_pnl_and_loss_limit_when_baseline_set tests/unit/test_web_service.py::test_load_dashboard_snapshot_realized_pnl_and_loss_limit_none_when_no_session -v
```

Expected: both FAIL — `DashboardSnapshot` has no `realized_pnl` attribute.

- [ ] **Step 3: Add fields to `DashboardSnapshot`**

In `src/alpaca_bot/web/service.py`, find the `DashboardSnapshot` dataclass (around line 137). Add `realized_pnl` and `loss_limit_amount` as optional fields **after** `latest_prices`:

Replace:
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
```

with:

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
```

- [ ] **Step 4: Compute `realized_pnl` and `loss_limit_amount` in `load_dashboard_snapshot`**

In `src/alpaca_bot/web/service.py`, find the `load_dashboard_snapshot` function. The current function ends with a `return DashboardSnapshot(...)` call that loads `session_state` inline. Refactor it to extract `session_state` first, then compute the new fields.

Replace the entire `return DashboardSnapshot(...)` block (starting at `return DashboardSnapshot(`) with:

```python
    session_state = daily_session_state_store.load(
        session_date=session_date,
        trading_mode=settings.trading_mode,
        strategy_version=settings.strategy_version,
        strategy_name=GLOBAL_SESSION_STATE_STRATEGY_NAME,
    )
    equity_baseline = session_state.equity_baseline if session_state is not None else None
    if equity_baseline is not None:
        realized_pnl: float | None = order_store.daily_realized_pnl(
            trading_mode=settings.trading_mode,
            strategy_version=settings.strategy_version,
            session_date=session_date,
            market_timezone=str(settings.market_timezone),
        )
        loss_limit_amount: float | None = equity_baseline * settings.daily_loss_limit_pct
    else:
        realized_pnl = None
        loss_limit_amount = None

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
            trading_mode=settings.trading_mode,
            strategy_version=settings.strategy_version,
            statuses=WORKING_ORDER_STATUSES,
        ),
        recent_orders=order_store.list_recent(
            trading_mode=settings.trading_mode,
            strategy_version=settings.strategy_version,
            limit=10,
        ),
        recent_events=recent_events,
        worker_health=_load_worker_health(
            audit_event_store=audit_event_store,
            recent_events=recent_events,
            now=generated_at,
        ),
        strategy_flags=strategy_flags,
        strategy_entries_disabled=strategy_entries_disabled,
        latest_prices=latest_prices or {},
        realized_pnl=realized_pnl,
        loss_limit_amount=loss_limit_amount,
    )
```

- [ ] **Step 5: Run the two new tests to verify they pass**

```bash
pytest tests/unit/test_web_service.py::test_load_dashboard_snapshot_populates_realized_pnl_and_loss_limit_when_baseline_set tests/unit/test_web_service.py::test_load_dashboard_snapshot_realized_pnl_and_loss_limit_none_when_no_session -v
```

Expected: both PASS.

- [ ] **Step 6: Run the full web-service test suite to confirm no regressions**

```bash
pytest tests/unit/test_web_service.py -v
```

Expected: all tests PASS. Existing tests pass `daily_session_state_store=SimpleNamespace(load=lambda **_: None)` which returns `None`, so `equity_baseline` is `None`, so `daily_realized_pnl` is not called — existing tests are not affected.

- [ ] **Step 7: Commit**

```bash
git add src/alpaca_bot/web/service.py tests/unit/test_web_service.py
git commit -m "feat: add realized_pnl and loss_limit_amount to DashboardSnapshot"
```

---

## Task 3: Add session P&L panel to dashboard template

**Files:**
- Modify: `src/alpaca_bot/web/templates/dashboard.html`

- [ ] **Step 1: Add two cells inside the `status-grid` div**

In `src/alpaca_bot/web/templates/dashboard.html`, find the closing `</div>` of the `<div class="status-grid">` block. The last two entries in that grid currently are "Worker Status" and "Last Worker Event" (around lines 224-243). Add the following two grid cells immediately **after** the "Last Worker Event" closing `</div>` and **before** the `</div>` that closes `status-grid`:

```html
            {% if snapshot.realized_pnl is not none %}
            <div>
              <p class="eyebrow">Session P&L</p>
              <p class="value" style="color: {{ 'var(--accent)' if snapshot.realized_pnl >= 0 else '#c0392b' }}">
                {% if snapshot.realized_pnl >= 0 %}+{{ format_price(snapshot.realized_pnl) }}{% else %}-{{ format_price(0 - snapshot.realized_pnl) }}{% endif %}
              </p>
            </div>
            {% if snapshot.loss_limit_amount is not none and snapshot.loss_limit_amount > 0 %}
            <div>
              {% set limit_used_pct = ((0 - snapshot.realized_pnl) / snapshot.loss_limit_amount * 100) if snapshot.realized_pnl < 0 else 0 %}
              <p class="eyebrow">Loss Limit</p>
              <p class="value {% if limit_used_pct >= 90 %}warn{% elif limit_used_pct >= 75 %}warn{% endif %}">
                {{ format_price(snapshot.loss_limit_amount) }}
                {% if limit_used_pct > 0 %}
                  <span class="muted" style="font-size: 0.85em;">({{ "%.0f" | format(limit_used_pct) }}% used)</span>
                {% endif %}
              </p>
            </div>
            {% endif %}
            {% endif %}
```

The `format_price` macro is already defined in the template (used extensively in the positions table). The `var(--accent)` CSS variable is the green color used for positive P&L throughout the template.

- [ ] **Step 2: Run the full test suite to verify no breakage**

```bash
pytest tests/unit/test_web_service.py tests/unit/test_web_app.py -v
```

Expected: all tests PASS. Template changes don't affect unit tests (templates are rendered in integration-level app tests that pass the full snapshot object).

- [ ] **Step 3: Commit**

```bash
git add src/alpaca_bot/web/templates/dashboard.html
git commit -m "feat: add session P&L and loss-limit panel to dashboard status grid"
```

---

## Task 4: Full regression run

- [ ] **Step 1: Run the complete test suite**

```bash
pytest
```

Expected: all tests PASS (currently 1027 tests before this change; expect 1032 after adding 5 new tests).

- [ ] **Step 2: Commit if any cleanup was needed; otherwise done**

No additional commit needed if Step 1 passes cleanly.

---

## Self-Review

**Spec coverage:**
- Fix 1 (exit submission failure notifier) — Task 1 Steps 3-6 ✓
- Fix 1 (stop cancel failure notifier) — Task 1 Steps 4 (cancel-hard-failed path) ✓
- Fix 1 (notifier=None safe) — Task 1 test 3 + no `if notifier` guard needed at call site ✓
- Fix 2 (DashboardSnapshot fields) — Task 2 Steps 3-4 ✓
- Fix 2 (template panel) — Task 3 Step 1 ✓
- Test 1 (exit submission failure notifies) — Task 1 Step 1 ✓
- Test 2 (stop cancel failure notifies) — Task 1 Step 1 ✓
- Test 3 (notifier=None no crash) — Task 1 Step 1 ✓
- Test 4 (dashboard fields populated) — Task 2 Step 1 ✓
- Test 5 (dashboard fields None when no session) — Task 2 Step 1 ✓

**Placeholder scan:** No TBDs, no "implement later". All code blocks are complete.

**Type consistency:**
- `realized_pnl: float | None` defined in Task 2 Step 3, used in Task 3 template as `snapshot.realized_pnl` ✓
- `loss_limit_amount: float | None` defined in Task 2 Step 3, used in Task 3 template as `snapshot.loss_limit_amount` ✓
- `notifier: Notifier | None = None` added to `_execute_exit` in Task 1 Step 3, referenced in Steps 4-5 ✓
- `Notifier` is already imported at line 14 of `cycle_intent_execution.py` (used by `_execute_update_stop`) — no new import needed ✓
