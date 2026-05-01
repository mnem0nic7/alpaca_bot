# Spec: Loss Limit Enhancements

**Date:** 2026-05-01

---

## Problem

Two weaknesses in the current daily loss limit system:

### 1. Loss limit is not sticky after a breach

`daily_loss_limit_breached` is re-computed each cycle as `account.equity - baseline_equity < -loss_limit`. Once positions are exited after a breach, equity can recover (e.g., stops fill at better prices, or positions were already flat when the limit fired). On the next cycle the condition becomes False, re-enabling entries for the rest of the day.

The `_loss_limit_alerted` set only prevents duplicate audit events — it does not gate `entries_disabled` or `flatten_all`. The `DailySessionState.entries_disabled` flag is only persisted when flatten intents are submitted AND succeed, leaving a gap when there are no open positions to flatten.

**Consequence:** A session that legitimately breached the loss limit may silently re-enable entries after a partial equity recovery, defeating the purpose of the circuit breaker.

### 2. No per-symbol daily loss limit

A single `DAILY_LOSS_LIMIT_PCT` governs the whole portfolio. One symbol with repeated bad fills could consume most of the daily loss budget before the portfolio-level circuit breaker triggers.

---

## Scope

### Fix 1: Sticky daily loss limit

Make the breach condition sticky in memory and in Postgres.

**In-memory:**  
Add `_loss_limit_fired: set[date]` to `RuntimeSupervisor.__init__`. Once `account.equity - baseline_equity < -loss_limit` evaluates True for a given `session_date`, add that date to `_loss_limit_fired`. Replace the live condition with:

```python
live_breach = (account.equity - baseline_equity) < -loss_limit
if live_breach:
    self._loss_limit_fired.add(session_date)
daily_loss_limit_breached = session_date in self._loss_limit_fired
```

This means: once the limit fires, `daily_loss_limit_breached` remains True for the rest of the day even if equity recovers.

**Persisted marker (restart recovery):**  
Reuse the existing `_equity` `DailySessionState` row to carry the sticky flag. When the loss limit fires, update the `_equity` row with `entries_disabled=True` (in addition to the baseline equity). On session init where the `_equity` row is loaded, if `entries_disabled=True`, pre-populate `_loss_limit_fired`.

```python
# On first-cycle init (existing code path):
persisted = self._load_session_state(session_date=session_date, strategy_name="_equity")
if persisted is not None:
    if persisted.equity_baseline is not None:
        self._session_equity_baseline[session_date] = persisted.equity_baseline
    if persisted.entries_disabled:                      # NEW
        self._loss_limit_fired.add(session_date)        # NEW
```

No new DB columns, tables, or migrations needed — `entries_disabled` already exists on `DailySessionState`.

**Audit trail:** The existing `daily_loss_limit_breached` audit event is already emitted once (guarded by `_loss_limit_alerted`). No new event type needed. The `_save_session_state` call updates Postgres, which already has an audit-ready row.

**Flatten-all:** `flatten_all=daily_loss_limit_breached` (now using the sticky flag) correctly flattens any positions that happen to be open on the cycle where the limit fires. After that cycle, positions are gone and flatten_all=True on subsequent cycles is harmless (no positions to flatten).

---

### Feature 2: Per-symbol daily loss limit

**What:** A new opt-in circuit breaker that tracks each symbol's realized PnL for the current session independently. When a symbol's realized losses exceed `PER_SYMBOL_LOSS_LIMIT_PCT × baseline_equity`, new entries for that symbol are blocked for the rest of the session.

**What it does NOT do:** Force-exit open positions in the breached symbol. The existing broker-held stop provides a loss floor; adding a force-exit here could race with the stop. Blocking new entries is the primary goal.

**Controlled by:** `PER_SYMBOL_LOSS_LIMIT_PCT` env var → `Settings.per_symbol_loss_limit_pct: float = 0.0`. Default `0.0` = disabled.

**Per-symbol PnL source:** Realized PnL only (closed trades). Unrealized PnL is not tracked per-symbol — computing per-symbol unrealized would require per-symbol position-level mark-to-market, which is complex and unnecessary. Realized PnL is already available via the `orders` table and never un-does itself intraday (closed trades stay closed).

