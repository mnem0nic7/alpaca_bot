from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from alpaca_bot.config import TradingMode
from alpaca_bot.runtime.supervisor import RuntimeSupervisor
from alpaca_bot.storage.models import StrategyFlag


def _make_settings(symbols=("AAPL",)):
    from alpaca_bot.config import Settings, TradingMode, MarketDataFeed
    from datetime import time
    return Settings(
        trading_mode=TradingMode.PAPER,
        enable_live_trading=False,
        strategy_version="v1",
        database_url="postgresql://localhost/test",
        market_data_feed=MarketDataFeed.SIP,
        symbols=symbols,
        daily_sma_period=20,
        breakout_lookback_bars=20,
        relative_volume_lookback_bars=20,
        relative_volume_threshold=1.5,
        entry_timeframe_minutes=15,
        risk_per_trade_pct=0.01,
        max_position_pct=0.1,
        max_open_positions=3,
        max_portfolio_exposure_pct=1.0,
        daily_loss_limit_pct=0.01,
        stop_limit_buffer_pct=0.001,
        breakout_stop_buffer_pct=0.001,
        entry_stop_price_buffer=0.01,
        entry_window_start=time(10, 0),
        entry_window_end=time(15, 30),
        flatten_time=time(15, 45),
    )


def _make_runtime(flags=None):
    return SimpleNamespace(
        connection=SimpleNamespace(),
        trading_status_store=SimpleNamespace(load=lambda **_: None),
        order_store=SimpleNamespace(
            save=lambda _: None,
            list_by_status=lambda **_: [],
            list_pending_submit=lambda **_: [],
            daily_realized_pnl=lambda **_: 0.0,
        ),
        daily_session_state_store=SimpleNamespace(
            load=lambda **_: None,
            save=lambda _: None,
        ),
        position_store=SimpleNamespace(list_all=lambda **_: [], replace_all=lambda **_: None),
        audit_event_store=SimpleNamespace(append=lambda _: None),
        strategy_flag_store=SimpleNamespace(
            load=lambda **_: None,
            list_all=lambda **_: flags or [],
        ),
    )


def _make_supervisor(runtime=None, cycle_calls=None):
    settings = _make_settings()
    if runtime is None:
        runtime = _make_runtime()
    if cycle_calls is None:
        cycle_calls = []

    def fake_cycle_runner(**kwargs):
        cycle_calls.append(kwargs)
        return SimpleNamespace(intents=[])

    return RuntimeSupervisor(
        settings=settings,
        runtime=runtime,
        broker=SimpleNamespace(
            get_account=lambda: SimpleNamespace(equity=100_000.0),
            get_open_orders=lambda: [],
            get_open_positions=lambda: [],
            get_clock=lambda: SimpleNamespace(is_open=False),
        ),
        market_data=SimpleNamespace(
            get_stock_bars=lambda **_: {},
            get_daily_bars=lambda **_: {},
        ),
        stream=SimpleNamespace(
            subscribe_trade_updates=lambda _: None,
            run=lambda: None,
            stop=lambda: None,
        ),
        cycle_runner=fake_cycle_runner,
        order_dispatcher=lambda **_: {"submitted_count": 0},
        cycle_intent_executor=lambda **_: None,
        close_runtime_fn=lambda _: None,
        connection_checker=lambda _: True,
    ), cycle_calls


def test_resolve_active_strategies_default_enabled():
    supervisor, _ = _make_supervisor()
    # No flags stored → breakout enabled (missing row = enabled)
    active = supervisor._resolve_active_strategies()
    strategy_names = [name for name, _ in active]
    assert "breakout" in strategy_names


def test_resolve_active_strategies_one_disabled():
    runtime = _make_runtime(flags=[
        StrategyFlag(
            strategy_name="breakout",
            trading_mode=TradingMode.PAPER,
            strategy_version="v1",
            enabled=False,
            updated_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
        )
    ])

    def fake_load(*, strategy_name, trading_mode, strategy_version):
        for f in runtime.strategy_flag_store.list_all():
            if f.strategy_name == strategy_name:
                return f
        return None

    runtime.strategy_flag_store.load = fake_load

    supervisor, _ = _make_supervisor(runtime=runtime)
    active = supervisor._resolve_active_strategies()
    strategy_names = [name for name, _ in active]
    assert "breakout" not in strategy_names


def test_cycle_runner_receives_strategy_name():
    cycle_calls = []
    supervisor, _ = _make_supervisor(cycle_calls=cycle_calls)
    # Patch broker to simulate open market
    supervisor.broker = SimpleNamespace(
        get_account=lambda: SimpleNamespace(equity=100_000.0),
        get_open_orders=lambda: [],
        get_open_positions=lambda: [],
        get_clock=lambda: SimpleNamespace(is_open=True),
    )
    supervisor.run_cycle_once(now=lambda: datetime(2026, 1, 2, 16, 0, tzinfo=timezone.utc))
    assert len(cycle_calls) >= 1
    for call in cycle_calls:
        assert "strategy_name" in call


def test_blocked_strategy_entries_not_dispatched():
    """Entry orders for a strategy with entries_disabled are not dispatched."""
    from alpaca_bot.storage.models import DailySessionState
    from datetime import date

    dispatch_calls = []

    def fake_dispatch(**kwargs):
        dispatch_calls.append(kwargs)
        return {"submitted_count": 0}

    session_date = date(2026, 1, 2)
    momentum_session = DailySessionState(
        session_date=session_date,
        trading_mode=TradingMode.PAPER,
        strategy_version="v1",
        strategy_name="breakout",
        entries_disabled=True,
        flatten_complete=False,
    )

    runtime = _make_runtime()
    runtime.daily_session_state_store = SimpleNamespace(
        load=lambda **_: momentum_session,
        save=lambda _: None,
    )

    settings = _make_settings()
    cycle_calls = []

    def fake_cycle_runner(**kwargs):
        cycle_calls.append(kwargs)
        return SimpleNamespace(intents=[])

    supervisor = RuntimeSupervisor(
        settings=settings,
        runtime=runtime,
        broker=SimpleNamespace(
            get_account=lambda: SimpleNamespace(equity=100_000.0),
            get_open_orders=lambda: [],
            get_open_positions=lambda: [],
            get_clock=lambda: SimpleNamespace(is_open=True),
        ),
        market_data=SimpleNamespace(
            get_stock_bars=lambda **_: {},
            get_daily_bars=lambda **_: {},
        ),
        stream=None,
        cycle_runner=fake_cycle_runner,
        order_dispatcher=fake_dispatch,
        cycle_intent_executor=lambda **_: None,
        close_runtime_fn=lambda _: None,
        connection_checker=lambda _: True,
    )

    supervisor.run_cycle_once(now=lambda: datetime(2026, 1, 2, 16, 0, tzinfo=timezone.utc))
    # The dispatcher should have been called with blocked_strategy_names containing "breakout"
    assert len(dispatch_calls) >= 1
    last_call = dispatch_calls[-1]
    assert "blocked_strategy_names" in last_call
    assert "breakout" in last_call["blocked_strategy_names"]
