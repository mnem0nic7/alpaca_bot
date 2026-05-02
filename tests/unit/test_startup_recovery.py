from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from importlib import import_module

import pytest

from alpaca_bot.config import Settings, TradingMode
from alpaca_bot.execution import BrokerOrder, BrokerPosition
from alpaca_bot.runtime import RuntimeContext
from alpaca_bot.runtime.reconcile import ReconciliationOutcome, SessionSnapshot
from alpaca_bot.runtime.startup_recovery import recover_startup_state
from alpaca_bot.runtime.trader import TraderStartupReport, TraderStartupStatus
from alpaca_bot.storage import (
    AuditEvent,
    DailySessionState,
    OrderRecord,
    PositionRecord,
)


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


class RecordingTradingStatusStore:
    def load(self, *, trading_mode: TradingMode, strategy_version: str):
        del trading_mode, strategy_version
        return None


class RecordingDailySessionStateStore:
    def __init__(self) -> None:
        self.saved: list[DailySessionState] = []

    def load(
        self,
        *,
        session_date: date,
        trading_mode: TradingMode,
        strategy_version: str,
    ) -> DailySessionState | None:
        del session_date, trading_mode, strategy_version
        return None

    def save(self, state: DailySessionState) -> None:
        self.saved.append(state)


class RecordingAuditEventStore:
    def __init__(self) -> None:
        self.appended: list[AuditEvent] = []

    def append(self, event: AuditEvent, *, commit: bool = True) -> None:
        self.appended.append(event)


class RecordingPositionStore:
    def __init__(self, existing_positions: list[PositionRecord] | None = None) -> None:
        self.existing_positions = list(existing_positions or [])
        self.replace_all_calls: list[dict[str, object]] = []

    def replace_all(
        self,
        *,
        positions: list[PositionRecord],
        trading_mode: TradingMode,
        strategy_version: str,
        commit: bool = True,
    ) -> None:
        self.replace_all_calls.append(
            {
                "positions": positions,
                "trading_mode": trading_mode,
                "strategy_version": strategy_version,
            }
        )

    def list_all(
        self,
        *,
        trading_mode: TradingMode,
        strategy_version: str,
    ) -> list[PositionRecord]:
        del trading_mode, strategy_version
        return list(self.existing_positions)


class RecordingOrderStore:
    def __init__(self, existing_orders: list[OrderRecord] | None = None) -> None:
        self.existing_orders = list(existing_orders or [])
        self.saved: list[OrderRecord] = []

    def save(self, order: OrderRecord, *, commit: bool = True) -> None:
        self.saved.append(order)

    def list_by_status(
        self,
        *,
        trading_mode: TradingMode,
        strategy_version: str,
        statuses: list[str],
    ) -> list[OrderRecord]:
        return [
            order
            for order in self.existing_orders
            if order.trading_mode is trading_mode
            and order.strategy_version == strategy_version
            and order.status in statuses
        ]

    def load(self, client_order_id: str) -> OrderRecord | None:
        # Check saved first so stops queued in the current recovery call are visible
        # to the Task 3 belt-and-suspenders check within the same call.
        for order in reversed(self.saved):
            if order.client_order_id == client_order_id:
                return order
        for order in self.existing_orders:
            if order.client_order_id == client_order_id:
                return order
        return None


class FakeConnection:
    def commit(self) -> None:
        pass


def make_runtime_context(
    settings: Settings,
    *,
    position_store: RecordingPositionStore,
    order_store: RecordingOrderStore,
    audit_event_store: RecordingAuditEventStore | None = None,
) -> RuntimeContext:
    return RuntimeContext(
        settings=settings,
        connection=FakeConnection(),  # type: ignore[arg-type]
        lock=object(),  # type: ignore[arg-type]
        trading_status_store=RecordingTradingStatusStore(),  # type: ignore[arg-type]
        audit_event_store=audit_event_store or RecordingAuditEventStore(),  # type: ignore[arg-type]
        order_store=order_store,  # type: ignore[arg-type]
        position_store=position_store,  # type: ignore[arg-type]
        daily_session_state_store=RecordingDailySessionStateStore(),  # type: ignore[arg-type]
    )


def load_supervisor_module():
    return import_module("alpaca_bot.runtime.supervisor")


@dataclass
class FakeBroker:
    open_orders: list[BrokerOrder]
    open_positions: list[BrokerPosition]

    def list_open_orders(self) -> list[BrokerOrder]:
        return list(self.open_orders)

    def list_open_positions(self) -> list[BrokerPosition]:
        return list(self.open_positions)


def make_startup_report() -> TraderStartupReport:
    session = SessionSnapshot(
        session_date=date(2026, 4, 24),
        as_of=datetime(2026, 4, 24, 14, 0, tzinfo=timezone.utc),
        is_open=True,
        opens_at=datetime(2026, 4, 24, 13, 30, tzinfo=timezone.utc),
        closes_at=datetime(2026, 4, 24, 20, 0, tzinfo=timezone.utc),
    )
    reconciliation = ReconciliationOutcome(
        session=session,
        mismatch_detected=False,
        mismatches=(),
        session_state=DailySessionState(
            session_date=session.session_date,
            trading_mode=TradingMode.PAPER,
            strategy_version="v1-breakout",
            entries_disabled=False,
            flatten_complete=False,
            last_reconciled_at=session.as_of,
            updated_at=session.as_of,
        ),
    )
    return TraderStartupReport(
        status=TraderStartupStatus.READY,
        session=session,
        reconciliation=reconciliation,
    )