**New repository method:** `OrderStore.daily_realized_pnl_by_symbol()` returns `dict[str, float]`. Shares the same correlated-subquery logic as `daily_realized_pnl()` but groups results by symbol rather than summing across all.

**Supervisor changes:** In `run_cycle_once()`, after computing `baseline_equity` and before the strategy loop:
1. Query `daily_realized_pnl_by_symbol()` when the feature is enabled
2. For each symbol whose realized PnL < `-(per_symbol_loss_limit_pct × baseline_equity)`:
   - Add to `working_order_symbols` (blocks entries across all strategies for this symbol this cycle)
   - Emit `per_symbol_loss_limit_breached` audit event (once per symbol per day, guarded by `_per_symbol_limit_alerted: dict[date, set[str]]`)
3. Notify via `_notifier` once per symbol per day

**Restart recovery:** No special recovery needed. The check is computed fresh from the DB each cycle, so symbols that exceeded their limit will be blocked again on the first cycle after restart.

---

## Settings

Two new fields added to `Settings`:

```python
per_symbol_loss_limit_pct: float = 0.0
```

`from_env()` addition:
```python
per_symbol_loss_limit_pct=float(values.get("PER_SYMBOL_LOSS_LIMIT_PCT", "0.0")),
```

`validate()` addition:
```python
if self.per_symbol_loss_limit_pct < 0:
    raise ValueError("PER_SYMBOL_LOSS_LIMIT_PCT must be >= 0")
if self.per_symbol_loss_limit_pct >= 1.0:
    raise ValueError("PER_SYMBOL_LOSS_LIMIT_PCT must be < 1.0")
```

The sticky loss limit fix requires no new settings — it is always-on behavior that corrects a logic gap.

---

## Audit Events

| Event type | When emitted | Payload |
|---|---|---|
| `daily_loss_limit_breached` | Unchanged — once per day when portfolio loss limit fires | `realized_pnl, total_pnl, limit, timestamp` |
| `per_symbol_loss_limit_breached` | Once per (symbol, session_date) when per-symbol limit fires | `symbol, realized_pnl, limit, timestamp` |

---

## Files to Modify

| File | Change |
|---|---|
| `src/alpaca_bot/config/__init__.py` | Add `per_symbol_loss_limit_pct` field, `from_env()`, and `validate()` |
| `src/alpaca_bot/storage/repositories.py` | Add `OrderStore.daily_realized_pnl_by_symbol()` |
| `src/alpaca_bot/runtime/supervisor.py` | (1) Sticky loss limit: `_loss_limit_fired` set, update init + cycle logic; (2) Per-symbol: `_per_symbol_limit_alerted` dict, call new repo method, block symbols |

## Files to Create

| File | Contents |
|---|---|
| `tests/unit/test_loss_limit_enhancements.py` | All tests for both features |

## Files Unchanged

- `src/alpaca_bot/core/engine.py` — `evaluate_cycle()` remains pure; all new logic is in the supervisor
- All strategy files
- All migration files — no schema changes needed

---

## Safety Properties

- **Default safe:** `per_symbol_loss_limit_pct=0.0` → feature disabled; no behavioral change for existing deployments. Sticky loss limit fix is always-on but only changes behavior when equity recovers after a breach (previously a gap, now correctly locked).
- **Pure engine boundary preserved:** All new logic lives in `supervisor.py`, not `evaluate_cycle()`.
- **No new I/O in engine:** `daily_realized_pnl_by_symbol()` is called from the supervisor, same as the existing `daily_realized_pnl()` call.
- **Intent/dispatch separation preserved:** Symbol blocking is implemented by adding to `working_order_symbols`, which prevents ENTRY intents from being generated — no bypass of the pending-submit queue.
- **Fail-safe on missing entry fill:** `daily_realized_pnl_by_symbol()` uses the same fail-safe as `daily_realized_pnl()`: if an exit row has no correlated entry fill, the full exit notional is treated as a loss.
- **No concurrent cycle risk:** Advisory lock prevents two supervisors running simultaneously. Sticky loss limit uses an in-memory set per process + Postgres persistence — no CAS or optimistic lock needed.
