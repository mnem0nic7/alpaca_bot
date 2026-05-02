from __future__ import annotations

from datetime import date, datetime, timezone
from types import SimpleNamespace

from alpaca_bot.config import Settings, TradingMode
from alpaca_bot.storage import AuditEvent, DailySessionState, OrderRecord, PositionRecord


def make_settings(**overrides: str) -> Settings:
    values = {
        "TRADING_MODE": "paper",
        "ENABLE_LIVE_TRADING": "false",
        "STRATEGY_VERSION": "v1-breakout",
        "DATABASE_URL": "postgresql://alpaca_bot:secret@db.example.com:5432/alpaca_bot",
        "MARKET_DATA_FEED": "sip",
        "SYMBOLS": "AAPL,MRVL",
        # Disable optional filters so _FakeMarketData needs no get_news/get_latest_quotes.
        "ENABLE_NEWS_FILTER": "false",
        "ENABLE_SPREAD_FILTER": "false",
        "ENABLE_REGIME_FILTER": "false",
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


class _RecordingOrderStore:
    def __init__(self, existing_orders: list[OrderRecord] | None = None) -> None:
        self.existing_orders = list(existing_orders or [])
        self.saved: list[OrderRecord] = []

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
        return [o for o in self.existing_orders if o.status in statuses]

    def daily_realized_pnl(
        self,
        *,
        trading_mode,
        strategy_version: str,
        session_date: date,
        market_timezone: str,
    ) -> float:
        return 0.0

    def daily_realized_pnl_by_symbol(
        self,
        *,
        trading_mode,
        strategy_version: str,
        session_date: date,
        market_timezone: str,
    ) -> dict[str, float]:
        return {}


class _RecordingPositionStore:
    def list_all(self, *, trading_mode, strategy_version: str) -> list[PositionRecord]:
        return []

    def save(self, position: PositionRecord, *, commit: bool = True) -> None:
        pass

    def replace_all(
        self,
        *,
        positions,
        trading_mode,
        strategy_version: str,
        commit: bool = True,
    ) -> None:
        pass


class _RecordingAuditEventStore:
    def __init__(self) -> None:
        self.appended: list[AuditEvent] = []

    def append(self, event: AuditEvent, *, commit: bool = True) -> None:
        self.appended.append(event)


class _FakeConnection:
    def commit(self) -> None:
        pass

    def rollback(self) -> None:
        pass

    def cursor(self):
        raise NotImplementedError("_FakeConnection.cursor should not be called in tests")


class _RecordingTradingStatusStore:
    def load(self, *, trading_mode, strategy_version: str):
        return None


class _RecordingDailySessionStateStore:
    def load(
        self,
        *,
        session_date: date,
        trading_mode,
        strategy_version: str,
        strategy_name: str | None = None,
    ):
        return None

    def save(self, state: DailySessionState) -> None:
        pass


def _make_runtime(settings: Settings, order_store: _RecordingOrderStore):
    from alpaca_bot.runtime import RuntimeContext

    return RuntimeContext(
        settings=settings,
        connection=_FakeConnection(),  # type: ignore[arg-type]
        lock=object(),  # type: ignore[arg-type]
        trading_status_store=_RecordingTradingStatusStore(),  # type: ignore[arg-type]
        audit_event_store=_RecordingAuditEventStore(),  # type: ignore[arg-type]
        order_store=order_store,  # type: ignore[arg-type]
        position_store=_RecordingPositionStore(),  # type: ignore[arg-type]
        daily_session_state_store=_RecordingDailySessionStateStore(),  # type: ignore[arg-type]
    )


class _Clock:
    is_open = True

    def __init__(self, now: datetime) -> None:
        self.timestamp = now
        self.next_open = now
        self.next_close = now


class _FakeBroker:
    def __init__(self, now: datetime) -> None:
        self._clock = _Clock(now)

    def get_clock(self):
        return self._clock

    def list_open_orders(self):
        return []

    def list_open_positions(self):
        return []

    def get_account(self):
        return SimpleNamespace(equity=100000.0, buying_power=90000.0, trading_blocked=False)


class _FakeMarketData:
    def get_stock_bars(self, **kwargs):
        return {}

    def get_daily_bars(self, **kwargs):
        return {}

    def get_latest_quotes(self, **kwargs):
        return {}


def test_supervisor_includes_active_stop_sell_symbols_in_working_order_symbols() -> None:
    """The supervisor must add symbols with active local stop-sell orders to
    working_order_symbols before calling run_cycle(), so evaluate_cycle() will
    skip them as entry candidates — preventing wash trades (RC-5).

    Scenario: MRVL has a stop at the broker (status='new' locally) but
    broker_open_orders is empty (e.g., truncated list pre-Fix-1). Without Fix 5,
    MRVL would not appear in working_order_symbols. With Fix 5, the supervisor
    queries local DB for active stops and adds MRVL regardless of broker list."""
    from alpaca_bot.runtime.supervisor import RuntimeSupervisor
    from alpaca_bot.core.engine import CycleResult

    settings = make_settings()
    now = datetime(2026, 5, 1, 14, 0, tzinfo=timezone.utc)

    active_stop = OrderRecord(
        client_order_id="orb:v1-breakout:2026-05-01:MRVL:stop:original",
        symbol="MRVL",
        side="sell",
        intent_type="stop",
        status="new",
        quantity=50,
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        strategy_name="breakout",
        stop_price=75.0,
        initial_stop_price=75.0,
    )

    order_store = _RecordingOrderStore(existing_orders=[active_stop])
    runtime = _make_runtime(settings, order_store)

    captured: dict[str, object] = {}

    def fake_cycle_runner(**kwargs):
        captured["working_order_symbols"] = set(kwargs["working_order_symbols"])
        return CycleResult(as_of=kwargs["now"])

    sup = RuntimeSupervisor(
        settings=settings,
        runtime=runtime,
        broker=_FakeBroker(now),
        market_data=_FakeMarketData(),
        stream=None,
        cycle_runner=fake_cycle_runner,
        order_dispatcher=lambda **kw: SimpleNamespace(submitted_count=0),
        cycle_intent_executor=lambda **kw: SimpleNamespace(
            failed_exit_count=0, submitted_exit_count=0
        ),
        connection_checker=lambda _conn: True,
    )

    sup.run_cycle_once()

    assert "MRVL" in captured.get("working_order_symbols", set()), (
        f"MRVL (active stop-sell status='new') must be in working_order_symbols passed to "
        f"run_cycle(); got {captured.get('working_order_symbols')!r}"
    )
