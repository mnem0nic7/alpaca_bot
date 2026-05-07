from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from alpaca_bot.domain import Bar, OpenPosition
from alpaca_bot.core.engine import CycleIntentType, evaluate_cycle


def make_settings(**overrides: str):
    from alpaca_bot.config import Settings

    values = {
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
    }
    values.update(overrides)
    return Settings.from_env(values)


def _now() -> datetime:
    return datetime(2026, 5, 6, 19, 0, tzinfo=timezone.utc)


def _make_signal(
    *,
    symbol: str = "AAPL",
    limit_price: float,
    initial_stop_price: float,
    stop_price: float | None = None,
):
    from alpaca_bot.domain.models import EntrySignal

    bar = Bar(
        symbol=symbol,
        timestamp=_now(),
        open=limit_price - 0.5,
        high=limit_price,
        low=limit_price - 1.0,
        close=limit_price - 0.1,
        volume=100000,
    )
    return EntrySignal(
        symbol=symbol,
        signal_bar=bar,
        entry_level=limit_price - 0.1,
        relative_volume=2.0,
        stop_price=stop_price if stop_price is not None else initial_stop_price,
        limit_price=limit_price,
        initial_stop_price=initial_stop_price,
    )


def _make_intraday_bars(price: float) -> list[Bar]:
    return [
        Bar(
            symbol="AAPL",
            timestamp=_now(),
            open=price - 0.5,
            high=price,
            low=price - 1.0,
            close=price - 0.1,
            volume=100_000,
        )
    ]


def _make_daily_bars() -> list[Bar]:
    bars = []
    base = datetime(2026, 4, 1, 14, 0, tzinfo=timezone.utc)
    for i in range(25):
        bars.append(
            Bar(
                symbol="AAPL",
                timestamp=base.replace(day=i + 1) if i < 28 else base,
                open=100.0,
                high=102.0,
                low=98.0,
                close=101.0,
                volume=1_000_000,
            )
        )
    return bars


def test_new_entry_stop_within_cap_is_unchanged():
    """initial_stop_price already within 5% — engine must not alter it."""
    settings = make_settings(MAX_STOP_PCT="0.05")
    limit_price = 100.0
    initial_stop = 97.0  # 3% below entry — within the 5% cap

    def signal_evaluator(*, symbol, intraday_bars, signal_index, daily_bars, settings):
        return _make_signal(
            limit_price=limit_price,
            initial_stop_price=initial_stop,
        )

    result = evaluate_cycle(
        settings=settings,
        now=_now(),
        equity=100_000.0,
        intraday_bars_by_symbol={"AAPL": _make_intraday_bars(limit_price)},
        daily_bars_by_symbol={"AAPL": _make_daily_bars()},
        open_positions=[],
        working_order_symbols=set(),
        traded_symbols_today=set(),
        entries_disabled=False,
        signal_evaluator=signal_evaluator,
    )

    entries = [i for i in result.intents if i.intent_type == CycleIntentType.ENTRY]
    assert len(entries) == 1
    assert entries[0].initial_stop_price == pytest.approx(97.0)


def test_new_entry_stop_beyond_cap_is_raised_to_cap():
    """initial_stop_price 8% below entry — engine must clamp to 5%."""
    settings = make_settings(MAX_STOP_PCT="0.05")
    limit_price = 100.0
    # 8% below entry — exceeds the 5% cap
    initial_stop = 92.0
    intraday_bars = _make_intraday_bars(limit_price)

    def signal_evaluator(*, symbol, intraday_bars, signal_index, daily_bars, settings):
        return _make_signal(
            limit_price=limit_price,
            initial_stop_price=initial_stop,
        )

    result = evaluate_cycle(
        settings=settings,
        now=_now(),
        equity=100_000.0,
        intraday_bars_by_symbol={"AAPL": intraday_bars},
        daily_bars_by_symbol={"AAPL": _make_daily_bars()},
        open_positions=[],
        working_order_symbols=set(),
        traded_symbols_today=set(),
        entries_disabled=False,
        signal_evaluator=signal_evaluator,
    )

    entries = [i for i in result.intents if i.intent_type == CycleIntentType.ENTRY]
    assert len(entries) == 1
    # 5% cap: 100.0 * (1 - 0.05) = 95.0
    assert entries[0].initial_stop_price == pytest.approx(95.0)


