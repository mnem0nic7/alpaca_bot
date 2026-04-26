from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from alpaca_bot.config import Settings, TradingMode
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
    def __init__(self, order: OrderRecord | None = None) -> None:
        self.order = order
        self.load_calls: list[str] = []
        self.load_by_broker_calls: list[str] = []
        self.saved: list[OrderRecord] = []

    def load(self, client_order_id: str) -> OrderRecord | None:
        self.load_calls.append(client_order_id)
        if self.order is not None and self.order.client_order_id == client_order_id:
            return self.order
        return None

    def load_by_broker_order_id(self, broker_order_id: str) -> OrderRecord | None:
        self.load_by_broker_calls.append(broker_order_id)
        if self.order is not None and self.order.broker_order_id == broker_order_id:
            return self.order
        return None

    def save(self, order: OrderRecord) -> None:
        self.saved.append(order)


class RecordingPositionStore:
    def __init__(self) -> None:
        self.saved: list[PositionRecord] = []
        self.deleted: list[dict[str, object]] = []

    def save(self, position: PositionRecord) -> None:
        self.saved.append(position)

    def delete(self, *, symbol: str, trading_mode: TradingMode, strategy_version: str, strategy_name: str = "breakout") -> None:
        self.deleted.append(
            {
                "symbol": symbol,
                "trading_mode": trading_mode,
                "strategy_version": strategy_version,
            }
        )


class RecordingAuditEventStore:
    def __init__(self) -> None:
        self.appended: list[AuditEvent] = []

    def append(self, event: AuditEvent) -> None:
        self.appended.append(event)


def load_trade_update_api():
    from alpaca_bot.runtime.trade_updates import apply_trade_update

    return apply_trade_update


def test_apply_trade_update_updates_entry_order_and_creates_position() -> None:
    apply_trade_update = load_trade_update_api()
    settings = make_settings()
    timestamp = datetime(2026, 4, 24, 19, 15, tzinfo=timezone.utc)
    existing_order = OrderRecord(
        client_order_id="v1-breakout:2026-04-24:AAPL:entry:2026-04-24T19:00:00+00:00",
        symbol="AAPL",
        side="buy",
        intent_type="entry",
        status="accepted",
        quantity=45,
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        created_at=timestamp,
        updated_at=timestamp,
        stop_price=111.01,
        limit_price=111.12,
        initial_stop_price=109.89,
        broker_order_id="broker-entry-1",
        signal_timestamp=datetime(2026, 4, 24, 19, 0, tzinfo=timezone.utc),
    )
    runtime = SimpleNamespace(
        order_store=RecordingOrderStore(existing_order),
        position_store=RecordingPositionStore(),
        audit_event_store=RecordingAuditEventStore(),
    )
    update = {
        "event": "fill",
        "client_order_id": existing_order.client_order_id,
        "broker_order_id": "broker-entry-1",
        "symbol": "AAPL",
        "side": "buy",
        "status": "FILLED",
        "qty": 45,
        "filled_qty": 45,
        "filled_avg_price": 111.02,
        "timestamp": timestamp.isoformat(),
    }

    report = apply_trade_update(
        settings=settings,
        runtime=runtime,  # type: ignore[arg-type]
        update=update,
        now=timestamp,
    )

    assert runtime.order_store.saved[0] == OrderRecord(
        client_order_id=existing_order.client_order_id,
        symbol="AAPL",
        side="buy",
        intent_type="entry",
        status="filled",
        quantity=45,
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        created_at=timestamp,
        updated_at=timestamp,
        stop_price=111.01,
        limit_price=111.12,
        initial_stop_price=109.89,
        broker_order_id="broker-entry-1",
        signal_timestamp=datetime(2026, 4, 24, 19, 0, tzinfo=timezone.utc),
        fill_price=111.02,
        filled_quantity=45,
    )
    assert runtime.position_store.saved == [
        PositionRecord(
            symbol="AAPL",
            trading_mode=TradingMode.PAPER,
            strategy_version="v1-breakout",
            quantity=45,
            entry_price=111.02,
            stop_price=109.89,
            initial_stop_price=109.89,
            opened_at=timestamp,
            updated_at=timestamp,
        )
    ]
    assert runtime.audit_event_store.appended == [
        AuditEvent(
            event_type="trade_update_applied",
            symbol="AAPL",
            payload={
                "client_order_id": existing_order.client_order_id,
                "broker_order_id": "broker-entry-1",
                "event": "fill",
                "status": "filled",
                "fill_price": 111.02,
                "filled_quantity": 45,
                "protective_stop_client_order_id": (
                    "v1-breakout:2026-04-24:AAPL:stop:2026-04-24T19:00:00+00:00"
                ),
            },
            created_at=timestamp,
        )
    ]
    assert report["order_updated"] is True
    assert report["position_updated"] is True
    assert report["protective_stop_queued"] is True
    assert (
        report["protective_stop_client_order_id"]
        == "v1-breakout:2026-04-24:AAPL:stop:2026-04-24T19:00:00+00:00"
    )


