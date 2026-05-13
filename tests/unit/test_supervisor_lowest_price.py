from __future__ import annotations

from datetime import datetime, timezone
from importlib import import_module
from types import SimpleNamespace

from alpaca_bot.config import TradingMode
from alpaca_bot.domain import Bar, OpenPosition
from alpaca_bot.storage.models import PositionRecord


def _make_settings(**overrides):
    from alpaca_bot.config import Settings

    base = {
        "TRADING_MODE": "paper",
        "ENABLE_LIVE_TRADING": "false",
        "STRATEGY_VERSION": "v1",
        "DATABASE_URL": "postgresql://x:y@localhost/db",
        "MARKET_DATA_FEED": "sip",
        "SYMBOLS": "QBTS",
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
        "PER_SYMBOL_LOSS_LIMIT_PCT": "0.0",
    }
    base.update(overrides)
    return Settings.from_env(base)


def _make_bar(low: float, symbol: str = "QBTS") -> Bar:
    return Bar(
        symbol=symbol,
        timestamp=datetime(2026, 5, 1, 14, 0, tzinfo=timezone.utc),
        open=low + 0.10,
        high=low + 0.20,
        low=low,
        close=low + 0.05,
        volume=100_000,
    )


def _make_short_position(lowest_price: float = 5.00, symbol: str = "QBTS") -> OpenPosition:
    return OpenPosition(
        symbol=symbol,
        entry_timestamp=datetime(2026, 5, 1, 10, 0, tzinfo=timezone.utc),
        entry_price=5.00,
        quantity=-100.0,
        entry_level=5.03,
        initial_stop_price=5.03,
        stop_price=5.03,
        trailing_active=False,
        highest_price=5.00,
        lowest_price=lowest_price,
        strategy_name="breakout",
    )


def _make_long_position(symbol: str = "AAPL") -> OpenPosition:
    return OpenPosition(
        symbol=symbol,
        entry_timestamp=datetime(2026, 5, 1, 10, 0, tzinfo=timezone.utc),
        entry_price=3.00,
        quantity=100.0,
        entry_level=2.97,
        initial_stop_price=2.97,
        stop_price=2.97,
        trailing_active=False,
        highest_price=3.00,
        lowest_price=3.00,
        strategy_name="breakout",
    )


class _RecordingPositionStore:
    def __init__(self):
        self.update_calls: list[dict] = []

    def update_lowest_price(self, **kwargs):
        self.update_calls.append(kwargs)


def _make_supervisor(settings, position_store=None):
    module = import_module("alpaca_bot.runtime.supervisor")
    RuntimeSupervisor = module.RuntimeSupervisor

    _position_store = position_store or _RecordingPositionStore()

    class _FakeRuntimeContext:
        connection = SimpleNamespace(commit=lambda: None, rollback=lambda: None)
        store_lock = None
        order_store = SimpleNamespace(
            save=lambda *a, **kw: None,
            list_by_status=lambda **kw: [],
            list_pending_submit=lambda **kw: [],
            daily_realized_pnl=lambda **kw: 0.0,
            daily_realized_pnl_by_symbol=lambda **kw: {},
        )
        strategy_weight_store = None
        option_order_store = None
        trading_status_store = SimpleNamespace(load=lambda **kw: None)
        position_store = _position_store
        daily_session_state_store = SimpleNamespace(
            load=lambda **kw: None, save=lambda **kw: None, list_by_session=lambda **kw: []
        )
        audit_event_store = SimpleNamespace(
            append=lambda *a, **kw: None,
            load_latest=lambda **kw: None,
            list_recent=lambda **kw: [],
            list_by_event_types=lambda **kw: [],
        )
        strategy_flag_store = SimpleNamespace(list_all=lambda **kw: [], load=lambda **kw: None)
        watchlist_store = SimpleNamespace(list_enabled=lambda *a: ["QBTS"], list_ignored=lambda *a: [])

        def commit(self):
            pass

    return RuntimeSupervisor(
        settings=settings,
        runtime=_FakeRuntimeContext(),
        broker=SimpleNamespace(
            get_account=lambda: SimpleNamespace(equity=10_000.0, buying_power=20_000.0, trading_blocked=False),
            list_open_orders=lambda: [],
        ),
        market_data=SimpleNamespace(get_stock_bars=lambda **kw: {}, get_daily_bars=lambda **kw: {}),
        stream=None,
        close_runtime_fn=lambda _: None,
        connection_checker=lambda _: True,
        cycle_runner=lambda **kw: SimpleNamespace(intents=[]),
        cycle_intent_executor=lambda **kw: SimpleNamespace(submitted_exit_count=0, failed_exit_count=0),
        order_dispatcher=lambda **kw: {"submitted_count": 0},
    )


