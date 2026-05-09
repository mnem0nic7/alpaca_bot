from __future__ import annotations

from datetime import datetime, timezone

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
        "ENABLE_BREAKEVEN_STOP": "false",
    }
    base.update(overrides)
    return Settings.from_env(base)


def _position(
    *,
    entry_price: float = 100.0,
    initial_stop_price: float = 95.0,
    stop_price: float = 95.0,
) -> OpenPosition:
    return OpenPosition(
        symbol="AAPL",
        entry_timestamp=datetime(2026, 5, 9, 10, 0, tzinfo=timezone.utc),
        entry_price=entry_price,
        quantity=10.0,
        entry_level=entry_price - 5.0,
        initial_stop_price=initial_stop_price,
        stop_price=stop_price,
        trailing_active=False,
        highest_price=entry_price,
        strategy_name="breakout",
    )


def _bar(high: float, close: float | None = None) -> Bar:
    if close is None:
        close = high - 0.5
    return Bar(
        symbol="AAPL",
        timestamp=datetime(2026, 5, 9, 13, 0, tzinfo=timezone.utc),
        open=close - 0.5,
        high=high,
        low=close - 1.0,
        close=close,
        volume=500_000,
    )


_NOW = datetime(2026, 5, 9, 13, 0, tzinfo=timezone.utc)


def _run(position: OpenPosition, bar: Bar, settings: Settings) -> list:
    return evaluate_cycle(
        settings=settings,
        now=_NOW,
        equity=100_000.0,
        intraday_bars_by_symbol={"AAPL": [bar]},
        daily_bars_by_symbol={"AAPL": []},
        open_positions=[position],
        working_order_symbols=set(),
        traded_symbols_today=set(),
        entries_disabled=True,
        session_type=SessionType.REGULAR,
    ).intents


# entry_price=100, stop=95 → risk_per_share=5 → target at 100 + 2*5 = 110


def test_profit_target_hit_emits_exit():
    settings = _settings(ENABLE_PROFIT_TARGET="true", PROFIT_TARGET_R="2.0")
    pos = _position(entry_price=100.0, initial_stop_price=95.0, stop_price=95.0)
    # bar.high=110.0 == target → exit
    intents = _run(pos, _bar(high=110.0, close=109.0), settings)
    exits = [i for i in intents if i.intent_type == CycleIntentType.EXIT]
    assert len(exits) == 1
    assert exits[0].reason == "profit_target"
    assert exits[0].symbol == "AAPL"


def test_profit_target_not_hit_no_exit():
    settings = _settings(ENABLE_PROFIT_TARGET="true", PROFIT_TARGET_R="2.0")
    pos = _position(entry_price=100.0, initial_stop_price=95.0, stop_price=95.0)
    # bar.high=109.99 < target=110 → no profit target exit
    intents = _run(pos, _bar(high=109.99, close=109.0), settings)
    exits = [i for i in intents if i.intent_type == CycleIntentType.EXIT]
    assert len(exits) == 0


def test_profit_target_disabled_no_exit():
    settings = _settings(ENABLE_PROFIT_TARGET="false", PROFIT_TARGET_R="2.0")
    pos = _position(entry_price=100.0, initial_stop_price=95.0, stop_price=95.0)
    # bar.high well above target, but feature is off
    intents = _run(pos, _bar(high=120.0, close=119.0), settings)
    exits = [i for i in intents if i.intent_type == CycleIntentType.EXIT]
    assert len(exits) == 0


def test_profit_target_skipped_during_extended_hours():
    settings = _settings(
        ENABLE_PROFIT_TARGET="true", PROFIT_TARGET_R="2.0", EXTENDED_HOURS_ENABLED="true"
    )
    pos = _position(entry_price=100.0, initial_stop_price=95.0, stop_price=95.0)
    bar = _bar(high=120.0, close=119.0)
    intents = evaluate_cycle(
        settings=settings,
        now=_NOW,
        equity=100_000.0,
        intraday_bars_by_symbol={"AAPL": [bar]},
        daily_bars_by_symbol={"AAPL": []},
        open_positions=[pos],
        working_order_symbols=set(),
        traded_symbols_today=set(),
        entries_disabled=True,
        session_type=SessionType.AFTER_HOURS,
    ).intents
    exits = [i for i in intents if i.intent_type == CycleIntentType.EXIT]
    # Extended hours: no profit target exit (stop_price=95 < close=119, no stop breach exit)
    assert not any(i.reason == "profit_target" for i in exits)


def test_profit_target_hit_suppresses_update_stop():
    """When target and trailing trigger both fire in the same bar, EXIT takes priority."""
    settings = _settings(
        ENABLE_PROFIT_TARGET="true",
        PROFIT_TARGET_R="2.0",
        TRAILING_STOP_PROFIT_TRIGGER_R="1.0",  # trailing activates at 1R
    )
    pos = _position(entry_price=100.0, initial_stop_price=95.0, stop_price=95.0)
    # high=110 hits both profit target (2R=110) and trailing trigger (1R=105)
    intents = _run(pos, _bar(high=110.0, close=108.0), settings)
    exits = [i for i in intents if i.intent_type == CycleIntentType.EXIT]
    updates = [i for i in intents if i.intent_type == CycleIntentType.UPDATE_STOP]
    assert len(exits) == 1
    assert exits[0].reason == "profit_target"
    # No dangling UPDATE_STOP for the same position that already triggered profit exit
    assert not any(u.symbol == "AAPL" for u in updates)
