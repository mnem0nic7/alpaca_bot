from __future__ import annotations

from datetime import date, datetime, timezone
from importlib import import_module
from types import SimpleNamespace

import pytest

from alpaca_bot.config import Settings, TradingMode
from alpaca_bot.storage import StrategyFlag
from tests.unit.helpers import _base_env


# ---------------------------------------------------------------------------
# Task 1: Settings config fields
# ---------------------------------------------------------------------------


def test_settings_circuit_breaker_defaults():
    """New fields default to 0.0 and 7 — feature off by default."""
    s = Settings.from_env(_base_env())
    assert s.option_strategy_max_rolling_loss_usd == 0.0
    assert s.option_strategy_rolling_loss_days == 7


def test_settings_circuit_breaker_parsed_from_env():
    """Both fields are parsed from env vars."""
    env = {
        **_base_env(),
        "OPTION_STRATEGY_MAX_ROLLING_LOSS_USD": "500.0",
        "OPTION_STRATEGY_ROLLING_LOSS_DAYS": "14",
    }
    s = Settings.from_env(env)
    assert s.option_strategy_max_rolling_loss_usd == 500.0
    assert s.option_strategy_rolling_loss_days == 14


def test_settings_circuit_breaker_rejects_negative_loss():
    env = {**_base_env(), "OPTION_STRATEGY_MAX_ROLLING_LOSS_USD": "-1.0"}
    with pytest.raises(ValueError, match="OPTION_STRATEGY_MAX_ROLLING_LOSS_USD"):
        Settings.from_env(env)


def test_settings_circuit_breaker_rejects_zero_days():
    env = {**_base_env(), "OPTION_STRATEGY_ROLLING_LOSS_DAYS": "0"}
    with pytest.raises(ValueError, match="OPTION_STRATEGY_ROLLING_LOSS_DAYS"):
        Settings.from_env(env)


# ---------------------------------------------------------------------------
# Task 2: OptionOrderRepository.rolling_realized_pnl_by_strategy
# ---------------------------------------------------------------------------


def test_rolling_realized_pnl_aggregates_by_strategy():
    """rolling_realized_pnl_by_strategy sums P&L per strategy_name over closed trades."""
    from alpaca_bot.storage.repositories import OptionOrderRepository

    class _FakeRepo(OptionOrderRepository):
        def __init__(self):
            pass  # skip DB connection in parent __init__

        def list_closed_option_trade_records(self, **kw):
            return [
                {"strategy_name": "bear_orb", "pnl": -300.0},
                {"strategy_name": "bear_orb", "pnl": -200.0},
                {"strategy_name": "bear_momentum", "pnl": 50.0},
            ]

    repo = _FakeRepo()
    result = repo.rolling_realized_pnl_by_strategy(
        trading_mode=TradingMode.PAPER,
        strategy_version="v1",
        since_date=date(2026, 5, 22),
        until_date=date(2026, 5, 28),
    )
    assert result == {"bear_orb": -500.0, "bear_momentum": 50.0}


def test_rolling_realized_pnl_empty_when_no_trades():
    """Returns empty dict when no closed trades in window."""
    from alpaca_bot.storage.repositories import OptionOrderRepository

    class _FakeRepo(OptionOrderRepository):
        def __init__(self):
            pass

        def list_closed_option_trade_records(self, **kw):
            return []

    repo = _FakeRepo()
    result = repo.rolling_realized_pnl_by_strategy(
        trading_mode=TradingMode.PAPER,
        strategy_version="v1",
        since_date=date(2026, 5, 22),
        until_date=date(2026, 5, 28),
    )
    assert result == {}


# ---------------------------------------------------------------------------
# Task 3: Supervisor _check_option_strategy_circuit_breakers
# ---------------------------------------------------------------------------


