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

    def save(self, order: OrderRecord) -> None:
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

    def save(self, position: PositionRecord) -> None:
        self.saved.append(position)


class RecordingAuditEventStore:
    def __init__(self) -> None:
        self.appended: list[AuditEvent] = []

    def append(self, event: AuditEvent) -> None:
        self.appended.append(event)


class RecordingBroker:
    def __init__(self) -> None:
        self.replace_calls: list[dict[str, object]] = []
        self.stop_calls: list[dict[str, object]] = []
        self.cancel_calls: list[str] = []
        self.exit_calls: list[dict[str, object]] = []

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
            "client_order_id": "v1-breakout:2026-04-24:AAPL:stop:2026-04-24T19:35:00+00:00",
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
            "client_order_id": "v1-breakout:2026-04-24:AAPL:exit:2026-04-24T19:45:00+00:00",
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
            client_order_id="v1-breakout:2026-04-24:AAPL:exit:2026-04-24T19:45:00+00:00",
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
                "client_order_id": "v1-breakout:2026-04-24:AAPL:exit:2026-04-24T19:45:00+00:00",
            },
            created_at=now,
        )
    ]
    assert report.canceled_stop_count == 1
    assert report.submitted_exit_count == 1


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
        def save(self, position):
            # Record whether the lock is held when save() is called
            lock_held_during_save.append(not real_lock.acquire(blocking=False))
            if lock_held_during_save[-1]:
                pass  # lock was held — correct
            else:
                real_lock.release()  # we acquired it just to check; release it
            super().save(position)

    runtime = SimpleNamespace(
        order_store=RecordingOrderStore(orders=[active_stop]),
        position_store=LockWatchingPositionStore(positions=[position]),
        audit_event_store=RecordingAuditEventStore(),
        store_lock=real_lock,
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
