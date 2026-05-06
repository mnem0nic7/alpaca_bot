from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from alpaca_bot.config import Settings, TradingMode
from alpaca_bot.core.engine import CycleIntent, CycleIntentType, CycleResult
from alpaca_bot.storage import AuditEvent, OrderRecord, PositionRecord


def make_settings(**overrides: str) -> Settings:
    values = {
        "TRADING_MODE": "paper",
        "ENABLE_LIVE_TRADING": "false",
        "STRATEGY_VERSION": "v1-breakout",
        "DATABASE_URL": "postgresql://alpaca_bot:secret@db.example.com:5432/alpaca_bot",
        "MARKET_DATA_FEED": "sip",
        "SYMBOLS": "AAPL,MSFT,SPY",
        "DAILY_SMA_PERIOD": "20",
        "BREAKOUT_LOOKBACK_BARS": "20",
        "RELATIVE_VOLUME_LOOKBACK_BARS": "20",
        "RELATIVE_VOLUME_THRESHOLD": "1.5",
        "ENTRY_TIMEFRAME_MINUTES": "15",
        "RISK_PER_TRADE_PCT": "0.0025",
        "MAX_POSITION_PCT": "0.05",
        "MAX_OPEN_POSITIONS": "3",
        "DAILY_LOSS_LIMIT_PCT": "0.01",
        "STOP_LIMIT_BUFFER_PCT": "0.001",
        "BREAKOUT_STOP_BUFFER_PCT": "0.001",
        "ENTRY_STOP_PRICE_BUFFER": "0.01",
        "ENTRY_WINDOW_START": "10:00",
        "ENTRY_WINDOW_END": "15:30",
        "FLATTEN_TIME": "15:45",
    }
    values.update(overrides)
    return Settings.from_env(values)


class RecordingOrderStore:
    def __init__(self, *, orders: list[OrderRecord] | None = None) -> None:
        self.orders = list(orders or [])
        self.status_calls: list[tuple[TradingMode, str, tuple[str, ...]]] = []
        self.saved: list[OrderRecord] = []

    def list_by_status(
        self,
        *,
        trading_mode: TradingMode,
        strategy_version: str,
        statuses: list[str],
        strategy_name: str | None = None,
    ) -> list[OrderRecord]:
        self.status_calls.append((trading_mode, strategy_version, tuple(statuses)))
        orders = [order for order in self.orders if order.status in statuses]
        if strategy_name is not None:
            orders = [o for o in orders if o.strategy_name == strategy_name]
        return orders

    def save(self, order: OrderRecord, *, commit: bool = True) -> None:
        self.saved.append(order)


class RecordingPositionStore:
    def __init__(self, *, positions: list[PositionRecord]) -> None:
        self.positions = list(positions)
        self.list_calls: list[tuple[TradingMode, str]] = []
        self.saved: list[PositionRecord] = []

    def list_all(
        self,
        *,
        trading_mode: TradingMode,
        strategy_version: str,
    ) -> list[PositionRecord]:
        self.list_calls.append((trading_mode, strategy_version))
        return list(self.positions)

    def save(self, position: PositionRecord, *, commit: bool = True) -> None:
        self.saved.append(position)


class RecordingAuditEventStore:
    def __init__(self) -> None:
        self.appended: list[AuditEvent] = []

    def append(self, event: AuditEvent, *, commit: bool = True) -> None:
        self.appended.append(event)


class FakeConnection:
    def __init__(self) -> None:
        self.rollback_count = 0

    def commit(self) -> None:
        pass

    def rollback(self) -> None:
        self.rollback_count += 1


class RecordingBroker:
    def __init__(self, *, cancel_raises: Exception | None = None) -> None:
        self.replace_calls: list[dict[str, object]] = []
        self.stop_calls: list[dict[str, object]] = []
        self.cancel_calls: list[str] = []
        self.exit_calls: list[dict[str, object]] = []
        self._cancel_raises = cancel_raises

    def replace_order(self, **kwargs):
        self.replace_calls.append(dict(kwargs))
        return SimpleNamespace(
            client_order_id=kwargs.get("client_order_id", ""),
            broker_order_id=kwargs["order_id"],
            symbol="AAPL",
            side="sell",
            status="ACCEPTED",
            quantity=25,
        )

    def submit_stop_order(self, **kwargs):
        self.stop_calls.append(dict(kwargs))
        return SimpleNamespace(
            client_order_id=kwargs["client_order_id"],
            broker_order_id="broker-stop-new",
            symbol=kwargs["symbol"],
            side="sell",
            status="NEW",
            quantity=kwargs["quantity"],
        )

    def cancel_order(self, order_id: str) -> None:
        self.cancel_calls.append(order_id)
        if self._cancel_raises is not None:
            raise self._cancel_raises

    def submit_market_exit(self, **kwargs):
        self.exit_calls.append(dict(kwargs))
        return SimpleNamespace(
            client_order_id=kwargs["client_order_id"],
            broker_order_id="broker-exit-1",
            symbol=kwargs["symbol"],
            side="sell",
            status="ACCEPTED",
            quantity=kwargs["quantity"],
        )


def load_cycle_intent_execution_api():
    from alpaca_bot.runtime.cycle_intent_execution import execute_cycle_intents

    return execute_cycle_intents


def test_execute_cycle_intents_replaces_active_stop_and_updates_position() -> None:
    execute_cycle_intents = load_cycle_intent_execution_api()
    settings = make_settings()
    now = datetime(2026, 4, 24, 19, 30, tzinfo=timezone.utc)
    active_stop = OrderRecord(
        client_order_id="v1-breakout:2026-04-24:AAPL:stop:2026-04-24T19:00:00+00:00",
        symbol="AAPL",
        side="sell",
        intent_type="stop",
        status="accepted",
        quantity=25,
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        created_at=now,
        updated_at=now,
        stop_price=109.89,
        initial_stop_price=109.89,
        broker_order_id="broker-stop-1",
        signal_timestamp=now,
    )
    position = PositionRecord(
        symbol="AAPL",
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        quantity=25,
        entry_price=111.02,
        stop_price=109.89,
        initial_stop_price=109.89,
        opened_at=now,
        updated_at=now,
    )
    runtime = SimpleNamespace(
        order_store=RecordingOrderStore(orders=[active_stop]),
        position_store=RecordingPositionStore(positions=[position]),
        audit_event_store=RecordingAuditEventStore(),
        connection=FakeConnection(),
    )
    broker = RecordingBroker()
    cycle_result = CycleResult(
        as_of=now,
        intents=[
            CycleIntent(
                intent_type=CycleIntentType.UPDATE_STOP,
                symbol="AAPL",
                timestamp=now,
                stop_price=111.7,
            )
        ],
    )

    report = execute_cycle_intents(
        settings=settings,
        runtime=runtime,
        broker=broker,
        cycle_result=cycle_result,
        now=now,
    )

    assert broker.replace_calls == [
        {
            "order_id": "broker-stop-1",
            "stop_price": 111.7,
        }
    ]
    assert runtime.order_store.saved == [
        OrderRecord(
            client_order_id=active_stop.client_order_id,
            symbol="AAPL",
            side="sell",
            intent_type="stop",
            status="accepted",
            quantity=25,
            trading_mode=TradingMode.PAPER,
            strategy_version="v1-breakout",
            created_at=now,
            updated_at=now,
            stop_price=111.7,
            initial_stop_price=109.89,
            broker_order_id="broker-stop-1",
            signal_timestamp=now,
        )
    ]
    assert runtime.position_store.saved == [
        PositionRecord(
            symbol="AAPL",
            trading_mode=TradingMode.PAPER,
            strategy_version="v1-breakout",
            quantity=25,
            entry_price=111.02,
            stop_price=111.7,
            initial_stop_price=109.89,
            opened_at=now,
            updated_at=now,
        )
    ]
    assert runtime.audit_event_store.appended == [
        AuditEvent(
            event_type="cycle_intent_executed",
            symbol="AAPL",
            payload={
                "intent_type": "update_stop",
                "action": "replaced",
                "stop_price": 111.7,
            },
            created_at=now,
        )
    ]
    assert report.replaced_stop_count == 1
    assert report.submitted_stop_count == 0


def test_execute_cycle_intents_submits_new_stop_when_no_active_stop_exists() -> None:
    execute_cycle_intents = load_cycle_intent_execution_api()
    settings = make_settings()
    now = datetime(2026, 4, 24, 19, 35, tzinfo=timezone.utc)
    position = PositionRecord(
        symbol="AAPL",
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        quantity=25,
        entry_price=111.02,
        stop_price=109.89,
        initial_stop_price=109.89,
        opened_at=now,
        updated_at=now,
    )
    runtime = SimpleNamespace(
        order_store=RecordingOrderStore(orders=[]),
        position_store=RecordingPositionStore(positions=[position]),
        audit_event_store=RecordingAuditEventStore(),
        connection=FakeConnection(),
    )
    broker = RecordingBroker()
    cycle_result = CycleResult(
        as_of=now,
        intents=[
            CycleIntent(
                intent_type=CycleIntentType.UPDATE_STOP,
                symbol="AAPL",
                timestamp=now,
                stop_price=111.5,
            )
        ],
    )

    report = execute_cycle_intents(
        settings=settings,
        runtime=runtime,
        broker=broker,
        cycle_result=cycle_result,
        now=now,
    )

    assert broker.stop_calls == [
        {
            "symbol": "AAPL",
            "quantity": 25,
            "stop_price": 111.5,
            "client_order_id": "v1-breakout:breakout:2026-04-24:AAPL:stop:2026-04-24T19:35:00+00:00",
        }
    ]
    assert runtime.order_store.saved[0].intent_type == "stop"
    assert runtime.order_store.saved[0].status == "new"
    assert report.replaced_stop_count == 0
    assert report.submitted_stop_count == 1


def test_execute_cycle_intents_cancels_active_stops_and_submits_exit_order() -> None:
    execute_cycle_intents = load_cycle_intent_execution_api()
    settings = make_settings()
    now = datetime(2026, 4, 24, 19, 45, tzinfo=timezone.utc)
    active_stop = OrderRecord(
        client_order_id="v1-breakout:2026-04-24:AAPL:stop:2026-04-24T19:00:00+00:00",
        symbol="AAPL",
        side="sell",
        intent_type="stop",
        status="accepted",
        quantity=25,
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        created_at=now,
        updated_at=now,
        stop_price=109.89,
        initial_stop_price=109.89,
        broker_order_id="broker-stop-1",
        signal_timestamp=now,
    )
    position = PositionRecord(
        symbol="AAPL",
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        quantity=25,
        entry_price=111.02,
        stop_price=111.7,
        initial_stop_price=109.89,
        opened_at=now,
        updated_at=now,
    )
    runtime = SimpleNamespace(
        order_store=RecordingOrderStore(orders=[active_stop]),
        position_store=RecordingPositionStore(positions=[position]),
        audit_event_store=RecordingAuditEventStore(),
        connection=FakeConnection(),
    )
    broker = RecordingBroker()
    cycle_result = CycleResult(
        as_of=now,
        intents=[
            CycleIntent(
                intent_type=CycleIntentType.EXIT,
                symbol="AAPL",
                timestamp=now,
                reason="eod_flatten",
            )
        ],
    )

    report = execute_cycle_intents(
        settings=settings,
        runtime=runtime,
        broker=broker,
        cycle_result=cycle_result,
        now=now,
    )

    assert broker.cancel_calls == ["broker-stop-1"]
    assert broker.exit_calls == [
        {
            "symbol": "AAPL",
            "quantity": 25,
            "client_order_id": "v1-breakout:breakout:2026-04-24:AAPL:exit:2026-04-24T19:45:00+00:00",
        }
    ]
    assert runtime.order_store.saved == [
        OrderRecord(
            client_order_id=active_stop.client_order_id,
            symbol="AAPL",
            side="sell",
            intent_type="stop",
            status="canceled",
            quantity=25,
            trading_mode=TradingMode.PAPER,
            strategy_version="v1-breakout",
            created_at=now,
            updated_at=now,
            stop_price=109.89,
            initial_stop_price=109.89,
            broker_order_id="broker-stop-1",
            signal_timestamp=now,
        ),
        OrderRecord(
            client_order_id="v1-breakout:breakout:2026-04-24:AAPL:exit:2026-04-24T19:45:00+00:00",
            symbol="AAPL",
            side="sell",
            intent_type="exit",
            status="accepted",
            quantity=25,
            trading_mode=TradingMode.PAPER,
            strategy_version="v1-breakout",
            created_at=now,
            updated_at=now,
            initial_stop_price=109.89,
            broker_order_id="broker-exit-1",
            signal_timestamp=now,
        ),
    ]
    assert runtime.audit_event_store.appended == [
        AuditEvent(
            event_type="cycle_intent_executed",
            symbol="AAPL",
            payload={
                "intent_type": "exit",
                "action": "submitted",
                "reason": "eod_flatten",
                "canceled_stop_count": 1,
                "client_order_id": "v1-breakout:breakout:2026-04-24:AAPL:exit:2026-04-24T19:45:00+00:00",
            },
            created_at=now,
        )
    ]
    assert report.canceled_stop_count == 1
    assert report.submitted_exit_count == 1


