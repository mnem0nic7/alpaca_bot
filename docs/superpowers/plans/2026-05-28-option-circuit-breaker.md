# Option Strategy Rolling Loss Circuit Breaker — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a per-strategy rolling realized P&L circuit breaker that automatically disables an option strategy when its cumulative closed-trade loss exceeds a configurable threshold over a rolling N-day window.

**Architecture:** Two new `Settings` fields gate the feature (default off). A new `OptionOrderRepository` method aggregates closed-trade P&L by strategy name. A new supervisor method reads that aggregation on every cycle and writes a `StrategyFlag(enabled=False)` when a strategy crosses the threshold — plugging into the existing flag-check at lines 828–843 that already filters disabled strategies out of `active_strategies`.

**Tech Stack:** Python 3.12, psycopg2 (Postgres), pytest, project fake-callable DI pattern (no mocking of own classes).

**Spec:** `docs/superpowers/specs/2026-05-28-option-circuit-breaker-design.md`

---

## File Map

| File | Change |
|---|---|
| `src/alpaca_bot/config/__init__.py` | Add 2 fields to `Settings`, 2 `from_env` lines, 2 `validate` checks |
| `src/alpaca_bot/storage/repositories.py` | Add `rolling_realized_pnl_by_strategy()` to `OptionOrderRepository` |
| `src/alpaca_bot/runtime/supervisor.py` | Add `StrategyFlag` import, `_check_option_strategy_circuit_breakers()` method, and call site |
| `tests/unit/test_option_circuit_breaker.py` | New file — 5 tests |

---

## Task 1: Config — two new Settings fields

**Files:**
- Modify: `src/alpaca_bot/config/__init__.py:180` (dataclass fields)
- Modify: `src/alpaca_bot/config/__init__.py:414-417` (from_env parsing)
- Modify: `src/alpaca_bot/config/__init__.py:628` (validate checks)

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_option_circuit_breaker.py`:

```python
from __future__ import annotations

import pytest
from alpaca_bot.config import Settings
from tests.unit.helpers import _base_env


def test_settings_circuit_breaker_defaults():
    """New fields default to 0.0 and 7 — feature off by default."""
    s = Settings.from_env(_base_env())
    assert s.option_strategy_max_rolling_loss_usd == 0.0
    assert s.option_strategy_rolling_loss_days == 7


def test_settings_circuit_breaker_parsed_from_env():
    """Both fields are parsed from env vars."""
    env = {
        **_base_env(),
        "OPTION_STRATEGY_MAX_ROLLING_LOSS_USD": "500.0",
        "OPTION_STRATEGY_ROLLING_LOSS_DAYS": "14",
    }
    s = Settings.from_env(env)
    assert s.option_strategy_max_rolling_loss_usd == 500.0
    assert s.option_strategy_rolling_loss_days == 14


def test_settings_circuit_breaker_rejects_negative_loss():
    env = {**_base_env(), "OPTION_STRATEGY_MAX_ROLLING_LOSS_USD": "-1.0"}
    with pytest.raises(ValueError, match="OPTION_STRATEGY_MAX_ROLLING_LOSS_USD"):
        Settings.from_env(env)


def test_settings_circuit_breaker_rejects_zero_days():
    env = {**_base_env(), "OPTION_STRATEGY_ROLLING_LOSS_DAYS": "0"}
    with pytest.raises(ValueError, match="OPTION_STRATEGY_ROLLING_LOSS_DAYS"):
        Settings.from_env(env)
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/unit/test_option_circuit_breaker.py::test_settings_circuit_breaker_defaults -v
```

Expected: `AttributeError: 'Settings' object has no attribute 'option_strategy_max_rolling_loss_usd'`

- [ ] **Step 3: Add fields to Settings dataclass**

In `src/alpaca_bot/config/__init__.py`, after line 180 (`enable_vwap_entry_filter: bool = False`), add:

```python
    option_strategy_max_rolling_loss_usd: float = 0.0
    option_strategy_rolling_loss_days: int = 7
