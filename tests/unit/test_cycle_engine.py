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
        "ATR_PERIOD": "14",
    }
    values.update(overrides)
    return Settings.from_env(values)


def make_daily_bars(symbol: str = "AAPL") -> list[Bar]:
    # 21 bars so daily_trend_filter_passes works with sma_period=20 (needs period+1 bars
    # to exclude the potentially-partial last bar from the SMA window).
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
        for index in range(21)
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
    # stop_price anchors to breakout_level (110.0) + buffer, not signal_bar.high (111.0).
    assert result.intents[0].stop_price == 110.01
    assert result.intents[0].limit_price == 110.12
    assert result.intents[0].initial_stop_price == 107.0
    assert result.intents[0].quantity == 45


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


def test_evaluate_cycle_emits_exits_for_late_positions_when_flatten_already_complete() -> None:
    """flatten_complete=True must NOT suppress EXIT intents for positions that
    exist after flatten_time. A position present here means it arrived after the
    initial flatten (e.g., late fill from a restart cascade). Duplicate broker
    submissions are prevented by _execute_exit's active_exit_orders idempotency guard."""
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
    assert len(exit_intents) == 1, (
        f"Expected EXIT intent for position even when flatten_complete=True, got: {exit_intents}"
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


def test_evaluate_cycle_skips_entry_when_position_size_rounds_to_zero() -> None:
    """With tiny equity, calculate_position_size returns 0 → quantity < 1 guard
    prevents an ENTRY intent from being emitted."""
    _CycleIntentType, evaluate_cycle = load_engine_api()
    now = datetime(2026, 4, 24, 19, 0, tzinfo=timezone.utc)

    result = evaluate_cycle(
        settings=make_settings(),
        now=now,
        equity=0.01,  # so tiny that position size rounds to 0 shares
        intraday_bars_by_symbol={"AAPL": make_breakout_intraday_bars()},
        daily_bars_by_symbol={"AAPL": make_daily_bars()},
        open_positions=[],
        working_order_symbols=set(),
        traded_symbols_today=set(),
        entries_disabled=False,
    )

    entry_intents = [i for i in result.intents if i.intent_type == _CycleIntentType.ENTRY]
    assert entry_intents == [], "Expected no ENTRY intent when position size is zero"


def test_evaluate_cycle_skips_overexposed_candidate_but_selects_next_fitting_symbol() -> None:
    """The exposure cap must skip candidates that would push portfolio exposure over the
    limit, but continue iterating so a cheaper candidate can still be selected."""
    CycleIntentType, evaluate_cycle = load_engine_api()
    now = datetime(2026, 4, 24, 19, 0, tzinfo=timezone.utc)

    # AAPL already has a valid breakout signal; add MSFT with its own bars.
    # Set MAX_PORTFOLIO_EXPOSURE_PCT so that only one entry fits, and rank MSFT first
    # (higher relative volume) but make it expensive enough to exceed the cap.
    # AAPL bars: limit_price ~$111, quantity ~44 → exposure ≈ 4.9% at 100k equity.
    # MSFT bars: make limit_price $2000 → even 1 share = 2%, but position_size
    # calc on 0.25% risk with $2000-$1990 stop = 25 shares → 50% exposure (too large).
    msft_start = datetime(2026, 4, 24, 14, 0, tzinfo=timezone.utc)
    msft_daily = [
        Bar(symbol="MSFT", timestamp=msft_start + timedelta(days=i),
            open=1990.0, high=2010.0 + i, low=1985.0, close=2000.0 + i, volume=1_000_000)
        for i in range(25)
    ]
    msft_intraday: list[Bar] = []
    for offset in range(20):
        h = 2008.5 + offset * 0.08
        c = h - 0.2
        msft_intraday.append(Bar(
            symbol="MSFT", timestamp=msft_start + timedelta(minutes=15 * offset),
            open=round(c - 0.1, 2), high=round(h, 2),
            low=round(c - 0.25, 2), close=round(c, 2), volume=5000 + offset * 10,
        ))
    # Signal bar: closes above prior range high, high relative volume
    msft_intraday[-1] = Bar(
        symbol="MSFT", timestamp=msft_intraday[-1].timestamp,
        open=2009.55, high=2012.0, low=2009.35, close=2011.75, volume=9000,
    )
    msft_intraday.append(Bar(
        symbol="MSFT", timestamp=datetime(2026, 4, 24, 19, 0, tzinfo=timezone.utc),
        open=2012.0, high=2015.0, low=2011.5, close=2014.0, volume=15000,
    ))

    # Low exposure cap of 5% — AAPL at ~4.9% fits; MSFT at ~50%+ does not.
    settings = make_settings(MAX_PORTFOLIO_EXPOSURE_PCT="0.05", MAX_OPEN_POSITIONS="3")

    result = evaluate_cycle(
        settings=settings,
        now=now,
        equity=100_000.0,
        intraday_bars_by_symbol={
            "AAPL": make_breakout_intraday_bars(),
            "MSFT": msft_intraday,
        },
        daily_bars_by_symbol={
            "AAPL": make_daily_bars(),
            "MSFT": msft_daily,
        },
        open_positions=[],
        working_order_symbols=set(),
        traded_symbols_today=set(),
        entries_disabled=False,
    )

    entry_intents = [i for i in result.intents if i.intent_type == CycleIntentType.ENTRY]
    symbols_selected = {i.symbol for i in entry_intents}
    # AAPL must be selected (fits within 5%); MSFT must be skipped (overexposed).
    assert "AAPL" in symbols_selected, "AAPL should be selected — fits within exposure cap"
    assert "MSFT" not in symbols_selected, "MSFT should be skipped — would exceed exposure cap"


def test_evaluate_cycle_emits_no_entry_when_signal_evaluator_returns_none() -> None:
    CycleIntentType, evaluate_cycle = load_engine_api()
    from datetime import datetime, timezone
    result = evaluate_cycle(
        settings=make_settings(),
        now=datetime(2026, 4, 24, 19, 0, tzinfo=timezone.utc),
        equity=100_000.0,
        intraday_bars_by_symbol={"AAPL": make_breakout_intraday_bars()},
        daily_bars_by_symbol={"AAPL": make_daily_bars()},
        open_positions=[],
        working_order_symbols=set(),
        traded_symbols_today=set(),
        entries_disabled=False,
        signal_evaluator=lambda **_: None,
    )
    entry_intents = [i for i in result.intents if i.intent_type == CycleIntentType.ENTRY]
    assert entry_intents == [], "No entries expected when signal_evaluator always returns None"


def test_evaluate_cycle_emits_no_entry_when_signal_has_inverted_stop() -> None:
    """Guard at engine.py prevents entry when initial_stop_price >= limit_price."""
    from types import SimpleNamespace
    CycleIntentType, evaluate_cycle = load_engine_api()
    from datetime import datetime, timezone
    now = datetime(2026, 4, 24, 19, 0, tzinfo=timezone.utc)

    def inverted_signal_evaluator(**kwargs):
        return SimpleNamespace(
            initial_stop_price=110.0,
            limit_price=110.0,  # stop == entry — invalid
            stop_price=110.0,
            entry_level=109.0,
            signal_bar=SimpleNamespace(timestamp=now, close=110.0),
            relative_volume=2.0,
            option_contract=None,
        )

    result = evaluate_cycle(
        settings=make_settings(),
        now=now,
        equity=100_000.0,
        intraday_bars_by_symbol={"AAPL": make_breakout_intraday_bars()},
        daily_bars_by_symbol={"AAPL": make_daily_bars()},
        open_positions=[],
        working_order_symbols=set(),
        traded_symbols_today=set(),
        entries_disabled=False,
        signal_evaluator=inverted_signal_evaluator,
    )
    entry_intents = [i for i in result.intents if i.intent_type == CycleIntentType.ENTRY]
    assert entry_intents == [], "No entries expected when stop_price >= limit_price"


def test_eod_flatten_uses_now_not_bar_timestamp_for_time_check() -> None:
    """EOD flatten must use the cycle wall-clock `now`, not the latest bar's timestamp.

    If bars are stale (or unavailable), using bar.timestamp would miss the flatten window.
    Using `now` ensures positions are always exited at EOD regardless of bar staleness.
    """
    CycleIntentType, evaluate_cycle = load_engine_api()

    # now is past flatten time (15:45 ET = 19:45 UTC)
    past_flatten_now = datetime(2026, 4, 24, 20, 0, tzinfo=timezone.utc)
    # Bar timestamp is before flatten time (would NOT trigger flatten if used instead of now)
    early_bar = Bar(
        symbol="AAPL",
        timestamp=datetime(2026, 4, 24, 18, 0, tzinfo=timezone.utc),
        open=110.0, high=111.0, low=109.5, close=110.5, volume=1500,
    )
    position = OpenPosition(
        symbol="AAPL",
        entry_timestamp=datetime(2026, 4, 24, 16, 0, tzinfo=timezone.utc),
        entry_price=110.0, quantity=45, entry_level=109.9,
        initial_stop_price=109.89, stop_price=109.89,
        trailing_active=False, highest_price=110.0,
    )

    result = evaluate_cycle(
        settings=make_settings(),
        now=past_flatten_now,
        equity=100_000.0,
        intraday_bars_by_symbol={"AAPL": [early_bar]},
        daily_bars_by_symbol={"AAPL": make_daily_bars()},
        open_positions=[position],
        working_order_symbols=set(),
        traded_symbols_today=set(),
        entries_disabled=False,
    )

    exit_intents = [i for i in result.intents if i.intent_type == CycleIntentType.EXIT]
    assert len(exit_intents) == 1, "EOD flatten must fire based on now, not bar.timestamp"
    assert exit_intents[0].reason == "eod_flatten"


def test_eod_flatten_fires_even_when_no_bars_available_for_symbol() -> None:
    """Positions must be exited at EOD even when the symbol has no bars.

    Without bars, the engine previously skipped the symbol entirely, leaving
    positions open past the flatten window.
    """
    CycleIntentType, evaluate_cycle = load_engine_api()

    past_flatten_now = datetime(2026, 4, 24, 20, 0, tzinfo=timezone.utc)
    position = OpenPosition(
        symbol="AAPL",
        entry_timestamp=datetime(2026, 4, 24, 16, 0, tzinfo=timezone.utc),
        entry_price=110.0, quantity=45, entry_level=109.9,
        initial_stop_price=109.89, stop_price=109.89,
        trailing_active=False, highest_price=110.0,
    )

    result = evaluate_cycle(
        settings=make_settings(),
        now=past_flatten_now,
        equity=100_000.0,
        intraday_bars_by_symbol={},  # no bars for AAPL
        daily_bars_by_symbol={"AAPL": make_daily_bars()},
        open_positions=[position],
        working_order_symbols=set(),
        traded_symbols_today=set(),
        entries_disabled=False,
    )

    exit_intents = [i for i in result.intents if i.intent_type == CycleIntentType.EXIT]
    assert len(exit_intents) == 1, "EOD flatten must emit EXIT even without bars"
    assert exit_intents[0].symbol == "AAPL"
    assert exit_intents[0].reason == "eod_flatten"


def test_stale_bar_suppresses_entry_signal() -> None:
    """Entry must be skipped when the latest bar is older than 2× timeframe_minutes.

    If the data feed stalls, the last bar from the previous cycle would still
    pass the session-time check (bar.timestamp is within the window), but it
    should not trigger a new order because the price data is stale.
    """
    CycleIntentType, evaluate_cycle = load_engine_api()

    # Bars end at 14:45 UTC; now is 15:30 UTC — 45 min gap > 2×15 = 30 min threshold
    stale_bars = make_breakout_intraday_bars()
    stale_bars[-1] = Bar(
        symbol="AAPL",
        timestamp=datetime(2026, 4, 24, 14, 45, tzinfo=timezone.utc),
        open=109.8,
        high=111.0,
        low=109.7,
        close=110.8,
        volume=2000,
    )
    now = datetime(2026, 4, 24, 15, 30, tzinfo=timezone.utc)

    result = evaluate_cycle(
        settings=make_settings(),
        now=now,
        equity=100_000.0,
        intraday_bars_by_symbol={"AAPL": stale_bars},
        daily_bars_by_symbol={"AAPL": make_daily_bars()},
        open_positions=[],
        working_order_symbols=set(),
        traded_symbols_today=set(),
        entries_disabled=False,
    )

    entry_intents = [i for i in result.intents if i.intent_type == CycleIntentType.ENTRY]
    assert entry_intents == [], "Stale bar must not trigger an entry order"


def test_stale_bar_suppresses_trailing_stop_update() -> None:
    """Trailing-stop updates must be skipped when the latest bar is stale.

    An UPDATE_STOP based on old high/low data could lock in a worse stop than
    the live market warrants; suppress it until fresh data arrives.
    """
    CycleIntentType, evaluate_cycle = load_engine_api()

    # Position is in profit and would normally trigger trailing-stop update
    position = OpenPosition(
        symbol="AAPL",
        entry_timestamp=datetime(2026, 4, 24, 14, 0, tzinfo=timezone.utc),
        entry_price=110.0,
        quantity=45,
        entry_level=109.9,
        initial_stop_price=109.89,
        stop_price=109.89,
        trailing_active=True,
        highest_price=112.0,
    )
    # Bar is 45 min old (> 2×15 = 30 min threshold)
    stale_bar = Bar(
        symbol="AAPL",
        timestamp=datetime(2026, 4, 24, 14, 45, tzinfo=timezone.utc),
        open=112.0,
        high=113.0,  # high enough to trigger trailing stop update
        low=111.5,
        close=112.5,
        volume=2000,
    )
    now = datetime(2026, 4, 24, 15, 30, tzinfo=timezone.utc)

    result = evaluate_cycle(
        settings=make_settings(),
        now=now,
        equity=100_000.0,
        intraday_bars_by_symbol={"AAPL": [stale_bar]},
        daily_bars_by_symbol={"AAPL": make_daily_bars()},
        open_positions=[position],
        working_order_symbols=set(),
        traded_symbols_today=set(),
        entries_disabled=False,
    )

    update_intents = [i for i in result.intents if i.intent_type == CycleIntentType.UPDATE_STOP]
    assert update_intents == [], "Stale bar must not trigger a trailing-stop update"


# ── ATR trailing stop ────────────────────────────────────────────────────────


def test_trailing_stop_disabled_uses_bar_low() -> None:
    """TRAILING_STOP_ATR_MULTIPLIER=0.0 → original bar.low behavior unchanged."""
    CycleIntentType, evaluate_cycle = load_engine_api()
    # bar.high=112.40 >= entry(111.02) + 1.0*risk(1.13) = 112.15 → trigger met
    # multiplier=0 → bar.low=111.70
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
    )
    result = evaluate_cycle(
        settings=make_settings(TRAILING_STOP_ATR_MULTIPLIER="0.0"),
        now=latest_bar.timestamp,
        equity=100_000.0,
        intraday_bars_by_symbol={"AAPL": [latest_bar]},
        daily_bars_by_symbol={"AAPL": make_daily_bars()},
        open_positions=[position],
        working_order_symbols=set(),
        traded_symbols_today=set(),
        entries_disabled=True,
    )
    assert [i.intent_type for i in result.intents] == [CycleIntentType.UPDATE_STOP]
    assert result.intents[0].stop_price == 111.70