def test_execute_cycle_intents_marks_stop_canceled_when_broker_reports_already_filled() -> None:
    """When cancel_order raises 'already filled', the stop record must still be
    saved as 'canceled' in DB so it doesn't resurface on subsequent cycles."""
    execute_cycle_intents = load_cycle_intent_execution_api()
    settings = make_settings()
    now = datetime(2026, 4, 24, 19, 50, tzinfo=timezone.utc)
    active_stop = OrderRecord(
        client_order_id="v1-breakout:2026-04-24:AAPL:stop:2026-04-24T19:00:00+00:00",
        symbol="AAPL",
        side="sell",
        intent_type="stop",
        status="accepted",
        quantity=25,
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        created_at=now,
        updated_at=now,
        stop_price=109.89,
        initial_stop_price=109.89,
        broker_order_id="broker-stop-1",
        signal_timestamp=now,
    )
    position = PositionRecord(
        symbol="AAPL",
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        quantity=25,
        entry_price=111.02,
        stop_price=109.89,
        initial_stop_price=109.89,
        opened_at=now,
        updated_at=now,
    )
    runtime = SimpleNamespace(
        order_store=RecordingOrderStore(orders=[active_stop]),
        position_store=RecordingPositionStore(positions=[position]),
        audit_event_store=RecordingAuditEventStore(),
        connection=FakeConnection(),
    )
    broker = RecordingBroker(cancel_raises=Exception("order not found: already filled"))
    cycle_result = CycleResult(
        as_of=now,
        intents=[
            CycleIntent(
                intent_type=CycleIntentType.EXIT,
                symbol="AAPL",
                timestamp=now,
                reason="eod_flatten",
            )
        ],
    )

    report = execute_cycle_intents(
        settings=settings,
        runtime=runtime,
        broker=broker,
        cycle_result=cycle_result,
        now=now,
    )

    # No exit submitted because position is already gone
    assert broker.exit_calls == []
    assert report.submitted_exit_count == 0
    assert report.canceled_stop_count == 1

    # The stop order MUST be marked canceled in DB so it doesn't resurface
    assert len(runtime.order_store.saved) == 1
    assert runtime.order_store.saved[0].client_order_id == active_stop.client_order_id
    assert runtime.order_store.saved[0].status == "canceled"


def test_execute_cycle_intents_acquires_store_lock_on_update_stop() -> None:
    """When runtime has a store_lock, position_store.save must be called while
    the lock is held — verified via a recording context manager."""
    import threading

    execute_cycle_intents = load_cycle_intent_execution_api()
    settings = make_settings()
    now = datetime(2026, 4, 24, 19, 30, tzinfo=timezone.utc)
    active_stop = OrderRecord(
        client_order_id="v1-breakout:2026-04-24:AAPL:stop:lock-test",
        symbol="AAPL",
        side="sell",
        intent_type="stop",
        status="accepted",
        quantity=25,
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        created_at=now,
        updated_at=now,
        stop_price=109.89,
        initial_stop_price=109.89,
        broker_order_id="broker-stop-lock",
        signal_timestamp=now,
    )
    position = PositionRecord(
        symbol="AAPL",
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        quantity=25,
        entry_price=111.02,
        stop_price=109.89,
        initial_stop_price=109.89,
        opened_at=now,
        updated_at=now,
    )

    lock_held_during_save: list[bool] = []
    real_lock = threading.Lock()

    class LockWatchingPositionStore(RecordingPositionStore):
        def save(self, position, *, commit: bool = True):
            # Record whether the lock is held when save() is called
            lock_held_during_save.append(not real_lock.acquire(blocking=False))
            if lock_held_during_save[-1]:
                pass  # lock was held — correct
            else:
                real_lock.release()  # we acquired it just to check; release it
            super().save(position, commit=commit)

    runtime = SimpleNamespace(
        order_store=RecordingOrderStore(orders=[active_stop]),
        position_store=LockWatchingPositionStore(positions=[position]),
        audit_event_store=RecordingAuditEventStore(),
        store_lock=real_lock,
        connection=FakeConnection(),
    )
    broker = RecordingBroker()
    cycle_result = CycleResult(
        as_of=now,
        intents=[
            CycleIntent(
                intent_type=CycleIntentType.UPDATE_STOP,
                symbol="AAPL",
                timestamp=now,
                stop_price=111.7,
            )
        ],
    )

    execute_cycle_intents(
        settings=settings,
        runtime=runtime,
        broker=broker,
        cycle_result=cycle_result,
        now=now,
    )

    assert lock_held_during_save, "position_store.save was never called"
    assert all(lock_held_during_save), (
        "store_lock must be held for every position_store.save call during UPDATE_STOP"
    )


def test_execute_cycle_intents_acquires_store_lock_on_order_store_save_during_update_stop() -> None:
    """order_store.save must also be called while store_lock is held during UPDATE_STOP."""
    import threading

    execute_cycle_intents = load_cycle_intent_execution_api()
    settings = make_settings()
    now = datetime(2026, 4, 24, 19, 30, tzinfo=timezone.utc)
    active_stop = OrderRecord(
        client_order_id="v1-breakout:2026-04-24:AAPL:stop:lock-order-test",
        symbol="AAPL",
        side="sell",
        intent_type="stop",
        status="accepted",
        quantity=25,
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        created_at=now,
        updated_at=now,
        stop_price=109.89,
        initial_stop_price=109.89,
        broker_order_id="broker-stop-lock-order",
        signal_timestamp=now,
    )
    position = PositionRecord(
        symbol="AAPL",
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        quantity=25,
        entry_price=111.02,
        stop_price=109.89,
        initial_stop_price=109.89,
        opened_at=now,
        updated_at=now,
    )

    lock_held_during_save: list[bool] = []
    real_lock = threading.Lock()

    class LockWatchingOrderStore(RecordingOrderStore):
        def save(self, order, *, commit: bool = True):
            lock_held_during_save.append(not real_lock.acquire(blocking=False))
            if not lock_held_during_save[-1]:
                real_lock.release()
            super().save(order, commit=commit)

    runtime = SimpleNamespace(
        order_store=LockWatchingOrderStore(orders=[active_stop]),
        position_store=RecordingPositionStore(positions=[position]),
        audit_event_store=RecordingAuditEventStore(),
        store_lock=real_lock,
        connection=FakeConnection(),
    )
    broker = RecordingBroker()
    cycle_result = CycleResult(
        as_of=now,
        intents=[
            CycleIntent(
                intent_type=CycleIntentType.UPDATE_STOP,
                symbol="AAPL",
                timestamp=now,
                stop_price=111.7,
            )
        ],
    )

    execute_cycle_intents(
        settings=settings,
        runtime=runtime,
        broker=broker,
        cycle_result=cycle_result,
        now=now,
    )

    assert lock_held_during_save, "order_store.save was never called"
    assert all(lock_held_during_save), (
        "store_lock must be held for every order_store.save call during UPDATE_STOP"
    )


def test_execute_cycle_intents_acquires_store_lock_on_order_store_save_during_exit() -> None:
    """order_store.save must be called while store_lock is held during EXIT intent."""
    import threading

    execute_cycle_intents = load_cycle_intent_execution_api()
    settings = make_settings()
    now = datetime(2026, 4, 24, 19, 50, tzinfo=timezone.utc)
    active_stop = OrderRecord(
        client_order_id="v1-breakout:2026-04-24:AAPL:stop:lock-exit-test",
        symbol="AAPL",
        side="sell",
        intent_type="stop",
        status="accepted",
        quantity=25,
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        created_at=now,
        updated_at=now,
        stop_price=109.89,
        initial_stop_price=109.89,
        broker_order_id="broker-stop-exit-lock",
        signal_timestamp=now,
    )
    position = PositionRecord(
        symbol="AAPL",
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        quantity=25,
        entry_price=111.02,
        stop_price=109.89,
        initial_stop_price=109.89,
        opened_at=now,
        updated_at=now,
    )

    lock_held_during_save: list[bool] = []
    real_lock = threading.Lock()

    class LockWatchingOrderStore(RecordingOrderStore):
        def save(self, order, *, commit: bool = True):
            lock_held_during_save.append(not real_lock.acquire(blocking=False))
            if not lock_held_during_save[-1]:
                real_lock.release()
            super().save(order, commit=commit)

    runtime = SimpleNamespace(
        order_store=LockWatchingOrderStore(orders=[active_stop]),
        position_store=RecordingPositionStore(positions=[position]),
        audit_event_store=RecordingAuditEventStore(),
        store_lock=real_lock,
        connection=FakeConnection(),
    )
    broker = RecordingBroker()
    cycle_result = CycleResult(
        as_of=now,
        intents=[
            CycleIntent(
                intent_type=CycleIntentType.EXIT,
                symbol="AAPL",
                timestamp=now,
                reason="eod_flatten",
            )
        ],
    )

    execute_cycle_intents(
        settings=settings,
        runtime=runtime,
        broker=broker,
        cycle_result=cycle_result,
        now=now,
    )

    assert lock_held_during_save, "order_store.save was never called during EXIT"
    assert all(lock_held_during_save), (
        "store_lock must be held for every order_store.save call during EXIT"
    )


def test_execute_cycle_intents_emits_audit_event_when_position_already_gone() -> None:
    """When broker reports position already gone, an AuditEvent must still be appended."""
    execute_cycle_intents = load_cycle_intent_execution_api()
    settings = make_settings()
    now = datetime(2026, 4, 24, 19, 50, tzinfo=timezone.utc)
    active_stop = OrderRecord(
        client_order_id="v1-breakout:2026-04-24:AAPL:stop:already-gone-audit-test",
        symbol="AAPL",
        side="sell",
        intent_type="stop",
        status="accepted",
        quantity=25,
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        created_at=now,
        updated_at=now,
        stop_price=109.89,
        initial_stop_price=109.89,
        broker_order_id="broker-stop-gone",
        signal_timestamp=now,
    )
    position = PositionRecord(
        symbol="AAPL",
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        quantity=25,
        entry_price=111.02,
        stop_price=109.89,
        initial_stop_price=109.89,
        opened_at=now,
        updated_at=now,
    )
    runtime = SimpleNamespace(
        order_store=RecordingOrderStore(orders=[active_stop]),
        position_store=RecordingPositionStore(positions=[position]),
        audit_event_store=RecordingAuditEventStore(),
        connection=FakeConnection(),
    )
    broker = RecordingBroker(cancel_raises=Exception("not found: already filled"))
    cycle_result = CycleResult(
        as_of=now,
        intents=[
            CycleIntent(
                intent_type=CycleIntentType.EXIT,
                symbol="AAPL",
                timestamp=now,
                reason="eod_flatten",
            )
        ],
    )

    execute_cycle_intents(
        settings=settings,
        runtime=runtime,
        broker=broker,
        cycle_result=cycle_result,
        now=now,
    )

    audit_events = runtime.audit_event_store.appended
    # An audit event must be emitted even on the position_already_gone early-return path.
    assert len(audit_events) >= 1, "No AuditEvent was appended for position_already_gone exit"
    skipped_event = next(
        (e for e in audit_events if e.event_type == "cycle_intent_executed"), None
    )
    assert skipped_event is not None, "Expected cycle_intent_executed event for position_already_gone"
    assert skipped_event.payload["action"] == "skipped_position_already_gone"


def test_canceled_stop_preserves_non_default_strategy_name() -> None:
    """strategy_name on a canceled stop OrderRecord must match the source stop order,
    not silently default to 'breakout'. Regression guard for the momentum strategy path."""
    execute_cycle_intents = load_cycle_intent_execution_api()
    settings = make_settings()
    now = datetime(2026, 4, 24, 19, 50, tzinfo=timezone.utc)
    active_stop = OrderRecord(
        client_order_id="v1-breakout:2026-04-24:AAPL:stop:2026-04-24T19:00:00+00:00",
        symbol="AAPL",
        side="sell",
        intent_type="stop",
        status="accepted",
        quantity=10,
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        created_at=now,
        updated_at=now,
        stop_price=109.89,
        initial_stop_price=109.89,
        broker_order_id="broker-stop-mom-1",
        signal_timestamp=now,
        strategy_name="momentum",
    )
    position = PositionRecord(
        symbol="AAPL",
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        quantity=10,
        entry_price=115.0,
        stop_price=109.89,
        initial_stop_price=109.89,
        opened_at=now,
        updated_at=now,
        strategy_name="momentum",
    )
    runtime = SimpleNamespace(
        order_store=RecordingOrderStore(orders=[active_stop]),
        position_store=RecordingPositionStore(positions=[position]),
        audit_event_store=RecordingAuditEventStore(),
        connection=FakeConnection(),
    )
    broker = RecordingBroker()
    cycle_result = CycleResult(
        as_of=now,
        intents=[
            CycleIntent(
                intent_type=CycleIntentType.EXIT,
                symbol="AAPL",
                timestamp=now,
                reason="eod_flatten",
                strategy_name="momentum",
            )
        ],
    )

    report = execute_cycle_intents(
        settings=settings,
        runtime=runtime,
        broker=broker,
        cycle_result=cycle_result,
        now=now,
    )

    assert report.canceled_stop_count == 1
    assert report.submitted_exit_count == 1
    # The canceled stop record must carry strategy_name="momentum", not "breakout".
    canceled_stop = runtime.order_store.saved[0]
    assert canceled_stop.intent_type == "stop"
    assert canceled_stop.status == "canceled"
    assert canceled_stop.strategy_name == "momentum", (
        f"Expected strategy_name='momentum' on canceled stop, got {canceled_stop.strategy_name!r}"
    )


