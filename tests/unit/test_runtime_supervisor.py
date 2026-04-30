from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from importlib import import_module
import threading
from types import SimpleNamespace

import pytest

from alpaca_bot.config import Settings, TradingMode
from alpaca_bot.domain import Bar
from alpaca_bot.execution import BrokerAccount, BrokerOrder, BrokerPosition
from alpaca_bot.runtime import RuntimeContext
from alpaca_bot.runtime.reconcile import ReconciliationOutcome, SessionSnapshot
from alpaca_bot.runtime.trader import TraderStartupReport, TraderStartupStatus
from alpaca_bot.storage import (
    AuditEvent,
    DailySessionState,
    OrderRecord,
    PositionRecord,
    TradingStatus,
    TradingStatusValue,
)


def load_supervisor_api():
    try:
        module = import_module("alpaca_bot.runtime.supervisor")
    except ModuleNotFoundError as exc:
        pytest.fail(f"Expected runtime supervisor module to exist: {exc}")
    return module, module.RuntimeSupervisor, module.SupervisorCycleReport


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
    def __init__(self, loaded_status: TradingStatus | None = None) -> None:
        self.loaded_status = loaded_status
        self.load_calls: list[tuple[TradingMode, str]] = []

    def load(self, *, trading_mode: TradingMode, strategy_version: str) -> TradingStatus | None:
        self.load_calls.append((trading_mode, strategy_version))
        return self.loaded_status


class RecordingDailySessionStateStore:
    def __init__(self) -> None:
        self.saved: list[DailySessionState] = []

    def load(
        self,
        *,
        session_date: date,
        trading_mode: TradingMode,
        strategy_version: str,
        strategy_name: str = "breakout",
    ) -> DailySessionState | None:
        del session_date, trading_mode, strategy_version, strategy_name
        return None

    def save(self, state: DailySessionState) -> None:
        self.saved.append(state)


class RecordingPositionStore:
    def __init__(self, existing_positions: list[PositionRecord] | None = None) -> None:
        self.existing_positions = list(existing_positions or [])
        self.replace_all_calls: list[dict[str, object]] = []
        self.list_all_calls: list[tuple[TradingMode, str]] = []

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
        self.list_all_calls.append((trading_mode, strategy_version))
        return list(self.existing_positions)


class RecordingAuditEventStore:
    def __init__(self) -> None:
        self.appended: list[object] = []

    def append(self, event: object, *, commit: bool = True) -> None:
        self.appended.append(event)


class RecordingOrderStore:
    def __init__(
        self,
        existing_orders: list[OrderRecord] | None = None,
        *,
        daily_pnl: float = 0.0,
    ) -> None:
        self.existing_orders = list(existing_orders or [])
        self.saved: list[object] = []
        self.list_by_status_calls: list[dict[str, object]] = []
        self._daily_pnl = daily_pnl

    def save(self, order: object, *, commit: bool = True) -> None:
        self.saved.append(order)

    def list_by_status(
        self,
        *,
        trading_mode: TradingMode,
        strategy_version: str,
        statuses: list[str],
        strategy_name: str | None = None,
    ) -> list[OrderRecord]:
        self.list_by_status_calls.append(
            {
                "trading_mode": trading_mode,
                "strategy_version": strategy_version,
                "statuses": statuses,
            }
        )
        return [
            order
            for order in self.existing_orders
            if order.trading_mode is trading_mode
            and order.strategy_version == strategy_version
            and order.status in statuses
        ]

    def list_pending_submit(
        self,
        *,
        trading_mode: TradingMode,
        strategy_version: str,
    ) -> list[OrderRecord]:
        return self.list_by_status(
            trading_mode=trading_mode,
            strategy_version=strategy_version,
            statuses=["pending_submit"],
        )

    def daily_realized_pnl(
        self,
        *,
        trading_mode: TradingMode,
        strategy_version: str,
        session_date: date,
        market_timezone: str = "America/New_York",
    ) -> float:
        return self._daily_pnl


def make_runtime_context(
    settings: Settings,
    *,
    trading_status_store: RecordingTradingStatusStore | None = None,
    position_store: RecordingPositionStore | None = None,
    order_store: RecordingOrderStore | None = None,
    daily_session_state_store: RecordingDailySessionStateStore | None = None,
) -> RuntimeContext:
    class _FakeConn:
        def commit(self) -> None:
            pass

    return RuntimeContext(
        settings=settings,
        connection=_FakeConn(),  # type: ignore[arg-type]
        lock=object(),  # type: ignore[arg-type]
        trading_status_store=trading_status_store or RecordingTradingStatusStore(),  # type: ignore[arg-type]
        audit_event_store=RecordingAuditEventStore(),  # type: ignore[arg-type]
        order_store=order_store or RecordingOrderStore(),  # type: ignore[arg-type]
        position_store=position_store or RecordingPositionStore(),  # type: ignore[arg-type]
        daily_session_state_store=daily_session_state_store or RecordingDailySessionStateStore(),  # type: ignore[arg-type]
    )


class FakeBroker:
    def __init__(
        self,
        *,
        account: BrokerAccount | None = None,
        open_orders: list[BrokerOrder] | None = None,
        open_positions: list[BrokerPosition] | None = None,
        market_is_open: bool = True,
    ) -> None:
        self.account = account or BrokerAccount(
            equity=100_000.0,
            buying_power=200_000.0,
            trading_blocked=False,
        )
        self.open_orders = list(open_orders or [])
        self.open_positions = list(open_positions or [])
        self.market_is_open = market_is_open
        self.account_calls = 0
        self.open_order_calls = 0
        self.open_position_calls = 0
        self.clock_calls = 0

    def get_account(self) -> BrokerAccount:
        self.account_calls += 1
        return self.account

    def list_open_orders(self) -> list[BrokerOrder]:
        self.open_order_calls += 1
        return list(self.open_orders)

    def list_open_positions(self) -> list[BrokerPosition]:
        self.open_position_calls += 1
        return list(self.open_positions)

    def get_clock(self):
        self.clock_calls += 1
        return SimpleNamespace(
            timestamp=datetime(2026, 4, 24, 19, 0, tzinfo=timezone.utc),
            is_open=self.market_is_open,
            next_open=datetime(2026, 4, 25, 13, 30, tzinfo=timezone.utc),
            next_close=datetime(2026, 4, 24, 20, 0, tzinfo=timezone.utc),
        )

    def get_calendar(self, *, start: date, end: date):
        del start, end
        return [
            SimpleNamespace(
                date=date(2026, 4, 24),
                open=datetime(2026, 4, 24, 13, 30, tzinfo=timezone.utc),
                close=datetime(2026, 4, 24, 20, 0, tzinfo=timezone.utc),
            )
        ]


class FakeMarketData:
    def __init__(
        self,
        *,
        intraday_bars_by_symbol: dict[str, list[Bar]],
        daily_bars_by_symbol: dict[str, list[Bar]],
    ) -> None:
        self.intraday_bars_by_symbol = intraday_bars_by_symbol
        self.daily_bars_by_symbol = daily_bars_by_symbol
        self.stock_bar_calls: list[dict[str, object]] = []
        self.daily_bar_calls: list[dict[str, object]] = []

    def get_stock_bars(
        self,
        *,
        symbols: list[str],
        start: datetime,
        end: datetime,
        timeframe_minutes: int,
    ) -> dict[str, list[Bar]]:
        self.stock_bar_calls.append(
            {
                "symbols": symbols,
                "start": start,
                "end": end,
                "timeframe_minutes": timeframe_minutes,
            }
        )
        return self.intraday_bars_by_symbol

    def get_daily_bars(
        self,
        *,
        symbols: list[str],
        start: datetime,
        end: datetime,
    ) -> dict[str, list[Bar]]:
        self.daily_bar_calls.append(
            {
                "symbols": symbols,
                "start": start,
                "end": end,
            }
        )
        return self.daily_bars_by_symbol


class FakeStream:
    def __init__(self, *, raise_on_run: Exception | None = None, block_until_stop: bool = False) -> None:
        self.handlers: list[object] = []
        self.run_calls = 0
        self.stop_calls = 0
        self.run_started = threading.Event()
        self._stop_requested = threading.Event()
        self._raise_on_run = raise_on_run
        self._block_until_stop = block_until_stop

    def subscribe_trade_updates(self, handler) -> None:
        self.handlers.append(handler)

    def run(self) -> None:
        self.run_calls += 1
        self.run_started.set()
        if self._raise_on_run is not None:
            raise self._raise_on_run
        if self._block_until_stop:
            self._stop_requested.wait(timeout=1.0)

    def stop(self) -> None:
        self.stop_calls += 1
        self._stop_requested.set()


@dataclass(frozen=True)
class FromSettingsCall:
    settings: Settings


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


def make_bar_series(symbol: str, *, end: datetime, count: int, days: bool = False) -> list[Bar]:
    step = timedelta(days=1) if days else timedelta(minutes=15)
    start = end - (step * (count - 1))
    bars: list[Bar] = []
    for index in range(count):
        timestamp = start + (step * index)
        price = 100.0 + index
        bars.append(
            Bar(
                symbol=symbol,
                timestamp=timestamp,
                open=price,
                high=price + 1.0,
                low=price - 1.0,
                close=price + 0.5,
                volume=1_000 + index,
            )
        )
    return bars


def make_trading_status(
    settings: Settings,
    *,
    status: TradingStatusValue,
    updated_at: datetime,
) -> TradingStatus:
    return TradingStatus(
        trading_mode=settings.trading_mode,
        strategy_version=settings.strategy_version,
        status=status,
        kill_switch_enabled=status is TradingStatusValue.HALTED,
        status_reason="manual intervention" if status is TradingStatusValue.HALTED else None,
        updated_at=updated_at,
    )


def test_runtime_supervisor_from_settings_bootstraps_runtime_and_builds_adapters(monkeypatch) -> None:
    module, RuntimeSupervisor, _SupervisorCycleReport = load_supervisor_api()
    settings = make_settings()
    runtime = make_runtime_context(settings)
    broker = FakeBroker()
    market_data = FakeMarketData(intraday_bars_by_symbol={}, daily_bars_by_symbol={})
    stream = FakeStream()
    calls: dict[str, list[FromSettingsCall]] = {
        "bootstrap": [],
        "broker": [],
        "market_data": [],
        "stream": [],
    }

    monkeypatch.setattr(
        module,
        "bootstrap_runtime",
        lambda resolved_settings: calls["bootstrap"].append(FromSettingsCall(resolved_settings))
        or runtime,
    )
    monkeypatch.setattr(
        module.AlpacaBroker,
        "from_settings",
        lambda resolved_settings: calls["broker"].append(FromSettingsCall(resolved_settings))
        or broker,
    )
    monkeypatch.setattr(
        module.AlpacaMarketDataAdapter,
        "from_settings",
        lambda resolved_settings: calls["market_data"].append(FromSettingsCall(resolved_settings))
        or market_data,
    )
    monkeypatch.setattr(
        module.AlpacaTradingStreamAdapter,
        "from_settings",
        lambda resolved_settings: calls["stream"].append(FromSettingsCall(resolved_settings))
        or stream,
    )

    supervisor = RuntimeSupervisor.from_settings(settings)

    assert supervisor.settings == settings
    assert supervisor.runtime is runtime
    assert supervisor.broker is broker
    assert supervisor.market_data is market_data
    assert supervisor.stream is stream
    assert calls == {
        "bootstrap": [FromSettingsCall(settings)],
        "broker": [FromSettingsCall(settings)],
        "market_data": [FromSettingsCall(settings)],
        "stream": [FromSettingsCall(settings)],
    }


def test_runtime_supervisor_startup_runs_reconciliation_syncs_positions_and_attaches_stream(
    monkeypatch,
) -> None:
    module, RuntimeSupervisor, _SupervisorCycleReport = load_supervisor_api()
    settings = make_settings()
    now = datetime(2026, 4, 24, 14, 5, tzinfo=timezone.utc)
    runtime = make_runtime_context(settings, position_store=RecordingPositionStore())
    startup_report = make_startup_report()
    broker = FakeBroker(
        open_positions=[
            BrokerPosition(symbol="AAPL", quantity=10, entry_price=189.25, market_value=1892.5),
            BrokerPosition(symbol="MSFT", quantity=5, entry_price=421.10, market_value=2105.5),
        ]
    )
    market_data = FakeMarketData(intraday_bars_by_symbol={}, daily_bars_by_symbol={})
    stream = FakeStream()
    start_calls: list[dict[str, object]] = []
    attach_calls: list[dict[str, object]] = []

    def fake_start_trader(
        resolved_settings: Settings,
        *,
        broker_client: object,
        bootstrap,
        mismatch_detector=None,
        now=None,
    ) -> TraderStartupReport:
        start_calls.append(
            {
                "settings": resolved_settings,
                "broker_client": broker_client,
                "runtime": bootstrap(resolved_settings),
                "mismatch_detector": mismatch_detector,
                "now": now() if callable(now) else now,
            }
        )
        return startup_report

    def fake_attach_trade_update_stream(**kwargs):
        attach_calls.append(kwargs)
        return object()

    monkeypatch.setattr(module, "start_trader", fake_start_trader)
    monkeypatch.setattr(module, "attach_trade_update_stream", fake_attach_trade_update_stream)

    supervisor = RuntimeSupervisor(
        settings=settings,
        runtime=runtime,
        broker=broker,
        market_data=market_data,
        stream=stream,
        close_runtime_fn=lambda _runtime: None,
        connection_checker=lambda _conn: True,
    )

    report = supervisor.startup(now=lambda: now)

    assert report is startup_report
    assert callable(start_calls[0]["mismatch_detector"])
    assert start_calls[0]["mismatch_detector"](runtime, startup_report.session) == (
        "broker position missing locally: AAPL",
        "broker position missing locally: MSFT",
    )
    assert start_calls == [
        {
            "settings": settings,
            "broker_client": broker,
            "runtime": runtime,
            "mismatch_detector": start_calls[0]["mismatch_detector"],
            "now": now,
        }
    ]
    assert runtime.position_store.replace_all_calls == [
        {
            "positions": [
                PositionRecord(
                    symbol="AAPL",
                    trading_mode=TradingMode.PAPER,
                    strategy_version="v1-breakout",
                    quantity=10,
                    entry_price=189.25,
                    stop_price=189.06075,
                    initial_stop_price=189.06075,
                    opened_at=now,
                    updated_at=now,
                ),
                PositionRecord(
                    symbol="MSFT",
                    trading_mode=TradingMode.PAPER,
                    strategy_version="v1-breakout",
                    quantity=5,
                    entry_price=421.10,
                    stop_price=420.6789,
                    initial_stop_price=420.6789,
                    opened_at=now,
                    updated_at=now,
                ),
            ],
            "trading_mode": TradingMode.PAPER,
            "strategy_version": "v1-breakout",
        }
    ]
    assert len(attach_calls) == 1
    assert attach_calls[0]["settings"] == settings
    assert attach_calls[0]["runtime"] is runtime
    assert attach_calls[0]["stream"] is stream
    assert callable(attach_calls[0]["now"])
    assert attach_calls[0]["now"]() == now
    assert stream.run_started.wait(timeout=1.0)
    assert stream.run_calls == 1
    completed_events = [
        e for e in runtime.audit_event_store.appended
        if e.event_type == "startup_recovery_completed"
    ]
    assert len(completed_events) == 1
    assert completed_events[0] == AuditEvent(
        event_type="startup_recovery_completed",
        payload={
            "mismatch_count": 2,
            "mismatches": [
                "broker position missing locally: AAPL",
                "broker position missing locally: MSFT",
            ],
            "synced_position_count": 2,
            "synced_order_count": 0,
            "cleared_position_count": 0,
            "cleared_order_count": 0,
        },
        created_at=now,
    )
    assert any(
        event.event_type == "trade_update_stream_started"
        for event in runtime.audit_event_store.appended
    )
    assert broker.open_order_calls == 1
    assert broker.open_position_calls == 1