def test_recover_startup_state_syncs_broker_only_positions_and_orders() -> None:
    settings = make_settings()
    now = datetime(2026, 4, 24, 19, 0, tzinfo=timezone.utc)
    position_store = RecordingPositionStore()
    order_store = RecordingOrderStore()
    audit_event_store = RecordingAuditEventStore()
    runtime = make_runtime_context(
        settings,
        position_store=position_store,
        order_store=order_store,
        audit_event_store=audit_event_store,
    )

    report = recover_startup_state(
        settings=settings,
        runtime=runtime,
        broker_open_positions=[
            BrokerPosition(symbol="AAPL", quantity=10, entry_price=189.25, market_value=1892.5)
        ],
        broker_open_orders=[
            BrokerOrder(
                client_order_id="v1-breakout:2026-04-24:SPY:entry:2026-04-24T19:00:00+00:00",
                broker_order_id="alpaca-entry-1",
                symbol="SPY",
                side="buy",
                status="new",
                quantity=15,
            )
        ],
        now=now,
    )

    assert report.mismatches == (
        "broker position missing locally: AAPL",
        "broker order missing locally: v1-breakout:2026-04-24:SPY:entry:2026-04-24T19:00:00+00:00",
    )
    assert report.synced_position_count == 1
    assert report.synced_order_count == 1
    assert report.cleared_position_count == 0
    assert report.cleared_order_count == 0
    assert position_store.replace_all_calls == [
        {
            "positions": [
                PositionRecord(
                    symbol="AAPL",
                    trading_mode=TradingMode.PAPER,
                    strategy_version="v1-breakout",
                    quantity=10,
                    entry_price=189.25,
                    stop_price=189.25 * (1 - settings.breakout_stop_buffer_pct),
                    initial_stop_price=189.25 * (1 - settings.breakout_stop_buffer_pct),
                    opened_at=now,
                    updated_at=now,
                )
            ],
            "trading_mode": TradingMode.PAPER,
            "strategy_version": "v1-breakout",
        }
    ]
    # A recovery stop order for the brand-new AAPL position must also be saved.
    aapl_stops = [
        o for o in order_store.saved
        if o.intent_type == "stop" and o.symbol == "AAPL" and o.status == "pending_submit"
    ]
    assert len(aapl_stops) == 1, "startup_recovery must queue a stop for the brand-new AAPL position"
    spy_entry_saves = [
        o for o in order_store.saved
        if o.symbol == "SPY" and o.intent_type == "entry"
    ]
    assert len(spy_entry_saves) == 1
    assert spy_entry_saves[0].client_order_id == "v1-breakout:2026-04-24:SPY:entry:2026-04-24T19:00:00+00:00"
    assert spy_entry_saves[0].status == "new"
    assert spy_entry_saves[0].broker_order_id == "alpaca-entry-1"
    assert audit_event_store.appended[-1] == AuditEvent(
        event_type="startup_recovery_completed",
        payload={
            "mismatch_count": 2,
            "mismatches": [
                "broker position missing locally: AAPL",
                "broker order missing locally: v1-breakout:2026-04-24:SPY:entry:2026-04-24T19:00:00+00:00",
            ],
            "synced_position_count": 1,
            "synced_order_count": 1,
            "cleared_position_count": 0,
            "cleared_order_count": 0,
        },
        created_at=now,
    )


def test_recover_startup_state_clears_local_state_missing_at_broker() -> None:
    settings = make_settings()
    now = datetime(2026, 4, 24, 19, 5, tzinfo=timezone.utc)
    position_store = RecordingPositionStore(
        existing_positions=[
            PositionRecord(
                symbol="MSFT",
                trading_mode=TradingMode.PAPER,
                strategy_version="v1-breakout",
                quantity=5,
                entry_price=421.10,
                stop_price=417.50,
                initial_stop_price=417.50,
                opened_at=datetime(2026, 4, 24, 18, 30, tzinfo=timezone.utc),
                updated_at=datetime(2026, 4, 24, 18, 30, tzinfo=timezone.utc),
            )
        ]
    )
    order_store = RecordingOrderStore(
        existing_orders=[
            OrderRecord(
                client_order_id="v1-breakout:2026-04-24:MSFT:stop:2026-04-24T19:00:00+00:00",
                symbol="MSFT",
                side="sell",
                intent_type="stop",
                status="submitted",
                quantity=5,
                trading_mode=TradingMode.PAPER,
                strategy_version="v1-breakout",
                created_at=datetime(2026, 4, 24, 19, 0, tzinfo=timezone.utc),
                updated_at=datetime(2026, 4, 24, 19, 0, tzinfo=timezone.utc),
                stop_price=417.50,
                initial_stop_price=417.50,
                broker_order_id="alpaca-stop-1",
            )
        ]
    )
    runtime = make_runtime_context(
        settings,
        position_store=position_store,
        order_store=order_store,
    )

    report = recover_startup_state(
        settings=settings,
        runtime=runtime,
        broker_open_positions=[],
        broker_open_orders=[],
        now=now,
    )

    # The stop order is in its grace period (miss count 0→1 < threshold 3).
    # Only the position mismatch appears; the stop mismatch is suppressed.
    assert report.mismatches == (
        "local position missing at broker: MSFT",
    )
    assert report.synced_position_count == 0
    assert report.synced_order_count == 0
    assert report.cleared_position_count == 1
    assert report.cleared_order_count == 0  # stop not cleared — grace period active
    assert position_store.replace_all_calls == [
        {
            "positions": [],
            "trading_mode": TradingMode.PAPER,
            "strategy_version": "v1-breakout",
        }
    ]
    assert order_store.saved == [
        OrderRecord(
            client_order_id="v1-breakout:2026-04-24:MSFT:stop:2026-04-24T19:00:00+00:00",
            symbol="MSFT",
            side="sell",
            intent_type="stop",
            status="submitted",  # status preserved — not yet cleared
            quantity=5,
            trading_mode=TradingMode.PAPER,
            strategy_version="v1-breakout",
            created_at=datetime(2026, 4, 24, 19, 0, tzinfo=timezone.utc),
            updated_at=now,
            stop_price=417.50,
            initial_stop_price=417.50,
            broker_order_id="alpaca-stop-1",
            reconciliation_miss_count=1,
        )
    ]