def test_execute_exit_aborts_when_position_disappears_before_market_exit() -> None:
    """Naked-short guard: if the position is gone by the time we re-check under lock
    (between cancel_order and submit_market_exit), no exit order must be submitted."""
    execute_cycle_intents = load_cycle_intent_execution_api()
    settings = make_settings()
    now = datetime(2026, 4, 24, 19, 50, tzinfo=timezone.utc)

    active_stop = OrderRecord(
        client_order_id="v1-breakout:breakout:2026-04-24:AAPL:stop:t",
        symbol="AAPL", side="sell", intent_type="stop", status="accepted",
        quantity=25, trading_mode=TradingMode.PAPER, strategy_version="v1-breakout",
        created_at=now, updated_at=now, stop_price=109.89, initial_stop_price=109.89,
        broker_order_id="broker-stop-1", signal_timestamp=now,
    )
    position = PositionRecord(
        symbol="AAPL", trading_mode=TradingMode.PAPER, strategy_version="v1-breakout",
        quantity=25, entry_price=111.02, stop_price=109.89, initial_stop_price=109.89,
        opened_at=now, updated_at=now,
    )

    call_count = [0]

    class DisappearingPositionStore(RecordingPositionStore):
        def list_all(self, *, trading_mode, strategy_version):
            call_count[0] += 1
            # Second call (re-check under lock) returns empty — position was filled by stream
            if call_count[0] >= 2:
                return []
            return list(self.positions)

    runtime = SimpleNamespace(
        order_store=RecordingOrderStore(orders=[active_stop]),
        position_store=DisappearingPositionStore(positions=[position]),
        audit_event_store=RecordingAuditEventStore(),
        connection=FakeConnection(),
    )
    broker = RecordingBroker()

    execute_cycle_intents(
        settings=settings,
        runtime=runtime,
        broker=broker,
        cycle_result=CycleResult(
            as_of=now,
            intents=[CycleIntent(
                intent_type=CycleIntentType.EXIT,
                symbol="AAPL",
                timestamp=now,
                reason="eod_flatten",
            )],
        ),
        now=now,
    )

    assert broker.exit_calls == [], "submit_market_exit must NOT be called when position disappeared"
    assert any(
        e.payload.get("action") == "skipped_position_already_gone"
        for e in runtime.audit_event_store.appended
    ), "Audit event must record the skipped-position-already-gone case"


def test_execute_update_stop_aborts_when_position_disappears_during_broker_call() -> None:
    """If the position disappears between the broker replace_order call and the DB write,
    the order and position write must be skipped to avoid stale state."""
    execute_cycle_intents = load_cycle_intent_execution_api()
    settings = make_settings()
    now = datetime(2026, 4, 24, 19, 30, tzinfo=timezone.utc)

    active_stop = OrderRecord(
        client_order_id="v1-breakout:2026-04-24:AAPL:stop:2026-04-24T19:00:00+00:00",
        symbol="AAPL", side="sell", intent_type="stop", status="accepted",
        quantity=25, trading_mode=TradingMode.PAPER, strategy_version="v1-breakout",
        created_at=now, updated_at=now, stop_price=109.89, initial_stop_price=109.89,
        broker_order_id="broker-stop-1", signal_timestamp=now,
    )
    position = PositionRecord(
        symbol="AAPL", trading_mode=TradingMode.PAPER, strategy_version="v1-breakout",
        quantity=25, entry_price=111.02, stop_price=109.89, initial_stop_price=109.89,
        opened_at=now, updated_at=now,
    )

    call_count = [0]

    class DisappearingPositionStore(RecordingPositionStore):
        def list_all(self, *, trading_mode, strategy_version):
            call_count[0] += 1
            # Second call (re-check after broker call) returns empty
            if call_count[0] >= 2:
                return []
            return list(self.positions)

    order_store = RecordingOrderStore(orders=[active_stop])
    runtime = SimpleNamespace(
        order_store=order_store,
        position_store=DisappearingPositionStore(positions=[position]),
        audit_event_store=RecordingAuditEventStore(),
        connection=FakeConnection(),
    )
    broker = RecordingBroker()

    execute_cycle_intents(
        settings=settings,
        runtime=runtime,
        broker=broker,
        cycle_result=CycleResult(
            as_of=now,
            intents=[CycleIntent(
                intent_type=CycleIntentType.UPDATE_STOP,
                symbol="AAPL",
                timestamp=now,
                stop_price=112.0,
            )],
        ),
        now=now,
    )

    # Broker was called (replace was attempted), but no DB writes happened
    assert broker.replace_calls, "replace_order should be called"
    assert order_store.saved == [], "No order write when position disappeared during broker call"


def test_execute_exit_skipped_when_active_exit_order_exists() -> None:
    """When an exit order with an active status already exists for the symbol,
    the function must return (0, 0) and emit a cycle_intent_skipped audit event
    without making any broker calls."""
    execute_cycle_intents = load_cycle_intent_execution_api()
    settings = make_settings()
    now = datetime(2026, 4, 24, 19, 50, tzinfo=timezone.utc)

    existing_exit = OrderRecord(
        client_order_id="v1-breakout:2026-04-24:AAPL:exit:existing",
        symbol="AAPL",
        side="sell",
        intent_type="exit",
        status="accepted",
        quantity=25,
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        created_at=now,
        updated_at=now,
        initial_stop_price=109.89,
        broker_order_id="broker-exit-existing",
        signal_timestamp=now,
    )
    position = PositionRecord(
        symbol="AAPL",
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        quantity=25,
        entry_price=111.02,
        stop_price=109.89,
        initial_stop_price=109.89,
        opened_at=now,
        updated_at=now,
    )
    runtime = SimpleNamespace(
        order_store=RecordingOrderStore(orders=[existing_exit]),
        position_store=RecordingPositionStore(positions=[position]),
        audit_event_store=RecordingAuditEventStore(),
        connection=FakeConnection(),
    )
    broker = RecordingBroker()

    report = execute_cycle_intents(
        settings=settings,
        runtime=runtime,
        broker=broker,
        cycle_result=CycleResult(
            as_of=now,
            intents=[CycleIntent(
                intent_type=CycleIntentType.EXIT,
                symbol="AAPL",
                timestamp=now,
                reason="eod_flatten",
            )],
        ),
        now=now,
    )

    assert report.submitted_exit_count == 0
    assert broker.cancel_calls == [], "No cancel calls when exit already in-flight"
    skipped = [
        e for e in runtime.audit_event_store.appended
        if e.event_type == "cycle_intent_skipped"
    ]
    assert len(skipped) == 1
    assert skipped[0].payload["reason"] == "active_exit_order_exists"


def test_execute_update_stop_broker_raises_unrecognized_error_skips_write() -> None:
    """When replace_order raises an exception that does NOT match known-gone phrases,
    the function must log at exception level and return without saving any order record."""
    execute_cycle_intents = load_cycle_intent_execution_api()
    settings = make_settings()
    now = datetime(2026, 4, 24, 19, 30, tzinfo=timezone.utc)

    active_stop = OrderRecord(
        client_order_id="v1-breakout:2026-04-24:AAPL:stop:2026-04-24T19:00:00+00:00",
        symbol="AAPL",
        side="sell",
        intent_type="stop",
        status="accepted",
        quantity=25,
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        created_at=now,
        updated_at=now,
        stop_price=109.89,
        initial_stop_price=109.89,
        broker_order_id="broker-stop-1",
        signal_timestamp=now,
    )
    position = PositionRecord(
        symbol="AAPL",
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        quantity=25,
        entry_price=111.02,
        stop_price=109.89,
        initial_stop_price=109.89,
        opened_at=now,
        updated_at=now,
    )

    class ExplodingBroker(RecordingBroker):
        def replace_order(self, **kwargs):
            raise RuntimeError("network timeout — not a known-gone phrase")

    order_store = RecordingOrderStore(orders=[active_stop])
    runtime = SimpleNamespace(
        order_store=order_store,
        position_store=RecordingPositionStore(positions=[position]),
        audit_event_store=RecordingAuditEventStore(),
        connection=FakeConnection(),
    )

    report = execute_cycle_intents(
        settings=settings,
        runtime=runtime,
        broker=ExplodingBroker(),
        cycle_result=CycleResult(
            as_of=now,
            intents=[CycleIntent(
                intent_type=CycleIntentType.UPDATE_STOP,
                symbol="AAPL",
                timestamp=now,
                stop_price=112.0,
            )],
        ),
        now=now,
    )

    assert report.replaced_stop_count == 0
    assert order_store.saved == [], "No DB write when broker replace_order fails with unrecognized error"


def test_execute_exit_aborts_when_cancel_order_raises_unrecognized_error() -> None:
    """When cancel_order raises an unrecognized error (not a known-gone phrase),
    _execute_exit must abort without calling submit_market_exit — prevents double-sell."""
    execute_cycle_intents = load_cycle_intent_execution_api()
    settings = make_settings()
    now = datetime(2026, 4, 24, 19, 50, tzinfo=timezone.utc)

    active_stop = OrderRecord(
        client_order_id="v1-breakout:2026-04-24:AAPL:stop:2026-04-24T19:00:00+00:00",
        symbol="AAPL",
        side="sell",
        intent_type="stop",
        status="accepted",
        quantity=25,
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        created_at=now,
        updated_at=now,
        stop_price=109.89,
        initial_stop_price=109.89,
        broker_order_id="broker-stop-1",
        signal_timestamp=now,
    )
    position = PositionRecord(
        symbol="AAPL",
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        quantity=25,
        entry_price=111.02,
        stop_price=109.89,
        initial_stop_price=109.89,
        opened_at=now,
        updated_at=now,
    )
    broker = RecordingBroker(cancel_raises=RuntimeError("rate limit exceeded"))
    runtime = SimpleNamespace(
        order_store=RecordingOrderStore(orders=[active_stop]),
        position_store=RecordingPositionStore(positions=[position]),
        audit_event_store=RecordingAuditEventStore(),
        connection=FakeConnection(),
    )

    report = execute_cycle_intents(
        settings=settings,
        runtime=runtime,
        broker=broker,
        cycle_result=CycleResult(
            as_of=now,
            intents=[CycleIntent(
                intent_type=CycleIntentType.EXIT,
                symbol="AAPL",
                timestamp=now,
                reason="eod_flatten",
            )],
        ),
        now=now,
    )

    # Cancel was attempted but failed; no market exit should have been submitted.
    assert broker.cancel_calls, "cancel_order should have been attempted"
    assert broker.exit_calls == [], "submit_market_exit must NOT be called when cancel fails with unrecognized error"
    assert report.submitted_exit_count == 0


def test_execute_exit_returns_without_db_write_when_submit_market_exit_raises() -> None:
    """When submit_market_exit raises after stops are already canceled, _execute_exit must:
    - NOT write an exit OrderRecord (exit never submitted to broker)
    - Queue a recovery stop immediately (position was left unprotected when stop was cancelled)
    - Emit a recovery_stop_queued_after_exit_failure audit event"""
    execute_cycle_intents = load_cycle_intent_execution_api()
    settings = make_settings()
    now = datetime(2026, 4, 24, 20, 0, tzinfo=timezone.utc)

    active_stop = OrderRecord(
        client_order_id="v1-breakout:2026-04-24:AAPL:stop:exit-raises-test",
        symbol="AAPL",
        side="sell",
        intent_type="stop",
        status="accepted",
        quantity=25,
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        created_at=now,
        updated_at=now,
        stop_price=109.89,
        initial_stop_price=109.89,
        broker_order_id="broker-stop-exit-raises",
        signal_timestamp=now,
    )
    position = PositionRecord(
        symbol="AAPL",
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        quantity=25,
        entry_price=111.02,
        stop_price=109.89,
        initial_stop_price=109.89,
        opened_at=now,
        updated_at=now,
    )

    class FailingExitBroker(RecordingBroker):
        def submit_market_exit(self, **kwargs):
            raise RuntimeError("broker timeout")

    broker = FailingExitBroker()
    order_store = RecordingOrderStore(orders=[active_stop])
    runtime = SimpleNamespace(
        order_store=order_store,
        position_store=RecordingPositionStore(positions=[position]),
        audit_event_store=RecordingAuditEventStore(),
        connection=FakeConnection(),
    )

    report = execute_cycle_intents(
        settings=settings,
        runtime=runtime,
        broker=broker,
        cycle_result=CycleResult(
            as_of=now,
            intents=[CycleIntent(
                intent_type=CycleIntentType.EXIT,
                symbol="AAPL",
                timestamp=now,
                reason="eod_flatten",
            )],
        ),
        now=now,
    )

    assert broker.cancel_calls, "stop cancel should have been attempted"
    assert broker.exit_calls == [], "submit_market_exit raised — no exit_calls recorded"
    # canceled_stop_count is returned (the stop WAS canceled), but submitted_exit_count = 0
    assert report.submitted_exit_count == 0
    # No exit OrderRecord written to DB
    exit_writes = [o for o in order_store.saved if o.intent_type == "exit"]
    assert exit_writes == [], "No exit record must be written when submit_market_exit raises"
    # A recovery stop must be queued immediately — position was left unprotected
    recovery_stops = [
        o for o in order_store.saved
        if o.intent_type == "stop"
        and o.status == "pending_submit"
        and o.symbol == "AAPL"
    ]
    assert len(recovery_stops) == 1, (
        "Must queue exactly one recovery stop when exit fails after stop cancel"
    )
    assert recovery_stops[0].stop_price == pytest.approx(109.89)
    assert recovery_stops[0].quantity == 25
    assert recovery_stops[0].side == "sell"
    recovery_events = [
        e for e in runtime.audit_event_store.appended
        if e.event_type == "recovery_stop_queued_after_exit_failure"
        and e.symbol == "AAPL"
    ]
    assert len(recovery_events) == 1, (
        "Must emit exactly one recovery_stop_queued_after_exit_failure audit event"
    )


