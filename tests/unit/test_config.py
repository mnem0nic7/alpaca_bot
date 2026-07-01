from __future__ import annotations

from datetime import date

import pytest

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


def test_option_chain_symbols_default_is_empty():
    s = Settings.from_env(_base_env())
    assert s.option_chain_symbols == ()


def test_option_chain_symbols_parsed_from_csv():
    env = _base_env(OPTION_CHAIN_SYMBOLS="ALHC,AMLX,AROC")
    s = Settings.from_env(env)
    assert s.option_chain_symbols == ("ALHC", "AMLX", "AROC")


def test_option_chain_symbols_strips_whitespace():
    env = _base_env(OPTION_CHAIN_SYMBOLS=" ALHC , AMLX ")
    s = Settings.from_env(env)
    assert s.option_chain_symbols == ("ALHC", "AMLX")


def test_floor_auto_raise_max_age_days_default_and_validation():
    settings = Settings.from_env(_base_env())
    assert settings.floor_auto_raise_max_age_days == 7

    with pytest.raises(ValueError, match="FLOOR_AUTO_RAISE_MAX_AGE_DAYS"):
        Settings.from_env(_base_env(FLOOR_AUTO_RAISE_MAX_AGE_DAYS="0"))


def test_floor_auto_raise_max_age_days_env_override():
    settings = Settings.from_env(_base_env(FLOOR_AUTO_RAISE_MAX_AGE_DAYS="14"))
    assert settings.floor_auto_raise_max_age_days == 14


def test_paper_proof_freeze_defaults_false_and_parses_env():
    settings = Settings.from_env(_base_env())
    assert settings.paper_proof_freeze is False

    settings = Settings.from_env(_base_env(PAPER_PROOF_FREEZE="true"))
    assert settings.paper_proof_freeze is True


def test_paper_readiness_max_pass_age_minutes_default_and_validation():
    settings = Settings.from_env(_base_env())
    assert settings.paper_readiness_max_pass_age_minutes == 180

    with pytest.raises(ValueError, match="PAPER_READINESS_MAX_PASS_AGE_MINUTES"):
        Settings.from_env(_base_env(PAPER_READINESS_MAX_PASS_AGE_MINUTES="0"))


def test_paper_readiness_max_pass_age_minutes_env_override():
    settings = Settings.from_env(
        _base_env(PAPER_READINESS_MAX_PASS_AGE_MINUTES="45")
    )
    assert settings.paper_readiness_max_pass_age_minutes == 45


def test_paper_readiness_decision_dry_run_thresholds_default_and_overrides():
    settings = Settings.from_env(_base_env())
    assert settings.paper_readiness_min_watchlist_symbols == 900
    assert settings.paper_readiness_decision_dry_run_min_records == 900
    assert settings.paper_readiness_decision_dry_run_min_evaluations == 6

    settings = Settings.from_env(
        _base_env(
            PAPER_READINESS_MIN_WATCHLIST_SYMBOLS="17",
            PAPER_READINESS_DECISION_DRY_RUN_MIN_RECORDS="23",
            PAPER_READINESS_DECISION_DRY_RUN_MIN_EVALUATIONS="2",
        )
    )
    assert settings.paper_readiness_min_watchlist_symbols == 17
    assert settings.paper_readiness_decision_dry_run_min_records == 23
    assert settings.paper_readiness_decision_dry_run_min_evaluations == 2

    with pytest.raises(ValueError, match="PAPER_READINESS_MIN_WATCHLIST_SYMBOLS"):
        Settings.from_env(_base_env(PAPER_READINESS_MIN_WATCHLIST_SYMBOLS="0"))
    with pytest.raises(
        ValueError, match="PAPER_READINESS_DECISION_DRY_RUN_MIN_RECORDS"
    ):
        Settings.from_env(
            _base_env(PAPER_READINESS_DECISION_DRY_RUN_MIN_RECORDS="-1")
        )
    with pytest.raises(
        ValueError, match="PAPER_READINESS_DECISION_DRY_RUN_MIN_EVALUATIONS"
    ):
        Settings.from_env(
            _base_env(PAPER_READINESS_DECISION_DRY_RUN_MIN_EVALUATIONS="0")
        )


def test_profit_probe_start_date_default_and_validation():
    settings = Settings.from_env(_base_env())
    assert settings.profit_probe_start_date == date(2026, 6, 30)

    with pytest.raises(ValueError, match="PROFIT_PROBE_START_DATE"):
        Settings.from_env(_base_env(PROFIT_PROBE_START_DATE="20260629"))


def test_profit_probe_start_date_env_override():
    settings = Settings.from_env(_base_env(PROFIT_PROBE_START_DATE="2026-07-06"))
    assert settings.profit_probe_start_date == date(2026, 7, 6)


def test_replay_slippage_bps_default_and_validation():
    settings = Settings.from_env(_base_env())
    assert settings.replay_slippage_bps == 5.0
    with pytest.raises(ValueError, match="REPLAY_SLIPPAGE_BPS"):
        Settings.from_env(_base_env(REPLAY_SLIPPAGE_BPS="-1"))
    with pytest.raises(ValueError, match="REPLAY_SLIPPAGE_BPS"):
        Settings.from_env(_base_env(REPLAY_SLIPPAGE_BPS="101"))


def test_replay_slippage_bps_env_override():
    settings = Settings.from_env(_base_env(REPLAY_SLIPPAGE_BPS="0"))
    assert settings.replay_slippage_bps == 0.0