def test_runtime_supervisor_startup_passes_recovery_mismatches_into_start_trader() -> None:
    module = load_supervisor_module()
    settings = make_settings()
    now = datetime(2026, 4, 24, 19, 10, tzinfo=timezone.utc)
    position_store = RecordingPositionStore()
    order_store = RecordingOrderStore()
    audit_event_store = RecordingAuditEventStore()
    runtime = make_runtime_context(
        settings,
        position_store=position_store,
        order_store=order_store,
        audit_event_store=audit_event_store,
    )
    broker = FakeBroker(
        open_orders=[
            BrokerOrder(
                client_order_id="v1-breakout:2026-04-24:AAPL:entry:2026-04-24T19:00:00+00:00",
                broker_order_id="alpaca-entry-2",
                symbol="AAPL",
                side="buy",
                status="accepted",
                quantity=10,
            )
        ],
        open_positions=[],
    )
    captured: dict[str, object] = {}

    def fake_start_trader(
        resolved_settings: Settings,
        *,
        broker_client: object,
        bootstrap,
        mismatch_detector=None,
        now=None,
    ) -> TraderStartupReport:
        captured["settings"] = resolved_settings
        captured["broker_client"] = broker_client
        captured["runtime"] = bootstrap(resolved_settings)
        captured["mismatch_detector"] = mismatch_detector
        captured["timestamp"] = now() if callable(now) else now
        return make_startup_report()

    supervisor = module.RuntimeSupervisor(
        settings=settings,
        runtime=runtime,
        broker=broker,
        market_data=object(),
        stream=None,
        start_trader_fn=fake_start_trader,
        close_runtime_fn=lambda _runtime: None,
    )

    report = supervisor.startup(now=lambda: now)

    assert report.status is TraderStartupStatus.READY
    assert captured["settings"] == settings
    assert captured["broker_client"] is broker
    assert captured["runtime"] is runtime
    assert captured["timestamp"] == now
    assert callable(captured["mismatch_detector"])
    assert captured["mismatch_detector"](runtime, report.session) == (
        "broker order missing locally: v1-breakout:2026-04-24:AAPL:entry:2026-04-24T19:00:00+00:00",
    )


def test_broker_only_position_gets_conservative_stop_price() -> None:
    """Broker-only position (no local record) should get a conservative stop derived from entry_price."""
    settings = make_settings()
    now = datetime(2026, 4, 24, 19, 0, tzinfo=timezone.utc)
    position_store = RecordingPositionStore()
    order_store = RecordingOrderStore()
    audit_event_store = RecordingAuditEventStore()
    runtime = make_runtime_context(
        settings,
        position_store=position_store,
        order_store=order_store,
        audit_event_store=audit_event_store,
    )

    entry_price = 189.25
    expected_stop = entry_price * (1 - settings.breakout_stop_buffer_pct)

    recover_startup_state(
        settings=settings,
        runtime=runtime,
        broker_open_positions=[
            BrokerPosition(symbol="AAPL", quantity=10, entry_price=entry_price, market_value=1892.5)
        ],
        broker_open_orders=[],
        now=now,
    )

    assert len(position_store.replace_all_calls) == 1
    synced_positions = position_store.replace_all_calls[0]["positions"]
    assert len(synced_positions) == 1
    synced = synced_positions[0]
    assert synced.symbol == "AAPL"
    assert synced.stop_price == expected_stop
    assert synced.initial_stop_price == expected_stop


def test_broker_only_position_with_no_entry_price_falls_back_to_zero_with_audit_event() -> None:
    """Broker-only position with None entry_price falls back to stop_price=0.0 and logs a warning audit event."""
    settings = make_settings()
    now = datetime(2026, 4, 24, 19, 0, tzinfo=timezone.utc)
    position_store = RecordingPositionStore()
    order_store = RecordingOrderStore()
    audit_event_store = RecordingAuditEventStore()
    runtime = make_runtime_context(
        settings,
        position_store=position_store,
        order_store=order_store,
        audit_event_store=audit_event_store,
    )

    recover_startup_state(
        settings=settings,
        runtime=runtime,
        broker_open_positions=[
            BrokerPosition(symbol="TSLA", quantity=3, entry_price=None, market_value=900.0)
        ],
        broker_open_orders=[],
        now=now,
    )

    assert len(position_store.replace_all_calls) == 1
    synced_positions = position_store.replace_all_calls[0]["positions"]
    assert len(synced_positions) == 1
    synced = synced_positions[0]
    assert synced.symbol == "TSLA"
    assert synced.stop_price == 0.0
    assert synced.initial_stop_price == 0.0

    warning_events = [
        e for e in audit_event_store.appended
        if e.event_type == "startup_recovery_missing_entry_price"
    ]
    assert len(warning_events) == 1
    assert warning_events[0].payload["symbol"] == "TSLA"


