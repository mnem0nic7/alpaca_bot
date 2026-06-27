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


def test_topk_cap_limits_concurrent_entries_to_max_open_positions(monkeypatch):
    # Two symbols both fire an ENTRY on the same tick; with max_open_positions=1
    # only the higher-ranked one is taken (engine enforces the cap).
    base_env = dict(ENV)
    base_env["MAX_OPEN_POSITIONS"] = "1"
    settings = Settings.from_env(base_env)

    t0 = _utc(2026, 1, 2, 14, 30)
    t1 = _utc(2026, 1, 2, 14, 45)
    t2 = _utc(2026, 1, 2, 15, 0)

    # A fake evaluator that emits a strong signal for both symbols at t0,
    # with AAA stronger (higher close/entry_level). We assert exactly one fill.
    from alpaca_bot.domain.models import EntrySignal

    def fake_eval(*, symbol, intraday_bars, signal_index, daily_bars, settings):
        bar = intraday_bars[signal_index]
        if bar.timestamp != t0:
            return None
        strength = 1.05 if symbol == "AAA" else 1.02
        return EntrySignal(
            symbol=symbol,
            signal_bar=bar,
            entry_level=100.0,
            relative_volume=2.0,
            stop_price=99.0,
            limit_price=round(100.0 * strength, 2),
            initial_stop_price=99.0,
            option_contract=None,
        )

    def mk(symbol):
        intraday = [
            _bar(symbol, t0, o=100, h=106, l=99, c=105, v=5000),
            _bar(symbol, t1, o=105, h=107, l=104, c=106, v=5000),
            _bar(symbol, t2, o=106, h=108, l=99, c=107, v=5000),
        ]
        daily = [_bar(symbol, _utc(2026, 1, 1, 5, 0))]
        return ReplayScenario(name=symbol, symbol=symbol, starting_equity=100000.0,
                              daily_bars=daily, intraday_bars=intraday)

    runner = PortfolioReplayRunner(settings, signal_evaluator=fake_eval, strategy_name="breakout")
    trades = runner.run([mk("AAA"), mk("BBB")])
    # With one capacity slot and AAA ranked higher, only AAA should ever hold a position.
    assert {t.symbol for t in trades} == {"AAA"}


def test_single_shared_equity_pool_not_per_symbol():
    # Two symbols, ample capacity: both can trade, but they draw from ONE pool.
    # We assert the runner runs to completion and produces trades for both,
    # confirming the shared-pool path executes end to end.
    settings = Settings.from_env(ENV)
    t0 = _utc(2026, 1, 2, 14, 30)
    t1 = _utc(2026, 1, 2, 14, 45)
    t2 = _utc(2026, 1, 2, 15, 0)
    from alpaca_bot.domain.models import EntrySignal

    def fake_eval(*, symbol, intraday_bars, signal_index, daily_bars, settings):
        bar = intraday_bars[signal_index]
        if bar.timestamp != t0:
            return None
        return EntrySignal(symbol=symbol, signal_bar=bar, entry_level=100.0,
                           relative_volume=2.0, stop_price=99.0, limit_price=100.5,
                           initial_stop_price=99.0, option_contract=None)

    def mk(symbol):
        intraday = [
            _bar(symbol, t0, o=100, h=106, l=99, c=105, v=5000),
            _bar(symbol, t1, o=100.5, h=107, l=100, c=106, v=5000),   # fills at 100.5
            _bar(symbol, t2, o=106, h=108, l=80, c=107, v=5000),       # stop hit (low 80)
        ]
        daily = [_bar(symbol, _utc(2026, 1, 1, 5, 0))]
        return ReplayScenario(name=symbol, symbol=symbol, starting_equity=100000.0,
                              daily_bars=daily, intraday_bars=intraday)

    runner = PortfolioReplayRunner(settings, signal_evaluator=fake_eval, strategy_name="breakout")
    trades = runner.run([mk("AAA"), mk("BBB")])
    assert {t.symbol for t in trades} == {"AAA", "BBB"}
    assert all(t.exit_reason == "stop" for t in trades)


def test_portfolio_fill_preserves_engine_selected_quantity():
    settings = Settings.from_env({**ENV, "REPLAY_SLIPPAGE_BPS": "20"})
    t0 = _utc(2026, 1, 2, 14, 30)
    t1 = _utc(2026, 1, 2, 14, 45)
    runner = PortfolioReplayRunner(settings, signal_evaluator=lambda **k: None, strategy_name="breakout")
    runner._index_scenarios([
        ReplayScenario(
            name="AAA",
            symbol="AAA",
            starting_equity=100000.0,
            daily_bars=[_bar("AAA", _utc(2026, 1, 1, 5, 0))],
            intraday_bars=[
                _bar("AAA", t0, o=100, h=101, l=99, c=100, v=5000),
                _bar("AAA", t1, o=100, h=102, l=99, c=101, v=5000),
            ],
        )
    ])
    lane = runner._lanes["AAA"]
    lane.cursor = 0
    from alpaca_bot.core.engine import CycleIntent, CycleIntentType

    runner._place_order(
        lane,
        CycleIntent(
            intent_type=CycleIntentType.ENTRY,
            symbol="AAA",
            timestamp=t0,
            quantity=123.0,
            stop_price=100.0,
            limit_price=101.0,
            initial_stop_price=95.0,
        ),
    )
    lane.cursor = 1

    runner._resolve_order(lane, lane.intraday[lane.cursor], 100000.0)

    assert lane.position is not None
    assert lane.position.quantity == 123.0
