from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from alpaca_bot.config import Settings
from alpaca_bot.web.service import (
    WORKER_ACTIVITY_EVENT_TYPES,
    WORKER_STALE_AFTER_SECONDS,
    WORKING_ORDER_STATUSES,
    _load_worker_health,
    _max_drawdown_pct,
    _mean_return_pct,
    _win_rate,
    load_dashboard_snapshot,
    load_health_snapshot,
    load_metrics_snapshot,
)


def make_settings(**overrides: str) -> Settings:
    values = {
        "TRADING_MODE": "paper",
        "ENABLE_LIVE_TRADING": "false",
        "STRATEGY_VERSION": "v1-breakout",
        "DATABASE_URL": "postgresql://example",
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
    values.update(overrides)
    return Settings.from_env(values)


def make_event(event_type: str, created_at: datetime) -> SimpleNamespace:
    return SimpleNamespace(event_type=event_type, created_at=created_at, symbol=None, payload={})


def make_audit_store(events: list | None = None, latest=None) -> SimpleNamespace:
    return SimpleNamespace(
        list_recent=lambda **_: events if events is not None else [],
        load_latest=lambda **_: latest,
    )


def make_snapshot_stores(*, events=None, latest=None):
    return dict(
        trading_status_store=SimpleNamespace(load=lambda **_: None),
        daily_session_state_store=SimpleNamespace(load=lambda **_: None),
        position_store=SimpleNamespace(list_all=lambda **_: []),
        order_store=SimpleNamespace(list_by_status=lambda **_: [], list_recent=lambda **_: []),
        audit_event_store=make_audit_store(events=events, latest=latest),
    )


# ---------------------------------------------------------------------------
# load_dashboard_snapshot
# ---------------------------------------------------------------------------


def test_load_dashboard_snapshot_uses_provided_now() -> None:
    fixed_now = datetime(2026, 4, 25, 14, 0, tzinfo=timezone.utc)

    snapshot = load_dashboard_snapshot(
        settings=make_settings(),
        connection=SimpleNamespace(),
        now=fixed_now,
        **make_snapshot_stores(),
    )

    assert snapshot.generated_at == fixed_now


def test_load_dashboard_snapshot_session_date_uses_market_timezone() -> None:
    # 03:00 UTC on Apr 25 = 23:00 ET on Apr 24 (daylight saving, UTC-4)
    now = datetime(2026, 4, 25, 3, 0, tzinfo=timezone.utc)
    captured: list[date] = []

    def state_load(**kwargs: object) -> None:
        captured.append(kwargs["session_date"])  # type: ignore[arg-type]
        return None

    load_dashboard_snapshot(
        settings=make_settings(),
        connection=SimpleNamespace(),
        now=now,
        trading_status_store=SimpleNamespace(load=lambda **_: None),
        daily_session_state_store=SimpleNamespace(load=state_load),
        position_store=SimpleNamespace(list_all=lambda **_: []),
        order_store=SimpleNamespace(list_by_status=lambda **_: [], list_recent=lambda **_: []),
        audit_event_store=make_audit_store(),
    )

    assert captured == [date(2026, 4, 24)]


def test_load_dashboard_snapshot_passes_trading_mode_and_strategy_to_stores() -> None:
    settings = make_settings()
    captured_kwargs: list[dict] = []
    fixed_now = datetime(2026, 4, 25, 14, 0, tzinfo=timezone.utc)

    def recording_load(**kwargs: object) -> None:
        captured_kwargs.append(dict(kwargs))
        return None

    load_dashboard_snapshot(
        settings=settings,
        connection=SimpleNamespace(),
        now=fixed_now,
        trading_status_store=SimpleNamespace(load=recording_load),
        daily_session_state_store=SimpleNamespace(load=lambda **_: None),
        position_store=SimpleNamespace(list_all=lambda **_: []),
        order_store=SimpleNamespace(list_by_status=lambda **_: [], list_recent=lambda **_: []),
        audit_event_store=make_audit_store(),
    )

    assert captured_kwargs[0]["trading_mode"] == settings.trading_mode
    assert captured_kwargs[0]["strategy_version"] == settings.strategy_version


def test_load_dashboard_snapshot_requests_12_recent_events() -> None:
    limits: list[int] = []

    def recording_list_recent(**kwargs: object) -> list:
        limits.append(kwargs.get("limit"))  # type: ignore[arg-type]
        return []

    load_dashboard_snapshot(
        settings=make_settings(),
        connection=SimpleNamespace(),
        trading_status_store=SimpleNamespace(load=lambda **_: None),
        daily_session_state_store=SimpleNamespace(load=lambda **_: None),
        position_store=SimpleNamespace(list_all=lambda **_: []),
        order_store=SimpleNamespace(list_by_status=lambda **_: [], list_recent=lambda **_: []),
        audit_event_store=SimpleNamespace(
            list_recent=recording_list_recent,
            load_latest=lambda **_: None,
        ),
    )

    assert limits == [12]


def test_load_dashboard_snapshot_requests_10_recent_orders() -> None:
    limits: list[int] = []

    def recording_list_recent(**kwargs: object) -> list:
        limits.append(kwargs.get("limit"))  # type: ignore[arg-type]
        return []

    load_dashboard_snapshot(
        settings=make_settings(),
        connection=SimpleNamespace(),
        **{
            **make_snapshot_stores(),
            "order_store": SimpleNamespace(
                list_by_status=lambda **_: [],
                list_recent=recording_list_recent,
            ),
        },
    )

    assert limits == [10]


def test_load_dashboard_snapshot_uses_working_order_statuses() -> None:
    captured_statuses: list[list[str]] = []

    def recording_list_by_status(**kwargs: object) -> list:
        captured_statuses.append(kwargs.get("statuses"))  # type: ignore[arg-type]
        return []

    load_dashboard_snapshot(
        settings=make_settings(),
        connection=SimpleNamespace(),
        **{
            **make_snapshot_stores(),
            "order_store": SimpleNamespace(
                list_by_status=recording_list_by_status,
                list_recent=lambda **_: [],
            ),
        },
    )

    assert captured_statuses == [WORKING_ORDER_STATUSES]


# ---------------------------------------------------------------------------
# load_health_snapshot
# ---------------------------------------------------------------------------


def test_load_health_snapshot_requests_12_events() -> None:
    limits: list[int] = []

    def recording_list_recent(**kwargs: object) -> list:
        limits.append(kwargs.get("limit"))  # type: ignore[arg-type]
        return []

    load_health_snapshot(
        settings=make_settings(),
        connection=SimpleNamespace(),
        trading_status_store=SimpleNamespace(load=lambda **_: None),
        audit_event_store=SimpleNamespace(
            list_recent=recording_list_recent,
            load_latest=lambda **_: None,
        ),
    )

    assert limits == [12]


# ---------------------------------------------------------------------------
# _load_worker_health — missing / present / fresh / stale
# ---------------------------------------------------------------------------


def test_worker_health_is_missing_when_no_events() -> None:
    now = datetime(2026, 4, 25, 14, 0, tzinfo=timezone.utc)
    health = _load_worker_health(
        audit_event_store=make_audit_store(events=[], latest=None),
        recent_events=[],
        now=now,
    )

    assert health.status == "missing"
    assert health.last_event_type is None
    assert health.last_event_at is None
    assert health.age_seconds is None


def test_worker_health_is_fresh_within_stale_threshold() -> None:
    now = datetime(2026, 4, 25, 14, 0, tzinfo=timezone.utc)
    event = make_event("supervisor_cycle", now - timedelta(seconds=WORKER_STALE_AFTER_SECONDS - 1))

    health = _load_worker_health(
        audit_event_store=make_audit_store(events=[], latest=event),
        recent_events=[],
        now=now,
    )

    assert health.status == "fresh"
    assert health.age_seconds == WORKER_STALE_AFTER_SECONDS - 1


def test_worker_health_is_stale_beyond_threshold() -> None:
    now = datetime(2026, 4, 25, 14, 0, tzinfo=timezone.utc)
    event = make_event("supervisor_cycle", now - timedelta(seconds=WORKER_STALE_AFTER_SECONDS + 1))

    health = _load_worker_health(
        audit_event_store=make_audit_store(events=[], latest=event),
        recent_events=[],
        now=now,
    )

    assert health.status == "stale"
    assert health.age_seconds == WORKER_STALE_AFTER_SECONDS + 1


def test_worker_health_boundary_conditions() -> None:
    now = datetime(2026, 4, 25, 14, 0, tzinfo=timezone.utc)

    at_threshold = make_event("supervisor_cycle", now - timedelta(seconds=WORKER_STALE_AFTER_SECONDS))
    one_past = make_event("supervisor_cycle", now - timedelta(seconds=WORKER_STALE_AFTER_SECONDS + 1))

    health_at = _load_worker_health(
        audit_event_store=make_audit_store(events=[], latest=at_threshold),
        recent_events=[],
        now=now,
    )
    health_past = _load_worker_health(
        audit_event_store=make_audit_store(events=[], latest=one_past),
        recent_events=[],
        now=now,
    )

    assert health_at.status == "fresh"      # exactly at threshold → still fresh
    assert health_at.age_seconds == WORKER_STALE_AFTER_SECONDS
    assert health_past.status == "stale"    # one second over → stale


def test_worker_health_uses_load_latest_over_recent_events() -> None:
    now = datetime(2026, 4, 25, 14, 0, tzinfo=timezone.utc)
    latest = make_event("supervisor_cycle", now - timedelta(seconds=5))
    older_in_list = make_event("supervisor_idle", now - timedelta(seconds=200))

    health = _load_worker_health(
        audit_event_store=make_audit_store(events=[older_in_list], latest=latest),
        recent_events=[older_in_list],
        now=now,
    )

    assert health.status == "fresh"
    assert health.last_event_type == "supervisor_cycle"


def test_worker_health_falls_back_to_recent_events_when_load_latest_returns_none() -> None:
    now = datetime(2026, 4, 25, 14, 0, tzinfo=timezone.utc)
    event = make_event("supervisor_idle", now - timedelta(seconds=10))

    health = _load_worker_health(
        audit_event_store=make_audit_store(events=[event], latest=None),
        recent_events=[event],
        now=now,
    )

    assert health.status == "fresh"
    assert health.last_event_type == "supervisor_idle"


def test_worker_health_falls_back_to_recent_events_when_load_latest_not_callable() -> None:
    now = datetime(2026, 4, 25, 14, 0, tzinfo=timezone.utc)
    event = make_event("supervisor_cycle", now - timedelta(seconds=10))

    store_without_load_latest = SimpleNamespace(
        list_recent=lambda **_: [event],
        load_latest="not-callable",
    )

    health = _load_worker_health(
        audit_event_store=store_without_load_latest,
        recent_events=[event],
        now=now,
    )

    assert health.status == "fresh"
    assert health.last_event_type == "supervisor_cycle"


def test_worker_health_skips_non_worker_event_types_in_recent_events() -> None:
    now = datetime(2026, 4, 25, 14, 0, tzinfo=timezone.utc)
    non_worker_event = make_event("order_submitted", now - timedelta(seconds=10))

    health = _load_worker_health(
        audit_event_store=make_audit_store(events=[non_worker_event], latest=None),
        recent_events=[non_worker_event],
        now=now,
    )

    assert health.status == "missing"
    assert health.last_event_type is None


def test_worker_health_age_is_clamped_to_zero_for_future_events() -> None:
    now = datetime(2026, 4, 25, 14, 0, tzinfo=timezone.utc)
    # Event timestamp slightly in the future (clock skew scenario)
    future_event = make_event("supervisor_cycle", now + timedelta(seconds=5))

    health = _load_worker_health(
        audit_event_store=make_audit_store(events=[], latest=future_event),
        recent_events=[],
        now=now,
    )

    assert health.age_seconds == 0
    assert health.status == "fresh"


def test_worker_health_populates_last_event_type_and_timestamp() -> None:
    now = datetime(2026, 4, 25, 14, 0, tzinfo=timezone.utc)
    event_time = now - timedelta(seconds=30)
    event = make_event("trader_startup_completed", event_time)

    health = _load_worker_health(
        audit_event_store=make_audit_store(events=[], latest=event),
        recent_events=[],
        now=now,
    )

    assert health.last_event_type == "trader_startup_completed"
    assert health.last_event_at == event_time
    assert health.age_seconds == 30


# ---------------------------------------------------------------------------
# load_metrics_snapshot — integration of computation helpers
# ---------------------------------------------------------------------------


def make_metrics_stores(trades: list[dict] | None = None, admin_events=None, last_tuning=None):
    return dict(
        order_store=SimpleNamespace(
            list_closed_trades=lambda **_: trades if trades is not None else [],
        ),
        audit_event_store=SimpleNamespace(
            list_by_event_types=lambda **_: admin_events if admin_events is not None else [],
        ),
        tuning_result_store=SimpleNamespace(
            load_latest_best=lambda **_: last_tuning,
        ),
    )


def test_load_metrics_snapshot_empty_session_returns_zero_pnl() -> None:
    fixed_now = datetime(2026, 4, 25, 14, 0, tzinfo=timezone.utc)
    metrics = load_metrics_snapshot(
        settings=make_settings(),
        connection=SimpleNamespace(),
        now=fixed_now,
        **make_metrics_stores(trades=[]),
    )
    assert metrics.total_pnl == 0.0
    assert metrics.win_rate is None
    assert metrics.mean_return_pct is None
    assert metrics.max_drawdown_pct is None
    assert metrics.sharpe_ratio is None


def test_load_metrics_snapshot_single_winning_trade() -> None:
    now = datetime(2026, 4, 25, 15, 0, tzinfo=timezone.utc)
    trade = {
        "symbol": "AAPL",
        "entry_fill": 110.0,
        "entry_limit": 111.0,
        "entry_time": now,
        "exit_fill": 115.0,
        "exit_time": now,
        "qty": 10,
    }
    metrics = load_metrics_snapshot(
        settings=make_settings(),
        connection=SimpleNamespace(),
        now=datetime(2026, 4, 25, 15, 30, tzinfo=timezone.utc),
        **make_metrics_stores(trades=[trade]),
    )
    assert metrics.total_pnl == pytest.approx(50.0)  # (115-110)*10
    assert metrics.win_rate == pytest.approx(1.0)
    assert metrics.trades[0].slippage == pytest.approx(1.0)  # 111 - 110 = favorable


def test_load_metrics_snapshot_all_loss_drawdown_is_none() -> None:
    """When no trade is profitable, peak never exceeds 0 → max_drawdown_pct is None."""
    now = datetime(2026, 4, 25, 15, 0, tzinfo=timezone.utc)
    trade = {
        "symbol": "AAPL",
        "entry_fill": 115.0,
        "entry_limit": None,
        "entry_time": now,
        "exit_fill": 110.0,
        "exit_time": now,
        "qty": 10,
    }
    metrics = load_metrics_snapshot(
        settings=make_settings(),
        connection=SimpleNamespace(),
        now=datetime(2026, 4, 25, 15, 30, tzinfo=timezone.utc),
        **make_metrics_stores(trades=[trade]),
    )
    assert metrics.total_pnl == pytest.approx(-50.0)
    assert metrics.max_drawdown_pct is None


def test_load_metrics_snapshot_sharpe_ratio_is_always_none() -> None:
    """Sharpe ratio is deferred to a future phase — always None."""
    metrics = load_metrics_snapshot(
        settings=make_settings(),
        connection=SimpleNamespace(),
        **make_metrics_stores(),
    )
    assert metrics.sharpe_ratio is None


def test_load_metrics_snapshot_passes_admin_event_types() -> None:
    """admin_history contains only trading_status_changed events."""
    captured_types: list[list[str]] = []

    def recording_list_by_event_types(**kwargs):
        captured_types.append(kwargs.get("event_types", []))
        return []

    load_metrics_snapshot(
        settings=make_settings(),
        connection=SimpleNamespace(),
        order_store=SimpleNamespace(list_closed_trades=lambda **_: []),
        audit_event_store=SimpleNamespace(list_by_event_types=recording_list_by_event_types),
        tuning_result_store=SimpleNamespace(load_latest_best=lambda **_: None),
    )

    assert captured_types == [["trading_status_changed"]]


# ---------------------------------------------------------------------------
# _win_rate, _mean_return_pct, _max_drawdown_pct helpers
# ---------------------------------------------------------------------------

from alpaca_bot.web.service import TradeRecord


def _trade(*, symbol="AAPL", entry=100.0, exit=110.0, qty=10, slippage=None):
    pnl = (exit - entry) * qty
    return TradeRecord(
        symbol=symbol,
        entry_time=None,
        exit_time=None,
        entry_price=entry,
        exit_price=exit,
        quantity=qty,
        pnl=pnl,
        slippage=slippage,
    )


def test_win_rate_empty_returns_none():
    assert _win_rate([]) is None


def test_win_rate_all_winners():
    trades = [_trade(entry=100, exit=110), _trade(entry=200, exit=210)]
    assert _win_rate(trades) == pytest.approx(1.0)


def test_win_rate_mixed():
    trades = [_trade(entry=100, exit=110), _trade(entry=100, exit=90)]
    assert _win_rate(trades) == pytest.approx(0.5)


def test_mean_return_pct_empty_returns_none():
    assert _mean_return_pct([]) is None


def test_mean_return_pct_single_trade():
    # pnl=100, cost=100*10=1000 → return 10%
    t = _trade(entry=100, exit=110, qty=10)
    assert _mean_return_pct([t]) == pytest.approx(0.10)


def test_max_drawdown_pct_empty_returns_none():
    assert _max_drawdown_pct([]) is None


def test_max_drawdown_pct_all_loss_returns_none():
    """When every trade is a loss, peak never exceeds 0 → None (not 0.0)."""
    trades = [_trade(entry=110, exit=100), _trade(entry=110, exit=100)]
    assert _max_drawdown_pct(trades) is None


def test_max_drawdown_pct_no_drawdown_returns_zero():
    """Monotonically increasing P&L → drawdown of exactly 0.0."""
    trades = [_trade(entry=100, exit=110), _trade(entry=100, exit=110)]
    assert _max_drawdown_pct(trades) == pytest.approx(0.0)


def test_max_drawdown_pct_with_recovery():
    """Peak=100, trough=50 → drawdown = 50/100 = 50%."""
    trades = [
        _trade(entry=100, exit=200, qty=1),   # pnl=+100, cumulative=100, peak=100
        _trade(entry=200, exit=150, qty=1),    # pnl= -50, cumulative=50
    ]
    assert _max_drawdown_pct(trades) == pytest.approx(0.5)