def test_execute_exit_saves_exit_record_when_position_disappears_after_submit() -> None:
    """When the position disappears between submit_market_exit and the DB write (TOCTOU race),
    _execute_exit must still save the exit order record so the fill event can be matched
    and daily_realized_pnl can account for the trade. Without saving, the fill arrives as
    trade_update_unmatched and PnL is permanently missing for this trade."""
    execute_cycle_intents = load_cycle_intent_execution_api()
    settings = make_settings()
    now = datetime(2026, 4, 24, 20, 10, tzinfo=timezone.utc)

    active_stop = OrderRecord(
        client_order_id="v1-breakout:2026-04-24:AAPL:stop:toctou-test",
        symbol="AAPL",
        side="sell",
        intent_type="stop",
        status="accepted",
        quantity=25,
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        created_at=now,
        updated_at=now,
        stop_price=109.89,
        initial_stop_price=109.89,
        broker_order_id="broker-stop-toctou",
        signal_timestamp=now,
    )
    position = PositionRecord(
        symbol="AAPL",
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        quantity=25,
        entry_price=111.02,
        stop_price=109.89,
        initial_stop_price=109.89,
        opened_at=now,
        updated_at=now,
    )

    # Position store returns position for the first two calls (execute_cycle_intents lookup
    # + _execute_exit pre-submit check) but returns empty on the third call (post-submit
    # re-check inside the final lock), simulating the fill stream closing the position
    # while broker calls were in-flight.
    call_count = 0

    class DisappearingPositionStore(RecordingPositionStore):
        def list_all(self, *, trading_mode, strategy_version):
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                return list(self.positions)  # initial lookup + pre-submit check: position exists
            return []  # post-submit re-check: position gone

    order_store = RecordingOrderStore(orders=[active_stop])
    runtime = SimpleNamespace(
        order_store=order_store,
        position_store=DisappearingPositionStore(positions=[position]),
        audit_event_store=RecordingAuditEventStore(),
        connection=FakeConnection(),
    )
    broker = RecordingBroker()

    report = execute_cycle_intents(
        settings=settings,
        runtime=runtime,
        broker=broker,
        cycle_result=CycleResult(
            as_of=now,
            intents=[CycleIntent(
                intent_type=CycleIntentType.EXIT,
                symbol="AAPL",
                timestamp=now,
                reason="eod_flatten",
            )],
        ),
        now=now,
    )

    assert broker.exit_calls, "submit_market_exit should have been called before position disappeared"
    assert report.submitted_exit_count == 1, (
        "submitted_exit_count must be 1: broker accepted the exit order even though "
        "the position was cleaned up by the fill stream before the DB write"
    )
    exit_writes = [o for o in order_store.saved if o.intent_type == "exit"]
    assert len(exit_writes) == 1, (
        "Exit order record must be saved for fill-event matching and PnL tracking"
    )
    assert exit_writes[0].broker_order_id == "broker-exit-1", (
        "Exit record must carry the broker_order_id returned by submit_market_exit"
    )


def test_execute_exit_cancel_hard_failed_writes_partial_cancels_before_early_return() -> None:
    """When two stop orders exist and the first cancels successfully but the second
    raises an unrecognized error (cancel_hard_failed=True), the successfully-canceled
    first stop must be written to DB before the early return — otherwise the next cycle
    sees it as an active stop, tries to cancel, gets 'already canceled', interprets that
    as position_already_gone, and permanently abandons the exit."""
    execute_cycle_intents = load_cycle_intent_execution_api()
    settings = make_settings()
    now = datetime(2026, 4, 24, 20, 20, tzinfo=timezone.utc)

    first_stop = OrderRecord(
        client_order_id="v1-breakout:2026-04-24:AAPL:stop:partial-cancel-1",
        symbol="AAPL",
        side="sell",
        intent_type="stop",
        status="accepted",
        quantity=15,
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        created_at=now,
        updated_at=now,
        stop_price=109.0,
        initial_stop_price=109.0,
        broker_order_id="broker-stop-first",
        signal_timestamp=now,
    )
    second_stop = OrderRecord(
        client_order_id="v1-breakout:2026-04-24:AAPL:stop:partial-cancel-2",
        symbol="AAPL",
        side="sell",
        intent_type="stop",
        status="accepted",
        quantity=10,
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        created_at=now,
        updated_at=now,
        stop_price=108.5,
        initial_stop_price=108.5,
        broker_order_id="broker-stop-second",
        signal_timestamp=now,
    )
    position = PositionRecord(
        symbol="AAPL",
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        quantity=25,
        entry_price=111.02,
        stop_price=109.0,
        initial_stop_price=109.0,
        opened_at=now,
        updated_at=now,
    )

    # First cancel_order call succeeds; second raises an unrecognized error.
    cancel_call_count = 0

    class SelectiveFailBroker(RecordingBroker):
        def cancel_order(self, order_id: str) -> None:
            nonlocal cancel_call_count
            cancel_call_count += 1
            self.cancel_calls.append(order_id)
            if cancel_call_count >= 2:
                raise RuntimeError("rate limit exceeded")

    broker = SelectiveFailBroker()
    order_store = RecordingOrderStore(orders=[first_stop, second_stop])
    runtime = SimpleNamespace(
        order_store=order_store,
        position_store=RecordingPositionStore(positions=[position]),
        audit_event_store=RecordingAuditEventStore(),
        connection=FakeConnection(),
    )

    report = execute_cycle_intents(
        settings=settings,
        runtime=runtime,
        broker=broker,
        cycle_result=CycleResult(
            as_of=now,
            intents=[CycleIntent(
                intent_type=CycleIntentType.EXIT,
                symbol="AAPL",
                timestamp=now,
                reason="eod_flatten",
            )],
        ),
        now=now,
    )

    # Abort: no market exit should have been submitted.
    assert broker.exit_calls == [], "submit_market_exit must NOT be called when a cancel hard-fails"
    assert report.submitted_exit_count == 0

    # The successfully-canceled first stop MUST be written to DB.
    saved_client_ids = {o.client_order_id for o in order_store.saved}
    assert first_stop.client_order_id in saved_client_ids, (
        "The first stop (which was successfully canceled at broker) must be written "
        "to DB as 'canceled' so the next cycle doesn't see it as active"
    )
    canceled_writes = [o for o in order_store.saved if o.status == "canceled"]
    assert len(canceled_writes) == 1, f"Expected exactly 1 canceled DB write, got {len(canceled_writes)}"


# ---------------------------------------------------------------------------
# Rollback guard coverage for _execute_exit early-return paths
# ---------------------------------------------------------------------------

class _FailingOnSaveOrderStore:
    """Order store that raises on save() — used to test rollback guards."""

    def __init__(self, *, orders: list[OrderRecord] | None = None) -> None:
        self._orders: dict[str, OrderRecord] = {o.client_order_id: o for o in (orders or [])}

    def load(self, client_order_id: str) -> OrderRecord | None:
        return self._orders.get(client_order_id)

    def load_by_broker_order_id(self, broker_order_id: str) -> OrderRecord | None:
        for o in self._orders.values():
            if o.broker_order_id == broker_order_id:
                return o
        return None

    def save(self, order: OrderRecord, *, commit: bool = True) -> None:
        raise RuntimeError("simulated_db_failure")

    def list_by_status(
        self,
        *,
        trading_mode,
        strategy_version: str,
        statuses: list[str],
        strategy_name: str | None = None,
    ) -> list[OrderRecord]:
        orders = [o for o in self._orders.values() if o.status in statuses]
        if strategy_name is not None:
            orders = [o for o in orders if o.strategy_name == strategy_name]
        return orders


class _RollbackTrackingConnection:
    def __init__(self) -> None:
        self.rollback_count = 0

    def commit(self) -> None:
        pass

    def rollback(self) -> None:
        self.rollback_count += 1


def _make_aapl_stop(*, now: datetime, broker_order_id: str, client_suffix: str) -> OrderRecord:
    return OrderRecord(
        client_order_id=f"v1-breakout:2026-04-24:AAPL:stop:{client_suffix}",
        symbol="AAPL",
        side="sell",
        intent_type="stop",
        status="accepted",
        quantity=10,
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        created_at=now,
        updated_at=now,
        stop_price=109.0,
        initial_stop_price=109.0,
        broker_order_id=broker_order_id,
        signal_timestamp=now,
    )


def _make_aapl_position(*, now: datetime) -> PositionRecord:
    return PositionRecord(
        symbol="AAPL",
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        quantity=10,
        entry_price=111.0,
        stop_price=109.0,
        initial_stop_price=109.0,
        opened_at=now,
        updated_at=now,
    )


def test_execute_exit_cancel_hard_failed_rollback_on_db_failure() -> None:
    """cancel_hard_failed path: if the DB write for successfully-canceled stops raises,
    connection.rollback() must be called but the exception must NOT propagate.

    Re-raising here would leave the successfully-canceled stops looking "active" in the
    DB, causing every subsequent cycle to re-cancel them, hit "already_canceled", set
    position_already_gone, and permanently abandon the exit for an unprotected position.
    Swallowing the DB failure (after rollback+log) is the safer outcome.
    """
    execute_cycle_intents = load_cycle_intent_execution_api()
    settings = make_settings()
    now = datetime(2026, 4, 24, 21, 0, tzinfo=timezone.utc)

    first_stop = _make_aapl_stop(now=now, broker_order_id="broker-hf-1", client_suffix="hf-1")
    second_stop = _make_aapl_stop(now=now, broker_order_id="broker-hf-2", client_suffix="hf-2")
    position = _make_aapl_position(now=now)

    cancel_call_count = 0

    class HardFailOnSecondBroker(RecordingBroker):
        def cancel_order(self, order_id: str) -> None:
            nonlocal cancel_call_count
            cancel_call_count += 1
            self.cancel_calls.append(order_id)
            if cancel_call_count >= 2:
                raise RuntimeError("network timeout")

    connection = _RollbackTrackingConnection()
    runtime = SimpleNamespace(
        order_store=_FailingOnSaveOrderStore(orders=[first_stop, second_stop]),
        position_store=RecordingPositionStore(positions=[position]),
        audit_event_store=RecordingAuditEventStore(),
        connection=connection,
    )

    # Must NOT raise — DB write failure is swallowed to avoid stale active-stop records.
    execute_cycle_intents(
        settings=settings,
        runtime=runtime,
        broker=HardFailOnSecondBroker(),
        cycle_result=CycleResult(
            as_of=now,
            intents=[CycleIntent(
                intent_type=CycleIntentType.EXIT,
                symbol="AAPL",
                timestamp=now,
                reason="eod_flatten",
            )],
        ),
        now=now,
    )

    assert connection.rollback_count == 1, (
        f"rollback must be called once when DB write fails in cancel_hard_failed path; "
        f"got rollback_count={connection.rollback_count}"
    )


def test_execute_exit_position_already_gone_rollback_on_db_failure() -> None:
    """position_already_gone path: if cancel raises 'not found' and the subsequent DB
    write raises, connection.rollback() must be called and the exception must propagate."""
    import pytest
    execute_cycle_intents = load_cycle_intent_execution_api()
    settings = make_settings()
    now = datetime(2026, 4, 24, 21, 10, tzinfo=timezone.utc)

    stop = _make_aapl_stop(now=now, broker_order_id="broker-pag-1", client_suffix="pag-1")
    position = _make_aapl_position(now=now)

    class NotFoundBroker(RecordingBroker):
        def cancel_order(self, order_id: str) -> None:
            self.cancel_calls.append(order_id)
            raise RuntimeError("order not found at broker")

    connection = _RollbackTrackingConnection()
    runtime = SimpleNamespace(
        order_store=_FailingOnSaveOrderStore(orders=[stop]),
        position_store=RecordingPositionStore(positions=[position]),
        audit_event_store=RecordingAuditEventStore(),
        connection=connection,
    )

    with pytest.raises(RuntimeError, match="simulated_db_failure"):
        execute_cycle_intents(
            settings=settings,
            runtime=runtime,
            broker=NotFoundBroker(),
            cycle_result=CycleResult(
                as_of=now,
                intents=[CycleIntent(
                    intent_type=CycleIntentType.EXIT,
                    symbol="AAPL",
                    timestamp=now,
                    reason="eod_flatten",
                )],
            ),
            now=now,
        )

    assert connection.rollback_count == 1, (
        f"rollback must be called once when DB write fails in position_already_gone path; "
        f"got rollback_count={connection.rollback_count}"
    )


def test_execute_exit_re_verify_position_gone_rollback_on_db_failure() -> None:
    """Re-verify path: stop cancel succeeds but position disappears between cancel and re-verify.
    If the DB write in that path raises, connection.rollback() must be called."""
    import pytest
    execute_cycle_intents = load_cycle_intent_execution_api()
    settings = make_settings()
    now = datetime(2026, 4, 24, 21, 20, tzinfo=timezone.utc)

    stop = _make_aapl_stop(now=now, broker_order_id="broker-rv-1", client_suffix="rv-1")
    position = _make_aapl_position(now=now)

    # Position store returns position on first call (for execute_cycle_intents fetch),
    # then empty on subsequent calls (re-verify inside _execute_exit).
    class VanishingPositionStore:
        def __init__(self) -> None:
            self._call_count = 0

        def list_all(self, *, trading_mode, strategy_version) -> list[PositionRecord]:
            self._call_count += 1
            return [position] if self._call_count == 1 else []

        def save(self, p: PositionRecord, *, commit: bool = True) -> None:
            pass

    connection = _RollbackTrackingConnection()
    runtime = SimpleNamespace(
        order_store=_FailingOnSaveOrderStore(orders=[stop]),
        position_store=VanishingPositionStore(),
        audit_event_store=RecordingAuditEventStore(),
        connection=connection,
    )

    with pytest.raises(RuntimeError, match="simulated_db_failure"):
        execute_cycle_intents(
            settings=settings,
            runtime=runtime,
            broker=RecordingBroker(),
            cycle_result=CycleResult(
                as_of=now,
                intents=[CycleIntent(
                    intent_type=CycleIntentType.EXIT,
                    symbol="AAPL",
                    timestamp=now,
                    reason="eod_flatten",
                )],
            ),
            now=now,
        )

    assert connection.rollback_count == 1, (
        f"rollback must be called once when DB write fails in re-verify-position-gone path; "
        f"got rollback_count={connection.rollback_count}"
    )


