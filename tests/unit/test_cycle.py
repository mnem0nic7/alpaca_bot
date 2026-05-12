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


class RecordingOptionOrderStore:
    def __init__(self) -> None:
        self.saved: list = []
        self.commit_args: list[bool] = []

    def save(self, record, *, commit: bool = True) -> None:
        self.saved.append(record)
        self.commit_args.append(commit)


def _make_option_entry_intent(occ_symbol: str, now: datetime) -> CycleIntent:
    from datetime import date as _date
    return CycleIntent(
        intent_type=CycleIntentType.ENTRY,
        symbol=occ_symbol,
        timestamp=now,
        client_order_id=f"option:{occ_symbol}:entry",
        quantity=1,
        stop_price=None,
        limit_price=1.20,
        initial_stop_price=None,
        signal_timestamp=now,
        strategy_name="breakout",
        underlying_symbol="ALHC",
        is_option=True,
        option_strike=17.5,
        option_expiry=_date(2026, 6, 18),
        option_type_str="put",
    )


def test_run_cycle_emits_option_entry_intent_created_event() -> None:
    """Each option ENTRY intent must produce exactly one option_entry_intent_created
    audit event with the correct payload fields."""
    from alpaca_bot.runtime.cycle import run_cycle
    import alpaca_bot.runtime.cycle as cycle_module

    settings = _make_settings()
    now = datetime(2026, 5, 12, 14, 0, tzinfo=timezone.utc)
    audit_store = RecordingAuditEventStore()
    connection = CountingConnection()
    option_store = RecordingOptionOrderStore()
    runtime = SimpleNamespace(
        order_store=RecordingOrderStore(),
        audit_event_store=audit_store,
        connection=connection,
        option_order_store=option_store,
    )

    occ = "ALHC260618P00017500"
    option_result = CycleResult(
        as_of=now,
        intents=[_make_option_entry_intent(occ, now)],
    )
    original = cycle_module.evaluate_cycle
    cycle_module.evaluate_cycle = lambda **_: option_result
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

    assert all(not c for c in option_store.commit_args), (
        "option_order_store.save() calls must use commit=False"
    )
    entry_events = [
        e for e in audit_store.appended
        if e.event_type == "option_entry_intent_created"
    ]
    assert len(entry_events) == 1, (
        f"Expected 1 option_entry_intent_created event, got {len(entry_events)}"
    )
    payload = entry_events[0].payload
    assert payload["occ_symbol"] == occ
    assert payload["underlying_symbol"] == "ALHC"
    assert payload["option_type"] == "put"
    assert payload["strike"] == 17.5
    assert payload["ask_price"] == 1.20
    assert payload["quantity"] == 1
    assert payload["expiry"] == "2026-06-18"
    assert payload["signal_timestamp"] == now.isoformat()


def test_run_cycle_equity_entry_does_not_emit_option_event() -> None:
    """Equity ENTRY intents must NOT emit option_entry_intent_created."""
    from alpaca_bot.runtime.cycle import run_cycle
    import alpaca_bot.runtime.cycle as cycle_module

    settings = _make_settings()
    now = datetime(2026, 5, 12, 14, 15, tzinfo=timezone.utc)
    audit_store = RecordingAuditEventStore()
    runtime = SimpleNamespace(
        order_store=RecordingOrderStore(),
        audit_event_store=audit_store,
        connection=CountingConnection(),
    )

    equity_result = CycleResult(
        as_of=now,
        intents=[_make_entry_intent("AAPL", now)],
    )
    original = cycle_module.evaluate_cycle
    cycle_module.evaluate_cycle = lambda **_: equity_result
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

    option_events = [
        e for e in audit_store.appended
        if e.event_type == "option_entry_intent_created"
    ]
    assert len(option_events) == 0, (
        "Equity ENTRY intent must not produce option_entry_intent_created"
    )


def test_run_cycle_rollback_on_db_failure() -> None:
    """If order_store.save() raises during the atomic write block, run_cycle must
    call connection.rollback() and re-raise the exception."""
    import pytest
    from alpaca_bot.runtime.cycle import run_cycle
    import alpaca_bot.runtime.cycle as cycle_module

    settings = _make_settings()
    now = datetime(2026, 4, 27, 14, 30, tzinfo=timezone.utc)

    class FailingOrderStore:
        def save(self, order: OrderRecord, *, commit: bool = True) -> None:
            raise RuntimeError("simulated_db_failure")

    class RollbackTrackingConnection:
        def __init__(self) -> None:
            self.rollback_count = 0

        def commit(self) -> None:
            pass

        def rollback(self) -> None:
            self.rollback_count += 1

    connection = RollbackTrackingConnection()
    runtime = SimpleNamespace(
        order_store=FailingOrderStore(),
        audit_event_store=RecordingAuditEventStore(),
        connection=connection,
    )

    one_entry = CycleResult(as_of=now, intents=[_make_entry_intent("AAPL", now)])
    original = cycle_module.evaluate_cycle
    cycle_module.evaluate_cycle = lambda **_: one_entry
    try:
        with pytest.raises(RuntimeError, match="simulated_db_failure"):
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

    assert connection.rollback_count == 1, (
        f"run_cycle must call rollback() once when order_store.save() raises; "
        f"got rollback_count={connection.rollback_count}"
    )


def test_run_cycle_option_entry_event_signal_timestamp_none() -> None:
    """When signal_timestamp is None, the payload field must be None (not crash)."""
    from alpaca_bot.runtime.cycle import run_cycle
    import alpaca_bot.runtime.cycle as cycle_module
    from datetime import date as _date

    settings = _make_settings()
    now = datetime(2026, 5, 12, 14, 30, tzinfo=timezone.utc)
    audit_store = RecordingAuditEventStore()
    runtime = SimpleNamespace(
        order_store=RecordingOrderStore(),
        audit_event_store=audit_store,
        connection=CountingConnection(),
        option_order_store=RecordingOptionOrderStore(),
    )

    no_ts_intent = CycleIntent(
        intent_type=CycleIntentType.ENTRY,
        symbol="ALHC260618P00017500",
        timestamp=now,
        client_order_id="option:ALHC260618P00017500:entry",
        quantity=1,
        stop_price=None,
        limit_price=1.20,
        initial_stop_price=None,
        signal_timestamp=None,
        strategy_name="breakout",
        underlying_symbol="ALHC",
        is_option=True,
        option_strike=17.5,
        option_expiry=_date(2026, 6, 18),
        option_type_str="put",
    )
    result = CycleResult(as_of=now, intents=[no_ts_intent])
    original = cycle_module.evaluate_cycle
    cycle_module.evaluate_cycle = lambda **_: result
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

    entry_events = [
        e for e in audit_store.appended
        if e.event_type == "option_entry_intent_created"
    ]
    assert len(entry_events) == 1
    assert entry_events[0].payload["signal_timestamp"] is None