def test_runtime_supervisor_close_stops_background_trade_update_stream() -> None:
    module, RuntimeSupervisor, _SupervisorCycleReport = load_supervisor_api()
    settings = make_settings()
    now = datetime(2026, 4, 24, 14, 5, tzinfo=timezone.utc)
    runtime = make_runtime_context(settings)
    broker = FakeBroker()
    market_data = FakeMarketData(intraday_bars_by_symbol={}, daily_bars_by_symbol={})
    stream = FakeStream(block_until_stop=True)

    supervisor = RuntimeSupervisor(
        settings=settings,
        runtime=runtime,
        broker=broker,
        market_data=market_data,
        stream=stream,
        close_runtime_fn=lambda _runtime: None,
        connection_checker=lambda _conn: True,
    )

    supervisor.startup(now=lambda: now)
    assert stream.run_started.wait(timeout=1.0)

    supervisor.close()

    assert stream.run_calls == 1
    assert stream.stop_calls == 1
    assert supervisor._closed is True


def test_runtime_supervisor_audits_trade_update_stream_failures() -> None:
    module, RuntimeSupervisor, _SupervisorCycleReport = load_supervisor_api()
    settings = make_settings()
    now = datetime(2026, 4, 24, 14, 5, tzinfo=timezone.utc)
    runtime = make_runtime_context(settings)
    broker = FakeBroker()
    market_data = FakeMarketData(intraday_bars_by_symbol={}, daily_bars_by_symbol={})
    stream = FakeStream(raise_on_run=RuntimeError("stream boom"))

    supervisor = RuntimeSupervisor(
        settings=settings,
        runtime=runtime,
        broker=broker,
        market_data=market_data,
        stream=stream,
        close_runtime_fn=lambda _runtime: None,
        connection_checker=lambda _conn: True,
    )

    supervisor.startup(now=lambda: now)
    assert stream.run_started.wait(timeout=1.0)

    for _ in range(100):
        if any(
            event.event_type == "trade_update_stream_failed"
            for event in runtime.audit_event_store.appended
        ):
            break
    else:
        pytest.fail("Expected trade_update_stream_failed audit event")

    assert any(
        event.event_type == "trade_update_stream_failed"
        and event.payload["error"] == "stream boom"
        for event in runtime.audit_event_store.appended
    )


def test_runtime_supervisor_run_cycle_once_gathers_runtime_inputs_and_dispatches_orders(
    monkeypatch,
) -> None:
    module, RuntimeSupervisor, SupervisorCycleReport = load_supervisor_api()
    settings = make_settings()
    now = datetime(2026, 4, 24, 19, 0, tzinfo=timezone.utc)
    runtime = make_runtime_context(
        settings,
        position_store=RecordingPositionStore(
            [
                PositionRecord(
                    symbol="AAPL",
                    trading_mode=TradingMode.PAPER,
                    strategy_version="v1-breakout",
                    quantity=25,
                    entry_price=111.02,
                    stop_price=109.89,
                    initial_stop_price=109.89,
                    opened_at=datetime(2026, 4, 24, 18, 45, tzinfo=timezone.utc),
                    updated_at=datetime(2026, 4, 24, 18, 45, tzinfo=timezone.utc),
                )
            ]
        ),
        order_store=RecordingOrderStore(
            [
                OrderRecord(
                    client_order_id="entry-1",
                    symbol="MSFT",
                    side="buy",
                    intent_type="entry",
                    status="new",
                    quantity=10,
                    trading_mode=TradingMode.PAPER,
                    strategy_version="v1-breakout",
                    created_at=datetime(2026, 4, 24, 18, 50, tzinfo=timezone.utc),
                    updated_at=datetime(2026, 4, 24, 18, 50, tzinfo=timezone.utc),
                    broker_order_id="alpaca-1",
                )
            ]
        ),
    )
    broker = FakeBroker(
        account=BrokerAccount(equity=123_456.78, buying_power=250_000.0, trading_blocked=False),
        open_orders=[
            BrokerOrder(
                client_order_id="entry-1",
                broker_order_id="alpaca-1",
                symbol="MSFT",
                side="buy",
                status="new",
                quantity=10,
            )
        ],
        open_positions=[
            BrokerPosition(symbol="AAPL", quantity=25, entry_price=111.02, market_value=2775.5)
        ],
    )
    market_data = FakeMarketData(
        intraday_bars_by_symbol={
            "AAPL": make_bar_series("AAPL", end=now, count=21),
            "MSFT": make_bar_series("MSFT", end=now, count=21),
            "SPY": make_bar_series("SPY", end=now, count=21),
        },
        daily_bars_by_symbol={
            "AAPL": make_bar_series("AAPL", end=now, count=20, days=True),
            "MSFT": make_bar_series("MSFT", end=now, count=20, days=True),
            "SPY": make_bar_series("SPY", end=now, count=20, days=True),
        },
    )
    stream = FakeStream()
    cycle_result = SimpleNamespace(intents=["entry"])
    dispatch_report = {"submitted_count": 1}
    cycle_calls: list[dict[str, object]] = []
    dispatch_calls: list[dict[str, object]] = []

    def fake_run_cycle(**kwargs):
        cycle_calls.append(kwargs)
        return cycle_result

    def fake_dispatch_pending_orders(**kwargs):
        dispatch_calls.append(kwargs)
        return dispatch_report

    monkeypatch.setattr(module, "run_cycle", fake_run_cycle)
    monkeypatch.setattr(module, "dispatch_pending_orders", fake_dispatch_pending_orders)

    supervisor = RuntimeSupervisor(
        settings=settings,
        runtime=runtime,
        broker=broker,
        market_data=market_data,
        stream=stream,
        close_runtime_fn=lambda _runtime: None,
        connection_checker=lambda _conn: True,
    )

    report = supervisor.run_cycle_once(now=lambda: now)

    assert isinstance(report, SupervisorCycleReport)
    assert report.entries_disabled is False
    assert report.cycle_result is cycle_result
    assert report.dispatch_report is dispatch_report
    assert broker.account_calls == 1
    assert broker.open_order_calls == 1
    assert broker.open_position_calls == 1
    assert runtime.position_store.list_all_calls == [
        (TradingMode.PAPER, "v1-breakout"),
        (TradingMode.PAPER, "v1-breakout"),
    ]
    assert tuple(market_data.stock_bar_calls[0]["symbols"]) == ("AAPL", "MSFT", "SPY")
    assert market_data.stock_bar_calls[0]["timeframe_minutes"] == 15
    assert tuple(market_data.daily_bar_calls[0]["symbols"]) == ("AAPL", "MSFT", "SPY")
    from alpaca_bot.strategy import STRATEGY_REGISTRY
    assert len(cycle_calls) == len(STRATEGY_REGISTRY)
    assert cycle_calls[0]["settings"] == settings
    assert cycle_calls[0]["runtime"] is runtime
    assert cycle_calls[0]["now"] == now
    assert cycle_calls[0]["equity"] == 123_456.78
    assert cycle_calls[0]["intraday_bars_by_symbol"] == market_data.intraday_bars_by_symbol
    assert cycle_calls[0]["daily_bars_by_symbol"] == market_data.daily_bars_by_symbol
    assert cycle_calls[0]["working_order_symbols"] == {"MSFT"}
    assert isinstance(cycle_calls[0]["traded_symbols_today"], set)
    assert cycle_calls[0]["entries_disabled"] is False
    assert len(cycle_calls[0]["open_positions"]) == 1
    assert cycle_calls[0]["open_positions"][0].symbol == "AAPL"
    assert cycle_calls[0]["open_positions"][0].quantity == 25
    assert cycle_calls[0]["open_positions"][0].entry_price == 111.02
    assert cycle_calls[0]["open_positions"][0].stop_price == 109.89
    assert cycle_calls[0]["open_positions"][0].initial_stop_price == 109.89
    assert dispatch_calls == [
        {
            "settings": settings,
            "runtime": runtime,
            "broker": broker,
            "now": now,
            "blocked_strategy_names": set(),
            "notifier": None,
            "session_type": None,
        }
    ]


def test_runtime_supervisor_run_cycle_once_disables_entries_when_runtime_reconciliation_finds_mismatch(
    monkeypatch,
) -> None:
    module, RuntimeSupervisor, SupervisorCycleReport = load_supervisor_api()
    settings = make_settings()
    now = datetime(2026, 4, 24, 19, 5, tzinfo=timezone.utc)
    runtime = make_runtime_context(
        settings,
        order_store=RecordingOrderStore(
            [
                # An order that WAS submitted to the broker (has a broker_order_id) but is
                # now absent from broker open orders — this is a real mismatch that should
                # disable entries. (pending_submit orders without broker_order_id are NOT
                # mismatches — they were queued locally and never sent to the broker.)
                OrderRecord(
                    client_order_id="v1-breakout:2026-04-24:AAPL:entry:2026-04-24T19:00:00+00:00",
                    symbol="AAPL",
                    side="buy",
                    intent_type="entry",
                    status="accepted",
                    quantity=10,
                    trading_mode=TradingMode.PAPER,
                    strategy_version="v1-breakout",
                    created_at=datetime(2026, 4, 24, 19, 0, tzinfo=timezone.utc),
                    updated_at=datetime(2026, 4, 24, 19, 0, tzinfo=timezone.utc),
                    stop_price=111.01,
                    limit_price=111.12,
                    initial_stop_price=109.89,
                    broker_order_id="broker-entry-accepted-missing",
                )
            ]
        ),
        position_store=RecordingPositionStore(),
    )
    broker = FakeBroker()
    market_data = FakeMarketData(
        intraday_bars_by_symbol={"AAPL": make_bar_series("AAPL", end=now, count=21)},
        daily_bars_by_symbol={"AAPL": make_bar_series("AAPL", end=now, count=20, days=True)},
    )
    stream = FakeStream()
    cycle_calls: list[dict[str, object]] = []
    dispatch_calls: list[dict[str, object]] = []

    monkeypatch.setattr(
        module,
        "run_cycle",
        lambda **kwargs: cycle_calls.append(kwargs) or SimpleNamespace(intents=[]),
    )
    monkeypatch.setattr(
        module,
        "dispatch_pending_orders",
        lambda **kwargs: dispatch_calls.append(kwargs) or {"submitted_count": 0},
    )

    supervisor = RuntimeSupervisor(
        settings=settings,
        runtime=runtime,
        broker=broker,
        market_data=market_data,
        stream=stream,
        close_runtime_fn=lambda _runtime: None,
        connection_checker=lambda _conn: True,
    )

    report = supervisor.run_cycle_once(now=lambda: now)

    assert isinstance(report, SupervisorCycleReport)
    assert report.entries_disabled is True
    assert cycle_calls[0]["entries_disabled"] is True
    assert dispatch_calls == [
        {
            "settings": settings,
            "runtime": runtime,
            "broker": broker,
            "now": now,
            "allowed_intent_types": {"stop", "exit"},
            "blocked_strategy_names": set(import_module("alpaca_bot.strategy").STRATEGY_REGISTRY.keys()),
            "notifier": None,
            "session_type": None,
        }
    ]
    assert runtime.order_store.saved[-1] == OrderRecord(
        client_order_id="v1-breakout:2026-04-24:AAPL:entry:2026-04-24T19:00:00+00:00",
        symbol="AAPL",
        side="buy",
        intent_type="entry",
        status="reconciled_missing",
        quantity=10,
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        created_at=datetime(2026, 4, 24, 19, 0, tzinfo=timezone.utc),
        updated_at=now,
        stop_price=111.01,
        limit_price=111.12,
        initial_stop_price=109.89,
        broker_order_id="broker-entry-accepted-missing",
    )
    assert runtime.audit_event_store.appended[-1] == AuditEvent(
        event_type="runtime_reconciliation_detected",
        payload={
            "mismatch_count": 1,
            "mismatches": [
                "local order missing at broker: v1-breakout:2026-04-24:AAPL:entry:2026-04-24T19:00:00+00:00",
            ],
            "synced_position_count": 0,
            "synced_order_count": 0,
            "cleared_position_count": 0,
            "cleared_order_count": 1,
            "timestamp": "2026-04-24T19:05:00+00:00",
        },
        created_at=now,
    )


