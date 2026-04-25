# Phase 6 — Live Trading Readiness: Implementation Plan

**Date**: 2026-04-25  
**Spec**: `docs/superpowers/specs/2026-04-25-phase6-live-trading-readiness.md`  
**Status**: Refined after grilling (v2)

---

## Execution Order

Recommended sequence: 2 (Settings first) → 1 (engine) → 7 (staleness + session threading) → 3 → 4 → 5 → 6.

---

## Task 1 — Hard flatten on loss limit breach

### Files to modify
- `src/alpaca_bot/core/engine.py`
- `src/alpaca_bot/runtime/cycle.py`
- `src/alpaca_bot/runtime/supervisor.py`
- `src/alpaca_bot/runtime/cycle_intent_execution.py`

### 1a. `src/alpaca_bot/core/engine.py` — add `flatten_all` parameter

Add `flatten_all: bool = False` to `evaluate_cycle()` signature. Add early-return flatten path before the existing position-loop:

```python
def evaluate_cycle(
    *,
    settings: Settings,
    now: datetime,
    equity: float,
    intraday_bars_by_symbol: Mapping[str, Sequence[Bar]],
    daily_bars_by_symbol: Mapping[str, Sequence[Bar]],
    open_positions: Sequence[OpenPosition],
    working_order_symbols: set[str],
    traded_symbols_today: set[tuple[str, date]],
    entries_disabled: bool,
    flatten_all: bool = False,
    signal_evaluator: StrategySignalEvaluator | None = None,
    session_state: "DailySessionState | None" = None,
) -> CycleResult:
    if signal_evaluator is None:
        signal_evaluator = evaluate_breakout_signal

    flatten_complete = (
        session_state is not None and session_state.flatten_complete
    )

    # Hard flatten: emit EXIT for every open position, overriding normal logic.
    if flatten_all:
        intents: list[CycleIntent] = []
        for position in open_positions:
            bars = intraday_bars_by_symbol.get(position.symbol, ())
            timestamp = bars[-1].timestamp if bars else now
            intents.append(
                CycleIntent(
                    intent_type=CycleIntentType.EXIT,
                    symbol=position.symbol,
                    timestamp=timestamp,
                    reason="loss_limit_flatten",
                )
            )
        intents.sort(key=lambda intent: (intent.timestamp, intent.symbol, intent.intent_type.value))
        return CycleResult(as_of=now, intents=intents)

    intents: list[CycleIntent] = []
    open_position_symbols = {position.symbol for position in open_positions}

    for position in open_positions:
        # ... rest of the existing code unchanged
```

The `flatten_all=True` path returns early — no stop-trail or entry logic runs.

### 1b. `src/alpaca_bot/runtime/cycle.py` — thread `flatten_all` through `run_cycle()`

Add `flatten_all: bool = False` to `run_cycle()` signature and pass it to `evaluate_cycle()`:

```python
def run_cycle(
    *,
    settings: Settings,
    runtime: RuntimeProtocol,
    now: datetime,
    equity: float,
    intraday_bars_by_symbol: Mapping[str, Sequence[Bar]],
    daily_bars_by_symbol: Mapping[str, Sequence[Bar]],
    open_positions: Sequence[OpenPosition],
    working_order_symbols: set[str],
    traded_symbols_today: set[tuple[str, date]],
    entries_disabled: bool,
    flatten_all: bool = False,
    session_state: "DailySessionState | None" = None,
) -> CycleResult:
    result = evaluate_cycle(
        settings=settings,
        now=now,
        equity=equity,
        intraday_bars_by_symbol=intraday_bars_by_symbol,
        daily_bars_by_symbol=daily_bars_by_symbol,
        open_positions=open_positions,
        working_order_symbols=working_order_symbols,
        traded_symbols_today=traded_symbols_today,
        entries_disabled=entries_disabled,
        flatten_all=flatten_all,
        session_state=session_state,
    )
    # ... rest unchanged
```

Note: `session_state` is also added here (required for Task 7 — threading `flatten_complete` to `evaluate_cycle()` so the deduplicate guard there is no longer dead code).

### 1c. `src/alpaca_bot/runtime/supervisor.py` — pass `flatten_all` in `run_cycle_once()`

In `run_cycle_once()`, at the `_cycle_runner` call (line 240), add `flatten_all=daily_loss_limit_breached` and `session_state=session_state` (Task 7 loads `session_state` separately — see Task 7):

