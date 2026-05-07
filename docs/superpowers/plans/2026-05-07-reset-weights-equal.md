# Reset Weights to Equal Allocation — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `alpaca-bot-admin reset-weights` that writes 1/N equal weights for all active strategies so every strategy starts fresh with equal capital allocation regardless of Sharpe history.

**Architecture:** New subcommand in `admin/cli.py` following the existing `_run_close_excess()` pattern — a private `_reset_weights_equal()` function, injected store factories for testability, and an atomic AuditEvent write.

**Tech Stack:** Python, argparse, psycopg2 (via existing ConnectionProtocol), pytest

---

## Files

| File | Action | Responsibility |
|---|---|---|
| `src/alpaca_bot/admin/cli.py` | Modify | Add `reset-weights` subparser, `_reset_weights_equal()` function, wire into `main()` |
| `tests/unit/test_admin_reset_weights.py` | Create | All tests for the `reset-weights` command |

No migration needed — no schema changes.

---

### Task 1: Write failing tests for `reset-weights`

**Files:**
- Create: `tests/unit/test_admin_reset_weights.py`

- [ ] **Step 1: Write the test file**

```python
# tests/unit/test_admin_reset_weights.py
from __future__ import annotations

import io
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from alpaca_bot.admin.cli import main
from alpaca_bot.config import Settings, TradingMode
from alpaca_bot.storage import AuditEvent, StrategyFlag, StrategyWeight
from alpaca_bot.strategy import STRATEGY_REGISTRY, OPTION_STRATEGY_FACTORIES

from tests.unit.helpers import _base_env


def _settings(*, enable_options: bool = False) -> Settings:
    env = {**_base_env()}
    if enable_options:
        env["ENABLE_OPTIONS_TRADING"] = "true"
    return Settings.from_env(env)


class _StoreFactoryStub:
    def __init__(self, store: object) -> None:
        self.store = store
        self.connections: list[object] = []

    def __call__(self, connection: object) -> object:
        self.connections.append(connection)
        return self.store


class _RecordingAuditEventStore:
    def __init__(self) -> None:
        self.appended: list[AuditEvent] = []

    def append(self, event: AuditEvent, *, commit: bool = True) -> None:
        self.appended.append(event)


class _RecordingStrategyWeightStore:
    def __init__(self) -> None:
        self.upserted: list[dict] = []

    def upsert_many(self, *, weights, sharpes, trading_mode, strategy_version, computed_at) -> None:
        self.upserted.append({
            "weights": dict(weights),
            "sharpes": dict(sharpes),
            "trading_mode": trading_mode,
            "strategy_version": strategy_version,
            "computed_at": computed_at,
        })


class _RecordingStrategyFlagStore:
    def __init__(self, disabled: list[str] | None = None) -> None:
        self._disabled = set(disabled or [])

    def list_all(self, *, trading_mode: TradingMode, strategy_version: str) -> list[StrategyFlag]:
        return [
            StrategyFlag(
                strategy_name=name,
                trading_mode=trading_mode,
                strategy_version=strategy_version,
                enabled=False,
                updated_at=datetime(2026, 5, 7, 14, 0, tzinfo=timezone.utc),
            )
            for name in self._disabled
        ]


def _run(
    *,
    argv: list[str],
    settings: Settings,
    weight_store: _RecordingStrategyWeightStore,
    flag_store: _RecordingStrategyFlagStore,
    audit_store: _RecordingAuditEventStore,
    stdout: io.StringIO | None = None,
) -> int:
    connection = SimpleNamespace(commit=lambda: None, rollback=lambda: None, close=lambda: None)
    now = datetime(2026, 5, 7, 14, 30, tzinfo=timezone.utc)
    return main(
        argv,
        connect=lambda: connection,
        settings=settings,
        now=lambda: now,
        strategy_weight_store_factory=_StoreFactoryStub(weight_store),
        strategy_flag_store_factory=_StoreFactoryStub(flag_store),
        audit_event_store_factory=_StoreFactoryStub(audit_store),
        stdout=stdout or io.StringIO(),
    )


# ---------------------------------------------------------------------------
# Core: equal weight distribution
# ---------------------------------------------------------------------------

def test_reset_weights_equity_only_writes_equal_weights_for_all_11_strategies() -> None:
    """With options disabled, all 11 STRATEGY_REGISTRY entries get 1/11 weight."""
    settings = _settings(enable_options=False)
    weight_store = _RecordingStrategyWeightStore()
    flag_store = _RecordingStrategyFlagStore()
    audit_store = _RecordingAuditEventStore()

    exit_code = _run(
        argv=["reset-weights", "--mode", "paper", "--strategy-version", "v1-breakout"],
        settings=settings,
        weight_store=weight_store,
        flag_store=flag_store,
        audit_store=audit_store,
    )

    assert exit_code == 0
    assert len(weight_store.upserted) == 1
    call = weight_store.upserted[0]
    expected_names = set(STRATEGY_REGISTRY)
    assert set(call["weights"].keys()) == expected_names
    expected_weight = 1.0 / len(expected_names)
    for name, w in call["weights"].items():
        assert abs(w - expected_weight) < 1e-10, f"{name}: expected {expected_weight}, got {w}"
    assert all(s == 0.0 for s in call["sharpes"].values())
    assert call["trading_mode"] == TradingMode.PAPER
    assert call["strategy_version"] == "v1-breakout"


def test_reset_weights_includes_option_strategies_when_options_enabled() -> None:
    """With options enabled, all 23 strategies (11 equity + 12 option) get 1/23 weight."""
    settings = _settings(enable_options=True)
    weight_store = _RecordingStrategyWeightStore()
    flag_store = _RecordingStrategyFlagStore()
    audit_store = _RecordingAuditEventStore()

    exit_code = _run(
        argv=["reset-weights", "--mode", "paper", "--strategy-version", "v1-breakout"],
        settings=settings,
        weight_store=weight_store,
        flag_store=flag_store,
        audit_store=audit_store,
    )

    assert exit_code == 0
    call = weight_store.upserted[0]
    expected_names = set(STRATEGY_REGISTRY) | set(OPTION_STRATEGY_FACTORIES)
    assert set(call["weights"].keys()) == expected_names
    expected_weight = 1.0 / len(expected_names)
    for name, w in call["weights"].items():
        assert abs(w - expected_weight) < 1e-10


def test_reset_weights_excludes_disabled_strategy() -> None:
    """A strategy with enabled=False flag must be omitted from the weight pool."""
    settings = _settings(enable_options=False)
    weight_store = _RecordingStrategyWeightStore()
    flag_store = _RecordingStrategyFlagStore(disabled=["breakout"])
    audit_store = _RecordingAuditEventStore()

    exit_code = _run(
        argv=["reset-weights", "--mode", "paper", "--strategy-version", "v1-breakout"],
        settings=settings,
        weight_store=weight_store,
        flag_store=flag_store,
        audit_store=audit_store,
    )

    assert exit_code == 0
    call = weight_store.upserted[0]
    assert "breakout" not in call["weights"]
    expected_n = len(STRATEGY_REGISTRY) - 1
    assert len(call["weights"]) == expected_n
    expected_weight = 1.0 / expected_n
    for w in call["weights"].values():
        assert abs(w - expected_weight) < 1e-10


# ---------------------------------------------------------------------------
# Audit trail
# ---------------------------------------------------------------------------

def test_reset_weights_appends_audit_event() -> None:
    """reset-weights must append strategy_weights_reset AuditEvent."""
    settings = _settings(enable_options=False)
    weight_store = _RecordingStrategyWeightStore()
    flag_store = _RecordingStrategyFlagStore()
    audit_store = _RecordingAuditEventStore()

    _run(
        argv=["reset-weights", "--mode", "paper", "--strategy-version", "v1-breakout"],
        settings=settings,
        weight_store=weight_store,
        flag_store=flag_store,
        audit_store=audit_store,
    )

    assert len(audit_store.appended) == 1
    event = audit_store.appended[0]
    assert event.event_type == "strategy_weights_reset"
    assert event.payload["mode"] == "paper"
    assert event.payload["version"] == "v1-breakout"
    assert event.payload["strategy_count"] == str(len(STRATEGY_REGISTRY))
    # All strategy names should appear in payload
    for name in STRATEGY_REGISTRY:
        assert name in event.payload


# ---------------------------------------------------------------------------
# Dry run
# ---------------------------------------------------------------------------

def test_reset_weights_dry_run_prints_plan_without_db_writes() -> None:
    """--dry-run must print summary and strategy names without touching the DB."""
    settings = _settings(enable_options=False)
    weight_store = _RecordingStrategyWeightStore()
    flag_store = _RecordingStrategyFlagStore()
    audit_store = _RecordingAuditEventStore()
    stdout = io.StringIO()

    exit_code = _run(
        argv=["reset-weights", "--dry-run", "--mode", "paper", "--strategy-version", "v1-breakout"],
        settings=settings,
        weight_store=weight_store,
        flag_store=flag_store,
        audit_store=audit_store,
        stdout=stdout,
    )

    assert exit_code == 0
    assert weight_store.upserted == [], "dry-run must not write to DB"
    assert audit_store.appended == [], "dry-run must not append audit events"
    rendered = stdout.getvalue()
    assert "dry-run" in rendered.lower()
    # At least one equity strategy name should appear in dry-run output
    assert "breakout" in rendered


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def test_reset_weights_prints_summary_to_stdout() -> None:
    """Non-dry-run execution must print a summary line."""
    settings = _settings(enable_options=False)
    stdout = io.StringIO()

    _run(
        argv=["reset-weights", "--mode", "paper", "--strategy-version", "v1-breakout"],
        settings=settings,
        weight_store=_RecordingStrategyWeightStore(),
        flag_store=_RecordingStrategyFlagStore(),
        audit_store=_RecordingAuditEventStore(),
        stdout=stdout,
    )

    rendered = stdout.getvalue()
    assert "reset" in rendered
    assert "paper" in rendered
```