def test_trailing_stop_uses_atr_distance_from_bar_high() -> None:
    """ATR=2.0, multiplier=1.5 → stop = bar.high - 3.0 = 113.0."""
    CycleIntentType, evaluate_cycle = load_engine_api()
    # entry=110.0, initial_stop=108.5, risk=1.5, trigger at 111.5
    # bar.high=116.0 → candidate=116.0-3.0=113.0
    # new_stop = max(108.5, 110.0, 113.0) = 113.0
    latest_bar = Bar(
        symbol="AAPL",
        timestamp=datetime(2026, 4, 24, 19, 15, tzinfo=timezone.utc),
        open=115.0,
        high=116.0,
        low=114.5,
        close=115.8,
        volume=3000,
    )
    position = OpenPosition(
        symbol="AAPL",
        entry_timestamp=datetime(2026, 4, 24, 18, 45, tzinfo=timezone.utc),
        entry_price=110.0,
        quantity=50,
        entry_level=109.0,
        initial_stop_price=108.5,
        stop_price=108.5,
    )
    result = evaluate_cycle(
        settings=make_settings(
            TRAILING_STOP_ATR_MULTIPLIER="1.5",
            TRAILING_STOP_PROFIT_TRIGGER_R="1.0",
        ),
        now=latest_bar.timestamp,
        equity=100_000.0,
        intraday_bars_by_symbol={"AAPL": [latest_bar]},
        daily_bars_by_symbol={"AAPL": make_daily_bars()},
        open_positions=[position],
        working_order_symbols=set(),
        traded_symbols_today=set(),
        entries_disabled=True,
    )
    assert [i.intent_type for i in result.intents] == [CycleIntentType.UPDATE_STOP]
    assert result.intents[0].stop_price == 113.0


