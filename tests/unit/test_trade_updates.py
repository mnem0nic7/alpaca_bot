from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from alpaca_bot.config import Settings, TradingMode
from alpaca_bot.storage import AuditEvent, OrderRecord, PositionRecord


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_settings(**overrides: str) -> Settings:
    values = {
        "TRADING_MODE": "paper",
        "ENABLE_LIVE_TRADING": "false",
        "STRATEGY_VERSION": "v1-breakout",
        "DATABASE_URL": "postgresql://alpaca_bot:secret@db.example.com:5432/alpaca_bot",
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


NOW = datetime(2026, 4, 25, 14, 30, tzinfo=timezone.utc)


def _make_entry_order(
    *,
    client_order_id: str = "v1-breakout:2026-04-25:AAPL:entry:2026-04-25T14:00:00+00:00",
    symbol: str = "AAPL",
    initial_stop_price: float | None = 109.50,
    quantity: int = 10,
) -> OrderRecord:
    return OrderRecord(
        client_order_id=client_order_id,
        symbol=symbol,
        side="buy",
        intent_type="entry",
        status="new",
        quantity=quantity,
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        created_at=NOW,
        updated_at=NOW,
        initial_stop_price=initial_stop_price,
        signal_timestamp=NOW,
    )


def _expected_stop_order_id(entry_id: str) -> str:
    """Mirror the production helper."""
    if ":entry:" in entry_id:
        return entry_id.replace(":entry:", ":stop:", 1)
    return f"{entry_id}:stop"


class RecordingOrderStore:
    """In-memory order store that records all saves.

    Supports ``load`` (by client_order_id) and ``load_by_broker_order_id``.
    Pre-populate ``orders`` to control what ``load`` returns.
    """

    def __init__(self, orders: list[OrderRecord] | None = None) -> None:
        self._orders: dict[str, OrderRecord] = {o.client_order_id: o for o in (orders or [])}
        self.saved: list[OrderRecord] = []

    def load(self, client_order_id: str) -> OrderRecord | None:
        return self._orders.get(client_order_id)

    def load_by_broker_order_id(self, broker_order_id: str) -> OrderRecord | None:
        for o in self._orders.values():
            if o.broker_order_id == broker_order_id:
                return o
        return None

    def save(self, order: OrderRecord, *, commit: bool = True) -> None:
        self._orders[order.client_order_id] = order
        self.saved.append(order)


class RecordingPositionStore:
    """In-memory position store that records saves and deletes."""

    def __init__(self) -> None:
        self.saved: list[PositionRecord] = []
        self.deleted: list[dict] = []

    def save(self, position: PositionRecord, *, commit: bool = True) -> None:
        self.saved.append(position)

    def delete(self, *, symbol: str, trading_mode, strategy_version: str, strategy_name: str = "breakout", commit: bool = True) -> None:
        self.deleted.append(
            {"symbol": symbol, "trading_mode": trading_mode, "strategy_version": strategy_version, "strategy_name": strategy_name}
        )


class RecordingAuditEventStore:
    def __init__(self) -> None:
        self.appended: list[AuditEvent] = []

    def append(self, event: AuditEvent, *, commit: bool = True) -> None:
        self.appended.append(event)


def _make_runtime(
    *,
    orders: list[OrderRecord] | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        order_store=RecordingOrderStore(orders=orders),
        position_store=RecordingPositionStore(),
        audit_event_store=RecordingAuditEventStore(),
        connection=SimpleNamespace(commit=lambda: None),
    )


def _make_trade_update(
    *,
    client_order_id: str = "v1-breakout:2026-04-25:AAPL:entry:2026-04-25T14:00:00+00:00",
    broker_order_id: str = "broker-entry-1",
    symbol: str = "AAPL",
    side: str = "buy",
    status: str = "filled",
    qty: int = 10,
    filled_qty: int = 10,
    filled_avg_price: float = 112.00,
) -> dict:
    return {
        "event": status,
        "client_order_id": client_order_id,
        "broker_order_id": broker_order_id,
        "symbol": symbol,
        "side": side,
        "status": status,
        "qty": qty,
        "filled_qty": filled_qty,
        "filled_avg_price": filled_avg_price,
        "timestamp": NOW.isoformat(),
    }


def _apply(runtime, update_dict, *, settings=None):
    from alpaca_bot.runtime.trade_updates import apply_trade_update

    return apply_trade_update(
        settings=settings or make_settings(),
        runtime=runtime,
        update=update_dict,
        now=NOW,
    )


# ---------------------------------------------------------------------------
# Bug A — Partial fill must queue protective stop
# ---------------------------------------------------------------------------

class TestProtectiveStopOnPartialFill:
    def test_apply_trade_update_queues_protective_stop_on_partial_fill(self):
        """A partial fill of an entry order must queue a pending stop order immediately."""
        entry_order = _make_entry_order()
        runtime = _make_runtime(orders=[entry_order])

        update = _make_trade_update(
            status="partially_filled",
            qty=10,
            filled_qty=5,
            filled_avg_price=112.00,
        )
        result = _apply(runtime, update)

        assert result["position_updated"] is True
        assert result["protective_stop_queued"] is True

        stop_id = _expected_stop_order_id(entry_order.client_order_id)
        assert result["protective_stop_client_order_id"] == stop_id

        stop_orders = [o for o in runtime.order_store.saved if o.intent_type == "stop"]
        assert len(stop_orders) == 1
        stop = stop_orders[0]
        assert stop.client_order_id == stop_id
        assert stop.status == "pending_submit"
        assert stop.symbol == "AAPL"
        assert stop.side == "sell"
        assert stop.stop_price == entry_order.initial_stop_price
        assert stop.quantity == 5  # reflects partial filled_qty

    def test_apply_trade_update_queues_protective_stop_on_full_fill(self):
        """A full fill of an entry order must also queue a pending stop order."""
        entry_order = _make_entry_order()
        runtime = _make_runtime(orders=[entry_order])

        update = _make_trade_update(
            status="filled",
            qty=10,
            filled_qty=10,
            filled_avg_price=112.00,
        )
        result = _apply(runtime, update)

        assert result["position_updated"] is True
        assert result["protective_stop_queued"] is True

        stop_id = _expected_stop_order_id(entry_order.client_order_id)
        stop_orders = [o for o in runtime.order_store.saved if o.intent_type == "stop"]
        assert len(stop_orders) == 1
        stop = stop_orders[0]
        assert stop.client_order_id == stop_id
        assert stop.status == "pending_submit"
        assert stop.quantity == 10

    def test_apply_trade_update_does_not_duplicate_stop_if_already_pending(self):
        """If a pending stop already exists for this entry, do not queue a second one."""
        entry_order = _make_entry_order()
        stop_id = _expected_stop_order_id(entry_order.client_order_id)

        existing_stop = OrderRecord(
            client_order_id=stop_id,
            symbol="AAPL",
            side="sell",
            intent_type="stop",
            status="pending_submit",
            quantity=5,
            trading_mode=TradingMode.PAPER,
            strategy_version="v1-breakout",
            created_at=NOW,
            updated_at=NOW,
            stop_price=entry_order.initial_stop_price,
            initial_stop_price=entry_order.initial_stop_price,
            signal_timestamp=NOW,
        )

        runtime = _make_runtime(orders=[entry_order, existing_stop])

        # Second partial fill (e.g. more shares fill)
        update = _make_trade_update(
            status="partially_filled",
            qty=10,
            filled_qty=8,
            filled_avg_price=112.10,
        )
        result = _apply(runtime, update)

        assert result["position_updated"] is True
        # No new stop should have been queued
        assert result["protective_stop_queued"] is False
        assert result["protective_stop_client_order_id"] is None

        new_stop_saves = [
            o for o in runtime.order_store.saved if o.intent_type == "stop"
        ]
        assert new_stop_saves == [], (
            "No new stop order should be saved when one is already pending"
        )


# ---------------------------------------------------------------------------
# Bug B — Cancellation / expiration cleanup
# ---------------------------------------------------------------------------

class TestCancellationCleanup:
    def test_apply_trade_update_clears_position_on_cancellation(self):
        """Cancelling a partially-filled entry order must delete the PositionRecord."""
        entry_order = _make_entry_order()
        runtime = _make_runtime(orders=[entry_order])

        update = _make_trade_update(
            status="cancelled",
            qty=10,
            filled_qty=0,
            filled_avg_price=0.0,
        )
        # Override filled_avg_price to None to simulate no fill info
        update["filled_avg_price"] = None
        result = _apply(runtime, update)

        assert result["position_cleared"] is True
        assert len(runtime.position_store.deleted) == 1
        deleted = runtime.position_store.deleted[0]
        assert deleted["symbol"] == "AAPL"
        assert deleted["trading_mode"] == TradingMode.PAPER
        assert deleted["strategy_version"] == "v1-breakout"

    def test_apply_trade_update_clears_position_on_expiration(self):
        """An expired entry order must delete the PositionRecord (same as cancellation)."""
        entry_order = _make_entry_order()
        runtime = _make_runtime(orders=[entry_order])

        update = _make_trade_update(
            status="expired",
            qty=10,
            filled_qty=0,
            filled_avg_price=0.0,
        )
        update["filled_avg_price"] = None
        result = _apply(runtime, update)

        assert result["position_cleared"] is True
        assert len(runtime.position_store.deleted) == 1
        deleted = runtime.position_store.deleted[0]
        assert deleted["symbol"] == "AAPL"

    def test_apply_trade_update_clears_phantom_stop_on_cancellation(self):
        """When an entry order is cancelled, any pending stop for that entry must be cancelled too."""
        entry_order = _make_entry_order()
        stop_id = _expected_stop_order_id(entry_order.client_order_id)

        existing_stop = OrderRecord(
            client_order_id=stop_id,
            symbol="AAPL",
            side="sell",
            intent_type="stop",
            status="pending_submit",
            quantity=5,
            trading_mode=TradingMode.PAPER,
            strategy_version="v1-breakout",
            created_at=NOW,
            updated_at=NOW,
            stop_price=entry_order.initial_stop_price,
            initial_stop_price=entry_order.initial_stop_price,
            signal_timestamp=NOW,
        )

        runtime = _make_runtime(orders=[entry_order, existing_stop])

        update = _make_trade_update(
            status="cancelled",
            qty=10,
            filled_qty=0,
            filled_avg_price=0.0,
        )
        update["filled_avg_price"] = None
        result = _apply(runtime, update)

        assert result["position_cleared"] is True

        # The pending stop should be saved with status "cancelled"
        stop_saves = [o for o in runtime.order_store.saved if o.client_order_id == stop_id]
        assert len(stop_saves) >= 1
        last_stop_save = stop_saves[-1]
        assert last_stop_save.status == "cancelled", (
            f"Expected stop status 'cancelled', got {last_stop_save.status!r}"
        )

    def test_apply_trade_update_cancellation_is_noop_when_no_position(self):
        """Cancellation of an entry with no position/stop should not error — delete is idempotent."""
        entry_order = _make_entry_order()
        runtime = _make_runtime(orders=[entry_order])

        update = _make_trade_update(
            status="cancelled",
            qty=10,
            filled_qty=0,
            filled_avg_price=0.0,
        )
        update["filled_avg_price"] = None

        # Should not raise
        result = _apply(runtime, update)

        assert result["position_cleared"] is True
        # delete was still called (idempotent)
        assert len(runtime.position_store.deleted) == 1
        # No phantom stop to cancel — no stop order saves beyond the entry update
        stop_saves = [o for o in runtime.order_store.saved if o.intent_type == "stop"]
        assert stop_saves == []


# ---------------------------------------------------------------------------
# Test 1 — filled_qty=0 (falsy but not None) is preserved, not replaced by order qty
# ---------------------------------------------------------------------------

class TestFilledQtyZeroNotFalsy:
    def test_filled_qty_zero_uses_zero_not_order_quantity(self):
        """filled_qty=0 is a valid explicit value; the is-not-None guard must not fall back
        to the order's quantity (10) just because 0 is falsy.

        This is a regression guard for the old `normalized.filled_qty or matched_order.quantity`
        bug: `0 or 10` would silently return 10.  The fixed form
        `normalized.filled_qty if normalized.filled_qty is not None else matched_order.quantity`
        correctly preserves the explicit 0.
        """
        entry_order = _make_entry_order(quantity=10)
        runtime = _make_runtime(orders=[entry_order])

        update = _make_trade_update(
            status="filled",
            qty=10,
            filled_qty=0,           # explicitly zero — falsy but not None
            filled_avg_price=112.00,
        )
        _apply(runtime, update)

        assert len(runtime.position_store.saved) == 1
        saved_position = runtime.position_store.saved[0]
        assert saved_position.quantity == 0, (
            "filled_qty=0 must be preserved as-is, not replaced with order quantity 10"
        )

        stop_orders = [o for o in runtime.order_store.saved if o.intent_type == "stop"]
        assert len(stop_orders) == 1
        assert stop_orders[0].quantity == 0, (
            "protective stop quantity must also be 0 (the explicit filled_qty), not the order quantity 10"
        )


# ---------------------------------------------------------------------------
# Test 2 — position_store.save() raising propagates (no silent swallow)
# ---------------------------------------------------------------------------

class TestPositionStoreSaveRaises:
    def test_position_store_save_raising_propagates(self):
        """When position_store.save() raises during entry fill processing, the exception
        must propagate rather than being swallowed.  This documents the known gap:
        no recovery or compensating transaction exists yet.
        """

        class RaisingPositionStore:
            def save(self, position, *, commit: bool = True):
                raise RuntimeError("db write failed")

            def delete(self, *, symbol, trading_mode, strategy_version, strategy_name="breakout", commit: bool = True):
                pass

        entry_order = _make_entry_order(quantity=10)
        from types import SimpleNamespace
        runtime = SimpleNamespace(
            order_store=RecordingOrderStore(orders=[entry_order]),
            position_store=RaisingPositionStore(),
            audit_event_store=RecordingAuditEventStore(),
            connection=SimpleNamespace(commit=lambda: None),
        )

        update = _make_trade_update(
            status="filled",
            qty=10,
            filled_qty=10,
            filled_avg_price=112.00,
        )

        with pytest.raises(RuntimeError, match="db write failed"):
            _apply(runtime, update)


# ---------------------------------------------------------------------------
# Test 3 — both IDs None → falls through to unmatched-update audit event
# ---------------------------------------------------------------------------

class TestBothOrderIdsNone:
    def test_both_ids_none_emits_unmatched_audit_event(self):
        """When client_order_id and broker_order_id are both None, _find_order returns
        None and apply_trade_update must emit an 'trade_update_unmatched' audit event
        without saving any position.
        """
        entry_order = _make_entry_order()
        runtime = _make_runtime(orders=[entry_order])

        update = _make_trade_update(
            status="filled",
            qty=10,
            filled_qty=10,
            filled_avg_price=112.00,
        )
        # Strip both IDs so _find_order has nothing to match on
        update["client_order_id"] = None
        update["broker_order_id"] = None

        result = _apply(runtime, update)

        assert result["unmatched"] is True
        assert result["matched_order_id"] is None
        assert result["position_updated"] is False

        # An unmatched audit event must have been appended
        unmatched_events = [
            e for e in runtime.audit_event_store.appended
            if e.event_type == "trade_update_unmatched"
        ]
        assert len(unmatched_events) == 1, (
            "Expected exactly one 'trade_update_unmatched' audit event"
        )

        # No position must have been saved
        assert runtime.position_store.saved == []


# ---------------------------------------------------------------------------
# Test 4 — position_store.delete() raising on cancellation propagates
# ---------------------------------------------------------------------------

class TestPositionStoreDeleteRaises:
    def test_position_store_delete_raising_on_cancellation_propagates(self):
        """When position_store.delete() raises during entry-order cancellation cleanup,
        the exception must propagate.  This documents the known gap: no recovery logic
        wraps the delete call, so an error leaves the system in a partially-updated state.
        """

        class RaisingDeletePositionStore:
            def save(self, position, *, commit: bool = True):
                pass

            def delete(self, *, symbol, trading_mode, strategy_version, strategy_name="breakout", commit: bool = True):
                raise RuntimeError("db delete failed")

        entry_order = _make_entry_order(quantity=10)
        from types import SimpleNamespace
        runtime = SimpleNamespace(
            order_store=RecordingOrderStore(orders=[entry_order]),
            position_store=RaisingDeletePositionStore(),
            audit_event_store=RecordingAuditEventStore(),
            connection=SimpleNamespace(commit=lambda: None),
        )

        update = _make_trade_update(
            status="cancelled",
            qty=10,
            filled_qty=0,
            filled_avg_price=None,
        )
        update["filled_avg_price"] = None

        with pytest.raises(RuntimeError, match="db delete failed"):
            _apply(runtime, update)


# ---------------------------------------------------------------------------
# Phase 1 — Fill price persistence
# ---------------------------------------------------------------------------

class TestFillPricePersistence:
    def test_fill_price_and_filled_quantity_persisted_on_filled_event(self):
        """On a 'filled' event, fill_price and filled_quantity must be written to the order."""
        entry_order = _make_entry_order(quantity=10)
        runtime = _make_runtime(orders=[entry_order])

        update = _make_trade_update(
            status="filled",
            qty=10,
            filled_qty=10,
            filled_avg_price=155.50,
        )
        _apply(runtime, update)

        saved_entry = next(
            o for o in runtime.order_store.saved if o.intent_type == "entry"
        )
        assert saved_entry.fill_price == 155.50
        assert saved_entry.filled_quantity == 10

    def test_fill_price_and_filled_quantity_persisted_on_partially_filled_event(self):
        """On a 'partially_filled' event, fill_price and filled_quantity must be written."""
        entry_order = _make_entry_order(quantity=10)
        runtime = _make_runtime(orders=[entry_order])

        update = _make_trade_update(
            status="partially_filled",
            qty=10,
            filled_qty=5,
            filled_avg_price=155.00,
        )
        _apply(runtime, update)

        saved_entry = next(
            o for o in runtime.order_store.saved if o.intent_type == "entry"
        )
        assert saved_entry.fill_price == 155.00
        assert saved_entry.filled_quantity == 5

    def test_fill_price_preserved_on_subsequent_cancel(self):
        """A cancel event after a fill must NOT overwrite fill_price with None."""
        already_filled = OrderRecord(
            client_order_id="v1-breakout:2026-04-25:AAPL:entry:2026-04-25T14:00:00+00:00",
            symbol="AAPL",
            side="buy",
            intent_type="entry",
            status="filled",
            quantity=10,
            trading_mode=TradingMode.PAPER,
            strategy_version="v1-breakout",
            created_at=NOW,
            updated_at=NOW,
            initial_stop_price=109.50,
            signal_timestamp=NOW,
            fill_price=155.50,
            filled_quantity=10,
        )
        runtime = _make_runtime(orders=[already_filled])

        update = _make_trade_update(
            status="cancelled",
            qty=10,
            filled_qty=0,
            filled_avg_price=None,
        )
        update["filled_avg_price"] = None
        _apply(runtime, update)

        saved_entry = next(
            o for o in runtime.order_store.saved if o.intent_type == "entry"
        )
        assert saved_entry.fill_price == 155.50, "fill_price must be preserved on cancel"
        assert saved_entry.filled_quantity == 10, "filled_quantity must be preserved on cancel"

    def test_fill_price_appears_in_audit_payload_on_filled_event(self):
        """fill_price and filled_quantity must be included in the trade_update_applied audit payload."""
        entry_order = _make_entry_order(quantity=10)
        runtime = _make_runtime(orders=[entry_order])

        update = _make_trade_update(
            status="filled",
            qty=10,
            filled_qty=8,
            filled_avg_price=160.25,
        )
        _apply(runtime, update)

        applied_events = [
            e for e in runtime.audit_event_store.appended
            if e.event_type == "trade_update_applied"
        ]
        assert len(applied_events) == 1
        payload = applied_events[0].payload
        assert payload["fill_price"] == 160.25
        assert payload["filled_quantity"] == 8

    def test_fill_price_absent_from_audit_payload_when_not_a_fill_event(self):
        """fill_price must NOT appear in the audit payload for non-fill events (e.g. new)."""
        entry_order = _make_entry_order(quantity=10)
        runtime = _make_runtime(orders=[entry_order])

        update = _make_trade_update(
            status="new",
            qty=10,
            filled_qty=None,
            filled_avg_price=None,
        )
        update["filled_avg_price"] = None
        update["filled_qty"] = None
        _apply(runtime, update)

        applied_events = [
            e for e in runtime.audit_event_store.appended
            if e.event_type == "trade_update_applied"
        ]
        assert len(applied_events) == 1
        payload = applied_events[0].payload
        assert "fill_price" not in payload
        assert "filled_quantity" not in payload

    def test_strategy_name_propagated_to_position_on_fill(self):
        """PositionRecord must inherit strategy_name from the matched order."""
        entry_order = OrderRecord(
            client_order_id="v1-breakout:2026-04-25:AAPL:entry:2026-04-25T14:00:00+00:00",
            symbol="AAPL",
            side="buy",
            intent_type="entry",
            status="new",
            quantity=10,
            trading_mode=TradingMode.PAPER,
            strategy_version="v1-breakout",
            strategy_name="momentum",
            created_at=NOW,
            updated_at=NOW,
            initial_stop_price=109.50,
            signal_timestamp=NOW,
        )
        runtime = _make_runtime(orders=[entry_order])
        update = _make_trade_update(status="filled", qty=10, filled_qty=10, filled_avg_price=112.00)
        _apply(runtime, update)

        saved_positions = [r for r in runtime.position_store.saved if isinstance(r, PositionRecord)]
        assert len(saved_positions) == 1
        assert saved_positions[0].strategy_name == "momentum"

    def test_strategy_name_propagated_to_protective_stop_on_fill(self):
        """Protective stop OrderRecord must inherit strategy_name from the entry order."""
        entry_order = OrderRecord(
            client_order_id="v1-breakout:2026-04-25:AAPL:entry:2026-04-25T14:00:00+00:00",
            symbol="AAPL",
            side="buy",
            intent_type="entry",
            status="new",
            quantity=10,
            trading_mode=TradingMode.PAPER,
            strategy_version="v1-breakout",
            strategy_name="orb",
            created_at=NOW,
            updated_at=NOW,
            initial_stop_price=109.50,
            signal_timestamp=NOW,
        )
        runtime = _make_runtime(orders=[entry_order])
        update = _make_trade_update(status="filled", qty=10, filled_qty=10, filled_avg_price=112.00)
        _apply(runtime, update)

        stop_orders = [o for o in runtime.order_store.saved if o.intent_type == "stop"]
        assert len(stop_orders) == 1
        assert stop_orders[0].strategy_name == "orb"


class RecordingNotifier:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []
        self.raise_on_send: Exception | None = None

    def send(self, subject: str, body: str) -> None:
        if self.raise_on_send is not None:
            raise self.raise_on_send
        self.sent.append((subject, body))


def _apply_with_notifier(runtime, update_dict, notifier, *, settings=None):
    from alpaca_bot.runtime.trade_updates import apply_trade_update

    return apply_trade_update(
        settings=settings or make_settings(),
        runtime=runtime,
        update=update_dict,
        now=NOW,
        notifier=notifier,
    )


# ---------------------------------------------------------------------------
# Notifier tests
# ---------------------------------------------------------------------------

class TestNotifier:
    def test_entry_fill_sends_notification_with_symbol_qty_price(self):
        """An entry fill must send one notification with the symbol, qty, and fill price."""
        entry_order = _make_entry_order(quantity=10)
        runtime = _make_runtime(orders=[entry_order])
        notifier = RecordingNotifier()

        update = _make_trade_update(status="filled", qty=10, filled_qty=10, filled_avg_price=112.00)
        _apply_with_notifier(runtime, update, notifier)

        assert len(notifier.sent) == 1
        subject, body = notifier.sent[0]
        assert "AAPL" in subject
        assert "10" in subject
        assert "112.0" in subject
        assert "AAPL" in body
        assert "10" in body
        assert "112.0" in body

    def test_entry_fill_includes_slippage_alert_when_fill_exceeds_limit(self):
        """When the fill price exceeds the limit price by more than the threshold,
        the notification body must include the slippage warning."""
        entry_order = OrderRecord(
            client_order_id="v1-breakout:2026-04-25:AAPL:entry:2026-04-25T14:00:00+00:00",
            symbol="AAPL",
            side="buy",
            intent_type="entry",
            status="new",
            quantity=10,
            trading_mode=TradingMode.PAPER,
            strategy_version="v1-breakout",
            created_at=NOW,
            updated_at=NOW,
            initial_stop_price=109.50,
            limit_price=110.00,   # fill at 111.00 → 0.9% adverse slippage > 0.5% threshold
            signal_timestamp=NOW,
        )
        runtime = _make_runtime(orders=[entry_order])
        notifier = RecordingNotifier()

        update = _make_trade_update(
            status="filled", qty=10, filled_qty=10, filled_avg_price=111.00
        )
        _apply_with_notifier(runtime, update, notifier)

        assert len(notifier.sent) == 1
        _, body = notifier.sent[0]
        assert "slippage" in body.lower() or "\u26a0" in body

    def test_entry_fill_no_slippage_alert_within_threshold(self):
        """When the fill is within the threshold, no slippage warning appears in the body."""
        entry_order = OrderRecord(
            client_order_id="v1-breakout:2026-04-25:AAPL:entry:2026-04-25T14:00:00+00:00",
            symbol="AAPL",
            side="buy",
            intent_type="entry",
            status="new",
            quantity=10,
            trading_mode=TradingMode.PAPER,
            strategy_version="v1-breakout",
            created_at=NOW,
            updated_at=NOW,
            initial_stop_price=109.50,
            limit_price=112.00,   # fill at 112.10 → 0.089% < 0.5% threshold
            signal_timestamp=NOW,
        )
        runtime = _make_runtime(orders=[entry_order])
        notifier = RecordingNotifier()

        update = _make_trade_update(
            status="filled", qty=10, filled_qty=10, filled_avg_price=112.10
        )
        _apply_with_notifier(runtime, update, notifier)

        assert len(notifier.sent) == 1
        _, body = notifier.sent[0]
        assert "\u26a0" not in body

    def test_exit_fill_sends_position_closed_notification(self):
        """A stop or exit fill must send a 'Position closed' notification."""
        stop_order = OrderRecord(
            client_order_id="v1-breakout:2026-04-25:AAPL:stop:2026-04-25T14:00:00+00:00",
            symbol="AAPL",
            side="sell",
            intent_type="stop",
            status="new",
            quantity=10,
            trading_mode=TradingMode.PAPER,
            strategy_version="v1-breakout",
            strategy_name="breakout",
            created_at=NOW,
            updated_at=NOW,
            stop_price=109.50,
            initial_stop_price=109.50,
            signal_timestamp=NOW,
        )
        runtime = _make_runtime(orders=[stop_order])
        notifier = RecordingNotifier()

        update = _make_trade_update(
            client_order_id=stop_order.client_order_id,
            symbol="AAPL",
            side="sell",
            status="filled",
            qty=10,
            filled_qty=10,
            filled_avg_price=109.20,
        )
        _apply_with_notifier(runtime, update, notifier)

        assert len(notifier.sent) == 1
        subject, body = notifier.sent[0]
        assert "AAPL" in subject
        assert "closed" in subject.lower() or "STOP" in body
        assert "109.2" in body

    def test_exit_fill_with_none_price_does_not_raise(self):
        """An exit fill where filled_avg_price is None must send a notification
        with '?' as the price rather than raising."""
        stop_order = OrderRecord(
            client_order_id="v1-breakout:2026-04-25:AAPL:stop:2026-04-25T14:00:00+00:00",
            symbol="AAPL",
            side="sell",
            intent_type="stop",
            status="new",
            quantity=10,
            trading_mode=TradingMode.PAPER,
            strategy_version="v1-breakout",
            strategy_name="breakout",
            created_at=NOW,
            updated_at=NOW,
            stop_price=109.50,
            initial_stop_price=109.50,
            signal_timestamp=NOW,
        )
        runtime = _make_runtime(orders=[stop_order])
        notifier = RecordingNotifier()

        update = _make_trade_update(
            client_order_id=stop_order.client_order_id,
            symbol="AAPL",
            side="sell",
            status="filled",
            qty=10,
            filled_qty=10,
            filled_avg_price=109.20,
        )
        update["filled_avg_price"] = None

        _apply_with_notifier(runtime, update, notifier)

        assert len(notifier.sent) == 1
        _, body = notifier.sent[0]
        assert "?" in body

    def test_notifier_send_exception_is_swallowed(self):
        """When notifier.send() raises, apply_trade_update must not re-raise —
        the fill is already processed and the notification is best-effort."""
        entry_order = _make_entry_order(quantity=10)
        runtime = _make_runtime(orders=[entry_order])
        notifier = RecordingNotifier()
        notifier.raise_on_send = RuntimeError("slack timeout")

        update = _make_trade_update(status="filled", qty=10, filled_qty=10, filled_avg_price=112.00)
        # Must not raise even though notifier.send() raises.
        result = _apply_with_notifier(runtime, update, notifier)

        assert result["position_updated"] is True

    def test_no_notification_sent_when_no_fill_event(self):
        """A non-fill status update (e.g. 'new') must not trigger any notification."""
        entry_order = _make_entry_order(quantity=10)
        runtime = _make_runtime(orders=[entry_order])
        notifier = RecordingNotifier()

        update = _make_trade_update(status="new", qty=10, filled_qty=None, filled_avg_price=None)
        update["filled_avg_price"] = None
        update["filled_qty"] = None
        _apply_with_notifier(runtime, update, notifier)

        assert notifier.sent == []


def test_final_fill_updates_pending_stop_quantity_to_match_actual_fill() -> None:
    """When a partial fill creates a pending_submit stop, the final fill event must
    update the stop quantity to the actual filled quantity so the protective stop
    covers the correct number of shares."""
    entry_order = _make_entry_order(quantity=10, initial_stop_price=109.50)
    stop_id = _expected_stop_order_id(entry_order.client_order_id)
    existing_stop = OrderRecord(
        client_order_id=stop_id,
        symbol="AAPL",
        side="sell",
        intent_type="stop",
        status="pending_submit",
        quantity=7,
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        strategy_name="breakout",
        created_at=NOW,
        updated_at=NOW,
        stop_price=109.50,
        initial_stop_price=109.50,
        signal_timestamp=NOW,
    )
    runtime = _make_runtime(orders=[entry_order, existing_stop])

    update = _make_trade_update(status="filled", qty=10, filled_qty=10, filled_avg_price=112.00)
    _apply(runtime, update)

    stop_saves = [o for o in runtime.order_store.saved if o.intent_type == "stop"]
    assert len(stop_saves) == 1, f"Expected 1 stop update, got {stop_saves}"
    assert stop_saves[0].quantity == 10, (
        f"Expected stop quantity updated to 10 (final filled qty), got {stop_saves[0].quantity}"
    )
    assert stop_saves[0].status == "pending_submit"


def test_two_event_partial_then_filled_position_quantity_reflects_final_fill() -> None:
    """After a partially_filled event followed by a filled event, the saved
    PositionRecord.quantity must equal the total filled quantity from the final
    fill event — not the partial-fill quantity."""
    entry_order = _make_entry_order(quantity=10, initial_stop_price=109.50)
    runtime = _make_runtime(orders=[entry_order])

    # Event 1: partial fill of 7 shares
    partial_update = _make_trade_update(status="partially_filled", qty=10, filled_qty=7, filled_avg_price=111.50)
    _apply(runtime, partial_update)

    # Event 2: final fill for remaining 3 (total 10)
    final_update = _make_trade_update(status="filled", qty=10, filled_qty=10, filled_avg_price=112.00)
    _apply(runtime, final_update)

    position_saves = runtime.position_store.saved
    assert len(position_saves) >= 2, f"Expected at least 2 position saves, got {len(position_saves)}"
    last_position = position_saves[-1]
    assert last_position.quantity == 10, (
        f"Expected final position quantity=10, got {last_position.quantity}"
    )


def test_filled_event_with_no_matching_local_order_emits_unmatched_audit_event() -> None:
    """A fill event whose client_order_id has no matching local record must emit
    a 'trade_update_unmatched' audit event and must not create any position."""
    runtime = _make_runtime(orders=[])  # empty — no local orders

    update = _make_trade_update(
        client_order_id="v1-breakout:2026-04-25:AAPL:entry:2026-04-25T14:00:00+00:00",
        broker_order_id="broker-unknown-1",
        status="filled",
        qty=10,
        filled_qty=10,
        filled_avg_price=112.00,
    )
    result = _apply(runtime, update)

    assert result["unmatched"] is True
    unmatched_events = [
        e for e in runtime.audit_event_store.appended
        if e.event_type == "trade_update_unmatched"
    ]
    assert len(unmatched_events) == 1, (
        f"Expected 1 unmatched audit event, got {len(unmatched_events)}"
    )
    assert runtime.position_store.saved == [], "No position must be saved for an unmatched fill"