def test_apply_lowest_price_updates_tracks_new_low():
    """When bar.low < position.lowest_price: DB updated, returned list has new value."""
    settings = _make_settings()
    pstore = _RecordingPositionStore()
    supervisor = _make_supervisor(settings, pstore)

    position = _make_short_position(lowest_price=5.00)
    bars = {"QBTS": [_make_bar(low=4.75)]}

    result = supervisor._apply_lowest_price_updates([position], bars)

    assert len(result) == 1
    assert result[0].lowest_price == 4.75
    assert len(pstore.update_calls) == 1
    assert pstore.update_calls[0]["lowest_price"] == 4.75
    assert pstore.update_calls[0]["symbol"] == "QBTS"


def test_apply_lowest_price_updates_ignores_higher_low():
    """When bar.low >= position.lowest_price: no DB call, position unchanged."""
    settings = _make_settings()
    pstore = _RecordingPositionStore()
    supervisor = _make_supervisor(settings, pstore)

    position = _make_short_position(lowest_price=4.75)
    bars = {"QBTS": [_make_bar(low=4.90)]}

    result = supervisor._apply_lowest_price_updates([position], bars)

    assert result[0].lowest_price == 4.75
    assert pstore.update_calls == []


def test_apply_lowest_price_updates_skips_long_positions():
    """Long positions (qty >= 0) must be passed through unchanged — lowest_price is only for shorts."""
    settings = _make_settings(SYMBOLS="AAPL")
    pstore = _RecordingPositionStore()
    supervisor = _make_supervisor(settings, pstore)

    position = _make_long_position(symbol="AAPL")
    bars = {"AAPL": [_make_bar(low=2.50, symbol="AAPL")]}

    result = supervisor._apply_lowest_price_updates([position], bars)

    assert result[0].lowest_price == 3.00  # unchanged
    assert pstore.update_calls == []


def test_load_open_positions_uses_db_lowest_price():
    """_load_open_positions() must use position.lowest_price (or entry_price if None) for short positions."""
    settings = _make_settings()

    class _PositionStoreWithRecord:
        def list_all(self, **kwargs):
            return [
                PositionRecord(
                    symbol="QBTS",
                    trading_mode=TradingMode.PAPER,
                    strategy_version="v1",
                    quantity=-10.0,
                    entry_price=5.00,
                    stop_price=5.03,
                    initial_stop_price=5.03,
                    opened_at=datetime(2026, 5, 1, tzinfo=timezone.utc),
                    highest_price=5.00,
                    lowest_price=4.80,
                )
            ]

    supervisor = _make_supervisor(settings)
    supervisor.runtime.position_store = _PositionStoreWithRecord()

    positions = supervisor._load_open_positions()
    assert len(positions) == 1
    assert positions[0].lowest_price == 4.80


def test_load_open_positions_null_lowest_price_falls_back_to_entry_price():
    """When DB lowest_price is NULL, fall back to entry_price."""
    settings = _make_settings()

    class _PositionStoreNullLowest:
        def list_all(self, **kwargs):
            return [
                PositionRecord(
                    symbol="QBTS",
                    trading_mode=TradingMode.PAPER,
                    strategy_version="v1",
                    quantity=-10.0,
                    entry_price=5.00,
                    stop_price=5.03,
                    initial_stop_price=5.03,
                    opened_at=datetime(2026, 5, 1, tzinfo=timezone.utc),
                    highest_price=5.00,
                    lowest_price=None,
                )
            ]

    supervisor = _make_supervisor(settings)
    supervisor.runtime.position_store = _PositionStoreNullLowest()

    positions = supervisor._load_open_positions()
    assert positions[0].lowest_price == 5.00  # falls back to entry_price
