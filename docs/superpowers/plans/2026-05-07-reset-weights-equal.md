# Reset Weights to Equal Allocation — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `alpaca-bot-admin reset-weights` that writes 1/N equal weights for all active strategies so every strategy starts fresh with equal capital allocation regardless of Sharpe history.

**Architecture:** New subcommand in `admin/cli.py` following the existing `_run_close_excess()` pattern — a private `_reset_weights_equal()` function, injected store factories for testability, and an atomic AuditEvent + weight write (single commit).

**Tech Stack:** Python, argparse, psycopg2 (via existing ConnectionProtocol), pytest

---

## Grilling decisions baked in

- **Atomicity:** `StrategyWeightStore.upsert_many()` gains a `commit: bool = True` parameter so the weight rows and audit event can be committed in a single `connection.commit()` call, consistent with `_write_strategy_flag()` pattern.
- **Empty-names guard:** If all strategies are disabled via flags, `_reset_weights_equal()` raises `ValueError` with a clear message rather than crashing with `ZeroDivisionError`.
- **Paper vs live isolation:** `--mode` parameter scopes all DB writes to the specified trading mode. Paper and live weight rows are independent.
- **Supervisor restart required:** The running supervisor's in-memory `_session_capital_weights` cache is not updated by this command. A restart is required for same-day effect.

---

## Files

| File | Action | Responsibility |
|---|---|---|
| `src/alpaca_bot/storage/repositories.py` | Modify | Add `commit: bool = True` to `StrategyWeightStore.upsert_many()` |
| `src/alpaca_bot/admin/cli.py` | Modify | Add `reset-weights` subparser, `_reset_weights_equal()` function, wire into `main()` |
| `tests/unit/test_admin_reset_weights.py` | Create | All tests for the `reset-weights` command |

No migration needed — no schema changes.

---

### Task 1: Add `commit` parameter to `StrategyWeightStore.upsert_many()`

**Files:**
- Modify: `src/alpaca_bot/storage/repositories.py` lines 1402–1443

- [ ] **Step 1: Write a failing test for commit=False behavior**

Add to `tests/unit/test_strategy_weight_store.py` (if it exists) or inline as a standalone test. Since the existing tests likely test the commit=True path, add a test that verifies commit=False does NOT commit:

Check whether `tests/unit/test_strategy_weight_store.py` exists:
```bash
ls tests/unit/ | grep weight
```

If it doesn't exist, add the test to `tests/unit/test_admin_reset_weights.py` (Task 2 will include it). The key behavioral contract: `upsert_many(..., commit=False)` calls execute with commit=False but does NOT call `connection.commit()`.

- [ ] **Step 2: Edit `upsert_many()` in `repositories.py`**

Change the method signature and body. Find this block (lines 1402–1443):

```python
    def upsert_many(
        self,
        *,
        weights: dict[str, float],
        sharpes: dict[str, float],
        trading_mode: TradingMode,
        strategy_version: str,
        computed_at: datetime,
    ) -> None:
        try:
            for strategy_name, weight in weights.items():
                execute(
                    self._connection,
                    """
                    INSERT INTO strategy_weights (
                        strategy_name, trading_mode, strategy_version,
                        weight, sharpe, computed_at
                    )
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT (strategy_name, trading_mode, strategy_version)
                    DO UPDATE SET
                        weight = EXCLUDED.weight,
                        sharpe = EXCLUDED.sharpe,
                        computed_at = EXCLUDED.computed_at
                    """,
                    (
                        strategy_name,
                        trading_mode.value,
                        strategy_version,
                        weight,
                        sharpes.get(strategy_name, 0.0),
                        computed_at,
                    ),
                    commit=False,
                )
            self._connection.commit()
        except Exception:
            try:
                self._connection.rollback()
            except Exception:
                pass
            raise
```

Replace with:

```python
    def upsert_many(
        self,
        *,
        weights: dict[str, float],
        sharpes: dict[str, float],
        trading_mode: TradingMode,
        strategy_version: str,
        computed_at: datetime,
        commit: bool = True,
    ) -> None:
        try:
            for strategy_name, weight in weights.items():
                execute(
                    self._connection,
                    """
                    INSERT INTO strategy_weights (
                        strategy_name, trading_mode, strategy_version,
                        weight, sharpe, computed_at
                    )
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT (strategy_name, trading_mode, strategy_version)
                    DO UPDATE SET
                        weight = EXCLUDED.weight,
                        sharpe = EXCLUDED.sharpe,
                        computed_at = EXCLUDED.computed_at
                    """,
                    (
                        strategy_name,
                        trading_mode.value,
                        strategy_version,
                        weight,
                        sharpes.get(strategy_name, 0.0),
                        computed_at,
                    ),
                    commit=False,
                )
            if commit:
                self._connection.commit()
        except Exception:
            try:
                self._connection.rollback()
            except Exception:
                pass
            raise
```

