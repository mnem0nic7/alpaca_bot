from __future__ import annotations

import io
import sys
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from alpaca_bot.admin.cli import main
from alpaca_bot.config import Settings, TradingMode
from alpaca_bot.storage import AuditEvent, ConfidenceFloor

from tests.unit.helpers import _base_env


def _settings() -> Settings:
    return Settings.from_env(_base_env())


def _now() -> datetime:
    return datetime(2026, 5, 8, 14, 0, tzinfo=timezone.utc)


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


class _RecordingConfidenceFloorStore:
    def __init__(self, existing: ConfidenceFloor | None = None) -> None:
        self._existing = existing
        self.upserted: list[ConfidenceFloor] = []

    def load(self, *, trading_mode: TradingMode, strategy_version: str) -> ConfidenceFloor | None:
        return self._existing

    def upsert(self, rec: ConfidenceFloor, *, commit: bool = True) -> None:
        self.upserted.append(rec)


def _run(
    argv: list[str],
    *,
    settings: Settings | None = None,
    floor_store: _RecordingConfidenceFloorStore | None = None,
    audit_store: _RecordingAuditEventStore | None = None,
    stdout: io.StringIO | None = None,
) -> int:
    _settings_val = settings or _settings()
    _floor_store = floor_store or _RecordingConfidenceFloorStore()
    _audit_store = audit_store or _RecordingAuditEventStore()
    connection = SimpleNamespace(commit=lambda: None, rollback=lambda: None, close=lambda: None)
    return main(
        argv,
        connect=lambda: connection,
        settings=_settings_val,
        now=lambda: _now(),
        audit_event_store_factory=_StoreFactoryStub(_audit_store),
        confidence_floor_store_factory=_StoreFactoryStub(_floor_store),
        stdout=stdout or io.StringIO(),
    )


# ---------------------------------------------------------------------------
# Core: sets floor_value and manual_floor_baseline to the provided value
# ---------------------------------------------------------------------------

def test_set_confidence_floor_writes_floor_value_and_baseline() -> None:
    """floor_value and manual_floor_baseline are both set to the provided value."""
    floor_store = _RecordingConfidenceFloorStore()
    audit_store = _RecordingAuditEventStore()

    exit_code = _run(
        ["set-confidence-floor", "--value", "0.15", "--reason", "vol subsided"],
        floor_store=floor_store,
        audit_store=audit_store,
    )

    assert exit_code == 0
    assert len(floor_store.upserted) == 1
    rec = floor_store.upserted[0]
    assert rec.floor_value == 0.15
    assert rec.manual_floor_baseline == 0.15
    assert rec.set_by == "operator"
    assert rec.reason == "vol subsided"


# ---------------------------------------------------------------------------
# Watermark preservation
# ---------------------------------------------------------------------------

def test_set_confidence_floor_preserves_existing_equity_high_watermark() -> None:
    """equity_high_watermark is taken from the existing record, not reset to 0."""
    existing = ConfidenceFloor(
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        floor_value=0.10,
        manual_floor_baseline=0.10,
        equity_high_watermark=125_000.0,
        set_by="operator",
        reason="old reason",
    )
    floor_store = _RecordingConfidenceFloorStore(existing=existing)

    exit_code = _run(
        ["set-confidence-floor", "--value", "0.20", "--reason", "risk-off"],
        floor_store=floor_store,
    )

    assert exit_code == 0
    rec = floor_store.upserted[0]
    assert rec.equity_high_watermark == 125_000.0


def test_set_confidence_floor_uses_zero_watermark_when_no_existing_record() -> None:
    """When no record exists, equity_high_watermark defaults to 0.0."""
    floor_store = _RecordingConfidenceFloorStore(existing=None)

    exit_code = _run(
        ["set-confidence-floor", "--value", "0.25", "--reason", "baseline"],
        floor_store=floor_store,
    )

    assert exit_code == 0
    rec = floor_store.upserted[0]
    assert rec.equity_high_watermark == 0.0


# ---------------------------------------------------------------------------
# Audit event
# ---------------------------------------------------------------------------

def test_set_confidence_floor_emits_audit_event_with_correct_payload() -> None:
    """confidence_floor_manual_set audit event is emitted with value, reason, previous_value, timestamp."""
    existing = ConfidenceFloor(
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        floor_value=0.10,
        manual_floor_baseline=0.10,
        equity_high_watermark=0.0,
        set_by="operator",
        reason="old",
    )
    floor_store = _RecordingConfidenceFloorStore(existing=existing)
    audit_store = _RecordingAuditEventStore()

    exit_code = _run(
        ["set-confidence-floor", "--value", "0.30", "--reason", "vol spike"],
        floor_store=floor_store,
        audit_store=audit_store,
    )

    assert exit_code == 0
    assert len(audit_store.appended) == 1
    event = audit_store.appended[0]
    assert event.event_type == "confidence_floor_manual_set"
    assert event.payload["value"] == 0.30
    assert event.payload["reason"] == "vol spike"
    assert event.payload["previous_value"] == 0.10
    assert "timestamp" in event.payload


