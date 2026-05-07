# Option Strategy Dashboard Visibility — Design Spec

## Goal

Extend the operator dashboard and supervisor runtime to treat option strategies
(from `OPTION_STRATEGY_FACTORIES`) as first-class entries: visible in the
strategy table, togglable via the Disable/Enable and Entries buttons, flag-gated
in the supervisor cycle loop, and included in the session weight pool when
options trading is active.

## Problem Statement

After the bearish-strategies implementation, `OPTION_STRATEGY_FACTORIES`
contains 12 strategies (11 bear + `breakout_calls`). Every one of them is
invisible to the operator:

- The dashboard strategy table iterates `STRATEGY_REGISTRY` only — option
  strategies produce no rows.
- The `/strategies/{name}/toggle` and `/strategies/{name}/toggle-entries`
  endpoints return 404 for any option strategy name.
- `_resolve_active_strategies()` only checks `strategy_flags` for equity
  strategies; option strategies are appended unconditionally (no flag row
  = always runs).
- `_update_session_weights()` calls `_resolve_active_strategies()` for its
  name pool — option strategies are absent, so they never receive a computed
  weight and dilute the equity-only pool.
- `/healthz` `strategy_flags` payload lists only equity strategy names.

## Architecture

### Single source of truth: `ALL_STRATEGY_NAMES`

Add to `strategy/__init__.py`:

```python
ALL_STRATEGY_NAMES: frozenset[str] = frozenset(STRATEGY_REGISTRY) | OPTION_STRATEGY_NAMES
```

All places that validate "is this a known strategy name?" switch from
`STRATEGY_REGISTRY` to `ALL_STRATEGY_NAMES`.

### Dashboard snapshot (`web/service.py`)

`load_dashboard_snapshot` currently builds:

```python
strategy_flags = [(name, flags_by_name.get(name)) for name in STRATEGY_REGISTRY]
```

Change to include option strategies after equity strategies, in sorted order:

```python
strategy_flags = (
    [(name, flags_by_name.get(name)) for name in STRATEGY_REGISTRY]
    + [(name, flags_by_name.get(name)) for name in sorted(OPTION_STRATEGY_FACTORIES)]
)
```

Same extension applies to `load_health_snapshot`, which produces the
`strategy_flags` list for the `/healthz` JSON payload.

No schema migration needed — `strategy_flags` and `strategy_weights` tables
have no constraint on which strategy names are valid; any name can be inserted.

### Toggle endpoints (`web/app.py`)

Both `/strategies/{name}/toggle` and `/strategies/{name}/toggle-entries`
validate `strategy_name not in STRATEGY_REGISTRY` and return 404.

Change both guards to:

```python
from alpaca_bot.strategy import ALL_STRATEGY_NAMES
...
if strategy_name not in ALL_STRATEGY_NAMES:
    return HTMLResponse(status_code=404, content="Unknown strategy")
```

No other logic changes — `StrategyFlagStore` and `DailySessionStateStore`
already accept any strategy name.

### Dashboard template (`web/templates/dashboard.html`)

Pass `option_strategy_names=OPTION_STRATEGY_NAMES` in the template context
from the GET `/` route in `app.py`.

In the strategy table row, add a small `OPT` badge next to the strategy name
when `name in option_strategy_names`:

```jinja
<td class="mono">
  {{ name }}
  {% if name in option_strategy_names %}
    <span style="font-size: 0.7em; color: var(--muted); margin-left: 0.3em">OPT</span>
  {% endif %}
</td>
```

This keeps the table visually unified while distinguishing option strategies
at a glance.

### Supervisor flag-gating (`runtime/supervisor.py`)

**`run_cycle_once()` — option strategy loop**

Currently appends all 12 option strategies unconditionally:

```python
for opt_name in OPTION_STRATEGY_NAMES:
    factory = OPTION_STRATEGY_FACTORIES[opt_name]
    active_strategies.append((opt_name, factory(option_chains_by_symbol)))
```

Add a flag check that mirrors `_resolve_active_strategies()`, holding the
store_lock once for all 12 lookups:

```python
_flag_store = getattr(self.runtime, "strategy_flag_store", None)
_store_lock = getattr(self.runtime, "store_lock", None)
with _store_lock if _store_lock is not None else contextlib.nullcontext():
    for opt_name in OPTION_STRATEGY_NAMES:
        if _flag_store is not None:
            flag = _flag_store.load(
                strategy_name=opt_name,
                trading_mode=self.settings.trading_mode,
                strategy_version=self.settings.strategy_version,
            )
            if flag is not None and not flag.enabled:
                continue
        factory = OPTION_STRATEGY_FACTORIES[opt_name]
        active_strategies.append((opt_name, factory(option_chains_by_symbol)))
```

The lock is held for the duration of all 12 DB reads to prevent a concurrent
trade-update stream thread from seeing a partially-updated flag state.

**`_update_session_weights()` — option strategy name pool**

Currently:

```python
active_names = [name for name, _ in self._resolve_active_strategies()]
```

When `self.settings.enable_options_trading` is True, also append enabled
option strategy names:

```python
active_names = [name for name, _ in self._resolve_active_strategies()]
if self.settings.enable_options_trading:
    store = getattr(self.runtime, "strategy_flag_store", None)
    store_lock = getattr(self.runtime, "store_lock", None)
    with store_lock if store_lock is not None else contextlib.nullcontext():
        for opt_name in sorted(OPTION_STRATEGY_FACTORIES):
            if store is not None:
                flag = store.load(
                    strategy_name=opt_name,
                    trading_mode=self.settings.trading_mode,
                    strategy_version=self.settings.strategy_version,
                )
                if flag is not None and not flag.enabled:
                    continue
            active_names.append(opt_name)
```

When options trading is disabled, option strategy names are excluded from the
weight pool — this prevents newly-started options-off sessions from halving
each equity strategy's allocation weight due to 12 zero-PnL option rows.

## Data flow summary

```
Operator clicks Disable on bear_breakdown
  → POST /strategies/bear_breakdown/toggle
  → validation: "bear_breakdown" in ALL_STRATEGY_NAMES ✓
  → StrategyFlagStore.save(enabled=False)
  → AuditEvent(strategy_flag_changed)

Next supervisor cycle
  → run_cycle_once() fetches option chains
  → loop over OPTION_STRATEGY_NAMES with flag check
  → bear_breakdown flag.enabled = False → skipped
  → 11 remaining option strategies appended to active_strategies

Dashboard GET /
  → load_dashboard_snapshot()
  → strategy_flags = equity rows + option rows (sorted)
  → template renders 23 rows total (11 equity + 12 option)
  → bear_breakdown row shows Disabled, no Disable button active
```

## What does NOT change

- No new database migrations. Tables already accept any strategy name.
- No new environment variables.
- `STRATEGY_REGISTRY` and `OPTION_STRATEGY_FACTORIES` are not merged — the
  split is load-order-dependent (option strategies need chains fetched first).
- `evaluate_cycle()` remains a pure function. All changes are in the
  supervisor orchestration layer and web layer.
- `ENABLE_OPTIONS_TRADING=false` still prevents option strategies from
  running. The flag-gating change only adds a second filter (DB flag) on top
  of the existing settings gate.

## Financial safety

This change does not touch order submission, position sizing, or stop
placement. It only affects:
1. Which strategies are added to `active_strategies` in the supervisor
   (adding a DB-flag filter on top of the existing options-enabled gate).
2. Which names are included in the session weight pool.
3. Which rows appear in the dashboard table and which toggle endpoints are
   valid.

The worst-case for a misconfigured flag: an operator disables a bear strategy
and it stops entering new put positions for that strategy. No existing orders
are affected — the supervisor never cancels working orders based on flag state.

## Test plan

- `test_web_service.py`: assert option strategy names appear in
  `load_dashboard_snapshot().strategy_flags` and
  `load_health_snapshot().strategy_flags`.
- `test_web_app.py`: assert toggle and toggle-entries accept `bear_breakdown`;
  assert unknown strategy still returns 404; update healthz assertion to
  include option strategy names.
- `test_supervisor.py`: assert a disabled option strategy is excluded from
  `active_strategies` in `run_cycle_once()`; assert weight pool includes
  option names when `enable_options_trading=True` and excludes them when
  `False`.
