# Option Strategy Dashboard Visibility — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make all 12 option strategies (11 bear + `breakout_calls`) visible in the operator dashboard with full toggle support, and ensure the supervisor respects their enable/disable flags.

**Architecture:** Add `ALL_STRATEGY_NAMES` as the single authority for "what strategies exist", extend `load_dashboard_snapshot` and `load_health_snapshot` to include option strategy rows, widen the toggle endpoint validation to accept any name in `ALL_STRATEGY_NAMES`, and add flag-check logic in the supervisor's option strategy activation loop and weight pool computation.

**Tech Stack:** Python, FastAPI, Jinja2, pytest, psycopg2, alpaca_bot domain layer

---

## File Structure

| File | Change |
|---|---|
| `src/alpaca_bot/strategy/__init__.py` | Add `ALL_STRATEGY_NAMES` export |
| `src/alpaca_bot/web/service.py` | Extend `load_dashboard_snapshot` and `load_health_snapshot` to include option strategy rows |
| `src/alpaca_bot/web/app.py` | Widen toggle validation; add `option_strategy_names` to template context |
| `src/alpaca_bot/web/templates/dashboard.html` | Add `OPT` badge for option strategy rows |
| `src/alpaca_bot/runtime/supervisor.py` | Flag-gate option strategies in cycle loop; include option names in weight pool |
| `tests/unit/test_bear_registry.py` | Add `ALL_STRATEGY_NAMES` assertion |
| `tests/unit/test_web_service.py` | Assert option strategies appear in snapshot strategy_flags |
| `tests/unit/test_web_app.py` | Assert toggle endpoints accept `bear_breakdown`; update healthz assertion |
| `tests/unit/test_supervisor_option_integration.py` | Assert disabled option strategy excluded from cycle |
| `tests/unit/test_supervisor_weights.py` | Assert option names included in weight pool when options enabled |

No migrations needed — `strategy_flags` and `strategy_weights` tables have no constraint on strategy name values.

---

### Task 1: Export `ALL_STRATEGY_NAMES` from strategy package

**Files:**
- Modify: `src/alpaca_bot/strategy/__init__.py:77`
- Test: `tests/unit/test_bear_registry.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/test_bear_registry.py`:

```python
def test_all_strategy_names_contains_equity_and_option_strategies():
    from alpaca_bot.strategy import ALL_STRATEGY_NAMES, STRATEGY_REGISTRY, OPTION_STRATEGY_NAMES
    assert ALL_STRATEGY_NAMES == frozenset(STRATEGY_REGISTRY) | OPTION_STRATEGY_NAMES
    assert "breakout" in ALL_STRATEGY_NAMES
    assert "bear_breakdown" in ALL_STRATEGY_NAMES
    assert "breakout_calls" in ALL_STRATEGY_NAMES
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/unit/test_bear_registry.py::test_all_strategy_names_contains_equity_and_option_strategies -v
```

Expected: `ImportError: cannot import name 'ALL_STRATEGY_NAMES'`

- [ ] **Step 3: Add `ALL_STRATEGY_NAMES` to `src/alpaca_bot/strategy/__init__.py`**

After the existing `OPTION_STRATEGY_NAMES` line (currently line 77), add:

```python
ALL_STRATEGY_NAMES: frozenset[str] = frozenset(STRATEGY_REGISTRY) | OPTION_STRATEGY_NAMES
```

The file's tail should now read:

```python
OPTION_STRATEGY_NAMES: frozenset[str] = frozenset(OPTION_STRATEGY_FACTORIES)

ALL_STRATEGY_NAMES: frozenset[str] = frozenset(STRATEGY_REGISTRY) | OPTION_STRATEGY_NAMES
```

- [ ] **Step 4: Run test to verify it passes**

```bash
pytest tests/unit/test_bear_registry.py -v
```

