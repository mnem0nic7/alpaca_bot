from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

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
    def commit(self) -> None:
        pass


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
            client_order_id=kwargs["client_order_id"],
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
            "client_order_id": active_stop.client_order_id,
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
