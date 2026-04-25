# Phase 7 — Strategy Feature Flags: Implementation Plan

**Date**: 2026-04-25  
**Spec**: `docs/superpowers/specs/2026-04-25-phase7-strategy-feature-flags.md`  
**Status**: Plan (v2)

---

## Execution Order

Task 1 → Task 2 → Task 3 → Task 4 → Task 5 → Task 6 → Task 7 → Task 8 (tests)

---

## Task 1 — Migration: add `strategy_flags` table

**File**: `migrations/005_add_strategy_flags.sql`

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

**File**: `migrations/005_add_strategy_flags.down.sql`

```sql
DROP TABLE IF EXISTS strategy_flags;
```

No seed rows. The application treats a missing row as `enabled=true`.

---

## Task 2 — Storage: `StrategyFlag` model + `StrategyFlagStore`

### 2a. Add `StrategyFlag` to `storage/models.py`

After the `DailySessionState` dataclass (end of file):

```python
@dataclass(frozen=True)
class StrategyFlag:
    strategy_name: str
    trading_mode: TradingMode
    strategy_version: str
    enabled: bool
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
```

### 2b. Add `StrategyFlagStore` to `storage/repositories.py`

Add at the end of the file, after `PositionStore`:

```python
class StrategyFlagStore:
    def __init__(self, connection: ConnectionProtocol) -> None:
        self._connection = connection

    def save(self, flag: StrategyFlag) -> None:
        execute(
            self._connection,
            """
            INSERT INTO strategy_flags (
                strategy_name, trading_mode, strategy_version, enabled, updated_at
            )
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (strategy_name, trading_mode, strategy_version)
            DO UPDATE SET
                enabled = EXCLUDED.enabled,
                updated_at = EXCLUDED.updated_at
            """,
            (
                flag.strategy_name,
                flag.trading_mode.value,
                flag.strategy_version,
                flag.enabled,
                flag.updated_at,
            ),
        )

    def load(
        self,
        *,
        strategy_name: str,
        trading_mode: TradingMode,
        strategy_version: str,
    ) -> StrategyFlag | None:
        row = fetch_one(
            self._connection,
            """
            SELECT strategy_name, trading_mode, strategy_version, enabled, updated_at
            FROM strategy_flags
            WHERE strategy_name = %s AND trading_mode = %s AND strategy_version = %s
            """,
            (strategy_name, trading_mode.value, strategy_version),
        )
        if row is None:
            return None
        return StrategyFlag(
            strategy_name=row[0],
            trading_mode=TradingMode(row[1]),
            strategy_version=row[2],
            enabled=bool(row[3]),
            updated_at=row[4],
        )

    def list_all(
        self,
        *,
        trading_mode: TradingMode,
        strategy_version: str,
    ) -> list[StrategyFlag]:
        rows = fetch_all(
            self._connection,
            """
            SELECT strategy_name, trading_mode, strategy_version, enabled, updated_at
            FROM strategy_flags
            WHERE trading_mode = %s AND strategy_version = %s
            ORDER BY strategy_name
            """,
            (trading_mode.value, strategy_version),
        )
        return [
            StrategyFlag(
                strategy_name=row[0],
                trading_mode=TradingMode(row[1]),
                strategy_version=row[2],
                enabled=bool(row[3]),
                updated_at=row[4],
            )
            for row in rows
        ]
```

### 2c. Export from `storage/__init__.py`

Add `StrategyFlag` and `StrategyFlagStore` to the imports and `__all__`:

```python
# in import block
from alpaca_bot.storage.models import (
    ...
    StrategyFlag,
)
from alpaca_bot.storage.repositories import (
    ...
    StrategyFlagStore,
)

# in __all__
    "StrategyFlag",
    "StrategyFlagStore",
```

---

## Task 3 — Strategy registry

**File**: `src/alpaca_bot/strategy/__init__.py`

Replace the entire file:

```python
from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from alpaca_bot.config import Settings
from alpaca_bot.domain.models import Bar, BreakoutSignal
from alpaca_bot.strategy.breakout import evaluate_breakout_signal


@runtime_checkable
class StrategySignalEvaluator(Protocol):
    def __call__(
        self,
        *,
        symbol: str,
        intraday_bars: Sequence[Bar],
        signal_index: int,
        daily_bars: Sequence[Bar],
        settings: Settings,
    ) -> BreakoutSignal | None: ...


STRATEGY_REGISTRY: dict[str, StrategySignalEvaluator] = {
    "breakout": evaluate_breakout_signal,
}
```