def test_recover_startup_state_records_mismatch_when_position_quantity_differs() -> None:
    """When broker reports a different quantity than the local record, a mismatch is logged
    and the synced position uses the broker's authoritative quantity."""
    settings = make_settings()
    now = datetime(2026, 4, 24, 14, 0, tzinfo=timezone.utc)

    local_position = PositionRecord(
        symbol="AAPL",
        trading_mode=settings.trading_mode,
        strategy_version=settings.strategy_version,
        strategy_name="breakout",
        quantity=5,           # local thinks 5 shares
        entry_price=150.0,
        stop_price=148.0,
        initial_stop_price=147.0,
        opened_at=now,
        updated_at=now,
    )
    broker_position = BrokerPosition(
        symbol="AAPL",
        quantity=10,          # broker says 10 shares
        entry_price=150.0,
    )

    position_store = RecordingPositionStore(existing_positions=[local_position])
    order_store = RecordingOrderStore()
    runtime = make_runtime_context(
        settings, position_store=position_store, order_store=order_store
    )

    report = recover_startup_state(
        settings=settings,
        runtime=runtime,  # type: ignore[arg-type]
        broker_open_positions=[broker_position],
        broker_open_orders=[],
        now=now,
        audit_event_type=None,
    )

    assert any("broker position differs locally" in m for m in report.mismatches)
    synced = position_store.replace_all_calls[0]["positions"]
    assert len(synced) == 1
    assert synced[0].quantity == 10, "Synced position must carry broker's authoritative quantity"
    assert synced[0].strategy_name == "breakout", "strategy_name must be preserved from local record"


def test_recover_startup_state_multi_strategy_same_symbol() -> None:
    """When two strategies each hold AAPL and broker reports a combined position,
    both local records are preserved with their per-strategy quantities."""
    settings = make_settings()
    now = datetime(2026, 4, 24, 14, 0, tzinfo=timezone.utc)

    breakout_pos = PositionRecord(
        symbol="AAPL", trading_mode=settings.trading_mode, strategy_version=settings.strategy_version,
        strategy_name="breakout", quantity=10, entry_price=150.0,
        stop_price=148.0, initial_stop_price=147.0, opened_at=now, updated_at=now,
    )
    momentum_pos = PositionRecord(
        symbol="AAPL", trading_mode=settings.trading_mode, strategy_version=settings.strategy_version,
        strategy_name="momentum", quantity=5, entry_price=152.0,
        stop_price=150.0, initial_stop_price=149.0, opened_at=now, updated_at=now,
    )
    broker_position = BrokerPosition(symbol="AAPL", quantity=15, entry_price=150.8)

    position_store = RecordingPositionStore(existing_positions=[breakout_pos, momentum_pos])
    order_store = RecordingOrderStore()
    runtime = make_runtime_context(
        settings, position_store=position_store, order_store=order_store
    )

    report = recover_startup_state(
        settings=settings,
        runtime=runtime,  # type: ignore[arg-type]
        broker_open_positions=[broker_position],
        broker_open_orders=[],
        now=now,
        audit_event_type=None,
    )

    assert report.mismatches == (), f"No mismatch expected when total qty matches: {report.mismatches}"
    synced = position_store.replace_all_calls[0]["positions"]
    assert len(synced) == 2, "Both per-strategy position records must be preserved"
    strategies = {p.strategy_name for p in synced}
    assert strategies == {"breakout", "momentum"}
    assert sum(p.quantity for p in synced) == 15


def test_infer_strategy_name_from_client_order_id() -> None:
    """Known strategy prefixes are parsed; unknown and empty strings fall back to 'breakout'."""
    from alpaca_bot.runtime.startup_recovery import _infer_strategy_name_from_client_order_id

    assert _infer_strategy_name_from_client_order_id("breakout:v1:2026-01-02:AAPL:stop:t") == "breakout"
    assert _infer_strategy_name_from_client_order_id("momentum:v1:2026-01-02:AAPL:entry:t") == "momentum"
    assert _infer_strategy_name_from_client_order_id("unknown_strategy:v1:2026-01-02:AAPL:stop:t") == "breakout"
    assert _infer_strategy_name_from_client_order_id("") == "breakout"


def test_infer_intent_type_all_branches() -> None:
    from alpaca_bot.runtime.startup_recovery import _infer_intent_type

    assert _infer_intent_type(client_order_id="breakout:v1:2026-01-02:AAPL:entry:t", side="buy") == "entry"
    assert _infer_intent_type(client_order_id="breakout:v1:2026-01-02:AAPL:stop:t", side="sell") == "stop"
    assert _infer_intent_type(client_order_id="breakout:v1:2026-01-02:AAPL:exit:t", side="sell") == "exit"
    # Fallback: unknown format, side=buy → entry; side=sell → stop
    assert _infer_intent_type(client_order_id="some-opaque-id", side="buy") == "entry"
    assert _infer_intent_type(client_order_id="some-opaque-id", side="sell") == "stop"


def test_recover_startup_state_preserves_pending_submit_stop_with_no_broker_id() -> None:
    """A pending_submit stop order that was never sent to the broker must NOT be
    written as 'reconciled_missing' on restart.  Its absence from broker open orders
    is expected — it was queued locally before the crash.  It must remain pending_submit
    so dispatch_pending_orders submits it on the next cycle and the position stays protected.
    """
    settings = make_settings()
    now = datetime(2026, 4, 24, 19, 5, tzinfo=timezone.utc)
    position = PositionRecord(
        symbol="AAPL",
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        quantity=10,
        entry_price=155.50,
        stop_price=152.00,
        initial_stop_price=152.00,
        opened_at=datetime(2026, 4, 24, 19, 0, tzinfo=timezone.utc),
        updated_at=datetime(2026, 4, 24, 19, 0, tzinfo=timezone.utc),
    )
    never_submitted_stop = OrderRecord(
        client_order_id="v1-breakout:2026-04-24:AAPL:stop:2026-04-24T19:00:00+00:00",
        symbol="AAPL",
        side="sell",
        intent_type="stop",
        status="pending_submit",
        quantity=10,
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        created_at=datetime(2026, 4, 24, 19, 0, tzinfo=timezone.utc),
        updated_at=datetime(2026, 4, 24, 19, 0, tzinfo=timezone.utc),
        stop_price=152.00,
        initial_stop_price=152.00,
        broker_order_id=None,  # never sent to broker before crash
    )
    position_store = RecordingPositionStore(existing_positions=[position])
    order_store = RecordingOrderStore(existing_orders=[never_submitted_stop])
    runtime = make_runtime_context(
        settings,
        position_store=position_store,
        order_store=order_store,
    )

    report = recover_startup_state(
        settings=settings,
        runtime=runtime,
        broker_open_positions=[],  # position not at broker yet (entry not reflected)
        broker_open_orders=[],
        now=now,
    )

    # Never-submitted stops must be excluded from mismatch reporting.
    missing_mismatch = [
        m for m in report.mismatches
        if "local order missing at broker" in m and "AAPL:stop" in m
    ]
    assert missing_mismatch == [], (
        "pending_submit stop with no broker_order_id must not be reported as a mismatch"
    )
    assert report.cleared_order_count == 0, (
        "pending_submit stop must not be counted as cleared"
    )

    # The stop must NOT have been written with reconciled_missing.
    reconciled_saves = [
        o for o in order_store.saved
        if getattr(o, "status", None) == "reconciled_missing"
        and getattr(o, "intent_type", None) == "stop"
    ]
    assert reconciled_saves == [], (
        "pending_submit stop must remain pending_submit so dispatch_pending_orders "
        "can submit it and keep the position protected after restart"
    )


