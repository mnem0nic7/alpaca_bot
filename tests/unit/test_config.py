from __future__ import annotations

from alpaca_bot.config import Settings


def _base_env(**overrides: str) -> dict[str, str]:
    base = {
        "TRADING_MODE": "paper",
        "ENABLE_LIVE_TRADING": "false",
        "STRATEGY_VERSION": "v1",
        "DATABASE_URL": "postgresql://x:y@localhost/z",
        "SYMBOLS": "AAPL",
    }
    base.update(overrides)
    return base


def test_market_context_filter_defaults():
    s = Settings.from_env(_base_env())
    assert s.enable_vix_filter is False
    assert s.vix_proxy_symbol == "VIXY"
    assert s.vix_lookback_bars == 20
    assert s.enable_sector_filter is False
    assert "XLK" in s.sector_etf_symbols
    assert len(s.sector_etf_symbols) == 11
    assert s.sector_etf_sma_period == 20
    assert s.sector_filter_min_passing_pct == 0.5
    assert s.enable_vwap_entry_filter is False


def test_market_context_filter_env_overrides():
    env = _base_env(
        ENABLE_VIX_FILTER="true",
        VIX_PROXY_SYMBOL="UVXY",
        VIX_LOOKBACK_BARS="30",
        ENABLE_SECTOR_FILTER="true",
        SECTOR_ETF_SYMBOLS="XLK,XLF,XLE",
        SECTOR_ETF_SMA_PERIOD="10",
        SECTOR_FILTER_MIN_PASSING_PCT="0.6",
        ENABLE_VWAP_ENTRY_FILTER="true",
    )
    s = Settings.from_env(env)
    assert s.enable_vix_filter is True
    assert s.vix_proxy_symbol == "UVXY"
    assert s.vix_lookback_bars == 30
    assert s.enable_sector_filter is True
    assert s.sector_etf_symbols == ("XLK", "XLF", "XLE")
    assert s.sector_etf_sma_period == 10
    assert s.sector_filter_min_passing_pct == 0.6
    assert s.enable_vwap_entry_filter is True
