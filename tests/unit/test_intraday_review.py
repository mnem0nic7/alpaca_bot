from __future__ import annotations

from datetime import date, datetime, timezone
from importlib import import_module
from types import SimpleNamespace

import pytest

from alpaca_bot.config import Settings
from alpaca_bot.runtime.daily_summary import trailing_consecutive_losses, build_intraday_digest
from alpaca_bot.storage import AuditEvent, DailySessionState
from alpaca_bot.execution import BrokerAccount


# ── Shared helpers ────────────────────────────────────────────────────────────

_SESSION_DATE = date(2026, 5, 6)
_NOW = datetime(2026, 5, 6, 18, 30, tzinfo=timezone.utc)  # 14:30 ET


def _make_settings(**overrides) -> Settings:
    base = {
        "TRADING_MODE": "paper",
        "ENABLE_LIVE_TRADING": "false",
        "STRATEGY_VERSION": "v1-breakout",
        "DATABASE_URL": "postgresql://x:y@localhost/db",
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
    base.update(overrides)
    return Settings.from_env(base)


def _trade(
    exit_fill: float = 155.0,
    entry_fill: float = 150.0,
    qty: int = 10,
    exit_time: str = "2026-05-06T14:00:00+00:00",
) -> dict:
    return {
        "entry_fill": entry_fill,
        "exit_fill": exit_fill,
        "qty": qty,
        "exit_time": exit_time,
    }


# ── Settings tests ────────────────────────────────────────────────────────────


def test_settings_intraday_digest_interval_cycles_default_zero():
    s = _make_settings()
    assert s.intraday_digest_interval_cycles == 0


def test_settings_intraday_consecutive_loss_gate_default_zero():
    s = _make_settings()
    assert s.intraday_consecutive_loss_gate == 0


def test_settings_intraday_digest_interval_cycles_parsed():
    s = _make_settings(INTRADAY_DIGEST_INTERVAL_CYCLES="60")
    assert s.intraday_digest_interval_cycles == 60


def test_settings_intraday_consecutive_loss_gate_parsed():
    s = _make_settings(INTRADAY_CONSECUTIVE_LOSS_GATE="3")
    assert s.intraday_consecutive_loss_gate == 3


def test_settings_intraday_digest_interval_cycles_negative_raises():
    with pytest.raises(ValueError, match="INTRADAY_DIGEST_INTERVAL_CYCLES"):
        _make_settings(INTRADAY_DIGEST_INTERVAL_CYCLES="-1")


def test_settings_intraday_consecutive_loss_gate_negative_raises():
    with pytest.raises(ValueError, match="INTRADAY_CONSECUTIVE_LOSS_GATE"):
        _make_settings(INTRADAY_CONSECUTIVE_LOSS_GATE="-1")


# ── trailing_consecutive_losses tests ─────────────────────────────────────────


class TestTrailingConsecutiveLosses:
    def test_empty_list_returns_zero(self):
        assert trailing_consecutive_losses([]) == 0

    def test_all_wins_returns_zero(self):
        trades = [
            _trade(exit_fill=155.0, entry_fill=150.0, exit_time="2026-05-06T14:01:00+00:00"),
            _trade(exit_fill=156.0, entry_fill=150.0, exit_time="2026-05-06T14:02:00+00:00"),
        ]
        assert trailing_consecutive_losses(trades) == 0

    def test_all_losses_returns_count(self):
        trades = [
            _trade(exit_fill=148.0, entry_fill=150.0, exit_time="2026-05-06T14:01:00+00:00"),
            _trade(exit_fill=147.0, entry_fill=150.0, exit_time="2026-05-06T14:02:00+00:00"),
            _trade(exit_fill=146.0, entry_fill=150.0, exit_time="2026-05-06T14:03:00+00:00"),
        ]
        assert trailing_consecutive_losses(trades) == 3

    def test_win_after_losses_returns_zero(self):
        """Most recent trade is a win — streak resets."""
        trades = [
            _trade(exit_fill=148.0, entry_fill=150.0, exit_time="2026-05-06T14:01:00+00:00"),
            _trade(exit_fill=147.0, entry_fill=150.0, exit_time="2026-05-06T14:02:00+00:00"),
            _trade(exit_fill=160.0, entry_fill=150.0, exit_time="2026-05-06T14:03:00+00:00"),
        ]
        assert trailing_consecutive_losses(trades) == 0

    def test_losses_after_win_returns_loss_count(self):
        """Most recent trades are losses — streak counts them, stops at earlier win."""
        trades = [
            _trade(exit_fill=160.0, entry_fill=150.0, exit_time="2026-05-06T14:01:00+00:00"),
            _trade(exit_fill=148.0, entry_fill=150.0, exit_time="2026-05-06T14:02:00+00:00"),
            _trade(exit_fill=147.0, entry_fill=150.0, exit_time="2026-05-06T14:03:00+00:00"),
        ]
        assert trailing_consecutive_losses(trades) == 2

    def test_trades_missing_fills_are_skipped(self):
        """Trades without entry_fill or exit_fill are ignored (not counted as win or loss)."""
        trades = [
            {"entry_fill": None, "exit_fill": None, "qty": 10, "exit_time": "2026-05-06T14:01:00+00:00"},
            _trade(exit_fill=148.0, entry_fill=150.0, exit_time="2026-05-06T14:02:00+00:00"),
        ]
        # Only one scoreable trade, which is a loss → streak = 1
        assert trailing_consecutive_losses(trades) == 1

    def test_only_missing_fills_returns_zero(self):
        trades = [
            {"entry_fill": None, "exit_fill": None, "qty": 10, "exit_time": "2026-05-06T14:01:00+00:00"},
        ]
        assert trailing_consecutive_losses(trades) == 0

    def test_sorted_by_exit_time(self):
        """Streak is computed from exit_time order, not list order."""
        trades = [
            # This loss appears first in the list but has a later exit_time
            _trade(exit_fill=148.0, entry_fill=150.0, exit_time="2026-05-06T14:02:00+00:00"),
            # This win appears second but has an earlier exit_time
            _trade(exit_fill=160.0, entry_fill=150.0, exit_time="2026-05-06T14:01:00+00:00"),
        ]
        # After sorting by exit_time: win (14:01), loss (14:02) → most recent is loss → streak=1
        assert trailing_consecutive_losses(trades) == 1

    def test_breakeven_trade_counts_as_loss(self):
        """exit_fill == entry_fill is not a win — counts toward loss streak."""
        trades = [
            _trade(exit_fill=150.0, entry_fill=150.0, exit_time="2026-05-06T14:01:00+00:00"),
        ]
        assert trailing_consecutive_losses(trades) == 1


# ── build_intraday_digest tests ───────────────────────────────────────────────


class TestBuildIntradayDigest:
    def _call(self, **overrides):
        defaults = dict(
            settings=_make_settings(INTRADAY_DIGEST_INTERVAL_CYCLES="60"),
            trades=[],
            open_positions=[],
            baseline_equity=46_100.0,
            current_equity=46_242.80,
            cycle_num=60,
            timestamp=_NOW,
            session_date=_SESSION_DATE,
        )
        defaults.update(overrides)
        return build_intraday_digest(**defaults)

    def test_subject_contains_session_date_mode_and_time(self):
        subject, _ = self._call()
        assert "2026-05-06" in subject
        assert "paper" in subject
        assert "14:30" in subject  # _NOW is 18:30 UTC = 14:30 ET

    def test_subject_is_intraday_digest(self):
        subject, _ = self._call()
        assert "Intra-day digest" in subject

    def test_body_contains_cycle_info(self):
        _, body = self._call(cycle_num=60)
        assert "Cycle: 60/60" in body

    def test_body_with_zero_trades(self):
        _, body = self._call(trades=[])
        assert "Trades: 0" in body
        assert "Win rate" not in body

    def test_body_with_trades_shows_win_rate(self):
        trades = [
            _trade(exit_fill=155.0, entry_fill=150.0),  # win
            _trade(exit_fill=155.0, entry_fill=150.0),  # win
            _trade(exit_fill=155.0, entry_fill=150.0),  # win
            _trade(exit_fill=148.0, entry_fill=150.0),  # loss
        ]
        _, body = self._call(trades=trades)
        assert "75.0%" in body
        assert "3W / 1L" in body

    def test_body_shows_pnl(self):
        trades = [_trade(exit_fill=155.0, entry_fill=150.0, qty=10)]  # $50 gain
        _, body = self._call(trades=trades)
        assert "$50.00" in body

    def test_loss_limit_headroom_calculation(self):
        # baseline=46100, daily_loss_limit_pct=0.01 → limit=461.00
        # current=45900 → session_pnl=-200 → headroom=461-200=261.00
        _, body = self._call(
            settings=_make_settings(INTRADAY_DIGEST_INTERVAL_CYCLES="60", DAILY_LOSS_LIMIT_PCT="0.01"),
            baseline_equity=46_100.0,
            current_equity=45_900.0,
        )
        assert "$261.00" in body
        assert "$461.00" in body

    def test_open_positions_listed(self):
        positions = [
            SimpleNamespace(symbol="AAPL", quantity=10, entry_price=182.50),
            SimpleNamespace(symbol="MSFT", quantity=5, entry_price=415.20),
        ]
        _, body = self._call(open_positions=positions)
        assert "Open positions: 2" in body
        assert "AAPL x10 @ 182.50" in body
        assert "MSFT x5 @ 415.20" in body

    def test_no_open_positions(self):
        _, body = self._call(open_positions=[])
        assert "Open positions: 0" in body

    def test_loss_limit_headroom_exactly_consumed(self):
        """When equity exactly hits the loss limit, headroom is $0.00."""
        _, body = self._call(
            settings=_make_settings(INTRADAY_DIGEST_INTERVAL_CYCLES="60", DAILY_LOSS_LIMIT_PCT="0.01"),
            baseline_equity=46_100.0,
            current_equity=45_639.0,  # exactly at limit: 46100 - 461 = 45639
        )
        assert "$0.00" in body

    def test_loss_limit_headroom_below_zero_clamped_to_zero(self):
        """When equity falls past the loss limit, headroom stays at $0.00 (not negative)."""
        _, body = self._call(
            settings=_make_settings(INTRADAY_DIGEST_INTERVAL_CYCLES="60", DAILY_LOSS_LIMIT_PCT="0.01"),
            baseline_equity=46_100.0,
            current_equity=45_000.0,  # 1100 loss, limit is 461 → well past limit
        )
        assert "$0.00" in body


# ── Supervisor test helpers ───────────────────────────────────────────────────


def _load_supervisor_api():
    module = import_module("alpaca_bot.runtime.supervisor")
    return module, module.RuntimeSupervisor


class _FakeConn:
    def commit(self) -> None:
        pass

    def rollback(self) -> None:
        pass


class _RecordingAuditStore:
    def __init__(self) -> None:
        self.appended: list[AuditEvent] = []

    def append(self, event: AuditEvent, *, commit: bool = True) -> None:
        self.appended.append(event)

    def load_latest(self, **kwargs) -> AuditEvent | None:
        return None

    def list_recent(self, **kwargs) -> list[AuditEvent]:
        return []

    def list_by_event_types(self, **kwargs) -> list[AuditEvent]:
        return []


class _RecordingOrderStore:
    def __init__(self, *, daily_pnl: float = 0.0, closed_trades: list | None = None) -> None:
        self._daily_pnl = daily_pnl
        self._closed_trades: list[dict] = closed_trades or []

    def daily_realized_pnl(self, **kwargs) -> float:
        return self._daily_pnl

    def daily_realized_pnl_by_symbol(self, **kwargs) -> dict[str, float]:
        return {}

    def list_by_status(self, **kwargs) -> list:
        return []

    def list_pending_submit(self, **kwargs) -> list:
        return []

    def list_closed_trades(self, **kwargs) -> list[dict]:
        return list(self._closed_trades)


class _RecordingSessionStateStore:
    def __init__(self) -> None:
        self.saved: list[DailySessionState] = []

    def load(self, *, session_date, trading_mode, strategy_version, strategy_name="breakout"):
        return None

    def save(self, state: DailySessionState) -> None:
        self.saved.append(state)

    def list_by_session(self, **kwargs) -> list:
        return []


class _RecordingNotifier:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    def send(self, subject: str, body: str) -> None:
        self.sent.append((subject, body))


class _FakeTradingStatusStore:
    def load(self, **kwargs):
        return None


class _FakePositionStore:
    def list_all(self, **kwargs) -> list:
        return []

    def replace_all(self, **kwargs) -> None:
        pass


class _FakeStrategyFlagStore:
    def list_all(self, **kwargs) -> list:
        return []

    def load(self, **kwargs):
        return None


class _FakeWatchlistStore:
    def list_enabled(self, *args) -> list[str]:
        return ["AAPL"]

    def list_ignored(self, *args) -> list[str]:
        return []


def _make_supervisor(
    *,
    settings: Settings,
    closed_trades: list[dict] | None = None,
    notifier=None,
    session_state_store=None,
):
    _, RuntimeSupervisor = _load_supervisor_api()

    class _FakeBroker:
        def get_account(self):
            return BrokerAccount(equity=10_000.0, buying_power=20_000.0, trading_blocked=False)

        def list_open_orders(self):
            return []

    class _FakeMarketData:
        def get_stock_bars(self, **kwargs):
            return {}

        def get_daily_bars(self, **kwargs):
            return {}

    _order_store = _RecordingOrderStore(closed_trades=closed_trades or [])
    _sess_store = session_state_store or _RecordingSessionStateStore()
    _audit_store = _RecordingAuditStore()

    class _FakeRuntimeContext:
        connection = _FakeConn()
        store_lock = None
        order_store = _order_store
        trading_status_store = _FakeTradingStatusStore()
        position_store = _FakePositionStore()
        daily_session_state_store = _sess_store
        audit_event_store = _audit_store
        strategy_flag_store = _FakeStrategyFlagStore()
        watchlist_store = _FakeWatchlistStore()

        def commit(self):
            pass

    sup = RuntimeSupervisor(
        settings=settings,
        runtime=_FakeRuntimeContext(),
        broker=_FakeBroker(),
        market_data=_FakeMarketData(),
        stream=None,
        close_runtime_fn=lambda _: None,
        connection_checker=lambda _: True,
        cycle_runner=lambda **kwargs: SimpleNamespace(intents=[]),
        cycle_intent_executor=lambda **kwargs: SimpleNamespace(
            submitted_exit_count=0, failed_exit_count=0
        ),
        order_dispatcher=lambda **kwargs: {"submitted_count": 0},
    )
    sup._session_equity_baseline[_SESSION_DATE] = 10_000.0
    if notifier is not None:
        sup._notifier = notifier
    return sup, _FakeRuntimeContext()


# ── _maybe_fire_consecutive_loss_gate tests ───────────────────────────────────


class TestMaybeFireConsecutiveLossGate:
    def test_gate_disabled_does_not_fire(self):
        """INTRADAY_CONSECUTIVE_LOSS_GATE=0 → gate never fires."""
        settings = _make_settings(INTRADAY_CONSECUTIVE_LOSS_GATE="0")
        notifier = _RecordingNotifier()
        sup, _ = _make_supervisor(settings=settings, notifier=notifier)

        sup._maybe_fire_consecutive_loss_gate(
            session_date=_SESSION_DATE,
            consecutive_losses=5,
            timestamp=_NOW,
        )

        assert _SESSION_DATE not in sup._consecutive_loss_gate_fired
        assert notifier.sent == []

    def test_gate_fires_when_threshold_met(self):
        """consecutive_losses >= gate → adds to fired set, notifies."""
        settings = _make_settings(INTRADAY_CONSECUTIVE_LOSS_GATE="3")
        notifier = _RecordingNotifier()
        sup, _ = _make_supervisor(settings=settings, notifier=notifier)

        sup._maybe_fire_consecutive_loss_gate(
            session_date=_SESSION_DATE,
            consecutive_losses=3,
            timestamp=_NOW,
        )

        assert _SESSION_DATE in sup._consecutive_loss_gate_fired
        assert len(notifier.sent) == 1
        assert "3 consecutive losses" in notifier.sent[0][1]

    def test_gate_does_not_fire_below_threshold(self):
        """consecutive_losses < gate → no fire."""
        settings = _make_settings(INTRADAY_CONSECUTIVE_LOSS_GATE="3")
        notifier = _RecordingNotifier()
        sup, _ = _make_supervisor(settings=settings, notifier=notifier)

        sup._maybe_fire_consecutive_loss_gate(
            session_date=_SESSION_DATE,
            consecutive_losses=2,
            timestamp=_NOW,
        )

        assert _SESSION_DATE not in sup._consecutive_loss_gate_fired
        assert notifier.sent == []

    def test_gate_does_not_fire_twice(self):
        """Gate fires at most once per session date."""
        settings = _make_settings(INTRADAY_CONSECUTIVE_LOSS_GATE="3")
        notifier = _RecordingNotifier()
        sup, _ = _make_supervisor(settings=settings, notifier=notifier)

        sup._maybe_fire_consecutive_loss_gate(
            session_date=_SESSION_DATE, consecutive_losses=3, timestamp=_NOW
        )
        sup._maybe_fire_consecutive_loss_gate(
            session_date=_SESSION_DATE, consecutive_losses=4, timestamp=_NOW
        )

        assert len(notifier.sent) == 1

    def test_gate_appends_audit_event(self):
        """Firing the gate appends an intraday_consecutive_loss_gate audit event."""
        settings = _make_settings(INTRADAY_CONSECUTIVE_LOSS_GATE="3")
        sup, ctx = _make_supervisor(settings=settings)

        sup._maybe_fire_consecutive_loss_gate(
            session_date=_SESSION_DATE, consecutive_losses=3, timestamp=_NOW
        )

        gate_events = [
            e for e in ctx.audit_event_store.appended
            if e.event_type == "intraday_consecutive_loss_gate"
        ]
        assert len(gate_events) == 1
        assert gate_events[0].payload["consecutive_losses"] == 3
        assert gate_events[0].payload["threshold"] == 3

    def test_gate_saves_entries_disabled_session_state(self):
        """Firing the gate saves DailySessionState with entries_disabled=True."""
        settings = _make_settings(INTRADAY_CONSECUTIVE_LOSS_GATE="3")
        sess_store = _RecordingSessionStateStore()
        sup, _ = _make_supervisor(settings=settings, session_state_store=sess_store)

        sup._maybe_fire_consecutive_loss_gate(
            session_date=_SESSION_DATE, consecutive_losses=3, timestamp=_NOW
        )

        assert len(sess_store.saved) == 1
        assert sess_store.saved[0].entries_disabled is True
        assert sess_store.saved[0].session_date == _SESSION_DATE

    def test_gate_notifier_optional(self):
        """Gate fires (audit event, fired set) even when notifier is None."""
        settings = _make_settings(INTRADAY_CONSECUTIVE_LOSS_GATE="3")
        sup, ctx = _make_supervisor(settings=settings, notifier=None)

        sup._maybe_fire_consecutive_loss_gate(
            session_date=_SESSION_DATE, consecutive_losses=3, timestamp=_NOW
        )

        assert _SESSION_DATE in sup._consecutive_loss_gate_fired
        gate_events = [
            e for e in ctx.audit_event_store.appended
            if e.event_type == "intraday_consecutive_loss_gate"
        ]
        assert len(gate_events) == 1

    def test_gate_db_failure_still_adds_to_fired_set(self):
        """When DailySessionState save raises, gate still marks session fired and audits."""
        class _FailingSessionStateStore(_RecordingSessionStateStore):
            def save(self, state):
                raise RuntimeError("DB unavailable")

        settings = _make_settings(INTRADAY_CONSECUTIVE_LOSS_GATE="3")
        sup, ctx = _make_supervisor(
            settings=settings, session_state_store=_FailingSessionStateStore()
        )

        sup._maybe_fire_consecutive_loss_gate(
            session_date=_SESSION_DATE, consecutive_losses=3, timestamp=_NOW
        )

        assert _SESSION_DATE in sup._consecutive_loss_gate_fired
        gate_events = [
            e for e in ctx.audit_event_store.appended
            if e.event_type == "intraday_consecutive_loss_gate"
        ]
        assert len(gate_events) == 1


# ── _maybe_send_intraday_digest tests ────────────────────────────────────────


class TestMaybeSendIntradayDigest:
    def test_digest_disabled_does_not_send(self):
        """INTRADAY_DIGEST_INTERVAL_CYCLES=0 → digest never sends."""
        settings = _make_settings(INTRADAY_DIGEST_INTERVAL_CYCLES="0")
        notifier = _RecordingNotifier()
        sup, _ = _make_supervisor(settings=settings, notifier=notifier)
        sup._session_cycle_count[_SESSION_DATE] = 60

        sup._maybe_send_intraday_digest(
            session_date=_SESSION_DATE,
            closed_trades=[_trade()],
            baseline_equity=10_000.0,
            current_equity=10_142.80,
            timestamp=_NOW,
        )

        assert notifier.sent == []

    def test_digest_sends_at_interval(self):
        """Sends when cycle_num % interval == 0 and cycle_num > 0 and trades present."""
        settings = _make_settings(INTRADAY_DIGEST_INTERVAL_CYCLES="60")
        notifier = _RecordingNotifier()
        sup, _ = _make_supervisor(settings=settings, notifier=notifier)
        sup._session_cycle_count[_SESSION_DATE] = 60

        sup._maybe_send_intraday_digest(
            session_date=_SESSION_DATE,
            closed_trades=[_trade()],
            baseline_equity=10_000.0,
            current_equity=10_050.0,
            timestamp=_NOW,
        )

        assert len(notifier.sent) == 1
        assert "Intra-day digest" in notifier.sent[0][0]

    def test_digest_does_not_send_between_intervals(self):
        """No send when cycle_num % interval != 0."""
        settings = _make_settings(INTRADAY_DIGEST_INTERVAL_CYCLES="60")
        notifier = _RecordingNotifier()
        sup, _ = _make_supervisor(settings=settings, notifier=notifier)
        sup._session_cycle_count[_SESSION_DATE] = 45  # not a multiple of 60

        sup._maybe_send_intraday_digest(
            session_date=_SESSION_DATE,
            closed_trades=[_trade()],
            baseline_equity=10_000.0,
            current_equity=10_050.0,
            timestamp=_NOW,
        )

        assert notifier.sent == []

    def test_digest_does_not_send_with_zero_trades(self):
        """No send when closed_trades is empty."""
        settings = _make_settings(INTRADAY_DIGEST_INTERVAL_CYCLES="60")
        notifier = _RecordingNotifier()
        sup, _ = _make_supervisor(settings=settings, notifier=notifier)
        sup._session_cycle_count[_SESSION_DATE] = 60

        sup._maybe_send_intraday_digest(
            session_date=_SESSION_DATE,
            closed_trades=[],
            baseline_equity=10_000.0,
            current_equity=10_000.0,
            timestamp=_NOW,
        )

        assert notifier.sent == []

    def test_digest_does_not_send_at_cycle_zero(self):
        """No send at cycle 0 even if interval divides it."""
        settings = _make_settings(INTRADAY_DIGEST_INTERVAL_CYCLES="60")
        notifier = _RecordingNotifier()
        sup, _ = _make_supervisor(settings=settings, notifier=notifier)
        sup._session_cycle_count[_SESSION_DATE] = 0

        sup._maybe_send_intraday_digest(
            session_date=_SESSION_DATE,
            closed_trades=[_trade()],
            baseline_equity=10_000.0,
            current_equity=10_050.0,
            timestamp=_NOW,
        )

        assert notifier.sent == []

    def test_digest_appends_audit_event(self):
        """Successful send appends intraday_digest_sent audit event."""
        settings = _make_settings(INTRADAY_DIGEST_INTERVAL_CYCLES="60")
        notifier = _RecordingNotifier()
        sup, ctx = _make_supervisor(settings=settings, notifier=notifier)
        sup._session_cycle_count[_SESSION_DATE] = 60

        sup._maybe_send_intraday_digest(
            session_date=_SESSION_DATE,
            closed_trades=[_trade()],
            baseline_equity=10_000.0,
            current_equity=10_050.0,
            timestamp=_NOW,
        )

        digest_events = [
            e for e in ctx.audit_event_store.appended
            if e.event_type == "intraday_digest_sent"
        ]
        assert len(digest_events) == 1
        assert digest_events[0].payload["cycle"] == 60
        assert digest_events[0].payload["digest_num"] == 1

    def test_digest_does_not_send_without_notifier(self):
        """No notifier → returns immediately, no audit event, no error."""
        settings = _make_settings(INTRADAY_DIGEST_INTERVAL_CYCLES="60")
        sup, ctx = _make_supervisor(settings=settings, notifier=None)
        sup._session_cycle_count[_SESSION_DATE] = 60

        sup._maybe_send_intraday_digest(
            session_date=_SESSION_DATE,
            closed_trades=[_trade()],
            baseline_equity=10_000.0,
            current_equity=10_050.0,
            timestamp=_NOW,
        )

        digest_events = [
            e for e in ctx.audit_event_store.appended
            if e.event_type == "intraday_digest_sent"
        ]
        assert digest_events == []

    def test_digest_updates_sent_count(self):
        """_digest_sent_count tracks digest_num correctly (cycle=20, interval=10 → num=2)."""
        settings = _make_settings(INTRADAY_DIGEST_INTERVAL_CYCLES="10")
        notifier = _RecordingNotifier()
        sup, _ = _make_supervisor(settings=settings, notifier=notifier)
        sup._session_cycle_count[_SESSION_DATE] = 20

        sup._maybe_send_intraday_digest(
            session_date=_SESSION_DATE,
            closed_trades=[_trade()],
            baseline_equity=10_000.0,
            current_equity=10_050.0,
            timestamp=_NOW,
        )

        assert sup._digest_sent_count[_SESSION_DATE] == 2

    def test_digest_notifier_failure_does_not_raise(self):
        """Notifier failure is logged, not propagated."""
        class _FailingNotifier:
            def send(self, *args, **kwargs):
                raise RuntimeError("SMTP down")

        settings = _make_settings(INTRADAY_DIGEST_INTERVAL_CYCLES="60")
        sup, _ = _make_supervisor(settings=settings, notifier=_FailingNotifier())
        sup._session_cycle_count[_SESSION_DATE] = 60

        # Must not raise
        sup._maybe_send_intraday_digest(
            session_date=_SESSION_DATE,
            closed_trades=[_trade()],
            baseline_equity=10_000.0,
            current_equity=10_050.0,
            timestamp=_NOW,
        )


# ── run_cycle_once integration tests ─────────────────────────────────────────


from alpaca_bot.strategy.session import SessionType


class TestRunCycleOnceIntegration:
    def _run(self, *, settings, closed_trades=None, notifier=None):
        sup, _ = _make_supervisor(
            settings=settings,
            closed_trades=closed_trades or [],
            notifier=notifier,
        )
        sup.run_cycle_once(
            now=lambda: _NOW,
            session_type=SessionType.REGULAR,
        )
        return sup

    def test_gate_fires_and_entries_disabled_in_cycle(self):
        """After gate fires, run_cycle_once returns entries_disabled=True."""
        settings = _make_settings(INTRADAY_CONSECUTIVE_LOSS_GATE="2")
        losses = [
            _trade(exit_fill=148.0, entry_fill=150.0, exit_time="2026-05-06T14:01:00+00:00"),
            _trade(exit_fill=147.0, entry_fill=150.0, exit_time="2026-05-06T14:02:00+00:00"),
        ]
        module, RuntimeSupervisor = _load_supervisor_api()
        sup, ctx = _make_supervisor(
            settings=settings,
            closed_trades=losses,
        )
        report = sup.run_cycle_once(
            now=lambda: _NOW,
            session_type=SessionType.REGULAR,
        )
        assert report.entries_disabled is True
        assert _SESSION_DATE in sup._consecutive_loss_gate_fired

    def test_gate_does_not_fire_during_non_regular_session(self):
        """Gate is skipped when session_type is AFTER_HOURS (non-REGULAR)."""
        settings = _make_settings(INTRADAY_CONSECUTIVE_LOSS_GATE="2")
        losses = [
            _trade(exit_fill=148.0, entry_fill=150.0, exit_time="2026-05-06T14:01:00+00:00"),
            _trade(exit_fill=147.0, entry_fill=150.0, exit_time="2026-05-06T14:02:00+00:00"),
        ]
        sup, _ = _make_supervisor(settings=settings, closed_trades=losses)
        sup.run_cycle_once(
            now=lambda: _NOW,
            session_type=SessionType.AFTER_HOURS,
        )
        assert _SESSION_DATE not in sup._consecutive_loss_gate_fired

    def test_session_cycle_count_increments_only_during_regular(self):
        """_session_cycle_count increments each REGULAR cycle, not for AFTER_HOURS."""
        settings = _make_settings()
        sup, _ = _make_supervisor(settings=settings)

        sup.run_cycle_once(now=lambda: _NOW, session_type=SessionType.REGULAR)
        sup.run_cycle_once(now=lambda: _NOW, session_type=SessionType.REGULAR)
        sup.run_cycle_once(now=lambda: _NOW, session_type=SessionType.AFTER_HOURS)

        assert sup._session_cycle_count.get(_SESSION_DATE, 0) == 2

    def test_digest_sends_at_interval_via_run_cycle_once(self):
        """Digest sends when session_cycle_count hits the interval."""
        settings = _make_settings(INTRADAY_DIGEST_INTERVAL_CYCLES="2")
        notifier = _RecordingNotifier()
        trades = [_trade()]
        sup, _ = _make_supervisor(settings=settings, closed_trades=trades, notifier=notifier)

        # First regular cycle: cycle_num=1, not a multiple of 2 → no send
        sup.run_cycle_once(now=lambda: _NOW, session_type=SessionType.REGULAR)
        assert len(notifier.sent) == 0

        # Second regular cycle: cycle_num=2, 2 % 2 == 0 → send
        sup.run_cycle_once(now=lambda: _NOW, session_type=SessionType.REGULAR)
        assert len(notifier.sent) == 1
        assert "Intra-day digest" in notifier.sent[0][0]