def test_apply_trade_update_falls_back_to_broker_order_id_for_partial_fill() -> None:
    apply_trade_update = load_trade_update_api()
    settings = make_settings()
    timestamp = datetime(2026, 4, 24, 19, 10, tzinfo=timezone.utc)
    existing_order = OrderRecord(
        client_order_id="v1-breakout:2026-04-24:AAPL:entry:2026-04-24T19:00:00+00:00",
        symbol="AAPL",
        side="buy",
        intent_type="entry",
        status="accepted",
        quantity=45,
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        created_at=timestamp,
        updated_at=timestamp,
        stop_price=111.01,
        limit_price=111.12,
        initial_stop_price=109.89,
        broker_order_id="broker-entry-1",
        signal_timestamp=datetime(2026, 4, 24, 19, 0, tzinfo=timezone.utc),
    )
    runtime = SimpleNamespace(
        order_store=RecordingOrderStore(existing_order),
        position_store=RecordingPositionStore(),
        audit_event_store=RecordingAuditEventStore(),
    )
    update = SimpleNamespace(
        event="partial_fill",
        client_order_id="unknown-client-id",
        broker_order_id="broker-entry-1",
        symbol="AAPL",
        side="buy",
        status="PARTIALLY_FILLED",
        qty=45,
        filled_qty=20,
        filled_avg_price=111.10,
        timestamp=timestamp,
    )

    report = apply_trade_update(
        settings=settings,
        runtime=runtime,  # type: ignore[arg-type]
        update=update,
        now=timestamp,
    )

    # _find_order tries client_order_id first, then broker_order_id
    assert runtime.order_store.load_calls[0] == "unknown-client-id"
    assert runtime.order_store.load_by_broker_calls == ["broker-entry-1"]
    assert runtime.position_store.saved[-1] == PositionRecord(
        symbol="AAPL",
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        quantity=20,
        entry_price=111.10,
        stop_price=109.89,
        initial_stop_price=109.89,
        opened_at=timestamp,
        updated_at=timestamp,
    )
    assert report["position_updated"] is True
    # Bug A fix: partial fill must queue a protective stop immediately
    assert report["protective_stop_queued"] is True
    assert (
        report["protective_stop_client_order_id"]
        == "v1-breakout:2026-04-24:AAPL:stop:2026-04-24T19:00:00+00:00"
    )


def test_apply_trade_update_for_stop_order_updates_order_without_position_change() -> None:
    apply_trade_update = load_trade_update_api()
    settings = make_settings()
    timestamp = datetime(2026, 4, 24, 19, 25, tzinfo=timezone.utc)
    existing_order = OrderRecord(
        client_order_id="v1-breakout:2026-04-24:AAPL:stop:2026-04-24T19:20:00+00:00",
        symbol="AAPL",
        side="sell",
        intent_type="stop",
        status="accepted",
        quantity=45,
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        created_at=timestamp,
        updated_at=timestamp,
        stop_price=109.89,
        broker_order_id="broker-stop-1",
    )
    runtime = SimpleNamespace(
        order_store=RecordingOrderStore(existing_order),
        position_store=RecordingPositionStore(),
        audit_event_store=RecordingAuditEventStore(),
    )
    update = {
        "event": "canceled",
        "client_order_id": existing_order.client_order_id,
        "broker_order_id": "broker-stop-1",
        "symbol": "AAPL",
        "side": "sell",
        "status": "CANCELED",
        "qty": 45,
        "filled_qty": 0,
        "filled_avg_price": None,
        "timestamp": timestamp.isoformat(),
    }

    report = apply_trade_update(
        settings=settings,
        runtime=runtime,  # type: ignore[arg-type]
        update=update,
        now=timestamp,
    )

    assert runtime.position_store.saved == []
    assert runtime.position_store.deleted == []
    assert runtime.order_store.saved[-1].status == "canceled"
    assert report["position_updated"] is False
    assert report["position_cleared"] is False


