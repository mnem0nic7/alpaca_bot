from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from alpaca_bot.config import Settings
from alpaca_bot.domain import Bar, OpenPosition


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
        "ATR_PERIOD": "50",
    }
    values.update(overrides)
    return Settings.from_env(values)


def make_daily_bars(symbol: str = "AAPL") -> list[Bar]:
    start = datetime(2026, 3, 26, 20, 0, tzinfo=timezone.utc)
    return [
        Bar(
            symbol=symbol,
            timestamp=start + timedelta(days=index),
            open=89.0 + index,
            high=90.0 + index,
            low=88.0 + index,
            close=90.0 + index,
            volume=1_000_000 + index * 1000,
        )
        for index in range(20)
    ]


def make_breakout_intraday_bars(symbol: str = "AAPL") -> list[Bar]:
    start = datetime(2026, 4, 24, 14, 0, tzinfo=timezone.utc)
    bars: list[Bar] = []
    for offset in range(20):
        high = 108.5 + offset * 0.08
        close = high - 0.2
        bars.append(
            Bar(
                symbol=symbol,
                timestamp=start + timedelta(minutes=15 * offset),
                open=round(close - 0.1, 2),
                high=round(high, 2),
                low=round(close - 0.25, 2),
                close=round(close, 2),
                volume=1000 + offset * 10,
            )
        )
    bars[-1] = Bar(
        symbol=symbol,
        timestamp=bars[-1].timestamp,
        open=109.55,
        high=110.0,
        low=109.35,
        close=109.75,
        volume=1190,
    )
    bars.append(
        Bar(
            symbol=symbol,
            timestamp=datetime(2026, 4, 24, 19, 0, tzinfo=timezone.utc),
            open=109.8,
            high=111.0,
            low=109.7,
            close=110.8,
            volume=2000,
        )
    )
    return bars


def load_engine_api():
    from alpaca_bot.core.engine import CycleIntentType, evaluate_cycle

    return CycleIntentType, evaluate_cycle


def test_evaluate_cycle_emits_entry_intent_for_valid_breakout() -> None:
    CycleIntentType, evaluate_cycle = load_engine_api()

    result = evaluate_cycle(
        settings=make_settings(),
        now=datetime(2026, 4, 24, 19, 0, tzinfo=timezone.utc),
        equity=100000.0,
        intraday_bars_by_symbol={"AAPL": make_breakout_intraday_bars()},
        daily_bars_by_symbol={"AAPL": make_daily_bars()},
        open_positions=[],
        working_order_symbols=set(),
        traded_symbols_today=set(),
        entries_disabled=False,
    )

    assert [intent.intent_type for intent in result.intents] == [CycleIntentType.ENTRY]
    assert result.intents[0].symbol == "AAPL"
    assert result.intents[0].quantity == 44
    assert result.intents[0].stop_price == 111.01
    assert result.intents[0].limit_price == 111.12
    assert result.intents[0].initial_stop_price == 109.89


def test_evaluate_cycle_skips_entry_when_symbol_already_traded_today() -> None:
    _CycleIntentType, evaluate_cycle = load_engine_api()

    result = evaluate_cycle(
        settings=make_settings(),
        now=datetime(2026, 4, 24, 19, 0, tzinfo=timezone.utc),
        equity=100000.0,
        intraday_bars_by_symbol={"AAPL": make_breakout_intraday_bars()},
        daily_bars_by_symbol={"AAPL": make_daily_bars()},
        open_positions=[],
        working_order_symbols=set(),
        traded_symbols_today={("AAPL", date(2026, 4, 24))},
        entries_disabled=False,
    )

    assert result.intents == []


def test_evaluate_cycle_emits_stop_update_after_plus_one_r_without_loosening() -> None:
    CycleIntentType, evaluate_cycle = load_engine_api()
    latest_bar = Bar(
        symbol="AAPL",
        timestamp=datetime(2026, 4, 24, 19, 15, tzinfo=timezone.utc),
        open=111.80,
        high=112.40,
        low=111.70,
        close=112.10,
        volume=2400,
    )
    position = OpenPosition(
        symbol="AAPL",
        entry_timestamp=datetime(2026, 4, 24, 18, 45, tzinfo=timezone.utc),
        entry_price=111.02,
        quantity=45,
        entry_level=109.90,
        initial_stop_price=109.89,
        stop_price=109.89,
        trailing_active=False,
        highest_price=111.20,
    )

    result = evaluate_cycle(
        settings=make_settings(),
        now=latest_bar.timestamp,
        equity=100000.0,
        intraday_bars_by_symbol={"AAPL": [latest_bar]},
        daily_bars_by_symbol={"AAPL": make_daily_bars()},
        open_positions=[position],
        working_order_symbols=set(),
        traded_symbols_today=set(),
        entries_disabled=False,
    )

    assert [intent.intent_type for intent in result.intents] == [CycleIntentType.UPDATE_STOP]
    assert result.intents[0].symbol == "AAPL"
    assert result.intents[0].stop_price == 111.70


