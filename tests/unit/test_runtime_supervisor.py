from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from importlib import import_module
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
    ) -> DailySessionState | None:
        del session_date, trading_mode, strategy_version
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

    def append(self, event: object) -> None:
        self.appended.append(event)


class RecordingOrderStore:
    def __init__(self, existing_orders: list[OrderRecord] | None = None) -> None:
        self.existing_orders = list(existing_orders or [])
        self.saved: list[object] = []
        self.list_by_status_calls: list[dict[str, object]] = []

    def save(self, order: object) -> None:
        self.saved.append(order)

    def list_by_status(
        self,
        *,
        trading_mode: TradingMode,
        strategy_version: str,
        statuses: list[str],
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


def make_runtime_context(
    settings: Settings,
    *,
    trading_status_store: RecordingTradingStatusStore | None = None,
    position_store: RecordingPositionStore | None = None,
    order_store: RecordingOrderStore | None = None,
) -> RuntimeContext:
    return RuntimeContext(
        settings=settings,
        connection=object(),  # type: ignore[arg-type]
        lock=object(),  # type: ignore[arg-type]
        trading_status_store=trading_status_store or RecordingTradingStatusStore(),  # type: ignore[arg-type]
        audit_event_store=RecordingAuditEventStore(),  # type: ignore[arg-type]
        order_store=order_store or RecordingOrderStore(),  # type: ignore[arg-type]
        position_store=position_store or RecordingPositionStore(),  # type: ignore[arg-type]
        daily_session_state_store=RecordingDailySessionStateStore(),  # type: ignore[arg-type]
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
    def __init__(self) -> None:
        self.handlers: list[object] = []

    def subscribe_trade_updates(self, handler) -> None:
        self.handlers.append(handler)

    def stop(self) -> None:
        return None


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
                    stop_price=0.0,
                    initial_stop_price=0.0,
                    opened_at=now,
                    updated_at=now,
                ),
                PositionRecord(
                    symbol="MSFT",
                    trading_mode=TradingMode.PAPER,
                    strategy_version="v1-breakout",
                    quantity=5,
                    entry_price=421.10,
                    stop_price=0.0,
                    initial_stop_price=0.0,
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
    assert runtime.audit_event_store.appended[0] == AuditEvent(
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
    assert broker.open_order_calls == 1
    assert broker.open_position_calls == 1


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
    assert len(cycle_calls) == 1
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
                OrderRecord(
                    client_order_id="v1-breakout:2026-04-24:AAPL:entry:2026-04-24T19:00:00+00:00",
                    symbol="AAPL",
                    side="buy",
                    intent_type="entry",
                    status="pending_submit",
                    quantity=10,
                    trading_mode=TradingMode.PAPER,
                    strategy_version="v1-breakout",
                    created_at=datetime(2026, 4, 24, 19, 0, tzinfo=timezone.utc),
                    updated_at=datetime(2026, 4, 24, 19, 0, tzinfo=timezone.utc),
                    stop_price=111.01,
                    limit_price=111.12,
                    initial_stop_price=109.89,
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
            "allowed_intent_types": {"stop"},
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
        broker_order_id=None,
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
    )

    assert startup_calls == [{}]
    assert len(cycle_calls) == 2
    assert sleep_calls == [12.5]
    assert close_calls == [True]
    assert report.iterations == 2
    assert report.active_iterations == 2
    assert report.idle_iterations == 0


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
