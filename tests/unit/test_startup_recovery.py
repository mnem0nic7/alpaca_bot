from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from importlib import import_module

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
    assert order_store.saved == [
        OrderRecord(
            client_order_id="v1-breakout:2026-04-24:SPY:entry:2026-04-24T19:00:00+00:00",
            symbol="SPY",
            side="buy",
            intent_type="entry",
            status="new",
            quantity=15,
            trading_mode=TradingMode.PAPER,
            strategy_version="v1-breakout",
            created_at=now,
            updated_at=now,
            broker_order_id="alpaca-entry-1",
        )
    ]
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

    assert report.mismatches == (
        "local position missing at broker: MSFT",
        "local order missing at broker: v1-breakout:2026-04-24:MSFT:stop:2026-04-24T19:00:00+00:00",
    )
    assert report.synced_position_count == 0
    assert report.synced_order_count == 0
    assert report.cleared_position_count == 1
    assert report.cleared_order_count == 1
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
            status="reconciled_missing",
            quantity=5,
            trading_mode=TradingMode.PAPER,
            strategy_version="v1-breakout",
            created_at=datetime(2026, 4, 24, 19, 0, tzinfo=timezone.utc),
            updated_at=now,
            stop_price=417.50,
            initial_stop_price=417.50,
            broker_order_id="alpaca-stop-1",
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
