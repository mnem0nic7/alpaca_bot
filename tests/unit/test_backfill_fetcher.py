from __future__ import annotations

import json
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


def test_fetch_embeds_regime_daily_bars_when_available(tmp_path):
    regime_bars = [_make_bar("SPY", _TS, 500.0 + i) for i in range(5)]
    adapter = FakeAdapter(
        daily={"AAPL": _DAILY_BARS, "SPY": regime_bars},
        intraday={"AAPL": _INTRADAY_BARS},
    )
    settings = _make_settings()
    fetcher = BackfillFetcher(adapter, settings)
    fetcher.fetch_and_save(symbols=["AAPL"], days=252, output_dir=tmp_path)

    scenario = ReplayRunner.load_scenario(tmp_path / "AAPL_252d.json")

    assert scenario.regime_daily_bars is not None
    assert [bar.symbol for bar in scenario.regime_daily_bars] == ["SPY"] * 5


def test_fetch_embeds_market_context_daily_bars_when_available(tmp_path):
    vix_bars = [_make_bar("VIXY", _TS, 20.0 + i) for i in range(5)]
    xlk_bars = [_make_bar("XLK", _TS, 200.0 + i) for i in range(5)]
    adapter = FakeAdapter(
        daily={"AAPL": _DAILY_BARS, "VIXY": vix_bars, "XLK": xlk_bars},
        intraday={"AAPL": _INTRADAY_BARS},
    )
    settings = _make_settings(sector_etf_symbols=("XLK", "XLF"))
    fetcher = BackfillFetcher(adapter, settings)
    fetcher.fetch_and_save(symbols=["AAPL"], days=252, output_dir=tmp_path)

    scenario = ReplayRunner.load_scenario(tmp_path / "AAPL_252d.json")

    assert scenario.vix_daily_bars is not None
    assert [bar.symbol for bar in scenario.vix_daily_bars] == ["VIXY"] * 5
    assert scenario.sector_daily_bars_by_etf is not None
    assert set(scenario.sector_daily_bars_by_etf) == {"XLK"}
    assert [bar.symbol for bar in scenario.sector_daily_bars_by_etf["XLK"]] == [
        "XLK"
    ] * 5


def test_context_only_enriches_existing_scenarios_without_replacing_bars(tmp_path):
    regime_bars = [_make_bar("SPY", _TS, 500.0 + i) for i in range(5)]
    vix_bars = [_make_bar("VIXY", _TS, 20.0 + i) for i in range(5)]
    xlk_bars = [_make_bar("XLK", _TS, 200.0 + i) for i in range(5)]
    adapter = FakeAdapter(
        daily={"SPY": regime_bars, "VIXY": vix_bars, "XLK": xlk_bars},
        intraday={},
    )
    settings = _make_settings(sector_etf_symbols=("XLK", "XLF"))
    original_payload = {
        "name": "AAPL_252d",
        "symbol": "AAPL",
        "starting_equity": 12345.0,
        "daily_bars": [_make_bar("AAPL", _TS, 150.0).__dict__],
        "intraday_bars": [_make_bar("AAPL", _TS, 151.0).__dict__],
    }
    for symbol in ("AAPL", "MSFT"):
        payload = dict(original_payload, name=f"{symbol}_252d", symbol=symbol)
        (tmp_path / f"{symbol}_252d.json").write_text(
            json.dumps(payload, default=str)
        )
    fetcher = BackfillFetcher(adapter, settings)

    results = fetcher.enrich_existing_scenarios_with_context(
        output_dir=tmp_path,
        days=252,
    )

    assert [path.name for path, *_ in results] == [
        "AAPL_252d.json",
        "MSFT_252d.json",
    ]
    scenario = ReplayRunner.load_scenario(tmp_path / "AAPL_252d.json")
    assert [bar.symbol for bar in scenario.daily_bars] == ["AAPL"]
    assert [bar.symbol for bar in scenario.intraday_bars] == ["AAPL"]
    assert scenario.regime_daily_bars is not None
    assert [bar.symbol for bar in scenario.regime_daily_bars] == ["SPY"] * 5
    assert scenario.vix_daily_bars is not None
    assert [bar.symbol for bar in scenario.vix_daily_bars] == ["VIXY"] * 5
    assert scenario.sector_daily_bars_by_etf is not None
    assert set(scenario.sector_daily_bars_by_etf) == {"XLK"}


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


def test_fetch_replaces_existing_scenario_atomically_without_tmp_leak(tmp_path):
    target = tmp_path / "AAPL_10d.json"
    target.write_text("old complete scenario")
    target.chmod(0o644)
    adapter = FakeAdapter(daily={"AAPL": _DAILY_BARS}, intraday={"AAPL": _INTRADAY_BARS})
    settings = _make_settings()
    fetcher = BackfillFetcher(adapter, settings)

    fetcher.fetch_and_save(symbols=["AAPL"], days=10, output_dir=tmp_path)

    scenario = ReplayRunner.load_scenario(target)
    assert scenario.symbol == "AAPL"
    assert target.stat().st_mode & 0o777 == 0o644
    assert not list(tmp_path.glob(".AAPL_10d.json.*.tmp"))