def test_new_entry_quantity_reflects_capped_stop():
    """Capped stop reduces risk per share, which increases share count."""
    settings = make_settings(MAX_STOP_PCT="0.05")
    limit_price = 100.0
    initial_stop_uncapped = 80.0  # 20% below entry
    intraday_bars = _make_intraday_bars(limit_price)

    def signal_evaluator(*, symbol, intraday_bars, signal_index, daily_bars, settings):
        return _make_signal(
            limit_price=limit_price,
            initial_stop_price=initial_stop_uncapped,
        )

    result = evaluate_cycle(
        settings=settings,
        now=_now(),
        equity=100_000.0,
        intraday_bars_by_symbol={"AAPL": intraday_bars},
        daily_bars_by_symbol={"AAPL": _make_daily_bars()},
        open_positions=[],
        working_order_symbols=set(),
        traded_symbols_today=set(),
        entries_disabled=False,
        signal_evaluator=signal_evaluator,
    )

    entries = [i for i in result.intents if i.intent_type == CycleIntentType.ENTRY]
    assert len(entries) == 1
    # Effective stop = 95.0; risk/share = 5.0
    # risk_dollars = 100_000 * 0.0025 = 250; qty = 250 / 5.0 = 50
    assert entries[0].initial_stop_price == pytest.approx(95.0)
    assert entries[0].quantity == 50


# ── Task 3 tests: cap-up pass for existing positions ──────────────────────────


def _make_position(
    symbol: str = "AAPL",
    entry_price: float = 100.0,
    stop_price: float = 90.0,
    initial_stop_price: float = 90.0,
) -> OpenPosition:
    return OpenPosition(
        symbol=symbol,
        entry_timestamp=datetime(2026, 5, 6, 14, 0, tzinfo=timezone.utc),
        entry_price=entry_price,
        quantity=10,
        entry_level=entry_price - 1.0,
        initial_stop_price=initial_stop_price,
        stop_price=stop_price,
    )


def test_existing_position_stop_beyond_cap_emits_update_stop():
    """Position with stop 10% below entry gets an UPDATE_STOP to the 5% cap level."""
    settings = make_settings(MAX_STOP_PCT="0.05", ENABLE_BREAKEVEN_STOP="false")
    position = _make_position(entry_price=100.0, stop_price=90.0, initial_stop_price=90.0)
    intraday_bars = _make_intraday_bars(101.0)

    result = evaluate_cycle(
        settings=settings,
        now=_now(),
        equity=100_000.0,
        intraday_bars_by_symbol={"AAPL": intraday_bars},
        daily_bars_by_symbol={"AAPL": _make_daily_bars()},
        open_positions=[position],
        working_order_symbols=set(),
        traded_symbols_today=set(),
        entries_disabled=False,
    )

    update_stops = [i for i in result.intents if i.intent_type == CycleIntentType.UPDATE_STOP]
    assert len(update_stops) == 1
    assert update_stops[0].symbol == "AAPL"
    # 5% cap: 100.0 * (1 - 0.05) = 95.0
    assert update_stops[0].stop_price == pytest.approx(95.0)
    assert update_stops[0].reason == "stop_cap_applied"


def test_existing_position_stop_within_cap_no_intent():
    """Position already within 5% cap produces no UPDATE_STOP."""
    settings = make_settings(MAX_STOP_PCT="0.05")
    position = _make_position(entry_price=100.0, stop_price=96.0, initial_stop_price=96.0)
    intraday_bars = _make_intraday_bars(101.0)

    result = evaluate_cycle(
        settings=settings,
        now=_now(),
        equity=100_000.0,
        intraday_bars_by_symbol={"AAPL": intraday_bars},
        daily_bars_by_symbol={"AAPL": _make_daily_bars()},
        open_positions=[position],
        working_order_symbols=set(),
        traded_symbols_today=set(),
        entries_disabled=False,
    )

    cap_updates = [
        i for i in result.intents
        if i.intent_type == CycleIntentType.UPDATE_STOP and i.reason == "stop_cap_applied"
    ]
    assert cap_updates == []


