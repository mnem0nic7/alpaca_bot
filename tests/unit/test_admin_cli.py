from __future__ import annotations

from datetime import datetime, timezone
import io
from types import SimpleNamespace

from alpaca_bot.admin.cli import main
from alpaca_bot.config import TradingMode
from alpaca_bot.storage import AuditEvent, OrderRecord, PositionRecord, TradingStatus, TradingStatusValue


class StoreFactoryStub:
    def __init__(self, store: object) -> None:
        self.store = store
        self.connections: list[object] = []

    def __call__(self, connection: object) -> object:
        self.connections.append(connection)
        return self.store


class RecordingTradingStatusStore:
    def __init__(self, loaded_status: TradingStatus | None = None) -> None:
        self.loaded_status = loaded_status
        self.saved: list[TradingStatus] = []
        self.load_calls: list[tuple[TradingMode, str]] = []

    def save(self, status: TradingStatus, *, commit: bool = True) -> None:
        self.saved.append(status)

    def load(
        self,
        *,
        trading_mode: TradingMode,
        strategy_version: str,
    ) -> TradingStatus | None:
        self.load_calls.append((trading_mode, strategy_version))
        return self.loaded_status


class RecordingAuditEventStore:
    def __init__(self) -> None:
        self.appended: list[AuditEvent] = []

    def append(self, event: AuditEvent, *, commit: bool = True) -> None:
        self.appended.append(event)


def test_halt_command_saves_halted_status_and_appends_audit_event() -> None:
    now = datetime(2026, 4, 24, 20, 30, tzinfo=timezone.utc)
    connection = SimpleNamespace(commit=lambda: None, close=lambda: None)
    status_store = RecordingTradingStatusStore()
    audit_store = RecordingAuditEventStore()
    stdout = io.StringIO()

    exit_code = main(
        [
            "halt",
            "--mode",
            "paper",
            "--strategy-version",
            "v1-breakout",
            "--reason",
            "manual intervention",
        ],
        connect=lambda: connection,
        trading_status_store_factory=StoreFactoryStub(status_store),
        audit_event_store_factory=StoreFactoryStub(audit_store),
        now=lambda: now,
        stdout=stdout,
    )

    assert exit_code == 0
    assert status_store.saved == [
        TradingStatus(
            trading_mode=TradingMode.PAPER,
            strategy_version="v1-breakout",
            status=TradingStatusValue.HALTED,
            kill_switch_enabled=True,
            status_reason="manual intervention",
            updated_at=now,
        )
    ]
    assert audit_store.appended == [
        AuditEvent(
            event_type="trading_status_changed",
            payload={
                "command": "halt",
                "trading_mode": "paper",
                "strategy_version": "v1-breakout",
                "status": "halted",
                "reason": "manual intervention",
            },
            created_at=now,
        )
    ]


def test_close_only_command_saves_close_only_status() -> None:
    now = datetime(2026, 4, 24, 20, 35, tzinfo=timezone.utc)
    connection = SimpleNamespace(commit=lambda: None, close=lambda: None)
    status_store = RecordingTradingStatusStore()
    audit_store = RecordingAuditEventStore()

    exit_code = main(
        [
            "close-only",
            "--mode",
            "live",
            "--strategy-version",
            "v2-breakout",
        ],
        connect=lambda: connection,
        trading_status_store_factory=StoreFactoryStub(status_store),
        audit_event_store_factory=StoreFactoryStub(audit_store),
        now=lambda: now,
        stdout=io.StringIO(),
    )

    assert exit_code == 0
    assert status_store.saved == [
        TradingStatus(
            trading_mode=TradingMode.LIVE,
            strategy_version="v2-breakout",
            status=TradingStatusValue.CLOSE_ONLY,
            kill_switch_enabled=False,
            status_reason=None,
            updated_at=now,
        )
    ]