```python
cycle_result = self._cycle_runner(
    settings=self.settings,
    runtime=self.runtime,
    now=timestamp,
    equity=account.equity,
    intraday_bars_by_symbol=intraday_bars_by_symbol,
    daily_bars_by_symbol=daily_bars_by_symbol,
    open_positions=open_positions,
    working_order_symbols=working_order_symbols,
    traded_symbols_today=self._load_traded_symbols(session_date=session_date),
    entries_disabled=entries_disabled,
    flatten_all=daily_loss_limit_breached,
    session_state=session_state,
)
```

Also extend the `has_flatten_intents` check (lines 263–279) to also save session state when `loss_limit_flatten` intents are present:

```python
has_flatten_intents = any(
    getattr(intent, "reason", None) in {"eod_flatten", "loss_limit_flatten"}
    for intent in getattr(cycle_result, "intents", [])
)
```

### 1d. `src/alpaca_bot/runtime/cycle_intent_execution.py` — guard against duplicate EXIT dispatch

In `_execute_exit()`, before calling `broker.submit_market_exit()`, add a check for existing active exit orders. This prevents the race where the next 60s cycle sees open positions (fill not yet processed) and dispatches a second market exit order.

Add the guard after the `canceled_stop_count` accumulation and before the `client_order_id` line:

```python
    if position_already_gone:
        return canceled_stop_count, 0

    # Guard: skip if an active exit order already exists for this symbol.
    active_exit_orders = [
        o for o in runtime.order_store.list_by_status(
            trading_mode=settings.trading_mode,
            strategy_version=settings.strategy_version,
            statuses=list(ACTIVE_STOP_STATUSES),
        )
        if o.symbol == symbol and o.intent_type == "exit"
    ]
    if active_exit_orders:
        runtime.audit_event_store.append(
            AuditEvent(
                event_type="cycle_intent_skipped",
                symbol=symbol,
                payload={
                    "intent_type": "exit",
                    "reason": "active_exit_already_exists",
                    "existing_order_id": active_exit_orders[0].client_order_id,
                },
                created_at=now,
            )
        )
        return canceled_stop_count, 0

    client_order_id = _exit_client_order_id(...)
```

This guard uses the same `ACTIVE_STOP_STATUSES` constant (which includes `pending_submit`, `new`, `accepted`, `submitted`, `partially_filled`). The AuditEvent gives an operator-visible paper trail when a duplicate EXIT is skipped.

**Crash safety note**: EXIT intents produced by `flatten_all=True` are NOT pre-written to Postgres (only ENTRY intents are written in `run_cycle()` before dispatch). If the supervisor crashes after `run_cycle()` returns but before `execute_cycle_intents()` completes the exit, startup recovery (`recover_startup_state()`) will detect the position/order mismatch and reconcile on next boot. This is the existing crash-recovery path — no new pre-write mechanism is needed.

### Tests for Task 1

**File**: `tests/unit/test_cycle_engine.py`

```python
def test_flatten_all_emits_exit_for_all_open_positions():
    """When flatten_all=True, EXIT intents are emitted for every open position."""
    settings = _make_settings()
    position = _make_open_position("AAPL")
    bars = [_make_bar("AAPL", ts=_dt("10:00"), close=151.0)]
    result = evaluate_cycle(
        settings=settings,
        now=_dt("10:00"),
        equity=100_000.0,
        intraday_bars_by_symbol={"AAPL": bars},
        daily_bars_by_symbol={},
        open_positions=[position],
        working_order_symbols=set(),
        traded_symbols_today=set(),
        entries_disabled=False,
        flatten_all=True,
    )
    assert len(result.intents) == 1
    assert result.intents[0].intent_type == CycleIntentType.EXIT
    assert result.intents[0].reason == "loss_limit_flatten"


def test_flatten_all_suppresses_entries():
    """When flatten_all=True, no entry intents are emitted."""
    settings = _make_settings()
    result = evaluate_cycle(
        settings=settings,
        now=_dt("10:00"),
        equity=100_000.0,
        intraday_bars_by_symbol={},
        daily_bars_by_symbol={},
        open_positions=[],
        working_order_symbols=set(),
        traded_symbols_today=set(),
        entries_disabled=False,
        flatten_all=True,
    )
    assert result.intents == []
```

