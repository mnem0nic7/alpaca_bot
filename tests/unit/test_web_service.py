from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from alpaca_bot.config import Settings
from alpaca_bot.storage import GLOBAL_SESSION_STATE_STRATEGY_NAME
from alpaca_bot.web.service import (
    WORKER_ACTIVITY_EVENT_TYPES,
    WORKER_STALE_AFTER_SECONDS,
    WORKING_ORDER_STATUSES,
    AuditLogPage,
    _compute_capital_pct,
    _load_worker_health,
    _max_drawdown_pct,
    _mean_return_pct,
    _win_rate,
    load_audit_page,
    load_confidence_floor_info,
    load_dashboard_snapshot,
    load_equity_chart_data,
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


def make_audit_store(events: list | None = None, latest=None, stale_events: list | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        list_recent=lambda **_: events if events is not None else [],
        load_latest=lambda **_: latest,
        list_by_event_types=lambda **_: stale_events if stale_events is not None else [],
    )


def make_snapshot_stores(*, events=None, latest=None):
    return dict(
        trading_status_store=SimpleNamespace(load=lambda **_: None),
        daily_session_state_store=SimpleNamespace(load=lambda **_: None),
        position_store=SimpleNamespace(list_all=lambda **_: []),
        order_store=SimpleNamespace(
            list_by_status=lambda **_: [],
            list_recent=lambda **_: [],
            win_loss_counts_by_strategy=lambda **_: {},
            lifetime_pnl_by_strategy=lambda **_: {},
        ),
        audit_event_store=make_audit_store(events=events, latest=latest),
        strategy_flag_store=SimpleNamespace(list_all=lambda **_: []),
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
    captured: list[dict[str, object]] = []

    def state_load(**kwargs: object) -> None:
        captured.append(dict(kwargs))
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
        strategy_flag_store=SimpleNamespace(list_all=lambda **_: []),
    )

    assert captured[0]["session_date"] == date(2026, 4, 24)
    assert captured[0]["strategy_name"] == GLOBAL_SESSION_STATE_STRATEGY_NAME


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
        strategy_flag_store=SimpleNamespace(list_all=lambda **_: []),
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
        strategy_flag_store=SimpleNamespace(list_all=lambda **_: []),
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
            list_by_event_types=lambda **_: [],
        ),
        strategy_flag_store=SimpleNamespace(list_all=lambda **_: []),
    )

    assert limits == [12]


def test_load_health_snapshot_includes_strategy_flags() -> None:
    from alpaca_bot.strategy import ALL_STRATEGY_NAMES

    enabled_flag = SimpleNamespace(strategy_name="breakout", enabled=True)
    snapshot = load_health_snapshot(
        settings=make_settings(),
        connection=SimpleNamespace(),
        trading_status_store=SimpleNamespace(load=lambda **_: None),
        audit_event_store=SimpleNamespace(
            list_recent=lambda **_: [],
            load_latest=lambda **_: None,
            list_by_event_types=lambda **_: [],
        ),
        strategy_flag_store=SimpleNamespace(list_all=lambda **_: [enabled_flag]),
    )

    flag_dict = dict(snapshot.strategy_flags)
    assert set(flag_dict.keys()) == ALL_STRATEGY_NAMES
    assert flag_dict["breakout"] is True


def test_load_dashboard_snapshot_includes_option_strategy_rows() -> None:
    from alpaca_bot.strategy import OPTION_STRATEGY_NAMES
    snapshot = load_dashboard_snapshot(
        settings=make_settings(),
        connection=SimpleNamespace(),
        now=datetime(2026, 4, 25, 14, 0, tzinfo=timezone.utc),
        **make_snapshot_stores(),
    )
    names_in_table = [name for name, _ in snapshot.strategy_flags]
    for opt_name in OPTION_STRATEGY_NAMES:
        assert opt_name in names_in_table, f"Option strategy {opt_name!r} missing from strategy_flags"


def test_load_health_snapshot_includes_option_strategy_names() -> None:
    from alpaca_bot.strategy import ALL_STRATEGY_NAMES
    snapshot = load_health_snapshot(
        settings=make_settings(),
        connection=SimpleNamespace(),
        trading_status_store=SimpleNamespace(load=lambda **_: None),
        audit_event_store=SimpleNamespace(
            list_recent=lambda **_: [],
            load_latest=lambda **_: None,
            list_by_event_types=lambda **_: [],
        ),
        strategy_flag_store=SimpleNamespace(list_all=lambda **_: []),
    )
    flag_names = {name for name, _ in snapshot.strategy_flags}
    assert flag_names == ALL_STRATEGY_NAMES


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


def make_metrics_stores(trades=None, admin_events=None, last_tuning=None, daily_session_state_store=None):
    default_state_store = daily_session_state_store or SimpleNamespace(load=lambda **_: None)
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
        daily_session_state_store=default_state_store,
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


def test_load_metrics_snapshot_sharpe_ratio_is_none_for_fewer_than_two_trades() -> None:
    """Sharpe requires ≥2 trades with variance; zero or one trade returns None."""
    metrics = load_metrics_snapshot(
        settings=make_settings(),
        connection=SimpleNamespace(),
        **make_metrics_stores(),
    )
    assert metrics.sharpe_ratio is None


def test_load_metrics_snapshot_sharpe_ratio_computed_for_multiple_trades() -> None:
    """Sharpe is computed as mean_return / std_return for two or more trades."""
    now = datetime(2026, 4, 25, 15, 0, tzinfo=timezone.utc)
    trades = [
        {"symbol": "AAPL", "entry_fill": 100.0, "entry_limit": None, "entry_time": now, "exit_fill": 110.0, "exit_time": now, "qty": 10},
        {"symbol": "AAPL", "entry_fill": 100.0, "entry_limit": None, "entry_time": now, "exit_fill": 90.0, "exit_time": now, "qty": 10},
    ]
    metrics = load_metrics_snapshot(
        settings=make_settings(),
        connection=SimpleNamespace(),
        now=now,
        **make_metrics_stores(trades=trades),
    )
    # returns: [+10%, -10%] → mean=0, std≈0.141 → sharpe=0.0
    assert metrics.sharpe_ratio == pytest.approx(0.0)


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

    assert captured_types == [["trading_status_changed", "strategy_flag_changed"]]