def test_cap_up_skips_position_scheduled_for_exit():
    """Position that gets an EXIT from the viability trend-filter must not also get a cap UPDATE_STOP.

    This exercises the emitted_exit_syms derivation from intents — emitted_exit_symbols in the
    engine is only populated by the past_flatten path; trend-filter exits are not tracked there.
    """
    settings = make_settings(
        MAX_STOP_PCT="0.05",
        ENABLE_TREND_FILTER_EXIT="true",
        DAILY_SMA_PERIOD="5",
    )
    # Stop 10% below entry — would trigger cap — but position will be exited by trend filter.
    position = _make_position(entry_price=100.0, stop_price=90.0)

    # Build daily bars with a downtrend so the trend filter fires: SMA window close > last close
    base = datetime(2026, 5, 1, 14, 0, tzinfo=timezone.utc)
    daily_bars = [
        Bar(symbol="AAPL", timestamp=base.replace(day=i + 1), open=110.0, high=112.0, low=108.0, close=110.0 - i * 2.0, volume=1_000_000)
        for i in range(7)
    ]
    intraday_bars = _make_intraday_bars(101.0)

    result = evaluate_cycle(
        settings=settings,
        now=_now(),
        equity=100_000.0,
        intraday_bars_by_symbol={"AAPL": intraday_bars},
        daily_bars_by_symbol={"AAPL": daily_bars},
        open_positions=[position],
        working_order_symbols=set(),
        traded_symbols_today=set(),
        entries_disabled=False,
    )

    exit_intents = [i for i in result.intents if i.intent_type == CycleIntentType.EXIT]
    cap_updates = [
        i for i in result.intents
        if i.intent_type == CycleIntentType.UPDATE_STOP and i.reason == "stop_cap_applied"
    ]
    assert len(exit_intents) == 1
    assert cap_updates == [], "cap-up must not fire when position already scheduled for exit"


def test_cap_up_does_not_duplicate_trailing_stop_intent():
    """If trailing stop already raised stop above cap level, no second UPDATE_STOP is emitted."""
    settings = make_settings(
        MAX_STOP_PCT="0.05",
        TRAILING_STOP_ATR_MULTIPLIER="0.5",
        TRAILING_STOP_PROFIT_TRIGGER_R="0.1",
        ENABLE_BREAKEVEN_STOP="false",
    )
    # Entry 100, initial stop 95 (5%), trailing fires and raises stop to 97 (above cap)
    position = _make_position(
        entry_price=100.0,
        stop_price=95.0,
        initial_stop_price=95.0,
    )
    # High of 101 triggers trailing with 0.5 * ATR; let trailing push stop above 95
    latest_bar = Bar(
        symbol="AAPL",
        timestamp=_now(),
        open=100.5,
        high=102.0,  # profit trigger: 100 + 0.1 * (100 - 95) = 100.5; 102 > 100.5
        low=100.0,
        close=101.0,
        volume=50_000,
    )

    result = evaluate_cycle(
        settings=settings,
        now=_now(),
        equity=100_000.0,
        intraday_bars_by_symbol={"AAPL": [latest_bar]},
        daily_bars_by_symbol={"AAPL": _make_daily_bars()},
        open_positions=[position],
        working_order_symbols=set(),
        traded_symbols_today=set(),
        entries_disabled=False,
    )

    update_stops = [i for i in result.intents if i.intent_type == CycleIntentType.UPDATE_STOP]
    # Trailing raises stop; that already satisfies or exceeds cap. Only one UPDATE_STOP.
    assert len(update_stops) == 1
    assert update_stops[0].reason != "stop_cap_applied"
