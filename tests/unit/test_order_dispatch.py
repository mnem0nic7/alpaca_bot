from __future__ import annotations

from datetime import datetime, timezone
from importlib import import_module
from types import SimpleNamespace

import pytest

from alpaca_bot.config import Settings, TradingMode
from alpaca_bot.storage import AuditEvent, OrderRecord


def load_order_dispatch_api():
    try:
        module = import_module("alpaca_bot.runtime.order_dispatch")
    except ModuleNotFoundError as exc:
        pytest.fail(f"Expected runtime dispatch module to exist: {exc}")
    return module, module.dispatch_pending_orders


def make_settings() -> Settings:
    return Settings.from_env(
        {
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
    )


class RecordingOrderStore:
    def __init__(self, pending_orders: list[OrderRecord]) -> None:
        self.pending_orders = list(pending_orders)
        self.find_pending_submit_calls: list[tuple[TradingMode, str]] = []
        self.saved: list[OrderRecord] = []

    def list_pending_submit(
        self, *, trading_mode: TradingMode, strategy_version: str
    ) -> list[OrderRecord]:
        self.find_pending_submit_calls.append((trading_mode, strategy_version))
        return list(self.pending_orders)

    def save(self, order: OrderRecord) -> None:
        self.saved.append(order)


class RecordingAuditEventStore:
    def __init__(self) -> None:
        self.appended: list[AuditEvent] = []

    def append(self, event: AuditEvent) -> None:
        self.appended.append(event)


class RecordingBroker:
    def __init__(self) -> None:
        self.entry_calls: list[dict[str, object]] = []
        self.stop_calls: list[dict[str, object]] = []

    def submit_stop_limit_entry(self, **kwargs: object) -> SimpleNamespace:
        self.entry_calls.append(dict(kwargs))
        return SimpleNamespace(
            client_order_id=kwargs["client_order_id"],
            broker_order_id="broker-entry-1",
            symbol=kwargs["symbol"],
            side="buy",
            status="ACCEPTED",
            quantity=kwargs["quantity"],
        )

    def submit_stop_order(self, **kwargs: object) -> SimpleNamespace:
        self.stop_calls.append(dict(kwargs))
        return SimpleNamespace(
            client_order_id=kwargs["client_order_id"],
            broker_order_id="broker-stop-1",
            symbol=kwargs["symbol"],
            side="sell",
            status="NEW",
            quantity=kwargs["quantity"],
        )


def test_dispatch_pending_orders_submits_entry_and_stop_orders_and_persists_updates() -> None:
    _, dispatch_pending_orders = load_order_dispatch_api()
    settings = make_settings()
    now = datetime(2026, 4, 24, 19, 30, tzinfo=timezone.utc)
    entry_order = OrderRecord(
        client_order_id="paper:v1-breakout:AAPL:entry:1",
        symbol="AAPL",
        side="buy",
        intent_type="entry",
        status="pending_submit",
        quantity=25,
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        created_at=now,
        updated_at=now,
        stop_price=101.25,
        limit_price=101.5,
        initial_stop_price=99.75,
        signal_timestamp=now,
    )
    stop_order = OrderRecord(
        client_order_id="paper:v1-breakout:AAPL:stop:1",
        symbol="AAPL",
        side="sell",
        intent_type="stop",
        status="pending_submit",
        quantity=25,
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        created_at=now,
        updated_at=now,
        stop_price=99.75,
        signal_timestamp=now,
    )
    order_store = RecordingOrderStore([entry_order, stop_order])
    audit_store = RecordingAuditEventStore()
    runtime = SimpleNamespace(order_store=order_store, audit_event_store=audit_store)
    broker = RecordingBroker()

    report = dispatch_pending_orders(settings=settings, runtime=runtime, broker=broker, now=now)

    assert order_store.find_pending_submit_calls == [(TradingMode.PAPER, "v1-breakout")]
    assert broker.entry_calls == [
        {
            "symbol": "AAPL",
            "quantity": 25,
            "stop_price": 101.25,
            "limit_price": 101.5,
            "client_order_id": "paper:v1-breakout:AAPL:entry:1",
        }
    ]
    assert broker.stop_calls == [
        {
            "symbol": "AAPL",
            "quantity": 25,
            "stop_price": 99.75,
            "client_order_id": "paper:v1-breakout:AAPL:stop:1",
        }
    ]
    assert order_store.saved == [
        OrderRecord(
            client_order_id="paper:v1-breakout:AAPL:entry:1",
            symbol="AAPL",
            side="buy",
            intent_type="entry",
            status="accepted",
            quantity=25,
            trading_mode=TradingMode.PAPER,
            strategy_version="v1-breakout",
            created_at=now,
            updated_at=now,
            stop_price=101.25,
            limit_price=101.5,
            initial_stop_price=99.75,
            broker_order_id="broker-entry-1",
            signal_timestamp=now,
        ),
        OrderRecord(
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
            stop_price=99.75,
            broker_order_id="broker-stop-1",
            signal_timestamp=now,
        ),
    ]
    assert audit_store.appended == [
        AuditEvent(
            event_type="order_submitted",
            symbol="AAPL",
            payload={
                "client_order_id": "paper:v1-breakout:AAPL:entry:1",
                "broker_order_id": "broker-entry-1",
                "intent_type": "entry",
                "status": "accepted",
            },
            created_at=now,
        ),
        AuditEvent(
            event_type="order_submitted",
            symbol="AAPL",
            payload={
                "client_order_id": "paper:v1-breakout:AAPL:stop:1",
                "broker_order_id": "broker-stop-1",
                "intent_type": "stop",
                "status": "new",
            },
            created_at=now,
        ),
    ]
    assert report["submitted_count"] == 2


def test_dispatch_pending_orders_returns_empty_report_when_no_pending_orders() -> None:
    _, dispatch_pending_orders = load_order_dispatch_api()
    settings = make_settings()
    now = datetime(2026, 4, 24, 19, 45, tzinfo=timezone.utc)
    order_store = RecordingOrderStore([])
    audit_store = RecordingAuditEventStore()
    runtime = SimpleNamespace(order_store=order_store, audit_event_store=audit_store)
    broker = RecordingBroker()

    report = dispatch_pending_orders(settings=settings, runtime=runtime, broker=broker, now=now)

    assert order_store.find_pending_submit_calls == [(TradingMode.PAPER, "v1-breakout")]
    assert broker.entry_calls == []
    assert broker.stop_calls == []
    assert order_store.saved == []
    assert audit_store.appended == []
    assert report["submitted_count"] == 0


def test_dispatch_pending_orders_filters_out_disallowed_intent_types() -> None:
    _, dispatch_pending_orders = load_order_dispatch_api()
    settings = make_settings()
    now = datetime(2026, 4, 24, 19, 50, tzinfo=timezone.utc)
    entry_order = OrderRecord(
        client_order_id="paper:v1-breakout:AAPL:entry:2",
        symbol="AAPL",
        side="buy",
        intent_type="entry",
        status="pending_submit",
        quantity=25,
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        created_at=now,
        updated_at=now,
        stop_price=101.25,
        limit_price=101.5,
        initial_stop_price=99.75,
        signal_timestamp=now,
    )
    stop_order = OrderRecord(
        client_order_id="paper:v1-breakout:AAPL:stop:2",
        symbol="AAPL",
        side="sell",
        intent_type="stop",
        status="pending_submit",
        quantity=25,
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        created_at=now,
        updated_at=now,
        stop_price=99.75,
        initial_stop_price=99.75,
        signal_timestamp=now,
    )
    order_store = RecordingOrderStore([entry_order, stop_order])
    audit_store = RecordingAuditEventStore()
    runtime = SimpleNamespace(order_store=order_store, audit_event_store=audit_store)
    broker = RecordingBroker()

    report = dispatch_pending_orders(
        settings=settings,
        runtime=runtime,
        broker=broker,
        now=now,
        allowed_intent_types={"stop"},
    )

    assert broker.entry_calls == []
    assert broker.stop_calls == [
        {
            "symbol": "AAPL",
            "quantity": 25,
            "stop_price": 99.75,
            "client_order_id": "paper:v1-breakout:AAPL:stop:2",
        }
    ]
    assert [saved.client_order_id for saved in order_store.saved] == [
        "paper:v1-breakout:AAPL:stop:2"
    ]
    assert [event.payload["intent_type"] for event in audit_store.appended] == ["stop"]
    assert report["submitted_count"] == 1


class FailingBroker:
    """Broker that always raises on submit_stop_limit_entry."""

    def submit_stop_limit_entry(self, **kwargs: object) -> None:
        raise RuntimeError("broker unavailable")

    def submit_stop_order(self, **kwargs: object) -> object:
        from types import SimpleNamespace
        return SimpleNamespace(
            client_order_id=kwargs["client_order_id"],
            broker_order_id="broker-stop-x",
            symbol=kwargs["symbol"],
            side="sell",
            status="NEW",
            quantity=kwargs["quantity"],
        )


def test_dispatch_records_error_status_on_broker_failure() -> None:
    _, dispatch_pending_orders = load_order_dispatch_api()
    settings = make_settings()
    now = datetime(2026, 4, 24, 19, 30, tzinfo=timezone.utc)
    entry_order = OrderRecord(
        client_order_id="paper:v1-breakout:AAPL:entry:fail",
        symbol="AAPL",
        side="buy",
        intent_type="entry",
        status="pending_submit",
        quantity=10,
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        created_at=now,
        updated_at=now,
        stop_price=101.0,
        limit_price=101.5,
        initial_stop_price=99.5,
        signal_timestamp=now,
    )
    order_store = RecordingOrderStore([entry_order])
    audit_store = RecordingAuditEventStore()
    runtime = SimpleNamespace(order_store=order_store, audit_event_store=audit_store)

    report = dispatch_pending_orders(
        settings=settings, runtime=runtime, broker=FailingBroker(), now=now
    )

    # Order must be saved with status="error"
    assert len(order_store.saved) == 1
    assert order_store.saved[0].status == "error"
    assert order_store.saved[0].client_order_id == entry_order.client_order_id
    # Audit event for the failure must be appended
    assert len(audit_store.appended) == 1
    assert audit_store.appended[0].event_type == "order_dispatch_failed"
    assert audit_store.appended[0].symbol == "AAPL"
    # No orders counted as submitted
    assert report["submitted_count"] == 0


def test_dispatch_skips_entry_orders_with_stale_signal_date() -> None:
    """Entry orders whose signal_timestamp is from a prior session date must be skipped."""
    _, dispatch_pending_orders = load_order_dispatch_api()
    settings = make_settings()
    # now is 14:30 ET on 2026-04-25 (18:30 UTC)
    now = datetime(2026, 4, 25, 18, 30, tzinfo=timezone.utc)
    # Signal was generated yesterday
    yesterday_signal = datetime(2026, 4, 24, 19, 0, tzinfo=timezone.utc)
    stale_entry = OrderRecord(
        client_order_id="paper:v1-breakout:AAPL:entry:stale",
        symbol="AAPL",
        side="buy",
        intent_type="entry",
        status="pending_submit",
        quantity=10,
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        created_at=yesterday_signal,
        updated_at=yesterday_signal,
        stop_price=101.0,
        limit_price=101.5,
        initial_stop_price=99.5,
        signal_timestamp=yesterday_signal,
    )
    order_store = RecordingOrderStore([stale_entry])
    audit_store = RecordingAuditEventStore()
    runtime = SimpleNamespace(order_store=order_store, audit_event_store=audit_store)
    broker = RecordingBroker()

    report = dispatch_pending_orders(settings=settings, runtime=runtime, broker=broker, now=now)

    assert broker.entry_calls == []
    assert order_store.saved == []
    assert report["submitted_count"] == 0