```

- [ ] **Step 4: Add from_env parsing**

In `src/alpaca_bot/config/__init__.py`, replace the closing of `Settings(...)` in `from_env()`. The current last line before the closing `)` is:

```python
            enable_vwap_entry_filter=_parse_bool(
                "ENABLE_VWAP_ENTRY_FILTER", values.get("ENABLE_VWAP_ENTRY_FILTER", "false")
            ),
        )
```

Replace it with:

```python
            enable_vwap_entry_filter=_parse_bool(
                "ENABLE_VWAP_ENTRY_FILTER", values.get("ENABLE_VWAP_ENTRY_FILTER", "false")
            ),
            option_strategy_max_rolling_loss_usd=float(
                values.get("OPTION_STRATEGY_MAX_ROLLING_LOSS_USD", "0.0")
            ),
            option_strategy_rolling_loss_days=int(
                values.get("OPTION_STRATEGY_ROLLING_LOSS_DAYS", "7")
            ),
        )
```

- [ ] **Step 5: Add validate() checks**

In `src/alpaca_bot/config/__init__.py`, after line 628 (`raise ValueError("VOL_RAISE_THRESHOLD must be between 0 (exclusive) and 1.0")`), add:

```python
        if self.option_strategy_max_rolling_loss_usd < 0:
            raise ValueError("OPTION_STRATEGY_MAX_ROLLING_LOSS_USD must be >= 0")
        if self.option_strategy_rolling_loss_days < 1:
            raise ValueError("OPTION_STRATEGY_ROLLING_LOSS_DAYS must be >= 1")
```

- [ ] **Step 6: Run config tests to verify they pass**

```bash
pytest tests/unit/test_option_circuit_breaker.py::test_settings_circuit_breaker_defaults \
       tests/unit/test_option_circuit_breaker.py::test_settings_circuit_breaker_parsed_from_env \
       tests/unit/test_option_circuit_breaker.py::test_settings_circuit_breaker_rejects_negative_loss \
       tests/unit/test_option_circuit_breaker.py::test_settings_circuit_breaker_rejects_zero_days -v
```

Expected: 4 PASSED

- [ ] **Step 7: Run full test suite**

```bash
pytest -q
```

Expected: all existing tests still pass (new fields default to non-breaking values).

- [ ] **Step 8: Commit**

```bash
git add src/alpaca_bot/config/__init__.py tests/unit/test_option_circuit_breaker.py
git commit -m "feat: add option_strategy_max_rolling_loss_usd/days Settings fields"
```

---

## Task 2: Repository — rolling_realized_pnl_by_strategy

**Files:**
- Modify: `src/alpaca_bot/storage/repositories.py:2131` (add method after list_closed_option_trade_records)
- Modify: `tests/unit/test_option_circuit_breaker.py` (add repository test)

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/test_option_circuit_breaker.py`:

```python
from datetime import date
from alpaca_bot.config import TradingMode
from alpaca_bot.storage.repositories import OptionOrderRepository


def test_rolling_realized_pnl_aggregates_by_strategy():
    """rolling_realized_pnl_by_strategy sums P&L per strategy_name over closed trades."""

    class _FakeRepo(OptionOrderRepository):
        def __init__(self):
            pass  # skip DB connection in parent __init__

        def list_closed_option_trade_records(self, **kw):
            return [
                {"strategy_name": "bear_orb", "pnl": -300.0},
                {"strategy_name": "bear_orb", "pnl": -200.0},
                {"strategy_name": "bear_momentum", "pnl": 50.0},
            ]

    repo = _FakeRepo()
    result = repo.rolling_realized_pnl_by_strategy(
        trading_mode=TradingMode.PAPER,
        strategy_version="v1",
        since_date=date(2026, 5, 22),
        until_date=date(2026, 5, 28),
    )
    assert result == {"bear_orb": -500.0, "bear_momentum": 50.0}


def test_rolling_realized_pnl_empty_when_no_trades():
    """Returns empty dict when no closed trades in window."""

    class _FakeRepo(OptionOrderRepository):
        def __init__(self):
            pass

        def list_closed_option_trade_records(self, **kw):
            return []

    repo = _FakeRepo()
    result = repo.rolling_realized_pnl_by_strategy(
        trading_mode=TradingMode.PAPER,
        strategy_version="v1",
        since_date=date(2026, 5, 22),
        until_date=date(2026, 5, 28),
    )
    assert result == {}
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/unit/test_option_circuit_breaker.py::test_rolling_realized_pnl_aggregates_by_strategy -v
```