def test_apply_trade_update_audits_unmatched_updates() -> None:
    apply_trade_update = load_trade_update_api()
    settings = make_settings()
    timestamp = datetime(2026, 4, 24, 19, 22, tzinfo=timezone.utc)
    runtime = SimpleNamespace(
        order_store=RecordingOrderStore(),
        position_store=RecordingPositionStore(),
        audit_event_store=RecordingAuditEventStore(),
    )

    report = apply_trade_update(
        settings=settings,
        runtime=runtime,  # type: ignore[arg-type]
        update={
            "event": "fill",
            "client_order_id": "unknown-entry",
            "broker_order_id": "broker-missing-1",
            "symbol": "AAPL",
            "side": "buy",
            "status": "FILLED",
            "qty": 25,
            "filled_qty": 25,
            "filled_avg_price": 111.05,
            "timestamp": timestamp.isoformat(),
        },
        now=timestamp,
    )

    assert runtime.order_store.saved == []
    assert runtime.position_store.saved == []
    assert runtime.audit_event_store.appended == [
        AuditEvent(
            event_type="trade_update_unmatched",
            symbol="AAPL",
            payload={
                "client_order_id": "unknown-entry",
                "broker_order_id": "broker-missing-1",
                "event": "fill",
                "status": "filled",
            },
            created_at=timestamp,
        )
    ]
    assert report == {
        "matched_order_id": None,
        "status": "filled",
        "position_updated": False,
        "order_updated": False,
        "unmatched": True,
    }


def test_apply_trade_update_for_filled_entry_queues_pending_protective_stop_order() -> None:
    apply_trade_update = load_trade_update_api()
    settings = make_settings()
    timestamp = datetime(2026, 4, 24, 19, 40, tzinfo=timezone.utc)
    existing_order = OrderRecord(
        client_order_id="v1-breakout:2026-04-24:AAPL:entry:2026-04-24T19:00:00+00:00",
        symbol="AAPL",
        side="buy",
        intent_type="entry",
        status="accepted",
        quantity=45,
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        created_at=timestamp,
        updated_at=timestamp,
        stop_price=111.01,
        limit_price=111.12,
        initial_stop_price=109.89,
        broker_order_id="broker-entry-1",
        signal_timestamp=datetime(2026, 4, 24, 19, 0, tzinfo=timezone.utc),
    )
    runtime = SimpleNamespace(
        order_store=RecordingOrderStore(existing_order),
        position_store=RecordingPositionStore(),
        audit_event_store=RecordingAuditEventStore(),
    )
    update = {
        "event": "fill",
        "client_order_id": existing_order.client_order_id,
        "broker_order_id": "broker-entry-1",
        "symbol": "AAPL",
        "side": "buy",
        "status": "FILLED",
        "qty": 45,
        "filled_qty": 45,
        "filled_avg_price": 111.02,
        "timestamp": timestamp.isoformat(),
    }

    report = apply_trade_update(
        settings=settings,
        runtime=runtime,  # type: ignore[arg-type]
        update=update,
        now=timestamp,
    )

    assert runtime.order_store.saved == [
        OrderRecord(
            client_order_id=existing_order.client_order_id,
            symbol="AAPL",
            side="buy",
            intent_type="entry",
            status="filled",
            quantity=45,
            trading_mode=TradingMode.PAPER,
            strategy_version="v1-breakout",
            created_at=timestamp,
            updated_at=timestamp,
            stop_price=111.01,
            limit_price=111.12,
            initial_stop_price=109.89,
            broker_order_id="broker-entry-1",
            signal_timestamp=datetime(2026, 4, 24, 19, 0, tzinfo=timezone.utc),
            fill_price=111.02,
            filled_quantity=45,
        ),
        OrderRecord(
            client_order_id="v1-breakout:2026-04-24:AAPL:stop:2026-04-24T19:00:00+00:00",
            symbol="AAPL",
            side="sell",
            intent_type="stop",
            status="pending_submit",
            quantity=45,
            trading_mode=TradingMode.PAPER,
            strategy_version="v1-breakout",
            created_at=timestamp,
            updated_at=timestamp,
            stop_price=109.89,
            limit_price=None,
            initial_stop_price=109.89,
            broker_order_id=None,
            signal_timestamp=datetime(2026, 4, 24, 19, 0, tzinfo=timezone.utc),
        ),
    ]
    assert runtime.audit_event_store.appended[-1] == AuditEvent(
        event_type="trade_update_applied",
        symbol="AAPL",
        payload={
            "client_order_id": existing_order.client_order_id,
            "broker_order_id": "broker-entry-1",
            "event": "fill",
            "status": "filled",
            "fill_price": 111.02,
            "filled_quantity": 45,
            "protective_stop_client_order_id": (
                "v1-breakout:2026-04-24:AAPL:stop:2026-04-24T19:00:00+00:00"
            ),
        },
        created_at=timestamp,
    )
    assert report["order_updated"] is True
    assert report["position_updated"] is True
    assert report["protective_stop_queued"] is True


