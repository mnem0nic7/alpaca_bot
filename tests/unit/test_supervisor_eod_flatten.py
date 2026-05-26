from __future__ import annotations

from datetime import date, datetime, timezone
from importlib import import_module
from types import SimpleNamespace

from alpaca_bot.config import TradingMode
from alpaca_bot.storage.models import OptionOrderRecord


def _settings():
    from tests.unit.helpers import _base_env
    from alpaca_bot.config import Settings
    return Settings.from_env(_base_env())


def _short_put(timestamp: datetime) -> OptionOrderRecord:
    return OptionOrderRecord(
        client_order_id="option:v1-breakout:2026-05-26:ALHC260618P00017500:sell:2026-05-26T14:00:00+00:00",
        occ_symbol="ALHC260618P00017500",
        underlying_symbol="ALHC",
        option_type="put",
        strike=17.5,
        expiry=date(2026, 6, 18),
        side="sell",
        status="filled",
        quantity=1,
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        strategy_name="bear_orb",
        created_at=timestamp,
        updated_at=timestamp,
    )


def _long_call(timestamp: datetime) -> OptionOrderRecord:
    return OptionOrderRecord(
        client_order_id="option:v1-breakout:2026-05-26:AAPL240701C00100000:buy:2026-05-26T14:00:00+00:00",
        occ_symbol="AAPL240701C00100000",
        underlying_symbol="AAPL",
        option_type="call",
        strike=100.0,
        expiry=date(2026, 7, 1),
        side="buy",
        status="filled",
        quantity=1,
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        strategy_name="breakout_calls",
        created_at=timestamp,
        updated_at=timestamp,
    )


class _FakeOptionOrderStore:
    def __init__(self, filled_records):
        self._filled = filled_records
        self.saved: list[OptionOrderRecord] = []

    def list_open_option_positions(self, **kwargs):
        return self._filled

    def list_by_status(self, **kwargs):
        return []

    def save(self, record, *, commit=True):
        self.saved.append(record)


class _FakeConn:
    def commit(self): pass
    def rollback(self): pass


def _make_supervisor(option_order_store, option_broker=None):
    module = import_module("alpaca_bot.runtime.supervisor")
    RuntimeSupervisor = module.RuntimeSupervisor
    settings = _settings()

    class _FakeRuntime:
        connection = _FakeConn()
        store_lock = None
        order_store = SimpleNamespace(
            list_by_status=lambda **k: [],
            list_pending_submit=lambda **k: [],
            daily_realized_pnl=lambda **k: 0.0,
            daily_realized_pnl_by_symbol=lambda **k: {},
            list_trade_pnl_by_strategy=lambda **k: [],
        )
        position_store = SimpleNamespace(list_all=lambda **k: [], replace_all=lambda **k: None)
        trading_status_store = SimpleNamespace(load=lambda **k: None)
        daily_session_state_store = SimpleNamespace(
            load=lambda **k: None,
            save=lambda *a, **k: None,
            list_by_session=lambda **k: [],
        )
        strategy_flag_store = SimpleNamespace(list_all=lambda **k: [], load=lambda **k: None)
        watchlist_store = SimpleNamespace(
            list_enabled=lambda *a: ["AAPL"], list_ignored=lambda *a: []
        )
        audit_event_store = SimpleNamespace(
            append=lambda *a, **k: None,
            load_latest=lambda **k: None,
            list_recent=lambda **k: [],
            list_by_event_types=lambda **k: [],
        )

    _FakeRuntime.option_order_store = option_order_store

    _broker = option_broker or SimpleNamespace(
        submit_option_market_exit=lambda **k: SimpleNamespace(broker_order_id="fake-btc-1"),
    )
    return RuntimeSupervisor(
        settings=settings,
        runtime=_FakeRuntime(),
        broker=SimpleNamespace(
            get_account=lambda: SimpleNamespace(
                equity=100_000.0, buying_power=200_000.0, trading_blocked=False
            ),
            list_open_orders=lambda: [],
            get_clock=lambda: SimpleNamespace(is_open=True),
        ),
        market_data=SimpleNamespace(
            get_stock_bars=lambda **_: {}, get_daily_bars=lambda **_: {}
        ),
        stream=None,
        close_runtime_fn=lambda _: None,
        connection_checker=lambda _: True,
        cycle_runner=lambda **k: SimpleNamespace(intents=[]),
        cycle_intent_executor=lambda **k: SimpleNamespace(
            submitted_exit_count=0,
            failed_exit_count=0,
            replaced_stop_count=0,
            submitted_stop_count=0,
            canceled_stop_count=0,
        ),
        order_dispatcher=lambda **k: {"submitted_count": 0},
        option_broker=_broker,
    )


# 15:50 EDT on Monday 2026-05-26 = 19:50 UTC (EDT = UTC-4 in May)
_PAST_FLATTEN = datetime(2026, 5, 26, 19, 50, tzinfo=timezone.utc)


def test_eod_flatten_skips_side_sell_positions():
    """Short puts (side='sell') must NOT generate a new pending_submit on EOD flatten.
    They are closed the next morning via the stale-position carryover mechanism."""
    entry_ts = datetime(2026, 5, 26, 14, 0, tzinfo=timezone.utc)
    store = _FakeOptionOrderStore([_short_put(entry_ts)])

    supervisor = _make_supervisor(store)
    supervisor.run_cycle_once(now=lambda: _PAST_FLATTEN)

    pending_submit = [r for r in store.saved if r.status == "pending_submit"]
    assert pending_submit == [], (
        "EOD flatten must not create pending_submit records for side='sell' positions"
    )


def test_eod_flatten_creates_close_for_side_buy_positions():
    """Long options (side='buy') DO get a side='sell' pending_submit on EOD flatten."""
    entry_ts = datetime(2026, 5, 26, 14, 0, tzinfo=timezone.utc)
    store = _FakeOptionOrderStore([_long_call(entry_ts)])
    supervisor = _make_supervisor(store)

    supervisor.run_cycle_once(now=lambda: _PAST_FLATTEN)

    pending_submit = [r for r in store.saved if r.status == "pending_submit"]
    assert len(pending_submit) == 1
    assert pending_submit[0].side == "sell"
    assert pending_submit[0].occ_symbol == "AAPL240701C00100000"
