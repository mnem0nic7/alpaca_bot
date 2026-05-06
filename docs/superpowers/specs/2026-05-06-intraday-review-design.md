# Intra-Day Trade Review & Session Improvement Design

**Goal:** Review today's trades continuously during the trading session and automatically
improve outcomes by (a) sending periodic intra-day performance digests via the existing
`Notifier` channel, and (b) disabling entries after a configurable number of consecutive
losing trades — giving the strategy a circuit-breaker before the daily loss limit fires.

**Architecture:** Two additions to the existing supervisor loop, both implemented as pure
functions that are called from `run_cycle_once()`. No new tables, no schema changes.
Session-level state is tracked using the same `dict[date, X]` in-process pattern the
supervisor already uses for loss limits and daily summaries.

**Tech Stack:** Python, pytest, existing `Notifier`, existing `OrderStore.list_closed_trades`.

---

## Context: What Already Exists

- `build_daily_summary()` fires once at market close — no intra-day notification exists.
- `daily_loss_limit_pct` halts trading on absolute dollar loss — no strategy quality gate exists.
- `list_closed_trades()` returns all closed round-trips for the session — the data needed for
  consecutive-loss computation is already fetched each cycle via `daily_realized_pnl()`.
- `BacktestReport.max_consecutive_losses` computes all-time streak from historical data — not
  usable for live intra-day assessment because it doesn't surface the *current* trailing streak.

---

## Feature 1: Intra-Day Performance Digest

A brief performance summary sent via `Notifier` at configurable intervals during the
REGULAR session. Gives the operator awareness of intra-day progress without switching
to the dashboard.

### Content
```
Intra-day digest — 2026-05-06 14:30 ET [paper]
Session: 2026-05-06  Cycle: 47/60

P&L: $142.80  |  Trades: 8  |  Win rate: 62.5%  (5W / 3L)
Loss limit headroom: $318.20 of $461.00 remaining

Open positions: 2 (AAPL x10 @ 182.50, MSFT x5 @ 415.20)
```

### Trigger Logic
- Tracked via `self._session_cycle_count: dict[date, int]` — incremented each REGULAR cycle.
- Tracked via `self._digest_sent_count: dict[date, int]` — how many digests sent today.
- Send when `session_cycle_count % INTRADAY_DIGEST_INTERVAL_CYCLES == 0` and
  `session_cycle_count > 0`.
- Guard: only send during REGULAR session hours (not PRE_MARKET or AFTER_HOURS).
- Guard: do not send if today's session has zero closed trades yet (no signal = no noise).
- Appends `intraday_digest_sent` audit event with `{cycle, digest_num, pnl, trades, win_rate}`.

### New Setting
`INTRADAY_DIGEST_INTERVAL_CYCLES` — int, default `60`.
At the default 60-second supervisor poll, this sends a digest approximately every 60 minutes.
Set to `0` to disable digests entirely.

---

## Feature 2: Consecutive-Loss Entry Gate

After N consecutive losing trades, automatically set `entries_disabled=True` for the remainder
of the session. This is a strategy quality circuit-breaker, distinct from the dollar-based
daily loss limit.

### What Counts as a Loss
A trade is a loss when `exit_fill < entry_fill` (same definition used by `_is_win()` in
`daily_summary.py`). Trades with missing fill prices are ignored (not counted as win or loss).

### Streak Computation
Each REGULAR cycle, after `list_closed_trades()` is called, compute the current trailing
consecutive loss streak:
- Sort trades by `exit_time` ascending.
- Walk backwards from the most recent trade, counting consecutive losses.
- Stop at the first win.
- Result: current streak length (0 if no losses, or if the most recent trade was a win).

This is restart-safe: it re-derives the streak from the full trade list on every cycle.

### Gate Trigger
When `consecutive_losses >= INTRADAY_CONSECUTIVE_LOSS_GATE` and the gate has not already
fired today:
1. Set `entries_disabled=True` in `DailySessionState` for every active strategy in the
   session (same pattern as daily loss limit breach).
2. Append `intraday_consecutive_loss_gate` audit event with
   `{consecutive_losses, threshold, timestamp}`.
3. Send notification via `Notifier`:
   `"Entries disabled — {N} consecutive losses this session. Resume manually to re-enable."`

Guard: once fired, the gate does NOT re-enable entries on a subsequent win. Re-enabling
requires operator action via `alpaca-bot-admin resume` (same as any other entries-disabled
condition). This prevents whipsawing.

Guard: gate does not fire during PRE_MARKET or AFTER_HOURS — only REGULAR session.

Guard: gate does not fire if `INTRADAY_CONSECUTIVE_LOSS_GATE` is `0` (disabled by default
to avoid surprising operators on first deploy).

