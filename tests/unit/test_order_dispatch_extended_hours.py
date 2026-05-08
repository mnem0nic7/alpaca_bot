from __future__ import annotations
from datetime import datetime, timezone
import pytest
from alpaca_bot.config import Settings
from alpaca_bot.execution.alpaca import BrokerOrder
from alpaca_bot.runtime.order_dispatch import dispatch_pending_orders
from alpaca_bot.storage import AuditEvent, OrderRecord
from alpaca_bot.strategy.session import SessionType


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
        "EXTENDED_HOURS_LIMIT_OFFSET_PCT": "0.001",
    })


def _pending_entry_order(stop_price: float = 100.0) -> OrderRecord:
    return OrderRecord(
        client_order_id="test:v1:2026-04-28:AAPL:entry:2026-04-28T06:00:00+00:00",
        symbol="AAPL",
        side="buy",
        intent_type="entry",
        status="pending_submit",
        quantity=10,
        trading_mode="paper",
        strategy_version="v1",
        strategy_name="breakout",
        created_at=datetime(2026, 4, 28, 10, 0, tzinfo=timezone.utc),
        updated_at=datetime(2026, 4, 28, 10, 0, tzinfo=timezone.utc),
        stop_price=stop_price,
        limit_price=stop_price * 1.001,
        initial_stop_price=stop_price * 0.99,
        signal_timestamp=datetime(2026, 4, 28, 10, 0, tzinfo=timezone.utc),
    )


def _fake_runtime(orders):
    saved = []
    audits = []

    class FakeOrderStore:
        def list_pending_submit(self, **kwargs):
            return orders
        def list_by_status(self, **kwargs):
            return orders
        def save(self, order, *, commit=True):
            saved.append(order)

    class FakeAuditStore:
        def append(self, event, *, commit=True):
            audits.append(event)

    class FakeConn:
        def commit(self): pass
        def rollback(self): pass

    class FakeRuntime:
        order_store = FakeOrderStore()
        audit_event_store = FakeAuditStore()
        connection = FakeConn()

    return FakeRuntime(), saved, audits


def _fake_broker():
    class FakeBroker:
        calls = []
        def submit_stop_limit_entry(self, **kwargs):
            self.calls.append(("stop_limit_entry", kwargs))
            return BrokerOrder(
                client_order_id=kwargs["client_order_id"],
                broker_order_id="brk1",
                symbol=kwargs["symbol"],
                side="buy",
                status="new",
                quantity=kwargs["quantity"],
            )
        def submit_limit_entry(self, **kwargs):
            self.calls.append(("limit_entry", kwargs))
            return BrokerOrder(
                client_order_id=kwargs["client_order_id"],
                broker_order_id="brk2",
                symbol=kwargs["symbol"],
                side="buy",
                status="new",
                quantity=kwargs["quantity"],
            )
        def submit_stop_order(self, **kwargs):
            self.calls.append(("stop_order", kwargs))
            return BrokerOrder(
                client_order_id=kwargs["client_order_id"],
                broker_order_id="brk3",
                symbol=kwargs["symbol"],
                side="sell",
                status="new",
                quantity=kwargs["quantity"],
            )
    return FakeBroker()


def test_regular_session_uses_stop_limit_entry():
    settings = _settings()
    runtime, saved, _ = _fake_runtime([_pending_entry_order()])
    broker = _fake_broker()
    now = datetime(2026, 4, 28, 14, 0, tzinfo=timezone.utc)  # 10am ET = regular

    dispatch_pending_orders(
        settings=settings,
        runtime=runtime,
        broker=broker,
        now=now,
        session_type=SessionType.REGULAR,
    )
    assert broker.calls[0][0] == "stop_limit_entry"