Expected: all 6 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/alpaca_bot/strategy/__init__.py tests/unit/test_bear_registry.py
git commit -m "feat: export ALL_STRATEGY_NAMES combining equity and option strategies"
```

---

### Task 2: Dashboard snapshot includes option strategy rows

`load_dashboard_snapshot` in `web/service.py` at line 214 builds:
```python
strategy_flags = [(name, flags_by_name.get(name)) for name in STRATEGY_REGISTRY]
```
`load_health_snapshot` at line 348 builds similarly. Both need to include option strategy names.

**Files:**
- Modify: `src/alpaca_bot/web/service.py:30,214,348`
- Test: `tests/unit/test_web_service.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/unit/test_web_service.py` after the existing `test_load_health_snapshot_includes_strategy_flags` test (around line 266):

```python
def test_load_dashboard_snapshot_includes_option_strategy_rows() -> None:
    from alpaca_bot.strategy import OPTION_STRATEGY_NAMES
    snapshot = load_dashboard_snapshot(
        settings=make_settings(),
        connection=SimpleNamespace(),
        now=datetime(2026, 4, 25, 14, 0, tzinfo=timezone.utc),
        **make_snapshot_stores(),
    )
    names_in_table = [name for name, _ in snapshot.strategy_flags]
    for opt_name in OPTION_STRATEGY_NAMES:
        assert opt_name in names_in_table, f"Option strategy {opt_name!r} missing from strategy_flags"


def test_load_health_snapshot_includes_option_strategy_names() -> None:
    from alpaca_bot.strategy import ALL_STRATEGY_NAMES
    snapshot = load_health_snapshot(
        settings=make_settings(),
        connection=SimpleNamespace(),
        trading_status_store=SimpleNamespace(load=lambda **_: None),
        audit_event_store=SimpleNamespace(
            list_recent=lambda **_: [],
            load_latest=lambda **_: None,
            list_by_event_types=lambda **_: [],
        ),
        strategy_flag_store=SimpleNamespace(list_all=lambda **_: []),
    )
    flag_names = {name for name, _ in snapshot.strategy_flags}
    assert flag_names == ALL_STRATEGY_NAMES
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/unit/test_web_service.py::test_load_dashboard_snapshot_includes_option_strategy_rows tests/unit/test_web_service.py::test_load_health_snapshot_includes_option_strategy_names -v
```

Expected: both FAIL (option strategy names absent from the lists)

- [ ] **Step 3: Add the import and extend both snapshot functions in `src/alpaca_bot/web/service.py`**

**Import change** (line 30 — change `from alpaca_bot.strategy import STRATEGY_REGISTRY` to):

```python
from alpaca_bot.strategy import ALL_STRATEGY_NAMES, OPTION_STRATEGY_FACTORIES, STRATEGY_REGISTRY
```

**`load_dashboard_snapshot` change** (line 214 — replace the single line):

```python
    strategy_flags = [(name, flags_by_name.get(name)) for name in STRATEGY_REGISTRY]
```

with:

```python
    strategy_flags = (
        [(name, flags_by_name.get(name)) for name in STRATEGY_REGISTRY]
        + [(name, flags_by_name.get(name)) for name in sorted(OPTION_STRATEGY_FACTORIES)]
    )
```

**`load_health_snapshot` change** (line 348 — replace the single line):

```python
    strategy_flags = [(name, flags_by_name.get(name, False)) for name in STRATEGY_REGISTRY]
```

with:

```python
    strategy_flags = (
        [(name, flags_by_name.get(name, True)) for name in STRATEGY_REGISTRY]
        + [(name, flags_by_name.get(name, True)) for name in sorted(OPTION_STRATEGY_FACTORIES)]
    )