def _make_circuit_breaker_supervisor(
    *,
    rolling_pnl_by_strategy: dict,
    existing_flag: StrategyFlag | None = None,
    max_rolling_loss_usd: float = 500.0,
):
    """Build a minimal supervisor with controllable rolling P&L and flag state.

    Returns (supervisor, saved_flags_list, audit_events_list).
    """
    module = import_module("alpaca_bot.runtime.supervisor")
    RuntimeSupervisor = module.RuntimeSupervisor

    env = {
        **_base_env(),
        "OPTION_STRATEGY_MAX_ROLLING_LOSS_USD": str(max_rolling_loss_usd),
        "OPTION_STRATEGY_ROLLING_LOSS_DAYS": "7",
    }
    settings = Settings.from_env(env)

    saved_flags: list[StrategyFlag] = []
    audit_events: list = []

    class _FakeOptStore:
        def rolling_realized_pnl_by_strategy(self, **kw):
            return rolling_pnl_by_strategy

        def list_open_option_positions(self, **kw):
            return []

        def list_pending_submit(self, **kw):
            return []

        def list_trade_pnl_by_strategy(self, **kw):
            return []

    class _FakeFlagStore:
        def load(self, **kw):
            return existing_flag

        def save(self, flag):
            saved_flags.append(flag)

        def list_all(self, **kw):
            return []

    class _FakeConn:
        def commit(self):
            pass

        def rollback(self):
            pass

    class _FakeRuntime:
        connection = _FakeConn()
        store_lock = None
        audit_event_store = SimpleNamespace(
            append=lambda event, **_: audit_events.append(event),
            load_latest=lambda **_: None,
            list_recent=lambda **_: [],
            list_by_event_types=lambda **_: [],
        )
        position_store = SimpleNamespace(list_all=lambda **kw: [])
        order_store = SimpleNamespace(
            list_by_status=lambda **kw: [],
            list_pending_submit=lambda **kw: [],
            daily_realized_pnl=lambda **kw: 0.0,
            daily_realized_pnl_by_symbol=lambda **kw: {},
            list_trade_pnl_by_strategy=lambda **kw: [],
        )
        trading_status_store = SimpleNamespace(load=lambda **kw: None)
        daily_session_state_store = SimpleNamespace(
            load=lambda **kw: None,
            save=lambda *a, **kw: None,
            list_by_session=lambda **kw: [],
        )
        strategy_flag_store = _FakeFlagStore()
        watchlist_store = SimpleNamespace(
            list_enabled=lambda *a: ["AAPL"],
            list_ignored=lambda *a: [],
        )
        option_order_store = _FakeOptStore()

    supervisor = RuntimeSupervisor(
        settings=settings,
        runtime=_FakeRuntime(),
        broker=SimpleNamespace(
            get_account=lambda: SimpleNamespace(
                equity=100_000.0, buying_power=200_000.0, trading_blocked=False
            ),
            list_open_orders=lambda: [],
            list_open_positions=lambda: [],
        ),
        market_data=SimpleNamespace(
            get_stock_bars=lambda **kw: {},
            get_daily_bars=lambda **kw: {},
        ),
        stream=None,
        close_runtime_fn=lambda _: None,
        connection_checker=lambda _: True,
        cycle_runner=lambda **kw: SimpleNamespace(intents=[]),
        cycle_intent_executor=lambda **kw: SimpleNamespace(
            submitted_exit_count=0,
            failed_exit_count=0,
            replaced_stop_count=0,
            submitted_stop_count=0,
            canceled_stop_count=0,
        ),
        order_dispatcher=lambda **kw: {"submitted_count": 0},
    )
    return supervisor, saved_flags, audit_events


def test_circuit_breaker_disables_strategy_below_threshold():
    """When rolling P&L <= -threshold, saves enabled=False flag and emits audit event."""
    supervisor, saved_flags, audit_events = _make_circuit_breaker_supervisor(
        rolling_pnl_by_strategy={"bear_orb": -600.0},
    )
    supervisor._check_option_strategy_circuit_breakers(
        session_date=date(2026, 5, 28),
        now=datetime(2026, 5, 28, 14, 0, tzinfo=timezone.utc),
    )

    assert len(saved_flags) == 1
    assert saved_flags[0].strategy_name == "bear_orb"
    assert saved_flags[0].enabled is False

    cb_events = [
        e for e in audit_events
        if e.event_type == "option_strategy_circuit_breaker_triggered"
    ]
    assert len(cb_events) == 1
    assert cb_events[0].payload["strategy_name"] == "bear_orb"
    assert cb_events[0].payload["rolling_pnl_usd"] == -600.0
    assert cb_events[0].payload["threshold_usd"] == -500.0
    assert cb_events[0].payload["window_days"] == 7


def test_circuit_breaker_no_op_when_above_threshold():
    """When rolling P&L > -threshold, no flag is saved."""
    supervisor, saved_flags, _ = _make_circuit_breaker_supervisor(
        rolling_pnl_by_strategy={"bear_orb": -400.0},  # -400 > -500 threshold
    )
    supervisor._check_option_strategy_circuit_breakers(
        session_date=date(2026, 5, 28),
        now=datetime(2026, 5, 28, 14, 0, tzinfo=timezone.utc),
    )
    assert saved_flags == []


