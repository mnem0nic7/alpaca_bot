# tests/unit/test_portfolio_runner.py
from datetime import datetime, timezone

from alpaca_bot.config import Settings
from alpaca_bot.core.engine import CycleResult
from alpaca_bot.domain.models import Bar, MarketContext, ReplayScenario
from alpaca_bot.replay.portfolio import (
    PortfolioBasketReplayRunner,
    PortfolioReplayRunner,
)

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
        _utc(2026, 1, 2, 14, 45),
        _utc(2026, 1, 2, 15, 0),
        _utc(2026, 1, 2, 15, 15),
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


def test_portfolio_runner_passes_regime_bars_from_spy_lane(monkeypatch):
    env = dict(ENV)
    env["SYMBOLS"] = "AAA,SPY"
    env["ENABLE_REGIME_FILTER"] = "true"
    env["REGIME_SMA_PERIOD"] = "2"
    settings = Settings.from_env(env)

    aaa = _scenario(
        "AAA",
        [_utc(2026, 1, 2, 14, 30)],
        [_utc(2026, 1, 1, 5, 0)],
    )
    spy = _scenario(
        "SPY",
        [_utc(2026, 1, 2, 14, 30)],
        [_utc(2025, 12, 31, 5, 0), _utc(2026, 1, 1, 5, 0), _utc(2026, 1, 2, 5, 0)],
    )
    seen_regime_dates: list[tuple] = []

    def fake_evaluate_cycle(**kwargs):
        bars = kwargs["regime_bars"]
        seen_regime_dates.append(tuple(bar.timestamp.date() for bar in bars or ()))
        return CycleResult(as_of=kwargs["now"])

    import alpaca_bot.replay.portfolio as portfolio_module

    monkeypatch.setattr(portfolio_module, "evaluate_cycle", fake_evaluate_cycle)
    runner = PortfolioReplayRunner(settings, signal_evaluator=lambda **k: None)
    runner.run([aaa, spy])

    assert seen_regime_dates
    assert seen_regime_dates[0] == (
        _utc(2025, 12, 31, 5, 0).date(),
        _utc(2026, 1, 1, 5, 0).date(),
    )


def test_portfolio_runner_passes_market_context_point_in_time(monkeypatch):
    env = dict(ENV)
    env["ENABLE_VIX_FILTER"] = "true"
    env["ENABLE_SECTOR_FILTER"] = "true"
    env["VIX_LOOKBACK_BARS"] = "2"
    env["SECTOR_ETF_SYMBOLS"] = "XLK"
    env["SECTOR_ETF_SMA_PERIOD"] = "2"
    settings = Settings.from_env(env)

    context_dates = [
        _utc(2025, 12, 30, 5, 0),
        _utc(2025, 12, 31, 5, 0),
        _utc(2026, 1, 1, 5, 0),
        _utc(2026, 1, 2, 5, 0),
    ]
    aaa = ReplayScenario(
        name="AAA_x",
        symbol="AAA",
        starting_equity=100000.0,
        daily_bars=[_bar("AAA", _utc(2026, 1, 1, 5, 0))],
        intraday_bars=[_bar("AAA", _utc(2026, 1, 2, 14, 30))],
        vix_daily_bars=[_bar("VIXY", ts) for ts in context_dates],
        sector_daily_bars_by_etf={"XLK": [_bar("XLK", ts) for ts in context_dates]},
    )
    seen_context_dates: list[tuple[tuple, tuple]] = []
    seen_market_context: list[MarketContext | None] = []

    def fake_compute_market_context(**kwargs):
        seen_context_dates.append(
            (
                tuple(bar.timestamp.date() for bar in kwargs["vix_bars"]),
                tuple(
                    bar.timestamp.date()
                    for bar in kwargs["sector_bars_by_etf"]["XLK"]
                ),
            )
        )
        return MarketContext(
            as_of=kwargs["as_of"],
            vix_above_sma=True,
            sector_passing_pct=0.0,
        )

    def fake_evaluate_cycle(**kwargs):
        seen_market_context.append(kwargs["market_context"])
        return CycleResult(as_of=kwargs["now"])

    import alpaca_bot.replay.portfolio as portfolio_module

    monkeypatch.setattr(
        portfolio_module, "compute_market_context", fake_compute_market_context
    )
    monkeypatch.setattr(portfolio_module, "evaluate_cycle", fake_evaluate_cycle)
    runner = PortfolioReplayRunner(settings, signal_evaluator=lambda **k: None)
    runner.run([aaa])

    assert seen_context_dates
    assert seen_context_dates[0] == (
        (
            _utc(2025, 12, 30, 5, 0).date(),
            _utc(2025, 12, 31, 5, 0).date(),
            _utc(2026, 1, 1, 5, 0).date(),
        ),
        (
            _utc(2025, 12, 30, 5, 0).date(),
            _utc(2025, 12, 31, 5, 0).date(),
            _utc(2026, 1, 1, 5, 0).date(),
        ),
    )
    assert seen_market_context == [
        MarketContext(
            as_of=_utc(2026, 1, 2, 14, 45),
            vix_above_sma=True,
            sector_passing_pct=0.0,
        )
    ]


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