---

## Task 4 — Runtime: wire `StrategyFlagStore` into `RuntimeContext` and bootstrap

### 4a. `runtime/bootstrap.py`

Add `StrategyFlagStore` to imports:

```python
from alpaca_bot.storage import (
    ...
    StrategyFlagStore,
)
```

Add field to `RuntimeContext` (after `position_store`):

```python
strategy_flag_store: StrategyFlagStore | None = None
```

In `bootstrap_runtime()`, add to the returned `RuntimeContext`:

```python
strategy_flag_store=StrategyFlagStore(runtime_connection),
```

In `reconnect_runtime_connection()`, add `"strategy_flag_store"` to the rewired attrs tuple:

```python
for attr in (
    "trading_status_store",
    "audit_event_store",
    "order_store",
    "daily_session_state_store",
    "position_store",
    "strategy_flag_store",   # ← add
):
```

### 4b. `runtime/supervisor.py`

Add import at the top:

```python
from alpaca_bot.strategy import STRATEGY_REGISTRY, StrategySignalEvaluator
from alpaca_bot.strategy.breakout import evaluate_breakout_signal as _default_evaluator
```

Add new private method to `RuntimeSupervisor` (after `_load_session_state`):

```python
def _resolve_signal_evaluator(self) -> StrategySignalEvaluator:
    store = getattr(self.runtime, "strategy_flag_store", None)
    if store is None:
        return _default_evaluator
    for name, evaluator in STRATEGY_REGISTRY.items():
        flag = store.load(
            strategy_name=name,
            trading_mode=self.settings.trading_mode,
            strategy_version=self.settings.strategy_version,
        )
        if flag is None or flag.enabled:
            return evaluator
    return lambda **_: None
```

In `run_cycle_once()`, add after the `session_state` staleness check and before the PnL calculation:

```python
signal_evaluator = self._resolve_signal_evaluator()
```

In the `_cycle_runner` call, add `signal_evaluator=signal_evaluator`:

```python
cycle_result = self._cycle_runner(
    ...existing kwargs...,
    signal_evaluator=signal_evaluator,
)
```

### 4c. `runtime/cycle.py`

Add `StrategySignalEvaluator` to imports:

```python
from alpaca_bot.strategy import StrategySignalEvaluator
```

Add `signal_evaluator` parameter to `run_cycle()`:

```python
def run_cycle(
    *,
    ...existing params...,
    signal_evaluator: StrategySignalEvaluator | None = None,
) -> CycleResult:
    result = evaluate_cycle(
        ...existing kwargs...,
        signal_evaluator=signal_evaluator,
    )
```

No other changes to `run_cycle()`. `evaluate_cycle()` already accepts `signal_evaluator` and uses `evaluate_breakout_signal` when `None` is passed — preserving backward compatibility in tests that don't pass the param.

---

## Task 5 — Web service: surface strategy flags in `DashboardSnapshot`

### 5a. `web/service.py`

Add `StrategyFlag` import:

```python
from alpaca_bot.storage import (
    ...
    StrategyFlag,
    StrategyFlagStore,
)
```

Add import for `STRATEGY_REGISTRY`:

```python
from alpaca_bot.strategy import STRATEGY_REGISTRY
```

Add field to `DashboardSnapshot`:

```python
strategy_flags: list[tuple[str, StrategyFlag | None]]
```

Update `load_dashboard_snapshot()` signature to accept `strategy_flag_store`:

```python
def load_dashboard_snapshot(
    *,
    settings: Settings,
    connection: ConnectionProtocol,
    trading_status_store: TradingStatusStore | None = None,
    daily_session_state_store: DailySessionStateStore | None = None,
    position_store: PositionStore | None = None,
    order_store: OrderStore | None = None,
    audit_event_store: AuditEventStore | None = None,
    strategy_flag_store: StrategyFlagStore | None = None,
    now: datetime | None = None,
) -> DashboardSnapshot:
```

Inside `load_dashboard_snapshot()`, compute strategy flags:

```python
strategy_flag_store = strategy_flag_store or StrategyFlagStore(connection)
flags_by_name = {
    f.strategy_name: f
    for f in strategy_flag_store.list_all(
        trading_mode=settings.trading_mode,
        strategy_version=settings.strategy_version,
    )
}
strategy_flags = [
    (name, flags_by_name.get(name))
    for name in STRATEGY_REGISTRY
]
```

And add to the `DashboardSnapshot(...)` constructor call:

```python
strategy_flags=strategy_flags,
```

### 5b. `web/app.py`

Add `StrategyFlagStore` to imports from `alpaca_bot.storage`.

Add `strategy_flag_store_factory` parameter to `create_app()`:

```python
def create_app(
    *,
    ...existing params...,
    strategy_flag_store_factory: Callable[[ConnectionProtocol], object] | None = None,
) -> FastAPI:
```

In the body:

```python
app.state.strategy_flag_store_factory = strategy_flag_store_factory or StrategyFlagStore
```

Update `_load_dashboard_data()` to pass the store:

```python
snapshot = load_dashboard_snapshot(
    settings=app.state.settings,
    connection=connection,
    ...existing stores...,
    strategy_flag_store=_build_store(
        app.state.strategy_flag_store_factory,
        connection,
    ),
)
```

Add the toggle endpoint (before the `return app` line):

```python
from alpaca_bot.strategy import STRATEGY_REGISTRY
from alpaca_bot.storage import AuditEvent, StrategyFlag, StrategyFlagStore
from datetime import timezone

@app.post("/strategies/{strategy_name}/toggle")
def toggle_strategy(strategy_name: str, request: Request) -> Response:
    operator = current_operator(request, settings=app_settings)
    if auth_enabled(app_settings) and operator is None:
        return RedirectResponse(url="/login?next=/", status_code=status.HTTP_303_SEE_OTHER)
    if strategy_name not in STRATEGY_REGISTRY:
        return HTMLResponse(status_code=status.HTTP_404_NOT_FOUND, content="Unknown strategy")
    now = datetime.now(timezone.utc)
    connection = app.state.connect_postgres(app_settings.database_url)
    try:
        flag_store = _build_store(app.state.strategy_flag_store_factory, connection)
        audit_store = _build_store(app.state.audit_event_store_factory, connection)
        current_flag = flag_store.load(
            strategy_name=strategy_name,
            trading_mode=app_settings.trading_mode,
            strategy_version=app_settings.strategy_version,
        )
        new_enabled = not (current_flag.enabled if current_flag is not None else True)
        execute(connection, "BEGIN")
        try:
            flag_store.save(
                StrategyFlag(
                    strategy_name=strategy_name,
                    trading_mode=app_settings.trading_mode,
                    strategy_version=app_settings.strategy_version,
                    enabled=new_enabled,
                    updated_at=now,
                )
            )
            audit_store.append(
                AuditEvent(
                    event_type="strategy_flag_changed",
                    payload={
                        "strategy_name": strategy_name,
                        "enabled": new_enabled,
                        "operator": operator or "web",
                    },
                    created_at=now,
                )
            )
            execute(connection, "COMMIT")
        except Exception:
            execute(connection, "ROLLBACK")
            raise
    finally:
        close = getattr(connection, "close", None)
        if callable(close):
            close()
    return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)
```

Also update `ADMIN_EVENT_TYPES` in `web/service.py`:

```python
ADMIN_EVENT_TYPES = ["trading_status_changed", "strategy_flag_changed"]
```

---

## Task 6 — Dashboard template

**File**: `src/alpaca_bot/web/templates/dashboard.html`

Add a "Strategies" panel inside the `{% if snapshot %}` block (after the session state panel, before the positions panel). Exact location: search for the first `{% if snapshot %}` block and insert within it, after the trading status section.

```html
<div class="panel">
  <h2>Strategies</h2>
  {% for name, flag in snapshot.strategy_flags %}
    <form method="post" action="/strategies/{{ name }}/toggle"
          style="margin-bottom: 0.6rem;">
      <div style="display: flex; align-items: center; gap: 1rem;">
        <span class="mono" style="min-width: 8rem;">{{ name }}</span>
        {% set is_enabled = (flag is none or flag.enabled) %}
        <span class="{{ '' if is_enabled else 'warn' }}">
          {{ "Enabled" if is_enabled else "Disabled" }}
        </span>
        <button type="submit"
                style="border: 1px solid var(--line); border-radius: 999px;
                       background: #f7f2e7; color: var(--ink);
                       padding: 0.35rem 0.8rem; font: inherit; cursor: pointer;">
          {{ "Disable" if is_enabled else "Enable" }}
        </button>
      </div>
    </form>
  {% endfor %}
</div>
```

