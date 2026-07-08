from __future__ import annotations

import os
import re
import subprocess
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
        "ENTRY_ORDER_ACTIVE_BARS",
        "ENTRY_MIN_CLOSE_TO_ENTRY_PCT",
        "ENTRY_MAX_CLOSE_TO_ENTRY_PCT",
        "MAX_LOSS_PER_TRADE_DOLLARS",
        "MAX_OPEN_POSITIONS",
        "CONFIDENCE_FLOOR",
        "ATR_STOP_MULTIPLIER",
        "TRAILING_STOP_ATR_MULTIPLIER",
        "OPTION_CHAIN_MIN_TOTAL_VOLUME",
        "OPTION_CHAIN_SNAPSHOT_DIR",
        "OPTION_STRATEGY_MAX_ROLLING_LOSS_USD",
        "OPTION_STRATEGY_ROLLING_LOSS_DAYS",
        "PAPER_PROOF_FREEZE",
        "PAPER_APPROVED_STRATEGIES",
        "PAPER_SCALE_MIN_TRADES",
        "PAPER_SCALE_MIN_STRATEGIES",
        "PAPER_SCALE_MIN_ACTIVE_DAYS",
        "PAPER_SCALE_MAX_SINGLE_WIN_PNL_SHARE",
        "PAPER_SCALE_MIN_PROFIT_FACTOR",
        "PAPER_SCALE_MAX_EOD_LOSS_SHARE",
        "PAPER_SCALE_MAX_OPERATIONAL_EXIT_LOSS_SHARE",
        "PAPER_EXECUTION_MIN_ENTRY_FILL_RATE",
        "PAPER_EXECUTION_MAX_CAPACITY_REJECT_RATE",
        "PAPER_READINESS_DECISION_DRY_RUN_STRATEGIES",
        "PAPER_ACTIVITY_STRATEGIES",
        "PROFIT_PROBE_STRATEGIES",
        "SESSION_GUARD_STRATEGIES",
        "SESSION_GUARD_MIN_TRADES",
        "SESSION_GUARD_FAIL_BELOW_PNL",
        "PAPER_READINESS_MAX_PASS_AGE_MINUTES",
        "PROFIT_PROBE_START_DATE",
        "PROFIT_PROBE_STRATEGY",
        "PROFIT_PROBE_MIN_TRADES",
        "PROFIT_PROBE_MIN_PNL",
        "ENABLE_REGIME_FILTER",
        "ENABLE_NEWS_FILTER",
        "ENABLE_SPREAD_FILTER",
        "ENABLE_PROFIT_TARGET",
        "PROFIT_TARGET_R",
        "ENABLE_BREAKEVEN_STOP",
        "BREAKEVEN_TRIGGER_PCT",
        "BREAKEVEN_TRAIL_PCT",
        "ENABLE_TREND_FILTER_EXIT",
        "ENABLE_VWAP_BREAKDOWN_EXIT",
        "VWAP_BREAKDOWN_MIN_BARS",
        "ENABLE_NO_FOLLOW_THROUGH_EXIT",
        "NO_FOLLOW_THROUGH_EXIT_MINUTES",
        "NO_FOLLOW_THROUGH_MIN_FAVORABLE_PCT",
        "ENABLE_GIVEBACK_EXIT",
        "GIVEBACK_EXIT_MIN_FAVORABLE_PCT",
        "GIVEBACK_EXIT_MAX_RETURN_PCT",
        "ENABLE_EARLY_LOSS_EXIT",
        "EARLY_LOSS_EXIT_MINUTES",
        "EARLY_LOSS_EXIT_RETURN_PCT",
        "REPLAY_SLIPPAGE_BPS",
    }

    assert expected <= passed_vars
    assert "ATR_STOP_MULTIPLIER: ${ATR_STOP_MULTIPLIER:-1.0}" in compose_text
    assert (
        "TRAILING_STOP_ATR_MULTIPLIER: ${TRAILING_STOP_ATR_MULTIPLIER:-0.0}"
        in compose_text
    )
    assert "BULL_FLAG_MIN_RUN_PCT: ${BULL_FLAG_MIN_RUN_PCT:-0.02}" in compose_text
    assert "ENTRY_ORDER_ACTIVE_BARS: ${ENTRY_ORDER_ACTIVE_BARS:-1}" in compose_text
    assert (
        "ENTRY_MIN_CLOSE_TO_ENTRY_PCT: ${ENTRY_MIN_CLOSE_TO_ENTRY_PCT:--0.01}"
        in compose_text
    )
    assert (
        "ENTRY_MAX_CLOSE_TO_ENTRY_PCT: ${ENTRY_MAX_CLOSE_TO_ENTRY_PCT:-1.0}"
        in compose_text
    )
    assert (
        "PAPER_APPROVED_STRATEGIES: "
        "${PAPER_APPROVED_STRATEGIES:-bull_flag}"
    ) in compose_text
    assert "PAPER_SCALE_MIN_TRADES: ${PAPER_SCALE_MIN_TRADES:-30}" in compose_text
    assert "PAPER_SCALE_MIN_STRATEGIES: ${PAPER_SCALE_MIN_STRATEGIES:-2}" in compose_text
    assert "PAPER_SCALE_MIN_ACTIVE_DAYS: ${PAPER_SCALE_MIN_ACTIVE_DAYS:-5}" in compose_text
    assert (
        "PAPER_SCALE_MAX_SINGLE_WIN_PNL_SHARE: "
        "${PAPER_SCALE_MAX_SINGLE_WIN_PNL_SHARE:-0.50}"
    ) in compose_text
    assert (
        "PAPER_SCALE_MIN_PROFIT_FACTOR: "
        "${PAPER_SCALE_MIN_PROFIT_FACTOR:-1.20}"
    ) in compose_text
    assert "PAPER_SCALE_MAX_EOD_LOSS_SHARE: ${PAPER_SCALE_MAX_EOD_LOSS_SHARE:-0.50}" in compose_text
    assert (
        "PAPER_SCALE_MAX_OPERATIONAL_EXIT_LOSS_SHARE: "
        "${PAPER_SCALE_MAX_OPERATIONAL_EXIT_LOSS_SHARE:-0.00}"
    ) in compose_text
    assert (
        "PAPER_EXECUTION_MIN_ENTRY_FILL_RATE: "
        "${PAPER_EXECUTION_MIN_ENTRY_FILL_RATE:-0.25}"
    ) in compose_text
    assert (
        "PAPER_EXECUTION_MAX_CAPACITY_REJECT_RATE: "
        "${PAPER_EXECUTION_MAX_CAPACITY_REJECT_RATE:-0.05}"
    ) in compose_text
    assert (
        "PAPER_READINESS_DECISION_DRY_RUN_STRATEGIES: "
        "${PAPER_READINESS_DECISION_DRY_RUN_STRATEGIES:-bull_flag}"
    ) in compose_text
    assert (
        "PAPER_ACTIVITY_STRATEGIES: "
        "${PAPER_ACTIVITY_STRATEGIES:-bull_flag}"
    ) in compose_text
    assert (
        "PROFIT_PROBE_STRATEGIES: "
        "${PROFIT_PROBE_STRATEGIES:-bull_flag}"
    ) in compose_text
    assert (
        "SESSION_GUARD_STRATEGIES: "
        "${SESSION_GUARD_STRATEGIES:-bull_flag}"
    ) in compose_text
    assert "SESSION_GUARD_MIN_TRADES: ${SESSION_GUARD_MIN_TRADES:-10}" in compose_text
    assert "SESSION_GUARD_FAIL_BELOW_PNL: ${SESSION_GUARD_FAIL_BELOW_PNL:-0}" in compose_text
    assert "PROFIT_PROBE_STRATEGY: ${PROFIT_PROBE_STRATEGY:-bull_flag}" in compose_text
    assert "PROFIT_PROBE_MIN_TRADES: ${PROFIT_PROBE_MIN_TRADES:-30}" in compose_text
    assert "PROFIT_PROBE_MIN_PNL: ${PROFIT_PROBE_MIN_PNL:-0.01}" in compose_text
    assert (
        "BULL_FLAG_CONSOLIDATION_VOLUME_RATIO: "
        "${BULL_FLAG_CONSOLIDATION_VOLUME_RATIO:-0.6}"
    ) in compose_text
    assert (
        "BULL_FLAG_CONSOLIDATION_RANGE_PCT: "
        "${BULL_FLAG_CONSOLIDATION_RANGE_PCT:-0.5}"
    ) in compose_text
    assert "CONFIDENCE_FLOOR: ${CONFIDENCE_FLOOR:-0.25}" in compose_text
    assert "OPTION_CHAIN_SNAPSHOT_DIR: ${OPTION_CHAIN_SNAPSHOT_DIR:-}" in compose_text
    assert (
        "/var/lib/alpaca-bot/option-chain-snapshots:/data/option-chain-snapshots"
        in compose_text
    )
    admin_block = compose_text[
        compose_text.index("  admin:\n") : compose_text.index("  # Run on-demand:")
    ]
    assert (
        "/var/lib/alpaca-bot/option-chain-snapshots:/data/option-chain-snapshots"
        in admin_block
    )
    assert "ENABLE_REGIME_FILTER: ${ENABLE_REGIME_FILTER:-false}" in compose_text
    assert "ENABLE_NEWS_FILTER: ${ENABLE_NEWS_FILTER:-false}" in compose_text
    assert "ENABLE_SPREAD_FILTER: ${ENABLE_SPREAD_FILTER:-false}" in compose_text
    assert "ENABLE_PROFIT_TARGET: ${ENABLE_PROFIT_TARGET:-false}" in compose_text
    assert "PROFIT_TARGET_R: ${PROFIT_TARGET_R:-2.0}" in compose_text
    assert "ENABLE_BREAKEVEN_STOP: ${ENABLE_BREAKEVEN_STOP:-true}" in compose_text
    assert "BREAKEVEN_TRIGGER_PCT: ${BREAKEVEN_TRIGGER_PCT:-0.0025}" in compose_text
    assert "BREAKEVEN_TRAIL_PCT: ${BREAKEVEN_TRAIL_PCT:-0.002}" in compose_text
    assert "ENABLE_TREND_FILTER_EXIT: ${ENABLE_TREND_FILTER_EXIT:-false}" in compose_text
    assert "ENABLE_VWAP_BREAKDOWN_EXIT: ${ENABLE_VWAP_BREAKDOWN_EXIT:-false}" in compose_text
    assert "VWAP_BREAKDOWN_MIN_BARS: ${VWAP_BREAKDOWN_MIN_BARS:-1}" in compose_text
    assert (
        "ENABLE_NO_FOLLOW_THROUGH_EXIT: ${ENABLE_NO_FOLLOW_THROUGH_EXIT:-false}"
        in compose_text
    )
    assert (
        "NO_FOLLOW_THROUGH_EXIT_MINUTES: ${NO_FOLLOW_THROUGH_EXIT_MINUTES:-0}"
        in compose_text
    )
    assert (
        "NO_FOLLOW_THROUGH_MIN_FAVORABLE_PCT: "
        "${NO_FOLLOW_THROUGH_MIN_FAVORABLE_PCT:-0.0025}"
    ) in compose_text
    assert "ENABLE_GIVEBACK_EXIT: ${ENABLE_GIVEBACK_EXIT:-false}" in compose_text
    assert (
        "GIVEBACK_EXIT_MIN_FAVORABLE_PCT: "
        "${GIVEBACK_EXIT_MIN_FAVORABLE_PCT:-0.0025}"
    ) in compose_text
    assert (
        "GIVEBACK_EXIT_MAX_RETURN_PCT: ${GIVEBACK_EXIT_MAX_RETURN_PCT:-0.0}"
        in compose_text
    )
    assert "ENABLE_EARLY_LOSS_EXIT: ${ENABLE_EARLY_LOSS_EXIT:-false}" in compose_text
    assert "EARLY_LOSS_EXIT_MINUTES: ${EARLY_LOSS_EXIT_MINUTES:-0}" in compose_text
    assert (
        "EARLY_LOSS_EXIT_RETURN_PCT: ${EARLY_LOSS_EXIT_RETURN_PCT:-0.01}"
        in compose_text
    )
    assert "MAX_LOSS_PER_TRADE_DOLLARS: ${MAX_LOSS_PER_TRADE_DOLLARS:-}" in compose_text