- [ ] **Step 2: Run tests to confirm they fail with the expected error**

```bash
cd /workspace/alpaca_bot
pytest tests/unit/test_admin_reset_weights.py -v 2>&1 | head -40
```

Expected: `error: argument command: invalid choice: 'reset-weights'` or similar — confirms tests exercise the right path.

- [ ] **Step 3: Commit the failing tests**

```bash
git add tests/unit/test_admin_reset_weights.py
git commit -m "test: failing tests for reset-weights admin command"
```

---

### Task 2: Implement `reset-weights` in admin CLI

**Files:**
- Modify: `src/alpaca_bot/admin/cli.py`

- [ ] **Step 1: Add imports at the top of `cli.py`**

After the existing imports, add:

```python
from alpaca_bot.storage import (
    ...existing imports...,
    StrategyWeightStore,
)
from alpaca_bot.strategy import STRATEGY_REGISTRY, OPTION_STRATEGY_FACTORIES
```

The existing import block already imports `StrategyFlagStore` and `StrategyFlag`. Add `StrategyWeightStore` to the `from alpaca_bot.storage import (...)` block. Add `OPTION_STRATEGY_FACTORIES` to the `from alpaca_bot.strategy import STRATEGY_REGISTRY` line.

Exact edit — change line 26 from:
```python
from alpaca_bot.strategy import STRATEGY_REGISTRY
```
to:
```python
from alpaca_bot.strategy import OPTION_STRATEGY_FACTORIES, STRATEGY_REGISTRY
```