### New Setting
`INTRADAY_CONSECUTIVE_LOSS_GATE` — int, default `0` (disabled).
Set to `3` to disable entries after 3 consecutive losses. Set to `0` to disable the gate.

---

## Files to Create/Modify

| File | Change |
|---|---|
| `src/alpaca_bot/config/__init__.py` | Add `intraday_digest_interval_cycles: int` and `intraday_consecutive_loss_gate: int` |
| `src/alpaca_bot/runtime/daily_summary.py` | Add `build_intraday_digest()` pure function and `trailing_consecutive_losses()` pure function |
| `src/alpaca_bot/runtime/supervisor.py` | Add `_session_cycle_count`, `_digest_sent_count`, `_consecutive_loss_gate_fired` instance vars; call `_maybe_send_intraday_digest()` and `_maybe_fire_consecutive_loss_gate()` from `run_cycle_once()` |
| `tests/unit/test_intraday_review.py` | New test file: tests for both pure functions and supervisor integration via DI callables |

---

## Integration Point in supervisor.py

Both features are called in `run_cycle_once()` after `daily_realized_pnl()` is computed
and before strategy cycles run. This ensures:
- The digest has accurate PnL and trade data for the current cycle.
- The consecutive-loss gate can prevent new entries before `evaluate_cycle()` is called.

```python
# After existing loss-limit block (~line 394):
self._session_cycle_count.setdefault(session_date, 0)
self._session_cycle_count[session_date] += 1

closed_trades = self.runtime.order_store.list_closed_trades(
    trading_mode=..., strategy_version=..., session_date=..., market_timezone=...
)
cl_streak = trailing_consecutive_losses(closed_trades)

if (
    not daily_loss_limit_breached
    and session_type is SessionType.REGULAR
):
    self._maybe_fire_consecutive_loss_gate(
        session_date=session_date,
        consecutive_losses=cl_streak,
        timestamp=timestamp,
    )
    self._maybe_send_intraday_digest(
        session_date=session_date,
        account=account,
        baseline_equity=baseline_equity,
        closed_trades=closed_trades,
        timestamp=timestamp,
        consecutive_losses=cl_streak,
    )

entries_disabled = (
    ...existing logic...
    or session_date in self._consecutive_loss_gate_fired
)
```

---

## Pure Functions

### `trailing_consecutive_losses(trades: list[dict]) -> int`
Location: `runtime/daily_summary.py`

```python
def trailing_consecutive_losses(trades: list[dict]) -> int:
    """Return the current trailing consecutive-loss count from today's closed trades.

    Trades without fill prices are skipped. Returns 0 if no trades or if the
    most-recent trade was a win.
    """
    scored = [t for t in trades if t.get("entry_fill") and t.get("exit_fill")]
    scored.sort(key=lambda t: t.get("exit_time") or "")
    streak = 0
    for t in reversed(scored):
        if not _is_win(t):
            streak += 1
        else:
            break
    return streak
```

### `build_intraday_digest(...)  -> tuple[str, str]`
Location: `runtime/daily_summary.py`

Returns `(subject, body)`. Pure — no I/O. Follows the `build_daily_summary()` pattern.

---

## Error Handling

- If `list_closed_trades()` raises: log exception, skip gate and digest for this cycle.
  Do NOT propagate — a digest failure must not abort the trading cycle.
- If `Notifier.send()` raises: log exception, continue. Same guard as existing loss-limit alert.
- If `DailySessionState` write fails: log exception. The gate is tracked in-memory via
  `_consecutive_loss_gate_fired`, so the in-process guard still holds even if the DB write fails.

---

## Testing Strategy

**Pure function tests** (no I/O, pure unit tests):
- `trailing_consecutive_losses` with: empty list, all wins, all losses, mix, win after losses,
  trades missing fill prices.
- `build_intraday_digest` with: zero trades, trades present, loss limit headroom calculation.

**Supervisor integration tests** (via existing DI fake callables):
- Gate fires when threshold is met (consecutive_losses >= N).
- Gate does not fire twice for same session.
- Gate does not fire when `INTRADAY_CONSECUTIVE_LOSS_GATE=0`.
- Gate does not fire during PRE_MARKET/AFTER_HOURS.
- Digest sends at correct cycle interval.
- Digest does not send with zero trades.
- `entries_disabled` is True after gate fires.

---

## Scope Boundaries

**In scope:**
- Intra-day notification digest (hourly, configurable)
- Consecutive-loss entry gate (configurable threshold, default off)
- Both gated to REGULAR session only

**Out of scope:**
- Win-rate-based gate (replaced by consecutive-loss gate, which is safer and more intuitive)
- Automated parameter tuning based on intra-day performance
- Per-strategy consecutive loss tracking (gate applies session-wide)
- PRE_MARKET or AFTER_HOURS gate evaluation