def test_resume_command_restores_enabled_status_for_requested_target() -> None:
    now = datetime(2026, 4, 24, 20, 40, tzinfo=timezone.utc)
    connection = SimpleNamespace(commit=lambda: None, close=lambda: None)
    status_store = RecordingTradingStatusStore()
    audit_store = RecordingAuditEventStore()

    exit_code = main(
        [
            "resume",
            "--mode",
            "live",
            "--strategy-version",
            "breakout-v2026-04",
        ],
        connect=lambda: connection,
        trading_status_store_factory=StoreFactoryStub(status_store),
        audit_event_store_factory=StoreFactoryStub(audit_store),
        now=lambda: now,
        stdout=io.StringIO(),
    )

    assert exit_code == 0
    assert status_store.saved == [
        TradingStatus(
            trading_mode=TradingMode.LIVE,
            strategy_version="breakout-v2026-04",
            status=TradingStatusValue.ENABLED,
            kill_switch_enabled=False,
            status_reason=None,
            updated_at=now,
        )
    ]


def test_status_command_renders_current_status_text() -> None:
    now = datetime(2026, 4, 24, 20, 45, tzinfo=timezone.utc)
    connection = object()
    status_store = RecordingTradingStatusStore(
        loaded_status=TradingStatus(
            trading_mode=TradingMode.PAPER,
            strategy_version="v1-breakout",
            status=TradingStatusValue.HALTED,
            kill_switch_enabled=True,
            status_reason="manual intervention",
            updated_at=now,
        )
    )
    audit_store = RecordingAuditEventStore()
    stdout = io.StringIO()

    exit_code = main(
        [
            "status",
            "--mode",
            "paper",
            "--strategy-version",
            "v1-breakout",
        ],
        connect=lambda: connection,
        trading_status_store_factory=StoreFactoryStub(status_store),
        audit_event_store_factory=StoreFactoryStub(audit_store),
        stdout=stdout,
    )

    assert exit_code == 0
    assert status_store.saved == []
    assert status_store.load_calls == [(TradingMode.PAPER, "v1-breakout")]
    assert audit_store.appended == []
    rendered = stdout.getvalue().strip()
    assert "paper" in rendered
    assert "v1-breakout" in rendered
    assert "halted" in rendered
    assert "manual intervention" in rendered


def test_main_uses_process_argv_when_invoked_as_console_script(monkeypatch) -> None:
    now = datetime(2026, 4, 24, 20, 50, tzinfo=timezone.utc)
    connection = object()
    status_store = RecordingTradingStatusStore()
    audit_store = RecordingAuditEventStore()
    stdout = io.StringIO()

    monkeypatch.setattr(
        "sys.argv",
        ["alpaca-bot-admin", "status", "--mode", "paper", "--strategy-version", "v1-breakout"],
    )

    exit_code = main(
        None,
        connect=lambda: connection,
        trading_status_store_factory=StoreFactoryStub(status_store),
        audit_event_store_factory=StoreFactoryStub(audit_store),
        now=lambda: now,
        stdout=stdout,
    )

    assert exit_code == 0
    assert "status=unknown" in stdout.getvalue()


# ---------------------------------------------------------------------------
# Notifier calls for close-only and resume in run_admin_command
# ---------------------------------------------------------------------------


class _RecordingNotifier:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def send(self, *, subject: str, body: str) -> None:
        self.calls.append({"subject": subject, "body": body})


def _make_connection():
    return SimpleNamespace(commit=lambda: None, close=lambda: None)


def test_close_only_notifies() -> None:
    from alpaca_bot.admin.cli import run_admin_command
    from alpaca_bot.config import Settings

    settings = Settings.from_env({
        "TRADING_MODE": "paper",
        "ENABLE_LIVE_TRADING": "false",
        "STRATEGY_VERSION": "v1-breakout",
        "DATABASE_URL": "postgresql://example",
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
    })
    notifier = _RecordingNotifier()
    connection = SimpleNamespace(
        commit=lambda: None,
        close=lambda: None,
        cursor=lambda: SimpleNamespace(execute=lambda *_a, **_k: None, fetchone=lambda: None),
    )

    run_admin_command(
        ["close-only", "--mode", "paper", "--strategy-version", "v1-breakout"],
        settings=settings,
        connection=connection,
        notifier=notifier,
    )

    assert len(notifier.calls) == 1
    assert notifier.calls[0]["subject"] == "Trading set to close-only"