def test_brand_new_broker_position_without_local_record_gets_stop_queued() -> None:
    """When broker has a position not tracked locally (e.g. manual trade during downtime),
    startup_recovery must synthesize a PositionRecord AND queue a pending_submit stop order
    so the position gets protection immediately on the next dispatch cycle."""
    settings = make_settings()
    now = datetime(2026, 4, 24, 19, 0, tzinfo=timezone.utc)

    broker_position = BrokerPosition(
        symbol="TSLA",
        quantity=20,
        entry_price=175.00,
    )
    position_store = RecordingPositionStore()  # no existing local positions
    order_store = RecordingOrderStore()  # no existing local orders
    runtime = make_runtime_context(
        settings,
        position_store=position_store,
        order_store=order_store,
    )

    report = recover_startup_state(
        settings=settings,
        runtime=runtime,
        broker_open_positions=[broker_position],
        broker_open_orders=[],
        now=now,
    )

    # A mismatch must be reported (brand-new position)
    assert any("broker position missing locally" in m for m in report.mismatches), (
        "Brand-new broker position must be reported as a mismatch"
    )

    # A pending_submit stop order must have been saved
    stop_saves = [
        o for o in order_store.saved
        if getattr(o, "intent_type", None) == "stop"
        and getattr(o, "status", None) == "pending_submit"
        and getattr(o, "symbol", None) == "TSLA"
    ]
    assert len(stop_saves) == 1, (
        "startup_recovery must queue exactly one pending_submit stop for a brand-new position"
    )
    stop = stop_saves[0]
    expected_stop_price = 175.00 * (1 - settings.breakout_stop_buffer_pct)
    assert stop.stop_price == pytest.approx(expected_stop_price), (
        "stop_price must be computed from entry_price * (1 - breakout_stop_buffer_pct)"
    )
    assert stop.quantity == 20
    assert stop.side == "sell"

    # An audit event must be appended for the queued stop
    audit_event_store = runtime.audit_event_store
    stop_queued_events = [
        e for e in audit_event_store.appended
        if getattr(e, "event_type", None) == "startup_recovery_stop_queued"
    ]
    assert len(stop_queued_events) == 1, (
        "startup_recovery must append a startup_recovery_stop_queued audit event"
    )


def test_brand_new_broker_position_does_not_queue_stop_when_one_already_active() -> None:
    """If a pending_submit stop for the symbol already exists locally, no duplicate stop is queued."""
    settings = make_settings()
    now = datetime(2026, 4, 24, 19, 0, tzinfo=timezone.utc)

    broker_position = BrokerPosition(symbol="TSLA", quantity=20, entry_price=175.00)
    existing_stop = OrderRecord(
        client_order_id="startup_recovery:v1-breakout:2026-04-24:TSLA:stop",
        symbol="TSLA",
        side="sell",
        intent_type="stop",
        status="pending_submit",
        quantity=20,
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        created_at=now,
        updated_at=now,
        stop_price=174.0,
        initial_stop_price=174.0,
        signal_timestamp=None,
        broker_order_id=None,
    )
    position_store = RecordingPositionStore()
    order_store = RecordingOrderStore(existing_orders=[existing_stop])
    runtime = make_runtime_context(
        settings,
        position_store=position_store,
        order_store=order_store,
    )

    recover_startup_state(
        settings=settings,
        runtime=runtime,
        broker_open_positions=[broker_position],
        broker_open_orders=[],
        now=now,
    )

    new_stops = [
        o for o in order_store.saved
        if getattr(o, "intent_type", None) == "stop"
        and getattr(o, "status", None) == "pending_submit"
        and getattr(o, "symbol", None) == "TSLA"
        and getattr(o, "client_order_id", None) != existing_stop.client_order_id
    ]
    assert new_stops == [], (
        "No duplicate stop must be queued when an active stop for the symbol already exists"
    )


# ---------------------------------------------------------------------------
# Gap 1: submitting status — startup recovery reset tests
# ---------------------------------------------------------------------------

