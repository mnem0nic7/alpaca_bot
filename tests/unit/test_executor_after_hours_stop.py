from __future__ import annotations

from datetime import datetime, timezone

import pytest

from alpaca_bot.config import Settings
from alpaca_bot.core.engine import CycleIntent, CycleIntentType, CycleResult
from alpaca_bot.runtime.cycle_intent_execution import execute_cycle_intents
from alpaca_bot.storage import AuditEvent, OrderRecord, PositionRecord
from alpaca_bot.strategy.session import SessionType

_NOW = datetime(2026, 5, 9, 21, 0, tzinfo=timezone.utc)  # 5pm ET = after hours
_NOW_REGULAR = datetime(2026, 5, 9, 16, 0, tzinfo=timezone.utc)  # noon ET = regular session


def _settings() -> Settings:
    return Settings.from_env({
        "TRADING_MODE": "paper",
        "ENABLE_LIVE_TRADING": "false",
        "STRATEGY_VERSION": "v1",
        "DATABASE_URL": "postgresql://test/db",
        "MARKET_DATA_FEED": "iex",
        "SYMBOLS": "AAPL",
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
        "EXTENDED_HOURS_ENABLED": "true",
    })


def _make_position(*, stop_price: float = 95.0) -> PositionRecord:
    return PositionRecord(
        symbol="AAPL",
        trading_mode="paper",
        strategy_version="v1",
        strategy_name="breakout",
        quantity=10,
        entry_price=100.0,
        stop_price=stop_price,
        initial_stop_price=94.0,
        opened_at=_NOW,
        updated_at=_NOW,
    )


def _make_pending_stop(*, stop_price: float = 95.0) -> OrderRecord:
    return OrderRecord(
        client_order_id="aapl-stop-1",
        symbol="AAPL",
        side="sell",
        intent_type="stop",
        status="pending_submit",
        quantity=10,
        trading_mode="paper",
        strategy_version="v1",
        strategy_name="breakout",
        created_at=_NOW,
        updated_at=_NOW,
        stop_price=stop_price,
        initial_stop_price=94.0,
        broker_order_id=None,
        signal_timestamp=_NOW,
    )


def _make_broker_stop(*, stop_price: float = 95.0) -> OrderRecord:
    return OrderRecord(
        client_order_id="aapl-stop-1",
        symbol="AAPL",
        side="sell",
        intent_type="stop",
        status="new",
        quantity=10,
        trading_mode="paper",
        strategy_version="v1",
        strategy_name="breakout",
        created_at=_NOW,
        updated_at=_NOW,
        stop_price=stop_price,
        initial_stop_price=94.0,
        broker_order_id="brk-123",
        signal_timestamp=_NOW,
    )


def _fake_runtime(stop_order: OrderRecord | None, position: PositionRecord):
    saved_orders: list[OrderRecord] = []
    saved_positions: list[PositionRecord] = []
    audits: list[AuditEvent] = []

    class FakeOrderStore:
        def save(self, order, *, commit=True): saved_orders.append(order)
        def list_by_status(self, **kwargs): return [] if stop_order is None else [stop_order]

    class FakePositionStore:
        def save(self, pos, *, commit=True): saved_positions.append(pos)
        def list_all(self, **kwargs): return [position]

    class FakeAuditStore:
        def append(self, event, *, commit=True): audits.append(event)

    class FakeConn:
        def commit(self): pass
        def rollback(self): pass

    class FakeRuntime:
        order_store = FakeOrderStore()
        position_store = FakePositionStore()
        audit_event_store = FakeAuditStore()
        connection = FakeConn()
        store_lock = None

    return FakeRuntime(), saved_orders, saved_positions, audits


class _BrokerThatMustNotBeCalled:
    def replace_order(self, **kwargs):
        raise AssertionError("broker.replace_order must not be called during extended hours")

    def submit_stop_order(self, **kwargs):
        raise AssertionError("broker.submit_stop_order must not be called during extended hours")

    def cancel_order(self, order_id):
        raise AssertionError("broker.cancel_order must not be called during extended hours")

    def submit_limit_exit(self, **kwargs):
        raise AssertionError("broker.submit_limit_exit must not be called during extended hours")

    def submit_market_exit(self, **kwargs):
        raise AssertionError("broker.submit_market_exit must not be called during extended hours")