Expected: `AttributeError: type object 'OptionOrderRepository' has no attribute 'rolling_realized_pnl_by_strategy'`

- [ ] **Step 3: Add the method to OptionOrderRepository**

In `src/alpaca_bot/storage/repositories.py`, after line 2130 (the `return result` at the end of `list_closed_option_trade_records`), insert:

```python
    def rolling_realized_pnl_by_strategy(
        self,
        *,
        trading_mode: TradingMode,
        strategy_version: str,
        since_date: date,
        until_date: date,
        market_timezone: str = "America/New_York",
    ) -> dict[str, float]:
        """Aggregate closed-trade P&L by strategy_name over a date window.

        Uses list_closed_option_trade_records (BTC-anchored) so the amount
        is attributable to the day the position was closed, not opened.
        Returns {strategy_name: sum_pnl}. Strategies with no closed trades
        in the window are absent from the result.
        """
        records = self.list_closed_option_trade_records(
            trading_mode=trading_mode,
            strategy_version=strategy_version,
            since_date=since_date,
            until_date=until_date,
            market_timezone=market_timezone,
        )
        result: dict[str, float] = {}
        for rec in records:
            name = rec["strategy_name"] or "_unknown"
            result[name] = result.get(name, 0.0) + rec["pnl"]
        return result
```

- [ ] **Step 4: Run repository tests to verify they pass**

```bash
pytest tests/unit/test_option_circuit_breaker.py::test_rolling_realized_pnl_aggregates_by_strategy \
       tests/unit/test_option_circuit_breaker.py::test_rolling_realized_pnl_empty_when_no_trades -v
```

Expected: 2 PASSED

- [ ] **Step 5: Run full test suite**

```bash
pytest -q
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/alpaca_bot/storage/repositories.py tests/unit/test_option_circuit_breaker.py
git commit -m "feat: add OptionOrderRepository.rolling_realized_pnl_by_strategy()"
```

---

## Task 3: Supervisor — _check_option_strategy_circuit_breakers

**Files:**
- Modify: `src/alpaca_bot/runtime/supervisor.py:49-57` (add StrategyFlag to storage import)
- Modify: `src/alpaca_bot/runtime/supervisor.py:804` (add call site in run_cycle_once)
- Modify: `src/alpaca_bot/runtime/supervisor.py` (add _check_option_strategy_circuit_breakers method)
- Modify: `tests/unit/test_option_circuit_breaker.py` (add 3 supervisor tests)

- [ ] **Step 1: Write the failing tests**

Add to `tests/unit/test_option_circuit_breaker.py`:

