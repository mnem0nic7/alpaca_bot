from __future__ import annotations

import math
from datetime import date

from alpaca_bot.domain.models import Bar, ReplayScenario


def split_scenario(
    scenario: ReplayScenario,
    *,
    in_sample_ratio: float = 0.8,
    daily_warmup: int = 30,
) -> tuple[ReplayScenario, ReplayScenario]:
    """Split a scenario chronologically into in-sample and out-of-sample halves.

    in_sample_ratio — fraction of unique trading dates allocated to IS (default 0.8)
    daily_warmup    — IS daily bars prepended to OOS daily_bars so that SMA/ATR
                      lookbacks have history at the start of OOS. Must be >= the
                      largest DAILY_SMA_PERIOD or ATR_PERIOD value in the sweep grid
                      (default 30 covers the max in STRATEGY_GRIDS).
    """
    all_dates: list[date] = sorted(
        {b.timestamp.date() for b in scenario.intraday_bars}
    )
    n = len(all_dates)
    if n < 10:
        raise ValueError(
            f"scenario '{scenario.name}' too short to split: "
            f"need at least 10 trading dates, got {n}"
        )

    split_idx = max(1, math.ceil(n * in_sample_ratio))
    split_idx = min(split_idx, n - 1)  # ensure at least 1 OOS date

    is_dates = set(all_dates[:split_idx])
    oos_dates = set(all_dates[split_idx:])
    last_is_date = all_dates[split_idx - 1]

    is_intraday = [b for b in scenario.intraday_bars if b.timestamp.date() in is_dates]
    oos_intraday = [b for b in scenario.intraday_bars if b.timestamp.date() in oos_dates]

    is_daily = [b for b in scenario.daily_bars if b.timestamp.date() <= last_is_date]
    warmup = is_daily[-daily_warmup:] if len(is_daily) >= daily_warmup else is_daily[:]
    oos_daily_tail = [b for b in scenario.daily_bars if b.timestamp.date() > last_is_date]
    oos_daily = warmup + oos_daily_tail

    return (
        ReplayScenario(
            name=f"{scenario.name}_is",
            symbol=scenario.symbol,
            starting_equity=scenario.starting_equity,
            daily_bars=is_daily,
            intraday_bars=is_intraday,
        ),
        ReplayScenario(
            name=f"{scenario.name}_oos",
            symbol=scenario.symbol,
            starting_equity=scenario.starting_equity,
            daily_bars=oos_daily,
            intraday_bars=oos_intraday,
        ),
    )
