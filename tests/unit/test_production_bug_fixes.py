"""Tests for 2026-05-01 production bug fixes.

Covers:
  - Bug 2: ACTIVE_ORDER_STATUSES / ACTIVE_STOP_STATUSES missing "held" / "pending_new"
  - Bug 1: Recovery stop not queued for positions whose prior-day stop was expired
  - Bug 5: Recovery exceptions propagating through run_cycle_once and crashing supervisor
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from importlib import import_module
from types import SimpleNamespace

import pytest

from alpaca_bot.config import Settings, TradingMode
from alpaca_bot.domain import Bar
from alpaca_bot.execution import BrokerAccount, BrokerOrder, BrokerPosition
from alpaca_bot.runtime import RuntimeContext
from alpaca_bot.runtime.startup_recovery import recover_startup_state
from alpaca_bot.storage import AuditEvent, DailySessionState, OrderRecord, PositionRecord, TradingStatusValue


# ---------------------------------------------------------------------------
# Shared factories
# ---------------------------------------------------------------------------


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
    def load(self, *, trading_mode, strategy_version: str):
        return None


class RecordingDailySessionStateStore:
    def load(self, *, session_date, trading_mode, strategy_version, strategy_name="breakout"):
        return None

    def save(self, state) -> None:
        pass


class RecordingAuditEventStore:
    def __init__(self) -> None:
        self.appended: list[AuditEvent] = []

    def append(self, event: AuditEvent, *, commit: bool = True) -> None:
        self.appended.append(event)


class FakeConnection:
    def commit(self) -> None:
        pass

    def rollback(self) -> None:
        pass


class RecordingOrderStore:
    def __init__(self, existing_orders: list[OrderRecord] | None = None, *, daily_pnl: float = 0.0) -> None:
        self.existing_orders = list(existing_orders or [])
        self.saved: list[OrderRecord] = []
        self._daily_pnl = daily_pnl

    def save(self, order: OrderRecord, *, commit: bool = True) -> None:
        self.saved.append(order)

    def list_by_status(
        self,
        *,
        trading_mode,
        strategy_version: str,
        statuses: list[str],
        strategy_name: str | None = None,
    ) -> list[OrderRecord]:
        return [
            order
            for order in self.existing_orders
            if order.trading_mode is trading_mode
            and order.strategy_version == strategy_version
            and order.status in statuses
        ]

    def load(self, client_order_id: str) -> OrderRecord | None:
        for order in reversed(self.saved):
            if order.client_order_id == client_order_id:
                return order
        for order in self.existing_orders:
            if order.client_order_id == client_order_id:
                return order
        return None

    def list_pending_submit(self, *, trading_mode, strategy_version: str) -> list[OrderRecord]:
        return self.list_by_status(
            trading_mode=trading_mode,
            strategy_version=strategy_version,
            statuses=["pending_submit"],
        )

    def daily_realized_pnl(self, *, trading_mode, strategy_version, session_date, market_timezone="America/New_York") -> float:
        return self._daily_pnl

    def daily_realized_pnl_by_symbol(self, *, trading_mode, strategy_version, session_date, market_timezone="America/New_York", strategy_name=None) -> dict:
        return {}

    def list_trade_pnl_by_strategy(self, **kwargs) -> list[dict]:
        return []


class RecordingPositionStore:
    def __init__(self, existing_positions: list[PositionRecord] | None = None) -> None:
        self.existing_positions = list(existing_positions or [])

    def replace_all(self, *, positions, trading_mode, strategy_version, commit=True) -> None:
        pass

    def list_all(self, *, trading_mode, strategy_version) -> list[PositionRecord]:
        return list(self.existing_positions)


def make_runtime_context(
    settings: Settings,
    *,
    order_store: RecordingOrderStore | None = None,
    position_store: RecordingPositionStore | None = None,
    audit_event_store: RecordingAuditEventStore | None = None,
) -> RuntimeContext:
    return RuntimeContext(
        settings=settings,
        connection=FakeConnection(),  # type: ignore[arg-type]
        lock=object(),  # type: ignore[arg-type]
        trading_status_store=RecordingTradingStatusStore(),  # type: ignore[arg-type]
        audit_event_store=audit_event_store or RecordingAuditEventStore(),  # type: ignore[arg-type]
        order_store=order_store or RecordingOrderStore(),  # type: ignore[arg-type]
        position_store=position_store or RecordingPositionStore(),  # type: ignore[arg-type]
        daily_session_state_store=RecordingDailySessionStateStore(),  # type: ignore[arg-type]
    )


_NOW = datetime(2026, 5, 1, 14, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Test 1 — ACTIVE_ORDER_STATUSES contains "held" and "pending_new"
# ---------------------------------------------------------------------------


def test_active_order_statuses_includes_held_and_pending_new() -> None:
    from alpaca_bot.runtime.startup_recovery import ACTIVE_ORDER_STATUSES

    assert "held" in ACTIVE_ORDER_STATUSES
    assert "pending_new" in ACTIVE_ORDER_STATUSES


# ---------------------------------------------------------------------------
# Test 2 — ACTIVE_STOP_STATUSES contains "held"
# ---------------------------------------------------------------------------


def test_active_stop_statuses_includes_held() -> None:
    from alpaca_bot.runtime.cycle_intent_execution import ACTIVE_STOP_STATUSES

    assert "held" in ACTIVE_STOP_STATUSES


# ---------------------------------------------------------------------------
# Test 3 — Held stop order does not appear as reconciliation mismatch
# ---------------------------------------------------------------------------


def test_held_stop_does_not_produce_reconciliation_mismatch() -> None:
    settings = make_settings()
    existing_stop = OrderRecord(
        client_order_id="breakout:v1-breakout:2026-05-01:AAPL:stop",
        broker_order_id="broker-stop-1",
        symbol="AAPL",
        side="sell",
        intent_type="stop",
        status="held",
        quantity=10,
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        stop_price=185.0,
        initial_stop_price=185.0,
        created_at=_NOW,
        updated_at=_NOW,
    )
    order_store = RecordingOrderStore(existing_orders=[existing_stop])
    # Local position matching the broker position.
    position_store = RecordingPositionStore(
        existing_positions=[
            PositionRecord(
                symbol="AAPL",
                trading_mode=TradingMode.PAPER,
                strategy_version="v1-breakout",
                quantity=10,
                entry_price=190.0,
                stop_price=185.0,
                initial_stop_price=185.0,
                opened_at=_NOW,
                updated_at=_NOW,
            )
        ]
    )
    runtime = make_runtime_context(settings, order_store=order_store, position_store=position_store)

    report = recover_startup_state(
        settings=settings,
        runtime=runtime,
        broker_open_positions=[
            BrokerPosition(symbol="AAPL", quantity=10, entry_price=190.0, market_value=1900.0)
        ],
        broker_open_orders=[
            BrokerOrder(
                client_order_id="breakout:v1-breakout:2026-05-01:AAPL:stop",
                broker_order_id="broker-stop-1",
                symbol="AAPL",
                side="sell",
                status="held",
                quantity=10,
            )
        ],
        now=_NOW,
        audit_event_type=None,
    )

    assert report.mismatches == ()


# ---------------------------------------------------------------------------
# Test 4 — Recovery stop queued for open position with no active stop
# ---------------------------------------------------------------------------


def test_recovery_stop_queued_for_open_position_with_no_active_stop() -> None:
    settings = make_settings()
    # Simulate: filled entry exists in DB, position exists, but NO stop order.
    position_store = RecordingPositionStore(
        existing_positions=[
            PositionRecord(
                symbol="SOUN",
                trading_mode=TradingMode.PAPER,
                strategy_version="v1-breakout",
                strategy_name="breakout",
                quantity=202,
                entry_price=4.50,
                stop_price=3.50,
                initial_stop_price=3.50,
                opened_at=_NOW,
                updated_at=_NOW,
            )
        ]
    )
    order_store = RecordingOrderStore()
    audit_store = RecordingAuditEventStore()
    runtime = make_runtime_context(
        settings,
        order_store=order_store,
        position_store=position_store,
        audit_event_store=audit_store,
    )

    report = recover_startup_state(
        settings=settings,
        runtime=runtime,
        broker_open_positions=[
            BrokerPosition(symbol="SOUN", quantity=202, entry_price=4.50, market_value=909.0)
        ],
        broker_open_orders=[],
        now=_NOW,
        audit_event_type=None,
    )

    recovery_stops = [
        o for o in order_store.saved
        if o.intent_type == "stop" and o.symbol == "SOUN" and o.status == "pending_submit"
    ]
    assert len(recovery_stops) == 1, "Expected exactly one recovery stop for SOUN"
    stop = recovery_stops[0]
    assert stop.stop_price == 3.50
    assert stop.quantity == 202

    audit_types = [e.event_type for e in audit_store.appended]
    assert "recovery_stop_queued_for_open_position" in audit_types


# ---------------------------------------------------------------------------
# Test 5 — Recovery stop NOT re-queued when active stop already exists
# ---------------------------------------------------------------------------


def test_recovery_stop_not_duplicated_when_active_stop_already_exists() -> None:
    settings = make_settings()
    recovery_stop_id = (
        f"startup_recovery:{settings.strategy_version}:"
        f"{_NOW.date().isoformat()}:SOUN:stop"
    )
    existing_stop = OrderRecord(
        client_order_id=recovery_stop_id,
        symbol="SOUN",
        side="sell",
        intent_type="stop",
        status="pending_submit",
        quantity=202,
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        stop_price=3.50,
        initial_stop_price=3.50,
        created_at=_NOW,
        updated_at=_NOW,
    )
    position_store = RecordingPositionStore(
        existing_positions=[
            PositionRecord(
                symbol="SOUN",
                trading_mode=TradingMode.PAPER,
                strategy_version="v1-breakout",
                strategy_name="breakout",
                quantity=202,
                entry_price=4.50,
                stop_price=3.50,
                initial_stop_price=3.50,
                opened_at=_NOW,
                updated_at=_NOW,
            )
        ]
    )
    order_store = RecordingOrderStore(existing_orders=[existing_stop])
    audit_store = RecordingAuditEventStore()
    runtime = make_runtime_context(
        settings,
        order_store=order_store,
        position_store=position_store,
        audit_event_store=audit_store,
    )

    recover_startup_state(
        settings=settings,
        runtime=runtime,
        broker_open_positions=[
            BrokerPosition(symbol="SOUN", quantity=202, entry_price=4.50, market_value=909.0)
        ],
        broker_open_orders=[],
        now=_NOW,
        audit_event_type=None,
    )

    recovery_stops_saved = [
        o for o in order_store.saved
        if o.intent_type == "stop" and o.symbol == "SOUN"
    ]
    assert len(recovery_stops_saved) == 0, "Should not re-queue stop when active stop already exists"
    audit_types = [e.event_type for e in audit_store.appended]
    assert "recovery_stop_queued_for_open_position" not in audit_types


# ---------------------------------------------------------------------------
# Test 6 — Recovery exception does not crash run_cycle_once
# ---------------------------------------------------------------------------


def _make_bar(symbol: str, ts: datetime) -> Bar:
    return Bar(symbol=symbol, timestamp=ts, open=100.0, high=101.0, low=99.0, close=100.5, volume=1000)


def test_recovery_exception_does_not_crash_run_cycle_once(monkeypatch) -> None:
    module = import_module("alpaca_bot.runtime.supervisor")
    RuntimeSupervisor = module.RuntimeSupervisor
    SupervisorCycleReport = module.SupervisorCycleReport

    settings = make_settings()
    now = datetime(2026, 5, 1, 14, 0, tzinfo=timezone.utc)
    runtime = make_runtime_context(settings)
    audit_store: RecordingAuditEventStore = runtime.audit_event_store  # type: ignore[assignment]

    # Make recover_startup_state always raise.
    def _raising_recovery(**_kwargs):
        raise RuntimeError("simulated recovery failure")

    monkeypatch.setattr(module, "recover_startup_state", _raising_recovery)

    # Stub out slow I/O: bars, cycle runner, dispatcher.
    bar = _make_bar("AAPL", now)
    market_data = SimpleNamespace(
        get_stock_bars=lambda **_kw: {"AAPL": [bar]},
        get_daily_bars=lambda **_kw: {"AAPL": [bar]},
    )
    broker = SimpleNamespace(
        get_account=lambda: BrokerAccount(equity=100_000.0, buying_power=200_000.0, trading_blocked=False),
        list_open_orders=lambda: [],
        list_open_positions=lambda: [],
        get_clock=lambda: SimpleNamespace(
            timestamp=now,
            is_open=True,
            next_open=now,
            next_close=now,
        ),
        get_calendar=lambda **_kw: [],
    )
    monkeypatch.setattr(module, "run_cycle", lambda **_kw: SimpleNamespace(intents=[]))
    monkeypatch.setattr(module, "dispatch_pending_orders", lambda **_kw: {})
    monkeypatch.setattr(module, "execute_cycle_intents", lambda **_kw: None)

    supervisor = RuntimeSupervisor(
        settings=settings,
        runtime=runtime,
        broker=broker,
        market_data=market_data,
        stream=None,
        close_runtime_fn=lambda _r: None,
        connection_checker=lambda _c: True,
    )

    # Must not raise despite recovery throwing RuntimeError.
    report = supervisor.run_cycle_once(now=lambda: now)

    assert isinstance(report, SupervisorCycleReport)

    # A recovery_exception audit event must have been appended.
    event_types = [e.event_type for e in audit_store.appended]
    assert "recovery_exception" in event_types, (
        f"Expected 'recovery_exception' audit event, got: {event_types}"
    )
