from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

from alpaca_bot.config import Settings
from alpaca_bot.storage import AuditEvent


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


class RecordingStream:
    def __init__(self) -> None:
        self.handler = None
        self.run_calls = 0

    def subscribe_trade_updates(self, handler) -> None:
        self.handler = handler

    def run(self) -> None:
        self.run_calls += 1


class RecordingAuditEventStore:
    def __init__(self) -> None:
        self.appended: list[AuditEvent] = []

    def append(self, event: AuditEvent) -> None:
        self.appended.append(event)


def load_streaming_api():
    from alpaca_bot.runtime.trade_update_stream import (
        attach_trade_update_stream,
        run_trade_update_stream,
    )

    return attach_trade_update_stream, run_trade_update_stream


def test_attach_trade_update_stream_registers_async_handler_and_applies_updates(monkeypatch) -> None:
    attach_trade_update_stream, _run_trade_update_stream = load_streaming_api()
    settings = make_settings()
    now = datetime(2026, 4, 24, 19, 35, tzinfo=timezone.utc)
    runtime = SimpleNamespace(audit_event_store=RecordingAuditEventStore())
    stream = RecordingStream()
    seen: list[dict[str, object]] = []

    def fake_apply_trade_update(**kwargs):
        seen.append(kwargs)
        return {"order_updated": True}

    monkeypatch.setattr(
        "alpaca_bot.runtime.trade_update_stream.apply_trade_update",
        fake_apply_trade_update,
    )

    handler = attach_trade_update_stream(
        settings=settings,
        runtime=runtime,
        stream=stream,
        now=lambda: now,
    )

    assert stream.handler is handler
    asyncio.run(
        handler(
            {
                "event": "fill",
                "client_order_id": "cid-1",
                "broker_order_id": "oid-1",
                "symbol": "AAPL",
                "status": "filled",
                "timestamp": now.isoformat(),
            }
        )
    )

    assert seen == [
        {
            "settings": settings,
            "runtime": runtime,
            "update": {
                "event": "fill",
                "client_order_id": "cid-1",
                "broker_order_id": "oid-1",
                "symbol": "AAPL",
                "status": "filled",
                "timestamp": now.isoformat(),
            },
            "now": now,
            "notifier": None,
        }
    ]


def test_attach_trade_update_stream_audits_handler_failures(monkeypatch) -> None:
    attach_trade_update_stream, _run_trade_update_stream = load_streaming_api()
    settings = make_settings()
    now = datetime(2026, 4, 24, 19, 36, tzinfo=timezone.utc)
    runtime = SimpleNamespace(audit_event_store=RecordingAuditEventStore())
    stream = RecordingStream()

    def fake_apply_trade_update(**kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(
        "alpaca_bot.runtime.trade_update_stream.apply_trade_update",
        fake_apply_trade_update,
    )

    handler = attach_trade_update_stream(
        settings=settings,
        runtime=runtime,
        stream=stream,
        now=lambda: now,
    )
    asyncio.run(
        handler(
            {
                "event": "fill",
                "client_order_id": "cid-1",
                "broker_order_id": "oid-1",
                "symbol": "AAPL",
                "status": "filled",
                "timestamp": now.isoformat(),
            }
        )
    )

    assert runtime.audit_event_store.appended == [
        AuditEvent(
            event_type="trade_update_failed",
            symbol="AAPL",
            payload={
                "error": "boom",
                "client_order_id": "cid-1",
                "broker_order_id": "oid-1",
            },
            created_at=now,
        )
    ]


def test_run_trade_update_stream_registers_handler_and_starts_stream(monkeypatch) -> None:
    attach_trade_update_stream, run_trade_update_stream = load_streaming_api()
    settings = make_settings()
    runtime = SimpleNamespace(audit_event_store=RecordingAuditEventStore())
    stream = RecordingStream()

    run_trade_update_stream(
        settings=settings,
        runtime=runtime,
        stream=stream,
    )

    assert stream.handler is not None
    assert stream.run_calls == 1