def test_trailing_stop_never_regresses() -> None:
    """Trailing candidate below existing stop → no UPDATE_STOP emitted."""
    CycleIntentType, evaluate_cycle = load_engine_api()
    # stop already at 114.0; bar.high=115.0 → candidate=112.0 < 114.0 → no intent
    latest_bar = Bar(
        symbol="AAPL",
        timestamp=datetime(2026, 4, 24, 19, 15, tzinfo=timezone.utc),
        open=114.5,
        high=115.0,
        low=113.8,
        close=114.2,
        volume=2500,
    )
    position = OpenPosition(
        symbol="AAPL",
        entry_timestamp=datetime(2026, 4, 24, 18, 45, tzinfo=timezone.utc),
        entry_price=110.0,
        quantity=50,
        entry_level=109.0,
        initial_stop_price=108.5,
        stop_price=114.0,
    )
    result = evaluate_cycle(
        settings=make_settings(TRAILING_STOP_ATR_MULTIPLIER="1.5"),
        now=latest_bar.timestamp,
        equity=100_000.0,
        intraday_bars_by_symbol={"AAPL": [latest_bar]},
        daily_bars_by_symbol={"AAPL": make_daily_bars()},
        open_positions=[position],
        working_order_symbols=set(),
        traded_symbols_today=set(),
        entries_disabled=True,
    )
    assert result.intents == []


