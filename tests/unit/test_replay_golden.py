from pathlib import Path

from alpaca_bot.config import Settings
from alpaca_bot.domain import Bar
from alpaca_bot.domain.enums import IntentType
from alpaca_bot.domain.models import ReplayScenario
from alpaca_bot.replay import ReplayRunner


GOLDEN_DIR = Path(__file__).resolve().parent.parent / "golden"


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


def test_breakout_success_golden_scenario() -> None:
    runner = ReplayRunner(make_settings())
    scenario = runner.load_scenario(GOLDEN_DIR / "breakout_success.json")

    result = runner.run(scenario)

    assert [event.event_type for event in result.events] == [
        IntentType.ENTRY_ORDER_PLACED,
        IntentType.ENTRY_FILLED,
        IntentType.STOP_UPDATED,
        IntentType.EOD_EXIT,
    ]
    assert result.events[1].details["entry_price"] == 110.05
    assert result.events[2].details["stop_price"] == 110.05
    assert result.events[3].details["exit_price"] == 112.5
    assert result.final_position is None


def test_breakout_entry_expiry_golden_scenario() -> None:
    runner = ReplayRunner(make_settings())
    scenario = runner.load_scenario(GOLDEN_DIR / "breakout_entry_expires.json")

    result = runner.run(scenario)

    assert [event.event_type for event in result.events] == [
        IntentType.ENTRY_ORDER_PLACED,
        IntentType.ENTRY_EXPIRED,
    ]
    assert result.final_position is None


def test_replay_runner_limits_symbol_to_one_trade_per_day() -> None:
    runner = ReplayRunner(make_settings())
    base_scenario = runner.load_scenario(GOLDEN_DIR / "breakout_success.json")
    bars = []
    for index in range(20):
        timestamp = base_scenario.intraday_bars[0].timestamp.replace(hour=13, minute=30) + (
            index * (base_scenario.intraday_bars[1].timestamp - base_scenario.intraday_bars[0].timestamp)
        )
        high = 108.5 + index * 0.08
        close = high - 0.2
        bars.append(
            Bar(
                symbol="AAPL",
                timestamp=timestamp,
                open=round(close - 0.1, 2),
                high=round(high, 2),
                low=round(close - 0.25, 2),
                close=round(close, 2),
                volume=1000 + index * 10,
            )
        )
    bars[-1] = Bar(
        symbol="AAPL",
        timestamp=bars[-1].timestamp,
        open=109.55,
        high=110.0,
        low=109.35,
        close=109.75,
        volume=1190,
    )
    bars.extend(
        [
            Bar(
                symbol="AAPL",
                timestamp=bars[-1].timestamp.replace(hour=18, minute=30),
                open=109.8,
                high=111.0,
                low=109.7,
                close=110.8,
                volume=2000,
            ),
            Bar(
                symbol="AAPL",
                timestamp=bars[-1].timestamp.replace(hour=18, minute=45),
                open=110.05,
                high=111.2,
                low=107.9,
                close=109.5,
                volume=1800,
            ),
            Bar(
                symbol="AAPL",
                timestamp=bars[-1].timestamp.replace(hour=19, minute=0),
                open=110.0,
                high=110.3,
                low=109.5,
                close=109.8,
                volume=1900,
            ),
            Bar(
                symbol="AAPL",
                timestamp=bars[-1].timestamp.replace(hour=19, minute=15),
                open=110.6,
                high=112.0,
                low=110.5,
                close=111.8,
                volume=2400,
            ),
            Bar(
                symbol="AAPL",
                timestamp=bars[-1].timestamp.replace(hour=19, minute=30),
                open=112.05,
                high=112.3,
                low=111.9,
                close=112.1,
                volume=1800,
            ),
        ]
    )
    scenario = ReplayScenario(
        name="one_trade_per_day",
        symbol="AAPL",
        starting_equity=base_scenario.starting_equity,
        daily_bars=base_scenario.daily_bars,
        intraday_bars=bars,
    )

    result = runner.run(scenario)

    assert [event.event_type for event in result.events].count(IntentType.ENTRY_ORDER_PLACED) == 1
    assert [event.event_type for event in result.events].count(IntentType.STOP_HIT) == 1
