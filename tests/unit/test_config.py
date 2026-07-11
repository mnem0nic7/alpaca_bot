from __future__ import annotations

from datetime import date, datetime, timezone
import hashlib
import json
from pathlib import Path

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


def _write_promotion_marker(
    tmp_path: Path,
    *,
    strategy: str = "ema_pullback",
    strategy_version: str = "v1",
    env_file: str = "/etc/alpaca_bot/alpaca-bot.env",
    validation_sha256: str | None = None,
    proof_eventual_pass_rate: float = 0.60,
) -> Path:
    fractionable_symbols = tmp_path / "fractionable_symbols.txt"
    fractionable_symbols.write_text("AAA\n", encoding="utf-8")
    scenario_symbols = tmp_path / "scenario_symbols.txt"
    scenario_symbols.write_text("AAA\n", encoding="utf-8")
    snapshot_sha256 = hashlib.sha256(fractionable_symbols.read_bytes()).hexdigest()
    universe_sha256 = hashlib.sha256(scenario_symbols.read_bytes()).hexdigest()
    fractionability_snapshot = {
        "schema_version": 1,
        "snapshot_file": str(fractionable_symbols),
        "snapshot_sha256": snapshot_sha256,
        "universe_symbols_file": str(scenario_symbols),
        "universe_sha256": universe_sha256,
        "universe_symbol_count": 1,
        "fractionable_symbol_count": 1,
        "non_fractionable_symbol_count": 0,
    }
    prefilter_dir = tmp_path / "latest"
    prefilter_dir.mkdir()
    prefilter_summary = prefilter_dir / "summary.json"
    prefilter_summary.write_text(
        json.dumps({"rows": [], "fractionability_snapshot": fractionability_snapshot}),
        encoding="utf-8",
    )
    prefilter_sha256 = hashlib.sha256(prefilter_summary.read_bytes()).hexdigest()
    validation_dir = tmp_path / "latest_validation"
    validation_dir.mkdir()
    validation_summary = validation_dir / "summary.json"
    row = {
        "candidate": strategy,
        "status": "passed",
        "verdict": "positive-edge",
        "candidate_verdict": "positive-edge",
        "candidate_contribution_status": "positive_pnl",
        "candidate_scale": "0.10",
        "candidate_trades": 292,
        "candidate_total_pnl": 150.76,
        "candidate_ci_low": 0.0707,
        "candidate_p_mean_le_zero": 0.009,
    }
    validation_summary.write_text(
        json.dumps(
            {
                "prefilter_summary_json": str(prefilter_summary),
                "prefilter_summary_sha256": prefilter_sha256,
                "fractionability_snapshot": fractionability_snapshot,
                "rows": [row],
            }
        ),
        encoding="utf-8",
    )
    summary_sha256 = hashlib.sha256(validation_summary.read_bytes()).hexdigest()
    marker_sha256 = validation_sha256 or summary_sha256
    proof_dir = tmp_path / "latest_proof_horizon"
    proof_dir.mkdir()
    proof_summary = proof_dir / "summary.json"
    proof_summary.write_text(
        json.dumps(
            {
                "strategy": f"bull_flag+{strategy}",
                "confidence_scales": {strategy: 0.10},
                "trades": 386,
                "total_pnl": 50.33,
                "starts_eventually_passed": int(278 * proof_eventual_pass_rate),
                "historical_starts_checked": 278,
                "eventual_pass_rate": proof_eventual_pass_rate,
                "min_pnl": 0.01,
                "fractionability_snapshot": fractionability_snapshot,
                "candidate_selection": {
                    "schema_version": 1,
                    "selected_candidate": strategy,
                    "selected_candidate_scale": "0.10",
                    "selection_reason": (
                        "first_passing"
                        if proof_eventual_pass_rate >= 0.50
                        else "top_ranked_failure"
                    ),
                    "candidate_count": 1,
                    "passing_candidate_count": int(
                        proof_eventual_pass_rate >= 0.50
                    ),
                    "min_eventual_pass_rate": 0.50,
                    "rows": [
                        {
                            "candidate": strategy,
                            "candidate_scale": "0.10",
                            "status": (
                                "ok"
                                if proof_eventual_pass_rate >= 0.50
                                else "failed"
                            ),
                            "detail": (
                                "fresh"
                                if proof_eventual_pass_rate >= 0.50
                                else "eventual_pass_rate_below_gate"
                            ),
                        }
                    ],
                },
            }
        ),
        encoding="utf-8",
    )
    proof_sha256 = hashlib.sha256(proof_summary.read_bytes()).hexdigest()
    marker = tmp_path / "promotion_approval.json"
    marker.write_text(
        json.dumps(
            {
                "schema_version": 3,
                "evidence_root": str(tmp_path.resolve()),
                "approved_at": datetime.now(timezone.utc).isoformat(),
                "strategy": strategy,
                "confirmation": (
                    f"approve-{strategy}-paper-promotion-sha256-{marker_sha256}"
                    f"-proof-sha256-{proof_sha256}"
                ),
                "validation_summary": str(validation_summary),
                "validation_summary_sha256": marker_sha256,
                "strategy_version": strategy_version,
                "env_file": env_file,
                "candidate_scale": row["candidate_scale"],
                "candidate_trades": row["candidate_trades"],
                "candidate_total_pnl": row["candidate_total_pnl"],
                "candidate_ci_low": row["candidate_ci_low"],
                "candidate_p_mean_le_zero": row["candidate_p_mean_le_zero"],
                "proof_horizon_summary": str(proof_summary),
                "proof_horizon_summary_sha256": proof_sha256,
                "proof_horizon_trades": 386,
                "proof_horizon_total_pnl": 50.33,
                "proof_horizon_eventual_pass_rate": proof_eventual_pass_rate,
                "proof_horizon_starts_eventually_passed": int(
                    278 * proof_eventual_pass_rate
                ),
                "proof_horizon_historical_starts": 278,
                "proof_horizon_selection_reason": (
                    "first_passing"
                    if proof_eventual_pass_rate >= 0.50
                    else "top_ranked_failure"
                ),
                "proof_horizon_candidate_count": 1,
                "proof_horizon_passing_candidate_count": int(
                    proof_eventual_pass_rate >= 0.50
                ),
            }
        ),
        encoding="utf-8",
    )
    return marker


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


