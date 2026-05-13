# Loss Limit Fixes — Re-fire Flag and External Short Exclusion

**Date:** 2026-05-13  
**Status:** Approved

## Problem

Two separate bugs degraded the daily loss limit system on 2026-05-13:

**Bug 1 — Re-fire noise:**  
`daily_loss_limit_breached` fires on every supervisor restart for the current session when
`entries_disabled=True` was already persisted to Postgres. The `_loss_limit_alerted` set is
in-memory only and cleared on startup. On the first cycle after a restart, `_loss_limit_fired`
contains `session_date` (loaded from DB) but `_loss_limit_alerted` does not, so the event fires
again — with the CURRENT `total_pnl`, which may be positive if positions recovered. Six of seven
events today showed positive P&L for this reason.

**Bug 2 — External shorts inflate the loss measurement:**  
The daily loss limit computes `total_pnl = account.equity - baseline_equity`. Account equity
is a broker-wide number that includes unrealized P&L from all positions, including the eight
externally-created short positions (`strategy_name in ('short_option', 'short_equity')`). At
market open on a bullish gap-up these shorts lost $1,773 — more than the $972 daily limit —
permanently blocking all new entries for the day before the bot could place a single trade.

## Scope

1. Add `re_fire: bool` to the `daily_loss_limit_breached` audit payload.
2. Exclude external short position unrealized P&L from the daily loss limit calculation.

---

## Design

### Fix 1: Re-fire flag

**Approach:** Track which session dates had `_loss_limit_fired` populated from Postgres (vs.
triggered by a real breach during the current process lifetime). Expose this as `re_fire` in
the audit payload.

**`runtime/supervisor.py`**

Add one instance variable in `__init__`:

```python
# Dates where _loss_limit_fired was seeded from persisted DB state at startup.
# Used to mark audit events as re-notifications, not new breaches.
self._loss_limit_loaded_from_db: set[date] = set()
```

In the equity-baseline-loading branch (the `if persisted.entries_disabled:` block, currently
line 346–347), also populate `_loss_limit_loaded_from_db`:

```python
if persisted.entries_disabled:
    self._loss_limit_fired.add(session_date)
    self._loss_limit_loaded_from_db.add(session_date)
```

In the audit event payload (the `daily_loss_limit_breached` event):

```python
payload={
    "realized_pnl": realized_pnl,
    "total_pnl": adjusted_pnl,        # see Fix 2
    "limit": loss_limit,
    "re_fire": session_date in self._loss_limit_loaded_from_db,
    "timestamp": timestamp.isoformat(),
},
```

`_loss_limit_loaded_from_db` is never cleared between cycles. On the first restart cycle it
contains `session_date`, so `re_fire: True`. If the process runs past midnight without a restart,
the new session date is never added to this set, so all genuine breaches get `re_fire: False`.

---

### Fix 2: External short exclusion

**Objective:** Measure only the intraday P&L change attributable to the bot's own positions when
evaluating the daily loss limit. External short positions should be factored out.

#### Data changes

**`execution/alpaca.py` — `BrokerPosition`**

Add field:
```python
unrealized_pl: float | None = None
```

Populate in `AlpacaBroker.list_positions()`:
```python
BrokerPosition(
    symbol=...,
    quantity=...,
    entry_price=...,
    market_value=...,
    unrealized_pl=float(position.unrealized_pl)
    if getattr(position, "unrealized_pl", None) is not None
    else None,
)
```

Alpaca's `unrealized_pl` is already in USD, already incorporates the ×100 options multiplier,
and works correctly for both long and short positions. Using it avoids replicating the
equity-vs-option multiplier logic.

**`storage/models.py` — `DailySessionState`**

Add field:
```python
external_upnl_baseline: float | None = None
```

This stores the sum of `unrealized_pl` for all broker positions with `quantity < 0` at the
moment the equity baseline was first recorded for this session. Default `None` (treated as
`0.0` on first use — see fallback in supervisor below).

**`migrations/021_add_external_upnl_baseline.sql`**

```sql
ALTER TABLE daily_session_state ADD COLUMN external_upnl_baseline REAL;
```

Existing rows receive `NULL`; treated as `0.0` on read (safe fallback — any re-fire event on a
day with a NULL baseline will emit `re_fire: True` so the signal is still interpretable).

#### Storage layer (`storage/repositories.py`)

`DailySessionStateStore.save()`: add `external_upnl_baseline` to the INSERT column list,
VALUES tuple, and the `ON CONFLICT DO UPDATE SET` clause (with
`COALESCE(EXCLUDED.external_upnl_baseline, daily_session_state.external_upnl_baseline)` so
subsequent saves that pass `None` do not overwrite a stored value).

