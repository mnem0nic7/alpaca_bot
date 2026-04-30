from __future__ import annotations

import threading
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

    def save(self, order: OrderRecord, *, commit: bool = True) -> None:
        self.saved.append(order)


class RecordingAuditEventStore:
    def __init__(self) -> None:
        self.appended: list[AuditEvent] = []

    def append(self, event: AuditEvent, *, commit: bool = True) -> None:
        self.appended.append(event)


class FakeConnection:
    def commit(self) -> None:
        pass


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
    runtime = SimpleNamespace(order_store=order_store, audit_event_store=audit_store, connection=FakeConnection())
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
    # submitting stamp is written before broker call; confirmed status written after
    assert order_store.saved == [
        OrderRecord(
            client_order_id="paper:v1-breakout:AAPL:entry:1",
            symbol="AAPL",
            side="buy",
            intent_type="entry",
            status="submitting",
            quantity=25,
            trading_mode=TradingMode.PAPER,
            strategy_version="v1-breakout",
            created_at=now,
            updated_at=now,
            stop_price=101.25,
            limit_price=101.5,
            initial_stop_price=99.75,
            broker_order_id=None,
            signal_timestamp=now,
        ),
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
            status="submitting",
            quantity=25,
            trading_mode=TradingMode.PAPER,
            strategy_version="v1-breakout",
            created_at=now,
            updated_at=now,
            stop_price=99.75,
            broker_order_id=None,
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
            event_type="order_dispatch_submitting",
            symbol="AAPL",
            payload={"client_order_id": "paper:v1-breakout:AAPL:entry:1", "intent_type": "entry"},
            created_at=now,
        ),
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
            event_type="order_dispatch_submitting",
            symbol="AAPL",
            payload={"client_order_id": "paper:v1-breakout:AAPL:stop:1", "intent_type": "stop"},
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
    runtime = SimpleNamespace(order_store=order_store, audit_event_store=audit_store, connection=FakeConnection())
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
    runtime = SimpleNamespace(order_store=order_store, audit_event_store=audit_store, connection=FakeConnection())
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
    # submitting stamp + confirmed status = 2 saves for the stop order
    assert [saved.client_order_id for saved in order_store.saved] == [
        "paper:v1-breakout:AAPL:stop:2",
        "paper:v1-breakout:AAPL:stop:2",
    ]
    assert [event.event_type for event in audit_store.appended] == [
        "order_dispatch_submitting",
        "order_submitted",
    ]
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
    runtime = SimpleNamespace(order_store=order_store, audit_event_store=audit_store, connection=FakeConnection())

    report = dispatch_pending_orders(
        settings=settings, runtime=runtime, broker=FailingBroker(), now=now
    )

    # submitting stamp written before broker call, then error status after failure
    assert len(order_store.saved) == 2
    assert order_store.saved[0].status == "submitting"
    assert order_store.saved[1].status == "error"
    assert order_store.saved[1].client_order_id == entry_order.client_order_id
    # submitting audit first, then failure audit
    assert len(audit_store.appended) == 2
    assert audit_store.appended[0].event_type == "order_dispatch_submitting"
    assert audit_store.appended[1].event_type == "order_dispatch_failed"
    assert audit_store.appended[1].symbol == "AAPL"
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
    runtime = SimpleNamespace(order_store=order_store, audit_event_store=audit_store, connection=FakeConnection())
    broker = RecordingBroker()

    report = dispatch_pending_orders(settings=settings, runtime=runtime, broker=broker, now=now)

    assert broker.entry_calls == []
    # Stale order must be persisted with status="expired"
    assert len(order_store.saved) == 1
    assert order_store.saved[0].status == "expired"
    assert order_store.saved[0].client_order_id == stale_entry.client_order_id
    # Audit event must be appended
    assert len(audit_store.appended) == 1
    assert audit_store.appended[0].event_type == "order_expired_stale_signal"
    assert audit_store.appended[0].symbol == "AAPL"
    assert audit_store.appended[0].payload["signal_date"] == "2026-04-24"
    assert audit_store.appended[0].payload["session_date"] == "2026-04-25"
    assert report["submitted_count"] == 0


