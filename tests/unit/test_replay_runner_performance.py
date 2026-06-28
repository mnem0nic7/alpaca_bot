from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Sequence

from alpaca_bot.config import Settings
from alpaca_bot.domain.models import Bar, EntrySignal, ReplayScenario
from alpaca_bot.replay.runner import ReplayRunner


def _settings() -> Settings:
    return Settings.from_env(
        {
            "TRADING_MODE": "paper",
            "ENABLE_LIVE_TRADING": "false",
            "STRATEGY_VERSION": "v1-breakout",
            "DATABASE_URL": "postgresql://alpaca_bot:secret@db.example.com:5432/alpaca_bot",
            "MARKET_DATA_FEED": "sip",
            "SYMBOLS": "AAPL",
            "DAILY_SMA_PERIOD": "5",
            "BREAKOUT_LOOKBACK_BARS": "5",
            "RELATIVE_VOLUME_LOOKBACK_BARS": "5",
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
        }
    )


def _bar(symbol: str, timestamp: datetime, close: float = 100.0) -> Bar:
    return Bar(
        symbol=symbol,
        timestamp=timestamp,
        open=close,
        high=close + 1.0,
        low=close - 1.0,
        close=close,
        volume=1_000_000,
    )


def test_replay_runner_uses_clean_prefix_views_for_bar_history() -> None:
    settings = _settings()
    intraday_start = datetime(2026, 4, 23, 14, 0, tzinfo=timezone.utc)
    daily_start = datetime(2026, 4, 1, 20, 0, tzinfo=timezone.utc)
    scenario = ReplayScenario(
        name="clean-prefix",
        symbol="AAPL",
        starting_equity=100_000.0,
        daily_bars=[
            _bar("AAPL", daily_start + timedelta(days=i), close=95.0 + i)
            for i in range(20)
        ],
        intraday_bars=[
            _bar("AAPL", intraday_start + timedelta(minutes=15 * i))
            for i in range(8)
        ],
    )
    captures: list[tuple[int, int, int, bool, bool]] = []

    def capture_prefixes(
        *,
        symbol: str,
        intraday_bars: Sequence[Bar],
        signal_index: int,
        daily_bars: Sequence[Bar],
        settings: Settings,
    ) -> EntrySignal | None:
        captures.append(
            (
                len(intraday_bars),
                signal_index,
                len(daily_bars),
                getattr(intraday_bars, "all_closes_positive", False),
                getattr(daily_bars, "all_closes_positive", False),
            )
        )
        return None

    ReplayRunner(settings, signal_evaluator=capture_prefixes).run(scenario)

    assert captures
    assert captures[0][0] == 1
    assert captures[-1][0] == len(scenario.intraday_bars)
    assert all(
        signal_index == intraday_len - 1
        for intraday_len, signal_index, *_ in captures
    )
    assert all(daily_len > 0 for _, _, daily_len, *_ in captures)
    assert all(intraday_clean for *_, intraday_clean, _ in captures)
    assert all(daily_clean for *_, daily_clean in captures)
