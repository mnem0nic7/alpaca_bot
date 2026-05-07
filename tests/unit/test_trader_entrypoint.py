from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from importlib import import_module
import io
import json

import pytest

from types import SimpleNamespace

from alpaca_bot.config import Settings, TradingMode
from alpaca_bot.execution import BrokerOrder, BrokerPosition, MarketCalendarDay, MarketClock
from alpaca_bot.runtime import RuntimeContext
from alpaca_bot.storage import AuditEvent, DailySessionState, OrderRecord, PositionRecord


def _make_healthy_connection_stub():
    """Return a minimal stub that passes check_connection() (cursor().execute succeeds)."""
    cursor_stub = SimpleNamespace(
        execute=lambda sql, params=None: None,
        fetchone=lambda: (1,),
        fetchall=lambda: [],
    )
    return SimpleNamespace(cursor=lambda: cursor_stub, commit=lambda: None)


def load_cli_main():
    module = import_module("alpaca_bot.runtime.cli")
    return module.main


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
        assert trading_mode is TradingMode.PAPER
        assert strategy_version == "v1-breakout"
        return None


class RecordingDailySessionStateStore:
    def __init__(self, loaded_state: DailySessionState | None = None) -> None:
        self.loaded_state = loaded_state
        self.saved: list[DailySessionState] = []

    def load(
        self,
        *,
        session_date: date,
        trading_mode: TradingMode,
        strategy_version: str,
    ) -> DailySessionState | None:
        assert session_date == date(2026, 4, 24)
        assert trading_mode is TradingMode.PAPER
        assert strategy_version == "v1-breakout"
        return self.loaded_state

    def save(self, state: DailySessionState) -> None:
        self.saved.append(state)


