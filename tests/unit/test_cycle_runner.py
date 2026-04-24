from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from importlib import import_module
from types import SimpleNamespace

from alpaca_bot.config import Settings, TradingMode
from alpaca_bot.storage import AuditEvent, OrderRecord


def load_cycle_runner_api() -> tuple[object, object]:
    module = import_module("alpaca_bot.runtime.cycle_runner")
    return module, module.run_cycle


def make_settings() -> Settings:
    return Settings.from_env(
        {
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
    )


class RecordingOrderStore:
    def __init__(self) -> None:
        self.saved: list[OrderRecord] = []

    def save(self, order: OrderRecord) -> None:
        self.saved.append(order)


class RecordingAuditEventStore:
    def __init__(self) -> None:
        self.appended: list[AuditEvent] = []

    def append(self, event: AuditEvent) -> None:
        self.appended.append(event)


@dataclass(frozen=True)
class EngineCall:
    settings: Settings
    runtime: object
    now: datetime
    bars_by_symbol: dict[str, list[object]]
    daily_bars_by_symbol: dict[str, list[object]]
    open_positions: list[object]
    traded_symbols_today: set[str]
    entries_disabled: bool


def test_run_cycle_delegates_to_engine_and_persists_generated_intents(monkeypatch) -> None:
    module, run_cycle = load_cycle_runner_api()
    settings = make_settings()
    order_store = RecordingOrderStore()
    audit_store = RecordingAuditEventStore()
    runtime = SimpleNamespace(order_store=order_store, audit_event_store=audit_store)
    now = datetime(2026, 4, 24, 19, 0, tzinfo=timezone.utc)
    bars_by_symbol = {"AAPL": []}
    daily_bars_by_symbol = {"AAPL": []}
    open_positions = [SimpleNamespace(symbol="MSFT")]
    traded_symbols_today = {"SPY"}
    entry_intent = OrderRecord(
        client_order_id="paper:v1-breakout:AAPL:2026-04-24T19:00:00+00:00:entry",
        symbol="AAPL",
        side="buy",
        intent_type="entry",
        status="pending",
        quantity=25,
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        signal_timestamp=now,
        stop_price=111.01,
        limit_price=111.12,
        initial_stop_price=109.89,
        created_at=now,
        updated_at=now,
    )
    stop_update_intent = AuditEvent(
        event_type="stop_updated",
        symbol="AAPL",
        payload={"intent_type": "stop_updated", "stop_price": 110.75},
        created_at=now,
    )
    exit_intent = AuditEvent(
        event_type="eod_exit",
        symbol="AAPL",
        payload={"intent_type": "eod_exit", "reason": "flatten_time"},
        created_at=now,
    )
    engine_intents = [entry_intent, stop_update_intent, exit_intent]
    captured: list[EngineCall] = []

    def fake_run_cycle_engine(
        settings: Settings,
        runtime: object,
        now: datetime,
        bars_by_symbol: dict[str, list[object]],
        daily_bars_by_symbol: dict[str, list[object]],
        open_positions: list[object],
        traded_symbols_today: set[str],
        entries_disabled: bool,
    ) -> list[object]:
        captured.append(
            EngineCall(
                settings=settings,
                runtime=runtime,
                now=now,
                bars_by_symbol=bars_by_symbol,
                daily_bars_by_symbol=daily_bars_by_symbol,
                open_positions=open_positions,
                traded_symbols_today=traded_symbols_today,
                entries_disabled=entries_disabled,
            )
        )
        return engine_intents

    monkeypatch.setattr(module, "run_cycle_engine", fake_run_cycle_engine, raising=False)

    result = run_cycle(
        settings,
        runtime,
        now,
        bars_by_symbol,
        daily_bars_by_symbol,
        open_positions,
        traded_symbols_today,
        False,
    )

    assert result == engine_intents
    assert captured == [
        EngineCall(
            settings=settings,
            runtime=runtime,
            now=now,
            bars_by_symbol=bars_by_symbol,
            daily_bars_by_symbol=daily_bars_by_symbol,
            open_positions=open_positions,
            traded_symbols_today=traded_symbols_today,
            entries_disabled=False,
        )
    ]
    assert order_store.saved == [entry_intent]
    assert stop_update_intent in audit_store.appended
    assert exit_intent in audit_store.appended
    assert any(
        event.payload.get("cycle_timestamp") == now.isoformat()
        and event.payload.get("action_count") == 3
        for event in audit_store.appended
    )


def test_run_cycle_appends_summary_audit_event_for_empty_engine_result(monkeypatch) -> None:
    module, run_cycle = load_cycle_runner_api()
    settings = make_settings()
    order_store = RecordingOrderStore()
    audit_store = RecordingAuditEventStore()
    runtime = SimpleNamespace(order_store=order_store, audit_event_store=audit_store)
    now = datetime(2026, 4, 24, 19, 15, tzinfo=timezone.utc)

    monkeypatch.setattr(module, "run_cycle_engine", lambda *args, **kwargs: [], raising=False)

    result = run_cycle(
        settings,
        runtime,
        now,
        {},
        {},
        [],
        set(),
        True,
    )

    assert result == []
    assert order_store.saved == []
    assert len(audit_store.appended) == 1
    assert audit_store.appended[0].payload["cycle_timestamp"] == now.isoformat()
    assert audit_store.appended[0].payload["action_count"] == 0