**File**: `tests/unit/test_runtime_supervisor.py`

```python
def test_run_cycle_once_passes_flatten_all_when_loss_limit_breached():
    captured = {}

    def fake_cycle_runner(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(intents=[])

    supervisor = _make_supervisor(
        cycle_runner=fake_cycle_runner,
        realized_pnl=-200.0,   # breaches 0.01 × 10000 = 100
        equity=10_000.0,
    )
    supervisor.run_cycle_once(now=lambda: _market_open_ts())
    assert captured.get("flatten_all") is True


def test_run_cycle_once_flatten_all_false_when_no_breach():
    captured = {}

    def fake_cycle_runner(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(intents=[])

    supervisor = _make_supervisor(
        cycle_runner=fake_cycle_runner,
        realized_pnl=0.0,
        equity=10_000.0,
    )
    supervisor.run_cycle_once(now=lambda: _market_open_ts())
    assert captured.get("flatten_all") is False
```

**File**: `tests/unit/test_cycle_intent_execution.py` (or `test_alpaca_execution.py`)

```python
def test_execute_exit_skips_when_active_exit_order_exists():
    """A second EXIT dispatch is skipped if an active exit order already exists."""
    existing_exit = _make_order(
        symbol="AAPL", intent_type="exit", status="submitted"
    )
    runtime = _make_runtime(
        positions=[_make_position("AAPL")],
        orders=[existing_exit],
    )
    broker = _make_broker()
    result = execute_cycle_intents(
        settings=_make_settings(),
        runtime=runtime,
        broker=broker,
        cycle_result=SimpleNamespace(intents=[
            SimpleNamespace(intent_type=CycleIntentType.EXIT, symbol="AAPL",
                            timestamp=_dt("10:00"), reason="loss_limit_flatten")
        ]),
    )
    assert result.submitted_exit_count == 0
    assert broker.submitted_exits == []  # no broker call
```

---

## Task 2 — Aggregate portfolio exposure cap

### Files to modify
- `src/alpaca_bot/config/__init__.py`
- `src/alpaca_bot/core/engine.py`

### 2a. `src/alpaca_bot/config/__init__.py` — add Settings fields

**Add to `Settings` dataclass** (after `daily_loss_limit_pct`):
```python
max_portfolio_exposure_pct: float = 0.15
notify_slippage_threshold_pct: float = 0.005
```

**Add to `from_env()`** (after `daily_loss_limit_pct=...`):
```python
max_portfolio_exposure_pct=float(values.get("MAX_PORTFOLIO_EXPOSURE_PCT", "0.15")),
notify_slippage_threshold_pct=float(values.get("NOTIFY_SLIPPAGE_THRESHOLD_PCT", "0.005")),
```

**Add to `validate()`** (after existing `_validate_positive_fraction` calls):
```python
if not 0 < self.max_portfolio_exposure_pct <= 1.0:
    raise ValueError("MAX_PORTFOLIO_EXPOSURE_PCT must be between 0 and 1.0 (inclusive)")
if self.notify_slippage_threshold_pct < 0:
    raise ValueError("NOTIFY_SLIPPAGE_THRESHOLD_PCT must be >= 0")
```

Note: `max_portfolio_exposure_pct=1.0` is valid (100% deployment, effectively no aggregate cap). `notify_slippage_threshold_pct=0.0` is valid (alert on any adverse slippage). Do NOT use `_validate_positive_fraction()` here — that helper enforces `0 < value < 1` which would wrongly reject both `1.0` and `0.0`.

### 2b. `src/alpaca_bot/core/engine.py` — enforce cap before emitting entries

Replace the `entry_candidates.sort(...)` + `intents.extend(...)` lines at the end of the `if not entries_disabled:` block:

```python
entry_candidates.sort(
    key=lambda item: (-item[0], -item[1], item[2].symbol),
)
# Aggregate exposure cap: skip entries that would push notional/equity over the limit.
current_exposure = (
    sum(p.entry_price * p.quantity for p in open_positions) / equity
    if equity > 0
    else 0.0
)
available_exposure = settings.max_portfolio_exposure_pct - current_exposure
admitted: list[CycleIntent] = []
for *_rank, candidate in entry_candidates:
    if len(admitted) >= available_slots:
        break
    candidate_notional = (
        (candidate.limit_price or candidate.stop_price or 0.0) * (candidate.quantity or 0)
    )
    candidate_exposure = candidate_notional / equity if equity > 0 else 0.0
    if candidate_exposure > available_exposure:
        continue
    admitted.append(candidate)
    available_exposure -= candidate_exposure
intents.extend(admitted)
```

