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
    assert result.intents[0].quantity == 45
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
        breakout_level=109.90,
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
        breakout_level=109.90,
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