- [ ] **Step 3: Run existing tests to verify no regressions**

```bash
cd /workspace/alpaca_bot
pytest tests/unit/ -x -q 2>&1 | tail -10
```

Expected: All existing tests pass (existing callers use the default `commit=True`, so behavior is unchanged).

- [ ] **Step 4: Commit**

```bash
git add src/alpaca_bot/storage/repositories.py
git commit -m "feat: add commit param to StrategyWeightStore.upsert_many for atomic writes"
```

---

### Task 2: Write failing tests for `reset-weights`

**Files:**
- Create: `tests/unit/test_admin_reset_weights.py`

- [ ] **Step 1: Write the test file**

```python
# tests/unit/test_admin_reset_weights.py
from __future__ import annotations

import io
from datetime import datetime, timezone
from types import SimpleNamespace

from alpaca_bot.admin.cli import main
from alpaca_bot.config import Settings, TradingMode
from alpaca_bot.storage import AuditEvent, StrategyFlag
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

    def __call__(self, connection: object) -> object:
        return self.store


class _RecordingAuditEventStore:
    def __init__(self) -> None:
        self.appended: list[AuditEvent] = []

    def append(self, event: AuditEvent, *, commit: bool = True) -> None:
        self.appended.append(event)


class _RecordingStrategyWeightStore:
    def __init__(self) -> None:
        self.upserted: list[dict] = []

    def upsert_many(
        self, *, weights, sharpes, trading_mode, strategy_version, computed_at, commit: bool = True
    ) -> None:
        self.upserted.append({
            "weights": dict(weights),
            "sharpes": dict(sharpes),
            "trading_mode": trading_mode,
            "strategy_version": strategy_version,
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

    exit_code = _run(
        argv=["reset-weights", "--mode", "paper", "--strategy-version", "v1-breakout"],
        settings=settings,
        weight_store=weight_store,
        flag_store=_RecordingStrategyFlagStore(),
        audit_store=_RecordingAuditEventStore(),
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

    exit_code = _run(
        argv=["reset-weights", "--mode", "paper", "--strategy-version", "v1-breakout"],
        settings=settings,
        weight_store=weight_store,
        flag_store=flag_store,
        audit_store=_RecordingAuditEventStore(),
    )

    assert exit_code == 0
    call = weight_store.upserted[0]
    assert "breakout" not in call["weights"]
    expected_n = len(STRATEGY_REGISTRY) - 1
    assert len(call["weights"]) == expected_n
    expected_weight = 1.0 / expected_n
    for w in call["weights"].values():
        assert abs(w - expected_weight) < 1e-10


def test_reset_weights_raises_when_all_strategies_disabled() -> None:
    """If every strategy flag is disabled, command must raise ValueError with a clear message."""
    import pytest
    settings = _settings(enable_options=False)
    all_disabled = list(STRATEGY_REGISTRY)
    flag_store = _RecordingStrategyFlagStore(disabled=all_disabled)

    with pytest.raises(SystemExit):
        _run(
            argv=["reset-weights", "--mode", "paper", "--strategy-version", "v1-breakout"],
            settings=settings,
            weight_store=_RecordingStrategyWeightStore(),
            flag_store=flag_store,
            audit_store=_RecordingAuditEventStore(),
        )


# ---------------------------------------------------------------------------
# Audit trail
# ---------------------------------------------------------------------------

def test_reset_weights_appends_audit_event() -> None:
    """reset-weights must append strategy_weights_reset AuditEvent."""
    settings = _settings(enable_options=False)
    audit_store = _RecordingAuditEventStore()

    _run(
        argv=["reset-weights", "--mode", "paper", "--strategy-version", "v1-breakout"],
        settings=settings,
        weight_store=_RecordingStrategyWeightStore(),
        flag_store=_RecordingStrategyFlagStore(),
        audit_store=audit_store,
    )

    assert len(audit_store.appended) == 1
    event = audit_store.appended[0]
    assert event.event_type == "strategy_weights_reset"
    assert event.payload["mode"] == "paper"
    assert event.payload["version"] == "v1-breakout"
    assert event.payload["strategy_count"] == str(len(STRATEGY_REGISTRY))
    for name in STRATEGY_REGISTRY:
        assert name in event.payload


# ---------------------------------------------------------------------------
# Dry run
# ---------------------------------------------------------------------------

def test_reset_weights_dry_run_prints_plan_without_db_writes() -> None:
    """--dry-run must print summary and strategy names without touching the DB."""
    settings = _settings(enable_options=False)
    weight_store = _RecordingStrategyWeightStore()
    audit_store = _RecordingAuditEventStore()
    stdout = io.StringIO()

    exit_code = _run(
        argv=["reset-weights", "--dry-run", "--mode", "paper", "--strategy-version", "v1-breakout"],
        settings=settings,
        weight_store=weight_store,
        flag_store=_RecordingStrategyFlagStore(),
        audit_store=audit_store,
        stdout=stdout,
    )

    assert exit_code == 0
    assert weight_store.upserted == [], "dry-run must not write to DB"
    assert audit_store.appended == [], "dry-run must not append audit events"
    rendered = stdout.getvalue()
    assert "dry-run" in rendered.lower()
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

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd /workspace/alpaca_bot
pytest tests/unit/test_admin_reset_weights.py -v 2>&1 | head -30
```