def test_startup_recovery_resets_submitting_order_to_pending_submit() -> None:
    """A 'submitting' order with no broker_order_id that isn't found at the broker
    must be reset to 'pending_submit' so dispatch_pending_orders retries it.
    This handles the crash-between-stamp-and-broker-confirmation scenario."""
    settings = make_settings()
    now = datetime(2026, 4, 24, 19, 30, tzinfo=timezone.utc)
    position_store = RecordingPositionStore()
    audit_event_store = RecordingAuditEventStore()

    # Local order stamped 'submitting' but never received a broker response
    in_flight_order = OrderRecord(
        client_order_id="paper:v1-breakout:AAPL:entry:inflight",
        symbol="AAPL",
        side="buy",
        intent_type="entry",
        status="submitting",
        quantity=10,
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        created_at=now,
        updated_at=now,
        stop_price=100.0,
        limit_price=100.5,
        initial_stop_price=100.0,
        broker_order_id=None,  # no confirmation received before crash
        signal_timestamp=now,
    )
    order_store = RecordingOrderStore(existing_orders=[in_flight_order])
    runtime = make_runtime_context(
        settings,
        position_store=position_store,
        order_store=order_store,
        audit_event_store=audit_event_store,
    )

    recover_startup_state(
        settings=settings,
        runtime=runtime,
        broker_open_positions=[],
        broker_open_orders=[],  # not found at broker
        now=now,
    )

    reset_saves = [
        r for r in order_store.saved
        if r.client_order_id == "paper:v1-breakout:AAPL:entry:inflight"
    ]
    assert len(reset_saves) == 1, "submitting order must be reset exactly once"
    assert reset_saves[0].status == "pending_submit", (
        f"submitting order must be reset to pending_submit, got {reset_saves[0].status}"
    )
    assert reset_saves[0].broker_order_id is None

    reset_audits = [
        e for e in audit_event_store.appended
        if e.event_type == "startup_recovery_submitting_reset"
    ]
    assert len(reset_audits) == 1
    assert reset_audits[0].symbol == "AAPL"


def test_startup_recovery_does_not_reset_submitting_order_found_at_broker() -> None:
    """A 'submitting' order that IS found at the broker (broker confirmed it)
    must NOT be reset to pending_submit — it was successfully received."""
    settings = make_settings()
    now = datetime(2026, 4, 24, 19, 30, tzinfo=timezone.utc)
    position_store = RecordingPositionStore()
    audit_event_store = RecordingAuditEventStore()

    in_flight_order = OrderRecord(
        client_order_id="paper:v1-breakout:AAPL:entry:confirmed",
        symbol="AAPL",
        side="buy",
        intent_type="entry",
        status="submitting",
        quantity=10,
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        created_at=now,
        updated_at=now,
        stop_price=100.0,
        limit_price=100.5,
        initial_stop_price=100.0,
        broker_order_id=None,
        signal_timestamp=now,
    )
    order_store = RecordingOrderStore(existing_orders=[in_flight_order])
    runtime = make_runtime_context(
        settings,
        position_store=position_store,
        order_store=order_store,
        audit_event_store=audit_event_store,
    )

    broker_order = BrokerOrder(
        client_order_id="paper:v1-breakout:AAPL:entry:confirmed",
        broker_order_id="alpaca-123",
        symbol="AAPL",
        side="buy",
        status="new",
        quantity=10,
    )

    recover_startup_state(
        settings=settings,
        runtime=runtime,
        broker_open_positions=[],
        broker_open_orders=[broker_order],
        now=now,
    )

    reset_audits = [
        e for e in audit_event_store.appended
        if e.event_type == "startup_recovery_submitting_reset"
    ]
    assert reset_audits == [], "confirmed broker order must not be reset to pending_submit"

    confirmed_saves = [
        r for r in order_store.saved
        if r.client_order_id == "paper:v1-breakout:AAPL:entry:confirmed"
    ]
    assert all(r.status != "pending_submit" for r in confirmed_saves), (
        "broker-confirmed order must not be reset to pending_submit"
    )


# ---------------------------------------------------------------------------
# Gap 6: pending_submit entry guard — no orphan stop when entry not yet sent
# ---------------------------------------------------------------------------

def test_startup_recovery_does_not_queue_stop_when_pending_entry_exists() -> None:
    """When a pending_submit entry order exists for a symbol, startup recovery must NOT
    queue a recovery stop — doing so would leave an orphaned stop if the entry is later
    cancelled or fails."""
    settings = make_settings()
    now = datetime(2026, 4, 30, 14, 0, tzinfo=timezone.utc)

    # Broker has a position for MSFT that isn't in our local DB
    broker_position = BrokerPosition(symbol="MSFT", quantity=10, entry_price=400.0)

    # Local DB has a pending_submit entry order for MSFT (not yet sent)
    pending_entry = OrderRecord(
        client_order_id="paper:v1-breakout:MSFT:entry:pending",
        symbol="MSFT",
        side="buy",
        intent_type="entry",
        status="pending_submit",
        quantity=10,
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        created_at=now,
        updated_at=now,
        stop_price=395.0,
        limit_price=400.5,
        initial_stop_price=395.0,
        broker_order_id=None,
        signal_timestamp=now,
    )
    position_store = RecordingPositionStore()
    order_store = RecordingOrderStore(existing_orders=[pending_entry])
    runtime = make_runtime_context(
        settings,
        position_store=position_store,
        order_store=order_store,
    )

    recover_startup_state(
        settings=settings,
        runtime=runtime,
        broker_open_positions=[broker_position],
        broker_open_orders=[],
        now=now,
    )

    stop_saves = [
        o for o in order_store.saved
        if getattr(o, "intent_type", None) == "stop"
        and getattr(o, "symbol", None) == "MSFT"
    ]
    assert stop_saves == [], (
        "Recovery must not queue a stop for a symbol that already has a pending_submit entry order"
    )


