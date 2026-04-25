from __future__ import annotations

from datetime import datetime, timezone, date
from types import SimpleNamespace

from alpaca_bot.core.engine import CycleIntent, CycleIntentType, evaluate_cycle
from alpaca_bot.domain.models import Bar, OpenPosition


def _make_settings(
    symbols=("AAPL",),
    max_open_positions=3,
):
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
        max_open_positions=max_open_positions,
        max_portfolio_exposure_pct=1.0,
        daily_loss_limit_pct=0.01,
        stop_limit_buffer_pct=0.001,
        breakout_stop_buffer_pct=0.001,
        entry_stop_price_buffer=0.01,
        entry_window_start=time(10, 0),
        entry_window_end=time(15, 30),
        flatten_time=time(15, 45),
    )


def test_cycle_intent_has_strategy_name():
    intent = CycleIntent(
        intent_type=CycleIntentType.EXIT,
        symbol="AAPL",
        timestamp=datetime(2026, 1, 2, 14, tzinfo=timezone.utc),
        strategy_name="momentum",
    )
    assert intent.strategy_name == "momentum"


def test_cycle_intent_default_strategy_name():
    intent = CycleIntent(
        intent_type=CycleIntentType.EXIT,
        symbol="AAPL",
        timestamp=datetime(2026, 1, 2, 14, tzinfo=timezone.utc),
    )
    assert intent.strategy_name == "breakout"


def test_evaluate_cycle_threads_strategy_name():
    settings = _make_settings()
    now = datetime(2026, 1, 2, 20, 50, tzinfo=timezone.utc)  # past flatten time (15:50 ET)

    open_positions = [
        OpenPosition(
            symbol="AAPL",
            entry_timestamp=datetime(2026, 1, 2, 10, tzinfo=timezone.utc),
            entry_price=150.0,
            quantity=10,
            entry_level=148.0,
            initial_stop_price=147.0,
            stop_price=147.0,
            strategy_name="momentum",
        )
    ]

    result = evaluate_cycle(
        settings=settings,
        now=now,
        equity=100_000.0,
        intraday_bars_by_symbol={"AAPL": [Bar(
            symbol="AAPL",
            timestamp=now,  # 20:50 UTC = 15:50 ET, past flatten time
            open=151.0, high=152.0, low=150.0, close=151.5, volume=10000.0,
        )]},
        daily_bars_by_symbol={"AAPL": []},
        open_positions=open_positions,
        working_order_symbols=set(),
        traded_symbols_today=set(),
        entries_disabled=True,
        strategy_name="momentum",
    )
    # All intents should carry strategy_name="momentum"
    assert len(result.intents) > 0
    for intent in result.intents:
        assert intent.strategy_name == "momentum"


def test_client_order_id_includes_strategy_name():
    from alpaca_bot.core.engine import _client_order_id

    settings = _make_settings()
    ts = datetime(2026, 1, 2, 14, 0, 0, tzinfo=timezone.utc)
    cid = _client_order_id(settings=settings, symbol="AAPL", signal_timestamp=ts, strategy_name="momentum")
    assert cid.startswith("momentum:")


def test_run_cycle_writes_order_with_strategy_name():
    from alpaca_bot.runtime.cycle import run_cycle
    from alpaca_bot.domain.models import Bar, EntrySignal

    saved_orders = []

    settings = _make_settings()
    now = datetime(2026, 1, 2, 14, 0, tzinfo=timezone.utc)

    signal_bar = Bar(
        symbol="AAPL", timestamp=now,
        open=100.0, high=105.0, low=99.0, close=104.0, volume=100_000.0,
    )

    def fake_evaluator(*, symbol, intraday_bars, signal_index, daily_bars, settings):
        return EntrySignal(
            symbol=symbol,
            signal_bar=signal_bar,
            entry_level=100.0,
            relative_volume=3.0,
            stop_price=99.5,
            limit_price=99.6,
            initial_stop_price=98.0,
        )

    runtime = SimpleNamespace(
        order_store=SimpleNamespace(save=saved_orders.append),
        audit_event_store=SimpleNamespace(append=lambda _: None),
    )

    run_cycle(
        settings=settings,
        runtime=runtime,
        now=now,
        equity=100_000.0,
        intraday_bars_by_symbol={"AAPL": [signal_bar] * 25},
        daily_bars_by_symbol={"AAPL": [signal_bar] * 25},
        open_positions=[],
        working_order_symbols=set(),
        traded_symbols_today=set(),
        entries_disabled=False,
        strategy_name="momentum",
        signal_evaluator=fake_evaluator,
    )
    assert len(saved_orders) == 1
    assert saved_orders[0].strategy_name == "momentum"


def test_exit_intent_does_not_cancel_other_strategy_stop():
    """A momentum EXIT intent must NOT cancel breakout's stop on the same symbol."""
    from alpaca_bot.runtime.cycle_intent_execution import execute_cycle_intents
    from alpaca_bot.storage.models import OrderRecord, PositionRecord
    from alpaca_bot.config import TradingMode

    now = datetime(2026, 1, 2, 14, 0, tzinfo=timezone.utc)
    settings = _make_settings()

    breakout_stop = OrderRecord(
        client_order_id="breakout:v1:2026-01-02:AAPL:stop:t",
        symbol="AAPL", side="sell", intent_type="stop", status="new",
        quantity=10, trading_mode=TradingMode.PAPER, strategy_version="v1",
        strategy_name="breakout", broker_order_id="broker-breakout-1",
    )
    momentum_stop = OrderRecord(
        client_order_id="momentum:v1:2026-01-02:AAPL:stop:t",
        symbol="AAPL", side="sell", intent_type="stop", status="new",
        quantity=5, trading_mode=TradingMode.PAPER, strategy_version="v1",
        strategy_name="momentum", broker_order_id="broker-momentum-1",
    )
    momentum_position = PositionRecord(
        symbol="AAPL", trading_mode=TradingMode.PAPER, strategy_version="v1",
        strategy_name="momentum", quantity=5, entry_price=150.0,
        stop_price=148.0, initial_stop_price=147.0,
        opened_at=now,
    )

    canceled_ids = []

    def fake_list_by_status(*, trading_mode, strategy_version, statuses, strategy_name=None):
        orders = [breakout_stop, momentum_stop]
        if strategy_name is not None:
            orders = [o for o in orders if o.strategy_name == strategy_name]
        return [o for o in orders if o.status in statuses]

    runtime = SimpleNamespace(
        order_store=SimpleNamespace(
            list_by_status=fake_list_by_status,
            save=lambda _: None,
        ),
        position_store=SimpleNamespace(
            list_all=lambda **_: [momentum_position],
            save=lambda _: None,
        ),
        audit_event_store=SimpleNamespace(append=lambda _: None),
    )
    fake_broker = SimpleNamespace(
        cancel_order=lambda order_id: canceled_ids.append(order_id),
        submit_market_exit=lambda **kw: SimpleNamespace(
            status="pending_new", broker_order_id="exit-1", quantity=kw["quantity"]
        ),
    )

    execute_cycle_intents(
        settings=settings,
        runtime=runtime,
        broker=fake_broker,
        cycle_result=SimpleNamespace(intents=[
            CycleIntent(
                intent_type=CycleIntentType.EXIT,
                symbol="AAPL",
                timestamp=now,
                strategy_name="momentum",
            )
        ]),
        now=now,
    )

    assert "broker-momentum-1" in canceled_ids
    assert "broker-breakout-1" not in canceled_ids, "EXIT for momentum must NOT cancel breakout stop"