def test_execute_exit_disappeared_after_submit_rollback_on_db_failure() -> None:
    """Final lock block, 'position disappeared during broker exit' branch:
    position vanishes between submit_market_exit and the final write; if the DB
    write for the canceled stop records fails, rollback() must be called."""
    import pytest
    execute_cycle_intents = load_cycle_intent_execution_api()
    settings = make_settings()
    now = datetime(2026, 4, 24, 21, 30, tzinfo=timezone.utc)

    stop = _make_aapl_stop(now=now, broker_order_id="broker-das-1", client_suffix="das-1")
    position = _make_aapl_position(now=now)

    # Position present for calls 1 (initial load) and 2 (re-verify before submit),
    # absent on call 3 (check inside final lock block after submit).
    class ThreeCallVanishingPositionStore:
        def __init__(self) -> None:
            self._call_count = 0

        def list_all(self, *, trading_mode, strategy_version) -> list[PositionRecord]:
            self._call_count += 1
            return [position] if self._call_count <= 2 else []

        def save(self, p: PositionRecord, *, commit: bool = True) -> None:
            pass

    connection = _RollbackTrackingConnection()
    runtime = SimpleNamespace(
        order_store=_FailingOnSaveOrderStore(orders=[stop]),
        position_store=ThreeCallVanishingPositionStore(),
        audit_event_store=RecordingAuditEventStore(),
        connection=connection,
    )

    with pytest.raises(RuntimeError, match="simulated_db_failure"):
        execute_cycle_intents(
            settings=settings,
            runtime=runtime,
            broker=RecordingBroker(),
            cycle_result=CycleResult(
                as_of=now,
                intents=[CycleIntent(
                    intent_type=CycleIntentType.EXIT,
                    symbol="AAPL",
                    timestamp=now,
                    reason="eod_flatten",
                )],
            ),
            now=now,
        )

    assert connection.rollback_count == 1, (
        f"rollback must be called once when DB write fails in 'position disappeared after submit' path; "
        f"got rollback_count={connection.rollback_count}"
    )


class _FailingAuditEventStore:
    """Audit event store that always raises on append — used to test rollback guards."""

    def append(self, event: AuditEvent, *, commit: bool = True) -> None:
        raise RuntimeError("simulated_audit_failure")


def test_execute_exit_active_exit_order_exists_rollback_on_db_failure() -> None:
    """active_exit_order_exists early-return path: if the audit append raises,
    connection.rollback() must be called and the exception must propagate."""
    import pytest
    execute_cycle_intents = load_cycle_intent_execution_api()
    settings = make_settings()
    now = datetime(2026, 4, 24, 21, 40, tzinfo=timezone.utc)

    existing_exit = OrderRecord(
        client_order_id="v1-breakout:2026-04-24:AAPL:exit:existing-ae",
        symbol="AAPL",
        side="sell",
        intent_type="exit",
        status="accepted",
        quantity=10,
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        created_at=now,
        updated_at=now,
        initial_stop_price=109.0,
        broker_order_id="broker-exit-ae",
        signal_timestamp=now,
    )
    position = _make_aapl_position(now=now)

    connection = _RollbackTrackingConnection()
    runtime = SimpleNamespace(
        order_store=RecordingOrderStore(orders=[existing_exit]),
        position_store=RecordingPositionStore(positions=[position]),
        audit_event_store=_FailingAuditEventStore(),
        connection=connection,
    )

    with pytest.raises(RuntimeError, match="simulated_audit_failure"):
        execute_cycle_intents(
            settings=settings,
            runtime=runtime,
            broker=RecordingBroker(),
            cycle_result=CycleResult(
                as_of=now,
                intents=[CycleIntent(
                    intent_type=CycleIntentType.EXIT,
                    symbol="AAPL",
                    timestamp=now,
                    reason="eod_flatten",
                )],
            ),
            now=now,
        )

    assert connection.rollback_count == 1, (
        f"rollback must be called once when audit append fails in active_exit_order_exists path; "
        f"got rollback_count={connection.rollback_count}"
    )


def test_execute_update_stop_rollback_on_db_failure() -> None:
    """If any store write fails inside _execute_update_stop's atomic block,
    connection.rollback() must be called and the exception must propagate."""
    import pytest
    execute_cycle_intents = load_cycle_intent_execution_api()
    settings = make_settings()
    now = datetime(2026, 4, 24, 19, 30, tzinfo=timezone.utc)

    active_stop = _make_aapl_stop(now=now, broker_order_id="broker-us-rb", client_suffix="us-rb")
    position = _make_aapl_position(now=now)

    class FailingPositionStore(RecordingPositionStore):
        def save(self, p: PositionRecord, *, commit: bool = True) -> None:
            raise RuntimeError("simulated_db_failure")

    connection = _RollbackTrackingConnection()
    runtime = SimpleNamespace(
        order_store=RecordingOrderStore(orders=[active_stop]),
        position_store=FailingPositionStore(positions=[position]),
        audit_event_store=RecordingAuditEventStore(),
        connection=connection,
    )

    with pytest.raises(RuntimeError, match="simulated_db_failure"):
        execute_cycle_intents(
            settings=settings,
            runtime=runtime,
            broker=RecordingBroker(),
            cycle_result=CycleResult(
                as_of=now,
                intents=[CycleIntent(
                    intent_type=CycleIntentType.UPDATE_STOP,
                    symbol="AAPL",
                    timestamp=now,
                    stop_price=112.0,
                )],
            ),
            now=now,
        )

    assert connection.rollback_count == 1, (
        f"rollback must be called once when DB write fails in _execute_update_stop; "
        f"got rollback_count={connection.rollback_count}"
    )


def test_execute_update_stop_uses_commit_false_for_all_writes() -> None:
    """order_store.save(), position_store.save(), and audit_event_store.append()
    in _execute_update_stop must all use commit=False; exactly one connection.commit() fires."""
    execute_cycle_intents = load_cycle_intent_execution_api()
    settings = make_settings()
    now = datetime(2026, 4, 24, 19, 40, tzinfo=timezone.utc)

    active_stop = _make_aapl_stop(now=now, broker_order_id="broker-us-cd", client_suffix="us-cd")
    position = _make_aapl_position(now=now)

    order_commit_args: list[bool] = []
    position_commit_args: list[bool] = []
    audit_commit_args: list[bool] = []
    commit_count = [0]

    class TrackingOrderStore(RecordingOrderStore):
        def save(self, order: OrderRecord, *, commit: bool = True) -> None:
            order_commit_args.append(commit)
            super().save(order, commit=commit)

    class TrackingPositionStore(RecordingPositionStore):
        def save(self, p: PositionRecord, *, commit: bool = True) -> None:
            position_commit_args.append(commit)
            super().save(p, commit=commit)

    class TrackingAuditStore(RecordingAuditEventStore):
        def append(self, event: AuditEvent, *, commit: bool = True) -> None:
            audit_commit_args.append(commit)
            super().append(event, commit=commit)

    class TrackingConnection:
        def commit(self) -> None:
            commit_count[0] += 1

        def rollback(self) -> None:
            pass

    runtime = SimpleNamespace(
        order_store=TrackingOrderStore(orders=[active_stop]),
        position_store=TrackingPositionStore(positions=[position]),
        audit_event_store=TrackingAuditStore(),
        connection=TrackingConnection(),
    )

    execute_cycle_intents(
        settings=settings,
        runtime=runtime,
        broker=RecordingBroker(),
        cycle_result=CycleResult(
            as_of=now,
            intents=[CycleIntent(
                intent_type=CycleIntentType.UPDATE_STOP,
                symbol="AAPL",
                timestamp=now,
                stop_price=112.0,
            )],
        ),
        now=now,
    )

    assert all(not c for c in order_commit_args), (
        f"order_store.save() must use commit=False in _execute_update_stop; got {order_commit_args}"
    )
    assert all(not c for c in position_commit_args), (
        f"position_store.save() must use commit=False in _execute_update_stop; got {position_commit_args}"
    )
    assert all(not c for c in audit_commit_args), (
        f"audit_event_store.append() must use commit=False in _execute_update_stop; got {audit_commit_args}"
    )
    assert commit_count[0] == 1, (
        f"Exactly one connection.commit() must fire in _execute_update_stop; got {commit_count[0]}"
    )


def test_execute_update_stop_does_not_submit_to_broker_when_stop_is_pending_submit() -> None:
    """UPDATE_STOP must not call broker.submit_stop_order when an unsubmitted (pending_submit)
    protective stop already exists.

    Sequence: entry fills → trade-update stream writes pending_submit stop (broker_order_id=None)
    → next cycle fires UPDATE_STOP for trailing-stop improvement → _execute_update_stop must
    update the pending record's stop_price in-place and let dispatch_pending_orders submit it.
    Calling submit_stop_order here AND dispatch both submitting would create two live sell-stop
    orders at the broker for the same position.
    """
    execute_cycle_intents = load_cycle_intent_execution_api()
    settings = make_settings()
    now = datetime(2026, 4, 24, 14, 0, tzinfo=timezone.utc)

    # pending_submit stop with no broker_order_id — as written by apply_trade_update on fill
    pending_stop = OrderRecord(
        client_order_id="v1-breakout:2026-04-24:AAPL:stop:pending",
        symbol="AAPL",
        side="sell",
        intent_type="stop",
        status="pending_submit",
        quantity=10,
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        created_at=now,
        updated_at=now,
        stop_price=109.89,
        initial_stop_price=109.89,
        broker_order_id=None,
        signal_timestamp=now,
    )
    position = _make_aapl_position(now=now)

    order_store = RecordingOrderStore(orders=[pending_stop])
    position_store = RecordingPositionStore(positions=[position])
    audit_store = RecordingAuditEventStore()
    broker = RecordingBroker()
    runtime = SimpleNamespace(
        order_store=order_store,
        position_store=position_store,
        audit_event_store=audit_store,
        connection=FakeConnection(),
    )

    execute_cycle_intents(
        settings=settings,
        runtime=runtime,
        broker=broker,
        cycle_result=CycleResult(
            as_of=now,
            intents=[CycleIntent(
                intent_type=CycleIntentType.UPDATE_STOP,
                symbol="AAPL",
                timestamp=now,
                stop_price=110.50,
            )],
        ),
        now=now,
    )

    assert not broker.stop_calls, (
        "submit_stop_order must NOT be called when a pending_submit stop exists — "
        "dispatch_pending_orders would submit the same stop, creating a duplicate"
    )
    assert not broker.replace_calls, "replace_order must not be called for an unsubmitted stop"
    # The DB record must be updated with the new stop_price so dispatch submits correctly
    assert len(order_store.saved) == 1
    saved = order_store.saved[0]
    assert saved.stop_price == 110.50, "pending_submit record must be updated with the improved stop"
    assert saved.broker_order_id is None, "broker_order_id must remain None — not yet dispatched"
    assert saved.status == "pending_submit", "status must remain pending_submit for dispatch"


def test_execute_exit_proceeds_when_cancel_returns_already_canceled() -> None:
    """cancel_order returning 'already canceled' must NOT be treated as position-gone.

    A stop can be canceled by a prior replace_order (the broker cancels the old order
    as a side effect of creating the replacement) while the equity position remains open.
    Treating 'already canceled' as position-gone would abandon the exit and leave an
    unprotected open position. The market exit must proceed; the pre-exit reverify step
    handles the case where the position is actually already closed.
    """
    execute_cycle_intents = load_cycle_intent_execution_api()
    settings = make_settings()
    now = datetime(2026, 4, 24, 19, 50, tzinfo=timezone.utc)

    active_stop = OrderRecord(
        client_order_id="v1-breakout:2026-04-24:AAPL:stop:already-canceled-test",
        symbol="AAPL",
        side="sell",
        intent_type="stop",
        status="accepted",
        quantity=25,
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        created_at=now,
        updated_at=now,
        stop_price=109.89,
        initial_stop_price=109.89,
        broker_order_id="broker-stop-ac",
        signal_timestamp=now,
    )
    position = PositionRecord(
        symbol="AAPL",
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        quantity=25,
        entry_price=111.02,
        stop_price=109.89,
        initial_stop_price=109.89,
        opened_at=now,
        updated_at=now,
    )
    broker = RecordingBroker(cancel_raises=Exception("already canceled"))
    runtime = SimpleNamespace(
        order_store=RecordingOrderStore(orders=[active_stop]),
        position_store=RecordingPositionStore(positions=[position]),
        audit_event_store=RecordingAuditEventStore(),
        connection=FakeConnection(),
    )

    report = execute_cycle_intents(
        settings=settings,
        runtime=runtime,
        broker=broker,
        cycle_result=CycleResult(
            as_of=now,
            intents=[CycleIntent(
                intent_type=CycleIntentType.EXIT,
                symbol="AAPL",
                timestamp=now,
                reason="eod_flatten",
            )],
        ),
        now=now,
    )

    assert broker.exit_calls, (
        "submit_market_exit must be called when cancel returns 'already canceled' — "
        "stop being canceled does not mean position is gone"
    )
    assert report.submitted_exit_count == 1


def test_execute_update_stop_pending_submit_counted_and_cache_refreshed() -> None:
    """updated_pending action must be counted in the report and the positions cache
    must be refreshed so a subsequent intent for the same symbol sees the new stop_price."""
    execute_cycle_intents = load_cycle_intent_execution_api()
    settings = make_settings()
    now = datetime(2026, 4, 24, 14, 0, tzinfo=timezone.utc)

    pending_stop = OrderRecord(
        client_order_id="v1-breakout:2026-04-24:AAPL:stop:cache-test",
        symbol="AAPL",
        side="sell",
        intent_type="stop",
        status="pending_submit",
        quantity=10,
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        created_at=now,
        updated_at=now,
        stop_price=109.89,
        initial_stop_price=109.89,
        broker_order_id=None,
        signal_timestamp=now,
    )
    position = _make_aapl_position(now=now)

    order_store = RecordingOrderStore(orders=[pending_stop])
    runtime = SimpleNamespace(
        order_store=order_store,
        position_store=RecordingPositionStore(positions=[position]),
        audit_event_store=RecordingAuditEventStore(),
        connection=FakeConnection(),
    )
    broker = RecordingBroker()

    report = execute_cycle_intents(
        settings=settings,
        runtime=runtime,
        broker=broker,
        cycle_result=CycleResult(
            as_of=now,
            intents=[CycleIntent(
                intent_type=CycleIntentType.UPDATE_STOP,
                symbol="AAPL",
                timestamp=now,
                stop_price=110.50,
            )],
        ),
        now=now,
    )

    assert report.updated_pending_stop_count == 1, (
        f"updated_pending_stop_count must be 1; got {report.updated_pending_stop_count}"
    )
    assert report.submitted_stop_count == 0
    assert report.replaced_stop_count == 0


