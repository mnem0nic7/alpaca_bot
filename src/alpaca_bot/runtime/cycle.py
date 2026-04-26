from __future__ import annotations

import contextlib
from datetime import date, datetime
from typing import Mapping, Protocol, Sequence

from alpaca_bot.config import Settings
from alpaca_bot.core.engine import CycleIntentType, CycleResult, evaluate_cycle
from alpaca_bot.domain import Bar, OpenPosition
from alpaca_bot.storage import AuditEvent, DailySessionState, OrderRecord
from alpaca_bot.strategy import StrategySignalEvaluator


class OrderStoreProtocol(Protocol):
    def save(self, order: OrderRecord) -> None: ...


class AuditEventStoreProtocol(Protocol):
    def append(self, event: AuditEvent) -> None: ...


class RuntimeProtocol(Protocol):
    order_store: OrderStoreProtocol
    audit_event_store: AuditEventStoreProtocol


def run_cycle(
    *,
    settings: Settings,
    runtime: RuntimeProtocol,
    now: datetime,
    equity: float,
    intraday_bars_by_symbol: Mapping[str, Sequence[Bar]],
    daily_bars_by_symbol: Mapping[str, Sequence[Bar]],
    open_positions: Sequence[OpenPosition],
    working_order_symbols: set[str],
    traded_symbols_today: set[tuple[str, date]],
    entries_disabled: bool,
    flatten_all: bool = False,
    session_state: DailySessionState | None = None,
    signal_evaluator: StrategySignalEvaluator | None = None,
    strategy_name: str = "breakout",
    global_open_count: int | None = None,
) -> CycleResult:
    result = evaluate_cycle(
        settings=settings,
        now=now,
        equity=equity,
        intraday_bars_by_symbol=intraday_bars_by_symbol,
        daily_bars_by_symbol=daily_bars_by_symbol,
        open_positions=open_positions,
        working_order_symbols=working_order_symbols,
        traded_symbols_today=traded_symbols_today,
        entries_disabled=entries_disabled,
        flatten_all=flatten_all,
        session_state=session_state,
        signal_evaluator=signal_evaluator,
        strategy_name=strategy_name,
        global_open_count=global_open_count,
    )

    _store_lock = getattr(runtime, "store_lock", None)
    with _store_lock if _store_lock is not None else contextlib.nullcontext():
        for intent in result.intents:
            if intent.intent_type is not CycleIntentType.ENTRY:
                continue
            runtime.order_store.save(
                OrderRecord(
                    client_order_id=intent.client_order_id or "",
                    symbol=intent.symbol,
                    side="buy",
                    intent_type=intent.intent_type.value,
                    status="pending_submit",
                    quantity=intent.quantity or 0,
                    trading_mode=settings.trading_mode,
                    strategy_version=settings.strategy_version,
                    created_at=now,
                    updated_at=now,
                    stop_price=intent.stop_price,
                    limit_price=intent.limit_price,
                    initial_stop_price=intent.initial_stop_price,
                    signal_timestamp=intent.signal_timestamp,
                    strategy_name=intent.strategy_name,
                )
            )

        runtime.audit_event_store.append(
            AuditEvent(
                event_type="decision_cycle_completed",
                payload={
                    "trading_mode": settings.trading_mode.value,
                    "strategy_version": settings.strategy_version,
                    "intent_count": len(result.intents),
                    "intent_types": [intent.intent_type.value for intent in result.intents],
                    "cycle_timestamp": now.isoformat(),
                },
                created_at=now,
            )
        )

    return result