def test_recent_nonfresh_symbol_reconsidered_after_capacity_frees():
    """Live evaluates all symbols with recent completed bars, not just fresh bars."""
    settings = Settings.from_env({**ENV, "MAX_OPEN_POSITIONS": "1", "REPLAY_SLIPPAGE_BPS": "0"})
    t0 = _utc(2026, 1, 2, 14, 30)
    t1 = _utc(2026, 1, 2, 14, 45)
    t2 = _utc(2026, 1, 2, 15, 0)
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

    daily = [_bar("AAA", _utc(2026, 1, 1, 5, 0))]
    aaa = ReplayScenario(
        name="AAA",
        symbol="AAA",
        starting_equity=100000.0,
        daily_bars=daily,
        intraday_bars=[
            _bar("AAA", t0, o=100, h=106, l=99, c=105, v=5000),
            _bar("AAA", t1, o=105, h=107, l=98, c=100, v=5000),
        ],
    )
    bbb = ReplayScenario(
        name="BBB",
        symbol="BBB",
        starting_equity=100000.0,
        daily_bars=[_bar("BBB", _utc(2026, 1, 1, 5, 0))],
        intraday_bars=[
            _bar("BBB", t0, o=100, h=103, l=99, c=102, v=5000),
            # BBB has no t1 bar. Its t0 signal remains recent when t1 closes.
            _bar("BBB", t2, o=102, h=104, l=98, c=99, v=5000),
        ],
    )

    runner = PortfolioReplayRunner(settings, signal_evaluator=fake_eval, strategy_name="breakout")
    trades = runner.run([aaa, bbb])

    assert [trade.symbol for trade in trades] == ["AAA", "BBB"]


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
            _bar(symbol, t0, o=100, h=106, l=99, c=100.5, v=5000),
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

    runner._resolve_order(lane, lane.intraday[lane.cursor], 100000.0, set())

    assert lane.position is not None
    assert lane.position.quantity == 123.0