def test_circuit_breaker_no_op_when_already_disabled():
    """When strategy flag already has enabled=False, no redundant save is made."""
    existing = StrategyFlag(
        strategy_name="bear_orb",
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        enabled=False,
    )
    supervisor, saved_flags, audit_events = _make_circuit_breaker_supervisor(
        rolling_pnl_by_strategy={"bear_orb": -600.0},
        existing_flag=existing,
    )
    supervisor._check_option_strategy_circuit_breakers(
        session_date=date(2026, 5, 28),
        now=datetime(2026, 5, 28, 14, 0, tzinfo=timezone.utc),
    )
    assert saved_flags == []
    cb_events = [
        e for e in audit_events
        if e.event_type == "option_strategy_circuit_breaker_triggered"
    ]
    assert cb_events == []


def test_circuit_breaker_skipped_when_config_zero():
    """With max_rolling_loss_usd=0.0 (disabled), no flags are written regardless of P&L."""
    supervisor, saved_flags, _ = _make_circuit_breaker_supervisor(
        rolling_pnl_by_strategy={"bear_orb": -999.0},
        max_rolling_loss_usd=0.0,
    )
    supervisor._check_option_strategy_circuit_breakers(
        session_date=date(2026, 5, 28),
        now=datetime(2026, 5, 28, 14, 0, tzinfo=timezone.utc),
    )
    assert saved_flags == []


# ---------------------------------------------------------------------------
# Task 4: Supervisor circuit breaker notification
# ---------------------------------------------------------------------------


def _make_notifier():
    """Fake notifier that records (subject, body) tuples."""
    sent: list[tuple[str, str]] = []

    class _FakeNotifier:
        def send(self, subject: str, body: str) -> None:
            sent.append((subject, body))

    return _FakeNotifier(), sent


def test_circuit_breaker_sends_notification():
    """When a strategy is disabled, notifier receives subject with strategy name and body with P&L details."""
    supervisor, _, _ = _make_circuit_breaker_supervisor(
        rolling_pnl_by_strategy={"bear_orb": -600.0},
    )
    notifier, sent = _make_notifier()
    supervisor._notifier = notifier

    supervisor._check_option_strategy_circuit_breakers(
        session_date=date(2026, 5, 28),
        now=datetime(2026, 5, 28, 14, 0, tzinfo=timezone.utc),
    )

    assert len(sent) == 1
    subject, body = sent[0]
    assert "bear_orb" in subject
    assert "-600.00" in body or "600.00" in body
    assert "enable-strategy" in body
    assert "bear_orb" in body


def test_circuit_breaker_no_notification_when_already_disabled():
    """When strategy already disabled, notifier.send() is not called."""
    existing = StrategyFlag(
        strategy_name="bear_orb",
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        enabled=False,
    )
    supervisor, _, _ = _make_circuit_breaker_supervisor(
        rolling_pnl_by_strategy={"bear_orb": -600.0},
        existing_flag=existing,
    )
    notifier, sent = _make_notifier()
    supervisor._notifier = notifier

    supervisor._check_option_strategy_circuit_breakers(
        session_date=date(2026, 5, 28),
        now=datetime(2026, 5, 28, 14, 0, tzinfo=timezone.utc),
    )

    assert sent == []


def test_circuit_breaker_no_notification_when_notifier_none():
    """With _notifier=None, no AttributeError is raised when breaching threshold."""
    supervisor, saved_flags, _ = _make_circuit_breaker_supervisor(
        rolling_pnl_by_strategy={"bear_orb": -600.0},
    )
    supervisor._notifier = None

    supervisor._check_option_strategy_circuit_breakers(
        session_date=date(2026, 5, 28),
        now=datetime(2026, 5, 28, 14, 0, tzinfo=timezone.utc),
    )
    assert len(saved_flags) == 1  # flag still written; notification just skipped


def test_circuit_breaker_notification_failure_does_not_crash_cycle():
    """When notifier.send() raises, _check_option_strategy_circuit_breakers() does not propagate."""
    supervisor, _, _ = _make_circuit_breaker_supervisor(
        rolling_pnl_by_strategy={"bear_orb": -600.0},
    )

    class _BrokenNotifier:
        def send(self, subject: str, body: str) -> None:
            raise RuntimeError("SMTP timeout")

    supervisor._notifier = _BrokenNotifier()

    # Must not raise
    supervisor._check_option_strategy_circuit_breakers(
        session_date=date(2026, 5, 28),
        now=datetime(2026, 5, 28, 14, 0, tzinfo=timezone.utc),
    )
