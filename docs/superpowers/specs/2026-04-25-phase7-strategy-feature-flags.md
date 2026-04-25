# Phase 7 — Strategy Feature Flags

**Date**: 2026-04-25  
**Status**: Spec

---

## Problem Statement

The supervisor's entry logic is hard-wired to the single breakout strategy via the default `signal_evaluator` parameter in `evaluate_cycle()`. There is no way to disable a strategy at runtime without restarting the supervisor (which re-reads `Settings` from env vars). Operators cannot react quickly to deteriorating market conditions by killing entries for a specific strategy from the dashboard.

---

## Goal

Allow the operator to enable or disable individual trading strategies from the web dashboard without restarting the supervisor. The effect takes hold within one cycle poll interval (≤ 60 s). Disabling a strategy blocks new entries from that strategy only — open position management (stop-trail updates, EOD flatten, loss-limit flatten) is **never gated on strategy flags**.

---

## What Is In Scope

1. **`strategy_flags` Postgres table** — stores `(strategy_name, trading_mode, strategy_version, enabled, updated_at)`. A missing row means `enabled = true` (safe, backward-compatible default).
2. **`StrategyFlag` model + `StrategyFlagStore`** — standard frozen dataclass + upsert/load/list-all repository, following the existing store pattern.
3. **`STRATEGY_REGISTRY`** in `strategy/__init__.py` — a `dict[str, StrategySignalEvaluator]` mapping canonical name → evaluator callable. Currently just `{"breakout": evaluate_breakout_signal}`. This is the authoritative list of what appears in the UI.
4. **Supervisor integration** — `run_cycle_once()` resolves the active signal evaluator from the flag store each cycle and passes it to `_cycle_runner`. If no enabled strategy is found, a no-op evaluator is used (no entries, existing positions still managed).
5. **`run_cycle()` receives `signal_evaluator` param** and passes it to `evaluate_cycle()`, completing the threading. `evaluate_cycle()` is unchanged.
6. **`RuntimeContext` gains `strategy_flag_store`** — wired in `bootstrap_runtime()` and rewired in `reconnect_runtime_connection()`.
7. **Dashboard toggle UI** — a "Strategies" panel on `/` showing each registered strategy with its current flag state and an Enable/Disable button.
8. **`POST /strategies/{strategy_name}/toggle`** — the first mutable web endpoint. Auth-protected (mirrors dashboard gate). Flips the flag, writes a `strategy_flag_changed` audit event, redirects to `/` (PRG pattern).
9. **Migration `005_add_strategy_flags.sql`** + down migration.

## What Is NOT In Scope

- Multi-strategy routing (running multiple strategies simultaneously per cycle) — Phase 8.
- Per-symbol strategy assignment.
- Strategy parameter editing via the dashboard.
- Hot-reload of `Settings` env vars.
- A second strategy implementation — the registry ships with `breakout` only.

---

## Architecture

### Table

```sql
CREATE TABLE IF NOT EXISTS strategy_flags (
    strategy_name    TEXT NOT NULL,
    trading_mode     TEXT NOT NULL,
    strategy_version TEXT NOT NULL,
    enabled          BOOLEAN NOT NULL DEFAULT TRUE,
    updated_at       TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (strategy_name, trading_mode, strategy_version),
    CHECK (trading_mode IN ('paper', 'live'))
);
```

No seed rows are inserted in the migration. The store `load()` returns `None` for an unknown key; callers treat `None` as `enabled=True`. Existing deployments therefore see no behavioral change after applying the migration.

### Model

```python
@dataclass(frozen=True)
class StrategyFlag:
    strategy_name: str
    trading_mode: TradingMode
    strategy_version: str
    enabled: bool
    updated_at: datetime
```

### Store

```python
class StrategyFlagStore:
    def save(self, flag: StrategyFlag) -> None: ...      # upsert
    def load(self, *, strategy_name, trading_mode, strategy_version) -> StrategyFlag | None: ...
    def list_all(self, *, trading_mode, strategy_version) -> list[StrategyFlag]: ...
```

### Strategy registry

```python
# strategy/__init__.py  (addition)
from alpaca_bot.strategy.breakout import evaluate_breakout_signal

STRATEGY_REGISTRY: dict[str, StrategySignalEvaluator] = {
    "breakout": evaluate_breakout_signal,
}
```

### Supervisor — resolving the evaluator

New private method on `RuntimeSupervisor`:

```python
def _resolve_signal_evaluator(self) -> StrategySignalEvaluator:
    from alpaca_bot.strategy import STRATEGY_REGISTRY
    from alpaca_bot.strategy.breakout import evaluate_breakout_signal as _default

    store = getattr(self.runtime, "strategy_flag_store", None)
    if store is None:
        # No store available (test shim or pre-migration env) — default to breakout.
        return _default

    for name, evaluator in STRATEGY_REGISTRY.items():
        flag = store.load(
            strategy_name=name,
            trading_mode=self.settings.trading_mode,
            strategy_version=self.settings.strategy_version,
        )
        if flag is None or flag.enabled:   # missing row → enabled
            return evaluator

    # All strategies explicitly disabled — no entries this cycle.
    return lambda **_: None
```

