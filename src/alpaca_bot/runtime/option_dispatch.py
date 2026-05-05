from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Protocol

from alpaca_bot.config import Settings
from alpaca_bot.storage import AuditEvent
from alpaca_bot.storage.models import OptionOrderRecord

logger = logging.getLogger(__name__)


class OptionOrderStoreProtocol(Protocol):
    def list_by_status(
        self, *, trading_mode, strategy_version: str, statuses: list[str]
    ) -> list[OptionOrderRecord]: ...

    def save(self, record: OptionOrderRecord, *, commit: bool = True) -> None: ...


class AuditEventStoreProtocol(Protocol):
    def append(self, event: AuditEvent, *, commit: bool = True) -> None: ...


class RuntimeProtocol(Protocol):
    option_order_store: OptionOrderStoreProtocol
    audit_event_store: AuditEventStoreProtocol


class BrokerProtocol(Protocol):
    def submit_option_limit_entry(self, **kwargs): ...

    def submit_option_market_exit(self, **kwargs): ...


@dataclass(frozen=True)
class OptionDispatchReport:
    submitted_count: int


def dispatch_pending_option_orders(
    *,
    settings: Settings,
    runtime: RuntimeProtocol,
    broker: BrokerProtocol,
    now: datetime | Callable[[], datetime] | None = None,
) -> OptionDispatchReport:
    timestamp = now() if callable(now) else (now or datetime.now(timezone.utc))

    pending = runtime.option_order_store.list_by_status(
        trading_mode=settings.trading_mode,
        strategy_version=settings.strategy_version,
        statuses=["pending_submit"],
    )

    submitted_count = 0
    for record in pending:
        try:
            submitting = OptionOrderRecord(
                client_order_id=record.client_order_id,
                occ_symbol=record.occ_symbol,
                underlying_symbol=record.underlying_symbol,
                option_type=record.option_type,
                strike=record.strike,
                expiry=record.expiry,
                side=record.side,
                status="submitting",
                quantity=record.quantity,
                trading_mode=record.trading_mode,
                strategy_version=record.strategy_version,
                strategy_name=record.strategy_name,
                limit_price=record.limit_price,
                broker_order_id=record.broker_order_id,
                fill_price=record.fill_price,
                filled_quantity=record.filled_quantity,
                created_at=record.created_at,
                updated_at=timestamp,
            )
            runtime.option_order_store.save(submitting, commit=True)

            if record.side == "buy":
                broker_order = broker.submit_option_limit_entry(
                    occ_symbol=record.occ_symbol,
                    quantity=record.quantity,
                    limit_price=record.limit_price,
                    client_order_id=record.client_order_id,
                )
            else:
                broker_order = broker.submit_option_market_exit(
                    occ_symbol=record.occ_symbol,
                    quantity=record.quantity,
                    client_order_id=record.client_order_id,
                )

            submitted = OptionOrderRecord(
                client_order_id=record.client_order_id,
                occ_symbol=record.occ_symbol,
                underlying_symbol=record.underlying_symbol,
                option_type=record.option_type,
                strike=record.strike,
                expiry=record.expiry,
                side=record.side,
                status="submitted",
                quantity=record.quantity,
                trading_mode=record.trading_mode,
                strategy_version=record.strategy_version,
                strategy_name=record.strategy_name,
                limit_price=record.limit_price,
                broker_order_id=broker_order.broker_order_id,
                fill_price=record.fill_price,
                filled_quantity=record.filled_quantity,
                created_at=record.created_at,
                updated_at=timestamp,
            )
            runtime.option_order_store.save(submitted, commit=True)
            submitted_count += 1

            runtime.audit_event_store.append(
                AuditEvent(
                    event_type="option_order_submitted",
                    symbol=record.underlying_symbol,
                    payload={
                        "occ_symbol": record.occ_symbol,
                        "side": record.side,
                        "quantity": record.quantity,
                        "broker_order_id": broker_order.broker_order_id,
                        "client_order_id": record.client_order_id,
                    },
                ),
                commit=True,
            )
        except Exception:
            logger.exception(
                "option order dispatch failed",
                extra={"client_order_id": record.client_order_id},
            )

    return OptionDispatchReport(submitted_count=submitted_count)