def test_vwap_reversion_specific_controls_parse_as_optional() -> None:
    defaults = Settings.from_env(_base_env())
    configured = Settings.from_env(
        _base_env(
            VWAP_REVERSION_RELATIVE_VOLUME_THRESHOLD="1.8",
            VWAP_REVERSION_ATR_STOP_MULTIPLIER="1.5",
        )
    )

    assert defaults.vwap_reversion_relative_volume_threshold is None
    assert defaults.vwap_reversion_atr_stop_multiplier is None
    assert configured.vwap_reversion_relative_volume_threshold == 1.8
    assert configured.vwap_reversion_atr_stop_multiplier == 1.5


def test_entry_min_close_to_entry_pct_defaults_on_for_paper_and_parses_env():
    settings = Settings.from_env(_base_env())
    assert settings.entry_min_close_to_entry_pct == -0.01
    assert settings.entry_max_close_to_entry_pct == 1.0
    assert settings.entry_order_active_bars == 1
    assert settings.entry_candidate_rank_mode == "close_to_entry"

    settings = Settings.from_env(_base_env(ENTRY_MIN_CLOSE_TO_ENTRY_PCT="-1.0"))
    assert settings.entry_min_close_to_entry_pct == -1.0

    settings = Settings.from_env(_base_env(ENTRY_MAX_CLOSE_TO_ENTRY_PCT="0.005"))
    assert settings.entry_max_close_to_entry_pct == 0.005

    settings = Settings.from_env(_base_env(ENTRY_ORDER_ACTIVE_BARS="3"))
    assert settings.entry_order_active_bars == 3

    settings = Settings.from_env(
        _base_env(ENTRY_CANDIDATE_RANK_MODE="RELATIVE_VOLUME")
    )
    assert settings.entry_candidate_rank_mode == "relative_volume"


def test_entry_min_close_to_entry_pct_defaults_off_for_live():
    settings = Settings.from_env(
        _base_env(TRADING_MODE="live", ENABLE_LIVE_TRADING="true")
    )
    assert settings.entry_min_close_to_entry_pct == -1.0
    assert settings.entry_max_close_to_entry_pct == 1.0


