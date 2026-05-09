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


def test_update_stop_suppressed_in_after_hours_safety_guard():
    """Safety guard fires when be_stop >= close — UPDATE_STOP must not be emitted.

    Setup:
      entry_price=100, highest_price=0 (default), high=106
      be_stop = round(106 * 0.998, 2) = 105.79
      close=103 < 105.79 → safety guard fires (stop would trigger at open) → no intent
    """
    settings = _settings()
    now = datetime(2026, 4, 28, 21, 0, tzinfo=timezone.utc)  # 5pm ET = after hours
    position = _position(stop_price=95.0)
    # high=106 clears the breakeven trigger; close=103 is below be_stop=105.79
    bar = _bar("AAPL", close=103.0, high=106.0)

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
    assert update_stops == [], "Safety guard must suppress UPDATE_STOP when be_stop >= close"


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
    # Set a large max-age so the staleness guard does not interfere; this test
    # specifically verifies the bar-age check in the position loop is bypassed.
    settings = _settings(EXTENDED_HOURS_SIGNAL_MAX_AGE_MINUTES="180")
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


def test_cap_up_stop_not_emitted_in_after_hours():
    """Cap-up UPDATE_STOP must not be emitted during extended hours."""
    # entry=100, max_stop_pct=5% → cap_stop=95.0; position stop=88.0 is below cap.
    # In regular session this would emit UPDATE_STOP; in AFTER_HOURS it must not.
    settings = _settings(MAX_STOP_PCT="0.05")
    now = datetime(2026, 4, 28, 21, 0, tzinfo=timezone.utc)  # 5pm ET
    position = OpenPosition(
        symbol="AAPL",
        quantity=10,
        entry_price=100.0,
        stop_price=88.0,
        initial_stop_price=88.0,
        entry_level=88.0,
        entry_timestamp=datetime(2026, 4, 28, 14, 0, tzinfo=timezone.utc),
    )
    bar = _bar("AAPL", close=100.0, ts=now)

    result = evaluate_cycle(
        settings=settings,
        now=now,
        equity=100_000.0,
        intraday_bars_by_symbol={"AAPL": [bar]},
        daily_bars_by_symbol={},
        open_positions=[position],
        working_order_symbols=set(),
        traded_symbols_today=set(),
        entries_disabled=True,
        session_type=SessionType.AFTER_HOURS,
    )
    update_stops = [i for i in result.intents if i.intent_type is CycleIntentType.UPDATE_STOP]
    assert update_stops == [], "cap-up UPDATE_STOP must be suppressed during extended hours"


