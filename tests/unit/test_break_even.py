from alpaca_bot.replay.break_even import (
    BreakEvenPoint,
    _interpolate_break_even,
)


def _pt(bps: float, ci_low: float | None) -> BreakEvenPoint:
    return BreakEvenPoint(
        slippage_bps=bps,
        trades=100,
        mean_trade_pnl=1.0,
        total_pnl=100.0,
        ci_low=ci_low,
        ci_high=None if ci_low is None else ci_low + 10.0,
        p_positive=0.05,
        verdict="no-evidence",
    )


def test_interpolate_returns_linear_zero_crossing():
    # ci_low: +4 at 3 bps, -1 at 4 bps -> crossing at 3 + 1*(4/5) = 3.8
    points = [_pt(3.0, 4.0), _pt(4.0, -1.0)]
    assert _interpolate_break_even(points) == 3.8


def test_interpolate_all_positive_returns_none():
    points = [_pt(0.0, 5.0), _pt(5.0, 1.0)]
    assert _interpolate_break_even(points) is None


def test_interpolate_frictionless_negative_returns_zero():
    points = [_pt(0.0, -2.0), _pt(5.0, -8.0)]
    assert _interpolate_break_even(points) == 0.0


def test_interpolate_first_rung_none_returns_none():
    points = [_pt(0.0, None), _pt(5.0, -1.0)]
    assert _interpolate_break_even(points) is None


def test_interpolate_skips_none_midladder_and_brackets_valid_pair():
    # 0->+3, 1->None, 2->-1 : first valid bracket is (0,2): 0 + 2*(3/4) = 1.5
    points = [_pt(0.0, 3.0), _pt(1.0, None), _pt(2.0, -1.0)]
    assert _interpolate_break_even(points) == 1.5


from datetime import datetime, timezone

from alpaca_bot.config import Settings
from alpaca_bot.replay.break_even import run_break_even_sweep
from alpaca_bot.replay.report import ReplayTradeRecord


def _settings() -> Settings:
    # Hermetic paper-mode base from an explicit env dict — the project idiom
    # (see _base_settings in test_lever_sweep.py). NEVER bare
    # Settings.from_env(): that reads ambient os.environ and is non-hermetic.
    return Settings.from_env({
        "TRADING_MODE": "paper",
        "ENABLE_LIVE_TRADING": "false",
        "STRATEGY_VERSION": "v1-breakout",
        "DATABASE_URL": "postgresql://alpaca_bot:secret@db.example.com:5432/alpaca_bot",
        "MARKET_DATA_FEED": "sip",
        "SYMBOLS": "AAPL",
        "ENTRY_TIMEFRAME_MINUTES": "15",
    })


def _trade(pnl: float) -> ReplayTradeRecord:
    t = datetime(2026, 1, 2, 15, 0, tzinfo=timezone.utc)
    return ReplayTradeRecord(
        symbol="AAA",
        entry_price=100.0,
        exit_price=100.0 + pnl,
        quantity=1,
        entry_time=t,
        exit_time=t,
        exit_reason="eod",
        pnl=pnl,
        return_pct=pnl / 100.0,
    )


def test_sweep_runs_once_per_rung_with_slippage_threaded():
    seen_bps: list[float] = []

    def fake_pooled(scenarios, settings, strategy):
        seen_bps.append(settings.replay_slippage_bps)
        # Mean pnl falls 2.0 per bps: edge crosses zero between 2 and 3 bps.
        per_trade = 5.0 - 2.0 * settings.replay_slippage_bps
        return [_trade(per_trade + j * 0.01) for j in range(40)]

    result = run_break_even_sweep(
        scenarios=[object()],
        settings=_settings(),
        strategy="bull_flag",
        slippage_ladder=(0.0, 1.0, 2.0, 3.0, 4.0, 5.0),
        pooled_trades_fn=fake_pooled,
    )

    assert seen_bps == [0.0, 1.0, 2.0, 3.0, 4.0, 5.0]
    assert [p.slippage_bps for p in result.points] == [0.0, 1.0, 2.0, 3.0, 4.0, 5.0]
    assert result.points[0].trades == 40
    assert result.strategy == "bull_flag"
    assert result.scenarios == 1
    # ci_low decreases with cost; break-even is a positive finite bps in-ladder.
    assert result.break_even_bps is not None
    assert 0.0 < result.break_even_bps < 5.0


def test_sweep_insufficient_trades_yields_none_ci():
    def fake_pooled(scenarios, settings, strategy):
        return [_trade(1.0), _trade(2.0)]  # < MIN_SAMPLES

    result = run_break_even_sweep(
        scenarios=[object()],
        settings=_settings(),
        strategy="bull_flag",
        slippage_ladder=(0.0, 5.0),
        pooled_trades_fn=fake_pooled,
    )
    assert result.points[0].ci_low is None
    assert result.points[0].verdict == "insufficient-data"
    assert result.break_even_bps is None