def test_nightly_compose_sweeps_enabled_strategy_flags() -> None:
    compose_text = Path("deploy/compose.yaml").read_text()
    nightly = re.search(r"(?ms)^  nightly:\n(?P<body>.*?)(?=^  [a-z][a-z0-9_-]*:\n|\Z)", compose_text)

    assert nightly is not None
    assert (
        "    command:\n"
        "      - alpaca-bot-nightly\n"
        "      - --output-dir\n"
        "      - /data/scenarios\n"
        "      - --output-env\n"
        "      - /data/candidate.env\n"
        "      - --strategies\n"
        "      - enabled\n"
        "      - --proof-guard\n"
        "      - --max-combos\n"
        "      - ${NIGHTLY_MAX_COMBOS:-24}\n"
    ) in nightly.group("body")


def test_deploy_ops_check_enforces_paper_readiness() -> None:
    deploy_text = Path("scripts/deploy.sh").read_text()

    assert 'DEPLOY_PROOF_SETTLE_SECONDS="${DEPLOY_PROOF_SETTLE_SECONDS:-15}"' in deploy_text
    assert "DEPLOY_PROOF_SETTLE_SECONDS must be a non-negative integer" in deploy_text
    assert 'DEPLOY_REQUIRE_DECISION_DRY_RUN="${DEPLOY_REQUIRE_DECISION_DRY_RUN:-true}"' in deploy_text
    assert "DEPLOY_REQUIRE_DECISION_DRY_RUN must be true or false" in deploy_text
    assert 'DEPLOY_READINESS_REFRESH_RETRIES="${DEPLOY_READINESS_REFRESH_RETRIES:-10}"' in deploy_text
    assert (
        'DEPLOY_READINESS_REFRESH_RETRY_SECONDS="${DEPLOY_READINESS_REFRESH_RETRY_SECONDS:-20}"'
        in deploy_text
    )
    assert 'DEPLOY_PREFLIGHT_EXPOSURE_RETRIES="${DEPLOY_PREFLIGHT_EXPOSURE_RETRIES:-1}"' in deploy_text
    assert (
        'DEPLOY_PREFLIGHT_EXPOSURE_RETRY_SECONDS="${DEPLOY_PREFLIGHT_EXPOSURE_RETRY_SECONDS:-30}"'
        in deploy_text
    )
    assert 'DEPLOY_DRAIN_PAPER_ENTRIES="${DEPLOY_DRAIN_PAPER_ENTRIES:-true}"' in deploy_text
    assert (
        'DEPLOY_MAINTENANCE_REASON="${DEPLOY_MAINTENANCE_REASON:-deploy maintenance drain}"'
        in deploy_text
    )
    assert "DEPLOY_READINESS_REFRESH_RETRIES must be a positive integer" in deploy_text
    assert (
        "DEPLOY_READINESS_REFRESH_RETRY_SECONDS must be a non-negative integer"
        in deploy_text
    )
    assert "DEPLOY_PREFLIGHT_EXPOSURE_RETRIES must be a positive integer" in deploy_text
    assert (
        "DEPLOY_PREFLIGHT_EXPOSURE_RETRY_SECONDS must be a non-negative integer"
        in deploy_text
    )
    assert "DEPLOY_DRAIN_PAPER_ENTRIES must be true or false" in deploy_text
    assert 'DEPLOY_DECISION_DRY_RUN_STRATEGY="${DEPLOY_DECISION_DRY_RUN_STRATEGY:-${PAPER_READINESS_DECISION_DRY_RUN_STRATEGY:-${PROFIT_PROBE_STRATEGY:-bull_flag}}}"' in deploy_text
    assert 'DEPLOY_DECISION_DRY_RUN_STRATEGIES="${DEPLOY_DECISION_DRY_RUN_STRATEGIES:-${PAPER_READINESS_DECISION_DRY_RUN_STRATEGIES:-${PAPER_APPROVED_STRATEGIES:-$DEPLOY_DECISION_DRY_RUN_STRATEGY}}}"' in deploy_text
    assert 'DEPLOY_DECISION_DRY_RUN_MIN_RECORDS="${DEPLOY_DECISION_DRY_RUN_MIN_RECORDS:-${PAPER_READINESS_DECISION_DRY_RUN_MIN_RECORDS:-900}}"' in deploy_text
    assert 'DEPLOY_DECISION_DRY_RUN_REQUIRE_ACCEPTED="${DEPLOY_DECISION_DRY_RUN_REQUIRE_ACCEPTED:-${PAPER_READINESS_DECISION_DRY_RUN_REQUIRE_ACCEPTED:-true}}"' in deploy_text
    assert 'DEPLOY_DECISION_DRY_RUN_SAMPLE_TIMES="${DEPLOY_DECISION_DRY_RUN_SAMPLE_TIMES:-${PAPER_READINESS_DECISION_DRY_RUN_SAMPLE_TIMES:-10:30,11:30,12:30,13:30,14:30,15:30}}"' in deploy_text
    assert "DEPLOY_DECISION_DRY_RUN_STRATEGY contains unsupported characters" in deploy_text
    assert "DEPLOY_DECISION_DRY_RUN_STRATEGIES contains unsupported strategy" in deploy_text
    assert "DEPLOY_DECISION_DRY_RUN_STRATEGIES must contain at least one strategy" in deploy_text
    assert "DEPLOY_DECISION_DRY_RUN_MIN_RECORDS must be a non-negative integer" in deploy_text
    assert "DEPLOY_DECISION_DRY_RUN_REQUIRE_ACCEPTED must be true or false" in deploy_text
    assert "DEPLOY_DECISION_DRY_RUN_SAMPLE_TIMES must be comma-separated HH:MM values" in deploy_text
    assert 'REQUIRE_CRON_HEALTH="${REQUIRE_CRON_HEALTH:-true}"' in deploy_text
    assert "REQUIRE_CRON_HEALTH must be true or false" in deploy_text
    assert '"$ROOT_DIR/scripts/cron_health_check.sh"' in deploy_text
    assert "Cron health check skipped because REQUIRE_CRON_HEALTH=false" in deploy_text
    assert "load_deploy_trading_status_line()" in deploy_text
    assert "load_deploy_ops_expected_trading_status()" in deploy_text
    assert "reason=paper profit lock" in deploy_text
    assert 'BROKER_FLAT_CONTEXT="deploy profit lock"' in deploy_text
    assert "deploy ops check accepting flat paper profit lock" in deploy_text
    assert "printf 'close_only\\n'" in deploy_text
    assert "printf 'enabled\\n'" in deploy_text
    assert "run_deploy_ops_check()" in deploy_text
    assert 'expected_status="$(load_deploy_ops_expected_trading_status)"' in deploy_text
    assert 'retry_expected_status="$(load_deploy_ops_expected_trading_status)"' in deploy_text
    assert "deploy ops check retrying after flat paper profit lock transition" in deploy_text
    assert "verify_paper_decision_dry_run()" in deploy_text
    assert 'for strategy in "${deploy_decision_dry_run_strategies[@]}"' in deploy_text
    assert "Paper decision dry run skipped because DEPLOY_REQUIRE_DECISION_DRY_RUN=false" in deploy_text
    assert 'PAPER_DECISION_DRY_RUN_STRATEGY="$strategy"' in deploy_text
    assert 'PAPER_DECISION_DRY_RUN_MIN_RECORDS="$DEPLOY_DECISION_DRY_RUN_MIN_RECORDS"' in deploy_text
    assert 'PAPER_DECISION_DRY_RUN_REQUIRE_ACCEPTED="$DEPLOY_DECISION_DRY_RUN_REQUIRE_ACCEPTED"' in deploy_text
    assert 'PAPER_DECISION_DRY_RUN_SAMPLE_TIMES="$DEPLOY_DECISION_DRY_RUN_SAMPLE_TIMES"' in deploy_text
    assert '"$ROOT_DIR/scripts/paper_decision_dry_run.sh" "$ENV_FILE"' in deploy_text
    assert '--expect-trading-mode "${TRADING_MODE}"' in deploy_text
    assert '--expect-strategy-version "${STRATEGY_VERSION}"' in deploy_text
    assert '--expect-trading-status "$expected_status"' in deploy_text
    assert "--expect-trading-status close_only" in deploy_text
    assert "--expect-kill-switch false" in deploy_text
    assert "DEPLOY_EXPECT_ENABLED_STRATEGIES" in deploy_text
    assert (
        'DEPLOY_EXPECT_ENABLED_STRATEGIES="${DEPLOY_EXPECT_ENABLED_STRATEGIES:-'
        '${PAPER_APPROVED_STRATEGIES:-bull_flag}}"'
    ) in deploy_text
    assert 'expected_enabled_strategy_args+=(--expect-only-enabled-strategy "$name")' in deploy_text
    assert '"${expected_enabled_strategy_args[@]}"' in deploy_text
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
    assert 'if [[ "$rc" -ne 48 || "$attempt" -ge "$DEPLOY_READINESS_REFRESH_RETRIES" ]]' in deploy_text
    assert "paper readiness refresh lock busy after deploy; retrying" in deploy_text
    assert 'sleep "$DEPLOY_READINESS_REFRESH_RETRY_SECONDS"' in deploy_text
    assert "verify_paper_proof_ready()" in deploy_text
    assert "deploy_paper_proof_status_ready()" in deploy_text
    assert "deploy_accepts_protected_paper_exposure()" in deploy_text
    assert "deploy_accepts_post_resume_entry_exposure()" in deploy_text
    assert "deploy_paper_exposure_safe()" in deploy_text
    assert "start_deploy_paper_drain()" in deploy_text
    assert "finish_deploy_paper_drain()" in deploy_text
    assert "restore_deploy_paper_drain_on_exit()" in deploy_text
    assert "verify_deploy_preflight_paper_exposure()" in deploy_text
    assert "deploy ops check accepting paper deploy maintenance drain" in deploy_text
    assert "deploy set paper trading close-only for maintenance drain" in deploy_text
    assert "deploy resumed paper trading after maintenance drain" in deploy_text
    assert "deploy restored paper trading after aborted maintenance drain" in deploy_text
    assert "deploy preflight failed: paper exposure is not flat or protected" in deploy_text
    assert "deploy preflight waiting for paper exposure to become flat/protected" in deploy_text
    assert "deploy accepting protected paper exposure after deploy" in deploy_text
    assert "deploy accepting fresh post-resume paper entry after deploy" in deploy_text
    assert '"${paper_proof_freeze,,}" == "true"' in deploy_text
    assert '"$ROOT_DIR/scripts/run_locked_check_with_audit.sh"' in deploy_text
    assert "PAPER_READINESS_FORCE_REFRESH=true" in deploy_text
    assert "paper_readiness" in deploy_text
    assert "/var/lock/alpaca-bot-paper-readiness.lock" in deploy_text
    assert '"$ROOT_DIR/scripts/paper_readiness_if_needed.sh"' in deploy_text
    assert '"$ROOT_DIR/scripts/paper_proof_status.sh" "$ENV_FILE"' in deploy_text
    assert "paper proof summary:" in deploy_text
    assert "paper proof exposure protection:" in deploy_text
    assert "|| true" in deploy_text
    assert '"readiness_audit_stale"' in deploy_text
    assert "paper proof readiness stale after deploy; refreshing once" in deploy_text
    assert '"readiness=ready"' in deploy_text
    assert '"blockers=none"' in deploy_text
    assert 'sleep "$DEPLOY_PROOF_SETTLE_SECONDS"' in deploy_text
    assert deploy_text.count("verify_paper_proof_ready") >= 3
    assert (
        "if paper_proof_enabled; then\n"
        "    start_deploy_paper_drain\n"
        "    verify_deploy_preflight_paper_exposure\n"
        "  fi\n"
        "  remove_supervisor_container"
    ) in deploy_text
    assert (
        "if paper_proof_enabled; then\n"
        "    finish_deploy_paper_drain\n"
        "    run_deploy_ops_check\n"
        "    refresh_paper_readiness\n"
        "  fi"
    ) in deploy_text
    assert (
        "trap restore_deploy_paper_drain_on_exit EXIT"
    ) in deploy_text
    assert (
        "run_deploy_ops_check\n"
        "  if paper_proof_enabled; then\n"
        "    finish_deploy_paper_drain"
    ) in deploy_text
    assert (
        "finish_deploy_paper_drain\n"
        "    run_deploy_ops_check"
    ) in deploy_text
    assert (
        "start_deploy_paper_drain\n"
        "    verify_deploy_preflight_paper_exposure\n"
    ) in deploy_text
    assert deploy_text.index("verify_paper_decision_dry_run") < deploy_text.rindex("verify_paper_proof_ready")
    assert "${proof_summary:-missing summary}" in deploy_text
    assert "deploy failed: paper proof status not ready after deploy" in deploy_text