@pytest.mark.parametrize("value", ["-1.01", "1.01"])
def test_entry_min_close_to_entry_pct_validates_bounds(value: str):
    with pytest.raises(ValueError, match="ENTRY_MIN_CLOSE_TO_ENTRY_PCT"):
        Settings.from_env(_base_env(ENTRY_MIN_CLOSE_TO_ENTRY_PCT=value))


@pytest.mark.parametrize("value", ["-1.01", "1.01"])
def test_entry_max_close_to_entry_pct_validates_bounds(value: str):
    with pytest.raises(ValueError, match="ENTRY_MAX_CLOSE_TO_ENTRY_PCT"):
        Settings.from_env(_base_env(ENTRY_MAX_CLOSE_TO_ENTRY_PCT=value))


def test_entry_max_close_to_entry_pct_must_not_be_below_min():
    with pytest.raises(ValueError, match="ENTRY_MAX_CLOSE_TO_ENTRY_PCT"):
        Settings.from_env(
            _base_env(
                ENTRY_MIN_CLOSE_TO_ENTRY_PCT="0.005",
                ENTRY_MAX_CLOSE_TO_ENTRY_PCT="0.001",
            )
        )


@pytest.mark.parametrize("value", ["0", "5"])
def test_entry_order_active_bars_validates_bounds(value: str):
    with pytest.raises(ValueError, match="ENTRY_ORDER_ACTIVE_BARS"):
        Settings.from_env(_base_env(ENTRY_ORDER_ACTIVE_BARS=value))


def test_entry_candidate_rank_mode_validates_known_modes() -> None:
    for mode in ("close_to_entry", "relative_volume", "balanced"):
        assert (
            Settings.from_env(
                _base_env(ENTRY_CANDIDATE_RANK_MODE=mode)
            ).entry_candidate_rank_mode
            == mode
        )

    with pytest.raises(ValueError, match="ENTRY_CANDIDATE_RANK_MODE"):
        Settings.from_env(_base_env(ENTRY_CANDIDATE_RANK_MODE="profit_hint"))


def test_no_follow_through_exit_defaults_and_env_overrides():
    settings = Settings.from_env(_base_env())
    assert settings.enable_no_follow_through_exit is False
    assert settings.no_follow_through_exit_minutes == 0
    assert settings.no_follow_through_min_favorable_pct == 0.0025

    settings = Settings.from_env(
        _base_env(
            ENABLE_NO_FOLLOW_THROUGH_EXIT="true",
            NO_FOLLOW_THROUGH_EXIT_MINUTES="90",
            NO_FOLLOW_THROUGH_MIN_FAVORABLE_PCT="0.005",
        )
    )
    assert settings.enable_no_follow_through_exit is True
    assert settings.no_follow_through_exit_minutes == 90
    assert settings.no_follow_through_min_favorable_pct == 0.005


def test_no_follow_through_exit_validation():
    with pytest.raises(ValueError, match="NO_FOLLOW_THROUGH_EXIT_MINUTES"):
        Settings.from_env(
            _base_env(
                ENABLE_NO_FOLLOW_THROUGH_EXIT="true",
                NO_FOLLOW_THROUGH_EXIT_MINUTES="0",
            )
        )
    with pytest.raises(ValueError, match="NO_FOLLOW_THROUGH_EXIT_MINUTES"):
        Settings.from_env(_base_env(NO_FOLLOW_THROUGH_EXIT_MINUTES="-1"))
    with pytest.raises(ValueError, match="NO_FOLLOW_THROUGH_MIN_FAVORABLE_PCT"):
        Settings.from_env(_base_env(NO_FOLLOW_THROUGH_MIN_FAVORABLE_PCT="-0.01"))
    with pytest.raises(ValueError, match="NO_FOLLOW_THROUGH_MIN_FAVORABLE_PCT"):
        Settings.from_env(_base_env(NO_FOLLOW_THROUGH_MIN_FAVORABLE_PCT="1.0"))