```

Note: the default changed from `False` to `True` — no DB row means enabled (consistent with the dashboard and supervisor behavior). The existing `test_load_health_snapshot_includes_strategy_flags` test at line 264 asserts `set(flag_dict.keys()) == set(STRATEGY_REGISTRY.keys())`. You must update that assertion:

```python
def test_load_health_snapshot_includes_strategy_flags() -> None:
    from alpaca_bot.strategy import ALL_STRATEGY_NAMES

    enabled_flag = SimpleNamespace(strategy_name="breakout", enabled=True)
    snapshot = load_health_snapshot(
        settings=make_settings(),
        connection=SimpleNamespace(),
        trading_status_store=SimpleNamespace(load=lambda **_: None),
        audit_event_store=SimpleNamespace(
            list_recent=lambda **_: [],
            load_latest=lambda **_: None,
            list_by_event_types=lambda **_: [],
        ),
        strategy_flag_store=SimpleNamespace(list_all=lambda **_: [enabled_flag]),
    )

    flag_dict = dict(snapshot.strategy_flags)
    assert set(flag_dict.keys()) == ALL_STRATEGY_NAMES
    assert flag_dict["breakout"] is True
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/unit/test_web_service.py -v
```

Expected: all tests PASS (including the updated healthz test)

- [ ] **Step 5: Commit**

```bash
git add src/alpaca_bot/web/service.py tests/unit/test_web_service.py
git commit -m "feat: include option strategy rows in dashboard and health snapshots"
```

---

### Task 3: Toggle endpoints accept option strategy names + OPT badge in template

Both POST `/strategies/{name}/toggle` (line 520) and POST `/strategies/{name}/toggle-entries` (line 459) in `web/app.py` guard against unknown strategy names using `if strategy_name not in STRATEGY_REGISTRY`. The GET `/` route at line 160 does not pass `option_strategy_names` to the template context.

**Files:**
- Modify: `src/alpaca_bot/web/app.py:34,459,520,160-172`
- Modify: `src/alpaca_bot/web/templates/dashboard.html:362`
- Test: `tests/unit/test_web_app.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/unit/test_web_app.py` after `test_toggle_entries_returns_404_for_unknown_strategy` (around line 1160):

```python
def test_toggle_entries_accepts_option_strategy_name() -> None:
    saved_states: list = []
    saved_events: list = []

    def state_store_factory(_conn):
        return SimpleNamespace(
            load=lambda **_: None,
            save=lambda state, *, commit=True: saved_states.append(state),
        )

    app = create_app(
        settings=make_settings(),
        connect_postgres_fn=lambda _url: FakeConnection(responses=[]),
        trading_status_store_factory=lambda _c: SimpleNamespace(load=lambda **_: None),
        daily_session_state_store_factory=state_store_factory,
        position_store_factory=lambda _c: SimpleNamespace(list_all=lambda **_: []),
        order_store_factory=lambda _c: SimpleNamespace(
            list_by_status=lambda **_: [],
            list_recent=lambda **_: [],
            list_closed_trades=lambda **_: [],
        ),
        audit_event_store_factory=lambda _c: SimpleNamespace(
            list_recent=lambda **_: [],
            load_latest=lambda **_: None,
            list_by_event_types=lambda **_: [],
            append=lambda event, *, commit=True: saved_events.append(event),
        ),
        strategy_flag_store_factory=lambda _c: SimpleNamespace(load=lambda **_: None, list_all=lambda **_: []),
    )

    client = TestClient(app, follow_redirects=False)
    token = _csrf_token(client, "toggle")

    response = client.post(
        "/strategies/bear_breakdown/toggle-entries",
        data={"_csrf_token": token},
    )

    assert response.status_code == 303
    assert len(saved_states) == 1
    assert saved_states[0].strategy_name == "bear_breakdown"
    assert saved_states[0].entries_disabled is True


def test_toggle_accepts_option_strategy_name() -> None:
    saved_flags: list = []
    saved_events: list = []

    def flag_store_factory(_conn):
        return SimpleNamespace(
            load=lambda **_: None,
            save=lambda flag, *, commit=True: saved_flags.append(flag),
            list_all=lambda **_: [],
        )

    app = create_app(
        settings=make_settings(),
        connect_postgres_fn=lambda _url: FakeConnection(responses=[]),
        trading_status_store_factory=lambda _c: SimpleNamespace(load=lambda **_: None),
        daily_session_state_store_factory=lambda _c: SimpleNamespace(load=lambda **_: None, save=lambda **_: None),
        position_store_factory=lambda _c: SimpleNamespace(list_all=lambda **_: []),
        order_store_factory=lambda _c: SimpleNamespace(
            list_by_status=lambda **_: [],
            list_recent=lambda **_: [],
            list_closed_trades=lambda **_: [],
        ),
        audit_event_store_factory=lambda _c: SimpleNamespace(
            list_recent=lambda **_: [],
            load_latest=lambda **_: None,
            list_by_event_types=lambda **_: [],
            append=lambda event, *, commit=True: saved_events.append(event),
        ),
        strategy_flag_store_factory=flag_store_factory,
    )

    client = TestClient(app, follow_redirects=False)
    token = _csrf_token(client, "toggle")

    response = client.post(
        "/strategies/bear_breakdown/toggle",
        data={"_csrf_token": token},
    )

    assert response.status_code == 303
    assert len(saved_flags) == 1
    assert saved_flags[0].strategy_name == "bear_breakdown"
    assert saved_flags[0].enabled is False  # toggled from default True to False
```

Also find and update the existing healthz strategy_flags assertion at line 1661:

```python
# Before (find this line):
assert set(flags.keys()) == set(STRATEGY_REGISTRY.keys())