def test_resume_notifies() -> None:
    from alpaca_bot.admin.cli import run_admin_command
    from alpaca_bot.config import Settings

    settings = Settings.from_env({
        "TRADING_MODE": "paper",
        "ENABLE_LIVE_TRADING": "false",
        "STRATEGY_VERSION": "v1-breakout",
        "DATABASE_URL": "postgresql://example",
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
    })
    notifier = _RecordingNotifier()
    connection = SimpleNamespace(
        commit=lambda: None,
        close=lambda: None,
        cursor=lambda: SimpleNamespace(execute=lambda *_a, **_k: None, fetchone=lambda: None),
    )

    run_admin_command(
        ["resume", "--mode", "paper", "--strategy-version", "v1-breakout"],
        settings=settings,
        connection=connection,
        notifier=notifier,
    )

    assert len(notifier.calls) == 1
    assert notifier.calls[0]["subject"] == "Trading resumed"


# ---------------------------------------------------------------------------
# Fakes for close-excess and cancel-partial-fills
# ---------------------------------------------------------------------------


class RecordingBroker:
    def __init__(self) -> None:
        self.cancel_calls: list[str] = []
        self.market_exit_calls: list[dict] = []

    def cancel_order(self, order_id: str) -> None:
        self.cancel_calls.append(order_id)

    def submit_market_exit(self, **kwargs) -> object:
        self.market_exit_calls.append(dict(kwargs))
        return SimpleNamespace(
            client_order_id=kwargs["client_order_id"],
            broker_order_id=f"broker-exit-{kwargs['symbol']}",
            symbol=kwargs["symbol"],
            side="sell",
            status="ACCEPTED",
            quantity=kwargs["quantity"],
        )


class RecordingOrderStore:
    def __init__(self, *, orders: list | None = None) -> None:
        self._orders: list = orders or []
        self.saved: list = []

    def list_by_status(
        self,
        *,
        trading_mode,
        strategy_version,
        statuses: list[str],
        strategy_name=None,
    ) -> list:
        return [o for o in self._orders if o.status in statuses]

    def save(self, order, *, commit: bool = True) -> None:
        self.saved.append(order)


class RecordingPositionStore:
    def __init__(self, *, positions: list | None = None) -> None:
        self._positions: list = positions or []

    def list_all(self, *, trading_mode, strategy_version, strategy_name=None) -> list:
        return self._positions


# ---------------------------------------------------------------------------
# close-excess tests
# ---------------------------------------------------------------------------


def test_close_excess_submits_market_exits_for_positions_outside_top_n() -> None:
    """close-excess --keep 1 must exit the 2 positions with the widest stops."""
    now = datetime(2026, 5, 5, 14, 0, tzinfo=timezone.utc)
    connection = SimpleNamespace(commit=lambda: None, close=lambda: None)
    audit_store = RecordingAuditEventStore()

    # stop_pct = (entry_price - stop_price) / entry_price
    positions = [
        PositionRecord(
            symbol="AAPL",
            trading_mode=TradingMode.PAPER,
            strategy_version="v1-breakout",
            quantity=10,
            entry_price=100.0,
            stop_price=99.0,   # stop_pct = 1% → KEEP
            initial_stop_price=99.0,
            opened_at=now,
        ),
        PositionRecord(
            symbol="MSFT",
            trading_mode=TradingMode.PAPER,
            strategy_version="v1-breakout",
            quantity=10,
            entry_price=100.0,
            stop_price=95.0,   # stop_pct = 5% → CLOSE
            initial_stop_price=95.0,
            opened_at=now,
        ),
        PositionRecord(
            symbol="SPY",
            trading_mode=TradingMode.PAPER,
            strategy_version="v1-breakout",
            quantity=10,
            entry_price=100.0,
            stop_price=90.0,   # stop_pct = 10% → CLOSE
            initial_stop_price=90.0,
            opened_at=now,
        ),
    ]
    order_store = RecordingOrderStore(orders=[])
    position_store = RecordingPositionStore(positions=positions)
    broker = RecordingBroker()
    stdout = io.StringIO()

    exit_code = main(
        ["close-excess", "--keep", "1", "--mode", "paper", "--strategy-version", "v1-breakout"],
        connect=lambda: connection,
        trading_status_store_factory=StoreFactoryStub(RecordingTradingStatusStore()),
        audit_event_store_factory=StoreFactoryStub(audit_store),
        now=lambda: now,
        stdout=stdout,
        broker_factory=lambda _: broker,
        position_store_factory=StoreFactoryStub(position_store),
        order_store_factory=StoreFactoryStub(order_store),
    )

    assert exit_code == 0
    exited_symbols = {call["symbol"] for call in broker.market_exit_calls}
    assert exited_symbols == {"MSFT", "SPY"}
    assert "AAPL" not in exited_symbols
    closed_event_symbols = {
        e.symbol for e in audit_store.appended if e.event_type == "position_force_closed"
    }
    assert closed_event_symbols == {"MSFT", "SPY"}