def test_evaluate_cycle_emits_eod_exit_for_open_position_after_flatten_time() -> None:
    CycleIntentType, evaluate_cycle = load_engine_api()
    latest_bar = Bar(
        symbol="AAPL",
        timestamp=datetime(2026, 4, 24, 20, 0, tzinfo=timezone.utc),
        open=112.20,
        high=112.50,
        low=112.0,
        close=112.30,
        volume=1800,
    )
    position = OpenPosition(
        symbol="AAPL",
        entry_timestamp=datetime(2026, 4, 24, 18, 45, tzinfo=timezone.utc),
        entry_price=111.02,
        quantity=45,
        entry_level=109.90,
        initial_stop_price=109.89,
        stop_price=111.70,
        trailing_active=True,
        highest_price=112.40,
    )

    result = evaluate_cycle(
        settings=make_settings(),
        now=latest_bar.timestamp,
        equity=100000.0,
        intraday_bars_by_symbol={"AAPL": [latest_bar]},
        daily_bars_by_symbol={"AAPL": make_daily_bars()},
        open_positions=[position],
        working_order_symbols=set(),
        traded_symbols_today=set(),
        entries_disabled=False,
    )

    assert [intent.intent_type for intent in result.intents] == [CycleIntentType.EXIT]
    assert result.intents[0].symbol == "AAPL"
    assert result.intents[0].reason == "eod_flatten"


def test_entries_disabled_still_produces_update_stop_for_profitable_position() -> None:
    CycleIntentType, evaluate_cycle = load_engine_api()
    latest_bar = Bar(
        symbol="AAPL",
        timestamp=datetime(2026, 4, 24, 19, 15, tzinfo=timezone.utc),
        open=111.80,
        high=112.40,
        low=111.70,
        close=112.10,
        volume=2400,
    )
    position = OpenPosition(
        symbol="AAPL",
        entry_timestamp=datetime(2026, 4, 24, 18, 45, tzinfo=timezone.utc),
        entry_price=111.02,
        quantity=45,
        entry_level=109.90,
        initial_stop_price=109.89,
        stop_price=109.89,
        trailing_active=False,
        highest_price=111.20,
    )

    result = evaluate_cycle(
        settings=make_settings(),
        now=latest_bar.timestamp,
        equity=100000.0,
        intraday_bars_by_symbol={"AAPL": [latest_bar]},
        daily_bars_by_symbol={"AAPL": make_daily_bars()},
        open_positions=[position],
        working_order_symbols=set(),
        traded_symbols_today=set(),
        entries_disabled=True,
    )

    intent_types = [intent.intent_type for intent in result.intents]
    assert CycleIntentType.UPDATE_STOP in intent_types
    assert CycleIntentType.ENTRY not in intent_types
    update_intent = next(i for i in result.intents if i.intent_type == CycleIntentType.UPDATE_STOP)
    assert update_intent.symbol == "AAPL"
    assert update_intent.stop_price == 111.70


def test_entries_disabled_still_produces_exit_for_position_past_flatten_time() -> None:
    CycleIntentType, evaluate_cycle = load_engine_api()
    latest_bar = Bar(
        symbol="AAPL",
        timestamp=datetime(2026, 4, 24, 20, 0, tzinfo=timezone.utc),
        open=112.20,
        high=112.50,
        low=112.0,
        close=112.30,
        volume=1800,
    )
    position = OpenPosition(
        symbol="AAPL",
        entry_timestamp=datetime(2026, 4, 24, 18, 45, tzinfo=timezone.utc),
        entry_price=111.02,
        quantity=45,
        entry_level=109.90,
        initial_stop_price=109.89,
        stop_price=111.70,
        trailing_active=True,
        highest_price=112.40,
    )

    result = evaluate_cycle(
        settings=make_settings(),
        now=latest_bar.timestamp,
        equity=100000.0,
        intraday_bars_by_symbol={"AAPL": [latest_bar]},
        daily_bars_by_symbol={"AAPL": make_daily_bars()},
        open_positions=[position],
        working_order_symbols=set(),
        traded_symbols_today=set(),
        entries_disabled=True,
    )

    intent_types = [intent.intent_type for intent in result.intents]
    assert CycleIntentType.EXIT in intent_types
    assert CycleIntentType.ENTRY not in intent_types
    exit_intent = next(i for i in result.intents if i.intent_type == CycleIntentType.EXIT)
    assert exit_intent.symbol == "AAPL"
    assert exit_intent.reason == "eod_flatten"