# After:
from alpaca_bot.strategy import ALL_STRATEGY_NAMES
assert set(flags.keys()) == ALL_STRATEGY_NAMES
```

- [ ] **Step 2: Run the new tests to verify they fail**

```bash
pytest tests/unit/test_web_app.py::test_toggle_entries_accepts_option_strategy_name tests/unit/test_web_app.py::test_toggle_accepts_option_strategy_name -v
```

Expected: both FAIL with 404 (option strategy name not in STRATEGY_REGISTRY)

- [ ] **Step 3: Update `src/alpaca_bot/web/app.py`**

**Import change** (line 34 — change `from alpaca_bot.strategy import STRATEGY_REGISTRY` to):

```python
from alpaca_bot.strategy import ALL_STRATEGY_NAMES, OPTION_STRATEGY_NAMES, STRATEGY_REGISTRY
```

**toggle-entries guard** (line 459 — replace):

```python
        if strategy_name not in STRATEGY_REGISTRY:
            return HTMLResponse(status_code=status.HTTP_404_NOT_FOUND, content="Unknown strategy")
```

with:

```python
        if strategy_name not in ALL_STRATEGY_NAMES:
            return HTMLResponse(status_code=status.HTTP_404_NOT_FOUND, content="Unknown strategy")
```

**toggle guard** (line 520 — replace):

```python
        if strategy_name not in STRATEGY_REGISTRY:
            return HTMLResponse(
                status_code=status.HTTP_404_NOT_FOUND,
                content="Unknown strategy",
            )
```

with:

```python
        if strategy_name not in ALL_STRATEGY_NAMES:
            return HTMLResponse(
                status_code=status.HTTP_404_NOT_FOUND,
                content="Unknown strategy",
            )
```

**Template context** (GET `/` route, lines 160-172 — add `option_strategy_names` to the context dict):

```python
        return templates.TemplateResponse(
            request=request,
            name="dashboard.html",
            context={
                "request": request,
                "trading_mode": app_settings.trading_mode.value,
                "strategy_version": app_settings.strategy_version,
                "snapshot": snapshot,
                "metrics": metrics,
                "operator_email": operator,
                "auto_refresh": not bool(no_refresh),
                "strategy_weights": strategy_weights,
                "option_strategy_names": OPTION_STRATEGY_NAMES,
            },
        )
```

- [ ] **Step 4: Update `src/alpaca_bot/web/templates/dashboard.html`**

Find line 362 (the strategy name `<td>` inside the `{% for name, flag in snapshot.strategy_flags %}` loop):

```jinja
                  <td class="mono">{{ name }}</td>
```

Replace with:

```jinja
                  <td class="mono">
                    {{ name }}
                    {% if option_strategy_names is defined and name in option_strategy_names %}
                      <span style="font-size: 0.7em; color: var(--muted); margin-left: 0.3em">OPT</span>
                    {% endif %}
                  </td>
```

- [ ] **Step 5: Run all tests to verify they pass**

```bash
pytest tests/unit/test_web_app.py -v
```

Expected: all tests PASS (including the updated healthz test and two new toggle tests)

- [ ] **Step 6: Commit**

```bash
git add src/alpaca_bot/web/app.py src/alpaca_bot/web/templates/dashboard.html tests/unit/test_web_app.py
git commit -m "feat: toggle endpoints accept option strategy names; add OPT badge in dashboard"
```

---

### Task 4: Supervisor flag-gates option strategies in the cycle loop

Currently in `run_cycle_once()` around line 608, option strategies are appended unconditionally:

```python
for opt_name in OPTION_STRATEGY_NAMES:
    factory = OPTION_STRATEGY_FACTORIES[opt_name]
    active_strategies.append(
        (opt_name, factory(option_chains_by_symbol))
    )
