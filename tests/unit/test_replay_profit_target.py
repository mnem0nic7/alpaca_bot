from __future__ import annotations

from datetime import datetime, timedelta, timezone

from alpaca_bot.config import Settings
from alpaca_bot.domain.enums import IntentType
from alpaca_bot.domain.models import Bar, OpenPosition, ReplayScenario
from alpaca_bot.replay import ReplayRunner
from alpaca_bot.replay.runner import ReplayState


def _settings(**overrides: str) -> Settings:
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
        "ENABLE_BREAKEVEN_STOP": "false",
        "ENABLE_PROFIT_TARGET": "true",
        "PROFIT_TARGET_R": "2.0",
    }
    base.update(overrides)
    return Settings.from_env(base)


def _bar(high: float, low: float, close: float, ts: datetime) -> Bar:
    return Bar(
        symbol="AAPL",
        timestamp=ts,
        open=close - 0.1,
        high=high,
        low=low,
        close=close,
        volume=500_000,
    )


def _position(
    *,
    entry_price: float = 100.0,
    initial_stop_price: float = 95.0,
    stop_price: float = 95.0,
    ts: datetime | None = None,
) -> OpenPosition:
    ts = ts or datetime(2026, 5, 9, 10, 0, tzinfo=timezone.utc)
    return OpenPosition(
        symbol="AAPL",
        entry_timestamp=ts,
        entry_price=entry_price,
        quantity=10.0,
        entry_level=entry_price - 5.0,
        initial_stop_price=initial_stop_price,
        stop_price=stop_price,
        trailing_active=False,
        highest_price=entry_price,
        strategy_name="breakout",
    )


# --- Unit tests for _process_profit_target_hit ---

def test_process_profit_target_hit_exits_at_target():
    settings = _settings()
    runner = ReplayRunner(settings)
    ts = datetime(2026, 5, 9, 13, 0, tzinfo=timezone.utc)
    pos = _position(entry_price=100.0, initial_stop_price=95.0, stop_price=95.0)
    # risk_per_share = 5, target = 100 + 2*5 = 110
    bar = _bar(high=112.0, low=108.0, close=111.0, ts=ts)
    state = ReplayState(equity=100_000.0, position=pos)
    events: list = []
    hit = runner._process_profit_target_hit(bar=bar, state=state, events=events)
    assert hit is True
    assert state.position is None
    assert len(events) == 1
    assert events[0].event_type == IntentType.PROFIT_TARGET_HIT
    assert events[0].details["exit_price"] == 110.0  # filled at target_price


def test_process_profit_target_not_hit():
    settings = _settings()
    runner = ReplayRunner(settings)
    ts = datetime(2026, 5, 9, 13, 0, tzinfo=timezone.utc)
    pos = _position(entry_price=100.0, initial_stop_price=95.0, stop_price=95.0)
    # high=109.99 < target=110
    bar = _bar(high=109.99, low=105.0, close=109.0, ts=ts)
    state = ReplayState(equity=100_000.0, position=pos)
    events: list = []
    hit = runner._process_profit_target_hit(bar=bar, state=state, events=events)
    assert hit is False
    assert state.position is not None
    assert len(events) == 0


def test_process_profit_target_hit_no_position():
    settings = _settings()
    runner = ReplayRunner(settings)
    ts = datetime(2026, 5, 9, 13, 0, tzinfo=timezone.utc)
    bar = _bar(high=200.0, low=100.0, close=150.0, ts=ts)
    state = ReplayState(equity=100_000.0, position=None)
    events: list = []
    hit = runner._process_profit_target_hit(bar=bar, state=state, events=events)
    assert hit is False
    assert len(events) == 0


def test_stop_takes_priority_over_profit_target_same_bar():
    """When stop AND target both hit in same bar, stop check runs first and returns True,
    so profit target check is skipped (stop priority)."""
    settings = _settings()
    runner = ReplayRunner(settings)
    ts = datetime(2026, 5, 9, 13, 0, tzinfo=timezone.utc)
    pos = _position(entry_price=100.0, initial_stop_price=95.0, stop_price=95.0)
    # bar.low=94 < stop=95 AND bar.high=111 > target=110
    bar = _bar(high=111.0, low=94.0, close=94.5, ts=ts)
    state = ReplayState(equity=100_000.0, position=pos)
    events: list = []

    stop_hit = runner._process_stop_hit(bar=bar, state=state, events=events)
    assert stop_hit is True  # stop fired first
    assert events[0].event_type == IntentType.STOP_HIT
    # Because stop_hit is True, caller does `continue` — profit target is never called


def test_profit_target_pnl_applied_to_equity():
    settings = _settings()
    runner = ReplayRunner(settings)
    ts = datetime(2026, 5, 9, 13, 0, tzinfo=timezone.utc)
    # risk=5, target=110, entry=100, qty=10 → gain = (110-100)*10 = 100
    pos = _position(entry_price=100.0, initial_stop_price=95.0, stop_price=95.0)
    pos.quantity = 10.0
    bar = _bar(high=115.0, low=108.0, close=114.0, ts=ts)
    state = ReplayState(equity=50_000.0, position=pos)
    events: list = []
    runner._process_profit_target_hit(bar=bar, state=state, events=events)
    assert state.equity == 50_100.0  # 50_000 + (110-100)*10