def test_entries_disabled_produces_no_entry_intents_even_when_signals_exist() -> None:
    _CycleIntentType, evaluate_cycle = load_engine_api()
    CycleIntentType = _CycleIntentType

    result = evaluate_cycle(
        settings=make_settings(),
        now=datetime(2026, 4, 24, 19, 0, tzinfo=timezone.utc),
        equity=100000.0,
        intraday_bars_by_symbol={"AAPL": make_breakout_intraday_bars()},
        daily_bars_by_symbol={"AAPL": make_daily_bars()},
        open_positions=[],
        working_order_symbols=set(),
        traded_symbols_today=set(),
        entries_disabled=True,
    )

    assert all(intent.intent_type != CycleIntentType.ENTRY for intent in result.intents)


# ---------------------------------------------------------------------------
# Fix #4: flatten_complete flag suppresses EXIT intents
# ---------------------------------------------------------------------------


def test_evaluate_cycle_emits_no_exits_when_flatten_already_complete() -> None:
    """When session_state.flatten_complete is True, evaluate_cycle must not emit
    any EXIT intents — prevents duplicate market orders when the trade stream
    is down and the fill hasn't been recorded yet."""
    from alpaca_bot.storage import DailySessionState
    from alpaca_bot.config import TradingMode

    CycleIntentType, evaluate_cycle = load_engine_api()

    # Past flatten_time (15:45 ET → 19:45 UTC; 20:00 UTC is well past it)
    past_flatten = datetime(2026, 4, 24, 20, 0, tzinfo=timezone.utc)
    latest_bar = Bar(
        symbol="AAPL",
        timestamp=past_flatten,
        open=112.20,
        high=112.50,
        low=112.0,
        close=112.30,
        volume=1800,
    )
    position = OpenPosition(
        symbol="AAPL",
        entry_timestamp=datetime(2026, 4, 24, 18, 45, tzinfo=timezone.utc),
        entry_price=111.02,
        quantity=45,
        entry_level=109.90,
        initial_stop_price=109.89,
        stop_price=111.70,
        trailing_active=True,
        highest_price=112.40,
    )
    session_state = DailySessionState(
        session_date=date(2026, 4, 24),
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        entries_disabled=True,
        flatten_complete=True,
        updated_at=past_flatten,
    )

    result = evaluate_cycle(
        settings=make_settings(),
        now=past_flatten,
        equity=100000.0,
        intraday_bars_by_symbol={"AAPL": [latest_bar]},
        daily_bars_by_symbol={"AAPL": make_daily_bars()},
        open_positions=[position],
        working_order_symbols=set(),
        traded_symbols_today=set(),
        entries_disabled=True,
        session_state=session_state,
    )

    exit_intents = [i for i in result.intents if i.intent_type == CycleIntentType.EXIT]
    assert exit_intents == [], (
        f"Expected no EXIT intents when flatten_complete=True, got: {exit_intents}"
    )


def test_evaluate_cycle_emits_exits_when_flatten_not_complete() -> None:
    """Control: when session_state.flatten_complete is False (default), EXIT
    intents are still emitted past flatten_time."""
    from alpaca_bot.storage import DailySessionState
    from alpaca_bot.config import TradingMode

    CycleIntentType, evaluate_cycle = load_engine_api()

    past_flatten = datetime(2026, 4, 24, 20, 0, tzinfo=timezone.utc)
    latest_bar = Bar(
        symbol="AAPL",
        timestamp=past_flatten,
        open=112.20,
        high=112.50,
        low=112.0,
        close=112.30,
        volume=1800,
    )
    position = OpenPosition(
        symbol="AAPL",
        entry_timestamp=datetime(2026, 4, 24, 18, 45, tzinfo=timezone.utc),
        entry_price=111.02,
        quantity=45,
        entry_level=109.90,
        initial_stop_price=109.89,
        stop_price=111.70,
        trailing_active=True,
        highest_price=112.40,
    )
    session_state = DailySessionState(
        session_date=date(2026, 4, 24),
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        entries_disabled=True,
        flatten_complete=False,
        updated_at=past_flatten,
    )

    result = evaluate_cycle(
        settings=make_settings(),
        now=past_flatten,
        equity=100000.0,
        intraday_bars_by_symbol={"AAPL": [latest_bar]},
        daily_bars_by_symbol={"AAPL": make_daily_bars()},
        open_positions=[position],
        working_order_symbols=set(),
        traded_symbols_today=set(),
        entries_disabled=True,
        session_state=session_state,
    )

    exit_intents = [i for i in result.intents if i.intent_type == CycleIntentType.EXIT]
    assert len(exit_intents) == 1, (
        f"Expected one EXIT intent when flatten_complete=False, got: {exit_intents}"
    )


# ---------------------------------------------------------------------------
# flatten_all=True path
# ---------------------------------------------------------------------------