```

A disabled flag row (written via the dashboard) is currently silently ignored.

**Files:**
- Modify: `src/alpaca_bot/runtime/supervisor.py:608-612`
- Test: `tests/unit/test_supervisor_option_integration.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/test_supervisor_option_integration.py`:

```python
def test_disabled_option_strategy_excluded_from_cycle(monkeypatch) -> None:
    """A bear strategy with enabled=False in the flag store must not be added to active_strategies."""
    from importlib import import_module
    from datetime import datetime, timezone
    from types import SimpleNamespace
    from alpaca_bot.config import Settings, TradingMode
    from alpaca_bot.storage import StrategyFlag

    module = import_module("alpaca_bot.runtime.supervisor")
    RuntimeSupervisor = module.RuntimeSupervisor

    _NOW = datetime(2026, 5, 1, 14, 30, tzinfo=timezone.utc)
    base_env = {
        "TRADING_MODE": "paper",
        "ENABLE_LIVE_TRADING": "false",
        "ENABLE_OPTIONS_TRADING": "true",
        "STRATEGY_VERSION": "v1",
        "DATABASE_URL": "postgresql://x",
        "MARKET_DATA_FEED": "sip",
        "SYMBOLS": "AAPL",
        "DAILY_SMA_PERIOD": "20",
        "BREAKOUT_LOOKBACK_BARS": "20",
        "RELATIVE_VOLUME_LOOKBACK_BARS": "20",
        "RELATIVE_VOLUME_THRESHOLD": "1.5",
        "ENTRY_TIMEFRAME_MINUTES": "15",
        "RISK_PER_TRADE_PCT": "0.0025",
        "MAX_POSITION_PCT": "0.05",
        "MAX_OPEN_POSITIONS": "3",
        "DAILY_LOSS_LIMIT_PCT": "0.01",
        "STOP_LIMIT_BUFFER_PCT": "0.001",
        "BREAKOUT_STOP_BUFFER_PCT": "0.001",
        "ENTRY_STOP_PRICE_BUFFER": "0.01",
        "ENTRY_WINDOW_START": "10:00",
        "ENTRY_WINDOW_END": "15:30",
        "FLATTEN_TIME": "15:45",
    }
    settings = Settings.from_env(base_env)

    disabled_flag = StrategyFlag(
        strategy_name="bear_breakdown",
        trading_mode=TradingMode.PAPER,
        strategy_version="v1",
        enabled=False,
        updated_at=_NOW,
    )

    active_strategy_names: list[str] = []
    def recording_cycle_runner(*, strategy_name, **kwargs):
        active_strategy_names.append(strategy_name)
        return SimpleNamespace(intents=[])

    class _FakeConn:
        def commit(self): pass
        def rollback(self): pass

    class _FakeStrategyFlagStore:
        def list_all(self, **kwargs): return [disabled_flag]
        def load(self, *, strategy_name, **kwargs):
            if strategy_name == "bear_breakdown":
                return disabled_flag
            return None

    class _FakeOptionChainAdapter:
        def get_option_chain(self, symbol, settings):
            return []

    class _FakeRuntime:
        connection = _FakeConn()
        store_lock = None
        order_store = SimpleNamespace(
            save=lambda *a, **k: None,
            list_by_status=lambda **k: [],
            list_pending_submit=lambda **k: [],
            daily_realized_pnl=lambda **k: 0.0,
            daily_realized_pnl_by_symbol=lambda **k: {},
        )
        strategy_weight_store = None
        trading_status_store = SimpleNamespace(load=lambda **_: None)
        position_store = SimpleNamespace(list_all=lambda **_: [], replace_all=lambda **_: None)
        daily_session_state_store = SimpleNamespace(
            load=lambda **_: None, save=lambda **_: None, list_by_session=lambda **_: []
        )
        audit_event_store = SimpleNamespace(
            append=lambda *a, **k: None,
            load_latest=lambda **_: None,
            list_recent=lambda **_: [],
            list_by_event_types=lambda **_: [],
        )
        strategy_flag_store = _FakeStrategyFlagStore()
        watchlist_store = SimpleNamespace(list_enabled=lambda *a: ["AAPL"], list_ignored=lambda *a: [])
        option_order_store = None

    supervisor = RuntimeSupervisor(
        settings=settings,
        runtime=_FakeRuntime(),
        broker=SimpleNamespace(
            get_account=lambda: SimpleNamespace(equity=10_000.0, buying_power=20_000.0, trading_blocked=False),
            list_open_orders=lambda: [],
            get_open_positions=lambda: [],
            get_clock=lambda: SimpleNamespace(is_open=False),
        ),
        market_data=SimpleNamespace(get_stock_bars=lambda **_: {}, get_daily_bars=lambda **_: {}),
        stream=None,
        close_runtime_fn=lambda _: None,
        connection_checker=lambda _: True,
        cycle_runner=recording_cycle_runner,
        cycle_intent_executor=lambda **kwargs: SimpleNamespace(submitted_exit_count=0, failed_exit_count=0),
        order_dispatcher=lambda **kwargs: {"submitted_count": 0},
        option_chain_adapter=_FakeOptionChainAdapter(),
    )

    supervisor.run_cycle_once(now=lambda: _NOW)

    assert "bear_breakdown" not in active_strategy_names, (
        "bear_breakdown has enabled=False flag — must not appear in cycle"
    )
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/unit/test_supervisor_option_integration.py::test_disabled_option_strategy_excluded_from_cycle -v
```

Expected: FAIL — `bear_breakdown` appears in `active_strategy_names` because the flag is not checked.

- [ ] **Step 3: Update the option strategy loop in `src/alpaca_bot/runtime/supervisor.py`**

Find the block around line 608 (inside `if self._option_chain_adapter is not None:`):

```python
            for opt_name in OPTION_STRATEGY_NAMES:
                factory = OPTION_STRATEGY_FACTORIES[opt_name]
                active_strategies.append(
                    (opt_name, factory(option_chains_by_symbol))
                )