def test_portfolio_order_can_fill_on_second_configured_active_bar():
    settings = Settings.from_env({
        **ENV,
        "ENTRY_ORDER_ACTIVE_BARS": "2",
        "REPLAY_SLIPPAGE_BPS": "0",
    })
    t0 = _utc(2026, 1, 2, 14, 30)
    t1 = _utc(2026, 1, 2, 14, 45)
    t2 = _utc(2026, 1, 2, 15, 0)
    runner = PortfolioReplayRunner(
        settings,
        signal_evaluator=lambda **k: None,
        strategy_name="breakout",
    )
    runner._index_scenarios([
        ReplayScenario(
            name="AAA",
            symbol="AAA",
            starting_equity=100000.0,
            daily_bars=[_bar("AAA", _utc(2026, 1, 1, 5, 0))],
            intraday_bars=[
                _bar("AAA", t0, o=99.0, h=99.5, l=98.5, c=99.0, v=5000),
                _bar("AAA", t1, o=99.0, h=99.5, l=98.5, c=99.0, v=5000),
                _bar("AAA", t2, o=99.5, h=100.5, l=99.0, c=100.2, v=5000),
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
            quantity=10.0,
            stop_price=100.0,
            limit_price=101.0,
            initial_stop_price=95.0,
        ),
    )

    assert lane.working_order is not None
    assert lane.working_order.expires_at_timestamp == t2

    lane.cursor = 1
    traded = set()
    runner._resolve_order(lane, lane.intraday[lane.cursor], 100000.0, traded)

    assert lane.working_order is not None
    assert lane.position is None
    assert traded == set()

    lane.cursor = 2
    runner._resolve_order(lane, lane.intraday[lane.cursor], 100000.0, traded)

    assert lane.working_order is None
    assert lane.position is not None
    assert lane.position.entry_timestamp == t2


def test_portfolio_order_fill_preserves_strategy_name():
    settings = Settings.from_env({**ENV, "REPLAY_SLIPPAGE_BPS": "0"})
    t0 = _utc(2026, 1, 2, 14, 30)
    t1 = _utc(2026, 1, 2, 14, 45)
    runner = PortfolioReplayRunner(
        settings,
        signal_evaluator=lambda **k: None,
        strategy_name="bull_flag",
    )
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
            quantity=10.0,
            stop_price=100.0,
            limit_price=101.0,
            initial_stop_price=95.0,
            strategy_name="momentum",
        ),
    )

    assert lane.working_order is not None
    assert lane.working_order.strategy_name == "momentum"

    lane.cursor = 1
    runner._resolve_order(lane, lane.intraday[lane.cursor], 100000.0, set())

    assert lane.position is not None
    assert lane.position.strategy_name == "momentum"


def test_portfolio_eod_exit_preserves_viability_reason():
    settings = Settings.from_env({**ENV, "REPLAY_SLIPPAGE_BPS": "0"})
    t0 = _utc(2026, 1, 2, 14, 30)
    t1 = _utc(2026, 1, 2, 14, 45)
    runner = PortfolioReplayRunner(
        settings,
        signal_evaluator=lambda **k: None,
        strategy_name="breakout",
    )
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
            quantity=10.0,
            stop_price=100.0,
            limit_price=101.0,
            initial_stop_price=95.0,
        ),
    )
    lane.cursor = 1
    runner._resolve_order(lane, lane.intraday[lane.cursor], 100000.0, set())

    trade, _equity = runner._eod_exit(
        lane,
        lane.intraday[lane.cursor],
        100000.0,
        set(),
        reason="viability_vwap_breakdown",
    )

    assert trade is not None
    assert trade.exit_reason == "viability_vwap_breakdown"


def test_portfolio_basket_blocks_duplicate_symbol_across_strategies():
    settings = Settings.from_env(
        {
            **ENV,
            "MAX_OPEN_POSITIONS": "2",
            "REPLAY_SLIPPAGE_BPS": "0",
            "ENTRY_MIN_CLOSE_TO_ENTRY_PCT": "-1.0",
        }
    )
    t0 = _utc(2026, 1, 2, 14, 30)
    t1 = _utc(2026, 1, 2, 14, 45)
    t2 = _utc(2026, 1, 2, 15, 0)
    from alpaca_bot.domain.models import EntrySignal

    def alpha(*, symbol, intraday_bars, signal_index, daily_bars, settings):
        del daily_bars, settings
        bar = intraday_bars[signal_index]
        if symbol == "AAA" and bar.timestamp == t0:
            return EntrySignal(
                symbol=symbol,
                signal_bar=bar,
                entry_level=100.0,
                relative_volume=2.0,
                stop_price=100.0,
                limit_price=101.0,
                initial_stop_price=95.0,
                option_contract=None,
            )
        return None

    def beta(*, symbol, intraday_bars, signal_index, daily_bars, settings):
        del daily_bars, settings
        bar = intraday_bars[signal_index]
        if symbol == "AAA" and bar.timestamp == t0:
            return EntrySignal(
                symbol=symbol,
                signal_bar=bar,
                entry_level=100.0,
                relative_volume=2.0,
                stop_price=100.0,
                limit_price=101.0,
                initial_stop_price=95.0,
                option_contract=None,
            )
        return None

    scenario = ReplayScenario(
        name="AAA",
        symbol="AAA",
        starting_equity=100000.0,
        daily_bars=[_bar("AAA", _utc(2026, 1, 1, 5, 0))],
        intraday_bars=[
            _bar("AAA", t0, o=100, h=101, l=99, c=100, v=5000),
            _bar("AAA", t1, o=100, h=102, l=99, c=101, v=5000),
            _bar("AAA", t2, o=101, h=102, l=80, c=99, v=5000),
        ],
    )

    runner = PortfolioBasketReplayRunner(
        settings,
        strategies=(("alpha", alpha), ("beta", beta)),
    )

    trades = runner.run([scenario])

    assert len(trades) == 1
    assert trades[0].symbol == "AAA"