def test_trailing_stop_respects_breakeven_floor() -> None:
    """When ATR-candidate < entry_price, stop moves to entry_price (break-even floor)."""
    CycleIntentType, evaluate_cycle = load_engine_api()
    # entry=110.0, risk=1.5, trigger at 111.5
    # bar.high=111.5 → candidate=111.5-3.0=108.5 < entry=110.0
    # new_stop = max(108.5, 110.0, 108.5) = 110.0
    latest_bar = Bar(
        symbol="AAPL",
        timestamp=datetime(2026, 4, 24, 19, 15, tzinfo=timezone.utc),
        open=110.8,
        high=111.5,
        low=110.5,
        close=111.0,
        volume=1800,
    )
    position = OpenPosition(
        symbol="AAPL",
        entry_timestamp=datetime(2026, 4, 24, 18, 45, tzinfo=timezone.utc),
        entry_price=110.0,
        quantity=50,
        entry_level=109.0,
        initial_stop_price=108.5,
        stop_price=108.5,
    )
    result = evaluate_cycle(
        settings=make_settings(TRAILING_STOP_ATR_MULTIPLIER="1.5"),
        now=latest_bar.timestamp,
        equity=100_000.0,
        intraday_bars_by_symbol={"AAPL": [latest_bar]},
        daily_bars_by_symbol={"AAPL": make_daily_bars()},
        open_positions=[position],
        working_order_symbols=set(),
        traded_symbols_today=set(),
        entries_disabled=True,
    )
    assert [i.intent_type for i in result.intents] == [CycleIntentType.UPDATE_STOP]
    assert result.intents[0].stop_price == 110.0


