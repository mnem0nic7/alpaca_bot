# Day Trade Evaluation Dashboard Panel — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend the `/metrics` dashboard to show `BacktestReport`-quality statistics (profit factor, avg hold time, stop/EOD exit breakdown, consecutive win/loss streaks) for today's live trades, matching what `alpaca-bot-session-eval` already shows in the terminal.

**Architecture:** Extend `TradeRecord` with `exit_reason` and `hold_minutes`; add `session_report: BacktestReport | None` to `MetricsSnapshot`; add a private `_row_to_replay_record()` helper in `service.py` (no cross-layer import); extend `load_metrics_snapshot()` with an optional `daily_session_state_store` DI parameter; wire `report_from_records()` from `replay/report.py`.

**Tech Stack:** Python frozen dataclasses (existing pattern), Jinja2 (existing), `BacktestReport`/`report_from_records` from `alpaca_bot.replay.report` (existing), `DailySessionStateStore` from `alpaca_bot.storage` (already imported in `service.py`).

---

## File Map

| File | Change |
|------|--------|
| `src/alpaca_bot/web/service.py` | Add 2 imports; extend `TradeRecord`; extend `_to_trade_record()`; add `_row_to_replay_record()`; extend `MetricsSnapshot`; extend `load_metrics_snapshot()` |
| `src/alpaca_bot/web/templates/dashboard.html` | Add Session Evaluation panel; add Hold + Exit columns to Trade Results table |
| `tests/unit/test_web_service.py` | Update `make_metrics_stores()`; update `_trade()`; add 4 new tests |

---

## Task 1 — Extend `TradeRecord` and `_to_trade_record()`

**Files:**
- Modify: `src/alpaca_bot/web/service.py:77-97`
- Test: `tests/unit/test_web_service.py`

- [ ] **Step 1: Write the failing test**

Add at the bottom of the `TestLoadMetricsSnapshot` block in `tests/unit/test_web_service.py` (after the last existing `test_load_metrics_snapshot_*` test, around line 499):

```python
def test_trade_record_exit_reason_and_hold_minutes() -> None:
    from datetime import timezone
    entry_time = datetime(2026, 5, 5, 10, 0, tzinfo=timezone.utc)
    exit_time = datetime(2026, 5, 5, 10, 45, tzinfo=timezone.utc)
    row = {
        "symbol": "AAPL",
        "entry_fill": 100.0,
        "entry_limit": 101.0,
        "entry_time": entry_time,
        "exit_time": exit_time,
        "exit_fill": 105.0,
        "qty": 10,
        "intent_type": "stop",
    }
    trade = _to_trade_record(row)
    assert trade.exit_reason == "stop"
    assert trade.hold_minutes == pytest.approx(45.0)

    row_eod = {**row, "intent_type": "eod"}
    trade_eod = _to_trade_record(row_eod)
    assert trade_eod.exit_reason == "eod"
```

Also add `_to_trade_record` to the imports near the top of the test section that already imports from `web.service`:

```python
from alpaca_bot.web.service import TradeRecord, _to_trade_record
```

(currently line ~552 reads `from alpaca_bot.web.service import TradeRecord`)

- [ ] **Step 2: Run test to verify it fails**

```
pytest tests/unit/test_web_service.py::test_trade_record_exit_reason_and_hold_minutes -v
```

Expected: FAIL — `TradeRecord` has no attribute `exit_reason`.

- [ ] **Step 3: Extend `TradeRecord` and `_to_trade_record()` in `service.py`**

Replace the `TradeRecord` dataclass (lines 77–88):

```python
@dataclass(frozen=True)
class TradeRecord:
    symbol: str
    strategy_name: str
    entry_time: datetime | None
    exit_time: datetime | None
    entry_price: float
    exit_price: float
    quantity: int
    pnl: float
    slippage: float | None  # limit_price - fill_price; positive=favorable, negative=adverse
    exit_reason: str = "eod"          # "stop" or "eod"
    hold_minutes: float | None = None  # (exit_time - entry_time).total_seconds() / 60
```

Replace `_to_trade_record()` (lines 377–397):

```python
def _to_trade_record(row: dict) -> TradeRecord:
    entry_fill = row["entry_fill"]
    exit_fill = row["exit_fill"]
    qty = row["qty"]
    pnl = (exit_fill - entry_fill) * qty
    slippage = (
        row["entry_limit"] - entry_fill
        if row.get("entry_limit") is not None
        else None
    )
    entry_time = row.get("entry_time")
    exit_time = row.get("exit_time")
    hold_minutes = (
        (exit_time - entry_time).total_seconds() / 60
        if entry_time is not None and exit_time is not None
        else None
    )
    exit_reason = "stop" if row.get("intent_type") == "stop" else "eod"
    return TradeRecord(
        symbol=row["symbol"],
        strategy_name=row.get("strategy_name", "breakout"),
        entry_time=entry_time,
        exit_time=exit_time,
        entry_price=entry_fill,
        exit_price=exit_fill,
        quantity=qty,
        pnl=pnl,
        slippage=slippage,
        exit_reason=exit_reason,
        hold_minutes=hold_minutes,
    )
```

