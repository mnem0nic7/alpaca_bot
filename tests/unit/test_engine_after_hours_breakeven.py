from __future__ import annotations

from datetime import datetime, timezone

import pytest

from alpaca_bot.config import Settings
from alpaca_bot.core.engine import CycleIntentType, evaluate_cycle
from alpaca_bot.domain import Bar, OpenPosition
from alpaca_bot.strategy.session import SessionType


def _settings(**overrides) -> Settings:
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
        # Without this, is_flatten_time() returns True for AFTER_HOURS sessions
        # (session.py:53-54), so the engine emits EXIT instead of UPDATE_STOP.
        "EXTENDED_HOURS_ENABLED": "true",
        # BREAKEVEN_TRIGGER_PCT=0.0025, BREAKEVEN_TRAIL_PCT=0.002
    }
    base.update(overrides)
    return Settings.from_env(base)


def _make_position(
    *,
    entry_price: float = 100.0,
    stop_price: float = 95.0,
    highest_price: float = 105.0,
) -> OpenPosition:
    return OpenPosition(
        symbol="AAPL",
        entry_timestamp=datetime(2026, 5, 9, 14, 0, tzinfo=timezone.utc),
        entry_price=entry_price,
        quantity=100.0,
        entry_level=entry_price - 5.0,
        initial_stop_price=stop_price,
        stop_price=stop_price,
        trailing_active=False,
        highest_price=highest_price,
        strategy_name="breakout",
    )


def _make_bar(*, high: float, close: float, ts: datetime | None = None) -> Bar:
    ts = ts or datetime(2026, 5, 9, 21, 0, tzinfo=timezone.utc)  # 5pm ET = after hours
    return Bar(
        symbol="AAPL",
        timestamp=ts,
        open=close - 0.10,
        high=high,
        low=close - 0.20,
        close=close,
        volume=300_000,
    )


def _run_after_hours(position: OpenPosition, bar: Bar, settings: Settings) -> list:
    now = datetime(2026, 5, 9, 21, 0, tzinfo=timezone.utc)
    daily_bar = Bar(
        symbol="AAPL",
        timestamp=datetime(2026, 5, 8, tzinfo=timezone.utc),
        open=99.0, high=106.0, low=98.0, close=100.0,
        volume=1_000_000,
    )
    result = evaluate_cycle(
        settings=settings,
        now=now,
        equity=10_000.0,
        open_positions=[position],
        intraday_bars_by_symbol={"AAPL": [bar]},
        daily_bars_by_symbol={"AAPL": [daily_bar] * 60},
        working_order_symbols=set(),
        traded_symbols_today=set(),
        entries_disabled=False,
        session_type=SessionType.AFTER_HOURS,
    )
    return [i for i in result.intents if i.intent_type == CycleIntentType.UPDATE_STOP]


def test_after_hours_breakeven_emits_intent_when_stop_below_close():
    """
    Extended hours: when the computed breakeven stop is below the bar's close,
    the engine must emit UPDATE_STOP (safety guard does not fire).

    Setup:
      entry_price=100, highest_price=105, bar.high=101, bar.close=106
      trigger = 100 * 1.0025 = 100.25  → bar.high 101 >= trigger ✓
      max_price = max(105, 101) = 105
      trail_stop = round(105 * 0.998, 2) = 104.79
      be_stop = max(100, 104.79) = 104.79
      safety guard: 104.79 >= 106.0 → False → intent emitted ✓
    """
    settings = _settings()
    position = _make_position(entry_price=100.0, stop_price=95.0, highest_price=105.0)
    bar = _make_bar(high=101.0, close=106.0)

    intents = _run_after_hours(position, bar, settings)

    assert len(intents) == 1, f"Expected 1 UPDATE_STOP intent, got {len(intents)}"
    assert intents[0].stop_price == pytest.approx(104.79)
    assert intents[0].reason == "breakeven"


def test_after_hours_safety_guard_suppresses_stop_at_or_above_close():
    """
    Extended hours: when the computed breakeven stop >= bar.close,
    submitting it would trigger immediately at open — engine must NOT emit.

    Setup:
      entry_price=100, highest_price=105, bar.high=106, bar.close=104
      trigger = 100.25  → bar.high 106 >= trigger ✓
      max_price = max(105, 106) = 106
      trail_stop = round(106 * 0.998, 2) = 105.79
      be_stop = max(100, 105.79) = 105.79
      safety guard: 105.79 >= 104.0 → True → no intent emitted ✓
    """
    settings = _settings()
    position = _make_position(entry_price=100.0, stop_price=95.0, highest_price=105.0)
    bar = _make_bar(high=106.0, close=104.0)

    intents = _run_after_hours(position, bar, settings)

    assert intents == [], (
        f"Safety guard should have suppressed intent when stop >= close; got {intents}"
    )


def test_after_hours_price_below_trigger_no_intent():
    """
    Extended hours: when bar.high < trigger, the breakeven condition is not met
    and no intent is emitted.

    Setup:
      entry_price=100, trigger=100.25, bar.high=100.0 < trigger → no intent
    """
    settings = _settings()
    position = _make_position(entry_price=100.0, stop_price=95.0, highest_price=100.0)
    bar = _make_bar(high=100.0, close=100.50)

    intents = _run_after_hours(position, bar, settings)

    assert intents == [], f"No intent expected below trigger; got {intents}"
