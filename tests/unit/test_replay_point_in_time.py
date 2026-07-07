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
from alpaca_bot.core.engine import CycleResult
from alpaca_bot.domain import Bar
from alpaca_bot.domain.enums import IntentType
from alpaca_bot.domain.models import EntrySignal, MarketContext, ReplayScenario
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


def test_daily_slice_is_point_in_time() -> None:
    """Defect 2: on session day D the evaluator must never see a daily bar
    dated >= D, and the slice must grow as the scenario crosses days."""
    settings = make_settings()
    captured: list[tuple[datetime, tuple[Bar, ...]]] = []

    def capturing_evaluator(*, symbol, intraday_bars, signal_index, daily_bars, settings):
        captured.append((intraday_bars[signal_index].timestamp, tuple(daily_bars)))
        return None

    # 22 daily bars 2026-04-03..2026-04-24 — includes bars dated ON both
    # intraday days, which the slice must hide.
    daily = _daily_bars(
        "AAPL", start=datetime(2026, 4, 3, 20, 0, tzinfo=timezone.utc), count=22
    )
    intraday: list[Bar] = []
    for day_num in (23, 24):
        t0 = datetime(2026, 4, day_num, 14, 30, tzinfo=timezone.utc)  # 10:30 ET
        intraday.extend(
            Bar(
                symbol="AAPL",
                timestamp=t0 + timedelta(minutes=15 * i),
                open=100.0,
                high=100.5,
                low=99.5,
                close=100.0,
                volume=1_000_000,
            )
            for i in range(8)
        )
    scenario = ReplayScenario(
        name="two-day",
        symbol="AAPL",
        starting_equity=100_000.0,
        daily_bars=daily,
        intraday_bars=intraday,
    )

    ReplayRunner(settings, signal_evaluator=capturing_evaluator).run(scenario)

    assert captured, "evaluator was never invoked"
    tz = settings.market_timezone
    sizes: dict[date, int] = {}
    for signal_ts, daily_slice in captured:
        day = session_day(signal_ts, settings)
        assert daily_slice, "daily slice was empty"
        assert max(b.timestamp.astimezone(tz).date() for b in daily_slice) < day
        sizes[day] = len(daily_slice)
    assert sorted(sizes) == [date(2026, 4, 23), date(2026, 4, 24)]
    # Exactly one more completed day visible on the second session day.
    assert sizes[date(2026, 4, 24)] == sizes[date(2026, 4, 23)] + 1


def _breakout_day(symbol: str, day_start_utc: datetime) -> list[Bar]:
    """One session: 20 quiet bars from 10:00 ET, a high-volume breakout bar at
    15:00 ET, an execution bar, and bars out to the 15:45 ET flatten."""
    t0 = day_start_utc.replace(hour=14, minute=0)  # 10:00 ET
    bars = [
        Bar(
            symbol=symbol,
            timestamp=t0 + timedelta(minutes=15 * i),
            open=100.0,
            high=100.5,
            low=99.5,
            close=100.0,
            volume=1_000_000,
        )
        for i in range(20)
    ]
    breakout_ts = t0 + timedelta(minutes=15 * 20)  # 15:00 ET, inside entry window
    bars.append(
        Bar(symbol=symbol, timestamp=breakout_ts,
            open=100.4, high=102.0, low=100.3, close=101.8, volume=2_500_000)
    )
    bars.append(  # execution bar: opens above stop 100.51, below limit 100.61
        Bar(symbol=symbol, timestamp=breakout_ts + timedelta(minutes=15),
            open=100.55, high=101.2, low=100.4, close=100.9, volume=1_200_000)
    )
    bars.append(
        Bar(symbol=symbol, timestamp=breakout_ts + timedelta(minutes=30),
            open=100.9, high=101.0, low=100.5, close=100.8, volume=900_000)
    )
    bars.append(  # 15:45 ET — engine emits the EOD flatten exit here
        Bar(symbol=symbol, timestamp=breakout_ts + timedelta(minutes=45),
            open=100.8, high=100.9, low=100.4, close=100.6, volume=900_000)
    )
    return bars


def test_trend_gate_varies_within_scenario() -> None:
    """Defect 2 end-to-end with the real breakout evaluator: an uptrend that
    breaks mid-scenario must allow entries while intact and block them after.

    Old behavior: the full-series trend filter saw the post-crash close on
    every bar, so the entire scenario produced zero entries."""
    settings = make_settings()
    symbol = "AAPL"
    rising = [  # 2026-03-28 .. 2026-04-21, close 100 -> 124
        Bar(
            symbol=symbol,
            timestamp=datetime(2026, 3, 28, 20, 0, tzinfo=timezone.utc) + timedelta(days=i),
            open=99.5 + i,
            high=100.5 + i,
            low=99.0 + i,
            close=100.0 + i,
            volume=1_000_000,
        )
        for i in range(25)
    ]
    crash = Bar(
        symbol=symbol,
        timestamp=datetime(2026, 4, 22, 20, 0, tzinfo=timezone.utc),
        open=123.5, high=124.0, low=79.5, close=80.0, volume=5_000_000,
    )
    flat = [
        Bar(
            symbol=symbol,
            timestamp=datetime(2026, 4, d, 20, 0, tzinfo=timezone.utc),
            open=80.0, high=80.5, low=79.5, close=80.0, volume=1_000_000,
        )
        for d in (23, 24)
    ]
    daily = rising + [crash] + flat

    intraday = _breakout_day(symbol, datetime(2026, 4, 23, tzinfo=timezone.utc)) + _breakout_day(
        symbol, datetime(2026, 4, 24, tzinfo=timezone.utc)
    )
    scenario = ReplayScenario(
        name="trend-breaks",
        symbol=symbol,
        starting_equity=100_000.0,
        daily_bars=daily,
        intraday_bars=intraday,
    )

    result = ReplayRunner(settings).run(scenario)

    placed = [e for e in result.events if e.event_type == IntentType.ENTRY_ORDER_PLACED]
    assert len(placed) == 1, f"expected exactly one entry (day 1 only), got {len(placed)}"
    assert session_day(placed[0].timestamp, settings) == date(2026, 4, 23)