---

## Task 7 — Add `strategy_flag_store` to ADMIN_EVENT_TYPES and update `web/service.py` audit list

Already covered in Task 5 (`ADMIN_EVENT_TYPES = ["trading_status_changed", "strategy_flag_changed"]`). No separate file change needed.

---

## Task 8 — Tests

### 8a. New test file: `tests/unit/test_strategy_flags.py`

Tests:

1. **`test_strategy_flag_store_save_and_load`**: Create `StrategyFlagStore` with an in-memory fake connection that records SQL. Save a flag, load it back — assert correct model fields returned.

2. **`test_strategy_flag_store_missing_returns_none`**: `load()` for an unknown key returns `None`.

3. **`test_strategy_flag_store_list_all`**: Save two flags with same trading_mode/version, one for different version. `list_all()` returns only the matching two.

4. **`test_supervisor_resolves_breakout_when_no_store`**: `RuntimeSupervisor._resolve_signal_evaluator()` with `runtime.strategy_flag_store = None` returns `evaluate_breakout_signal`.

5. **`test_supervisor_resolves_breakout_when_no_flag_row`**: Store present but `load()` returns `None` → evaluator is `evaluate_breakout_signal`.

6. **`test_supervisor_returns_noop_when_all_disabled`**: Store present, `load()` returns flag with `enabled=False` → returned evaluator always returns `None`.

7. **`test_supervisor_resolves_breakout_when_enabled`**: Store present, flag `enabled=True` → returns `evaluate_breakout_signal`.

8. **`test_run_cycle_passes_signal_evaluator_to_engine`**: `run_cycle()` called with a custom `signal_evaluator`; assert `evaluate_cycle` receives it (inject via the `signal_evaluator` param, verify the injected fn is called).

9. **`test_toggle_endpoint_flips_flag_and_audits`**: FastAPI `TestClient`, fake `StrategyFlagStore` and `AuditEventStore`. POST to `/strategies/breakout/toggle`. Assert flag saved with `enabled=False` and audit event written. Assert redirect to `/`.

10. **`test_toggle_endpoint_unknown_strategy_returns_404`**: POST to `/strategies/unknown_xyz/toggle` → 404.

11. **`test_toggle_endpoint_redirects_to_login_when_auth_enabled`**: Settings with `dashboard_auth_enabled=True`. POST without session cookie → 303 to `/login`.

12. **`test_load_dashboard_snapshot_includes_strategy_flags`**: `load_dashboard_snapshot()` with a fake `StrategyFlagStore` returning one flag. Assert `snapshot.strategy_flags` contains `("breakout", <flag>)`.

13. **`test_load_dashboard_snapshot_missing_flag_shows_none`**: `StrategyFlagStore.list_all()` returns empty list. Assert `snapshot.strategy_flags == [("breakout", None)]`.

### 8b. Existing test updates

- `tests/unit/test_cycle_runner.py`: Any test that calls `run_cycle()` without `signal_evaluator` should still pass (default `None` is unchanged). Add one test that explicitly passes a custom evaluator and asserts it is forwarded to `evaluate_cycle`.
- `tests/unit/test_runtime_supervisor.py`: Any test that calls `run_cycle_once()` should still pass. Add test for `_resolve_signal_evaluator` with various store states.

### Test command

```bash
pytest tests/unit/ -q
```

All existing 313 tests must continue to pass. New tests bring total to ~330.

---

## Rollout Notes

1. `alpaca-bot-migrate` runs migration `005` automatically on deploy — adds the table, no data change.
2. On first toggle from the dashboard, a row is inserted into `strategy_flags`. Before any toggle, the strategy behaves as if enabled (missing row = enabled).
3. No supervisor restart required at any point.
4. To verify: enable/disable from dashboard, watch `strategy_flag_changed` entries in admin history, observe that no entry intents are emitted when disabled (check `decision_cycle_completed` audit events with `intent_count: 0`).
