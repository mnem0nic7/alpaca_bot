from __future__ import annotations

from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest

from alpaca_bot.config import Settings
from alpaca_bot.runtime.daily_summary import build_daily_summary


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def make_settings() -> Settings:
    return Settings.from_env(
        {
            "TRADING_MODE": "paper",
            "ENABLE_LIVE_TRADING": "false",
            "STRATEGY_VERSION": "v1-breakout",
            "DATABASE_URL": "postgresql://test/db",
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


SESSION_DATE = date(2026, 4, 28)


class FakeOrderStore:
    def __init__(self, *, trades=None, pnl: float = 0.0):
        self._trades = trades or []
        self._pnl = pnl

    def list_closed_trades(
        self,
        *,
        trading_mode,
        strategy_version,
        session_date,
        market_timezone="America/New_York",
    ) -> list[dict]:
        return list(self._trades)

    def daily_realized_pnl(
        self,
        *,
        trading_mode,
        strategy_version,
        session_date,
        market_timezone="America/New_York",
    ) -> float:
        return self._pnl


class FakePositionStore:
    def __init__(self, *, positions=None):
        self._positions = positions or []

    def list_all(self, *, trading_mode, strategy_version) -> list:
        return list(self._positions)


def _trade(
    symbol: str = "AAPL",
    strategy_name: str = "breakout",
    entry_fill: float = 150.0,
    exit_fill: float = 155.0,
    qty: int = 10,
) -> dict:
    return {
        "symbol": symbol,
        "strategy_name": strategy_name,
        "entry_fill": entry_fill,
        "exit_fill": exit_fill,
        "qty": qty,
    }


def _position(
    symbol: str = "AAPL",
    quantity: int = 10,
    entry_price: float = 150.0,
    stop_price: float = 148.0,
) -> SimpleNamespace:
    return SimpleNamespace(
        symbol=symbol,
        quantity=quantity,
        entry_price=entry_price,
        stop_price=stop_price,
    )


# ---------------------------------------------------------------------------
# build_daily_summary tests
# ---------------------------------------------------------------------------


class TestBuildDailySummary:
    def test_zero_trades_no_positions(self):
        """Empty session: 0 trades, no positions, no loss limit breach."""
        settings = make_settings()
        subject, body = build_daily_summary(
            settings=settings,
            order_store=FakeOrderStore(pnl=0.0),
            position_store=FakePositionStore(),
            session_date=SESSION_DATE,
            daily_loss_limit_breached=False,
        )
        assert "2026-04-28" in subject
        assert "Trades       : 0" in body
        assert "Win rate" not in body
        assert "Daily loss limit breached: No" in body
        assert "Open positions: 0" in body

    def test_positive_pnl_multiple_trades(self):
        """3W/1L → 75.0%  (3W / 1L)."""
        trades = [
            _trade(exit_fill=155.0),  # win
            _trade(exit_fill=155.0),  # win
            _trade(exit_fill=155.0),  # win
            _trade(exit_fill=148.0),  # loss
        ]
        settings = make_settings()
        _, body = build_daily_summary(
            settings=settings,
            order_store=FakeOrderStore(trades=trades, pnl=142.50),
            position_store=FakePositionStore(),
            session_date=SESSION_DATE,
            daily_loss_limit_breached=False,
        )
        assert "Trades       : 4" in body
        assert "75.0%" in body
        assert "3W / 1L" in body
        assert "$142.50" in body

    def test_per_strategy_breakdown(self):
        """Two strategies appear separately in the breakdown."""
        trades = [
            _trade(strategy_name="breakout"),
            _trade(strategy_name="breakout"),
            _trade(strategy_name="momentum"),
        ]
        settings = make_settings()
        _, body = build_daily_summary(
            settings=settings,
            order_store=FakeOrderStore(trades=trades, pnl=50.0),
            position_store=FakePositionStore(),
            session_date=SESSION_DATE,
            daily_loss_limit_breached=False,
        )
        assert "breakout" in body
        assert "momentum" in body

    def test_open_positions_at_close(self):
        """Two open positions are listed with symbol, qty, entry, stop."""
        positions = [
            _position("AAPL", quantity=10, entry_price=150.0, stop_price=148.0),
            _position("MSFT", quantity=5, entry_price=420.0, stop_price=415.0),
        ]
        settings = make_settings()
        _, body = build_daily_summary(
            settings=settings,
            order_store=FakeOrderStore(),
            position_store=FakePositionStore(positions=positions),
            session_date=SESSION_DATE,
            daily_loss_limit_breached=False,
        )
        assert "Open positions: 2" in body
        assert "AAPL" in body
        assert "MSFT" in body

    def test_loss_limit_breached_true(self):
        """daily_loss_limit_breached=True shows 'Yes' in body."""
        settings = make_settings()
        _, body = build_daily_summary(
            settings=settings,
            order_store=FakeOrderStore(),
            position_store=FakePositionStore(),
            session_date=SESSION_DATE,
            daily_loss_limit_breached=True,
        )
        assert "Daily loss limit breached: Yes" in body

    def test_subject_contains_session_date_and_mode(self):
        """Subject includes ISO date and trading_mode value."""
        settings = make_settings()
        subject, _ = build_daily_summary(
            settings=settings,
            order_store=FakeOrderStore(),
            position_store=FakePositionStore(),
            session_date=SESSION_DATE,
            daily_loss_limit_breached=False,
        )
        assert "2026-04-28" in subject
        assert "paper" in subject

    def test_negative_pnl_formats_correctly(self):
        """Negative PnL shows as -$42.00, not $-42.00."""
        settings = make_settings()
        _, body = build_daily_summary(
            settings=settings,
            order_store=FakeOrderStore(pnl=-42.0),
            position_store=FakePositionStore(),
            session_date=SESSION_DATE,
            daily_loss_limit_breached=False,
        )
        assert "-$42.00" in body
        assert "$-" not in body


# ---------------------------------------------------------------------------
# Supervisor trigger tests
# ---------------------------------------------------------------------------


class RecordingNotifier:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def send(self, subject: str, body: str) -> None:
        self.calls.append((subject, body))


class FailingNotifier:
    def send(self, subject: str, body: str) -> None:
        raise RuntimeError("notifier down")


class SequencedFakeBroker:
    """Returns is_open values from a fixed sequence; repeats last value when exhausted."""

    def __init__(self, open_sequence: list[bool]) -> None:
        self._seq = list(open_sequence)
        self._idx = 0

    def get_clock(self):
        is_open = self._seq[min(self._idx, len(self._seq) - 1)]
        self._idx += 1
        return SimpleNamespace(is_open=is_open)


def _make_supervisor(
    *,
    open_sequence: list[bool],
    notifier=None,
    runtime_context=None,
):
    from alpaca_bot.runtime.supervisor import RuntimeSupervisor, SupervisorCycleReport

    settings = make_settings()

    if runtime_context is None:
        events: list = []
        runtime_context = SimpleNamespace(
            store_lock=None,
            order_store=FakeOrderStore(),
            position_store=FakePositionStore(),
            audit_event_store=SimpleNamespace(
                append=lambda e, *, commit=True: events.append(e)
            ),
            daily_session_state_store=None,
            trading_status_store=None,
            connection=SimpleNamespace(commit=lambda: None, rollback=lambda: None),
            _events=events,
        )

    broker = SequencedFakeBroker(open_sequence)

    supervisor = RuntimeSupervisor(
        settings=settings,
        runtime=runtime_context,
        broker=broker,
        market_data=None,
        stream=None,
        close_runtime_fn=lambda _r: None,
        connection_checker=lambda _c: True,
        notifier=notifier,
    )
    return supervisor, runtime_context


_BASE_TS = datetime(2026, 4, 28, 19, 0, tzinfo=timezone.utc)  # after-close ET


def _noop_cycle_report():
    from alpaca_bot.runtime.supervisor import SupervisorCycleReport

    return SupervisorCycleReport(
        entries_disabled=False,
        cycle_result=SimpleNamespace(intents=[]),
        dispatch_report={"submitted_count": 0},
    )


class TestSupervisorDailySummaryTrigger:
    def test_summary_sent_once_after_active_session_closes(self, monkeypatch):
        """open ×2 then closed ×2 → exactly one notifier call."""
        notifier = RecordingNotifier()
        supervisor, _ = _make_supervisor(
            open_sequence=[True, True, False, False],
            notifier=notifier,
        )
        report = _noop_cycle_report()
        monkeypatch.setattr(supervisor, "startup", lambda **_: None)
        monkeypatch.setattr(supervisor, "run_cycle_once", lambda **_: report)

        supervisor.run_forever(
            max_iterations=4,
            sleep_fn=lambda _: None,
            cycle_now=lambda: _BASE_TS,
        )

        assert len(notifier.calls) == 1
        subject, _ = notifier.calls[0]
        assert "2026-04-28" in subject

    def test_summary_not_sent_if_market_never_opened(self, monkeypatch):
        """Supervisor started after close — no active cycle → no summary."""
        notifier = RecordingNotifier()
        supervisor, _ = _make_supervisor(
            open_sequence=[False, False, False],
            notifier=notifier,
        )
        monkeypatch.setattr(supervisor, "startup", lambda **_: None)

        supervisor.run_forever(
            max_iterations=3,
            sleep_fn=lambda _: None,
            cycle_now=lambda: _BASE_TS,
        )

        assert notifier.calls == []

    def test_summary_not_sent_twice_same_day(self, monkeypatch):
        """open→closed→open→closed on same session_date → exactly 1 call."""
        notifier = RecordingNotifier()
        supervisor, _ = _make_supervisor(
            open_sequence=[True, False, True, False],
            notifier=notifier,
        )
        report = _noop_cycle_report()
        monkeypatch.setattr(supervisor, "startup", lambda **_: None)
        monkeypatch.setattr(supervisor, "run_cycle_once", lambda **_: report)

        supervisor.run_forever(
            max_iterations=4,
            sleep_fn=lambda _: None,
            cycle_now=lambda: _BASE_TS,
        )

        assert len(notifier.calls) == 1

    def test_summary_sent_per_day_on_multi_day_run(self, monkeypatch):
        """Two trading days → exactly 2 notifier calls with distinct dates in subjects."""
        notifier = RecordingNotifier()
        supervisor, _ = _make_supervisor(
            open_sequence=[True, False, True, False],
            notifier=notifier,
        )
        report = _noop_cycle_report()
        monkeypatch.setattr(supervisor, "startup", lambda **_: None)
        monkeypatch.setattr(supervisor, "run_cycle_once", lambda **_: report)

        _day1 = datetime(2026, 4, 28, 19, 0, tzinfo=timezone.utc)
        _day2 = datetime(2026, 4, 29, 19, 0, tzinfo=timezone.utc)
        call_counter = [0]

        def controlled_now():
            idx = call_counter[0]
            call_counter[0] += 1
            return _day1 if idx < 2 else _day2

        supervisor.run_forever(
            max_iterations=4,
            sleep_fn=lambda _: None,
            cycle_now=controlled_now,
        )

        assert len(notifier.calls) == 2
        subjects = [s for s, _ in notifier.calls]
        assert "2026-04-28" in subjects[0]
        assert "2026-04-29" in subjects[1]

    def test_summary_not_sent_when_notifier_is_none(self, monkeypatch):
        """notifier=None → no exception raised even after active session closes."""
        supervisor, _ = _make_supervisor(
            open_sequence=[True, False],
            notifier=None,
        )
        report = _noop_cycle_report()
        monkeypatch.setattr(supervisor, "startup", lambda **_: None)
        monkeypatch.setattr(supervisor, "run_cycle_once", lambda **_: report)

        supervisor.run_forever(
            max_iterations=2,
            sleep_fn=lambda _: None,
            cycle_now=lambda: _BASE_TS,
        )

    def test_notifier_failure_does_not_abort_loop(self, monkeypatch):
        """Notifier raising on send() → loop continues, audit event not appended."""
        notifier = FailingNotifier()
        supervisor, ctx = _make_supervisor(
            open_sequence=[True, False, False],
            notifier=notifier,
        )
        report = _noop_cycle_report()
        monkeypatch.setattr(supervisor, "startup", lambda **_: None)
        monkeypatch.setattr(supervisor, "run_cycle_once", lambda **_: report)

        supervisor.run_forever(
            max_iterations=3,
            sleep_fn=lambda _: None,
            cycle_now=lambda: _BASE_TS,
        )

        assert not any(
            getattr(e, "event_type", None) == "daily_summary_sent"
            for e in ctx._events
        )

    def test_audit_event_appended_on_success(self, monkeypatch):
        """Successful send → daily_summary_sent audit event with session_date payload."""
        notifier = RecordingNotifier()
        supervisor, ctx = _make_supervisor(
            open_sequence=[True, False],
            notifier=notifier,
        )
        report = _noop_cycle_report()
        monkeypatch.setattr(supervisor, "startup", lambda **_: None)
        monkeypatch.setattr(supervisor, "run_cycle_once", lambda **_: report)

        supervisor.run_forever(
            max_iterations=2,
            sleep_fn=lambda _: None,
            cycle_now=lambda: _BASE_TS,
        )

        summary_events = [
            e for e in ctx._events
            if getattr(e, "event_type", None) == "daily_summary_sent"
        ]
        assert len(summary_events) == 1
        assert summary_events[0].payload["session_date"] == "2026-04-28"