```

Replace with:

```python
            _flag_store = getattr(self.runtime, "strategy_flag_store", None)
            _store_lock = getattr(self.runtime, "store_lock", None)
            with _store_lock if _store_lock is not None else contextlib.nullcontext():
                for opt_name in OPTION_STRATEGY_NAMES:
                    if _flag_store is not None:
                        _flag = _flag_store.load(
                            strategy_name=opt_name,
                            trading_mode=self.settings.trading_mode,
                            strategy_version=self.settings.strategy_version,
                        )
                        if _flag is not None and not _flag.enabled:
                            continue
                    factory = OPTION_STRATEGY_FACTORIES[opt_name]
                    active_strategies.append(
                        (opt_name, factory(option_chains_by_symbol))
                    )
```

- [ ] **Step 4: Run test to verify it passes**

```bash
pytest tests/unit/test_supervisor_option_integration.py -v
```

Expected: all tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/alpaca_bot/runtime/supervisor.py tests/unit/test_supervisor_option_integration.py
git commit -m "feat: supervisor flag-gates option strategies in cycle loop"
```

---

### Task 5: Weight pool includes option strategy names when options are enabled

Currently `_update_session_weights()` builds its strategy name pool solely from `_resolve_active_strategies()`, which iterates `STRATEGY_REGISTRY`. Option strategy names are never included, so:
- Their historical trade PnL is excluded from Sharpe/weight computation.
- When options is off, their zero-PnL rows would dilute equity weights.

Fix: when `self.settings.enable_options_trading` is True, also include enabled option strategy names in the pool.

**Files:**
- Modify: `src/alpaca_bot/runtime/supervisor.py:1260`
- Test: `tests/unit/test_supervisor_weights.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/unit/test_supervisor_weights.py`:

```python
def test_update_session_weights_includes_option_names_when_options_enabled() -> None:
    """When enable_options_trading=True, option strategy names join the weight pool."""
    from alpaca_bot.strategy import OPTION_STRATEGY_NAMES

    captured_names: list[list[str]] = []

    from alpaca_bot.runtime.portfolio import compute_strategy_weights as _orig

    def capturing_compute(trade_rows, active_names):
        captured_names.append(list(active_names))
        return _orig(trade_rows, active_names)

    import alpaca_bot.runtime.supervisor as _sup_mod
    original = _sup_mod.compute_strategy_weights
    _sup_mod.compute_strategy_weights = capturing_compute

    try:
        settings = _make_settings(ENABLE_OPTIONS_TRADING="true")
        supervisor, _ = _make_supervisor(settings=settings, weight_store=_FakeWeightStore(preloaded=[]), only_breakout=False)
        supervisor._update_session_weights(_SESSION_DATE)
    finally:
        _sup_mod.compute_strategy_weights = original

    assert len(captured_names) == 1
    pool = set(captured_names[0])
    for opt_name in OPTION_STRATEGY_NAMES:
        assert opt_name in pool, f"Option strategy {opt_name!r} missing from weight pool"


def test_update_session_weights_excludes_option_names_when_options_disabled() -> None:
    """When enable_options_trading=False, option strategy names must NOT join the weight pool."""
    from alpaca_bot.strategy import OPTION_STRATEGY_NAMES

    captured_names: list[list[str]] = []

    from alpaca_bot.runtime.portfolio import compute_strategy_weights as _orig

    def capturing_compute(trade_rows, active_names):
        captured_names.append(list(active_names))
        return _orig(trade_rows, active_names)

    import alpaca_bot.runtime.supervisor as _sup_mod
    original = _sup_mod.compute_strategy_weights
    _sup_mod.compute_strategy_weights = capturing_compute

    try:
        settings = _make_settings(ENABLE_OPTIONS_TRADING="false")
        supervisor, _ = _make_supervisor(settings=settings, weight_store=_FakeWeightStore(preloaded=[]), only_breakout=False)
        supervisor._update_session_weights(_SESSION_DATE)
    finally:
        _sup_mod.compute_strategy_weights = original

    assert len(captured_names) == 1
    pool = set(captured_names[0])
    for opt_name in OPTION_STRATEGY_NAMES:
        assert opt_name not in pool, f"Option strategy {opt_name!r} must not be in weight pool when options disabled"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/unit/test_supervisor_weights.py::test_update_session_weights_includes_option_names_when_options_enabled tests/unit/test_supervisor_weights.py::test_update_session_weights_excludes_option_names_when_options_disabled -v
```

