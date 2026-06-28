from __future__ import annotations

import re
from pathlib import Path


def test_compose_passes_paper_edge_and_risk_env_vars() -> None:
    compose_text = Path("deploy/compose.yaml").read_text()
    passed_vars = set(re.findall(r"([A-Z][A-Z0-9_]*): \$\{", compose_text))

    expected = {
        "ENABLE_OPTIONS_TRADING",
        "ENABLE_SECTOR_FILTER",
        "ENABLE_VIX_FILTER",
        "ENABLE_VWAP_ENTRY_FILTER",
        "FLOOR_AUTO_RAISE_MAX_AGE_DAYS",
        "BULL_FLAG_MIN_RUN_PCT",
        "BULL_FLAG_CONSOLIDATION_VOLUME_RATIO",
        "BULL_FLAG_CONSOLIDATION_RANGE_PCT",
        "MAX_OPEN_POSITIONS",
        "ATR_STOP_MULTIPLIER",
        "TRAILING_STOP_ATR_MULTIPLIER",
        "OPTION_CHAIN_MIN_TOTAL_VOLUME",
        "OPTION_STRATEGY_MAX_ROLLING_LOSS_USD",
        "OPTION_STRATEGY_ROLLING_LOSS_DAYS",
        "PAPER_PROOF_FREEZE",
        "PAPER_READINESS_MAX_PASS_AGE_MINUTES",
        "PROFIT_PROBE_START_DATE",
        "REPLAY_SLIPPAGE_BPS",
    }

    assert expected <= passed_vars
    assert "ATR_STOP_MULTIPLIER: ${ATR_STOP_MULTIPLIER:-1.0}" in compose_text
    assert (
        "TRAILING_STOP_ATR_MULTIPLIER: ${TRAILING_STOP_ATR_MULTIPLIER:-0.0}"
        in compose_text
    )
    assert "BULL_FLAG_MIN_RUN_PCT: ${BULL_FLAG_MIN_RUN_PCT:-0.02}" in compose_text
    assert (
        "BULL_FLAG_CONSOLIDATION_VOLUME_RATIO: "
        "${BULL_FLAG_CONSOLIDATION_VOLUME_RATIO:-0.6}"
    ) in compose_text
    assert (
        "BULL_FLAG_CONSOLIDATION_RANGE_PCT: "
        "${BULL_FLAG_CONSOLIDATION_RANGE_PCT:-0.5}"
    ) in compose_text