def test_regime_bars_are_point_in_time_not_full_scenario(monkeypatch) -> None:
    settings = make_settings(
        ENABLE_REGIME_FILTER="true",
        REGIME_SYMBOL="SPY",
        REGIME_SMA_PERIOD="2",
    )
    symbol = "NVDA"
    session = datetime(2026, 4, 24, 14, 30, tzinfo=timezone.utc)
    intraday = [
        Bar(
            symbol=symbol,
            timestamp=session + timedelta(minutes=15 * i),
            open=100.0,
            high=101.0,
            low=99.0,
            close=100.5,
            volume=1_000_000,
        )
        for i in range(3)
    ]
    regime_daily = [
        Bar(
            symbol="SPY",
            timestamp=datetime(2026, 4, day, 20, 0, tzinfo=timezone.utc),
            open=500.0,
            high=501.0,
            low=499.0,
            close=500.0 + day,
            volume=10_000_000,
        )
        for day in (21, 22, 23, 24)
    ]
    seen_regime_dates: list[tuple[date, ...]] = []

    def fake_evaluate_cycle(**kwargs):
        bars = kwargs["regime_bars"]
        seen_regime_dates.append(tuple(bar.timestamp.date() for bar in bars or ()))
        return CycleResult(as_of=kwargs["now"])

    import alpaca_bot.replay.runner as runner_module

    monkeypatch.setattr(runner_module, "evaluate_cycle", fake_evaluate_cycle)
    scenario = ReplayScenario(
        name="regime-point-in-time",
        symbol=symbol,
        starting_equity=100_000.0,
        daily_bars=[],
        intraday_bars=intraday,
        regime_daily_bars=regime_daily,
    )

    ReplayRunner(settings).run(scenario)

    assert seen_regime_dates
    assert all(date(2026, 4, 24) not in dates for dates in seen_regime_dates)
    assert seen_regime_dates[0] == (
        date(2026, 4, 21),
        date(2026, 4, 22),
        date(2026, 4, 23),
    )


def test_market_context_bars_are_point_in_time(monkeypatch) -> None:
    settings = make_settings(
        ENABLE_VIX_FILTER="true",
        ENABLE_SECTOR_FILTER="true",
        VIX_LOOKBACK_BARS="2",
        SECTOR_ETF_SYMBOLS="XLK",
        SECTOR_ETF_SMA_PERIOD="2",
    )
    symbol = "NVDA"
    session = datetime(2026, 4, 24, 14, 30, tzinfo=timezone.utc)
    intraday = [
        Bar(
            symbol=symbol,
            timestamp=session + timedelta(minutes=15 * i),
            open=100.0,
            high=101.0,
            low=99.0,
            close=100.5,
            volume=1_000_000,
        )
        for i in range(3)
    ]
    vix_daily = _daily_bars(
        "VIXY", start=datetime(2026, 4, 21, 20, 0, tzinfo=timezone.utc), count=4
    )
    sector_daily = _daily_bars(
        "XLK", start=datetime(2026, 4, 21, 20, 0, tzinfo=timezone.utc), count=4
    )
    seen_context_dates: list[tuple[tuple[date, ...], tuple[date, ...]]] = []
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

    import alpaca_bot.replay.runner as runner_module

    monkeypatch.setattr(
        runner_module, "compute_market_context", fake_compute_market_context
    )
    monkeypatch.setattr(runner_module, "evaluate_cycle", fake_evaluate_cycle)
    scenario = ReplayScenario(
        name="context-point-in-time",
        symbol=symbol,
        starting_equity=100_000.0,
        daily_bars=[],
        intraday_bars=intraday,
        vix_daily_bars=vix_daily,
        sector_daily_bars_by_etf={"XLK": sector_daily},
    )

    ReplayRunner(settings).run(scenario)

    assert seen_context_dates
    for vix_dates, sector_dates in seen_context_dates:
        assert date(2026, 4, 24) not in vix_dates
        assert date(2026, 4, 24) not in sector_dates
    assert seen_context_dates[0] == (
        (date(2026, 4, 21), date(2026, 4, 22), date(2026, 4, 23)),
        (date(2026, 4, 21), date(2026, 4, 22), date(2026, 4, 23)),
    )
    assert seen_market_context and all(ctx is not None for ctx in seen_market_context)
