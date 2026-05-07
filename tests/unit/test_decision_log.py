from __future__ import annotations

import logging
import threading
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import pytest

from alpaca_bot.config import Settings
from alpaca_bot.core.engine import CycleResult, evaluate_cycle
from alpaca_bot.domain import Bar, EntrySignal, OpenPosition
from alpaca_bot.domain.decision_record import DecisionRecord
from alpaca_bot.storage.migrations import discover_migrations, resolve_migrations_path


def make_settings(**overrides: str) -> Settings:
    values = {
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
        "ATR_PERIOD": "14",
    }
    values.update(overrides)
    return Settings.from_env(values)


_NOW = datetime(2026, 5, 7, 14, 30, tzinfo=timezone.utc)


def _make_record(**overrides: Any) -> DecisionRecord:
    defaults: dict[str, Any] = {
        "cycle_at": _NOW,
        "symbol": "AAPL",
        "strategy_name": "breakout",
        "trading_mode": "paper",
        "strategy_version": "v1",
        "decision": "rejected",
        "reject_stage": "pre_filter",
        "reject_reason": "regime_blocked",
        "entry_level": None,
        "signal_bar_close": None,
        "relative_volume": None,
        "atr": None,
        "stop_price": None,
        "limit_price": None,
        "initial_stop_price": None,
        "quantity": None,
        "risk_per_share": None,
        "equity": None,
        "filter_results": {},
    }
    defaults.update(overrides)
    return DecisionRecord(**defaults)


# ── Migration exists ─────────────────────────────────────────────────────────

def test_migration_015_exists_and_contains_decision_log() -> None:
    migrations_dir = resolve_migrations_path(None)
    migrations = discover_migrations(migrations_dir)
    m015 = next((m for m in migrations if m.version == 15), None)
    assert m015 is not None, "015_add_decision_log.sql not found in migrations/"
    assert "decision_log" in m015.sql.lower()
    assert "filter_results" in m015.sql.lower()


# ── DecisionRecord frozen dataclass ─────────────────────────────────────────

def test_decision_record_is_frozen() -> None:
    rec = _make_record()
    with pytest.raises((AttributeError, TypeError)):
        rec.decision = "accepted"  # type: ignore[misc]


def test_decision_record_accepted_fields() -> None:
    rec = _make_record(
        decision="accepted",
        reject_stage=None,
        reject_reason=None,
        entry_level=150.25,
        signal_bar_close=151.0,
        relative_volume=2.5,
        quantity=10.0,
        stop_price=148.0,
        limit_price=151.0,
        initial_stop_price=148.0,
        risk_per_share=3.0,
        equity=100_000.0,
        filter_results={"regime": True, "news": True, "spread": True},
    )
    assert rec.decision == "accepted"
    assert rec.reject_stage is None
    assert rec.entry_level == 150.25
    assert rec.filter_results["regime"] is True


def test_decision_record_exported_from_domain() -> None:
    from alpaca_bot.domain import DecisionRecord as DR
    assert DR is DecisionRecord


# ── CycleResult has decision_records field ───────────────────────────────────

def test_cycle_result_has_decision_records_field() -> None:
    result = CycleResult(as_of=_NOW)
    assert result.decision_records == ()


# ── Helpers for evaluate_cycle tests ────────────────────────────────────────

def make_daily_bars(symbol: str = "AAPL", count: int = 22) -> list[Bar]:
    start = datetime(2026, 3, 26, 20, 0, tzinfo=timezone.utc)
    return [
        Bar(
            symbol=symbol,
            timestamp=start + timedelta(days=i),
            open=89.0 + i,
            high=90.0 + i,
            low=88.0 + i,
            close=90.0 + i,
            volume=1_000_000,
        )
        for i in range(count)
    ]


def make_intraday_bar(symbol: str = "AAPL", *, high: float = 151.0) -> Bar:
    return Bar(
        symbol=symbol,
        timestamp=datetime(2026, 5, 7, 14, 15, tzinfo=timezone.utc),
        open=149.0,
        high=high,
        low=148.0,
        close=150.0,
        volume=500_000,
    )


# ── evaluate_cycle decision records ─────────────────────────────────────────

