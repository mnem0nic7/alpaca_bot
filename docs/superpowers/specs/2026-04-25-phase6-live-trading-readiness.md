# Phase 6 — Live Trading Readiness: Operational Hardening

**Date**: 2026-04-25  
**Status**: Spec

---

## Problem Statement

Phases 1–5 produced a working paper-trading bot with a replay/backtest engine and parameter sweep. Before enabling live trading (`TRADING_MODE=live`) the following gaps must be closed:

1. **Financial safety gap**: daily loss limit only disables *new entries* — it does not close open positions. If the daily loss limit fires with 3 open positions, those positions can continue losing indefinitely until the flatten window.
2. **Portfolio exposure gap**: up to `max_open_positions × max_position_pct` of equity can be deployed simultaneously (e.g. 3 × 5% = 15%). There is no aggregate cap.
3. **Visibility gap**: live session Sharpe ratio is always `None` in `MetricsSnapshot`; slippage exists on `TradeRecord` but is not shown in the dashboard; no fill notification fires when a trade executes.
4. **Safety gate gap**: no dashboard warning when `TRADING_MODE=live`; no credential validation that live keys differ from paper keys.
5. **Session continuity gap**: if the supervisor crashes and restarts mid-day, `DailySessionState.entries_disabled` could be stale, and there is no automatic reset at market open on a new trading day.

---

## What Is In Scope

1. **Hard flatten on loss limit breach** — when `daily_loss_limit_breached`, emit EXIT intents for all open positions in the same cycle (not just disable entries). This converts the existing soft gate to a hard stop.
2. **Aggregate portfolio exposure cap** — new `MAX_PORTFOLIO_EXPOSURE_PCT` setting (default 0.15 = 15%); validated in `Settings.validate()`; enforced in `evaluate_cycle()` before emitting entry intents.
3. **Live session Sharpe ratio** — compute Sharpe from same-session `TradeRecord` list in `load_metrics_snapshot()` using the same `_compute_sharpe()` logic from Phase 5.
4. **Slippage column on dashboard** — surface `TradeRecord.slippage` in the trades table on the `/metrics` page.
5. **Trade fill notification** — send a notification when a position fill is recorded; include symbol, fill price, quantity, and slippage if available. Fire when `slippage < -NOTIFY_SLIPPAGE_THRESHOLD_PCT` (adverse slippage alert).
6. **Live-mode dashboard banner** — show a red `⚠ LIVE TRADING ACTIVE` banner on the dashboard when `settings.trading_mode == "live"`.
7. **Session state staleness guard** — in `run_cycle_once()`, detect when the current session date differs from the stored `DailySessionState.session_date` and treat it as a fresh session (entries not disabled, flatten not complete), rather than replaying stale state.

## What Is NOT In Scope

- Walk-forward validation for sweep (Phase 7).
- Multi-strategy router (Phase 7).
- ML signals (Phase 7+).
- Hot-reload of supervisor settings (architectural constraint: frozen dataclass + advisory lock).
- Automatic supervisor restart after loss limit (operator applies manually).
- Compliance logging or pre-signed execution records.

---

## Architecture

### Task 1 — Hard flatten on loss limit (engine change)

`evaluate_cycle()` currently receives `entries_disabled: bool`. We extend it with a new parameter `flatten_all: bool` that, when `True`, appends EXIT intents for every open position — identical to the EOD flatten path but triggered by the loss limit.

In `RuntimeSupervisor.run_cycle_once()`, pass `flatten_all=daily_loss_limit_breached` to `_cycle_runner`.

**Safety**: `flatten_all=True` does NOT bypass the HALTED status guard — if status is HALTED, intent execution is already skipped. The EXIT intents are written to Postgres regardless (audit trail), but `cycle_intent_executor` only dispatches when status is not HALTED.

