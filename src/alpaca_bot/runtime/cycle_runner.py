from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping, Sequence

from alpaca_bot.config import Settings
from alpaca_bot.core.engine import CycleIntentType, evaluate_cycle
from alpaca_bot.domain import Bar, OpenPosition
from alpaca_bot.storage import AuditEvent, OrderRecord


def run_cycle(
    settings: Settings,
    runtime: object,
    now: datetime,
    bars_by_symbol: Mapping[str, list[object]],
    daily_bars_by_symbol: Mapping[str, list[object]],
    open_positions: list[object],
    traded_symbols_today: set[str],
    entries_disabled: bool,
) -> list[object]:
    intents = run_cycle_engine(
        settings,
        runtime,
        now,
        bars_by_symbol,
        daily_bars_by_symbol,
        open_positions,
        traded_symbols_today,
        entries_disabled,
    )

    for intent in intents:
        if isinstance(intent, OrderRecord):
            runtime.order_store.save(intent)
        else:
            runtime.audit_event_store.append(intent)

    runtime.audit_event_store.append(
        AuditEvent(
            event_type="decision_cycle_completed",
            payload={
                "cycle_timestamp": now.isoformat(),
                "action_count": len(intents),
            },
            created_at=now,
        )
    )
    return intents


def run_cycle_engine(
    settings: Settings,
    runtime: object,
    now: datetime,
    bars_by_symbol: Mapping[str, list[object]],
    daily_bars_by_symbol: Mapping[str, list[object]],
    open_positions: list[object],
    traded_symbols_today: set[str],
    entries_disabled: bool,
) -> list[object]:
    equity = float(getattr(runtime, "account_equity", 100000.0))
    working_order_symbols = set(getattr(runtime, "working_order_symbols", set()))
    normalized_traded_symbols = _normalize_traded_symbols(traded_symbols_today)
    cycle_result = evaluate_cycle(
        settings=settings,
        now=now,
        equity=equity,
        intraday_bars_by_symbol=_coerce_bar_mapping(bars_by_symbol),
        daily_bars_by_symbol=_coerce_bar_mapping(daily_bars_by_symbol),
        open_positions=_coerce_open_positions(open_positions),
        working_order_symbols=working_order_symbols,
        traded_symbols_today=normalized_traded_symbols,
        entries_disabled=entries_disabled,
    )
    return [
        _to_storage_intent(
            intent=intent,
            settings=settings,
            now=now,
        )
        for intent in cycle_result.intents
    ]


def _normalize_traded_symbols(traded_symbols_today: set[str]) -> set[tuple[str, datetime.date]]:
    normalized: set[tuple[str, datetime.date]] = set()
    for item in traded_symbols_today:
        if isinstance(item, tuple):
            normalized.add(item)
        else:
            raise TypeError(
                "runtime.cycle_runner default engine requires traded_symbols_today "
                "as (symbol, session_date) tuples"
            )
    return normalized


def _coerce_bar_mapping(mapping: Mapping[str, list[object]]) -> dict[str, list[Bar]]:
    result: dict[str, list[Bar]] = {}
    for symbol, bars in mapping.items():
        result[symbol] = [bar for bar in bars if isinstance(bar, Bar)]
    return result


def _coerce_open_positions(open_positions: list[object]) -> list[OpenPosition]:
    return [position for position in open_positions if isinstance(position, OpenPosition)]


def _to_storage_intent(
    *,
    intent: Any,
    settings: Settings,
    now: datetime,
) -> object:
    if intent.intent_type is CycleIntentType.ENTRY:
        return OrderRecord(
            client_order_id=intent.client_order_id or "",
            symbol=intent.symbol,
            side="buy",
            intent_type="entry",
            status="pending_submit",
            quantity=intent.quantity or 0,
            trading_mode=settings.trading_mode,
            strategy_version=settings.strategy_version,
            signal_timestamp=intent.signal_timestamp,
            stop_price=intent.stop_price,
            limit_price=intent.limit_price,
            initial_stop_price=intent.initial_stop_price,
            created_at=now,
            updated_at=now,
        )

    payload = {"intent_type": intent.intent_type.value}
    if intent.stop_price is not None:
        payload["stop_price"] = intent.stop_price
    if intent.reason is not None:
        payload["reason"] = intent.reason
    return AuditEvent(
        event_type=intent.intent_type.value,
        symbol=intent.symbol,
        payload=payload,
        created_at=now,
    )