And add `StrategyWeightStore,` to the storage import block after `StrategyFlagStore,`.

- [ ] **Step 2: Add `reset-weights` subparser in `build_parser()`**

Insert after the `cpf_parser` block (before the closing `return parser`):

```python
    rw_parser = subparsers.add_parser("reset-weights")
    rw_parser.add_argument(
        "--mode",
        choices=[mode.value for mode in TradingMode],
        default=defaults.trading_mode.value,
    )
    rw_parser.add_argument("--strategy-version", default=defaults.strategy_version)
    rw_parser.add_argument("--dry-run", action="store_true")
```

- [ ] **Step 3: Add `strategy_weight_store_factory` and `strategy_flag_store_factory` parameters to `main()`**

Change the `main()` signature from:

```python
def main(
    argv: Sequence[str] | None = None,
    *,
    connect: Callable[[], ConnectionProtocol] | None = None,
    trading_status_store_factory: Callable[[ConnectionProtocol], TradingStatusStore] = TradingStatusStore,
    audit_event_store_factory: Callable[[ConnectionProtocol], AuditEventStore] = AuditEventStore,
    now: Callable[[], datetime] | None = None,
    stdout: TextIO | None = None,
    settings: Settings | None = None,
    notifier: Notifier | None = None,
    broker_factory: Callable[["Settings"], object] | None = None,
    position_store_factory: Callable[[ConnectionProtocol], PositionStore] = PositionStore,
    order_store_factory: Callable[[ConnectionProtocol], OrderStore] = OrderStore,
) -> int:
```

to:

```python
def main(
    argv: Sequence[str] | None = None,
    *,
    connect: Callable[[], ConnectionProtocol] | None = None,
    trading_status_store_factory: Callable[[ConnectionProtocol], TradingStatusStore] = TradingStatusStore,
    audit_event_store_factory: Callable[[ConnectionProtocol], AuditEventStore] = AuditEventStore,
    now: Callable[[], datetime] | None = None,
    stdout: TextIO | None = None,
    settings: Settings | None = None,
    notifier: Notifier | None = None,
    broker_factory: Callable[["Settings"], object] | None = None,
    position_store_factory: Callable[[ConnectionProtocol], PositionStore] = PositionStore,
    order_store_factory: Callable[[ConnectionProtocol], OrderStore] = OrderStore,
    strategy_weight_store_factory: Callable[[ConnectionProtocol], StrategyWeightStore] = StrategyWeightStore,
    strategy_flag_store_factory: Callable[[ConnectionProtocol], StrategyFlagStore] = StrategyFlagStore,
) -> int:
```

