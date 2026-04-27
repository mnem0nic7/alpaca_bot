"""Tests for alpaca_bot.runtime.cycle — commit discipline and multi-intent atomicity."""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from alpaca_bot.config import Settings, TradingMode
from alpaca_bot.core.engine import CycleIntent, CycleIntentType, CycleResult
from alpaca_bot.storage import AuditEvent, OrderRecord


def _make_settings(**overrides: str) -> Settings:
    values = {
        "TRADING_MODE": "paper",
        "ENABLE_LIVE_TRADING": "false",
        "STRATEGY_VERSION": "v1-breakout",
        "DATABASE_URL": "postgresql://alpaca_bot:secret@db.example.com:5432/alpaca_bot",
        "MARKET_DATA_FEED": "sip",
        "SYMBOLS": "AAPL,MSFT",
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


class CountingConnection:
    def __init__(self) -> None:
        self.commit_count = 0

    def commit(self) -> None:
        self.commit_count += 1


class RecordingOrderStore:
    def __init__(self) -> None:
        self.saved: list[OrderRecord] = []
        self.commit_args: list[bool] = []

    def save(self, order: OrderRecord, *, commit: bool = True) -> None:
        self.saved.append(order)
        self.commit_args.append(commit)


class RecordingAuditEventStore:
    def __init__(self) -> None:
        self.appended: list[AuditEvent] = []
        self.commit_args: list[bool] = []

    def append(self, event: AuditEvent, *, commit: bool = True) -> None:
        self.appended.append(event)
        self.commit_args.append(commit)


def _make_entry_intent(symbol: str, now: datetime) -> CycleIntent:
    return CycleIntent(
        intent_type=CycleIntentType.ENTRY,
        symbol=symbol,
        timestamp=now,
        client_order_id=f"v1-breakout:{symbol}:entry",
        quantity=10,
        stop_price=99.0,
        limit_price=101.0,
        initial_stop_price=99.0,
        signal_timestamp=now,
        strategy_name="breakout",
    )


def test_run_cycle_commits_exactly_once_per_invocation() -> None:
    """Regardless of how many ENTRY intents are produced, run_cycle must call
    connection.commit() exactly once — all writes are batched in one transaction."""
    from alpaca_bot.runtime.cycle import run_cycle
    import alpaca_bot.runtime.cycle as cycle_module

    settings = _make_settings()
    now = datetime(2026, 4, 27, 14, 0, tzinfo=timezone.utc)
    order_store = RecordingOrderStore()
    audit_store = RecordingAuditEventStore()
    connection = CountingConnection()
    runtime = SimpleNamespace(
        order_store=order_store,
        audit_event_store=audit_store,
        connection=connection,
    )

    one_entry = CycleResult(as_of=now, intents=[_make_entry_intent("AAPL", now)])
    original = cycle_module.evaluate_cycle
    cycle_module.evaluate_cycle = lambda **_: one_entry
    try:
        run_cycle(
            settings=settings,
            runtime=runtime,
            now=now,
            equity=100_000.0,
            intraday_bars_by_symbol={},
            daily_bars_by_symbol={},
            open_positions=[],
            working_order_symbols=set(),
            traded_symbols_today=set(),
            entries_disabled=False,
        )
    finally:
        cycle_module.evaluate_cycle = original

    assert connection.commit_count == 1, (
        f"run_cycle must commit exactly once per invocation; got {connection.commit_count}"
    )


def test_run_cycle_saves_all_entry_intents_with_single_commit() -> None:
    """When evaluate_cycle returns multiple ENTRY intents, all orders must be saved
    with commit=False individually and only one connection.commit() must fire at the end."""
    from alpaca_bot.runtime.cycle import run_cycle
    import alpaca_bot.runtime.cycle as cycle_module

    settings = _make_settings()
    now = datetime(2026, 4, 27, 14, 15, tzinfo=timezone.utc)
    order_store = RecordingOrderStore()
    audit_store = RecordingAuditEventStore()
    connection = CountingConnection()
    runtime = SimpleNamespace(
        order_store=order_store,
        audit_event_store=audit_store,
        connection=connection,
    )

    two_entries = CycleResult(
        as_of=now,
        intents=[
            _make_entry_intent("AAPL", now),
            _make_entry_intent("MSFT", now),
        ],
    )
    original = cycle_module.evaluate_cycle
    cycle_module.evaluate_cycle = lambda **_: two_entries
    try:
        run_cycle(
            settings=settings,
            runtime=runtime,
            now=now,
            equity=100_000.0,
            intraday_bars_by_symbol={},
            daily_bars_by_symbol={},
            open_positions=[],
            working_order_symbols=set(),
            traded_symbols_today=set(),
            entries_disabled=False,
        )
    finally:
        cycle_module.evaluate_cycle = original

    assert len(order_store.saved) == 2, f"Expected 2 entry orders saved, got {len(order_store.saved)}"
    assert {o.symbol for o in order_store.saved} == {"AAPL", "MSFT"}
    assert all(o.status == "pending_submit" for o in order_store.saved)
    # Every individual save must use commit=False — single terminal commit
    assert all(not c for c in order_store.commit_args), (
        "All order_store.save() calls must use commit=False; "
        f"got commit_args={order_store.commit_args}"
    )
    assert all(not c for c in audit_store.commit_args), (
        "All audit_event_store.append() calls must use commit=False; "
        f"got commit_args={audit_store.commit_args}"
    )
    assert connection.commit_count == 1, (
        f"Exactly one connection.commit() must fire; got {connection.commit_count}"
    )