- [ ] **Step 4: Run test to verify it passes**

```
pytest tests/unit/test_web_service.py::test_trade_record_exit_reason_and_hold_minutes -v
```

Expected: PASS

- [ ] **Step 5: Run full suite — no regressions**

```
pytest tests/unit/test_web_service.py -v
```

Expected: all tests pass. `_trade()` helper at line 555 constructs `TradeRecord` positionally — confirm it still works since the new fields have defaults.

- [ ] **Step 6: Commit**

```bash
git add tests/unit/test_web_service.py src/alpaca_bot/web/service.py
git commit -m "feat: add exit_reason and hold_minutes to TradeRecord"
```

---

## Task 2 — Add `session_report` to `MetricsSnapshot` and wire `report_from_records()`

**Files:**
- Modify: `src/alpaca_bot/web/service.py`
- Test: `tests/unit/test_web_service.py`

- [ ] **Step 1: Update `make_metrics_stores()` and write 3 failing tests**

Replace `make_metrics_stores()` in `tests/unit/test_web_service.py` (lines 429–440):

```python
def make_metrics_stores(trades=None, admin_events=None, last_tuning=None, daily_session_state_store=None):
    default_state_store = daily_session_state_store or SimpleNamespace(load=lambda **_: None)
    return dict(
        order_store=SimpleNamespace(
            list_closed_trades=lambda **_: trades if trades is not None else [],
        ),
        audit_event_store=SimpleNamespace(
            list_by_event_types=lambda **_: admin_events if admin_events is not None else [],
        ),
        tuning_result_store=SimpleNamespace(
            load_latest_best=lambda **_: last_tuning,
        ),
        daily_session_state_store=default_state_store,
    )
```

Then add the three new tests after `test_trade_record_exit_reason_and_hold_minutes`:

```python
def test_session_report_none_when_no_trades() -> None:
    now = datetime(2026, 5, 5, 15, 0, tzinfo=timezone.utc)
    metrics = load_metrics_snapshot(
        settings=make_settings(),
        connection=SimpleNamespace(),
        now=now,
        **make_metrics_stores(trades=[]),
    )
    assert metrics.session_report is None


def test_session_report_populated_from_trades() -> None:
    now = datetime(2026, 5, 5, 15, 0, tzinfo=timezone.utc)
    entry_time = datetime(2026, 5, 5, 10, 0, tzinfo=timezone.utc)
    exit_time = datetime(2026, 5, 5, 10, 30, tzinfo=timezone.utc)
    trades = [
        {
            "symbol": "AAPL",
            "entry_fill": 100.0,
            "entry_limit": None,
            "entry_time": entry_time,
            "exit_time": exit_time,
            "exit_fill": 105.0,
            "qty": 10,
            "intent_type": "stop",
        },
        {
            "symbol": "GOOG",
            "entry_fill": 200.0,
            "entry_limit": None,
            "entry_time": entry_time,
            "exit_time": exit_time,
            "exit_fill": 190.0,
            "qty": 5,
            "intent_type": "eod",
        },
    ]
    metrics = load_metrics_snapshot(
        settings=make_settings(),
        connection=SimpleNamespace(),
        now=now,
        **make_metrics_stores(trades=trades),
    )
    assert metrics.session_report is not None
    assert metrics.session_report.profit_factor is not None
    assert metrics.session_report.stop_wins == 1
    assert metrics.session_report.eod_losses == 1


def test_session_report_uses_starting_equity_from_store() -> None:
    now = datetime(2026, 5, 5, 15, 0, tzinfo=timezone.utc)
    entry_time = datetime(2026, 5, 5, 10, 0, tzinfo=timezone.utc)
    exit_time = datetime(2026, 5, 5, 10, 30, tzinfo=timezone.utc)
    trades = [
        {
            "symbol": "AAPL",
            "entry_fill": 100.0,
            "entry_limit": None,
            "entry_time": entry_time,
            "exit_time": exit_time,
            "exit_fill": 95.0,
            "qty": 10,
            "intent_type": "eod",
        },
    ]
    fake_state = SimpleNamespace(equity_baseline=50_000.0)
    fake_store = SimpleNamespace(load=lambda **_: fake_state)
    metrics = load_metrics_snapshot(
        settings=make_settings(),
        connection=SimpleNamespace(),
        now=now,
        **make_metrics_stores(trades=trades, daily_session_state_store=fake_store),
    )
    assert metrics.session_report is not None
    # pnl = (95-100)*10 = -50; peak = 50_000; drawdown = 50/50_000 = 0.001
    assert metrics.session_report.max_drawdown_pct == pytest.approx(50.0 / 50_000.0)
```

