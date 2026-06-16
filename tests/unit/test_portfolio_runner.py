# tests/unit/test_portfolio_runner.py
from datetime import datetime, timezone

from alpaca_bot.config import Settings
from alpaca_bot.domain.models import Bar, ReplayScenario
from alpaca_bot.replay.portfolio import PortfolioReplayRunner

ENV = {
    "TRADING_MODE": "paper",
    "ENABLE_LIVE_TRADING": "false",
    "STRATEGY_VERSION": "v1-breakout",
    "DATABASE_URL": "postgresql://u:p@h:5432/d",
    "MARKET_DATA_FEED": "sip",
    "SYMBOLS": "AAA,BBB",
    "ENTRY_TIMEFRAME_MINUTES": "15",
}


def _bar(symbol, ts, o=100.0, h=101.0, l=99.0, c=100.5, v=1000):
    return Bar(symbol=symbol, timestamp=ts, open=o, high=h, low=l, close=c, volume=v)


def _utc(y, mo, d, h, mi):
    return datetime(y, mo, d, h, mi, tzinfo=timezone.utc)


def _scenario(symbol, intraday_ts, daily_ts):
    return ReplayScenario(
        name=f"{symbol}_x",
        symbol=symbol,
        starting_equity=100000.0,
        daily_bars=[_bar(symbol, ts) for ts in daily_ts],
        intraday_bars=[_bar(symbol, ts) for ts in intraday_ts],
    )


def test_union_timeline_merges_and_dedupes_across_symbols():
    settings = Settings.from_env(ENV)
    a = _scenario("AAA", [_utc(2026, 1, 2, 14, 30), _utc(2026, 1, 2, 14, 45)], [_utc(2026, 1, 1, 5, 0)])
    # BBB missing the 14:30 bar (a gap) and adds a 15:00 bar
    b = _scenario("BBB", [_utc(2026, 1, 2, 14, 45), _utc(2026, 1, 2, 15, 0)], [_utc(2026, 1, 1, 5, 0)])
    runner = PortfolioReplayRunner(settings, signal_evaluator=lambda **k: None, strategy_name="breakout")
    timeline = runner._build_timeline([a, b])
    assert timeline == [
        _utc(2026, 1, 2, 14, 30),
        _utc(2026, 1, 2, 14, 45),
        _utc(2026, 1, 2, 15, 0),
    ]


def test_point_in_time_daily_slice_excludes_current_and_future_days():
    settings = Settings.from_env(ENV)
    daily = [_utc(2026, 1, 1, 5, 0), _utc(2026, 1, 2, 5, 0)]  # day-1 and day-2 daily bars
    a = _scenario("AAA", [_utc(2026, 1, 2, 14, 30)], daily)
    runner = PortfolioReplayRunner(settings, signal_evaluator=lambda **k: None, strategy_name="breakout")
    runner._index_scenarios([a])
    # On session day 2026-01-02, only the 2026-01-01 daily bar is visible (< day).
    sliced = runner._daily_slice_for("AAA", _utc(2026, 1, 2, 14, 30))
    assert len(sliced) == 1
    assert sliced[0].timestamp == _utc(2026, 1, 1, 5, 0)