```python
from __future__ import annotations

from datetime import date, datetime, timezone
from importlib import import_module
from types import SimpleNamespace

from alpaca_bot.config import Settings, TradingMode
from alpaca_bot.storage import StrategyFlag
from tests.unit.helpers import _base_env


def _make_circuit_breaker_supervisor(
    *,
    rolling_pnl_by_strategy: dict,
    existing_flag: StrategyFlag | None = None,
    max_rolling_loss_usd: float = 500.0,
):
    """Build a minimal supervisor with controllable rolling P&L and flag state.

    Returns (supervisor, saved_flags_list, audit_events_list).
    """
    module = import_module("alpaca_bot.runtime.supervisor")
    RuntimeSupervisor = module.RuntimeSupervisor

    env = {
        **_base_env(),
        "OPTION_STRATEGY_MAX_ROLLING_LOSS_USD": str(max_rolling_loss_usd),
        "OPTION_STRATEGY_ROLLING_LOSS_DAYS": "7",
    }
    settings = Settings.from_env(env)

    saved_flags: list[StrategyFlag] = []
    audit_events: list = []

    class _FakeOptStore:
        def rolling_realized_pnl_by_strategy(self, **kw):
            return rolling_pnl_by_strategy

        def list_open_option_positions(self, **kw):
            return []

        def list_pending_submit(self, **kw):
            return []

        def list_trade_pnl_by_strategy(self, **kw):
            return []

    class _FakeFlagStore:
        def load(self, **kw):
            return existing_flag

        def save(self, flag):
            saved_flags.append(flag)

        def list_all(self, **kw):
            return []

    class _FakeConn:
        def commit(self):
            pass

        def rollback(self):
            pass

    class _FakeRuntime:
        connection = _FakeConn()
        store_lock = None
        audit_event_store = SimpleNamespace(
            append=lambda event, **_: audit_events.append(event),
            load_latest=lambda **_: None,
            list_recent=lambda **_: [],
            list_by_event_types=lambda **_: [],
        )
        position_store = SimpleNamespace(list_all=lambda **kw: [])
        order_store = SimpleNamespace(
            list_by_status=lambda **kw: [],
            list_pending_submit=lambda **kw: [],
            daily_realized_pnl=lambda **kw: 0.0,
            daily_realized_pnl_by_symbol=lambda **kw: {},
            list_trade_pnl_by_strategy=lambda **kw: [],
        )
        trading_status_store = SimpleNamespace(load=lambda **kw: None)
        daily_session_state_store = SimpleNamespace(
            load=lambda **kw: None,
            save=lambda *a, **kw: None,
            list_by_session=lambda **kw: [],
        )
        strategy_flag_store = _FakeFlagStore()
        watchlist_store = SimpleNamespace(
            list_enabled=lambda *a: ["AAPL"],
            list_ignored=lambda *a: [],
        )
        option_order_store = _FakeOptStore()

    supervisor = RuntimeSupervisor(
        settings=settings,
        runtime=_FakeRuntime(),
        broker=SimpleNamespace(
            get_account=lambda: SimpleNamespace(
                equity=100_000.0, buying_power=200_000.0, trading_blocked=False
            ),
            list_open_orders=lambda: [],
            list_open_positions=lambda: [],
        ),
        market_data=SimpleNamespace(
            get_stock_bars=lambda **kw: {},
            get_daily_bars=lambda **kw: {},
        ),
        stream=None,
        close_runtime_fn=lambda _: None,
        connection_checker=lambda _: True,
        cycle_runner=lambda **kw: SimpleNamespace(intents=[]),
        cycle_intent_executor=lambda **kw: SimpleNamespace(
            submitted_exit_count=0,
            failed_exit_count=0,
            replaced_stop_count=0,
            submitted_stop_count=0,
            canceled_stop_count=0,
        ),
        order_dispatcher=lambda **kw: {"submitted_count": 0},
    )
    return supervisor, saved_flags, audit_events


def test_circuit_breaker_disables_strategy_below_threshold():
    """When rolling P&L ≤ -threshold, saves enabled=False flag and emits audit event."""
    supervisor, saved_flags, audit_events = _make_circuit_breaker_supervisor(
        rolling_pnl_by_strategy={"bear_orb": -600.0},
    )
    supervisor._check_option_strategy_circuit_breakers(
        session_date=date(2026, 5, 28),
        now=datetime(2026, 5, 28, 14, 0, tzinfo=timezone.utc),
    )

    assert len(saved_flags) == 1
    assert saved_flags[0].strategy_name == "bear_orb"
    assert saved_flags[0].enabled is False

    cb_events = [
        e for e in audit_events
        if e.event_type == "option_strategy_circuit_breaker_triggered"
    ]
    assert len(cb_events) == 1
    assert cb_events[0].payload["strategy_name"] == "bear_orb"
    assert cb_events[0].payload["rolling_pnl_usd"] == -600.0
    assert cb_events[0].payload["threshold_usd"] == -500.0
    assert cb_events[0].payload["window_days"] == 7


def test_circuit_breaker_no_op_when_above_threshold():
    """When rolling P&L > -threshold, no flag is saved."""
    supervisor, saved_flags, _ = _make_circuit_breaker_supervisor(
        rolling_pnl_by_strategy={"bear_orb": -400.0},  # -400 > -500 threshold
    )
    supervisor._check_option_strategy_circuit_breakers(
        session_date=date(2026, 5, 28),
        now=datetime(2026, 5, 28, 14, 0, tzinfo=timezone.utc),
    )
    assert saved_flags == []


def test_circuit_breaker_no_op_when_already_disabled():
    """When strategy flag already has enabled=False, no redundant save is made."""
    existing = StrategyFlag(
        strategy_name="bear_orb",
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        enabled=False,
    )
    supervisor, saved_flags, audit_events = _make_circuit_breaker_supervisor(
        rolling_pnl_by_strategy={"bear_orb": -600.0},
        existing_flag=existing,
    )
    supervisor._check_option_strategy_circuit_breakers(
        session_date=date(2026, 5, 28),
        now=datetime(2026, 5, 28, 14, 0, tzinfo=timezone.utc),
    )
    assert saved_flags == []
    cb_events = [
        e for e in audit_events
        if e.event_type == "option_strategy_circuit_breaker_triggered"
    ]
    assert cb_events == []


def test_circuit_breaker_skipped_when_config_zero():
    """With max_rolling_loss_usd=0.0 (disabled), no flags are written regardless of P&L."""
    supervisor, saved_flags, _ = _make_circuit_breaker_supervisor(
        rolling_pnl_by_strategy={"bear_orb": -999.0},
        max_rolling_loss_usd=0.0,
    )
    supervisor._check_option_strategy_circuit_breakers(
        session_date=date(2026, 5, 28),
        now=datetime(2026, 5, 28, 14, 0, tzinfo=timezone.utc),
    )
    assert saved_flags == []
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/unit/test_option_circuit_breaker.py::test_circuit_breaker_disables_strategy_below_threshold -v
```