def test_close_excess_dry_run_prints_plan_without_broker_calls() -> None:
    """close-excess --dry-run must print ranked table but make no broker calls or DB writes."""
    now = datetime(2026, 5, 5, 14, 0, tzinfo=timezone.utc)
    connection = SimpleNamespace(commit=lambda: None, close=lambda: None)
    audit_store = RecordingAuditEventStore()

    positions = [
        PositionRecord(
            symbol="AAPL",
            trading_mode=TradingMode.PAPER,
            strategy_version="v1-breakout",
            quantity=10,
            entry_price=100.0,
            stop_price=99.0,
            initial_stop_price=99.0,
            opened_at=now,
        ),
        PositionRecord(
            symbol="MSFT",
            trading_mode=TradingMode.PAPER,
            strategy_version="v1-breakout",
            quantity=10,
            entry_price=100.0,
            stop_price=95.0,
            initial_stop_price=95.0,
            opened_at=now,
        ),
        PositionRecord(
            symbol="SPY",
            trading_mode=TradingMode.PAPER,
            strategy_version="v1-breakout",
            quantity=10,
            entry_price=100.0,
            stop_price=90.0,
            initial_stop_price=90.0,
            opened_at=now,
        ),
    ]
    order_store = RecordingOrderStore(orders=[])
    position_store = RecordingPositionStore(positions=positions)
    broker = RecordingBroker()
    stdout = io.StringIO()

    exit_code = main(
        ["close-excess", "--keep", "1", "--dry-run", "--mode", "paper", "--strategy-version", "v1-breakout"],
        connect=lambda: connection,
        trading_status_store_factory=StoreFactoryStub(RecordingTradingStatusStore()),
        audit_event_store_factory=StoreFactoryStub(audit_store),
        now=lambda: now,
        stdout=stdout,
        broker_factory=lambda _: broker,
        position_store_factory=StoreFactoryStub(position_store),
        order_store_factory=StoreFactoryStub(order_store),
    )

    assert exit_code == 0
    assert broker.market_exit_calls == []
    assert broker.cancel_calls == []
    assert audit_store.appended == []
    rendered = stdout.getvalue()
    assert "AAPL" in rendered
    assert "MSFT" in rendered
    assert "SPY" in rendered


# ---------------------------------------------------------------------------
# cancel-partial-fills tests
# ---------------------------------------------------------------------------