def _run_deploy_proof_status_ready(tmp_path: Path, proof_output: str) -> str:
    env_file = tmp_path / "alpaca-bot.env"
    env_file.write_text("")
    env = os.environ.copy()
    env.update({"ENV_FILE": str(env_file), "PROOF_OUTPUT": proof_output})
    result = subprocess.run(
        [
            "bash",
            "-c",
            (
                'DEPLOY_SH_SOURCE_ONLY=true source scripts/deploy.sh "$ENV_FILE"; '
                'if deploy_paper_proof_status_ready "$PROOF_OUTPUT"; '
                "then printf ready; else printf blocked; fi"
            ),
        ],
        cwd=Path.cwd(),
        env=env,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


def _run_deploy_exposure_safe(tmp_path: Path, proof_output: str) -> str:
    env_file = tmp_path / "alpaca-bot.env"
    env_file.write_text("")
    env = os.environ.copy()
    env.update({"ENV_FILE": str(env_file), "PROOF_OUTPUT": proof_output})
    result = subprocess.run(
        [
            "bash",
            "-c",
            (
                'DEPLOY_SH_SOURCE_ONLY=true source scripts/deploy.sh "$ENV_FILE"; '
                'if deploy_paper_exposure_safe "$PROOF_OUTPUT"; '
                "then printf safe; else printf blocked; fi"
            ),
        ],
        cwd=Path.cwd(),
        env=env,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


def _run_deploy_expected_trading_status(tmp_path: Path, status_line: str) -> str:
    env_file = tmp_path / "alpaca-bot.env"
    env_file.write_text("")
    env = os.environ.copy()
    env.update(
        {
            "ENV_FILE": str(env_file),
            "STATUS_LINE": status_line,
            "TRADING_MODE": "paper",
            "PAPER_PROOF_FREEZE": "true",
        }
    )
    result = subprocess.run(
        [
            "bash",
            "-c",
            (
                'DEPLOY_SH_SOURCE_ONLY=true source scripts/deploy.sh "$ENV_FILE"; '
                'load_deploy_trading_status_line() { printf "%s\\n" "$STATUS_LINE"; }; '
                "load_deploy_ops_expected_trading_status"
            ),
        ],
        cwd=Path.cwd(),
        env=env,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def test_deploy_accepts_protected_paper_exposure_after_restart(tmp_path: Path) -> None:
    proof_output = "\n".join(
        [
            "paper proof summary: readiness=blocked proof=pending reason=awaiting_completed_proof_session blockers=local_open_positions,local_active_orders,broker_open_orders,broker_open_positions evidence_blockers=sample_trades,active_days sealed_evidence_blockers=sample_trades,active_days overall_blockers=sample_trades,active_days clean_window_blockers=sample_trades,active_days sealed_clean_window_blockers=sample_trades,active_days warnings=none",
            "paper proof runtime: ops_status=ok ops_detail=status=ok image_status=ok image_detail=runtime image health ok",
            "paper proof stream: status=ok latest_start=2026-07-07T16:09:19+00:00 latest_event=trade_update_stream_started:2026-07-07T16:09:19+00:00",
            "paper proof readiness audit: status=ok target_session=2026-07-07 check_status=passed created_at=2026-07-07T16:18:18+00:00",
            "paper proof exposure protection: status=protected issues=none local_positions=1 local_stop_orders=1 local_entry_orders=0 broker_positions=1 broker_orders=1 symbols=ARQT",
        ]
    )

    assert _run_deploy_proof_status_ready(tmp_path, proof_output) == "ready"
    assert _run_deploy_exposure_safe(tmp_path, proof_output) == "safe"


def test_deploy_accepts_fresh_entry_exposure_after_restart(tmp_path: Path) -> None:
    proof_output = "\n".join(
        [
            "paper proof summary: readiness=blocked proof=pending reason=awaiting_completed_proof_session blockers=local_active_orders,broker_open_orders evidence_blockers=sample_trades sealed_evidence_blockers=sample_trades overall_blockers=sample_trades clean_window_blockers=sample_trades sealed_clean_window_blockers=sample_trades warnings=none",
            "paper proof runtime: ops_status=ok ops_detail=status=ok image_status=ok image_detail=runtime image health ok",
            "paper proof stream: status=ok latest_start=2026-07-07T18:00:43+00:00 latest_event=trade_update_stream_started:2026-07-07T18:00:43+00:00",
            "paper proof readiness audit: status=ok target_session=2026-07-07 check_status=passed created_at=2026-07-07T18:03:26+00:00",
            "paper proof exposure protection: status=entry_pending issues=none local_positions=0 local_stop_orders=0 local_entry_orders=1 broker_positions=0 broker_orders=1 symbols=none",
        ]
    )

    assert _run_deploy_proof_status_ready(tmp_path, proof_output) == "ready"
    assert _run_deploy_exposure_safe(tmp_path, proof_output) == "blocked"


def test_deploy_rejects_unprotected_paper_exposure_after_restart(tmp_path: Path) -> None:
    proof_output = "\n".join(
        [
            "paper proof summary: readiness=blocked proof=pending reason=awaiting_completed_proof_session blockers=local_open_positions,broker_open_orders evidence_blockers=sample_trades sealed_evidence_blockers=sample_trades overall_blockers=sample_trades clean_window_blockers=sample_trades sealed_clean_window_blockers=sample_trades warnings=none",
            "paper proof runtime: ops_status=ok ops_detail=status=ok image_status=ok image_detail=runtime image health ok",
            "paper proof stream: status=ok latest_start=2026-07-07T16:09:19+00:00 latest_event=trade_update_stream_started:2026-07-07T16:09:19+00:00",
            "paper proof readiness audit: status=ok target_session=2026-07-07 check_status=passed created_at=2026-07-07T16:18:18+00:00",
            "paper proof exposure protection: status=needs_attention issues=missing_local_stop local_positions=1 local_stop_orders=0 local_entry_orders=0 broker_positions=1 broker_orders=0 symbols=ARQT",
        ]
    )

    assert _run_deploy_proof_status_ready(tmp_path, proof_output) == "blocked"
    assert _run_deploy_exposure_safe(tmp_path, proof_output) == "blocked"


def test_deploy_ops_check_accepts_maintenance_drain_status(tmp_path: Path) -> None:
    status_line = (
        "mode=paper strategy=v1-breakout status=close_only kill_switch=false "
        "reason=deploy maintenance drain updated_at=2026-07-07T17:30:00+00:00"
    )

    assert _run_deploy_expected_trading_status(tmp_path, status_line) == "close_only"


def test_deploy_preflight_rejects_active_entry_orders(tmp_path: Path) -> None:
    proof_output = "\n".join(
        [
            "paper proof summary: readiness=blocked proof=pending reason=awaiting_completed_proof_session blockers=local_active_orders,broker_open_orders evidence_blockers=sample_trades sealed_evidence_blockers=sample_trades overall_blockers=sample_trades clean_window_blockers=sample_trades sealed_clean_window_blockers=sample_trades warnings=none",
            "paper proof runtime: ops_status=ok ops_detail=status=ok image_status=blocked image_detail=runtime image health failed",
            "paper proof stream: status=ok latest_start=2026-07-07T16:09:19+00:00 latest_event=trade_update_stream_started:2026-07-07T16:09:19+00:00",
            "paper proof readiness audit: status=ok target_session=2026-07-07 check_status=passed created_at=2026-07-07T16:18:18+00:00",
            "paper proof exposure protection: status=entry_pending issues=none local_positions=0 local_stop_orders=0 local_entry_orders=1 broker_positions=0 broker_orders=1 symbols=none",
        ]
    )

    assert _run_deploy_exposure_safe(tmp_path, proof_output) == "blocked"


def test_deploy_rejects_mixed_proof_blockers_with_protected_exposure(
    tmp_path: Path,
) -> None:
    proof_output = "\n".join(
        [
            "paper proof summary: readiness=blocked proof=pending reason=awaiting_completed_proof_session blockers=local_open_positions,readiness_audit_stale evidence_blockers=sample_trades sealed_evidence_blockers=sample_trades overall_blockers=sample_trades clean_window_blockers=sample_trades sealed_clean_window_blockers=sample_trades warnings=none",
            "paper proof runtime: ops_status=ok ops_detail=status=ok image_status=ok image_detail=runtime image health ok",
            "paper proof stream: status=ok latest_start=2026-07-07T16:09:19+00:00 latest_event=trade_update_stream_started:2026-07-07T16:09:19+00:00",
            "paper proof readiness audit: status=ok target_session=2026-07-07 check_status=passed created_at=2026-07-07T16:18:18+00:00",
            "paper proof exposure protection: status=protected issues=none local_positions=1 local_stop_orders=1 local_entry_orders=0 broker_positions=1 broker_orders=1 symbols=ARQT",
        ]
    )

    assert _run_deploy_proof_status_ready(tmp_path, proof_output) == "blocked"


def test_paper_env_example_matches_audited_bull_flag_posture() -> None:
    env_text = Path("deploy/paper.env.example").read_text()

    assert "MARKET_DATA_FEED=iex" in env_text
    assert "RELATIVE_VOLUME_THRESHOLD=2.0" in env_text
    assert "MAX_OPEN_POSITIONS=1" in env_text
    assert "BULL_FLAG_MIN_RUN_PCT=0.02" in env_text
    assert "BULL_FLAG_CONSOLIDATION_VOLUME_RATIO=0.6" in env_text
    assert "BULL_FLAG_CONSOLIDATION_RANGE_PCT=0.5" in env_text
    assert "REPLAY_SLIPPAGE_BPS=2.0" in env_text
    assert "NIGHTLY_MAX_COMBOS=24" in env_text
    assert "NIGHTLY_TIMEOUT_SECONDS=14400" in env_text
    assert "SECOND_STRATEGY_SCAN_TIMEOUT_SECONDS=7200" in env_text
    assert "SECOND_STRATEGY_OUTPUT_ROOT=/var/lib/alpaca-bot/nightly/second_strategy" in env_text
    assert "SECOND_STRATEGY_EXCLUDE_CANDIDATES=vwap_cross" in env_text
    assert "SECOND_STRATEGY_CANDIDATE_SCALES=0.10,0.25,0.50" in env_text
    assert "SECOND_STRATEGY_INCLUDE_OPTION_CANDIDATES=auto" in env_text
    assert "SECOND_STRATEGY_VALIDATE_POSITIVES=true" in env_text
    assert "SECOND_STRATEGY_VALIDATE_ALL_POSITIVE_ROWS=true" in env_text
    assert "SECOND_STRATEGY_MAX_VALIDATION_CANDIDATES=0" in env_text
    assert "SECOND_STRATEGY_SCAN_JOBS=2" in env_text
    assert "SECOND_STRATEGY_VALIDATION_SAMPLE_SIZE=160" in env_text
    assert "SECOND_STRATEGY_VALIDATION_SAMPLE_SEED=second-strategy-independent-validation" in env_text
    assert "PAPER_PROOF_FREEZE=true" in env_text
    assert "PAPER_READINESS_MAX_PASS_AGE_MINUTES=180" in env_text
    assert "PROFIT_PROBE_START_DATE=2026-07-07" in env_text
    assert "PROFIT_PROBE_STRATEGY=bull_flag" in env_text
    assert "PROFIT_PROBE_STRATEGIES=bull_flag" in env_text
    assert "PROFIT_PROBE_MIN_TRADES=30" in env_text
    assert "PAPER_SCALE_MIN_TRADES=30" in env_text
    assert "PAPER_SCALE_MIN_STRATEGIES=2" in env_text
    assert "PAPER_APPROVED_STRATEGIES=bull_flag" in env_text
    assert "PAPER_READINESS_DECISION_DRY_RUN_STRATEGIES=bull_flag" in env_text
    assert "PAPER_ACTIVITY_STRATEGIES=bull_flag" in env_text
    assert "SESSION_GUARD_STRATEGIES=bull_flag" in env_text
    assert "SESSION_GUARD_MIN_TRADES=10" in env_text
    assert "SESSION_GUARD_FAIL_BELOW_PNL=0" in env_text
    assert "PAPER_EXECUTION_MIN_ENTRY_FILL_RATE=0.25" in env_text
    assert "PAPER_EXECUTION_MAX_CAPACITY_REJECT_RATE=0.05" in env_text
    assert "PROFIT_PROBE_MIN_PNL=0.01" in env_text
    assert "RISK_PER_TRADE_PCT=0.01" in env_text
    assert "MAX_POSITION_PCT=0.05" in env_text
    assert "MAX_LOSS_PER_TRADE_DOLLARS=20.0" in env_text
    assert "MAX_PORTFOLIO_EXPOSURE_PCT=0.30" in env_text
    assert "INTRADAY_CONSECUTIVE_LOSS_GATE=0" in env_text
    assert "STOP_LIMIT_BUFFER_PCT=0.0005" in env_text
    assert "ENTRY_STOP_PRICE_BUFFER=0.02" in env_text
    assert "ATR_PERIOD=20" in env_text
    assert "ATR_STOP_MULTIPLIER=1.0" in env_text
    assert "TRAILING_STOP_ATR_MULTIPLIER=1.0" in env_text
    assert "TRAILING_STOP_PROFIT_TRIGGER_R=1.0" in env_text
    assert "ENABLE_VIX_FILTER=false" in env_text
    assert "ENABLE_SECTOR_FILTER=false" in env_text
    assert "ENABLE_VWAP_ENTRY_FILTER=false" in env_text
    assert "ENTRY_ORDER_ACTIVE_BARS=1" in env_text
    assert "ENABLE_PROFIT_TRAIL=true" in env_text
    assert "PROFIT_TRAIL_PCT=0.90" in env_text
    assert "ENABLE_PROFIT_TARGET=true" in env_text
    assert "PROFIT_TARGET_R=3.0" in env_text
    assert "ENABLE_BREAKEVEN_STOP=true" in env_text
    assert "BREAKEVEN_TRIGGER_PCT=0.005" in env_text
    assert "BREAKEVEN_TRAIL_PCT=0.002" in env_text
    assert "ENABLE_TREND_FILTER_EXIT=false" in env_text
    assert "ENABLE_VWAP_BREAKDOWN_EXIT=false" in env_text
    assert "VWAP_BREAKDOWN_MIN_BARS=1" in env_text
    assert "ENABLE_NO_FOLLOW_THROUGH_EXIT=false" in env_text
    assert "NO_FOLLOW_THROUGH_EXIT_MINUTES=0" in env_text
    assert "NO_FOLLOW_THROUGH_MIN_FAVORABLE_PCT=0.0025" in env_text
    assert "ENABLE_GIVEBACK_EXIT=true" in env_text
    assert "GIVEBACK_EXIT_MIN_FAVORABLE_PCT=0.0025" in env_text
    assert "GIVEBACK_EXIT_MAX_RETURN_PCT=0.0" in env_text
    assert "ENABLE_EARLY_LOSS_EXIT=false" in env_text
    assert "EARLY_LOSS_EXIT_MINUTES=0" in env_text
    assert "EARLY_LOSS_EXIT_RETURN_PCT=0.01" in env_text
    assert "ENABLE_REGIME_FILTER=false" in env_text
    assert "ENABLE_NEWS_FILTER=false" in env_text
    assert "ENABLE_SPREAD_FILTER=false" in env_text
    assert "ENABLE_OPTIONS_TRADING=false" in env_text
    assert "OPTION_CHAIN_SYMBOLS=" in env_text


def test_init_server_generates_audited_paper_posture() -> None:
    script = Path("scripts/init_server.sh").read_text()

    assert "MARKET_DATA_FEED=iex" in script
    assert 'RISK_PER_TRADE_PCT="0.01"' in script
    assert 'MAX_OPEN_POSITIONS="1"' in script
    assert 'RELATIVE_VOLUME_THRESHOLD="2.0"' in script
    assert "BULL_FLAG_MIN_RUN_PCT=0.02" in script
    assert "BULL_FLAG_CONSOLIDATION_VOLUME_RATIO=0.6" in script
    assert "BULL_FLAG_CONSOLIDATION_RANGE_PCT=0.5" in script
    assert "ENTRY_ORDER_ACTIVE_BARS=1" in script
    assert 'REPLAY_SLIPPAGE_BPS="2.0"' in script
    assert "NIGHTLY_MAX_COMBOS=24" in script
    assert "NIGHTLY_TIMEOUT_SECONDS=14400" in script
    assert "SECOND_STRATEGY_SCAN_TIMEOUT_SECONDS=7200" in script
    assert "SECOND_STRATEGY_OUTPUT_ROOT=/var/lib/alpaca-bot/nightly/second_strategy" in script
    assert "SECOND_STRATEGY_EXCLUDE_CANDIDATES=vwap_cross" in script
    assert "SECOND_STRATEGY_CANDIDATE_SCALES=0.10,0.25,0.50" in script
    assert "SECOND_STRATEGY_INCLUDE_OPTION_CANDIDATES=auto" in script
    assert "SECOND_STRATEGY_VALIDATE_POSITIVES=true" in script
    assert "SECOND_STRATEGY_VALIDATE_ALL_POSITIVE_ROWS=true" in script
    assert "SECOND_STRATEGY_MAX_VALIDATION_CANDIDATES=0" in script
    assert "SECOND_STRATEGY_SCAN_JOBS=2" in script
    assert "SECOND_STRATEGY_VALIDATION_SAMPLE_SIZE=160" in script
    assert "SECOND_STRATEGY_VALIDATION_SAMPLE_SEED=second-strategy-independent-validation" in script
    assert 'PAPER_PROOF_FREEZE="true"' in script
    assert "PAPER_READINESS_MAX_PASS_AGE_MINUTES=180" in script
    assert "PROFIT_PROBE_START_DATE=2026-07-07" in script
    assert "PROFIT_PROBE_STRATEGY=bull_flag" in script
    assert "PROFIT_PROBE_STRATEGIES=bull_flag" in script
    assert "PROFIT_PROBE_MIN_TRADES=30" in script
    assert "PAPER_SCALE_MIN_TRADES=30" in script
    assert "PAPER_SCALE_MIN_STRATEGIES=2" in script
    assert "PAPER_APPROVED_STRATEGIES=bull_flag" in script
    assert "PAPER_READINESS_DECISION_DRY_RUN_STRATEGIES=bull_flag" in script
    assert "PAPER_ACTIVITY_STRATEGIES=bull_flag" in script
    assert "SESSION_GUARD_STRATEGIES=bull_flag" in script
    assert "SESSION_GUARD_MIN_TRADES=10" in script
    assert "SESSION_GUARD_FAIL_BELOW_PNL=0" in script
    assert "PAPER_EXECUTION_MIN_ENTRY_FILL_RATE=0.25" in script
    assert "PAPER_EXECUTION_MAX_CAPACITY_REJECT_RATE=0.05" in script
    assert "PROFIT_PROBE_MIN_PNL=0.01" in script
    assert "CONFIDENCE_FLOOR=0.25" in script
    assert "INTRADAY_CONSECUTIVE_LOSS_GATE=0" in script
    assert "STOP_LIMIT_BUFFER_PCT=0.0005" in script
    assert "ENTRY_STOP_PRICE_BUFFER=0.02" in script
    assert "ATR_PERIOD=20" in script
    assert "ATR_STOP_MULTIPLIER=1.0" in script
    assert "TRAILING_STOP_ATR_MULTIPLIER=1.0" in script
    assert "TRAILING_STOP_PROFIT_TRIGGER_R=1.0" in script
    assert 'ENABLE_VIX_FILTER="false"' in script
    assert 'ENABLE_SECTOR_FILTER="false"' in script
    assert 'ENABLE_VWAP_ENTRY_FILTER="false"' in script
    assert 'ENABLE_PROFIT_TRAIL="true"' in script
    assert "PROFIT_TRAIL_PCT=0.90" in script
    assert 'ENABLE_PROFIT_TARGET="true"' in script
    assert 'PROFIT_TARGET_R="3.0"' in script
    assert "ENABLE_PROFIT_TARGET=$ENABLE_PROFIT_TARGET" in script
    assert "PROFIT_TARGET_R=$PROFIT_TARGET_R" in script
    assert 'ENABLE_BREAKEVEN_STOP="true"' in script
    assert 'BREAKEVEN_TRIGGER_PCT="0.005"' in script
    assert "ENABLE_BREAKEVEN_STOP=$ENABLE_BREAKEVEN_STOP" in script
    assert "BREAKEVEN_TRIGGER_PCT=$BREAKEVEN_TRIGGER_PCT" in script
    assert "BREAKEVEN_TRAIL_PCT=$BREAKEVEN_TRAIL_PCT" in script
    assert 'ENABLE_TREND_FILTER_EXIT="false"' in script
    assert "ENABLE_TREND_FILTER_EXIT=$ENABLE_TREND_FILTER_EXIT" in script
    assert 'ENABLE_VWAP_BREAKDOWN_EXIT="false"' in script
    assert "ENABLE_VWAP_BREAKDOWN_EXIT=$ENABLE_VWAP_BREAKDOWN_EXIT" in script
    assert "VWAP_BREAKDOWN_MIN_BARS=1" in script
    assert 'ENABLE_NO_FOLLOW_THROUGH_EXIT="false"' in script
    assert "ENABLE_NO_FOLLOW_THROUGH_EXIT=$ENABLE_NO_FOLLOW_THROUGH_EXIT" in script
    assert "NO_FOLLOW_THROUGH_EXIT_MINUTES=0" in script
    assert "NO_FOLLOW_THROUGH_MIN_FAVORABLE_PCT=0.0025" in script
    assert 'ENABLE_GIVEBACK_EXIT="true"' in script
    assert 'ENABLE_GIVEBACK_EXIT="false"' in script
    assert "ENABLE_GIVEBACK_EXIT=$ENABLE_GIVEBACK_EXIT" in script
    assert "GIVEBACK_EXIT_MIN_FAVORABLE_PCT=0.0025" in script
    assert "GIVEBACK_EXIT_MAX_RETURN_PCT=0.0" in script
    assert 'ENABLE_EARLY_LOSS_EXIT="false"' in script
    assert "ENABLE_EARLY_LOSS_EXIT=$ENABLE_EARLY_LOSS_EXIT" in script
    assert "EARLY_LOSS_EXIT_MINUTES=0" in script
    assert "EARLY_LOSS_EXIT_RETURN_PCT=0.01" in script
    assert "ENABLE_NEWS_FILTER=false" in script
    assert "ENABLE_SPREAD_FILTER=false" in script
    assert "OPTION_CHAIN_SYMBOLS=" in script