def test_apply_trade_update_for_filled_stop_removes_matching_position() -> None:
    apply_trade_update = load_trade_update_api()
    settings = make_settings()
    timestamp = datetime(2026, 4, 24, 19, 55, tzinfo=timezone.utc)
    existing_order = OrderRecord(
        client_order_id="v1-breakout:2026-04-24:AAPL:stop:2026-04-24T19:20:00+00:00",
        symbol="AAPL",
        side="sell",
        intent_type="stop",
        status="new",
        quantity=45,
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        created_at=timestamp,
        updated_at=timestamp,
        stop_price=109.89,
        initial_stop_price=109.89,
        broker_order_id="broker-stop-1",
        signal_timestamp=datetime(2026, 4, 24, 19, 0, tzinfo=timezone.utc),
    )
    runtime = SimpleNamespace(
        order_store=RecordingOrderStore(existing_order),
        position_store=RecordingPositionStore(),
        audit_event_store=RecordingAuditEventStore(),
    )
    update = {
        "event": "fill",
        "client_order_id": existing_order.client_order_id,
        "broker_order_id": "broker-stop-1",
        "symbol": "AAPL",
        "side": "sell",
        "status": "FILLED",
        "qty": 45,
        "filled_qty": 45,
        "filled_avg_price": 109.89,
        "timestamp": timestamp.isoformat(),
    }

    report = apply_trade_update(
        settings=settings,
        runtime=runtime,  # type: ignore[arg-type]
        update=update,
        now=timestamp,
    )

    assert runtime.order_store.saved == [
        OrderRecord(
            client_order_id=existing_order.client_order_id,
            symbol="AAPL",
            side="sell",
            intent_type="stop",
            status="filled",
            quantity=45,
            trading_mode=TradingMode.PAPER,
            strategy_version="v1-breakout",
            created_at=timestamp,
            updated_at=timestamp,
            stop_price=109.89,
            limit_price=None,
            initial_stop_price=109.89,
            broker_order_id="broker-stop-1",
            signal_timestamp=datetime(2026, 4, 24, 19, 0, tzinfo=timezone.utc),
            fill_price=109.89,
            filled_quantity=45,
        )
    ]
    assert runtime.position_store.saved == []
    assert runtime.position_store.deleted == [
        {
            "symbol": "AAPL",
            "trading_mode": TradingMode.PAPER,
            "strategy_version": "v1-breakout",
        }
    ]
    assert runtime.audit_event_store.appended[-1] == AuditEvent(
        event_type="trade_update_applied",
        symbol="AAPL",
        payload={
            "client_order_id": existing_order.client_order_id,
            "broker_order_id": "broker-stop-1",
            "event": "fill",
            "status": "filled",
            "fill_price": 109.89,
            "filled_quantity": 45,
            "position_cleared": True,
        },
        created_at=timestamp,
    )
    assert report["order_updated"] is True
    assert report["position_updated"] is True
    assert report["position_cleared"] is True


def test_apply_trade_update_for_filled_exit_removes_matching_position() -> None:
    apply_trade_update = load_trade_update_api()
    settings = make_settings()
    timestamp = datetime(2026, 4, 24, 19, 58, tzinfo=timezone.utc)
    existing_order = OrderRecord(
        client_order_id="v1-breakout:2026-04-24:AAPL:exit:2026-04-24T19:45:00+00:00",
        symbol="AAPL",
        side="sell",
        intent_type="exit",
        status="accepted",
        quantity=45,
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        created_at=timestamp,
        updated_at=timestamp,
        initial_stop_price=109.89,
        broker_order_id="broker-exit-1",
        signal_timestamp=datetime(2026, 4, 24, 19, 45, tzinfo=timezone.utc),
    )
    runtime = SimpleNamespace(
        order_store=RecordingOrderStore(existing_order),
        position_store=RecordingPositionStore(),
        audit_event_store=RecordingAuditEventStore(),
    )
    update = {
        "event": "fill",
        "client_order_id": existing_order.client_order_id,
        "broker_order_id": "broker-exit-1",
        "symbol": "AAPL",
        "side": "sell",
        "status": "FILLED",
        "qty": 45,
        "filled_qty": 45,
        "filled_avg_price": 112.10,
        "timestamp": timestamp.isoformat(),
    }

    report = apply_trade_update(
        settings=settings,
        runtime=runtime,  # type: ignore[arg-type]
        update=update,
        now=timestamp,
    )

    assert runtime.position_store.saved == []
    assert runtime.position_store.deleted == [
        {
            "symbol": "AAPL",
            "trading_mode": TradingMode.PAPER,
            "strategy_version": "v1-breakout",
        }
    ]
    assert runtime.order_store.saved[-1].status == "filled"
    assert report["position_updated"] is True
    assert report["position_cleared"] is True