### Tests for Task 2

**File**: `tests/unit/test_cycle_engine.py`

```python
def test_settings_rejects_max_portfolio_exposure_pct_above_one():
    with pytest.raises(ValueError, match="MAX_PORTFOLIO_EXPOSURE_PCT"):
        Settings.from_env({**_base_env(), "MAX_PORTFOLIO_EXPOSURE_PCT": "1.5"})


def test_settings_accepts_max_portfolio_exposure_pct_of_one():
    """1.0 (100% deployment) is a valid value."""
    s = Settings.from_env({**_base_env(), "MAX_PORTFOLIO_EXPOSURE_PCT": "1.0"})
    assert s.max_portfolio_exposure_pct == 1.0


def test_settings_rejects_notify_slippage_threshold_pct_negative():
    with pytest.raises(ValueError, match="NOTIFY_SLIPPAGE_THRESHOLD_PCT"):
        Settings.from_env({**_base_env(), "NOTIFY_SLIPPAGE_THRESHOLD_PCT": "-0.1"})


def test_settings_accepts_notify_slippage_threshold_pct_zero():
    """0.0 means alert on any adverse slippage."""
    s = Settings.from_env({**_base_env(), "NOTIFY_SLIPPAGE_THRESHOLD_PCT": "0.0"})
    assert s.notify_slippage_threshold_pct == 0.0


def test_aggregate_exposure_cap_blocks_entry_exceeding_limit():
    """Entry is skipped if it would push exposure over max_portfolio_exposure_pct."""
    settings = _make_settings(
        max_portfolio_exposure_pct=0.10,
        max_open_positions=3,
    )
    # Existing position: 80 * 10 = 800 / 10000 = 8% of equity
    existing = _make_open_position("MSFT", entry_price=80.0, quantity=10)
    # AAPL signal at ~$25/share × 10 = $250 = 2.5%; 8% + 2.5% = 10.5% > 10% cap
    bars = _make_breakout_bars("AAPL", price=25.0)
    result = evaluate_cycle(
        settings=settings,
        now=_dt("10:00"),
        equity=10_000.0,
        intraday_bars_by_symbol={"AAPL": bars, "MSFT": []},
        daily_bars_by_symbol={"AAPL": [_make_daily_bar("AAPL")]},
        open_positions=[existing],
        working_order_symbols=set(),
        traded_symbols_today=set(),
        entries_disabled=False,
    )
    entries = [i for i in result.intents if i.intent_type == CycleIntentType.ENTRY]
    assert len(entries) == 0
```

---

## Task 3 — Live session Sharpe ratio

### Files to modify
- `src/alpaca_bot/web/service.py`
- `src/alpaca_bot/web/templates/dashboard.html`

### service.py changes

Add the Sharpe helper before `_win_rate()`:

```python
def _compute_sharpe_from_trade_records(trades: list[TradeRecord]) -> float | None:
    returns = [
        t.pnl / (t.entry_price * t.quantity)
        for t in trades
        if t.entry_price > 0 and t.quantity > 0
    ]
    n = len(returns)
    if n < 2:
        return None
    mean_r = sum(returns) / n
    variance = sum((r - mean_r) ** 2 for r in returns) / (n - 1)
    std_r = variance ** 0.5
    if std_r == 0.0:
        return None
    return mean_r / std_r
```

In `load_metrics_snapshot()`, change `sharpe_ratio=None` to:
```python
sharpe_ratio=_compute_sharpe_from_trade_records(trades),
```

### dashboard.html changes

Replace the Sharpe panel (currently hardcoded `n/a`):
```html
<div>
  <p class="eyebrow">Sharpe</p>
  <p class="value">
    {% if metrics.sharpe_ratio is not none %}
      {{ "%.2f" | format(metrics.sharpe_ratio) }}
    {% else %}
      <span class="muted">n/a</span>
    {% endif %}
  </p>
</div>
```

### Tests for Task 3

**File**: `tests/unit/test_web_service.py`