def test_dispatch_handles_naive_signal_timestamp_without_crashing() -> None:
    """A naive signal_timestamp (no tzinfo) must not raise ValueError."""
    _, dispatch_pending_orders = load_order_dispatch_api()
    settings = make_settings()
    now = datetime(2026, 4, 25, 18, 30, tzinfo=timezone.utc)
    # Naive datetime — as might come from Bar.from_dict without timezone info
    naive_signal = datetime(2026, 4, 24, 19, 0)  # no tzinfo
    stale_entry = OrderRecord(
        client_order_id="paper:v1-breakout:AAPL:entry:naive",
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
        signal_timestamp=naive_signal,
    )
    order_store = RecordingOrderStore([stale_entry])
    audit_store = RecordingAuditEventStore()
    runtime = SimpleNamespace(order_store=order_store, audit_event_store=audit_store, connection=FakeConnection())
    broker = RecordingBroker()

    # Must not raise — naive timestamp is treated as UTC, so stale date is detected
    report = dispatch_pending_orders(settings=settings, runtime=runtime, broker=broker, now=now)
    assert broker.entry_calls == []
    assert report["submitted_count"] == 0


def test_dispatch_does_not_skip_naive_signal_timestamp_from_same_day() -> None:
    """A naive signal_timestamp from the same session day (treated as UTC) must NOT be
    skipped — only genuinely stale dates should be filtered."""
    _, dispatch_pending_orders = load_order_dispatch_api()
    settings = make_settings()
    # now is 14:30 ET on 2026-04-25 (18:30 UTC)
    now = datetime(2026, 4, 25, 18, 30, tzinfo=timezone.utc)
    # Naive datetime from today's session (naive → assume UTC → 2026-04-25)
    same_day_naive = datetime(2026, 4, 25, 14, 0)  # no tzinfo, same UTC date
    entry = OrderRecord(
        client_order_id="paper:v1-breakout:AAPL:entry:sameday",
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
        signal_timestamp=same_day_naive,
    )
    order_store = RecordingOrderStore([entry])
    audit_store = RecordingAuditEventStore()
    runtime = SimpleNamespace(order_store=order_store, audit_event_store=audit_store, connection=FakeConnection())
    broker = RecordingBroker()

    report = dispatch_pending_orders(settings=settings, runtime=runtime, broker=broker, now=now)

    # Same-day naive signal must be dispatched, not skipped
    assert len(broker.entry_calls) == 1
    assert report["submitted_count"] == 1


