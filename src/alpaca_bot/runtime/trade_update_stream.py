from __future__ import annotations

import contextlib
from datetime import datetime, timezone
from typing import Any, Callable, Protocol

from alpaca_bot.config import Settings
from alpaca_bot.notifications import Notifier
from alpaca_bot.runtime.trade_updates import apply_trade_update
from alpaca_bot.storage import AuditEvent


class TradeUpdateStreamProtocol(Protocol):
    def subscribe_trade_updates(self, handler: Any) -> None: ...

    def run(self) -> None: ...


class AuditEventStoreProtocol(Protocol):
    def append(self, event: AuditEvent) -> None: ...


class RuntimeProtocol(Protocol):
    audit_event_store: AuditEventStoreProtocol


def attach_trade_update_stream(
    *,
    settings: Settings,
    runtime: RuntimeProtocol,
    stream: TradeUpdateStreamProtocol,
    now: Callable[[], datetime] | None = None,
    notifier: Notifier | None = None,
):
    async def handler(update: Any) -> None:
        timestamp = (now or (lambda: datetime.now(timezone.utc)))()
        try:
            apply_trade_update(
                settings=settings,
                runtime=runtime,  # type: ignore[arg-type]
                update=update,
                now=timestamp,
                notifier=notifier,
            )
        except Exception as exc:
            _lock = getattr(runtime, "store_lock", None)
            with _lock if _lock is not None else contextlib.nullcontext():
                try:
                    runtime.audit_event_store.append(
                        AuditEvent(
                            event_type="trade_update_failed",
                            symbol=_update_value(update, "symbol"),
                            payload={
                                "error": str(exc),
                                "client_order_id": _update_value(update, "client_order_id"),
                                "broker_order_id": _update_value(update, "broker_order_id")
                                or _update_value(update, "order_id"),
                            },
                            created_at=timestamp,
                        )
                    )
                except Exception:
                    try:
                        conn = getattr(runtime, "connection", None)
                        if conn is not None:
                            conn.rollback()
                    except Exception:
                        pass

    stream.subscribe_trade_updates(handler)
    return handler


def run_trade_update_stream(
    *,
    settings: Settings,
    runtime: RuntimeProtocol,
    stream: TradeUpdateStreamProtocol,
    now: Callable[[], datetime] | None = None,
) -> None:
    attach_trade_update_stream(
        settings=settings,
        runtime=runtime,
        stream=stream,
        now=now,
    )
    stream.run()


def _update_value(update: Any, field: str) -> Any:
    if isinstance(update, dict):
        return update.get(field)
    return getattr(update, field, None)
