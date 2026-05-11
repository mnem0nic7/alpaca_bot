from __future__ import annotations

from datetime import datetime, timezone

from alpaca_bot.config import Settings
from alpaca_bot.core.engine import CycleIntentType, evaluate_cycle
from alpaca_bot.domain import Bar, OpenPosition


def _make_settings(**overrides) -> Settings:
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
        "ENTRY_WINDOW_START": "09:30",
        "ENTRY_WINDOW_END": "15:30",
        "FLATTEN_TIME": "15:45",
        "PER_SYMBOL_LOSS_LIMIT_PCT": "0.0",
        # BREAKEVEN_TRIGGER_PCT defaults to 0.0025, BREAKEVEN_TRAIL_PCT to 0.002
    }
    base.update(overrides)
    return Settings.from_env(base)


def _make_bar(symbol: str, high: float, ts: datetime | None = None) -> Bar:
    ts = ts or datetime(2026, 5, 1, 14, 0, tzinfo=timezone.utc)
    return Bar(
        symbol=symbol,
        timestamp=ts,
        open=high - 0.10,
        high=high,
        low=high - 0.20,
        close=high - 0.05,
        volume=500_000,
    )


def test_breakeven_trail_uses_highest_price_not_current_bar():
    """
    Regression: the breakeven trail stop must be computed from max(highest_price, bar.high),
    where highest_price is the persisted historical maximum — NOT just the current bar.

    Setup:
      entry_price = 40.00
      highest_price = 50.04  (persisted from a prior cycle)
      current bar.high = 50.00  (retrace — below the historical max)

    Breakeven trail (0.2%):
      Correct:  50.04 * (1 - 0.002) = 49.9399  → rounded = 49.94
      Buggy:    50.00 * (1 - 0.002) = 49.9000  → rounded = 49.90

    The stop intent's stop_price must be >= 49.94.
    Prices must be above ~$25 so that 0.2% of highest_price exceeds the
    _make_bar close-to-high spread of $0.05, keeping be_stop < close.
    """
    settings = _make_settings()
    entry_price = 40.00
    highest_price = 50.04  # from a prior cycle — retraced
    bar_high = 50.00       # trigger met (50.00 >= 40.00 * 1.0025 = 40.10)

    position = OpenPosition(
        symbol="AAPL",
        entry_timestamp=datetime(2026, 5, 1, 10, 0, tzinfo=timezone.utc),
        entry_price=entry_price,
        quantity=100.0,
        entry_level=39.00,
        initial_stop_price=39.00,
        stop_price=39.00,
        trailing_active=False,
        highest_price=highest_price,
        strategy_name="breakout",
    )

    current_bar = _make_bar("AAPL", high=bar_high)
    historical_bar = _make_bar(
        "AAPL",
        high=39.50,
        ts=datetime(2026, 5, 1, 9, 30, tzinfo=timezone.utc),
    )
    daily_bar = Bar(
        symbol="AAPL",
        timestamp=datetime(2026, 4, 30, tzinfo=timezone.utc),
        open=38.50, high=42.00, low=38.00, close=40.00,
        volume=1_000_000,
    )

    intraday_bars = {"AAPL": [historical_bar] * 19 + [current_bar]}
    daily_bars = {"AAPL": [daily_bar] * 60}

    now = datetime(2026, 5, 1, 14, 0, tzinfo=timezone.utc)

    result = evaluate_cycle(
        settings=settings,
        now=now,
        equity=10_000.0,
        open_positions=[position],
        intraday_bars_by_symbol=intraday_bars,
        daily_bars_by_symbol=daily_bars,
        working_order_symbols=set(),
        traded_symbols_today=set(),
        entries_disabled=False,
    )

    breakeven_intents = [
        i for i in result.intents
        if i.intent_type == CycleIntentType.UPDATE_STOP and i.reason == "breakeven"
    ]
    assert len(breakeven_intents) == 1, f"Expected 1 breakeven UPDATE_STOP intent, got {len(breakeven_intents)}"

    stop = breakeven_intents[0].stop_price
    correct_floor = round(highest_price * (1 - settings.breakeven_trail_pct), 2)
    buggy_ceiling = round(bar_high * (1 - settings.breakeven_trail_pct), 2)

    assert stop >= correct_floor, (
        f"Stop {stop:.4f} is below the correct trail floor {correct_floor:.4f} "
        f"(computed from highest_price={highest_price}). "
        f"Buggy value based on bar.high alone would be {buggy_ceiling:.4f}."
    )
    assert stop > buggy_ceiling, (
        f"Stop {stop:.4f} should exceed the buggy bar-only trail {buggy_ceiling:.4f}"
    )