def test_deploy_ops_check_enforces_paper_readiness() -> None:
    deploy_text = Path("scripts/deploy.sh").read_text()

    assert 'DEPLOY_PROOF_SETTLE_SECONDS="${DEPLOY_PROOF_SETTLE_SECONDS:-15}"' in deploy_text
    assert "DEPLOY_PROOF_SETTLE_SECONDS must be a non-negative integer" in deploy_text
    assert 'DEPLOY_REQUIRE_DECISION_DRY_RUN="${DEPLOY_REQUIRE_DECISION_DRY_RUN:-true}"' in deploy_text
    assert "DEPLOY_REQUIRE_DECISION_DRY_RUN must be true or false" in deploy_text
    assert 'DEPLOY_DECISION_DRY_RUN_STRATEGY="${DEPLOY_DECISION_DRY_RUN_STRATEGY:-${PAPER_READINESS_DECISION_DRY_RUN_STRATEGY:-${PROFIT_PROBE_STRATEGY:-bull_flag}}}"' in deploy_text
    assert 'DEPLOY_DECISION_DRY_RUN_MIN_RECORDS="${DEPLOY_DECISION_DRY_RUN_MIN_RECORDS:-${PAPER_READINESS_DECISION_DRY_RUN_MIN_RECORDS:-900}}"' in deploy_text
    assert 'DEPLOY_DECISION_DRY_RUN_REQUIRE_ACCEPTED="${DEPLOY_DECISION_DRY_RUN_REQUIRE_ACCEPTED:-${PAPER_READINESS_DECISION_DRY_RUN_REQUIRE_ACCEPTED:-false}}"' in deploy_text
    assert 'DEPLOY_DECISION_DRY_RUN_SAMPLE_TIMES="${DEPLOY_DECISION_DRY_RUN_SAMPLE_TIMES:-${PAPER_READINESS_DECISION_DRY_RUN_SAMPLE_TIMES:-10:30,11:30,12:30,13:30,14:30,15:30}}"' in deploy_text
    assert "DEPLOY_DECISION_DRY_RUN_STRATEGY contains unsupported characters" in deploy_text
    assert "DEPLOY_DECISION_DRY_RUN_MIN_RECORDS must be a non-negative integer" in deploy_text
    assert "DEPLOY_DECISION_DRY_RUN_REQUIRE_ACCEPTED must be true or false" in deploy_text
    assert "DEPLOY_DECISION_DRY_RUN_SAMPLE_TIMES must be comma-separated HH:MM values" in deploy_text
    assert 'REQUIRE_CRON_HEALTH="${REQUIRE_CRON_HEALTH:-true}"' in deploy_text
    assert "REQUIRE_CRON_HEALTH must be true or false" in deploy_text
    assert '"$ROOT_DIR/scripts/cron_health_check.sh"' in deploy_text
    assert "Cron health check skipped because REQUIRE_CRON_HEALTH=false" in deploy_text
    assert "verify_paper_decision_dry_run()" in deploy_text
    assert "Paper decision dry run skipped because DEPLOY_REQUIRE_DECISION_DRY_RUN=false" in deploy_text
    assert 'PAPER_DECISION_DRY_RUN_STRATEGY="$DEPLOY_DECISION_DRY_RUN_STRATEGY"' in deploy_text
    assert 'PAPER_DECISION_DRY_RUN_MIN_RECORDS="$DEPLOY_DECISION_DRY_RUN_MIN_RECORDS"' in deploy_text
    assert 'PAPER_DECISION_DRY_RUN_REQUIRE_ACCEPTED="$DEPLOY_DECISION_DRY_RUN_REQUIRE_ACCEPTED"' in deploy_text
    assert 'PAPER_DECISION_DRY_RUN_SAMPLE_TIMES="$DEPLOY_DECISION_DRY_RUN_SAMPLE_TIMES"' in deploy_text
    assert '"$ROOT_DIR/scripts/paper_decision_dry_run.sh" "$ENV_FILE"' in deploy_text
    assert '--expect-trading-mode "${TRADING_MODE}"' in deploy_text
    assert '--expect-strategy-version "${STRATEGY_VERSION}"' in deploy_text
    assert "--expect-trading-status enabled" in deploy_text
    assert "--expect-kill-switch false" in deploy_text
    assert "--expect-only-enabled-strategy bull_flag" in deploy_text
    assert 'compose=(docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE")' in deploy_text
    assert "remove_supervisor_container()" in deploy_text
    assert '"${compose[@]}" stop supervisor >/dev/null 2>&1 || true' in deploy_text
    assert '"${compose[@]}" rm -sf supervisor >/dev/null 2>&1 || true' in deploy_text
    assert 'label=com.docker.compose.service=supervisor' in deploy_text
    assert 'xargs -r docker rm -f' in deploy_text
    assert '"${fallback_project_name}-supervisor-1"' in deploy_text
    assert "remove_supervisor_container\n  \"${compose[@]}\" up" in deploy_text
    assert "paper_proof_enabled()" in deploy_text
    assert "refresh_paper_readiness()" in deploy_text
    assert "verify_paper_proof_ready()" in deploy_text
    assert '"${paper_proof_freeze,,}" == "true"' in deploy_text
    assert '"$ROOT_DIR/scripts/run_locked_check_with_audit.sh"' in deploy_text
    assert "PAPER_READINESS_FORCE_REFRESH=true" in deploy_text
    assert "paper_readiness" in deploy_text
    assert "/var/lock/alpaca-bot-paper-readiness.lock" in deploy_text
    assert '"$ROOT_DIR/scripts/paper_readiness_if_needed.sh"' in deploy_text
    assert '"$ROOT_DIR/scripts/paper_proof_status.sh" "$ENV_FILE"' in deploy_text
    assert "paper proof summary:" in deploy_text
    assert "|| true" in deploy_text
    assert '"readiness_audit_stale"' in deploy_text
    assert "paper proof readiness stale after deploy; refreshing once" in deploy_text
    assert '"readiness=ready"' in deploy_text
    assert '"blockers=none"' in deploy_text
    assert 'sleep "$DEPLOY_PROOF_SETTLE_SECONDS"' in deploy_text
    assert deploy_text.count("verify_paper_proof_ready") >= 3
    assert deploy_text.index("verify_paper_decision_dry_run") < deploy_text.rindex("verify_paper_proof_ready")
    assert "${proof_summary:-missing summary}" in deploy_text
    assert "deploy failed: paper proof status not ready after deploy" in deploy_text