@pytest.mark.parametrize(
    ("status", "expected_entries_disabled"),
    [
        (TradingStatusValue.ENABLED, False),
        (TradingStatusValue.CLOSE_ONLY, True),
        (TradingStatusValue.HALTED, True),
    ],
)
def test_runtime_supervisor_run_cycle_once_respects_trading_status_for_entries_disabled(
    monkeypatch,
    status: TradingStatusValue,
    expected_entries_disabled: bool,
) -> None:
    module, RuntimeSupervisor, _SupervisorCycleReport = load_supervisor_api()
    settings = make_settings()
    now = datetime(2026, 4, 24, 19, 15, tzinfo=timezone.utc)
    trading_status_store = RecordingTradingStatusStore(
        loaded_status=make_trading_status(settings, status=status, updated_at=now)
    )
    runtime = make_runtime_context(
        settings,
        trading_status_store=trading_status_store,
        position_store=RecordingPositionStore(),
    )
    broker = FakeBroker()
    market_data = FakeMarketData(
        intraday_bars_by_symbol={"AAPL": make_bar_series("AAPL", end=now, count=21)},
        daily_bars_by_symbol={"AAPL": make_bar_series("AAPL", end=now, count=20, days=True)},
    )
    stream = FakeStream()
    cycle_calls: list[dict[str, object]] = []

    monkeypatch.setattr(
        module,
        "run_cycle",
        lambda **kwargs: cycle_calls.append(kwargs) or SimpleNamespace(intents=[]),
    )
    monkeypatch.setattr(
        module,
        "dispatch_pending_orders",
        lambda **kwargs: {"submitted_count": 0},
    )

    supervisor = RuntimeSupervisor(
        settings=settings,
        runtime=runtime,
        broker=broker,
        market_data=market_data,
        stream=stream,
        close_runtime_fn=lambda _runtime: None,
        connection_checker=lambda _conn: True,
    )

    supervisor.run_cycle_once(now=lambda: now)

    assert trading_status_store.load_calls == [(TradingMode.PAPER, "v1-breakout")]
    assert cycle_calls[0]["entries_disabled"] is expected_entries_disabled


def test_runtime_supervisor_run_forever_starts_once_loops_until_stop_and_sleeps(
    monkeypatch,
) -> None:
    module, RuntimeSupervisor, SupervisorCycleReport = load_supervisor_api()
    settings = make_settings()
    runtime = make_runtime_context(settings)
    broker = FakeBroker()
    market_data = FakeMarketData(intraday_bars_by_symbol={}, daily_bars_by_symbol={})
    stream = FakeStream()
    supervisor = RuntimeSupervisor(
        settings=settings,
        runtime=runtime,
        broker=broker,
        market_data=market_data,
        stream=stream,
        close_runtime_fn=lambda _runtime: None,
        connection_checker=lambda _conn: True,
    )

    startup_calls: list[object] = []
    cycle_calls: list[object] = []
    sleep_calls: list[float] = []
    close_calls: list[object] = []
    stop_sequence = iter([False, False, False, True])

    monkeypatch.setattr(
        supervisor,
        "startup",
        lambda **kwargs: startup_calls.append(kwargs) or make_startup_report(),
    )
    monkeypatch.setattr(
        supervisor,
        "run_cycle_once",
        lambda **kwargs: cycle_calls.append(kwargs)
        or SupervisorCycleReport(
            entries_disabled=False,
            cycle_result=SimpleNamespace(intents=[]),
            dispatch_report={"submitted_count": 0},
        ),
    )
    monkeypatch.setattr(supervisor, "close", lambda: close_calls.append(True))

    report = supervisor.run_forever(
        should_stop=lambda: next(stop_sequence),
        sleep_fn=lambda seconds: sleep_calls.append(seconds),
        poll_interval_seconds=12.5,
        cycle_now=lambda: datetime(2026, 4, 24, 14, 30, tzinfo=timezone.utc),
    )

    assert startup_calls == [{}]
    assert len(cycle_calls) == 2
    assert sleep_calls == [12.5]
    assert close_calls == [True]
    assert report.iterations == 2
    assert report.active_iterations == 2
    assert report.idle_iterations == 0


def test_runtime_supervisor_run_forever_uses_time_sleep_by_default(
    monkeypatch,
) -> None:
    # Verify the production default sleeper is time.sleep, not a no-op.
    module, RuntimeSupervisor, SupervisorCycleReport = load_supervisor_api()
    settings = make_settings()
    runtime = make_runtime_context(settings)
    broker = FakeBroker()
    market_data = FakeMarketData(intraday_bars_by_symbol={}, daily_bars_by_symbol={})
    stream = FakeStream()
    supervisor = RuntimeSupervisor(
        settings=settings,
        runtime=runtime,
        broker=broker,
        market_data=market_data,
        stream=stream,
        close_runtime_fn=lambda _runtime: None,
        connection_checker=lambda _conn: True,
    )

    sleep_calls: list[float] = []
    stop_sequence = iter([False, False, True])

    import alpaca_bot.runtime.supervisor as supervisor_module

    monkeypatch.setattr(supervisor_module.time, "sleep", lambda s: sleep_calls.append(s))
    monkeypatch.setattr(
        supervisor,
        "startup",
        lambda **kwargs: make_startup_report(),
    )
    monkeypatch.setattr(
        supervisor,
        "run_cycle_once",
        lambda **kwargs: SupervisorCycleReport(
            entries_disabled=False,
            cycle_result=SimpleNamespace(intents=[]),
            dispatch_report={"submitted_count": 0},
        ),
    )
    monkeypatch.setattr(supervisor, "close", lambda: None)

    supervisor.run_forever(
        should_stop=lambda: next(stop_sequence),
        poll_interval_seconds=7.5,
        # No sleep_fn passed — should default to time.sleep
    )

    assert sleep_calls == [7.5]


def test_runtime_supervisor_run_forever_skips_cycle_when_market_is_closed_and_audits_idle() -> None:
    _module, RuntimeSupervisor, _SupervisorCycleReport = load_supervisor_api()
    settings = make_settings()
    runtime = make_runtime_context(settings)
    broker = FakeBroker(market_is_open=False)
    market_data = FakeMarketData(intraday_bars_by_symbol={}, daily_bars_by_symbol={})
    stream = FakeStream()
    supervisor = RuntimeSupervisor(
        settings=settings,
        runtime=runtime,
        broker=broker,
        market_data=market_data,
        stream=stream,
        close_runtime_fn=lambda _runtime: None,
        connection_checker=lambda _conn: True,
    )

    report = supervisor.run_forever(
        max_iterations=1,
        sleep_fn=lambda _seconds: None,
        startup_now=lambda: datetime(2026, 4, 24, 19, 0, tzinfo=timezone.utc),
        cycle_now=lambda: datetime(2026, 4, 24, 19, 0, tzinfo=timezone.utc),
    )

    assert report.iterations == 1
    assert report.active_iterations == 0
    assert report.idle_iterations == 1
    assert runtime.audit_event_store.appended[0] == AuditEvent(
        event_type="startup_recovery_completed",
        payload={
            "mismatch_count": 0,
            "mismatches": [],
            "synced_position_count": 0,
            "synced_order_count": 0,
            "cleared_position_count": 0,
            "cleared_order_count": 0,
        },
        created_at=datetime(2026, 4, 24, 19, 0, tzinfo=timezone.utc),
    )
    assert runtime.audit_event_store.appended[-1] == AuditEvent(
        event_type="supervisor_idle",
        payload={
            "reason": "market_closed",
            "timestamp": "2026-04-24T19:00:00+00:00",
        },
        created_at=datetime(2026, 4, 24, 19, 0, tzinfo=timezone.utc),
    )


def test_runtime_supervisor_run_forever_runs_cycle_when_market_is_open_and_audits_heartbeat(
    monkeypatch,
) -> None:
    module, RuntimeSupervisor, SupervisorCycleReport = load_supervisor_api()
    settings = make_settings()
    runtime = make_runtime_context(settings)
    broker = FakeBroker(market_is_open=True)
    market_data = FakeMarketData(intraday_bars_by_symbol={}, daily_bars_by_symbol={})
    stream = FakeStream()
    supervisor = RuntimeSupervisor(
        settings=settings,
        runtime=runtime,
        broker=broker,
        market_data=market_data,
        stream=stream,
        close_runtime_fn=lambda _runtime: None,
        connection_checker=lambda _conn: True,
    )

    monkeypatch.setattr(
        supervisor,
        "startup",
        lambda **kwargs: make_startup_report(),
    )
    monkeypatch.setattr(
        supervisor,
        "run_cycle_once",
        lambda **kwargs: SupervisorCycleReport(
            entries_disabled=True,
            cycle_result=SimpleNamespace(intents=[]),
            dispatch_report={"submitted_count": 0},
        ),
    )

    report = supervisor.run_forever(
        max_iterations=1,
        sleep_fn=lambda _seconds: None,
        cycle_now=lambda: datetime(2026, 4, 24, 19, 0, tzinfo=timezone.utc),
    )

    assert report.iterations == 1
    assert report.active_iterations == 1
    assert report.idle_iterations == 0
    assert runtime.audit_event_store.appended[-1].event_type == "supervisor_cycle"
    assert runtime.audit_event_store.appended[-1].payload["entries_disabled"] is True


# ---------------------------------------------------------------------------
# Fix #1: CLOSE_ONLY mode must allow "exit" intents through dispatch
# ---------------------------------------------------------------------------


def test_runtime_supervisor_close_only_includes_exit_in_allowed_intent_types(
    monkeypatch,
) -> None:
    """When trading status is CLOSE_ONLY, dispatch_pending_orders must receive
    both 'stop' AND 'exit' in allowed_intent_types so EOD flatten orders are not
    silently blocked."""
    module, RuntimeSupervisor, SupervisorCycleReport = load_supervisor_api()
    settings = make_settings()
    now = datetime(2026, 4, 24, 19, 15, tzinfo=timezone.utc)
    trading_status_store = RecordingTradingStatusStore(
        loaded_status=make_trading_status(
            settings, status=TradingStatusValue.CLOSE_ONLY, updated_at=now
        )
    )
    runtime = make_runtime_context(
        settings,
        trading_status_store=trading_status_store,
        position_store=RecordingPositionStore(),
    )
    broker = FakeBroker()
    market_data = FakeMarketData(
        intraday_bars_by_symbol={"AAPL": make_bar_series("AAPL", end=now, count=21)},
        daily_bars_by_symbol={"AAPL": make_bar_series("AAPL", end=now, count=20, days=True)},
    )
    stream = FakeStream()
    dispatch_calls: list[dict[str, object]] = []

    monkeypatch.setattr(
        module,
        "run_cycle",
        lambda **kwargs: SimpleNamespace(intents=[]),
    )
    monkeypatch.setattr(
        module,
        "dispatch_pending_orders",
        lambda **kwargs: dispatch_calls.append(kwargs) or {"submitted_count": 0},
    )

    supervisor = RuntimeSupervisor(
        settings=settings,
        runtime=runtime,
        broker=broker,
        market_data=market_data,
        stream=stream,
        close_runtime_fn=lambda _runtime: None,
        connection_checker=lambda _conn: True,
    )

    supervisor.run_cycle_once(now=lambda: now)

    assert len(dispatch_calls) == 1
    allowed = dispatch_calls[0]["allowed_intent_types"]
    assert "stop" in allowed, f"'stop' missing from allowed_intent_types: {allowed}"
    assert "exit" in allowed, f"'exit' missing from allowed_intent_types: {allowed}"


# ---------------------------------------------------------------------------
# Fix #4: flatten_complete flag — supervisor writes it after flatten cycle
# ---------------------------------------------------------------------------


def test_supervisor_writes_flatten_complete_after_flatten_cycle(
    monkeypatch,
) -> None:
    """After a cycle that emits EXIT intents past flatten_time, the supervisor
    must write flatten_complete=True to the DailySessionStateStore."""
    from alpaca_bot.core.engine import CycleIntent, CycleIntentType, CycleResult

    module, RuntimeSupervisor, SupervisorCycleReport = load_supervisor_api()
    settings = make_settings()
    # 20:00 UTC = 16:00 ET — past the 15:45 flatten time
    now = datetime(2026, 4, 24, 20, 0, tzinfo=timezone.utc)

    session_state_store = RecordingDailySessionStateStore()
    runtime = make_runtime_context(
        settings,
        position_store=RecordingPositionStore(),
        daily_session_state_store=session_state_store,
    )

    broker = FakeBroker()
    market_data = FakeMarketData(
        intraday_bars_by_symbol={"AAPL": make_bar_series("AAPL", end=now, count=21)},
        daily_bars_by_symbol={"AAPL": make_bar_series("AAPL", end=now, count=20, days=True)},
    )
    stream = FakeStream()

    # Simulate a cycle result that contains an EXIT intent (past flatten_time)
    flatten_intent = CycleIntent(
        intent_type=CycleIntentType.EXIT,
        symbol="AAPL",
        timestamp=now,
        reason="eod_flatten",
    )
    fake_cycle_result = CycleResult(as_of=now, intents=[flatten_intent])

    monkeypatch.setattr(
        module,
        "run_cycle",
        lambda **kwargs: fake_cycle_result,
    )
    monkeypatch.setattr(
        module,
        "dispatch_pending_orders",
        lambda **kwargs: {"submitted_count": 0},
    )

    supervisor = RuntimeSupervisor(
        settings=settings,
        runtime=runtime,
        broker=broker,
        market_data=market_data,
        stream=stream,
        close_runtime_fn=lambda _runtime: None,
        connection_checker=lambda _conn: True,
    )

    supervisor.run_cycle_once(now=lambda: now)

    assert len(session_state_store.saved) >= 1, (
        "Expected DailySessionStateStore.save() to be called after a flatten cycle"
    )
    last_saved = session_state_store.saved[-1]
    assert last_saved.flatten_complete is True, (
        f"Expected flatten_complete=True, got {last_saved.flatten_complete}"
    )
    assert last_saved.session_date == now.astimezone(settings.market_timezone).date()
    assert last_saved.trading_mode == settings.trading_mode
    assert last_saved.strategy_version == settings.strategy_version