def test_afterhours_spread_filter_uses_extended_threshold():
    """During extended hours, extended_hours_max_spread_pct applies, not max_spread_pct."""
    settings = _settings(
        EXTENDED_HOURS_MAX_SPREAD_PCT="0.01",
        ENABLE_SPREAD_FILTER="true",
        MAX_SPREAD_PCT="0.002",
        EXTENDED_HOURS_SIGNAL_MAX_AGE_MINUTES="180",  # bar is 2.5h old; not testing staleness here
    )
    # 0.5% spread: blocked by regular 0.2% threshold, allowed by extended 1% threshold
    class FakeQuote:
        spread_pct = 0.005

    now = datetime(2026, 4, 28, 22, 0, tzinfo=timezone.utc)  # 6pm ET
    # Bar at ENTRY_WINDOW_END (3:30pm ET = 19:30 UTC) so signal_index walk-back (Task 5) finds it.
    bar = _bar("AAPL", close=105.0, ts=datetime(2026, 4, 28, 19, 30, tzinfo=timezone.utc))

    result = evaluate_cycle(
        settings=settings,
        now=now,
        equity=100_000.0,
        intraday_bars_by_symbol={"AAPL": [bar]},
        daily_bars_by_symbol={"AAPL": [bar]},
        open_positions=[],
        working_order_symbols=set(),
        traded_symbols_today=set(),
        entries_disabled=False,
        session_type=SessionType.AFTER_HOURS,
        quotes_by_symbol={"AAPL": FakeQuote()},
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
    assert result.spread_blocked_symbols == (), (
        "0.5% spread should pass the 1% extended-hours threshold; "
        "regular-session 0.2% threshold must not apply during extended hours"
    )


def test_position_bar_age_check_bypassed_in_after_hours():
    """Position loop must not drop positions with stale bars during AFTER_HOURS.

    Regression: the bar-age guard in the position loop was unconditional, so
    an after-hours position whose latest bar was a regular-session bar (hours
    old) was silently skipped before the is_extended guard could fire.
    The flatten/EXIT path must still be reachable for such positions.
    """
    settings = _settings()  # EXTENDED_HOURS_FLATTEN_TIME=19:45 ET
    # 7:50pm ET = 23:50 UTC: past flatten (7:45pm ET). Bar at 3:30pm ET = 19:30 UTC → 4h20m old.
    now = datetime(2026, 4, 28, 23, 50, tzinfo=timezone.utc)
    stale_bar = _bar("AAPL", close=100.0, ts=datetime(2026, 4, 28, 19, 30, tzinfo=timezone.utc))
    position = OpenPosition(
        symbol="AAPL",
        quantity=10,
        entry_price=100.0,
        stop_price=95.0,
        initial_stop_price=95.0,
        entry_level=95.0,
        entry_timestamp=datetime(2026, 4, 28, 14, 0, tzinfo=timezone.utc),
    )
    result = evaluate_cycle(
        settings=settings,
        now=now,
        equity=100_000.0,
        intraday_bars_by_symbol={"AAPL": [stale_bar]},
        daily_bars_by_symbol={},
        open_positions=[position],
        working_order_symbols=set(),
        traded_symbols_today=set(),
        entries_disabled=True,
        session_type=SessionType.AFTER_HOURS,
    )
    exits = [i for i in result.intents if i.intent_type is CycleIntentType.EXIT]
    assert exits, (
        "Position with stale regular-session bar must still reach the flatten path "
        "during AFTER_HOURS — bar-age check must be bypassed in the position loop"
    )


def test_afterhours_signal_uses_last_in_window_bar():
    """During extended hours, signal_evaluator must receive the last bar within ENTRY_WINDOW_END."""
    settings = _settings(EXTENDED_HOURS_SIGNAL_MAX_AGE_MINUTES="180")  # ENTRY_WINDOW_END=15:30
    now = datetime(2026, 4, 28, 22, 0, tzinfo=timezone.utc)  # 6pm ET

    # Two bars: 3:30pm ET (within ENTRY_WINDOW_END=15:30) and 3:45pm ET (past it)
    bar_in_window = _bar("AAPL", close=105.0, ts=datetime(2026, 4, 28, 19, 30, tzinfo=timezone.utc))
    bar_past_window = _bar("AAPL", close=106.0, ts=datetime(2026, 4, 28, 19, 45, tzinfo=timezone.utc))

    seen_signal_ts: list = []

    def recording_evaluator(**kwargs) -> EntrySignal | None:
        seen_signal_ts.append(kwargs["intraday_bars"][kwargs["signal_index"]].timestamp)
        return EntrySignal(
            symbol="AAPL",
            signal_bar=kwargs["intraday_bars"][kwargs["signal_index"]],
            entry_level=105.1,
            relative_volume=2.0,
            stop_price=103.0,
            limit_price=105.2,
            initial_stop_price=103.0,
        )

    evaluate_cycle(
        settings=settings,
        now=now,
        equity=100_000.0,
        intraday_bars_by_symbol={"AAPL": [bar_in_window, bar_past_window]},
        daily_bars_by_symbol={"AAPL": [bar_in_window]},
        open_positions=[],
        working_order_symbols=set(),
        traded_symbols_today=set(),
        entries_disabled=False,
        session_type=SessionType.AFTER_HOURS,
        signal_evaluator=recording_evaluator,
    )
    assert seen_signal_ts, "signal_evaluator must be called during AFTER_HOURS"
    assert seen_signal_ts[0] == bar_in_window.timestamp, (
        "signal_index must point to the last bar within ENTRY_WINDOW_END, not bars[-1]"
    )


def test_pre_market_signal_index_uses_last_bar():
    """During PRE_MARKET, signal_index must be len(bars)-1 — no walk-back to REGULAR window."""
    settings = _settings()  # pre_market_entry_window_start=04:00, end=09:20
    # 8:00am ET = 12:00 UTC = pre-market, within PRE_MARKET entry window
    now = datetime(2026, 4, 28, 12, 0, tzinfo=timezone.utc)
    bar = _bar("AAPL", close=105.0, ts=datetime(2026, 4, 28, 12, 0, tzinfo=timezone.utc))  # 8am ET

    seen_signal_index: list = []

    def recording_evaluator(**kwargs) -> EntrySignal | None:
        seen_signal_index.append(kwargs["signal_index"])
        return EntrySignal(
            symbol="AAPL",
            signal_bar=kwargs["intraday_bars"][kwargs["signal_index"]],
            entry_level=105.1,
            relative_volume=2.0,
            stop_price=103.0,
            limit_price=105.2,
            initial_stop_price=103.0,
        )

    evaluate_cycle(
        settings=settings,
        now=now,
        equity=100_000.0,
        intraday_bars_by_symbol={"AAPL": [bar]},
        daily_bars_by_symbol={"AAPL": [bar]},
        open_positions=[],
        working_order_symbols=set(),
        traded_symbols_today=set(),
        entries_disabled=False,
        session_type=SessionType.PRE_MARKET,
        signal_evaluator=recording_evaluator,
    )
    assert seen_signal_index, "signal_evaluator must be called during PRE_MARKET"
    assert seen_signal_index[0] == 0, (
        "PRE_MARKET must use signal_index=len(bars)-1 (no REGULAR walk-back)"
    )


# ---------------------------------------------------------------------------
# Stale-signal staleness guard (Bug 1 fix)
# ---------------------------------------------------------------------------

def test_stale_ah_signal_rejected_when_bar_age_exceeds_threshold():
    """Signal bar older than EXTENDED_HOURS_SIGNAL_MAX_AGE_MINUTES must suppress the entry."""
    # Bar at 3:30pm ET (19:30 UTC), now at 6pm ET (22:00 UTC) = 150 min > 60 min threshold.
    settings = _settings(EXTENDED_HOURS_SIGNAL_MAX_AGE_MINUTES="60")
    now = datetime(2026, 4, 28, 22, 0, tzinfo=timezone.utc)
    old_bar = _bar("AAPL", close=105.0, ts=datetime(2026, 4, 28, 19, 30, tzinfo=timezone.utc))

    result = evaluate_cycle(
        settings=settings,
        now=now,
        equity=100_000.0,
        intraday_bars_by_symbol={"AAPL": [old_bar]},
        daily_bars_by_symbol={"AAPL": [old_bar]},
        open_positions=[],
        working_order_symbols=set(),
        traded_symbols_today=set(),
        entries_disabled=False,
        session_type=SessionType.AFTER_HOURS,
        signal_evaluator=lambda **kwargs: EntrySignal(
            symbol="AAPL",
            signal_bar=kwargs["intraday_bars"][kwargs["signal_index"]],
            entry_level=105.1,
            relative_volume=2.0,
            stop_price=103.0,
            limit_price=105.2,
            initial_stop_price=103.0,
        ),
    )
    entries = [i for i in result.intents if i.intent_type is CycleIntentType.ENTRY]
    assert entries == [], "Signal bar 150 min old must be rejected (threshold=60 min)"


def test_fresh_ah_signal_fires_within_threshold():
    """Signal bar within EXTENDED_HOURS_SIGNAL_MAX_AGE_MINUTES must not be rejected."""
    # Bar at 3:30pm ET (19:30 UTC), now at 4:15pm ET (20:15 UTC) = 45 min < 60 min threshold.
    settings = _settings(EXTENDED_HOURS_SIGNAL_MAX_AGE_MINUTES="60")
    now = datetime(2026, 4, 28, 20, 15, tzinfo=timezone.utc)
    fresh_bar = _bar("AAPL", close=105.0, ts=datetime(2026, 4, 28, 19, 30, tzinfo=timezone.utc))

    result = evaluate_cycle(
        settings=settings,
        now=now,
        equity=100_000.0,
        intraday_bars_by_symbol={"AAPL": [fresh_bar]},
        daily_bars_by_symbol={"AAPL": [fresh_bar]},
        open_positions=[],
        working_order_symbols=set(),
        traded_symbols_today=set(),
        entries_disabled=False,
        session_type=SessionType.AFTER_HOURS,
        signal_evaluator=lambda **kwargs: EntrySignal(
            symbol="AAPL",
            signal_bar=kwargs["intraday_bars"][kwargs["signal_index"]],
            entry_level=105.1,
            relative_volume=2.0,
            stop_price=103.0,
            limit_price=105.2,
            initial_stop_price=103.0,
        ),
    )
    entries = [i for i in result.intents if i.intent_type is CycleIntentType.ENTRY]
    assert entries, "Signal bar 45 min old must fire when threshold=60 min"


# ---------------------------------------------------------------------------
# Soft-stop EXIT during extended hours (Bug 2B fix)
# ---------------------------------------------------------------------------

def test_soft_stop_exit_emitted_when_close_breaches_stop_during_ah():
    """close <= stop_price during AFTER_HOURS must emit EXIT(stop_breach_extended_hours)."""
    settings = _settings()
    now = datetime(2026, 4, 28, 21, 0, tzinfo=timezone.utc)  # 5pm ET
    position = _position(stop_price=95.0)
    bar = _bar("AAPL", close=94.0, ts=now)  # close < stop_price

    result = evaluate_cycle(
        settings=settings,
        now=now,
        equity=100_000.0,
        intraday_bars_by_symbol={"AAPL": [bar]},
        daily_bars_by_symbol={},
        open_positions=[position],
        working_order_symbols=set(),
        traded_symbols_today=set(),
        entries_disabled=True,
        session_type=SessionType.AFTER_HOURS,
    )
    exits = [i for i in result.intents if i.intent_type is CycleIntentType.EXIT]
    assert len(exits) == 1
    assert exits[0].reason == "stop_breach_extended_hours"
    assert exits[0].limit_price == pytest.approx(round(94.0 * (1 - 0.001), 2), rel=1e-5)


def test_no_soft_stop_exit_when_close_above_stop_price_during_ah():
    """close > stop_price during AFTER_HOURS must not emit EXIT."""
    settings = _settings()
    now = datetime(2026, 4, 28, 21, 0, tzinfo=timezone.utc)  # 5pm ET
    position = _position(stop_price=95.0)
    bar = _bar("AAPL", close=100.0, ts=now)  # close > stop_price

    result = evaluate_cycle(
        settings=settings,
        now=now,
        equity=100_000.0,
        intraday_bars_by_symbol={"AAPL": [bar]},
        daily_bars_by_symbol={},
        open_positions=[position],
        working_order_symbols=set(),
        traded_symbols_today=set(),
        entries_disabled=True,
        session_type=SessionType.AFTER_HOURS,
    )
    exits = [i for i in result.intents if i.intent_type is CycleIntentType.EXIT]
    assert exits == [], "No EXIT emitted when price is above stop during extended hours"


def test_no_soft_stop_exit_when_stop_price_zero_during_ah():
    """stop_price=0 during AFTER_HOURS must not emit EXIT (position has no stop set)."""
    settings = _settings()
    now = datetime(2026, 4, 28, 21, 0, tzinfo=timezone.utc)  # 5pm ET
    position = _position(stop_price=0.0)
    bar = _bar("AAPL", close=50.0, ts=now)

    result = evaluate_cycle(
        settings=settings,
        now=now,
        equity=100_000.0,
        intraday_bars_by_symbol={"AAPL": [bar]},
        daily_bars_by_symbol={},
        open_positions=[position],
        working_order_symbols=set(),
        traded_symbols_today=set(),
        entries_disabled=True,
        session_type=SessionType.AFTER_HOURS,
    )
    exits = [i for i in result.intents if i.intent_type is CycleIntentType.EXIT]
    assert exits == [], "stop_price=0 must not trigger soft-stop EXIT"
