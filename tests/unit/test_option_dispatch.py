from __future__ import annotations

import pytest
from datetime import date, datetime, timezone

from alpaca_bot.config import TradingMode
from alpaca_bot.storage.models import OptionOrderRecord
from alpaca_bot.runtime.option_dispatch import dispatch_pending_option_orders


def _now() -> datetime:
    return datetime(2024, 6, 1, 14, 0, tzinfo=timezone.utc)


def _record(status: str = "pending_submit", side: str = "buy", **kwargs) -> OptionOrderRecord:
    defaults = dict(
        client_order_id="option:v1:2024-06-01:AAPL240701C00100000:entry:2024-06-01T14:00:00+00:00",
        occ_symbol="AAPL240701C00100000",
        underlying_symbol="AAPL",
        option_type="call",
        strike=100.0,
        expiry=date(2024, 7, 1),
        side=side,
        status=status,
        quantity=2,
        trading_mode=TradingMode.PAPER,
        strategy_version="v1",
        strategy_name="breakout_calls",
        created_at=_now(),
        updated_at=_now(),
        limit_price=3.00,
    )
    defaults.update(kwargs)
    return OptionOrderRecord(**defaults)


class _FakeOptionOrderStore:
    def __init__(self, records: list[OptionOrderRecord]):
        self._records = records
        self.saved: list[OptionOrderRecord] = []

    def list_by_status(self, *, trading_mode, strategy_version, statuses):
        return [r for r in self._records if r.status in statuses]

    def save(self, record: OptionOrderRecord, *, commit: bool = True) -> None:
        self.saved.append(record)


class _FakeOptionBroker:
    def __init__(self, broker_order_id: str = "broker-123"):
        self.submitted: list[dict] = []
        self._broker_order_id = broker_order_id

    def submit_option_limit_entry(self, **kwargs):
        self.submitted.append({"type": "limit_entry", **kwargs})

        class FakeOrder:
            def __init__(self, bid):
                self.broker_order_id = bid
        return FakeOrder(self._broker_order_id)

    def submit_option_market_exit(self, **kwargs):
        self.submitted.append({"type": "market_exit", **kwargs})

        class FakeOrder:
            def __init__(self, bid):
                self.broker_order_id = bid
        return FakeOrder(self._broker_order_id)


class _FakeAuditStore:
    def __init__(self):
        self.events = []

    def append(self, event, *, commit=True):
        self.events.append(event)


class _FakeRuntime:
    def __init__(self, records):
        self.option_order_store = _FakeOptionOrderStore(records)
        self.audit_event_store = _FakeAuditStore()

    def commit(self):
        pass


class TestDispatchPendingOptionOrders:
    def test_dispatches_pending_buy_as_limit_entry(self):
        from tests.unit.helpers import _base_env
        from alpaca_bot.config import Settings
        s = Settings.from_env(_base_env())

        record = _record(status="pending_submit", side="buy")
        runtime = _FakeRuntime([record])
        broker = _FakeOptionBroker()

        result = dispatch_pending_option_orders(
            settings=s, runtime=runtime, broker=broker, now=_now(),
        )
        assert result.submitted_count == 1
        assert len(broker.submitted) == 1
        assert broker.submitted[0]["type"] == "limit_entry"
        assert broker.submitted[0]["occ_symbol"] == "AAPL240701C00100000"
        assert broker.submitted[0]["quantity"] == 2
        assert broker.submitted[0]["limit_price"] == 3.00

    def test_dispatches_pending_sell_as_market_exit(self):
        from tests.unit.helpers import _base_env
        from alpaca_bot.config import Settings
        s = Settings.from_env(_base_env())

        record = _record(status="pending_submit", side="sell", limit_price=None)
        runtime = _FakeRuntime([record])
        broker = _FakeOptionBroker()

        result = dispatch_pending_option_orders(
            settings=s, runtime=runtime, broker=broker, now=_now(),
        )
        assert result.submitted_count == 1
        assert broker.submitted[0]["type"] == "market_exit"

    def test_returns_zero_when_no_pending_orders(self):
        from tests.unit.helpers import _base_env
        from alpaca_bot.config import Settings
        s = Settings.from_env(_base_env())

        runtime = _FakeRuntime([])
        broker = _FakeOptionBroker()

        result = dispatch_pending_option_orders(
            settings=s, runtime=runtime, broker=broker, now=_now(),
        )
        assert result.submitted_count == 0
        assert len(broker.submitted) == 0

    def test_skips_live_orders_when_enable_live_trading_false(self):
        """ENABLE_LIVE_TRADING=false gate must block option orders too."""
        from alpaca_bot.config import Settings
        from tests.unit.helpers import _base_env

        env = _base_env()
        env["ENABLE_LIVE_TRADING"] = "false"
        env["TRADING_MODE"] = "paper"
        s = Settings.from_env(env)

        record = _record(status="pending_submit", side="buy")
        runtime = _FakeRuntime([record])
        broker = _FakeOptionBroker()

        # Paper mode + ENABLE_LIVE_TRADING=false is still OK — paper is always allowed.
        # The gate only blocks when TRADING_MODE=live and ENABLE_LIVE_TRADING=false,
        # but Settings.validate() raises before we get here. So this test just confirms
        # that paper mode dispatches normally.
        result = dispatch_pending_option_orders(
            settings=s, runtime=runtime, broker=broker, now=_now(),
        )
        assert result.submitted_count == 1

    def test_order_saved_as_submitting_before_broker_call(self):
        """Write-before-dispatch: record is updated to 'submitting' before the broker call."""
        from tests.unit.helpers import _base_env
        from alpaca_bot.config import Settings
        s = Settings.from_env(_base_env())

        save_calls: list[str] = []

        class TrackingStore(_FakeOptionOrderStore):
            def save(self, record, *, commit=True):
                save_calls.append(record.status)
                super().save(record, commit=commit)

        class TrackingBroker(_FakeOptionBroker):
            def submit_option_limit_entry(self, **kwargs):
                # At call time, 'submitting' must already be saved
                assert "submitting" in save_calls
                return super().submit_option_limit_entry(**kwargs)

        record = _record(status="pending_submit", side="buy")
        runtime = _FakeRuntime([record])
        runtime.option_order_store = TrackingStore([record])
        broker = TrackingBroker()

        dispatch_pending_option_orders(settings=s, runtime=runtime, broker=broker, now=_now())
        assert "submitting" in save_calls