def test_flatten_complete_not_set_when_exit_hard_fails(monkeypatch) -> None:
    """flatten_complete must NOT be written when a broker call hard-fails during EOD exit.

    If cancel_order or submit_market_exit raises an unrecognized error, the
    position may still be open. Writing flatten_complete=True would permanently
    prevent subsequent cycles from retrying the exit.
    """
    from alpaca_bot.core.engine import CycleIntent, CycleIntentType, CycleResult
    from alpaca_bot.runtime.cycle_intent_execution import CycleIntentExecutionReport

    module, RuntimeSupervisor, SupervisorCycleReport = load_supervisor_api()
    settings = make_settings()
    now = datetime(2026, 4, 24, 20, 0, tzinfo=timezone.utc)  # past 15:45 ET flatten_time

    session_state_store = RecordingDailySessionStateStore()
    runtime = make_runtime_context(
        settings,
        position_store=RecordingPositionStore(),
        daily_session_state_store=session_state_store,
    )

    flatten_intent = CycleIntent(
        intent_type=CycleIntentType.EXIT,
        symbol="AAPL",
        timestamp=now,
        reason="eod_flatten",
    )
    fake_cycle_result = CycleResult(as_of=now, intents=[flatten_intent])

    monkeypatch.setattr(module, "run_cycle", lambda **kwargs: fake_cycle_result)
    monkeypatch.setattr(module, "dispatch_pending_orders", lambda **kwargs: {"submitted_count": 0})

    def hard_failing_executor(**kwargs) -> CycleIntentExecutionReport:
        # Simulates cancel_order raising an unrecognized broker error.
        return CycleIntentExecutionReport(
            replaced_stop_count=0,
            submitted_stop_count=0,
            submitted_exit_count=0,
            canceled_stop_count=0,
            failed_exit_count=1,
        )

    supervisor = RuntimeSupervisor(
        settings=settings,
        runtime=runtime,
        broker=FakeBroker(),
        market_data=FakeMarketData(
            intraday_bars_by_symbol={"AAPL": make_bar_series("AAPL", end=now, count=21)},
            daily_bars_by_symbol={"AAPL": make_bar_series("AAPL", end=now, count=20, days=True)},
        ),
        stream=FakeStream(),
        cycle_intent_executor=hard_failing_executor,
        close_runtime_fn=lambda _runtime: None,
        connection_checker=lambda _conn: True,
    )

    supervisor.run_cycle_once(now=lambda: now)

    flatten_complete_saves = [
        s for s in session_state_store.saved if getattr(s, "flatten_complete", False)
    ]
    assert len(flatten_complete_saves) == 0, (
        "flatten_complete must not be written when an exit broker call hard-failed; "
        "the position may still be open and the next cycle must retry"
    )


# ---------------------------------------------------------------------------
# Fix #8: Stream restart backoff — emits "stream_restart_failed" after N failures
# ---------------------------------------------------------------------------


def test_supervisor_emits_stream_restart_failed_after_consecutive_failures(
    monkeypatch,
) -> None:
    """After 5 consecutive stream failures, the supervisor must emit a
    'stream_restart_failed' audit event containing the attempt count.

    We drive this by directly setting _stream_restart_attempts to 4 (one below
    the threshold), provide a dead stream thread, and run one cycle — which
    triggers attempt #5 and should emit the alert event.
    """
    module, RuntimeSupervisor, SupervisorCycleReport = load_supervisor_api()
    settings = make_settings()
    runtime = make_runtime_context(settings)
    broker = FakeBroker(market_is_open=True)
    market_data = FakeMarketData(intraday_bars_by_symbol={}, daily_bars_by_symbol={})

    # Stream always raises immediately so every restart is a failure
    stream = FakeStream(raise_on_run=RuntimeError("persistent stream failure"))

    supervisor = RuntimeSupervisor(
        settings=settings,
        runtime=runtime,
        broker=broker,
        market_data=market_data,
        stream=stream,
        close_runtime_fn=lambda _runtime: None,
        connection_checker=lambda _conn: True,
    )

    monkeypatch.setattr(
        supervisor,
        "startup",
        lambda **kwargs: make_startup_report(),
    )
    monkeypatch.setattr(
        supervisor,
        "run_cycle_once",
        lambda **kwargs: SupervisorCycleReport(
            entries_disabled=False,
            cycle_result=SimpleNamespace(intents=[]),
            dispatch_report={"submitted_count": 0},
        ),
    )

    # Pre-set state: 4 prior failures already counted.  A dead stream thread
    # is in place so the watchdog will fire and increment to attempt 5.
    supervisor._stream_restart_attempts = 4
    supervisor._next_stream_restart_at = None  # allow restart immediately

    dead_thread = threading.Thread(target=lambda: None, daemon=True)
    dead_thread.start()
    dead_thread.join()  # ensure it is no longer alive
    supervisor._stream_thread = dead_thread  # watchdog checks is_alive()

    # One iteration is enough: watchdog fires, attempt count becomes 5,
    # stream_restart_failed event is emitted.
    now_ts = datetime(2026, 4, 24, 19, 0, tzinfo=timezone.utc)
    supervisor.run_forever(
        max_iterations=1,
        sleep_fn=lambda _seconds: None,
        cycle_now=lambda: now_ts,
    )

    failure_events = [
        event
        for event in runtime.audit_event_store.appended
        if event.event_type == "stream_restart_failed"
    ]
    assert len(failure_events) >= 1, (
        "Expected at least one 'stream_restart_failed' audit event after 5 "
        f"consecutive stream failures. Got events: "
        f"{[e.event_type for e in runtime.audit_event_store.appended]}"
    )
    assert failure_events[0].payload.get("attempt_count") >= 5, (
        f"Expected attempt_count >= 5, got: {failure_events[0].payload}"
    )


# ---------------------------------------------------------------------------
# Test 1: Postgres reconnect path is called when connection_checker returns False
# ---------------------------------------------------------------------------


def test_runtime_supervisor_reconnects_when_connection_checker_returns_false(
    monkeypatch,
) -> None:
    """When connection_checker returns False, run_cycle_once must call reconnect_fn
    exactly once before proceeding with the cycle."""
    module, RuntimeSupervisor, _SupervisorCycleReport = load_supervisor_api()
    settings = make_settings()
    now = datetime(2026, 4, 24, 19, 0, tzinfo=timezone.utc)
    runtime = make_runtime_context(settings, position_store=RecordingPositionStore())
    broker = FakeBroker()
    market_data = FakeMarketData(
        intraday_bars_by_symbol={"AAPL": make_bar_series("AAPL", end=now, count=21)},
        daily_bars_by_symbol={"AAPL": make_bar_series("AAPL", end=now, count=20, days=True)},
    )
    stream = FakeStream()

    reconnect_calls: list[object] = []

    def recording_reconnect_fn(rt: object) -> None:
        reconnect_calls.append(rt)

    monkeypatch.setattr(module, "run_cycle", lambda **kwargs: SimpleNamespace(intents=[]))
    monkeypatch.setattr(
        module, "dispatch_pending_orders", lambda **kwargs: {"submitted_count": 0}
    )

    supervisor = RuntimeSupervisor(
        settings=settings,
        runtime=runtime,
        broker=broker,
        market_data=market_data,
        stream=stream,
        close_runtime_fn=lambda _runtime: None,
        connection_checker=lambda _conn: False,
        reconnect_fn=recording_reconnect_fn,
    )

    supervisor.run_cycle_once(now=lambda: now)

    assert len(reconnect_calls) == 1, (
        f"Expected reconnect_fn to be called exactly once; called {len(reconnect_calls)} time(s)"
    )
    assert reconnect_calls[0] is runtime, (
        "Expected reconnect_fn to be called with the RuntimeContext"
    )


def test_postgres_reconnect_emits_audit_event(monkeypatch) -> None:
    """When connection_checker returns False and reconnect succeeds, a
    postgres_reconnected audit event must be appended to the audit store."""
    module, RuntimeSupervisor, _SupervisorCycleReport = load_supervisor_api()
    settings = make_settings()
    now = datetime(2026, 4, 24, 19, 0, tzinfo=timezone.utc)
    runtime = make_runtime_context(settings, position_store=RecordingPositionStore())
    broker = FakeBroker()
    market_data = FakeMarketData(
        intraday_bars_by_symbol={"AAPL": make_bar_series("AAPL", end=now, count=21)},
        daily_bars_by_symbol={"AAPL": make_bar_series("AAPL", end=now, count=20, days=True)},
    )
    stream = FakeStream()

    monkeypatch.setattr(module, "run_cycle", lambda **kwargs: SimpleNamespace(intents=[]))
    monkeypatch.setattr(
        module, "dispatch_pending_orders", lambda **kwargs: {"submitted_count": 0}
    )

    supervisor = RuntimeSupervisor(
        settings=settings,
        runtime=runtime,
        broker=broker,
        market_data=market_data,
        stream=stream,
        close_runtime_fn=lambda _runtime: None,
        connection_checker=lambda _conn: False,
        reconnect_fn=lambda _rt: None,  # no-op reconnect
    )

    supervisor.run_cycle_once(now=lambda: now)

    reconnect_events = [
        e for e in runtime.audit_event_store.appended
        if getattr(e, "event_type", None) == "postgres_reconnected"
    ]
    assert len(reconnect_events) == 1, (
        "Expected exactly one postgres_reconnected audit event after reconnect"
    )


# ---------------------------------------------------------------------------
# Test 2: Stream restart backoff — no restart when _next_stream_restart_at is
# in the future
# ---------------------------------------------------------------------------


def test_supervisor_stream_watchdog_does_not_restart_during_backoff_window(
    monkeypatch,
) -> None:
    """When _next_stream_restart_at is in the future (backoff window active),
    run_forever's watchdog must NOT start a new stream thread even though the
    current stream thread is dead."""
    module, RuntimeSupervisor, SupervisorCycleReport = load_supervisor_api()
    settings = make_settings()
    now_ts = datetime(2026, 4, 24, 19, 0, tzinfo=timezone.utc)
    runtime = make_runtime_context(settings)
    broker = FakeBroker(market_is_open=True)
    market_data = FakeMarketData(intraday_bars_by_symbol={}, daily_bars_by_symbol={})
    stream = FakeStream()

    stream_start_calls: list[object] = []

    def recording_stream_attacher(**kwargs):
        stream_start_calls.append(kwargs)
        return object()

    supervisor = RuntimeSupervisor(
        settings=settings,
        runtime=runtime,
        broker=broker,
        market_data=market_data,
        stream=stream,
        close_runtime_fn=lambda _runtime: None,
        connection_checker=lambda _conn: True,
        stream_attacher=recording_stream_attacher,
    )

    monkeypatch.setattr(
        supervisor,
        "startup",
        lambda **kwargs: make_startup_report(),
    )
    monkeypatch.setattr(
        supervisor,
        "run_cycle_once",
        lambda **kwargs: SupervisorCycleReport(
            entries_disabled=False,
            cycle_result=SimpleNamespace(intents=[]),
            dispatch_report={"submitted_count": 0},
        ),
    )

    # Plant a dead thread so the watchdog will inspect it.
    dead_thread = threading.Thread(target=lambda: None, daemon=True)
    dead_thread.start()
    dead_thread.join()  # guaranteed not alive
    supervisor._stream_thread = dead_thread

    # Set backoff window 60 seconds into the future — restart should be skipped.
    supervisor._next_stream_restart_at = now_ts + timedelta(seconds=60)
    supervisor._stream_restart_attempts = 1  # prior attempt already counted

    supervisor.run_forever(
        max_iterations=1,
        sleep_fn=lambda _seconds: None,
        cycle_now=lambda: now_ts,
    )

    # The stream_attacher was injected but startup was monkeypatched, so
    # stream_start_calls should remain empty (no restart during backoff).
    # We also check that no "trade_update_stream_restarted" audit event was emitted.
    restart_events = [
        e
        for e in runtime.audit_event_store.appended
        if e.event_type == "trade_update_stream_restarted"
    ]
    assert len(restart_events) == 0, (
        "Expected no stream restart during active backoff window, "
        f"but got {len(restart_events)} restart event(s)"
    )


# ---------------------------------------------------------------------------
# Test 3: flatten_complete negative case — normal cycle does NOT write
# flatten_complete=True
# ---------------------------------------------------------------------------


def test_supervisor_does_not_write_flatten_complete_on_normal_cycle(
    monkeypatch,
) -> None:
    """A cycle with no eod_flatten intents must NOT call
    DailySessionStateStore.save() with flatten_complete=True."""
    module, RuntimeSupervisor, _SupervisorCycleReport = load_supervisor_api()
    settings = make_settings()
    now = datetime(2026, 4, 24, 19, 0, tzinfo=timezone.utc)

    session_state_store = RecordingDailySessionStateStore()
    runtime = make_runtime_context(
        settings,
        position_store=RecordingPositionStore(),
        daily_session_state_store=session_state_store,
    )
    broker = FakeBroker()
    market_data = FakeMarketData(
        intraday_bars_by_symbol={"AAPL": make_bar_series("AAPL", end=now, count=21)},
        daily_bars_by_symbol={"AAPL": make_bar_series("AAPL", end=now, count=20, days=True)},
    )
    stream = FakeStream()

    # Cycle result with no intents at all — definitely no eod_flatten.
    monkeypatch.setattr(
        module,
        "run_cycle",
        lambda **kwargs: SimpleNamespace(intents=[]),
    )
    monkeypatch.setattr(
        module,
        "dispatch_pending_orders",
        lambda **kwargs: {"submitted_count": 0},
    )

    supervisor = RuntimeSupervisor(
        settings=settings,
        runtime=runtime,
        broker=broker,
        market_data=market_data,
        stream=stream,
        close_runtime_fn=lambda _runtime: None,
        connection_checker=lambda _conn: True,
    )

    supervisor.run_cycle_once(now=lambda: now)

    flatten_complete_saves = [
        s for s in session_state_store.saved if s.flatten_complete is True
    ]
    assert len(flatten_complete_saves) == 0, (
        "Expected DailySessionStateStore.save(flatten_complete=True) NOT to be called "
        f"on a normal cycle, but it was called {len(flatten_complete_saves)} time(s)"
    )


# ---------------------------------------------------------------------------
# Test 4: HALTED status — order dispatcher is never called
# ---------------------------------------------------------------------------


def test_runtime_supervisor_halted_status_skips_order_dispatcher(
    monkeypatch,
) -> None:
    """When trading status is HALTED, run_cycle_once() must return early without
    invoking the order dispatcher."""
    module, RuntimeSupervisor, SupervisorCycleReport = load_supervisor_api()
    settings = make_settings()
    now = datetime(2026, 4, 24, 19, 15, tzinfo=timezone.utc)
    trading_status_store = RecordingTradingStatusStore(
        loaded_status=make_trading_status(
            settings, status=TradingStatusValue.HALTED, updated_at=now
        )
    )
    runtime = make_runtime_context(
        settings,
        trading_status_store=trading_status_store,
        position_store=RecordingPositionStore(),
    )
    broker = FakeBroker()
    market_data = FakeMarketData(
        intraday_bars_by_symbol={"AAPL": make_bar_series("AAPL", end=now, count=21)},
        daily_bars_by_symbol={"AAPL": make_bar_series("AAPL", end=now, count=20, days=True)},
    )
    stream = FakeStream()

    dispatch_calls: list[dict[str, object]] = []

    monkeypatch.setattr(
        module,
        "run_cycle",
        lambda **kwargs: SimpleNamespace(intents=[]),
    )
    monkeypatch.setattr(
        module,
        "dispatch_pending_orders",
        lambda **kwargs: dispatch_calls.append(kwargs) or {"submitted_count": 0},
    )

    supervisor = RuntimeSupervisor(
        settings=settings,
        runtime=runtime,
        broker=broker,
        market_data=market_data,
        stream=stream,
        close_runtime_fn=lambda _runtime: None,
        connection_checker=lambda _conn: True,
        order_dispatcher=lambda **kwargs: dispatch_calls.append(kwargs) or {"submitted_count": 0},
    )

    report = supervisor.run_cycle_once(now=lambda: now)

    assert isinstance(report, SupervisorCycleReport)
    assert report.entries_disabled is True
    assert len(dispatch_calls) == 0, (
        f"Expected order dispatcher to never be called when HALTED, "
        f"but it was called {len(dispatch_calls)} time(s)"
    )


