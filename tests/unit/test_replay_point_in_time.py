"""Regression tests for the two replay-harness defects found in the
2026-06-11 contrarian audit (docs/strategy-audit/):

1. ReplayRunner never passed the scenario symbol to evaluate_cycle(), so
   scenarios for symbols outside settings.symbols were silently never
   evaluated (991/999 nightly scenarios produced zero decisions).
2. ReplayRunner passed the FULL daily series on every intraday bar, so
   end-anchored daily trend filters were look-ahead and scenario-constant.

The fixed runner must mirror the live supervisor's data shape: on session
day D the engine sees only daily bars dated < D (runtime/supervisor.py
fetches daily bars with end = midnight ET of the session date).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from alpaca_bot.config import Settings
from alpaca_bot.domain import Bar
from alpaca_bot.domain.enums import IntentType
from alpaca_bot.domain.models import EntrySignal, ReplayScenario
from alpaca_bot.replay import ReplayRunner
from alpaca_bot.strategy.breakout import session_day


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
        "REPLAY_SLIPPAGE_BPS": "0",
        "ATR_PERIOD": "14",
    }
    values.update(overrides)
    return Settings.from_env(values)


def _daily_bars(symbol: str, *, start: datetime, count: int) -> list[Bar]:
    """Uniform +1/day ramp with constant true range (2.0), so ATR and the
    trend-filter verdict are invariant to where the series is cut."""
    return [
        Bar(
            symbol=symbol,
            timestamp=start + timedelta(days=i),
            open=88.0 + i,
            high=89.0 + i,
            low=87.0 + i,
            close=89.0 + i,
            volume=1_000_000,
        )
        for i in range(count)
    ]


def _fires_at_index_5(*, symbol, intraday_bars, signal_index, daily_bars, settings):
    if signal_index != 5:
        return None
    return EntrySignal(
        symbol=symbol,
        signal_bar=intraday_bars[signal_index],
        entry_level=100.5,
        relative_volume=2.0,
        stop_price=101.0,
        limit_price=101.5,
        initial_stop_price=99.0,
    )


def test_off_watchlist_scenario_symbol_is_evaluated() -> None:
    """Defect 1: NVDA is not in SYMBOLS, but its scenario must still be evaluated."""
    settings = make_settings()  # SYMBOLS=AAPL,MSFT,SPY — no NVDA
    daily = _daily_bars(
        "NVDA", start=datetime(2026, 4, 3, 20, 0, tzinfo=timezone.utc), count=21
    )  # ends 2026-04-23, strictly before the intraday day
    t0 = datetime(2026, 4, 24, 14, 30, tzinfo=timezone.utc)  # 10:30 ET
    intraday = [
        Bar(
            symbol="NVDA",
            timestamp=t0 + timedelta(minutes=15 * i),
            open=100.0,
            high=100.5,
            low=99.5,
            close=100.0,
            volume=1_000_000,
        )
        for i in range(10)
    ]
    scenario = ReplayScenario(
        name="off-watchlist",
        symbol="NVDA",
        starting_equity=100_000.0,
        daily_bars=daily,
        intraday_bars=intraday,
    )

    result = ReplayRunner(settings, signal_evaluator=_fires_at_index_5).run(scenario)

    placed = [e for e in result.events if e.event_type == IntentType.ENTRY_ORDER_PLACED]
    assert placed, "scenario symbol outside settings.symbols was never evaluated"