def test_cancel_partial_fills_cancels_at_broker_and_marks_canceled_in_db() -> None:
    """cancel-partial-fills must cancel each partially_filled entry at broker and DB."""
    now = datetime(2026, 5, 5, 14, 0, tzinfo=timezone.utc)
    connection = SimpleNamespace(commit=lambda: None, close=lambda: None)
    audit_store = RecordingAuditEventStore()
    orders = [
        OrderRecord(
            client_order_id="v1-breakout:AAPL:entry:1",
            symbol="AAPL",
            side="buy",
            intent_type="entry",
            status="partially_filled",
            quantity=10,
            trading_mode=TradingMode.PAPER,
            strategy_version="v1-breakout",
            broker_order_id="broker-entry-aapl-1",
            created_at=now,
            updated_at=now,
        ),
        OrderRecord(
            client_order_id="v1-breakout:MSFT:entry:1",
            symbol="MSFT",
            side="buy",
            intent_type="entry",
            status="partially_filled",
            quantity=5,
            trading_mode=TradingMode.PAPER,
            strategy_version="v1-breakout",
            broker_order_id="broker-entry-msft-1",
            created_at=now,
            updated_at=now,
        ),
    ]
    order_store = RecordingOrderStore(orders=orders)
    position_store = RecordingPositionStore()
    broker = RecordingBroker()
    stdout = io.StringIO()

    exit_code = main(
        ["cancel-partial-fills", "--mode", "paper", "--strategy-version", "v1-breakout"],
        connect=lambda: connection,
        trading_status_store_factory=StoreFactoryStub(RecordingTradingStatusStore()),
        audit_event_store_factory=StoreFactoryStub(audit_store),
        now=lambda: now,
        stdout=stdout,
        broker_factory=lambda _: broker,
        position_store_factory=StoreFactoryStub(position_store),
        order_store_factory=StoreFactoryStub(order_store),
    )

    assert exit_code == 0
    assert set(broker.cancel_calls) == {"broker-entry-aapl-1", "broker-entry-msft-1"}
    canceled_ids = {o.client_order_id for o in order_store.saved if o.status == "canceled"}
    assert canceled_ids == {"v1-breakout:AAPL:entry:1", "v1-breakout:MSFT:entry:1"}
    event_types = [e.event_type for e in audit_store.appended]
    assert event_types.count("partial_fill_canceled_by_admin") == 2


def test_cancel_partial_fills_dry_run_prints_without_acting() -> None:
    """cancel-partial-fills --dry-run must print order info but make no broker or DB calls."""
    now = datetime(2026, 5, 5, 14, 0, tzinfo=timezone.utc)
    connection = SimpleNamespace(commit=lambda: None, close=lambda: None)
    audit_store = RecordingAuditEventStore()
    orders = [
        OrderRecord(
            client_order_id="v1-breakout:AAPL:entry:1",
            symbol="AAPL",
            side="buy",
            intent_type="entry",
            status="partially_filled",
            quantity=10,
            trading_mode=TradingMode.PAPER,
            strategy_version="v1-breakout",
            broker_order_id="broker-entry-aapl-1",
            created_at=now,
            updated_at=now,
        ),
        OrderRecord(
            client_order_id="v1-breakout:MSFT:entry:1",
            symbol="MSFT",
            side="buy",
            intent_type="entry",
            status="partially_filled",
            quantity=5,
            trading_mode=TradingMode.PAPER,
            strategy_version="v1-breakout",
            broker_order_id="broker-entry-msft-1",
            created_at=now,
            updated_at=now,
        ),
    ]
    order_store = RecordingOrderStore(orders=orders)
    position_store = RecordingPositionStore()
    broker = RecordingBroker()
    stdout = io.StringIO()

    exit_code = main(
        ["cancel-partial-fills", "--dry-run", "--mode", "paper", "--strategy-version", "v1-breakout"],
        connect=lambda: connection,
        trading_status_store_factory=StoreFactoryStub(RecordingTradingStatusStore()),
        audit_event_store_factory=StoreFactoryStub(audit_store),
        now=lambda: now,
        stdout=stdout,
        broker_factory=lambda _: broker,
        position_store_factory=StoreFactoryStub(position_store),
        order_store_factory=StoreFactoryStub(order_store),
    )

    assert exit_code == 0
    assert broker.cancel_calls == []
    assert order_store.saved == []
    assert audit_store.appended == []
    rendered = stdout.getvalue()
    assert "AAPL" in rendered
    assert "MSFT" in rendered