# ---------------------------------------------------------------------------
# Phase 1 — Daily loss limit enforcement
# ---------------------------------------------------------------------------

def _make_minimal_supervisor(
    module,
    RuntimeSupervisor,
    *,
    settings,
    order_store: RecordingOrderStore,
    broker,
    now: datetime,
    equity_baseline: float | None = None,
):
    """Build a minimal RuntimeSupervisor with injected fakes for loss limit tests."""
    from alpaca_bot.strategy.breakout import session_day as _session_day
    market_data = FakeMarketData(intraday_bars_by_symbol={}, daily_bars_by_symbol={})
    stream = FakeStream()
    runtime = make_runtime_context(settings, order_store=order_store)

    supervisor = RuntimeSupervisor(
        settings=settings,
        runtime=runtime,
        broker=broker,
        market_data=market_data,
        stream=stream,
        close_runtime_fn=lambda _runtime: None,
        connection_checker=lambda _conn: True,
        cycle_runner=lambda **kwargs: SimpleNamespace(intents=[]),
        cycle_intent_executor=lambda **kwargs: None,
        order_dispatcher=lambda **kwargs: {"submitted_count": 0},
    )
    if equity_baseline is not None:
        session_date = _session_day(now, settings)
        supervisor._session_equity_baseline[session_date] = equity_baseline
    return supervisor, runtime


def test_daily_loss_limit_disables_entries_and_emits_audit_event_when_breached(
    monkeypatch,
) -> None:
    """When realized_pnl < -(daily_loss_limit_pct × equity), entries must be disabled
    and a daily_loss_limit_breached audit event with correct payload must be appended."""
    module, RuntimeSupervisor, SupervisorCycleReport = load_supervisor_api()
    settings = Settings.from_env(
        {
            "TRADING_MODE": "paper",
            "ENABLE_LIVE_TRADING": "false",
            "STRATEGY_VERSION": "v1-breakout",
            "DATABASE_URL": "postgresql://alpaca_bot:secret@db.example.com:5432/alpaca_bot",
            "MARKET_DATA_FEED": "sip",
            "SYMBOLS": "AAPL",
            "DAILY_SMA_PERIOD": "20",
            "BREAKOUT_LOOKBACK_BARS": "20",
            "RELATIVE_VOLUME_LOOKBACK_BARS": "20",
            "RELATIVE_VOLUME_THRESHOLD": "1.5",
            "ENTRY_TIMEFRAME_MINUTES": "15",
            "RISK_PER_TRADE_PCT": "0.0025",
            "MAX_POSITION_PCT": "0.05",
            "MAX_OPEN_POSITIONS": "3",
            "DAILY_LOSS_LIMIT_PCT": "0.05",   # 5% of equity
            "STOP_LIMIT_BUFFER_PCT": "0.001",
            "BREAKOUT_STOP_BUFFER_PCT": "0.001",
            "ENTRY_STOP_PRICE_BUFFER": "0.01",
            "ENTRY_WINDOW_START": "10:00",
            "ENTRY_WINDOW_END": "15:30",
            "FLATTEN_TIME": "15:45",
        }
    )
    now = datetime(2026, 4, 25, 14, 30, tzinfo=timezone.utc)
    # baseline=10_000, equity=9_400 → total_pnl=-600; limit_pct=0.05 → limit=500 → breached
    order_store = RecordingOrderStore(daily_pnl=-600.0)
    broker = FakeBroker(
        account=BrokerAccount(equity=9_400.0, buying_power=18_800.0, trading_blocked=False)
    )
    supervisor, runtime = _make_minimal_supervisor(
        module,
        RuntimeSupervisor,
        settings=settings,
        order_store=order_store,
        broker=broker,
        now=now,
        equity_baseline=10_000.0,
    )

    monkeypatch.setattr(module, "run_cycle", lambda **kwargs: SimpleNamespace(intents=[]))
    monkeypatch.setattr(module, "dispatch_pending_orders", lambda **kwargs: {"submitted_count": 0})
    monkeypatch.setattr(module, "execute_cycle_intents", lambda **kwargs: None)

    report = supervisor.run_cycle_once(now=lambda: now)

    assert isinstance(report, SupervisorCycleReport)
    assert report.entries_disabled is True

    breach_events = [
        e for e in runtime.audit_event_store.appended
        if getattr(e, "event_type", None) == "daily_loss_limit_breached"
    ]
    assert len(breach_events) == 1, "Expected exactly one daily_loss_limit_breached audit event"
    payload = breach_events[0].payload
    assert payload["realized_pnl"] == -600.0
    assert payload["total_pnl"] == pytest.approx(-600.0)
    assert payload["limit"] == pytest.approx(500.0)


def test_daily_loss_limit_allows_entries_when_not_breached(
    monkeypatch,
) -> None:
    """When realized_pnl is within the daily loss limit, entries must NOT be disabled
    and no daily_loss_limit_breached audit event must be emitted."""
    module, RuntimeSupervisor, SupervisorCycleReport = load_supervisor_api()
    settings = Settings.from_env(
        {
            "TRADING_MODE": "paper",
            "ENABLE_LIVE_TRADING": "false",
            "STRATEGY_VERSION": "v1-breakout",
            "DATABASE_URL": "postgresql://alpaca_bot:secret@db.example.com:5432/alpaca_bot",
            "MARKET_DATA_FEED": "sip",
            "SYMBOLS": "AAPL",
            "DAILY_SMA_PERIOD": "20",
            "BREAKOUT_LOOKBACK_BARS": "20",
            "RELATIVE_VOLUME_LOOKBACK_BARS": "20",
            "RELATIVE_VOLUME_THRESHOLD": "1.5",
            "ENTRY_TIMEFRAME_MINUTES": "15",
            "RISK_PER_TRADE_PCT": "0.0025",
            "MAX_POSITION_PCT": "0.05",
            "MAX_OPEN_POSITIONS": "3",
            "DAILY_LOSS_LIMIT_PCT": "0.05",
            "STOP_LIMIT_BUFFER_PCT": "0.001",
            "BREAKOUT_STOP_BUFFER_PCT": "0.001",
            "ENTRY_STOP_PRICE_BUFFER": "0.01",
            "ENTRY_WINDOW_START": "10:00",
            "ENTRY_WINDOW_END": "15:30",
            "FLATTEN_TIME": "15:45",
        }
    )
    now = datetime(2026, 4, 25, 14, 30, tzinfo=timezone.utc)
    # equity=10_000, limit_pct=0.05 → limit=500; pnl=-100 → well within limit
    order_store = RecordingOrderStore(daily_pnl=-100.0)
    broker = FakeBroker(
        account=BrokerAccount(equity=10_000.0, buying_power=20_000.0, trading_blocked=False)
    )
    supervisor, runtime = _make_minimal_supervisor(
        module,
        RuntimeSupervisor,
        settings=settings,
        order_store=order_store,
        broker=broker,
        now=now,
    )

    monkeypatch.setattr(module, "run_cycle", lambda **kwargs: SimpleNamespace(intents=[]))
    monkeypatch.setattr(module, "dispatch_pending_orders", lambda **kwargs: {"submitted_count": 0})
    monkeypatch.setattr(module, "execute_cycle_intents", lambda **kwargs: None)

    report = supervisor.run_cycle_once(now=lambda: now)

    breach_events = [
        e for e in runtime.audit_event_store.appended
        if getattr(e, "event_type", None) == "daily_loss_limit_breached"
    ]
    assert breach_events == [], "No breach event expected when PnL is within limit"


def test_daily_loss_limit_breach_fires_notifier(monkeypatch) -> None:
    """When the daily loss limit is breached, the injected notifier must receive a send() call."""
    module, RuntimeSupervisor, SupervisorCycleReport = load_supervisor_api()
    settings = Settings.from_env(
        {
            "TRADING_MODE": "paper",
            "ENABLE_LIVE_TRADING": "false",
            "STRATEGY_VERSION": "v1-breakout",
            "DATABASE_URL": "postgresql://alpaca_bot:secret@db.example.com:5432/alpaca_bot",
            "MARKET_DATA_FEED": "sip",
            "SYMBOLS": "AAPL",
            "DAILY_SMA_PERIOD": "20",
            "BREAKOUT_LOOKBACK_BARS": "20",
            "RELATIVE_VOLUME_LOOKBACK_BARS": "20",
            "RELATIVE_VOLUME_THRESHOLD": "1.5",
            "ENTRY_TIMEFRAME_MINUTES": "15",
            "RISK_PER_TRADE_PCT": "0.0025",
            "MAX_POSITION_PCT": "0.05",
            "MAX_OPEN_POSITIONS": "3",
            "DAILY_LOSS_LIMIT_PCT": "0.05",
            "STOP_LIMIT_BUFFER_PCT": "0.001",
            "BREAKOUT_STOP_BUFFER_PCT": "0.001",
            "ENTRY_STOP_PRICE_BUFFER": "0.01",
            "ENTRY_WINDOW_START": "10:00",
            "ENTRY_WINDOW_END": "15:30",
            "FLATTEN_TIME": "15:45",
        }
    )
    now = datetime(2026, 4, 25, 14, 30, tzinfo=timezone.utc)
    # baseline=10_000, equity=9_400 → total_pnl=-600; limit=500 → breached
    order_store = RecordingOrderStore(daily_pnl=-600.0)
    broker = FakeBroker(
        account=BrokerAccount(equity=9_400.0, buying_power=18_800.0, trading_blocked=False)
    )

    notifier_calls: list[tuple[str, str]] = []

    class _RecordingNotifier:
        def send(self, subject: str, body: str) -> None:
            notifier_calls.append((subject, body))

    from alpaca_bot.strategy.breakout import session_day as _session_day
    market_data = FakeMarketData(intraday_bars_by_symbol={}, daily_bars_by_symbol={})
    stream = FakeStream()
    runtime = make_runtime_context(settings, order_store=order_store)

    supervisor = RuntimeSupervisor(
        settings=settings,
        runtime=runtime,
        broker=broker,
        market_data=market_data,
        stream=stream,
        notifier=_RecordingNotifier(),
        close_runtime_fn=lambda _runtime: None,
        connection_checker=lambda _conn: True,
        cycle_runner=lambda **kwargs: SimpleNamespace(intents=[]),
        cycle_intent_executor=lambda **kwargs: None,
        order_dispatcher=lambda **kwargs: {"submitted_count": 0},
    )
    supervisor._session_equity_baseline[_session_day(now, settings)] = 10_000.0

    monkeypatch.setattr(module, "run_cycle", lambda **kwargs: SimpleNamespace(intents=[]))
    monkeypatch.setattr(module, "dispatch_pending_orders", lambda **kwargs: {"submitted_count": 0})
    monkeypatch.setattr(module, "execute_cycle_intents", lambda **kwargs: None)

    supervisor.run_cycle_once(now=lambda: now)

    assert len(notifier_calls) == 1, "Expected exactly one notifier call on breach"
    subject, body = notifier_calls[0]
    assert "loss" in subject.lower()
    assert "-600" in body or "600" in body


def test_daily_loss_limit_no_breach_does_not_fire_notifier(monkeypatch) -> None:
    """When the daily loss limit is not breached, notifier must not be called."""
    module, RuntimeSupervisor, SupervisorCycleReport = load_supervisor_api()
    settings = Settings.from_env(
        {
            "TRADING_MODE": "paper",
            "ENABLE_LIVE_TRADING": "false",
            "STRATEGY_VERSION": "v1-breakout",
            "DATABASE_URL": "postgresql://alpaca_bot:secret@db.example.com:5432/alpaca_bot",
            "MARKET_DATA_FEED": "sip",
            "SYMBOLS": "AAPL",
            "DAILY_SMA_PERIOD": "20",
            "BREAKOUT_LOOKBACK_BARS": "20",
            "RELATIVE_VOLUME_LOOKBACK_BARS": "20",
            "RELATIVE_VOLUME_THRESHOLD": "1.5",
            "ENTRY_TIMEFRAME_MINUTES": "15",
            "RISK_PER_TRADE_PCT": "0.0025",
            "MAX_POSITION_PCT": "0.05",
            "MAX_OPEN_POSITIONS": "3",
            "DAILY_LOSS_LIMIT_PCT": "0.05",
            "STOP_LIMIT_BUFFER_PCT": "0.001",
            "BREAKOUT_STOP_BUFFER_PCT": "0.001",
            "ENTRY_STOP_PRICE_BUFFER": "0.01",
            "ENTRY_WINDOW_START": "10:00",
            "ENTRY_WINDOW_END": "15:30",
            "FLATTEN_TIME": "15:45",
        }
    )
    now = datetime(2026, 4, 25, 14, 30, tzinfo=timezone.utc)
    order_store = RecordingOrderStore(daily_pnl=-100.0)
    broker = FakeBroker(
        account=BrokerAccount(equity=10_000.0, buying_power=20_000.0, trading_blocked=False)
    )

    notifier_calls: list[tuple[str, str]] = []

    class _RecordingNotifier:
        def send(self, subject: str, body: str) -> None:
            notifier_calls.append((subject, body))

    market_data = FakeMarketData(intraday_bars_by_symbol={}, daily_bars_by_symbol={})
    stream = FakeStream()
    runtime = make_runtime_context(settings, order_store=order_store)

    supervisor = RuntimeSupervisor(
        settings=settings,
        runtime=runtime,
        broker=broker,
        market_data=market_data,
        stream=stream,
        notifier=_RecordingNotifier(),
        close_runtime_fn=lambda _runtime: None,
        connection_checker=lambda _conn: True,
        cycle_runner=lambda **kwargs: SimpleNamespace(intents=[]),
        cycle_intent_executor=lambda **kwargs: None,
        order_dispatcher=lambda **kwargs: {"submitted_count": 0},
    )

    monkeypatch.setattr(module, "run_cycle", lambda **kwargs: SimpleNamespace(intents=[]))
    monkeypatch.setattr(module, "dispatch_pending_orders", lambda **kwargs: {"submitted_count": 0})
    monkeypatch.setattr(module, "execute_cycle_intents", lambda **kwargs: None)

    supervisor.run_cycle_once(now=lambda: now)

    assert notifier_calls == [], "Notifier must not fire when PnL is within limit"