def test_giveback_exit_defaults_and_env_overrides():
    settings = Settings.from_env(_base_env())
    assert settings.enable_giveback_exit is False
    assert settings.giveback_exit_min_favorable_pct == 0.0025
    assert settings.giveback_exit_max_return_pct == 0.0

    settings = Settings.from_env(
        _base_env(
            ENABLE_GIVEBACK_EXIT="true",
            GIVEBACK_EXIT_MIN_FAVORABLE_PCT="0.005",
            GIVEBACK_EXIT_MAX_RETURN_PCT="0.001",
        )
    )
    assert settings.enable_giveback_exit is True
    assert settings.giveback_exit_min_favorable_pct == 0.005
    assert settings.giveback_exit_max_return_pct == 0.001


def test_giveback_exit_validation():
    with pytest.raises(ValueError, match="GIVEBACK_EXIT_MIN_FAVORABLE_PCT"):
        Settings.from_env(_base_env(GIVEBACK_EXIT_MIN_FAVORABLE_PCT="-0.01"))
    with pytest.raises(ValueError, match="GIVEBACK_EXIT_MIN_FAVORABLE_PCT"):
        Settings.from_env(_base_env(GIVEBACK_EXIT_MIN_FAVORABLE_PCT="1.0"))
    with pytest.raises(ValueError, match="GIVEBACK_EXIT_MAX_RETURN_PCT"):
        Settings.from_env(_base_env(GIVEBACK_EXIT_MAX_RETURN_PCT="-0.01"))
    with pytest.raises(ValueError, match="GIVEBACK_EXIT_MAX_RETURN_PCT"):
        Settings.from_env(_base_env(GIVEBACK_EXIT_MAX_RETURN_PCT="1.0"))


def test_early_loss_exit_defaults_and_env_overrides():
    settings = Settings.from_env(_base_env())
    assert settings.enable_early_loss_exit is False
    assert settings.early_loss_exit_minutes == 0
    assert settings.early_loss_exit_return_pct == 0.01

    settings = Settings.from_env(
        _base_env(
            ENABLE_EARLY_LOSS_EXIT="true",
            EARLY_LOSS_EXIT_MINUTES="45",
            EARLY_LOSS_EXIT_RETURN_PCT="0.005",
        )
    )
    assert settings.enable_early_loss_exit is True
    assert settings.early_loss_exit_minutes == 45
    assert settings.early_loss_exit_return_pct == 0.005


def test_early_loss_exit_validation():
    with pytest.raises(ValueError, match="EARLY_LOSS_EXIT_MINUTES"):
        Settings.from_env(
            _base_env(
                ENABLE_EARLY_LOSS_EXIT="true",
                EARLY_LOSS_EXIT_MINUTES="0",
            )
        )
    with pytest.raises(ValueError, match="EARLY_LOSS_EXIT_MINUTES"):
        Settings.from_env(_base_env(EARLY_LOSS_EXIT_MINUTES="-1"))
    with pytest.raises(ValueError, match="EARLY_LOSS_EXIT_RETURN_PCT"):
        Settings.from_env(_base_env(EARLY_LOSS_EXIT_RETURN_PCT="0"))
    with pytest.raises(ValueError, match="EARLY_LOSS_EXIT_RETURN_PCT"):
        Settings.from_env(_base_env(EARLY_LOSS_EXIT_RETURN_PCT="1.0"))


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


def test_option_chain_snapshot_dir_default_and_override():
    assert Settings.from_env(_base_env()).option_chain_snapshot_dir is None

    settings = Settings.from_env(
        _base_env(OPTION_CHAIN_SNAPSHOT_DIR=" /tmp/alpaca-options ")
    )
    assert settings.option_chain_snapshot_dir == "/tmp/alpaca-options"


def test_option_chain_request_timeout_default_override_and_validation():
    assert Settings.from_env(_base_env()).option_chain_request_timeout_seconds == 10.0

    settings = Settings.from_env(_base_env(OPTION_CHAIN_REQUEST_TIMEOUT_SECONDS="2.5"))
    assert settings.option_chain_request_timeout_seconds == 2.5

    with pytest.raises(ValueError, match="OPTION_CHAIN_REQUEST_TIMEOUT_SECONDS"):
        Settings.from_env(_base_env(OPTION_CHAIN_REQUEST_TIMEOUT_SECONDS="0"))


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