`DailySessionStateStore.load()`: add `external_upnl_baseline` to the SELECT list and populate
`DailySessionState.external_upnl_baseline = float(row[10]) if row[10] is not None else None`.
Row index shifts: previous `updated_at` was at `row[9]`; it moves to `row[10]`... 

Wait — `external_upnl_baseline` must be appended at the end to avoid shifting existing column
indices. The SELECT order becomes:
`session_date [0], trading_mode [1], strategy_version [2], strategy_name [3],
entries_disabled [4], flatten_complete [5], last_reconciled_at [6], notes [7],
equity_baseline [8], updated_at [9], external_upnl_baseline [10]`.

Same reordering in `list_by_session()`.

#### Supervisor logic (`runtime/supervisor.py`)

**New instance variable in `__init__`:**

```python
# External short unrealized P&L at the moment the equity baseline was set.
# Keyed by session_date. Used to neutralise external-short intraday swings
# from the loss-limit calculation.
self._session_external_upnl_baseline: dict[date, float] = {}
```

**Helper (module-level or static):**

```python
def _external_short_upnl(broker_positions: list[BrokerPosition]) -> float:
    return sum(
        bp.unrealized_pl
        for bp in broker_positions
        if bp.quantity < 0 and bp.unrealized_pl is not None
    )
```

All broker positions with `quantity < 0` are externally-created short positions; the bot never
opens short equity positions itself (all bear-strategy entries use long put options).

**When setting the equity baseline** (the `else` branch at lines 348–361):

After `self._session_equity_baseline[session_date] = account.equity`, also record:

```python
_ext_upnl = _external_short_upnl(broker_open_positions)
self._session_external_upnl_baseline[session_date] = _ext_upnl
self._save_session_state(
    DailySessionState(
        ...,
        equity_baseline=account.equity,
        external_upnl_baseline=_ext_upnl,
        ...
    )
)
```

**When loading the equity baseline from DB on restart** (the `if persisted is not None` branch
at lines 344–347):

After loading `equity_baseline`, also load:

```python
if persisted.external_upnl_baseline is not None:
    self._session_external_upnl_baseline[session_date] = persisted.external_upnl_baseline
```

**Loss limit evaluation (replacing line 433):**

```python
external_upnl_now = _external_short_upnl(broker_open_positions)
external_upnl_baseline_val = self._session_external_upnl_baseline.get(
    session_date, external_upnl_now
)
# Exclude the intraday change in external short P&L from the loss measurement.
# If baseline is unavailable (NULL in DB from before this fix), the fallback
# external_upnl_now == external_upnl_baseline_val means zero adjustment, which
# preserves pre-fix behaviour.
adjusted_pnl = (account.equity - baseline_equity) - (
    external_upnl_now - external_upnl_baseline_val
)
if adjusted_pnl < -loss_limit:
    self._loss_limit_fired.add(session_date)
```

Replace all subsequent references to `total_pnl` in the loss limit block with `adjusted_pnl`
(the breach condition, the extended-session check, and the audit payload).

The existing `total_pnl` variable can be kept with its current computation for the
`_maybe_send_intraday_digest` call at line 519 — the digest should reflect real total equity
change, not the adjusted figure.

---

## Safety Properties

**No new broker calls.** `broker_open_positions` is already fetched at the top of
`run_cycle_once()` (line 253) before the loss limit check. `_external_short_upnl` reads from
the same list — zero extra I/O.

**Graceful fallback.** If `external_upnl_baseline` is `NULL` in DB (rows written before this
migration), the `dict.get(session_date, external_upnl_now)` fallback sets baseline == now,
making the adjustment zero. This preserves pre-fix behaviour for existing rows rather than
causing a spurious breach or block on the first restart after deploy.

**Option multiplier correctness.** Alpaca's `unrealized_pl` for option positions already
incorporates the ×100 contract multiplier. We do not need to special-case OCC symbols.

**Re-fire event still fires.** `_loss_limit_alerted` is still populated on the re-fire,
preventing a second re-fire on subsequent cycles within the same process lifetime.

**`_loss_limit_loaded_from_db` is additive.** It is populated only once per process lifetime
per session date, never cleared. It cannot produce false negatives after the first cycle.

---

## Out of Scope

- Suppressing re-fire events entirely (they remain useful for monitoring that the loss limit
  persisted across a restart).
- Changing the `_maybe_send_intraday_digest` / notifier path — those use real total equity, not
  adjusted P&L.
- Persisting `_loss_limit_alerted` to DB (would require an additional column/row and adds
  complexity; the `re_fire` flag gives enough observability).