def test_trailing_stop_atr_unavailable_falls_back_to_bar_low() -> None:
    """Fewer daily bars than ATR period → calculate_atr returns None → bar.low fallback."""
    CycleIntentType, evaluate_cycle = load_engine_api()
    # ATR_PERIOD=14 needs 15 bars; provide only 5 → None
    short_daily_bars = make_daily_bars()[:5]
    latest_bar = Bar(
        symbol="AAPL",
        timestamp=datetime(2026, 4, 24, 19, 15, tzinfo=timezone.utc),
        open=115.0,
        high=116.0,
        low=114.0,
        close=115.5,
        volume=2800,
    )
    position = OpenPosition(
        symbol="AAPL",
        entry_timestamp=datetime(2026, 4, 24, 18, 45, tzinfo=timezone.utc),
        entry_price=110.0,
        quantity=50,
        entry_level=109.0,
        initial_stop_price=108.5,
        stop_price=108.5,
    )
    result = evaluate_cycle(
        settings=make_settings(TRAILING_STOP_ATR_MULTIPLIER="1.5"),
        now=latest_bar.timestamp,
        equity=100_000.0,
        intraday_bars_by_symbol={"AAPL": [latest_bar]},
        daily_bars_by_symbol={"AAPL": short_daily_bars},
        open_positions=[position],
        working_order_symbols=set(),
        traded_symbols_today=set(),
        entries_disabled=True,
    )
    # Falls back to bar.low=114.0 > entry=110.0 → stop=114.0
    assert [i.intent_type for i in result.intents] == [CycleIntentType.UPDATE_STOP]
    assert result.intents[0].stop_price == 114.0