def test_set_confidence_floor_audit_event_previous_value_is_none_when_no_existing_record() -> None:
    """previous_value is None in the audit payload when no prior record exists."""
    floor_store = _RecordingConfidenceFloorStore(existing=None)
    audit_store = _RecordingAuditEventStore()

    _run(
        ["set-confidence-floor", "--value", "0.15", "--reason", "first time"],
        floor_store=floor_store,
        audit_store=audit_store,
    )

    event = audit_store.appended[0]
    assert event.payload["previous_value"] is None


# ---------------------------------------------------------------------------
# Validation: out-of-range values
# ---------------------------------------------------------------------------

def test_set_confidence_floor_rejects_negative_value() -> None:
    """value < 0.0 must exit with non-zero code and not write to DB."""
    floor_store = _RecordingConfidenceFloorStore()
    audit_store = _RecordingAuditEventStore()
    stdout = io.StringIO()

    with pytest.raises(SystemExit) as exc_info:
        _run(
            ["set-confidence-floor", "--value", "-0.01", "--reason", "oops"],
            floor_store=floor_store,
            audit_store=audit_store,
            stdout=stdout,
        )

    assert exc_info.value.code != 0
    assert floor_store.upserted == []
    assert audit_store.appended == []


def test_set_confidence_floor_rejects_value_greater_than_one() -> None:
    """value > 1.0 must exit with non-zero code and not write to DB."""
    floor_store = _RecordingConfidenceFloorStore()
    audit_store = _RecordingAuditEventStore()

    with pytest.raises(SystemExit) as exc_info:
        _run(
            ["set-confidence-floor", "--value", "1.01", "--reason", "oops"],
            floor_store=floor_store,
            audit_store=audit_store,
        )

    assert exc_info.value.code != 0
    assert floor_store.upserted == []
    assert audit_store.appended == []


# ---------------------------------------------------------------------------
# Boundary values accepted
# ---------------------------------------------------------------------------

def test_set_confidence_floor_accepts_zero() -> None:
    """0.0 is a valid floor value."""
    floor_store = _RecordingConfidenceFloorStore()
    exit_code = _run(
        ["set-confidence-floor", "--value", "0.0", "--reason", "aggressive"],
        floor_store=floor_store,
    )
    assert exit_code == 0
    assert floor_store.upserted[0].floor_value == 0.0


def test_set_confidence_floor_accepts_one() -> None:
    """1.0 is a valid floor value."""
    floor_store = _RecordingConfidenceFloorStore()
    exit_code = _run(
        ["set-confidence-floor", "--value", "1.0", "--reason", "full stop"],
        floor_store=floor_store,
    )
    assert exit_code == 0
    assert floor_store.upserted[0].floor_value == 1.0


# ---------------------------------------------------------------------------
# --reason is required
# ---------------------------------------------------------------------------

def test_set_confidence_floor_requires_reason_argument() -> None:
    """Omitting --reason must cause argparse to exit with non-zero code."""
    with pytest.raises(SystemExit) as exc_info:
        _run(["set-confidence-floor", "--value", "0.20"])

    assert exc_info.value.code != 0


# ---------------------------------------------------------------------------
# --value is required
# ---------------------------------------------------------------------------

def test_set_confidence_floor_requires_value_argument() -> None:
    """Omitting --value must cause argparse to exit with non-zero code."""
    with pytest.raises(SystemExit) as exc_info:
        _run(["set-confidence-floor", "--reason", "some reason"])

    assert exc_info.value.code != 0


# ---------------------------------------------------------------------------
# Success prints confirmation message
# ---------------------------------------------------------------------------

def test_set_confidence_floor_success_prints_confirmation_message() -> None:
    """Success should print a confirmation message to stdout."""
    floor_store = _RecordingConfidenceFloorStore()
    audit_store = _RecordingAuditEventStore()
    stdout = io.StringIO()

    exit_code = _run(
        ["set-confidence-floor", "--value", "0.35", "--reason", "market change"],
        floor_store=floor_store,
        audit_store=audit_store,
        stdout=stdout,
    )

    assert exit_code == 0
    output = stdout.getvalue()
    assert len(output) > 0
    assert "confidence_floor set" in output
    assert "0.35" in output
    assert "market change" in output