def test_pre_market_uses_limit_entry():
    settings = _settings()
    runtime, saved, _ = _fake_runtime([_pending_entry_order()])
    broker = _fake_broker()
    now = datetime(2026, 4, 28, 10, 0, tzinfo=timezone.utc)  # 6am ET = pre-market

    dispatch_pending_orders(
        settings=settings,
        runtime=runtime,
        broker=broker,
        now=now,
        session_type=SessionType.PRE_MARKET,
    )
    assert broker.calls[0][0] == "limit_entry"
    _, kwargs = broker.calls[0]
    # limit price = stop_price * (1 + 0.001)
    assert kwargs["limit_price"] == pytest.approx(100.0 * 1.001, rel=1e-5)


def test_after_hours_uses_limit_entry():
    settings = _settings()
    runtime, saved, _ = _fake_runtime([_pending_entry_order()])
    broker = _fake_broker()
    now = datetime(2026, 4, 28, 21, 0, tzinfo=timezone.utc)  # 5pm ET = after hours

    dispatch_pending_orders(
        settings=settings,
        runtime=runtime,
        broker=broker,
        now=now,
        session_type=SessionType.AFTER_HOURS,
    )
    assert broker.calls[0][0] == "limit_entry"


def _pending_stop_order() -> OrderRecord:
    return OrderRecord(
        client_order_id="test:v1:2026-04-28:AAPL:stop:2026-04-28T14:00:00+00:00",
        symbol="AAPL",
        side="sell",
        intent_type="stop",
        status="pending_submit",
        quantity=10,
        trading_mode="paper",
        strategy_version="v1",
        strategy_name="breakout",
        created_at=datetime(2026, 4, 28, 14, 0, tzinfo=timezone.utc),
        updated_at=datetime(2026, 4, 28, 14, 0, tzinfo=timezone.utc),
        stop_price=95.0,
        limit_price=None,
        initial_stop_price=95.0,
        signal_timestamp=datetime(2026, 4, 28, 14, 0, tzinfo=timezone.utc),
    )


# ---------------------------------------------------------------------------
# Stop order dispatch: AH/PM skip (Bug 2A fix)
# ---------------------------------------------------------------------------

def test_stop_order_skipped_during_after_hours():
    """Stop orders must be left pending_submit during AFTER_HOURS — Alpaca rejects them."""
    settings = _settings()
    runtime, saved, audits = _fake_runtime([_pending_stop_order()])
    broker = _fake_broker()
    now = datetime(2026, 4, 28, 21, 0, tzinfo=timezone.utc)  # 5pm ET = after hours

    dispatch_pending_orders(
        settings=settings,
        runtime=runtime,
        broker=broker,
        now=now,
        session_type=SessionType.AFTER_HOURS,
    )
    stop_calls = [c for c in broker.calls if c[0] == "stop_order"]
    assert stop_calls == [], "broker.submit_stop_order must not be called during AFTER_HOURS"


def test_stop_order_skipped_during_pre_market():
    """Stop orders must be left pending_submit during PRE_MARKET — Alpaca rejects them."""
    settings = _settings()
    runtime, saved, audits = _fake_runtime([_pending_stop_order()])
    broker = _fake_broker()
    now = datetime(2026, 4, 28, 10, 0, tzinfo=timezone.utc)  # 6am ET = pre-market

    dispatch_pending_orders(
        settings=settings,
        runtime=runtime,
        broker=broker,
        now=now,
        session_type=SessionType.PRE_MARKET,
    )
    stop_calls = [c for c in broker.calls if c[0] == "stop_order"]
    assert stop_calls == [], "broker.submit_stop_order must not be called during PRE_MARKET"


def test_stop_order_submitted_normally_during_regular_session():
    """Stop orders must reach the broker during a REGULAR session."""
    settings = _settings()
    runtime, saved, audits = _fake_runtime([_pending_stop_order()])
    broker = _fake_broker()
    now = datetime(2026, 4, 28, 14, 0, tzinfo=timezone.utc)  # 10am ET = regular

    dispatch_pending_orders(
        settings=settings,
        runtime=runtime,
        broker=broker,
        now=now,
        session_type=SessionType.REGULAR,
    )
    stop_calls = [c for c in broker.calls if c[0] == "stop_order"]
    assert len(stop_calls) == 1, "broker.submit_stop_order must be called during REGULAR session"


