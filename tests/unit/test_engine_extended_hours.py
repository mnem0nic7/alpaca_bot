from __future__ import annotations
from datetime import datetime, timezone
import pytest
from alpaca_bot.config import Settings
from alpaca_bot.core.engine import CycleIntentType, evaluate_cycle
from alpaca_bot.domain.models import Bar, EntrySignal, OpenPosition
from alpaca_bot.strategy.session import SessionType


def _settings(**overrides) -> Settings:
    base = {
        "TRADING_MODE": "paper",
        "ENABLE_LIVE_TRADING": "false",
        "STRATEGY_VERSION": "v1",
        "DATABASE_URL": "postgresql://test/db",
        "MARKET_DATA_FEED": "iex",
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
        "EXTENDED_HOURS_ENABLED": "true",
        "EXTENDED_HOURS_FLATTEN_TIME": "19:45",
        "EXTENDED_HOURS_LIMIT_OFFSET_PCT": "0.001",
    }
    base.update(overrides)
    return Settings.from_env(base)


def _position(symbol: str = "AAPL", stop_price: float = 95.0) -> OpenPosition:
    from datetime import timezone
    return OpenPosition(
        symbol=symbol,
        quantity=10,
        entry_price=100.0,
        stop_price=stop_price,
        # risk_per_share = entry_price - initial_stop_price = 100 - 95 = 5
        initial_stop_price=95.0,
        entry_level=95.0,
        entry_timestamp=datetime(2026, 4, 28, 14, 0, tzinfo=timezone.utc),
    )


def _bar(symbol: str, close: float, high: float | None = None, ts: datetime | None = None) -> Bar:
    if ts is None:
        ts = datetime(2026, 4, 28, 21, 0, tzinfo=timezone.utc)  # 5pm ET
    return Bar(
        symbol=symbol,
        timestamp=ts,
        open=close,
        high=high or close,
        low=close,
        close=close,
        volume=1000,
    )


def test_update_stop_suppressed_in_after_hours():
    """UPDATE_STOP intents must not be emitted during extended hours."""
    settings = _settings()
    # 5pm ET = after hours, position has profited enough to trigger a stop update
    now = datetime(2026, 4, 28, 21, 0, tzinfo=timezone.utc)
    position = _position(stop_price=95.0)
    bar = _bar("AAPL", close=106.0, high=106.0)  # high >= entry_price + risk_per_share = 105

    result = evaluate_cycle(
        settings=settings,
        now=now,
        equity=100_000.0,
        intraday_bars_by_symbol={"AAPL": [bar]},
        daily_bars_by_symbol={"AAPL": [bar]},
        open_positions=[position],
        working_order_symbols=set(),
        traded_symbols_today=set(),
        entries_disabled=False,
        session_type=SessionType.AFTER_HOURS,
    )
    update_stops = [i for i in result.intents if i.intent_type is CycleIntentType.UPDATE_STOP]
    assert update_stops == [], "UPDATE_STOP must be suppressed in extended hours"


def test_update_stop_allowed_in_regular_session():
    settings = _settings()
    now = datetime(2026, 4, 28, 16, 0, tzinfo=timezone.utc)  # 12pm ET = regular
    position = _position(stop_price=95.0)
    bar = _bar("AAPL", close=106.0, high=106.0, ts=now)

    result = evaluate_cycle(
        settings=settings,
        now=now,
        equity=100_000.0,
        intraday_bars_by_symbol={"AAPL": [bar]},
        daily_bars_by_symbol={"AAPL": [bar]},
        open_positions=[position],
        working_order_symbols=set(),
        traded_symbols_today=set(),
        entries_disabled=False,
        session_type=SessionType.REGULAR,
    )
    update_stops = [i for i in result.intents if i.intent_type is CycleIntentType.UPDATE_STOP]
    assert len(update_stops) == 1