def test_trade_record_exit_reason_and_hold_minutes() -> None:
    entry_time = datetime(2026, 5, 5, 10, 0, tzinfo=timezone.utc)
    exit_time = datetime(2026, 5, 5, 10, 45, tzinfo=timezone.utc)
    row = {
        "symbol": "AAPL",
        "entry_fill": 100.0,
        "entry_limit": 101.0,
        "entry_time": entry_time,
        "exit_time": exit_time,
        "exit_fill": 105.0,
        "qty": 10,
        "intent_type": "stop",
    }
    trade = _to_trade_record(row)
    assert trade.exit_reason == "stop"
    assert trade.hold_minutes == pytest.approx(45.0)

    row_eod = {**row, "intent_type": "eod"}
    trade_eod = _to_trade_record(row_eod)
    assert trade_eod.exit_reason == "eod"


def test_session_report_none_when_no_trades() -> None:
    now = datetime(2026, 5, 5, 15, 0, tzinfo=timezone.utc)
    metrics = load_metrics_snapshot(
        settings=make_settings(),
        connection=SimpleNamespace(),
        now=now,
        **make_metrics_stores(trades=[]),
    )
    assert metrics.session_report is None


def test_session_report_populated_from_trades() -> None:
    now = datetime(2026, 5, 5, 15, 0, tzinfo=timezone.utc)
    entry_time = datetime(2026, 5, 5, 10, 0, tzinfo=timezone.utc)
    exit_time = datetime(2026, 5, 5, 10, 30, tzinfo=timezone.utc)
    trades = [
        {
            "symbol": "AAPL",
            "entry_fill": 100.0,
            "entry_limit": None,
            "entry_time": entry_time,
            "exit_time": exit_time,
            "exit_fill": 105.0,
            "qty": 10,
            "intent_type": "stop",
        },
        {
            "symbol": "GOOG",
            "entry_fill": 200.0,
            "entry_limit": None,
            "entry_time": entry_time,
            "exit_time": exit_time,
            "exit_fill": 190.0,
            "qty": 5,
            "intent_type": "eod",
        },
    ]
    metrics = load_metrics_snapshot(
        settings=make_settings(),
        connection=SimpleNamespace(),
        now=now,
        **make_metrics_stores(trades=trades),
    )
    assert metrics.session_report is not None
    assert metrics.session_report.profit_factor is not None
    assert metrics.session_report.stop_wins == 1
    assert metrics.session_report.eod_losses == 1


def test_session_report_uses_starting_equity_from_store() -> None:
    now = datetime(2026, 5, 5, 15, 0, tzinfo=timezone.utc)
    entry_time = datetime(2026, 5, 5, 10, 0, tzinfo=timezone.utc)
    exit_time = datetime(2026, 5, 5, 10, 30, tzinfo=timezone.utc)
    trades = [
        {
            "symbol": "AAPL",
            "entry_fill": 100.0,
            "entry_limit": None,
            "entry_time": entry_time,
            "exit_time": exit_time,
            "exit_fill": 95.0,
            "qty": 10,
            "intent_type": "eod",
        },
    ]
    fake_state = SimpleNamespace(equity_baseline=50_000.0)
    fake_store = SimpleNamespace(load=lambda **_: fake_state)
    metrics = load_metrics_snapshot(
        settings=make_settings(),
        connection=SimpleNamespace(),
        now=now,
        **make_metrics_stores(trades=trades, daily_session_state_store=fake_store),
    )
    assert metrics.session_report is not None
    # pnl = (95-100)*10 = -50; peak = 50_000; drawdown = 50/50_000 = 0.001
    assert metrics.session_report.max_drawdown_pct == pytest.approx(50.0 / 50_000.0)


# ---------------------------------------------------------------------------
# _win_rate, _mean_return_pct, _max_drawdown_pct helpers
# ---------------------------------------------------------------------------

from alpaca_bot.web.service import TradeRecord, _to_trade_record