Add `load_metrics_snapshot` to the import at the top of the relevant test block (the import near line 1 of the test file already imports it; verify it's present):

```python
from alpaca_bot.web.service import load_metrics_snapshot, MetricsSnapshot
```

- [ ] **Step 2: Run tests to verify they fail**

```
pytest tests/unit/test_web_service.py::test_session_report_none_when_no_trades tests/unit/test_web_service.py::test_session_report_populated_from_trades tests/unit/test_web_service.py::test_session_report_uses_starting_equity_from_store -v
```

Expected: all 3 FAIL — `MetricsSnapshot` has no attribute `session_report` / `load_metrics_snapshot()` does not accept `daily_session_state_store`.

- [ ] **Step 3: Implement — add imports, `_row_to_replay_record()`, extend `MetricsSnapshot`, extend `load_metrics_snapshot()`**

**3a. Add imports** in `service.py` (after the existing `from alpaca_bot.storage.repositories import TuningResultStore` line):

```python
from alpaca_bot.replay.report import BacktestReport, ReplayTradeRecord, report_from_records
```

Also add `EQUITY_SESSION_STATE_STRATEGY_NAME` to the existing `from alpaca_bot.storage import (...)` block (the constant is already exported there; add it to the list).

**3b. Extend `MetricsSnapshot`** (lines 127–139). Add `session_report` field after `last_backtest`:

```python
@dataclass(frozen=True)
class MetricsSnapshot:
    generated_at: datetime
    session_date: date
    trades: list[TradeRecord]
    trades_by_strategy: dict[str, list[TradeRecord]]
    total_pnl: float
    win_rate: float | None
    mean_return_pct: float | None
    max_drawdown_pct: float | None
    sharpe_ratio: float | None
    admin_history: list[AuditEvent]
    last_backtest: object | None = None
    session_report: BacktestReport | None = None
```

**3c. Add `_row_to_replay_record()` private helper** after `_to_trade_record()` (after line 397):

```python
def _row_to_replay_record(row: dict) -> ReplayTradeRecord:
    entry = row["entry_fill"]
    exit_ = row["exit_fill"]
    qty = row["qty"]
    pnl = (exit_ - entry) * qty
    return_pct = (exit_ - entry) / entry
    exit_reason = "stop" if row.get("intent_type") == "stop" else "eod"
    return ReplayTradeRecord(
        symbol=row["symbol"],
        entry_price=entry,
        exit_price=exit_,
        quantity=qty,
        entry_time=row["entry_time"],
        exit_time=row["exit_time"],
        exit_reason=exit_reason,
        pnl=pnl,
        return_pct=return_pct,
    )
```

**3d. Extend `load_metrics_snapshot()`** signature and body (lines 303–346).

Add the new parameter to the signature:

```python
def load_metrics_snapshot(
    *,
    settings: Settings,
    connection: ConnectionProtocol,
    order_store: OrderStore | None = None,
    audit_event_store: AuditEventStore | None = None,
    tuning_result_store: TuningResultStore | None = None,
    daily_session_state_store: DailySessionStateStore | None = None,
    now: datetime | None = None,
    session_date: date | None = None,
) -> MetricsSnapshot:
```

After `trades = [_to_trade_record(t) for t in raw_trades]`, add the session_report computation:

```python
    session_report: BacktestReport | None = None
    if raw_trades:
        state_store = daily_session_state_store or DailySessionStateStore(connection)
        state = state_store.load(
            session_date=session_date,
            trading_mode=settings.trading_mode,
            strategy_version=settings.strategy_version,
            strategy_name=EQUITY_SESSION_STATE_STRATEGY_NAME,
        )
        starting_equity = (
            state.equity_baseline
            if state is not None and state.equity_baseline is not None
            else 100_000.0
        )
        replay_records = [_row_to_replay_record(r) for r in raw_trades]
        session_report = report_from_records(
            replay_records, starting_equity=starting_equity, strategy_name="all"
        )
```

Update the `MetricsSnapshot(...)` return to include `session_report=session_report`.

- [ ] **Step 4: Run tests to verify they pass**

```
pytest tests/unit/test_web_service.py::test_session_report_none_when_no_trades tests/unit/test_web_service.py::test_session_report_populated_from_trades tests/unit/test_web_service.py::test_session_report_uses_starting_equity_from_store -v
```

Expected: all 3 PASS

- [ ] **Step 5: Run full suite — no regressions**

```
pytest tests/unit/test_web_service.py -v
```

Expected: all tests pass (the updated `make_metrics_stores()` provides a default fake `daily_session_state_store` returning `None` for all existing tests, so they fall back to `starting_equity=100_000.0`).

- [ ] **Step 6: Commit**

```bash
git add tests/unit/test_web_service.py src/alpaca_bot/web/service.py
git commit -m "feat: add session_report to MetricsSnapshot; wire report_from_records into load_metrics_snapshot"
```

---

## Task 3 — Dashboard template: Session Evaluation panel + Hold/Exit columns

**Files:**
- Modify: `src/alpaca_bot/web/templates/dashboard.html`

- [ ] **Step 1: Add the Session Evaluation panel**

Insert the new panel between the closing `</div>` of the "Session P&L Summary" panel (line ~640) and the opening `<div class="panel">` of the "Trade Results" panel (line ~642).

Insert between those two lines:

```html
        {% if metrics.session_report %}
        <div class="panel">
          <h2>Session Evaluation</h2>
          <div class="status-grid" style="margin-top: 0.8rem;">
            <div>
              <p class="eyebrow">Profit Factor</p>
              <p class="value {% if metrics.session_report.profit_factor is not none and metrics.session_report.profit_factor < 1.0 %}warn{% endif %}">
                {{ "%.2f"|format(metrics.session_report.profit_factor) if metrics.session_report.profit_factor is not none else "&mdash;" }}
              </p>
            </div>
            <div>
              <p class="eyebrow">Avg Hold</p>
              <p class="value">
                {{ "%.0fm"|format(metrics.session_report.avg_hold_minutes) if metrics.session_report.avg_hold_minutes is not none else "&mdash;" }}
              </p>
            </div>
            <div>
              <p class="eyebrow">Stop Exits</p>
              <p class="value">{{ metrics.session_report.stop_wins }}W / {{ metrics.session_report.stop_losses }}L</p>
            </div>
            <div>
              <p class="eyebrow">EOD Exits</p>
              <p class="value">{{ metrics.session_report.eod_wins }}W / {{ metrics.session_report.eod_losses }}L</p>
            </div>
            <div>
              <p class="eyebrow">Max Consec. Wins</p>
              <p class="value">{{ metrics.session_report.max_consecutive_wins }}</p>
            </div>
            <div>
              <p class="eyebrow">Max Consec. Losses</p>
              <p class="value {% if metrics.session_report.max_consecutive_losses >= 3 %}warn{% endif %}">
                {{ metrics.session_report.max_consecutive_losses }}
              </p>
            </div>
          </div>
        </div>
        {% endif %}
```

- [ ] **Step 2: Add Hold and Exit columns to the Trade Results table**

In the table `<thead>`, after `<th>Slippage</th>` (line ~654), add:

```html
                  <th>Hold</th>
                  <th>Exit</th>
```

In each `<tr>` inside the `{% for trade in metrics.trades %}` loop (line ~658), after the Slippage `<td>` (line ~669), add:

```html
                    <td class="muted">
                      {{ "%.0fm"|format(trade.hold_minutes) if trade.hold_minutes is not none else "&mdash;" }}
                    </td>
                    <td class="muted">{{ trade.exit_reason }}</td>
```

Update the empty-state row's `colspan` from `"7"` to `"9"` (was covering 7 columns; now 9):

```html
                  <tr><td colspan="9" class="muted">No closed trades today.</td></tr>
```

- [ ] **Step 3: Run full pytest to verify no regressions**

```
pytest
```

Expected: all tests pass. (Template changes have no unit tests; visual correctness verified by observing the dashboard.)

- [ ] **Step 4: Commit**

```bash
git add src/alpaca_bot/web/templates/dashboard.html
git commit -m "feat: add Session Evaluation panel and Hold/Exit columns to metrics dashboard"
```

---

## Self-Review Checklist

**Spec coverage:**

| Spec requirement | Task |
|---|---|
| `TradeRecord.exit_reason` | Task 1 |
| `TradeRecord.hold_minutes` | Task 1 |
| `_to_trade_record()` populates new fields | Task 1 |
| `MetricsSnapshot.session_report: BacktestReport | None` | Task 2 |
| `_row_to_replay_record()` private helper (no cross-layer import) | Task 2 |
| `load_metrics_snapshot()` DI param `daily_session_state_store` | Task 2 |
| `starting_equity` from store, fallback `100_000.0` | Task 2 |
| `report_from_records(replay_records, starting_equity, strategy_name="all")` | Task 2 |
| Session Evaluation panel in dashboard | Task 3 |
| Hold/Exit columns in Trade Results table | Task 3 |
| `test_session_report_populated_from_trades` | Task 2, Step 1 |
| `test_session_report_none_when_no_trades` | Task 2, Step 1 |
| `test_trade_record_exit_reason_and_hold_minutes` | Task 1, Step 1 |
| `test_session_report_uses_starting_equity_from_store` | Task 2, Step 1 |

All spec requirements covered. No placeholders.