def test_evaluate_cycle_regime_blocked_emits_records_per_symbol() -> None:
    settings = make_settings(
        SYMBOLS="AAPL,MSFT",
        ENABLE_REGIME_FILTER="true",
        REGIME_SMA_PERIOD="5",
    )
    regime_bars = [
        Bar(
            symbol="SPY",
            timestamp=datetime(2026, 5, 7 - i, 20, 0, tzinfo=timezone.utc),
            open=400.0,
            high=401.0,
            low=399.0,
            close=395.0 - i,
            volume=50_000_000,
        )
        for i in range(7)
    ]
    result = evaluate_cycle(
        settings=settings,
        now=datetime(2026, 5, 7, 14, 30, tzinfo=timezone.utc),
        equity=100_000.0,
        intraday_bars_by_symbol={},
        daily_bars_by_symbol={},
        open_positions=[],
        working_order_symbols=set(),
        traded_symbols_today=set(),
        entries_disabled=False,
        regime_bars=regime_bars,
    )
    assert result.regime_blocked is True
    regime_records = [r for r in result.decision_records if r.reject_reason == "regime_blocked"]
    assert len(regime_records) == 2
    symbols = {r.symbol for r in regime_records}
    assert symbols == {"AAPL", "MSFT"}
    for r in regime_records:
        assert r.decision == "rejected"
        assert r.reject_stage == "pre_filter"


def test_evaluate_cycle_no_signal_emits_skipped_no_signal() -> None:
    settings = make_settings(SYMBOLS="AAPL")
    now = datetime(2026, 5, 7, 14, 30, tzinfo=timezone.utc)

    def no_signal(*, symbol, intraday_bars, signal_index, daily_bars, settings):
        return None

    result = evaluate_cycle(
        settings=settings,
        now=now,
        equity=100_000.0,
        intraday_bars_by_symbol={"AAPL": [make_intraday_bar("AAPL")]},
        daily_bars_by_symbol={"AAPL": make_daily_bars("AAPL")},
        open_positions=[],
        working_order_symbols=set(),
        traded_symbols_today=set(),
        entries_disabled=False,
        signal_evaluator=no_signal,
    )

    no_sig = [r for r in result.decision_records if r.decision == "skipped_no_signal"]
    assert len(no_sig) == 1
    assert no_sig[0].symbol == "AAPL"


def test_evaluate_cycle_already_traded_emits_skipped_already_traded() -> None:
    settings = make_settings(SYMBOLS="AAPL")
    now = datetime(2026, 5, 7, 14, 30, tzinfo=timezone.utc)
    bar = make_intraday_bar("AAPL")
    from alpaca_bot.strategy.breakout import session_day
    already_traded = {("AAPL", session_day(bar.timestamp, settings))}

    result = evaluate_cycle(
        settings=settings,
        now=now,
        equity=100_000.0,
        intraday_bars_by_symbol={"AAPL": [bar]},
        daily_bars_by_symbol={"AAPL": make_daily_bars("AAPL")},
        open_positions=[],
        working_order_symbols=set(),
        traded_symbols_today=already_traded,
        entries_disabled=False,
    )

    records = [r for r in result.decision_records if r.decision == "skipped_already_traded"]
    assert len(records) == 1
    assert records[0].symbol == "AAPL"


def test_evaluate_cycle_open_position_emits_skipped_existing_position() -> None:
    settings = make_settings(SYMBOLS="AAPL")
    now = datetime(2026, 5, 7, 14, 30, tzinfo=timezone.utc)
    pos = OpenPosition(
        symbol="AAPL",
        quantity=10,
        entry_price=150.0,
        entry_level=149.0,
        initial_stop_price=148.0,
        stop_price=148.0,
        entry_timestamp=now,
    )

    result = evaluate_cycle(
        settings=settings,
        now=now,
        equity=100_000.0,
        intraday_bars_by_symbol={"AAPL": [make_intraday_bar("AAPL")]},
        daily_bars_by_symbol={"AAPL": make_daily_bars("AAPL")},
        open_positions=[pos],
        working_order_symbols=set(),
        traded_symbols_today=set(),
        entries_disabled=False,
    )

    records = [r for r in result.decision_records if r.decision == "skipped_existing_position"]
    assert len(records) == 1
    assert records[0].symbol == "AAPL"