def make_open_position(symbol: str) -> OpenPosition:
    return OpenPosition(
        symbol=symbol,
        entry_timestamp=datetime(2026, 4, 24, 18, 0, tzinfo=timezone.utc),
        entry_price=100.0,
        quantity=10,
        entry_level=99.0,
        initial_stop_price=98.0,
        stop_price=98.0,
        trailing_active=False,
        highest_price=102.0,
    )


def test_flatten_all_emits_exit_for_every_open_position() -> None:
    CycleIntentType, evaluate_cycle = load_engine_api()
    now = datetime(2026, 4, 24, 19, 0, tzinfo=timezone.utc)

    result = evaluate_cycle(
        settings=make_settings(),
        now=now,
        equity=100000.0,
        intraday_bars_by_symbol={"AAPL": make_breakout_intraday_bars()},
        daily_bars_by_symbol={"AAPL": make_daily_bars()},
        open_positions=[make_open_position("AAPL"), make_open_position("MSFT")],
        working_order_symbols=set(),
        traded_symbols_today=set(),
        entries_disabled=False,
        flatten_all=True,
    )

    assert all(i.intent_type == CycleIntentType.EXIT for i in result.intents)
    assert {i.symbol for i in result.intents} == {"AAPL", "MSFT"}
    assert all(i.reason == "loss_limit_flatten" for i in result.intents)


def test_flatten_all_with_no_open_positions_returns_empty_intents() -> None:
    _CycleIntentType, evaluate_cycle = load_engine_api()
    now = datetime(2026, 4, 24, 19, 0, tzinfo=timezone.utc)

    result = evaluate_cycle(
        settings=make_settings(),
        now=now,
        equity=100000.0,
        intraday_bars_by_symbol={"AAPL": make_breakout_intraday_bars()},
        daily_bars_by_symbol={"AAPL": make_daily_bars()},
        open_positions=[],
        working_order_symbols=set(),
        traded_symbols_today=set(),
        entries_disabled=False,
        flatten_all=True,
    )

    assert result.intents == []


# ---------------------------------------------------------------------------
# global_open_count cross-strategy slot enforcement
# ---------------------------------------------------------------------------


def test_global_open_count_blocks_entry_when_slots_exhausted() -> None:
    """When global_open_count >= max_open_positions, no ENTRY intents are emitted."""
    _CycleIntentType, evaluate_cycle = load_engine_api()
    settings = make_settings(MAX_OPEN_POSITIONS="3")
    now = datetime(2026, 4, 24, 19, 0, tzinfo=timezone.utc)

    result = evaluate_cycle(
        settings=settings,
        now=now,
        equity=100000.0,
        intraday_bars_by_symbol={"AAPL": make_breakout_intraday_bars()},
        daily_bars_by_symbol={"AAPL": make_daily_bars()},
        open_positions=[],
        working_order_symbols=set(),
        traded_symbols_today=set(),
        entries_disabled=False,
        # Simulate two other strategies already using all 3 slots
        global_open_count=3,
    )

    assert all(i.intent_type != _CycleIntentType.ENTRY for i in result.intents)


def test_global_open_count_allows_partial_slot_usage() -> None:
    """When global_open_count leaves one slot free, exactly one ENTRY can fire."""
    CycleIntentType, evaluate_cycle = load_engine_api()
    settings = make_settings(MAX_OPEN_POSITIONS="3")
    now = datetime(2026, 4, 24, 19, 0, tzinfo=timezone.utc)

    result = evaluate_cycle(
        settings=settings,
        now=now,
        equity=100000.0,
        intraday_bars_by_symbol={"AAPL": make_breakout_intraday_bars()},
        daily_bars_by_symbol={"AAPL": make_daily_bars()},
        open_positions=[],
        working_order_symbols=set(),
        traded_symbols_today=set(),
        entries_disabled=False,
        global_open_count=2,  # 1 slot remaining
    )

    entry_intents = [i for i in result.intents if i.intent_type == CycleIntentType.ENTRY]
    assert len(entry_intents) <= 1


def test_zero_equity_does_not_raise_with_open_positions() -> None:
    """evaluate_cycle must handle equity=0 gracefully (no ZeroDivisionError)."""
    _CycleIntentType, evaluate_cycle = load_engine_api()
    now = datetime(2026, 4, 24, 19, 0, tzinfo=timezone.utc)

    result = evaluate_cycle(
        settings=make_settings(),
        now=now,
        equity=0.0,
        intraday_bars_by_symbol={"AAPL": make_breakout_intraday_bars()},
        daily_bars_by_symbol={"AAPL": make_daily_bars()},
        open_positions=[make_open_position("AAPL")],
        working_order_symbols=set(),
        traded_symbols_today=set(),
        entries_disabled=False,
    )

    assert result is not None