Expected: `AttributeError: 'RuntimeSupervisor' object has no attribute '_check_option_strategy_circuit_breakers'`

- [ ] **Step 3: Add StrategyFlag to storage imports in supervisor.py**

In `src/alpaca_bot/runtime/supervisor.py`, replace lines 49–57:

```python
from alpaca_bot.storage import (
    AuditEvent,
    ConfidenceFloor,
    DailySessionState,
    EQUITY_SESSION_STATE_STRATEGY_NAME,
    GLOBAL_SESSION_STATE_STRATEGY_NAME,
    PositionRecord,
    TradingStatusValue,
)
```

with:

```python
from alpaca_bot.storage import (
    AuditEvent,
    ConfidenceFloor,
    DailySessionState,
    EQUITY_SESSION_STATE_STRATEGY_NAME,
    GLOBAL_SESSION_STATE_STRATEGY_NAME,
    PositionRecord,
    StrategyFlag,
    TradingStatusValue,
)
```

- [ ] **Step 4: Add call site in run_cycle_once**

In `src/alpaca_bot/runtime/supervisor.py`, replace:

```python
        option_chains_by_symbol: dict = {}
        option_order_store = getattr(self.runtime, "option_order_store", None)
        if self._option_chain_adapter is not None:
```

with:

```python
        option_chains_by_symbol: dict = {}
        option_order_store = getattr(self.runtime, "option_order_store", None)
        self._check_option_strategy_circuit_breakers(session_date=session_date, now=timestamp)
        if self._option_chain_adapter is not None:
```

- [ ] **Step 5: Add _check_option_strategy_circuit_breakers method**

Locate the private methods section. The exact insertion point is after `_check_and_update_floor_triggers` (starts line 1796, ends before `_effective_trading_status` at line 1909) — insert the new method between those two.

`StrategyFlagStore.save()` defaults to `commit=True`, which commits the transaction. This is intentional: the flag must be durably committed before the `_flag_store.load()` call at lines 828–843 later in the same cycle reads it back. Do NOT pass `commit=False`.

