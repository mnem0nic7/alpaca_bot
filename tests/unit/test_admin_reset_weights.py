from __future__ import annotations

import io
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

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

def test_reset_weights_equity_only_writes_equal_weights_for_all_strategies() -> None:
    """With options disabled, all equity strategies in STRATEGY_REGISTRY get 1/N weight."""
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
    """With options enabled, equity + option strategies all get 1/N weight."""
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
    """If every strategy flag is disabled, command must raise ValueError."""
    settings = _settings(enable_options=False)
    all_disabled = list(STRATEGY_REGISTRY)
    flag_store = _RecordingStrategyFlagStore(disabled=all_disabled)

    with pytest.raises(ValueError, match="No active strategies"):
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
    """reset-weights must append strategy_weights_reset AuditEvent with strategy names."""
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
    """--dry-run must print summary without touching the DB or appending audit events."""
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
    """Non-dry-run execution must print a confirmation summary."""
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