Expected: `error: argument command: invalid choice: 'reset-weights'` or similar.

- [ ] **Step 3: Commit the failing tests**

```bash
git add tests/unit/test_admin_reset_weights.py
git commit -m "test: failing tests for reset-weights admin command"
```

---

### Task 3: Implement `reset-weights` in admin CLI

**Files:**
- Modify: `src/alpaca_bot/admin/cli.py`

- [ ] **Step 1: Update imports**

Change line 26 from:
```python
from alpaca_bot.strategy import STRATEGY_REGISTRY
```
to:
```python
from alpaca_bot.strategy import OPTION_STRATEGY_FACTORIES, STRATEGY_REGISTRY
```

Add `StrategyWeightStore,` to the `from alpaca_bot.storage import (...)` block, after the line `StrategyFlagStore,`.

- [ ] **Step 2: Add `reset-weights` subparser in `build_parser()`**

Insert this block immediately before `return parser` (after the `cpf_parser` block):

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

- [ ] **Step 3: Add new factory parameters to `main()` signature**

Add to the end of the parameter list (before `-> int:`):

```python
    strategy_weight_store_factory: Callable[[ConnectionProtocol], StrategyWeightStore] = StrategyWeightStore,
    strategy_flag_store_factory: Callable[[ConnectionProtocol], StrategyFlagStore] = StrategyFlagStore,
```

- [ ] **Step 4: Wire the `reset-weights` case into `main()`**

In `main()`, add the `elif args.command == "reset-weights":` block. Place it before the `else:` at the very end of the command dispatch chain (around line 404):

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

Add this function after `_run_cancel_partial_fills()` and before `_make_default_broker()`:

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

    if not active_names:
        raise ValueError(
            "No active strategies — all strategy flags are disabled. "
            "Enable at least one strategy before resetting weights."
        )

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
        commit=False,
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
        commit=False,
    )
    connection.commit()
    print(
        f"reset strategy_count={n} equal_weight={equal_weight * 100:.1f}%"
        f" mode={trading_mode.value} version={strategy_version}",
        file=stdout,
    )
```

- [ ] **Step 6: Run the new tests and verify they pass**

```bash
cd /workspace/alpaca_bot
pytest tests/unit/test_admin_reset_weights.py -v
```

Expected: All 7 tests pass.

- [ ] **Step 7: Run full test suite to catch regressions**

```bash
cd /workspace/alpaca_bot
pytest tests/unit/ -x -q 2>&1 | tail -10
```

Expected: All tests pass.

- [ ] **Step 8: Commit**

```bash
git add src/alpaca_bot/admin/cli.py
git commit -m "feat: add reset-weights admin command for equal allocation"
```

---

## Operator usage after deployment

```bash
# 1. Confirm options trading is enabled in env:
grep ENABLE_OPTIONS_TRADING /etc/alpaca_bot/alpaca-bot.env

# 2. Preview (dry run):
docker exec deploy-supervisor-1 alpaca-bot-admin reset-weights --dry-run

# 3. Apply:
docker exec deploy-supervisor-1 alpaca-bot-admin reset-weights

# 4. Restart supervisor to pick up new weights in-memory:
sudo -n docker compose -f /workspace/alpaca_bot/deploy/compose.yaml \
  --env-file /etc/alpaca_bot/alpaca-bot.env restart supervisor
```