def test_paper_approved_strategies_defaults_to_current_proof_basket_and_parses_env():
    settings = Settings.from_env(_base_env())
    assert settings.paper_approved_strategies == ("bull_flag",)

    settings = Settings.from_env(
        _base_env(PAPER_APPROVED_STRATEGIES="bull_flag,failed_breakdown")
    )
    assert settings.paper_approved_strategies == ("bull_flag", "failed_breakdown")


def test_paper_approved_strategies_strips_whitespace_and_rejects_empty():
    settings = Settings.from_env(
        _base_env(PAPER_APPROVED_STRATEGIES=" bull_flag , failed_breakdown ")
    )
    assert settings.paper_approved_strategies == ("bull_flag", "failed_breakdown")

    with pytest.raises(ValueError, match="PAPER_APPROVED_STRATEGIES"):
        Settings.from_env(_base_env(PAPER_APPROVED_STRATEGIES=" , "))


def test_paper_approved_strategies_rejects_unsupported_characters():
    with pytest.raises(ValueError, match="PAPER_APPROVED_STRATEGIES"):
        Settings.from_env(_base_env(PAPER_APPROVED_STRATEGIES="bull flag"))


def test_paper_approved_strategies_appends_valid_approval_marker(tmp_path: Path):
    env_file = "/etc/alpaca_bot/alpaca-bot.env"
    marker = _write_promotion_marker(tmp_path, env_file=env_file)

    settings = Settings.from_env(
        _base_env(
            PAPER_APPROVED_STRATEGIES="bull_flag",
            PAPER_STRATEGY_PROMOTION_DENYLIST="none",
            PAPER_APPROVED_STRATEGIES_APPROVAL_MARKER=str(marker),
            PAPER_APPROVED_STRATEGIES_APPROVAL_ENV_FILE=env_file,
        )
    )

    assert settings.paper_approved_strategies == ("bull_flag", "ema_pullback")


def test_paper_approved_strategies_excludes_default_promotion_denylist(
    tmp_path: Path,
):
    env_file = "/etc/alpaca_bot/alpaca-bot.env"
    marker = _write_promotion_marker(tmp_path, env_file=env_file)

    settings = Settings.from_env(
        _base_env(
            PAPER_APPROVED_STRATEGIES="bull_flag,ema_pullback,vwap_cross",
            PAPER_APPROVED_STRATEGIES_APPROVAL_MARKER=str(marker),
            PAPER_APPROVED_STRATEGIES_APPROVAL_ENV_FILE=env_file,
        )
    )

    assert settings.paper_approved_strategies == ("bull_flag",)


def test_paper_approved_strategies_accepts_non_denied_valid_marker(tmp_path: Path):
    env_file = "/etc/alpaca_bot/alpaca-bot.env"
    marker = _write_promotion_marker(
        tmp_path,
        strategy="orb",
        env_file=env_file,
    )

    settings = Settings.from_env(
        _base_env(
            PAPER_APPROVED_STRATEGIES="bull_flag",
            PAPER_APPROVED_STRATEGIES_APPROVAL_MARKER=str(marker),
            PAPER_APPROVED_STRATEGIES_APPROVAL_ENV_FILE=env_file,
        )
    )

    assert settings.paper_approved_strategies == ("bull_flag", "orb")


def test_paper_approved_strategies_ignores_tampered_approval_marker(tmp_path: Path):
    env_file = "/etc/alpaca_bot/alpaca-bot.env"
    marker = _write_promotion_marker(
        tmp_path,
        env_file=env_file,
        validation_sha256="0" * 64,
    )

    settings = Settings.from_env(
        _base_env(
            PAPER_APPROVED_STRATEGIES="bull_flag",
            PAPER_APPROVED_STRATEGIES_APPROVAL_MARKER=str(marker),
            PAPER_APPROVED_STRATEGIES_APPROVAL_ENV_FILE=env_file,
        )
    )

    assert settings.paper_approved_strategies == ("bull_flag",)


