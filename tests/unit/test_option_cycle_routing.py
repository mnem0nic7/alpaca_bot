from __future__ import annotations

import pytest
from datetime import date, datetime, timezone

from alpaca_bot.config import TradingMode
from alpaca_bot.core.engine import CycleIntent, CycleIntentType, CycleResult
from alpaca_bot.domain.models import Bar, EntrySignal, OptionContract
from alpaca_bot.storage import AuditEvent, OrderRecord
from alpaca_bot.storage.models import OptionOrderRecord
from alpaca_bot.runtime.cycle import run_cycle
from tests.unit.helpers import _base_env, _make_settings


def _now() -> datetime:
    return datetime(2024, 6, 1, 14, 0, tzinfo=timezone.utc)


def _settings(**overrides):
    env = _base_env()
    env.update(overrides)
    return _make_settings(env)


class _FakeOrderStore:
    def __init__(self):
        self.saved: list[OrderRecord] = []

    def save(self, record, *, commit=True):
        self.saved.append(record)


class _FakeOptionOrderStore:
    def __init__(self):
        self.saved: list[OptionOrderRecord] = []

    def save(self, record, *, commit=True):
        self.saved.append(record)


class _FakeAuditStore:
    def __init__(self):
        self.events: list[AuditEvent] = []

    def append(self, event, *, commit=True):
        self.events.append(event)


class _FakeConn:
    def commit(self): pass
    def rollback(self): pass


class _FakeRuntime:
    def __init__(self):
        self.order_store = _FakeOrderStore()
        self.option_order_store = _FakeOptionOrderStore()
        self.audit_event_store = _FakeAuditStore()
        self.connection = _FakeConn()


def _bar() -> Bar:
    return Bar(symbol="AAPL", timestamp=_now(), open=100.0, high=101.0, low=99.0, close=100.0, volume=1000.0)


class TestRunCycleOptionRouting:
    def test_option_entry_intent_saves_option_order_record(self):
        s = _settings()
        runtime = _FakeRuntime()
        contract = OptionContract(
            occ_symbol="AAPL240701C00100000",
            underlying="AAPL",
            option_type="call",
            strike=100.0,
            expiry=date(2024, 7, 1),
            bid=2.50,
            ask=3.00,
            delta=0.50,
        )

        def fake_evaluator(settings=None, **kwargs):
            return CycleResult(
                as_of=_now(),
                intents=[
                    CycleIntent(
                        intent_type=CycleIntentType.ENTRY,
                        symbol="AAPL240701C00100000",
                        timestamp=_now(),
                        quantity=2,
                        limit_price=3.00,
                        client_order_id="option:v1:2024-06-01:AAPL240701C00100000:entry:2024-06-01T14:00:00+00:00",
                        strategy_name="breakout_calls",
                        is_option=True,
                        underlying_symbol="AAPL",
                        option_strike=100.0,
                        option_expiry=date(2024, 7, 1),
                        option_type_str="call",
                    )
                ],
            )

        run_cycle(
            settings=s,
            runtime=runtime,
            now=_now(),
            equity=100_000.0,
            intraday_bars_by_symbol={"AAPL": [_bar()]},
            daily_bars_by_symbol={"AAPL": [_bar()]},
            open_positions=[],
            working_order_symbols=set(),
            traded_symbols_today=set(),
            entries_disabled=False,
            strategy_name="breakout_calls",
            _evaluate_fn=fake_evaluator,
        )

        assert len(runtime.option_order_store.saved) == 1
        assert len(runtime.order_store.saved) == 0
        opt_rec = runtime.option_order_store.saved[0]
        assert opt_rec.occ_symbol == "AAPL240701C00100000"
        assert opt_rec.underlying_symbol == "AAPL"
        assert opt_rec.status == "pending_submit"
        assert opt_rec.side == "buy"
        assert opt_rec.quantity == 2
        assert opt_rec.limit_price == 3.00

    def test_equity_entry_intent_saves_order_record_not_option(self):
        s = _settings()
        runtime = _FakeRuntime()

        def fake_evaluator(settings=None, **kwargs):
            return CycleResult(
                as_of=_now(),
                intents=[
                    CycleIntent(
                        intent_type=CycleIntentType.ENTRY,
                        symbol="AAPL",
                        timestamp=_now(),
                        quantity=10,
                        limit_price=100.0,
                        initial_stop_price=98.0,
                        stop_price=98.0,
                        client_order_id="breakout:v1:2024-06-01:AAPL:entry:2024-06-01T14:00:00+00:00",
                        strategy_name="breakout",
                        is_option=False,
                    )
                ],
            )

        run_cycle(
            settings=s, runtime=runtime, now=_now(), equity=100_000.0,
            intraday_bars_by_symbol={"AAPL": [_bar()]},
            daily_bars_by_symbol={"AAPL": [_bar()]},
            open_positions=[], working_order_symbols=set(),
            traded_symbols_today=set(), entries_disabled=False,
            _evaluate_fn=fake_evaluator,
        )

        assert len(runtime.order_store.saved) == 1
        assert len(runtime.option_order_store.saved) == 0


class TestTradeStreamOptionRouting:
    def test_option_prefix_fill_routes_to_option_store(self):
        from alpaca_bot.runtime.trade_updates import apply_trade_update

        updated_option_records: list[dict] = []

        class FakeOptionOrderStore:
            def load_by_broker_order_id(self, broker_order_id):
                return None

            def update_fill(self, **kwargs):
                updated_option_records.append(kwargs)

        class FakeOrderStore:
            def load(self, client_order_id):
                return None

            def load_by_broker_order_id(self, broker_order_id):
                return None

            def save(self, record, *, commit=True):
                pass

        class FakePositionStore:
            def save(self, record, *, commit=True):
                pass

            def delete(self, **kwargs):
                pass

        class FakeAuditStore:
            def append(self, event, *, commit=True):
                pass

        class FakeConn:
            def commit(self): pass
            def rollback(self): pass

        class FakeRuntime:
            order_store = FakeOrderStore()
            option_order_store = FakeOptionOrderStore()
            position_store = FakePositionStore()
            audit_event_store = FakeAuditStore()
            connection = FakeConn()

        fake_update = {
            "event": "fill",
            "client_order_id": "option:v1:2024-06-01:AAPL240701C00100000:entry:2024-06-01T14:00:00+00:00",
            "broker_order_id": "broker-123",
            "symbol": "AAPL240701C00100000",
            "side": "buy",
            "status": "filled",
            "qty": 2,
            "filled_qty": 2,
            "filled_avg_price": 3.10,
            "timestamp": "2024-06-01T14:05:00+00:00",
        }

        s = _make_settings(_base_env())
        apply_trade_update(
            settings=s,
            runtime=FakeRuntime(),
            update=fake_update,
            now=_now(),
        )
        assert len(updated_option_records) == 1
        assert updated_option_records[0]["fill_price"] == 3.10
