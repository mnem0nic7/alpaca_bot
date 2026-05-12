from datetime import date, datetime, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from alpaca_bot.domain.models import Bar, OptionContract


def _settings(**overrides) -> object:
    base = dict(
        daily_sma_period=5,
        breakout_lookback_bars=3,
        relative_volume_lookback_bars=3,
        relative_volume_threshold=1.5,
        atr_period=3,
        atr_stop_multiplier=1.0,
        entry_stop_price_buffer=0.01,
        ema_period=9,
        high_watermark_lookback_days=5,
        orb_opening_bars=2,
        vwap_dip_threshold_pct=0.015,
        gap_threshold_pct=0.02,
        gap_volume_threshold=2.0,
        bull_flag_min_run_pct=0.02,
        bull_flag_consolidation_volume_ratio=0.6,
        bull_flag_consolidation_range_pct=0.5,
        bb_period=5,
        bb_std_dev=2.0,
        bb_squeeze_threshold_pct=0.03,
        bb_squeeze_min_bars=2,
        failed_breakdown_volume_ratio=2.0,
        failed_breakdown_recapture_buffer_pct=0.001,
        option_dte_min=21,
        option_dte_max=60,
        option_delta_target=0.5,
        option_max_spread_pct=0.50,
        option_min_open_interest=0,
        market_timezone=ZoneInfo("America/New_York"),
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _bar(
    close: float,
    *,
    ts: datetime | None = None,
    volume: float = 1000.0,
    high: float | None = None,
    low: float | None = None,
    open: float | None = None,
) -> Bar:
    ts = ts or datetime(2024, 1, 15, 10, 0, tzinfo=timezone.utc)
    return Bar(
        symbol="AAPL",
        timestamp=ts,
        open=open if open is not None else close,
        high=high if high is not None else close,
        low=low if low is not None else close,
        close=close,
        volume=volume,
    )


def _downtrend_daily_bars() -> list[Bar]:
    """7 daily bars where prior close (80) is below 5-bar SMA (104) → downtrend True.

    window = daily_bars[-6:-1] = bars[1:6], closes=[110,110,110,110,80], sma=104
    latest_close = window[-1].close = 80, 80 < 104 → True
    """
    closes = [110, 110, 110, 110, 110, 80, 80]
    bars = []
    for i, c in enumerate(closes):
        d = date(2024, 1, 8 + i)
        ts = datetime(d.year, d.month, d.day, 16, 0, tzinfo=timezone.utc)
        bars.append(
            Bar(symbol="AAPL", timestamp=ts, open=c, high=c + 2, low=c - 2, close=c, volume=1000.0)
        )
    return bars


def _uptrend_daily_bars() -> list[Bar]:
    """7 daily bars where prior close (100) is above 5-bar SMA (80) → downtrend False.

    window = bars[1:6], closes=[80,80,80,80,100], sma=84
    latest_close = window[-1].close = 100, 100 < 84 → False
    """
    closes = [80, 80, 80, 80, 80, 100, 100]
    bars = []
    for i, c in enumerate(closes):
        d = date(2024, 1, 8 + i)
        ts = datetime(d.year, d.month, d.day, 16, 0, tzinfo=timezone.utc)
        bars.append(
            Bar(symbol="AAPL", timestamp=ts, open=c, high=c + 2, low=c - 2, close=c, volume=1000.0)
        )
    return bars


def _put_contract(*, strike: float = 95.0, delta: float = -0.5) -> OptionContract:
    expiry = date(2024, 2, 16)
    return OptionContract(
        occ_symbol=f"AAPL{expiry.strftime('%y%m%d')}P{int(strike * 1000):08d}",
        underlying="AAPL",
        option_type="put",
        strike=strike,
        expiry=expiry,
        bid=2.0,
        ask=2.10,
        delta=delta,
    )