def test_trailing_stop_profit_trigger_r_controls_activation() -> None:
    """TRAILING_STOP_PROFIT_TRIGGER_R=2.0 → no trailing until price is 2R above entry."""
    CycleIntentType, evaluate_cycle = load_engine_api()
    # entry=110.0, risk=1.5, trigger at 110.0+2.0*1.5=113.0
    # bar.high=112.0 < 113.0 → no intent emitted
    latest_bar = Bar(
        symbol="AAPL",
        timestamp=datetime(2026, 4, 24, 19, 15, tzinfo=timezone.utc),
        open=111.5,
        high=112.0,
        low=111.0,
        close=111.8,
        volume=2000,
    )
    position = OpenPosition(
        symbol="AAPL",
        entry_timestamp=datetime(2026, 4, 24, 18, 45, tzinfo=timezone.utc),
        entry_price=110.0,
        quantity=50,
        entry_level=109.0,
        initial_stop_price=108.5,
        stop_price=108.5,
    )
    result = evaluate_cycle(
        settings=make_settings(
            TRAILING_STOP_ATR_MULTIPLIER="1.5",
            TRAILING_STOP_PROFIT_TRIGGER_R="2.0",
        ),
        now=latest_bar.timestamp,
        equity=100_000.0,
        intraday_bars_by_symbol={"AAPL": [latest_bar]},
        daily_bars_by_symbol={"AAPL": make_daily_bars()},
        open_positions=[position],
        working_order_symbols=set(),
        traded_symbols_today=set(),
        entries_disabled=True,
    )
    assert result.intents == []


# ── evaluate_cycle symbols param ─────────────────────────────────────────────


def test_evaluate_cycle_respects_symbols_param_override() -> None:
    """symbols param limits evaluation to the given set, ignoring settings.symbols."""
    CycleIntentType, evaluate_cycle = load_engine_api()

    # AAPL has a valid breakout signal; MSFT is NOT in the symbols override
    result = evaluate_cycle(
        settings=make_settings(SYMBOLS="AAPL,MSFT,SPY"),
        now=datetime(2026, 4, 24, 19, 0, tzinfo=timezone.utc),
        equity=100000.0,
        intraday_bars_by_symbol={
            "AAPL": make_breakout_intraday_bars("AAPL"),
        },
        daily_bars_by_symbol={
            "AAPL": make_daily_bars("AAPL"),
        },
        open_positions=[],
        working_order_symbols=set(),
        traded_symbols_today=set(),
        entries_disabled=False,
        symbols=("AAPL",),
    )

    assert any(i.symbol == "AAPL" for i in result.intents)


def test_evaluate_cycle_symbols_none_falls_back_to_settings() -> None:
    """Passing symbols=None uses settings.symbols (backward compat)."""
    CycleIntentType, evaluate_cycle = load_engine_api()

    result = evaluate_cycle(
        settings=make_settings(SYMBOLS="AAPL,MSFT,SPY"),
        now=datetime(2026, 4, 24, 19, 0, tzinfo=timezone.utc),
        equity=100000.0,
        intraday_bars_by_symbol={"AAPL": make_breakout_intraday_bars()},
        daily_bars_by_symbol={"AAPL": make_daily_bars()},
        open_positions=[],
        working_order_symbols=set(),
        traded_symbols_today=set(),
        entries_disabled=False,
        symbols=None,
    )

    # Same result as not passing symbols at all
    assert any(i.symbol == "AAPL" for i in result.intents)


def test_evaluate_cycle_symbols_param_excludes_symbols_not_in_list() -> None:
    """Symbols not in the watchlist override are not evaluated even if bars exist."""
    _CycleIntentType, evaluate_cycle = load_engine_api()

    # Pass MSFT as the only watchlist symbol, but only AAPL has bars with a signal
    result = evaluate_cycle(
        settings=make_settings(SYMBOLS="AAPL,MSFT"),
        now=datetime(2026, 4, 24, 19, 0, tzinfo=timezone.utc),
        equity=100000.0,
        intraday_bars_by_symbol={"AAPL": make_breakout_intraday_bars("AAPL")},
        daily_bars_by_symbol={"AAPL": make_daily_bars("AAPL")},
        open_positions=[],
        working_order_symbols=set(),
        traded_symbols_today=set(),
        entries_disabled=False,
        symbols=("MSFT",),  # only MSFT — AAPL excluded
    )

    # No intents because AAPL is excluded and MSFT has no bars
    assert result.intents == []


# ---------------------------------------------------------------------------
# Deduplication guards
# ---------------------------------------------------------------------------


def test_flatten_all_deduplicates_exits_when_same_symbol_appears_twice() -> None:
    CycleIntentType, evaluate_cycle = load_engine_api()
    now = datetime(2026, 4, 24, 19, 0, tzinfo=timezone.utc)

    dup_position = make_open_position("AAPL")
    result = evaluate_cycle(
        settings=make_settings(),
        now=now,
        equity=100000.0,
        intraday_bars_by_symbol={},
        daily_bars_by_symbol={},
        open_positions=[dup_position, dup_position],
        working_order_symbols=set(),
        traded_symbols_today=set(),
        entries_disabled=False,
        flatten_all=True,
    )

    aapl_exits = [i for i in result.intents if i.symbol == "AAPL"]
    assert len(aapl_exits) == 1, "flatten_all must emit at most one EXIT per symbol"