def test_supervisor_passes_midnight_of_session_date_as_daily_bars_end(monkeypatch) -> None:
    """get_daily_bars must receive end=midnight-of-session-date (ET) to avoid including
    today's in-progress bar, which would corrupt signal calculations for all 5 strategies."""
    module, RuntimeSupervisor, SupervisorCycleReport = load_supervisor_api()
    settings = make_settings()
    # 19:00 UTC on 2026-04-25 = 15:00 ET (within session)
    now = datetime(2026, 4, 25, 19, 0, tzinfo=timezone.utc)
    market_data = FakeMarketData(
        intraday_bars_by_symbol={},
        daily_bars_by_symbol={},
    )
    stream = FakeStream()
    order_store = RecordingOrderStore()
    runtime = make_runtime_context(settings, order_store=order_store)

    supervisor = RuntimeSupervisor(
        settings=settings,
        runtime=runtime,
        broker=FakeBroker(),
        market_data=market_data,
        stream=stream,
        close_runtime_fn=lambda _runtime: None,
        connection_checker=lambda _conn: True,
        cycle_runner=lambda **kwargs: SimpleNamespace(intents=[]),
        cycle_intent_executor=lambda **kwargs: None,
        order_dispatcher=lambda **kwargs: {"submitted_count": 0},
    )

    monkeypatch.setattr(module, "run_cycle", lambda **kwargs: SimpleNamespace(intents=[]))
    monkeypatch.setattr(module, "dispatch_pending_orders", lambda **kwargs: {"submitted_count": 0})
    monkeypatch.setattr(module, "execute_cycle_intents", lambda **kwargs: None)

    supervisor.run_cycle_once(now=lambda: now)

    assert len(market_data.daily_bar_calls) >= 1, "get_daily_bars must have been called"
    call = market_data.daily_bar_calls[0]
    end = call["end"]
    # Midnight ET on 2026-04-25: represented as 00:00 in the ET timezone (UTC-4)
    end_et = end.astimezone(settings.market_timezone)
    assert end_et.hour == 0 and end_et.minute == 0, (
        f"Expected end=midnight ET to exclude today's in-progress bar, got {end_et}"
    )
    assert end_et.date().isoformat() == "2026-04-25", f"Wrong date in daily_bars end: {end_et}"
    # Must be strictly before now so today's bar is excluded
    assert end < now, f"daily_bars end ({end}) must be before now ({now})"


def test_run_cycle_once_continues_after_all_strategy_intent_executors_raise(monkeypatch) -> None:
    """If every per-strategy _cycle_intent_executor raises, run_cycle_once must still
    return a report and dispatch_pending_orders must still be called."""
    module, RuntimeSupervisor, SupervisorCycleReport = load_supervisor_api()
    settings = make_settings()
    now = datetime(2026, 4, 25, 19, 0, tzinfo=timezone.utc)
    dispatch_calls: list[dict] = []

    def raising_executor(**kwargs):
        raise RuntimeError("executor exploded")

    supervisor = RuntimeSupervisor(
        settings=settings,
        runtime=make_runtime_context(settings),
        broker=FakeBroker(),
        market_data=FakeMarketData(intraday_bars_by_symbol={}, daily_bars_by_symbol={}),
        stream=FakeStream(),
        close_runtime_fn=lambda _runtime: None,
        connection_checker=lambda _conn: True,
        cycle_runner=lambda **kwargs: SimpleNamespace(intents=[]),
        cycle_intent_executor=raising_executor,
        order_dispatcher=lambda **kwargs: dispatch_calls.append(kwargs) or {"submitted_count": 0},
    )

    monkeypatch.setattr(module, "run_cycle", lambda **kwargs: SimpleNamespace(intents=[]))
    monkeypatch.setattr(module, "dispatch_pending_orders", lambda **kwargs: dispatch_calls.append(kwargs) or {"submitted_count": 0})
    monkeypatch.setattr(module, "execute_cycle_intents", raising_executor)

    report = supervisor.run_cycle_once(now=lambda: now)

    assert report is not None, "run_cycle_once must return a report even when all executors raise"
    assert len(dispatch_calls) >= 1, "dispatch_pending_orders must still be called after executor failures"


def test_equity_baseline_persisted_on_first_cycle_and_recovered_on_restart(
    monkeypatch,
) -> None:
    """On the first cycle of a session day the supervisor must persist equity_baseline to
    DailySessionStateStore. On subsequent cycles (simulating a mid-day restart with an
    empty in-memory dict), it must load the persisted value instead of resetting to the
    current (post-loss) equity so the daily-loss-limit calculation is always anchored to
    start-of-day capital."""
    module, RuntimeSupervisor, SupervisorCycleReport = load_supervisor_api()
    settings = Settings.from_env(
        {
            "TRADING_MODE": "paper",
            "ENABLE_LIVE_TRADING": "false",
            "STRATEGY_VERSION": "v1-breakout",
            "DATABASE_URL": "postgresql://alpaca_bot:secret@db.example.com:5432/alpaca_bot",
            "MARKET_DATA_FEED": "sip",
            "SYMBOLS": "AAPL",
            "DAILY_SMA_PERIOD": "20",
            "BREAKOUT_LOOKBACK_BARS": "20",
            "RELATIVE_VOLUME_LOOKBACK_BARS": "20",
            "RELATIVE_VOLUME_THRESHOLD": "1.5",
            "ENTRY_TIMEFRAME_MINUTES": "15",
            "RISK_PER_TRADE_PCT": "0.0025",
            "MAX_POSITION_PCT": "0.05",
            "MAX_OPEN_POSITIONS": "3",
            "DAILY_LOSS_LIMIT_PCT": "0.05",
            "STOP_LIMIT_BUFFER_PCT": "0.001",
            "BREAKOUT_STOP_BUFFER_PCT": "0.001",
            "ENTRY_STOP_PRICE_BUFFER": "0.01",
            "ENTRY_WINDOW_START": "10:00",
            "ENTRY_WINDOW_END": "15:30",
            "FLATTEN_TIME": "15:45",
        }
    )
    now = datetime(2026, 4, 25, 14, 30, tzinfo=timezone.utc)
    session_date = date(2026, 4, 25)

    # Session state store that remembers the saved baseline row
    class BaselineCapturingSessionStore(RecordingDailySessionStateStore):
        def load(self, *, session_date, trading_mode, strategy_version, strategy_name="breakout"):
            if strategy_name == "_equity":
                # Return the saved baseline so the second cycle uses it
                for saved in self.saved:
                    if getattr(saved, "strategy_name", None) == "_equity":
                        return saved
            return None

    session_store = BaselineCapturingSessionStore()
    order_store = RecordingOrderStore(daily_pnl=0.0)
    runtime = make_runtime_context(
        settings, order_store=order_store, daily_session_state_store=session_store
    )
    # First cycle: equity = 10_000 (morning, no loss yet)
    broker_first = FakeBroker(
        account=BrokerAccount(equity=10_000.0, buying_power=20_000.0, trading_blocked=False)
    )
    supervisor = RuntimeSupervisor(
        settings=settings,
        runtime=runtime,
        broker=broker_first,
        market_data=FakeMarketData(intraday_bars_by_symbol={}, daily_bars_by_symbol={}),
        stream=FakeStream(),
        close_runtime_fn=lambda _runtime: None,
        connection_checker=lambda _conn: True,
        cycle_runner=lambda **kwargs: SimpleNamespace(intents=[]),
        cycle_intent_executor=lambda **kwargs: None,
        order_dispatcher=lambda **kwargs: {"submitted_count": 0},
    )
    monkeypatch.setattr(module, "run_cycle", lambda **kwargs: SimpleNamespace(intents=[]))
    monkeypatch.setattr(module, "dispatch_pending_orders", lambda **kwargs: {"submitted_count": 0})
    monkeypatch.setattr(module, "execute_cycle_intents", lambda **kwargs: None)

    supervisor.run_cycle_once(now=lambda: now)

    # Baseline must have been saved to the store
    baseline_rows = [s for s in session_store.saved if getattr(s, "strategy_name", None) == "_equity"]
    assert len(baseline_rows) == 1, "Expected one equity_baseline row saved after first cycle"
    assert baseline_rows[0].equity_baseline == pytest.approx(10_000.0)

    # Simulate a mid-day restart: clear in-memory baseline, switch to post-loss equity
    supervisor._session_equity_baseline.clear()
    broker_second = FakeBroker(
        account=BrokerAccount(equity=9_000.0, buying_power=18_000.0, trading_blocked=False)
    )
    supervisor.broker = broker_second

    supervisor.run_cycle_once(now=lambda: now)

    # The in-memory baseline must be recovered from the store (10_000), not reset to 9_000
    assert supervisor._session_equity_baseline.get(session_date) == pytest.approx(10_000.0), (
        "After restart, equity baseline must be recovered from session store, not reset to current equity"
    )


def test_equity_baseline_set_to_current_equity_when_no_persisted_row(
    monkeypatch,
) -> None:
    """When no _equity row exists in the session store (e.g. fresh deploy), the
    supervisor must set the baseline to the current broker equity and persist it."""
    module, RuntimeSupervisor, SupervisorCycleReport = load_supervisor_api()
    settings = make_settings()
    now = datetime(2026, 4, 25, 14, 30, tzinfo=timezone.utc)
    session_date = date(2026, 4, 25)

    session_store = RecordingDailySessionStateStore()  # load always returns None
    order_store = RecordingOrderStore(daily_pnl=0.0)
    runtime = make_runtime_context(
        settings, order_store=order_store, daily_session_state_store=session_store
    )
    broker = FakeBroker(
        account=BrokerAccount(equity=12_500.0, buying_power=25_000.0, trading_blocked=False)
    )
    supervisor = RuntimeSupervisor(
        settings=settings,
        runtime=runtime,
        broker=broker,
        market_data=FakeMarketData(intraday_bars_by_symbol={}, daily_bars_by_symbol={}),
        stream=FakeStream(),
        close_runtime_fn=lambda _runtime: None,
        connection_checker=lambda _conn: True,
        cycle_runner=lambda **kwargs: SimpleNamespace(intents=[]),
        cycle_intent_executor=lambda **kwargs: None,
        order_dispatcher=lambda **kwargs: {"submitted_count": 0},
    )
    monkeypatch.setattr(module, "run_cycle", lambda **kwargs: SimpleNamespace(intents=[]))
    monkeypatch.setattr(module, "dispatch_pending_orders", lambda **kwargs: {"submitted_count": 0})
    monkeypatch.setattr(module, "execute_cycle_intents", lambda **kwargs: None)

    supervisor.run_cycle_once(now=lambda: now)

    assert supervisor._session_equity_baseline.get(session_date) == pytest.approx(12_500.0)
    baseline_rows = [s for s in session_store.saved if getattr(s, "strategy_name", None) == "_equity"]
    assert len(baseline_rows) == 1
    assert baseline_rows[0].equity_baseline == pytest.approx(12_500.0)


def test_strategy_fan_out_continues_after_one_strategy_raises(monkeypatch) -> None:
    """If one strategy's cycle_runner raises, run_cycle_once must continue running
    the remaining strategies and emit a strategy_cycle_error audit event."""
    module, RuntimeSupervisor, SupervisorCycleReport = load_supervisor_api()
    settings = make_settings()
    now = datetime(2026, 4, 25, 14, 30, tzinfo=timezone.utc)

    from alpaca_bot.strategy import STRATEGY_REGISTRY
    strategy_names = list(STRATEGY_REGISTRY.keys())
    assert len(strategy_names) >= 2, "Need at least 2 strategies for this test"

    call_log: list[str] = []

    def selective_cycle_runner(**kwargs):
        name = kwargs.get("strategy_name", "")
        call_log.append(name)
        if name == strategy_names[0]:
            raise RuntimeError("first strategy exploded")
        return SimpleNamespace(intents=[])

    order_store = RecordingOrderStore(daily_pnl=0.0)
    runtime = make_runtime_context(settings, order_store=order_store)

    supervisor = RuntimeSupervisor(
        settings=settings,
        runtime=runtime,
        broker=FakeBroker(),
        market_data=FakeMarketData(intraday_bars_by_symbol={}, daily_bars_by_symbol={}),
        stream=FakeStream(),
        close_runtime_fn=lambda _runtime: None,
        connection_checker=lambda _conn: True,
        cycle_runner=selective_cycle_runner,
        cycle_intent_executor=lambda **kwargs: None,
        order_dispatcher=lambda **kwargs: {"submitted_count": 0},
    )
    monkeypatch.setattr(module, "dispatch_pending_orders", lambda **kwargs: {"submitted_count": 0})

    report = supervisor.run_cycle_once(now=lambda: now)

    # All strategies must have been attempted
    assert set(call_log) == set(strategy_names), (
        f"Expected all strategies to be called; got {call_log}"
    )
    # A strategy_cycle_error audit event must be appended for the failing strategy
    error_events = [
        e for e in runtime.audit_event_store.appended
        if getattr(e, "event_type", None) == "strategy_cycle_error"
    ]
    assert len(error_events) == 1
    assert error_events[0].payload["strategy_name"] == strategy_names[0]
    # The overall cycle must still return a report
    assert isinstance(report, SupervisorCycleReport)


# ── _append_audit rollback guard ─────────────────────────────────────────────


