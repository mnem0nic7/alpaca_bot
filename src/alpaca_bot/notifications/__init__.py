from __future__ import annotations

import logging
from typing import Protocol

logger = logging.getLogger(__name__)


class Notifier(Protocol):
    def send(self, subject: str, body: str) -> None: ...


class CompositeNotifier:
    """Calls each registered notifier in sequence; catches and logs per-notifier failures."""

    def __init__(self, notifiers: list[Notifier]) -> None:
        self._notifiers = notifiers

    def send(self, subject: str, body: str) -> None:
        for notifier in self._notifiers:
            try:
                notifier.send(subject, body)
            except Exception:
                logger.exception("Notifier %s failed to send: %s", notifier, subject)


class _LogOnlyNotifier:
    """Fallback when no channels are configured — logs to the application logger."""

    def send(self, subject: str, body: str) -> None:
        logger.info("NOTIFY [%s] %s", subject, body)


LOG_ONLY: Notifier = _LogOnlyNotifier()
