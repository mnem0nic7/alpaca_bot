# tests/unit/test_replay_mechanics.py
from datetime import datetime, timezone

from alpaca_bot.domain.models import Bar
from alpaca_bot.replay.mechanics import (
    apply_slippage,
    simulate_buy_stop_limit_fill,
    entry_fill_price,
    eod_exit_price,
)


def _bar(o, h, l, c):
    return Bar(
        symbol="AAA",
        timestamp=datetime(2026, 1, 2, 14, 30, tzinfo=timezone.utc),
        open=o, high=h, low=l, close=c, volume=1000,
    )


def test_apply_slippage_buy_raises_sell_lowers():
    assert apply_slippage(100.0, side="buy", bps=10.0) == 100.1
    assert apply_slippage(100.0, side="sell", bps=10.0) == 99.9


def test_apply_slippage_zero_is_identity():
    assert apply_slippage(100.0, side="buy", bps=0.0) == 100.0


def test_simulate_buy_stop_limit_fill_matches_existing_rules():
    # Open above limit -> no fill
    assert simulate_buy_stop_limit_fill(bar=_bar(101, 102, 100, 101), stop_price=100.5, limit_price=100.5) is None
    # High below stop -> no fill
    assert simulate_buy_stop_limit_fill(bar=_bar(99, 100, 98, 99.5), stop_price=100.5, limit_price=101.0) is None
    # Normal fill at max(open, stop)
    assert simulate_buy_stop_limit_fill(bar=_bar(99.8, 101, 99, 100.5), stop_price=100.0, limit_price=101.0) == 100.0


def test_entry_fill_price_capped_at_limit():
    # raw fill slipped up, but capped at limit
    assert entry_fill_price(raw_fill=100.0, limit_price=100.05, bps=10.0) == 100.05
    # slipped fill below limit passes through
    assert entry_fill_price(raw_fill=100.0, limit_price=101.0, bps=10.0) == 100.1


def test_eod_exit_price_applies_sell_slippage():
    assert eod_exit_price(bar=_bar(100, 101, 99, 100.0), bps=10.0) == 99.9