def test_evaluate_cycle_capacity_full_emits_capacity_rejected() -> None:
    """When available_slots == 0 (max positions reached), symbols get capacity-rejected."""
    settings = make_settings(SYMBOLS="AAPL,MSFT", MAX_OPEN_POSITIONS="1")
    now = datetime(2026, 5, 7, 14, 30, tzinfo=timezone.utc)
    pos = OpenPosition(
        symbol="GOOGL",
        quantity=5,
        entry_price=200.0,
        entry_level=198.0,
        initial_stop_price=195.0,
        stop_price=195.0,
        entry_timestamp=now,
    )

    def fake_signal(*, symbol, intraday_bars, signal_index, daily_bars, settings):
        bar = intraday_bars[signal_index]
        return EntrySignal(
            symbol=symbol,
            signal_bar=bar,
            entry_level=bar.close - 1.0,
            relative_volume=2.0,
            stop_price=bar.close - 3.0,
            limit_price=bar.close,
            initial_stop_price=bar.close - 3.0,
        )

    result = evaluate_cycle(
        settings=settings,
        now=now,
        equity=100_000.0,
        intraday_bars_by_symbol={
            "AAPL": [make_intraday_bar("AAPL")],
            "MSFT": [make_intraday_bar("MSFT")],
        },
        daily_bars_by_symbol={
            "AAPL": make_daily_bars("AAPL"),
            "MSFT": make_daily_bars("MSFT"),
        },
        open_positions=[pos],
        working_order_symbols=set(),
        traded_symbols_today=set(),
        entries_disabled=False,
        signal_evaluator=fake_signal,
        global_open_count=1,
    )

    capacity_recs = [r for r in result.decision_records if r.reject_stage == "capacity"]
    assert len(capacity_recs) >= 1


def test_evaluate_cycle_flatten_all_returns_empty_decision_records() -> None:
    settings = make_settings(SYMBOLS="AAPL")
    now = datetime(2026, 5, 7, 14, 30, tzinfo=timezone.utc)
    pos = OpenPosition(
        symbol="AAPL",
        quantity=10,
        entry_price=150.0,
        entry_level=149.0,
        initial_stop_price=148.0,
        stop_price=148.0,
        entry_timestamp=now,
    )

    result = evaluate_cycle(
        settings=settings,
        now=now,
        equity=100_000.0,
        intraday_bars_by_symbol={},
        daily_bars_by_symbol={},
        open_positions=[pos],
        working_order_symbols=set(),
        traded_symbols_today=set(),
        entries_disabled=False,
        flatten_all=True,
    )

    assert result.decision_records == ()


def test_evaluate_cycle_accepted_entry_emits_accepted_record() -> None:
    """A symbol that produces a valid signal and fits capacity emits an accepted record."""
    settings = make_settings(SYMBOLS="AAPL")
    now = datetime(2026, 5, 7, 14, 30, tzinfo=timezone.utc)
    daily_bars = make_daily_bars("AAPL", count=22)
    signal_bar = Bar(
        symbol="AAPL",
        timestamp=datetime(2026, 5, 7, 14, 15, tzinfo=timezone.utc),
        open=149.0,
        high=156.0,
        low=148.0,
        close=155.0,
        volume=2_000_000,
    )

    def fake_signal(*, symbol, intraday_bars, signal_index, daily_bars, settings):
        return EntrySignal(
            symbol=symbol,
            signal_bar=intraday_bars[signal_index],
            entry_level=150.0,
            relative_volume=2.5,
            stop_price=148.0,
            limit_price=151.0,
            initial_stop_price=148.0,
        )

    result = evaluate_cycle(
        settings=settings,
        now=now,
        equity=100_000.0,
        intraday_bars_by_symbol={"AAPL": [signal_bar]},
        daily_bars_by_symbol={"AAPL": daily_bars},
        open_positions=[],
        working_order_symbols=set(),
        traded_symbols_today=set(),
        entries_disabled=False,
        signal_evaluator=fake_signal,
    )

    accepted = [r for r in result.decision_records if r.decision == "accepted"]
    assert len(accepted) == 1
    rec = accepted[0]
    assert rec.symbol == "AAPL"
    assert rec.entry_level == 150.0
    assert rec.relative_volume == 2.5
    assert rec.limit_price == 151.0
    assert rec.quantity is not None and rec.quantity > 0


# ── DecisionLogStore ─────────────────────────────────────────────────────────

from alpaca_bot.storage.repositories import DecisionLogStore


class _TrackingConnection:
    def __init__(self) -> None:
        self.commit_count = 0
        self.execute_calls: list[tuple] = []

    def commit(self) -> None:
        self.commit_count += 1

    def cursor(self):
        conn = self

        class _Cursor:
            def executemany(self, sql: str, params) -> None:
                conn.execute_calls.append(("executemany", sql, list(params)))

        return _Cursor()


def test_decision_log_store_bulk_insert_calls_executemany() -> None:
    conn = _TrackingConnection()
    store = DecisionLogStore(conn)
    records = [
        _make_record(decision="accepted", reject_stage=None, reject_reason=None),
        _make_record(decision="rejected", reject_stage="pre_filter", reject_reason="regime_blocked"),
    ]
    store.bulk_insert(records, conn)
    assert len(conn.execute_calls) == 1
    _, sql, params = conn.execute_calls[0]
    assert "decision_log" in sql.lower()
    assert len(params) == 2