Called in `run_cycle_once()` before `_cycle_runner`, result passed as `signal_evaluator=...`.

### `run_cycle()` — add `signal_evaluator` param

```python
def run_cycle(
    *,
    ...existing params...,
    signal_evaluator: StrategySignalEvaluator | None = None,
) -> CycleResult:
    result = evaluate_cycle(
        ...existing args...,
        signal_evaluator=signal_evaluator,
    )
```

`evaluate_cycle()` already handles `signal_evaluator=None` by defaulting to `evaluate_breakout_signal`. When the supervisor passes an explicit evaluator (including the no-op lambda), that default is bypassed. **`evaluate_cycle()` is unchanged.**

### Toggle endpoint

```
POST /strategies/{strategy_name}/toggle
```

Handler logic:
1. Auth gate: if `auth_enabled` and not logged in → redirect to `/login?next=/`.
2. Validate `strategy_name ∈ STRATEGY_REGISTRY` → 404 if unknown (prevents inserting phantom rows).
3. Open a Postgres connection.
4. Load current flag; determine `new_enabled = not (flag.enabled if flag else True)`.
5. `StrategyFlagStore.save(StrategyFlag(strategy_name, trading_mode, strategy_version, new_enabled, now))`.
6. `AuditEventStore.append(AuditEvent(event_type="strategy_flag_changed", payload={strategy_name, enabled: new_enabled, operator: current_operator or "web"}))`.
7. Close connection.
8. `RedirectResponse(url="/", status_code=303)`.

`trading_mode` and `strategy_version` come from `app.state.settings`.

### Dashboard panel

A new "Strategies" panel in `dashboard.html` (rendered only when `snapshot` is not None, i.e. the `/` route):

```html
<div class="panel">
  <h2>Strategies</h2>
  {% for name, flag in snapshot.strategy_flags %}
    <form method="post" action="/strategies/{{ name }}/toggle">
      <div style="display:flex; align-items:center; gap:1rem;">
        <span class="mono">{{ name }}</span>
        <span class="{% if flag %}{% if not flag.enabled %}warn{% endif %}{% endif %}">
          {{ "Enabled" if (flag is none or flag.enabled) else "Disabled" }}
        </span>
        <button type="submit">
          {{ "Disable" if (flag is none or flag.enabled) else "Enable" }}
        </button>
      </div>
    </form>
  {% endfor %}
</div>
```

`snapshot.strategy_flags` is a `list[tuple[str, StrategyFlag | None]]` — one entry per key in `STRATEGY_REGISTRY`, paired with the current flag row (or `None` if no DB row exists yet).

### `DashboardSnapshot` change

Add field:
```python
strategy_flags: list[tuple[str, StrategyFlag | None]]
```

`load_dashboard_snapshot()` populates it by calling `StrategyFlagStore.list_all()` then joining against `STRATEGY_REGISTRY` keys.

---

## Safety Analysis

- **Position management unaffected**: the hard flatten, EOD flatten, and stop-trail paths in `evaluate_cycle()` iterate `open_positions` before the entry-candidate block. They are not gated on `signal_evaluator` at all. Disabling a strategy never closes open positions.
- **60 s lag**: The supervisor reads the flag once per cycle. The maximum lag between toggling and effect is one poll interval (60 s by default). Acceptable for operational use.
- **No-op evaluator safety**: `lambda **_: None` satisfies `StrategySignalEvaluator` (returns `None` for every symbol). `evaluate_cycle()` sees no signals → zero entry candidates → no entry intents. Existing stop-trail and flatten logic runs normally.
- **Auth**: the toggle endpoint applies the same `current_operator()` / `auth_enabled()` checks as the dashboard routes. If auth is disabled, the endpoint is open (same threat model as the existing unauthenticated dashboard).
- **ENABLE_LIVE_TRADING gate**: unchanged — the flag only affects which evaluator is passed. The advisory lock, two-phase dispatch, and live-trading gate are all upstream of this path.
- **Audit trail**: every toggle writes a `strategy_flag_changed` event. The `admin_history` panel already filters by `ADMIN_EVENT_TYPES`; add `"strategy_flag_changed"` to that list so toggles appear in admin history.
- **Concurrent toggle + cycle**: the supervisor reads the flag, web layer writes it — both via separate connections. Postgres serializes the upsert. Worst case: the supervisor reads the old value and the new value takes effect on the next cycle (60 s). No order duplication risk.

---

## Migration

`migrations/005_add_strategy_flags.sql` — `CREATE TABLE IF NOT EXISTS strategy_flags (...)`.  
`migrations/005_add_strategy_flags.down.sql` — `DROP TABLE IF EXISTS strategy_flags`.

No data migration required.