def test_recovery_stop_suppressed_when_broker_has_sell_order_for_symbol() -> None:
    """When the broker already has an open sell order for a symbol, startup_recovery must
    NOT queue a recovery stop for that symbol — even if the local DB has no active stop.

    This is RC-3 defense: after RC-1 truncated list_open_orders to 50, the local DB
    cleared MRVL's stop as reconciled_missing. Next cycle tried to queue a recovery stop
    but the broker still had the real stop, causing a 40310000 infinite loop."""
    settings = make_settings()
    now = datetime(2026, 5, 1, 19, 0, tzinfo=timezone.utc)

    # MRVL has a local position but NO local active stop order (simulates post-truncation state)
    mrvl_position = PositionRecord(
        symbol="MRVL",
        trading_mode=settings.trading_mode,
        strategy_version=settings.strategy_version,
        strategy_name="breakout",
        quantity=10,
        entry_price=76.0,
        stop_price=75.0,
        initial_stop_price=75.0,
        opened_at=now,
        updated_at=now,
    )
    position_store = RecordingPositionStore(existing_positions=[mrvl_position])
    audit_event_store = RecordingAuditEventStore()
    order_store = RecordingOrderStore()  # no active stop order for MRVL locally
    runtime = make_runtime_context(
        settings,
        position_store=position_store,
        order_store=order_store,
        audit_event_store=audit_event_store,
    )

    # Broker has MRVL position AND a sell order (the real stop that was dropped by truncation)
    broker_position = BrokerPosition(symbol="MRVL", quantity=10, entry_price=76.0)
    broker_sell_order = BrokerOrder(
        client_order_id="breakout:v1-breakout:2026-05-01:MRVL:stop:t",
        broker_order_id="4c9a5044",
        symbol="MRVL",
        side="sell",
        status="accepted",
        quantity=10,
    )

    recover_startup_state(
        settings=settings,
        runtime=runtime,
        broker_open_positions=[broker_position],
        broker_open_orders=[broker_sell_order],
        now=now,
    )

    stop_saves = [
        o for o in order_store.saved
        if getattr(o, "intent_type", None) == "stop"
        and getattr(o, "status", None) == "pending_submit"
        and getattr(o, "symbol", None) == "MRVL"
    ]
    assert stop_saves == [], (
        "Must not queue a recovery stop when broker already has a sell order for MRVL"
    )

    suppressed_events = [
        e for e in audit_event_store.appended
        if getattr(e, "event_type", None) == "recovery_stop_suppressed_broker_has_stop"
        and getattr(e, "symbol", None) == "MRVL"
    ]
    assert len(suppressed_events) == 1, (
        "Must emit exactly one recovery_stop_suppressed_broker_has_stop event for MRVL"
    )


# ── Grace-period tests (Fix 3) ─────────────────────────────────────────────


def test_reconciliation_grace_period_first_miss_increments_count() -> None:
    """First consecutive miss: count 0→1, status unchanged, audit event emitted, no mismatch."""
    settings = make_settings()
    now = datetime(2026, 5, 2, 14, 0, tzinfo=timezone.utc)
    stop = OrderRecord(
        client_order_id="orb:v1-breakout:2026-05-02:MRVL:stop:original",
        symbol="MRVL",
        side="sell",
        intent_type="stop",
        status="new",
        quantity=50,
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        broker_order_id="broker-stop-1",
        stop_price=75.0,
        initial_stop_price=75.0,
        reconciliation_miss_count=0,
    )
    order_store = RecordingOrderStore(existing_orders=[stop])
    audit_store = RecordingAuditEventStore()
    runtime = make_runtime_context(
        settings,
        position_store=RecordingPositionStore(),
        order_store=order_store,
        audit_event_store=audit_store,
    )

    report = recover_startup_state(
        settings=settings,
        runtime=runtime,
        broker_open_positions=[],
        broker_open_orders=[],
        now=now,
    )

    saved = next((o for o in order_store.saved if o.client_order_id == stop.client_order_id), None)
    assert saved is not None, "Expected a saved record for the stop"
    assert saved.status == "new", f"Expected status='new', got {saved.status!r}"
    assert saved.reconciliation_miss_count == 1, (
        f"Expected reconciliation_miss_count=1, got {saved.reconciliation_miss_count}"
    )
    miss_events = [e for e in audit_store.appended if e.event_type == "reconciliation_miss_count_incremented"]
    assert len(miss_events) == 1, f"Expected 1 miss event, got {len(miss_events)}"
    assert miss_events[0].payload["reconciliation_miss_count"] == 1
    assert miss_events[0].payload["threshold"] == 3
    assert not any(e.event_type == "reconciled_missing_stop_cleared" for e in audit_store.appended)
    assert not any(o.status == "reconciled_missing" for o in order_store.saved), (
        "Stop must NOT be cleared to reconciled_missing on first miss"
    )
    assert not any(stop.client_order_id in m for m in report.mismatches), (
        "Grace-period stop must not appear in report.mismatches on first miss"
    )


def test_reconciliation_grace_period_second_miss_increments_count() -> None:
    """Second consecutive miss: count 1→2, status unchanged, audit event emitted, no mismatch."""
    settings = make_settings()
    now = datetime(2026, 5, 2, 14, 0, tzinfo=timezone.utc)
    stop = OrderRecord(
        client_order_id="orb:v1-breakout:2026-05-02:MRVL:stop:original",
        symbol="MRVL",
        side="sell",
        intent_type="stop",
        status="new",
        quantity=50,
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        broker_order_id="broker-stop-1",
        stop_price=75.0,
        initial_stop_price=75.0,
        reconciliation_miss_count=1,
    )
    order_store = RecordingOrderStore(existing_orders=[stop])
    audit_store = RecordingAuditEventStore()
    runtime = make_runtime_context(
        settings,
        position_store=RecordingPositionStore(),
        order_store=order_store,
        audit_event_store=audit_store,
    )

    report = recover_startup_state(
        settings=settings,
        runtime=runtime,
        broker_open_positions=[],
        broker_open_orders=[],
        now=now,
    )

    saved = next((o for o in order_store.saved if o.client_order_id == stop.client_order_id), None)
    assert saved is not None
    assert saved.status == "new"
    assert saved.reconciliation_miss_count == 2, (
        f"Expected reconciliation_miss_count=2, got {saved.reconciliation_miss_count}"
    )
    miss_events = [e for e in audit_store.appended if e.event_type == "reconciliation_miss_count_incremented"]
    assert len(miss_events) == 1
    assert miss_events[0].payload["reconciliation_miss_count"] == 2
    assert not any(e.event_type == "reconciled_missing_stop_cleared" for e in audit_store.appended)
    assert not any(o.status == "reconciled_missing" for o in order_store.saved)
    assert not any(stop.client_order_id in m for m in report.mismatches), (
        "Grace-period stop must not appear in report.mismatches on second miss"
    )


