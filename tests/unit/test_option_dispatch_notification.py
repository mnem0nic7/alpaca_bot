from __future__ import annotations

from importlib import import_module

from alpaca_bot.config import Settings
from tests.unit.helpers import _base_env


def _make_supervisor_with_notifier(notifier):
    """Build a bare RuntimeSupervisor instance suitable for testing _notify_option_dispatch_failures."""
    module = import_module("alpaca_bot.runtime.supervisor")
    RuntimeSupervisor = module.RuntimeSupervisor
    supervisor = RuntimeSupervisor.__new__(RuntimeSupervisor)
    supervisor.settings = Settings.from_env(_base_env())
    supervisor._notifier = notifier
    return supervisor


def test_notify_sends_when_failures_positive():
    """_notify_option_dispatch_failures sends a notification when total_failed > 0."""
    sent: list[tuple[str, str]] = []

    class _FakeNotifier:
        def send(self, subject: str, body: str) -> None:
            sent.append((subject, body))

    supervisor = _make_supervisor_with_notifier(_FakeNotifier())
    supervisor._notify_option_dispatch_failures(total_failed=3)

    assert len(sent) == 1
    subject, body = sent[0]
    assert "3" in subject or "3" in body
    assert "dispatch" in subject.lower() or "dispatch" in body.lower()


def test_notify_no_alert_when_zero_failures():
    """_notify_option_dispatch_failures does not call send when total_failed == 0."""
    sent: list[tuple[str, str]] = []

    class _FakeNotifier:
        def send(self, subject: str, body: str) -> None:
            sent.append((subject, body))

    supervisor = _make_supervisor_with_notifier(_FakeNotifier())
    supervisor._notify_option_dispatch_failures(total_failed=0)

    assert sent == []


def test_notify_no_alert_when_notifier_none():
    """_notify_option_dispatch_failures does not raise when _notifier is None."""
    supervisor = _make_supervisor_with_notifier(None)
    supervisor._notify_option_dispatch_failures(total_failed=5)  # must not raise


def test_notify_no_crash_on_notifier_send_exception():
    """_notify_option_dispatch_failures swallows exceptions from notifier.send()."""
    class _BrokenNotifier:
        def send(self, subject: str, body: str) -> None:
            raise RuntimeError("SMTP timeout")

    supervisor = _make_supervisor_with_notifier(_BrokenNotifier())
    supervisor._notify_option_dispatch_failures(total_failed=1)  # must not raise