- [ ] **Step 4: Wire the `reset-weights` case into `main()`**

In `main()`, add the `reset-weights` handler before the `else: raise ValueError(...)` at the bottom. Insert it just before the `else` block:

```python
        elif args.command == "reset-weights":
            _reset_weights_equal(
                connection=connection,
                event_store=audit_store,
                settings=resolved_settings,
                trading_mode=trading_mode,
                strategy_version=strategy_version,
                dry_run=args.dry_run,
                now=timestamp,
                stdout=stdout or sys.stdout,
                weight_store_factory=strategy_weight_store_factory,
                flag_store_factory=strategy_flag_store_factory,
            )
```

- [ ] **Step 5: Add `_reset_weights_equal()` function**

Add this function after `_run_cancel_partial_fills()`:

```python
def _reset_weights_equal(
    *,
    connection: ConnectionProtocol,
    event_store: AuditEventStore,
    settings: Settings,
    trading_mode: TradingMode,
    strategy_version: str,
    dry_run: bool,
    now: datetime,
    stdout: TextIO,
    weight_store_factory: Callable[[ConnectionProtocol], StrategyWeightStore] = StrategyWeightStore,
    flag_store_factory: Callable[[ConnectionProtocol], StrategyFlagStore] = StrategyFlagStore,
) -> None:
    flag_store = flag_store_factory(connection)
    disabled = {
        f.strategy_name
        for f in flag_store.list_all(
            trading_mode=trading_mode,
            strategy_version=strategy_version,
        )
        if not f.enabled
    }

    all_names: list[str] = list(STRATEGY_REGISTRY)
    if settings.enable_options_trading:
        all_names += list(OPTION_STRATEGY_FACTORIES)
    active_names = [n for n in all_names if n not in disabled]

    n = len(active_names)
    equal_weight = 1.0 / n
    weights = {name: equal_weight for name in active_names}
    sharpes = {name: 0.0 for name in active_names}

    if dry_run:
        print(
            f"[dry-run] would reset {n} strategies to equal_weight={equal_weight * 100:.1f}%",
            file=stdout,
        )
        print(", ".join(sorted(active_names)), file=stdout)
        return

    weight_store = weight_store_factory(connection)
    weight_store.upsert_many(
        weights=weights,
        sharpes=sharpes,
        trading_mode=trading_mode,
        strategy_version=strategy_version,
        computed_at=now,
    )
    event_store.append(
        AuditEvent(
            event_type="strategy_weights_reset",
            payload={
                "mode": trading_mode.value,
                "version": strategy_version,
                "strategy_count": str(n),
                "equal_weight": str(round(equal_weight, 6)),
                **{name: str(round(equal_weight, 4)) for name in active_names},
            },
            created_at=now,
        ),
        commit=True,
    )
    print(
        f"reset strategy_count={n} equal_weight={equal_weight * 100:.1f}%"
        f" mode={trading_mode.value} version={strategy_version}",
        file=stdout,
    )
```

- [ ] **Step 6: Run tests and verify they pass**

```bash
cd /workspace/alpaca_bot
pytest tests/unit/test_admin_reset_weights.py -v
```

Expected output: All 6 tests pass.

- [ ] **Step 7: Run full test suite to catch regressions**

```bash
cd /workspace/alpaca_bot
pytest tests/unit/ -x -q 2>&1 | tail -20
```

Expected: All tests pass.

- [ ] **Step 8: Commit the implementation**

```bash
git add src/alpaca_bot/admin/cli.py
git commit -m "feat: add reset-weights admin command for equal allocation"
```

---

## Operator usage after deployment

After both commits are deployed:

```bash
# 1. Ensure ENABLE_OPTIONS_TRADING=true is in /etc/alpaca_bot/alpaca-bot.env
# 2. Run reset-weights (preview first)
docker exec deploy-supervisor-1 alpaca-bot-admin reset-weights --dry-run
# 3. Apply
docker exec deploy-supervisor-1 alpaca-bot-admin reset-weights
# 4. Restart supervisor to clear in-memory weight cache
docker restart deploy-supervisor-1
```