def test_dispatch_pending_orders_acquires_store_lock_for_order_store_save() -> None:
    """store_lock must be held when order_store.save() is called so the stream
    thread cannot concurrently use the shared psycopg2 connection."""
    _, dispatch_pending_orders = load_order_dispatch_api()
    settings = make_settings()
    now = datetime(2026, 4, 25, 18, 30, tzinfo=timezone.utc)
    entry = OrderRecord(
        client_order_id="paper:v1-breakout:AAPL:entry:lock-test",
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

    real_lock = threading.Lock()
    lock_held_during_save: list[bool] = []

    class LockWatchingOrderStore(RecordingOrderStore):
        def save(self, order: OrderRecord, *, commit: bool = True) -> None:
            # If lock is held by the caller, acquire(blocking=False) returns False.
            acquired = real_lock.acquire(blocking=False)
            lock_held_during_save.append(not acquired)
            if acquired:
                real_lock.release()
            super().save(order, commit=commit)

    runtime = SimpleNamespace(
        order_store=LockWatchingOrderStore([entry]),
        audit_event_store=RecordingAuditEventStore(),
        store_lock=real_lock,
        connection=FakeConnection(),
    )
    broker = RecordingBroker()

    dispatch_pending_orders(settings=settings, runtime=runtime, broker=broker, now=now)

    assert lock_held_during_save, "order_store.save was never called"
    assert all(lock_held_during_save), (
        "store_lock must be held for every order_store.save call in dispatch_pending_orders"
    )


def test_dispatch_skips_entry_when_strategy_is_blocked() -> None:
    """Entry orders whose strategy_name appears in blocked_strategy_names must be skipped."""
    _, dispatch_pending_orders = load_order_dispatch_api()
    settings = make_settings()
    now = datetime(2026, 4, 24, 19, 30, tzinfo=timezone.utc)
    entry_order = OrderRecord(
        client_order_id="paper:v1-breakout:breakout:AAPL:entry:1",
        symbol="AAPL",
        side="buy",
        intent_type="entry",
        status="pending_submit",
        quantity=10,
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        strategy_name="breakout",
        created_at=now,
        updated_at=now,
        stop_price=101.0,
        limit_price=101.5,
        initial_stop_price=99.5,
        signal_timestamp=now,
    )
    order_store = RecordingOrderStore([entry_order])
    audit_store = RecordingAuditEventStore()
    runtime = SimpleNamespace(order_store=order_store, audit_event_store=audit_store, connection=FakeConnection())

    report = dispatch_pending_orders(
        settings=settings,
        runtime=runtime,
        broker=RecordingBroker(),
        now=now,
        blocked_strategy_names={"breakout"},
    )

    assert order_store.saved == [], "blocked entry must not be submitted or saved"
    assert audit_store.appended == []
    assert report["submitted_count"] == 0


def test_dispatch_does_not_block_non_entry_order_for_blocked_strategy() -> None:
    """blocked_strategy_names only filters entry intents, not stops."""
    _, dispatch_pending_orders = load_order_dispatch_api()
    settings = make_settings()
    now = datetime(2026, 4, 24, 19, 30, tzinfo=timezone.utc)
    stop_order = OrderRecord(
        client_order_id="paper:v1-breakout:breakout:AAPL:stop:1",
        symbol="AAPL",
        side="sell",
        intent_type="stop",
        status="pending_submit",
        quantity=10,
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        strategy_name="breakout",
        created_at=now,
        updated_at=now,
        stop_price=99.5,
        signal_timestamp=now,
    )
    order_store = RecordingOrderStore([stop_order])
    audit_store = RecordingAuditEventStore()
    runtime = SimpleNamespace(order_store=order_store, audit_event_store=audit_store, connection=FakeConnection())

    report = dispatch_pending_orders(
        settings=settings,
        runtime=runtime,
        broker=RecordingBroker(),
        now=now,
        blocked_strategy_names={"breakout"},
    )

    assert report["submitted_count"] == 1, "stop order must not be filtered by blocked_strategy_names"


def test_dispatch_notifier_called_on_broker_failure() -> None:
    """When broker submission fails and a notifier is provided, it must receive send()."""
    _, dispatch_pending_orders = load_order_dispatch_api()
    settings = make_settings()
    now = datetime(2026, 4, 24, 19, 30, tzinfo=timezone.utc)
    entry_order = OrderRecord(
        client_order_id="paper:v1-breakout:breakout:AAPL:entry:notifier",
        symbol="AAPL",
        side="buy",
        intent_type="entry",
        status="pending_submit",
        quantity=10,
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        strategy_name="breakout",
        created_at=now,
        updated_at=now,
        stop_price=101.0,
        limit_price=101.5,
        initial_stop_price=99.5,
        signal_timestamp=now,
    )
    notifier_calls: list[dict] = []

    class RecordingNotifier:
        def send(self, *, subject: str, body: str) -> None:
            notifier_calls.append({"subject": subject, "body": body})

    runtime = SimpleNamespace(
        order_store=RecordingOrderStore([entry_order]),
        audit_event_store=RecordingAuditEventStore(),
        connection=FakeConnection(),
    )

    dispatch_pending_orders(
        settings=settings,
        runtime=runtime,
        broker=FailingBroker(),
        now=now,
        notifier=RecordingNotifier(),
    )

    assert len(notifier_calls) == 1
    assert "AAPL" in notifier_calls[0]["subject"]
    assert "entry" in notifier_calls[0]["subject"]


def test_dispatch_notifier_not_called_on_success() -> None:
    """Notifier must not be called when broker submission succeeds."""
    _, dispatch_pending_orders = load_order_dispatch_api()
    settings = make_settings()
    now = datetime(2026, 4, 24, 19, 30, tzinfo=timezone.utc)
    entry_order = OrderRecord(
        client_order_id="paper:v1-breakout:breakout:AAPL:entry:ok",
        symbol="AAPL",
        side="buy",
        intent_type="entry",
        status="pending_submit",
        quantity=10,
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        strategy_name="breakout",
        created_at=now,
        updated_at=now,
        stop_price=101.0,
        limit_price=101.5,
        initial_stop_price=99.5,
        signal_timestamp=now,
    )
    notifier_calls: list[dict] = []

    class RecordingNotifier:
        def send(self, *, subject: str, body: str) -> None:
            notifier_calls.append({"subject": subject, "body": body})

    runtime = SimpleNamespace(
        order_store=RecordingOrderStore([entry_order]),
        audit_event_store=RecordingAuditEventStore(),
        connection=FakeConnection(),
    )

    dispatch_pending_orders(
        settings=settings,
        runtime=runtime,
        broker=RecordingBroker(),
        now=now,
        notifier=RecordingNotifier(),
    )

    assert notifier_calls == []


def test_dispatch_unsupported_intent_type_sets_order_to_error_status() -> None:
    """An order with an unsupported intent_type (e.g. 'exit') that somehow reaches
    pending_submit must be marked as 'error' — not submitted — and must emit an
    order_dispatch_failed audit event."""
    _, dispatch_pending_orders = load_order_dispatch_api()
    settings = make_settings()
    now = datetime(2026, 4, 24, 19, 30, tzinfo=timezone.utc)

    rogue_order = OrderRecord(
        client_order_id="paper:v1-breakout:AAPL:exit:rogue",
        symbol="AAPL",
        side="sell",
        intent_type="exit",
        status="pending_submit",
        quantity=10,
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        created_at=now,
        updated_at=now,
        signal_timestamp=now,
    )
    order_store = RecordingOrderStore([rogue_order])
    audit_store = RecordingAuditEventStore()
    runtime = SimpleNamespace(
        order_store=order_store,
        audit_event_store=audit_store,
        connection=FakeConnection(),
    )

    report = dispatch_pending_orders(
        settings=settings,
        runtime=runtime,
        broker=RecordingBroker(),
        now=now,
    )

    assert report["submitted_count"] == 0
    # submitting stamp written first; error written after _submit_order raises for unsupported intent
    assert len(order_store.saved) == 2
    assert order_store.saved[0].status == "submitting"
    assert order_store.saved[1].status == "error"
    assert audit_store.appended[0].event_type == "order_dispatch_submitting"
    assert audit_store.appended[1].event_type == "order_dispatch_failed"


# ---------------------------------------------------------------------------
# commit=False discipline and rollback guard tests
# ---------------------------------------------------------------------------

class _CommitTrackingOrderStore:
    """Order store that records commit= args on save()."""

    def __init__(self, pending_orders: list[OrderRecord]) -> None:
        self.pending_orders = list(pending_orders)
        self.saved: list[OrderRecord] = []
        self.commit_args: list[bool] = []

    def list_pending_submit(self, *, trading_mode, strategy_version) -> list[OrderRecord]:
        return list(self.pending_orders)

    def save(self, order: OrderRecord, *, commit: bool = True) -> None:
        self.saved.append(order)
        self.commit_args.append(commit)


class _CommitTrackingAuditEventStore:
    def __init__(self) -> None:
        self.appended: list[AuditEvent] = []
        self.commit_args: list[bool] = []

    def append(self, event: AuditEvent, *, commit: bool = True) -> None:
        self.appended.append(event)
        self.commit_args.append(commit)


class _RollbackTrackingConnection:
    def __init__(self) -> None:
        self.commit_count = 0
        self.rollback_count = 0

    def commit(self) -> None:
        self.commit_count += 1

    def rollback(self) -> None:
        self.rollback_count += 1


class _FailingOnSaveOrderStore:
    """Fails on save() to test rollback guards."""

    def __init__(self, pending_orders: list[OrderRecord]) -> None:
        self.pending_orders = list(pending_orders)

    def list_pending_submit(self, *, trading_mode, strategy_version) -> list[OrderRecord]:
        return list(self.pending_orders)

    def save(self, order: OrderRecord, *, commit: bool = True) -> None:
        raise RuntimeError("simulated_db_failure")


def _make_entry_order(*, now: datetime) -> OrderRecord:
    return OrderRecord(
        client_order_id="paper:v1-breakout:AAPL:entry:t1",
        symbol="AAPL",
        side="buy",
        intent_type="entry",
        status="pending_submit",
        quantity=10,
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        created_at=now,
        updated_at=now,
        stop_price=99.0,
        limit_price=101.0,
        initial_stop_price=99.0,
        signal_timestamp=now,
    )


def test_dispatch_success_path_uses_commit_false_for_all_writes() -> None:
    """Both order_store.save() and audit_event_store.append() in the success path
    must use commit=False; exactly one connection.commit() must fire."""
    _, dispatch_pending_orders = load_order_dispatch_api()
    settings = make_settings()
    now = datetime(2026, 4, 27, 14, 0, tzinfo=timezone.utc)
    order = _make_entry_order(now=now)

    order_store = _CommitTrackingOrderStore([order])
    audit_store = _CommitTrackingAuditEventStore()
    connection = _RollbackTrackingConnection()
    runtime = SimpleNamespace(
        order_store=order_store,
        audit_event_store=audit_store,
        connection=connection,
    )

    dispatch_pending_orders(settings=settings, runtime=runtime, broker=RecordingBroker(), now=now)

    assert all(not c for c in order_store.commit_args), (
        f"order_store.save() must use commit=False; got {order_store.commit_args}"
    )
    assert all(not c for c in audit_store.commit_args), (
        f"audit_event_store.append() must use commit=False; got {audit_store.commit_args}"
    )
    assert connection.commit_count == 2, (
        f"Two connection.commit() per order (submitting stamp + confirmed status); got {connection.commit_count}"
    )


def test_dispatch_success_path_rollback_on_db_failure() -> None:
    """If order_store.save() raises in the success path, rollback() must be called
    and the exception must propagate."""
    _, dispatch_pending_orders = load_order_dispatch_api()
    settings = make_settings()
    now = datetime(2026, 4, 27, 14, 5, tzinfo=timezone.utc)
    order = _make_entry_order(now=now)

    connection = _RollbackTrackingConnection()
    runtime = SimpleNamespace(
        order_store=_FailingOnSaveOrderStore([order]),
        audit_event_store=RecordingAuditEventStore(),
        connection=connection,
    )

    with pytest.raises(RuntimeError, match="simulated_db_failure"):
        dispatch_pending_orders(
            settings=settings, runtime=runtime, broker=RecordingBroker(), now=now
        )

    assert connection.rollback_count == 1, (
        f"rollback must be called once when DB write fails in success path; "
        f"got rollback_count={connection.rollback_count}"
    )


def test_dispatch_broker_failure_path_rollback_on_db_failure() -> None:
    """If broker submission fails and then order_store.save() raises in the
    broker-failure path, rollback() must be called and the exception must propagate."""
    _, dispatch_pending_orders = load_order_dispatch_api()
    settings = make_settings()
    now = datetime(2026, 4, 27, 14, 10, tzinfo=timezone.utc)
    order = _make_entry_order(now=now)

    class FailingBroker:
        def submit_stop_limit_entry(self, **kwargs):
            raise RuntimeError("broker_down")

        def submit_stop_order(self, **kwargs):
            raise RuntimeError("broker_down")

    connection = _RollbackTrackingConnection()
    runtime = SimpleNamespace(
        order_store=_FailingOnSaveOrderStore([order]),
        audit_event_store=RecordingAuditEventStore(),
        connection=connection,
    )

    with pytest.raises(RuntimeError, match="simulated_db_failure"):
        dispatch_pending_orders(
            settings=settings, runtime=runtime, broker=FailingBroker(), now=now
        )

    assert connection.rollback_count == 1, (
        f"rollback must be called once when DB write fails in broker-failure path; "
        f"got rollback_count={connection.rollback_count}"
    )


def test_dispatch_entry_order_with_none_stop_price_records_error_status() -> None:
    """Entry order missing stop_price/limit_price raises ValueError inside _submit_order.
    dispatch_pending_orders must catch it, record status='error' in the DB, and continue."""
    _, dispatch_pending_orders = load_order_dispatch_api()
    settings = make_settings()
    now = datetime(2026, 4, 25, 14, 30, tzinfo=timezone.utc)
    bad_entry = OrderRecord(
        client_order_id="paper:v1-breakout:AAPL:entry:no-prices",
        symbol="AAPL",
        side="buy",
        intent_type="entry",
        status="pending_submit",
        quantity=10,
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        created_at=now,
        updated_at=now,
        stop_price=None,   # missing — must raise ValueError
        limit_price=None,  # missing
        initial_stop_price=None,
        signal_timestamp=now,
    )
    order_store = RecordingOrderStore([bad_entry])
    audit_store = RecordingAuditEventStore()
    runtime = SimpleNamespace(order_store=order_store, audit_event_store=audit_store, connection=FakeConnection())

    report = dispatch_pending_orders(settings=settings, runtime=runtime, broker=RecordingBroker(), now=now)

    # submitting stamp written first; error written after _submit_order raises
    assert len(order_store.saved) == 2
    assert order_store.saved[0].status == "submitting"
    assert order_store.saved[1].status == "error"
    assert order_store.saved[1].client_order_id == bad_entry.client_order_id
    # submitting audit first, then failure audit
    assert len(audit_store.appended) == 2
    assert audit_store.appended[0].event_type == "order_dispatch_submitting"
    assert audit_store.appended[1].event_type == "order_dispatch_failed"
    # Dispatch loop continued — 0 orders submitted (broker call never made)
    assert report["submitted_count"] == 0


def test_dispatch_stale_stop_order_expires_with_prior_day_created_at() -> None:
    """A pending_submit stop order created on a prior trading day must be expired instead
    of submitted — submitting it would create a naked short against a closed position."""
    _, dispatch_pending_orders = load_order_dispatch_api()
    settings = make_settings()
    # now is 10:00 ET on 2026-04-25 (14:00 UTC)
    now = datetime(2026, 4, 25, 14, 0, tzinfo=timezone.utc)
    # Stop was created yesterday
    yesterday = datetime(2026, 4, 24, 19, 0, tzinfo=timezone.utc)
    stale_stop = OrderRecord(
        client_order_id="paper:v1-breakout:AAPL:stop:stale",
        symbol="AAPL",
        side="sell",
        intent_type="stop",
        status="pending_submit",
        quantity=10,
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        created_at=yesterday,
        updated_at=yesterday,
        stop_price=99.75,
        initial_stop_price=99.75,
        signal_timestamp=None,
        broker_order_id=None,
    )
    order_store = RecordingOrderStore([stale_stop])
    audit_store = RecordingAuditEventStore()
    runtime = SimpleNamespace(order_store=order_store, audit_event_store=audit_store, connection=FakeConnection())
    broker = RecordingBroker()

    report = dispatch_pending_orders(settings=settings, runtime=runtime, broker=broker, now=now)

    # Broker must NOT be called
    assert broker.stop_calls == []
    # Order must be saved with status="expired"
    assert len(order_store.saved) == 1
    assert order_store.saved[0].status == "expired"
    assert order_store.saved[0].client_order_id == stale_stop.client_order_id
    # Audit event must be appended
    assert len(audit_store.appended) == 1
    assert audit_store.appended[0].event_type == "order_expired_stale_stop"
    assert audit_store.appended[0].symbol == "AAPL"
    assert audit_store.appended[0].payload["created_date"] == "2026-04-24"
    assert audit_store.appended[0].payload["session_date"] == "2026-04-25"
    assert report["submitted_count"] == 0


def test_dispatch_notifier_send_failure_is_swallowed() -> None:
    """If notifier.send() raises, dispatch must NOT re-raise — a notification failure
    must never abort the dispatch loop and leave subsequent orders unsubmitted."""
    _, dispatch_pending_orders = load_order_dispatch_api()
    settings = make_settings()
    now = datetime(2026, 4, 27, 14, 10, tzinfo=timezone.utc)
    order = _make_entry_order(now=now)

    class FailingBroker:
        def submit_stop_limit_entry(self, **kwargs):
            raise RuntimeError("broker_down")

        def submit_stop_order(self, **kwargs):
            raise RuntimeError("broker_down")

    class ExplodingNotifier:
        def send(self, *, subject: str, body: str) -> None:
            raise RuntimeError("smtp_exploded")

    connection = _RollbackTrackingConnection()
    runtime = SimpleNamespace(
        order_store=_CommitTrackingOrderStore([order]),
        audit_event_store=_CommitTrackingAuditEventStore(),
        connection=connection,
    )

    # Must NOT raise even though both broker and notifier fail
    dispatch_pending_orders(
        settings=settings,
        runtime=runtime,
        broker=FailingBroker(),
        now=now,
        notifier=ExplodingNotifier(),
    )


# ---------------------------------------------------------------------------
# Gap 1: submitting intermediate status tests
# ---------------------------------------------------------------------------

def test_dispatch_submitting_stamp_written_before_broker_call() -> None:
    """The order must be stamped 'submitting' in the DB before the broker call.
    On crash between stamp and broker confirmation, startup_recovery can detect
    and reset the in-flight order rather than re-submitting it blindly."""
    _, dispatch_pending_orders = load_order_dispatch_api()
    settings = make_settings()
    now = datetime(2026, 4, 27, 14, 30, tzinfo=timezone.utc)
    order = _make_entry_order(now=now)

    broker_call_order: list[str] = []

    class OrderingOrderStore(RecordingOrderStore):
        def save(self, rec: OrderRecord, *, commit: bool = True) -> None:
            broker_call_order.append(f"save:{rec.status}")
            super().save(rec, commit=commit)

    class OrderingBroker(RecordingBroker):
        def submit_stop_limit_entry(self, **kwargs):
            broker_call_order.append("broker_call")
            return super().submit_stop_limit_entry(**kwargs)

    runtime = SimpleNamespace(
        order_store=OrderingOrderStore([order]),
        audit_event_store=RecordingAuditEventStore(),
        connection=FakeConnection(),
    )

    dispatch_pending_orders(settings=settings, runtime=runtime, broker=OrderingBroker(), now=now)

    assert broker_call_order[0] == "save:submitting", (
        "submitting stamp must be the first save before the broker call"
    )
    assert "broker_call" in broker_call_order
    submitting_idx = broker_call_order.index("save:submitting")
    broker_idx = broker_call_order.index("broker_call")
    assert submitting_idx < broker_idx, "submitting stamp must precede broker call"


def test_dispatch_submitting_stamp_has_no_broker_order_id() -> None:
    """The submitting stamp must set broker_order_id=None — the audit checkpoint
    that signals the order was in-flight when the process last died."""
    _, dispatch_pending_orders = load_order_dispatch_api()
    settings = make_settings()
    now = datetime(2026, 4, 27, 14, 35, tzinfo=timezone.utc)
    order = _make_entry_order(now=now)
    order_store = RecordingOrderStore([order])
    runtime = SimpleNamespace(
        order_store=order_store,
        audit_event_store=RecordingAuditEventStore(),
        connection=FakeConnection(),
    )

    dispatch_pending_orders(settings=settings, runtime=runtime, broker=RecordingBroker(), now=now)

    submitting_saves = [r for r in order_store.saved if r.status == "submitting"]
    assert len(submitting_saves) == 1
    assert submitting_saves[0].broker_order_id is None
    assert submitting_saves[0].client_order_id == order.client_order_id