# ---------------------------------------------------------------------------
# Bug 3 — AH stop expiration at regular-session open
# ---------------------------------------------------------------------------

def _pending_stop_order_from_ah(broker_order_id: str | None = None) -> OrderRecord:
    """Stop created during AH session (yesterday evening) — may or may not be submitted."""
    return OrderRecord(
        client_order_id="test:v1:2026-05-07:AAPL:stop:2026-05-07T21:42:00+00:00",
        symbol="AAPL",
        side="sell",
        intent_type="stop",
        status="pending_submit",
        quantity=10,
        trading_mode="paper",
        strategy_version="v1",
        strategy_name="breakout",
        created_at=datetime(2026, 5, 7, 21, 42, tzinfo=timezone.utc),   # 17:42 ET May 7
        updated_at=datetime(2026, 5, 7, 21, 42, tzinfo=timezone.utc),
        stop_price=95.0,
        limit_price=None,
        initial_stop_price=95.0,
        signal_timestamp=datetime(2026, 5, 7, 21, 42, tzinfo=timezone.utc),  # 17:42 ET May 7
        broker_order_id=broker_order_id,
    )


def test_ah_stop_not_expired_at_regular_session_open():
    """
    A pending_submit stop with no broker_order_id (never submitted during AH) must be
    dispatched — not expired — when regular session opens the next morning.
    """
    settings = _settings()
    order = _pending_stop_order_from_ah(broker_order_id=None)
    runtime, saved, audits = _fake_runtime([order])
    broker = _fake_broker()
    now = datetime(2026, 5, 8, 14, 0, tzinfo=timezone.utc)  # 10:00 ET May 8 = regular session

    dispatch_pending_orders(
        settings=settings,
        runtime=runtime,
        broker=broker,
        now=now,
        session_type=SessionType.REGULAR,
    )

    stop_calls = [c for c in broker.calls if c[0] == "stop_order"]
    assert len(stop_calls) == 1, (
        "AH-deferred stop (broker_order_id=None) must be submitted at regular-session open"
    )
    expired_saves = [s for s in saved if s.status == "expired"]
    assert expired_saves == [], "AH-deferred stop must NOT be expired"
    expired_audit = [a for a in audits if a.event_type == "order_expired_stale_stop"]
    assert expired_audit == [], "AH-deferred stop must NOT emit order_expired_stale_stop audit event"


def test_submitted_stop_still_expires_at_next_session():
    """
    A stop that was previously submitted to the broker (broker_order_id set) but has a
    signal_timestamp from a prior session must still be expired — this is the original
    stale-stop guard for submitted-then-disappeared orders.
    """
    settings = _settings()
    order = _pending_stop_order_from_ah(broker_order_id="brk123")
    runtime, saved, audits = _fake_runtime([order])
    broker = _fake_broker()
    now = datetime(2026, 5, 8, 14, 0, tzinfo=timezone.utc)  # 10:00 ET May 8 = regular session

    dispatch_pending_orders(
        settings=settings,
        runtime=runtime,
        broker=broker,
        now=now,
        session_type=SessionType.REGULAR,
    )

    stop_calls = [c for c in broker.calls if c[0] == "stop_order"]
    assert stop_calls == [], "Stale submitted stop must NOT be re-submitted"
    expired_saves = [s for s in saved if s.status == "expired"]
    assert len(expired_saves) == 1, "Stale submitted stop must be saved with status='expired'"
    expired_audit = [a for a in audits if a.event_type == "order_expired_stale_stop"]
    assert len(expired_audit) == 1, "Stale submitted stop must emit order_expired_stale_stop audit event"