class RecordingPositionStore:
    def __init__(self, existing_positions: list[PositionRecord] | None = None) -> None:
        self.existing_positions = list(existing_positions or [])
        self.calls: list[dict[str, object]] = []

    def replace_all(
        self,
        *,
        positions: list[PositionRecord],
        trading_mode: TradingMode,
        strategy_version: str,
        commit: bool = True,
    ) -> None:
        self.calls.append(
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
        for order in reversed(self.saved):
            if order.client_order_id == client_order_id:
                return order
        for order in self.existing_orders:
            if order.client_order_id == client_order_id:
                return order
        return None


class RecordingAuditEventStore:
    def __init__(self) -> None:
        self.appended: list[AuditEvent] = []

    def append(self, event: AuditEvent, *, commit: bool = True) -> None:
        self.appended.append(event)


class FakeBroker:
    def __init__(
        self,
        *,
        clock: MarketClock,
        calendar: list[MarketCalendarDay],
        open_orders: list[BrokerOrder],
        open_positions: list[BrokerPosition],
    ) -> None:
        self._clock = clock
        self._calendar = list(calendar)
        self._open_orders = list(open_orders)
        self._open_positions = list(open_positions)

    def get_market_clock(self) -> MarketClock:
        return self._clock

    def get_calendar(self, *, start: date, end: date) -> list[MarketCalendarDay]:
        assert start == date(2026, 4, 24)
        assert end == date(2026, 4, 24)
        return list(self._calendar)

    def list_open_orders(self) -> list[BrokerOrder]:
        return list(self._open_orders)

    def list_open_positions(self) -> list[BrokerPosition]:
        return list(self._open_positions)


@dataclass
class BootstrapStub:
    runtime: RuntimeContext
    calls: int = 0

    def __call__(self, settings: Settings) -> RuntimeContext:
        self.calls += 1
        assert settings == self.runtime.settings
        return self.runtime


def make_runtime_context(
    settings: Settings,
    *,
    daily_session_state_store: RecordingDailySessionStateStore,
    position_store: RecordingPositionStore,
    order_store: RecordingOrderStore,
    audit_event_store: RecordingAuditEventStore,
) -> RuntimeContext:
    return RuntimeContext(
        settings=settings,
        connection=_make_healthy_connection_stub(),  # type: ignore[arg-type]
        lock=object(),  # type: ignore[arg-type]
        trading_status_store=RecordingTradingStatusStore(),  # type: ignore[arg-type]
        audit_event_store=audit_event_store,  # type: ignore[arg-type]
        order_store=order_store,  # type: ignore[arg-type]
        position_store=position_store,  # type: ignore[arg-type]
        daily_session_state_store=daily_session_state_store,  # type: ignore[arg-type]
    )


def test_main_runs_trader_startup_and_persists_positions_with_summary() -> None:
    main = load_cli_main()
    settings = make_settings()
    now = datetime(2026, 4, 24, 14, 5, tzinfo=timezone.utc)
    daily_session_state_store = RecordingDailySessionStateStore()
    position_store = RecordingPositionStore()
    order_store = RecordingOrderStore()
    audit_event_store = RecordingAuditEventStore()
    runtime = make_runtime_context(
        settings,
        daily_session_state_store=daily_session_state_store,
        position_store=position_store,
        order_store=order_store,
        audit_event_store=audit_event_store,
    )
    broker = FakeBroker(
        clock=MarketClock(
            timestamp=now,
            is_open=True,
            next_open=datetime(2026, 4, 27, 13, 30, tzinfo=timezone.utc),
            next_close=datetime(2026, 4, 24, 20, 0, tzinfo=timezone.utc),
        ),
        calendar=[
            MarketCalendarDay(
                session_date=date(2026, 4, 24),
                open_at=datetime(2026, 4, 24, 13, 30, tzinfo=timezone.utc),
                close_at=datetime(2026, 4, 24, 20, 0, tzinfo=timezone.utc),
            )
        ],
        open_orders=[
            BrokerOrder(
                client_order_id="entry-1",
                broker_order_id="alpaca-1",
                symbol="AAPL",
                side="buy",
                status="new",
                quantity=10,
            )
        ],
        open_positions=[
            BrokerPosition(symbol="AAPL", quantity=10, entry_price=189.25, market_value=1892.5),
            BrokerPosition(symbol="MSFT", quantity=5, entry_price=421.10, market_value=2105.5),
        ],
    )
    stdout = io.StringIO()

    exit_code = main(
        [],
        settings=settings,
        bootstrap=BootstrapStub(runtime),
        broker_factory=lambda *_args, **_kwargs: broker,
        now=lambda: now,
        stdout=stdout,
    )

    assert exit_code == 0
    assert position_store.calls == [
        {
            "positions": [
                PositionRecord(
                    symbol="AAPL",
                    trading_mode=TradingMode.PAPER,
                    strategy_version="v1-breakout",
                    quantity=10,
                    entry_price=189.25,
                    stop_price=round(189.25 * (1 - 0.001), 2),
                    initial_stop_price=round(189.25 * (1 - 0.001), 2),
                    opened_at=now,
                    updated_at=now,
                ),
                PositionRecord(
                    symbol="MSFT",
                    trading_mode=TradingMode.PAPER,
                    strategy_version="v1-breakout",
                    quantity=5,
                    entry_price=421.10,
                    stop_price=round(421.10 * (1 - 0.001), 2),
                    initial_stop_price=round(421.10 * (1 - 0.001), 2),
                    opened_at=now,
                    updated_at=now,
                ),
            ],
            "trading_mode": TradingMode.PAPER,
            "strategy_version": "v1-breakout",
        }
    ]
    assert audit_event_store.appended[-1] == AuditEvent(
        event_type="trader_startup_completed",
        payload={
            "trading_mode": "paper",
            "strategy_version": "v1-breakout",
            "effective_status": "halted",
            "open_order_count": 1,
            "open_position_count": 2,
            "mismatch_detected": True,
            "session_date": "2026-04-24",
        },
        created_at=now,
    )
    # C-2 fix: startup recovery now queues pending_submit stops for brand-new positions
    aapl_stop_saves = [r for r in order_store.saved if r.symbol == "AAPL" and r.intent_type == "stop"]
    msft_stop_saves = [r for r in order_store.saved if r.symbol == "MSFT" and r.intent_type == "stop"]
    entry_saves = [r for r in order_store.saved if r.client_order_id == "entry-1"]
    assert len(aapl_stop_saves) == 1
    assert aapl_stop_saves[0].status == "pending_submit"
    assert aapl_stop_saves[0].stop_price == round(189.25 * (1 - 0.001), 2)
    assert len(msft_stop_saves) == 1
    assert msft_stop_saves[0].status == "pending_submit"
    assert msft_stop_saves[0].stop_price == round(421.10 * (1 - 0.001), 2)
    assert len(entry_saves) == 1
    assert entry_saves[0] == OrderRecord(
        client_order_id="entry-1",
        symbol="AAPL",
        side="buy",
        intent_type="entry",
        status="new",
        quantity=10,
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        created_at=now,
        updated_at=now,
        broker_order_id="alpaca-1",
    )
    assert json.loads(stdout.getvalue()) == {
        "effective_status": "halted",
        "open_order_count": 1,
        "open_position_count": 2,
        "mismatch_detected": True,
        "session_date": "2026-04-24",
    }


def test_main_renders_mismatch_summary_from_startup_reconciliation() -> None:
    main = load_cli_main()
    settings = make_settings()
    now = datetime(2026, 4, 24, 20, 5, tzinfo=timezone.utc)
    daily_session_state_store = RecordingDailySessionStateStore(
        loaded_state=DailySessionState(
            session_date=date(2026, 4, 24),
            trading_mode=TradingMode.PAPER,
            strategy_version="v1-breakout",
            entries_disabled=True,
            flatten_complete=True,
            last_reconciled_at=datetime(2026, 4, 24, 19, 59, tzinfo=timezone.utc),
            notes="flattened",
            updated_at=datetime(2026, 4, 24, 19, 59, tzinfo=timezone.utc),
        )
    )
    position_store = RecordingPositionStore()
    order_store = RecordingOrderStore()
    audit_event_store = RecordingAuditEventStore()
    runtime = make_runtime_context(
        settings,
        daily_session_state_store=daily_session_state_store,
        position_store=position_store,
        order_store=order_store,
        audit_event_store=audit_event_store,
    )
    broker = FakeBroker(
        clock=MarketClock(
            timestamp=now,
            is_open=False,
            next_open=datetime(2026, 4, 27, 13, 30, tzinfo=timezone.utc),
            next_close=datetime(2026, 4, 24, 20, 0, tzinfo=timezone.utc),
        ),
        calendar=[
            MarketCalendarDay(
                session_date=date(2026, 4, 24),
                open_at=datetime(2026, 4, 24, 13, 30, tzinfo=timezone.utc),
                close_at=datetime(2026, 4, 24, 20, 0, tzinfo=timezone.utc),
            )
        ],
        open_orders=[],
        open_positions=[
            BrokerPosition(symbol="AAPL", quantity=10, entry_price=189.25, market_value=1892.5)
        ],
    )
    stdout = io.StringIO()

    exit_code = main(
        [],
        settings=settings,
        bootstrap=BootstrapStub(runtime),
        broker_factory=lambda *_args, **_kwargs: broker,
        now=lambda: now,
        stdout=stdout,
    )

    assert exit_code == 0
    assert json.loads(stdout.getvalue()) == {
        "effective_status": "halted",
        "open_order_count": 0,
        "open_position_count": 1,
        "mismatch_detected": True,
        "session_date": "2026-04-24",
    }