Expected: first test FAIL (option names not in pool), second test PASS (they happen to be excluded already).

- [ ] **Step 3: Update `_update_session_weights` in `src/alpaca_bot/runtime/supervisor.py`**

There are **two** `active_names = [name for name, _ in self._resolve_active_strategies()]` lines in `_update_session_weights`:

1. The early-return path (when `weight_store is None`) around line 1252.
2. The main computation path around line 1269 (after the early-return block).

Both must be extended. The early-return path uses equal weights with no DB lookup (used when weight_store is absent — not the production path, but fix for correctness). The main path uses the already-defined `lock_ctx` variable (defined a few lines above as `lock_ctx = store_lock if store_lock is not None else contextlib.nullcontext()`).

**Early-return patch** (find the `if weight_store is None:` block ~line 1251):

```python
        if weight_store is None:
            active_names = [name for name, _ in self._resolve_active_strategies()]
            if self.settings.enable_options_trading:
                _early_flag_store = getattr(self.runtime, "strategy_flag_store", None)
                _early_lock = getattr(self.runtime, "store_lock", None)
                with _early_lock if _early_lock is not None else contextlib.nullcontext():
                    for opt_name in sorted(OPTION_STRATEGY_FACTORIES):
                        if _early_flag_store is not None:
                            _flag = _early_flag_store.load(
                                strategy_name=opt_name,
                                trading_mode=self.settings.trading_mode,
                                strategy_version=self.settings.strategy_version,
                            )
                            if _flag is not None and not _flag.enabled:
                                continue
                        active_names.append(opt_name)
            n = max(len(active_names), 1)
            return {name: 1.0 / n for name in active_names}
```

**Main computation patch** (find the line `active_names = [name for name, _ in self._resolve_active_strategies()]` after the early-return block, ~line 1269). Insert option names immediately after it, reusing `lock_ctx` that is already defined a few lines above:

```python
        active_names = [name for name, _ in self._resolve_active_strategies()]
        if self.settings.enable_options_trading:
            _wt_flag_store = getattr(self.runtime, "strategy_flag_store", None)
            with lock_ctx:
                for opt_name in sorted(OPTION_STRATEGY_FACTORIES):
                    if _wt_flag_store is not None:
                        _flag = _wt_flag_store.load(
                            strategy_name=opt_name,
                            trading_mode=self.settings.trading_mode,
                            strategy_version=self.settings.strategy_version,
                        )
                        if _flag is not None and not _flag.enabled:
                            continue
                    active_names.append(opt_name)
```

Note: `lock_ctx` is already defined at the top of the main path as `lock_ctx = store_lock if store_lock is not None else contextlib.nullcontext()`. Do NOT redefine it — just use it.

`OPTION_STRATEGY_FACTORIES` is already imported at the top of `supervisor.py` (line 54). No new import needed.

- [ ] **Step 4: Run all supervisor weight tests to verify they pass**

```bash
pytest tests/unit/test_supervisor_weights.py -v
```

Expected: all tests PASS

- [ ] **Step 5: Run the full test suite**

```bash
pytest
```

Expected: all tests PASS (no regressions)

- [ ] **Step 6: Commit**

```bash
git add src/alpaca_bot/runtime/supervisor.py tests/unit/test_supervisor_weights.py
git commit -m "feat: include option strategy names in weight pool when options trading is enabled"
```