def test_reconciliation_grace_period_third_miss_clears_to_reconciled_missing() -> None:
    """Third consecutive miss: count=2 → threshold reached → status='reconciled_missing'."""
    settings = make_settings()
    now = datetime(2026, 5, 2, 14, 0, tzinfo=timezone.utc)
    stop = OrderRecord(
        client_order_id="orb:v1-breakout:2026-05-02:MRVL:stop:original",
        symbol="MRVL",
        side="sell",
        intent_type="stop",
        status="new",
        quantity=50,
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        broker_order_id="broker-stop-1",
        stop_price=75.0,
        initial_stop_price=75.0,
        reconciliation_miss_count=2,
    )
    order_store = RecordingOrderStore(existing_orders=[stop])
    audit_store = RecordingAuditEventStore()
    runtime = make_runtime_context(
        settings,
        position_store=RecordingPositionStore(),
        order_store=order_store,
        audit_event_store=audit_store,
    )

    recover_startup_state(
        settings=settings,
        runtime=runtime,
        broker_open_positions=[],
        broker_open_orders=[],
        now=now,
    )

    saved = next((o for o in order_store.saved if o.client_order_id == stop.client_order_id), None)
    assert saved is not None
    assert saved.status == "reconciled_missing", (
        f"Expected status='reconciled_missing' on 3rd miss, got {saved.status!r}"
    )
    cleared_events = [e for e in audit_store.appended if e.event_type == "reconciled_missing_stop_cleared"]
    assert len(cleared_events) == 1, f"Expected 1 cleared event, got {len(cleared_events)}"
    assert cleared_events[0].payload["client_order_id"] == stop.client_order_id
    assert not any(e.event_type == "reconciliation_miss_count_incremented" for e in audit_store.appended)


def test_reconciliation_grace_period_does_not_apply_to_entry_orders() -> None:
    """Entry orders must be cleared to reconciled_missing immediately on the first miss."""
    settings = make_settings()
    now = datetime(2026, 5, 2, 14, 0, tzinfo=timezone.utc)
    entry = OrderRecord(
        client_order_id="orb:v1-breakout:2026-05-02:MRVL:entry:2026-05-02T14:00:00+00:00",
        symbol="MRVL",
        side="buy",
        intent_type="entry",
        status="new",
        quantity=50,
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        broker_order_id="broker-entry-1",
        reconciliation_miss_count=0,
    )
    order_store = RecordingOrderStore(existing_orders=[entry])
    audit_store = RecordingAuditEventStore()
    runtime = make_runtime_context(
        settings,
        position_store=RecordingPositionStore(),
        order_store=order_store,
        audit_event_store=audit_store,
    )

    recover_startup_state(
        settings=settings,
        runtime=runtime,
        broker_open_positions=[],
        broker_open_orders=[],
        now=now,
    )

    saved = next((o for o in order_store.saved if o.client_order_id == entry.client_order_id), None)
    assert saved is not None
    assert saved.status == "reconciled_missing", (
        f"Entry orders must be cleared immediately; got {saved.status!r}"
    )
    assert not any(e.event_type == "reconciliation_miss_count_incremented" for e in audit_store.appended)
    assert not any(e.event_type == "reconciled_missing_stop_cleared" for e in audit_store.appended)


def test_reconciliation_grace_period_resets_count_when_stop_found_at_broker() -> None:
    """When broker confirms the stop exists, reconciliation_miss_count resets to 0."""
    settings = make_settings()
    now = datetime(2026, 5, 2, 14, 0, tzinfo=timezone.utc)
    stop = OrderRecord(
        client_order_id="orb:v1-breakout:2026-05-02:MRVL:stop:original",
        symbol="MRVL",
        side="sell",
        intent_type="stop",
        status="new",
        quantity=50,
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        broker_order_id="broker-stop-1",
        stop_price=75.0,
        initial_stop_price=75.0,
        reconciliation_miss_count=2,
    )
    order_store = RecordingOrderStore(existing_orders=[stop])
    audit_store = RecordingAuditEventStore()
    runtime = make_runtime_context(
        settings,
        position_store=RecordingPositionStore(),
        order_store=order_store,
        audit_event_store=audit_store,
    )
    broker_stop = BrokerOrder(
        client_order_id=stop.client_order_id,
        broker_order_id="broker-stop-1",
        symbol="MRVL",
        side="sell",
        status="new",
        quantity=50,
    )

    recover_startup_state(
        settings=settings,
        runtime=runtime,
        broker_open_positions=[],
        broker_open_orders=[broker_stop],
        now=now,
    )

    saved = next((o for o in order_store.saved if o.client_order_id == stop.client_order_id), None)
    assert saved is not None
    assert saved.reconciliation_miss_count == 0, (
        f"Expected reset to 0 when broker confirms stop; got {saved.reconciliation_miss_count}"
    )
    assert not any(e.event_type == "reconciliation_miss_count_incremented" for e in audit_store.appended)
    assert not any(e.event_type == "reconciled_missing_stop_cleared" for e in audit_store.appended)
