from __future__ import annotations
from datetime import datetime, timezone
import pytest
from alpaca_bot.config import Settings
from alpaca_bot.core.engine import CycleIntent, CycleIntentType, CycleResult
from alpaca_bot.execution.alpaca import BrokerOrder
from alpaca_bot.runtime.cycle_intent_execution import execute_cycle_intents
from alpaca_bot.storage import AuditEvent, OrderRecord, PositionRecord


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


def _make_position() -> PositionRecord:
    now = datetime(2026, 4, 28, 21, 0, tzinfo=timezone.utc)
    return PositionRecord(
        symbol="AAPL",
        trading_mode="paper",
        strategy_version="v1",
        strategy_name="breakout",
        quantity=10,
        entry_price=100.0,
        stop_price=95.0,
        initial_stop_price=94.0,
        opened_at=now,
        updated_at=now,
    )


def _fake_runtime(position: PositionRecord):
    saved_orders = []
    audits = []

    class FakeOrderStore:
        def save(self, order, *, commit=True): saved_orders.append(order)
        def list_by_status(self, **kwargs): return []

    class FakePositionStore:
        def save(self, pos, *, commit=True): pass
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

    return FakeRuntime(), saved_orders, audits


def test_exit_with_limit_price_calls_submit_limit_exit():
    settings = _settings()
    position = _make_position()
    runtime, saved_orders, _ = _fake_runtime(position)

    limit_exit_calls = []
    market_exit_calls = []

    class FakeBroker:
        def submit_limit_exit(self, **kwargs):
            limit_exit_calls.append(kwargs)
            return BrokerOrder(
                client_order_id=kwargs["client_order_id"],
                broker_order_id="brk1",
                symbol=kwargs["symbol"],
                side="sell",
                status="new",
                quantity=kwargs["quantity"],
            )
        def submit_market_exit(self, **kwargs):
            market_exit_calls.append(kwargs)
            return BrokerOrder(
                client_order_id=kwargs["client_order_id"],
                broker_order_id="brk2",
                symbol=kwargs["symbol"],
                side="sell",
                status="new",
                quantity=kwargs["quantity"],
            )
        def cancel_order(self, order_id): pass
        def replace_order(self, **kwargs): pass
        def submit_stop_order(self, **kwargs):
            return BrokerOrder("x", "y", "AAPL", "sell", "new", 10)

    now = datetime(2026, 4, 28, 21, 0, tzinfo=timezone.utc)
    intent = CycleIntent(
        intent_type=CycleIntentType.EXIT,
        symbol="AAPL",
        timestamp=now,
        reason="eod_flatten",
        limit_price=104.895,  # 105 * (1 - 0.001)
        strategy_name="breakout",
    )
    cycle_result = CycleResult(as_of=now, intents=[intent])

    execute_cycle_intents(
        settings=settings,
        runtime=runtime,
        broker=FakeBroker(),
        cycle_result=cycle_result,
        now=now,
    )

    assert len(limit_exit_calls) == 1
    assert len(market_exit_calls) == 0
    assert limit_exit_calls[0]["limit_price"] == pytest.approx(104.895)


def test_exit_without_limit_price_calls_submit_market_exit():
    settings = _settings()
    position = _make_position()
    runtime, saved_orders, _ = _fake_runtime(position)

    limit_exit_calls = []
    market_exit_calls = []

    class FakeBroker:
        def submit_limit_exit(self, **kwargs):
            limit_exit_calls.append(kwargs)
            return BrokerOrder(kwargs["client_order_id"], "brk1", kwargs["symbol"], "sell", "new", kwargs["quantity"])
        def submit_market_exit(self, **kwargs):
            market_exit_calls.append(kwargs)
            return BrokerOrder(kwargs["client_order_id"], "brk2", kwargs["symbol"], "sell", "new", kwargs["quantity"])
        def cancel_order(self, order_id): pass
        def replace_order(self, **kwargs): pass
        def submit_stop_order(self, **kwargs):
            return BrokerOrder("x", "y", "AAPL", "sell", "new", 10)

    now = datetime(2026, 4, 28, 19, 50, tzinfo=timezone.utc)
    intent = CycleIntent(
        intent_type=CycleIntentType.EXIT,
        symbol="AAPL",
        timestamp=now,
        reason="eod_flatten",
        limit_price=None,
        strategy_name="breakout",
    )
    cycle_result = CycleResult(as_of=now, intents=[intent])

    execute_cycle_intents(
        settings=settings,
        runtime=runtime,
        broker=FakeBroker(),
        cycle_result=cycle_result,
        now=now,
    )

    assert len(market_exit_calls) == 1
    assert len(limit_exit_calls) == 0
