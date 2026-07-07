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
    is_regime_daily = None
    oos_regime_daily = None
    if scenario.regime_daily_bars:
        is_regime_daily, oos_regime_daily = _split_daily_context(
            scenario.regime_daily_bars,
            last_is_date=last_is_date,
            daily_warmup=daily_warmup,
        )
    is_vix_daily = None
    oos_vix_daily = None
    if scenario.vix_daily_bars:
        is_vix_daily, oos_vix_daily = _split_daily_context(
            scenario.vix_daily_bars,
            last_is_date=last_is_date,
            daily_warmup=daily_warmup,
        )
    is_sector_daily_by_etf = None
    oos_sector_daily_by_etf = None
    if scenario.sector_daily_bars_by_etf:
        is_sector_daily_by_etf = {}
        oos_sector_daily_by_etf = {}
        for etf, bars in scenario.sector_daily_bars_by_etf.items():
            is_context, oos_context = _split_daily_context(
                bars,
                last_is_date=last_is_date,
                daily_warmup=daily_warmup,
            )
            is_sector_daily_by_etf[etf] = is_context
            oos_sector_daily_by_etf[etf] = oos_context

    return (
        ReplayScenario(
            name=f"{scenario.name}_is",
            symbol=scenario.symbol,
            starting_equity=scenario.starting_equity,
            daily_bars=is_daily,
            intraday_bars=is_intraday,
            regime_daily_bars=is_regime_daily,
            vix_daily_bars=is_vix_daily,
            sector_daily_bars_by_etf=is_sector_daily_by_etf,
        ),
        ReplayScenario(
            name=f"{scenario.name}_oos",
            symbol=scenario.symbol,
            starting_equity=scenario.starting_equity,
            daily_bars=oos_daily,
            intraday_bars=oos_intraday,
            regime_daily_bars=oos_regime_daily,
            vix_daily_bars=oos_vix_daily,
            sector_daily_bars_by_etf=oos_sector_daily_by_etf,
        ),
    )


def _split_daily_context(
    bars: list[Bar],
    *,
    last_is_date: date,
    daily_warmup: int,
) -> tuple[list[Bar], list[Bar]]:
    is_bars = [b for b in bars if b.timestamp.date() <= last_is_date]
    warmup = is_bars[-daily_warmup:] if len(is_bars) >= daily_warmup else is_bars[:]
    oos_tail = [b for b in bars if b.timestamp.date() > last_is_date]
    return is_bars, warmup + oos_tail
