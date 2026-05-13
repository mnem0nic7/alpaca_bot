from __future__ import annotations

import threading
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
        "PER_SYMBOL_LOSS_LIMIT_PCT": "0.0",
    }
    base.update(overrides)
    return Settings.from_env(base)


def _make_bar(high: float) -> Bar:
    return Bar(
        symbol="AAPL",
        timestamp=datetime(2026, 5, 1, 14, 0, tzinfo=timezone.utc),
        open=high - 0.10,
        high=high,
        low=high - 0.20,
        close=high - 0.05,
        volume=100_000,
    )


def _make_position(highest_price: float = 3.00) -> OpenPosition:
    return OpenPosition(
        symbol="AAPL",
        entry_timestamp=datetime(2026, 5, 1, 10, 0, tzinfo=timezone.utc),
        entry_price=3.00,
        quantity=100.0,
        entry_level=2.97,
        initial_stop_price=2.97,
        stop_price=2.97,
        trailing_active=False,
        highest_price=highest_price,
        strategy_name="breakout",
    )


class _RecordingPositionStore:
    def __init__(self):
        self.update_calls: list[dict] = []

    def update_highest_price(self, **kwargs):
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
        watchlist_store = SimpleNamespace(list_enabled=lambda *a: ["AAPL"], list_ignored=lambda *a: [])

        def commit(self): pass

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


def test_apply_highest_price_updates_bar_high_exceeds_current():
    """When bar.high > position.highest_price: DB updated, returned list has new value."""
    settings = _make_settings()
    pstore = _RecordingPositionStore()
    supervisor = _make_supervisor(settings, pstore)

    position = _make_position(highest_price=3.00)
    bars = {"AAPL": [_make_bar(high=3.20)]}

    result = supervisor._apply_highest_price_updates([position], bars)

    assert len(result) == 1
    assert result[0].highest_price == 3.20
    assert len(pstore.update_calls) == 1
    assert pstore.update_calls[0]["highest_price"] == 3.20
    assert pstore.update_calls[0]["symbol"] == "AAPL"


def test_apply_highest_price_updates_bar_high_equal_no_update():
    """When bar.high == position.highest_price: no DB call, position unchanged."""
    settings = _make_settings()
    pstore = _RecordingPositionStore()
    supervisor = _make_supervisor(settings, pstore)

    position = _make_position(highest_price=3.20)
    bars = {"AAPL": [_make_bar(high=3.20)]}

    result = supervisor._apply_highest_price_updates([position], bars)

    assert result[0].highest_price == 3.20
    assert pstore.update_calls == []


def test_apply_highest_price_updates_bar_high_lower_no_update():
    """When bar.high < position.highest_price: no DB call, position unchanged."""
    settings = _make_settings()
    pstore = _RecordingPositionStore()
    supervisor = _make_supervisor(settings, pstore)

    position = _make_position(highest_price=3.20)
    bars = {"AAPL": [_make_bar(high=3.09)]}

    result = supervisor._apply_highest_price_updates([position], bars)

    assert result[0].highest_price == 3.20
    assert pstore.update_calls == []


def test_apply_highest_price_updates_no_bars_skipped():
    """Position absent from bars dict: skipped, returned unchanged."""
    settings = _make_settings()
    pstore = _RecordingPositionStore()
    supervisor = _make_supervisor(settings, pstore)

    position = _make_position(highest_price=3.00)
    bars = {}  # no bars for AAPL

    result = supervisor._apply_highest_price_updates([position], bars)

    assert result[0].highest_price == 3.00
    assert pstore.update_calls == []


def test_apply_highest_price_updates_store_lock_held():
    """DB write must occur inside store_lock."""
    settings = _make_settings()
    pstore = _RecordingPositionStore()
    lock = threading.Lock()
    lock_acquired_during_update = []

    original_update = pstore.update_highest_price

    def recording_update(**kwargs):
        lock_acquired_during_update.append(not lock.acquire(blocking=False))
        if not lock_acquired_during_update[-1]:
            lock.release()
        original_update(**kwargs)

    pstore.update_highest_price = recording_update

    supervisor = _make_supervisor(settings, pstore)
    supervisor.runtime.store_lock = lock

    position = _make_position(highest_price=3.00)
    bars = {"AAPL": [_make_bar(high=3.20)]}
    supervisor._apply_highest_price_updates([position], bars)

    # Lock must have been held during the update call (lock.acquire returned False)
    assert lock_acquired_during_update == [True], "store_lock was not held during DB update"


def test_load_open_positions_uses_db_highest_price():
    """_load_open_positions() must use position.highest_price (or entry_price if None), not always entry_price."""
    settings = _make_settings()

    class _PositionStoreWithRecord:
        def list_all(self, **kwargs):
            return [
                PositionRecord(
                    symbol="AAPL",
                    trading_mode=TradingMode.PAPER,
                    strategy_version="v1",
                    quantity=10.0,
                    entry_price=3.00,
                    stop_price=2.97,
                    initial_stop_price=2.97,
                    opened_at=datetime(2026, 5, 1, tzinfo=timezone.utc),
                    highest_price=3.20,
                )
            ]

    supervisor = _make_supervisor(settings)
    supervisor.runtime.position_store = _PositionStoreWithRecord()

    positions = supervisor._load_open_positions()
    assert len(positions) == 1
    assert positions[0].highest_price == 3.20


def test_load_open_positions_null_highest_price_falls_back_to_entry_price():
    """When DB highest_price is NULL, fall back to entry_price."""
    settings = _make_settings()

    class _PositionStoreNullHighest:
        def list_all(self, **kwargs):
            return [
                PositionRecord(
                    symbol="AAPL",
                    trading_mode=TradingMode.PAPER,
                    strategy_version="v1",
                    quantity=10.0,
                    entry_price=3.00,
                    stop_price=2.97,
                    initial_stop_price=2.97,
                    opened_at=datetime(2026, 5, 1, tzinfo=timezone.utc),
                    highest_price=None,
                )
            ]

    supervisor = _make_supervisor(settings)
    supervisor.runtime.position_store = _PositionStoreNullHighest()

    positions = supervisor._load_open_positions()
    assert positions[0].highest_price == 3.00  # falls back to entry_price


def test_apply_highest_price_updates_skips_short_positions():
    """Short positions (qty < 0) must be passed through unchanged — highest_price is only for longs."""
    settings = _make_settings()
    pstore = _RecordingPositionStore()
    supervisor = _make_supervisor(settings, pstore)

    # bar.high=5.20 > highest_price=5.00 — without the guard this WOULD trigger an update,
    # writing a new highest_price to the DB for a short position.
    position = OpenPosition(
        symbol="AAPL",
        entry_timestamp=datetime(2026, 5, 1, 10, 0, tzinfo=timezone.utc),
        entry_price=5.00,
        quantity=-100.0,
        entry_level=5.03,
        initial_stop_price=5.03,
        stop_price=5.03,
        trailing_active=False,
        highest_price=5.00,
        strategy_name="bear_breakdown",
    )
    bars = {"AAPL": [_make_bar(high=5.20)]}

    result = supervisor._apply_highest_price_updates([position], bars)

    assert result[0].highest_price == 5.00, "Short position highest_price must not be updated"
    assert pstore.update_calls == [], "No DB write should occur for short positions"