def test_execute_update_stop_no_op_when_stop_not_improving() -> None:
    """_execute_update_stop must be a no-op when the new stop_price <= current position.stop_price.
    No broker call and no DB write should occur — this is the hot path in a trending session."""
    execute_cycle_intents = load_cycle_intent_execution_api()
    settings = make_settings()
    now = datetime(2026, 4, 24, 14, 0, tzinfo=timezone.utc)

    active_stop = _make_aapl_stop(now=now, broker_order_id="broker-us-noi", client_suffix="us-noi")
    position = _make_aapl_position(now=now)  # stop_price=109.0

    order_store = RecordingOrderStore(orders=[active_stop])
    position_store = RecordingPositionStore(positions=[position])
    audit_store = RecordingAuditEventStore()
    broker = RecordingBroker()
    runtime = SimpleNamespace(
        order_store=order_store,
        position_store=position_store,
        audit_event_store=audit_store,
        connection=FakeConnection(),
    )

    for non_improving_stop in (109.0, 108.5, 90.0):
        execute_cycle_intents(
            settings=settings,
            runtime=runtime,
            broker=broker,
            cycle_result=CycleResult(
                as_of=now,
                intents=[CycleIntent(
                    intent_type=CycleIntentType.UPDATE_STOP,
                    symbol="AAPL",
                    timestamp=now,
                    stop_price=non_improving_stop,
                )],
            ),
            now=now,
        )

    assert not broker.replace_calls, (
        "replace_order must NOT be called when stop_price does not improve"
    )
    assert order_store.saved == [], (
        "No DB write must occur when stop_price does not improve"
    )


def test_execute_update_stop_skips_write_on_extended_already_filled_phrases() -> None:
    """_execute_update_stop must treat Alpaca's alternate 'has been filled' and 'is filled'
    phrases as known-gone conditions (skip write, no exception) — not unrecognized errors."""
    execute_cycle_intents = load_cycle_intent_execution_api()
    settings = make_settings()
    now = datetime(2026, 4, 25, 14, 30, tzinfo=timezone.utc)

    active_stop = OrderRecord(
        client_order_id="v1-breakout:2026-04-25:AAPL:stop:2026-04-25T14:00:00+00:00",
        symbol="AAPL",
        side="sell",
        intent_type="stop",
        status="accepted",
        quantity=25,
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        created_at=now,
        updated_at=now,
        stop_price=109.89,
        initial_stop_price=109.89,
        broker_order_id="broker-stop-1",
        signal_timestamp=now,
    )
    position = PositionRecord(
        symbol="AAPL",
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        quantity=25,
        entry_price=111.02,
        stop_price=109.89,
        initial_stop_price=109.89,
        opened_at=now,
        updated_at=now,
    )

    for phrase in ("order has been filled", "order is filled", "order is already gone"):
        class ExplodingBroker(RecordingBroker):
            _phrase = phrase

            def replace_order(self, **kwargs):
                raise RuntimeError(self._phrase)

        order_store = RecordingOrderStore(orders=[active_stop])
        runtime = SimpleNamespace(
            order_store=order_store,
            position_store=RecordingPositionStore(positions=[position]),
            audit_event_store=RecordingAuditEventStore(),
            connection=FakeConnection(),
        )

        report = execute_cycle_intents(
            settings=settings,
            runtime=runtime,
            broker=ExplodingBroker(),
            cycle_result=CycleResult(
                as_of=now,
                intents=[CycleIntent(
                    intent_type=CycleIntentType.UPDATE_STOP,
                    symbol="AAPL",
                    timestamp=now,
                    stop_price=111.00,
                )],
            ),
            now=now,
        )

        assert order_store.saved == [], (
            f"No DB write expected for known-gone phrase {phrase!r}; got {order_store.saved}"
        )
        assert report.replaced_stop_count == 0, (
            f"replaced_stop_count must be 0 for known-gone phrase {phrase!r}"
        )


# ---------------------------------------------------------------------------
# Gap 2: UPDATE_STOP unrecognized exception — audit + notifier
# ---------------------------------------------------------------------------

def test_update_stop_unrecognized_exception_writes_audit_and_fires_notifier() -> None:
    """An unrecognized exception from replace_order() must emit stop_update_failed
    audit event and fire notifier alert so operator knows the position lost stop protection."""
    from alpaca_bot.runtime.cycle_intent_execution import execute_cycle_intents

    settings = make_settings()
    now = datetime(2026, 4, 27, 14, 30, tzinfo=timezone.utc)

    active_stop = OrderRecord(
        client_order_id="paper:v1-breakout:AAPL:stop:1",
        symbol="AAPL",
        side="sell",
        intent_type="stop",
        status="new",
        quantity=25,
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        created_at=now,
        updated_at=now,
        stop_price=109.89,
        broker_order_id="alpaca-stop-1",
    )
    position = PositionRecord(
        symbol="AAPL",
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        quantity=25,
        entry_price=112.0,
        stop_price=109.89,
        initial_stop_price=109.89,
        opened_at=now,
        updated_at=now,
    )
    audit_event_store = RecordingAuditEventStore()

    class UnknownErrorBroker(RecordingBroker):
        def replace_order(self, **kwargs):
            raise RuntimeError("connection_timeout_unknown_error_xyz")

    notifier_calls: list[dict] = []

    class RecordingNotifier:
        def send(self, *, subject: str, body: str) -> None:
            notifier_calls.append({"subject": subject, "body": body})

    runtime = SimpleNamespace(
        order_store=RecordingOrderStore(orders=[active_stop]),
        position_store=RecordingPositionStore(positions=[position]),
        audit_event_store=audit_event_store,
        connection=FakeConnection(),
    )

    execute_cycle_intents(
        settings=settings,
        runtime=runtime,
        broker=UnknownErrorBroker(),
        cycle_result=CycleResult(
            as_of=now,
            intents=[CycleIntent(
                intent_type=CycleIntentType.UPDATE_STOP,
                symbol="AAPL",
                timestamp=now,
                stop_price=111.00,
            )],
        ),
        now=now,
        notifier=RecordingNotifier(),
    )

    failed_audits = [e for e in audit_event_store.appended if e.event_type == "stop_update_failed"]
    assert len(failed_audits) == 1, "stop_update_failed audit event must be appended"
    assert failed_audits[0].symbol == "AAPL"
    assert "connection_timeout_unknown_error_xyz" in failed_audits[0].payload["error"]

    assert len(notifier_calls) == 1, "notifier must be called for unrecognized exception"
    assert "AAPL" in notifier_calls[0]["subject"]


def test_update_stop_known_gone_phrase_does_not_fire_audit_or_notifier() -> None:
    """Known 'order already gone' phrases must NOT emit stop_update_failed or fire notifier."""
    from alpaca_bot.runtime.cycle_intent_execution import execute_cycle_intents

    settings = make_settings()
    now = datetime(2026, 4, 27, 14, 35, tzinfo=timezone.utc)

    active_stop = OrderRecord(
        client_order_id="paper:v1-breakout:AAPL:stop:gone",
        symbol="AAPL",
        side="sell",
        intent_type="stop",
        status="new",
        quantity=25,
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        created_at=now,
        updated_at=now,
        stop_price=109.89,
        broker_order_id="alpaca-stop-gone",
    )
    position = PositionRecord(
        symbol="AAPL",
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        quantity=25,
        entry_price=112.0,
        stop_price=109.89,
        initial_stop_price=109.89,
        opened_at=now,
        updated_at=now,
    )
    audit_event_store = RecordingAuditEventStore()

    class GoneBroker(RecordingBroker):
        def replace_order(self, **kwargs):
            raise RuntimeError("order not found")

    notifier_calls: list[dict] = []

    class RecordingNotifier:
        def send(self, *, subject: str, body: str) -> None:
            notifier_calls.append({"subject": subject, "body": body})

    runtime = SimpleNamespace(
        order_store=RecordingOrderStore(orders=[active_stop]),
        position_store=RecordingPositionStore(positions=[position]),
        audit_event_store=audit_event_store,
        connection=FakeConnection(),
    )

    execute_cycle_intents(
        settings=settings,
        runtime=runtime,
        broker=GoneBroker(),
        cycle_result=CycleResult(
            as_of=now,
            intents=[CycleIntent(
                intent_type=CycleIntentType.UPDATE_STOP,
                symbol="AAPL",
                timestamp=now,
                stop_price=111.00,
            )],
        ),
        now=now,
        notifier=RecordingNotifier(),
    )

    failed_audits = [e for e in audit_event_store.appended if e.event_type == "stop_update_failed"]
    assert failed_audits == [], "known-gone phrase must not emit stop_update_failed audit"
    assert notifier_calls == [], "known-gone phrase must not fire notifier"


# ---------------------------------------------------------------------------
# Exit hard-failure notifier
# ---------------------------------------------------------------------------


def test_exit_submission_failure_fires_notifier() -> None:
    """When submit_market_exit raises after the stop is already canceled,
    notifier.send() must be called exactly once with the symbol in the subject."""
    from alpaca_bot.runtime.cycle_intent_execution import execute_cycle_intents

    settings = make_settings()
    now = datetime(2026, 5, 2, 19, 0, tzinfo=timezone.utc)

    active_stop = OrderRecord(
        client_order_id="v1-breakout:2026-05-02:AAPL:stop:1",
        symbol="AAPL",
        side="sell",
        intent_type="stop",
        status="accepted",
        quantity=25,
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        created_at=now,
        updated_at=now,
        stop_price=109.89,
        initial_stop_price=109.89,
        broker_order_id="broker-stop-notify",
        signal_timestamp=now,
    )
    position = PositionRecord(
        symbol="AAPL",
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        quantity=25,
        entry_price=111.02,
        stop_price=109.89,
        initial_stop_price=109.89,
        opened_at=now,
        updated_at=now,
    )

    class ExitRaisesBroker(RecordingBroker):
        def submit_market_exit(self, **kwargs):
            raise RuntimeError("broker_connection_error")

    notifier_calls: list[dict] = []

    class RecordingNotifier:
        def send(self, *, subject: str, body: str) -> None:
            notifier_calls.append({"subject": subject, "body": body})

    runtime = SimpleNamespace(
        order_store=RecordingOrderStore(orders=[active_stop]),
        position_store=RecordingPositionStore(positions=[position]),
        audit_event_store=RecordingAuditEventStore(),
        connection=FakeConnection(),
    )

    report = execute_cycle_intents(
        settings=settings,
        runtime=runtime,
        broker=ExitRaisesBroker(),
        cycle_result=CycleResult(
            as_of=now,
            intents=[CycleIntent(
                intent_type=CycleIntentType.EXIT,
                symbol="AAPL",
                timestamp=now,
                reason="eod_flatten",
            )],
        ),
        now=now,
        notifier=RecordingNotifier(),
    )

    assert report.failed_exit_count == 1
    assert len(notifier_calls) == 1, "notifier.send must be called exactly once on exit submission failure"
    assert "AAPL" in notifier_calls[0]["subject"]
    assert "HARD FAILED" in notifier_calls[0]["subject"]


def test_stop_cancel_failure_fires_notifier() -> None:
    """When cancel_order raises an unrecognized error (cancel_hard_failed path),
    notifier.send() must be called exactly once with the symbol in the subject."""
    from alpaca_bot.runtime.cycle_intent_execution import execute_cycle_intents

    settings = make_settings()
    now = datetime(2026, 5, 2, 19, 5, tzinfo=timezone.utc)

    active_stop = OrderRecord(
        client_order_id="v1-breakout:2026-05-02:AAPL:stop:cancel-fail",
        symbol="AAPL",
        side="sell",
        intent_type="stop",
        status="accepted",
        quantity=25,
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        created_at=now,
        updated_at=now,
        stop_price=109.89,
        initial_stop_price=109.89,
        broker_order_id="broker-stop-cancel-fail",
        signal_timestamp=now,
    )
    position = PositionRecord(
        symbol="AAPL",
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        quantity=25,
        entry_price=111.02,
        stop_price=109.89,
        initial_stop_price=109.89,
        opened_at=now,
        updated_at=now,
    )

    notifier_calls: list[dict] = []

    class RecordingNotifier:
        def send(self, *, subject: str, body: str) -> None:
            notifier_calls.append({"subject": subject, "body": body})

    runtime = SimpleNamespace(
        order_store=RecordingOrderStore(orders=[active_stop]),
        position_store=RecordingPositionStore(positions=[position]),
        audit_event_store=RecordingAuditEventStore(),
        connection=FakeConnection(),
    )

    report = execute_cycle_intents(
        settings=settings,
        runtime=runtime,
        broker=RecordingBroker(cancel_raises=RuntimeError("rate_limit_exceeded_unknown")),
        cycle_result=CycleResult(
            as_of=now,
            intents=[CycleIntent(
                intent_type=CycleIntentType.EXIT,
                symbol="AAPL",
                timestamp=now,
                reason="eod_flatten",
            )],
        ),
        now=now,
        notifier=RecordingNotifier(),
    )

    assert report.failed_exit_count == 1
    assert len(notifier_calls) == 1, "notifier.send must be called exactly once on cancel hard-failure"
    assert "AAPL" in notifier_calls[0]["subject"]
    assert "HARD FAILED" in notifier_calls[0]["subject"]