def test_append_audit_rollback_on_store_failure() -> None:
    """_append_audit must call connection.rollback() and NOT re-raise when the store fails."""
    module, RuntimeSupervisor, _ = load_supervisor_api()
    settings = make_settings()

    rollback_count = 0

    class _FailConn:
        def rollback(self) -> None:
            nonlocal rollback_count
            rollback_count += 1

        def commit(self) -> None:
            pass

    class _FailingAuditStore:
        def append(self, event: object, *, commit: bool = True) -> None:
            raise RuntimeError("audit store failed")

    runtime = make_runtime_context(settings)
    runtime = runtime.__class__(
        settings=settings,
        connection=_FailConn(),  # type: ignore[arg-type]
        lock=object(),  # type: ignore[arg-type]
        trading_status_store=RecordingTradingStatusStore(),  # type: ignore[arg-type]
        audit_event_store=_FailingAuditStore(),  # type: ignore[arg-type]
        order_store=RecordingOrderStore(),  # type: ignore[arg-type]
        position_store=RecordingPositionStore(),  # type: ignore[arg-type]
        daily_session_state_store=RecordingDailySessionStateStore(),  # type: ignore[arg-type]
    )

    supervisor = RuntimeSupervisor(
        settings=settings,
        runtime=runtime,
        broker=FakeBroker(),
        market_data=FakeMarketData(intraday_bars_by_symbol={}, daily_bars_by_symbol={}),
        stream=None,
    )

    event = AuditEvent(
        event_type="test_event",
        payload={},
        created_at=datetime(2026, 4, 24, 14, 0, tzinfo=timezone.utc),
    )
    # Must not raise — _append_audit swallows exceptions
    supervisor._append_audit(event)

    assert rollback_count == 1, "rollback() must be called when audit store append fails"


# ── _save_session_state rollback guard ───────────────────────────────────────


def test_save_session_state_rollback_and_reraise_on_store_failure() -> None:
    """_save_session_state must call connection.rollback() then re-raise."""
    module, RuntimeSupervisor, _ = load_supervisor_api()
    settings = make_settings()

    rollback_count = 0

    class _FailConn:
        def rollback(self) -> None:
            nonlocal rollback_count
            rollback_count += 1

        def commit(self) -> None:
            pass

    class _FailingSessionStateStore:
        def save(self, state: object) -> None:
            raise RuntimeError("state store failed")

        def load(self, **kwargs: object) -> None:
            return None

    runtime = make_runtime_context(settings)
    runtime = runtime.__class__(
        settings=settings,
        connection=_FailConn(),  # type: ignore[arg-type]
        lock=object(),  # type: ignore[arg-type]
        trading_status_store=RecordingTradingStatusStore(),  # type: ignore[arg-type]
        audit_event_store=RecordingAuditEventStore(),  # type: ignore[arg-type]
        order_store=RecordingOrderStore(),  # type: ignore[arg-type]
        position_store=RecordingPositionStore(),  # type: ignore[arg-type]
        daily_session_state_store=_FailingSessionStateStore(),  # type: ignore[arg-type]
    )

    supervisor = RuntimeSupervisor(
        settings=settings,
        runtime=runtime,
        broker=FakeBroker(),
        market_data=FakeMarketData(intraday_bars_by_symbol={}, daily_bars_by_symbol={}),
        stream=None,
    )

    state = DailySessionState(
        session_date=date(2026, 4, 24),
        trading_mode=settings.trading_mode,
        strategy_version=settings.strategy_version,
        entries_disabled=False,
        flatten_complete=False,
        updated_at=datetime(2026, 4, 24, 14, 0, tzinfo=timezone.utc),
    )

    with pytest.raises(RuntimeError, match="state store failed"):
        supervisor._save_session_state(state)

    assert rollback_count == 1, "rollback() must be called when session state save fails"


# ── _start_stream_thread rollback guards ─────────────────────────────────────


def _make_supervisor_with_failing_audit_on_nth_append(
    *,
    settings: Settings,
    fail_on_nth: int,
    stream_raises: Exception | None = None,
) -> tuple:
    """Build a supervisor whose audit store raises on the Nth append call.
    Returns (supervisor, rollback_counter_dict, RuntimeSupervisor class).
    """
    module, RuntimeSupervisor, _ = load_supervisor_api()
    rollback_counter = {"count": 0}

    class _FailConn:
        def rollback(self) -> None:
            rollback_counter["count"] += 1

        def commit(self) -> None:
            pass

    class _NthFailAuditStore:
        def __init__(self) -> None:
            self._calls = 0

        def append(self, event: object, *, commit: bool = True) -> None:
            self._calls += 1
            if self._calls >= fail_on_nth:
                raise RuntimeError(f"audit append failed on call {self._calls}")

    runtime = make_runtime_context(settings)
    runtime = runtime.__class__(
        settings=settings,
        connection=_FailConn(),  # type: ignore[arg-type]
        lock=object(),  # type: ignore[arg-type]
        trading_status_store=RecordingTradingStatusStore(),  # type: ignore[arg-type]
        audit_event_store=_NthFailAuditStore(),  # type: ignore[arg-type]
        order_store=RecordingOrderStore(),  # type: ignore[arg-type]
        position_store=RecordingPositionStore(),  # type: ignore[arg-type]
        daily_session_state_store=RecordingDailySessionStateStore(),  # type: ignore[arg-type]
    )

    stream = FakeStream(raise_on_run=stream_raises)
    supervisor = RuntimeSupervisor(
        settings=settings,
        runtime=runtime,
        broker=FakeBroker(),
        market_data=FakeMarketData(intraday_bars_by_symbol={}, daily_bars_by_symbol={}),
        stream=stream,
    )
    return supervisor, rollback_counter, stream


def test_stream_thread_started_audit_rollback_on_failure() -> None:
    """If the trade_update_stream_started audit append fails, rollback must be called
    and the stream thread must continue to call stream.run()."""
    settings = make_settings()
    now = datetime(2026, 4, 24, 14, 0, tzinfo=timezone.utc)

    # 1st append (stream_started) fails
    supervisor, rollback_counter, stream = _make_supervisor_with_failing_audit_on_nth_append(
        settings=settings,
        fail_on_nth=1,
    )

    supervisor._start_stream_thread(now=lambda: now)
    assert stream.run_started.wait(timeout=2.0), "stream.run() was never called — thread did not start"

    assert rollback_counter["count"] >= 1, (
        "rollback() must be called when trade_update_stream_started audit append fails"
    )
    assert stream.run_calls == 1, "stream.run() must still be called despite audit failure"


def test_stream_thread_failed_audit_rollback_on_failure() -> None:
    """If the trade_update_stream_failed audit append fails (double-failure: stream crashes
    AND audit store is down), rollback must be called and the thread must exit cleanly."""
    settings = make_settings()
    now = datetime(2026, 4, 24, 14, 0, tzinfo=timezone.utc)

    # 2nd append (stream_failed) fails — 1st (stream_started) succeeds
    supervisor, rollback_counter, stream = _make_supervisor_with_failing_audit_on_nth_append(
        settings=settings,
        fail_on_nth=2,
        stream_raises=RuntimeError("stream_crashed"),
    )

    supervisor._start_stream_thread(now=lambda: now)
    stream.run_started.wait(timeout=2.0)
    # Thread may complete very quickly; join via stored reference if available
    t = supervisor._stream_thread
    if t is not None:
        t.join(timeout=2.0)

    assert rollback_counter["count"] >= 1, (
        "rollback() must be called when trade_update_stream_failed audit append fails"
    )


def test_stream_thread_stopped_audit_rollback_on_failure() -> None:
    """If trade_update_stream_stopped audit append fails (stream exits cleanly but audit
    store is down), rollback must be called and the thread must exit cleanly."""
    settings = make_settings()
    now = datetime(2026, 4, 24, 14, 0, tzinfo=timezone.utc)

    # 2nd append (stream_stopped) fails — 1st (stream_started) succeeds
    supervisor, rollback_counter, stream = _make_supervisor_with_failing_audit_on_nth_append(
        settings=settings,
        fail_on_nth=2,
        stream_raises=None,  # stream exits cleanly
    )

    supervisor._start_stream_thread(now=lambda: now)
    stream.run_started.wait(timeout=2.0)
    t = supervisor._stream_thread
    if t is not None:
        t.join(timeout=2.0)

    assert rollback_counter["count"] >= 1, (
        "rollback() must be called when trade_update_stream_stopped audit append fails"
    )


# ---------------------------------------------------------------------------
# Round 40: loss limit uses total_pnl (equity delta), not just realized PnL
# ---------------------------------------------------------------------------


def test_daily_loss_limit_uses_unrealized_pnl_via_equity_delta(monkeypatch) -> None:
    """Loss limit must use total_pnl = account.equity - baseline_equity,
    which includes unrealized losses on open positions.

    Scenario: realized_pnl=0 (no closed trades) but equity dropped 600
    due to an open losing position. Limit of 500 should be breached.
    """
    module, RuntimeSupervisor, SupervisorCycleReport = load_supervisor_api()
    settings = Settings.from_env(
        {
            "TRADING_MODE": "paper",
            "ENABLE_LIVE_TRADING": "false",
            "STRATEGY_VERSION": "v1-breakout",
            "DATABASE_URL": "postgresql://alpaca_bot:secret@db.example.com:5432/alpaca_bot",
            "MARKET_DATA_FEED": "sip",
            "SYMBOLS": "AAPL",
            "DAILY_SMA_PERIOD": "20",
            "BREAKOUT_LOOKBACK_BARS": "20",
            "RELATIVE_VOLUME_LOOKBACK_BARS": "20",
            "RELATIVE_VOLUME_THRESHOLD": "1.5",
            "ENTRY_TIMEFRAME_MINUTES": "15",
            "RISK_PER_TRADE_PCT": "0.0025",
            "MAX_POSITION_PCT": "0.05",
            "MAX_OPEN_POSITIONS": "3",
            "DAILY_LOSS_LIMIT_PCT": "0.05",
            "STOP_LIMIT_BUFFER_PCT": "0.001",
            "BREAKOUT_STOP_BUFFER_PCT": "0.001",
            "ENTRY_STOP_PRICE_BUFFER": "0.01",
            "ENTRY_WINDOW_START": "10:00",
            "ENTRY_WINDOW_END": "15:30",
            "FLATTEN_TIME": "15:45",
        }
    )
    now = datetime(2026, 4, 25, 14, 30, tzinfo=timezone.utc)
    # realized_pnl=0 (no closed trades) but equity dropped due to open position
    order_store = RecordingOrderStore(daily_pnl=0.0)
    broker = FakeBroker(
        account=BrokerAccount(equity=9_400.0, buying_power=18_800.0, trading_blocked=False)
    )
    supervisor, runtime = _make_minimal_supervisor(
        module,
        RuntimeSupervisor,
        settings=settings,
        order_store=order_store,
        broker=broker,
        now=now,
        equity_baseline=10_000.0,  # equity dropped 600 → total_pnl=-600 → breach
    )

    monkeypatch.setattr(module, "run_cycle", lambda **kwargs: SimpleNamespace(intents=[]))
    monkeypatch.setattr(module, "dispatch_pending_orders", lambda **kwargs: {"submitted_count": 0})
    monkeypatch.setattr(module, "execute_cycle_intents", lambda **kwargs: None)

    report = supervisor.run_cycle_once(now=lambda: now)

    assert report.entries_disabled is True, (
        "Entries must be disabled when unrealized loss exceeds daily loss limit"
    )
    breach_events = [
        e for e in runtime.audit_event_store.appended
        if getattr(e, "event_type", None) == "daily_loss_limit_breached"
    ]
    assert len(breach_events) == 1
    assert breach_events[0].payload["realized_pnl"] == 0.0
    assert breach_events[0].payload["total_pnl"] == pytest.approx(-600.0)


# ---------------------------------------------------------------------------
# Round 40: flatten_complete only set when executor succeeds, not on exception
# ---------------------------------------------------------------------------


def test_flatten_complete_not_set_when_executor_raises(monkeypatch) -> None:
    """flatten_complete must NOT be written when execute_cycle_intents raises.

    If the executor crashes during EOD flatten, the flag must remain False so
    that the next cycle retries the flatten instead of silently skipping it.
    """
    from alpaca_bot.core.engine import CycleIntent, CycleIntentType, CycleResult

    module, RuntimeSupervisor, SupervisorCycleReport = load_supervisor_api()
    settings = make_settings()
    # 20:00 UTC = 16:00 ET — past the 15:45 flatten time
    now = datetime(2026, 4, 24, 20, 0, tzinfo=timezone.utc)

    session_state_store = RecordingDailySessionStateStore()
    runtime = make_runtime_context(
        settings,
        position_store=RecordingPositionStore(),
        daily_session_state_store=session_state_store,
    )

    broker = FakeBroker()
    market_data = FakeMarketData(
        intraday_bars_by_symbol={"AAPL": make_bar_series("AAPL", end=now, count=21)},
        daily_bars_by_symbol={"AAPL": make_bar_series("AAPL", end=now, count=20, days=True)},
    )
    stream = FakeStream()

    flatten_intent = CycleIntent(
        intent_type=CycleIntentType.EXIT,
        symbol="AAPL",
        timestamp=now,
        reason="eod_flatten",
    )
    fake_cycle_result = CycleResult(as_of=now, intents=[flatten_intent])

    monkeypatch.setattr(module, "run_cycle", lambda **kwargs: fake_cycle_result)
    monkeypatch.setattr(module, "dispatch_pending_orders", lambda **kwargs: {"submitted_count": 0})

    def _raising_executor(**kwargs):
        raise RuntimeError("broker unavailable")

    supervisor = RuntimeSupervisor(
        settings=settings,
        runtime=runtime,
        broker=broker,
        market_data=market_data,
        stream=stream,
        close_runtime_fn=lambda _runtime: None,
        connection_checker=lambda _conn: True,
        cycle_intent_executor=_raising_executor,
    )

    supervisor.run_cycle_once(now=lambda: now)

    flatten_complete_saved = [
        s for s in session_state_store.saved if s.flatten_complete is True
    ]
    assert flatten_complete_saved == [], (
        "flatten_complete must NOT be written when execute_cycle_intents raises"
    )