def test_eod_flatten_deduplicates_exits_when_same_symbol_appears_twice() -> None:
    CycleIntentType, evaluate_cycle = load_engine_api()
    past_flatten_now = datetime(2026, 4, 24, 20, 0, tzinfo=timezone.utc)

    dup_position = make_open_position("AAPL")
    result = evaluate_cycle(
        settings=make_settings(),
        now=past_flatten_now,
        equity=100000.0,
        intraday_bars_by_symbol={},
        daily_bars_by_symbol={},
        open_positions=[dup_position, dup_position],
        working_order_symbols=set(),
        traded_symbols_today=set(),
        entries_disabled=False,
    )

    aapl_exits = [i for i in result.intents if i.symbol == "AAPL"]
    assert len(aapl_exits) == 1, "EOD flatten must emit at most one EXIT per symbol"


def test_min_stop_distance_guard_rejects_signal_with_penny_spread() -> None:
    CycleIntentType, evaluate_cycle = load_engine_api()
    from alpaca_bot.domain import EntrySignal

    def _tight_signal_evaluator(**_kwargs):
        bar = Bar(
            symbol="AAPL",
            timestamp=datetime(2026, 4, 24, 19, 0, tzinfo=timezone.utc),
            open=110.0, high=111.0, low=109.5, close=110.5, volume=5000,
        )
        return EntrySignal(
            symbol="AAPL",
            signal_bar=bar,
            entry_level=110.50,
            limit_price=110.509,
            stop_price=110.500,
            initial_stop_price=110.500,  # spread = 0.009 < 0.01
            relative_volume=2.0,
        )

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
        signal_evaluator=_tight_signal_evaluator,
    )

    entry_intents = [i for i in result.intents if i.intent_type == CycleIntentType.ENTRY]
    assert entry_intents == [], "Signals with stop spread < $0.01 must be rejected"


def test_evaluate_cycle_skips_entry_when_symbol_has_active_stop_in_working_order_symbols() -> None:
    """Symbols already in working_order_symbols are excluded from entry candidates.

    This documents the existing engine behavior that Fix 5 relies on: the supervisor
    adds active stop-sell symbols to working_order_symbols BEFORE calling run_cycle(),
    so evaluate_cycle() naturally skips entry for any symbol already covered by a stop.
    """
    CycleIntentType, evaluate_cycle = load_engine_api()

    result = evaluate_cycle(
        settings=make_settings(),
        now=datetime(2026, 4, 24, 19, 0, tzinfo=timezone.utc),
        equity=100000.0,
        intraday_bars_by_symbol={"AAPL": make_breakout_intraday_bars()},
        daily_bars_by_symbol={"AAPL": make_daily_bars()},
        open_positions=[],
        working_order_symbols={"AAPL"},  # AAPL has an active stop-sell at the broker
        traded_symbols_today=set(),
        entries_disabled=False,
    )

    entry_intents = [i for i in result.intents if i.intent_type == CycleIntentType.ENTRY]
    assert entry_intents == [], (
        "evaluate_cycle must skip entry for any symbol already in working_order_symbols"
    )


# --- Options engine tests ---

def test_cycle_intent_is_option_defaults_false():
    from alpaca_bot.core.engine import CycleIntent, CycleIntentType
    intent = CycleIntent(
        intent_type=CycleIntentType.ENTRY,
        symbol="AAPL",
        timestamp=datetime(2024, 6, 1, 14, 0, tzinfo=timezone.utc),
    )
    assert intent.is_option is False
    assert intent.underlying_symbol is None


def test_cycle_intent_with_option_fields():
    from alpaca_bot.core.engine import CycleIntent, CycleIntentType
    intent = CycleIntent(
        intent_type=CycleIntentType.ENTRY,
        symbol="AAPL240701C00100000",
        timestamp=datetime(2024, 6, 1, 14, 0, tzinfo=timezone.utc),
        is_option=True,
        underlying_symbol="AAPL",
    )
    assert intent.is_option is True
    assert intent.underlying_symbol == "AAPL"


