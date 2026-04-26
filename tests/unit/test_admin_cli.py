from __future__ import annotations

from datetime import datetime, timezone
import io
from types import SimpleNamespace

from alpaca_bot.admin.cli import main
from alpaca_bot.config import TradingMode
from alpaca_bot.storage import AuditEvent, TradingStatus, TradingStatusValue


class StoreFactoryStub:
    def __init__(self, store: object) -> None:
        self.store = store
        self.connections: list[object] = []

    def __call__(self, connection: object) -> object:
        self.connections.append(connection)
        return self.store


class RecordingTradingStatusStore:
    def __init__(self, loaded_status: TradingStatus | None = None) -> None:
        self.loaded_status = loaded_status
        self.saved: list[TradingStatus] = []
        self.load_calls: list[tuple[TradingMode, str]] = []

    def save(self, status: TradingStatus, *, commit: bool = True) -> None:
        self.saved.append(status)

    def load(
        self,
        *,
        trading_mode: TradingMode,
        strategy_version: str,
    ) -> TradingStatus | None:
        self.load_calls.append((trading_mode, strategy_version))
        return self.loaded_status


class RecordingAuditEventStore:
    def __init__(self) -> None:
        self.appended: list[AuditEvent] = []

    def append(self, event: AuditEvent, *, commit: bool = True) -> None:
        self.appended.append(event)


def test_halt_command_saves_halted_status_and_appends_audit_event() -> None:
    now = datetime(2026, 4, 24, 20, 30, tzinfo=timezone.utc)
    connection = SimpleNamespace(commit=lambda: None, close=lambda: None)
    status_store = RecordingTradingStatusStore()
    audit_store = RecordingAuditEventStore()
    stdout = io.StringIO()

    exit_code = main(
        [
            "halt",
            "--mode",
            "paper",
            "--strategy-version",
            "v1-breakout",
            "--reason",
            "manual intervention",
        ],
        connect=lambda: connection,
        trading_status_store_factory=StoreFactoryStub(status_store),
        audit_event_store_factory=StoreFactoryStub(audit_store),
        now=lambda: now,
        stdout=stdout,
    )

    assert exit_code == 0
    assert status_store.saved == [
        TradingStatus(
            trading_mode=TradingMode.PAPER,
            strategy_version="v1-breakout",
            status=TradingStatusValue.HALTED,
            kill_switch_enabled=True,
            status_reason="manual intervention",
            updated_at=now,
        )
    ]
    assert audit_store.appended == [
        AuditEvent(
            event_type="trading_status_changed",
            payload={
                "command": "halt",
                "trading_mode": "paper",
                "strategy_version": "v1-breakout",
                "status": "halted",
                "reason": "manual intervention",
            },
            created_at=now,
        )
    ]


def test_close_only_command_saves_close_only_status() -> None:
    now = datetime(2026, 4, 24, 20, 35, tzinfo=timezone.utc)
    connection = SimpleNamespace(commit=lambda: None, close=lambda: None)
    status_store = RecordingTradingStatusStore()
    audit_store = RecordingAuditEventStore()

    exit_code = main(
        [
            "close-only",
            "--mode",
            "live",
            "--strategy-version",
            "v2-breakout",
        ],
        connect=lambda: connection,
        trading_status_store_factory=StoreFactoryStub(status_store),
        audit_event_store_factory=StoreFactoryStub(audit_store),
        now=lambda: now,
        stdout=io.StringIO(),
    )

    assert exit_code == 0
    assert status_store.saved == [
        TradingStatus(
            trading_mode=TradingMode.LIVE,
            strategy_version="v2-breakout",
            status=TradingStatusValue.CLOSE_ONLY,
            kill_switch_enabled=False,
            status_reason=None,
            updated_at=now,
        )
    ]


def test_resume_command_restores_enabled_status_for_requested_target() -> None:
    now = datetime(2026, 4, 24, 20, 40, tzinfo=timezone.utc)
    connection = SimpleNamespace(commit=lambda: None, close=lambda: None)
    status_store = RecordingTradingStatusStore()
    audit_store = RecordingAuditEventStore()

    exit_code = main(
        [
            "resume",
            "--mode",
            "live",
            "--strategy-version",
            "breakout-v2026-04",
        ],
        connect=lambda: connection,
        trading_status_store_factory=StoreFactoryStub(status_store),
        audit_event_store_factory=StoreFactoryStub(audit_store),
        now=lambda: now,
        stdout=io.StringIO(),
    )

    assert exit_code == 0
    assert status_store.saved == [
        TradingStatus(
            trading_mode=TradingMode.LIVE,
            strategy_version="breakout-v2026-04",
            status=TradingStatusValue.ENABLED,
            kill_switch_enabled=False,
            status_reason=None,
            updated_at=now,
        )
    ]


def test_status_command_renders_current_status_text() -> None:
    now = datetime(2026, 4, 24, 20, 45, tzinfo=timezone.utc)
    connection = object()
    status_store = RecordingTradingStatusStore(
        loaded_status=TradingStatus(
            trading_mode=TradingMode.PAPER,
            strategy_version="v1-breakout",
            status=TradingStatusValue.HALTED,
            kill_switch_enabled=True,
            status_reason="manual intervention",
            updated_at=now,
        )
    )
    audit_store = RecordingAuditEventStore()
    stdout = io.StringIO()

    exit_code = main(
        [
            "status",
            "--mode",
            "paper",
            "--strategy-version",
            "v1-breakout",
        ],
        connect=lambda: connection,
        trading_status_store_factory=StoreFactoryStub(status_store),
        audit_event_store_factory=StoreFactoryStub(audit_store),
        stdout=stdout,
    )

    assert exit_code == 0
    assert status_store.saved == []
    assert status_store.load_calls == [(TradingMode.PAPER, "v1-breakout")]
    assert audit_store.appended == []
    rendered = stdout.getvalue().strip()
    assert "paper" in rendered
    assert "v1-breakout" in rendered
    assert "halted" in rendered
    assert "manual intervention" in rendered


def test_main_uses_process_argv_when_invoked_as_console_script(monkeypatch) -> None:
    now = datetime(2026, 4, 24, 20, 50, tzinfo=timezone.utc)
    connection = object()
    status_store = RecordingTradingStatusStore()
    audit_store = RecordingAuditEventStore()
    stdout = io.StringIO()

    monkeypatch.setattr(
        "sys.argv",
        ["alpaca-bot-admin", "status", "--mode", "paper", "--strategy-version", "v1-breakout"],
    )

    exit_code = main(
        None,
        connect=lambda: connection,
        trading_status_store_factory=StoreFactoryStub(status_store),
        audit_event_store_factory=StoreFactoryStub(audit_store),
        now=lambda: now,
        stdout=stdout,
    )

    assert exit_code == 0
    assert "status=unknown" in stdout.getvalue()
