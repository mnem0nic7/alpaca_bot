from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from alpaca_bot.config import TradingMode
from alpaca_bot.runtime.cycle_intent_execution import _execute_exit
from alpaca_bot.storage import AuditEvent, PositionRecord
from tests.unit.test_cycle_intent_execution import (
    FakeConnection,
    RecordingAuditEventStore,
    RecordingOrderStore,
    RecordingPositionStore,
    make_settings,
)


def _short_put_position(now: datetime) -> PositionRecord:
    return PositionRecord(
        symbol="ALHC260618P00017500",
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        quantity=-1,
        entry_price=0.20,
        stop_price=0.0,
        initial_stop_price=0.0,
        opened_at=now,
        strategy_name="bear_orb",
    )


class _OptionBroker:
    def __init__(self):
        self.btc_calls: list[dict] = []
        self.cancel_calls: list[str] = []

    def submit_option_market_buy_to_close(self, **kwargs):
        self.btc_calls.append(kwargs)
        return SimpleNamespace(broker_order_id="fake-btc-1", status="accepted")

    def cancel_order(self, order_id: str) -> None:
        self.cancel_calls.append(order_id)

    def list_open_orders(self):
        return []


def _make_runtime(position: PositionRecord):
    return SimpleNamespace(
        order_store=RecordingOrderStore(orders=[]),
        position_store=RecordingPositionStore(positions=[position]),
        audit_event_store=RecordingAuditEventStore(),
        connection=FakeConnection(),
    )


# 20:00 UTC = 16:00 EDT — options market is closed
_AFTER_HOURS = datetime(2026, 5, 26, 20, 0, tzinfo=timezone.utc)
# 14:30 UTC = 10:30 EDT — options market is open
_MARKET_HOURS = datetime(2026, 5, 26, 14, 30, tzinfo=timezone.utc)


def test_execute_exit_returns_zero_when_options_market_closed():
    """After 4pm ET, _execute_exit returns (0,0,0) and fires cycle_intent_skipped."""
    settings = make_settings()
    position = _short_put_position(_AFTER_HOURS)
    runtime = _make_runtime(position)
    broker = _OptionBroker()

    result = _execute_exit(
        settings=settings,
        runtime=runtime,
        broker=broker,
        symbol="ALHC260618P00017500",
        intent_timestamp=_AFTER_HOURS,
        reason="stale_position_carryover",
        position=position,
        now=_AFTER_HOURS,
        strategy_name="bear_orb",
    )

    assert result == (0, 0, 0)
    assert broker.btc_calls == []
    skipped = [
        e for e in runtime.audit_event_store.appended
        if e.event_type == "cycle_intent_skipped"
    ]
    assert len(skipped) == 1
    assert skipped[0].payload["reason"] == "options_market_closed"
    assert skipped[0].symbol == "ALHC260618P00017500"


def test_execute_exit_calls_broker_during_market_hours():
    """During 09:30–16:00 ET, _execute_exit calls submit_option_market_buy_to_close."""
    settings = make_settings()
    position = _short_put_position(_MARKET_HOURS)
    runtime = _make_runtime(position)
    broker = _OptionBroker()

    _execute_exit(
        settings=settings,
        runtime=runtime,
        broker=broker,
        symbol="ALHC260618P00017500",
        intent_timestamp=_MARKET_HOURS,
        reason="stale_position_carryover",
        position=position,
        now=_MARKET_HOURS,
        strategy_name="bear_orb",
    )

    assert len(broker.btc_calls) == 1
    assert broker.btc_calls[0]["occ_symbol"] == "ALHC260618P00017500"
    assert broker.btc_calls[0]["quantity"] == 1