def test_paper_env_example_matches_audited_bull_flag_posture() -> None:
    env_text = Path("deploy/paper.env.example").read_text()

    assert "MARKET_DATA_FEED=iex" in env_text
    assert "RELATIVE_VOLUME_THRESHOLD=2.0" in env_text
    assert "MAX_OPEN_POSITIONS=3" in env_text
    assert "BULL_FLAG_MIN_RUN_PCT=0.02" in env_text
    assert "BULL_FLAG_CONSOLIDATION_VOLUME_RATIO=0.6" in env_text
    assert "BULL_FLAG_CONSOLIDATION_RANGE_PCT=0.5" in env_text
    assert "REPLAY_SLIPPAGE_BPS=2.0" in env_text
    assert "PAPER_PROOF_FREEZE=true" in env_text
    assert "PAPER_READINESS_MAX_PASS_AGE_MINUTES=180" in env_text
    assert "PROFIT_PROBE_START_DATE=2026-06-29" in env_text
    assert "PROFIT_PROBE_STRATEGY=bull_flag" in env_text
    assert "PROFIT_PROBE_MIN_TRADES=10" in env_text
    assert "PROFIT_PROBE_MIN_PNL=0.01" in env_text
    assert "RISK_PER_TRADE_PCT=0.01" in env_text
    assert "MAX_POSITION_PCT=0.05" in env_text
    assert "MAX_PORTFOLIO_EXPOSURE_PCT=0.30" in env_text
    assert "INTRADAY_CONSECUTIVE_LOSS_GATE=0" in env_text
    assert "ATR_PERIOD=14" in env_text
    assert "ATR_STOP_MULTIPLIER=1.0" in env_text
    assert "TRAILING_STOP_ATR_MULTIPLIER=1.5" in env_text
    assert "TRAILING_STOP_PROFIT_TRIGGER_R=1.0" in env_text
    assert "ENABLE_VIX_FILTER=false" in env_text
    assert "ENABLE_SECTOR_FILTER=false" in env_text
    assert "ENABLE_VWAP_ENTRY_FILTER=true" in env_text
    assert "ENABLE_PROFIT_TRAIL=true" in env_text
    assert "PROFIT_TRAIL_PCT=0.95" in env_text
    assert "ENABLE_REGIME_FILTER=false" in env_text
    assert "ENABLE_NEWS_FILTER=false" in env_text
    assert "ENABLE_SPREAD_FILTER=false" in env_text
    assert "ENABLE_OPTIONS_TRADING=false" in env_text
    assert "OPTION_CHAIN_SYMBOLS=" in env_text


def test_init_server_generates_audited_paper_posture() -> None:
    script = Path("scripts/init_server.sh").read_text()

    assert "MARKET_DATA_FEED=iex" in script
    assert 'RISK_PER_TRADE_PCT="0.01"' in script
    assert 'MAX_OPEN_POSITIONS="3"' in script
    assert 'RELATIVE_VOLUME_THRESHOLD="2.0"' in script
    assert "BULL_FLAG_MIN_RUN_PCT=0.02" in script
    assert "BULL_FLAG_CONSOLIDATION_VOLUME_RATIO=0.6" in script
    assert "BULL_FLAG_CONSOLIDATION_RANGE_PCT=0.5" in script
    assert 'REPLAY_SLIPPAGE_BPS="2.0"' in script
    assert 'PAPER_PROOF_FREEZE="true"' in script
    assert "PAPER_READINESS_MAX_PASS_AGE_MINUTES=180" in script
    assert "PROFIT_PROBE_START_DATE=2026-06-29" in script
    assert "PROFIT_PROBE_STRATEGY=bull_flag" in script
    assert "PROFIT_PROBE_MIN_TRADES=10" in script
    assert "PROFIT_PROBE_MIN_PNL=0.01" in script
    assert "CONFIDENCE_FLOOR=0.25" in script
    assert "INTRADAY_CONSECUTIVE_LOSS_GATE=0" in script
    assert "ATR_PERIOD=14" in script
    assert "ATR_STOP_MULTIPLIER=1.0" in script
    assert "TRAILING_STOP_ATR_MULTIPLIER=1.5" in script
    assert "TRAILING_STOP_PROFIT_TRIGGER_R=1.0" in script
    assert 'ENABLE_VIX_FILTER="false"' in script
    assert 'ENABLE_SECTOR_FILTER="false"' in script
    assert 'ENABLE_VWAP_ENTRY_FILTER="true"' in script
    assert 'ENABLE_PROFIT_TRAIL="true"' in script
    assert "PROFIT_TRAIL_PCT=0.95" in script
    assert "ENABLE_NEWS_FILTER=false" in script
    assert "ENABLE_SPREAD_FILTER=false" in script
    assert "OPTION_CHAIN_SYMBOLS=" in script