```python
def test_load_metrics_snapshot_sharpe_computed_for_two_trades():
    trade1 = _make_closed_trade(entry_fill=100.0, exit_fill=110.0, qty=10)
    trade2 = _make_closed_trade(entry_fill=100.0, exit_fill=90.0, qty=10)
    stores = make_metrics_stores(trades=[trade1, trade2])
    snapshot = load_metrics_snapshot(settings=_make_settings(), **stores)
    assert snapshot.sharpe_ratio is not None


def test_load_metrics_snapshot_sharpe_none_for_single_trade():
    trade = _make_closed_trade(entry_fill=100.0, exit_fill=110.0, qty=10)
    stores = make_metrics_stores(trades=[trade])
    snapshot = load_metrics_snapshot(settings=_make_settings(), **stores)
    assert snapshot.sharpe_ratio is None


def test_compute_sharpe_identical_returns_none():
    """Zero std dev → None (no divide-by-zero)."""
    t1 = TradeRecord(symbol="A", entry_time=None, exit_time=None,
                     entry_price=100.0, exit_price=110.0, quantity=10, pnl=100.0, slippage=None)
    t2 = TradeRecord(symbol="B", entry_time=None, exit_time=None,
                     entry_price=100.0, exit_price=110.0, quantity=10, pnl=100.0, slippage=None)
    assert _compute_sharpe_from_trade_records([t1, t2]) is None
```

---

## Task 4 — Slippage column on dashboard

### Files to modify
- `src/alpaca_bot/web/templates/dashboard.html`

### Changes

**Replace the standalone Slippage panel** (lines 451–479, a dedicated panel showing only slippage) with a slippage column inline in the Trade Results table. Having both would show slippage data twice.

**Remove** the entire `<div class="panel">` block containing `<h2>Slippage</h2>` (lines 451–479).

**In the Trade Results table**, replace:
```html
<tr>
  <th>Symbol</th>
  <th>Qty</th>
  <th>Entry</th>
  <th>Exit</th>
  <th>P&amp;L</th>
</tr>
```
with:
```html
<tr>
  <th>Symbol</th>
  <th>Qty</th>
  <th>Entry</th>
  <th>Exit</th>
  <th>P&amp;L</th>
  <th>Slippage</th>
</tr>
```

Replace the `{% for trade in metrics.trades %}` row:
```html
{% for trade in metrics.trades %}
  <tr>
    <td class="mono">{{ trade.symbol }}</td>
    <td>{{ trade.quantity }}</td>
    <td>{{ format_price(trade.entry_price) }}</td>
    <td>{{ format_price(trade.exit_price) }}</td>
    <td class="{% if trade.pnl < 0 %}warn{% endif %}">
      {{ format_price(trade.pnl) }}
    </td>
    <td class="{% if trade.slippage is not none and trade.slippage < 0 %}warn{% endif %}">
      {% if trade.slippage is not none %}
        {{ "%.4f" | format(trade.slippage) }}
      {% else %}
        <span class="muted">—</span>
      {% endif %}
    </td>
  </tr>
{% else %}
  <tr><td colspan="6" class="muted">No closed trades today.</td></tr>
{% endfor %}
```

Note: `colspan` changes from 5 to 6. The standalone Slippage panel is removed entirely.

### Tests for Task 4

**File**: `tests/unit/test_web_app.py`

```python
def test_metrics_page_shows_slippage_column_header():
    app = create_app(settings=_make_settings(), connection=_make_connection(trades=[]))
    client = TestClient(app)
    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert "Slippage" in resp.text


def test_metrics_page_renders_adverse_slippage_warn_class():
    trade = _make_closed_trade_with_slippage(slippage=-0.05)
    app = create_app(settings=_make_settings(), connection=_make_connection(trades=[trade]))
    client = TestClient(app)
    resp = client.get("/metrics")
    assert "warn" in resp.text
```

---

## Task 5 — Trade fill notification

### Files to modify
- `src/alpaca_bot/runtime/trade_updates.py`

### Changes

1. **Remove `del settings`** (line 58) — `settings` is now used for `notify_slippage_threshold_pct`.

2. At the end of the entry-fill block (after `protective_stop_queued = True`/`else` block, before falling through to the audit event), add the fill notification:

```python
        if notifier is not None:
            fill_price = normalized.filled_avg_price  # already checked not None
            qty = normalized.filled_qty or matched_order.quantity
            slippage = (
                (matched_order.limit_price - fill_price)
                if matched_order.limit_price is not None
                else None
            )
            slippage_msg = ""
            if slippage is not None and slippage < -(fill_price * settings.notify_slippage_threshold_pct):
                slippage_msg = f"  ⚠ Adverse slippage: {slippage:.4f}"
            notifier.send(
                subject=f"Fill: {matched_order.symbol} {qty}@{fill_price}",
                body=(
                    f"{matched_order.symbol}: {qty} shares filled at {fill_price}"
                    f"{slippage_msg}"
                ),
            )
```

The existing `elif matched_order.intent_type in {"stop", "exit"}` block (lines 162–179) already has its own notification. The two blocks are separate `if/elif` branches — no interaction.

### Tests for Task 5

**File**: `tests/unit/test_trade_update_reconciliation.py`

```python
def test_entry_fill_sends_notification():
    """notifier.send() fires when an entry order is filled."""
    sent = []

    class FakeNotifier:
        def send(self, *, subject, body):
            sent.append({"subject": subject, "body": body})

    runtime = _make_runtime_with_entry_order(limit_price=100.0)
    apply_trade_update(
        settings=_make_settings(),
        runtime=runtime,
        update=_make_fill_update(symbol="AAPL", filled_avg_price=100.0, filled_qty=10),
        notifier=FakeNotifier(),
    )
    assert len(sent) == 1
    assert "Fill: AAPL" in sent[0]["subject"]


def test_entry_fill_adverse_slippage_alert():
    """⚠ warning included when slippage exceeds threshold."""
    sent = []

    class FakeNotifier:
        def send(self, *, subject, body):
            sent.append(body)

    runtime = _make_runtime_with_entry_order(limit_price=100.0)
    # fill at 99.0 → slippage = -1.0 → 1% > 0.5% threshold
    apply_trade_update(
        settings=_make_settings(notify_slippage_threshold_pct=0.005),
        runtime=runtime,
        update=_make_fill_update(symbol="AAPL", filled_avg_price=99.0, filled_qty=10),
        notifier=FakeNotifier(),
    )
    assert any("Adverse slippage" in body for body in sent)


def test_entry_fill_no_adverse_alert_within_threshold():
    """No ⚠ when slippage is within the threshold."""
    sent = []

    class FakeNotifier:
        def send(self, *, subject, body):
            sent.append(body)

    runtime = _make_runtime_with_entry_order(limit_price=100.0)
    # fill at 99.8 → slippage = -0.2 → 0.2% < 0.5% threshold
    apply_trade_update(
        settings=_make_settings(notify_slippage_threshold_pct=0.005),
        runtime=runtime,
        update=_make_fill_update(symbol="AAPL", filled_avg_price=99.8, filled_qty=10),
        notifier=FakeNotifier(),
    )
    assert all("Adverse slippage" not in body for body in sent)


def test_no_crash_when_notifier_is_none():
    runtime = _make_runtime_with_entry_order(limit_price=100.0)
    result = apply_trade_update(
        settings=_make_settings(),
        runtime=runtime,
        update=_make_fill_update(symbol="AAPL", filled_avg_price=100.0, filled_qty=10),
        notifier=None,
    )
    assert result["order_updated"] is True


def test_stop_fill_still_sends_notification_after_entry_fill_changes():
    """Regression: stop/exit fill notification fires exactly once after Task 5 changes."""
    sent = []

    class FakeNotifier:
        def send(self, *, subject, body):
            sent.append({"subject": subject})

    runtime = _make_runtime_with_stop_order(symbol="AAPL")
    apply_trade_update(
        settings=_make_settings(),
        runtime=runtime,
        update=_make_fill_update(
            symbol="AAPL",
            client_order_id=_stop_client_order_id("AAPL"),
            filled_avg_price=95.0,
            filled_qty=10,
            status="filled",
            intent_type="stop",
        ),
        notifier=FakeNotifier(),
    )
    assert len(sent) == 1
    assert "Position closed" in sent[0]["subject"]
```

---

## Task 6 — Live-mode dashboard banner

### Files to modify
- `src/alpaca_bot/web/templates/dashboard.html`

### Changes