def test_paper_approved_strategies_ignores_failed_proof_horizon_marker(
    tmp_path: Path,
):
    env_file = "/etc/alpaca_bot/alpaca-bot.env"
    marker = _write_promotion_marker(
        tmp_path,
        env_file=env_file,
        proof_eventual_pass_rate=0.3813,
    )

    settings = Settings.from_env(
        _base_env(
            PAPER_APPROVED_STRATEGIES="bull_flag",
            PAPER_APPROVED_STRATEGIES_APPROVAL_MARKER=str(marker),
            PAPER_APPROVED_STRATEGIES_APPROVAL_ENV_FILE=env_file,
        )
    )

    assert settings.paper_approved_strategies == ("bull_flag",)


def test_paper_approved_strategies_ignores_validation_only_legacy_marker(
    tmp_path: Path,
):
    env_file = "/etc/alpaca_bot/alpaca-bot.env"
    marker = _write_promotion_marker(tmp_path, env_file=env_file)
    payload = json.loads(marker.read_text(encoding="utf-8"))
    payload["schema_version"] = 2
    marker.write_text(json.dumps(payload), encoding="utf-8")

    settings = Settings.from_env(
        _base_env(
            PAPER_APPROVED_STRATEGIES="bull_flag",
            PAPER_APPROVED_STRATEGIES_APPROVAL_MARKER=str(marker),
            PAPER_APPROVED_STRATEGIES_APPROVAL_ENV_FILE=env_file,
        )
    )

    assert settings.paper_approved_strategies == ("bull_flag",)


def test_paper_approved_strategies_ignores_marker_with_stale_evidence_root(
    tmp_path: Path,
):
    env_file = "/etc/alpaca_bot/alpaca-bot.env"
    marker = _write_promotion_marker(tmp_path, env_file=env_file)
    payload = json.loads(marker.read_text(encoding="utf-8"))
    payload["evidence_root"] = str(tmp_path / "older_run")
    marker.write_text(json.dumps(payload), encoding="utf-8")

    settings = Settings.from_env(
        _base_env(
            PAPER_APPROVED_STRATEGIES="bull_flag",
            PAPER_APPROVED_STRATEGIES_APPROVAL_MARKER=str(marker),
            PAPER_APPROVED_STRATEGIES_APPROVAL_ENV_FILE=env_file,
        )
    )

    assert settings.paper_approved_strategies == ("bull_flag",)


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
    assert settings.paper_readiness_decision_dry_run_strategy == "bull_flag"
    assert settings.paper_readiness_decision_dry_run_min_records == 900
    assert settings.paper_readiness_decision_dry_run_min_evaluations == 6

    settings = Settings.from_env(
        _base_env(
            PAPER_READINESS_MIN_WATCHLIST_SYMBOLS="17",
            PAPER_READINESS_DECISION_DRY_RUN_STRATEGY="custom_flag",
            PAPER_READINESS_DECISION_DRY_RUN_MIN_RECORDS="23",
            PAPER_READINESS_DECISION_DRY_RUN_MIN_EVALUATIONS="2",
        )
    )
    assert settings.paper_readiness_min_watchlist_symbols == 17
    assert settings.paper_readiness_decision_dry_run_strategy == "custom_flag"
    assert settings.paper_readiness_decision_dry_run_min_records == 23
    assert settings.paper_readiness_decision_dry_run_min_evaluations == 2

    settings = Settings.from_env(_base_env(PROFIT_PROBE_STRATEGY="probe_flag"))
    assert settings.paper_readiness_decision_dry_run_strategy == "probe_flag"

    with pytest.raises(ValueError, match="PAPER_READINESS_MIN_WATCHLIST_SYMBOLS"):
        Settings.from_env(_base_env(PAPER_READINESS_MIN_WATCHLIST_SYMBOLS="0"))
    with pytest.raises(
        ValueError, match="PAPER_READINESS_DECISION_DRY_RUN_STRATEGY"
    ):
        Settings.from_env(_base_env(PAPER_READINESS_DECISION_DRY_RUN_STRATEGY=""))
    with pytest.raises(
        ValueError, match="PAPER_READINESS_DECISION_DRY_RUN_STRATEGY"
    ):
        Settings.from_env(_base_env(PAPER_READINESS_DECISION_DRY_RUN_STRATEGY="bad flag"))
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
    assert settings.profit_probe_start_date == date(2026, 7, 7)

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