def test_after_hours_flatten_emits_exit_with_limit_price():
    """Flatten at extended_hours_flatten_time must set limit_price on EXIT intent."""
    settings = _settings()
    # 7:50pm ET = past EXTENDED_HOURS_FLATTEN_TIME (19:45)
    now = datetime(2026, 4, 28, 23, 50, tzinfo=timezone.utc)
    position = _position()
    bar = _bar("AAPL", close=105.0, ts=now)

    result = evaluate_cycle(
        settings=settings,
        now=now,
        equity=100_000.0,
        intraday_bars_by_symbol={"AAPL": [bar]},
        daily_bars_by_symbol={"AAPL": [bar]},
        open_positions=[position],
        working_order_symbols=set(),
        traded_symbols_today=set(),
        entries_disabled=False,
        session_type=SessionType.AFTER_HOURS,
    )
    exits = [i for i in result.intents if i.intent_type is CycleIntentType.EXIT]
    assert len(exits) == 1
    assert exits[0].limit_price is not None
    assert exits[0].limit_price == pytest.approx(round(105.0 * (1 - 0.001), 2), rel=1e-5)
    assert exits[0].reason == "eod_flatten"


def test_regular_session_flatten_no_limit_price():
    """Regular session flatten must NOT set limit_price (market exit)."""
    settings = _settings()
    # 3:50pm ET = past FLATTEN_TIME (15:45)
    now = datetime(2026, 4, 28, 19, 50, tzinfo=timezone.utc)
    position = _position()
    bar = _bar("AAPL", close=105.0, ts=now)

    result = evaluate_cycle(
        settings=settings,
        now=now,
        equity=100_000.0,
        intraday_bars_by_symbol={"AAPL": [bar]},
        daily_bars_by_symbol={"AAPL": [bar]},
        open_positions=[position],
        working_order_symbols=set(),
        traded_symbols_today=set(),
        entries_disabled=False,
        session_type=SessionType.REGULAR,
    )
    exits = [i for i in result.intents if i.intent_type is CycleIntentType.EXIT]
    assert len(exits) == 1
    assert exits[0].limit_price is None


def test_no_session_type_defaults_to_regular_behaviour():
    """Existing callers that omit session_type must see unchanged behaviour."""
    settings = _settings()
    now = datetime(2026, 4, 28, 19, 50, tzinfo=timezone.utc)  # 3:50pm ET
    position = _position()
    bar = _bar("AAPL", close=105.0, ts=now)

    result = evaluate_cycle(
        settings=settings,
        now=now,
        equity=100_000.0,
        intraday_bars_by_symbol={"AAPL": [bar]},
        daily_bars_by_symbol={"AAPL": [bar]},
        open_positions=[position],
        working_order_symbols=set(),
        traded_symbols_today=set(),
        entries_disabled=False,
        # no session_type
    )
    exits = [i for i in result.intents if i.intent_type is CycleIntentType.EXIT]
    assert len(exits) == 1
    assert exits[0].limit_price is None  # market exit


def test_afterhours_entry_not_blocked_by_stale_bars():
    """Entries must be possible during afterhours even with 2.5-hour-old bars."""
    settings = _settings()
    # 6pm ET = 22:00 UTC; bar from 3:30pm ET = 19:30 UTC → 2.5h old → fails 30-min check
    # Bar is at ENTRY_WINDOW_END (15:30 ET) so signal_index walk-back (Task 5) finds it.
    now = datetime(2026, 4, 28, 22, 0, tzinfo=timezone.utc)
    stale_bar = _bar("AAPL", close=105.0, ts=datetime(2026, 4, 28, 19, 30, tzinfo=timezone.utc))

    result = evaluate_cycle(
        settings=settings,
        now=now,
        equity=100_000.0,
        intraday_bars_by_symbol={"AAPL": [stale_bar]},
        daily_bars_by_symbol={"AAPL": [stale_bar]},
        open_positions=[],
        working_order_symbols=set(),
        traded_symbols_today=set(),
        entries_disabled=False,
        session_type=SessionType.AFTER_HOURS,
        signal_evaluator=lambda **kwargs: EntrySignal(
            symbol="AAPL",
            signal_bar=kwargs["intraday_bars"][-1],
            entry_level=105.1,
            relative_volume=2.0,
            stop_price=103.0,
            limit_price=105.2,
            initial_stop_price=103.0,
        ),
    )
    entries = [i for i in result.intents if i.intent_type is CycleIntentType.ENTRY]
    assert entries, (
        "AFTER_HOURS entries must not be blocked by the 30-minute bar-age check; "
        "regular session bars are the correct and only available signal basis"
    )