def test_entry_symbols_excludes_ignored_but_market_data_includes_all(monkeypatch) -> None:
    """Ignored symbols are excluded from entry evaluation but still get bars fetched."""
    module, RuntimeSupervisor, _SupervisorCycleReport = load_supervisor_api()
    settings = make_settings()
    now = datetime(2026, 4, 24, 19, 0, tzinfo=timezone.utc)
    runtime = make_runtime_context(settings)
    # Inject a watchlist_store with AAPL+TSLA enabled, TSLA ignored
    runtime.watchlist_store = SimpleNamespace(  # type: ignore[attr-defined]
        list_enabled=lambda trading_mode: ["AAPL", "TSLA"],
        list_ignored=lambda trading_mode: ["TSLA"],
    )
    broker = FakeBroker(
        account=BrokerAccount(equity=100_000.0, buying_power=200_000.0, trading_blocked=False),
    )
    market_data = FakeMarketData(
        intraday_bars_by_symbol={
            "AAPL": make_bar_series("AAPL", end=now, count=21),
            "TSLA": make_bar_series("TSLA", end=now, count=21),
        },
        daily_bars_by_symbol={
            "AAPL": make_bar_series("AAPL", end=now, count=20, days=True),
            "TSLA": make_bar_series("TSLA", end=now, count=20, days=True),
        },
    )
    stream = FakeStream()
    cycle_calls: list[dict] = []

    def fake_run_cycle(**kwargs):
        cycle_calls.append(kwargs)
        return SimpleNamespace(intents=[])

    monkeypatch.setattr(module, "run_cycle", fake_run_cycle)
    monkeypatch.setattr(module, "dispatch_pending_orders", lambda **_: {"submitted_count": 0})

    supervisor = RuntimeSupervisor(
        settings=settings,
        runtime=runtime,
        broker=broker,
        market_data=market_data,
        stream=stream,
        close_runtime_fn=lambda _runtime: None,
        connection_checker=lambda _conn: True,
    )

    supervisor.run_cycle_once(now=lambda: now)

    # Market data fetch includes BOTH symbols — bars needed for stop/exit on TSLA positions
    stock_bar_symbols = market_data.stock_bar_calls[0]["symbols"]
    assert "TSLA" in stock_bar_symbols
    assert "AAPL" in stock_bar_symbols

    # Entry evaluation only receives AAPL — TSLA is ignored for new entries
    assert len(cycle_calls) >= 1
    symbols_arg = cycle_calls[0]["symbols"]
    assert "TSLA" not in symbols_arg
    assert "AAPL" in symbols_arg


# ---------------------------------------------------------------------------
# Supervisor exits after 10 consecutive cycle failures
# ---------------------------------------------------------------------------


def test_run_cycle_once_returns_proper_report_on_empty_watchlist(monkeypatch) -> None:
    """When watchlist_store.list_enabled() returns empty, run_cycle_once must return a
    SupervisorCycleReport (not None) so run_forever can safely access report.entries_disabled."""
    module, RuntimeSupervisor, SupervisorCycleReport = load_supervisor_api()
    settings = make_settings()
    now = datetime(2026, 4, 25, 14, 30, tzinfo=timezone.utc)

    class EmptyWatchlistStore:
        def list_enabled(self, trading_mode: str) -> list[str]:
            return []

        def list_ignored(self, trading_mode: str) -> list[str]:
            return []

    runtime = make_runtime_context(settings)
    # Inject a watchlist_store that reports zero enabled symbols
    object.__setattr__(runtime, "watchlist_store", EmptyWatchlistStore())

    supervisor = RuntimeSupervisor(
        settings=settings,
        runtime=runtime,
        broker=FakeBroker(),
        market_data=FakeMarketData(intraday_bars_by_symbol={}, daily_bars_by_symbol={}),
        stream=FakeStream(),
        close_runtime_fn=lambda _runtime: None,
        connection_checker=lambda _conn: True,
    )
    monkeypatch.setattr(supervisor, "startup", lambda **kwargs: make_startup_report())

    report = supervisor.run_cycle_once(now=lambda: now)

    assert report is not None, "run_cycle_once must not return None on empty watchlist"
    assert isinstance(report, SupervisorCycleReport), (
        "run_cycle_once must return a SupervisorCycleReport, not None, when watchlist is empty"
    )
    # run_forever accesses report.entries_disabled — must not AttributeError
    _ = report.entries_disabled


def test_strategy_cycle_error_sends_notifier_alert(monkeypatch) -> None:
    """When _cycle_runner raises for a strategy, supervisor must send a notifier alert
    so an operator is immediately aware that positions may be unprotected."""
    module, RuntimeSupervisor, SupervisorCycleReport = load_supervisor_api()
    settings = make_settings()
    now = datetime(2026, 4, 25, 14, 30, tzinfo=timezone.utc)
    notifier_calls: list[dict[str, str]] = []

    class RecordingNotifier:
        def send(self, *, subject: str, body: str) -> None:
            notifier_calls.append({"subject": subject, "body": body})

    supervisor = RuntimeSupervisor(
        settings=settings,
        runtime=make_runtime_context(settings),
        broker=FakeBroker(),
        market_data=FakeMarketData(intraday_bars_by_symbol={}, daily_bars_by_symbol={}),
        stream=FakeStream(),
        close_runtime_fn=lambda _runtime: None,
        connection_checker=lambda _conn: True,
        cycle_runner=lambda **kwargs: (_ for _ in ()).throw(RuntimeError("cycle exploded")),
        cycle_intent_executor=lambda **kwargs: None,
        order_dispatcher=lambda **kwargs: {"submitted_count": 0},
        notifier=RecordingNotifier(),
    )
    monkeypatch.setattr(module, "run_cycle", lambda **kwargs: SimpleNamespace(intents=[]))
    monkeypatch.setattr(module, "dispatch_pending_orders", lambda **kwargs: {"submitted_count": 0})
    monkeypatch.setattr(module, "execute_cycle_intents", lambda **kwargs: None)
    monkeypatch.setattr(supervisor, "startup", lambda **kwargs: make_startup_report())

    report = supervisor.run_cycle_once(now=lambda: now)

    assert report is not None
    assert len(notifier_calls) >= 1, (
        "Notifier must be called when _cycle_runner raises so the operator is alerted"
    )
    assert any("cycle" in c["subject"].lower() or "strategy" in c["subject"].lower() for c in notifier_calls), (
        "Notifier subject must reference the cycle/strategy error"
    )


def test_supervisor_exits_after_10_consecutive_cycle_failures(monkeypatch) -> None:
    module, RuntimeSupervisor, _SupervisorCycleReport = load_supervisor_api()
    settings = make_settings()
    runtime = make_runtime_context(settings)
    broker = FakeBroker(market_is_open=True)
    market_data = FakeMarketData(intraday_bars_by_symbol={}, daily_bars_by_symbol={})
    stream = FakeStream()
    supervisor = RuntimeSupervisor(
        settings=settings,
        runtime=runtime,
        broker=broker,
        market_data=market_data,
        stream=stream,
        close_runtime_fn=lambda _runtime: None,
        connection_checker=lambda _conn: True,
    )

    monkeypatch.setattr(
        supervisor,
        "startup",
        lambda **kwargs: make_startup_report(),
    )

    call_count = 0

    def _always_fail(**kwargs):
        nonlocal call_count
        call_count += 1
        raise RuntimeError("simulated cycle failure")

    monkeypatch.setattr(supervisor, "run_cycle_once", _always_fail)

    import pytest
    with pytest.raises(SystemExit) as exc_info:
        supervisor.run_forever(
            max_iterations=20,
            sleep_fn=lambda _seconds: None,
            cycle_now=lambda: datetime(2026, 4, 24, 14, 30, tzinfo=timezone.utc),
        )

    assert exc_info.value.code == 1, "Supervisor must exit with code 1 after 10 consecutive failures"
    assert call_count == 10, f"Supervisor must exit exactly on the 10th failure, got {call_count}"


def test_stream_heartbeat_stale_fires_audit_and_notifier(monkeypatch) -> None:
    """When the stream thread is alive but no event has arrived in >300s, supervisor must
    append a stream_heartbeat_stale audit event and fire the notifier exactly once."""
    module, RuntimeSupervisor, _ = load_supervisor_api()
    settings = make_settings()
    runtime = make_runtime_context(settings)
    notifier_calls: list[dict[str, str]] = []

    class RecordingNotifier:
        def send(self, *, subject: str, body: str) -> None:
            notifier_calls.append({"subject": subject, "body": body})

    supervisor = RuntimeSupervisor(
        settings=settings,
        runtime=runtime,
        broker=FakeBroker(market_is_open=True),
        market_data=FakeMarketData(intraday_bars_by_symbol={}, daily_bars_by_symbol={}),
        stream=FakeStream(),
        close_runtime_fn=lambda _runtime: None,
        connection_checker=lambda _conn: True,
        notifier=RecordingNotifier(),
    )
    monkeypatch.setattr(supervisor, "startup", lambda **kwargs: make_startup_report())

    # Simulate: stream thread alive, last event was 310s ago
    base_now = datetime(2026, 4, 30, 14, 30, tzinfo=timezone.utc)
    stale_ts = base_now - timedelta(seconds=310)
    supervisor._last_stream_event_at = stale_ts
    fake_thread = threading.Thread(target=lambda: None)  # never started → not alive
    # We need an alive thread — use a blocking event
    _alive_event = threading.Event()
    alive_thread = threading.Thread(target=lambda: _alive_event.wait(timeout=5.0), daemon=True)
    alive_thread.start()
    supervisor._stream_thread = alive_thread

    iteration = 0

    def fake_run_cycle_once(**kwargs):
        nonlocal iteration
        iteration += 1
        if iteration >= 2:
            _alive_event.set()  # unblock the thread so test cleanup is fast
            raise SystemExit(0)
        return SimpleNamespace(entries_disabled=False, cycle_result=None, dispatch_report=None)

    monkeypatch.setattr(supervisor, "run_cycle_once", fake_run_cycle_once)

    with pytest.raises(SystemExit):
        supervisor.run_forever(
            max_iterations=3,
            sleep_fn=lambda _: None,
            cycle_now=lambda: base_now,
        )

    audit_events = runtime.audit_event_store.appended
    stale_events = [e for e in audit_events if e.event_type == "stream_heartbeat_stale"]
    assert len(stale_events) >= 1, "stream_heartbeat_stale audit event must be appended"
    assert any("heartbeat" in c["subject"].lower() or "stream" in c["subject"].lower() for c in notifier_calls), (
        "Notifier must be fired when heartbeat is stale"
    )


def test_stream_heartbeat_alert_fires_only_once_per_stale_window(monkeypatch) -> None:
    """Heartbeat alert must not repeat on every cycle while the stream remains stale."""
    module, RuntimeSupervisor, _ = load_supervisor_api()
    settings = make_settings()
    runtime = make_runtime_context(settings)
    notifier_calls: list[dict[str, str]] = []

    class RecordingNotifier:
        def send(self, *, subject: str, body: str) -> None:
            notifier_calls.append({"subject": subject, "body": body})

    supervisor = RuntimeSupervisor(
        settings=settings,
        runtime=runtime,
        broker=FakeBroker(market_is_open=True),
        market_data=FakeMarketData(intraday_bars_by_symbol={}, daily_bars_by_symbol={}),
        stream=FakeStream(),
        close_runtime_fn=lambda _runtime: None,
        connection_checker=lambda _conn: True,
        notifier=RecordingNotifier(),
    )
    monkeypatch.setattr(supervisor, "startup", lambda **kwargs: make_startup_report())

    base_now = datetime(2026, 4, 30, 14, 30, tzinfo=timezone.utc)
    stale_ts = base_now - timedelta(seconds=310)
    supervisor._last_stream_event_at = stale_ts

    _alive_event = threading.Event()
    alive_thread = threading.Thread(target=lambda: _alive_event.wait(timeout=5.0), daemon=True)
    alive_thread.start()
    supervisor._stream_thread = alive_thread

    cycle_count = 0

    def fake_run_cycle_once(**kwargs):
        nonlocal cycle_count
        cycle_count += 1
        if cycle_count >= 4:
            _alive_event.set()
            raise SystemExit(0)
        return SimpleNamespace(entries_disabled=False, cycle_result=None, dispatch_report=None)

    monkeypatch.setattr(supervisor, "run_cycle_once", fake_run_cycle_once)

    with pytest.raises(SystemExit):
        supervisor.run_forever(
            max_iterations=5,
            sleep_fn=lambda _: None,
            cycle_now=lambda: base_now,
        )

    heartbeat_notifier_calls = [
        c for c in notifier_calls
        if "heartbeat" in c["subject"].lower() or "stream" in c["subject"].lower()
    ]
    assert len(heartbeat_notifier_calls) == 1, (
        f"Heartbeat alert must fire exactly once per stale window, got {len(heartbeat_notifier_calls)}"
    )


def test_record_stream_event_updates_last_event_timestamp() -> None:
    """_record_stream_event must update _last_stream_event_at to the current UTC time."""
    _, RuntimeSupervisor, _ = load_supervisor_api()
    settings = make_settings()
    supervisor = RuntimeSupervisor(
        settings=settings,
        runtime=make_runtime_context(settings),
        broker=FakeBroker(),
        market_data=FakeMarketData(intraday_bars_by_symbol={}, daily_bars_by_symbol={}),
        stream=FakeStream(),
        close_runtime_fn=lambda _runtime: None,
        connection_checker=lambda _conn: True,
    )

    assert supervisor._last_stream_event_at is None
    supervisor._record_stream_event()
    assert supervisor._last_stream_event_at is not None
    before = supervisor._last_stream_event_at
    supervisor._record_stream_event()
    after = supervisor._last_stream_event_at
    assert after >= before, "_record_stream_event must advance the timestamp"


def test_lock_acquisition_error_halts_supervisor_immediately(monkeypatch) -> None:
    """When LockAcquisitionError is raised during a cycle (e.g. reconnect lost the lock),
    the supervisor must exit with code 1 immediately — not retry like a regular cycle error."""
    module, RuntimeSupervisor, _ = load_supervisor_api()
    from alpaca_bot.runtime.bootstrap import LockAcquisitionError

    settings = make_settings()

    supervisor = RuntimeSupervisor(
        settings=settings,
        runtime=make_runtime_context(settings),
        broker=FakeBroker(market_is_open=True),
        market_data=FakeMarketData(intraday_bars_by_symbol={}, daily_bars_by_symbol={}),
        stream=FakeStream(),
        close_runtime_fn=lambda _runtime: None,
        connection_checker=lambda _conn: True,
    )
    monkeypatch.setattr(supervisor, "startup", lambda **kwargs: make_startup_report())

    call_count = 0

    def _raise_lock_error(**kwargs):
        nonlocal call_count
        call_count += 1
        raise LockAcquisitionError("lock stolen by another instance")

    monkeypatch.setattr(supervisor, "run_cycle_once", _raise_lock_error)

    with pytest.raises(SystemExit) as exc_info:
        supervisor.run_forever(
            max_iterations=10,
            sleep_fn=lambda _: None,
            cycle_now=lambda: datetime(2026, 4, 30, 14, 30, tzinfo=timezone.utc),
        )

    assert exc_info.value.code == 1, "LockAcquisitionError must cause SystemExit(1)"
    assert call_count == 1, (
        f"Supervisor must halt on the first LockAcquisitionError, not retry; got {call_count} call(s)"
    )
