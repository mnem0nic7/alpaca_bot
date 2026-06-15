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