**Re-entry prevention**: the existing `daily_loss_limit_breached` flag persists for the session (it's recomputed from `realized_pnl` each cycle), so after the hard flatten, the next cycle also sees `entries_disabled=True` and does not open new positions.

### Task 2 — Aggregate portfolio exposure cap

Add to `Settings`:
```
max_portfolio_exposure_pct: float = 0.15
```
Env var: `MAX_PORTFOLIO_EXPOSURE_PCT` (default 0.15).

In `evaluate_cycle()`, before emitting entry candidates:
```python
current_exposure = sum(p.entry_price * p.quantity for p in open_positions) / equity
available_exposure = settings.max_portfolio_exposure_pct - current_exposure
```
Skip entry if adding it would exceed `max_portfolio_exposure_pct`. Apply to each candidate in the sorted list greedily.

**Pure function**: no I/O required — all inputs available in existing parameters.

### Task 3 — Live session Sharpe

In `load_metrics_snapshot()`, after computing `trades`:
```python
from alpaca_bot.replay.report import _compute_sharpe
sharpe = _compute_sharpe_from_trade_records(trades)
```

Helper:
```python
def _compute_sharpe_from_trade_records(trades: list[TradeRecord]) -> float | None:
    n = len(trades)
    if n < 2:
        return None
    returns = [t.pnl / (t.entry_price * t.quantity) for t in trades
               if t.entry_price > 0 and t.quantity > 0]
    if len(returns) < 2:
        return None
    mean_r = sum(returns) / len(returns)
    variance = sum((r - mean_r) ** 2 for r in returns) / (len(returns) - 1)
    std_r = variance ** 0.5
    if std_r == 0.0:
        return None
    return mean_r / std_r
```

Note: this duplicates the formula from `replay/report.py` rather than importing it, because `web/service.py` operates on `TradeRecord` (live fills) not `ReplayTradeRecord` (replay events). The formula is 3 lines — not worth a shared utility module.

### Task 4 — Slippage on dashboard

`TradeRecord.slippage` already exists. Add a "Slippage" column to the trades table in `dashboard.html`:
- Green if `> 0` (favorable)
- Red if `< 0` (adverse)
- `—` if `None` (market orders or no limit recorded)

### Task 5 — Trade fill notification

In `RuntimeSupervisor`, the trade update stream calls `_handle_fill()`. Add notification on fill:
```python
if self._notifier is not None:
    slippage_msg = ""
    if fill.slippage is not None and fill.slippage < -(settings.notify_slippage_threshold_pct * fill.fill_price):
        slippage_msg = f"  ⚠ Adverse slippage: {fill.slippage:.3f}"
    self._notifier.send(
        subject=f"Fill: {fill.symbol} {fill.quantity}@{fill.fill_price}",
        body=f"{fill.symbol}: {fill.quantity} shares filled at {fill.fill_price}{slippage_msg}",
    )
```

New setting: `NOTIFY_SLIPPAGE_THRESHOLD_PCT` (default 0.005 = 0.5%). Only fires notification when adverse slippage exceeds threshold. Fill notification always fires (regardless of slippage).

### Task 6 — Live-mode dashboard banner

In `dashboard.html`, add a conditional banner:
```html
{% if settings.trading_mode == "live" %}
<div class="live-banner">⚠ LIVE TRADING ACTIVE — real capital at risk</div>
{% endif %}
```

`settings` is already available in the template context (passed via `app.state.settings`). Add the banner just below `<main>`.

The `settings` object needs to be passed into both template render calls (`dashboard` and `metrics` routes). Check whether it's already in context — if not, add it.

### Task 7 — Session staleness guard

In `RuntimeSupervisor.run_cycle_once()`, after loading `session_state`:
```python
current_session_date = _session_date(timestamp, self.settings)
if session_state is not None and session_state.session_date != current_session_date:
    # Stale state from a previous session — treat as fresh
    session_state = None
```

This ensures that if the supervisor was running yesterday and is restarted today (or crashes at midnight), it doesn't carry over `flatten_complete=True` or `entries_disabled=True` from the previous session.

---

## New Environment Variables

| Var | Default | Description |
|-----|---------|-------------|
| `MAX_PORTFOLIO_EXPOSURE_PCT` | `0.15` | Max aggregate notional / equity |
| `NOTIFY_SLIPPAGE_THRESHOLD_PCT` | `0.005` | Adverse slippage fraction that triggers alert |

Both validated in `Settings.validate()`: must be positive fractions ≤ 1.0.

---

## Migration

No new tables. All changes are logic-only or template-only.

---

## Safety Analysis

- **Hard flatten path**: EXIT intents written to Postgres before broker call — consistent with the existing two-phase pattern. Crash after write but before dispatch: startup recovery will re-dispatch on next boot.
- **Aggregate cap**: pure computation in `evaluate_cycle()` — no I/O, no new state. Cannot cause spurious orders; only prevents some from being emitted.
- **Session staleness guard**: sets `session_state = None`, which causes `evaluate_cycle()` to use `flatten_complete=False` — the conservative safe default. Worst case: a position that was already flattened gets an extra EXIT intent emitted; the cycle_intent_executor deduplicates via `client_order_id`.
- **`ENABLE_LIVE_TRADING` gate**: unchanged — none of these changes touch the live trading gate.
- **Paper vs. live**: all seven changes are mode-agnostic. The live banner is purely cosmetic. The hard flatten and aggregate cap improve safety in both modes.