def test_portfolio_basket_consumes_global_slots_across_strategies():
    settings = Settings.from_env(
        {
            **ENV,
            "MAX_OPEN_POSITIONS": "1",
            "REPLAY_SLIPPAGE_BPS": "0",
            "ENTRY_MIN_CLOSE_TO_ENTRY_PCT": "-1.0",
        }
    )
    t0 = _utc(2026, 1, 2, 14, 30)
    t1 = _utc(2026, 1, 2, 14, 45)
    t2 = _utc(2026, 1, 2, 15, 0)
    from alpaca_bot.domain.models import EntrySignal

    def alpha(*, symbol, intraday_bars, signal_index, daily_bars, settings):
        del daily_bars, settings
        bar = intraday_bars[signal_index]
        if symbol == "AAA" and bar.timestamp == t0:
            return EntrySignal(
                symbol=symbol,
                signal_bar=bar,
                entry_level=100.0,
                relative_volume=2.0,
                stop_price=100.0,
                limit_price=101.0,
                initial_stop_price=95.0,
                option_contract=None,
            )
        return None

    def beta(*, symbol, intraday_bars, signal_index, daily_bars, settings):
        del daily_bars, settings
        bar = intraday_bars[signal_index]
        if symbol == "BBB" and bar.timestamp == t0:
            return EntrySignal(
                symbol=symbol,
                signal_bar=bar,
                entry_level=100.0,
                relative_volume=2.0,
                stop_price=100.0,
                limit_price=101.0,
                initial_stop_price=95.0,
                option_contract=None,
            )
        return None

    def scenario(symbol):
        return ReplayScenario(
            name=symbol,
            symbol=symbol,
            starting_equity=100000.0,
            daily_bars=[_bar(symbol, _utc(2026, 1, 1, 5, 0))],
            intraday_bars=[
                _bar(symbol, t0, o=100, h=101, l=99, c=100, v=5000),
                _bar(symbol, t1, o=100, h=102, l=99, c=101, v=5000),
                _bar(symbol, t2, o=101, h=102, l=80, c=99, v=5000),
            ],
        )

    runner = PortfolioBasketReplayRunner(
        settings,
        strategies=(("alpha", alpha), ("beta", beta)),
    )

    trades = runner.run([scenario("AAA"), scenario("BBB")])

    assert [trade.symbol for trade in trades] == ["AAA"]


def test_portfolio_basket_applies_strategy_equity_scales(monkeypatch):
    settings = Settings.from_env(ENV)
    t0 = _utc(2026, 1, 2, 14, 30)
    scenario = ReplayScenario(
        name="AAA",
        symbol="AAA",
        starting_equity=100000.0,
        daily_bars=[_bar("AAA", _utc(2026, 1, 1, 5, 0))],
        intraday_bars=[_bar("AAA", t0, o=100, h=101, l=99, c=100, v=5000)],
    )
    seen: list[tuple[str, float]] = []

    def fake_evaluate_cycle(**kwargs):
        seen.append((kwargs["strategy_name"], kwargs["equity"]))
        return CycleResult(as_of=kwargs["now"])

    import alpaca_bot.replay.portfolio as portfolio_module

    monkeypatch.setattr(portfolio_module, "evaluate_cycle", fake_evaluate_cycle)
    runner = PortfolioBasketReplayRunner(
        settings,
        strategies=(("alpha", lambda **k: None), ("beta", lambda **k: None)),
        strategy_equity_scales={"alpha": 1.0, "beta": 0.25},
    )

    runner.run([scenario])

    assert seen == [("alpha", 100000.0), ("beta", 25000.0)]