**Add CSS** inside `<style>` block (after `.muted { color: var(--muted); }`):
```css
.live-banner {
  background: #8f3b2e;
  color: #fff8f6;
  text-align: center;
  padding: 0.6rem 1rem;
  font-weight: 700;
  letter-spacing: 0.04em;
  border-radius: 12px;
  margin-bottom: 1rem;
}
```

**Add banner** immediately after `<main>` opening tag, before `{% if operator_email %}`:
```html
<main>
  {% if settings.trading_mode.value == "live" %}
  <div class="live-banner">⚠ LIVE TRADING ACTIVE — real capital at risk</div>
  {% endif %}
  {% if operator_email %}
```

`settings.trading_mode.value` is used (consistent with existing template at line 141). `settings` is already in both the `dashboard` and `metrics` template contexts (confirmed in `app.py` lines 99–106 and 128–136).

### Tests for Task 6

**File**: `tests/unit/test_web_app.py`

```python
def test_dashboard_shows_live_banner_in_live_mode():
    settings = _make_settings(trading_mode="live", enable_live_trading=True)
    app = create_app(settings=settings, connection=_make_connection())
    client = TestClient(app)
    resp = client.get("/")
    assert resp.status_code == 200
    assert "LIVE TRADING ACTIVE" in resp.text


def test_dashboard_no_live_banner_in_paper_mode():
    settings = _make_settings(trading_mode="paper")
    app = create_app(settings=settings, connection=_make_connection())
    client = TestClient(app)
    resp = client.get("/")
    assert resp.status_code == 200
    assert "LIVE TRADING ACTIVE" not in resp.text
```

---

## Task 7 — Session staleness guard + thread `session_state` to `evaluate_cycle()`

### Files to modify
- `src/alpaca_bot/runtime/supervisor.py`

### Context

This task does two things:

1. **Staleness guard**: if `DailySessionState` stored in Postgres has a `session_date` from a previous session (e.g. supervisor restarted after midnight), treat it as `None` (fresh session).

2. **Thread `session_state` to `evaluate_cycle()`**: currently `session_state` is loaded in `_effective_trading_status()` but never passed to `run_cycle()` / `evaluate_cycle()`. This means the `flatten_complete` deduplication guard inside `evaluate_cycle()` is permanently dead code. This fix makes it live.

### Changes in `run_cycle_once()`

Load `session_state` explicitly at the top of `run_cycle_once()`, BEFORE calling `_effective_trading_status()`. Apply the staleness check at load time. Pass it down to `_cycle_runner`:

```python
def run_cycle_once(
    self,
    *,
    now: Callable[[], datetime] | None = None,
) -> SupervisorCycleReport:
    # ... existing connection check and recovery ...

    timestamp = _resolve_now(now)
    # ... broker calls, recovery_report ...

    session_date = _session_date(timestamp, self.settings)

    # Load and validate session state before computing entries_disabled.
    session_state: DailySessionState | None = None
    if (
        self.runtime.daily_session_state_store is not None
        and hasattr(self.runtime.daily_session_state_store, "load")
    ):
        session_state = self.runtime.daily_session_state_store.load(
            session_date=session_date,
            trading_mode=self.settings.trading_mode,
            strategy_version=self.settings.strategy_version,
        )
        # Staleness guard: discard state from a previous session.
        if session_state is not None and session_state.session_date != session_date:
            session_state = None

    # ... realized_pnl, daily_loss_limit_breached ...

    status = self._effective_trading_status(session_date=session_date)
    entries_disabled = (
        status in {TradingStatusValue.CLOSE_ONLY, TradingStatusValue.HALTED}
        or bool(recovery_report.mismatches)
        or daily_loss_limit_breached
        or (session_state is not None and session_state.entries_disabled)
    )

    # ... bars fetching ...

    cycle_result = self._cycle_runner(
        settings=self.settings,
        runtime=self.runtime,
        now=timestamp,
        equity=account.equity,
        intraday_bars_by_symbol=intraday_bars_by_symbol,
        daily_bars_by_symbol=daily_bars_by_symbol,
        open_positions=open_positions,
        working_order_symbols=working_order_symbols,
        traded_symbols_today=self._load_traded_symbols(session_date=session_date),
        entries_disabled=entries_disabled,
        flatten_all=daily_loss_limit_breached,
        session_state=session_state,
    )
```

