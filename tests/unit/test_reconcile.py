"""Tests for alpaca_bot.runtime.reconcile."""
from __future__ import annotations

from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest

from alpaca_bot.config import Settings, TradingMode
from alpaca_bot.runtime.reconcile import (
    ReconciliationOutcome,
    SessionSnapshot,
    reconcile_startup,
    resolve_current_session,
)
from alpaca_bot.storage import DailySessionState


def _make_settings() -> Settings:
    return Settings.from_env(
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
            "DAILY_LOSS_LIMIT_PCT": "0.01",
            "STOP_LIMIT_BUFFER_PCT": "0.001",
            "BREAKOUT_STOP_BUFFER_PCT": "0.001",
            "ENTRY_STOP_PRICE_BUFFER": "0.01",
            "ENTRY_WINDOW_START": "10:00",
            "ENTRY_WINDOW_END": "15:30",
            "FLATTEN_TIME": "15:45",
        }
    )


def _make_session() -> SessionSnapshot:
    ts = datetime(2024, 6, 3, 14, 0, 0, tzinfo=timezone.utc)
    return SessionSnapshot(
        session_date=date(2024, 6, 3),
        as_of=ts,
        is_open=True,
        opens_at=ts,
        closes_at=ts,
    )


class _TrackingSessionStateStore:
    def __init__(self) -> None:
        self.saved: list[DailySessionState] = []

    def save(self, state: DailySessionState) -> None:
        self.saved.append(state)


class _FailingSessionStateStore:
    def save(self, state: DailySessionState) -> None:
        raise RuntimeError("db write failed")


class _RollbackTrackingConnection:
    def __init__(self) -> None:
        self.rollback_count = 0

    def rollback(self) -> None:
        self.rollback_count += 1

    def commit(self) -> None:
        pass

    def cursor(self):
        raise NotImplementedError


def _make_context(*, state_store, connection=None):
    conn = connection or _RollbackTrackingConnection()
    settings = _make_settings()
    return SimpleNamespace(
        settings=settings,
        connection=conn,
        daily_session_state_store=state_store,
    )


# ── resolve_current_session ──────────────────────────────────────────────────


def test_resolve_current_session_matches_timestamp_date() -> None:
    ts = datetime(2024, 6, 3, 14, 0, 0, tzinfo=timezone.utc)
    clock = SimpleNamespace(
        timestamp=ts,
        is_open=True,
        next_open=ts,
        next_close=ts,
    )
    calendar = [SimpleNamespace(date=date(2024, 6, 3), open=ts, close=ts)]
    snap = resolve_current_session(clock=clock, calendar=calendar)
    assert snap.session_date == date(2024, 6, 3)
    assert snap.is_open is True


def test_resolve_current_session_falls_back_to_next_open_date() -> None:
    ts = datetime(2024, 6, 1, 18, 0, 0, tzinfo=timezone.utc)  # Saturday — no session
    next_open = datetime(2024, 6, 3, 13, 30, 0, tzinfo=timezone.utc)
    clock = SimpleNamespace(
        timestamp=ts,
        is_open=False,
        next_open=next_open,
        next_close=next_open,
    )
    calendar = [SimpleNamespace(date=date(2024, 6, 3), open=next_open, close=next_open)]
    snap = resolve_current_session(clock=clock, calendar=calendar)
    assert snap.session_date == date(2024, 6, 3)


def test_resolve_current_session_raises_when_no_matching_session() -> None:
    ts = datetime(2024, 6, 1, 18, 0, 0, tzinfo=timezone.utc)
    next_open = datetime(2024, 6, 3, 13, 30, 0, tzinfo=timezone.utc)
    clock = SimpleNamespace(
        timestamp=ts,
        is_open=False,
        next_open=next_open,
        next_close=next_open,
    )
    # Calendar contains a completely different date — nothing matches
    calendar = [SimpleNamespace(date=date(2024, 5, 1), open=ts, close=ts)]
    with pytest.raises(RuntimeError, match="Could not resolve market clock"):
        resolve_current_session(clock=clock, calendar=calendar)


def test_resolve_current_session_raises_on_empty_calendar() -> None:
    ts = datetime(2024, 6, 3, 14, 0, 0, tzinfo=timezone.utc)
    clock = SimpleNamespace(
        timestamp=ts,
        is_open=True,
        next_open=ts,
        next_close=ts,
    )
    with pytest.raises(RuntimeError, match="Could not resolve market clock"):
        resolve_current_session(clock=clock, calendar=[])


# ── reconcile_startup ────────────────────────────────────────────────────────


def test_reconcile_startup_saves_session_state() -> None:
    store = _TrackingSessionStateStore()
    ctx = _make_context(state_store=store)
    session = _make_session()

    outcome = reconcile_startup(context=ctx, session=session, entries_disabled=False)

    assert isinstance(outcome, ReconciliationOutcome)
    assert len(store.saved) == 1
    assert store.saved[0].session_date == date(2024, 6, 3)
    assert store.saved[0].entries_disabled is False


def test_reconcile_startup_enables_entries_disabled_when_mismatches() -> None:
    store = _TrackingSessionStateStore()
    ctx = _make_context(state_store=store)
    session = _make_session()

    outcome = reconcile_startup(
        context=ctx,
        session=session,
        entries_disabled=False,
        mismatch_detector=lambda _ctx, _snap: ["position_mismatch"],
    )

    assert outcome.mismatch_detected is True
    assert store.saved[0].entries_disabled is True


def test_reconcile_startup_rollback_on_db_failure() -> None:
    conn = _RollbackTrackingConnection()
    ctx = _make_context(state_store=_FailingSessionStateStore(), connection=conn)
    session = _make_session()

    with pytest.raises(RuntimeError, match="db write failed"):
        reconcile_startup(context=ctx, session=session, entries_disabled=False)

    assert conn.rollback_count == 1


def test_reconcile_startup_raises_missing_store() -> None:
    ctx = _make_context(state_store=None)
    ctx.daily_session_state_store = None
    session = _make_session()

    with pytest.raises(RuntimeError, match="missing a daily session state store"):
        reconcile_startup(context=ctx, session=session, entries_disabled=False)