def test_decision_log_store_bulk_insert_empty_is_noop() -> None:
    conn = _TrackingConnection()
    store = DecisionLogStore(conn)
    store.bulk_insert([], conn)
    assert conn.execute_calls == []
    assert conn.commit_count == 0


def test_decision_log_store_exported_from_storage() -> None:
    from alpaca_bot.storage import DecisionLogStore as DLS
    assert DLS is DecisionLogStore


# ── run_cycle best-effort write ──────────────────────────────────────────────

from alpaca_bot.runtime.cycle import run_cycle


class _FakeOrderStore:
    def save(self, order, *, commit=True):
        pass


class _FakeAuditStore:
    def append(self, event, *, commit=True):
        pass


class _FakeConnection:
    def commit(self):
        pass

    def rollback(self):
        pass


def _make_runtime(*, decision_log_store=None):
    return SimpleNamespace(
        order_store=_FakeOrderStore(),
        audit_event_store=_FakeAuditStore(),
        connection=_FakeConnection(),
        store_lock=threading.Lock(),
        decision_log_store=decision_log_store,
    )


def _make_cycle_result(*, decision_records=()):
    return CycleResult(
        as_of=_NOW,
        intents=[],
        decision_records=decision_records,
    )


def test_run_cycle_calls_bulk_insert_when_store_present() -> None:
    inserted: list = []

    class _FakeDecisionLogStore:
        def bulk_insert(self, records, conn):
            inserted.extend(records)

    records = [_make_record()]
    fake_result = _make_cycle_result(decision_records=tuple(records))
    runtime = _make_runtime(decision_log_store=_FakeDecisionLogStore())

    run_cycle(
        settings=make_settings(),
        runtime=runtime,
        now=_NOW,
        equity=100_000.0,
        intraday_bars_by_symbol={},
        daily_bars_by_symbol={},
        open_positions=[],
        working_order_symbols=set(),
        traded_symbols_today=set(),
        entries_disabled=False,
        _evaluate_fn=lambda **_: fake_result,
    )

    assert inserted == records


def test_run_cycle_skips_bulk_insert_when_no_store() -> None:
    fake_result = _make_cycle_result(decision_records=(_make_record(),))
    runtime = _make_runtime(decision_log_store=None)

    run_cycle(
        settings=make_settings(),
        runtime=runtime,
        now=_NOW,
        equity=100_000.0,
        intraday_bars_by_symbol={},
        daily_bars_by_symbol={},
        open_positions=[],
        working_order_symbols=set(),
        traded_symbols_today=set(),
        entries_disabled=False,
        _evaluate_fn=lambda **_: fake_result,
    )


def test_run_cycle_decision_log_failure_does_not_raise(caplog) -> None:
    class _FailingStore:
        def bulk_insert(self, records, conn):
            raise RuntimeError("DB write failed")

    fake_result = _make_cycle_result(decision_records=(_make_record(),))
    runtime = _make_runtime(decision_log_store=_FailingStore())

    with caplog.at_level(logging.WARNING, logger="alpaca_bot.runtime.cycle"):
        result = run_cycle(
            settings=make_settings(),
            runtime=runtime,
            now=_NOW,
            equity=100_000.0,
            intraday_bars_by_symbol={},
            daily_bars_by_symbol={},
            open_positions=[],
            working_order_symbols=set(),
            traded_symbols_today=set(),
            entries_disabled=False,
            _evaluate_fn=lambda **_: fake_result,
        )

    assert result is fake_result
    assert any("decision" in rec.message.lower() for rec in caplog.records)


# ── RuntimeContext has decision_log_store field ──────────────────────────────

from alpaca_bot.runtime.bootstrap import RuntimeContext


def test_runtime_context_has_decision_log_store_field() -> None:
    from dataclasses import fields
    field_names = {f.name for f in fields(RuntimeContext)}
    assert "decision_log_store" in field_names


def test_reconnect_rewires_decision_log_store() -> None:
    from alpaca_bot.runtime.bootstrap import reconnect_runtime_connection

    class _FakeConn:
        pass

    new_conn = _FakeConn()

    class _FakeStore:
        _connection = object()

    class _FakeLock:
        _connection = object()

        def try_acquire(self):
            return True

    store = _FakeStore()
    ctx = SimpleNamespace(
        connection=object(),
        decision_log_store=store,
        trading_status_store=None,
        audit_event_store=None,
        order_store=None,
        daily_session_state_store=None,
        position_store=None,
        strategy_flag_store=None,
        watchlist_store=None,
        option_order_store=None,
        strategy_weight_store=None,
        settings=make_settings(),
        lock=_FakeLock(),
    )

    reconnect_runtime_connection(ctx, _new_conn=new_conn)

    assert store._connection is new_conn
