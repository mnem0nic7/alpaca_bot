from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from alpaca_bot.backfill.fetcher import BackfillFetcher
from alpaca_bot.domain.models import Bar
from alpaca_bot.replay.runner import ReplayRunner


def _make_settings(**overrides):
    from alpaca_bot.config import Settings, TradingMode, MarketDataFeed
    from datetime import time

    defaults = dict(
        trading_mode=TradingMode.PAPER,
        enable_live_trading=False,
        strategy_version="v1",
        database_url="postgresql://localhost/test",
        market_data_feed=MarketDataFeed.SIP,
        symbols=("AAPL",),
        daily_sma_period=5,
        breakout_lookback_bars=5,
        relative_volume_lookback_bars=5,
        relative_volume_threshold=1.5,
        entry_timeframe_minutes=15,
        risk_per_trade_pct=0.01,
        max_position_pct=0.1,
        max_open_positions=3,
        daily_loss_limit_pct=0.01,
        stop_limit_buffer_pct=0.001,
        breakout_stop_buffer_pct=0.001,
        entry_stop_price_buffer=0.01,
        entry_window_start=time(10, 0),
        entry_window_end=time(15, 30),
        flatten_time=time(15, 45),
        prior_day_high_lookback_bars=1,
    )
    defaults.update(overrides)
    return Settings(**defaults)


def _make_bar(symbol: str, ts: datetime, price: float = 100.0) -> Bar:
    return Bar(
        symbol=symbol,
        timestamp=ts,
        open=price - 0.5,
        high=price + 1.0,
        low=price - 1.0,
        close=price,
        volume=10_000.0,
    )


class FakeAdapter:
    def __init__(self, daily: dict, intraday: dict):
        self._daily = daily
        self._intraday = intraday

    def get_daily_bars(self, *, symbols, start, end):
        return {s: self._daily.get(s, []) for s in symbols}

    def get_stock_bars(self, *, symbols, start, end, timeframe_minutes):
        return {s: self._intraday.get(s, []) for s in symbols}


_TS = datetime(2026, 1, 2, 15, 0, tzinfo=timezone.utc)
_DAILY_BARS = [_make_bar("AAPL", _TS, 150.0 + i) for i in range(5)]
_INTRADAY_BARS = [_make_bar("AAPL", _TS, 151.0 + i * 0.1) for i in range(20)]


def test_fetch_writes_one_file_per_symbol(tmp_path):
    adapter = FakeAdapter(
        daily={"AAPL": _DAILY_BARS, "MSFT": [_make_bar("MSFT", _TS)]},
        intraday={"AAPL": _INTRADAY_BARS, "MSFT": [_make_bar("MSFT", _TS)]},
    )
    settings = _make_settings(symbols=("AAPL", "MSFT"))
    fetcher = BackfillFetcher(adapter, settings)
    results = fetcher.fetch_and_save(symbols=["AAPL", "MSFT"], days=10, output_dir=tmp_path)
    assert len(results) == 2
    assert (tmp_path / "AAPL_10d.json").exists()
    assert (tmp_path / "MSFT_10d.json").exists()


def test_fetch_file_is_loadable_by_replay_runner(tmp_path):
    adapter = FakeAdapter(daily={"AAPL": _DAILY_BARS}, intraday={"AAPL": _INTRADAY_BARS})
    settings = _make_settings()
    fetcher = BackfillFetcher(adapter, settings)
    fetcher.fetch_and_save(symbols=["AAPL"], days=252, output_dir=tmp_path)
    scenario = ReplayRunner.load_scenario(tmp_path / "AAPL_252d.json")
    assert scenario.symbol == "AAPL"
    assert len(scenario.daily_bars) == 5
    assert len(scenario.intraday_bars) == 20


def test_fetch_skips_symbol_with_no_bars(tmp_path):
    adapter = FakeAdapter(daily={}, intraday={})
    settings = _make_settings()
    fetcher = BackfillFetcher(adapter, settings)
    results = fetcher.fetch_and_save(symbols=["AAPL"], days=10, output_dir=tmp_path)
    assert results == []
    assert not (tmp_path / "AAPL_10d.json").exists()


def test_fetch_filename_convention(tmp_path):
    adapter = FakeAdapter(daily={"AAPL": _DAILY_BARS}, intraday={"AAPL": _INTRADAY_BARS})
    settings = _make_settings()
    fetcher = BackfillFetcher(adapter, settings)
    [(path, _, _)] = fetcher.fetch_and_save(symbols=["AAPL"], days=90, output_dir=tmp_path)
    assert path.name == "AAPL_90d.json"


def test_fetch_respects_output_dir(tmp_path):
    sub = tmp_path / "custom" / "dir"
    adapter = FakeAdapter(daily={"AAPL": _DAILY_BARS}, intraday={"AAPL": _INTRADAY_BARS})
    settings = _make_settings()
    fetcher = BackfillFetcher(adapter, settings)
    [(path, _, _)] = fetcher.fetch_and_save(symbols=["AAPL"], days=10, output_dir=sub)
    assert path.parent == sub
    assert sub.exists()


def test_fetch_returns_bar_counts(tmp_path):
    adapter = FakeAdapter(daily={"AAPL": _DAILY_BARS}, intraday={"AAPL": _INTRADAY_BARS})
    settings = _make_settings()
    fetcher = BackfillFetcher(adapter, settings)
    [(_, n_intraday, n_daily)] = fetcher.fetch_and_save(
        symbols=["AAPL"], days=10, output_dir=tmp_path
    )
    assert n_intraday == 20
    assert n_daily == 5