def test_exit_failure_none_notifier_does_not_raise() -> None:
    """When notifier=None (the default), a hard-failed exit must not raise."""
    from alpaca_bot.runtime.cycle_intent_execution import execute_cycle_intents

    settings = make_settings()
    now = datetime(2026, 5, 2, 19, 10, tzinfo=timezone.utc)

    active_stop = OrderRecord(
        client_order_id="v1-breakout:2026-05-02:AAPL:stop:no-notifier",
        symbol="AAPL",
        side="sell",
        intent_type="stop",
        status="accepted",
        quantity=25,
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        created_at=now,
        updated_at=now,
        stop_price=109.89,
        initial_stop_price=109.89,
        broker_order_id="broker-stop-no-notifier",
        signal_timestamp=now,
    )
    position = PositionRecord(
        symbol="AAPL",
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        quantity=25,
        entry_price=111.02,
        stop_price=109.89,
        initial_stop_price=109.89,
        opened_at=now,
        updated_at=now,
    )

    class ExitRaisesBroker(RecordingBroker):
        def submit_market_exit(self, **kwargs):
            raise RuntimeError("timeout")

    runtime = SimpleNamespace(
        order_store=RecordingOrderStore(orders=[active_stop]),
        position_store=RecordingPositionStore(positions=[position]),
        audit_event_store=RecordingAuditEventStore(),
        connection=FakeConnection(),
    )

    report = execute_cycle_intents(
        settings=settings,
        runtime=runtime,
        broker=ExitRaisesBroker(),
        cycle_result=CycleResult(
            as_of=now,
            intents=[CycleIntent(
                intent_type=CycleIntentType.EXIT,
                symbol="AAPL",
                timestamp=now,
                reason="eod_flatten",
            )],
        ),
        now=now,
        # notifier omitted — tests the default None path
    )

    assert report.failed_exit_count == 1


def test_execute_exit_cancels_partial_fill_entry_before_market_exit() -> None:
    """_execute_exit cancels an open partial-fill entry before submitting market exit."""
    execute_cycle_intents = load_cycle_intent_execution_api()
    settings = make_settings()
    now = datetime(2026, 5, 4, 15, 30, tzinfo=timezone.utc)

    partial_entry = OrderRecord(
        client_order_id="paper:v1-breakout:SONO:entry:1",
        symbol="SONO",
        side="buy",
        intent_type="entry",
        status="partially_filled",
        quantity=187,
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        created_at=now,
        updated_at=now,
        stop_price=14.88,
        limit_price=14.90,
        broker_order_id="broker-entry-sono-1",
        signal_timestamp=now,
    )
    position = PositionRecord(
        symbol="SONO",
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        quantity=187,
        entry_price=14.88,
        stop_price=14.00,
        initial_stop_price=14.00,
        opened_at=now,
    )
    order_store = RecordingOrderStore(orders=[partial_entry])
    position_store = RecordingPositionStore(positions=[position])
    audit_store = RecordingAuditEventStore()
    conn = FakeConnection()
    runtime = SimpleNamespace(
        order_store=order_store,
        position_store=position_store,
        audit_event_store=audit_store,
        connection=conn,
    )
    broker = RecordingBroker()

    execute_cycle_intents(
        settings=settings,
        runtime=runtime,
        broker=broker,
        cycle_result=CycleResult(
            as_of=now,
            intents=[CycleIntent(
                intent_type=CycleIntentType.EXIT,
                symbol="SONO",
                timestamp=now,
                strategy_name="breakout",
            )],
        ),
        now=now,
    )

    # Partial-fill entry was canceled at broker before market exit was submitted.
    assert "broker-entry-sono-1" in broker.cancel_calls
    assert len(broker.exit_calls) == 1
    assert broker.exit_calls[0]["symbol"] == "SONO"

    # Audit trail includes partial_fill_entry_canceled
    event_types = [e.event_type for e in audit_store.appended]
    assert "partial_fill_entry_canceled" in event_types


def test_execute_update_stop_cancels_partial_fill_entry_before_submitting_new_stop() -> None:
    """_execute_update_stop (else branch: no active stop) must cancel any partially-filled
    entry before submitting a new stop to prevent Alpaca error 40310000."""
    execute_cycle_intents = load_cycle_intent_execution_api()
    settings = make_settings()
    now = datetime(2026, 5, 5, 14, 0, tzinfo=timezone.utc)

    partial_entry = OrderRecord(
        client_order_id="paper:v1-breakout:QQQ:entry:1",
        symbol="QQQ",
        side="buy",
        intent_type="entry",
        status="partially_filled",
        quantity=4,
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        created_at=now,
        updated_at=now,
        stop_price=490.00,
        limit_price=495.00,
        broker_order_id="broker-entry-qqq-1",
        signal_timestamp=now,
        strategy_name="breakout",
    )
    position = PositionRecord(
        symbol="QQQ",
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        quantity=4,
        entry_price=495.00,
        stop_price=490.00,
        initial_stop_price=490.00,
        opened_at=now,
    )
    audit_store = RecordingAuditEventStore()
    runtime = SimpleNamespace(
        order_store=RecordingOrderStore(orders=[partial_entry]),
        position_store=RecordingPositionStore(positions=[position]),
        audit_event_store=audit_store,
        connection=FakeConnection(),
    )
    broker = RecordingBroker()

    execute_cycle_intents(
        settings=settings,
        runtime=runtime,
        broker=broker,
        cycle_result=CycleResult(
            as_of=now,
            intents=[CycleIntent(
                intent_type=CycleIntentType.UPDATE_STOP,
                symbol="QQQ",
                timestamp=now,
                stop_price=496.00,  # higher than position.stop_price=490.00 → triggers update
            )],
        ),
        now=now,
    )

    # Partial-fill entry was canceled before stop was submitted.
    assert "broker-entry-qqq-1" in broker.cancel_calls
    assert len(broker.stop_calls) == 1
    assert broker.stop_calls[0]["symbol"] == "QQQ"

    # Audit trail: partial_fill_entry_canceled with context="update_stop"
    event_types = [e.event_type for e in audit_store.appended]
    assert "partial_fill_entry_canceled" in event_types
    canceled_event = next(
        e for e in audit_store.appended if e.event_type == "partial_fill_entry_canceled"
    )
    assert canceled_event.payload["context"] == "update_stop"


# ---------------------------------------------------------------------------
# Regression: replace_order must NOT pass client_order_id
# ---------------------------------------------------------------------------

def test_replace_stop_does_not_pass_client_order_id() -> None:
    """replace_order must never include client_order_id in kwargs.

    Passing the old ID causes Alpaca to reject with 'client_order_id must be
    unique' because the old order (now 'replaced' status) still holds that ID.
    Omitting it tells Alpaca to transfer the original ID automatically.
    """
    execute_cycle_intents = load_cycle_intent_execution_api()
    settings = make_settings()
    now = datetime(2026, 4, 24, 19, 30, tzinfo=timezone.utc)
    active_stop = OrderRecord(
        client_order_id="v1-breakout:2026-04-24:AAPL:stop:2026-04-24T19:00:00+00:00",
        symbol="AAPL",
        side="sell",
        intent_type="stop",
        status="accepted",
        quantity=25,
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        created_at=now,
        updated_at=now,
        stop_price=109.89,
        initial_stop_price=109.89,
        broker_order_id="broker-stop-1",
        signal_timestamp=now,
    )
    position = PositionRecord(
        symbol="AAPL",
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        quantity=25,
        entry_price=111.02,
        stop_price=109.89,
        initial_stop_price=109.89,
        opened_at=now,
        updated_at=now,
    )
    runtime = SimpleNamespace(
        order_store=RecordingOrderStore(orders=[active_stop]),
        position_store=RecordingPositionStore(positions=[position]),
        audit_event_store=RecordingAuditEventStore(),
        connection=FakeConnection(),
    )
    broker = RecordingBroker()

    execute_cycle_intents(
        settings=settings,
        runtime=runtime,
        broker=broker,
        cycle_result=CycleResult(
            as_of=now,
            intents=[CycleIntent(
                intent_type=CycleIntentType.UPDATE_STOP,
                symbol="AAPL",
                timestamp=now,
                stop_price=111.7,
            )],
        ),
        now=now,
    )

    assert len(broker.replace_calls) == 1
    call = broker.replace_calls[0]
    assert call["order_id"] == "broker-stop-1"
    assert call["stop_price"] == 111.7
    assert "client_order_id" not in call, (
        "replace_order must NOT pass client_order_id — "
        "Alpaca transfers the original ID automatically; "
        "passing the old ID triggers 'client_order_id must be unique'"
    )


# ---------------------------------------------------------------------------
# Regression: "insufficient qty" must be silently skipped
# ---------------------------------------------------------------------------

def test_replace_stop_insufficient_qty_silently_skipped() -> None:
    """'insufficient qty available' means a working stop already holds the full
    position qty at the broker. Position is protected. Must NOT write
    stop_update_failed or raise — skip at debug level only.

    Production scenario: CLOV had 'insufficient qty available for order
    (requested: 101, available: 0, held_for_orders: 101)' every cycle for 4+ hours.
    """
    execute_cycle_intents = load_cycle_intent_execution_api()
    settings = make_settings()
    now = datetime(2026, 4, 24, 19, 30, tzinfo=timezone.utc)

    active_stop = OrderRecord(
        client_order_id="v1-breakout:2026-04-24:CLOV:stop:2026-04-24T14:30:00+00:00",
        symbol="CLOV",
        side="sell",
        intent_type="stop",
        status="accepted",
        quantity=101,
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        created_at=now,
        updated_at=now,
        stop_price=2.20,
        initial_stop_price=2.20,
        broker_order_id="broker-stop-clov",
        signal_timestamp=now,
    )
    position = PositionRecord(
        symbol="CLOV",
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        quantity=101,
        entry_price=2.63,
        stop_price=2.20,
        initial_stop_price=2.20,
        opened_at=now,
        updated_at=now,
    )

    class InsufficientQtyBroker(RecordingBroker):
        def replace_order(self, **kwargs):
            raise RuntimeError(
                "insufficient qty available for order "
                "(requested: 101, available: 0, held_for_orders: 101)"
            )

    audit_store = RecordingAuditEventStore()
    runtime = SimpleNamespace(
        order_store=RecordingOrderStore(orders=[active_stop]),
        position_store=RecordingPositionStore(positions=[position]),
        audit_event_store=audit_store,
        connection=FakeConnection(),
    )

    report = execute_cycle_intents(
        settings=settings,
        runtime=runtime,
        broker=InsufficientQtyBroker(),
        cycle_result=CycleResult(
            as_of=now,
            intents=[CycleIntent(
                intent_type=CycleIntentType.UPDATE_STOP,
                symbol="CLOV",
                timestamp=now,
                stop_price=2.50,
            )],
        ),
        now=now,
    )

    stop_failed_events = [
        e for e in audit_store.appended if e.event_type == "stop_update_failed"
    ]
    assert stop_failed_events == [], (
        "insufficient qty means a working stop already holds the full qty; "
        "position is protected; must not write stop_update_failed"
    )
    assert report.replaced_stop_count == 0


# ---------------------------------------------------------------------------
# Self-healing: 40010001 duplicate client_order_id resync
# ---------------------------------------------------------------------------

def test_path_c_duplicate_client_order_id_resyncs_stop_and_emits_stop_order_resynced() -> None:
    """When submit_stop_order raises 40010001 (client_order_id must be unique),
    the handler must: fetch broker orders for symbol, find matching stop, UPSERT to DB,
    replace_order() to correct price, emit stop_order_resynced, NOT emit stop_update_failed."""
    execute_cycle_intents = load_cycle_intent_execution_api()
    settings = make_settings()
    now = datetime(2026, 5, 6, 19, 30, tzinfo=timezone.utc)
    bar_ts = datetime(2026, 5, 6, 19, 15, tzinfo=timezone.utc)

    position = PositionRecord(
        symbol="ACHR",
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        strategy_name="breakout",
        quantity=100,
        entry_price=6.00,
        stop_price=5.50,
        initial_stop_price=5.50,
        opened_at=now,
        updated_at=now,
    )
    order_store = RecordingOrderStore(orders=[])
    audit_store = RecordingAuditEventStore()

    conflicting_client_order_id = (
        f"v1-breakout:breakout:{now.date().isoformat()}:ACHR:stop:{bar_ts.isoformat()}"
    )
    conflicting_broker_order = SimpleNamespace(
        client_order_id=conflicting_client_order_id,
        broker_order_id="broker-conflicting-stop-1",
        symbol="ACHR",
        side="sell",
        status="new",
        quantity=100,
    )

    replace_calls: list[dict] = []
    open_orders_for_symbol_calls: list[str] = []

    class ResyncBroker:
        def submit_stop_order(self, **kwargs):
            raise Exception("client_order_id must be unique")

        def get_open_orders_for_symbol(self, symbol: str):
            open_orders_for_symbol_calls.append(symbol)
            return [conflicting_broker_order]

        def replace_order(self, **kwargs):
            replace_calls.append(dict(kwargs))
            return SimpleNamespace(
                client_order_id=conflicting_client_order_id,
                broker_order_id="broker-conflicting-stop-1",
                symbol="ACHR",
                side="sell",
                status="accepted",
                quantity=100,
            )

        def cancel_order(self, order_id: str) -> None:
            pass

        def submit_market_exit(self, **kwargs):
            pass

        def submit_limit_exit(self, **kwargs):
            pass

    runtime = SimpleNamespace(
        order_store=order_store,
        position_store=RecordingPositionStore(positions=[position]),
        audit_event_store=audit_store,
        connection=FakeConnection(),
    )

    execute_cycle_intents(
        settings=settings,
        runtime=runtime,
        broker=ResyncBroker(),
        cycle_result=CycleResult(
            as_of=now,
            intents=[CycleIntent(
                intent_type=CycleIntentType.UPDATE_STOP,
                symbol="ACHR",
                timestamp=bar_ts,
                stop_price=5.75,
                strategy_name="breakout",
            )],
        ),
        now=now,
    )

    assert open_orders_for_symbol_calls == ["ACHR"], (
        "get_open_orders_for_symbol must be called with the symbol on 40010001"
    )
    assert len(replace_calls) == 1, "replace_order must be called once with found broker_order_id"
    assert replace_calls[0]["order_id"] == "broker-conflicting-stop-1"
    assert replace_calls[0]["stop_price"] == pytest.approx(5.75)

    event_types = [e.event_type for e in audit_store.appended]
    assert "stop_order_resynced" in event_types, "stop_order_resynced audit event must be emitted"
    assert "stop_update_failed" not in event_types, "stop_update_failed must NOT be emitted on successful resync"

    saved_stops = [o for o in order_store.saved if o.intent_type == "stop"]
    assert any(o.stop_price == pytest.approx(5.75) for o in saved_stops), (
        "DB order record must reflect the new stop_price after resync"
    )