def test_evaluate_cycle_option_entry_uses_option_sizing():
    """When a breakout_calls evaluator returns an option signal, evaluate_cycle uses premium-based sizing."""
    from alpaca_bot.core.engine import evaluate_cycle, CycleIntentType
    from alpaca_bot.domain.models import Bar, EntrySignal, OptionContract
    from tests.unit.helpers import _base_env, _make_settings

    contract = OptionContract(
        occ_symbol="AAPL240701C00100000",
        underlying="AAPL",
        option_type="call",
        strike=100.0,
        expiry=date(2024, 7, 1),
        bid=2.50,
        ask=3.00,
        delta=0.50,
    )

    now = datetime(2024, 6, 1, 14, 0, tzinfo=timezone.utc)

    def fake_option_evaluator(*, symbol, intraday_bars, signal_index, daily_bars, settings):
        bar = intraday_bars[-1]
        return EntrySignal(
            symbol=symbol,
            signal_bar=bar,
            entry_level=bar.close,
            relative_volume=2.0,
            stop_price=0.0,
            limit_price=contract.ask,
            initial_stop_price=0.01,
            option_contract=contract,
        )

    env = _base_env()
    env["RISK_PER_TRADE_PCT"] = "0.01"  # 1% of 100k = 1000 budget; ask=3.0, cost=300/contract → 3 contracts
    env["MAX_POSITION_PCT"] = "0.10"
    s = _make_settings(env)

    bar = Bar(symbol="AAPL", timestamp=now, open=100.0, high=101.0, low=99.0, close=100.0, volume=1000.0)
    daily_bar = Bar(symbol="AAPL", timestamp=datetime(2024, 5, 31, 0, 0, tzinfo=timezone.utc), open=95.0, high=100.0, low=94.0, close=98.0, volume=1_000_000.0)

    result = evaluate_cycle(
        settings=s,
        now=now,
        equity=100_000.0,
        intraday_bars_by_symbol={"AAPL": [bar]},
        daily_bars_by_symbol={"AAPL": [daily_bar]},
        open_positions=[],
        working_order_symbols=set(),
        traded_symbols_today=set(),
        entries_disabled=False,
        signal_evaluator=fake_option_evaluator,
        strategy_name="breakout_calls",
    )

    option_entries = [i for i in result.intents if i.intent_type == CycleIntentType.ENTRY and i.is_option]
    assert len(option_entries) == 1
    intent = option_entries[0]
    assert intent.symbol == "AAPL240701C00100000"
    assert intent.underlying_symbol == "AAPL"
    assert intent.is_option is True
    assert intent.quantity == 3  # floor(1000 / 300) = 3
    assert intent.client_order_id is not None
    assert intent.client_order_id.startswith("option:")


def test_evaluate_cycle_option_entry_skipped_when_quantity_zero():
    """If option sizing returns 0 contracts, no ENTRY intent is emitted."""
    from alpaca_bot.core.engine import evaluate_cycle, CycleIntentType
    from alpaca_bot.domain.models import Bar, EntrySignal, OptionContract
    from tests.unit.helpers import _base_env, _make_settings

    contract = OptionContract(
        occ_symbol="AAPL240701C00100000",
        underlying="AAPL",
        option_type="call",
        strike=100.0,
        expiry=date(2024, 7, 1),
        bid=99.50,
        ask=100.0,  # $100 ask → contract_cost = $10000; tiny budget → 0 contracts
        delta=0.50,
    )
    now = datetime(2024, 6, 1, 14, 0, tzinfo=timezone.utc)

    def fake_option_evaluator(*, symbol, intraday_bars, signal_index, daily_bars, settings):
        bar = intraday_bars[-1]
        return EntrySignal(
            symbol=symbol, signal_bar=bar, entry_level=bar.close, relative_volume=2.0,
            stop_price=0.0, limit_price=contract.ask, initial_stop_price=0.01,
            option_contract=contract,
        )

    env = _base_env()
    env["RISK_PER_TRADE_PCT"] = "0.001"  # tiny budget
    s = _make_settings(env)

    bar = Bar(symbol="AAPL", timestamp=now, open=100.0, high=101.0, low=99.0, close=100.0, volume=1000.0)
    daily_bar = Bar(symbol="AAPL", timestamp=datetime(2024, 5, 31, 0, 0, tzinfo=timezone.utc), open=95.0, high=100.0, low=94.0, close=98.0, volume=1_000_000.0)

    result = evaluate_cycle(
        settings=s, now=now, equity=10_000.0,
        intraday_bars_by_symbol={"AAPL": [bar]},
        daily_bars_by_symbol={"AAPL": [daily_bar]},
        open_positions=[], working_order_symbols=set(),
        traded_symbols_today=set(), entries_disabled=False,
        signal_evaluator=fake_option_evaluator, strategy_name="breakout_calls",
    )
    option_entries = [i for i in result.intents if i.intent_type == CycleIntentType.ENTRY and i.is_option]
    assert len(option_entries) == 0