def _update_stop_intent(*, stop_price: float = 104.79, now: datetime = _NOW) -> CycleIntent:
    return CycleIntent(
        intent_type=CycleIntentType.UPDATE_STOP,
        symbol="AAPL",
        timestamp=now,
        stop_price=stop_price,
        strategy_name="breakout",
        reason="breakeven",
    )


_settings_obj = _settings()


def test_after_hours_pending_stop_updated_db_only_no_broker_call():
    """
    After hours + pending_submit stop: order and position updated in DB,
    broker is never called, updated_pending_stop_count == 1.
    """
    position = _make_position(stop_price=95.0)
    pending_stop = _make_pending_stop(stop_price=95.0)
    runtime, saved_orders, saved_positions, audits = _fake_runtime(pending_stop, position)
    cycle_result = CycleResult(as_of=_NOW, intents=[_update_stop_intent(stop_price=104.79)])

    report = execute_cycle_intents(
        settings=_settings_obj,
        runtime=runtime,
        broker=_BrokerThatMustNotBeCalled(),
        cycle_result=cycle_result,
        now=_NOW,
        session_type=SessionType.AFTER_HOURS,
    )

    assert report.updated_pending_stop_count == 1
    assert len(saved_orders) == 1
    assert saved_orders[0].stop_price == pytest.approx(104.79)
    assert saved_orders[0].status == "pending_submit"
    assert saved_orders[0].broker_order_id is None
    assert len(saved_positions) == 1
    assert saved_positions[0].stop_price == pytest.approx(104.79)
    assert len(audits) == 1
    assert audits[0].event_type == "cycle_intent_executed"


def test_after_hours_broker_stop_skipped_no_broker_call():
    """
    After hours + stop already at broker (broker_order_id set): no action.
    Alpaca rejects stop replacement after hours; we must not attempt it.
    """
    position = _make_position(stop_price=95.0)
    broker_stop = _make_broker_stop(stop_price=95.0)
    runtime, saved_orders, saved_positions, audits = _fake_runtime(broker_stop, position)
    cycle_result = CycleResult(as_of=_NOW, intents=[_update_stop_intent(stop_price=104.79)])

    report = execute_cycle_intents(
        settings=_settings_obj,
        runtime=runtime,
        broker=_BrokerThatMustNotBeCalled(),
        cycle_result=cycle_result,
        now=_NOW,
        session_type=SessionType.AFTER_HOURS,
    )

    assert report.updated_pending_stop_count == 0
    assert saved_orders == []


def test_after_hours_no_stop_skipped_no_broker_call():
    """
    After hours + no stop order at all: no action.
    No pending_submit record means dispatch hasn't happened yet; skip silently.
    """
    position = _make_position(stop_price=95.0)
    runtime, saved_orders, saved_positions, audits = _fake_runtime(None, position)
    cycle_result = CycleResult(as_of=_NOW, intents=[_update_stop_intent(stop_price=104.79)])

    report = execute_cycle_intents(
        settings=_settings_obj,
        runtime=runtime,
        broker=_BrokerThatMustNotBeCalled(),
        cycle_result=cycle_result,
        now=_NOW,
        session_type=SessionType.AFTER_HOURS,
    )

    assert report.updated_pending_stop_count == 0
    assert saved_orders == []


def test_regular_session_pending_stop_updated():
    """
    Regular session + pending_submit stop: updated_pending path runs as before.
    The db_only change must not regress the regular-hours execution path.
    """
    position = _make_position(stop_price=95.0)
    pending_stop = _make_pending_stop(stop_price=95.0)
    runtime, saved_orders, saved_positions, audits = _fake_runtime(pending_stop, position)
    cycle_result = CycleResult(
        as_of=_NOW_REGULAR,
        intents=[_update_stop_intent(stop_price=104.79, now=_NOW_REGULAR)],
    )

    report = execute_cycle_intents(
        settings=_settings_obj,
        runtime=runtime,
        broker=_BrokerThatMustNotBeCalled(),  # pending stop → no broker call in any session
        cycle_result=cycle_result,
        now=_NOW_REGULAR,
        session_type=SessionType.REGULAR,
    )

    assert report.updated_pending_stop_count == 1
    assert saved_orders[0].stop_price == pytest.approx(104.79)
    assert saved_positions[0].stop_price == pytest.approx(104.79)