def test_path_c_duplicate_client_order_id_falls_back_to_stop_update_failed_when_order_not_found() -> None:
    """When submit_stop_order raises 40010001 but get_open_orders_for_symbol returns no
    matching order, the handler must fall back to stop_update_failed."""
    execute_cycle_intents = load_cycle_intent_execution_api()
    settings = make_settings()
    now = datetime(2026, 5, 6, 19, 30, tzinfo=timezone.utc)
    bar_ts = datetime(2026, 5, 6, 19, 15, tzinfo=timezone.utc)

    position = PositionRecord(
        symbol="ACHR",
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        strategy_name="breakout",
        quantity=100,
        entry_price=6.00,
        stop_price=5.50,
        initial_stop_price=5.50,
        opened_at=now,
        updated_at=now,
    )
    order_store = RecordingOrderStore(orders=[])
    audit_store = RecordingAuditEventStore()

    class NotFoundResyncBroker:
        def submit_stop_order(self, **kwargs):
            raise Exception("client_order_id must be unique")

        def get_open_orders_for_symbol(self, symbol: str):
            return []

        def replace_order(self, **kwargs):
            pass

        def cancel_order(self, order_id: str) -> None:
            pass

        def submit_market_exit(self, **kwargs):
            pass

        def submit_limit_exit(self, **kwargs):
            pass

    runtime = SimpleNamespace(
        order_store=order_store,
        position_store=RecordingPositionStore(positions=[position]),
        audit_event_store=audit_store,
        connection=FakeConnection(),
    )

    execute_cycle_intents(
        settings=settings,
        runtime=runtime,
        broker=NotFoundResyncBroker(),
        cycle_result=CycleResult(
            as_of=now,
            intents=[CycleIntent(
                intent_type=CycleIntentType.UPDATE_STOP,
                symbol="ACHR",
                timestamp=bar_ts,
                stop_price=5.75,
                strategy_name="breakout",
            )],
        ),
        now=now,
    )

    event_types = [e.event_type for e in audit_store.appended]
    assert "stop_update_failed" in event_types, (
        "stop_update_failed must be emitted when resync cannot find the conflicting order"
    )


# ---------------------------------------------------------------------------
# Self-healing: 40310000 insufficient qty — stop update path
# ---------------------------------------------------------------------------

def test_path_c_insufficient_qty_cancels_blocking_orders_and_emits_blocking_stop_canceled() -> None:
    """When submit_stop_order raises 40310000 (insufficient qty available), the handler must
    parse related_orders, cancel each blocking order, update DB status, and emit
    blocking_stop_canceled — NOT stop_update_failed, NOT retry submit_stop_order."""
    import json as _json
    execute_cycle_intents = load_cycle_intent_execution_api()
    settings = make_settings()
    now = datetime(2026, 5, 6, 19, 30, tzinfo=timezone.utc)
    bar_ts = datetime(2026, 5, 6, 19, 15, tzinfo=timezone.utc)

    position = PositionRecord(
        symbol="CLOV",
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        strategy_name="breakout",
        quantity=101,
        entry_price=2.63,
        stop_price=2.40,
        initial_stop_price=2.40,
        opened_at=now,
        updated_at=now,
    )
    order_store = RecordingOrderStore(orders=[])
    audit_store = RecordingAuditEventStore()

    cancel_calls: list[str] = []
    submit_stop_calls: list[dict] = []

    error_body = _json.dumps({
        "code": 40310000,
        "message": "insufficient qty available for order",
        "related_orders": ["broker-phantom-stop-99"],
    })

    class InsufficientQtyBroker:
        def submit_stop_order(self, **kwargs):
            submit_stop_calls.append(dict(kwargs))
            raise Exception(error_body)

        def get_open_orders_for_symbol(self, symbol: str):
            return []

        def cancel_order(self, order_id: str) -> None:
            cancel_calls.append(order_id)

        def replace_order(self, **kwargs):
            pass

        def submit_market_exit(self, **kwargs):
            pass

        def submit_limit_exit(self, **kwargs):
            pass

    runtime = SimpleNamespace(
        order_store=order_store,
        position_store=RecordingPositionStore(positions=[position]),
        audit_event_store=audit_store,
        connection=FakeConnection(),
    )

    execute_cycle_intents(
        settings=settings,
        runtime=runtime,
        broker=InsufficientQtyBroker(),
        cycle_result=CycleResult(
            as_of=now,
            intents=[CycleIntent(
                intent_type=CycleIntentType.UPDATE_STOP,
                symbol="CLOV",
                timestamp=bar_ts,
                stop_price=2.50,
                strategy_name="breakout",
            )],
        ),
        now=now,
    )

    assert len(submit_stop_calls) == 1, "submit_stop_order must be called exactly once (no retry)"
    assert cancel_calls == ["broker-phantom-stop-99"], (
        "cancel_order must be called for each blocking order in related_orders"
    )

    event_types = [e.event_type for e in audit_store.appended]
    assert "blocking_stop_canceled" in event_types, "blocking_stop_canceled audit event must be emitted"
    assert "stop_update_failed" not in event_types, "stop_update_failed must NOT be emitted for 40310000"


# ---------------------------------------------------------------------------
# Self-healing: 40310000 insufficient qty — exit path
# ---------------------------------------------------------------------------

def test_exit_insufficient_qty_cancels_blocking_orders_retries_and_succeeds() -> None:
    """When submit_market_exit raises 40310000, the handler must cancel blocking orders,
    emit blocking_stop_canceled_for_exit, retry once, and succeed without exit_hard_failed."""
    import json as _json
    execute_cycle_intents = load_cycle_intent_execution_api()
    settings = make_settings()
    now = datetime(2026, 5, 6, 19, 50, tzinfo=timezone.utc)

    position = PositionRecord(
        symbol="CLOV",
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        strategy_name="breakout",
        quantity=101,
        entry_price=2.63,
        stop_price=2.40,
        initial_stop_price=2.40,
        opened_at=now,
        updated_at=now,
    )
    order_store = RecordingOrderStore(orders=[])
    audit_store = RecordingAuditEventStore()

    cancel_calls: list[str] = []
    exit_attempt = [0]

    error_body = _json.dumps({
        "code": 40310000,
        "message": "insufficient qty available for order",
        "related_orders": ["broker-phantom-stop-77"],
    })

    class BlockedExitBroker:
        def submit_market_exit(self, **kwargs):
            exit_attempt[0] += 1
            if exit_attempt[0] == 1:
                raise Exception(error_body)
            return SimpleNamespace(
                client_order_id=kwargs["client_order_id"],
                broker_order_id="broker-exit-ok",
                symbol=kwargs["symbol"],
                side="sell",
                status="accepted",
                quantity=kwargs["quantity"],
            )

        def cancel_order(self, order_id: str) -> None:
            cancel_calls.append(order_id)

        def replace_order(self, **kwargs):
            pass

        def submit_stop_order(self, **kwargs):
            pass

        def submit_limit_exit(self, **kwargs):
            pass

        def get_open_orders_for_symbol(self, symbol: str):
            return []

    runtime = SimpleNamespace(
        order_store=order_store,
        position_store=RecordingPositionStore(positions=[position]),
        audit_event_store=audit_store,
        connection=FakeConnection(),
    )

    report = execute_cycle_intents(
        settings=settings,
        runtime=runtime,
        broker=BlockedExitBroker(),
        cycle_result=CycleResult(
            as_of=now,
            intents=[CycleIntent(
                intent_type=CycleIntentType.EXIT,
                symbol="CLOV",
                timestamp=now,
                reason="eod_flatten",
                strategy_name="breakout",
            )],
        ),
        now=now,
    )

    assert exit_attempt[0] == 2, "submit_market_exit must be called twice (first fails, retry succeeds)"
    assert cancel_calls == ["broker-phantom-stop-77"], "cancel_order must be called for the blocking order"

    event_types = [e.event_type for e in audit_store.appended]
    assert "blocking_stop_canceled_for_exit" in event_types, (
        "blocking_stop_canceled_for_exit must be emitted before retry"
    )
    assert "exit_hard_failed" not in event_types, "exit_hard_failed must NOT be emitted when retry succeeds"
    assert report.submitted_exit_count == 1, "submitted_exit_count must be 1 after successful retry"


def test_exit_insufficient_qty_emits_exit_hard_failed_when_retry_also_fails() -> None:
    """When submit_market_exit raises 40310000 and the retry also fails,
    exit_hard_failed must be emitted and a recovery stop must be queued."""
    import json as _json
    execute_cycle_intents = load_cycle_intent_execution_api()
    settings = make_settings()
    now = datetime(2026, 5, 6, 19, 50, tzinfo=timezone.utc)

    position = PositionRecord(
        symbol="CLOV",
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        strategy_name="breakout",
        quantity=101,
        entry_price=2.63,
        stop_price=2.40,
        initial_stop_price=2.40,
        opened_at=now,
        updated_at=now,
    )
    order_store = RecordingOrderStore(orders=[])
    audit_store = RecordingAuditEventStore()

    error_body = _json.dumps({
        "code": 40310000,
        "message": "insufficient qty available for order",
        "related_orders": ["broker-phantom-stop-88"],
    })

    class DoubleFailExitBroker:
        def submit_market_exit(self, **kwargs):
            raise Exception(error_body)

        def cancel_order(self, order_id: str) -> None:
            pass

        def replace_order(self, **kwargs):
            pass

        def submit_stop_order(self, **kwargs):
            pass

        def submit_limit_exit(self, **kwargs):
            pass

        def get_open_orders_for_symbol(self, symbol: str):
            return []

    runtime = SimpleNamespace(
        order_store=order_store,
        position_store=RecordingPositionStore(positions=[position]),
        audit_event_store=audit_store,
        connection=FakeConnection(),
    )

    report = execute_cycle_intents(
        settings=settings,
        runtime=runtime,
        broker=DoubleFailExitBroker(),
        cycle_result=CycleResult(
            as_of=now,
            intents=[CycleIntent(
                intent_type=CycleIntentType.EXIT,
                symbol="CLOV",
                timestamp=now,
                reason="eod_flatten",
                strategy_name="breakout",
            )],
        ),
        now=now,
    )

    event_types = [e.event_type for e in audit_store.appended]
    assert "blocking_stop_canceled_for_exit" in event_types, (
        "blocking_stop_canceled_for_exit must be emitted even when retry also fails"
    )
    assert "exit_hard_failed" in event_types, "exit_hard_failed must be emitted when retry also fails"
    assert report.submitted_exit_count == 0
    assert "recovery_stop_queued_after_exit_failure" in event_types, (
        "recovery_stop_queued_after_exit_failure must be emitted when retry fails "
        "and position has a stop_price — canceled_stop_count==0 must not suppress it"
    )


def test_path_c_get_open_orders_for_symbol_raises_falls_back_to_stop_update_failed() -> None:
    """When submit_stop_order raises 40010001 but get_open_orders_for_symbol also raises,
    the handler must fall back to stop_update_failed."""
    execute_cycle_intents = load_cycle_intent_execution_api()
    settings = make_settings()
    now = datetime(2026, 5, 6, 19, 30, tzinfo=timezone.utc)
    bar_ts = datetime(2026, 5, 6, 19, 15, tzinfo=timezone.utc)

    position = PositionRecord(
        symbol="ACHR",
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        strategy_name="breakout",
        quantity=100,
        entry_price=6.00,
        stop_price=5.50,
        initial_stop_price=5.50,
        opened_at=now,
        updated_at=now,
    )
    order_store = RecordingOrderStore(orders=[])
    audit_store = RecordingAuditEventStore()

    class BrokerFetchFails:
        def submit_stop_order(self, **kwargs):
            raise Exception("client_order_id must be unique")

        def get_open_orders_for_symbol(self, symbol: str):
            raise RuntimeError("broker unavailable")

        def cancel_order(self, order_id: str) -> None:
            pass

        def replace_order(self, **kwargs):
            pass

        def submit_market_exit(self, **kwargs):
            pass

        def submit_limit_exit(self, **kwargs):
            pass

    runtime = SimpleNamespace(
        order_store=order_store,
        position_store=RecordingPositionStore(positions=[position]),
        audit_event_store=audit_store,
        connection=FakeConnection(),
    )

    execute_cycle_intents(
        settings=settings,
        runtime=runtime,
        broker=BrokerFetchFails(),
        cycle_result=CycleResult(
            as_of=now,
            intents=[CycleIntent(
                intent_type=CycleIntentType.UPDATE_STOP,
                symbol="ACHR",
                timestamp=bar_ts,
                stop_price=5.75,
                strategy_name="breakout",
            )],
        ),
        now=now,
    )

    event_types = [e.event_type for e in audit_store.appended]
    assert "stop_update_failed" in event_types, (
        "stop_update_failed must be emitted when get_open_orders_for_symbol raises during resync"
    )