Note: `_effective_trading_status()` also loads session_state internally. Now that `run_cycle_once()` loads it too, there is a minor double-read. This is acceptable (two identical fast Postgres reads per cycle). If optimizing is desired in the future, `_effective_trading_status()` could accept an optional `session_state` param — but that is not part of Phase 6.

The `entries_disabled` computation now incorporates `session_state.entries_disabled` directly, making it consistent whether the state comes from `_effective_trading_status()` or the freshly-loaded value (they should be the same).

### Tests for Task 7

**File**: `tests/unit/test_runtime_supervisor.py`

```python
def test_stale_session_state_ignored_on_new_day():
    """Stale session_state from yesterday is discarded; entries not disabled."""
    yesterday = date(2024, 1, 1)
    today = date(2024, 1, 2)
    stale_state = _make_session_state(
        session_date=yesterday,
        entries_disabled=True,
        flatten_complete=True,
    )
    captured = {}

    def fake_cycle_runner(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(intents=[])

    supervisor = _make_supervisor(
        cycle_runner=fake_cycle_runner,
        session_state=stale_state,
    )
    supervisor.run_cycle_once(now=lambda: _ts_for_date(today))
    # stale state must not propagate entries_disabled
    assert captured.get("session_state") is None
    assert supervisor_report.entries_disabled is False  # adjust to actual API


def test_same_day_session_state_used():
    """session_state from today is passed through to cycle runner."""
    today = date(2024, 1, 2)
    current_state = _make_session_state(
        session_date=today,
        entries_disabled=True,
        flatten_complete=False,
    )
    captured = {}

    def fake_cycle_runner(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(intents=[])

    supervisor = _make_supervisor(
        cycle_runner=fake_cycle_runner,
        session_state=current_state,
    )
    supervisor.run_cycle_once(now=lambda: _ts_for_date(today))
    assert captured.get("session_state") is current_state


def test_flatten_complete_prevents_duplicate_eod_exits():
    """When flatten_complete=True in session_state, evaluate_cycle emits no eod_flatten exits."""
    from datetime import date
    today = date(2024, 1, 2)
    done_state = _make_session_state(
        session_date=today,
        entries_disabled=True,
        flatten_complete=True,
    )
    settings = _make_settings()
    position = _make_open_position("AAPL")
    # Time is after flatten_time
    flat_ts = _dt_at(today, settings.flatten_time, offset_minutes=5)
    bars = [_make_bar("AAPL", ts=flat_ts, close=151.0)]
    result = evaluate_cycle(
        settings=settings,
        now=flat_ts,
        equity=100_000.0,
        intraday_bars_by_symbol={"AAPL": bars},
        daily_bars_by_symbol={},
        open_positions=[position],
        working_order_symbols=set(),
        traded_symbols_today=set(),
        entries_disabled=True,
        flatten_all=False,
        session_state=done_state,
    )
    assert result.intents == []
```

---

## Test Gate

After all tasks:

```bash
pytest tests/unit/ -q
```

Must be green. Key test files modified:

| File | Tasks |
|---|---|
| `tests/unit/test_cycle_engine.py` | 1, 2, 7 |
| `tests/unit/test_runtime_supervisor.py` | 1, 7 |
| `tests/unit/test_cycle_intent_execution.py` | 1 (duplicate EXIT guard) |
| `tests/unit/test_trade_update_reconciliation.py` | 5 |
| `tests/unit/test_web_service.py` | 3 |
| `tests/unit/test_web_app.py` | 4, 6 |

---

## Rollout Notes

- **No new migrations** — all changes are logic-only or template-only.
- New env vars `MAX_PORTFOLIO_EXPOSURE_PCT` (default 0.15) and `NOTIFY_SLIPPAGE_THRESHOLD_PCT` (default 0.005) have safe defaults; existing deployments need no env file changes.
- **EXIT intents are NOT pre-written to Postgres** — only ENTRY intents are pre-written in `run_cycle()`. If the supervisor crashes after `run_cycle()` returns but before `execute_cycle_intents()` completes an exit, startup recovery (`recover_startup_state()`) detects the position/order mismatch on next boot. This is the existing crash-recovery path.
- **Duplicate EXIT guard** (Task 1d) uses `ACTIVE_STOP_STATUSES` to check for existing active exit orders. An AuditEvent is written when a duplicate is skipped — operator-visible via the dashboard Event History.
- All changes are mode-agnostic — paper and live behave identically. The live banner is purely cosmetic.