```python
    def _check_option_strategy_circuit_breakers(
        self, *, session_date: date, now: datetime
    ) -> None:
        """Disable any option strategy whose rolling realized P&L breaches the threshold.

        Reads closed option trade records anchored on buy-to-close date. Writes
        StrategyFlag(enabled=False) and emits an audit event the first time a
        strategy crosses the threshold. Subsequent cycles are no-ops (flag already
        disabled). Re-enable via: alpaca-bot-admin enable-strategy --strategy <name>.

        save() commits immediately (commit=True default) so the flag is visible to
        the _flag_store.load() check at lines 828-843 later in the same cycle.
        """
        if self.settings.option_strategy_max_rolling_loss_usd <= 0.0:
            return
        opt_store = getattr(self.runtime, "option_order_store", None)
        flag_store = getattr(self.runtime, "strategy_flag_store", None)
        if opt_store is None or flag_store is None:
            return

        window_days = self.settings.option_strategy_rolling_loss_days
        since_date = session_date - timedelta(days=window_days - 1)
        store_lock = getattr(self.runtime, "store_lock", None)

        try:
            with store_lock if store_lock is not None else contextlib.nullcontext():
                rolling_pnl = opt_store.rolling_realized_pnl_by_strategy(
                    trading_mode=self.settings.trading_mode,
                    strategy_version=self.settings.strategy_version,
                    since_date=since_date,
                    until_date=session_date,
                    market_timezone=str(self.settings.market_timezone),
                )
        except Exception:
            logger.exception(
                "_check_option_strategy_circuit_breakers: failed to query rolling P&L"
            )
            return

        threshold = -abs(self.settings.option_strategy_max_rolling_loss_usd)
        for strategy_name, pnl in rolling_pnl.items():
            if pnl > threshold:
                continue
            with store_lock if store_lock is not None else contextlib.nullcontext():
                existing = flag_store.load(
                    strategy_name=strategy_name,
                    trading_mode=self.settings.trading_mode,
                    strategy_version=self.settings.strategy_version,
                )
            if existing is not None and not existing.enabled:
                continue
            flag = StrategyFlag(
                strategy_name=strategy_name,
                trading_mode=self.settings.trading_mode,
                strategy_version=self.settings.strategy_version,
                enabled=False,
                updated_at=now,
            )
            with store_lock if store_lock is not None else contextlib.nullcontext():
                flag_store.save(flag)  # commit=True (default): flag visible to _flag_store.load() later this cycle
            self._append_audit(
                AuditEvent(
                    event_type="option_strategy_circuit_breaker_triggered",
                    payload={
                        "strategy_name": strategy_name,
                        "rolling_pnl_usd": round(pnl, 2),
                        "threshold_usd": threshold,
                        "window_days": window_days,
                    },
                    created_at=now,
                )
            )
            logger.warning(
                "Option strategy %s disabled by circuit breaker: "
                "rolling P&L %.2f < threshold %.2f (window: %d days)",
                strategy_name,
                pnl,
                threshold,
                window_days,
            )
```

- [ ] **Step 6: Run circuit breaker supervisor tests to verify they pass**

```bash
pytest tests/unit/test_option_circuit_breaker.py::test_circuit_breaker_disables_strategy_below_threshold \
       tests/unit/test_option_circuit_breaker.py::test_circuit_breaker_no_op_when_above_threshold \
       tests/unit/test_option_circuit_breaker.py::test_circuit_breaker_no_op_when_already_disabled \
       tests/unit/test_option_circuit_breaker.py::test_circuit_breaker_skipped_when_config_zero -v
```

Expected: 4 PASSED

- [ ] **Step 7: Run the entire test suite**

```bash
pytest -q
```

Expected: all tests pass. If any test fails, diagnose before committing.

- [ ] **Step 8: Commit**

```bash
git add src/alpaca_bot/runtime/supervisor.py tests/unit/test_option_circuit_breaker.py
git commit -m "feat: add option strategy rolling loss circuit breaker

Per-strategy rolling realized P&L computed over N-day window;
auto-disables strategy via StrategyFlag when threshold breached.
Re-enable via: alpaca-bot-admin enable-strategy --strategy <name>.
Set OPTION_STRATEGY_MAX_ROLLING_LOSS_USD=500 to activate."
```

---

## Post-Implementation Checklist

- [ ] Verify `StrategyFlag` is exported from `alpaca_bot.storage` (check `storage/__init__.py`)
- [ ] Confirm `StrategyFlag` dataclass `updated_at` field has a default factory (it does — `datetime.now(timezone.utc)`); passing `updated_at=now` in the constructor is valid
- [ ] Check that `settings.market_timezone` is accessible as a string (it's a `ZoneInfo` object — use `str()`)
- [ ] Confirm `_append_audit` method signature in supervisor accepts `AuditEvent` positionally
- [ ] No migration needed — uses existing `strategy_flags` table via `StrategyFlagStore`