def _trade(*, symbol="AAPL", entry=100.0, exit=110.0, qty=10, slippage=None, strategy_name="breakout"):
    pnl = (exit - entry) * qty
    return TradeRecord(
        symbol=symbol,
        strategy_name=strategy_name,
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


# ---------------------------------------------------------------------------
# trades_by_strategy (Task 6)
# ---------------------------------------------------------------------------


def test_metrics_snapshot_has_trades_by_strategy():
    from alpaca_bot.web.service import MetricsSnapshot
    fields = {name for name in MetricsSnapshot.__dataclass_fields__}
    assert "trades_by_strategy" in fields


def test_load_metrics_snapshot_groups_by_strategy():
    raw_trades = [
        {
            "symbol": "AAPL",
            "strategy_name": "breakout",
            "entry_fill": 150.0,
            "entry_limit": 150.1,
            "entry_time": datetime(2026, 1, 2, 10, tzinfo=timezone.utc),
            "exit_fill": 155.0,
            "exit_time": datetime(2026, 1, 2, 14, tzinfo=timezone.utc),
            "qty": 10,
        },
        {
            "symbol": "TSLA",
            "strategy_name": "momentum",
            "entry_fill": 200.0,
            "entry_limit": 200.2,
            "entry_time": datetime(2026, 1, 2, 10, tzinfo=timezone.utc),
            "exit_fill": 210.0,
            "exit_time": datetime(2026, 1, 2, 14, tzinfo=timezone.utc),
            "qty": 5,
        },
    ]

    settings = make_settings()
    snapshot = load_metrics_snapshot(
        settings=settings,
        connection=SimpleNamespace(),
        order_store=SimpleNamespace(
            list_closed_trades=lambda **_: raw_trades,
        ),
        audit_event_store=SimpleNamespace(
            list_by_event_types=lambda **_: [],
        ),
        tuning_result_store=SimpleNamespace(
            load_latest_best=lambda **_: None,
        ),
        daily_session_state_store=SimpleNamespace(load=lambda **_: None),
    )

    assert "breakout" in snapshot.trades_by_strategy
    assert "momentum" in snapshot.trades_by_strategy
    assert len(snapshot.trades_by_strategy["breakout"]) == 1
    assert len(snapshot.trades_by_strategy["momentum"]) == 1
    assert len(snapshot.trades) == 2


def test_trade_record_has_strategy_name():
    from alpaca_bot.web.service import TradeRecord
    fields = {name for name in TradeRecord.__dataclass_fields__}
    assert "strategy_name" in fields


# ---------------------------------------------------------------------------
# load_audit_page
# ---------------------------------------------------------------------------


def make_paged_audit_store(events: list):
    """Store that returns a slice of events based on limit/offset."""
    def list_recent(*, limit: int = 20, offset: int = 0) -> list:
        return events[offset:offset + limit]

    def list_by_event_types(*, event_types: list, limit: int = 20, offset: int = 0) -> list:
        filtered = [e for e in events if e.event_type in event_types]
        return filtered[offset:offset + limit]

    return SimpleNamespace(list_recent=list_recent, list_by_event_types=list_by_event_types)


def test_load_audit_page_no_filter_returns_events() -> None:
    events = [make_event("supervisor_cycle", datetime(2026, 4, 25, 14, i, tzinfo=timezone.utc)) for i in range(5)]
    store = make_paged_audit_store(events)
    page = load_audit_page(connection=SimpleNamespace(), audit_event_store=store, limit=3, offset=0)
    assert len(page.events) == 3
    assert page.has_more is True
    assert page.next_offset == 3
    assert page.prev_offset is None
    assert page.event_type_filter is None


def test_load_audit_page_with_filter() -> None:
    events = [
        make_event("supervisor_cycle", datetime(2026, 4, 25, 14, 0, tzinfo=timezone.utc)),
        make_event("trading_status_changed", datetime(2026, 4, 25, 14, 1, tzinfo=timezone.utc)),
    ]
    store = make_paged_audit_store(events)
    page = load_audit_page(connection=SimpleNamespace(), audit_event_store=store, limit=10, offset=0, event_type_filter="trading_status_changed")
    assert len(page.events) == 1
    assert page.event_type_filter == "trading_status_changed"


def test_load_audit_page_prev_offset_on_second_page() -> None:
    events = [make_event("supervisor_cycle", datetime(2026, 4, 25, 14, i, tzinfo=timezone.utc)) for i in range(20)]
    store = make_paged_audit_store(events)
    page = load_audit_page(connection=SimpleNamespace(), audit_event_store=store, limit=5, offset=5)
    assert page.prev_offset == 0
    assert page.next_offset == 10


def test_load_audit_page_no_more_when_exhausted() -> None:
    events = [make_event("supervisor_cycle", datetime(2026, 4, 25, 14, i, tzinfo=timezone.utc)) for i in range(3)]
    store = make_paged_audit_store(events)
    page = load_audit_page(connection=SimpleNamespace(), audit_event_store=store, limit=5, offset=0)
    assert page.has_more is False
    assert page.next_offset is None


def test_audit_log_page_prev_offset_clamped_to_zero() -> None:
    page = AuditLogPage(events=[], limit=10, offset=5, has_more=False, event_type_filter=None)
    assert page.prev_offset == 0


# ---------------------------------------------------------------------------
# load_metrics_snapshot with explicit session_date
# ---------------------------------------------------------------------------


def test_load_metrics_snapshot_explicit_session_date_overrides_now() -> None:
    captured_dates: list = []

    def recording_list_closed_trades(**kwargs):
        captured_dates.append(kwargs.get("session_date"))
        return []

    explicit_date = date(2026, 4, 20)
    load_metrics_snapshot(
        settings=make_settings(),
        connection=SimpleNamespace(),
        session_date=explicit_date,
        order_store=SimpleNamespace(list_closed_trades=recording_list_closed_trades),
        audit_event_store=SimpleNamespace(list_by_event_types=lambda **_: []),
        tuning_result_store=SimpleNamespace(load_latest_best=lambda **_: None),
    )

    assert captured_dates == [explicit_date]


# ---------------------------------------------------------------------------
# load_dashboard_snapshot — strategy_entries_disabled
# ---------------------------------------------------------------------------


def test_load_dashboard_snapshot_populates_strategy_entries_disabled() -> None:
    from types import SimpleNamespace as NS

    entries_row = NS(strategy_name="breakout", entries_disabled=True)
    state_store = NS(
        load=lambda **_: None,
        list_by_session=lambda **_: [entries_row],
    )

    snapshot = load_dashboard_snapshot(
        settings=make_settings(),
        connection=SimpleNamespace(),
        now=datetime(2026, 4, 25, 14, 0, tzinfo=timezone.utc),
        trading_status_store=SimpleNamespace(load=lambda **_: None),
        daily_session_state_store=state_store,
        position_store=SimpleNamespace(list_all=lambda **_: []),
        order_store=SimpleNamespace(list_by_status=lambda **_: [], list_recent=lambda **_: []),
        audit_event_store=make_audit_store(),
        strategy_flag_store=SimpleNamespace(list_all=lambda **_: []),
    )

    assert snapshot.strategy_entries_disabled == {"breakout": True}


def test_load_dashboard_snapshot_entries_disabled_defaults_empty_without_list_by_session() -> None:
    snapshot = load_dashboard_snapshot(
        settings=make_settings(),
        connection=SimpleNamespace(),
        now=datetime(2026, 4, 25, 14, 0, tzinfo=timezone.utc),
        **make_snapshot_stores(),  # fake stores don't have list_by_session
    )

    assert snapshot.strategy_entries_disabled == {}


def test_load_dashboard_snapshot_latest_prices_passed_through() -> None:
    snapshot = load_dashboard_snapshot(
        settings=make_settings(),
        connection=SimpleNamespace(),
        now=datetime(2026, 4, 28, 14, 0, tzinfo=timezone.utc),
        latest_prices={"AAPL": 175.50},
        **make_snapshot_stores(),
    )

    assert snapshot.latest_prices == {"AAPL": 175.50}


def test_load_dashboard_snapshot_latest_prices_defaults_to_empty() -> None:
    snapshot = load_dashboard_snapshot(
        settings=make_settings(),
        connection=SimpleNamespace(),
        now=datetime(2026, 4, 28, 14, 0, tzinfo=timezone.utc),
        **make_snapshot_stores(),
    )

    assert snapshot.latest_prices == {}


# ---------------------------------------------------------------------------
# load_health_snapshot — stream staleness fields
# ---------------------------------------------------------------------------

from alpaca_bot.web.service import STREAM_STALE_WINDOW_SECONDS


def test_health_snapshot_stream_stale_when_recent_stale_event() -> None:
    """stream_stale=True when stream_heartbeat_stale event within 600s."""
    real_now = datetime.now(timezone.utc)
    stale_event = SimpleNamespace(
        event_type="stream_heartbeat_stale",
        created_at=real_now - timedelta(seconds=300),  # 5 minutes ago — within window
        symbol=None,
        payload={},
    )
    snapshot = load_health_snapshot(
        settings=make_settings(),
        connection=SimpleNamespace(),
        trading_status_store=SimpleNamespace(load=lambda **_: None),
        audit_event_store=SimpleNamespace(
            list_recent=lambda **_: [],
            load_latest=lambda **_: None,
            list_by_event_types=lambda **_: [stale_event],
        ),
        strategy_flag_store=SimpleNamespace(list_all=lambda **_: []),
    )
    assert snapshot.stream_stale is True
    assert snapshot.stream_last_stale_at == stale_event.created_at


def test_health_snapshot_stream_fresh_when_no_recent_stale_event() -> None:
    """stream_stale=False when no stream_heartbeat_stale within STREAM_STALE_WINDOW_SECONDS."""
    real_now = datetime.now(timezone.utc)
    # Event is older than the staleness window
    old_stale_event = SimpleNamespace(
        event_type="stream_heartbeat_stale",
        created_at=real_now - timedelta(seconds=STREAM_STALE_WINDOW_SECONDS + 60),
        symbol=None,
        payload={},
    )
    snapshot = load_health_snapshot(
        settings=make_settings(),
        connection=SimpleNamespace(),
        trading_status_store=SimpleNamespace(load=lambda **_: None),
        audit_event_store=SimpleNamespace(
            list_recent=lambda **_: [],
            load_latest=lambda **_: None,
            list_by_event_types=lambda **_: [old_stale_event],
        ),
        strategy_flag_store=SimpleNamespace(list_all=lambda **_: []),
    )
    assert snapshot.stream_stale is False
    assert snapshot.stream_last_stale_at is None


def test_health_snapshot_stream_fresh_when_empty_stale_events() -> None:
    """stream_stale=False when list_by_event_types returns empty list."""
    snapshot = load_health_snapshot(
        settings=make_settings(),
        connection=SimpleNamespace(),
        trading_status_store=SimpleNamespace(load=lambda **_: None),
        audit_event_store=SimpleNamespace(
            list_recent=lambda **_: [],
            load_latest=lambda **_: None,
            list_by_event_types=lambda **_: [],
        ),
        strategy_flag_store=SimpleNamespace(list_all=lambda **_: []),
    )
    assert snapshot.stream_stale is False
    assert snapshot.stream_last_stale_at is None


# ---------------------------------------------------------------------------
# Dashboard session P&L and loss-limit fields
# ---------------------------------------------------------------------------


def test_load_dashboard_snapshot_populates_realized_pnl_and_loss_limit_when_baseline_set() -> None:
    """When session_state has equity_baseline, load_dashboard_snapshot must populate
    realized_pnl from daily_realized_pnl() and loss_limit_amount from the settings pct."""
    from datetime import date as date_cls
    from alpaca_bot.storage import DailySessionState
    from alpaca_bot.config import TradingMode

    fixed_now = datetime(2026, 5, 2, 15, 0, tzinfo=timezone.utc)
    settings = make_settings(DAILY_LOSS_LIMIT_PCT="0.01")  # 1% => loss_limit = 500 on 50000 baseline

    session_state = DailySessionState(
        session_date=date_cls(2026, 5, 2),
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        entries_disabled=False,
        flatten_complete=False,
        equity_baseline=50000.0,
        updated_at=fixed_now,
    )

    realized_pnl_calls: list[dict] = []

    def fake_daily_realized_pnl(**kwargs):
        realized_pnl_calls.append(kwargs)
        return 142.50

    snapshot = load_dashboard_snapshot(
        settings=settings,
        connection=SimpleNamespace(),
        now=fixed_now,
        trading_status_store=SimpleNamespace(load=lambda **_: None),
        daily_session_state_store=SimpleNamespace(load=lambda **_: session_state),
        position_store=SimpleNamespace(list_all=lambda **_: []),
        order_store=SimpleNamespace(
            list_by_status=lambda **_: [],
            list_recent=lambda **_: [],
            daily_realized_pnl=fake_daily_realized_pnl,
        ),
        audit_event_store=make_audit_store(),
        strategy_flag_store=SimpleNamespace(list_all=lambda **_: []),
    )

    assert snapshot.realized_pnl == pytest.approx(142.50)
    assert snapshot.loss_limit_amount == pytest.approx(500.0)  # 50000 * 0.01
    assert len(realized_pnl_calls) == 1, "daily_realized_pnl must be called exactly once"


def test_load_dashboard_snapshot_realized_pnl_and_loss_limit_none_when_no_session() -> None:
    """When session_state is None (no equity_baseline), both realized_pnl and
    loss_limit_amount must be None; daily_realized_pnl must NOT be called."""
    realized_pnl_calls: list[dict] = []

    def should_not_be_called(**kwargs):
        realized_pnl_calls.append(kwargs)
        return 0.0

    fixed_now = datetime(2026, 5, 2, 15, 0, tzinfo=timezone.utc)

    snapshot = load_dashboard_snapshot(
        settings=make_settings(),
        connection=SimpleNamespace(),
        now=fixed_now,
        trading_status_store=SimpleNamespace(load=lambda **_: None),
        daily_session_state_store=SimpleNamespace(load=lambda **_: None),
        position_store=SimpleNamespace(list_all=lambda **_: []),
        order_store=SimpleNamespace(
            list_by_status=lambda **_: [],
            list_recent=lambda **_: [],
            daily_realized_pnl=should_not_be_called,
        ),
        audit_event_store=make_audit_store(),
        strategy_flag_store=SimpleNamespace(list_all=lambda **_: []),
    )

    assert snapshot.realized_pnl is None
    assert snapshot.loss_limit_amount is None
    assert realized_pnl_calls == [], "daily_realized_pnl must NOT be called when equity_baseline is None"


# ---------------------------------------------------------------------------
# load_equity_chart_data
# ---------------------------------------------------------------------------


def test_load_equity_chart_data_1d_no_trades():
    now = datetime(2026, 1, 2, 20, 0, tzinfo=timezone.utc)
    settings = make_settings()

    order_store = SimpleNamespace(
        list_trade_exits_in_range=lambda **_: []
    )
    dss_store = SimpleNamespace(
        list_equity_baselines=lambda **_: {date(2026, 1, 2): 100000.0}
    )

    data = load_equity_chart_data(
        settings=settings,
        connection=None,
        range_code="1d",
        anchor_date=date(2026, 1, 2),
        now=now,
        order_store=order_store,
        daily_session_state_store=dss_store,
    )

    assert data.range_code == "1d"
    assert data.current == 100000.0
    assert data.pct_change == 0.0
    assert len(data.points) == 1
    assert data.points[0].v == 100000.0


def test_load_equity_chart_data_1d_with_trades():
    now = datetime(2026, 1, 2, 20, 0, tzinfo=timezone.utc)
    settings = make_settings()
    # session_start in ET is 9:30 AM = 14:30 UTC on 2026-01-02
    session_start_utc = datetime(2026, 1, 2, 14, 30, tzinfo=timezone.utc)
    exit1 = datetime(2026, 1, 2, 15, 0, tzinfo=timezone.utc)  # after open
    exit2 = datetime(2026, 1, 2, 16, 0, tzinfo=timezone.utc)  # after open

    order_store = SimpleNamespace(
        list_trade_exits_in_range=lambda **_: [
            {"exit_time": exit1, "pnl": 200.0},
            {"exit_time": exit2, "pnl": -50.0},
        ]
    )
    dss_store = SimpleNamespace(
        list_equity_baselines=lambda **_: {date(2026, 1, 2): 100000.0}
    )

    data = load_equity_chart_data(
        settings=settings,
        connection=None,
        range_code="1d",
        anchor_date=date(2026, 1, 2),
        now=now,
        order_store=order_store,
        daily_session_state_store=dss_store,
    )

    # Points: session_start baseline + 2 trade exits
    assert len(data.points) == 3
    assert data.points[0].v == 100000.0
    assert data.points[1].v == 100200.0
    assert data.points[2].v == 100150.0
    assert abs(data.pct_change - 0.15) < 0.001  # 150/100000 * 100


def test_load_equity_chart_data_multi_session():
    now = datetime(2026, 1, 5, 20, 0, tzinfo=timezone.utc)
    settings = make_settings()

    baselines = {
        date(2026, 1, 2): 100000.0,
        date(2026, 1, 3): 100150.0,
        date(2026, 1, 4): 100300.0,
    }
    # one trade exit per session date (all at 15:00 ET = 20:00 UTC)
    exits = [
        {"exit_time": datetime(2026, 1, 2, 20, 0, tzinfo=timezone.utc), "pnl": 150.0},
        {"exit_time": datetime(2026, 1, 3, 20, 0, tzinfo=timezone.utc), "pnl": 150.0},
        {"exit_time": datetime(2026, 1, 4, 20, 0, tzinfo=timezone.utc), "pnl": 150.0},
    ]

    order_store = SimpleNamespace(list_trade_exits_in_range=lambda **_: exits)
    dss_store = SimpleNamespace(list_equity_baselines=lambda **_: baselines)

    data = load_equity_chart_data(
        settings=settings,
        connection=None,
        range_code="1m",
        anchor_date=date(2026, 1, 5),
        now=now,
        order_store=order_store,
        daily_session_state_store=dss_store,
    )

    # One point per session: baseline + cumulative P&L for that session
    assert len(data.points) == 3
    assert data.points[0].v == pytest.approx(100150.0)  # 100000 + 150
    assert data.points[1].v == pytest.approx(100300.0)  # 100150 + 150
    assert data.points[2].v == pytest.approx(100450.0)  # 100300 + 150
    assert data.range_code == "1m"
    assert data.pct_change == pytest.approx(0.45)  # (100450 - 100000) / 100000 * 100


# ── test_load_strategy_weights ────────────────────────────────────────────────

class TestLoadStrategyWeights:
    """Tests for load_strategy_weights() service function."""

    def _make_fake_weight_store(self, weights: list) -> object:
        class _FakeStore:
            def __init__(self) -> None:
                self.last_load_kwargs: dict = {}

            def load_all(self, **kwargs):
                self.last_load_kwargs = kwargs
                return weights

        return _FakeStore()

    def test_returns_empty_list_when_no_weights(self) -> None:
        from alpaca_bot.web.service import load_strategy_weights
        store = self._make_fake_weight_store([])
        result = load_strategy_weights(
            settings=make_settings(),
            connection=None,
            strategy_weight_store=store,
        )
        assert result == []

    def test_returns_weight_rows_sorted_by_weight_descending(self) -> None:
        from alpaca_bot.web.service import load_strategy_weights, StrategyWeightRow
        from alpaca_bot.storage import StrategyWeight
        from alpaca_bot.config import TradingMode
        from datetime import datetime, timezone

        now = datetime(2026, 5, 1, 15, 0, tzinfo=timezone.utc)
        store = self._make_fake_weight_store([
            StrategyWeight("momentum", TradingMode.PAPER, "v1", 0.3, 0.9, now),
            StrategyWeight("breakout", TradingMode.PAPER, "v1", 0.4, 1.8, now),
            StrategyWeight("orb", TradingMode.PAPER, "v1", 0.3, 0.5, now),
        ])
        result = load_strategy_weights(
            settings=make_settings(),
            connection=None,
            strategy_weight_store=store,
        )
        assert len(result) == 3
        assert result[0].strategy_name == "breakout"  # highest weight first
        assert abs(result[0].weight - 0.4) < 1e-9
        assert abs(result[0].sharpe - 1.8) < 1e-9
        assert isinstance(result[0], StrategyWeightRow)

    def test_weight_row_fields(self) -> None:
        from alpaca_bot.web.service import load_strategy_weights, StrategyWeightRow
        from alpaca_bot.storage import StrategyWeight
        from alpaca_bot.config import TradingMode
        from datetime import datetime, timezone

        now = datetime(2026, 5, 1, 15, 0, tzinfo=timezone.utc)
        store = self._make_fake_weight_store([
            StrategyWeight("breakout", TradingMode.PAPER, "v1", 0.6, 2.1, now),
        ])
        result = load_strategy_weights(
            settings=make_settings(),
            connection=None,
            strategy_weight_store=store,
        )
        row = result[0]
        assert row.strategy_name == "breakout"
        assert abs(row.weight - 0.6) < 1e-9
        assert abs(row.sharpe - 2.1) < 1e-9

    def test_forwards_trading_mode_and_strategy_version(self) -> None:
        from alpaca_bot.web.service import load_strategy_weights

        store = self._make_fake_weight_store([])
        settings = make_settings()
        load_strategy_weights(settings=settings, connection=None, strategy_weight_store=store)
        assert store.last_load_kwargs.get("trading_mode") == settings.trading_mode
        assert store.last_load_kwargs.get("strategy_version") == settings.strategy_version


# ---------------------------------------------------------------------------
# _compute_capital_pct
# ---------------------------------------------------------------------------

def test_compute_capital_pct_empty_positions() -> None:
    result = _compute_capital_pct([], {})
    assert result == {}


def test_compute_capital_pct_single_strategy_all_capital() -> None:
    pos = SimpleNamespace(symbol="AAPL", entry_price=100.0, quantity=10, strategy_name="breakout")
    result = _compute_capital_pct([pos], {"AAPL": 105.0})
    assert result == {"breakout": pytest.approx(100.0)}


def test_compute_capital_pct_two_strategies() -> None:
    pos_a = SimpleNamespace(symbol="AAPL", entry_price=100.0, quantity=10, strategy_name="breakout")
    pos_b = SimpleNamespace(symbol="MSFT", entry_price=200.0, quantity=5, strategy_name="momentum")
    # AAPL: 10*100=1000, MSFT: 5*200=1000 (no latest_prices, uses entry_price)
    result = _compute_capital_pct([pos_a, pos_b], {})
    assert result["breakout"] == pytest.approx(50.0)
    assert result["momentum"] == pytest.approx(50.0)


def test_compute_capital_pct_uses_latest_price_over_entry_price() -> None:
    pos = SimpleNamespace(symbol="AAPL", entry_price=100.0, quantity=10, strategy_name="breakout")
    # latest price 110 → value 1100; still 100% (only one strategy)
    result = _compute_capital_pct([pos], {"AAPL": 110.0})
    assert result == {"breakout": pytest.approx(100.0)}


def test_compute_capital_pct_falls_back_to_entry_price_when_no_latest() -> None:
    pos_a = SimpleNamespace(symbol="AAPL", entry_price=100.0, quantity=10, strategy_name="breakout")
    pos_b = SimpleNamespace(symbol="MSFT", entry_price=300.0, quantity=10, strategy_name="momentum")
    # AAPL: 1000, MSFT: 3000 → breakout=25%, momentum=75%
    result = _compute_capital_pct([pos_a, pos_b], {})
    assert result["breakout"] == pytest.approx(25.0)
    assert result["momentum"] == pytest.approx(75.0)


def test_compute_capital_pct_rounds_to_one_decimal() -> None:
    pos_a = SimpleNamespace(symbol="AAPL", entry_price=100.0, quantity=1, strategy_name="breakout")
    pos_b = SimpleNamespace(symbol="MSFT", entry_price=200.0, quantity=1, strategy_name="momentum")
    # breakout: 100/300 = 33.333...% → rounds to 33.3; momentum: 66.7
    result = _compute_capital_pct([pos_a, pos_b], {})
    assert result["breakout"] == pytest.approx(33.3, abs=0.05)
    assert result["momentum"] == pytest.approx(66.7, abs=0.05)


# ---------------------------------------------------------------------------
# DashboardSnapshot.strategy_win_loss and strategy_capital_pct
# ---------------------------------------------------------------------------

def test_load_dashboard_snapshot_populates_strategy_win_loss() -> None:
    fixed_now = datetime(2026, 5, 6, 14, 0, tzinfo=timezone.utc)
    win_loss_data = {"breakout": (5, 2), "momentum": (1, 3)}

    snapshot = load_dashboard_snapshot(
        settings=make_settings(),
        connection=SimpleNamespace(),
        now=fixed_now,
        **{
            **make_snapshot_stores(),
            "order_store": SimpleNamespace(
                list_by_status=lambda **_: [],
                list_recent=lambda **_: [],
                win_loss_counts_by_strategy=lambda **_: win_loss_data,
            ),
        },
    )

    assert snapshot.strategy_win_loss == {"breakout": (5, 2), "momentum": (1, 3)}


def test_load_dashboard_snapshot_strategy_win_loss_empty_when_no_closed_trades() -> None:
    """strategy_win_loss is {} when win_loss_counts_by_strategy returns empty dict."""
    fixed_now = datetime(2026, 5, 6, 14, 0, tzinfo=timezone.utc)

    snapshot = load_dashboard_snapshot(
        settings=make_settings(),
        connection=SimpleNamespace(),
        now=fixed_now,
        **make_snapshot_stores(),
    )

    assert snapshot.strategy_win_loss == {}


def test_load_dashboard_snapshot_populates_strategy_capital_pct() -> None:
    fixed_now = datetime(2026, 5, 6, 14, 0, tzinfo=timezone.utc)
    pos = SimpleNamespace(symbol="AAPL", entry_price=100.0, quantity=10, strategy_name="breakout")

    snapshot = load_dashboard_snapshot(
        settings=make_settings(),
        connection=SimpleNamespace(),
        now=fixed_now,
        **{
            **make_snapshot_stores(),
            "position_store": SimpleNamespace(list_all=lambda **_: [pos]),
        },
        latest_prices={"AAPL": 105.0},
    )

    assert snapshot.strategy_capital_pct == {"breakout": pytest.approx(100.0)}


def test_load_dashboard_snapshot_populates_strategy_lifetime_pnl() -> None:
    fixed_now = datetime(2026, 5, 7, 14, 0, tzinfo=timezone.utc)
    lifetime_data = {"breakout": 1234.56, "momentum": -200.0}

    snapshot = load_dashboard_snapshot(
        settings=make_settings(),
        connection=SimpleNamespace(),
        now=fixed_now,
        **{
            **make_snapshot_stores(),
            "order_store": SimpleNamespace(
                list_by_status=lambda **_: [],
                list_recent=lambda **_: [],
                win_loss_counts_by_strategy=lambda **_: {},
                lifetime_pnl_by_strategy=lambda **_: lifetime_data,
            ),
        },
    )

    assert snapshot.strategy_lifetime_pnl == {"breakout": pytest.approx(1234.56), "momentum": pytest.approx(-200.0)}


def test_load_dashboard_snapshot_strategy_lifetime_pnl_empty_when_no_closed_trades() -> None:
    """strategy_lifetime_pnl is {} when lifetime_pnl_by_strategy returns {}."""
    fixed_now = datetime(2026, 5, 7, 14, 0, tzinfo=timezone.utc)

    snapshot = load_dashboard_snapshot(
        settings=make_settings(),
        connection=SimpleNamespace(),
        now=fixed_now,
        **make_snapshot_stores(),
    )

    assert snapshot.strategy_lifetime_pnl == {}


def test_load_dashboard_snapshot_populates_account_equity() -> None:
    """account_equity is read from the latest supervisor_cycle audit event payload."""
    fixed_now = datetime(2026, 5, 7, 14, 0, tzinfo=timezone.utc)
    cycle_event = SimpleNamespace(
        event_type="supervisor_cycle",
        created_at=fixed_now,
        payload={"entries_disabled": False, "account_equity": 9_234.56},
    )

    snapshot = load_dashboard_snapshot(
        settings=make_settings(),
        connection=SimpleNamespace(),
        now=fixed_now,
        **make_snapshot_stores(latest=cycle_event),
    )

    assert snapshot.account_equity is not None
    assert abs(snapshot.account_equity - 9_234.56) < 1e-6


def test_load_dashboard_snapshot_account_equity_none_when_no_cycle_event() -> None:
    """account_equity is None when no supervisor_cycle event exists."""
    fixed_now = datetime(2026, 5, 7, 14, 0, tzinfo=timezone.utc)

    snapshot = load_dashboard_snapshot(
        settings=make_settings(),
        connection=SimpleNamespace(),
        now=fixed_now,
        **make_snapshot_stores(latest=None),
    )

    assert snapshot.account_equity is None


def test_load_dashboard_snapshot_populates_total_deployed_notional() -> None:
    """total_deployed_notional sums quantity * entry_price over open positions."""
    fixed_now = datetime(2026, 5, 7, 14, 0, tzinfo=timezone.utc)
    pos1 = SimpleNamespace(symbol="AAPL", quantity=10.0, entry_price=150.0, strategy_name="breakout")
    pos2 = SimpleNamespace(symbol="MSFT", quantity=5.0, entry_price=300.0, strategy_name="orb")

    stores = make_snapshot_stores()
    stores["position_store"] = SimpleNamespace(list_all=lambda **_: [pos1, pos2])

    snapshot = load_dashboard_snapshot(
        settings=make_settings(),
        connection=SimpleNamespace(),
        now=fixed_now,
        **stores,
    )

    # 10 * 150 + 5 * 300 = 1500 + 1500 = 3000
    assert abs(snapshot.total_deployed_notional - 3_000.0) < 1e-6


# ---------------------------------------------------------------------------
# load_confidence_floor_info
# ---------------------------------------------------------------------------


def _make_confidence_floor_record(**overrides):
    """Build a minimal fake ConfidenceFloor-like namespace."""
    defaults = dict(
        floor_value=0.30,
        manual_floor_baseline=0.25,
        set_by="system",
        reason="auto-raised: drawdown",
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _make_floor_store(record=None):
    """Return a fake store whose load() returns record."""
    return SimpleNamespace(load=lambda **_: record)


def test_load_confidence_floor_info_with_record_auto_raised_drawdown() -> None:
    """When a DB record exists with floor_value > manual_floor_baseline, auto_raised=True
    and trigger is parsed from the reason field."""
    settings = make_settings()
    record = _make_confidence_floor_record(
        floor_value=0.35,
        manual_floor_baseline=0.25,
        set_by="system",
        reason="auto-raised: drawdown",
    )
    result = load_confidence_floor_info(settings=settings, confidence_floor_store=_make_floor_store(record))

    assert result["floor_value"] == pytest.approx(0.35)
    assert result["manual_baseline"] == pytest.approx(0.25)
    assert result["set_by"] == "system"
    assert result["reason"] == "auto-raised: drawdown"
    assert result["auto_raised"] is True
    assert result["trigger"] == "drawdown"
    assert result["no_record"] is False


def test_load_confidence_floor_info_with_record_auto_raised_volatility() -> None:
    """trigger is 'volatility' when reason contains 'volatility'."""
    settings = make_settings()
    record = _make_confidence_floor_record(
        floor_value=0.32,
        manual_floor_baseline=0.25,
        set_by="system",
        reason="auto-raised: volatility spike",
    )
    result = load_confidence_floor_info(settings=settings, confidence_floor_store=_make_floor_store(record))

    assert result["auto_raised"] is True
    assert result["trigger"] == "volatility"


def test_load_confidence_floor_info_with_record_auto_raised_vol_abbreviation() -> None:
    """trigger is 'volatility' when reason contains 'vol' (abbreviation)."""
    settings = make_settings()
    record = _make_confidence_floor_record(
        floor_value=0.32,
        manual_floor_baseline=0.25,
        set_by="system",
        reason="auto-raised: vol subsided",
    )
    result = load_confidence_floor_info(settings=settings, confidence_floor_store=_make_floor_store(record))

    assert result["trigger"] == "volatility"


def test_load_confidence_floor_info_trigger_none_for_operator_reason() -> None:
    """trigger is None when reason is 'operator' or does not match known patterns."""
    settings = make_settings()
    record = _make_confidence_floor_record(
        floor_value=0.25,
        manual_floor_baseline=0.25,
        set_by="operator",
        reason="operator",
    )
    result = load_confidence_floor_info(settings=settings, confidence_floor_store=_make_floor_store(record))

    assert result["trigger"] is None


def test_load_confidence_floor_info_trigger_none_for_manual_reason() -> None:
    """trigger is None when reason is 'manual set'."""
    settings = make_settings()
    record = _make_confidence_floor_record(
        floor_value=0.25,
        manual_floor_baseline=0.25,
        set_by="operator",
        reason="manual set",
    )
    result = load_confidence_floor_info(settings=settings, confidence_floor_store=_make_floor_store(record))

    assert result["trigger"] is None


def test_load_confidence_floor_info_auto_raised_false_when_equal() -> None:
    """auto_raised is False when floor_value == manual_floor_baseline."""
    settings = make_settings()
    record = _make_confidence_floor_record(
        floor_value=0.25,
        manual_floor_baseline=0.25,
        set_by="operator",
        reason="operator",
    )
    result = load_confidence_floor_info(settings=settings, confidence_floor_store=_make_floor_store(record))

    assert result["auto_raised"] is False


def test_load_confidence_floor_info_no_record_falls_back_to_settings() -> None:
    """When no DB record exists, floor_value and manual_baseline use settings.confidence_floor."""
    settings = make_settings()  # default confidence_floor = 0.25
    result = load_confidence_floor_info(settings=settings, confidence_floor_store=_make_floor_store(None))

    assert result["floor_value"] == pytest.approx(settings.confidence_floor)
    assert result["manual_baseline"] == pytest.approx(settings.confidence_floor)
    assert result["set_by"] == "operator"
    assert result["reason"] is None
    assert result["auto_raised"] is False
    assert result["trigger"] is None
    assert result["no_record"] is True


def test_load_confidence_floor_info_no_record_has_no_record_true() -> None:
    """no_record is True when the store returns None."""
    settings = make_settings()
    result = load_confidence_floor_info(settings=settings, confidence_floor_store=_make_floor_store(None))

    assert result["no_record"] is True


def test_load_confidence_floor_info_with_record_no_record_false() -> None:
    """no_record is False when a record exists."""
    settings = make_settings()
    record = _make_confidence_floor_record()
    result = load_confidence_floor_info(settings=settings, confidence_floor_store=_make_floor_store(record))

    assert result["no_record"] is False


def test_load_confidence_floor_info_store_called_with_trading_mode_and_strategy_version() -> None:
    """The store is called with the correct trading_mode and strategy_version from settings."""
    settings = make_settings()
    captured: list[dict] = []

    def recording_load(**kwargs):
        captured.append(dict(kwargs))
        return None

    load_confidence_floor_info(
        settings=settings,
        confidence_floor_store=SimpleNamespace(load=recording_load),
    )

    assert len(captured) == 1
    assert captured[0]["trading_mode"] == settings.trading_mode
    assert captured[0]["strategy_version"] == settings.strategy_version
